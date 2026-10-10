# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""通用 delta 应用器：把 delta JSON 落到本地权威图（normalized.json）+ 重建 viz 快照。

背景
----
此前各轮的「本地图更新」都是写一次性脚本（apply_element_fix.py /
normalize_elements.py / normalize_periodic.py …），模式重复且易错。
本脚本把该模式固化为**可复用一条命令**，与 `merge_seed_delta.py`
（append-only 并入 seed 切片）、`11_真实数据/push_element_merge.py`
（推 Aura）互补：本条负责 **本地 upsert / 删除**。

delta schema（四段，与 push_element_merge.py 对齐）
--------------------------------------------------
    {
      "meta": {...},
      "nodes":         [ {id, labels?, props} … ],   # upsert：存在则合并 props，缺 labels 保留原样
      "delete_nodes":  [ {id} | "id" … ],            # DETACH DELETE：连同关联边一并移除
      "edges":         [ {id?, source, target, type, kind?, props?} … ],   # MERGE（按 type|source|target）
      "delete_edges":  [ {source, target, type} | {id} … ]
    }

行为
----
  * dry-run 默认；`--apply` 才落盘（自动备份 normalized.before_<tag>.json）
  * 边 id 一律归一为 `type|source|target`（与快照约定一致）
  * 落盘后自动用 graph_export 重建 `06_PoC/graph_data_phase<N>.json`
  * 自检：节点/边 id 唯一性、悬空边、自环边、delta 实际生效数

用法
----
    python 06_PoC/apply_delta.py --delta <path> --phase 19 [--tag codata] [--apply]
