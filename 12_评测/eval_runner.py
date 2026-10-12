#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""eval_runner.py —— R11 规则基线运行器（第 28 轮 · Phase 38）

对 `12_评测/tasks.json`（40 题冻结任务集）执行**规则基线**：只读
`06_PoC/etl/normalized.json`（规范图）+ 两个真值表（`element_reference` /
`dimension_table`），**不访问网络、不使用 LLM**；每题产出一个结果
（`answer` / `refuse`），与锚定外部权威源的 `expected` 比对。

度量（复评 R11 要求）
---------------------
* 正确完成数 / 全部任务（**correct rate**）
* 尝试率 attempt = answered / total；拒答率 refusal = refused / total
* 逐类（category）与逐切分（dev/test）结果
* Wilson 95% 置信区间（公开样本数与区间）
* 错误类型分列：**假答**（该拒答却作答）/ **误拒**（该作答却拒答）/ **答错**

候选空间 / 拆分规则 / 随机种子
--------------------------------
候选空间与 seed 写入 `tasks.json` 元数据；切分为确定性分层抽样（seed=20261012）。
基线本身**无随机性**（seed 对基线无影响，仅为后续 GNN 对比留位）。

用法
----
    python 12_评测/eval_runner.py                # 跑全量，打印摘要
    python 12_评测/eval_runner.py --json out.json # 另存 JSON 报告
    python 12_评测/eval_runner.py --split test    # 只跑测试集
    python 12_评测/eval_runner.py --strict        # 任一困难反例答错 → 非零退出
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
POC = os.path.join(ROOT, "06_PoC")
REAL = os.path.join(ROOT, "11_真实数据")
TASKS = os.path.join(HERE, "tasks.json")
NORM = os.path.join(POC, "etl", "normalized.json")

sys.path.insert(0, REAL)
import dimension_table as dt                                    # noqa: E402
import element_reference as er                                  # noqa: E402

DIM = dt.DIM


# ---------------------------------------------------------------------------
# 图与真值表载入
# ---------------------------------------------------------------------------
class World:
    """基线可访问的候选空间：规范图 + 真值表。"""

    def __init__(self, norm=NORM):
        with open(norm, encoding="utf-8") as f:
            g = json.load(f)
        self.nodes = {n["id"]: n for n in g["nodes"]}
        self.edges = g["edges"]
        self._edge_types = collections.defaultdict(set)
        for e in self.edges:
            self._edge_types[(e["source"], e["target"])].add(e["type"])

    def prop(self, nid, key, default=None):
        return (self.nodes.get(nid, {}).get("props") or {}).get(key, default)

    def edge_types(self, src, dst):
        return set(self._edge_types.get((src, dst), set()))


# ---------------------------------------------------------------------------
# 工具：量纲向量运算 / 单位展开 / Wilson CI
# ---------------------------------------------------------------------------
def _vec(d):
    return {k: float(d.get(k, 0)) for k in DIM}


def _norm(d):
    return tuple((k, round(float(d.get(k, 0)), 9)) for k in DIM)


def dim_product(quantities, exponents):
    """量的量纲线性组合；任一未知返回 None（三态：不可判定 ≠ 0）。"""
    acc = collections.defaultdict(float)
    for q, e in zip(quantities, exponents):
        d = dt.dim_of(q)
        if d is None:
            return None
        for k, v in d.items():
            acc[k] += float(e) * v
    return acc


_UNIT_BASE = {
    # SI 单位（单位语义：m=米, K/k=开尔文, mol=摩尔, J=焦耳, N=牛顿 …）
    "j": {"M": 1, "L": 2, "T": -2}, "kg": {"M": 1}, "g": {"M": 1},
    "m": {"L": 1}, "s": {"T": 1}, "k": {"Th": 1}, "kelvin": {"Th": 1},
    "mol": {"N": 1}, "n": {"M": 1, "L": 1, "T": -2}, "pa": {"M": 1, "L": -1, "T": -2},
    "c": {"I": 1, "T": 1}, "v": {"M": 1, "L": 2, "T": -3, "I": -1},
    "a": {"I": 1}, "w": {"M": 1, "L": 2, "T": -3}, "cd": {"J": 1},
}

