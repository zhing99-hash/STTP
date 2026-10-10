# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""
Phase 5.E — 集成脚本（主协调方）
把 Phase 5.A (Wikidata same_as) + 5.B (GNN 推断) + 5.C (跨源对齐) 三路产出
合并到现有 36 节点 / 52 边图谱，输出 Phase 5 增强版数据集 + 可视化 JSON。

输入（各子 agent 产出，缺则跳过并告警）：
  - 06_PoC/etl/neo4j/neo4j_ready.json               (基线，不可改)
  - 09_科研扩展/5A_wikidata/same_as_edges.json      (WD 对齐)
  - 09_科研扩展/5B_gnn/phase5_gnn_edges.json        (GNN 推断，结构 {"edges":[...]})
  - 09_科研扩展/5C_entity_linker/aligned_candidates.json (跨源对齐，结构 {"candidates":[...]})

输出：
  - 06_PoC/etl/neo4j/phase5_neo4j_ready.json        (合并后完整数据集，不破坏原文件)
  - 06_PoC/graph_data_phase5.json                  (前端可视化用)
  - 09_科研扩展/5E_integration/phase5_metrics.json  (集成指标)
"""
import json
import os
from datetime import datetime

ROOT = r"C:\Users\Administrator\WorkBuddy\2026-10-08-10-51-20\STTP"
BASE = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "neo4j_ready.json")
OUT_NEO4J = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase5_neo4j_ready.json")
OUT_VIZ = os.path.join(ROOT, "06_PoC", "graph_data_phase5.json")
OUT_METRICS = os.path.join(ROOT, "09_科研扩展", "5E_integration", "phase5_metrics.json")

A = os.path.join(ROOT, "09_科研扩展", "5A_wikidata", "same_as_edges.json")
B = os.path.join(ROOT, "09_科研扩展", "5B_gnn", "phase5_gnn_edges.json")
C = os.path.join(ROOT, "09_科研扩展", "5C_entity_linker", "aligned_candidates.json")


def load_json(path):
    if not os.path.exists(path):
        print(f"  [WARN] 缺失: {os.path.relpath(path, ROOT)} (跳过)", flush=True)
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_list(obj):
    """兼容多种结构：直接 list，或 {'edges':[...]} / {'candidates':[...]} / {'results':[...]} / {'data':[...]} / {'items':[...]}"""
    if obj is None:
        return []
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in ("edges", "candidates", "results", "data", "items", "same_as_edges"):
            if key in obj and isinstance(obj[key], list):
                return obj[key]
    return []


def node_ref(item, *keys):
    for k in keys:
        v = item.get(k)
        if v:
            return v
    return None


def main():
    print("=" * 70, flush=True)
    print("Phase 5.E 集成：合并 5.A + 5.B + 5.C 到基线图谱", flush=True)
    print("=" * 70, flush=True)

    base = load_json(BASE)
    if base is None:
        raise SystemExit("[FAIL] 基线数据集缺失，无法集成")

    nodes = list(base.get("nodes", []))
    edges = list(base.get("edges", []))
    n0, e0 = len(nodes), len(edges)
    print(f"  [BASE] 节点 {n0} / 边 {e0}", flush=True)

    node_ids = {n.get("id") for n in nodes}
    edge_keys = {f"{e.get('source')}|{e.get('target')}|{e.get('type')}" for e in edges}
    metrics = {"base": {"nodes": n0, "edges": e0}, "added": {}, "warnings": []}

    # ---------- 5.A Wikidata same_as ----------
    wa = load_json(A)
    if wa:
        lst = extract_list(wa)
        added, skipped = 0, 0
        for e in lst:
            src = node_ref(e, "source", "source_id")
            tgt = node_ref(e, "target", "target_id", "wikidata_qid")
            if not src or not tgt:
                continue
            key = f"{src}|{tgt}|same_as"
            if key in edge_keys:
                skipped += 1
                continue
            # WD 目标节点（真实 QID）需新建并加入节点列表
            if tgt not in node_ids:
                nodes.append({
                    "id": tgt,
                    "labels": ["Entity", "WikidataEntity"],
                    "props": {"qid": e.get("qid"), "label": e.get("label"),
                              "source": "wikidata", "ntype": "wikidata"},
                })
                node_ids.add(tgt)
            edges.append({
                "id": e.get("id", f"WD:same_as_{added:04d}"),
                "source": src,
                "target": tgt,
                "type": "same_as",
                "kind": e.get("kind", "wikidata_alignment"),
                "confidence": float(e.get("confidence", 0.8)),
                "explicit_or_inferred": "inferred",
                "data_source": "Phase5.Wikidata",
                "props": e.get("props", {}),
            })
            edge_keys.add(key)
            added += 1
        metrics["added"]["wikidata_same_as"] = added
        print(f"  [5.A] same_as 边 +{added}（跳过 {skipped} 重复）", flush=True)

    # ---------- 5.B GNN 推断 ----------
    gb = load_json(B)
    if gb:
        lst = extract_list(gb)
        added, skipped = 0, 0
        for e in lst:
            src = node_ref(e, "source", "source_id")
            tgt = node_ref(e, "target", "target_id")
            etype = e.get("type", "derived_from")
            if not src or not tgt:
                continue
            key = f"{src}|{tgt}|{etype}"
            if key in edge_keys:
                skipped += 1
                continue
            edges.append({
                "id": e.get("id", f"GN:inf_{added:04d}"),
                "source": src,
                "target": tgt,
                "type": etype,
                "kind": e.get("kind", "gnn_inferred"),
                "confidence": float(e.get("confidence", 0.5)),
                "explicit_or_inferred": "inferred",
                "data_source": "Phase5.GNN",
                "props": {"rationale": e.get("rationale", "")},
            })
            edge_keys.add(key)
            added += 1
        metrics["added"]["gnn_inferred"] = added
        print(f"  [5.B] GNN 推断边 +{added}（跳过 {skipped} 重复）", flush=True)

    # ---------- 5.C 跨源对齐 ----------
    cc = load_json(C)
    if cc:
        lst = extract_list(cc)
        added, skipped = 0, 0
        SRC_LABEL = {"WD": "WikidataEntity", "EK": "ElementEntity", "PB": "PhysicsEntity"}
        for item in lst:
            src = node_ref(item, "source_node", "source", "source_id")
            tgt = node_ref(item, "target_id", "target", "target_node")
            score = float(item.get("score", 0))
            if not src or not tgt:
                continue
            if score < 0.7:  # 高置信才入图
                continue
            key = f"{src}|{tgt}|same_as"
            if key in edge_keys:
                skipped += 1
                continue
            # 跨源目标节点（wd:/ek:/pb: 占位）需新建并加入节点列表
            if tgt not in node_ids:
                tsrc = item.get("target_source", "WD")
                nodes.append({
                    "id": tgt,
                    "labels": ["Entity", SRC_LABEL.get(tsrc, "Entity")],
                    "props": {"source": tsrc.lower(), "label": item.get("target_label"),
                              "ntype": tsrc.lower()},
                })
                node_ids.add(tgt)
            edges.append({
                "id": item.get("id", f"AL:align_{added:04d}"),
                "source": src,
                "target": tgt,
                "type": "same_as",
                "kind": "cross_source_alignment",
                "confidence": score,
                "explicit_or_inferred": "inferred",
                "data_source": "Phase5.EntityLinker",
                "props": {"matched_by": item.get("matched_by", item.get("high_confidence", ""))},
            })
            edge_keys.add(key)
            added += 1
        metrics["added"]["cross_source_same_as"] = added
        print(f"  [5.C] 跨源 same_as 边 +{added}（跳过 {skipped} 低置信/重复）", flush=True)

    n1, e1 = len(nodes), len(edges)
    metrics["final"] = {"nodes": n1, "edges": e1}
    metrics["delta"] = {"nodes": n1 - n0, "edges": e1 - e0}

    # ---------- 写出 ----------
    out = {
        "schema_version": base.get("schema_version", "0.1"),
        "generated_at": datetime.now().isoformat(),
        "phase": "5",
        "nodes": nodes,
        "edges": edges,
    }
    with open(OUT_NEO4J, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"  [OUT] 合并数据集: {os.path.relpath(OUT_NEO4J, ROOT)} ({n1} 节点 / {e1} 边)", flush=True)

    # 简单可视化导出（节点 + 边，前端可直接用）
    viz = {
        "nodes": [
            {
                "id": n.get("id"),
                "labels": n.get("labels", ["Entity"]),
                "type": (n.get("labels") or ["Entity"])[0],
                "props": n.get("props", {}),
            }
            for n in nodes
        ],
        "edges": [
            {
                "id": e.get("id"),
                "source": e.get("source"),
                "target": e.get("target"),
                "type": e.get("type"),
                "confidence": e.get("confidence", 1.0),
                "explicit_or_inferred": e.get("explicit_or_inferred", "explicit"),
                "data_source": e.get("data_source", ""),
            }
            for e in edges
        ],
    }
    with open(OUT_VIZ, "w", encoding="utf-8") as f:
        json.dump(viz, f, ensure_ascii=False, indent=2)
    print(f"  [OUT] 可视化 JSON: {os.path.relpath(OUT_VIZ, ROOT)}", flush=True)

    with open(OUT_METRICS, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 70, flush=True)
    print(f"集成完成：节点 {n0}→{n1} (Δ+{n1-n0})，边 {e0}→{e1} (Δ+{e1-e0})", flush=True)
    print("=" * 70, flush=True)
    print("[END] exit=0", flush=True)


if __name__ == "__main__":
    main()
