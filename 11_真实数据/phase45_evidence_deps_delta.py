# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
"""phase45_evidence_deps_delta.py —— Phase 45 · A04：把「验证依赖」写进图

做什么
------
给每条 `verification_evidence` 补上**记录时刻的依赖快照**（紧凑编码见
`evidence_deps.recorded_deps` 文档头）：

    evd  evidence_id（`evd:<16 hex>`，确定性）
    refs {ref_id: 内容哈希}      —— 参考表依赖（A04b 的核心修复）
    vfr  verifier_id             —— 验证器身份（A04a 的核心修复）
    vch  验证器 code_hash
    dvs  {rel: 依赖文件哈希}      —— 验证器的直接本地依赖版本
    chk  checked_at（本轮统一时间戳，写在 meta 亦可，这里逐条写以便逐条复核）

**非破坏性**：不增删任何边、不改任何档位/结论、不改 `src`/`src_hash`/`impl`/`impl_ver`
（那四个字段的写入仍归 `phase41_lineage_delta.py`，本轮**完全不碰**）。
**幂等**：复跑 0 改动。

★ 为什么要落「记录时刻的哈希」而不是「现算」
-------------------------------------------
`status` 是**读取时现算**的（`evidence_deps.evidence_status`），现算才不会被写坏。
但现算需要**记录侧**的值作对照 —— 只记 ref_id 而不记当时的哈希，就永远比不出「变更」。
故本 delta 记录的是**生成时刻**的仓库 blob 摘要；此后任何参考表/验证器改动都会被
失效器与查询层看见（这正是 A04 验收要的「变异量纲表/元素表/验证器 ⇒ 定位受影响证据」）。

用法
----
    python 11_真实数据/phase45_evidence_deps_delta.py [--base <graph>] [--dry] [--out <path>]
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

import evidence_deps as ed                              # noqa: E402
import source_registry as sr                            # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase45_evidence_deps_delta.json")

# 只写这三个键到 evidence（verifier 身份在 meta 里另存一份供人读）
EV_KEYS = ("evd", "refs", "vfr", "vch", "dvs")


def eid_of(e):
    return e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))


def main():
    ap = argparse.ArgumentParser(description="Phase 45 证据依赖绑定 delta（A04）")
    ap.add_argument("--base", default=NORM)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    base = os.path.abspath(a.base)
    d = json.load(open(base, encoding="utf-8"))
    nodes, edges = d["nodes"], d["edges"]

    ref_hashes = ed.reference_hashes(ROOT)
    vident = ed.verifier_identity(base=ROOT)
    stamp = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")

    print("=" * 92)
    print("Phase 45 delta —— 把「验证依赖」写进图（A04：参考表 / 验证器 / 依赖版本）")
    print("  基线 %s" % os.path.relpath(base, ROOT))
    print("  输入 %d 节点 / %d 边" % (len(nodes), len(edges)))
    print("=" * 92)
    print("  参考表内容哈希（仓库 blob 口径）：")
    for rid, h in ref_hashes.items():
        print("    %-22s %s" % (rid, h))
    print("  验证器身份：")
    for vid, v in vident.items():
        print("    %-22s code=%s deps=%s" % (vid, v["code_hash"],
                                              {os.path.basename(k): x for k, x in
                                               (v["dependency_versions"] or {}).items()}))

    out_edges = []
    n_ev_total = 0
    n_ev_touched = 0
    ref_c = collections.Counter()
    vfr_c = collections.Counter()
    no_dep = 0
    missing_registry = 0

    for e in edges:
        p = dict(e.get("props") or {})
        evs = p.get("verification_evidence")
        if not isinstance(evs, list):
            continue
        cid = eid_of(e)
        new_evs = []
        touched = False
        for i, ev in enumerate(evs):
            if not isinstance(ev, dict):
                new_evs.append(ev)
                continue
            n_ev_total += 1
            ev2 = dict(ev)
            refs, vobj = ed.dependencies_for(ev, vident)
            if not refs and not vobj.get("verifier_id"):
                no_dep += 1
            ev2["evd"] = ed.evidence_id(cid, i, ev)
            if refs:
                ev2["refs"] = {rr["ref_id"]: rr["content_hash"] for rr in refs}
            if vobj.get("verifier_id"):
                ev2["vfr"] = vobj["verifier_id"]
                ev2["vch"] = vobj["code_hash"]
                if vobj.get("dependency_versions"):
                    ev2["dvs"] = dict(vobj["dependency_versions"])
            for k in EV_KEYS:
                if ev2.get(k) != ev.get(k) and ev2.get(k) is not None:
                    touched = True
            if ev2 != ev:
                n_ev_touched += 1
                for rr in refs:
                    ref_c[rr["ref_id"]] += 1
                vfr_c[vobj.get("verifier_id")] += 1
            new_evs.append(ev2)
        if new_evs != evs:
            touched = True
        if touched:
            out_edges.append({
                "id": cid, "source": e["source"], "target": e["target"],
                "type": e.get("type"),
                "kind": p.get("kind") or e.get("kind") or "",
                "props": {"verification_evidence": new_evs},
            })

    print("\n【证据依赖绑定】")
    print("  evidence 对象总数        : %d" % n_ev_total)
    print("  被写入/更新的 evidence   : %d" % n_ev_touched)
    print("  无参考表亦无验证器        : %d（纯数据断言；状态只由输入来源决定）" % no_dep)
    print("\n  【reference_refs 命中】（一个 evidence 可命中多张表）")
    for rid, c in ref_c.most_common():
        print("    %-22s %7d" % (rid, c))
    print("\n  【验证器分布】")
    for v, c in vfr_c.most_common():
        print("    %-22s %7d" % (str(v), c))
    print("\n【delta 汇总】edges=%d｜delete_edges=0｜改动键=%s"
          % (len(out_edges), sorted({k for e in out_edges for k in e["props"]})))

    payload = {
        "meta": {
            "phase": 45, "tag": "evidence-deps", "theme": "A04 从来源绑定升级为验证依赖绑定",
            "generator": "phase45_evidence_deps_delta.py",
            "base_graph": os.path.relpath(base, ROOT),
            "schema": "sttp-evidence/v2",
            "slim": True,
            "slim_note": "只交付 verification_evidence 一个键；apply_delta 对 props 浅合并",
            "changed_keys": ["verification_evidence"],
            "checked_at": stamp,
            "reference_hashes": ref_hashes,
            "verifiers": vident,
            "n_evidence_total": n_ev_total,
            "n_evidence_touched": n_ev_touched,
            "n_evidence_no_dep": no_dep,
            "byte_source_note": ("依赖哈希的权威字节源 = **仓库 blob**（git cat-file blob HEAD:<rel>，"
                                 "Phase 44 · D1）。取不到 ⇒ null，**不**用空串顶替（fail-closed）。"),
            "binding_note": ("refs 的**绑定强度**（impl_declared / code_import）不落库，"
                             "由 evidence_deps.dependencies_for() 在读取时**确定性重导**；"
                             "code_import 是按验证器模块 AST import 推出的**保守过近似**"
                             "（任务单明确允许：先按整文件保守失效）。"),
        },
        "nodes": [], "delete_nodes": [], "edges": out_edges, "delete_edges": [],
    }
    if a.dry:
        print("[DRY] 未落盘")
        return 0
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print("-> %s (%.1f MB)" % (os.path.relpath(a.out, ROOT), os.path.getsize(a.out) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
