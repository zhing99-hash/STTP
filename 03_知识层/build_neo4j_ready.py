# -*- coding: utf-8 -*-
"""
公式知识图谱 · Neo4j 就绪数据集构建器（build_neo4j_ready.py）
================================================================

读取 Phase 3 回写产物 ``06_PoC/etl/with_inferred.json``（22 节点 / 52 边），
**修复引用完整性（reference integrity）**：

Phase 3 的 8 条 LLM 验证边中，有 7 条引用了 **14 个外部实体**
（``MX:phy:*`` 物理量 / ``MX:chem:*`` 分子·元素 / ``MX:math:*`` 数学概念 /
``MX:sym:*`` 符号），它们并未收录进 ``nodes[]``。直接用 ``neo4j-admin import``
会因悬空 ``:END_ID/:START_ID`` 失败。

本脚本把这些缺失端点**注册为正式节点**（type 由 id 前缀推断），产出真正可导入的
Neo4j 数据集：

    - 预期 36 节点（22 原有 + 14 合成） / 52 边 / 0 悬空引用。
    - 数据集输出到 ``06_PoC/etl/neo4j/``：
        nodes.csv          neo4j-admin import 节点表（:ID,:LABEL,...）
        relationships.csv  neo4j-admin import 关系表（:START_ID,:END_ID,:TYPE,...）
        neo4j_ready.json   同源 JSON（供 load_neo4j.py 在线 MERGE 使用）

合成节点字段补全（见 ``synthesize_node``）：
    confidence=1.0, explicit_or_inferred='inferred', source='llm_hypothesis'，
    并带 ntype/name/domain/meaning/local_id 等。

运行
----
    cd 01tuopu/03_知识层
    python build_neo4j_ready.py
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import sys
from typing import Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "06_PoC", "etl", "with_inferred.json")
OUT_DIR = os.path.join(ROOT, "06_PoC", "etl", "neo4j")

# ----------------------------------------------------------------------------
# 前缀 → (Neo4j Label, ntype 小写, domain)
# 严格按任务约定的 id 前缀推断节点类型。
# 说明：MX:chem:* 统一归为 Molecule（Schema 中 chem 范畴主类）。
#   MX:chem:combustion 语义上是一个“反应”，但前缀归 chem，故标 Molecule；
#   若后续采用 Schema §4 的 Reaction 超边建模，可将其重分类为 Reaction。
# ----------------------------------------------------------------------------
PREFIX_MAP: Dict[str, Tuple[str, str, str]] = {
    "MX:phy:":  ("PhysicalQuantity", "physical_quantity", "physics"),
    "MX:chem:": ("Molecule",         "molecule",          "chemistry"),
    "MX:math:": ("MathConcept",      "math_concept",      "math"),
    "MX:sym:":  ("Symbol",           "symbol",            "cross"),
}

# 合成节点的友好名称 / 释义（提升可读性；缺省回退到 local key）
KNOWN_NAMES: Dict[str, Tuple[str, str]] = {
    "MX:sym:c":                      ("C",            "碳元素符号 C（用于碳燃烧反应）"),
    "MX:chem:co2":                   ("CO₂",          "二氧化碳（分子）"),
    "MX:chem:methane":               ("CH₄",          "甲烷（分子）"),
    "MX:chem:combustion":            ("combustion",   "燃烧反应（chem 范畴，语义为反应）"),
    "MX:phy:newton2":                ("F=ma",         "牛顿第二定律（力 = 质量 × 加速度）"),
    "MX:phy:acceleration":           ("a",            "加速度"),
    "MX:phy:kinetic_energy":         ("E_k",          "动能"),
    "MX:phy:energy":                 ("E",            "能量"),
    "MX:math:binomial":              ("(a+b)²",       "二项式展开"),
    "MX:math:expand":                ("expand",       "代数展开"),
    "MX:math:pythagorean_identity":  ("sin²θ+cos²θ=1", "勾股三角恒等式"),
    "MX:math:trig_unit":             ("unit circle",  "单位圆（三角函数）"),
    "MX:math:derivative_power":      ("d/dx(xⁿ)",     "幂函数求导"),
    "MX:math:power_rule":            ("n·xⁿ⁻¹",       "幂函数求导法则"),
}

# 所有节点统一附加一个通用 Label “Entity”，便于建立全局唯一约束
# 与 ntype/domain 索引（见 load.cypher / load_neo4j.py）。不改变既有语义 Label。
COMMON_LABEL = "Entity"


def ensure_common_label(labels: list) -> list:
    if COMMON_LABEL not in labels:
        labels = [COMMON_LABEL] + list(labels)
    return labels


# 节点 CSV 列顺序（含 :ID / :LABEL 特殊列；其余均为节点属性）
NODE_COLUMNS = [
    ":ID", ":LABEL", "ntype", "name", "domain", "confidence",
    "explicit_or_inferred", "source", "type", "local_id", "latex",
    "meaning", "symbol_type", "created_at", "version",
]

# 关系 CSV 列顺序（含 :START_ID / :END_ID / :TYPE 特殊列；其余为关系属性）
REL_COLUMNS = [
    ":START_ID", ":END_ID", ":TYPE", "id", "confidence",
    "explicit_or_inferred", "source", "kind", "evidence", "created_at",
]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def load_source(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def classify_missing(node_id: str) -> Tuple[str, str, str]:
    """根据 id 前缀推断 (label, ntype, domain)。未知前缀抛错。"""
    for prefix, (label, ntype, domain) in PREFIX_MAP.items():
        if node_id.startswith(prefix):
            return label, ntype, domain
    raise ValueError(f"无法识别的缺失端点前缀：{node_id}")


def synthesize_node(node_id: str, created_at: str) -> dict:
    """为缺失端点合成一个正式节点记录（与既有节点同构：id/labels/props）。"""
    label, ntype, domain = classify_missing(node_id)
    local_key = node_id.split(":", 2)[-1] if node_id.count(":") >= 2 else node_id
    friendly, meaning = KNOWN_NAMES.get(node_id, (local_key, f"合成节点（LLM 假设端点）：{node_id}"))
    return {
        "id": node_id,
        "labels": ensure_common_label([label]),
        # 注意：ntype 同时写入 props，供 MERGE / 索引使用
        "props": {
            "ntype": ntype,
            "name": friendly,
            "type": ntype,
            "domain": domain,
            "meaning": meaning,
            "local_id": local_key,
            "confidence": 1.0,
            "explicit_or_inferred": "inferred",
            "source": "llm_hypothesis",
            "created_at": created_at,
            "version": "v0.1",
        },
    }


def existing_node_to_record(node: dict) -> dict:
    """把 with_inferred.json 的既有节点转为扁平属性记录。"""
    p = node.get("props", {}) or {}
    return {
        "id": node["id"],
        "labels": ensure_common_label(list(node.get("labels", []))),
        "props": p,
        "_local_id": node.get("local_id", ""),
        "_latex": p.get("latex", ""),
        "_symbol_type": p.get("symbol_type", ""),
    }


def node_row(rec: dict) -> Dict[str, str]:
    """把节点记录写为 CSV 行（dict[列名]=字符串）。"""
    p = rec["props"]
    labels = rec["labels"] if isinstance(rec["labels"], list) else [str(rec["labels"])]
    return {
        ":ID": rec["id"],
        ":LABEL": ";".join(labels),
        "ntype": str(p.get("ntype", p.get("type", ""))),
        "name": str(p.get("name", "")),
        "domain": str(p.get("domain", "")),
        "confidence": str(p.get("confidence", 1.0)),
        "explicit_or_inferred": str(p.get("explicit_or_inferred", "explicit")),
        "source": str(p.get("source", "")),
        "type": str(p.get("type", p.get("ntype", ""))),
        "local_id": str(p.get("local_id", rec.get("_local_id", ""))),
        "latex": str(p.get("latex", rec.get("_latex", ""))),
        "meaning": str(p.get("meaning", "")),
        "symbol_type": str(p.get("symbol_type", rec.get("_symbol_type", ""))),
        "created_at": str(p.get("created_at", "")),
        "version": str(p.get("version", "v0.1")),
    }


def edge_row(edge: dict) -> Dict[str, str]:
    p = edge.get("props", {}) or {}
    return {
        ":START_ID": edge["source"],
        ":END_ID": edge["target"],
        ":TYPE": edge["type"],
        "id": edge.get("id", ""),
        "confidence": str(p.get("confidence", "")),
        "explicit_or_inferred": str(p.get("explicit_or_inferred", "")),
        "source": str(p.get("source", "")),
        "kind": str(edge.get("kind", p.get("kind", ""))),
        "evidence": str(p.get("evidence", "")),
        "created_at": str(p.get("created_at", "")),
    }


def write_csv(path: str, columns: List[str], rows: List[Dict[str, str]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main() -> int:
    if not os.path.exists(SRC):
        print(f"[ERR] 未找到源文件：{SRC}（请先运行 Phase 2/3 的 ETL）")
        return 0

    data = load_source(SRC)
    existing_nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    existing_ids = {n["id"] for n in existing_nodes}

    created_at = now_iso()

    # 1) 扫描边端点，收集缺失的节点 id（去重，保持首次出现顺序）
    missing_ids: List[str] = []
    seen = set()
    dangling_check: Dict[str, int] = {}
    for e in edges:
        for end in (e["source"], e["target"]):
            if end not in existing_ids:
                dangling_check[end] = dangling_check.get(end, 0) + 1
                if end not in seen:
                    seen.add(end)
                    missing_ids.append(end)

    # 2) 合成缺失节点
    synthesized = [synthesize_node(mid, created_at) for mid in missing_ids]

    # 3) 合并节点（既有 + 合成）
    all_node_records = [existing_node_to_record(n) for n in existing_nodes] + synthesized

    # 4) 写出 CSV
    node_rows = [node_row(r) for r in all_node_records]
    rel_rows = [edge_row(e) for e in edges]
    nodes_csv = os.path.join(OUT_DIR, "nodes.csv")
    rels_csv = os.path.join(OUT_DIR, "relationships.csv")
    write_csv(nodes_csv, NODE_COLUMNS, node_rows)
    write_csv(rels_csv, REL_COLUMNS, rel_rows)

    # 5) 引用完整性二次校验（防御性）
    node_id_set = {r[":ID"] for r in node_rows}
    dangling = []
    for r in rel_rows:
        if r[":START_ID"] not in node_id_set:
            dangling.append(r[":START_ID"])
        if r[":END_ID"] not in node_id_set:
            dangling.append(r[":END_ID"])

    # 6) 写出 JSON（与 load_neo4j.py 的 load_normalized 结构兼容）
    neo4j_ready = {
        "schema_version": data.get("schema_version", "v0.1"),
        "source": "06_PoC/etl/with_inferred.json",
        "generated_at": created_at,
        "reference_integrity": {
            "original_nodes": len(existing_nodes),
            "nodes": len(all_node_records),
            "edges": len(edges),
            "synthesized_nodes": len(synthesized),
            "dangling_references": len(dangling),
        },
        "nodes": [
            {"id": r["id"], "labels": r["labels"], "props": r["props"]}
            for r in all_node_records
        ],
        "edges": edges,
    }
    json_path = os.path.join(OUT_DIR, "neo4j_ready.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(neo4j_ready, f, ensure_ascii=False, indent=2)

    # 7) 打印证据
    print("=" * 64)
    print("Neo4j 就绪数据集构建完成")
    print("=" * 64)
    print(f"源文件            : {SRC}")
    print(f"输出目录          : {OUT_DIR}")
    print(f"节点数（既有）    : {len(existing_nodes)}")
    print(f"合成节点数        : {len(synthesized)}")
    print(f"节点总数          : {len(all_node_records)}  （预期 36）")
    print(f"边数              : {len(edges)}  （预期 52）")
    print(f"悬空引用数        : {len(dangling)}  （预期 0）")
    print("-" * 64)
    print("合成节点清单（id → Label / ntype / domain）：")
    for s in synthesized:
        p = s["props"]
        print(f"  {s['id']:<28} -> {s['labels'][0]:<18} {p['ntype']:<18} {p['domain']}")
    print("-" * 64)
    print(f"CSV 节点表        : {nodes_csv}")
    print(f"CSV 关系表        : {rels_csv}")
    print(f"JSON 数据集       : {json_path}")
    print("=" * 64)

    # 8) 断言（静态自检，失败即非零退出）
    ok = (len(all_node_records) == 36 and len(edges) == 52
          and len(synthesized) == 14 and len(dangling) == 0)
    if not ok:
        print("[FAIL] 预期指标未达成，请检查。")
        return 1
    print("[OK] 全部指标达成：36 节点 / 52 边 / 14 合成 / 0 悬空。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
