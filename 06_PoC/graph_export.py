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
import json
import os
import sys
from collections import Counter

# Windows 控制台 UTF-8 修复，避免中文统计输出乱码
# 注意：必须用 reconfigure() 原地改编码，**不能**用
#   sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)
# 因为旧 wrapper 被 GC 时会连带关掉同一个底层 fd，此后所有 print 都会抛
# "I/O operation on closed file"。本模块会被长驻服务 import（viz_server 的
# /api/stats 与降级路径），一旦踩到就会让服务对**所有**请求失联（连访问日志都打不出）。
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))          # STTP/06_PoC
ROOT = os.path.dirname(HERE)                                # STTP
ETL_DIR = os.path.join(HERE, "etl")
DEFAULT_WITH_INFERRED = os.path.join(ETL_DIR, "with_inferred.json")
DEFAULT_NORMALIZED = os.path.join(ETL_DIR, "normalized.json")
DEFAULT_OUT = os.path.join(HERE, "graph_data.json")

# 节点类型优先级：命中越靠前越「具体」，用于从 labels 里挑主类型
# （例如 labels=["Formula","Theorem"] → type="Theorem"）
# 注：Unit 用于 Phase 6 单位节点（能量/燃烧切片）。
TYPE_PRIORITY = [
    "Symbol", "Element", "Molecule", "Reaction", "FunctionalGroup", "Constant", "PhysicalQuantity", "Unit",
    "Paper",   # Phase 8：OpenAlex 文献节点（Paper）
    # Phase 9：Wikidata 真实世界实体。原先未登记 —— 24 个 WD 节点只能靠
    # ``labels[0]`` 的偶然顺序被识别（2026-10-09 标签规范化审计发现），
    # 登记后判定不再依赖顺序。
    "WikidataEntity",
    "Definition", "Theorem", "Lemma", "Equation", "MathConcept", "Formula",
]

