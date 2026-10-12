# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
"""phase36_rescope_delta.py —— 第 26 轮 · 验证语义校正（GPT6 复评 R03/R04/R05）重定范围 delta

本轮命题
--------
GPT6 复评（`07_交付物/外部评审/STTP_复评与Apache2.0许可意见_20261011.md`）指出三类
「验证语义」错误，本 delta 把**已经改好的判级模型**（verification_model，同源铁律 #36）
在全图上重算并落盘：

  R03  Rhea×ChEBI 侧别校验是**共享词典的数据一致性检查**，不是独立证据
       → 3991 条 `cross_source/equation_species_cross_source` 降为
         `source_asserted/equation_species_consistency`（indep=False），T9-i 如实下调。
  R04a 算子出现 ≠ 推导关系：11 条模型数学桥 `derived_from` **正名**为 `uses_operator`
       （kind=math_operator_bridge；删旧边、加新边，铁律 #21 残留回查）。
  R04b formula 通道收窄为「仅候选」（同分异构不排除），不再升级、不再反驳。
  R05a webbook `n_references` 汇总计数无明细 → 不得升 cross_source（图内 0 条受影响）。
  R05b pubchem_bridge 两端 InChIKey 未落库 → 20 条降 rule_checked（沿用摄入时验证）。

与 Phase 34 的关键差别：**本轮降级是预期产物**（不是护栏）。输出含
`edges`（新增 + 更新，含降级更新）与 `delete_edges`（正名删旧）。

用法：python 11_真实数据/phase36_rescope_delta.py [--dry]
"""
from __future__ import annotations
import argparse
import collections
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import verification_model as vm                       # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase36_rescope_delta.json")

_STRIP_PROPS = ("verification_level", "verification_scope", "verifier", "verified",
                "verification_note", "verification_evidence", "claim", "evidence_at",
                "evidence")


def eid_of(e):
    return e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))


def operator_match(src_node, tgt_id):
    """R04a 正名判据（复用 vm 的既有谓词，**不新增判据**）：
    源表达式命中目标数学对象所辖算子 → 该 derived_from 实为 uses_operator。"""
    hits = vm.math_op_of(vm.expr_of_node(src_node))
    return [k for k in hits if str(tgt_id) in vm.MATH_OP_RULES[k][1]]


