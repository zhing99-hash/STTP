# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""
公式知识图谱 · Neo4j 加载器（部署包内嵌版本）
================================================================

把图谱数据写入 Neo4j。两种来源任选：
  - 默认输入 ``neo4j/neo4j_ready.json``（36 节点 / 52 边，引用完整，0 悬空）；
  - 通过 ``--input`` 指定任意 JSON 路径覆盖默认。

三种运行模式
------------
1. ``--admin-import``：仅**打印**可直接执行的 ``neo4j-admin database import full``
   命令（指向 ``neo4j/`` 下 CSV），不做连接。
2. 默认（在线）：用 neo4j Python 驱动连接后：
     (a) 建约束 / 索引（Entity 全局唯一约束 + ntype/domain 索引）；
     (b) APOC 可用则用 APOC MERGE，否则按首个 Label 原生 MERGE；
     (c) 跑 6 类校验查询并打印结果。
3. 无驱动 / 无实例：友好提示 + **非致命退出（exit 0）**，不崩。

环境变量
--------
  NEO4J_URI       默认 bolt://localhost:7687
  NEO4J_USER      默认 neo4j
  NEO4J_PASSWORD  默认 formula_graph_2026
  NEO4J_DATABASE  默认 None（使用服务器默认库）

运行
----
    cd 08_部署包/neo4j          # 进入部署包目录
    set NEO4J_PASSWORD=yourpassword
    python load_neo4j.py                     # 在线 MERGE（默认源 neo4j/neo4j_ready.json）
    python load_neo4j.py --input your.json   # 指定输入
    python load_neo4j.py --admin-import      # 仅打印离线导入命令
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

# ── 可移植路径解析（相对于本脚本所在目录）─────────────────────────
PKG_DIR = Path(__file__).parent.resolve()
NEO4J_READY_INPUT = PKG_DIR / "neo4j" / "neo4j_ready.json"
NEO4J_CSV_DIR = PKG_DIR / "neo4j"
DEFAULT_INPUT = NEO4J_READY_INPUT          # 默认优先用 neo4j_ready.json

# 所有节点统一附加的通用 Label，用于建立全局唯一约束与属性索引。
COMMON_LABEL = "Entity"

# 校验查询（在线模式跑完 MERGE 后执行）
VALIDATION_QUERIES: Dict[str, str] = {
    "node_count": "MATCH (n:Entity) RETURN count(n) AS c",
    "edge_count": "MATCH ()-[r]->() RETURN count(r) AS c",
    "theorems": (
        "MATCH (n:Entity) WHERE n.ntype = 'theorem' OR 'Theorem' IN labels(n) "
        "RETURN n.id AS id, n.name AS name ORDER BY n.id"
    ),
    "low_conf_inferred": (
        "MATCH (a)-[r]->(b) "
        "WHERE r.explicit_or_inferred IN ['inferred','llm_inferred'] "
        "  AND r.confidence < $th "
        "RETURN a.id AS a, b.id AS b, type(r) AS t, r.confidence AS conf "
        "ORDER BY conf ASC"
    ),
    "gauss_bonnet_to_manifold": (
        # Cypher 25 不支持 [*1..6] 变长不带类型 + 列表推导。改用定长 3 跳，指定关系类型。
        "MATCH (a:Entity {id:'MX:thm:gauss_bonnet'}) "
        "MATCH (a)-[:derived_from|proves]->(m1:Entity) "
        "MATCH (m1)-[:derived_from|proves]->(m2:Entity) "
        "MATCH (m2)-[:derived_from|proves]->(b:Entity {id:'MX:def:manifold'}) "
        "RETURN [a.id, m1.id, m2.id, b.id] AS path "
        "LIMIT 5"
    ),
    "type_stats": (
        "MATCH (n:Entity) "
        "RETURN COALESCE(n.ntype, head(labels(n))) AS t, count(*) AS c "
        "ORDER BY c DESC"
    ),
}


def load_graph(path: str) -> Dict[str, list]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {"nodes": data.get("nodes", []), "edges": data.get("edges", [])}


def _flatten_props(props: dict) -> dict:
    """Neo4j 属性只能是标量/标量数组；复杂值做安全处理。"""
    out: Dict[str, object] = {}
    for k, v in props.items():
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            out[k] = [x for x in v if isinstance(x, (str, int, float, bool))]
        elif isinstance(v, (str, int, float, bool)):
            out[k] = v
        else:
            out[k] = str(v)
    return out


def _node_labels(node: dict) -> List[str]:
    """返回带通用 Entity 标签的标签列表。"""
    labels = list(node.get("labels", []))
    if COMMON_LABEL not in labels:
        labels = [COMMON_LABEL] + labels
    return labels


