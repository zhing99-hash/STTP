#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""_recon_phase33_align2.py —— 第 23 轮只读侦察 4：T4 跨列复算的**真实天花板**（含 formula 判据）。

对每条 T4 边，用「参与物的 label 或 formula」去匹配方程两侧，分类：
  correct  = 只出现在**正确侧**
  wrong    = 只出现在**相反侧**（若存在 → 说明有真错，需细查）
  both     = 两侧都出现（催化剂/同物异名歧义）
  nomatch  = 两侧都不出现
**只读，不改任何数据。**
"""
from __future__ import annotations
import collections, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NORM = os.path.join(HERE, "etl", "normalized.json")


def split_eq(eq):
    if " = " not in eq:
        return None, None
    lhs, rhs = eq.split(" = ", 1)
    f = lambda s: [x.strip() for x in s.split(" + ") if x.strip()]
    return f(lhs), f(rhs)


def canon_tok(x):
    """归一：小写、去空格、去电荷括注 (…)、去正负号。"""
    s = str(x).lower()
    s = re.sub(r"\([^)]*\)", "", s)      # 去 (in)/(out)/(+)/(n)
    s = s.replace(" ", "").replace("-", "").replace("+", "")
    return s


def main():
    d = json.load(open(NORM, encoding="utf-8"))
    nodes, edges = d["nodes"], d["edges"]
    N = {n["id"]: n for n in nodes}

    t4 = [e for e in edges if e["type"] in ("reactant_of", "product_of")]
    rxn_cache = {}
    stat = collections.Counter()
    wrong_samples, both_samples = [], []
    by_conf = collections.defaultdict(collections.Counter)

    for e in t4:
        rid = e["target"]
        r = rxn_cache.get(rid)
        if r is None:
            r = (N.get(rid) or {}).get("props") or {}
            rxn_cache[rid] = r
        eq = r.get("equation")
        lhs, rhs = split_eq(eq) if eq else (None, None)
        if lhs is None:
            stat["no_equation"] += 1
            continue
        part = (N.get(e["source"]) or {}).get("props") or {}
        cand = []
        for k in ("name", "formula"):
            v = part.get(k)
            if v:
                cand.append(canon_tok(v))
        cand = [c for c in cand if c]
        if not cand:
            stat["no_key"] += 1
            continue
        L = {canon_tok(x) for x in lhs}
        R = {canon_tok(x) for x in rhs}
        correct_side = L if e["type"] == "reactant_of" else R
        other_side = R if e["type"] == "reactant_of" else L

        in_c = any(c in correct_side for c in cand)
        in_o = any(c in other_side for c in cand)
        if in_c and not in_o:
            stat["correct"] += 1
        elif in_c and in_o:
            stat["both"] += 1
            if len(both_samples) < 6:
                both_samples.append((part.get("name"), part.get("formula"), eq))
        elif in_o:
            stat["wrong"] += 1
            if len(wrong_samples) < 8:
                wrong_samples.append((e["id"], part.get("name"), part.get("formula"), eq))
        else:
            stat["nomatch"] += 1
        # 按来源细分
        src = part.get("source") or "?"
        if in_c and not in_o:
            by_conf[src]["correct"] += 1
        by_conf[src]["total"] += 1

    tot = len(t4)
    print("=" * 100)
    print("T4 跨列复算天花板（判据 = label 或 formula 归一后匹配）总 %d" % tot)
    print("=" * 100)
    for k in ("correct", "both", "wrong", "nomatch", "no_equation", "no_key"):
        print("   %-12s %6d  (%.1f%%)" % (k, stat[k], 100.0 * stat[k] / tot))
    print("\n   可提升（correct）= %d → T9-i 上限提升估值 %.1f%%"
          % (stat["correct"], 100.0 * (34771 + stat["correct"]) / 40482))

    print("\n按来源：")
    for src, c in sorted(by_conf.items(), key=lambda x: -x[1]["total"]):
        print("   %-16s total=%5d correct=%5d (%.1f%%)"
              % (src, c["total"], c["correct"], 100.0 * c["correct"] / c["total"] if c["total"] else 0))

    print("\nwrong（出现在相反侧 —— 可能真错，需细查）：")
    for s in wrong_samples:
        print("   %-44s label=%-20s formula=%-10s" % (s[0][:44], str(s[1])[:20], str(s[2])[:10]))
        print("         eq=%s" % (s[3][:80]))
    print("\nboth（两侧都出现 —— 歧义，不予采信）：")
    for s in both_samples:
        print("   label=%-20s formula=%-10s eq=%s" % (str(s[0])[:20], str(s[1])[:10], s[2][:70]))


if __name__ == "__main__":
    main()
