# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Phase 8.B — ElementKG 2.0 全量 10M CSV 真实化学核心子集接入产物构建。"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(ROOT, "11_真实数据", "elementkg10m_raw.json")
VIZ_OUT = os.path.join(HERE, "graph_data_phase10.json")
AURA_DELTA = os.path.join(HERE, "etl", "neo4j", "phase10_aura_delta.json")

sys.path.insert(0, HERE)
import graph_export


def main():
    assert os.path.exists(RAW), f"缺少 {RAW}（先跑 elementkg10m_ingest.py）"
    print("=" * 64)
    print("  Phase 8.B · ElementKG 2.0 真实化学核心子集接入产物构建")
    print("=" * 64)
    viz = graph_export.build_graph_data(RAW)
    meta = viz["meta"]
    os.makedirs(os.path.dirname(VIZ_OUT), exist_ok=True)
    with open(VIZ_OUT, "w", encoding="utf-8") as f:
        json.dump(viz, f, ensure_ascii=False, indent=2)
    print(f"  [viz] {VIZ_OUT}: 节点 {meta['node_count']} / 边 {meta['edge_count']}")
    print(f"        学科: {meta['subjects']}  节点类型: {meta['node_types']}  边类型: {meta['edge_types']}")
    print(f"        悬空端点(预期0): {len(meta['dangling_endpoints'])}  警告: {meta['warnings']}")

    os.makedirs(os.path.dirname(AURA_DELTA), exist_ok=True)
    shutil.copyfile(RAW, AURA_DELTA)
    with open(RAW, encoding="utf-8") as f:
        raw = json.load(f)
    print(f"  [aura] 增量 {AURA_DELTA}: 节点 {len(raw['nodes'])} / 边 {len(raw['edges'])}")


if __name__ == "__main__":
    main()