def write_graph(driver, nodes: List[dict], edges: List[dict],
                database: Optional[str], use_apoc: bool) -> None:
    """分批写入节点与边。"""
    batch = 200

    # ---- 节点 ----
    if use_apoc:
        q_nodes = (
            "UNWIND $rows AS row "
            "CALL apoc.merge.node(row.labels, {id: row.id}, row.props, {}) "
            "YIELD node RETURN count(node) AS c"
        )
    else:
        q_nodes = (
            "UNWIND $rows AS row "
            "MERGE (n:`{lab}` {id: row.id}) "
            "SET n += row.props "
            "RETURN count(n) AS c"
        )
    rows = [{"id": n["id"], "labels": _node_labels(n),
             "props": _flatten_props(n.get("props", {}))} for n in nodes]
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        primary = chunk[0]["labels"][-1] if len(chunk[0]["labels"]) > 1 else chunk[0]["labels"][0]
        q = q_nodes.format(lab=primary) if not use_apoc else q_nodes
        with driver.session(database=database) as s:
            s.run(q, rows=chunk, batch=batch)
        print(f"[LOAD] 节点 {i + len(chunk)}/{len(rows)}")

    # ---- 边 ----
    if use_apoc:
        q_edge = (
            "UNWIND $rows AS row "
            "MATCH (a:Entity {id: row.start}), (b:Entity {id: row.end}) "
            "CALL apoc.merge.relationship(a, row.type, {kind: row.kind}, "
            "row.props, b) YIELD rel SET rel += row.props RETURN count(rel) AS c"
        )
        brows = [{"start": e["source"], "end": e["target"], "type": e["type"],
                  "kind": e.get("kind", e.get("props", {}).get("kind", "")),
                  "props": _flatten_props(e.get("props", {}))} for e in edges]
        for i in range(0, len(brows), batch):
            chunk = brows[i:i + batch]
            with driver.session(database=database) as s:
                s.run(q_edge, rows=chunk)
            print(f"[LOAD] 边 {i + len(chunk)}/{len(brows)}")
    else:
        groups: Dict[str, List[dict]] = {}
        for e in edges:
            groups.setdefault(e["type"], []).append(e)
        for rtype, group in groups.items():
            q = (f"UNWIND $rows AS row "
                 f"MATCH (a:Entity {{id: row.start}}), (b:Entity {{id: row.end}}) "
                 f"MERGE (a)-[r:`{rtype}`]->(b) SET r += row.props "
                 f"RETURN count(r) AS c")
            brows = [{"start": e["source"], "end": e["target"],
                      "props": _flatten_props(e.get("props", {}))} for e in group]
            for i in range(0, len(brows), batch):
                chunk = brows[i:i + batch]
                with driver.session(database=database) as s:
                    s.run(q, rows=chunk)
            print(f"[LOAD] 边类型 {rtype}: {len(brows)} 条")


def build_constraints(driver, database: Optional[str]) -> None:
    """建约束与索引（Entity 全局唯一约束 + ntype/domain 索引）。"""
    stmts = [
        "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE",
        "CREATE INDEX entity_ntype IF NOT EXISTS FOR (n:Entity) ON (n.ntype)",
        "CREATE INDEX entity_domain IF NOT EXISTS FOR (n:Entity) ON (n.domain)",
    ]
    with driver.session(database=database) as s:
        for c in stmts:
            try:
                s.run(c)
            except Exception as e:  # noqa: BLE001
                print(f"[WARN] 建约束/索引失败（可忽略）：{e}")


def run_validation(driver, database: Optional[str]) -> None:
    """跑 6 类校验查询并打印结果。"""
    with driver.session(database=database) as s:
        print("\n--- 校验查询 ---")
        nc = s.run(VALIDATION_QUERIES["node_count"]).single()["c"]
        ec = s.run(VALIDATION_QUERIES["edge_count"]).single()["c"]
        print(f"  节点总数   : {nc}")
        print(f"  边总数     : {ec}")

        thms = [r["id"] for r in s.run(VALIDATION_QUERIES["theorems"])]
        print(f"  Theorem 列表 ({len(thms)}): {thms}")

        low = list(s.run(VALIDATION_QUERIES["low_conf_inferred"], th=0.85))
        print(f"  低置信推断边 (<0.85, {len(low)}):")
        for r in low:
            print(f"      {r['a']} -[{r['t']}]-> {r['b']} conf={r['conf']}")

        paths = [r["path"] for r in s.run(VALIDATION_QUERIES["gauss_bonnet_to_manifold"])]
        print(f"  gauss_bonnet→manifold 路径 ({len(paths)}):")
        for p in paths:
            print(f"      {' -> '.join(p)}")

        stats = list(s.run(VALIDATION_QUERIES["type_stats"]))
        print("  按类型统计:")
        for r in stats:
            print(f"      {r['t']:<22} {r['c']}")


