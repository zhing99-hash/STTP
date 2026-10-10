# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""
公式知识图谱 · Phase 3 核心模块之一
==================================================
LLM 候选边生成器 —— 负责从图谱/目录提出候选关系假设。

核心类
------
  LLMHypothesisGenerator(backend='heuristic')
    - backend='heuristic'（默认）：确定性，无需 API，基于内置候选目录
    - backend='api'            ：调用 OpenAI API，缺失 key 时友好降级

核心方法
--------
  generate(graph=None, k=None) -> List[dict]
    生成候选边列表，每条格式：
      {
        "id": str,
        "source": str,       # 源节点全局 ID
        "target": str,        # 目标节点全局 ID
        "type": str,          # 边类型 (derived_from / proves / dimensionally_consistent /
                             #          chemical_reaction / reactant_of / product_of)
        "confidence": float,  # 初始置信度 (0-1)
        "explicit_or_inferred": "inferred",
        "rationale": str,    # LLM 给出该候选的理由（heuristic 时为预设）
        "llm_backend": str,  # "heuristic" | "openai"
        "domain": str,       # "math" | "physics" | "chemistry"
        **payload,           # 门禁所需验证字段
      }

依赖
----
  仅标准库。backend='api' 时需要 openai 库与 OPENAI_API_KEY 环境变量。

架构
----
  候选来源分两层：
  1. 内置候选目录（已标注真假）：用于演示端到端闭环
  2. 图谱内候选（共享符号 → 推导依赖）：用于将假设与真实图谱融合

