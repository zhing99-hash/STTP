"""
Phase 2 图库化真实执行演示（嵌入式 Cypher 引擎 Kuzu，无需 Java/Neo4j 服务器）。

作用：
- 读取 ETL 管道产出的 normalized.json（与 Neo4j 导入 CSV 同源）
- 在 Kuzu 嵌入式图库中建表、灌入节点/边
- 运行若干 Cypher 查询，证明「图库化 + 查询接口」端到端可用

说明：生产环境按既定架构使用 Neo4j 服务器（需要 JVM，本沙箱未安装）。
Kuzu 作为本地开发态的嵌入式 Cypher 引擎，用于在此环境给出真实执行证据；
其 Cypher 语法与 Neo4j 高度兼容，查询可平移。

幂等性（A9）
-----------
DDL 一律 `IF NOT EXISTS`，数据写入一律 `MERGE`（节点按 id、边按 (起点, 类型, 终点)），
因此**反复运行不会报 `Node already exists`，也不会重复灌数据**。
默认在既有库上增量对齐；需要从零重建时显式加 `--fresh`（会先删库）。

运行：python load_kuzu_demo.py            # 幂等：复用/对齐既有库
      python load_kuzu_demo.py --fresh    # 先删库再全量重建
依赖：kuzu
"""
import argparse
import os
import json
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
NORMALIZED = os.path.join(HERE, "..", "06_PoC", "etl", "normalized.json")
DB_DIR = os.path.abspath(os.path.join(HERE, "..", "06_PoC", "etl", "kuzu_db"))


def rel_name(etype):
    """边类型 -> Kuzu 合法关系表名（大写、非字母数字转下划线）。"""
    out = []
    for ch in etype.lower():
        out.append(ch if ch.isalnum() else "_")
    return "REL_" + "".join(out).upper().strip("_")


