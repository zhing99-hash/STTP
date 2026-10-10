# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""sync_seed_delta.py —— 把「重跑后的 seed_*.json」差量 upsert 回权威图。

与既有两条路径的分工
--------------------
* ``merge_seed_delta.py``：**append-only** 并入新切片（节点/边都不存在时才加，
  一旦 id 撞车直接 assert 失败）。适用于「新增切片」。
* ``apply_delta.py``：通用 upsert / 删除 + 重建快照。适用于「已备好 delta」。
* 本脚本：**补齐中间那一步** —— 当脚本改了源头（build_*.py 的 props / 边）后，
  把重跑出来的 seed 与权威图**逐字段比对**，自动产出 ``apply_delta.py`` 能吃的
  upsert delta。避免每次改源头都手搓一次性脚本。

为什么必须有它：项目铁律「改数据前先改源头」，但改完源头若不做差量回灌，
权威图仍是旧的 —— 两边静默漂移，且下一次 ETL 重跑会「重新长错」。

比对口径
--------
* 节点：仅回灌 **props 有差异的键**（最小 diff），且**绝不改已存在节点的 labels**。
  原因：seed 里的基础库节点是「镜像声明」（如 PQ:energy），图谱中的 labels 是权威值
  （形如 ``["Physical_quantity","Entity"]``），用 seed 的 CamelCase 覆写会污染类型判定。
  不回灌整份 props —— seed 里没有的键（如 ETL 后续补的 derived_at）必须保留。
* 边  ：按 ``type|source|target`` 判存；不存在且两端节点可解析才纳入。
* 悬空边：两端不在「权威图 ∪ delta 新增节点」内 → 计入报告，**不入 delta**。

用法
----
    python 06_PoC/sync_seed_delta.py \
        --seeds 10_种子数据/seed_*.json \
        --out 06_PoC/etl/neo4j/phase23_audit_fix_delta.json \
        --delete-orphans EK2:rxn \
        --label Phase8c-auditfix \
        --reason "连通性审计修复：符号 domain 纠偏 + 补 has_symbol 边 + 清孤儿反应"
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")

# 易变字段：每次重跑脚本都会刷新时间戳，若不排除则**每个** seed 节点都会被误判为「有差异」，
# 让 delta 淹没在噪音里（实测 336/343 条 upsert 全是 created_at 造成的假阳性）。
VOLATILE = {"created_at", "updated_at", "generated_at", "ingested_at", "retrieved_at",
            "fetched_at", "timestamp"}

# ---- 权威性守卫（铁律 #10「静默降级最危险」）--------------------------------
# 种子是「手工策划的早期快照」，其数值常常**早于**后来的权威真实化（CODATA/PubChem/
# OpenAlex/ElementKG）。实测踩中：seed 的 ``TH:pq:gas_const.value = 8.314`` 会把图谱里
# CODATA 2022 的 ``8.314462618`` 覆盖掉；``EM:pq:permittivity`` 会被退回 2018 值。
# 因此分两类硬保护：
#   1) 溯源键：种子的 source 是「手工」，绝不能改写图谱里已有的真实出处（只填空缺）。
#   2) 载荷键：若图谱节点出自**外部权威源**，其 value/unit/不确定度由该源独占，种子不得覆盖。
PROVENANCE_KEYS = {"source", "source_url", "license", "doi", "retrieved_at", "source_id"}
PAYLOAD_KEYS = {"value", "unit", "uncertainty", "standard_uncertainty",
                "relative_uncertainty", "unit_str"}
EXTERNAL_AUTHORITY = ("nist_codata", "codata", "openalex", "pubchem", "chembl",
                      "elementkg", "physicsbabel", "crossref", "datacite", "wikidata")


def _is_external(src: str) -> bool:
    s = (src or "").lower()
    return any(s.startswith(a) for a in EXTERNAL_AUTHORITY)


