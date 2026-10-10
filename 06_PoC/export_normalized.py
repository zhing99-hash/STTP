# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""从 Aura 重新导出权威原始图 -> 06_PoC/etl/normalized.json（ETL 原始格式）。
用于恢复被 Phase 12 清理误删的 viz_server 后端源文件。
原始格式: {nodes:[{id,labels:[...],props:{...}}], edges:[{id,source,target,type,kind,props:{...}}]}
"""
import json, os, sys
from neo4j import GraphDatabase

URI = os.environ.get("NEO4J_URI", "neo4j+ssc://853a33bc.databases.neo4j.io")
USER = os.environ.get("NEO4J_USER", "853a33bc")
PW = os.environ.get("NEO4J_PASSWORD", "")
DB = os.environ.get("NEO4J_DATABASE", "853a33bc")

ROOT = r"C:\Users\Administrator\WorkBuddy\2026-10-08-10-51-20\STTP"
OUT = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")

d = GraphDatabase.driver(URI, auth=(USER, PW), database=DB)
nodes, edges = [], []
with d.session(database=DB) as s:
    print("[1/2] 导出节点 ...")
    for rec in s.run("MATCH (n) RETURN n"):
        n = rec["n"]
        props = dict(n)
        nid = props.get("id")
        if nid is None:
            continue
        labels = list(n.labels)
        if "Entity" not in labels:
            labels = ["Entity"] + labels
        nodes.append({"id": nid, "labels": labels, "props": props})
    print(f"    节点 {len(nodes)}")
    print("[2/2] 导出边 ...")
    for rec in s.run(
        "MATCH (a)-[r]->(b) RETURN a.id AS s, b.id AS t, type(r) AS ty, r AS r"
    ):
        a_id, b_id, ty, r = rec["s"], rec["t"], rec["ty"], rec["r"]
        if a_id is None or b_id is None:
            continue
        rp = dict(r)
        kind = rp.get("kind", "explicit")
        # 边 id 必须唯一：仅用 type|source|target 会在「同三元组、不同 kind」上撞号，
        # 撞号的边在 NetworkX 里会被静默折叠、在 Cytoscape 里会渲染异常。
        # 故 fallback 直接带上 kind，并在末尾统一兜底去重。
        eid = rp.get("id") or f"{ty}|{a_id}|{b_id}|{kind}"
        edges.append({
            "id": eid,
            "source": a_id,
            "target": b_id,
            "type": ty,
            "kind": kind,
            "props": rp,
        })
    print(f"    边 {len(edges)}")
d.close()

# 兜底：确保 id 全局唯一（Aura 侧存量的 id 属性可能已撞号）
_seen, _fixed = set(), 0
for _e in edges:
    if _e["id"] in _seen:
        _base, _kind, _n = _e["id"], (_e.get("kind") or "unknown"), 2
        _cand = f"{_base}|{_kind}"
        while _cand in _seen:
            _cand = f"{_base}|{_kind}#{_n}"
            _n += 1
        _e["id"] = _cand
        _fixed += 1
    _seen.add(_e["id"])
if _fixed:
    print(f"    [WARN] 修正 {_fixed} 条撞号的边 id（追加 |kind 后缀）")

raw = {"nodes": nodes, "edges": edges}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(raw, f, ensure_ascii=False, indent=2)
print(f"[OK] 已写出 {OUT}  (节点 {len(nodes)} / 边 {len(edges)})")
