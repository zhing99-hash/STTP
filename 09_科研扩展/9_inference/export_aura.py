# -*- coding: utf-8 -*-
"""从 Aura 反向导出当前全量图 -> **viz 快照**。

默认输出 `06_PoC/graph_data_aura.json`（语义明确：来自 Aura 的反向导出）。

⚠ 历史教训（2026-10-09）：本脚本曾把输出**硬编码**为
`06_PoC/graph_data_phase12.json`，而推送编排每次跑都会执行本脚本 ——
于是「phase12 历史快照」被反复重写、名不副实，回滚/排查时失去参照。
现改为：

  * 默认输出 `06_PoC/graph_data_aura.json`；
  * 支持 `--out <path>` 自定义；
  * **内置守卫**：目标路径若形如 `graph_data_phase<N>.json`（历史快照命名），
    除非显式 `--force`，一律拒绝写入 —— 结构化地防止再次污染历史快照；
  * 原始 dump 走系统临时目录（不再往仓库里丢 `_phase12_export_raw.json`，
    异常退出也不会残留）。

⚠ 格式说明：本脚本产出的是 **viz 格式**（经 `graph_export.build_graph_data`
转换，含 `schema` / `meta` / 富化的 `attrs`）。它与权威 raw 图
`06_PoC/etl/normalized.json`（仅 `nodes`/`edges`）**不是同一格式，不可互相覆盖**。

用法：
    python 09_科研扩展/9_inference/export_aura.py                       # -> graph_data_aura.json
    python 09_科研扩展/9_inference/export_aura.py --out 06_PoC/x.json
    python 09_科研扩展/9_inference/export_aura.py --out 06_PoC/graph_data_phase30.json --force
"""
import argparse
import json
import os
import re
import sys
import tempfile

from neo4j import GraphDatabase

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # STTP
DEFAULT_OUT = os.path.join(ROOT, "06_PoC", "graph_data_aura.json")

URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PW = os.environ.get("NEO4J_PASSWORD", "formula_graph_2026")
DB = os.environ.get("NEO4J_DATABASE", "neo4j")

# graph_data_phase12.json / graph_data_phase22.json …（历史快照命名）
PHASE_SNAPSHOT_RE = re.compile(r"^graph_data_phase\d+\.json$", re.I)


def is_phase_snapshot(path):
    """目标是否是「历史 phase 快照」命名（不应被反向导出覆盖）。"""
    return bool(PHASE_SNAPSHOT_RE.match(os.path.basename(path)))


def parse_args():
    ap = argparse.ArgumentParser(description="从 Aura 反向导出 viz 快照")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="输出路径（默认 %s）" % os.path.relpath(DEFAULT_OUT, ROOT))
    ap.add_argument("--force", action="store_true",
                    help="允许写入 graph_data_phase<N>.json 历史快照命名")
    return ap.parse_args()


def main():
    a = parse_args()
    out = a.out if os.path.isabs(a.out) else os.path.join(ROOT, a.out)

    # ---- 守卫：拒绝污染历史 phase 快照 ----
    if is_phase_snapshot(out) and not a.force:
        print("[REFUSE] 拒绝写入历史快照命名：%s" % os.path.relpath(out, ROOT))
        print("         反向导出不应覆盖 graph_data_phase<N>.json。")
        print("         请使用默认输出（%s），" % os.path.relpath(DEFAULT_OUT, ROOT))
        print("         或确实需要覆盖时显式加 --force。")
        return 4

    d = GraphDatabase.driver(URI, auth=(USER, PW), database=DB)
    nodes, edges = [], []
    with d.session(database=DB) as s:
        for rec in s.run("MATCH (n:Entity) RETURN n.id AS id, labels(n) AS labels, properties(n) AS p"):
            nodes.append({"id": rec["id"], "labels": list(rec["labels"]),
                          "props": dict(rec["p"])})
        for rec in s.run(
            "MATCH (a)-[r]->(b) RETURN a.id AS s, b.id AS t, type(r) AS ty, "
            "r.kind AS kind, properties(r) AS p"):
            p = dict(rec["p"])
            eid = p.get("id") or "%s->%s:%s" % (rec["s"], rec["t"], rec["ty"])
            edges.append({"id": eid, "source": rec["s"], "target": rec["t"],
                          "type": rec["ty"], "kind": rec["kind"] or "real",
                          "props": p})
    d.close()

    raw = {"nodes": nodes, "edges": edges}

    # 原始 dump 放系统临时目录：不污染仓库，异常退出也不残留
    fd, tmp = tempfile.mkstemp(prefix="sttp_aura_export_", suffix=".json")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        sys.path.insert(0, os.path.join(ROOT, "06_PoC"))
        import graph_export
        viz = graph_export.build_graph_data(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    meta = viz["meta"]
    out_dir = os.path.dirname(out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(viz, f, ensure_ascii=False, indent=2)

    print("[export] Aura -> %s" % os.path.relpath(out, ROOT))
    print("         节点 %d / 边 %d" % (meta["node_count"], meta["edge_count"]))
    print("         学科: %s" % (meta["subjects"],))
    print("         悬空端点: %d" % len(meta["dangling_endpoints"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