# 项目「量纲串」语法（与单位串**不同**：此处的 m 表示质量 M、l 表示长度 L …）
_DIMTOK_BASE = {
    "m": "M", "mass": "M", "kg": "M", "g": "M",
    "l": "L", "length": "L", "meter": "L", "metre": "L",
    "t": "T", "time": "T", "s": "T", "second": "T",
    "i": "I", "current": "I", "a": "I", "ampere": "I",
    "th": "Th", "temperature": "Th", "k": "Th", "kelvin": "Th",
    "n": "N", "amount": "N", "mol": "N", "mole": "N",
    "j": "J", "luminous": "J", "cd": "J", "candela": "J",
}


def _parse_tokens(s, base, preserve_case=False):
    """通用：把 `a^2 b^-1` / `a/b` 形式的串展开为量纲向量。"""
    if not s:
        return None
    raw = str(s).strip().replace("·", " ").replace("*", " ").replace("/", " ")
    acc = collections.defaultdict(float)
    for tok in raw.split():
        if not tok:
            continue
        m = re.match(r"^([A-Za-z]+)\^?(-?\d+)?$", tok)
        if not m:
            return None
        name = m.group(1) if preserve_case else m.group(1).lower()
        exp = float(m.group(2)) if m.group(2) else 1.0
        target = base.get(name) if preserve_case else base.get(name)
        if target is None:
            return None
        if isinstance(target, str):                       # 量纲串：token → 单一维度
            acc[target] += exp
        else:                                             # 单位：token → 量纲向量
            for k, v in target.items():
                acc[k] += exp * v
    return {k: v for k, v in acc.items() if abs(v) > 1e-9} or None


def expand_unit(unit):
    """单位串 → 量纲向量（单位语义）。支持空格分隔与斜杠形式。"""
    return _parse_tokens(unit, _UNIT_BASE)


def parse_dim_string(dimstr):
    """项目量纲串（`M*L^2*T^-2*mol^-1*K^-1`）→ 量纲向量（维度语义）。"""
    return _parse_tokens(dimstr, _DIMTOK_BASE)


def wilson(p_hat, n, z=1.96):
    """Wilson score 95% 置信区间。"""
    if n == 0:
        return (0.0, 0.0)
    denom = 1 + z * z / n
    center = (p_hat + z * z / (2 * n)) / denom
    half = z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


# ---------------------------------------------------------------------------
# 解题器（规则基线）
# ---------------------------------------------------------------------------
class Refuse(Exception):
    def __init__(self, reason_class):
        self.reason_class = reason_class


def _R(world):
    """从图上取摩尔气体常数 R（缺失则拒答）。"""
    v = world.prop("TH:pq:gas_const", "value")
    if v is None:
        raise Refuse("constant_missing_from_graph")
    return float(v)


def solve(task, world):
    """返回 (outcome, payload)。outcome ∈ {'answer','refuse'}。"""
    m = task["method"]
    inp = task["inputs"]
    fn = SOLVERS.get(m)
    if fn is None:
        return "refuse", {"reason_class": "no_rule_for_method:%s" % m}
    try:
        return "answer", fn(task, inp, world)
    except Refuse as r:
        return "refuse", {"reason_class": r.reason_class}


# ---- 1) 数据查证 ----
def s_lookup_constant(task, inp, world):
    nid = inp["graph_node"]
    got = world.prop(nid, "value")
    if got is None:
        raise Refuse("graph_value_missing")
    return float(got)