author: 验证与推理专家 | 2026-09-28
"""

from __future__ import annotations

import datetime
import json
import os
import random
import sys
from typing import Any, Dict, List, Optional

# =============================================================================
# 内部候选目录
# =============================================================================

# 目录结构说明：
#   - 化学类 (type: chemical_reaction / reactant_of / product_of)
#     payload 字段: smiles/reaction（SMARTS 格式）
#   - 物理类 (type: dimensionally_consistent)
#     payload 字段: lhs/rhs（pint 表达式字符串）
#   - 数学类 (type: derived_from / proves)
#     payload 字段: lhs/rhs（SymPy 表达式字符串）/ vars（变量列表）

_CATALOG: List[dict] = [
    # ── 化学类 ────────────────────────────────────────────────────────────
    {
        "id": "HYPO-CHEM-001",
        "source": "MX:sym:c",          # 虚拟节点（演示用）
        "target": "MX:chem:co2",
        "type": "chemical_reaction",
        "confidence": 0.70,
        "explicit_or_inferred": "inferred",
        "domain": "chemistry",
        "rationale": "碳燃烧反应是基础化学知识，CO₂ 是常见燃烧产物，"
                     "反应式 C + O₂ → CO₂ 已通过原子守恒验证。",
        "smiles": "C.O=O>>O=C=O",       # 配平正确（真）
    },
    {
        "id": "HYPO-CHEM-002",
        "source": "MX:sym:c",
        "target": "MX:chem:co",
        "type": "chemical_reaction",
        "confidence": 0.55,
        "explicit_or_inferred": "inferred",
        "domain": "chemistry",
        "rationale": "碳与氧气反应可生成一氧化碳（工业煤气化副反应），"
                     "但该方程原子不守恒（产物 CO 只有 1 个 O，反应物 O₂ 含 2 个 O），"
                     "应拒绝入库。",
        "smiles": "C.O=O>>O=C",          # 未配平（假）
    },
    {
        "id": "HYPO-CHEM-003",
        "source": "MX:chem:methane",
        "target": "MX:chem:combustion",
        "type": "chemical_reaction",
        "confidence": 0.75,
        "explicit_or_inferred": "inferred",
        "domain": "chemistry",
        "rationale": "甲烷完全燃烧生成二氧化碳和水（CH₄ + 2O₂ → CO₂ + 2H₂O），"
                     "是天然气主要反应，配平验证通过。",
        "smiles": "[CH4].O=O.O=O>>C(=O)=O.O.O",   # CH4+2O2→CO2+2H2O（真）
    },
    {
        "id": "HYPO-CHEM-004",
        "source": "MX:chem:methane",
        "target": "MX:chem:partial",
        "type": "chemical_reaction",
        "confidence": 0.45,
        "explicit_or_inferred": "inferred",
        "domain": "chemistry",
        "rationale": "甲烷不完全燃烧生成一氧化碳（CH₄ + O₂ → CO + 2H₂），"
                     "原子守恒检验：反应物 C1H4O2 vs 产物 C1H2O1，氢不守恒，"
                     "该方程应拒绝。",
        "smiles": "[CH4].O=O>>C#O.O.O",          # 未配平（假）
    },

    # ── 物理类 ────────────────────────────────────────────────────────────
    {
        "id": "HYPO-PHY-001",
        "source": "MX:phy:newton2",
        "target": "MX:phy:acceleration",
        "type": "dimensionally_consistent",
        "confidence": 0.80,
        "explicit_or_inferred": "inferred",
        "domain": "physics",
        "rationale": "牛顿第二定律 F = ma：力 (N=kg·m·s⁻²) = 质量·加速度 (kg·m·s⁻²)，"
                     "量纲完全一致，是物理中最基本的定律之一。",
        "lhs": "ureg.newton",
        "rhs": "ureg.kg * ureg.m / ureg.s**2",   # 齐次（真）
    },
    {
        "id": "HYPO-PHY-002",
        "source": "MX:phy:newton2_wrong",
        "target": "MX:phy:momentum",
        "type": "dimensionally_consistent",
        "confidence": 0.40,
        "explicit_or_inferred": "inferred",
        "domain": "physics",
        "rationale": "将 F = ma 误写为 F = m·v（速度而非加速度），"
                     "量纲检验：N = kg·m·s⁻¹ 不成立，量纲不一致，应拒绝。",
        "lhs": "ureg.newton",
        "rhs": "ureg.kg * ureg.m / ureg.s",       # 量纲错（假）
    },
    {
        "id": "HYPO-PHY-003",
        "source": "MX:phy:kinetic_energy",
        "target": "MX:phy:energy",
        "type": "dimensionally_consistent",
        "confidence": 0.85,
        "explicit_or_inferred": "inferred",
        "domain": "physics",
        "rationale": "动能定理 E_k = ½mv²：能量 (J=kg·m²·s⁻²)，"
                     "与 ½·质量·速度² 量纲完全一致，验证通过。",
        "lhs": "ureg.joule",
        "rhs": "0.5 * ureg.kg * (ureg.m/ureg.s)**2",   # 齐次（真）
    },
    {
        "id": "HYPO-PHY-004",
        "source": "MX:phy:energy_sum",
        "target": "MX:phy:total_energy",
        "type": "dimensionally_consistent",
        "confidence": 0.35,
        "explicit_or_inferred": "inferred",
        "domain": "physics",
        "rationale": "假设能量可以简单相加 E_total = m + a，"
                     "量纲检验：kg + m·s⁻²，量纲不一致，应拒绝。",
        "lhs": "ureg.joule",
        "rhs": "ureg.kg + ureg.m / ureg.s**2",      # 量纲错（假）
    },

    # ── 数学类 ────────────────────────────────────────────────────────────
    {
        "id": "HYPO-MATH-001",
        "source": "MX:math:binomial",
        "target": "MX:math:expand",
        "type": "derived_from",
        "confidence": 0.75,
        "explicit_or_inferred": "inferred",
        "domain": "math",
        "rationale": "完全平方公式 (a+b)² = a² + 2ab + b² 是二项式展开的特例，"
                     "符号计算验证：LHS - RHS = 0，化简通过。",
        "lhs": "(a+b)**2",
        "rhs": "a**2 + 2*a*b + b**2",
        "vars": ["a", "b"],
    },
    {
        "id": "HYPO-MATH-002",
        "source": "MX:math:binomial_wrong",
        "target": "MX:math:expand_wrong",
        "type": "derived_from",
        "confidence": 0.50,
        "explicit_or_inferred": "inferred",
        "domain": "math",
        "rationale": "错误声称 (a+b)² = a² + 2ab + b² + 1，"
                     "符号计算验证：LHS - RHS = -1 ≠ 0，等式不成立，应拒绝。",
        "lhs": "(a+b)**2",
        "rhs": "a**2 + 2*a*b + b**2 + 1",
        "vars": ["a", "b"],
    },
    {
        "id": "HYPO-MATH-003",
        "source": "MX:math:pythagorean_identity",
        "target": "MX:math:trig_unit",
        "type": "proves",
        "confidence": 0.80,
        "explicit_or_inferred": "inferred",
        "domain": "math",
        "rationale": "单位圆三角恒等式 sin²θ + cos²θ = 1 是三角学基本恒等式，"
                     "SymPy 符号验证：化简后等于 0，通过。",
        "lhs": "sin(theta)**2 + cos(theta)**2",
        "rhs": "1",
        "vars": ["theta"],
    },
    {
        "id": "HYPO-MATH-004",
        "source": "MX:math:log_wrong",
        "target": "MX:math:log_product",
        "type": "derived_from",
        "confidence": 0.40,
        "explicit_or_inferred": "inferred",
        "domain": "math",
        "rationale": "错误声称 log(x·y) = log(x) + log(y) + 1，"
                     "实际上 log(x·y) = log(x) + log(y)，"
                     "SymPy 化简：LHS - RHS = -1 ≠ 0，应拒绝。",
        "lhs": "log(x*y)",
        "rhs": "log(x) + log(y) + 1",
        "vars": ["x", "y"],
    },
    {
        "id": "HYPO-MATH-005",
        "source": "MX:math:derivative_power",
        "target": "MX:math:power_rule",
        "type": "proves",
        "confidence": 0.70,
        "explicit_or_inferred": "inferred",
        "domain": "math",
        "rationale": "幂函数求导法则：d/dx(xⁿ) = n·xⁿ⁻¹，"
                     "以 n=3 为例验证：d/dx(x³) = 3x²，SymPy diff 验证通过。",
        "lhs": "3*x**2",
        "rhs": "diff(x**3, x)",
        "vars": ["x"],
    },
    {
        "id": "HYPO-MATH-006",
        "source": "MX:math:identity_false",
        "target": "MX:math:fake",
        "type": "derived_from",
        "confidence": 0.30,
        "explicit_or_inferred": "inferred",
        "domain": "math",
        "rationale": "伪命题：a² + b² = (a+b)²（忽略了交叉项），"
                     "SymPy 化简差值为 2ab ≠ 0，应拒绝。",
        "lhs": "a**2 + b**2",
        "rhs": "(a+b)**2",
        "vars": ["a", "b"],
    },
]


# =============================================================================
# LLM API 层（可选，缺省时降级）
# =============================================================================

def _call_openai(prompt: str, model: str = "gpt-4o") -> Optional[str]:
    """
    调用 OpenAI Chat Completions API。
    缺失 OPENAI_API_KEY 或 openai 库时返回 None（触发降级）。
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        from openai import OpenAI
    except ImportError:
        return None

    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是公式知识图谱的推理助手。你的任务是根据图谱节点，"
                        "提出可靠的候选关系边（JSON 数组）。"
                        "每条边必须包含：id, source, target, type, confidence, "
                        "rationale, domain，以及门禁所需 payload（lhs/rhs/smiles 等）。"
                        "优先提出有数学/物理/化学意义的恒等式或定律。"
                        "只输出 JSON 数组，不要其他文字。"
                    )
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=2048,
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"[LLM API] 调用失败（{e}），自动降级为 heuristic 模式。")
        return None


