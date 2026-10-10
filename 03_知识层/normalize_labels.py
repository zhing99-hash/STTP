# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""标签规范化（Phase 8d）：把 legacy 标签拼写/占位类型收敛到受控词表。

背景
----
`graph_export.pick_type()` 与前端 `graph_view.TYPE_STYLE` 都依赖**受控标签词表**
（见 `graph_export.TYPE_PRIORITY`）。但 Phase 5 早期链路留下了一批不在词表内的标签：

  1. **拼写漂移**：``Physical_quantity``（7 个 PQ: 节点）与词表的 ``PhysicalQuantity``
     只差一个下划线 → `pick_type` 匹配失败，退化用 ``labels[0]``，于是
     `sttp.sh stats` 里多出一个假的 ``Physical_quantity`` 类型桶，
     前端也拿不到 `PhysicalQuantity` 的样式。
     佐证：`09_科研扩展/9_inference/gnn_infer.py:97` **已在消费端打了补丁**
     （``return "PhysicalQuantity" if t == "Physical_quantity" else t``）——
     典型「下游补丁替代源头修复」，正是铁律 #9 要避免的。

  2. **占位类型**：`09_科研扩展/5E_integration/phase5_integration.py` 的 5.C 跨源对齐
     为「没解析到的目标实体」新建占位节点时，把**源前缀**当成了标签
     （``SRC_LABEL = {"WD": "WikidataEntity", "EK": "ElementEntity", "PB": "PhysicsEntity"}``）。
     以 ``*Entity`` 结尾的 ``ElementEntity`` / ``PhysicsEntity`` 并不在词表内。

处理原则
--------
* 只做**有证据**的收敛，不做类型猜测：
  - 拼写漂移 → 按受控词表改正（一一对应，无损）；
  - 占位类型 → 仅当 id/label 自身给出明确证据时才解析，且逐条登记理由；
    无法确定的**保持原样并计入报告**，交人工裁定。
* 归一化后做**词表封闭性断言**：仍不在受控词表内的标签会被列出（不阻断，只告警），
  这样「标签受控」从口头约定变成可复查的不变量。

用法
----
    python 03_知识层/normalize_labels.py            # dry-run（默认）
    python 03_知识层/normalize_labels.py --apply    # 备份 + 落盘 + 重建快照 + 出 delta
