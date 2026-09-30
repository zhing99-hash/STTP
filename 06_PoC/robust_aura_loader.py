# -*- coding: utf-8 -*-
"""健壮 Aura 边加载器：边按小批显式事务提交 + 断连重连重试，避免大事务在 flaky 连接下整体回滚。
用法: python robust_aura_loader.py --input <delta.json> [--edges-only]
"""
import json, time, argparse
from neo4j import GraphDatabase

import os
URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PW = os.environ.get("NEO4J_PASSWORD", "formula_graph_2026")
DB = os.environ.get("NEO4J_DATABASE", "neo4j")

EDGE_CYPHER = """
UNWIND $rows AS row
MATCH (a:Entity {id: row.source}), (b:Entity {id: row.target})
CALL apoc.merge.relationship(a, row.type, {kind: row.kind}, row.props, b) YIELD rel
RETURN count(rel) AS c
"""

def write_edges(driver, edges, batch=100, max_retry=6):
    n = len(edges)
    committed = 0
    for i in range(0, n, batch):
        seg = edges[i:i + batch]
        ok = False
        for attempt in range(max_retry):
            try:
                with driver.session(database=DB) as s:
                    tx = s.begin_transaction()
                    tx.run(EDGE_CYPHER, rows=seg)
                    tx.commit()
                ok = True
                committed += len(seg)
                break
            except Exception as e:
                wait = 2 + attempt * 2
                print(f"  [retry {attempt+1}/{max_retry}] 边批 {i}-{i+len(seg)} 失败: {e} (等 {wait}s)")
                time.sleep(wait)
        if not ok:
            print(f"  !! 边批 {i} 多次重试仍失败，放弃该批")
        if (i // batch) % 20 == 0:
            print(f"  进度 边 {committed}/{n}")
    return committed

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--edges-only", action="store_true")
    ap.add_argument("--batch", type=int, default=100)
    a = ap.parse_args()

    data = json.load(open(a.input, encoding="utf-8"))
    edges = data.get("edges", [])
    print(f"[LOAD] 读入边 {len(edges)} (edges-only={a.edges_only})")
    driver = GraphDatabase.driver(URI, auth=(USER, PW), database=DB)
    try:
        c = write_edges(driver, edges, batch=a.batch)
        print(f"[DONE] 已提交边 {c}/{len(edges)}")
    finally:
        driver.close()

if __name__ == "__main__":
    main()
