#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""Phase 68 —— G4 论文线 · 只读报告与对账（U 族）

定位
----
与 `11_真实数据/phase68_paper_mention_delta.py` **互为对手**：

* 不联网、不写图 —— 只读**已落盘**的 normalized.json；
* **不复用**生成侧代码：这里的分词/匹配是**第二实现**（比生成侧更笨一点没关系，
  关键是不能共用 bug）。否则就变成「用同一段代码验自己」（铁律 #87 的同族）。

它要回答的问题不是「有多少条」，而是「**写进图的每一条能不能被独立复核**」：

  U1 status 全覆盖：每个 Formula 的 `papers_status` 必须是三值之一
  U2 指称复核（独立实现）：每条 refs 的 snippet 里必须能重新找到公式名
  U3 空位不撒谎：`no_referent` 不得带任何 refs；`extracted` 必须带非空 refs
  U4 归属清晰：extracted 必须同时带 papers_source / papers_rule_version
  U5 非真空：三态都要 >0，否则 U2/U3 通过可能只是分支没走到
  U6 边完整性：`mentioned_in` 的 source 必须是 Paper、target 必须是 Formula，且都在图里
  U7 语义边界：**不得**出现 Formula 端点的 `discusses` 边
     （V1 判过 178 条 discusses 全是 Paper→MathConcept；将来谁越过这条线，
      就是把「主题对齐」打扮成「文献讨论了这条公式」）
  U8 与人工裁决表一致：落盘的 extracted 集合 == VERDICT 里 accept* 的集合

退出码：`0` 全过 ／ `1` 有 FAIL ／ `2` 缺权威图
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
DELTA = os.path.join(ROOT, "11_真实数据", "phase68_paper_mention_delta.py")

STATUSES = ("extracted", "no_referent", "not_extracted")


# ---------------------------------------------------------------------------
# 独立实现的规范化（**故意**与生成侧写法不同：
#   这里先把词 token 取出来再逐词清洗，生成侧是先字符级折叠再取 token。
#   两条路径能对上，说明不是同一个 bug 在两边一起通过。）
def words(s: str):
    out = []
    for m in re.finditer(r"[\w]+(?:-[\w]+)*|[^\w\s]", s or "", re.UNICODE):
        t = m.group(0)
        if not re.match(r"[\w]", t, re.UNICODE):
            continue
        t = t.lower().replace("-", "")
        t = re.sub(r"'s$", "", t).replace("'", "")
        if t:
            out.append(t)
    return out


def contains_seq(hay: str, needle: list) -> bool:
    h = words(hay)
    n = len(needle)
    return any(h[i:i + n] == needle for i in range(max(0, len(h) - n + 1)))