def print_admin_import_command() -> None:
    """打印可直接执行的 neo4j-admin 离线导入命令。"""
    nodes = str(NEO4J_CSV_DIR / "nodes.csv").replace("\\", "/")
    rels = str(NEO4J_CSV_DIR / "relationships.csv").replace("\\", "/")
    print("=" * 64)
    print("neo4j-admin 离线导入命令（请在有 JVM 的部署主机上执行）")
    print("=" * 64)
    print("# 1) 先停止 Neo4j 服务（或在离线导入模式下执行）：\n")
    print("neo4j-admin database import full formula-graph \\")
    print(f"  --nodes={nodes} \\")
    print(f"  --relationships={rels} \\")
    print("  --delimiter=, \\")
    print("  --array-delimiter=\";\" \\")
    print("  --id-type=STRING \\")
    print("  --skip-duplicate-nodes=true \\")
    print("  --overwrite-destination=true\n")
    print("# 2) 重启服务并设置其为默认库（如需要）：")
    print("neo4j-admin database set-default formula-graph   # 5.x 单机")
    print("\n# 说明：")
    print("#   - nodes.csv 的 :LABEL 用 ';' 分隔多 Label（含通用 Entity）。")
    print("#   - 关系 :START_ID/:END_ID 均为 STRING 类型，已保证 0 悬空。")
    print("#   - 导入后用 cypher-shell -f load.cypher 建立索引/约束并校验。")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="把图谱写入 Neo4j（APOC/原生 MERGE）")
    ap.add_argument("--input", default=None,
                    help="图谱 JSON（缺省时使用 neo4j/neo4j_ready.json）")
    ap.add_argument("--uri", default=os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    ap.add_argument("--user", default=os.environ.get("NEO4J_USER", "neo4j"))
    ap.add_argument("--password", default=os.environ.get("NEO4J_PASSWORD", "formula_graph_2026"))
    ap.add_argument("--database", default=os.environ.get("NEO4J_DATABASE", None))
    ap.add_argument("--admin-import", action="store_true",
                    help="仅打印 neo4j-admin 离线导入命令，不连接数据库")
    args = ap.parse_args(argv)

    # 1) 离线导入模式：仅打印命令
    if args.admin_import:
        print_admin_import_command()
        return 0

    # 2) 依赖守卫：neo4j 驱动缺失 -> 友好提示 + 非致命退出
    try:
        from neo4j import GraphDatabase
    except ImportError:
        print("[SKIP] 未安装 neo4j 驱动，跳过写库（不影响其它脚本）。")
        print("       安装：pip install neo4j==5.21.0")
        return 0

    # 3) 解析默认输入：优先 neo4j/neo4j_ready.json
    input_path = args.input or str(NEO4J_READY_INPUT)
    if not os.path.exists(input_path):
        print(f"[SKIP] 未找到输入文件：{input_path}")
        print("       请确认 neo4j/neo4j_ready.json 或 neo4j/nodes.csv 已内嵌到部署包。")
        return 0
    data = load_graph(input_path)
    print(f"[LOAD] 读入节点 {len(data['nodes'])} / 边 {len(data['edges'])} "
          f"（{input_path}）")

    # 4) 连接守卫：数据库不可达 -> 清晰报错 + 非致命退出（待部署）
    try:
        driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
        driver.verify_connectivity()
    except Exception as e:  # noqa: BLE001
        print(f"[SKIP] 无法连接 Neo4j（{args.uri}）：{e}")
        print("       排查：")
        print("        1) 是否已启动 Neo4j 容器：")
        print("           docker compose up -d")
        print("        2) 环境变量 NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD 是否正确")
        print("        3) 生产/大批量导入建议用 --admin-import 离线导入：")
        print("           python load_neo4j.py --admin-import")
        return 0

    # 5) APOC 可用性探测（不可用则退回普通 MERGE）
    use_apoc = False
    try:
        with driver.session(database=args.database) as s:
            s.run("RETURN apoc.version() AS v").single()
        use_apoc = True
        print("[LOAD] 检测到 APOC，使用 apoc.merge.* 写入。")
    except Exception:  # noqa: BLE001
        print("[LOAD] 未检测到 APOC，退回普通 MERGE 写入。")

    # 6) 建约束 / 索引（幂等）
    build_constraints(driver, args.database)

    # 7) 写入
    try:
        write_graph(driver, data["nodes"], data["edges"], args.database, use_apoc)
        print(f"\n[Done] 写入完成：节点 {len(data['nodes'])} / 边 {len(data['edges'])}")
        run_validation(driver, args.database)
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 写入/校验过程出现异常（非致命）：{e}")
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
