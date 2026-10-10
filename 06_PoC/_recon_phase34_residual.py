#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""_recon_phase34_residual.py —— 第 24 轮只读侦察 1：T4 残差全量构成。
以「已落库的 Phase 33 判级」为准（铁律 #44），把 T4 残差（仍 equation_sidedness 的边）
按 target 反应节点逐条分解：
  - no_equation      : 反应节点无 equation 串（ElementKG2.0 来源）
  - both_ambiguity   : label/formula 两侧都出现（歧义）
  - nomatch_generic  : 参与物是泛称（name/formula 均不命中方程任一物种）
  - nomatch_formula  : 有 formula 但方程物种是文本名（无可比形式）
并统计：残差反应节点数 / 每条反应的参与物数 / 来源分布 / chebi-id 覆盖。只读。
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


def canon(x):
    s = str(x).lower()
    s = re.sub(r"\([^)]*\)", "", s)
    return s.replace(" ", "").replace("-", "").replace("+", "")


def main():
    d = json.load(open(NORM, encoding="utf-8"))
    N = {n["id"]: n for n in d["nodes"]}
    t4 = [e for e in d["edges"] if e["type"] in ("reactant_of", "product_of")]

    # 1) 按落库 scope 分层
    by_scope = collections.Counter((e.get("props") or {}).get("verification_scope") for e in t4)
    print("=== T4 边（%d 条）按落库 verification_scope ===" % len(t4))
    for k, v in by_scope.most_common():
        print("   %-34s %6d" % (k, v))

    resid = [e for e in t4
             if (e.get("props") or {}).get("verification_scope") != "equation_species_cross_source"]
    print("\n残差（未升 cross_source）= %d 条" % len(resid))

    # 2) 残差逐条分解
    st = collections.Counter()
    resid_rxns = set()
    node_kind = collections.Counter()   # 残差边的 source 节点类型
    rxn_src = collections.Counter()     # 反应节点来源
    per_rxn_resid = collections.Counter()
    for e in resid:
        resid_rxns.add(e["target"])
        per_rxn_resid[e["target"]] += 1
        p = (N.get(e["source"]) or {}).get("props") or {}
        rp = (N.get(e["target"]) or {}).get("props") or {}
        rxn_src[rp.get("source") or rp.get("src") or "-"] += 1
        eq = rp.get("equation")
        if not eq or " = " not in eq:
            st["no_equation"] += 1
            continue
        lhs, rhs = split_eq(eq)
        lab = canon(p.get("name") or "")
        fml = canon(p.get("formula") or "")
        L = [canon(x) for x in lhs]
        R = [canon(x) for x in rhs]
        cs, os_ = (L, R) if e["type"] == "reactant_of" else (R, L)
        lh, lw = bool(lab) and lab in cs, bool(lab) and lab in os_
        fh, fw = bool(fml) and fml in cs, bool(fml) and fml in os_
        if lh and lw:
            st["both_label"] += 1
        elif fh and fw:
            st["both_formula"] += 1
        elif lw or fw:
            st["wrong_side"] += 1
        elif not lab and not fml:
            st["nomatch_empty_ident"] += 1   # 参与物连 name/formula 都没有
        elif fml and not any(re.match(r"^[A-Za-z(]", x) for x in cs):
            st["nomatch_eq_text_only"] += 1  # 方程物种全是文本名，formula 无可比对象
        else:
            st["nomatch_generic"] += 1        # 泛称（如 "an alcohol" / "protein"）
    print("\n=== 残差逐条分解 ===")
    for k, v in st.most_common():
        print("   %-24s %6d" % (k, v))

    # 3) 剩余反应节点 —— 它们是否都在 Rhea 之外
    print("\n=== 残差涉及的反应节点 ===")
    print("   唯一反应节点 = %d" % len(resid_rxns))
    print("   反应来源分布 =", dict(rxn_src))
    print("   每反应残差边数 top:", per_rxn_resid.most_common(5))

    # 4) 残差边的 source 节点是否带 ChEBI 标识（决定能否跨库对齐）
    has_chebi = 0
    for e in resid:
        p = (N.get(e["source"]) or {}).get("props") or {}
        if any("CHEBI" in str(v) or str(k).lower().startswith("chebi")
               for k, v in p.items()):
            has_chebi += 1
    print("\n残差边 source 带 ChEBI 标识 =", has_chebi, "/", len(resid))

    # 5) 直接看几条残差样例
    print("\n=== 残差样例（前 8）===")
    shown = 0
    for e in resid:
        p = (N.get(e["source"]) or {}).get("props") or {}
        rp = (N.get(e["target"]) or {}).get("props") or {}
        print("   [%s] %s | name=%r formula=%r" %
              (e["type"], e["source"], p.get("name"), p.get("formula")))
        print("        rxn=%s eq=%r" % (e["target"], rp.get("equation")))
        shown += 1
        if shown >= 8:
            break


if __name__ == "__main__":
    main()
