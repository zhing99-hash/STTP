#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""make_sample.py —— 离线研究样例构建器（Phase 37 · GPT6 复评 R06）

为什么需要它
------------
复评的第 6 项工单（R06）指出：**别人下载代码后无法复现报告中的完整系统**
—— `normalized.json`（48 MB）与阶段快照被 `.gitignore` 排除，delta 文件又只有边没有节点。

本项目是「路线 A：研究原型（收敛版）」，原始大数据不入库是合理的；
但必须配套一个**自包含、可入库、可离线运行**的小型研究样例，让第三人能：

    1. 用**同一条数据**启动可视化页面（`/graph_data.json`）
    2. 用**同一条数据**跑查询（`/api/neighbors`、`/api/path`）
    3. 跑**已知反例**回归（frozen_gate 的自包含子集）
    4. 校验页面与查询确实属于**同一数据版本**（id 集合逐元素相等）

本脚本做什么
------------
从权威主图 `06_PoC/etl/normalized.json` **确定性**抽取一个「**跨域桥核心样例**」：
以跨学科边为种子、按 (是否 verified_strict, type, id) 稳定排序后 BFS 扩展，
输出**同一份切片的两种投影**：

    sample/sample_normalized.json   —— 规范图格式（查询后端 / 门禁读这个）
    sample/sample_graph.json        —— 前端投影格式（页面读这个；schema=formula-graph-view/v1）

两者节点 id 与边 id 集合**必须逐元素相等**（由 verify_release.py 断言）。

★ 口径纪律（铁律 #40 同源）：学科判定一律走 `graph_export.subject_of`，
  与北极星仪器**同源**；不在此处另写一套学科映射。

★ 确定性：切片的每一步排序都基于 (type, id)，不使用集合遍历顺序、不使用随机数、
  不在样例文件里写时间戳 —— 同输入永远同输出（sha256 可复现）。

用法
----
    python 06_PoC/release/make_sample.py                       # 默认参数
    python 06_PoC/release/make_sample.py --node-cap 600 --edge-cap 1200
    python 06_PoC/release/make_sample.py --source <path>       # 换输入图
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))                 # 06_PoC/release
POC = os.path.dirname(HERE)                                       # 06_PoC
ROOT = os.path.dirname(POC)                                       # STTP
sys.path.insert(0, POC)

import graph_export as gx                                          # noqa: E402

DEFAULT_SRC = os.path.join(POC, "etl", "normalized.json")
SAMPLE_DIR = os.path.join(HERE, "sample")

# 扩展时允许纳入的**核心语义关系**（定义 / 计量 / 组成 / 反应 / 元数据）。
# 只走白名单，避免把 24000+ 条 `has_symbol` 之类的低信息边全拉进来把样例撑爆。
CORE_TYPES = (
    "has_quantity", "has_unit", "dimensionally_consistent", "uses_operator",
    "derived_from", "defines", "composed_of", "reactant_of", "product_of",
    "reagent_of", "has_element", "same_period", "same_family", "same_as",
    "same_formula_as", "has_functionalgroup", "related_to", "cites", "discusses",
)
CORE_TYPE_SET = frozenset(CORE_TYPES)

# 邻域扩展的语义优先级（小 = 先取）。跨学科边恒为 0（样例的主角）。
# 目的：防止 BFS 被 `composed_of`（分子→元素，化学内部）之类的**高产量低信息**边
#       占满配额，把真正的跨域桥挤出样例。
QUANT_TYPES = frozenset({
    "has_quantity", "has_unit", "dimensionally_consistent", "uses_operator",
    "derived_from", "defines",
})


def edge_priority(e: dict, cross: bool) -> int:
    if cross:
        return 0
    t = e.get("type") or ""
    if t in QUANT_TYPES:
        return 1
    if t in ("same_as", "same_formula_as", "has_element", "related_to", "cites", "discusses"):
        return 2
    if t == "composed_of":
        return 4
    return 3