def edge_id(e: dict) -> str:
    return "%s|%s|%s" % ((e.get("type") or ""), (e.get("source") or ""), (e.get("target") or ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="重跑 seed 后差量 upsert 回权威图")
    ap.add_argument("--seeds", nargs="+", required=True,
                    help="重跑后的 seed_*.json（支持通配符，由 shell 展开）")
    ap.add_argument("--out", required=True, help="产出的 delta 路径")
    ap.add_argument("--label", default="seed-sync", help="delta meta 阶段标签")
    ap.add_argument("--reason", default="build 脚本重跑后的 seed 差量回灌",
                    help="delta meta 的 reason 文案")
    ap.add_argument("--delete-orphans", nargs="*", default=[],
                    help="删除「id 前缀命中且在图谱中度数为 0」的孤立节点")
    ap.add_argument("--delete-nodes", nargs="*", default=[],
                    help="显式删除的节点 id（DETACH，连同关联边）")
    a = ap.parse_args()

    norm = json.load(open(NORM, encoding="utf-8"))
    cur_nodes = norm["nodes"]
    cur_edges = norm["edges"]
    node_by_id = {n["id"]: n for n in cur_nodes}
    cur_eids = {edge_id(e) for e in cur_edges}

    # 度数（供孤儿识别）
    deg = Counter()
    for e in cur_edges:
        deg[e.get("source")] += 1
        deg[e.get("target")] += 1

    # ---------------- 1. seed 差量 ----------------
    seeds = []
    for pat in a.seeds:
        seeds.extend(sorted(glob.glob(pat) if any(c in pat for c in "*?[") else [pat]))
    seeds = list(dict.fromkeys(seeds))
    assert seeds, "未匹配到任何 seed 文件"

    upsert_nodes, new_edges = [], []
    seen_node_ids, seen_edge_ids = set(), set()
    changed_keys = Counter()
    dangling_samples = []
    protected = []
    per_seed = []
    stat = Counter()

    for p in seeds:
        d = json.load(open(p, encoding="utf-8"))
        n_diff = n_new = e_new = 0
        for sn in d.get("nodes", []):
            nid = sn["id"]
            sprops = dict(sn.get("props") or {})
            slabels = list(sn.get("labels") or [])
            if nid in node_by_id:
                old = node_by_id[nid]
                oprops = dict(old.get("props") or {})
                diff = {k: v for k, v in sprops.items()
                        if k != "id" and k not in VOLATILE and oprops.get(k) != v}
                # ---- 权威性守卫 ----
                g_src = str(oprops.get("source") or "")
                blocked = {}
                if g_src:   # 溯源键：图谱已有出处就不许种子改写
                    for k in list(diff):
                        if k in PROVENANCE_KEYS:
                            blocked[k] = diff.pop(k)
                if _is_external(g_src):   # 载荷键：外部权威源的数值/单位不容种子覆盖
                    for k in list(diff):
                        if k in PAYLOAD_KEYS:
                            blocked[k] = diff.pop(k)
                if blocked:
                    protected.append((nid, g_src, blocked))
                # ⚠ 已存在节点**只改 props，不碰 labels**（见文件头「比对口径」）
                if diff:
                    upsert_nodes.append({"id": nid, "props": diff})
                    seen_node_ids.add(nid)
                    n_diff += 1
                    for k in diff:
                        changed_keys[k] += 1
            else:
                upsert_nodes.append({"id": nid, "labels": slabels or ["Entity"], "props": sprops})
                node_by_id[nid] = {"id": nid, "labels": slabels or ["Entity"], "props": sprops}
                seen_node_ids.add(nid)
                n_new += 1
        for se in d.get("edges", []):
            eid = edge_id(se)
            if eid in cur_eids or eid in seen_edge_ids:
                continue
            if se.get("source") not in node_by_id or se.get("target") not in node_by_id:
                stat["dangling_skipped"] += 1
                dangling_samples.append("%s -[%s]-> %s  (%s)"
                                        % (se.get("source"), se.get("type"),
                                           se.get("target"), os.path.basename(p)))
                continue
            new_edges.append({
                "id": eid,
                "source": se["source"],
                "target": se["target"],
                "type": se.get("type"),
                "kind": se.get("kind") or (se.get("props") or {}).get("kind") or "",
                "props": dict(se.get("props") or {}),
            })
            seen_edge_ids.add(eid)
            e_new += 1
        per_seed.append((os.path.basename(p), n_diff, n_new, e_new))
        stat["nodes_changed"] += n_diff
        stat["nodes_new"] += n_new
        stat["edges_new"] += e_new

    # ---------------- 2. 删除集 ----------------
    del_ids = set(a.delete_nodes)
    orphan_report = []
    for pfx in a.delete_orphans:
        for n in cur_nodes:
            if n["id"].startswith(pfx) and deg[n["id"]] == 0:
                del_ids.add(n["id"])
                orphan_report.append(n["id"])
    del_ids -= seen_node_ids          # 本轮回灌的节点不删

    # ---------------- 3. 自检 ----------------
    dup_u = [k for k, v in Counter(n["id"] for n in upsert_nodes).items() if v > 1]
    assert not dup_u, f"delta 节点 id 重复: {dup_u[:5]}"
    dup_e = [k for k, v in Counter(e["id"] for e in new_edges).items() if v > 1]
    assert not dup_e, f"delta 边 id 重复: {dup_e[:5]}"
    assert len(seen_node_ids & del_ids) == 0, "同一节点既 upsert 又删除"

    delta = {
        "meta": {
            "phase": a.label,
            "source": "06_PoC/sync_seed_delta.py",
            "reason": a.reason,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "seeds": [os.path.basename(p) for p in seeds],
        },
        "nodes": upsert_nodes,
        # ⚠ 必须是**纯 id 字符串**列表：`11_真实数据/push_element_merge.py` 以字符串为约定
        # （`apply_delta.py` 两种都吃，故本地不会暴露问题，但推到 Aura 会静默不删）。
        "delete_nodes": sorted(del_ids),
        "edges": new_edges,
        "delete_edges": [],
    }

    # ---------------- 4. 报告 ----------------
    print("=" * 84)
    print("sync_seed_delta  →  %s" % os.path.relpath(a.out, ROOT))
    print("=" * 84)
    print("扫描 seed 文件 %d 个：" % len(seeds))
    for name, nd, nn, en in per_seed:
        if nd or nn or en:
            print("  • %-40s 改props %3d  新节点 %2d  新边 %2d" % (name, nd, nn, en))
    print("-" * 84)
    print("权威图现状：%d 节点 / %d 边" % (len(cur_nodes), len(cur_edges)))
    print("delta  产出：")
    print("    节点 upsert %3d（其中 props 变更 %d / 新增节点 %d）"
          % (len(upsert_nodes), stat["nodes_changed"], stat["nodes_new"]))
    print("    边  新增   %3d" % len(new_edges))
    print("    节点 删除   %3d（孤儿 %d + 显式 %d）"
          % (len(del_ids), len(orphan_report), len(a.delete_nodes)))
    if changed_keys:
        print("    props 变更键分布：%s"
              % ", ".join("%s×%d" % (k, v) for k, v in changed_keys.most_common()))
    if protected:
        print("    [GUARD] 拦截 %d 处越权覆盖（种子不得改写权威出处/数值）:" % len(protected))
        for nid, g_src, blocked in protected[:8]:
            print("            %-24s 图谱出处=%-18s 拦下 %s"
                  % (nid, g_src, {k: v for k, v in blocked.items()}))
    if stat["dangling_skipped"]:
        print("    [WARN] 跳过悬空边 %d 条（端点不在图中）:" % stat["dangling_skipped"])
        for s in dangling_samples[:10]:
            print("           %s" % s)
    if orphan_report:
        print("    孤儿示例：%s%s" % (", ".join(orphan_report[:6]),
                                     " …" if len(orphan_report) > 6 else ""))
    print("=" * 84)

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(delta, f, ensure_ascii=False, indent=2)
    print("[OK] delta 已写出  %.1f KB" % (os.path.getsize(a.out) / 1024))
    print("     下一步：python 06_PoC/apply_delta.py --delta %s --phase <N> --tag audit --apply"
          % os.path.relpath(a.out, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