"""

import argparse
import json
import os
import shutil
import sys
import datetime
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import graph_export  # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def node_id(x):
    return x if isinstance(x, str) else (x or {}).get("id")


def edge_id(e):
    if e.get("id"):
        return e["id"]
    return "%s|%s|%s" % (e.get("type") or "", e.get("source") or "", e.get("target") or "")


def norm_edge(e):
    out = dict(e)
    out["id"] = edge_id(e)
    out.setdefault("kind", (e.get("props") or {}).get("kind") or e.get("kind") or "")
    out.setdefault("props", {})
    return out


def main():
    ap = argparse.ArgumentParser(description="通用本地 delta 应用器")
    ap.add_argument("--delta", required=True)
    ap.add_argument("--phase", required=True, help="输出快照编号 graph_data_phase<N>.json")
    ap.add_argument("--tag", default="delta", help="备份后缀 normalized.before_<tag>.json")
    ap.add_argument("--apply", action="store_true", help="真正落盘（默认 dry-run）")
    a = ap.parse_args()

    delta = load(a.delta)
    norm = load(NORM)
    nodes = norm["nodes"]
    edges = norm["edges"]
    n_before, e_before = len(nodes), len(edges)

    print("=" * 84)
    print("应用 delta: %s" % os.path.relpath(a.delta, ROOT))
    print("  图谱当前 %d 节点 / %d 边" % (n_before, e_before))
    print("=" * 84)

    node_by_id = {n["id"]: n for n in nodes}
    edge_by_id = {edge_id(e): e for e in edges}

    d_nodes = delta.get("nodes") or []
    d_del_nodes = delta.get("delete_nodes") or []
    d_edges = [norm_edge(e) for e in (delta.get("edges") or [])]
    d_del_edges = delta.get("delete_edges") or []

    # ---------------- 1. delete_nodes（DETACH：连同关联边） ----------------
    del_ids = set(filter(None, (node_id(x) for x in d_del_nodes)))
    cand_edges = [e for e in edges if e.get("source") not in del_ids and e.get("target") not in del_ids]
    detached = len(edges) - len(cand_edges)
    nodes = [n for n in nodes if n["id"] not in del_ids]
    edges = cand_edges

    # ---------------- 2. upsert 节点 ----------------
    added_n, updated_n = 0, 0
    if del_ids:
        node_by_id = {n["id"]: n for n in nodes}
    for dn in d_nodes:
        nid = node_id(dn)
        if not nid:
            continue
        props = dict(dn.get("props") or {})
        props.setdefault("id", nid)
        if nid in node_by_id:
            old = node_by_id[nid]
            merged = dict(old.get("props") or {})
            merged.update(props)
            if merged != (old.get("props") or {}):
                old["props"] = merged
                updated_n += 1
            if dn.get("labels"):
                old["labels"] = dn["labels"]
        else:
            labels = dn.get("labels") or ["Entity"]
            nn = {"id": nid, "labels": labels, "props": props}
            nodes.append(nn)
            node_by_id[nid] = nn
            added_n += 1

    # ---------------- 3. delete_edges ----------------
    del_edge_ids = set()
    del_edge_keys = set()
    for de in d_del_edges:
        if isinstance(de, str):
            del_edge_ids.add(de)
        else:
            if de.get("id"):
                del_edge_ids.add(de["id"])
            if de.get("source") and de.get("target") and de.get("type"):
                del_edge_keys.add((de["source"], de["type"], de["target"]))
    if del_edge_ids or del_edge_keys:
        keep = []
        removed = 0
        for e in edges:
            eid = edge_id(e)
            key = (e.get("source"), e.get("type"), e.get("target"))
            if eid in del_edge_ids or key in del_edge_keys:
                removed += 1
                continue
            keep.append(e)
        edges = keep
    else:
        removed = 0

    # ---------------- 4. MERGE 边 ----------------
    edge_by_id = {edge_id(e): e for e in edges}
    add_e, upd_e, exist_e = 0, 0, 0
    dangling = []
    self_loop = []
    for e in d_edges:
        if e.get("source") == e.get("target"):
            self_loop.append(e["id"])
        if e.get("source") not in node_by_id or e.get("target") not in node_by_id:
            dangling.append(e["id"])
            continue
        eid = e["id"]
        if eid in edge_by_id:
            old = edge_by_id[eid]
            merged = dict(old.get("props") or {})
            merged.update(e.get("props") or {})
            if merged != (old.get("props") or {}):
                old["props"] = merged
                upd_e += 1
            else:
                exist_e += 1
        else:
            edges.append(e)
            edge_by_id[eid] = e
            add_e += 1

    # ---------------- 5. 自检 ----------------
    nids = [n["id"] for n in nodes]
    eids = [edge_id(e) for e in edges]
    dup_n = [k for k, v in Counter(nids).items() if v > 1]
    dup_e = [k for k, v in Counter(eids).items() if v > 1]
    node_set = set(nids)
    dfinal = [e["id"] for e in edges if e.get("source") not in node_set or e.get("target") not in node_set]
    sfinal = [e["id"] for e in edges if e.get("source") == e.get("target")]

    print("  节点：新增 %d / 更新 %d / 删除 %d（连带删边 %d）" % (added_n, updated_n, len(del_ids), detached))
    print("  边  ：新增 %d / 更新 %d / 已存在 %d / 按 id 删 %d" % (add_e, upd_e, exist_e, removed))
    print("  规模：%d 节点 / %d 边  (%+d / %+d)" % (len(nodes), len(edges), len(nodes) - n_before, len(edges) - e_before))
    print("-" * 84)
    ok = True
    checks = [
        ("节点 id 唯一", not dup_n, dup_n[:3]),
        ("边 id 唯一", not dup_e, dup_e[:3]),
        ("无悬空边", not dangling, dangling[:3]),
        ("无自环边", not self_loop, self_loop[:3]),
        ("全图无悬空边", not dfinal, dfinal[:3]),
        ("全图无自环边", not sfinal, sfinal[:3]),
    ]
    for name, passed, sample in checks:
        print("  [%s] %s %s" % ("PASS" if passed else "FAIL", name, ("样本=%s" % sample) if sample else ""))
        ok = ok and passed
    print("=" * 84)

    if not ok:
        print("[ABORT] 自检未通过，不落盘。")
        return 2

    if not a.apply:
        print("[DRY-RUN] 未落盘。加 --apply 实际应用。")
        return 0

    # ---------------- 6. 落盘 ----------------
    bak = os.path.join(ROOT, "06_PoC", "etl", "normalized.before_%s.json" % a.tag)
    if not os.path.exists(bak):
        shutil.copy2(NORM, bak)
        print("  已备份 -> %s" % os.path.relpath(bak, ROOT))
    norm["nodes"], norm["edges"] = nodes, edges
    with open(NORM, "w", encoding="utf-8") as f:
        json.dump(norm, f, ensure_ascii=False)
    print("  已写回 %s" % os.path.relpath(NORM, ROOT))

    viz_path = os.path.join(ROOT, "06_PoC", "graph_data_phase%s.json" % a.phase)
    viz = graph_export.build_graph_data(NORM)
    vnodes = viz.get("nodes") or viz.get("elements") or []
    vedges = viz.get("edges") or []
    with open(viz_path, "w", encoding="utf-8") as f:
        json.dump(viz, f, ensure_ascii=False)
    print("  已重建快照 -> %s（%d 节点 / %d 边，%.2f MB）" % (
        os.path.relpath(viz_path, ROOT), len(vnodes), len(vedges),
        os.path.getsize(viz_path) / 1048576))
    print("[DONE]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