def s_recompute_derived_constant(task, inp, world):
    vals = []
    for nid in inp["from"]:
        v = world.prop(nid, "value")
        if v is None:
            raise Refuse("derived_input_missing:%s" % nid)
        vals.append(float(v))
    out = 1.0
    for v in vals:
        out *= v
    return out


def s_lookup_element(task, inp, world):
    rec = er.by_symbol(inp["symbol"])
    if not rec:
        raise Refuse("element_not_in_reference")
    field = inp["field"]
    if field == "atomic_weight":
        return rec.get("atomic_weight")
    if field == "period_group":
        z = rec["atomic_number"]
        return {"period": er.period_of_z(z), "group": er.group_of_z(z)}
    raise Refuse("unsupported_element_field:%s" % field)


# ---- 2) 单位与状态 ----
def s_dimensional_product_check(task, inp, world):
    got = dim_product(inp["quantities"], inp["exponents"])
    if got is None:
        raise Refuse("dimension_unknown_for_quantity")
    want = dt.dim_of(inp["expected_class"])
    if want is None:
        raise Refuse("target_class_dimension_unknown")
    return _norm(got) == _norm(want)


def s_dimension_compare(task, inp, world):
    a, b = inp["a"], inp["b"]
    da, db = dt.dim_of(a), dt.dim_of(b)
    if da is None or db is None:
        raise Refuse("dimension_unknown")
    va, vb = _vec(da), _vec(db)
    diff = {k: round(va[k] - vb[k], 9) for k in DIM if abs(va[k] - vb[k]) > 1e-9}
    if not diff:
        # 量纲相同 → 是否同一物理量？由名称判定：不同名 ⇒ 同量纲不同物理意义
        return ("same_physical_quantity" if a.strip().lower() == b.strip().lower()
                else "same_dimension_different_meaning")
    if set(diff) == {"N"}:
        # 仅相差「物质的量」因子 ⇒ 摩尔量 vs 广度量
        return "different_dimension_by_amount"
    return "different_dimension"


def s_dimensionless_check(task, inp, world):
    nid = inp["graph_node"]
    dim = world.prop(nid, "dimension_vector")   # R09 字段迁移：dimension → dimension_vector
    if dim is None:
        raise Refuse("graph_dimension_missing")
    return str(dim).strip() in ("1", "dimensionless", "")


def s_dimension_ambiguity(task, inp, world):
    cands = inp["candidates"]
    dims = {c: dt.dim_of(c) for c in cands}
    known = [c for c, d in dims.items() if d is not None]
    if len(known) < 2:
        raise Refuse("insufficient_candidates")
    if len({_norm(dims[c]) for c in known}) == 1:
        return "cannot_determine_from_dimension_alone"
    return "determinable"


def s_unit_dimension_match(task, inp, world):
    got = expand_unit(inp["unit"])
    if got is None:
        raise Refuse("unit_unparsable")
    want = parse_dim_string(inp["dimension"])
    if want is None:
        raise Refuse("dimension_unparsable")
    return _norm(got) == _norm(want)


# ---- 3) 有限数值计算 ----
def s_compute_K_from_dG(task, inp, world):
    if inp.get("T_K") is None:
        raise Refuse("missing_temperature")
    R = _R(world)
    dG = float(inp["delta_g_kj"]) * 1000.0
    return math.exp(-dG / (R * float(inp["T_K"])))


def s_compute_dG_from_K(task, inp, world):
    if inp.get("T_K") is None:
        raise Refuse("missing_temperature")
    K = float(inp["K"])
    if K <= 0:
        raise Refuse("log_domain")
    R = _R(world)
    return -R * float(inp["T_K"]) * math.log(K) / 1000.0


def s_compute_pKa_from_Ka(task, inp, world):
    Ka = float(inp["Ka"])
    if Ka <= 0:
        raise Refuse("log_domain")
    return -math.log10(Ka)


