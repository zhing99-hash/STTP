#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
"""symbol_report.py —— G1 符号回填的**只读**报告 + 对账（Phase 61）

规划：`00_项目管理/下一阶段规划_数据扩展与前端优化_20261019.md` §3.1 F1
    「`symbols` 从边回填到节点：覆盖率 0.16% → ≥90%；抽 20 个公式核对符号表正确」

它做什么
--------
1. 报**覆盖率**（按来源前缀分列）与**字形来源**（源声明 / 目标节点自带）；
2. 报**变量表**条目数与「同一量的两种写法」「原文查无字形」这两类待复核项；
3. 跑对账断言 **Y1–Y5**，把「回填对不对」从一句承诺变成可回归的事实；
4. `--selfcheck` 用**合成节点**验证每条判据**能被踩红**（D72 家族：恒真判据比没有判据更坏）。

它不做什么
----------
* **不改树**（产物只去 `--json`，默认 `.runlog/`）；
* **不联网**、不重新解析 parquet —— 只读**已落盘**的图。派生规则在
  `11_真实数据/phase61_symbol_backfill_delta.py` 里，两边不共享代码：
  报告若复用生成侧的函数，就变成了「用同一段代码验自己」（铁律 #87 的同族）。

判据（Y 系列，与 G/R/V/F 族不撞号）
----------------------------------
  Y1 覆盖率 ≥ 90%（F1 的验收线）
  Y2 守恒：`symbols` **恰好等于**表内非空字形去重后的有序序列（不多不少、不重不乱）
  Y3 字形可查：每个写进 `symbols` 的字形都必须出现在该节点的原文里
  Y4 归属清晰：每个公式节点要么**由规则管理**（带 rule version + 表），
     要么**保留人工值**（无 rule version、无表）—— 不允许「写了一半」的中间态
  Y5 非真空：全局必须同时存在「同一量两种写法」与「字形未确认」两类条目，
     否则 Y3/Y4 的通过可能只是那些分支根本没走到

退出码：`0` 全过 ／ `1` 有 FAIL ／ `2` 缺权威图 ／ `3` 仪器自证失败

用法
----
    bash sttp.sh symbols [--sample 20] [--json .runlog/symbol_report.json]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
FORMULA_LABELS = ("Formula", "Definition", "Lemma", "Theorem", "Law")
RULE_VERSION = "s1"
COVER_MIN = 90.0


class Rep:
    """三态报告（与仓内其它仪器同形：`[PASS]` / `[FAIL]` / `[SKIP]` + 末尾计数）。"""

    def __init__(self):
        self.rows = []

    def add(self, ok, name, detail=""):
        self.rows.append((("PASS" if ok else "FAIL") if ok is not None else "SKIP",
                          name, detail))

    def dump(self, title="symbol_report"):
        for st, name, detail in self.rows:
            print("  [%s] %s%s" % (st, name, ("  —— " + detail) if detail else ""))
        n = collections.Counter(s for s, _, _ in self.rows)
        print("\n  %s: PASS %d / FAIL %d / SKIP %d" % (title, n["PASS"], n["FAIL"], n["SKIP"]))
        return n


# ---------------------------------------------------------------------------
# 判据核心（真图与自证图**共用同一段**，否则自证等于另写一套）
# ---------------------------------------------------------------------------
def hay_of(node):
    """「这个记号属于这条记录吗」的可查文本：公式本体 + 简述 + 名称。"""
    p = node.get("props") or {}
    return " ".join(str(p.get(k) or "") for k in ("latex", "equation", "informal", "name"))


def audit_formula_nodes(nodes):
    """对公式类节点逐条核 Y2/Y3/Y4，返回 (stats, problems)。

    `problems` 是 rule → [node_id…]，调用方决定是报红还是自证。
    """
    stats = {"formula": 0, "with_symbols": 0, "managed": 0, "human": 0,
             "entries": 0, "glyphs": 0, "curated_diff": 0, "unresolved_entries": 0}
    problems = collections.defaultdict(list)
    for n in nodes:
        if not any(lb in FORMULA_LABELS for lb in (n.get("labels") or [])):
            continue
        stats["formula"] += 1
        p = n.get("props") or {}
        syms = p.get("symbols")
        table = p.get("symbol_table")
        managed = bool(p.get("symbols_rule_version"))
        if managed:
            stats["managed"] += 1
        elif syms:
            stats["human"] += 1
        if syms:
            stats["with_symbols"] += 1
            stats["glyphs"] += len(syms)

        # Y4 归属清晰：带规则版本就必须带表；不带规则版本就**不许**带表（写了一半）
        if managed and not isinstance(table, list):
            problems["Y4"].append(n["id"])
        if not managed and isinstance(table, list):
            problems["Y4"].append(n["id"])
        if not isinstance(table, list):
            continue
        stats["entries"] += len(table)
        for ent in table:
            if not ent.get("sym"):
                stats["unresolved_entries"] += 1
            if ent.get("sym_curated"):
                stats["curated_diff"] += 1
        # Y2 守恒：列 == 表内非空字形（集合相等、且列内不重复）
        seen, uniq = set(), []
        for ent in table:
            s = ent.get("sym")
            if s and s not in seen:
                seen.add(s)
                uniq.append(s)
        if sorted(syms or []) != sorted(uniq) or len(syms or []) != len(set(syms or [])):
            problems["Y2"].append(n["id"])
        # Y3 字形可查
        hay = hay_of(n)
        bad = [s for s in (syms or []) if s and s not in hay]
        if bad:
            problems["Y3"].append(n["id"])
        # Y2b 次序：能在原文里定位的字形，位置必须**单调不减**（＝按出现次序排）
        pos = [hay.find(s) for s in (syms or []) if s and s in hay]
        if any(pos[i] > pos[i + 1] for i in range(len(pos) - 1)):
            problems["Y2b"].append(n["id"])
    return stats, problems


def coverage_pct(stats):
    return 100.0 * stats["with_symbols"] / max(1, stats["formula"])


# ---------------------------------------------------------------------------
# 自证：合成节点把每条判据踩红
# ---------------------------------------------------------------------------
def _syn(nid, syms, table, rule=None, latex="x = y", informal="", name=""):
    p = {"latex": latex, "informal": informal, "name": name}
    if syms is not None:
        p["symbols"] = syms
    if table is not None:
        p["symbol_table"] = table
    if rule:
        p["symbols_rule_version"] = rule
    return {"id": nid, "labels": ["Formula", "Entity"], "props": p}


def selfcheck():
    """每条判据都必须**能被踩红**；踩不红就是判据死了（D72）。

    ★ 断言名**以判据 ID 开头**（`Y3:bad …`）是硬要求：`red_probe.starts_with_id()`
      按「记号前缀 + 非字母数字分界」匹配，名字里把 ID 埋在中间 ⇒ 探针认不到 ⇒
      本自检就脱离了 R21 的看护（#98 自指的失败）。
    """
    ent = lambda s, q="mass", **kw: dict({"sym": s, "quantity": q, "quantity_id": "PQ:x",
                                          "src": "edge_target"}, **kw)
    cases = {
        # 正常：字形 x 在 latex 里，表与列一致
        "Y0ok": _syn("Y0:ok", ["x", "y"], [ent("x"), ent("y", "length")], RULE_VERSION),
        # Y2：列里有 z，表里没有
        "Y2": _syn("Y2:bad", ["x", "z"], [ent("x"), ent("y", "length")], RULE_VERSION),
        # Y2b：集合相同但次序反了（"x = y" 里 y 在后，列里却在前面）
        "Y2b": _syn("Y2b:bad", ["y", "x"], [ent("x"), ent("y", "length")], RULE_VERSION),
        # Y3：字形 q 不在原文
        "Y3": _syn("Y3:bad", ["x", "q"], [ent("x"), ent("q", "charge")], RULE_VERSION),
        # Y4：有规则版本但没有表（写了一半）
        "Y4a": _syn("Y4a:bad", ["x"], None, RULE_VERSION),
        # Y4b：没有规则版本却带表（人工值上覆了派生表）
        "Y4b": _syn("Y4b:bad", ["x"], [ent("x")], None),
    }
    nodes = list(cases.values())
    stats, problems = audit_formula_nodes(nodes)
    rep = Rep()
    # 正常样本**不得**报问题：否则判据只会乱叫，同样是坏判据
    bad_ok = [r for r in ("Y2", "Y2b", "Y3", "Y4") if "Y0:ok" in problems.get(r, [])]
    rep.add(not bad_ok, "Y0 自证·正常样本不报错", "误报：%s" % bad_ok)
    for rule, nid in (("Y2", "Y2:bad"), ("Y2b", "Y2b:bad"), ("Y3", "Y3:bad"),
                      ("Y4a", "Y4a:bad"), ("Y4b", "Y4b:bad")):
        r = "Y4" if rule.startswith("Y4") else rule
        rep.add(nid in problems.get(r, []), "%s 能被踩红（合成节点）" % nid,
                "构造的违规节点必须进 problems[%s]" % r)
    return rep


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def head_sha(short=True):
    try:
        r = subprocess.run(["git", "rev-parse", "--short" if short else "HEAD", "HEAD"],
                           cwd=ROOT, capture_output=True, text=True, timeout=15)
        return r.stdout.strip() if r.returncode == 0 else "?"
    except Exception:                                   # noqa: BLE001
        return "?"


def main():
    ap = argparse.ArgumentParser(description="G1 符号回填只读报告 + 对账 Y1–Y5")
    ap.add_argument("--sample", type=int, default=0, help="打印 N 条变量表供人工核对")
    ap.add_argument("--json", default=os.path.join(ROOT, ".runlog", "symbol_report.json"))
    ap.add_argument("--selfcheck", action="store_true", help="只用合成节点验判据，不读图")
    a = ap.parse_args()

    if a.selfcheck:
        print("=" * 92)
        print("symbol_report --selfcheck —— 判据自证（不依赖权威图）")
        print("=" * 92)
        rep = selfcheck()
        n = rep.dump("symbol_selfcheck")
        return 0 if n["FAIL"] == 0 else 1

    if not os.path.isfile(NORM):
        print("[不可用] 缺少权威图 06_PoC/etl/normalized.json（被 .gitignore 刻意排除）")
        print("  恢复：bash sttp.sh export，或从 Release 资产下载同名快照。")
        return 2
    with open(NORM, encoding="utf-8") as f:
        g = json.load(f)
    nodes = g["nodes"]

    print("=" * 92)
    print("G1 符号回填 · 只读报告  （HEAD %s）" % head_sha())
    print("=" * 92)

    stats, problems = audit_formula_nodes(nodes)
    if stats["formula"] == 0:
        print("[FATAL] 一个公式节点都没读到 ⇒ 不得报通过（铁律 #20 同族）")
        return 3
    cov = coverage_pct(stats)
    print("\n【覆盖率】公式/定义类节点 %d：有 symbols %d = %.2f%%"
          % (stats["formula"], stats["with_symbols"], cov))
    by_prefix = collections.Counter()
    tot_prefix = collections.Counter()
    for n in nodes:
        if not any(lb in FORMULA_LABELS for lb in (n.get("labels") or [])):
            continue
        pre = ":".join(n["id"].split(":")[:2])
        tot_prefix[pre] += 1
        if (n.get("props") or {}).get("symbols"):
            by_prefix[pre] += 1
    top = sorted(tot_prefix.items(), key=lambda kv: -kv[1])[:8]
    for pre, tot in top:
        print("   %-16s %5d / %5d" % (pre, by_prefix.get(pre, 0), tot))

    print("\n【归属】由规则 s1 管理 %d ／ 保留人工值 %d ／ 合计 %d"
          % (stats["managed"], stats["human"], stats["managed"] + stats["human"]))
    print("【变量表】条目 %d；字形 %d；「同一量两种写法」%d；字形未确认 %d"
          % (stats["entries"], stats["glyphs"], stats["curated_diff"],
             stats["unresolved_entries"]))

    print("\n" + "=" * 92)
    print("对账断言 Y1–Y5")
    print("=" * 92)
    rep = Rep()
    rep.add(cov >= COVER_MIN, "Y1 覆盖率 ≥ %.0f%%" % COVER_MIN, "实测 %.2f%%" % cov)
    rep.add(not problems["Y2"], "Y2 列 == 表内字形（守恒）",
            "不一致节点 %d 个%s" % (len(problems["Y2"]),
                                ("（如 %s）" % problems["Y2"][:3]) if problems["Y2"] else ""))
    rep.add(not problems["Y2b"], "Y2b 次序 == 原文出现次序",
            "逆序节点 %d 个%s" % (len(problems["Y2b"]),
                              ("（如 %s）" % problems["Y2b"][:3]) if problems["Y2b"] else ""))
    rep.add(not problems["Y3"], "Y3 字形在该节点原文里可查",
            "违规节点 %d 个%s" % (len(problems["Y3"]),
                              ("（如 %s）" % problems["Y3"][:3]) if problems["Y3"] else ""))
    rep.add(not problems["Y4"], "Y4 归属清晰（规则管理 或 人工保留）",
            "中间态节点 %d 个%s" % (len(problems["Y4"]),
                                ("（如 %s）" % problems["Y4"][:3]) if problems["Y4"] else ""))
    rep.add(stats["curated_diff"] > 0 and stats["unresolved_entries"] > 0,
            "Y5 非真空（两条分支都真的走到过）",
            "「同一量两种写法」%d 条、「字形未确认」%d 条" % (stats["curated_diff"],
                                                       stats["unresolved_entries"]))
    n = rep.dump()

    if a.sample:
        print("\n【抽样 %d 条 · 人工核对用】" % a.sample)
        picked = [n_ for n_ in sorted(nodes, key=lambda x: x["id"])
                  if any(lb in FORMULA_LABELS for lb in (n_.get("labels") or []))
                  and (n_.get("props") or {}).get("symbol_table")][:a.sample]
        for n_ in picked:
            p = n_["props"]
            print("  %-22s %s" % (n_["id"], str(p.get("latex") or p.get("equation"))[:66]))
            print("     symbols = %s" % (p.get("symbols") or []))
            for ent in p.get("symbol_table") or []:
                print("       %-8s ← %-16s %s%s"
                      % (ent.get("sym") if ent.get("sym") else "（未确认 %s）" % ent.get("sym_candidate"),
                         ent.get("quantity"), ent.get("quantity_id"),
                         ("   (策展 %s)" % ent["sym_curated"]) if ent.get("sym_curated") else ""))

    out = {"head": head_sha(False), "rule_version": RULE_VERSION,
           "stats": stats, "coverage_pct": round(cov, 2),
           "problems": {k: v for k, v in problems.items() if v}}
    try:
        os.makedirs(os.path.dirname(a.json), exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("\n  → %s" % os.path.relpath(a.json, ROOT))
    except Exception as exc:                            # noqa: BLE001
        print("\n  [WARN] 写 JSON 失败（%s）—— 报告本身不受影响" % exc)
    return 0 if n["FAIL"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
