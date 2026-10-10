#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""phase28_trust_delta.py —— 第 18 轮 · Claim/Evidence/Verification 轻量落地（只读生成 delta）

做三件事（全部**从源头恢复 / 由规则重算**，不改任何生成器逻辑之外的东西）：

1) **属性形态修复**（新发现缺陷，铁律 #15 家族）
   权威图里 5000 个 Formula 的 `dim_exponents` 与 14 个分子的 `composition` 存的是
   **Python repr 字符串**（`"{'mass': '1', ...}"`），而本地 ingest 产出的是 dict。
   溯源：早期 Phase 11/12 的「viz 投影 → 回写 raw」往返把投影层形态冻进了权威图。
   → 从 pristine 源恢复：`dim_exponents` 取自 `physicsbabel_raw.json`；
     `composition` 由 `formula` **独立解析**得到（同 `verification_model` 的解析器）。

2) **边级可信性分层落库**（北极星口径落地）
   为**每条边**写入 `verification_level` / `verification_scope` / `verifier` /
   `verified`（= level ≥ by_construction 的派生布尔，仅为向后兼容保留）。

3) **清理 1 条陈旧错挂边**
   `has_symbol|PB:fo:600|PQ:force`：无 `rationale`、量名不在该公式 exponents 中
   → 早期 ingest 的 `PB:fo:<i>` 是**位置型 id**，重跑改号后旧边残留（详见报告）。

4) **撤回虚假门禁归因**（铁律 #15：下游打补丁=源头有错）
   Phase 27 的「归因纠正」只改了**派生**的 `verification_scope`，却把**已存**的
   `verification_gate` 原样留在库里 —— 于是权威图里仍留着一句「某个从未跑过的门禁
   验过它」的假话（如 24104 条 `has_symbol` 挂 `R-PHY`，而 has_symbol 由组成成员
   核对产生、与量纲门禁无关）。本步对凡 `not _gate_matches_scope(gate, scope)` 的边
   执行：`verification_gate := "NONE"`，并把旧值留痕在 `verification_gate_withdrawn`。
   （由 Phase 28 冻结门禁 `P0-5-main-gate-attrib-consistent` 断言守卫。）

用法
----
    python phase28_trust_delta.py                       # 只读，打印统计
    python phase28_trust_delta.py --out <path> --dry    # 生成 delta 但不落盘
    python phase28_trust_delta.py --out <path>          # 生成 delta
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import verification_model as vm          # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
PB_RAW = os.path.join(HERE, "physicsbabel_raw.json")
OUT_DEFAULT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase28_trust_delta.json")

# 校验只写这些字段，避免把本地临时键（_qname/_tname）漏进 delta
PROP_KEYS = ("verification_level", "verification_scope", "verifier",
             "verified", "verification_note")


def load(p):
    return json.load(open(p, encoding="utf-8"))


def build_node_fixes(nodes):
    """返回 (node_props_fixes, stats)。"""
    fixes = {}
    stats = collections.Counter()

    # ---- (1a) dim_exponents：从 pristine PhysicsBabel raw 恢复为 dict ----
    pb = load(PB_RAW)
    pristine = {}
    for n in pb.get("nodes", []):
        de = (n.get("props") or {}).get("dim_exponents")
        if isinstance(de, dict) and de:
            pristine[n["id"]] = de
    for n in nodes:
        p = n.get("props") or {}
        if n["id"].startswith("PB:fo:") and isinstance(p.get("dim_exponents"), str):
            fix = pristine.get(n["id"])
            if fix:
                fixes.setdefault(n["id"], {})["dim_exponents"] = fix
                stats["dim_exponents_restored"] += 1
            else:
                stats["dim_exponents_unmatched"] += 1

    # ---- (1b) composition：由 formula 独立解析（不读被污染的字段） ----
    for n in nodes:
        p = n.get("props") or {}
        if isinstance(p.get("composition"), str):
            try:
                fixes.setdefault(n["id"], {})["composition"] = \
                    vm.parse_formula_independent(p.get("formula") or "")
                stats["composition_restored"] += 1
            except Exception:
                stats["composition_failed"] += 1
    return fixes, stats


