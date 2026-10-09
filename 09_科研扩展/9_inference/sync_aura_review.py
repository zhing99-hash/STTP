# -*- coding: utf-8 -*-
"""把 LLM 复核结果(llm_review_85.json)同步到 Aura：
  - VERIFY: 新建已验证 typed 桥(reactant_of/product_of, verified=True)，并删除原 related_to 边
  - REJECT: 删除原 related_to 边(剔除 GNN 伪影)
采用小批显式事务 + 断连重连重试，匹配 robust_aura_loader 的健壮性。
"""
import json, time, argparse
from neo4j import GraphDatabase

import os
URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PW = os.environ.get("NEO4J_PASSWORD", "formula_graph_2026")
DB = os.environ.get("NEO4J_DATABASE", "neo4j")

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW = json.load(open(HERE + "\\llm_review_85.json", encoding="utf-8"))

verify = REVIEW["verified"]
reject = REVIEW["rejected"]

# 构造 typed 边（VERIFY）
typed_rows = []
for v in verify:
    role = v["role"]
    typed_rows.append({
        "id": f"llmrv:{role}:{v['source']}:{v['target']}",
        "source": v["source"], "target": v["target"], "type": role, "kind": "llm_review",
        "props": {"confidence": 0.9, "explicit_or_inferred": "inferred", "verified": True,
                  "verification_gate": "R-BIO", "source": "LLM-review", "rationale": v["reason"]},
    })
# 删除源（related_to 边，按端点定位）
del_rows = []
for r in verify + reject:
    del_rows.append({"source": r["source"], "target": r["target"]})

MERGE_CY = """
UNWIND $rows AS row
MATCH (a:Entity {id: row.source}), (b:Entity {id: row.target})
CALL apoc.merge.relationship(a, row.type, {kind: row.kind}, row.props, b) YIELD rel
RETURN count(rel) AS c
"""
DELETE_CY = """
UNWIND $rows AS row
MATCH (a:Entity {id: row.source})-[r:related_to]->(b:Entity {id: row.target})
DELETE r
RETURN count(r) AS c
"""

def run_batches(driver, cypher, rows, batch=100, max_retry=6):
    n = len(rows); done = 0
    for i in range(0, n, batch):
        seg = rows[i:i + batch]; ok = False
        for attempt in range(max_retry):
            try:
                with driver.session(database=DB) as s:
                    tx = s.begin_transaction()
                    tx.run(cypher, rows=seg)
                    tx.commit()
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
        print(f"[MERGE] 新建 {len(typed_rows)} 条已验证桥(VERIFY)...")
        c = run_batches(driver, MERGE_CY, typed_rows)
        print(f"  完成 {c}/{len(typed_rows)}")
        print(f"[DELETE] 删除 {len(del_rows)} 条 related_to 伪影/旧边...")
        c2 = run_batches(driver, DELETE_CY, del_rows)
        print(f"  完成 {c2}/{len(del_rows)}")
    finally:
        driver.close()

if __name__ == "__main__":
    main()
