# -*- coding: utf-8 -*-
"""
公式知识图谱 · Neo4j 部署前静态校验（preflight_neo4j.py）
================================================================

**关键证据来源**：在沙箱无 Java/Docker 的情况下，静态校验 Neo4j 就绪数据集，
证明产物可被 ``neo4j-admin import`` 安全导入（无悬空引用、无重复主键）。

校验项
------
1. ``06_PoC/etl/neo4j/nodes.csv`` 表头含 ``:ID``、``:LABEL``。
2. ``06_PoC/etl/neo4j/relationships.csv`` 表头含 ``:START_ID``、``:END_ID``、``:TYPE``。
3. nodes 内无重复 ``:ID``（主键唯一，避免 import 冲突）。
4. **引用完整性（核心）**：每条关系的 ``:START_ID`` / ``:END_ID`` 都存在于 nodes 的
   ``:ID`` 集合中，悬空引用数 = 0。
5. 统计：节点数 / 边数 / 合成节点数（source=llm_hypothesis）/ 悬空数。
6. 若设置了 ``NEO4J_URI`` 且可达，探测连通性并报告；否则标注「待部署」。

约定：所有静态检查通过才 **exit 0**；任一关键检查失败则 exit 1（供 CI 拦截）。

运行
----
    cd 01tuopu/03_知识层
    python preflight_neo4j.py
    # 可选：设置环境变量探测真实实例
    set NEO4J_URI=bolt://localhost:7687
    python preflight_neo4j.py
"""

from __future__ import annotations

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NODES_CSV = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "nodes.csv")
RELS_CSV = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "relationships.csv")

# 预期指标（与 build_neo4j_ready.py 的断言一致）
EXPECT = {"nodes": 36, "edges": 52, "synthesized": 14, "dangling": 0}


def read_csv(path: str):
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        header = reader.fieldnames or []
    return header, rows


def check_connectivity(uri: str, user: str, password: str) -> dict:
    """若 neo4j 驱动可用且实例可达，探测连通性。失败返回待部署信息。"""
    try:
        from neo4j import GraphDatabase
    except ImportError:
        return {"reachable": False, "reason": "未安装 neo4j 驱动（仅做静态校验）"}
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password),
                                      connection_timeout=5)
        driver.verify_connectivity()
        driver.close()
        return {"reachable": True, "reason": "连通性 OK"}
    except Exception as e:  # noqa: BLE001 - 任何异常都视为不可达
        return {"reachable": False, "reason": f"实例不可达：{type(e).__name__}: {e}"}


def main() -> int:
    print("=" * 64)
    print("Neo4j 部署前静态校验（preflight）")
    print("=" * 64)

    failures: List[str] = []

    # ---- 0) 文件存在性 ----
    for p in (NODES_CSV, RELS_CSV):
        if not os.path.exists(p):
            print(f"[FAIL] 缺少文件：{p}")
            failures.append(f"missing:{p}")
    if failures:
        print("\n[RESULT] 静态校验未通过（exit 1）。")
        return 1

    # ---- 1) 读取表头 ----
    node_header, node_rows = read_csv(NODES_CSV)
    rel_header, rel_rows = read_csv(RELS_CSV)

    node_have = set(node_header)
    rel_have = set(rel_header)

    print(f"节点表头 ({len(node_header)} 列): {node_header}")
    print(f"关系表头 ({len(rel_header)} 列): {rel_header}")

    # ---- 2) 表头合法性（:ID / :LABEL / :START_ID / :END_ID / :TYPE）----
    for need in (":ID", ":LABEL"):
        ok = need in node_have
        print(f"  [{'OK' if ok else 'FAIL'}] nodes.csv 含 {need}")
        if not ok:
            failures.append(f"node_header:{need}")
    for need in (":START_ID", ":END_ID", ":TYPE"):
        ok = need in rel_have
        print(f"  [{'OK' if ok else 'FAIL'}] relationships.csv 含 {need}")
        if not ok:
            failures.append(f"rel_header:{need}")

    # ---- 3) 节点主键唯一性 ----
    id_col = ":ID"
    seen = set()
    dup = 0
    for r in node_rows:
        vid = r.get(id_col, "")
        if vid in seen:
            dup += 1
            print(f"  [WARN] 重复 :ID：{vid}")
        seen.add(vid)
    print(f"  [{'OK' if dup == 0 else 'FAIL'}] nodes 内无重复 :ID（重复 {dup}）")
    if dup != 0:
        failures.append(f"duplicate_id:{dup}")

    # ---- 4) 引用完整性（核心）----
    node_ids = seen  # 已包含所有 :ID
    dangling = []
    for r in rel_rows:
        s, e = r.get(":START_ID", ""), r.get(":END_ID", "")
        if s not in node_ids:
            dangling.append(s)
        if e not in node_ids:
            dangling.append(e)
    print(f"  [{'OK' if not dangling else 'FAIL'}] 引用完整性：悬空引用 {len(dangling)} 条")
    if dangling:
        for d in sorted(set(dangling)):
            print(f"      - 悬空端点：{d}")
        failures.append(f"dangling:{len(dangling)}")

    # ---- 5) 统计 ----
    n_nodes = len(node_rows)
    n_edges = len(rel_rows)
    n_synth = sum(1 for r in node_rows
                  if r.get("source") == "llm_hypothesis")
    n_dangling = len(dangling)

    print("-" * 64)
    print(f"节点总数      : {n_nodes}    （预期 {EXPECT['nodes']}）")
    print(f"边总数        : {n_edges}    （预期 {EXPECT['edges']}）")
    print(f"合成节点数    : {n_synth}    （预期 {EXPECT['synthesized']}）")
    print(f"悬空引用数    : {n_dangling}    （预期 {EXPECT['dangling']}）")

    if n_nodes != EXPECT["nodes"]:
        failures.append(f"nodes!={EXPECT['nodes']}")
    if n_edges != EXPECT["edges"]:
        failures.append(f"edges!={EXPECT['edges']}")
    if n_synth != EXPECT["synthesized"]:
        failures.append(f"synthesized!={EXPECT['synthesized']}")
    if n_dangling != EXPECT["dangling"]:
        failures.append(f"dangling!={EXPECT['dangling']}")

    # ---- 6) 实测连通性（可选）----
    uri = os.environ.get("NEO4J_URI")
    print("-" * 64)
    if uri:
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "neo4j")
        conn = check_connectivity(uri, user, password)
        status = "可达" if conn["reachable"] else "不可达（待部署）"
        print(f"NEO4J_URI 已设置（{uri}）：{status}")
        print(f"  说明：{conn['reason']}")
    else:
        print("NEO4J_URI 未设置 → 跳过连通性探测，标注「待部署」。")
        print("  在含 JVM 的部署主机上设置环境变量后再跑本脚本可探测实例。")

    # ---- 结论 ----
    print("=" * 64)
    if failures:
        print(f"[RESULT] 静态校验存在 {len(failures)} 项失败：{failures}")
        print("[RESULT] 静态校验未通过（exit 1）。")
        return 1
    print("[RESULT] 静态校验全部通过 [OK]（exit 0）：")
    print(f"          节点={n_nodes} 边={n_edges} 重复ID=0 悬空引用={n_dangling} 表头合法。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
