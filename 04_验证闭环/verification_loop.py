# -*- coding: utf-8 -*-
"""
公式知识图谱 · Phase 3 核心模块之二
==================================================
校验闭环（Verification Loop）—— 候选边的三道门禁自动验证。

核心类
------
  VerificationLoop
    verify(candidate)  →  Verdict dict
    verify_all(candidates) → List[Verdict dict]

Verdict 字典格式
----------------
{
    "candidate": dict,          # 原始候选边
    "verdict": str,             # VERIFIED | REJECTED | NEEDS_REVIEW
    "final_confidence": float,  # 0-1，校验后置信度
    "gate": str,                # 执行的 gate 名 (R-CHEM / R-PHY / R-MATH / N/A)
    "evidence": str,            # 门禁给出的详细证据
    "verified_at": str,         # ISO 时间戳
    "error_codes": List[str],   # 触发错误码
    "verified": bool,           # True only when VERIFIED
    "rejected": bool,           # True only when REJECTED
}

门禁路由规则
------------
  chemical_reaction / reactant_of / product_of
    → gate_chem(): RDKit 原子守恒检查
  dimensionally_consistent
    → gate_phy(): pint 量纲齐次性检查
  derived_from / proves
    → gate_math(): SymPy 符号等式验证
  其他
    → NEEDS_REVIEW（无自动门禁）

置信度策略
----------
  VERIFIED   → final_confidence = max(initial, 0.9)
  REJECTED   → final_confidence = initial * 0.2
  NEEDS_REVIEW → final_confidence = initial * 0.5（低置信，需人工）

author: 验证与推理专家 | 2026-09-28
"""

from __future__ import annotations

import datetime
import sys
import traceback
from typing import Any, Dict, List, Optional, Tuple

# =============================================================================
# 门禁函数（各学科核心验证）
# 所有门禁自带 import 守卫，缺失库时返回 NEEDS_REVIEW，绝不崩溃
# =============================================================================

# -----------------------------------------------------------------------------
# GATE R-CHEM：化学方程式配平检查（RDKit）
# -----------------------------------------------------------------------------

def gate_chem(candidate: dict) -> Tuple[str, str, List[str]]:
    """
    化学门禁：RDKit 原子守恒检查。

    Returns
    -------
    (verdict, evidence, error_codes)
        verdict: "VERIFIED" | "REJECTED" | "NEEDS_REVIEW"
    """
    try:
        from rdkit import Chem
        from rdkit.Chem import rdChemReactions
    except ImportError:
        return (
            "NEEDS_REVIEW",
            "RDKit 未安装，无法执行化学配平检查。安装：pip install rdkit-pypi",
            ["R-CHEM-00"]
        )

    smarts = candidate.get("smiles") or candidate.get("smarts") or ""
    if not smarts:
        return "NEEDS_REVIEW", "候选边缺少 smiles 字段，无法验证", ["R-CHEM-02"]

    def count_atoms(mols) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for mol in mols:
            if mol is None:
                continue
            for atom in mol.GetAtoms():
                counts[atom.GetSymbol()] = counts.get(atom.GetSymbol(), 0) + 1
        return counts

    try:
        rxn = rdChemReactions.ReactionFromSmarts(smarts)
        if not rxn:
            return (
                "REJECTED",
                f"R-CHEM-02：无法解析 SMARTS 字符串 '{smarts}'",
                ["R-CHEM-02"]
            )
    except Exception as e:
        return (
            "REJECTED",
            f"R-CHEM-02：SMARTS 解析异常 {e}",
            ["R-CHEM-02"]
        )

    r_counts = count_atoms(rxn.GetReactants())
    p_counts = count_atoms(rxn.GetProducts())

    if r_counts == p_counts:
        diff_str = ", ".join(
            f"{el}: 反应物 {r_counts.get(el,0)} → 产物 {p_counts.get(el,0)}"
            for el in sorted(set(list(r_counts) + list(p_counts)))
        )
        return (
            "VERIFIED",
            f"R-CHEM 原子守恒检验通过。原子计数：{r_counts}（反应物）= {p_counts}（产物）。"
            f"详情：{diff_str}",
            []
        )
    else:
        diff = {
            el: r_counts.get(el, 0) - p_counts.get(el, 0)
            for el in set(list(r_counts) + list(p_counts))
        }
        non_zero = {el: v for el, v in diff.items() if v != 0}
        return (
            "REJECTED",
            f"R-CHEM-01：原子守恒违反。反应物原子 {r_counts}，产物原子 {p_counts}，"
            f"差值（非零原子）: {non_zero}。"
            f"示例差值：{dict(list(non_zero.items())[:3])}",
            ["R-CHEM-01"]
        )


