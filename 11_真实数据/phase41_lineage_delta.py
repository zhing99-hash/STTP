# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
"""phase41_lineage_delta.py —— 第 31 轮 · R10 证据血缘绑定 delta

复评指摘（`07_交付物/外部评审/STTP_复评与Apache2.0许可意见_20261011.md`）
----------------------------------------------------------------------
> 「（证据对象）至少补齐：… `source_id + source_version + locator`、**原始内容哈希**、
>   `verifier_version`、**输入哈希**、实际检查项目及结果、**证据血缘**。」
> R10 完成定义：**「来源变更能定位受影响断言并重新验证」**。

本 delta 做两件事（**非破坏性**：不增删任何边、不改任何档位/结论）：
1. **回填缺失的来源**：`props.source` 为空的边，按 `source_registry.proposer_source`
   的**确定性归因**（依据 `verifier`/`type`）补 `source`，并把归因方式记入
   `source_inferred_from`（留痕，铁律 #22）。Phase 41 侦察坐实：R04a 正名产生的
   11 条 `uses_operator` 数学桥**丢失了 `props.source`**，连同其余共 53 条。
2. **给每条证据绑定来源身份**：每条 `verification_evidence[i]` 增补
   `src`（断言提出者来源 id）、`src_ver`（来源版本标签）、
   `src_hash`（**不可变输入哈希**，该来源在生成时刻的内容哈希）、
   `impl_ver`（**验证器版本** = `verification_model.py` 的 sha256 前缀）。

**幂等**：复跑 0 改动（来源已在、证据已绑定时不比出差异）。

**瘦 delta（slim）**：`apply_delta.py` 对 props 是浅合并（`merged.update(delta.props)`），
只需交付**真正改动的键**。Phase 41 实测：全量 props 47.3 MB → 瘦身 24.7 MB
（只差 `verification_evidence` / `source` / `source_inferred_from` 三个键，
其余键原样保留由 apply_delta 合并）。故 `--base` 必须指向**变更前的图**，
典型为 `06_PoC/etl/normalized.before_lineage.json`。

用法：python 11_真实数据/phase41_lineage_delta.py [--base <pre-graph>] [--dry] [--out <path>]
"""
from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import source_registry as sr                             # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase41_lineage_delta.json")

VERIFIER_SRC = os.path.join(HERE, "verification_model.py")


def eid_of(e):
    return e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))


