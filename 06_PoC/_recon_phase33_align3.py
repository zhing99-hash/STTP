#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""_recon_phase33_align3.py —— 第 23 轮只读侦察 5：T4 判据强度分层 + 假阳风险。
区分 label 命中 / 仅 formula 命中；并检测「同侧 formula 命中但被其它同式物种指代」的歧义。只读。
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
    rxn_cache = {}
    st = collections.Counter()
    for e in t4:
        r = rxn_cache.setdefault(e["target"], (N.get(e["target"]) or {}).get("props") or {})
        eq = r.get("equation")
        lhs, rhs = split_eq(eq) if eq else (None, None)
        if lhs is None:
            st["no_equation"] += 1
            continue
        p = (N.get(e["source"]) or {}).get("props") or {}
        lab = canon(p.get("name") or "")
        fml = canon(p.get("formula") or "")
        L = [canon(x) for x in lhs]
        R = [canon(x) for x in rhs]
        cs, os_ = (L, R) if e["type"] == "reactant_of" else (R, L)
        lh, lw = lab and lab in cs, lab and lab in os_
        fh, fw = fml and fml in cs, fml and fml in os_
        # label 命中
        if lh and not lw:
            st["label_only_ok"] += 1
        elif lh and lw:
            st["label_both"] += 1
        elif lw:
            st["label_wrong"] += 1
        # 仅 formula
        if not lh and fh and not fw:
            st["formula_only_ok"] += 1
            # 同侧该 formula 的重复次数（>1 → 可能指代他物）
            st["formula_only_dup_side"] += (1 if cs.count(fml) > 1 else 0)
        elif not lh and fh and fw:
            st["formula_both"] += 1
        elif not lh and fw:
            st["formula_wrong"] += 1
        if not lh and not fh:
            st["no_match"] += 1
        # 全侧 formula 冲突：参与物 formula 出现在相反侧
    print("总 T4 =", len(t4))
    for k in ("label_only_ok", "label_both", "label_wrong",
              "formula_only_ok", "formula_only_dup_side", "formula_both", "formula_wrong",
              "no_match", "no_equation"):
        print("   %-22s %6d" % (k, st[k]))
    print("\n   强判据（label 命中，无歧义）= %d" % st["label_only_ok"])
    print("   弱判据（仅 formula 命中）= %d （其中同侧重复 %d）"
          % (st["formula_only_ok"], st["formula_only_dup_side"]))
    print("   → 保守可提升 = %d ；乐观可提升 = %d"
          % (st["label_only_ok"], st["label_only_ok"] + st["formula_only_ok"]))


if __name__ == "__main__":
    main()