def s_compute_pH_HH(task, inp, world):
    ratio = float(inp["ratio_base_acid"])
    if ratio <= 0:
        raise Refuse("log_domain")
    return float(inp["pKa"]) + math.log10(ratio)


def s_compute_pKw_neutral_pH(task, inp, world):
    Kw = float(inp["Kw"])
    if Kw <= 0:
        raise Refuse("log_domain")
    return {"pKw": -math.log10(Kw), "pH": -math.log10(math.sqrt(Kw))}


def s_compute_dG_from_dH_dS(task, inp, world):
    if inp.get("T_K") is None:
        raise Refuse("missing_temperature")
    dH = float(inp["delta_h_kj"])
    dS = float(inp["delta_s_j"]) / 1000.0
    return dH - float(inp["T_K"]) * dS


def s_sign_to_direction(task, inp, world):
    dG = float(inp["delta_g_kj"])
    if abs(dG) < 1e-12:
        return "K=1_and_no_driving_force"
    if dG < 0:
        return "K>1_and_forward_spontaneous"
    return "K<1_and_reverse_spontaneous"


def s_compute_dG_at_composition(task, inp, world):
    if inp.get("T_K") is None:
        raise Refuse("missing_temperature")
    Q = float(inp["Q"])
    if Q <= 0:
        raise Refuse("log_domain")
    R = _R(world)
    return (float(inp["delta_g_kj"]) + R * float(inp["T_K"]) * math.log(Q) / 1000.0)


# ---- 4) 方法选择 ----
def s_select_law(task, inp, world):
    return ("mass_action_K" if inp.get("at_equilibrium") else "reaction_quotient_Q")


def s_select_gibbs_relation(task, inp, world):
    known = set(inp["known"])
    if known == {"delta_g_std", "Q"}:
        return "dG_equals_dG0_plus_RT_lnQ"
    if known == {"K"}:
        return "dG0_equals_minus_RT_lnK"
    raise Refuse("unknown_known_set")


def s_select_operator_bridge(task, inp, world):
    types = world.edge_types(inp["formula"], inp["math_object"])
    for t in ("uses_operator", "derived_from", "defines", "same_as"):
        if t in types:
            return t
    raise Refuse("no_bridge_edge_between_formula_and_math_object")


def s_select_math_object(task, inp, world):
    nid = inp["formula"]
    # 图上该公式指向的数学对象（uses_operator 目标）
    targets = {e["target"] for e in world.edges
               if e["source"] == nid and e["type"] == "uses_operator"}
    if not targets:
        raise Refuse("no_operator_bridge")
    return sorted(targets)[0]


def s_concept_equilibrium_vs_rate(task, inp, world):
    return False


# ---- 5) 拒答 ----
def s_refuse_missing_T(task, inp, world):
    if inp.get("T_K") is None:
        raise Refuse("missing_temperature")
    R = _R(world)
    return math.exp(-float(inp["delta_g_kj"]) * 1000.0 / (R * float(inp["T_K"])))


def s_refuse_missing_phase(task, inp, world):
    if inp.get("phase") != "aqueous" or not inp.get("equilibrium_declared"):
        raise Refuse("phase_confusion")
    return "computable"


def s_refuse_protonation(task, inp, world):
    if inp.get("protonation_state") is None and (inp.get("n_protons") or 1) > 1:
        raise Refuse("protonation_state")
    return "computable"


def s_refuse_isomer(task, inp, world):
    if len(inp.get("same_formula_isomers") or []) > 1:
        raise Refuse("isomer")
    return "unique"


def s_refuse_log_domain(task, inp, world):
    K = float(inp["K"])
    if K <= 0:
        raise Refuse("log_domain")
    return math.log(K)


def s_refuse_dimension_ambiguous(task, inp, world):
    unit = inp.get("unit")
    d = expand_unit(unit) if unit else None
    if d is None:
        raise Refuse("missing_unit")
    # N·m 量纲可对应能量（功）或力矩 → 不唯一 → 拒答
    clash = [c for c in ("energy", "torque")
             if dt.dim_of(c) is not None and _norm(dt.dim_of(c)) == _norm(d)]
    if len(clash) > 1:
        raise Refuse("same_dimension_diff_meaning")
    return "unique"