def verifier_version() -> str:
    """验证器版本 = `verification_model.py` 内容 sha256 的前 12 位（可核对、可复现）。"""
    with open(VERIFIER_SRC, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def main():
    ap = argparse.ArgumentParser(description="Phase 41 证据血缘绑定 delta（R10）")
    ap.add_argument("--base", default=NORM,
                    help="变更前的图（默认 normalized.json；做瘦 delta 时须指向 Phase 41 前基线）")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    base = os.path.abspath(a.base)
    d = json.load(open(base, encoding="utf-8"))
    nodes, edges = d["nodes"], d["edges"]
    print("=" * 92)
    print("Phase 41 delta —— 证据血缘绑定（R10：来源身份 + 版本 + 内容哈希 + 验证器版本）")
    print("  基线 %s" % os.path.relpath(base, ROOT))
    print("  输入 %d 节点 / %d 边" % (len(nodes), len(edges)))
    print("=" * 92)

    hmap = sr.hash_map()
    impl_ver = verifier_version()
    print("  验证器版本 impl_ver = %s（verification_model.py sha256[:12]）" % impl_ver)
    print("  已登记来源 %d 个；有内容哈希 %d 个；血缘缺口 %d 个"
          % (len(sr.load()), sum(1 for v in hmap.values() if v),
             sum(1 for s in sr.load() if not sr.load()[s].get("lineage_recorded"))))

    stamp = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    out_edges = []
    n_backfill = 0
    n_ev_aug = 0
    infer_kinds = collections.Counter()
    src_of_edge = collections.Counter()   # 归因后：来源 → 边数
    n_dropped_key = collections.Counter()  # 浅合并无法表达的「删除键」（应为 0）
    unreg = []

    for e in edges:
        orig_p = dict(e.get("props") or {})
        p = dict(orig_p)
        sid, inferred = sr.proposer_source(e)
        changed = False

        if not p.get("source"):
            p["source"] = sid
            p["source_inferred_from"] = (
                "Phase 41 R10：原 props.source 缺失，按 (type,verifier) 确定性归因")
            if not inferred:
                p["source_inferred_from"] = "Phase 41 R10：原 props.source 缺失（无法归因）"
            n_backfill += 1
            infer_kinds[(e.get("type"), sid)] += 1
            changed = True

        if sid == "unregistered":
            unreg.append(eid_of(e))

        evs = p.get("verification_evidence")
        if isinstance(evs, list):
            new_evs = []
            for ev in evs:
                if not isinstance(ev, dict):
                    new_evs.append(ev)
                    continue
                ev2 = dict(ev)
                ev2["src"] = sid
                ev2["src_ver"] = sr.version_of(sid)
                ev2["src_hash"] = hmap.get(sid)
                ev2["impl_ver"] = impl_ver
                if ev2 != ev:
                    n_ev_aug += 1
                new_evs.append(ev2)
            if new_evs != evs:
                p["verification_evidence"] = new_evs
                changed = True

        src_of_edge[sid] += 1
        if changed:
            # 瘦 delta：只交付真正改动的键（apply_delta 对 props 做浅合并 merged.update）
            diff = {k: p[k] for k in p if k in orig_p and p[k] != orig_p[k]}
            diff.update({k: p[k] for k in p if k not in orig_p})   # 新增键
            dropped = [k for k in orig_p if k not in p]             # 删除键（浅合并无法表达）
            for k in dropped:
                n_dropped_key[k] += 1
            out_edges.append({
                "id": eid_of(e), "source": e["source"], "target": e["target"],
                "type": e.get("type"),
                "kind": p.get("kind") or e.get("kind") or "",
                "props": diff,
            })

    print("\n【回填缺失来源】%d 条" % n_backfill)
    for (t, s), v in sorted(infer_kinds.items(), key=lambda x: -x[1]):
        print("   %-16s -> %-24s %5d" % (t, s, v))
    print("\n【证据绑定】%d 条证据记录被绑定来源/版本/哈希/验证器版本" % n_ev_aug)
    print("\n【归因后：来源 → 断言数（前 12）】")
    for k, v in src_of_edge.most_common(12):
        print("   %-28s %6d" % (k, v))

    print("\n【自检】")
    print("  未登记来源的断言 = %d %s" % (len(unreg), "✅" if not unreg else "❌ %s" % unreg[:3]))
    print("  props.source 缺失 = %d %s"
          % (sum(1 for e in edges if not (e.get("props") or {}).get("source")),
             "（回填前）"))
    print("  规模：节点 %d（不变）｜边 %d（不增不减）" % (len(nodes), len(edges)))

    payload = {
        "meta": {
            "phase": 41, "tag": "lineage-provenance",
            "generator": "phase41_lineage_delta.py",
            "base_graph": os.path.relpath(base, ROOT),
            "theme": "R10 证据血缘/版本/失效：来源身份 + 内容哈希 + 验证器版本",
            "slim": True,
            "slim_note": "仅交付改动的 props 键；apply_delta 对 props 做浅合并（merged.update）",
            "changed_keys": sorted({k for e in out_edges for k in e["props"]}),
            "dropped_keys": dict(n_dropped_key),
            "evidence_at": stamp,
            "impl_ver": impl_ver,
            "n_backfilled_source": n_backfill,
            "n_evidence_bound": n_ev_aug,
            "n_edges_touched": len(out_edges),
            "inferred": {("%s|%s" % k): v for k, v in infer_kinds.items()},
            "source_counts": dict(src_of_edge),
            "hashes": {k: v for k, v in hmap.items() if v},
        },
        "nodes": [],
        "delete_nodes": [],
        "edges": out_edges,
        "delete_edges": [],
    }
    print("\n【delta 汇总】edges=%d（含回填 %d）｜ delete_edges=0｜ 改动键=%s"
          % (len(out_edges), n_backfill,
             sorted({k for e in out_edges for k in e["props"]})))
    if n_dropped_key:
        print("  [WARN] 出现浅合并无法表达的删除键：%s" % dict(n_dropped_key))
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