def load_verdict():
    """裁决表是**事实**（80 条逐条读过的裁决），不是逻辑 —— 两边共享同一份。"""
    spec = importlib.util.spec_from_file_location("_p68delta", DELTA)
    if spec is None or spec.loader is None:
        return {}
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return {}
    return getattr(mod, "VERDICT", {}) or {}


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 68 G4 mentioned_in 只读报告")
    ap.add_argument("--json", default="")
    ap.add_argument("--sample", type=int, default=0)
    a = ap.parse_args()

    if not os.path.exists(NORM):
        print("缺权威图：%s" % NORM)
        return 2
    with open(NORM, encoding="utf-8") as f:
        g = json.load(f)
    nodes, edges = g["nodes"], g["edges"]
    by = {n["id"]: n for n in nodes}
    forms = [n for n in nodes if "Formula" in (n.get("labels") or [])]
    papers = [n for n in nodes if "Paper" in (n.get("labels") or [])]

    st = collections.Counter()
    unmanaged, ref_bad, attr_bad, noref_with_ref, ext_no_ref = [], [], [], [], []
    me = [e for e in edges if e.get("type") == "mentioned_in"]
    edge_bad, discussed_formula = [], []

    print("=" * 78)
    print("Phase 68 · G4 论文线 · 只读报告（定位句逐条独立复核）")
    print("=" * 78)
    print("  Formula %d ｜ Paper %d ｜ mentioned_in 边 %d" % (len(forms), len(papers), len(me)))

    extracted = set()
    for n in forms:
        p = n.get("props") or {}
        s = p.get("papers_status")
        if s not in STATUSES:
            unmanaged.append(n["id"])
            continue
        st[s] += 1
        refs = p.get("papers")
        if s == "no_referent" and refs:
            noref_with_ref.append(n["id"])
        if s == "extracted":
            extracted.add(n["id"])
            if not refs:
                ext_no_ref.append(n["id"])
                continue
            if not (p.get("papers_source") and p.get("papers_rule_version")):
                attr_bad.append(n["id"])
            nm = p.get("name") or ""
            toks = words(nm)
            for r in refs:
                # U2 的核心：**每条公告都必须能在自己的定位句里被重新找到**
                ok = contains_seq(r.get("snippet") or "", toks) \
                    or contains_seq(r.get("title") or "", toks)
                if not ok:
                    ref_bad.append((n["id"], r.get("openalex_id")))

    for e in me:
        src, tgt = by.get(e.get("source")), by.get(e.get("target"))
        if not src or not tgt or "Paper" not in (src.get("labels") or []) \
                or "Formula" not in (tgt.get("labels") or []):
            edge_bad.append((e.get("source"), e.get("target")))
    for e in edges:
        if e.get("type") != "discusses":
            continue
        s, t = by.get(e.get("source")), by.get(e.get("target"))
        if (s and "Formula" in (s.get("labels") or [])) or \
           (t and "Formula" in (t.get("labels") or [])):
            discussed_formula.append((e.get("source"), e.get("target")))

    verdict = load_verdict()
    want_extracted = {fid for fid, v in verdict.items() if str(v[0]).startswith("accept")}
    # U8 双方向比对：**多了**与**少了**都要报（只看总数会漏掉"加 2 删 1"）
    only_disk = sorted(extracted - want_extracted)
    only_verdict = sorted(want_extracted - extracted)

    if a.sample:
        print("\n【抽样 %d 条 extracted】" % a.sample)
        for fid in sorted(extracted)[:a.sample]:
            p = by[fid]["props"]
            r = (p.get("papers") or [{}])[0]
            print("  %-24s %-32s %s｜%s" % (fid, (p.get("name") or "")[:32],
                                            r.get("openalex_id"), r.get("where")))
            print("      「%s」" % (r.get("snippet") or "")[:150])

    checks = [
        ("U1 status 全覆盖", not unmanaged and (st["extracted"] + st["no_referent"]
                                                + st["not_extracted"]) == len(forms),
         "未落/越界 %d 条；合计 %d / %d" % (len(unmanaged),
                                          st["extracted"] + st["no_referent"] + st["not_extracted"],
                                          len(forms))),
        ("U2 每条引用可独立复核", not ref_bad,
         "定位句里找不到公式名的：%d 条（须 0）%s" % (len(ref_bad), ref_bad[:3])),
        ("U3 空位不撒谎", not noref_with_ref and not ext_no_ref,
         "no_referent 带 refs 的 %d ／ extracted 无 refs 的 %d（皆须 0）"
         % (len(noref_with_ref), len(ext_no_ref))),
        ("U4 归属清晰", not attr_bad,
         "extracted 缺 source/rule_version 的：%d 条（须 0）%s" % (len(attr_bad), attr_bad[:3])),
        ("U5 非真空", st["extracted"] > 0 and st["no_referent"] > 0 and st["not_extracted"] > 0,
         "extracted %d ／ no_referent %d ／ not_extracted %d（三者都要 >0）"
         % (st["extracted"], st["no_referent"], st["not_extracted"])),
        ("U6 边端点正确", not edge_bad,
         "端点不是 Paper→Formula 的：%d 条（须 0）%s" % (len(edge_bad), edge_bad[:3])),
        ("U7 语义边界（discusses 不得连公式）", not discussed_formula,
         "Formula 端点的 discusses：%d 条（须 0 —— 一旦有就是把主题对齐当文献讨论）"
         % len(discussed_formula)),
        ("U8 与人工裁决表一致", not only_disk and not only_verdict,
         "只在图里 %d %s ／ 只在表里 %d %s" % (len(only_disk), only_disk[:3],
                                        len(only_verdict), only_verdict[:3])),
    ]
    print("\n【U 族对账（独立实现，不复用生成侧代码）】")
    bad = 0
    for name, ok, detail in checks:
        print("  [%s] %-30s %s" % ("PASS" if ok else "FAIL", name, detail))
        if not ok:
            bad += 1

    res = {"statuses": dict(st), "n_edges": len(me), "n_paper": len(papers),
           "fail": bad, "only_disk": only_disk, "only_verdict": only_verdict}
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print("  → %s" % a.json)
    print("\n  extracted %d ｜ not_extracted %d ｜ no_referent %d ｜ 合计 %d"
          % (st["extracted"], st["not_extracted"], st["no_referent"],
             st["extracted"] + st["no_referent"] + st["not_extracted"]))
    print("  ⇒ RC=%d" % (1 if bad else 0))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