def s_refuse_missing_standard_state(task, inp, world):
    if inp.get("standard_state") is None or inp.get("T_K") is None:
        raise Refuse("missing_standard_state")
    return "computable"


def s_refuse_missing_unit(task, inp, world):
    if inp.get("unit") is None:
        raise Refuse("missing_unit")
    d = expand_unit(inp["unit"])
    if d is None:
        raise Refuse("unit_unparsable")
    return _norm(d) == _norm(dt.dim_of("energy") or {})


SOLVERS = {
    "lookup_constant": s_lookup_constant,
    "recompute_derived_constant": s_recompute_derived_constant,
    "lookup_element": s_lookup_element,
    "dimensional_product_check": s_dimensional_product_check,
    "dimension_compare": s_dimension_compare,
    "dimensionless_check": s_dimensionless_check,
    "dimension_ambiguity": s_dimension_ambiguity,
    "unit_dimension_match": s_unit_dimension_match,
    "compute_K_from_dG": s_compute_K_from_dG,
    "compute_dG_from_K": s_compute_dG_from_K,
    "compute_pKa_from_Ka": s_compute_pKa_from_Ka,
    "compute_pH_HH": s_compute_pH_HH,
    "compute_pKw_neutral_pH": s_compute_pKw_neutral_pH,
    "compute_dG_from_dH_dS": s_compute_dG_from_dH_dS,
    "sign_to_direction": s_sign_to_direction,
    "compute_dG_at_composition": s_compute_dG_at_composition,
    "select_law": s_select_law,
    "select_gibbs_relation": s_select_gibbs_relation,
    "select_operator_bridge": s_select_operator_bridge,
    "select_math_object": s_select_math_object,
    "concept_equilibrium_vs_rate": s_concept_equilibrium_vs_rate,
    "refuse_missing_T": s_refuse_missing_T,
    "refuse_missing_phase": s_refuse_missing_phase,
    "refuse_protonation": s_refuse_protonation,
    "refuse_isomer": s_refuse_isomer,
    "refuse_log_domain": s_refuse_log_domain,
    "refuse_dimension_ambiguous": s_refuse_dimension_ambiguous,
    "refuse_missing_standard_state": s_refuse_missing_standard_state,
    "refuse_missing_unit": s_refuse_missing_unit,
}


# ---------------------------------------------------------------------------
# 判分
# ---------------------------------------------------------------------------
def grade(task, outcome, payload):
    """返回 (verdict, detail)。verdict ∈ {'correct','incorrect','false_answer','false_refusal'}。"""
    exp = task["expected"]
    kind = exp["kind"]
    if kind == "refusal":
        if outcome == "refuse":
            ok = payload.get("reason_class") == exp["reason_class"]
            return ("correct" if ok else "incorrect",
                    "拒答(%s)" % payload.get("reason_class"))
        return "false_answer", "应拒答却作答: %s" % payload
    # 非拒绝型
    if outcome == "refuse":
        return "false_refusal", "应作答却拒答: %s" % payload.get("reason_class")
    if kind in ("choice",):
        ok = payload == exp["value"]
        return ("correct" if ok else "incorrect"), "got=%s exp=%s" % (payload, exp["value"])
    if kind == "boolean":
        ok = bool(payload) == bool(exp["value"])
        return ("correct" if ok else "incorrect"), "got=%s exp=%s" % (payload, exp["value"])
    if kind == "value":
        verdict, detail = _grade_value(payload, exp)
        return verdict, detail
    return "incorrect", "未知 expected.kind: %s" % kind


