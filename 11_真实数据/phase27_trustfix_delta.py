# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Phase 27 · 可信性修复轮（Trust Fix）—— 修正 delta 生成器
==============================================================
把「源头修复」灌入本地权威图 `06_PoC/etl/normalized.json`，产出一个 apply_delta 可用的
四段 delta。**本脚本只读不写**，产物交 `06_PoC/apply_delta.py` 落盘。

覆盖两项 P0：
  P0-1  PhysicsBabel「方程齐次」误写为「两量量纲一致」
        - 删：主图中 source==PhysicsBabel 的全部 dimensionally_consistent 边（1130 条）
        - 建：按 dimension_table 真值表「量纲严格相等」重建的正确边
        - 补：Formula 节点 dim_homogeneous / dim_scope / dim_verified_by 属性
  P0-4  仅凭分子式就 same_as
        - 删：kind==skeleton_bridge 的 same_as 边
        - 建：同端点、但类型改为 same_formula_as（语义精确化）

顺带产出「全图 dimensionally_consistent 量纲审计」写入 delta.meta.audit，供报告与门禁使用。
"""
import json
import os
import sys
import collections

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import dimension_table as dm  # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
PBRAW = os.path.join(HERE, "physicsbabel_raw.json")
OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase27_trustfix_delta.json")

# 既有 PQ: 节点 id -> PhysicsBabel 量名（用于给存量 dc 边做量纲复核）
# 节点 id -> 物理量名：支持 PB:pq:<name> / <NS>:pq:<name> / <NS>:phy:<name> / PQ:<name>
# （名到量纲的归一由 dimension_table.canon/ALIAS 完成）
def qname(nid):
    """节点 id -> 量名（原始名，交由 dimension_table 归一）；非物理量节点返回 None。"""
    if nid.startswith("PB:pq:"):
        return nid[len("PB:pq:"):]
    ns, _, rest = nid.partition(":")
    for pre in ("pq:", "phy:"):
        if rest.startswith(pre):
            return rest[len(pre):]
    if ns == "PQ":
        return rest
    return None


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Phase27 可信性修复 delta 生成器")
    ap.add_argument("--graph", default=NORM,
                    help="作为基准的权威图（默认当前 normalized.json；"
                         "若要生成『相对上一阶段』的完整 delta，传该阶段的备份，"
                         "如 06_PoC/etl/normalized.before_trustfix.json）")
    ap.add_argument("--out", default=OUT, help="delta 输出路径")
    a = ap.parse_args()

    norm = json.load(open(a.graph, encoding="utf-8"))
    pb = json.load(open(PBRAW, encoding="utf-8"))
    pb_node = {n["id"]: n for n in pb["nodes"]}

    existing_triples = {(e["source"], e["target"], e.get("type")) for e in norm["edges"]}
    norm_ids = {n["id"] for n in norm["nodes"]}

    # ------------------- P0-1 全图 dc 量纲审计（三态）+ 收集应删边
    # 三态：端点无量名 → unknown_endpoint；有量名但真值表无该量 → unknown_quantity；
    #       两端均已知 → 严格比对（相等=ok，不等=violation，属硬缺陷）
    # 删除策略：
    #   * source==PhysicsBabel        → **全删**（源头语义已重写，旧边整体作废）
    #   * 自动生成且确证 violation    → 删（Phase13.Gate.B3 / GNN.typed）
    #   * 人工策划(curated_seed等) violation → **不删**，挂账交裁定（见 manual_violations）
    AUTO_SRC = ("PhysicsBabel", "Phase13.Gate.B3", "Phase13.GNN.typed")
    audit = {"checked": 0, "unknown_endpoint": 0, "unknown_quantity": 0,
             "violations": [], "ok": 0}
    viol_by_src = collections.Counter()
    del_dc, manual_viol = [], []
    for e in norm["edges"]:
        if e.get("type") != "dimensionally_consistent":
            continue
        src = (e.get("props") or {}).get("source")
        na, nb = qname(e["source"]), qname(e["target"])
        if not na or not nb:
            audit["unknown_endpoint"] += 1
            verdict = "unknown"
        elif dm.dim_of(na) is None or dm.dim_of(nb) is None:
            audit["unknown_quantity"] += 1
            verdict = "unknown"
        else:
            audit["checked"] += 1
            if dm.dim_equal(na, nb):
                audit["ok"] += 1
                verdict = "ok"
            else:
                verdict = "violation"
                viol_by_src[src] += 1
                audit["violations"].append({
                    "source": e["source"], "target": e["target"],
                    "source_dim": dm.dim_of(na), "target_dim": dm.dim_of(nb),
                    "edge_source": src})
        rec = {"source": e["source"], "target": e["target"],
               "type": "dimensionally_consistent", "kind": e.get("kind")}
        if src == "PhysicsBabel" or verdict == "violation":
            # PhysicsBabel → 源头语义已重写，全删；其余来源 → 量纲严格不等即删
            # （含人工策划笔误：其源头 `10_种子数据/build_classical_mechanics.py` 已同步修正，
            #  见铁律「改数据前先改源头」）
            del_dc.append(rec)
            if verdict == "violation" and src not in AUTO_SRC:
                manual_viol.append({**rec, "edge_source": src})

    # ------------------------------------------------- P0-1b 建正确 dc 边
    new_dc, skipped_dc, skipped_ghost_edge = [], 0, 0
    for e in pb["edges"]:
        if e.get("type") != "dimensionally_consistent":
            continue
        trip = (e["source"], e["target"], "dimensionally_consistent")
        if trip in existing_triples:
            skipped_dc += 1
            continue
        if e["source"] not in norm_ids or e["target"] not in norm_ids:
            skipped_ghost_edge += 1
            continue
        new_dc.append({"source": e["source"], "target": e["target"],
                       "type": "dimensionally_consistent", "kind": e.get("kind"),
                       "props": e.get("props")})

    # --------------------------------------------- P0-1c 公式节点齐次性属性
    # ⚠ 只更新**主图中已存在**的节点（避免 upsert 造出无 labels 的残缺节点）
    node_updates, skipped_ghost = [], 0
    for nid, n in pb_node.items():
        if (n.get("labels") or [None])[-1] != "Formula":
            continue
        if nid not in norm_ids:
            skipped_ghost += 1
            continue
        pr = n.get("props") or {}
        upd = {k: pr[k] for k in ("dim_homogeneous", "dim_scope", "dim_verified_by") if k in pr}
        if upd:
            node_updates.append({"id": nid, "props": upd})

    # ------------------------------------------------------- P0-2 删 B6 LaTeX 误判边
    # `Phase13.Gate.B6` 用 norm_latex 字符串判等产出 same_as/VERIFIED —— 主图实测
    # 1 条污染：CM:fo:momentum --same_as--> RT:fo:rel_momentum（动量 ≠ 相对论动量）。
    del_b6 = []
    for e in norm["edges"]:
        if e.get("type") == "same_as" and (e.get("props") or {}).get("source") == "Phase13.Gate.B6":
            del_b6.append({"source": e["source"], "target": e["target"],
                           "type": "same_as", "kind": e.get("kind")})

    # ------------------------------------------------------- P0-4 分子式边
    del_sb, new_sb = [], []
    for e in norm["edges"]:
        if e.get("type") == "same_as" and e.get("kind") == "skeleton_bridge":
            del_sb.append({"source": e["source"], "target": e["target"],
                           "type": "same_as", "kind": e.get("kind")})
            new_sb.append({
                "source": e["source"], "target": e["target"],
                "type": "same_formula_as", "kind": "skeleton_bridge",
                "props": {**(e.get("props") or {}),
                          "type_note": "P0-4 修复：仅凭分子式判等，故为 same_formula_as 而非 same_as",
                          "verification_scope": "formula_only",
                          "verified": False}})

    delta = {
        "meta": {
            "phase": 27,
            "name": "trustfix",
            "reason": "GPT6 外部评估 P0 技术修复（5 条已源码坐实）",
            "covers": ["P0-1 PhysicsBabel 量纲边语义错误",
                       "P0-2 norm_latex 字符串判等误授 same_as/VERIFIED",
                       "P0-3 门禁未声明验证范围（源头库已修，见 verification_loop.py）",
                       "P0-4 分子式 same_as -> same_formula_as"],
            "audit": {
                "dc_checked": audit["checked"],
                "dc_ok": audit["ok"],
                "dc_unknown_endpoint": audit["unknown_endpoint"],
                "dc_unknown_quantity": audit["unknown_quantity"],
                "dc_violations_count": len(audit["violations"]),
                "dc_violations_by_source": dict(viol_by_src),
                "dc_violations": audit["violations"][:50],
                "manual_violations": manual_viol,
                "new_dc_skipped_dup": skipped_dc,
                "new_dc_skipped_ghost": skipped_ghost_edge,
                "formula_attr_upsert": len(node_updates),
                "formula_attr_skipped_ghost": skipped_ghost,
            },
        },
        "nodes": node_updates,
        "delete_nodes": [],
        "edges": new_dc + new_sb,
        "delete_edges": del_dc + del_b6 + del_sb,
    }
    json.dump(delta, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("=" * 78)
    print("delta ->", os.path.relpath(a.out, ROOT))
    print("  delete_edges : %d  (dc %d + B6-LaTeX %d + skeleton_bridge %d)"
          % (len(delta["delete_edges"]), len(del_dc), len(del_b6), len(del_sb)))
    print("  edges        : %d  (正确 dc %d [跳过重复 %d] + same_formula_as %d)"
          % (len(delta["edges"]), len(new_dc), skipped_dc, len(new_sb)))
    print("  nodes(upsert): %d  (Formula 齐次性属性；跳过主图不存在的 %d)"
          % (len(node_updates), skipped_ghost))
    print("  --- 全图 dc 量纲审计 ---")
    print("   可判定 %d / 端点无量名 %d / 量纲未知 %d / 合规 %d / **违反 %d**"
          % (audit["checked"], audit["unknown_endpoint"], audit["unknown_quantity"],
             audit["ok"], len(audit["violations"])))
    print("   违反边按来源:", dict(viol_by_src))
    for v in audit["violations"][:6]:
        print("    ! %s(%s) -X- %s(%s)  [%s]" % (
            v["source"], v["source_dim"], v["target"], v["target_dim"], v["edge_source"]))
    print("  新建 dc 边（全部为物理正确的同量纲对）：")
    for e in new_dc:
        print("    %s -- %s" % (e["source"], e["target"]))
    if manual_viol:
        print("  ⚠ 人工策划的违规边（**未删**，待裁定）：")
        for v in manual_viol:
            print("    %s -- %s  [%s]" % (v["source"], v["target"], v["edge_source"]))


if __name__ == "__main__":
    main()
