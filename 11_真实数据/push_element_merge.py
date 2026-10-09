# -*- coding: utf-8 -*-
"""把 A5「元素节点规范化去重」的增量推送到 Aura。

为什么需要单独一个脚本（而不是复用 robust_aura_loader / push_element_fix）：
本次操作的主体是**删除节点**（20 个元素别名节点）并**改挂它们的边**。
`robust_aura_loader.py` 只写边；`push_element_fix.py` 只能 upsert 节点 + 写/删边，
没有 DETACH DELETE 节点的能力。

步骤（幂等，可重复执行；顺序不可颠倒）：
    1. upsert 15 个规范元素节点的**合并后属性**
       （合并了别名的 group / period / ek_* 等字段，并写入 same_as_aliases）
    2. DETACH DELETE 20 个别名节点
       （S→E 的旧边随之消失，不会悬挂）
    3. MERGE 端点已重定向到规范节点的边
       （必须在删除之后做，才能把被连带删掉的边补回来）

用法（需网络）：
    source env.sh
    python 11_真实数据/push_element_merge.py --dry-run   # 只打印计划，不连库
    python 11_真实数据/push_element_merge.py             # 实际推送

    # 该脚本已通用化为「delta 推送器」：任何含 nodes / delete_nodes / edges /
    # delete_edges 四段的 delta 都可推送（A7 的 phase16_periodic_delta /
    # phase16_trends_delta 亦复用之）：
    python 11_真实数据/push_element_merge.py --delta 06_PoC/etl/neo4j/phase16_periodic_delta.json
"""
import argparse
import json
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DELTA = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase15_elementmerge_delta.json")

# DB 边界属性消毒（Neo4j 不接受 dict / list[dict]；详见 06_PoC/neo4j_props.py 的说明）
sys.path.insert(0, os.path.join(ROOT, "06_PoC"))
import neo4j_props                                        # noqa: E402

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

DEL_NODE_CYPHER = """
UNWIND $rows AS row
MATCH (n:Entity {id: row.id})
DETACH DELETE n
RETURN count(*) AS c
"""

EDGE_CYPHER = """
UNWIND $rows AS row
MATCH (a:Entity {id: row.source}), (b:Entity {id: row.target})
CALL apoc.merge.relationship(a, row.type, {kind: row.kind}, row.props, b) YIELD rel
RETURN count(rel) AS c
"""

DEL_EDGE_CYPHER = """
UNWIND $rows AS row
MATCH (a:Entity {id: row.source})-[r]->(b:Entity {id: row.target})
WHERE type(r) = row.type AND coalesce(r.kind,'') = coalesce(row.kind,'')
DELETE r
RETURN count(r) AS c
"""


