# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""merge_seed_delta.py —— 把 Phase 7 新切片 append-only 并入权威图并重建可视化快照。

背景：`06_PoC/etl/normalized.json` 是本地权威原始图（ETL 格式），近期各轮
（元素修复 / 命名空间去重 / 周期表修复 / 元素性质）均采用「就地修补 + graph_export 重建快照」
的方式推进，本脚本沿用同一模式，但把「切片并入」固化成可复跑的一条命令。

做四件事：
  1. 备份 normalized.json（normalized.before_<tag>.json）
  2. 把 seed_*.json 归一成 ETL 格式后 append 进 normalized.json
     · 节点 labels 统一为 ``[具体类型, "Entity"]``（与 ETL normalize() 产物一致）
     · 边 id 由 ``src->tgt[type]``（seed_common 约定）归一为 ``type|source|target``（快照/Aura 约定）
  3. 用 graph_export.build_graph_data() 重建可视化快照 graph_data_phase<N>.json
  4. 产出 Aura 增量 delta（四段：meta / nodes / delete_nodes / edges / delete_edges）

用法：
    python 06_PoC/merge_seed_delta.py --seeds 10_种子数据/seed_chem_equilibrium.json \
        10_种子数据/seed_math_numbertheory.json --phase 17          # dry-run（默认）
    python 06_PoC/merge_seed_delta.py ... --phase 17 --apply        # 落盘
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import sys
import collections
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import graph_export  # noqa: E402

NORMALIZED = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
ETL_NEO4J = os.path.join(ROOT, "06_PoC", "etl", "neo4j")


def _norm_node(n: dict) -> dict:
    """seed 节点 -> ETL 节点（labels 顺序 [具体类型, Entity]，props 补 id）。"""
    labels = list(n.get("labels") or [])
    if "Entity" in labels:
        labels = [x for x in labels if x != "Entity"]
    # 具体类型排在前，Entity 末尾
    out_labels = labels + ["Entity"] if labels else ["Entity"]
    props = dict(n.get("props") or {})
    props.setdefault("id", n["id"])
    return {"id": n["id"], "labels": out_labels, "props": props}


