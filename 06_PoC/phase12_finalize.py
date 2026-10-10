# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Phase 12 终态整合（一体化、幂等、可断点重跑）：
  1) 健壮加载 PhysicsBabel 5000-eq 增量边到 Aura（robust_aura_loader.write_edges 小批+重连）；
  2) LLM 复核同步：VERIFY 9 条升级 typed 桥(verified)，REJECT 76 条删除 related_to 伪影；
  3) 从 Aura 反向导出权威全量图 graph_data_phase12.json。
独立进程顺序执行，避免并发写冲突。
"""
import json, time, os, sys
from neo4j import GraphDatabase

import os
URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PW = os.environ.get("NEO4J_PASSWORD", "formula_graph_2026")
DB = os.environ.get("NEO4J_DATABASE", "neo4j")

HERE = os.path.dirname(os.path.abspath(__file__))
DELTA = os.path.join(HERE, "etl", "neo4j", "phase12_aura_delta.json")
REVIEW = os.path.join(HERE, "..", "09_科研扩展", "9_inference", "llm_review_85.json")
REVIEW = os.path.normpath(REVIEW)
OUT = os.path.join(HERE, "graph_data_phase12.json")

sys.path.insert(0, HERE)
from robust_aura_loader import write_edges

MERGE_CY = """
UNWIND $rows AS row
MATCH (a:Entity {id: row.source}), (b:Entity {id: row.target})
CALL apoc.merge.relationship(a, row.type, {kind: row.kind}, row.props, b) YIELD rel
SET rel += row.props
RETURN count(rel) AS c
"""
DELETE_CY = """
UNWIND $rows AS row
MATCH (a:Entity {id: row.source})-[r:related_to]->(b:Entity {id: row.target})
DELETE r RETURN count(r) AS c
"""

def run_batches(driver, cypher, rows, batch=100, max_retry=6):
    n = len(rows); done = 0
    for i in range(0, n, batch):
        seg = rows[i:i + batch]; ok = False
        for attempt in range(max_retry):
            try:
                with driver.session(database=DB) as s:
                    tx = s.begin_transaction(); tx.run(cypher, rows=seg); tx.commit()
                ok = True; done += len(seg); break
            except Exception as e:
                w = 2 + attempt * 2
                print(f"  [retry {attempt+1}] 批 {i} 失败: {e} (等 {w}s)"); time.sleep(w)
        if not ok:
            print(f"  !! 批 {i} 失败，放弃")
        if (i // batch) % 10 == 0:
            print(f"  进度 {done}/{n}")
    return done

def main():
    driver = GraphDatabase.driver(URI, auth=(USER, PW), database=DB)
    try:
        # 1) 边加载（节点已在，edges-only）
        delta = json.load(open(DELTA, encoding="utf-8"))
        edges = delta["edges"]
        print(f"[1] 加载 {len(edges)} 条 PhysicsBabel 边...")
        c = write_edges(driver, edges, batch=2000)
        print(f"    完成 {c}/{len(edges)}")

        # 2) LLM 复核同步
        rv = json.load(open(REVIEW, encoding="utf-8"))
        typed = []
        for v in rv["verified"]:
            typed.append({"id": f"llmrv:{v['role']}:{v['source']}:{v['target']}",
                          "source": v["source"], "target": v["target"], "type": v["role"],
                          "kind": "llm_review",
                          "props": {"confidence": 0.9, "explicit_or_inferred": "inferred",
                                    "verified": True, "verification_gate": "R-BIO",
                                    "source": "LLM-review", "rationale": v["reason"]}})
        dels = [{"source": r["source"], "target": r["target"]} for r in rv["verified"] + rv["rejected"]]
        print(f"[2] 升级 {len(typed)} 条 VERIFY 桥 + 删除 {len(dels)} 条 related_to...")
        c1 = run_batches(driver, MERGE_CY, typed)
        c2 = run_batches(driver, DELETE_CY, dels)
        print(f"    升级 {c1}/{len(typed)}  删除 {c2}/{len(dels)}")
    finally:
        driver.close()
    print("[DONE] Phase 12 边加载 + 复核同步完成（导出请用 export_aura.py）")

if __name__ == "__main__":
    main()
