#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""_recon_phase34_rulepreview.py —— 第 24 轮只读侦察 6：同义词判据全量预演 + 残差清算。

在「缓存已就绪」后运行。输出：
  A. Rhea 残差在多种判据变体下的 upgrade / WRONG / both 数（**全量**，非抽样）。
  B. 全残差（含 ElementKG2.0 / curated_seed）按「不可独立复算的显式理由」清算，验证**零未归类**。
只读，绝不写图。
"""
from __future__ import annotations
import collections, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
NORM = os.path.join(HERE, "etl", "normalized.json")
SYN = os.path.join(ROOT, "11_真实数据", "chebi_synonym_cache.json")

_RXN_PLUS = re.compile(r" \+ ")
_RXN_PAREN = re.compile(r"\([^)]*\)")


def canon(x):
    s = str(x).lower()
    s = _RXN_PAREN.sub("", s)
    return s.replace(" ", "").replace("-", "").replace("+", "")


def split_eq(eq):
    if not eq:
        return None, None
    e = str(eq)
    for sep in (" = ", " <=> ", " -> ", " → ", " => "):
        if sep in e:
            lhs, rhs = e.split(sep, 1)
            f = lambda s: [x.strip() for x in _RXN_PLUS.split(s) if x.strip()]
            return f(lhs), f(rhs)
    return None, None


def main():
    d = json.load(open(NORM, encoding="utf-8"))
    N = {n["id"]: n for n in d["nodes"]}
    syn = json.load(open(SYN, encoding="utf-8")) if os.path.exists(SYN) else {}
    t4 = [e for e in d["edges"] if e["type"] in ("reactant_of", "product_of")]
    resid = [e for e in t4
             if (e.get("props") or {}).get("verification_scope") != "equation_species_cross_source"]
    rh = [e for e in resid if ((N.get(e["target"]) or {}).get("props") or {}).get("source") == "Rhea"]

    print("=== A. Rhea 残差 %d 条，判据变体预演 ===" % len(rh))
    variants = collections.defaultdict(collections.Counter)
    samples = collections.defaultdict(list)
    no_eq = 0
    for e in rh:
        p = (N.get(e["source"]) or {}).get("props") or {}
        rp = (N.get(e["target"]) or {}).get("props") or {}
        lhs, rhs = split_eq(rp.get("equation"))
        if lhs is None:
            no_eq += 1
            continue
        lab = canon(p.get("name") or "")
        fml = canon(p.get("formula") or "")
        sy = [canon(s) for s in (syn.get(p.get("chebi_id")) or {}).get("synonyms", []) if s]
        L = [canon(x) for x in lhs]
        R = [canon(x) for x in rhs]
        cs, os_ = (L, R) if e["type"] == "reactant_of" else (R, L)
        for vname, names in (
                ("V1_label", {lab} if lab else set()),
                ("V2_lab+syn", {lab} if lab else set() | set()),
                ("V3_lab+syn+fml", None)):
            if vname == "V2_lab+syn":
                names = ({lab} if lab else set()) | set(sy)
            if vname == "V3_lab+syn+fml":
                names = ({lab} if lab else set()) | set(sy) | ({fml} if fml else set())
            names.discard("")
            if not names:
                variants[vname]["no_ident"] += 1
                continue
            hc = any(n in cs for n in names)
            ho = any(n in os_ for n in names)
            if hc and ho:
                variants[vname]["both"] += 1
            elif ho:
                variants[vname]["WRONG"] += 1
                if len(samples[vname]) < 6:
                    samples[vname].append((p.get("name"), rp.get("equation"), e["type"]))
            elif hc:
                variants[vname]["upgrade"] += 1
            else:
                variants[vname]["nomatch"] += 1
    print("  无方程可切分的 Rhea 残差 = %d" % no_eq)
    for vname in ("V1_label", "V2_lab+syn", "V3_lab+syn+fml"):
        c = variants[vname]
        tot = sum(c.values())
        print("  %-14s upgrade=%-5d WRONG=%-4d both=%-4d nomatch=%-5d (n=%d)"
              % (vname, c["upgrade"], c["WRONG"], c["both"], c["nomatch"], tot))
        if samples[vname]:
            print("       WRONG 样例:", samples[vname][:3])

    # --- B. 全残差清算（零未归类断言） ---
    print("\n=== B. 全残差 %d 条按「不可独立复算理由」清算 ===" % len(resid))
    reason = collections.Counter()
    for e in resid:
        p = (N.get(e["source"]) or {}).get("props") or {}
        rp = (N.get(e["target"]) or {}).get("props") or {}
        rsrc = rp.get("source") or rp.get("src") or "-"
        eq = rp.get("equation")
        if rsrc == "ElementKG2.0":
            reason["同源：ElementKG2.0 无方程（SMILES 亦同源）"] += 1
        elif rsrc.startswith("curated_seed"):
            reason["同源：curated_seed 策划数据（方程与节点同源）"] += 1
        elif rsrc == "Rhea":
            if p.get("is_generic"):
                reason["Rhea：参与物为泛称类（无具体对应物）"] += 1
            elif p.get("is_polymer"):
                reason["Rhea：参与物为聚合物/残基占位"] += 1
            elif eq and "[" in eq:
                reason["Rhea：方程含 [占位复合物]（不可判定）"] += 1
            else:
                reason["Rhea：命名/质子化变体，无第二源】"] += 1
        else:
            reason["其他来源未归类"] += 1
    for k, v in reason.most_common():
        print("   %-46s %6d" % (k, v))
    unclass = reason.get("其他来源未归类", 0)
    print("   → 未归类 =", unclass, "（应为 0）")

    # --- C. 若采用 V2，最终 T9-i 估算 ---
    up = variants["V2_lab+syn"]["upgrade"]
    base_i, base_n = 37564, 40484
    print("\n=== C. 估算（V2 判据） ===")
    print("   新增升级 ≈ %d → T9-i ≈ %d/%d = %.1f%%"
          % (up, base_i + up, base_n, 100.0 * (base_i + up) / base_n))


if __name__ == "__main__":
    main()
