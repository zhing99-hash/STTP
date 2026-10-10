# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""把 phase12_aura_delta.json 拆成：1 个全节点块 + N 个边块（每块 ~5000 边），便于分连接推送避免 Aura 超时。"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "etl", "neo4j")
SRC = os.path.join(D, "phase12_aura_delta.json")
CHUNK = 5000

data = json.load(open(SRC, encoding="utf-8"))
nodes = data["nodes"]
edges = data["edges"]

# 节点块（无重复，apoc.merge 幂等）
json.dump({"nodes": nodes, "edges": []},
          open(os.path.join(D, "pb_chunk_nodes.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
n_chunks = (len(edges) + CHUNK - 1) // CHUNK
for i in range(n_chunks):
    seg = edges[i * CHUNK:(i + 1) * CHUNK]
    json.dump({"nodes": [], "edges": seg},
              open(os.path.join(D, f"pb_chunk_edges_{i:02d}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
print(f"节点块: 1 (节点 {len(nodes)}); 边块: {n_chunks} (每块<= {CHUNK}, 共边 {len(edges)})")
