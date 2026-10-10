#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""phase34_alias_delta.py —— 第 24 轮 · T4 残差「跨源名称对齐」攻坚（Alias Cross-Source）

本轮命题
--------
第 23 轮把 T9-i 证据独立拉到 **92.8%**（37564/40484），残差 **2920** 条集中在
T4（反应方向，Rhea 1761 + ElementKG2.0 707 + curated_seed 71）/ T8（文献 354）/ T6（常量 64）。

只读侦察（`06_PoC/_recon_phase34_*.py`）给出的结论：

  · **真·第二「反应」源不可行（负结果）**：KEGG 的 `link/rhea/*` 端点全空、FTP 不可达
    ⇒ 批量 Rhea→KEGG 映射不可得（需 ~11k 次单请求）。Reactome 的 Rhea 映射文件 404。
  · **残差根因是「命名」而非「缺源」**：Rhea 方程写 `pentanoate`，ChEBI 主名是 `valerate`
    （同物异名）；`ammonium` vs `NH4(+)`；质子化/电荷变体。
  · **可达的真·第二源 = ChEBI 本体（OLS4 同义词集）** —— 与 Rhea 是**不同数据库**。
    全量预演：`label ∪ synonyms`（归一后长度 ≥3）→ **升 1218 / WRONG 0 / both 31 / nomatch 507**。

产物（**非破坏性**：`delete_edges: []`，只更新判级与证据对象）
--------------------------------------------------------------
  06_PoC/etl/neo4j/phase34_alias_delta.json

★ 同源保证（铁律 #36）：档位/证据**全部**由 `verification_model.classify_all` 重算，
  本脚本**不新增任何判据**，只做「对比存储 → 搬运变更」。

用法：python 11_真实数据/phase34_alias_delta.py [--dry]
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
OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase34_alias_delta.json")

EXPECT_UPGRADE = {"equation_species_cross_source": 1218}


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
    ap = argparse.ArgumentParser(description="Phase 34 T4 跨源名称对齐 delta")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    d = json.load(open(NORM, encoding="utf-8"))
    nodes, edges = d["nodes"], d["edges"]
    N = {n["id"]: n for n in nodes}
    print("=" * 92)
    print("Phase 34 delta —— T4 残差跨源名称对齐（ChEBI 同义词 × Rhea 方程）")
    print("  输入 %d 节点 / %d 边" % (len(nodes), len(edges)))
    print("=" * 92)

    res, _ctx = vm.classify_all(nodes, edges)
    stored = {eid_of(e): e for e in edges}
    stamp = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")

    out_edges, downgrades = [], []
    migrations = collections.Counter()
    sc_counter = collections.Counter()
    crit = collections.Counter()          # 判据分布（从 verifier 串里提取）
    n_generic = 0
    indep_missing = detail_empty = impl_mismatch = 0
    no_alias_cache = not bool(getattr(vm, "_CHEBI_SYN", {}))

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
        ver = r.get("verifier") or ""
        for c in ("synonym", "formula", "label"):
            if "× ChEBI %s)" % c in ver:
                crit[c] += 1
                break
        if ((N.get(e["source"]) or {}).get("props") or {}).get("is_generic"):
            n_generic += 1

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
                "verification_note": "Phase34 跨源名称对齐：%s（原 %s/%s）"
                                     % ((r.get("detail") or new_sc)[:140], old_lv, old_sc),
                "claim": vm.claim_of(e, N),
                "verification_evidence": entries,
                "evidence_at": stamp,
            },
        })

    print("\n【升档迁移】")
    for k, v in sorted(migrations.items(), key=lambda x: -x[1]):
        print("   %-16s -> %-14s %-34s %5d" % (k[0], k[1], k[2], v))
    print("【判据分布】", dict(crit), " （其中参与物为泛称类 %d）" % n_generic)
    print("\n【降级】（应为 0）: %d" % len(downgrades))
    for x in downgrades[:10]:
        print("   ⚠", x)

    lv_after = collections.Counter()
    for _e, r in res:
        lv_after[r["verification_level"]] += 1
    print("\n【升档后分层分布】")
    for k in vm.LEVELS:
        print("   %-16s %6d" % (k, lv_after.get(k, 0)))

    ok_shape = (indep_missing == 0 and detail_empty == 0 and impl_mismatch == 0)
    ok_expect = all(sc_counter.get(k, 0) == v for k, v in EXPECT_UPGRADE.items())
    print("\n【自检】")
    print("  同义词缓存可用 = %s" % ("否（**退化为 Phase 33 口径**）" if no_alias_cache else "是"))
    print("  升档边数 = %d（预期 %d）%s"
          % (sum(sc_counter.values()), sum(EXPECT_UPGRADE.values()), "✅" if ok_expect else "❌"))
    print("  升档后无独立证据 / detail 空 / impl≠verifier = %d / %d / %d %s"
          % (indep_missing, detail_empty, impl_mismatch, "✅" if ok_shape else "❌"))
    print("  规模零变化（非破坏性）: %d 节点 / %d 边 %s"
          % (len(nodes), len(edges), "✅" if len(edges) == 48712 else "❌"))

    payload = {
        "meta": {
            "phase": 34, "tag": "alias-cross-source", "generator": "phase34_alias_delta.py",
            "base_graph": os.path.relpath(NORM, ROOT),
            "theme": "T4 残差跨源名称对齐：ChEBI 本体同义词（OLS4）× Rhea 方程侧别",
            "evidence_at": stamp,
            "upgrades": sum(sc_counter.values()),
            "upgrade_breakdown": {k: sc_counter.get(k, 0) for k in EXPECT_UPGRADE},
            "criteria_breakdown": dict(crit),
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