def _norm_edge(e: dict) -> dict:
    """seed 边 -> ETL 边（id 归一为 type|source|target）。"""
    out = dict(e)
    out["id"] = "%s|%s|%s" % (e.get("type") or "", e.get("source") or "", e.get("target") or "")
    out.setdefault("kind", (e.get("props") or {}).get("kind") or "")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", nargs="+", required=True, help="要并入的 seed_*.json")
    ap.add_argument("--phase", default="17", help="快照阶段号（graph_data_phase<N>.json）")
    ap.add_argument("--tag", default="slices", help="备份后缀 normalized.before_<tag>.json")
    ap.add_argument("--reason", default="Phase 7 广度切片补齐（化学平衡 / 数论），append-only 并入权威图",
                    help="delta meta 的 reason 文案")
    ap.add_argument("--label", default="Phase7b-seeds", help="delta meta 的阶段标签前缀")
    ap.add_argument("--apply", action="store_true", help="真正落盘（默认 dry-run）")
    args = ap.parse_args()

    src = json.load(open(NORMALIZED, encoding="utf-8"))
    base_nodes, base_edges = src["nodes"], src["edges"]
    base_ids = {n["id"] for n in base_nodes}
    base_eids = {e.get("id") for e in base_edges}

    new_nodes, new_edges, per_slice = [], [], []
    for p in args.seeds:
        d = json.load(open(p, encoding="utf-8"))
        ns = [_norm_node(n) for n in d.get("nodes", [])]
        es = [_norm_edge(e) for e in d.get("edges", [])]
        per_slice.append((os.path.basename(p), len(ns), len(es)))
        new_nodes += ns
        new_edges += es

    # ---- 校验（并入前）----
    dup_node = [n["id"] for n in new_nodes if n["id"] in base_ids]
    assert not dup_node, f"节点 id 与既有图冲突: {dup_node[:8]}"
    seen = set()
    dup_in_new = [n["id"] for n in new_nodes if n["id"] in seen or seen.add(n["id"])]
    assert not dup_in_new, f"新节点内部 id 重复: {dup_in_new[:8]}"
    dup_edge = [e["id"] for e in new_edges if e["id"] in base_eids]
    assert not dup_edge, f"边 id 与既有图冲突: {dup_edge[:8]}"
    all_ids = base_ids | {n["id"] for n in new_nodes}
    dangling = [e["id"] for e in new_edges if e["source"] not in all_ids or e["target"] not in all_ids]
    assert not dangling, f"悬空边: {dangling[:8]}"
    selfloop = [e["id"] for e in new_edges if e["source"] == e["target"]]
    assert not selfloop, f"自环边: {selfloop[:8]}"

    merged = {"nodes": base_nodes + new_nodes, "edges": base_edges + new_edges}

    print("=" * 70)
    print("merge_seed_delta  phase=%s  (%s)" % (args.phase, "APPLY" if args.apply else "DRY-RUN"))
    print("=" * 70)
    for name, nn, ne in per_slice:
        print("  + %-38s 节点 %3d  边 %3d" % (name, nn, ne))
    print("  并入前: %d 节点 / %d 边" % (len(base_nodes), len(base_edges)))
    print("  并入后: %d 节点 / %d 边  (+%d / +%d)"
          % (len(merged["nodes"]), len(merged["edges"]), len(new_nodes), len(new_edges)))

    # ---- 快照 + 指标 ----
    tmp = os.path.join(ETL_NEO4J, "_merge_seed_tmp.json")
    json.dump(merged, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    data = graph_export.build_graph_data(tmp)
    m = data["meta"]
    print("  快照 meta: 节点 %d / 边 %d  悬空 %d  verified %d  id 撞号修复 %s"
          % (m["node_count"], m["edge_count"], len(m["dangling_endpoints"]),
             m.get("verified_edges", 0), m.get("duplicate_edge_ids_fixed", "?")))
    print("  学科分布: %s" % m["subjects"])
    subj = m["subjects"]
    print("  MathConcept 节点数: %d" % sum(1 for x in data["nodes"] if x["type"] == "MathConcept"))

    # 跨学科边统计（源/目标 subject 不同）
    nsub = {x["id"]: x["subject"] for x in data["nodes"]}
    cross = [e for e in data["edges"]
             if nsub.get(e["source"]) and nsub.get(e["target"])
             and nsub[e["source"]] != nsub[e["target"]]]
    print("  跨学科边（两端 subject 不同）: %d" % len(cross))

    if not args.apply:
        os.remove(tmp)
        print("\n[dry-run] 未写盘。加 --apply 落盘。")
        return 0

    # ---- 落盘 ----
    bak = os.path.join(ROOT, "06_PoC", "etl", "normalized.before_%s.json" % args.tag)
    shutil.copy2(NORMALIZED, bak)
    json.dump(merged, open(NORMALIZED, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n[OK] 备份 -> %s" % os.path.relpath(bak, ROOT))
    print("[OK] 写入 -> %s" % os.path.relpath(NORMALIZED, ROOT))

    snap = os.path.join(HERE, "graph_data_phase%s.json" % args.phase)
    json.dump(data, open(snap, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("[OK] 快照 -> %s  (%.2f MB)" % (os.path.relpath(snap, ROOT),
                                          os.path.getsize(snap) / 1024 / 1024))
    os.remove(tmp)

    os.makedirs(ETL_NEO4J, exist_ok=True)
    delta = {
        "meta": {
            "phase": "%s-%s" % (args.label, args.phase),
            "source": "06_PoC/merge_seed_delta.py",
            "reason": args.reason,
            "seeds": [os.path.basename(p) for p in args.seeds],
            "generated_at": datetime.now().isoformat(),
            "node_count": len(new_nodes), "edge_count": len(new_edges),
        },
        "nodes": new_nodes, "delete_nodes": [], "edges": new_edges, "delete_edges": [],
    }
    dp = os.path.join(ETL_NEO4J, "phase%s_seed_delta.json" % args.phase)
    json.dump(delta, open(dp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("[OK] Aura delta -> %s  (节点 %d / 边 %d)"
          % (os.path.relpath(dp, ROOT), len(new_nodes), len(new_edges)))
    print("\n提示：把 .env 的 GRAPH_DATA_FILE 指向 graph_data_phase%s.json 并重启 viz。" % args.phase)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