def _grade_value(payload, exp):
    ev = exp["value"]
    if isinstance(ev, dict):            # 结构化（如 period_group / pKw+pH）
        if not isinstance(payload, dict):
            return "incorrect", "期望结构对象，得到 %r" % (payload,)
        for k, v in ev.items():
            got = payload.get(k)
            if got is None or abs(float(got) - float(v)) > exp.get("abs_tol", 1e-6):
                return "incorrect", "字段 %s: got=%s exp=%s" % (k, got, v)
        return "correct", "全字段匹配"
    if payload is None:
        return "incorrect", "无值"
    try:
        got = float(payload)
    except (TypeError, ValueError):
        return "incorrect", "非数值: %r" % (payload,)
    if "rel_tol" in exp:
        ok = abs(got - float(ev)) <= abs(float(ev)) * exp["rel_tol"] + 1e-15
    elif "abs_tol" in exp:
        ok = abs(got - float(ev)) <= exp["abs_tol"]
    else:
        ok = abs(got - float(ev)) <= 1e-9
    return ("correct" if ok else "incorrect"), "got=%.6g exp=%s" % (got, ev)


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def _bucket(rows):
    n = len(rows)
    c = sum(1 for r in rows if r["verdict"] == "correct")
    ans = sum(1 for r in rows if r["outcome"] == "answer")
    ref = n - ans
    p = c / n if n else 0.0
    lo, hi = wilson(p, n)
    return {
        "n": n, "correct": c, "incorrect": n - c,
        "answered": ans, "refused": ref,
        "correct_rate": round(p, 4),
        "attempt_rate": round(ans / n, 4) if n else 0.0,
        "refusal_rate": round(ref / n, 4) if n else 0.0,
        "correct_ci95": [round(lo, 4), round(hi, 4)],
        "false_answer": sum(1 for r in rows if r["verdict"] == "false_answer"),
        "false_refusal": sum(1 for r in rows if r["verdict"] == "false_refusal"),
    }


def run(doc, world, split=None, verbose=True):
    tasks = [t for t in doc["tasks"] if split is None or t["split"] == split]
    rows = []
    for t in tasks:
        outcome, payload = solve(t, world)
        verdict, detail = grade(t, outcome, payload)
        rows.append({"id": t["id"], "category": t["category"], "split": t["split"],
                     "hardness": t["hardness"], "hard_example": t["hard_example"],
                     "outcome": outcome, "verdict": verdict, "detail": detail,
                     "payload": payload if not isinstance(payload, float) else round(payload, 8)})
        if verbose:
            mark = {"correct": "✅", "incorrect": "❌", "false_answer": "⛔",
                    "false_refusal": "🚫"}.get(verdict, "?")
            print("%s %-6s [%-16s/%s] %s" % (mark, t["id"], t["category"],
                                             t["split"], detail))

    summary = {"overall": _bucket(rows)}
    for dim in ("category", "split", "hardness"):
        summary["by_" + dim] = {
            k: _bucket([r for r in rows if r[dim] == k])
            for k in sorted({r[dim] for r in rows})
        }
    hard_rows = [r for r in rows if r["hard_example"]]
    summary["hard_kinds"] = {
        k: _bucket([r for r in hard_rows if r["hard_example"] == k])
        for k in sorted({r["hard_example"] for r in hard_rows})
    }
    return rows, summary