def main():
    ap = argparse.ArgumentParser(description="Phase 28 可信性分层 delta 生成器（只读）")
    ap.add_argument("--graph", default=NORM, help="基准权威图（默认 normalized.json）")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--dry", action="store_true", help="只打印，不落盘")
    a = ap.parse_args()

    nodes, edges = vm.load_norm(a.graph)
    print("=" * 92)
    print("Phase 28 · 可信性分层 delta —— 基准 %s" % os.path.relpath(a.graph, ROOT))
    print("  %d 节点 / %d 边" % (len(nodes), len(edges)))
    print("=" * 92)

    # ---------- 1. 节点属性形态修复 ----------
    node_fixes, nstat = build_node_fixes(nodes)
    print("\n[1] 属性形态修复（repr 字符串 -> dict）")
    for k in ("dim_exponents_restored", "dim_exponents_unmatched",
              "composition_restored", "composition_failed"):
        print("    %-28s %d" % (k, nstat.get(k, 0)))

    # ---------- 2. 边可信性分层 ----------
    res, ctx = vm.classify_all(nodes, edges)
    lv = collections.Counter(r["verification_level"] for _, r in res)
    print("\n[2] 边级可信性分层（%d 条）" % len(res))
    for k in vm.LEVELS:
        print("    %-16s %6d  %5.1f%%" % (k, lv.get(k, 0), 100.0 * lv.get(k, 0) / len(res)))
    old_true = sum(1 for e in edges if (e.get("props") or {}).get("verified") is True)
    new_true = sum(1 for _, r in res if vm.is_verified(r["verification_level"]))
    strict = sum(1 for _, r in res if vm.is_strict(r["verification_level"]))
    print("    旧 verified=True %d  ->  新 verified %d  /  verified_strict %d"
          % (old_true, new_true, strict))

    edge_rows = []
    # 先定「待删边」——重标时须**排除**它们，否则 apply_delta 先删后并会把它们复活成
    # 只剩新属性的残缺边（顺序陷阱：delete 在 merge 之前执行）。
    del_keys = set()
    del_edges = []
    for e, r in res:
        if (r["verification_scope"] == "formula_participation_not_found"
                and e["type"] == "has_symbol"):
            del_keys.add((e["source"], e["type"], e["target"]))
            del_edges.append({"source": e["source"], "target": e["target"],
                              "type": e["type"],
                              "kind": (e.get("props") or {}).get("kind") or ""})
    print("\n[3] 待删陈旧错挂边：%d 条" % len(del_edges))
    for d in del_edges[:10]:
        print("    -", d["source"], "->", d["target"], "(%s)" % d["type"])

    stale_fixed = 0
    # 陈旧式串按**分子粒度**修正（同一分子的所有组成边共用一条式串）
    stale_srcs = set()
    for e, r in res:
        if (e["type"] == "composed_of"
                and r["verification_scope"] == "formula_count_cross_source"):
            eid = e.get("id") or "composed_of|%s|%s" % (e["source"], e["target"])
            if ctx["composed_ok"].get(eid, (None,))[0] is False:
                stale_srcs.add(e["source"])
    print("\n[2b] 陈旧式串涉及分子：%d 个" % len(stale_srcs))
    gate_withdrawn = 0
    for e, r in res:
        if (e["source"], e["type"], e["target"]) in del_keys:
            continue                      # 已删除的边不再重标（见上）
        props = {"verification_level": r["verification_level"],
                 "verification_scope": r["verification_scope"],
                 "verifier": r["verifier"],
                 "verified": vm.is_verified(r["verification_level"])}
        if r.get("note"):
            props["verification_note"] = r["note"]
        # 撤回虚假门禁归因（见模块 docstring 第 4 点）：库里已存的 gate 必须与
        # 派生 scope 配对合法；不合法者说明该门禁**从未**跑过这条边 → 撤回归因并留痕。
        old_gate = (e.get("props") or {}).get("verification_gate")
        if old_gate and old_gate != "NONE" \
                and not vm._gate_matches_scope(old_gate, r["verification_scope"]):
            props["verification_gate"] = "NONE"
            props["verification_gate_withdrawn"] = old_gate
            gate_withdrawn += 1
        # 式串陈旧修正：计数已与节点权威分子式一致，把式串改回权威值（消除自相矛盾）
        if e["type"] == "composed_of" and e["source"] in stale_srcs:
            eid = e.get("id") or "composed_of|%s|%s" % (e["source"], e["target"])
            bad = vm.formula_in_rationale((e.get("props") or {}).get("rationale"))
            auth = ctx["composed_auth_ok"].get(eid, (None, None, None))[1]
            if auth and bad != auth:
                sym = vm.sym_of_el_id(e["target"])
                props["from_formula"] = auth
                props["rationale"] = ("化学式 %s 含 %s×%s（组成解析；"
                                      "2026-10-10 修正陈旧式串「%s」）"
                                      % (auth, sym, (e["props"] or {}).get("count"), bad))
                stale_fixed += 1
        edge_rows.append({
            # 必须带 `id`：本地边 id 约定为 `type|src|tgt`，但仍有 5 条历史遗留自定义 id
            # （`gnn9v:...`）。若不带 id，apply_delta 会按 `type|src|tgt` 找不到它们
            # → 误判为「新增边」而**复制**出重复边（铁律 #1 的活体）。
            "id": e.get("id") or "%s|%s|%s" % (e["type"], e["source"], e["target"]),
            "source": e["source"], "target": e["target"], "type": e["type"],
            "kind": (e.get("props") or {}).get("kind") or e.get("kind") or "",
            "props": props,
        })
    print("[2b] composed_of 陈旧式串修正：%d 条" % stale_fixed)
    print("[2c] 撤回虚假门禁归因（gate := NONE）：%d 条" % gate_withdrawn)

    node_rows = [{"id": nid, "props": props} for nid, props in sorted(node_fixes.items())]
    delta = {
        "meta": {
            "phase": 28,
            "tag": "trust",
            "generator": "phase28_trust_delta.py",
            "base_graph": os.path.relpath(a.graph, ROOT),
            "nodes_prop_fix": len(node_rows),
            "edges_relabeled": len(edge_rows),
            "delete_edges": len(del_edges),
            "gate_attrib_withdrawn": gate_withdrawn,
            "level_distribution": dict(lv),
            "verified_true_before": old_true,
            "verified_after": new_true,
            "verified_strict_after": strict,
        },
        "nodes": node_rows,
        "delete_nodes": [],
        "edges": edge_rows,
        "delete_edges": del_edges,
    }

    print("\n[4] delta 规模：节点属性 %d / 边重标 %d / 删边 %d"
          % (len(node_rows), len(edge_rows), len(del_edges)))
    if a.dry:
        txt = json.dumps(delta, ensure_ascii=False)
        print("    [DRY] 未落盘；紧凑 JSON 大小约 %.1f MB" % (len(txt.encode('utf-8')) / 1048576.0))
        return 0
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(delta, f, ensure_ascii=False)
    mb = os.path.getsize(a.out) / 1048576.0
    print("    已写 %s（%.1f MB）" % (os.path.relpath(a.out, ROOT), mb))
    return 0


if __name__ == "__main__":
    sys.exit(main())
