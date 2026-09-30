# -*- coding: utf-8 -*-
"""Phase 8.A — 构建 PubChem 真实分子数据接入产物（同 build_phase8.py 结构）。"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(ROOT, "11_真实数据", "pubchem_mol_raw.json")
VIZ_OUT = os.path.join(HERE, "graph_data_phase9.json")
AURA_DELTA = os.path.join(HERE, "etl", "neo4j", "phase9_aura_delta.json")

sys.path.insert(0, HERE)
import graph_export


def main():
    assert os.path.exists(RAW), f"缺少 {RAW}"
    print("=" * 64)
    print("  Phase 8.A · PubChem 真实分子数据接入产物构建")
    print("=" * 64)
    viz = graph_export.build_graph_data(RAW)
    meta = viz["meta"]
    os.makedirs(os.path.dirname(VIZ_OUT), exist_ok=True)
    with open(VIZ_OUT, "w", encoding="utf-8") as f:
        json.dump(viz, f, ensure_ascii=False, indent=2)
    print(f"  [viz] {VIZ_OUT}: 节点 {meta['node_count']} / 边 {meta['edge_count']}")
    print(f"        学科: {meta['subjects']}  节点类型: {meta['node_types']}  边类型: {meta['edge_types']}")
    print(f"        悬空端点(预期0): {len(meta['dangling_endpoints'])}")

    os.makedirs(os.path.dirname(AURA_DELTA), exist_ok=True)
    shutil.copyfile(RAW, AURA_DELTA)
    with open(RAW, encoding="utf-8") as f:
        raw = json.load(f)
    print(f"  [aura] 增量 {AURA_DELTA}: 节点 {len(raw['nodes'])} / 边 {len(raw['edges'])}")


if __name__ == "__main__":
    main()
