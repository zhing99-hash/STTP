#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""phase33_indep_delta.py —— 第 23 轮 · T9-i 证据独立攻坚（Evidence Independence）

本轮命题
--------
第 22 轮新增 T9「证据**可追溯**」（100.0%）时，同时**分列**出更强的口径
**T9-i 证据独立**（`indep=True`）= **34771/40482 = 85.9%**，并把 5711 条「达标但不独立」
标为**下一轮靶子**。侦察（`06_PoC/_recon_phase33_*.py`）证明这 5711 条：

  · **全部** level=`source_asserted`、kind=`source_assertion`；
  · **全部**落在 T4（反应方向 5275）/ T6（常量 82）/ T8（文献 354）—— 三族门槛都是
    `source_asserted`（"来源自述即可"），缺口是**结构性**的。

三条可行性结论（只读实测）
------------------------
  · **T4**：朴素「重解析方程」是**同源自证**（适配器正由 `equation` 推 `n_left` 再切
    `chebi-id`，`rhea_ingest.py` 第 355~378 行）→ 否定。**真·跨源路径**：用 **ChEBI 本体的
    label/formula** 校验 **Rhea 方程**给出的侧别（两个不同数据库）→ 升 **2773 条**
    （label 1784 + formula 989），**both=22（歧义·不采信）、wrong=0**。
  · **T6**：`constant_derivation` 用**定义式**读输入常量值复算被派生常量 → 升 **18 条**
    （rel 1e-11 ~ 1e-9）。`constant_unit`(6) 量名不在真值表 → **负结果**（不无依据升档）。
  · **T8**：`cites` 无第二索引、`discusses` 仅名字包含（同源）→ **负结果**。

产物（**非破坏性**：`delete_edges: []`，只更新判级与证据对象）
--------------------------------------------------------------
  06_PoC/etl/neo4j/phase33_indep_delta.json

★ 同源保证（铁律 #36）：档位/证据**全部**由 `verification_model.classify_all` 重算，
  本脚本**不新增任何判据**，只做「对比存储 → 搬运变更」。

用法
----
  python 11_真实数据/phase33_indep_delta.py [--dry]
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
sys.path.insert(0, os.path.join(ROOT, "06_PoC"))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import verification_model as vm              # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase33_indep_delta.json")

EXPECT_UPGRADE = {"equation_species_cross_source": 2773, "codata_definition_recompute": 18}


def eid_of(e):
    return e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))


def convert_legacy(ev, r):
    """遗留 `evidence`（str / list）→ 对象形式；kind/indep 继承该边判级（同源）。"""
    if ev is None:
        return []
    lv, sc = r["verification_level"], r["verification_scope"]
    kind, indep = vm.SCOPE_KIND.get(sc) or vm._fallback_kind(lv)
    items = ev if isinstance(ev, list) else [ev]
    out = []
    for s in items:
        if s is None:
            continue
        txt = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)
        out.append({"kind": kind, "impl": r["verifier"], "detail": txt, "indep": bool(indep)})
    return out


