# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""侦察 5：T6 = derived_from | has_unit 的档位构成；以及 T5 的 230 缺口构成。只读。"""
import json, os, collections
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
g = json.load(open(os.path.join(ROOT, "06_PoC", "etl", "normalized.json"), encoding="utf-8"))
edges = g["edges"]

def lv(e):
    return (e.get("props") or {}).get("verification_level")

for label, types in (("T6", ("derived_from", "has_unit")), ("T5", ("has_symbol",))):
    sub = [e for e in edges if e.get("type") in types]
    print("== %s  共 %d ==" % (label, len(sub)))
    c = collections.Counter((e.get("type"), lv(e)) for e in sub)
    for (t, l), n in sorted(c.items(), key=lambda x: -x[1]):
        print("   %-16s %-18s %5d" % (t, l, n))
    print()

# T6 的 model_inferred 抽样
print("== T6 model_inferred 抽样 ==")
k = 0
for e in edges:
    if e.get("type") in ("derived_from", "has_unit") and lv(e) == "model_inferred":
        p = e.get("props") or {}
        print("   %s | %s -> %s | scope=%s kind=%s" % (e["type"], e["source"], e["target"],
              p.get("verification_scope"), e.get("kind")))
        k += 1
        if k >= 15:
            break
print()
print("== T5 (has_symbol) source_asserted / by_construction 抽样 ==")
k = 0
for e in edges:
    if e.get("type") == "has_symbol" and lv(e) in ("source_asserted", "by_construction"):
        p = e.get("props") or {}
        print("   %-16s %s -> %s | scope=%s" % (lv(e), e["source"], e["target"], p.get("verification_scope")))
        k += 1
        if k >= 12:
            break
