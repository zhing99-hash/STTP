# -*- coding: utf-8 -*-
"""
公式知识图谱 · Phase 4 图谱导出（前端友好格式）
==================================================================
读取 Phase 3 回写结果 ``06_PoC/etl/with_inferred.json``（22 节点 / 52 边，
含 verified / verification_gate 字段），导出前端可直接消费的
``06_PoC/graph_data.json``。

设计要点
--------
* **节点 type 来自 labels 字段**（Symbol / Formula / Definition / Theorem /
  Lemma ...），彻底修复此前 Kuzu 演示中 type 为空的缺陷——本文件对
  ``type`` 做强制非空校验，任何空 type 都会在导出时被修正并告警。
* 边携带 ``confidence`` / ``explicit_or_inferred`` / ``verified`` /
  ``gate`` / ``evidence``，供前端做置信度滑块、实/虚线区分、tooltip。
* 不做任何过滤/裁剪：``nodes`` / ``edges`` 与源文件一一对应，便于核对
  「22 节点 / 52 边」这一硬性验收指标。

用法
----
    python graph_export.py
    python graph_export.py --input etl/with_inferred.json --out graph_data.json

author: 可视化与前端专家 | 2026-09-28
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from collections import Counter

# Windows 控制台 UTF-8 修复，避免中文统计输出乱码
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))          # 01tuopu/06_PoC
ROOT = os.path.dirname(HERE)                                # 01tuopu
ETL_DIR = os.path.join(HERE, "etl")
DEFAULT_WITH_INFERRED = os.path.join(ETL_DIR, "with_inferred.json")
DEFAULT_NORMALIZED = os.path.join(ETL_DIR, "normalized.json")
DEFAULT_OUT = os.path.join(HERE, "graph_data.json")

# 节点类型优先级：命中越靠前越「具体」，用于从 labels 里挑主类型
# （例如 labels=["Formula","Theorem"] → type="Theorem"）
# 注：Unit 用于 Phase 6 单位节点（能量/燃烧切片）。
TYPE_PRIORITY = [
    "Symbol", "Element", "Molecule", "Reaction", "FunctionalGroup", "Constant", "PhysicalQuantity", "Unit",
    "Definition", "Theorem", "Lemma", "Equation", "MathConcept", "Formula",
]

# 原始 kind → explicit_or_inferred 兜底映射（与 03_知识层/README 一致）
EXPLICIT_KINDS = {
    "explicit_citation", "defines", "reactant_of", "product_of", "part_of",
}


def pick_type(labels) -> str:
    """从 labels 中挑选主类型；保证返回非空字符串。"""
    labels = list(labels or [])
    for t in TYPE_PRIORITY:
        if t in labels:
            return t
    if labels:
        return str(labels[0])
    return "Unknown"          # 仅当源数据既无 labels 也无 type 时兜底


def subject_of(domain: str) -> str:
    """域名 → 学科中文名（可视化填充色按学科区分）。"""
    d = (domain or "").strip().lower()
    if d.startswith("chem") or d.startswith("ek"):
        return "化学"
    if d.startswith("phys") or d.startswith("phy") or d.startswith("pb"):
        return "物理"
    if d.startswith("math") or d.startswith("mx") or d.startswith("mg"):
        return "数学"
    return "跨学科" if d else "数学"


def _resolve_input(path: str | None) -> str:
    """解析输入文件：显式指定 > with_inferred.json > normalized.json。"""
    if path:
        return path
    if os.path.exists(DEFAULT_WITH_INFERRED):
        return DEFAULT_WITH_INFERRED
    return DEFAULT_NORMALIZED


def build_graph_data(src_path: str) -> dict:
    """把规范化图谱 JSON 转成前端友好结构（纯函数，供 server 复用）。"""
    with open(src_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    warnings = []

    # ------------------------ 节点 ------------------------
    nodes_out = []
    for n in data.get("nodes", []):
        props = dict(n.get("props") or {})
        labels = list(n.get("labels") or [])
        ntype = pick_type(labels)
        if not ntype or ntype == "Unknown":
            # 强制非空：即使 labels 全空，也退化为 props.type / "Formula"
            ntype = str(props.get("type") or "Formula")
            warnings.append(f"节点 {n.get('id')} 无有效 labels，type 回退为 {ntype}")

        nodes_out.append({
            "id": n.get("id"),
            "label": (props.get("name") or n.get("local_id") or n.get("id")),
            "type": ntype,                                  # ← 来自 labels，非空
            "labels": labels,
            "subject": subject_of(props.get("domain")),
            "domain": props.get("domain", ""),
            "formula": props.get("latex") or "",            # LaTeX（供 MathJax）
            "confidence": props.get("confidence"),
            "attrs": props,                                 # 原始属性全量透传
        })

    # ------------------------ 边 ------------------------
    edges_out = []
    for e in data.get("edges", []):
        props = dict(e.get("props") or {})
        kind = e.get("kind") or props.get("kind") or ""
        eoi = props.get("explicit_or_inferred")
        if not eoi:                                          # 兜底推导
            eoi = "explicit" if kind in EXPLICIT_KINDS else "inferred"

        edges_out.append({
            "id": e.get("id"),
            "source": e.get("source"),
            "target": e.get("target"),
            "type": e.get("type") or kind,
            "kind": kind,
            "confidence": props.get("confidence"),
            "explicit_or_inferred": eoi,                     # explicit / inferred
            "verified": bool(props.get("verified", False)),  # Phase 3 校验通过
            "gate": props.get("verification_gate"),          # R-MATH / R-PHY / R-CHEM
            "evidence": props.get("verification_evidence") or props.get("evidence"),
            "rationale": props.get("rationale"),
            "domain": props.get("domain"),
        })

    # ------------------------ 统计 meta ------------------------
    ntype_counter = Counter(nd["type"] for nd in nodes_out)
    etype_counter = Counter(ed["type"] for ed in edges_out)
    kind_counter = Counter(ed["kind"] for ed in edges_out)
    subj_counter = Counter(nd["subject"] for nd in nodes_out)
    eoi_counter = Counter(ed["explicit_or_inferred"] for ed in edges_out)
    verified_count = sum(1 for ed in edges_out if ed["verified"])

    node_ids = {nd["id"] for nd in nodes_out}
    dangling = set()
    for ed in edges_out:
        for ep in (ed["source"], ed["target"]):
            if ep not in node_ids:
                dangling.add(ep)

    meta = {
        "source_file": os.path.basename(src_path),
        "node_count": len(nodes_out),
        "edge_count": len(edges_out),
        "node_types": dict(sorted(ntype_counter.items())),
        "edge_types": dict(sorted(etype_counter.items())),
        "edge_kinds": dict(sorted(kind_counter.items())),
        "subjects": dict(sorted(subj_counter.items())),
        "explicit_or_inferred": dict(sorted(eoi_counter.items())),
        "verified_edges": verified_count,
        "dangling_endpoints": sorted(dangling),
        "warnings": warnings,
    }

    return {
        "schema": "formula-graph-view/v1",
        "nodes": nodes_out,
        "edges": edges_out,
        "meta": meta,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="公式知识图谱 · 前端数据导出")
    ap.add_argument("--input", default=None, help="源图谱 JSON（默认 with_inferred.json，回退 normalized.json）")
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出路径（默认 06_PoC/graph_data.json）")
    args = ap.parse_args(argv)

    src = _resolve_input(args.input)
    if not os.path.exists(src):
        print(f"[ERROR] 源文件不存在：{src}")
        return 1

    print("=" * 68)
    print("  公式知识图谱 · Phase 4 图谱导出")
    print("=" * 68)
    print(f"  源文件   : {src}")
    data = build_graph_data(src)
    meta = data["meta"]

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"  输出文件 : {out}")
    print("-" * 68)
    print(f"  节点数 : {meta['node_count']}")
    print(f"  边数   : {meta['edge_count']}")
    print(f"  各 type 计数（节点）:")
    for t, c in meta["node_types"].items():
        print(f"      {t:<16s} {c}")
    print(f"  各 type 计数（边）:")
    for t, c in meta["edge_types"].items():
        print(f"      {t:<26s} {c}")
    print(f"  explicit / inferred : {meta['explicit_or_inferred']}")
    print(f"  verified 边         : {meta['verified_edges']}")
    print(f"  学科分布            : {meta['subjects']}")
    print(f"  外部端点(未在节点表): {len(meta['dangling_endpoints'])} 个 → 前端合成 ghost 节点")
    if meta["warnings"]:
        print("  [WARN] 空 type 修正：")
        for w in meta["warnings"]:
            print("      - " + w)

    # 硬性自检：type 非空
    empty_types = [nd["id"] for nd in data["nodes"] if not nd.get("type")]
    print("-" * 68)
    print(f"  自检 type 非空 : {'通过 ✅' if not empty_types else '失败 ❌ ' + str(empty_types)}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