# -----------------------------------------------------------------------------
# GATE R-PHY：物理量量纲齐次性检查（pint）
# -----------------------------------------------------------------------------

# 全局 UnitRegistry（延迟初始化）
_UREG: Optional[Any] = None

def _get_ureg():
    global _UREG
    if _UREG is None:
        import pint as _pint_mod
        _UREG = _pint_mod.UnitRegistry()
    return _UREG


def _dim_of(expr_str: str, ureg) -> Optional[str]:
    """将物理量表达式转为量纲字符串（repr 规范化）。"""
    expr_str = expr_str.strip()
    if not expr_str:
        return None
    # 纯标识符：尝试作为单位名
    if expr_str.isidentifier():
        try:
            return repr(getattr(ureg, expr_str).dimensionality)
        except AttributeError:
            return None
    # 带运算符：安全 eval
    allowed_chars = set("0123456789.+-*/**()e ")
    if not all(c in allowed_chars or c.isalpha() for c in expr_str):
        return None
    try:
        val = eval(
            expr_str,
            {
                "ureg": ureg,
                "Quantity": ureg.Quantity,
                "sqrt": lambda x: x ** 0.5,
                "sin": ureg.Quantity,
                "cos": ureg.Quantity,
                "exp": ureg.Quantity,
                "log": ureg.Quantity,
            }
        )
        return repr(val.dimensionality)
    except Exception:
        return None


def gate_phy(candidate: dict) -> Tuple[str, str, List[str]]:
    """
    物理门禁：pint 量纲齐次性检查。

    Returns
    -------
    (verdict, evidence, error_codes)
    """
    try:
        ureg = _get_ureg()
    except Exception:
        return (
            "NEEDS_REVIEW",
            "pint 未安装或 UnitRegistry 初始化失败。安装：pip install pint",
            ["R-PHY-00"]
        )

    lhs = candidate.get("lhs", "")
    rhs = candidate.get("rhs", "")
    if not lhs or not rhs:
        return (
            "NEEDS_REVIEW",
            "候选边缺少 lhs/rhs 字段，无法进行量纲验证",
            ["R-PHY-03"]
        )

    lhs_dim = _dim_of(lhs, ureg)
    rhs_dim = _dim_of(rhs, ureg)

    def fmt_dim(d: Optional[str]) -> str:
        """将 repr(UnitsContainer(...)) 格式化为人类可读字符串。"""
        if not d:
            return "<解析失败>"
        import re
        parts = re.findall(r"'(\[[^']+\])':\s*([-\d]+)", d)
        readable = []
        for base, exp in parts:
            base_name = base.strip("[]")
            exp_i = int(exp)
            if exp_i == 1:
                readable.append(base_name)
            elif exp_i == -1:
                readable.append(f"1/{base_name}")
            else:
                readable.append(f"{base_name}^{exp_i}")
        return "·".join(readable) if readable else d

    if lhs_dim is None:
        return (
            "REJECTED",
            f"R-PHY-02：无法解析左侧表达式 '{lhs}'（未知单位或语法错误）",
            ["R-PHY-02"]
        )
    if rhs_dim is None:
        return (
            "REJECTED",
            f"R-PHY-02：无法解析右侧表达式 '{rhs}'（未知单位或语法错误）",
            ["R-PHY-02"]
        )

    homogeneous = (lhs_dim == rhs_dim)
    if homogeneous:
        return (
            "VERIFIED",
            f"R-PHY 量纲齐次性检验通过。"
            f"左侧量纲={fmt_dim(lhs_dim)}，右侧量纲={fmt_dim(rhs_dim)}，完全一致。",
            []
        )
    else:
        return (
            "REJECTED",
            f"R-PHY-01：量纲不一致。"
            f"左侧量纲={fmt_dim(lhs_dim)}，右侧量纲={fmt_dim(rhs_dim)}，不相等。"
            f"公式物理意义存疑，拒绝入库。",
            ["R-PHY-01"]
        )


# -----------------------------------------------------------------------------
# GATE R-MATH：数学符号等式验证（SymPy）
# -----------------------------------------------------------------------------

def _parse_math(expr_str: str, symbols: List[str]) -> Optional[Any]:
    """解析数学表达式字符串为 SymPy 表达式。"""
    try:
        import sympy as sp
        from sympy.parsing.sympy_parser import (
            parse_expr,
            standard_transformations,
            implicit_multiplication_application,
            convert_xor,
        )
        TRANSFORMS = standard_transformations + (
            implicit_multiplication_application,
            convert_xor,
        )
        local_dict = {s: sp.Symbol(s, real=True, positive=True) for s in symbols}
        local_dict.update(sp.__dict__)
        return parse_expr(
            expr_str,
            transformations=TRANSFORMS,
            local_dict=local_dict,
            evaluate=True
        )
    except Exception:
        return None


