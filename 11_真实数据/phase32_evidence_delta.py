#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""phase32_evidence_delta.py —— 第 22 轮 · Claim / Evidence 完整对象化

本轮命题
--------
第 21 轮遗留⑤：「`evidence[]` 仅落到跨域桥边、未推广全图」。
侦察（`06_PoC/_recon_phase32_*.py`）结论：
  · `verification_level/scope/verifier` 已 **100% 覆盖**，但那是**标签**
    ——「谁验的 / 怎么验的」的**名字**，不是**证据数据**；
  · `evidence` 仅 **1.18%**（575 条，400 str + 175 list，**形态不统一**）、`created_at` 仅 **1.9%**；
  · 但 `build_ctx` **早已算出每条边的证据明细串**（如「独立解析 CH4 中 C=1 vs 记录 1」），
    **除判否外全部被丢弃**。
→ 故本轮 = **把已算出的证据持久化为「一等对象」**（非重算）。

一等对象（落在边 props）
------------------------
  claim                 断言的可读陈述（`verification_model.claim_of`）
  verification_evidence 证据链：list of {kind, impl, detail, indep}
  evidence_at           证据生成时刻（ISO8601）

★ 同源保证（铁律 #36）：kind/indep 由 `SCOPE_KIND[scope]` 唯一决定，detail 由 `classify`
  在**判定现场**给出 —— 本脚本**不重新推导任何判据**，只做「搬用 + 落盘」。

★ 非破坏性：**不删**任何字段；遗留的 `evidence`（575 条）**转为对象形式**后并入
  `verification_evidence` 的**首位**（保留 Phase 29 WebBook 的「独立复算 + PubChem 第二源」原样）。

用法
----
  python 11_真实数据/phase32_evidence_delta.py [--dry]
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
OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase32_evidence_delta.json")


def eid_of(e):
    return e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))


def convert_legacy(ev, r):
    """把遗留 `evidence`（str / list，形态不统一）转为**对象形式**。

    kind/indep **继承**该边的判级结果（同源）；impl 用该边的 verifier。
    """
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
    ap = argparse.ArgumentParser(description="Phase 32 Claim/Evidence 对象化 delta")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    d = json.load(open(NORM, encoding="utf-8"))
    nodes, edges = d["nodes"], d["edges"]
    N = {n["id"]: n for n in nodes}
    print("=" * 92)
    print("Phase 32 delta —— Claim/Evidence 完整对象化")
    print("  输入 %d 节点 / %d 边" % (len(nodes), len(edges)))
    print("=" * 92)

    res, _ctx = vm.classify_all(nodes, edges)
    stamp = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")

    out_edges = []
    stat = collections.Counter()
    indep_edges = 0
    detail_empty = 0
    kind_counter = collections.Counter()
    impl_mismatch = 0
    legacy_merged = 0

    for e, r in res:
        p = (e.get("props") or {})
        legacy = p.get("evidence")
        entries = convert_legacy(legacy, r)
        if entries:
            legacy_merged += 1
        entries.extend(vm.build_evidence(r))

        claim = vm.claim_of(e, N)
        # 统计（只读检查，不阻断生成）
        n_indep = sum(1 for it in entries if it.get("indep"))
        if n_indep:
            indep_edges += 1
        if any(not it.get("detail") for it in entries):
            detail_empty += 1
        if any(it.get("impl") != r.get("verifier") for it in entries if it.get("kind") != "source_assertion"):
            # 仅记录「同源 impl」异常（遗留项的 impl 也取自 verifier，故正常应全一致）
            impl_mismatch += 1
        for it in entries:
            kind_counter[it["kind"]] += 1
        stat[r["verification_level"]] += 1

        out_edges.append({
            "id": eid_of(e), "source": e["source"], "target": e["target"],
            "type": e.get("type"), "kind": p.get("kind") or e.get("kind") or "",
            "props": {
                "claim": claim,
                "verification_evidence": entries,
                "evidence_at": stamp,
            },
        })

    n_indep_strict = sum(
        1 for e, r in res
        if vm.RANK.get(r["verification_level"], -1) >= vm.STRICT_MIN
        and any(it.get("indep") for it in
                convert_legacy((e.get("props") or {}).get("evidence"), r)
                + vm.build_evidence(r)))
    n_strict = sum(1 for _, r in res
                   if vm.RANK.get(r["verification_level"], -1) >= vm.STRICT_MIN)

    print("\n【证据链生成】")
    print("  覆盖边数          : %d / %d (100%%)" % (len(out_edges), len(edges)))
    print("  证据条目总数      : %d" % sum(kind_counter.values()))
    print("  并入遗留 evidence : %d 条（Phase 29 WebBook 多源证据等）" % legacy_merged)
    print("  detail 为空的边   : %d" % detail_empty)
    print("  impl 与 verifier 不一致（非 source_assertion）: %d" % impl_mismatch)
    print("\n【证据 kind 分布】")
    for k, n in kind_counter.most_common():
        print("     %-18s %6d" % (k, n))
    print("\n【不变量预检（门禁将重复断言）】")
    print("  带 indep=真 证据的边 : %d / %d" % (indep_edges, len(edges)))
    print("  level>=rule_checked 且带 indep 证据 : %d / %d  %s"
          % (n_indep_strict, n_strict, "✅" if n_indep_strict == n_strict else "❌"))
    print("\n【分层分布（应与 Phase 31 完全一致 —— 本轮不改判级）】")
    for k in vm.LEVELS:
        print("     %-16s %6d" % (k, stat.get(k, 0)))

    payload = {
        "meta": {
            "phase": 32, "tag": "claim-evidence-object", "generator": "phase32_evidence_delta.py",
            "base_graph": os.path.relpath(NORM, ROOT),
            "theme": "Claim/Evidence 完整对象化：把已算出的证据明细持久化为一等对象",
            "evidence_kinds": vm.EVIDENCE_KINDS,
            "evidence_at": stamp,
            "edges_with_evidence": len(out_edges),
            "evidence_entries_total": sum(kind_counter.values()),
            "kind_distribution": {k: kind_counter.get(k, 0) for k in vm.EVIDENCE_KINDS},
            "legacy_evidence_merged": legacy_merged,
            "strict_edges_with_indep_evidence": n_indep_strict,
            "strict_edges_total": n_strict,
            "level_distribution_after": {k: stat.get(k, 0) for k in vm.LEVELS},
        },
        "nodes": [],
        "delete_nodes": [],
        "edges": out_edges,
        "delete_edges": [],
    }
    print("\n【delta 汇总】edges=%d  delete_edges=0（非破坏性，只加字段）" % len(out_edges))
    if a.dry:
        print("[DRY] 未落盘")
        return 0
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print("-> %s (%.1f MB)" % (os.path.relpath(a.out, ROOT), os.path.getsize(a.out) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
