# -*- coding: utf-8 -*-
"""
公式知识图谱 · 部署后校验（verify_deploy.py）
================================================================

在 Neo4j 实例可达时，用 neo4j 驱动跑 6 类校验查询，打印结果；
不可达（未设 NEO4J_URI / 缺驱动 / 实例未起）则打印「待部署」并以 **exit 0** 退出
（非致命，供 CI 在部署前阶段安全跳过）。

6 类校验（与 load_neo4j.py / load.cypher 口径一致）
-------------------------------------------------
1. 节点总数（预期 36）
2. 边总数（预期 52）
3. Theorem 列表
4. 低置信推断边（explicit_or_inferred ∈ {inferred,llm_inferred} 且 confidence < 0.85）
5. gauss_bonnet → manifold 路径（预期非空）
6. 按类型（ntype）统计

运行
----
    cd STTP/03_知识层
    set NEO4J_URI=bolt://localhost:7687
    set NEO4J_PASSWORD=yourpassword
    python verify_deploy.py
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

QUERIES: Dict[str, str] = {
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
        "MATCH p = (a:Entity {id:'MX:thm:gauss_bonnet'})-[:*1..6]->"
        "(b:Entity {id:'MX:def:manifold'}) "
        "RETURN [n IN nodes(p) | n.id] AS path LIMIT 5"
    ),
    "type_stats": (
        "MATCH (n:Entity) "
        "RETURN COALESCE(n.ntype, head(labels(n))) AS t, count(*) AS c "
        "ORDER BY c DESC"
    ),
}


def main() -> int:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "neo4j")
    database = os.environ.get("NEO4J_DATABASE", None)

    print("=" * 64)
    print("Neo4j 部署后校验（verify_deploy.py）")
    print("=" * 64)

    # ---- 1) 依赖守卫：neo4j 驱动缺失 → 友好提示 + exit 0 ----
    try:
        from neo4j import GraphDatabase
    except ImportError:
        print("[待部署] 未安装 neo4j 驱动，无法连接 Neo4j 实例。")
        print("         安装：pip install neo4j")
        print("         安装后设置 NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD 即可校验。")
        return 0

    # ---- 2) 连接守卫：实例不可达 → 打印「待部署」+ exit 0 ----
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password),
                                      connection_timeout=5)
        driver.verify_connectivity()
    except Exception as e:  # noqa: BLE001
        print(f"[待部署（无实例）] 无法连接 Neo4j（{uri}）：{type(e).__name__}: {e}")
        print("  部署步骤：")
        print("    1) docker compose -f docker-compose.yml up -d")
        print("    2) 等待容器 healthy（约 30-40 秒）")
        print("    3) python load_neo4j.py          # 在线 MERGE 写入")
        print("    4) cypher-shell -f load.cypher   # 建索引/约束 + 校验")
        print("    5) python verify_deploy.py       # 再次执行本脚本校验")
        return 0

    print(f"[已连接] {uri}  (user={user}, database={database or '默认'})")
    print("-" * 64)
    try:
        with driver.session(database=database) as s:
            nc = s.run(QUERIES["node_count"]).single()["c"]
            ec = s.run(QUERIES["edge_count"]).single()["c"]
            print(f"[1] 节点总数      : {nc}    （预期 36）")
            print(f"[2] 边总数        : {ec}    （预期 52）")

            thms: List[dict] = list(s.run(QUERIES["theorems"]))
            print(f"[3] Theorem 列表  ({len(thms)}):")
            for r in thms:
                print(f"      {r['id']}  ({r['name']})")

            low = list(s.run(QUERIES["low_conf_inferred"], th=0.85))
            print(f"[4] 低置信推断边  (<0.85, {len(low)}):")
            for r in low:
                print(f"      {r['a']} -[{r['t']}]-> {r['b']}  conf={r['conf']}")

            paths = [r["path"] for r in s.run(QUERIES["gauss_bonnet_to_manifold"])]
            print(f"[5] gauss_bonnet→manifold 路径 ({len(paths)}):")
            for p in paths:
                print(f"      {' -> '.join(p)}")
            if not paths:
                print("      [WARN] 未找到路径，请检查 derived_from 边。")

            stats = list(s.run(QUERIES["type_stats"]))
            print("[6] 按类型统计:")
            for r in stats:
                print(f"      {str(r['t']):<22} {r['c']}")
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 校验查询过程出现异常：{type(e).__name__}: {e}")
    finally:
        driver.close()

    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
