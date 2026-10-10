# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""
构建 Phase 5 → Aura 的"加法性"delta 文件：
  以基线 neo4j_ready.json（36 节点/52 边）为参照，
  从 phase5_neo4j_ready.json（36 节点/98 边）中滤出 Aura 上尚不存在的 46 条新边。
输出 _phase5_delta_neo4j_ready.json，可直接喂给 08_部署包/neo4j/load_neo4j.py。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ETL = r"C:\Users\Administrator\WorkBuddy\2026-10-08-10-51-20\STTP\06_PoC\etl\neo4j"

BASE = os.path.join(ETL, "neo4j_ready.json")
PH5 = os.path.join(ETL, "phase5_neo4j_ready.json")
OUT = os.path.join(HERE, "_phase5_delta_neo4j_ready.json")


def edge_key(e):
    return (e["source"], e["target"], e["type"])


def main():
    base = json.load(open(BASE, encoding="utf-8"))
    ph5 = json.load(open(PH5, encoding="utf-8"))

    base_keys = {edge_key(e) for e in base["edges"]}
    new_edges = [e for e in ph5["edges"] if edge_key(e) not in base_keys]
    nodes = ph5["nodes"]  # 全部 36 节点（MERGE 幂等，安全）

    out = {
        "schema_version": ph5.get("schema_version", "0.1"),
        "note": "Phase 5 additive delta: 36 nodes (MERGE) + 46 new edges only",
        "nodes": nodes,
        "edges": new_edges,
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[OK] 基线边: {len(base['edges'])}")
    print(f"[OK] Phase5 总边: {len(ph5['edges'])}")
    print(f"[OK] delta 新边: {len(new_edges)}  (预期 46)")
    print(f"[OK] 节点(幂等MERGE): {len(nodes)}")
    # 新边类型分布
    from collections import Counter
    print("     新增边类型:", dict(Counter(e["type"] for e in new_edges)))
    # 校验：新边引用节点必须都在节点列表内（无悬空）
    node_ids = {n["id"] for n in nodes}
    dangling = [e for e in new_edges if e["source"] not in node_ids or e["target"] not in node_ids]
    assert not dangling, f"悬空边: {dangling}"
    print("[ASSERT] 无悬空边 ✅")
    print(f"[OUT] {os.path.relpath(OUT)}")


if __name__ == "__main__":
    main()
