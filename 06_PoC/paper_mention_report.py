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
import unicodedata

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
    """复核侧分词 —— **必须与匹配侧同口径**，否则两边在核对两件不同的事。

    ★ D102：本函数前两版漏了匹配侧早已修过的规范化，于是同一份证据
      生成侧判「可复核」、复核侧判「不可复核」（10 条 → 4 条 → 3 条，逐个暴露）：
      ① **en dash**：文献写 Maxwell–Boltzmann / Henderson–Hasselbalch（U+2013），
         只认 ASCII `-` 就切成两词，永远对不上 maxwellboltzmann（D93 已修）。
      ② **弯撇号**：Faraday’s / Euler’s 用 U+2019，只认 ASCII `'` 会切成
         [faraday, s, law]，对不上 [faraday, law]。
      ③ **变音符**：图里 Schrödinger，老论文写 Schrodinger ⇒ 两边都要 NFKD 剥离。
      ④ **括号**：`Schrödinger equation (time-independent)` 的指称对象是
         Schrödinger equation，括号是修饰语 —— 匹配侧删的是**全部**括号内容，
         复核侧若只删末尾括号（第一版就是），口径又分叉了。
      ★ 反过来也别「多修」：匹配侧只把 `–—−` 三类 dash 当连字符，
        U+2010(‐) **不在其中**，所以 `non‐ideal gas law` 会断成 non/ideal/gas/law
        ⇒ 才能命中 [ideal, gas, law]。复核侧若把 U+2010 也折成连字符，
        就会得到 nonideal 而对不上 —— 多修一处同样制造分歧。
    ★ 这里是**独立实现**（照抄口径，不 import 抓取代码）：复核的价值在于
      「不复用同一个函数」，不在于「用另一套规则」。规则不同 ⇒ 复核的是别的命题。
    """
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    for ch in "\u2013\u2014\u2212":          # en dash / em dash / minus —— 仅此三类
        s = s.replace(ch, "-")
    s = s.replace("\u2019", "'")             # 弯撇号
    s = re.sub(r"'s(?![A-Za-z])", " ", s)    # 所有格整体删
    s = re.sub(r"\([^)]*\)", " ", s)         # 全部括号内容（匹配侧同口径）
    out = []
    for m in re.finditer(r"[\w]+(?:-[\w]+)*", s, re.UNICODE):
        t = m.group(0).lower().replace("-", "").replace("'", "")
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


# ---------------------------------------------------------------- 口径自检
# ★ D102 的判据：今天两侧口径连漂三次（10 → 4 → 3 条「不可复核」全是漂移造成的
#   假警报），根因是**同一套规范化规则存在两份实现，改了一份没改另一份**。
#   把「两份实现必须给出同样的分词」变成可反复跑的断言：将来谁再改一侧，立刻红。
# ★ 只在 --selfcheck 里 import 生成侧 —— 主流程的复核仍是独立实现（不复用同一函数），
#   自检的目的恰恰是**对照**两份实现，而不是共用一份。
_CROSS_CASES = [
    "Maxwell–Boltzmann distribution",        # en dash ⇒ 必须连词
    "Henderson–Hasselbalch equation",        # en dash ⇒ 必须连词
    "Gauss's-law constraints",               # 所有格 + ASCII 连字符
    "Faraday’s Law",                         # 弯撇号 U+2019
    "Euler’s polyhedron formula",            # 弯撇号
    "Schrödinger equation (time-independent)",  # 变音符 + 括号修饰语
    "non‐ideal gas law",                     # U+2010 ⇒ 必须**断**词（多修一处就错）
    "The Law of Mass Action",                # 普通多词
]


def _gen_side_tokens(s: str):
    """生成侧分词（自检专用）—— 对照基准。"""
    spec = importlib.util.spec_from_file_location(
        "_p68fetch", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "..", "11_真实数据", "phase68_paper_fetch.py"))
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return [t[0] for t in mod.canon_tokens(s)]


def _legacy_words(s: str):
    """负对照用的**旧口径**（D102 修之前的实现）：不折 dash、不认弯撇号、不变音符。"""
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


def selfcheck() -> int:
    print("Phase 68 · 分词口径跨实现自检（D102：两侧规则必须一致）")
    gen = _gen_side_tokens
    mism = []
    for s in _CROSS_CASES:
        g, w = gen(s), words(s)
        if g is None:
            print("  [FAIL] 取不到生成侧实现"); return 1
        if g != w:
            mism.append((s, g, w))
    ok1 = not mism
    print("  [%s] W1 复核侧 words() == 生成侧 canon_tokens（%d 个样例）"
          % ("PASS" if ok1 else "FAIL", len(_CROSS_CASES)))
    for s, g, w in mism[:4]:
        print("      %-42s 生成侧 %s ／ 复核侧 %s" % (s[:42], g, w))
    # W2 负对照：旧口径**必须**与生成侧不一致（否则 W1 是恒真的，等于没测）
    diff = [s for s in _CROSS_CASES if _legacy_words(s) != gen(s)]
    ok2 = len(diff) > 0
    print("  [%s] W2 负对照：旧口径与生成侧在 %d/%d 个样例上不一致（须 >0，"
          "否则 W1 恒真 = 没测到东西）" % ("PASS" if ok2 else "FAIL", len(diff), len(_CROSS_CASES)))
    bad = (0 if ok1 else 1) + (0 if ok2 else 1)
    print("  合计 %d / 2" % (2 - bad))
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 68 G4 mentioned_in 只读报告")
    ap.add_argument("--json", default="")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--selfcheck", action="store_true",
                    help="分词口径跨实现自检（D102：复核侧与生成侧必须同规则）")
    a = ap.parse_args()
    if a.selfcheck:
        return selfcheck()

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
            # ★ D102：复核必须用**与匹配时同一个名字**，否则两边在核对两件不同的事。
            #   图节点的 name 是「Schrödinger equation (time-independent)」，而抓取时
            #   用来匹配的是 search_name（去括号后缀的核心名）「Schrödinger equation」。
            #   拿全名去论文标题里找 3 词序列，当然找不到 —— 于是 U2 报了 10 条「不可复核」，
            #   而生成侧 S2 报 0 条：**同一份证据，两套口径，两个结论**。
            #   这些边本身是对的（"Solution of the Schrödinger equation by a spectral
            #   method" 确实提及了它）⇒ 错的是复核侧的口径，不是数据。
            #   ★ 独立实现（不 import 生成侧 name_tokens）：括号后缀是**修饰语**，
            #     指称对象是去掉它之后的核心名。
            # words() 内部已按匹配侧口径剥掉括号修饰语（⇒ Schrödinger equation）
            nm = p.get("name") or ""
            toks = words(nm)
            for r in refs:
                # U2 的核心：**每条引用都必须能在自己的定位句里被重新找到**
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
        # ★ D101：旧版要求「三态都要 >0」——那是**劣化池时代**的假设（65 条抓取失败）。
        #   抓取补全之后 not_extracted 会**正确地**变成 0，再要求它 >0 就是在奖励故障：
        #   为了绿，就得盼着有一批没查到。它该等于「本轮没检索到的条数」，完整运行即 0；
        #   若真的 >0，由生成侧 S5b 咬住「必须等于池里 error 条数」。
        ("U5 非真空（not_extracted 应 0，除非真有没检索的）",
         st["extracted"] > 0 and st["no_referent"] > 0,
         "extracted %d ／ no_referent %d（皆须 >0，防分支没走到）／ not_extracted %d"
         "（完整运行须 0；>0 时由 S5b 保证它等于池里 error 条数）"
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