def run_batched(driver, cypher, rows, label, batch=200, max_retry=6):
    """返回 (成功行数, 被放弃的行数)。

    ⚠ 「重试 6 次后放弃该批」是**静默降级**的高发点：脚本仍会 exit=0，日志里只有
    几行 [retry] 容易被淹没。故这里显式把放弃的行数返回，由 main 决定退出码。
    """
    total, done, abandoned = len(rows), 0, 0
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
            abandoned += len(seg)
            print(f"  !! {label} 批 {i}-{i+len(seg)} 重试 {max_retry} 次仍失败，放弃 {len(seg)} 条")
        if (i // batch) % 10 == 0:
            print(f"  进度 {label} {done}/{total}")
    return done, abandoned


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不连接数据库")
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--delta", default=DELTA,
                    help="delta 文件路径（默认 phase15 元素合并）")
    a = ap.parse_args()

    with open(a.delta, encoding="utf-8") as f:
        d = json.load(f)
    nodes = d.get("nodes", [])
    del_nodes = d.get("delete_nodes", [])
    edges = d.get("edges", [])
    dels = d.get("delete_edges", [])

    # ⚠ 关键：在推送到 Neo4j 之前消毒属性。
    # Neo4j 只接受 primitive / 同质 primitive 数组；delta 里可能夹带 dict（`alias_sources`）
    # 或 list[dict]（`gnn_type_probs`），本地 NetworkX 不校验 → 推到 Aura 会**整批**失败。
    # 2026-10-09 实测：因缺这一步，2 个边批（600 边）+ 节点批（118 元素的 period/group）被静默放弃。
    nodes = neo4j_props.sanitize_rows(nodes, where="node")
    edges = neo4j_props.sanitize_rows(edges, where="edge")

    print(f"[in] {os.path.relpath(a.delta, ROOT)}")
    print(neo4j_props.report())
    print(f"     upsert 节点 {len(nodes)} / 待删节点 {len(del_nodes)}"
          f" / 新增·改挂边 {len(edges)} / 待删边 {len(dels)}")
    print(f"     phase: {d.get('meta', {}).get('phase')}")
    print(f"     reason: {d.get('meta', {}).get('reason')}")

    if a.dry_run:
        print("\n[dry-run] 将执行：")
        if nodes:
            print(f"  1) MERGE+SET 节点 {len(nodes)} 个")
            for n in nodes[:5]:
                props = n.get("props") or {}
                aliases = props.get("same_as_aliases") or []
                extra = f"（合并别名 {aliases}）" if aliases else ""
                print(f"       {n['id']:18} {len(props)} 字段{extra}")
            if len(nodes) > 5:
                print(f"       … 共 {len(nodes)}")
        if del_nodes:
            print(f"  2) DETACH DELETE 节点 {len(del_nodes)} 个：{del_nodes}")
        if edges:
            print(f"  3) MERGE 边 {len(edges)} 条")
            for e in edges[:5]:
                print(f"       {e['type']:22} {e['source']} -> {e['target']}")
            if len(edges) > 5:
                print(f"       … 共 {len(edges)}")
        if dels:
            print(f"  4) DELETE 陈错边 {len(dels)} 条")
            for e in dels[:5]:
                print(f"       {str(e.get('type')):22} {e.get('source')} -> {e.get('target')}")
            if len(dels) > 5:
                print(f"       … 共 {len(dels)}")
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

        an, bn = run_batched(driver, NODE_CYPHER, nodes, "节点 upsert", a.batch) if nodes else (0, 0)
        print(f"[1/4] 节点 upsert 完成 {an}/{len(nodes)}")
        adn, bdn = (run_batched(driver, DEL_NODE_CYPHER, [{"id": i} for i in del_nodes],
                                "删除节点", 50) if del_nodes else (0, 0))
        print(f"[2/4] 节点 DETACH DELETE 完成 {adn}/{len(del_nodes)}")
        ae, be = run_batched(driver, EDGE_CYPHER, edges, "MERGE 边", a.batch) if edges else (0, 0)
        print(f"[3/4] 边 MERGE 完成 {ae}/{len(edges)}")
        if dels:
            add, bd = run_batched(driver, DEL_EDGE_CYPHER, dels, "删除陈错边", a.batch)
            print(f"[4/4] 边 DELETE 完成 {add}/{len(dels)}")
        else:
            bd = 0

        with driver.session(database=DB) as s:
            n1 = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            e1 = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            left = s.run("MATCH (n) WHERE n.id IN $ids RETURN count(n) AS c",
                         ids=del_nodes).single()["c"]
            elem = s.run("MATCH (n:Element) RETURN count(n) AS c").single()["c"]
        print(f"[after ] Aura: {n1} 节点 / {e1} 边  (Δ{n1-n0:+d} / Δ{e1-e0:+d})")
        print(f"[check ] 别名残留 {left}（应为 0） · Element 节点 {elem}（应为 118）")

        lost = bn + bdn + be + bd
        if lost:
            print("\n" + "!" * 72)
            print(f"!! 本步有 {lost} 条记录因批次反复失败被放弃 → **云端与本地已分叉**！")
            print("!! 看上方 [retry] 行的异常类型；修好后重跑本步即可（MERGE 幂等，可安全重复）。")
            print("!" * 72)
            return 3
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