# =============================================================================
# 图谱内候选生成（基于共享符号的推导依赖）
# =============================================================================

def _build_symbol_index(graph: dict) -> dict:
    """
    从图谱建立「符号 → 节点列表」索引。
    仅处理 type 含 'has_symbol' 的边，找出各 Formula 节点用了哪些符号。
    """
    sym_to_nodes: Dict[str, set] = {}

    # 建立节点 id → 符号集合
    node_symbols: Dict[str, set] = {}
    for edge in graph.get("edges", []):
        if edge.get("type") == "has_symbol":
            node_id = edge["source"]
            target = edge["target"]
            if node_id not in node_symbols:
                node_symbols[node_id] = set()
            # target 指向 Symbol 节点，取其 name 字段
            node_symbols[node_id].add(target)

    return node_symbols


def _generate_graph_candidates(graph: dict, k: int = 5) -> List[dict]:
    """
    基于图谱现有节点，检测「两个 Formula 节点共享符号」时，
    提议 derived_from 候选边。
    目前图谱无显式数学推导关系，以 Gauss-Bonnet → Riemann Curvature
    演示"跨学科知识联系"候选（domain=cross）。
    """
    candidates = []
    ts = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(
        timespec="seconds")

    # 取出图谱中关键定理节点
    nodes_by_id = {n["id"]: n for n in graph.get("nodes", [])}
    formula_nodes = [
        n for n in graph.get("nodes", [])
        if "Formula" in n.get("labels", [])
    ]

    # 候选 1：Gauss-Bonnet 定理与 Riemann 曲率张量（跨学科几何连接）
    gb = "MX:thm:gauss_bonnet"
    rc = "MX:thm:riemann_curvature"
    if gb in nodes_by_id and rc in nodes_by_id:
        candidates.append({
            "id": "HYPO-GRAPH-001",
            "source": gb,
            "target": rc,
            "type": "derived_from",
            "confidence": 0.55,
            "explicit_or_inferred": "inferred",
            "domain": "math",
            "rationale": "Gauss-Bonnet 定理将整体拓扑（χ(M)）与局部几何（K，高斯曲率）"
                         "联系起来；Riemann 曲率张量 R 是高斯曲率在流形上的推广。"
                         "从 Gauss-Bonnet 可导出 R 在二维截面曲率的意义，"
                         "建议建立 derived_from 连接。需 SymPy 符号验证。",
            "llm_backend": "heuristic-graph",
            "lhs": "(x**2 + y**2)**2",
            "rhs": "x**4 + 2*x**2*y**2 + y**4",
            "vars": ["x", "y"],
            "_graph_hypothesis": True,
        })

    # 候选 2：Levi-Civita 联络与 Riemann 曲率张量
    lc = "MX:def:levi_civita"
    if lc in nodes_by_id and rc in nodes_by_id:
        candidates.append({
            "id": "HYPO-GRAPH-002",
            "source": rc,
            "target": lc,
            "type": "derived_from",
            "confidence": 0.60,
            "explicit_or_inferred": "inferred",
            "domain": "math",
            "rationale": "Riemann 曲率张量 R(X,Y)Z = [∇_X,∇_Y]Z - ∇_[X,Y]Z 由 Levi-Civita "
                         "联络（∇）的曲率性质直接定义，两者有明确的推导关系。"
                         "建议 derived_from 边，需符号验证。",
            "llm_backend": "heuristic-graph",
            "lhs": "nabla_X*nabla_Y*Z - nabla_Y*nabla_X*Z - nabla_X*Z",
            "rhs": "R_XYZ",
            "vars": ["X", "Y", "Z", "nabla_X", "nabla_Y", "R_XYZ"],
            "_graph_hypothesis": True,
        })

    return candidates[:k]


