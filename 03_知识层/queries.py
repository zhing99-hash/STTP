# -*- coding: utf-8 -*-
"""
公式知识图谱 · 查询层（queries.py）
================================================================

对外提供 6 个查询接口：

    get_node(id)                      取单个节点
    get_neighbors(id)                 取邻居（方向/边类型/置信度）
    subgraph_by_confidence(threshold) 按置信度阈值取子图
    paths_between(a, b)               取两点间路径（有向，最长 5 跳）
    list_by_type(t)                   按标签/类型列节点
    filter_edges_by_kind(kind)        按 kind 过滤边

双后端
------
1. **Neo4j 驱动路径**：数据库可达时优先走 Cypher（语句见 ``queries.cypher``）。
2. **NetworkX 兜底**：无可用 DB 时，把 ``normalized.json`` 载入 ``nx.MultiDiGraph``
   并在内存中执行等价查询——即使没有运行中的 Neo4j 也能证明接口逻辑正确。

运行
----
    python queries.py                 # 自动选择后端并跑示例查询
    python queries.py --backend nx    # 强制 NetworkX 兜底
    python queries.py --backend neo4j --uri bolt://localhost:7687
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_INPUT = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")

# ----------------------------------------------------------------------------
# Cypher 语句（与 queries.cypher 保持一字不差）
# ----------------------------------------------------------------------------
CYPHER: Dict[str, str] = {
    "get_node": (
        "MATCH (n {id:$id}) "
        "RETURN n.id AS id, labels(n) AS labels, properties(n) AS props"
    ),
    "get_neighbors": (
        "MATCH (n {id:$id})-[r]-(m) "
        "RETURN m.id AS id, TYPE(r) AS type, properties(r) AS props, "
        "       CASE WHEN startNode(r).id = $id THEN 'out' ELSE 'in' END AS direction"
    ),
    "subgraph_by_confidence": (
        "MATCH (a)-[r]->(b) WHERE r.confidence >= $threshold "
        "RETURN a.id AS start, b.id AS end, TYPE(r) AS type, properties(r) AS props"
    ),
    "paths_between": (
        "MATCH p=(a {id:$a})-[:*1..5]->(b {id:$b}) "
        "RETURN [n IN nodes(p) | n.id] AS path LIMIT 20"
    ),
    "list_by_type": (
        "MATCH (n) WHERE $t IN labels(n) OR n.type = $t "
        "RETURN n.id AS id, labels(n) AS labels, properties(n) AS props"
    ),
    "filter_edges_by_kind": (
        "MATCH (a)-[r]->(b) WHERE r.kind = $kind "
        "RETURN a.id AS start, b.id AS end, TYPE(r) AS type, properties(r) AS props"
    ),
}


# ----------------------------------------------------------------------------
# 后端基类
# ----------------------------------------------------------------------------

class Backend:
    name = "base"

    def get_node(self, node_id: str) -> Optional[dict]:
        raise NotImplementedError

    def get_neighbors(self, node_id: str) -> List[dict]:
        raise NotImplementedError

    def subgraph_by_confidence(self, threshold: float) -> Dict[str, list]:
        raise NotImplementedError

    def paths_between(self, a: str, b: str, cutoff: int = 5) -> List[list]:
        raise NotImplementedError

    def list_by_type(self, t: str) -> List[dict]:
        raise NotImplementedError

    def filter_edges_by_kind(self, kind: str) -> List[dict]:
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover
        pass


# ----------------------------------------------------------------------------
# NetworkX 兜底后端
# ----------------------------------------------------------------------------

class NXBackend(Backend):
    """把归一化 JSON 载入 nx.MultiDiGraph 并执行查询。"""

    name = "networkx"

    def __init__(self, path: str = DEFAULT_INPUT):
        import networkx as nx  # 仅兜底路径需要的依赖
        self._nx = nx
        with open(path, "r", encoding="utf-8") as f:
            import json
            data = json.load(f)
        self.G = nx.MultiDiGraph()
        for n in data.get("nodes", []):
            attrs = dict(n.get("props") or {})
            attrs["labels"] = n.get("labels", [])
            self.G.add_node(n["id"], **attrs)
        for e in data.get("edges", []):
            attrs = dict(e.get("props") or {})
            attrs["type"] = e["type"]
            attrs["kind"] = e.get("kind", attrs.get("kind", ""))
            self.G.add_edge(e["source"], e["target"], key=e["id"], **attrs)
        self.node_count = self.G.number_of_nodes()
        self.edge_count = self.G.number_of_edges()

    def get_node(self, node_id: str) -> Optional[dict]:
        if node_id not in self.G:
            return None
        d = dict(self.G.nodes[node_id])
        return {"id": node_id, "labels": d.pop("labels", []), "props": d}

    def get_neighbors(self, node_id: str) -> List[dict]:
        out: List[dict] = []
        if node_id not in self.G:
            return out
        # 出边
        for _, m, k, d in self.G.out_edges(node_id, keys=True, data=True):
            out.append({"id": m, "type": d.get("type", ""), "direction": "out",
                        "kind": d.get("kind", ""),
                        "confidence": d.get("confidence")})
        # 入边
        for u, _, k, d in self.G.in_edges(node_id, keys=True, data=True):
            out.append({"id": u, "type": d.get("type", ""), "direction": "in",
                        "kind": d.get("kind", ""),
                        "confidence": d.get("confidence")})
        return out

    def subgraph_by_confidence(self, threshold: float) -> Dict[str, list]:
        nodes, edges = {}, []
        for u, v, k, d in self.G.edges(keys=True, data=True):
            conf = d.get("confidence")
            if conf is not None and float(conf) >= threshold:
                edges.append({"start": u, "end": v, "type": d.get("type", ""),
                              "kind": d.get("kind", ""), "confidence": conf})
                nodes[u] = True
                nodes[v] = True
        return {"nodes": sorted(nodes), "edges": edges}

    def paths_between(self, a: str, b: str, cutoff: int = 5) -> List[list]:
        if a not in self.G or b not in self.G:
            return []
        try:
            return list(self._nx.all_simple_paths(self.G, a, b, cutoff=cutoff))
        except Exception:
            return []

    def list_by_type(self, t: str) -> List[dict]:
        res = []
        for nid, d in self.G.nodes(data=True):
            labels = d.get("labels", [])
            if t in labels or d.get("type") == t:
                res.append({"id": nid, "labels": labels})
        return res

    def filter_edges_by_kind(self, kind: str) -> List[dict]:
        res = []
        for u, v, k, d in self.G.edges(keys=True, data=True):
            if d.get("kind") == kind:
                res.append({"start": u, "end": v, "type": d.get("type", ""),
                            "kind": kind, "confidence": d.get("confidence")})
        return res


# ----------------------------------------------------------------------------
# Neo4j 后端
# ----------------------------------------------------------------------------

class Neo4jBackend(Backend):
    """通过 neo4j 驱动执行 Cypher 查询。"""

    name = "neo4j"

    def __init__(self, uri: str, user: str, password: str,
                 database: Optional[str] = None):
        from neo4j import GraphDatabase
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.driver.verify_connectivity()
        self.database = database

    def _run(self, qname: str, **params) -> List[dict]:
        with self.driver.session(database=self.database) as s:
            return [dict(r) for r in s.run(CYPHER[qname], **params)]

    def get_node(self, node_id: str) -> Optional[dict]:
        rows = self._run("get_node", id=node_id)
        return rows[0] if rows else None

    def get_neighbors(self, node_id: str) -> List[dict]:
        return self._run("get_neighbors", id=node_id)

    def subgraph_by_confidence(self, threshold: float) -> Dict[str, list]:
        rows = self._run("subgraph_by_confidence", threshold=threshold)
        nodes = sorted({r["start"] for r in rows} | {r["end"] for r in rows})
        return {"nodes": nodes, "edges": rows}

    def paths_between(self, a: str, b: str, cutoff: int = 5) -> List[list]:
        return [r["path"] for r in self._run("paths_between", a=a, b=b)]

    def list_by_type(self, t: str) -> List[dict]:
        return self._run("list_by_type", t=t)

    def filter_edges_by_kind(self, kind: str) -> List[dict]:
        return self._run("filter_edges_by_kind", kind=kind)

    def close(self) -> None:
        try:
            self.driver.close()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# 后端工厂
# ----------------------------------------------------------------------------

def get_backend(which: str = "auto", uri: str = "bolt://localhost:7687",
                user: str = "neo4j", password: str = "neo4j",
                database: Optional[str] = None,
                input_path: str = DEFAULT_INPUT) -> Backend:
    """按需返回后端：auto 优先 Neo4j，不可用则回退 NetworkX。"""
    if which in ("auto", "neo4j"):
        try:
            return Neo4jBackend(uri, user, password, database)
        except Exception as e:
            if which == "neo4j":
                print(f"[WARN] Neo4j 不可用（{e}），仍尝试 NetworkX 兜底。")
            else:
                print(f"[提示] 未检测到可用 Neo4j（{type(e).__name__}），"
                      f"回退 NetworkX 兜底。")
    # 兜底
    try:
        return NXBackend(input_path)
    except FileNotFoundError:
        print(f"[SKIP] 未找到 {input_path}，请先运行：python etl_pipeline.py")
        raise SystemExit(0)
    except ImportError:
        print("[SKIP] 缺少 networkx，无法使用兜底后端。pip install networkx")
        raise SystemExit(0)


# ----------------------------------------------------------------------------
# CLI 示例
# ----------------------------------------------------------------------------

def _demo(b: Backend) -> None:
    print(f"\n后端：{b.name}")
    if isinstance(b, NXBackend):
        print(f"图规模：节点 {b.node_count} / 边 {b.edge_count}")

    print("\n--- 1) get_node('MX:thm:gauss_bonnet') ---")
    print(b.get_node("MX:thm:gauss_bonnet"))

    print("\n--- 2) get_neighbors('MX:def:levi_civita') ---")
    for nb in b.get_neighbors("MX:def:levi_civita"):
        print(f"    {nb['direction']:>3}  {nb['type']:<12} -> {nb['id']} "
              f"(kind={nb.get('kind')}, conf={nb.get('confidence')})")

    print("\n--- 3) subgraph_by_confidence(0.95) ---")
    sg = b.subgraph_by_confidence(0.95)
    print(f"    节点 {len(sg['nodes'])} 个，边 {len(sg['edges'])} 条")
    for e in sg["edges"][:6]:
        print(f"    {e['start']} -[{e['type']}]-> {e['end']} conf={e['confidence']}")

    print("\n--- 4) paths_between('MX:thm:gauss_bonnet', 'MX:def:manifold') ---")
    for p in b.paths_between("MX:thm:gauss_bonnet", "MX:def:manifold"):
        print("    " + " -> ".join(p))

    print("\n--- 5) list_by_type('Symbol')（前 5）---")
    for n in b.list_by_type("Symbol")[:5]:
        print(f"    {n['id']}  labels={n.get('labels')}")

    print("\n--- 6) filter_edges_by_kind('llm_inferred') ---")
    for e in b.filter_edges_by_kind("llm_inferred"):
        print(f"    {e['start']} -[{e['type']}]-> {e['end']} conf={e['confidence']}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="公式知识图谱查询层（Neo4j + NetworkX 兜底）")
    ap.add_argument("--backend", default="auto", choices=["auto", "neo4j", "nx"])
    ap.add_argument("--uri", default=os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    ap.add_argument("--user", default=os.environ.get("NEO4J_USER", "neo4j"))
    ap.add_argument("--password", default=os.environ.get("NEO4J_PASSWORD", "neo4j"))
    ap.add_argument("--database", default=os.environ.get("NEO4J_DATABASE", None))
    ap.add_argument("--input", default=DEFAULT_INPUT)
    args = ap.parse_args(argv)

    which = "nx" if args.backend == "nx" else args.backend
    b = get_backend(which, args.uri, args.user, args.password,
                    args.database, args.input)
    try:
        _demo(b)
    finally:
        b.close()
    print("\n[Done] 查询示例执行完毕。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