def main():
    ap = argparse.ArgumentParser(description="Phase 33 T9-i 证据独立攻坚 delta")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    d = json.load(open(NORM, encoding="utf-8"))
    nodes, edges = d["nodes"], d["edges"]
    N = {n["id"]: n for n in nodes}
    print("=" * 92)
    print("Phase 33 delta —— T9-i 证据独立攻坚")
    print("  输入 %d 节点 / %d 边" % (len(nodes), len(edges)))
    print("=" * 92)

    res, _ctx = vm.classify_all(nodes, edges)
    stored = {eid_of(e): e for e in edges}
    stamp = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")

    out_edges, downgrades, migrations = [], [], collections.Counter()
    sc_counter = collections.Counter()
    indep_missing = 0          # 升档后仍无独立证据（应为 0）
    detail_empty = 0
    impl_mismatch = 0

    for e, r in res:
        eid = eid_of(e)
        p = (stored.get(eid) or {}).get("props") or {}
        old_lv, old_sc = p.get("verification_level"), p.get("verification_scope")
        new_lv, new_sc = r["verification_level"], r["verification_scope"]
        if old_lv == new_lv and old_sc == new_sc:
            continue
        if vm.RANK.get(new_lv, -1) < vm.RANK.get(old_lv, -1):
            downgrades.append((eid, old_lv, new_lv, new_sc))
            continue
        migrations[(old_lv, new_lv, new_sc)] += 1
        sc_counter[new_sc] += 1

        entries = convert_legacy(p.get("evidence"), r)
        entries.extend(vm.build_evidence(r))
        if not any(it.get("indep") for it in entries):
            indep_missing += 1
        if any(not it.get("detail") for it in entries):
            detail_empty += 1
        if any(it.get("impl") != r.get("verifier") for it in entries):
            impl_mismatch += 1

        out_edges.append({
            "id": eid, "source": e["source"], "target": e["target"],
            "type": e.get("type"), "kind": p.get("kind") or e.get("kind") or "",
            "props": {
                "verification_level": new_lv,
                "verification_scope": new_sc,
                "verifier": r["verifier"],
                "verified": vm.is_verified(new_lv),
                "verification_note": "Phase33 独立证据：%s（原 %s/%s）"
                                     % ((r.get("detail") or new_sc)[:140], old_lv, old_sc),
                "claim": vm.claim_of(e, N),
                "verification_evidence": entries,
                "evidence_at": stamp,
            },
        })

    print("\n【升档迁移】")
    for k, v in sorted(migrations.items(), key=lambda x: -x[1]):
        print("   %-16s -> %-14s %-34s %5d" % (k[0], k[1], k[2], v))
    print("\n【降级】（应为 0）: %d" % len(downgrades))
    for x in downgrades[:10]:
        print("   ⚠", x)

    lv_after = collections.Counter()
    for e, r in res:
        lv_after[r["verification_level"]] += 1
    print("\n【升档后分层分布】")
    for k in vm.LEVELS:
        print("   %-16s %6d" % (k, lv_after.get(k, 0)))

    # ---- 自检（铁律 #38：非真空断言） ----
    ok_shape = (indep_missing == 0 and detail_empty == 0 and impl_mismatch == 0)
    ok_expect = all(sc_counter.get(k, 0) == v for k, v in EXPECT_UPGRADE.items())
    print("\n【自检】")
    print("  升档边数 = %d（预期 %d）%s"
          % (sum(sc_counter.values()), sum(EXPECT_UPGRADE.values()),
             "✅" if ok_expect else "❌"))
    print("  升档后无独立证据 / detail 空 / impl≠verifier = %d / %d / %d %s"
          % (indep_missing, detail_empty, impl_mismatch, "✅" if ok_shape else "❌"))
    print("  规模零变化（非破坏性）: %d 节点 / %d 边 %s"
          % (len(nodes), len(edges), "✅" if len(edges) == 48712 else "❌"))

    payload = {
        "meta": {
            "phase": 33, "tag": "evidence-independence", "generator": "phase33_indep_delta.py",
            "base_graph": os.path.relpath(NORM, ROOT),
            "theme": "T9-i 证据独立攻坚：用跨源/复算把 T4/T6 的 source_asserted 升为独立证据",
            "evidence_at": stamp,
            "upgrades": sum(sc_counter.values()),
            "upgrade_breakdown": {k: sc_counter.get(k, 0) for k in EXPECT_UPGRADE},
            "downgrades": len(downgrades),
            "edges_withdrawn": 0,
            "level_distribution_after": {k: lv_after.get(k, 0) for k in vm.LEVELS},
        },
        "nodes": [],
        "delete_nodes": [],
        "edges": out_edges,
        "delete_edges": [],
    }
    print("\n【delta 汇总】edges=%d  delete_edges=0（非破坏性）" % len(out_edges))
    if a.dry:
        print("[DRY] 未落盘")
        return 0
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print("-> %s (%.1f MB)" % (os.path.relpath(a.out, ROOT), os.path.getsize(a.out) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
