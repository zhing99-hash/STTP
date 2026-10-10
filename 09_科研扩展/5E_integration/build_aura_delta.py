# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""
从 Phase 5 三个原始源文件（含正确节点 id）重建推往 Aura 的"加法性"delta。
绕开 phase5_integration.py 的已知 bug（把边 source 错写成管线名）。

产物 _aura_delta.json：
  - 36 个基线 MX 节点（MERGE 幂等，安全）
  - 新增外部节点：19 个 Wikidata(QID) + 5C 的 7 个跨源占位节点
  - 新增边：5.A 19 + 5.B 20 + 5.C 7 = 46 条，source/target 均为真实节点 id
可直接喂给 08_部署包/neo4j/load_neo4j.py --input _aura_delta.json
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.normpath(r"C:\Users\Administrator\WorkBuddy\2026-10-08-10-51-20\STTP\09_科研扩展")
ETL = os.path.normpath(r"C:\Users\Administrator\WorkBuddy\2026-10-08-10-51-20\STTP\06_PoC\etl\neo4j")
OUT = os.path.join(HERE, "_aura_delta.json")


def main():
    nodes = {}      # id -> node dict
    edges = []      # edge dict

    def add_node(nid, labels, props):
        if nid not in nodes:
            nodes[nid] = {"id": nid, "labels": labels, "props": props}

    # ---------- 基线 36 MX 节点（幂等 MERGE） ----------
    base = json.load(open(os.path.join(ETL, "neo4j_ready.json"), encoding="utf-8"))
    for n in base["nodes"]:
        add_node(n["id"], list(n.get("labels", ["Entity"])), dict(n.get("props", {})))

    # ---------- 5.A Wikidata same_as ----------
    a = json.load(open(os.path.join(R, "5A_wikidata", "same_as_edges.json"), encoding="utf-8"))
    for e in a["edges"]:
        src, tgt = e["source"], e["target"]   # MX:... -> WD:Q...
        # 源节点已在基线 36 中
        # 目标 Wikidata 节点需新建
        add_node(tgt, ["Entity", "WikidataEntity"],
                 {"qid": e.get("qid"), "label": e.get("label"),
                  "source": "wikidata", "ntype": "wikidata"})
        edges.append({
            "source": src, "target": tgt, "type": "same_as", "kind": e.get("kind", "SAME_AS"),
            "props": {"confidence": e.get("confidence"),
                      "explicit_or_inferred": e.get("explicit_or_inferred", "inferred"),
                      "alignment_source": e.get("alignment_source"),
                      "qid": e.get("qid"), "label": e.get("label"),
                      "method": e.get("method")},
        })

    # ---------- 5.B GNN 推断边（source/target 均为 MX，节点已存在） ----------
    b = json.load(open(os.path.join(R, "5B_gnn", "phase5_gnn_edges.json"), encoding="utf-8"))
    for e in b["edges"]:
        edges.append({
            "source": e["source"], "target": e["target"],
            "type": e.get("type", "derived_from"), "kind": e.get("kind", "llm_inferred_gnn"),
            "props": {"confidence": e.get("confidence"),
                      "explicit_or_inferred": e.get("explicit_or_inferred", "inferred"),
                      "rationale": e.get("rationale"), "data_source": e.get("data_source")},
        })

    # ---------- 5.C 跨源对齐（仅 high_confidence） ----------
    c = json.load(open(os.path.join(R, "5C_entity_linker", "aligned_candidates.json"), encoding="utf-8"))
    src_label = {"WD": "WikidataEntity", "EK": "ElementEntity", "PB": "PhysicsEntity"}
    for cand in c["candidates"]:
        if not cand.get("high_confidence"):
            continue
        src = cand["source_node"]          # MX:...
        tgt = cand["target_id"]             # wd:Q_pi / ek:molecule:CO2 / pb:newton_second
        tsrc = cand.get("target_source", "WD")
        add_node(tgt, ["Entity", src_label.get(tsrc, "Entity")],
                 {"source": tsrc.lower(), "label": cand.get("target_label"), "ntype": tsrc.lower()})
        edges.append({
            "source": src, "target": tgt, "type": "same_as", "kind": "cross_source_alignment",
            "props": {"confidence": cand.get("score"),
                      "explicit_or_inferred": "inferred",
                      "matched_by": cand.get("matched_by"),
                      "alignment_source": "entity_linker"},
        })

    # ---------- 校验：无悬空 ----------
    node_ids = set(nodes.keys())
    dangling = [e for e in edges if e["source"] not in node_ids or e["target"] not in node_ids]
    assert not dangling, f"悬空边: {dangling[:3]}"

    out = {
        "schema_version": "0.1",
        "note": "Aura additive delta: baseline 36 MX nodes (MERGE) + new WD/EK/PB nodes + 46 new edges",
        "nodes": list(nodes.values()),
        "edges": edges,
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    from collections import Counter
    print(f"[OK] 总节点: {len(nodes)} (基线36 + 新增{len(nodes)-36})")
    print(f"[OK] 总边: {len(edges)} (预期46)")
    print(f"     边类型: {dict(Counter(e['type'] for e in edges))}")
    print(f"     边 kind: {dict(Counter(e['kind'] for e in edges))}")
    print(f"     新增节点来源: {dict(Counter(n['props'].get('source','?') for n in nodes.values() if n['id'] not in {x['id'] for x in base['nodes']}))}")
    print("[ASSERT] 无悬空边 ✅")
    print(f"[OUT] {os.path.relpath(OUT)}")


if __name__ == "__main__":
    main()
