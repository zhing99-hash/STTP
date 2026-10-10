# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""侦察 4b：用 dm.dim_of 真复算 T3 的 model_inferred 边。只读。"""
import json, sys, os, collections
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
g = json.load(open(os.path.join(ROOT, "06_PoC", "etl", "normalized.json"), encoding="utf-8"))
nodes, edges = g["nodes"], g["edges"]
N = {n["id"]: n for n in nodes}
import dimension_table as dm

def nm(nid):
    p = (N.get(nid) or {}).get("props") or {}
    for k in ("name", "qname", "label", "en", "zh", "symbol"):
        v = p.get(k)
        if isinstance(v, str) and v:
            return v
    return None

t3 = [e for e in edges if e.get("type") == "dimensionally_consistent"]
same = diff = unk = 0
rows = []
for e in t3:
    l = (e.get("props") or {}).get("verification_level")
    if l != "model_inferred":
        continue
    sa, sb = nm(e["source"]), nm(e["target"])
    da = dm.dim_of(sa) if sa else None
    db = dm.dim_of(sb) if sb else None
    if da is None or db is None:
        unk += 1; tag = "UNK "
    elif dm._norm(da) == dm._norm(db):
        same += 1; tag = "SAME"
    else:
        diff += 1; tag = "DIFF"
    rows.append((tag, sa, da, sb, db))

print("model_inferred T3 边 %d 条：" % len(rows))
print("  真量纲相同 → 可升 rule_checked : %d" % same)
print("  真量纲不同 → 应撤/降级         : %d" % diff)
print("  量纲不可查 → 不可判定          : %d" % unk)
print()
for t, sa, da, sb, db in rows:
    print("  [%s] %-24s %-34s  <->  %-24s %s" % (t, sa, str(da), sb, str(db)))

print()
print("== 顺带：31 条 rule_checked 的 T3 边抽样（看它们为何达标）==")
k = 0
for e in t3:
    if (e.get("props") or {}).get("verification_level") == "rule_checked":
        print("  %-22s <-> %-22s  scope=%s" % (nm(e["source"]), nm(e["target"]),
              (e.get("props") or {}).get("verification_scope")))
        k += 1
        if k >= 12:
            break