def gate_math(candidate: dict) -> Tuple[str, str, List[str]]:
    """
    数学门禁：SymPy 符号等式验证。

    Returns
    -------
    (verdict, evidence, error_codes)
    """
    try:
        import sympy as sp
    except ImportError:
        return (
            "NEEDS_REVIEW",
            "SymPy 未安装，无法执行符号等式验证。安装：pip install sympy",
            ["R-MATH-00"]
        )

    lhs = candidate.get("lhs", "")
    rhs = candidate.get("rhs", "")
    vars_list = candidate.get("vars", [])

    if not lhs or not rhs:
        return (
            "NEEDS_REVIEW",
            "候选边缺少 lhs/rhs 字段，无法进行符号验证",
            ["R-MATH-03"]
        )

    sym_lhs = _parse_math(lhs, vars_list)
    sym_rhs = _parse_math(rhs, vars_list)

    if sym_lhs is None:
        return (
            "REJECTED",
            f"R-MATH-03：无法解析左侧表达式 '{lhs}'（SymPy 语法错误）",
            ["R-MATH-03"]
        )
    if sym_rhs is None:
        return (
            "REJECTED",
            f"R-MATH-03：无法解析右侧表达式 '{rhs}'（SymPy 语法错误）",
            ["R-MATH-03"]
        )

    # 核心验证：LHS - RHS 化简
    diff = sp.simplify(sym_lhs - sym_rhs)
    is_zero = (diff == 0)

    # 多重化简策略，防止漏判
    if not is_zero:
        diff2 = sp.simplify(sp.expand(diff))
        is_zero = (diff2 == 0)
        diff_display = diff2 if not is_zero else sp.Integer(0)
    else:
        diff_display = sp.Integer(0)

    if is_zero:
        return (
            "VERIFIED",
            f"R-MATH 符号等式验证通过。"
            f"变量列表: {vars_list or '无'}; "
            f"LHS={lhs}, RHS={rhs}; "
            f"LHS - RHS 化简 = {diff_display}（= 0），等式恒成立。",
            []
        )
    else:
        return (
            "REJECTED",
            f"R-MATH-01：等式不成立。"
            f"LHS={lhs}, RHS={rhs}; "
            f"LHS - RHS 化简结果 = {diff_display}（≠ 0）。"
            f"符号验证失败，拒绝入库。",
            ["R-MATH-01"]
        )


# -----------------------------------------------------------------------------
# 门禁路由
# -----------------------------------------------------------------------------

# 化学类边类型 → 化学门禁
CHEM_TYPES = {"chemical_reaction", "reactant_of", "product_of"}
# 物理类边类型 → 物理门禁
PHY_TYPES = {"dimensionally_consistent"}
# 数学类边类型 → 数学门禁
MATH_TYPES = {"derived_from", "proves"}


def route_to_gate(candidate: dict) -> Tuple[str, str, str, List[str]]:
    """
    根据候选边 type 路由到对应门禁，返回 (verdict, evidence, gate_name, error_codes)。

    Parameters
    ----------
    candidate : dict
        候选边（需含 type 字段）

    Returns
    -------
    (verdict, evidence, gate_name, error_codes)
        gate_name: "R-CHEM" | "R-PHY" | "R-MATH" | "N/A"
    """
    edge_type = candidate.get("type", "")

    if edge_type in CHEM_TYPES:
        verdict, evidence, codes = gate_chem(candidate)
        return verdict, evidence, "R-CHEM", codes

    if edge_type in PHY_TYPES:
        verdict, evidence, codes = gate_phy(candidate)
        return verdict, evidence, "R-PHY", codes

    if edge_type in MATH_TYPES:
        verdict, evidence, codes = gate_math(candidate)
        return verdict, evidence, "R-MATH", codes

    # 其他类型：无自动门禁
    return (
        "NEEDS_REVIEW",
        f"边类型 '{edge_type}' 目前无对应自动门禁（支持：{', '.join(CHEM_TYPES | PHY_TYPES | MATH_TYPES)}），"
        f"需人工审核",
        "N/A",
        []
    )


# =============================================================================
# 置信度策略
# =============================================================================

def apply_confidence_policy(
    initial_confidence: float,
    verdict: str
) -> Tuple[float, bool, bool]:
    """
    根据 verdict 应用置信度策略。

    Returns
    -------
    (final_confidence, verified, rejected)
    """
    if verdict == "VERIFIED":
        final = max(initial_confidence, 0.9)
        return final, True, False
    elif verdict == "REJECTED":
        final = initial_confidence * 0.2
        return final, False, True
    else:  # NEEDS_REVIEW
        final = initial_confidence * 0.5
        return final, False, False