def main():
    ap = argparse.ArgumentParser(description="Phase 36 验证语义校正重定范围 delta")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    d = json.load(open(NORM, encoding="utf-8"))
    nodes, edges = d["nodes"], d["edges"]
    N = {n["id"]: n for n in nodes}
    print("=" * 92)
    print("Phase 36 delta —— 验证语义校正（R03 共享词典 / R04 正名与收窄 / R05 直通移除）")
    print("  输入 %d 节点 / %d 边" % (len(nodes), len(edges)))
    print("=" * 92)

    # ---------------- 1. R04a：derived_from 数学桥正名为 uses_operator ----------------
    renames = []          # (old_edge, new_edge, matched_rule)
    for e in edges:
        if e.get("type") != "derived_from":
            continue
        tgt = str(e.get("target"))
        if not tgt.startswith(("MA:", "MX:math")):
            continue
        matched = operator_match(N.get(e.get("source")), tgt)
        if not matched:
            continue
        p = dict(e.get("props") or {})
        for k in _STRIP_PROPS:
            p.pop(k, None)
        p["renamed_from"] = "derived_from"
        p["rename_reason"] = "R04：算子出现 ≠ 推导关系（GPT6 复评 2026-10-11）"
        new = {"id": "uses_operator|%s|%s" % (e["source"], e["target"]),
               "source": e["source"], "target": e["target"],
               "type": "uses_operator", "kind": "math_operator_bridge",
               "props": p}
        renames.append((e, new, matched[0]))
    ren_old_ids = {eid_of(o) for o, _n, _m in renames}
    print("\n【R04a 正名】derived_from → uses_operator：%d 条" % len(renames))
    for o, _n, m in renames:
        print("   %s -> %s  (%s)" % (eid_of(o), _n["id"], m))

    # ---------------- 2. 全量重算（R03/R04b/R05 已进判级模型） ----------------
    #    注意：classify_all 看到的是**正名后**的边集（uses_operator 才能走 A9）。
    edges2 = [e for e in edges if eid_of(e) not in ren_old_ids] + [n for _o, n, _m in renames]
    res, _ctx = vm.classify_all(nodes, edges2)
    stored = {eid_of(e): e for e in edges}
    stamp = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")

    out_edges = []
    mig = collections.Counter()          # (old_lv, new_lv, new_sc)
    scope_counter = collections.Counter()
    n_down = n_up = n_same_scope_chg = 0
    for e, r in res:
        eid = eid_of(e)
        old_p = (stored.get(eid) or {}).get("props") or {}
        old_lv, old_sc = old_p.get("verification_level"), old_p.get("verification_scope")
        new_lv, new_sc = r["verification_level"], r["verification_scope"]
        is_new = eid not in stored
        if not is_new and old_lv == new_lv and old_sc == new_sc:
            continue
        if is_new:
            mig[("(new)", new_lv, new_sc)] += 1
        else:
            mig[(old_lv, new_lv, new_sc)] += 1
            if vm.RANK.get(new_lv, -1) < vm.RANK.get(old_lv, -1):
                n_down += 1
            elif vm.RANK.get(new_lv, -1) > vm.RANK.get(old_lv, -1):
                n_up += 1
            else:
                n_same_scope_chg += 1
        scope_counter[new_sc] += 1
        entries = vm.build_evidence(r)
        out_edges.append({
            "id": eid, "source": e["source"], "target": e["target"],
            "type": e.get("type"),
            "kind": (e.get("props") or {}).get("kind") or e.get("kind") or "",
            "props": {
                "verification_level": new_lv,
                "verification_scope": new_sc,
                "verifier": r["verifier"],
                "verified": vm.is_verified(new_lv),
                "verification_note": "Phase36 验证语义校正：%s（原 %s/%s）"
                                     % ((r.get("detail") or new_sc)[:140], old_lv, old_sc),
                "claim": vm.claim_of(e, N),
                "verification_evidence": entries,
                "evidence_at": stamp,
            },
        })

    print("\n【判级迁移】（old_lv -> new_lv / new_scope）")
    for k, v in sorted(mig.items(), key=lambda x: -x[1]):
        print("   %-16s -> %-16s %-40s %5d" % (k[0], k[1], k[2], v))
    print("\n  升级 %d ｜ **降级 %d（R03/R05 预期产物）** ｜ 同级换 scope %d ｜ 新增(正名) %d"
          % (n_up, n_down, n_same_scope_chg, len(renames)))

    lv_after = collections.Counter()
    for _e, r in res:
        lv_after[r["verification_level"]] += 1
    print("\n【重算后分层分布】")
    for k in vm.LEVELS:
        print("   %-16s %6d" % (k, lv_after.get(k, 0)))

    # ---------------- 3. 自检 ----------------
    # (a) 正名后 derived_from→数学对象 的算子桥必须清零（铁律 #21 残留回查）
    resid = [e for e in edges2
             if e.get("type") == "derived_from"
             and str(e.get("target")).startswith(("MA:", "MX:math"))
             and operator_match(N.get(e["source"]), e["target"])]
    # (b) cross_source 的 reactant/product_of 必须清零（R03）
    cs_rxn = sum(1 for _e, r in res
                 if r["verification_level"] == "cross_source"
                 and _e.get("type") in ("reactant_of", "product_of"))
    # (c) equation_species_consistency 条数（R03 主迁移目标）
    n_cons = scope_counter.get("equation_species_consistency", 0)
    print("\n【自检】")
    print("  正名残留（derived_from→数学 且有算子匹配）= %d %s"
          % (len(resid), "✅" if not resid else "❌ %s" % resid[:3]))
    print("  cross_source 的 reactant/product_of = %d %s" % (cs_rxn, "✅" if cs_rxn == 0 else "❌"))
    print("  equation_species_consistency = %d（预期 ≈3991 - formula 通道收窄）" % n_cons)
    print("  规模：节点 %d（不变）｜边 %d → %d（正名：-%d +%d，净 0）"
          % (len(nodes), len(edges), len(edges2), len(renames), len(renames)))

    payload = {
        "meta": {
            "phase": 36, "tag": "verification-semantics-rescope",
            "generator": "phase36_rescope_delta.py",
            "base_graph": os.path.relpath(NORM, ROOT),
            "theme": "GPT6 复评 R03/R04/R05：共享词典一致性重定 / 算子桥正名 / 标签直通移除",
            "evidence_at": stamp,
            "renames_uses_operator": len(renames),
            "upgrades": n_up, "downgrades": n_down,
            "same_level_scope_changes": n_same_scope_chg,
            "scope_breakdown": dict(scope_counter),
            "level_distribution_after": {k: lv_after.get(k, 0) for k in vm.LEVELS},
        },
        "nodes": [],
        "delete_nodes": [],
        "edges": out_edges,
        "delete_edges": [{"id": i} for i in sorted(ren_old_ids)],
    }
    print("\n【delta 汇总】edges=%d（含正名新增 %d）｜ delete_edges=%d（正名删旧）"
          % (len(out_edges), len(renames), len(ren_old_ids)))
    if a.dry:
        print("[DRY] 未落盘")
        return 0
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print("-> %s (%.1f MB)" % (os.path.relpath(a.out, ROOT), os.path.getsize(a.out) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
