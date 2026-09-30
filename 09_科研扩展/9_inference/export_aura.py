# -*- coding: utf-8 -*-
"""从 Aura 反向导出当前全量图 -> graph_data_phase12.json（权威 viz 源）。"""
import json, os, sys
from neo4j import GraphDatabase

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # 01tuopu
OUT = os.path.join(ROOT, "06_PoC", "graph_data_phase12.json")
TMP = os.path.join(ROOT, "06_PoC", "_phase12_export_raw.json")

import os
URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PW = os.environ.get("NEO4J_PASSWORD", "formula_graph_2026")
DB = os.environ.get("NEO4J_DATABASE", "neo4j")


def main():
    d = GraphDatabase.driver(URI, auth=(USER, PW), database=DB)
    nodes, edges = [], []
    with d.session(database=DB) as s:
        for rec in s.run("MATCH (n:Entity) RETURN n.id AS id, labels(n) AS labels, properties(n) AS p"):
            nodes.append({"id": rec["id"], "labels": list(rec["labels"]),
                          "props": dict(rec["p"])})
        for rec in s.run(
            "MATCH (a)-[r]->(b) RETURN a.id AS s, b.id AS t, type(r) AS ty, "
            "r.kind AS kind, properties(r) AS p"):
            p = dict(rec["p"])
            eid = p.get("id") or f"{rec['s']}->{rec['t']}:{rec['ty']}"
            edges.append({"id": eid, "source": rec["s"], "target": rec["t"],
                          "type": rec["ty"], "kind": rec["kind"] or "real",
                          "props": p})
    d.close()
    raw = {"nodes": nodes, "edges": edges}
    json.dump(raw, open(TMP, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    sys.path.insert(0, os.path.join(ROOT, "06_PoC"))
    import graph_export
    viz = graph_export.build_graph_data(TMP)
    meta = viz["meta"]
    json.dump(viz, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[export] Aura -> {OUT}: 节点 {meta['node_count']} / 边 {meta['edge_count']}")
    print(f"         学科: {meta['subjects']}")
    print(f"         悬空端点: {len(meta['dangling_endpoints'])}")
    os.remove(TMP)


if __name__ == "__main__":
    main()
