# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Phase 9 任务2 · 合并全量图与 PhysicsBabel 真实方程 -> graph_data_phase12.json + Aura 增量。"""
import json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FULL = os.path.join(HERE, "graph_data_phase11b.json")  # 已含 Phase9 推断 + 任务1 重筛
RAW = os.path.join(ROOT, "11_真实数据", "physicsbabel_raw.json")
TMP = os.path.join(HERE, "_phase12_raw.json")
VIZ = os.path.join(HERE, "graph_data_phase12.json")
AURA = os.path.join(HERE, "etl", "neo4j", "phase12_aura_delta.json")

sys.path.insert(0, HERE)
import graph_export


def main():
    full = json.load(open(FULL, encoding="utf-8"))
    raw = json.load(open(RAW, encoding="utf-8"))
    raw_nodes = [{"id": n["id"], "labels": list(n.get("labels") or ["Entity"]),
                  "props": dict(n.get("attrs") or {})} for n in full["nodes"]]
    raw_edges = []
    for e in full["edges"]:
        raw_edges.append({"id": e["id"], "source": e["source"], "target": e["target"],
                          "type": e.get("type"), "kind": e.get("kind"),
                          "props": {"confidence": e.get("confidence"),
                                    "explicit_or_inferred": e.get("explicit_or_inferred"),
                                    "verified": bool(e.get("verified", False)),
                                    "verification_gate": e.get("gate"),
                                    "rationale": e.get("rationale"),
                                    "domain": e.get("domain"),
                                    "evidence": e.get("evidence")}})
    raw_nodes.extend(raw["nodes"])
    raw_edges.extend(raw["edges"])
    combined = {"nodes": raw_nodes, "edges": raw_edges}
    json.dump(combined, open(TMP, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    viz = graph_export.build_graph_data(TMP)
    meta = viz["meta"]
    json.dump(viz, open(VIZ, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[viz] {VIZ}: 节点 {meta['node_count']} / 边 {meta['edge_count']}")
    print(f"      学科: {meta['subjects']}")
    print(f"      新增边: dimensionally_consistent={meta['edge_types'].get('dimensionally_consistent')}, has_symbol={meta['edge_types'].get('has_symbol')}")
    print(f"      悬空端点(预期0): {len(meta['dangling_endpoints'])}  警告: {meta['warnings']}")
    os.makedirs(os.path.dirname(AURA), exist_ok=True)
    shutil.copyfile(RAW, AURA)
    print(f"[aura] 增量 {AURA}: 节点 {len(raw['nodes'])} / 边 {len(raw['edges'])}")
    # 同步为新的 canonical full（后续阶段基于此构建）
    shutil.copyfile(VIZ, FULL)
    print(f"[full] 已更新 {FULL} 为当前全量图")
    os.remove(TMP)


if __name__ == "__main__":
    main()
