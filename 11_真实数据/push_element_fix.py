# -*- coding: utf-8 -*-
"""把元素层修复的增量推送到 Aura。

为什么需要单独一个脚本：`06_PoC/robust_aura_loader.py` **只写边、不写节点**，
而本次修复的主体是**节点属性更新**（氧节点新增、硫节点属性纠正、14 个元素补原子量），
并且还需要**删除**因符号错配而指向错误目标的 25 条 has_element 边。

用法（需网络）：
    source env.sh
    python 11_真实数据/push_element_fix.py --dry-run   # 只打印计划，不连库
    python 11_真实数据/push_element_fix.py             # 实际推送

步骤（幂等，可重复执行）：
    1. upsert 节点：MERGE (n:Entity {id}) SET n += props，并用 APOC 补类型标签
    2. 新增边：MATCH 两端 + apoc.merge.relationship(type, {kind}, props)
    3. 删除陈旧边：按 (source, target, type, kind) 定位并删除
"""
import argparse
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DELTA = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase13_elementfix_delta.json")

URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PW = os.environ.get("NEO4J_PASSWORD")
DB = os.environ.get("NEO4J_DATABASE", "neo4j")

NODE_CYPHER = """
UNWIND $rows AS row
MERGE (n:Entity {id: row.id})
SET n += row.props
WITH n, row
CALL apoc.create.addLabels(n, row.labels) YIELD node
RETURN count(node) AS c
"""

EDGE_CYPHER = """
UNWIND $rows AS row
MATCH (a:Entity {id: row.source}), (b:Entity {id: row.target})
CALL apoc.merge.relationship(a, row.type, {kind: row.kind}, row.props, b) YIELD rel
RETURN count(rel) AS c
"""

DEL_CYPHER = """
UNWIND $rows AS row
MATCH (a:Entity {id: row.source})-[r]->(b:Entity {id: row.target})
WHERE type(r) = row.type AND coalesce(r.kind,'') = coalesce(row.kind,'')
DELETE r
RETURN count(r) AS c
"""


def run_batched(driver, cypher, rows, label, batch=200, max_retry=6):
    total, done = len(rows), 0
    for i in range(0, total, batch):
        seg = rows[i:i + batch]
        for attempt in range(max_retry):
            try:
                with driver.session(database=DB) as s:
                    tx = s.begin_transaction()
                    tx.run(cypher, rows=seg)
                    tx.commit()
                done += len(seg)
                break
            except Exception as e:
                wait = 2 + attempt * 2
                print(f"  [retry {attempt+1}/{max_retry}] {label} 批 {i}-{i+len(seg)} 失败: {e} (等 {wait}s)")
                time.sleep(wait)
        else:
            print(f"  !! {label} 批 {i} 多次重试仍失败，放弃")
        if (i // batch) % 10 == 0:
            print(f"  进度 {label} {done}/{total}")
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不连接数据库")
    ap.add_argument("--batch", type=int, default=200)
    a = ap.parse_args()

    with open(DELTA, encoding="utf-8") as f:
        d = json.load(f)
    nodes, edges, dels = d.get("nodes", []), d.get("edges", []), d.get("delete_edges", [])
    print(f"[in] {os.path.relpath(DELTA, ROOT)}")
    print(f"     节点 upsert {len(nodes)} / 新增边 {len(edges)} / 待删边 {len(dels)}")
    print(f"     meta: {d.get('meta', {}).get('reason')}")

    if a.dry_run:
        print("\n[dry-run] 将执行：")
        print(f"  1) MERGE+SET 节点 {len(nodes)} 个:")
        for n in nodes[:8]:
            print(f"       {n['id']}  labels={n.get('labels')}")
        print(f"     ... 共 {len(nodes)}")
        print(f"  2) 新增边 {len(edges)} 条:")
        for e in edges[:6]:
            print(f"       {e['id']}")
        print(f"  3) 删除边 {len(dels)} 条:")
        for e in dels[:6]:
            print(f"       {e['type']}|{e['source']}|{e['target']} (kind={e.get('kind')})")
        print("\n[dry-run] 未连接数据库，未做任何改动。")
        return 0

    if not PW:
        print("[ERR] 未设置 NEO4J_PASSWORD，请先 source env.sh", file=sys.stderr)
        return 2

    from neo4j import GraphDatabase
    print(f"[conn] {URI} db={DB}")
    driver = GraphDatabase.driver(URI, auth=(USER, PW), database=DB)
    try:
        with driver.session(database=DB) as s:
            n0 = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            e0 = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        print(f"[before] Aura: {n0} 节点 / {e0} 边")

        an = run_batched(driver, NODE_CYPHER, nodes, "节点", a.batch)
        print(f"[1/3] 节点 upsert 完成 {an}/{len(nodes)}")
        ae = run_batched(driver, EDGE_CYPHER, edges, "新增边", a.batch)
        print(f"[2/3] 新增边完成 {ae}/{len(edges)}")
        ad = run_batched(driver, DEL_CYPHER, dels, "删除边", a.batch)
        print(f"[3/3] 删除边完成 {ad}/{len(dels)}")

        with driver.session(database=DB) as s:
            n1 = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            e1 = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            ox = s.run("MATCH (n {id:'EK2:el:O'}) RETURN n.name AS name, n.atomic_weight AS w").single()
        print(f"[after ] Aura: {n1} 节点 / {e1} 边  (Δ{n1-n0:+d} / Δ{e1-e0:+d})")
        print(f"[check ] EK2:el:O -> {dict(ox) if ox else '未找到'}")
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