def selfcheck(doc, world):
    """非真空自检（铁律 #14/#38）：对每题注入确定性扰动，断言判分器**真的会判非通过**。

    「PASS 只说明当前没红」——须证明这套判分有能力红，否则 100% 无意义。
    """
    rows = []
    for t in doc["tasks"]:
        outcome, payload = solve(t, world)
        v0, _ = grade(t, outcome, payload)
        if outcome == "refuse":
            mo, mp = "answer", "mutated_value"
        elif isinstance(payload, bool):
            mo, mp = "answer", (not payload)
        elif isinstance(payload, float):
            mo, mp = "answer", payload * 2.0 + 1.0
        elif isinstance(payload, dict):
            mo = "answer"
            mp = {k: ((v * 2.0 + 1.0) if isinstance(v, (int, float)) else "mut")
                  for k, v in payload.items()}
        else:
            mo, mp = "answer", str(payload) + "_mutated"
        v1, _ = grade(t, mo, mp)
        rows.append({"id": t["id"], "baseline": v0, "mutated": v1,
                     "detected": v1 != "correct"})
    n = len(rows)
    det = sum(1 for r in rows if r["detected"])
    return {"n": n, "detected": det, "all_detected": det == n, "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="另存 JSON 报告到该路径")
    ap.add_argument("--split", choices=["dev", "test"], help="只跑指定切分")
    ap.add_argument("--strict", action="store_true",
                    help="任一困难反例答错（含假答/误拒）→ 非零退出")
    ap.add_argument("--selfcheck", action="store_true",
                    help="非真空自检：注入扰动，断言判分器真的会红")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    with open(TASKS, encoding="utf-8") as f:
        doc = json.load(f)
    world = World()

    if a.selfcheck:
        sc = selfcheck(doc, world)
        print("非真空自检：%d/%d 题扰动后判为非通过 ⇒ %s"
              % (sc["detected"], sc["n"], "判分器有牙 ✅" if sc["all_detected"] else "存在哑判 ❌"))
        if not sc["all_detected"]:
            for r in sc["rows"]:
                if not r["detected"]:
                    print("  ⚠ 未检出扰动: %s（%s → %s）" % (r["id"], r["baseline"], r["mutated"]))
        return 0 if sc["all_detected"] else 3

    rows, summary = run(doc, world, split=a.split, verbose=not a.quiet)

    o = summary["overall"]
    print("=" * 78)
    print("  总体：正确 %d/%d = %.1f%% ｜ 尝试率 %.1f%% ｜ 拒答率 %.1f%% ｜ 95%%CI [%.1f%%, %.1f%%]"
          % (o["correct"], o["n"], o["correct_rate"] * 100, o["attempt_rate"] * 100,
             o["refusal_rate"] * 100, o["correct_ci95"][0] * 100, o["correct_ci95"][1] * 100))
    print("  假答（该拒却答）%d ｜ 误拒（该答却拒）%d" % (o["false_answer"], o["false_refusal"]))
    if not a.quiet:
        print("-" * 78)
        for k, v in summary["by_category"].items():
            print("  [%-16s] %2d 题 正确 %.0f%%  尝试 %.0f%%  拒答 %.0f%%"
                  % (k, v["n"], v["correct_rate"] * 100, v["attempt_rate"] * 100,
                     v["refusal_rate"] * 100))
        print("-" * 78)
        for k, v in summary["by_split"].items():
            print("  [split=%-4s] %2d 题 正确 %.0f%%  尝试 %.0f%%"
                  % (k, v["n"], v["correct_rate"] * 100, v["attempt_rate"] * 100))
        print("-" * 78)
        for k, v in summary["hard_kinds"].items():
            print("  [hard=%-30s] %d 题 正确 %.0f%%" % (k, v["n"], v["correct_rate"] * 100))
    print("=" * 78)

    report = {
        "schema": "sttp-eval-report/v1",
        "tasks_schema": doc["schema"], "seed": doc["seed"],
        "split_rule": doc["split_rule"], "solution_space": doc["solution_space"],
        "answer_policy": doc["answer_policy"],
        "n_tasks": len(rows), "summary": summary, "results": rows,
    }
    if a.json:
        with open(a.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("报告已写入 %s" % os.path.relpath(a.json).replace("\\", "/"))

    if a.strict:
        bad = [r for r in rows if r["hard_example"] and r["verdict"] != "correct"]
        if bad:
            print("★ --strict：%d 条困难反例未通过 → 非零退出" % len(bad))
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
