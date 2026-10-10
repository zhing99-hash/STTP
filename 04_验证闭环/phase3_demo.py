# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""
公式知识图谱 · Phase 3 端到端演示
==================================================
运行 LLM 候选边生成 → 三道门禁校验闭环 → 结果回写图谱。

步骤
----
  1. 加载 06_PoC/etl/normalized.json（图谱）
  2. LLMHypothesisGenerator 生成候选边（含真/假混合）
  3. VerificationLoop 批量执行化学/物理/数学门禁
  4. 打印汇总（VERIFIED / REJECTED / NEEDS_REVIEW 计数 + 示例 verdict）
  5. 将 VERIFIED 推断边回写图谱 → 06_PoC/etl/with_inferred.json

运行
----
  python phase3_demo.py

输出
----
  - 控制台打印完整验证报告
  - 06_PoC/etl/with_inferred.json（追加了已验证推断边）

author: 验证与推理专家 | 2026-09-28
"""

from __future__ import annotations

import datetime
import io
import json
import os
import sys

# Windows 控制台 UTF-8 修复
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# -----------------------------------------------------------------------------
# 项目路径
# -----------------------------------------------------------------------------
ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)  # STTP/
ETL_DIR = os.path.join(ROOT, "06_PoC", "etl")
NORMALIZED_JSON = os.path.join(ETL_DIR, "normalized.json")
OUT_JSON = os.path.join(ETL_DIR, "with_inferred.json")

# -----------------------------------------------------------------------------
# 导入 Phase 3 模块
# -----------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(__file__))
from llm_hypothesis import LLMHypothesisGenerator
from verification_loop import VerificationLoop

# -----------------------------------------------------------------------------
# 工具
# -----------------------------------------------------------------------------

def banner(title: str, width: int = 72):
    sep = "=" * width
    print(f"\n{sep}")
    print(f"  {title}")
    print(sep)


def section(title: str, width: int = 72):
    print(f"\n{'─' * width}")
    print(f"  ▶ {title}")
    print(f"{'─' * width}")


def print_verdict_row(result: dict, max_detail: int = 200):
    """打印单条验证结果（用于示例展示）。"""
    cand = result["candidate"]
    v = result["verdict"]
    icon = {"VERIFIED": "✅", "REJECTED": "❌", "NEEDS_REVIEW": "⚠️"}.get(v, "?")
    evidence = result["evidence"]
    if len(evidence) > max_detail:
        evidence = evidence[:max_detail] + "…"

    print(f"\n  {icon} [{v}] {cand.get('id', 'N/A')}")
    print(f"     类型: {cand.get('type', 'N/A')}  |  学科: {cand.get('domain', 'N/A')}")
    print(f"     源: {cand.get('source', 'N/A')}")
    print(f"     目标: {cand.get('target', 'N/A')}")
    print(f"     理由: {cand.get('rationale', 'N/A')[:120]}{'…' if len(cand.get('rationale',''))>120 else ''}")
    print(f"     门禁: {result['gate']}  |  初始置信: {cand.get('confidence', 0.5)}  →  最终置信: {result['final_confidence']}")
    if result['error_codes']:
        print(f"     错误码: {result['error_codes']}")
    print(f"     证据: {evidence}")


def print_summary(summary: dict):
    """打印汇总统计。"""
    print("\n")
    banner("校验汇总报告", 72)
    print(f"\n  总候选边数 : {summary['total']}")
    print(f"\n  判决分布：")
    for k, v in summary["by_verdict"].items():
        pct = v / summary["total"] * 100 if summary["total"] > 0 else 0
        print(f"    {k:<15s}  {v:>3d} 条  ({pct:5.1f}%)")

    print(f"\n  按门禁分布：")
    for k, v in summary["by_gate"].items():
        print(f"    {k:<10s}  {v:>3d} 条")

    print(f"\n  按类型分布：")
    for k, v in summary["by_type"].items():
        print(f"    {k:<30s}  {v:>3d} 条")

    print(f"\n  按学科分布：")
    for k, v in summary["by_domain"].items():
        print(f"    {k:<15s}  {v:>3d} 条")

    cs = summary["confidence_stats"]
    print(f"\n  置信度统计（校验后）：")
    print(f"    平均  {cs['avg']:.4f}")
    print(f"    最小  {cs['min']:.4f}")
    print(f"    最大  {cs['max']:.4f}")

    print(f"\n  验证通过（VERIFIED）: {summary['verified_count']} 条")
    print(f"  验证拒绝（REJECTED）: {summary['rejected_count']} 条")
    verified = summary["verified_count"]
    rejected = summary["rejected_count"]
    total = summary["total"]
    print(f"  人工待审（NEEDS_REVIEW）: {total - verified - rejected} 条")


def build_verified_edges(results: list) -> list:
    """
    将 VERIFIED 候选边转换为符合图 Schema 的 edges 条目。
    追加到 normalized.json 的 edges[] 后即为完整图谱。
    """
    verified_edges = []
    for r in results:
        if not r.get("verified", False):
            continue
        cand = r["candidate"]
        ts = r.get("verified_at", datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=8))
        ).isoformat(timespec="seconds"))

        edge = {
            "id": f"INFERRED:{cand.get('id', 'unknown')}",
            "source": cand.get("source", ""),
            "target": cand.get("target", ""),
            "type": cand.get("type", "derived_from"),
            "kind": "llm_inferred",
            "props": {
                "confidence": r["final_confidence"],
                "confidence_before_verify": cand.get("confidence", 0.5),
                "explicit_or_inferred": "inferred",
                "source": cand.get("llm_backend", "heuristic"),
                "created_at": ts,
                "verified_at": ts,
                "verification_gate": r["gate"],
                "verification_evidence": r["evidence"],
                "verification_error_codes": r["error_codes"],
                "rationale": cand.get("rationale", ""),
                "domain": cand.get("domain", "math"),
                "verified": True,
                "rejected": False,
                "original_candidate_id": cand.get("id", ""),
                "version": "v0.1",
            }
        }
        verified_edges.append(edge)
    return verified_edges


# -----------------------------------------------------------------------------
# 主流程
# -----------------------------------------------------------------------------

def main():
    banner("公式知识图谱 · Phase 3 端到端演示")
    print(f"\n  项目根目录 : {ROOT}")
    print(f"  加载图谱   : {NORMALIZED_JSON}")

    # ── Step 1：加载图谱 ─────────────────────────────────────────────────
    section("Step 1 — 加载图谱")
    if os.path.exists(NORMALIZED_JSON):
        with open(NORMALIZED_JSON, "r", encoding="utf-8") as f:
            graph = json.load(f)
        node_count = len(graph.get("nodes", []))
        edge_count = len(graph.get("edges", []))
        print(f"  ✅ 成功加载图谱：{node_count} 个节点 / {edge_count} 条边")
    else:
        print(f"  ⚠️  图谱文件不存在（{NORMALIZED_JSON}），以空图谱继续")
        graph = {"nodes": [], "edges": []}

    # ── Step 2：生成候选边 ───────────────────────────────────────────────
    section("Step 2 — LLM 候选边生成（heuristic 模式）")
    gen = LLMHypothesisGenerator(backend="heuristic")
    candidates = gen.generate(graph=graph, k=None)
    print(f"  生成候选边 {len(candidates)} 条")

    summ = LLMHypothesisGenerator.summarize(candidates)
    print(f"\n  按类型分布: {summ['by_type']}")
    print(f"  按学科分布: {summ['by_domain']}")

    # ── Step 3：运行校验闭环 ──────────────────────────────────────────────
    section("Step 3 — 三道门禁校验闭环")
    print("  门禁说明：")
    print("    R-CHEM → RDKit 原子守恒检查（化学类边）")
    print("    R-PHY  → pint 量纲齐次性检查（物理类边）")
    print("    R-MATH → SymPy 符号等式验证（数学类边）")
    print("    其他   → NEEDS_REVIEW（无自动门禁）")
    print()

    vl = VerificationLoop()
    results = vl.verify_all(candidates)

    # ── Step 4：汇总报告 ─────────────────────────────────────────────────
    section("Step 4 — 验证结果汇总")
    summary = vl.summary(results)
    print_summary(summary)

    # ── Step 5：示例 verdict 详情 ────────────────────────────────────────
    section("Step 5 — 示例 verdict 详情")
    print("\n  【VERIFIED 示例（前 2 条）】")
    verified = [r for r in results if r["verdict"] == "VERIFIED"]
    for r in verified[:2]:
        print_verdict_row(r)

    print("\n\n  【REJECTED 示例（前 2 条）】")
    rejected = [r for r in results if r["verdict"] == "REJECTED"]
    for r in rejected[:2]:
        print_verdict_row(r)

    nr = [r for r in results if r["verdict"] == "NEEDS_REVIEW"]
    if nr:
        print(f"\n\n  【NEEDS_REVIEW 示例（前 1 条）】")
        print_verdict_row(nr[0])

    # ── Step 6：回写图谱 ────────────────────────────────────────────────
    section("Step 6 — 回写图谱（追加 VERIFIED 推断边）")
    new_edges = build_verified_edges(results)
    print(f"  追加 {len(new_edges)} 条已验证推断边到图谱")

    # 读取原始图谱，追加新边
    if os.path.exists(NORMALIZED_JSON):
        with open(NORMALIZED_JSON, "r", encoding="utf-8") as f:
            base_graph = json.load(f)
    else:
        base_graph = {"nodes": [], "edges": []}

    base_graph["nodes"] = base_graph.get("nodes", [])
    base_graph["edges"] = base_graph.get("edges", []) + new_edges
    base_graph["_phase3_meta"] = {
        "phase": "3-llm-inference",
        "verified_inferred_edges_added": len(new_edges),
        "verified_at": datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=8))
        ).isoformat(timespec="seconds"),
        "verification_summary": {
            k: v for k, v in summary.items()
            if k not in ("verified_edges", "rejected_edges")
        }
    }

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(base_graph, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 写入 {OUT_JSON}")
    print(f"     现有边 + 新增推断边 = {len(base_graph['edges'])} 条")

    # ── 完成 ────────────────────────────────────────────────────────────
    banner("Phase 3 演示完成")
    print(f"""
  产出摘要：
    · 候选边总数      : {len(candidates)}
    · VERIFIED（通过）  : {summary['verified_count']}
    · REJECTED（拒绝）  : {summary['rejected_count']}
    · NEEDS_REVIEW     : {len(results) - summary['verified_count'] - summary['rejected_count']}
    · 追加到图谱      : {OUT_JSON}

  置信度策略说明：
    VERIFIED   → final_confidence = max(初始, 0.9)
    REJECTED   → final_confidence = 初始 × 0.2
    NEEDS_REVIEW → final_confidence = 初始 × 0.5（低置信待审）

  Phase 3 核心闭环已打通：
    LLM 提出候选关系 → 三道门禁自动验证 → 通过者置信度确认/回写图谱
  """)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("\n❌ phase3_demo.py 执行异常：")
        traceback.print_exc()
        sys.exit(1)