def main():
    import kuzu

    ap = argparse.ArgumentParser(description="Kuzu 嵌入式图库演示（幂等）")
    ap.add_argument("--fresh", action="store_true",
                    help="先删除既有库再全量重建；默认在既有库上幂等对齐")
    ap.add_argument("--db", default=DB_DIR, help=f"图库路径（默认 {DB_DIR}）")
    ap.add_argument("--limit", type=int, default=0, help="只灌前 N 个节点/N 条边（调试用，0=全部）")
    args = ap.parse_args()
    db_dir = os.path.abspath(args.db)

    if not os.path.exists(NORMALIZED):
        raise SystemExit(f"[ERROR] 未找到 {NORMALIZED}，请先运行 etl_pipeline.py")

    data = json.load(open(NORMALIZED, encoding="utf-8"))
    nodes = data["nodes"]
    edges = data["edges"]
    if args.limit:
        keep = {n["id"] for n in nodes[: args.limit]}
        nodes = nodes[: args.limit]
        edges = [e for e in edges if e["source"] in keep and e["target"] in keep]

    # --fresh：显式删库（Kuzu 在 Windows 上可能以单目录或单文件形式存储）
    if args.fresh and os.path.exists(db_dir):
        if os.path.isdir(db_dir):
            shutil.rmtree(db_dir)
        else:
            os.remove(db_dir)
        print(f"[0] --fresh：已清除旧库 {db_dir}")

    db = kuzu.Database(db_dir)
    conn = kuzu.Connection(db)

    print("[1] 建立节点表 Node（IF NOT EXISTS）...")
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS Node("
        "id STRING, name STRING, ntype STRING, latex STRING, "
        "informal STRING, confidence DOUBLE, labels STRING, "
        "PRIMARY KEY(id))"
    )

    # 为每个出现的边类型建一张关系表，统一引用 Node
    edge_types = sorted({e["type"] for e in edges})
    print(f"[2] 为 {len(edge_types)} 种边类型建立关系表（IF NOT EXISTS）: {edge_types}")
    for et in edge_types:
        rname = rel_name(et)
        conn.execute(
            f"CREATE REL TABLE IF NOT EXISTS {rname}("
            f"FROM Node TO Node, kind STRING, confidence DOUBLE, "
            f"explicit_or_inferred STRING, source STRING)"
        )

    print(f"[3] 对齐 {len(nodes)} 个节点（MERGE by id）...")
    for n in nodes:
        p = n.get("props", {})
        labels = n.get("labels", [])
        labels_str = ";".join(labels) if isinstance(labels, list) else str(labels)
        conn.execute(
            "MERGE (x:Node {id:$id}) "
            "SET x.name=$name, x.ntype=$ntype, x.latex=$latex, "
            "x.informal=$informal, x.confidence=$conf, x.labels=$labels",
            {
                "id": n["id"],
                "name": str(p.get("name", n.get("local_id", ""))),
                "ntype": str(p.get("type", "")),
                "latex": str(p.get("latex", "")),
                "informal": str(p.get("informal", "")),
                "conf": float(p.get("confidence", 1.0)),
                "labels": labels_str,
            },
        )

    print(f"[4] 对齐 {len(edges)} 条边（MERGE by 起点/类型/终点）...")
    for e in edges:
        p = e.get("props", {})
        rname = rel_name(e["type"])
        conn.execute(
            f"MATCH (a:Node {{id:$sid}}), (b:Node {{id:$eid}}) "
            f"MERGE (a)-[r:{rname}]->(b) "
            f"SET r.kind=$kind, r.confidence=$conf, "
            f"r.explicit_or_inferred=$eoi, r.source=$src",
            {
                "sid": e["source"],
                "eid": e["target"],
                "kind": str(p.get("kind", "")),
                "conf": float(p.get("confidence", 1.0)),
                "eoi": str(p.get("explicit_or_inferred", "")),
                "src": str(p.get("source", "")),
            },
        )

    def run(cypher, n=8):
        res = conn.execute(cypher)
        rows = []
        while res.has_next():
            rows.append(res.get_next())
            if len(rows) >= n:
                break
        return rows

    print("\n===== Cypher 查询结果（真实执行）=====")
    print("Q1 节点总数:")
    print("  ", run("MATCH (n:Node) RETURN count(n)")[0][0])

    print("Q2 边总数:")
    print("  ", run("MATCH ()-[r]->() RETURN count(r)")[0][0])

    print("Q3 所有定理节点 (ntype='theorem'):")
    for r in run("MATCH (n:Node) WHERE n.ntype='theorem' RETURN n.id, n.latex"):
        print("  ", r[0], "->", r[1][:50])

    print("Q4 低置信度/推断边 (confidence < 1.0):")
    for r in run("MATCH (a)-[r]->(b) WHERE r.confidence < 1.0 RETURN a.id, b.id, r.confidence, r.explicit_or_inferred"):
        print("  ", r[0], "-[", r[3], "conf=", round(r[2], 2), "]->", r[1])

    print("Q5 多跳追溯(定长2跳, derived_from): gauss_bonnet 的两跳下游")
    try:
        res = conn.execute(
            "MATCH (a:Node {id:$s})-[:REL_DERIVED_FROM]->(b)-[:REL_DERIVED_FROM]->(c) "
            "RETURN a.id, b.id, c.id LIMIT 5",
            {"s": "MX:thm:gauss_bonnet"},
        )
        while res.has_next():
            r = res.get_next()
            print("  ", r[0], "->", r[1], "->", r[2])
    except Exception as ex:
        print("  (路径查询示例):", ex)

    print("Q6 按学科/类型统计节点数:")
    for r in run("MATCH (n:Node) RETURN n.ntype, count(*) ORDER BY count(*) DESC"):
        print("  ", r[0], ":", r[1])

    print("\n[Done] Kuzu 嵌入式图库演示完成（生产态对应 Neo4j 服务器）。")


if __name__ == "__main__":
    main()