# =============================================================================
# 主类：校验闭环
# =============================================================================

class VerificationLoop:
    """
    校验闭环 —— 对 LLM 候选边执行三道门禁验证，并更新置信度。

    使用方式
    --------
    vl = VerificationLoop()
    result = vl.verify(candidate)        # 单条
    results = vl.verify_all(candidates)  # 批量

    Attributes
    ----------
    total_processed : int
        已处理候选边总数
    stats : dict
        实时统计（VERIFIED / REJECTED / NEEDS_REVIEW 计数）
    """

    def __init__(self):
        self.total_processed = 0
        self.stats: Dict[str, int] = {
            "VERIFIED": 0,
            "REJECTED": 0,
            "NEEDS_REVIEW": 0,
        }

    def verify(self, candidate: dict) -> dict:
        """
        对单条候选边执行完整校验闭环。

        Parameters
        ----------
        candidate : dict
            LLM 生成的候选边（需含 id / type / confidence 等字段）

        Returns
        -------
        dict
            完整 Verdict 报告，格式：
            {
                "candidate": dict,           # 原始候选
                "verdict": str,              # VERIFIED | REJECTED | NEEDS_REVIEW
                "final_confidence": float,   # 0-1
                "gate": str,                 # R-CHEM | R-PHY | R-MATH | N/A
                "evidence": str,            # 门禁给出的详细证据
                "error_codes": List[str],    # 错误代码
                "verified_at": str,          # ISO 时间戳
                "verified": bool,
                "rejected": bool,
            }
        """
        ts = datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=8))
        ).isoformat(timespec="seconds")

        try:
            verdict, evidence, gate, error_codes = route_to_gate(candidate)
        except Exception as e:
            # 门禁崩溃时降级为 NEEDS_REVIEW，绝不抛出
            verdict = "NEEDS_REVIEW"
            evidence = f"门禁执行异常（{type(e).__name__}: {e}），需人工审核"
            gate = "N/A"
            error_codes = ["GATE-CRASH"]

        initial_conf = float(candidate.get("confidence", 0.5))
        final_conf, verified, rejected = apply_confidence_policy(initial_conf, verdict)

        self.total_processed += 1
        self.stats[verdict] = self.stats.get(verdict, 0) + 1

        return {
            "candidate": candidate,
            "verdict": verdict,
            "final_confidence": round(final_conf, 4),
            "gate": gate,
            "evidence": evidence,
            "error_codes": error_codes,
            "verified_at": ts,
            "verified": verified,
            "rejected": rejected,
        }

    def verify_all(self, candidates: List[dict]) -> List[dict]:
        """
        对候选边列表批量执行校验闭环。

        Parameters
        ----------
        candidates : List[dict]
            LLM 生成的候选边列表

        Returns
        -------
        List[dict]
            每条候选的 Verdict 报告列表
        """
        results = []
        for c in candidates:
            results.append(self.verify(c))
        return results

    def summary(self, results: List[dict]) -> Dict[str, Any]:
        """
        生成验证结果汇总统计。

        Returns
        -------
        dict
            包含 total / by_type / by_gate / confidence_stats / verified_edges 等
        """
        by_verdict = {"VERIFIED": 0, "REJECTED": 0, "NEEDS_REVIEW": 0}
        by_gate = {}
        by_type = {}
        by_domain = {}
        confidences = []
        verified_edges = []
        rejected_edges = []

        for r in results:
            v = r["verdict"]
            by_verdict[v] = by_verdict.get(v, 0) + 1
            gate = r.get("gate", "N/A")
            by_gate[gate] = by_gate.get(gate, 0) + 1
            cand = r["candidate"]
            etype = cand.get("type", "unknown")
            by_type[etype] = by_type.get(etype, 0) + 1
            domain = cand.get("domain", "unknown")
            by_domain[domain] = by_domain.get(domain, 0) + 1
            confidences.append(r["final_confidence"])

            if r["verified"]:
                verified_edges.append(r)
            elif r["rejected"]:
                rejected_edges.append(r)

        conf_avg = sum(confidences) / len(confidences) if confidences else 0.0
        conf_min = min(confidences) if confidences else 0.0
        conf_max = max(confidences) if confidences else 0.0

        return {
            "total": len(results),
            "by_verdict": by_verdict,
            "by_gate": by_gate,
            "by_type": by_type,
            "by_domain": by_domain,
            "confidence_stats": {
                "avg": round(conf_avg, 4),
                "min": round(conf_min, 4),
                "max": round(conf_max, 4),
            },
            "verified_count": len(verified_edges),
            "rejected_count": len(rejected_edges),
            "verified_edges": verified_edges,
            "rejected_edges": rejected_edges,
        }