"""
import argparse
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "06_PoC"))

import graph_export  # noqa: E402

RAW = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
RAW_BAK = os.path.join(ROOT, "06_PoC", "etl", "normalized.before_labels.json")
OUT_VIZ = os.path.join(ROOT, "06_PoC", "graph_data_phase24.json")
OUT_DELTA = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase24_label_fix_delta.json")

# ---- 1) 拼写漂移：legacy -> 受控词表（一一对应，无损） ----
SPELLING = {
    "Physical_quantity": "PhysicalQuantity",
    "physical_quantity": "PhysicalQuantity",
    "physicalquantity": "PhysicalQuantity",
    "Mathconcept": "MathConcept",
    "Wikidataentity": "WikidataEntity",
}

# ---- 2) 占位类型：仅在有明确证据时解析，逐条登记理由 ----
PLACEHOLDER = {
    # 5.C 为「ElementKG 里未解析到的 CO2」建的占位节点：id 段自身写着 molecule，
    # label 为 "carbon dioxide"，与 MO:co2 / BC:mo:co2 同物 → 类型明确是 Molecule。
    "ek:molecule:CO2": ("ElementEntity", "Molecule"),
    # 5.C 为「PhysicsBabel 未解析到的牛顿第二定律」建的占位节点：label 为
    # "newton's second law"，图中 FO:fma 已承载同一概念（Formula）→ 类型明确是 Formula。
    "pb:newton_second": ("PhysicsEntity", "Formula"),
}

# 受控标签词表（与 graph_export.TYPE_PRIORITY 同源；Entity 为通用兜底标签）
CONTROLLED = set(graph_export.TYPE_PRIORITY) | {"Entity"}


def norm_label_list(labels, nid):
    """返回 (新 labels, 命中的改动说明)。"""
    out, hits = [], []
    for lb in labels:
        if lb in SPELLING:
            out.append(SPELLING[lb])
            hits.append(("spelling", lb, SPELLING[lb]))
        elif nid in PLACEHOLDER and lb == PLACEHOLDER[nid][0]:
            out.append(PLACEHOLDER[nid][1])
            hits.append(("placeholder", lb, PLACEHOLDER[nid][1]))
        else:
            out.append(lb)
    # 去重但保序（防止别名并集引入重复）
    seen, ded = set(), []
    for lb in out:
        if lb not in seen:
            seen.add(lb)
            ded.append(lb)
    return ded, hits


def _write_delta(rows):
    """写出 Aura delta。rows = [{"id","labels","props"} ...]。"""
    delta = {
        "meta": {
            "phase": "Phase8d-labels",
            "source": "03_知识层/normalize_labels.py",
            "reason": "标签规范化：legacy 拼写漂移（Physical_quantity→PhysicalQuantity 等）"
                      " + 5.C 占位类型解析（ElementEntity→Molecule / PhysicsEntity→Formula）"
                      "。推送须加 --set-labels（apoc.create.setLabels 整体替换），"
                      "否则 addLabels 会留下旧标签",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "nodes": rows,
        "delete_nodes": [], "edges": [], "delete_edges": [],
    }
    os.makedirs(os.path.dirname(OUT_DELTA), exist_ok=True)
    json.dump(delta, open(OUT_DELTA, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("  已写出 Aura delta %s（%d 个节点）"
          % (os.path.relpath(OUT_DELTA, ROOT), len(delta["nodes"])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=RAW)
    ap.add_argument("--apply", action="store_true", help="落盘（默认 dry-run）")
    ap.add_argument("--delta-only", action="store_true",
                    help="不落盘，仅据备份 normalized.before_labels.json 重新生成 Aura delta"
                         "（用于已 --apply 过、之后又调整了 delta 格式的情况）")
    a = ap.parse_args()

    if a.delta_only:
        if not os.path.exists(RAW_BAK):
            print("[ERR] 找不到备份 %s，无法重建 delta。" % os.path.relpath(RAW_BAK, ROOT),
                  file=sys.stderr)
            return 2
        bak_nodes = json.load(open(RAW_BAK, encoding="utf-8"))["nodes"]
        rows = []
        for n in bak_nodes:
            new_labels, hits = norm_label_list(list(n.get("labels") or []), n["id"])
            if hits:
                rows.append({"id": n["id"], "labels": new_labels, "props": {}})
        _write_delta(rows)
        print("已据备份重建 Aura delta：%d 个节点" % len(rows))
        return 0

    data = json.load(open(a.input, encoding="utf-8"))
    nodes = data["nodes"]

    changed, kind_cnt = [], Counter()
    detail = defaultdict(list)
    for n in nodes:
        labels = list(n.get("labels") or [])
        new_labels, hits = norm_label_list(labels, n["id"])
        if new_labels != labels:
            changed.append((n, labels, new_labels))
            for kind, old, new in hits:
                kind_cnt[kind] += 1
                detail[(old, new)].append(n["id"])

    # 归一化后的词表封闭性检查（用**归一化后**的 labels 判定，否则会把待改项误报为遗留）
    offenders = []
    for n in nodes:
        labels = norm_label_list(list(n.get("labels") or []), n["id"])[0]
        t = graph_export.pick_type(labels)
        if t not in CONTROLLED:
            offenders.append((n["id"], t, labels))

    print("=" * 84)
    print("标签规范化  %s  (%s)" % (os.path.relpath(a.input, ROOT),
                                    "APPLY" if a.apply else "DRY-RUN"))
    print("=" * 84)
    print("  节点总数 %d；待改标签 %d 个" % (len(nodes), len(changed)))
    if kind_cnt:
        print("  命中类别：%s" % dict(kind_cnt))
        for (old, new), ids in sorted(detail.items(), key=lambda kv: -len(kv[1])):
            print("      %-22s -> %-20s %2d 个  %s"
                  % (old, new, len(ids), ids[:4]))
    print("-" * 84)
    print("  受控词表：%d 个主类型（graph_export.TYPE_PRIORITY）" % len(CONTROLLED))
    if offenders:
        print("  [WARN] 归一化后仍有 %d 个节点主类型不在词表内（需人工裁定）：" % len(offenders))
        for nid, t, labels in offenders[:10]:
            print("        %-28s type=%-18s labels=%s" % (nid, t, labels))
    else:
        print("  [PASS] 词表封闭性：全部节点主类型都在受控词表内")
    print("=" * 84)

    if not a.apply:
        print("[DRY-RUN] 未写任何文件。加 --apply 落盘。")
        return 0

    if not changed:
        print("[SKIP] 无需改动。")
        return 0

    for n, _old, new in changed:
        n["labels"] = new

    if not os.path.exists(RAW_BAK):
        shutil.copy2(a.input, RAW_BAK)
        print("  已备份 -> %s" % os.path.relpath(RAW_BAK, ROOT))
    json.dump(data, open(a.input, "w", encoding="utf-8"), ensure_ascii=False)
    print("  已写回 %s" % os.path.relpath(a.input, ROOT))

    viz = graph_export.build_graph_data(a.input)
    json.dump(viz, open(OUT_VIZ, "w", encoding="utf-8"), ensure_ascii=False)
    print("  已重建快照 %s（%d 节点 / %d 边）"
          % (os.path.relpath(OUT_VIZ, ROOT), len(viz["nodes"]), len(viz["edges"])))
    print("  类型分布：%s" % dict(Counter(x["type"] for x in viz["nodes"]).most_common()))

    # props 显式给空 dict：推送器 NODE_CYPHER 会执行 `SET n += row.props`，
    # 缺该键会变成 `SET n += null` 报类型错（Neo4j 期望 Map）。
    _write_delta([{"id": n["id"], "labels": n["labels"], "props": {}} for n, _o, _x in changed])
    return 0


if __name__ == "__main__":
    sys.exit(main())
