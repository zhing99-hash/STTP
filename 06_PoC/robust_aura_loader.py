# -*- coding: utf-8 -*-
"""健壮 Aura 边加载器：边按小批显式事务提交 + 断连重连重试，避免大事务在 flaky 连接下整体回滚。
用法: python robust_aura_loader.py --input <delta.json> [--edges-only]
"""
import json, time, argparse, os, sys
from neo4j import GraphDatabase

# DB 边界属性消毒（Neo4j 不接受 dict / list[dict]；详见本目录 neo4j_props.py）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import neo4j_props                                       # noqa: E402

URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PW = os.environ.get("NEO4J_PASSWORD", "formula_graph_2026")
DB = os.environ.get("NEO4J_DATABASE", "neo4j")

EDGE_CYPHER = """
UNWIND $rows AS row
MATCH (a:Entity {id: row.source}), (b:Entity {id: row.target})
CALL apoc.merge.relationship(a, row.type, {kind: row.kind}, row.props, b) YIELD rel
SET rel += row.props
RETURN count(rel) AS c
"""

def write_edges(driver, edges, batch=100, max_retry=6):
    """返回 (成功条数, 被放弃条数)。放弃批次是「静默降级」高发点，故显式上抛给退出码。"""
    n = len(edges)
    committed = 0
    abandoned = 0
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
            abandoned += len(seg)
            print(f"  !! 边批 {i}-{i+len(seg)} 重试 {max_retry} 次仍失败，放弃 {len(seg)} 条")
        if (i // batch) % 20 == 0:
            print(f"  进度 边 {committed}/{n}")
    return committed, abandoned

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--edges-only", action="store_true")
    ap.add_argument("--batch", type=int, default=100)
    a = ap.parse_args()

    data = json.load(open(a.input, encoding="utf-8"))
    edges = data.get("edges", [])
    # ⚠ 消毒：`gnn_type_probs` 这类 list[dict] 会让整批被 Aura 拒绝（曾丢 600 条带类型边）
    edges = neo4j_props.sanitize_rows(edges, where="edge")
    print(f"[LOAD] 读入边 {len(edges)} (edges-only={a.edges_only})")
    print(neo4j_props.report())
    driver = GraphDatabase.driver(URI, auth=(USER, PW), database=DB)
    try:
        c, lost = write_edges(driver, edges, batch=a.batch)
        print(f"[DONE] 已提交边 {c}/{len(edges)}")
        if lost:
            print("\n" + "!" * 72)
            print(f"!! 有 {lost} 条边因批次反复失败被放弃 → **云端与本地已分叉**！")
            print("!! 看上方 [retry] 行的异常类型；修好后重跑本步即可（MERGE 幂等）。")
            print("!" * 72)
            return 3
    finally:
        driver.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
