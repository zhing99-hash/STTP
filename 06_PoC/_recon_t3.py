# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""侦察 4：T3 = dimensionally_consistent 边，两端点是否可用 dimension_table 真量纲复算。只读。"""
import json, sys, os, collections
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
g = json.load(open(os.path.join(ROOT, "06_PoC", "etl", "normalized.json"), encoding="utf-8"))
nodes, edges = g["nodes"], g["edges"]
N = {n["id"]: n for n in nodes}

import dimension_table as dm

t3 = [e for e in edges if e.get("type") == "dimensionally_consistent"]
print("T3 边总数：%d" % len(t3))

lv = collections.Counter()
for e in t3:
    lv[(e.get("props") or {}).get("verification_level")] += 1
print("档位分布：", dict(lv))

def dim_of(nid):
    n = N.get(nid)
    if not n:
        return None
    p = n.get("props") or {}
    for key in ("qname", "name", "label", "en", "zh"):
        v = p.get(key)
        if isinstance(v, str) and v in dm.DIM:
            return dm.DIM[v]
    return None

ok = bad = unk = 0
rows = []
for e in t3:
    l = (e.get("props") or {}).get("verification_level")
    if l != "model_inferred":
        continue
    da, db = dim_of(e["source"]), dim_of(e["target"])
    sa = (N.get(e["source"], {}).get("props") or {}).get("qname") or (N.get(e["source"], {}).get("props") or {}).get("name")
    sb = (N.get(e["target"], {}).get("props") or {}).get("qname") or (N.get(e["target"], {}).get("props") or {}).get("name")
    if da is None or db is None:
        unk += 1
        rows.append(("UNK", sa, sb, da, db))
    elif da == db:
        ok += 1
        rows.append(("SAME", sa, sb, da, db))
    else:
        bad += 1
        rows.append(("DIFF", sa, sb, da, db))

print("model_inferred 的 T3 边：%d" % (ok + bad + unk))
print("  真量纲相同(可升 rule_checked)：%d" % ok)
print("  真量纲不同(应撤/降级)      ：%d" % bad)
print("  至少一端量纲不可查        ：%d" % unk)
print()
for r in rows[:30]:
    print("  [%s] %s  <->  %s   %s / %s" % r)