STRICT_MIN_RANK = 4        # verification_level 序号 ≥ rule_checked（与 verification_model 一致）
_LEVEL_RANK = {
    "unverified": 0, "model_inferred": 1, "source_asserted": 2,
    "by_construction": 3, "rule_checked": 4, "cross_source": 5, "human_reviewed": 6,
}


def level_of(edge: dict) -> str:
    return str((edge.get("props") or {}).get("verification_level") or "")


def is_strict(edge: dict) -> bool:
    return _LEVEL_RANK.get(level_of(edge), -1) >= STRICT_MIN_RANK


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dump_json(path: str, obj) -> None:
    """确定性写出：sort_keys + 固定缩进 + 无尾随空白。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


# ---------------------------------------------------------------------------
# 切片
# ---------------------------------------------------------------------------
def slice_graph(nodes, edges, *, node_cap, edge_cap, per_node_cap, seed_cap):
    """返回 (nodes_out, edges_out, info)。全部按 id 稳定排序，确定性。"""
    node_by_id = {n["id"]: n for n in nodes}
    subj = {nid: gx.subject_of((n.get("props") or {}).get("domain"), str(nid))
            for nid, n in node_by_id.items()}

    # ---- 1. 跨学科边 = 种子（按**学科对**分层，保证每个学科对都有代表）----
    cross = []
    by_pair = collections.defaultdict(list)
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s in subj and t in subj and subj[s] != subj[t]:
            cross.append(e)
            pair = " ↔ ".join(sorted([subj[s], subj[t]]))
            by_pair[pair].append(e)
    cross_ids = {e["id"] for e in cross}
    for p in by_pair:                       # 学科对内稳定排序
        by_pair[p].sort(key=lambda e: (0 if is_strict(e) else 1, e.get("type") or "", e.get("id") or ""))

    # 轮转取种子：pair 顺序 + 每对内部顺序都确定 ⇒ 结果确定
    seeds, idx = [], 0
    pair_keys = sorted(by_pair)
    while len(seeds) < seed_cap:
        progressed = False
        for p in pair_keys:
            if idx < len(by_pair[p]):
                seeds.append(by_pair[p][idx])
                progressed = True
                if len(seeds) >= seed_cap:
                    break
        if not progressed:
            break
        idx += 1

    # ---- 2. 邻接表（按 (语义优先级, is_strict, id) 排序，仅核心关系类型）----
    adj = collections.defaultdict(list)
    for e in edges:
        if (e.get("type") or "") not in CORE_TYPE_SET:
            continue
        s, t = e.get("source"), e.get("target")
        if s in node_by_id:
            adj[s].append(e)
        if t in node_by_id:
            adj[t].append(e)
    for k in adj:
        adj[k].sort(key=lambda e: (edge_priority(e, e["id"] in cross_ids),
                                   0 if is_strict(e) else 1, e.get("id") or ""))

    # ---- 3. 确定性 BFS ----
    visited = set()
    picked_edges = {}
    for e in seeds:
        if len(picked_edges) >= edge_cap:
            break
        picked_edges[e["id"]] = e
        visited.add(e["source"])
        visited.add(e["target"])

    frontier = sorted(visited)
    while frontier and len(visited) < node_cap and len(picked_edges) < edge_cap:
        nxt = []
        for n in frontier:
            for e in adj.get(n, [])[:per_node_cap]:
                if len(picked_edges) >= edge_cap:
                    break
                eid = e["id"]
                if eid in picked_edges:
                    continue
                other = e["target"] if e["source"] == n else e["source"]
                if other not in visited and len(visited) >= node_cap:
                    continue          # 节点到顶：只收内部边，不引新节点
                picked_edges[eid] = e
                if other not in visited:
                    visited.add(other)
                    nxt.append(other)
        frontier = sorted(nxt)

    # ---- 4. 归集输出（稳定排序）----
    # 只保留两端都在样例内的边（BFS 已保证，这里做防御性回查）
    edges_out = [picked_edges[eid] for eid in sorted(picked_edges)
                 if picked_edges[eid]["source"] in visited and picked_edges[eid]["target"] in visited]
    live = set()
    for e in edges_out:
        live.add(e["source"])
        live.add(e["target"])
    nodes_out = [node_by_id[nid] for nid in sorted(live)]

    info = {
        "source_edges_total": len(edges),
        "source_nodes_total": len(nodes),
        "cross_domain_edges_total": len(cross),
        "cross_domain_pairs": sorted(by_pair),
        "seeds_taken": len(seeds),
        "nodes": len(nodes_out),
        "edges": len(edges_out),
        "subjects": dict(sorted(collections.Counter(
            subj[n["id"]] for n in nodes_out).items())),
    }
    return nodes_out, edges_out, info


def build_queries(nodes_out, edges_out):
    """从切片**生成**查询契约（保证可满足，非手写）。"""
    node_by_id = {n["id"]: n for n in nodes_out}
    subj = {nid: gx.subject_of((n.get("props") or {}).get("domain"), str(nid))
            for nid, n in node_by_id.items()}
    deg = collections.Counter()
    for e in edges_out:
        deg[e["source"]] += 1
        deg[e["target"]] += 1

    cross = [e for e in edges_out
             if subj.get(e["source"]) and subj.get(e["target"])
             and subj[e["source"]] != subj[e["target"]]]
    cross.sort(key=lambda e: (e.get("type") or "", e.get("id") or ""))

    qs = []
    qs.append({
        "id": "Q1-stats",
        "kind": "stats",
        "desc": "页面与查询必须属于同一数据版本：节点/边计数一致（页面 /graph_data.json ↔ 查询后端）",
        "expect": {"nodes": len(nodes_out), "edges": len(edges_out)},
    })

    # Q2：跨学科边（本项目核心对象）必须存在于样例中
    qs.append({
        "id": "Q2-cross-domain",
        "kind": "cross_domain_count_min",
        "desc": "样例必须保留跨学科桥：跨学科边数量不低于所报值",
        "expect": {"min": len(cross), "exact": len(cross)},
    })

    # Q3：度数最高的节点，邻居数必须 ≥ 所报值
    if deg:
        top = min(sorted(deg), key=lambda n: (-deg[n], n))
        qs.append({
            "id": "Q3-neighbors",
            "kind": "neighbors_min",
            "desc": "查询后端 get_neighbors 在样例上可用",
            "node": top,
            "expect": {"min_degree": deg[top]},
        })

    # Q4：一条真实跨学科桥，两端之间必须有路径（页面高亮跨域路径依赖它）
    if cross:
        br = cross[0]
        qs.append({
            "id": "Q4-path",
            "kind": "path_exists",
            "desc": "跨学科桥两端在同一连通分量内，路径查询可用",
            "src": br["source"], "dst": br["target"],
            "expect": {"min_paths": 1, "via_edge": br["id"]},
        })

    # Q5：强证据边必须带完整证据链（claim/verification_evidence）
    strict = sorted([e for e in edges_out if is_strict(e)], key=lambda e: e.get("id") or "")
    if strict:
        e = strict[0]
        qs.append({
            "id": "Q5-evidence",
            "kind": "edge_evidence",
            "desc": "样例保留证据链：达标边必须带 verification_level 与逐条 evidence",
            "edge_id": e["id"],
            "expect": {"min_level_rank": STRICT_MIN_RANK, "require_evidence": True},
        })

    return {
        "schema": "sttp-sample-queries/v1",
        "note": ("这些查询由 make_sample.py 从切片**生成**（非手写），"
                 "verify_release.py 逐条执行；全过才认为「页面与查询同一数据版本」。"),
        "queries": qs,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="STTP 离线研究样例构建器（Phase 37 R06）")
    ap.add_argument("--source", default=DEFAULT_SRC, help="规范图输入（默认 06_PoC/etl/normalized.json）")
    ap.add_argument("--out-dir", default=SAMPLE_DIR)
    ap.add_argument("--node-cap", type=int, default=380)
    ap.add_argument("--edge-cap", type=int, default=900)
    ap.add_argument("--per-node-cap", type=int, default=5)
    ap.add_argument("--seed-cap", type=int, default=90)
    a = ap.parse_args(argv)

    if not os.path.exists(a.source):
        print("[ERROR] 找不到规范图：%s" % a.source)
        print("        （本文件被 .gitignore 排除；离线复现请改用 06_PoC/release/sample/ 内的成品样例）")
        return 2

    with open(a.source, encoding="utf-8") as f:
        data = json.load(f)
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []

    nodes_out, edges_out, info = slice_graph(
        nodes, edges,
        node_cap=a.node_cap, edge_cap=a.edge_cap,
        per_node_cap=a.per_node_cap, seed_cap=a.seed_cap,
    )

    # ---- 规范格式（查询/门禁读）----
    norm_path = os.path.join(a.out_dir, "sample_normalized.json")
    dump_json(norm_path, {
        "schema": "sttp-sample-normalized/v1",
        "meta": {
            "note": ("跨域桥核心样例（确定性抽取）。查询后端与 frozen_gate 读本文件。"
                     "与 sample_graph.json 同源同切片，id 集合必须逐元素相等。"),
            "source": os.path.basename(a.source),
            "source_nodes_total": info["source_nodes_total"],
            "source_edges_total": info["source_edges_total"],
            "cross_domain_edges_total": info["cross_domain_edges_total"],
            "seeds_taken": info["seeds_taken"],
            "node_count": len(nodes_out),
            "edge_count": len(edges_out),
            "params": {"node_cap": a.node_cap, "edge_cap": a.edge_cap,
                       "per_node_cap": a.per_node_cap, "seed_cap": a.seed_cap},
        },
        "nodes": nodes_out,
        "edges": edges_out,
    })

    # ---- 前端投影格式（页面读）----
    view = gx.build_graph_data(norm_path)
    view_path = os.path.join(a.out_dir, "sample_graph.json")
    dump_json(view_path, view)

    # ---- 查询契约 ----
    queries = build_queries(nodes_out, edges_out)
    q_path = os.path.join(a.out_dir, "sample_queries.json")
    dump_json(q_path, queries)

    # ---- 自检：两投影同版本 ----
    nid_norm = {n["id"] for n in nodes_out}
    nid_view = {n["id"] for n in (view.get("nodes") or [])}
    eid_norm = {e["id"] for e in edges_out}
    eid_view = {e["id"] for e in (view.get("edges") or [])}
    same = (nid_norm == nid_view) and (eid_norm == eid_view)

    print("=" * 70)
    print("  离线研究样例（Phase 37 · R06）")
    print("=" * 70)
    print("  源图        : %s (%d 节点 / %d 边)" % (os.path.basename(a.source),
                                                   info["source_nodes_total"], info["source_edges_total"]))
    print("  跨学科边    : %d（学科对 %s；取前 %d 条为种子）"
          % (info["cross_domain_edges_total"], "/".join(info["cross_domain_pairs"]), info["seeds_taken"]))
    print("  样例        : %d 节点 / %d 边 ｜ 学科 %s"
          % (len(nodes_out), len(edges_out), info["subjects"]))
    print("  两投影同版本: %s（节点 id %s / 边 id %s）"
          % ("✅ 是" if same else "❌ 否",
             "一致" if nid_norm == nid_view else "不一致",
             "一致" if eid_norm == eid_view else "不一致"))
    print("-" * 70)
    for p in (norm_path, view_path, q_path):
        print("  %-34s %8.1f KB  sha256=%s" % (
            os.path.relpath(p, ROOT).replace("\\", "/"),
            os.path.getsize(p) / 1024.0, sha256_file(p)[:16]))
    print("=" * 70)
    return 0 if same else 3


if __name__ == "__main__":
    sys.exit(main())
