# -*- coding: utf-8 -*-
"""
Phase 8 — 构建 ElementKG 真实数据接入产物
=========================================
读取 11_真实数据/elementkg_raw.json（真实元素图谱，既有权威 raw 格式），
- 经 graph_export.build_graph_data 生成前端友好的 06_PoC/graph_data_phase8.json
- 复制 raw 为 Aura 增量 06_PoC/etl/neo4j/phase8_aura_delta.json（load_neo4j 直接消费）
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # STTP/06_PoC
ROOT = os.path.dirname(HERE)                                # STTP
RAW = os.path.join(ROOT, "11_真实数据", "elementkg_raw.json")
VIZ_OUT = os.path.join(HERE, "graph_data_phase8.json")
AURA_DELTA = os.path.join(HERE, "etl", "neo4j", "phase8_aura_delta.json")

sys.path.insert(0, HERE)
import graph_export


def main():
    assert os.path.exists(RAW), f"缺少 {RAW}"
    print("=" * 64)
    print("  Phase 8 · ElementKG 真实数据接入产物构建")
    print("=" * 64)
    print(f"  源(raw): {RAW}")

    viz = graph_export.build_graph_data(RAW)
    meta = viz["meta"]
    os.makedirs(os.path.dirname(VIZ_OUT), exist_ok=True)
    with open(VIZ_OUT, "w", encoding="utf-8") as f:
        json.dump(viz, f, ensure_ascii=False, indent=2)
    print(f"  [viz] 写出 {VIZ_OUT}: 节点 {meta['node_count']} / 边 {meta['edge_count']}")
    print(f"        学科分布: {meta['subjects']}")
    print(f"        节点类型: {meta['node_types']}")
    print(f"        边类型:   {meta['edge_types']}")
    print(f"        悬空端点(同骨架桥接, 预期): {len(meta['dangling_endpoints'])}")

    # Aura 增量 = raw 本身（load_neo4j 直接消费 nodes/labels/edges/type）
    os.makedirs(os.path.dirname(AURA_DELTA), exist_ok=True)
    shutil.copyfile(RAW, AURA_DELTA)
    with open(RAW, encoding="utf-8") as f:
        raw = json.load(f)
    print(f"  [aura] 复制增量 {AURA_DELTA}: 节点 {len(raw['nodes'])} / 边 {len(raw['edges'])}")
    print("=" * 64)
    print("  下一步: load_neo4j.py --input phase8_aura_delta.json 推 Aura (幂等 MERGE)")


if __name__ == "__main__":
    main()