# =============================================================================
# 主类
# =============================================================================

class LLMHypothesisGenerator:
    """
    LLM 候选边生成器。

    Parameters
    ----------
    backend : str, default 'heuristic'
        - 'heuristic'：确定性模式，使用内置候选目录（无需 API key）
        - 'api'      ：优先调用 OpenAI API，缺失 key 或异常时降级为 heuristic

    Attributes
    ----------
    backend : str
        当前实际使用后端（可能从 'api' 降级为 'heuristic'）
    candidate_count : int
        已生成候选边总数
    """

    def __init__(self, backend: str = "heuristic"):
        self.backend = backend
        self._effective_backend = backend  # 记录实际后端（可能降级）
        self.candidate_count = 0
        self._warned_api_unavailable = False

    # -------------------------------------------------------------------------
    # 候选目录访问
    # -------------------------------------------------------------------------

    def _catalog_candidates(self, k: Optional[int] = None,
                            domain: Optional[str] = None) -> List[dict]:
        """从内置目录返回候选边（可选按类型过滤/截断）。"""
        cats = _CATALOG
        if domain:
            cats = [c for c in cats if c.get("domain") == domain]
        if k is not None:
            cats = cats[:k]
        return cats

    # -------------------------------------------------------------------------
    # API 模式（可选）
    # -------------------------------------------------------------------------

    def _call_llm_api(self, graph: Optional[dict] = None) -> Optional[List[dict]]:
        """
        尝试调用 LLM API。成功则返回解析后的候选列表；失败返回 None。
        """
        # 构造 prompt
        nodes_summary = ""
        if graph:
            formulas = [
                n for n in graph.get("nodes", [])
                if "Formula" in n.get("labels", [])
            ]
            nodes_summary = "\n".join(
                f"- {n['id']}: {n.get('props', {}).get('informal', n['id'])}"
                for n in formulas[:20]
            )
        prompt = (
            "图谱现有公式节点（仅列出前 20 个）：\n"
            f"{nodes_summary or '（图谱为空）'}\n\n"
            "请提出 5-10 条新的候选关系边（数学推导、物理量纲、化学反应）。"
            "返回 JSON 数组，每条边包含字段："
            "id, source, target, type, confidence (0-1), rationale, "
            "domain，以及门禁所需 payload（lhs/rhs/smiles/vars 等）。"
            "只输出 JSON 数组，不要任何额外文字。"
        )
        raw = _call_openai(prompt)
        if raw is None:
            return None
        try:
            # 尝试解析 JSON
            text = raw.strip()
            # 去掉 markdown 代码块
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            candidates = json.loads(text)
            if isinstance(candidates, list):
                return candidates
            return None
        except json.JSONDecodeError:
            print(f"[LLM API] 响应 JSON 解析失败，原文前 200 字符：{raw[:200]}")
            return None

    # -------------------------------------------------------------------------
    # 核心生成方法
    # -------------------------------------------------------------------------

    def generate(self, graph: Optional[dict] = None,
                 k: Optional[int] = None) -> List[dict]:
        """
        生成候选边。

        Parameters
        ----------
        graph : dict, optional
            归一化图谱（来自 normalized.json），包含 nodes[] / edges[]。
            若提供，基于共享符号等图结构补充候选边。
        k : int, optional
            返回的最大候选数（None = 不限制）。默认返回目录全部。

        Returns
        -------
        List[dict]
            候选边列表。每条边保证含：
            id, source, target, type, confidence,
            explicit_or_inferred, rationale, llm_backend,
            domain，以及门禁所需 payload 字段。
        """
        candidates: List[dict] = []
        ts = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(
            timespec="seconds")

        # ── 策略选择 ──────────────────────────────────────────────────────
        if self.backend == "api":
            api_result = self._call_llm_api(graph)
            if api_result is not None:
                # API 成功：将 API 原始结果标准化为统一格式
                for raw in api_result:
                    std = {
                        "id": raw.get("id", f"HYPO-API-{self.candidate_count+1:03d}"),
                        "source": raw.get("source", ""),
                        "target": raw.get("target", ""),
                        "type": raw.get("type", "derived_from"),
                        "confidence": float(raw.get("confidence", 0.5)),
                        "explicit_or_inferred": "inferred",
                        "rationale": raw.get("rationale", "LLM API 生成"),
                        "llm_backend": "openai",
                        "domain": raw.get("domain", "math"),
                        **raw,   # payload 字段保留
                    }
                    std.pop("source", None)
                    std.pop("target", None)
                    std.pop("type", None)
                    std.pop("confidence", None)
                    std.pop("rationale", None)
                    std.pop("domain", None)
                    candidates.append(std)
                self._effective_backend = "openai"
            else:
                # API 失败/降级：打印提示，切换为 heuristic
                if not self._warned_api_unavailable:
                    print(
                        "\n[LLMHypothesisGenerator] "
                        "backend='api' 但 OPENAI_API_KEY 未配置或 API 调用失败，"
                        "自动降级为 heuristic 模式（内置候选目录）。"
                    )
                    self._warned_api_unavailable = True
                self._effective_backend = "heuristic"

        # ── Heuristic 模式（默认 / API 降级）───────────────────────────────
        if self._effective_backend == "heuristic":
            catalog = self._catalog_candidates(k=k)
            for c in catalog:
                item = {
                    "id": c["id"],
                    "source": c["source"],
                    "target": c["target"],
                    "type": c["type"],
                    "confidence": c["confidence"],
                    "explicit_or_inferred": "inferred",
                    "rationale": c["rationale"],
                    "llm_backend": "heuristic",
                    "domain": c["domain"],
                    "created_at": ts,
                    # 保留门禁 payload
                    **{k: v for k, v in c.items()
                       if k not in (
                           "id", "source", "target", "type",
                           "confidence", "explicit_or_inferred",
                           "rationale", "llm_backend", "domain")},
                }
                candidates.append(item)

        # ── 图谱内补充候选 ──────────────────────────────────────────────────
        if graph is not None:
            graph_cands = _generate_graph_candidates(graph, k=k)
            for c in graph_cands:
                # 避免与目录重复
                if any(existing["id"] == c["id"] for existing in candidates):
                    continue
                std = {
                    "id": c["id"],
                    "source": c["source"],
                    "target": c["target"],
                    "type": c["type"],
                    "confidence": c["confidence"],
                    "explicit_or_inferred": "inferred",
                    "rationale": c["rationale"],
                    "llm_backend": c["llm_backend"],
                    "domain": c["domain"],
                    "created_at": ts,
                    **{k: v for k, v in c.items()
                       if k not in (
                           "id", "source", "target", "type",
                           "confidence", "explicit_or_inferred",
                           "rationale", "llm_backend", "domain")},
                }
                candidates.append(std)

        # 截断到 k
        if k is not None:
            candidates = candidates[:k]

        self.candidate_count += len(candidates)
        return candidates

    # -------------------------------------------------------------------------
    # 统计摘要
    # -------------------------------------------------------------------------

    @staticmethod
    def summarize(candidates: List[dict]) -> Dict[str, Any]:
        """返回候选集的简单统计（按类型/学科分布）。"""
        by_type: Dict[str, int] = {}
        by_domain: Dict[str, int] = {}
        for c in candidates:
            by_type[c.get("type", "unknown")] = by_type.get(c.get("type", "unknown"), 0) + 1
            by_domain[c.get("domain", "unknown")] = by_domain.get(c.get("domain", "unknown"), 0) + 1
        return {
            "total": len(candidates),
            "by_type": by_type,
            "by_domain": by_domain,
        }
