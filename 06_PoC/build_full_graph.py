# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""合并 phase7(骨架)+phase8(真实元素)+phase9(PubChem分子)+phase10(ElementKG2.0真实化学核心) 为全量可视化图（按 id 去重）。"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
p7 = os.path.join(HERE, "graph_data_phase7.json")
p8 = os.path.join(HERE, "graph_data_phase8.json")
p9 = os.path.join(HERE, "graph_data_phase9.json")
p10 = os.path.join(HERE, "graph_data_phase10.json")
out = os.path.join(HERE, "graph_data_full.json")

for p in (p7, p8, p9, p10):
    assert os.path.exists(p), f"缺少 {p}"

with open(p7, encoding="utf-8") as f:
    a = json.load(f)
with open(p8, encoding="utf-8") as f:
    b = json.load(f)
with open(p9, encoding="utf-8") as f:
    c = json.load(f)
with open(p10, encoding="utf-8") as f:
    d = json.load(f)

nodes, seen = [], {}
for n in a["nodes"] + b["nodes"] + c["nodes"] + d["nodes"]:
    if n["id"] not in seen:
        seen[n["id"]] = True
        nodes.append(n)
edges, eseen = [], {}
for e in a["edges"] + b["edges"] + c["edges"] + d["edges"]:
    key = (e.get("id") or f'{e["source"]}->{e["target"]}:{e["type"]}')
    if key not in eseen:
        eseen[key] = True
        edges.append(e)

from collections import Counter
nt = Counter(n["type"] for n in nodes)
et = Counter(e["type"] for e in edges)
full = {
    "schema": "formula-graph-view/v1",
    "nodes": nodes,
    "edges": edges,
    "meta": {
        "source": "phase7(骨架)+phase8(ElementKG真实元素)+phase9(PubChem真实分子)+phase10(ElementKG2.0真实化学核心)",
        "node_count": len(nodes), "edge_count": len(edges),
        "node_types": dict(nt), "edge_types": dict(et),
    },
}
with open(out, "w", encoding="utf-8") as f:
    json.dump(full, f, ensure_ascii=False, indent=2)
print(f"[OK] 全量图 {out}: 节点 {len(nodes)} / 边 {len(edges)}")
print("  节点类型:", dict(nt))
print("  边类型:  ", dict(et))