# 原始 kind → explicit_or_inferred 兜底映射（与 03_知识层/README 一致）
EXPLICIT_KINDS = {
    "explicit_citation", "defines", "reactant_of", "product_of", "part_of",
    "cites",   # Phase 8：OpenAlex 真实引用
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


_NS_SUBJECT = {
    # ---- 物理 ----
    "PQ": "物理", "PB": "物理",
    # ⚠ 修正（2026-10-09 连通性审计）：CM/QM/TH/EM 原被误标为「数学」。
    # 它们是**物理切片**的命名空间（Classical Mechanics / Quantum Mechanics /
    # Thermodynamics / Electromagnetism），不是数学。OP/SM/RT 此前缺失。
    # 这些键仅在本节点 domain 为空时才生效，但错标会直接污染跨学科判定。
    "CM": "物理", "QM": "物理", "TH": "物理", "EM": "物理",
    "OP": "物理", "SM": "物理", "RT": "物理",
    "UN": "物理",   # SI 单位层
    "SY": "物理",   # 共享符号（E/m/c/F/a/v/KE，源自能量·燃烧切片）
    "CO": "物理",   # CODATA 常量层
    "FO": "物理",   # 能量·燃烧切片
    # ---- 化学 ----
    "EL": "化学", "EK": "化学", "EK2": "化学", "IC": "化学", "BC": "化学",
    "RX": "化学", "OM": "化学", "MO": "化学", "PC": "化学",
    "CE": "化学",   # Chemical Equilibrium（Phase 7b 新增切片）
    "RH": "化学",   # Rhea 反应（Phase 26 新增真实源）
    "CH": "化学",   # ChEBI 化合物（Phase 26 新增真实源）
    # ---- 数学 ----
    "MX": "数学", "MG": "数学", "MA": "数学", "MC": "数学",
    "NT": "数学",   # Number Theory（Phase 7b 新增切片）
    # ---- 文献 / 跨世界实体 ----
    "PA": "数学",   # Paper：Phase 8 选取的是**数学文献**切片，故归数学
    "WD": "跨学科", "wd": "跨学科",   # Wikidata 真实世界实体（跨学科桥）
}


def subject_of(domain: str, node_id: str = "") -> str:
    """域名 → 学科中文名（可视化填充色按学科区分）。

    优先级：**显式 domain** > **id 命名空间兜底**。

    domain 缺失时按命名空间兜底：原实现一律回退「数学」，会把
    ``PQ:energy`` / ``PQ:mass`` / ``PB:*`` 等物理量误标为数学，
    直接污染前端「跨域路径」的学科切换判定（化学→物理 会显示成 化学→数学）。

    ⚠ 2026-10-09 连通性审计进一步修正 ``_NS_SUBJECT``：
    CM/QM/TH/EM 等**物理切片**命名空间原被误标「数学」；补 MO/UN/SY/CO/WD 等键。

    另：``biology.*`` 归 **化学**。本项目学科口径只有 物理/化学/数学 三类，
    生物化学是化学的分支（``BC:rx:*`` 三条反应原因此落入「跨学科」，
    在连通性审计里表现为 BC 命名空间「混标」并虚增跨域边）。
    若将来引入独立「生物」层，需同步前端 TYPE_STYLE 与本映射。
    """
    d = (domain or "").strip().lower()
    if d.startswith("chem") or d.startswith("ek") or d.startswith("bio"):
        return "化学"
    if d.startswith("phys") or d.startswith("phy") or d.startswith("pb"):
        return "物理"
    if d.startswith("math") or d.startswith("mx") or d.startswith("mg"):
        return "数学"
    if d:
        return "跨学科"
    # domain 为空 → 用 id 命名空间兜底；未知前缀保持旧的「数学」行为，避免大面积改色
    pfx = (node_id or "").split(":")[0]
    # 命名空间大小写混用（历史数据里有 `ek:` / `pb:` 小写写法）→ 回退到大写键再查一次
    return _NS_SUBJECT.get(pfx) or _NS_SUBJECT.get(pfx.upper(), "数学")


def uniquify_edge_ids(edges: list) -> int:
    """保证边 id 全局唯一（就地把撞号的边改名），返回被改名的条数。

    为什么必须做：Cytoscape.js 要求元素 id 唯一，重复 id 会导致渲染异常；
    NetworkX 的 ``MultiDiGraph.add_edge(key=id)`` 也会把撞号的边**静默折叠**。

    撞号来源：Aura 侧边 id 约定为 ``type|source|target``，不含 ``kind``；
    同一 (type, source, target) 上若存在不同 kind 的边（如 curated_seed 的
    explicit 边与 PhysicsBabel 的 inferred 边），id 就撞了。
    这里保留第一条原名，后续撞号者追加 ``|kind``（再撞则加 ``#n``）。
    """
    used = set()
    fixed = 0
    for e in edges:
        eid = e.get("id")
        if eid is None:
            eid = "%s|%s|%s" % (e.get("type") or "", e.get("source") or "", e.get("target") or "")
            e["id"] = eid
        if eid in used:
            kind = e.get("kind") or "unknown"
            cand, n = "%s|%s" % (eid, kind), 2
            while cand in used:
                cand = "%s|%s#%d" % (eid, kind, n)
                n += 1
            e["id"] = cand
            eid = cand
            fixed += 1
        used.add(eid)
    return fixed


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
            "subject": subject_of(props.get("domain"), str(n.get("id") or "")),
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
            # 化学计量数等数值属性透传（否则 viz 投影丢字段，下游按 count=1 误算摩尔质量）
            **({k: props[k] for k in ("count", "value", "unit", "element_symbol")
                if props.get(k) is not None}),
        })

    # ------------------------ 统计 meta ------------------------
    id_fixed = uniquify_edge_ids(edges_out)     # 保证 viz 快照边 id 唯一（见函数 docstring）
    dupe_nodes = [n["id"] for n, c in Counter(nd["id"] for nd in nodes_out).items() if c > 1]
    if dupe_nodes:
        warnings.append(f"节点 id 重复 {len(dupe_nodes)} 个：{dupe_nodes[:5]}")

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
        "edge_id_collisions_fixed": id_fixed,
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
