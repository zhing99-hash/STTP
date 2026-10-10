# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Phase 9 · 推理生成层产出构建。
把全量真实图(graph_data_full)与 180 条 GNN/符号校验跨域边合并，
产出前端 viz 图(graph_data_phase11.json) 与 Aura 增量(phase11_aura_delta.json)。
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FULL = os.path.join(HERE, "graph_data_full.json")
NEW_RAW = os.path.join(ROOT, "09_科研扩展", "9_inference", "phase9_raw.json")
TMP_RAW = os.path.join(HERE, "_phase11_combined_raw.json")
VIZ_OUT = os.path.join(HERE, "graph_data_phase11.json")
AURA_DELTA = os.path.join(HERE, "etl", "neo4j", "phase11_aura_delta.json")

sys.path.insert(0, HERE)
import graph_export


def main():
    assert os.path.exists(FULL), f"缺少 {FULL}"
    assert os.path.exists(NEW_RAW), f"缺少 {NEW_RAW}（先跑 gnn_infer.py）"
    full = json.load(open(FULL, encoding="utf-8"))
    newraw = json.load(open(NEW_RAW, encoding="utf-8"))

    # viz 格式 -> raw 格式（节点）
    raw_nodes = []
    for n in full["nodes"]:
        raw_nodes.append({
            "id": n["id"],
            "labels": list(n.get("labels") or ["Entity"]),
            "props": dict(n.get("attrs") or {}),
        })

    # viz 格式 -> raw 格式（边）
    raw_edges = []
    for e in full["edges"]:
        raw_edges.append({
            "id": e["id"], "source": e["source"], "target": e["target"],
            "type": e.get("type"), "kind": e.get("kind"),
            "props": {
                "confidence": e.get("confidence"),
                "explicit_or_inferred": e.get("explicit_or_inferred"),
                "verified": bool(e.get("verified", False)),
                "verification_gate": e.get("gate"),
                "rationale": e.get("rationale"),
                "domain": e.get("domain"),
                "evidence": e.get("evidence"),
            },
        })
    # 追加 Phase 9 新边
    raw_edges.extend(newraw["edges"])
    combined = {"nodes": raw_nodes, "edges": raw_edges}
    with open(TMP_RAW, "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)

    viz = graph_export.build_graph_data(TMP_RAW)
    meta = viz["meta"]
    with open(VIZ_OUT, "w", encoding="utf-8") as f:
        json.dump(viz, f, ensure_ascii=False, indent=2)
    print(f"  [viz] {VIZ_OUT}: 节点 {meta['node_count']} / 边 {meta['edge_count']}")
    print(f"        学科: {meta['subjects']}")
    print(f"        新增边类型: {dict([(k, v) for k, v in meta['edge_types'].items() if k in ('dimensionally_consistent','has_quantity','related_to')])}")
    print(f"        悬空端点(预期0): {len(meta['dangling_endpoints'])}  警告: {meta['warnings']}")

    os.makedirs(os.path.dirname(AURA_DELTA), exist_ok=True)
    shutil.copyfile(NEW_RAW, AURA_DELTA)
    print(f"  [aura] 增量 {AURA_DELTA}: 节点 {len(newraw['nodes'])} / 边 {len(newraw['edges'])}")
    os.remove(TMP_RAW)


if __name__ == "__main__":
    main()
