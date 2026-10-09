# -*- coding: utf-8 -*-
"""把 GNN 产出的**带类型边**合并进本地权威快照，并生成新的可视化快照 phase14。

项目铁律（PROJECT_DEVELOPMENT_GUIDE §5.1）：用于 viz / Neo4j 的 JSON 必须经
`06_PoC/graph_export.build_graph_data()` 生成，禁止手工 merge。

流程：
    normalized.json (raw)  --[追加 typed edges]-->  normalized.json (raw, 备份后覆盖)
                                                        |
                                    graph_export.build_graph_data()
                                                        v
                                       06_PoC/graph_data_phase14.json (viz)

用法：
    python 09_科研扩展/9_inference/apply_typed_edges.py [--delta <path>] [--dry-run]
"""
import os
import sys
import json
import shutil
import argparse
import collections

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "06_PoC"))
import graph_export  # noqa: E402

RAW = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
RAW_BAK = os.path.join(ROOT, "06_PoC", "etl", "normalized.before_typedgnn.json")
DEFAULT_DELTA = os.path.join(HERE, "phase13_typed_edges.json")
OUT_VIZ = os.path.join(ROOT, "06_PoC", "graph_data_phase14.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delta", default=DEFAULT_DELTA)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    raw = json.load(open(RAW, encoding="utf-8"))
    delta = json.load(open(a.delta, encoding="utf-8"))
    print("[1] raw 快照 %d 节点 / %d 边" % (len(raw["nodes"]), len(raw["edges"])))
    print("    增量边 %d 条" % len(delta.get("edges", [])))

    node_ids = {n["id"] for n in raw["nodes"]}
    exist = set((e.get("source"), e.get("type"), e.get("target")) for e in raw["edges"])

    added, skipped_dup, skipped_dangling = [], 0, 0
    for e in delta.get("edges", []):
        s, t, ty = e["source"], e["target"], e["type"]
        if s not in node_ids or t not in node_ids:
            skipped_dangling += 1
            continue
        if (s, ty, t) in exist:
            skipped_dup += 1
            continue
        # 统一 id 约定为 raw/viz 的 `type|source|target`
        raw_edges = {
            "id": "%s|%s|%s" % (ty, s, t),
            "source": s, "target": t, "type": ty,
            "kind": e.get("kind") or "gnn_typed_inferred",
            "props": dict(e.get("props") or {}),
        }
        raw_edges["props"].setdefault("kind", raw_edges["kind"])
        added.append(raw_edges)
        exist.add((s, ty, t))

    print("    可新增 %d 条（重复跳过 %d / 悬空跳过 %d）" % (len(added), skipped_dup, skipped_dangling))
    print("    新增类型分布: %s" % dict(collections.Counter(e["type"] for e in added).most_common()))

    if a.dry_run:
        print("[DRY-RUN] 未写任何文件。")
        return

    if not os.path.exists(RAW_BAK):
        shutil.copy2(RAW, RAW_BAK)
        print("[2] 已备份 raw -> %s" % os.path.relpath(RAW_BAK, ROOT))
    else:
        print("[2] 备份已存在，跳过（%s）" % os.path.relpath(RAW_BAK, ROOT))

    raw["edges"].extend(added)
    json.dump(raw, open(RAW, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("[3] 已写回 raw：%d 节点 / %d 边" % (len(raw["nodes"]), len(raw["edges"])))

    viz = graph_export.build_graph_data(RAW)
    json.dump(viz, open(OUT_VIZ, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("[4] 已生成 viz 快照 %s：%d 节点 / %d 边"
          % (os.path.relpath(OUT_VIZ, ROOT), len(viz["nodes"]), len(viz["edges"])))

    meta = viz.get("meta") or {}
    if meta.get("dangling"):
        print("    !! 悬空节点: %s" % list(meta["dangling"])[:10])
    else:
        print("    悬空检查：通过")
    print("    边类型: %s" % dict(collections.Counter(e["type"] for e in viz["edges"]).most_common(8)))


if __name__ == "__main__":
    main()
