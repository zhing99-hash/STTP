# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Phase 7 合并脚本：base(phase5 + phase6) + 所有 seed_*.json 切片 -> graph_data_phase7.json(可视化) + phase7_aura_delta.json(Aura 增量)。

新增切片只需在 10_种子数据/ 放一个 seed_*.json（节点用 CamelCase 标签 + domain，边类型全小写，
桥接/引用基础库或他切片节点时通过 seed_common.build_and_write 的 extra_external 声明），本脚本自动纳入。
"""
import glob
import json
import os
import sys

sys.path.insert(0, r"C:\Users\Administrator\WorkBuddy\2026-10-08-10-51-20\STTP\06_PoC")
import graph_export

ROOT = r"C:\Users\Administrator\WorkBuddy\2026-10-08-10-51-20\STTP"
SEED = os.path.join(ROOT, r"10_种子数据")
ETL = os.path.join(ROOT, r"06_PoC\etl\neo4j")

ready = json.load(open(os.path.join(ETL, "phase5_neo4j_ready.json"), encoding="utf-8"))
phase6 = json.load(open(os.path.join(SEED, "seed_energy_combustion.json"), encoding="utf-8"))

# 自动扫描所有切片（排除已并入 base 的 phase6 种子）
all_seeds = sorted(glob.glob(os.path.join(SEED, "seed_*.json")))
slice_files = [f for f in all_seeds if os.path.basename(f) != "seed_energy_combustion.json"]
phase7 = [json.load(open(f, encoding="utf-8")) for f in slice_files]
print(f"[scan] 发现 {len(phase7)} 个切片: {[os.path.basename(f) for f in slice_files]}")

base_nodes = ready["nodes"] + phase6["nodes"]
base_edges = ready["edges"] + phase6["edges"]
new_nodes = [n for sl in phase7 for n in sl["nodes"]]
new_edges = [e for sl in phase7 for e in sl["edges"]]

base_ids = {x["id"] for x in base_nodes}
overlap = [x["id"] for x in new_nodes if x["id"] in base_ids]
assert not overlap, f"新切片与 base 节点 id 重叠: {overlap[:5]}"

merged = {"nodes": base_nodes + new_nodes, "edges": base_edges + new_edges}
tmp = os.path.join(ETL, "_phase7_raw.json")
json.dump(merged, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
data = graph_export.build_graph_data(tmp)
out_viz = os.path.join(ROOT, r"06_PoC\graph_data_phase7.json")
json.dump(data, open(out_viz, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
m = data["meta"]
print(f"[OK] {os.path.relpath(out_viz)}: 节点 {m['node_count']} 边 {m['edge_count']}")
print(f"  节点类型: {m['node_types']}")
print(f"  边类型:   {m['edge_types']}")
print(f"  学科分布: {m['subjects']}")
print(f"  悬空端点: {len(m['dangling_endpoints'])}  verified: {m['verified_edges']}")
if m["warnings"]:
    print("  [WARN]", m["warnings"][:3])

# Aura 增量（仅新切片，幂等 MERGE）
delta = {"nodes": new_nodes, "edges": new_edges}
json.dump(delta, open(os.path.join(ETL, "phase7_aura_delta.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print(f"[OK] Aura delta: 节点 {len(new_nodes)} 边 {len(new_edges)} -> phase7_aura_delta.json")
os.remove(tmp)
