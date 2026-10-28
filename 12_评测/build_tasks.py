#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""build_tasks.py —— R11 固定任务评测集构建器（第 28 轮 · Phase 38）

复评 R11 完成定义：「公开候选空间、拆分规则、随机种子和逐类结果」。
本文件定义**专题任务集**：热力学 — 化学平衡 — 数学方法，覆盖 5 大类 +
6 型困难反例；`expected`（期望答案 / 拒答）**锚定独立权威源**（CODATA/IUPAC/
标准热力学关系式），不由被测图生成。

设计纪律
--------
* **答案独立**：期望值来自外部权威（NIST CODATA 2018 / SI 2019 / IUPAC 2021 /
  教科书标准关系），不是把图里的值抄一遍当答案。
* **困难反例必入**：6 型困难反例（温度缺失 / 物相混淆 / 质子化态不一致 /
  同分子式异构体 / 对数定义域 / 量纲相同但物理意义不同）每题至少一例。
* **切分冻结**：`dev` / `test` 由确定性分层抽样产生（SEED 固定、按类别分层、
  基本题与困难题同比例入两集），结果写入 `tasks.json` 冻结。
* **可复现**：同输入 ⇒ 同 `tasks.json`（无时间戳、无隐式随机源）。

局限（如实披露，见报告）
------------------------
任务与答案由**本轮依据复评要求设计**，权威源为外部标准，但**学科人员外部审查
仍待进行**（复评明确要求「不能完全由被测系统生成」——本集做到了「不由被测系统
生成」，但「学科人员已审」尚未满足）。因此本报告结论仅限：
**规则基线在自建冻结集上的表现**，不构成全学科可靠性证明。

用法
----
    python 12_评测/build_tasks.py            # 生成 tasks.json（幂等）
    python 12_评测/build_tasks.py --check     # 只校验，不写盘
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NORM_GRAPH = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT = os.path.join(HERE, "tasks.json")

SCHEMA = "sttp-eval-tasks/v1"
SEED = 20261012                     # 切分随机种子（冻结；baseline 本身确定，无采样）

# ---------------------------------------------------------------------------
# 类别与困难反例的受控词表
# ---------------------------------------------------------------------------
CATEGORIES = (
    "data_verification",    # 数据查证
    "unit_state",           # 单位与状态检查
    "numeric",              # 有限数值计算
    "method_selection",     # 方法选择
    "refusal",              # 条件不足时拒答
)

HARD_EXAMPLES = (
    "missing_temperature",          # 温度缺失
    "phase_confusion",              # 物相混淆
    "protonation_state",            # 质子化态不一致
    "isomer",                       # 同分子式异构体
    "log_domain",                   # 对数定义域
    "same_dimension_diff_meaning",  # 量纲相同但物理意义不同
)

# ---------------------------------------------------------------------------
# 独立权威源（答案出处，非被测图）
# ---------------------------------------------------------------------------
SRC_CODATA = "NIST CODATA 2018 基本物理常量表"
SRC_SI = "SI 2019 定义（第 26 届国际计量大会）"
SRC_IUPAC = "IUPAC 2021 标准原子量"
SRC_THERMO = "标准热力学关系 ΔG°=−RT ln K（IUPAC/NIST 通用形式）"
SRC_HH = "Henderson–Hasselbalch 方程（酸碱平衡标准形式）"
SRC_DIM = "量纲分析（Buckingham π 定理）"
SRC_PH = "pH 的操作定义 pH=−log10 a(H⁺)（IUPAC）"

CODATA = {
    "R":  (8.314462618, "J mol^-1 K^-1", SRC_CODATA),
    "NA": (6.02214076e23, "mol^-1", SRC_SI),
    "kB": (1.380649e-23, "J K^-1", SRC_SI),
    "e":  (1.602176634e-19, "C", SRC_SI),
    "h":  (6.62607015e-34, "J s", SRC_SI),
    "F":  (96485.33212, "C mol^-1", SRC_CODATA),
}


def _t(**kw):
    """构造一个任务，补默认字段。"""
    task = {
        "id": None, "category": None, "topic": None, "hardness": "basic",
        "hard_example": None, "question": "", "inputs": {}, "method": None,
        "expected": None, "criteria": "", "source": "", "graph_anchor": [],
        "split": None,
    }
    task.update(kw)
    return task


# ---------------------------------------------------------------------------
# 任务定义（40 题）
# ---------------------------------------------------------------------------
TASKS = [

    # ===================== 1. 数据查证 data_verification (10) =================
    _t(id="DV-01", category="data_verification", topic="thermo",
       question="NIST CODATA(2018) 给出的摩尔气体常数 R 是多少？图谱节点 TH:pq:gas_const "
                "记录的值是否与之相符（相对误差 ≤1e-9）？",
       inputs={"graph_node": "TH:pq:gas_const", "quantity": "R"},
       method="lookup_constant",
       expected={"kind": "value", "value": CODATA["R"][0], "unit": CODATA["R"][1],
                 "rel_tol": 1e-9},
       criteria="图谱值与 CODATA 2018 值相对误差 ≤1e-9 判正确；不符判错误；无节点判拒答。",
       source=SRC_CODATA, graph_anchor=["TH:pq:gas_const"]),

    _t(id="DV-02", category="data_verification", topic="cross", hardness="hard",
       question="由 N_A 与 k_B 复算摩尔气体常数 R=N_A·k_B，结果是否与 CODATA 的 R 一致？",
       inputs={"derived": "R", "from": ["CO:pq:avogadro", "SM:pq:boltz_const"],
               "relation": "product"},
       method="recompute_derived_constant",
       expected={"kind": "value", "value": CODATA["R"][0], "unit": CODATA["R"][1],
                 "rel_tol": 1e-8},
       criteria="用图上两个常量相乘复算，与 CODATA 值相对误差 ≤1e-8 判正确。",
       source=SRC_SI, graph_anchor=["CO:pq:avogadro", "SM:pq:boltz_const"]),

    _t(id="DV-03", category="data_verification", topic="cross",
       question="由 N_A 与元电荷 e 复算法拉第常数 F=N_A·e，结果是否与 CODATA 一致？",
       inputs={"derived": "F", "from": ["CO:pq:avogadro", "CO:pq:elementary_charge"],
               "relation": "product"},
       method="recompute_derived_constant",
       expected={"kind": "value", "value": CODATA["F"][0], "unit": CODATA["F"][1],
                 "rel_tol": 1e-8},
       criteria="图上两常量相乘，与 CODATA 值相对误差 ≤1e-8 判正确。",
       source=SRC_CODATA, graph_anchor=["CO:pq:avogadro", "CO:pq:elementary_charge"]),

    _t(id="DV-04", category="data_verification", topic="chem",
       question="银（Ag）的标准原子量（IUPAC 2021）是多少？",
       inputs={"symbol": "Ag", "field": "atomic_weight"},
       method="lookup_element",
       expected={"kind": "value", "value": 107.8682, "unit": "g mol^-1",
                 "abs_tol": 0.001},
       criteria="与 IUPAC 2021 值绝对误差 ≤0.001 判正确。", source=SRC_IUPAC),

    _t(id="DV-05", category="data_verification", topic="cross",
       question="阿伏伽德罗常数（SI 2019 定义，精确值）是多少？图谱 CO:pq:avogadro 是否相符？",
       inputs={"graph_node": "CO:pq:avogadro", "quantity": "NA"},
       method="lookup_constant",
       expected={"kind": "value", "value": CODATA["NA"][0], "unit": CODATA["NA"][1],
                 "rel_tol": 1e-12},
       criteria="相对误差 ≤1e-12 判正确。", source=SRC_SI, graph_anchor=["CO:pq:avogadro"]),

    _t(id="DV-06", category="data_verification", topic="thermo",
       question="玻尔兹曼常数（SI 2019 定义，精确值）是多少？图谱 SM:pq:boltz_const 是否相符？",
       inputs={"graph_node": "SM:pq:boltz_const", "quantity": "kB"},
       method="lookup_constant",
       expected={"kind": "value", "value": CODATA["kB"][0], "unit": CODATA["kB"][1],
                 "rel_tol": 1e-12},
       criteria="相对误差 ≤1e-12 判正确。", source=SRC_SI, graph_anchor=["SM:pq:boltz_const"]),

    _t(id="DV-07", category="data_verification", topic="cross",
       question="元电荷（SI 2019 定义，精确值）是多少？图谱 CO:pq:elementary_charge 是否相符？",
       inputs={"graph_node": "CO:pq:elementary_charge", "quantity": "e"},
       method="lookup_constant",
       expected={"kind": "value", "value": CODATA["e"][0], "unit": CODATA["e"][1],
                 "rel_tol": 1e-12},
       criteria="相对误差 ≤1e-12 判正确。", source=SRC_SI,
       graph_anchor=["CO:pq:elementary_charge"]),

    _t(id="DV-08", category="data_verification", topic="math",
       question="普朗克常数（SI 2019 定义，精确值）是多少？图谱 QM:pq:planck_const 是否相符？",
       inputs={"graph_node": "QM:pq:planck_const", "quantity": "h"},
       method="lookup_constant",
       expected={"kind": "value", "value": CODATA["h"][0], "unit": CODATA["h"][1],
                 "rel_tol": 1e-12},
       criteria="相对误差 ≤1e-12 判正确。", source=SRC_SI,
       graph_anchor=["QM:pq:planck_const"]),

    _t(id="DV-09", category="data_verification", topic="chem",
       question="铁（Fe）在元素周期表中的周期与族分别是多少？",
       inputs={"symbol": "Fe", "field": "period_group"},
       method="lookup_element",
       expected={"kind": "value", "value": {"period": 4, "group": 8}},
       criteria="周期与族同时正确判正确。",
       source="IUPAC 周期表（元素位置由原子序数唯一确定）"),

    _t(id="DV-10", category="data_verification", topic="chem",
       question="碳（C）的常规标准原子量（IUPAC 2021 惯例值）是多少？",
       inputs={"symbol": "C", "field": "atomic_weight"},
       method="lookup_element",
       expected={"kind": "value", "value": 12.011, "unit": "g mol^-1",
                 "abs_tol": 0.002},
       criteria="与 IUPAC 惯例值绝对误差 ≤0.002 判正确（C 存在区间 [12.0096,12.0116]）。",
       source=SRC_IUPAC),

    # ===================== 2. 单位与状态检查 unit_state (8) ====================
    _t(id="US-01", category="unit_state", topic="cross",
       question="ΔG°=−RT ln K 中的 R·T 是否具有「能量/物质的量」的量纲？"
                "（用图上 R 与温度量的量纲做乘积检验）",
       inputs={"quantities": ["gas_constant", "temperature"], "exponents": [1, 1],
               "expected_class": "molar_energy"},
       method="dimensional_product_check",
       expected={"kind": "boolean", "value": True},
       criteria="[R]·[T] 与摩尔能量量纲严格相等判正确。", source=SRC_DIM),

    _t(id="US-02", category="unit_state", topic="phys", hardness="hard",
       hard_example="same_dimension_diff_meaning",
       question="功（work）与力矩（torque）量纲相同（M L² T⁻²）。能否据此断定它们是"
                "同一物理量？",
       inputs={"a": "Work", "b": "Torque"},
       method="dimension_compare",
       expected={"kind": "choice", "value": "same_dimension_different_meaning"},
       criteria="答「量纲相同但物理意义不同，不能等同」判正确。", source=SRC_DIM),

    _t(id="US-03", category="unit_state", topic="phys", hardness="hard",
       hard_example="same_dimension_diff_meaning",
       question="冲量（impulse）与动量（momentum）量纲相同（M L T⁻¹）。它们是同一"
                "物理量吗？",
       inputs={"a": "Impulse", "b": "Momentum"},
       method="dimension_compare",
       expected={"kind": "choice", "value": "same_dimension_different_meaning"},
       criteria="答「量纲相同、物理意义不同」判正确。", source=SRC_DIM),

    _t(id="US-04", category="unit_state", topic="equilibrium",
       question="标准平衡常数 K 是无量纲量吗？（核对图谱节点 CE:pq:equilibrium_constant "
                "声明的 dimension）",
       inputs={"graph_node": "CE:pq:equilibrium_constant"},
       method="dimensionless_check",
       expected={"kind": "boolean", "value": True},
       criteria="答「是（严格为活度商，无量纲）」判正确。",
       source=SRC_THERMO + "；IUPAC 平衡常数（活度商）定义",
       graph_anchor=["CE:pq:equilibrium_constant"]),

    _t(id="US-05", category="unit_state", topic="equilibrium",
       question="pH 是无量纲量吗？（核对图谱节点 CE:pq:ph 声明的 dimension）",
       inputs={"graph_node": "CE:pq:ph"},
       method="dimensionless_check",
       expected={"kind": "boolean", "value": True},
       criteria="答「是（对数之商，无量纲）」判正确。", source=SRC_PH,
       graph_anchor=["CE:pq:ph"]),

    _t(id="US-06", category="unit_state", topic="phys", hardness="hard",
       hard_example="same_dimension_diff_meaning",
       question="仅凭量纲 M L² T⁻²，能否唯一判定某量是能量还是力矩？",
       inputs={"dimension": "M*L^2*T^-2", "candidates": ["energy", "torque"]},
       method="dimension_ambiguity",
       expected={"kind": "choice", "value": "cannot_determine_from_dimension_alone"},
       criteria="答「不能，量纲不足以区分」判正确。", source=SRC_DIM),

    _t(id="US-07", category="unit_state", topic="cross",
       question="单位 J mol⁻¹ K⁻¹ 与量纲 M L² T⁻² mol⁻¹ K⁻¹ 是否等价？",
       inputs={"unit": "J mol^-1 K^-1", "dimension": "M*L^2*T^-2*mol^-1*K^-1"},
       method="unit_dimension_match",
       expected={"kind": "boolean", "value": True},
       criteria="单位展开后的量纲与给定量纲严格相等判正确。", source=SRC_DIM),

    _t(id="US-08", category="unit_state", topic="thermo",
       question="摩尔熵（molar entropy）与熵（entropy）的量纲是否相同？",
       inputs={"a": "molar_entropy", "b": "entropy"},
       method="dimension_compare",
       expected={"kind": "choice", "value": "different_dimension_by_amount"},
       criteria="答「不同：摩尔熵 = 熵/物质的量，量纲相差 mol⁻¹」判正确。", source=SRC_DIM),

    # ===================== 3. 有限数值计算 numeric (8) =========================
    _t(id="NM-01", category="numeric", topic="equilibrium",
       question="298.15 K 下某反应标准吉布斯自由能变 ΔG°=−5.708 kJ/mol，求标准平衡常数 K。",
       inputs={"delta_g_kj": -5.708, "T_K": 298.15},
       method="compute_K_from_dG",
       expected={"kind": "value", "value": 10.0, "unit": "1", "rel_tol": 0.01},
       criteria="K=exp(−ΔG°/(RT))，相对误差 ≤1% 判正确（正确值 ≈10.0）。",
       source=SRC_THERMO),

    _t(id="NM-02", category="numeric", topic="equilibrium",
       question="298.15 K 下某反应标准平衡常数 K=1.0×10⁵，求标准吉布斯自由能变 ΔG°。",
       inputs={"K": 1.0e5, "T_K": 298.15},
       method="compute_dG_from_K",
       expected={"kind": "value", "value": -28.54, "unit": "kJ mol^-1",
                 "abs_tol": 0.05},
       criteria="ΔG°=−RT ln K，绝对误差 ≤0.05 kJ/mol 判正确（正确值 ≈−28.54）。",
       source=SRC_THERMO),

    _t(id="NM-03", category="numeric", topic="equilibrium",
       question="乙酸在 298.15 K 的酸解离常数 Ka=1.8×10⁻⁵，求 pKa。",
       inputs={"Ka": 1.8e-5},
       method="compute_pKa_from_Ka",
       expected={"kind": "value", "value": 4.74, "unit": "1", "abs_tol": 0.01},
       criteria="pKa=−log10(Ka)，绝对误差 ≤0.01 判正确（正确值 ≈4.745）。",
       source=SRC_PH),

    _t(id="NM-04", category="numeric", topic="equilibrium",
       question="某弱酸 pKa=4.75，其共轭碱/酸浓度比 [A⁻]/[HA]=10，用 Henderson–"
                "Hasselbalch 方程求 pH。",
       inputs={"pKa": 4.75, "ratio_base_acid": 10.0},
       method="compute_pH_HH",
       expected={"kind": "value", "value": 5.75, "unit": "1", "abs_tol": 0.01},
       criteria="pH=pKa+log10([A⁻]/[HA])，绝对误差 ≤0.01 判正确（正确值 5.75）。",
       source=SRC_HH),

    _t(id="NM-05", category="numeric", topic="equilibrium",
       question="298.15 K 下水的离子积 Kw=1.0×10⁻¹⁴，求 pKw 与中性水的 pH。",
       inputs={"Kw": 1.0e-14},
       method="compute_pKw_neutral_pH",
       expected={"kind": "value", "value": {"pKw": 14.0, "pH": 7.0}, "abs_tol": 0.01},
       criteria="pKw=14、中性 pH=7.00，两者均对判正确。", source=SRC_PH),

    _t(id="NM-06", category="numeric", topic="thermo",
       question="哈伯合成氨反应在 298.15 K：ΔH°=−92.4 kJ/mol、ΔS°=−198.3 J mol⁻¹ K⁻¹，"
                "求 ΔG°。",
       inputs={"delta_h_kj": -92.4, "delta_s_j": -198.3, "T_K": 298.15},
       method="compute_dG_from_dH_dS",
       expected={"kind": "value", "value": -33.28, "unit": "kJ mol^-1",
                 "abs_tol": 0.1},
       criteria="ΔG°=ΔH°−TΔS°，绝对误差 ≤0.1 kJ/mol 判正确（正确值 ≈−33.28）。",
       source="标准热力学关系 ΔG°=ΔH°−TΔS°；数据取自 NIST 热化学表（Haber 过程）"),

    _t(id="NM-07", category="numeric", topic="thermo",
       question="某反应 ΔG°=−33.28 kJ/mol（298.15 K）。该反应的平衡常数大于 1 还是小于 1？"
                "正反应在当前标准条件下自发吗？",
       inputs={"delta_g_kj": -33.28, "T_K": 298.15},
       method="sign_to_direction",
       expected={"kind": "choice", "value": "K>1_and_forward_spontaneous"},
       criteria="ΔG°<0 ⇒ K>1 且正反应自发，判正确。", source=SRC_THERMO),

    _t(id="NM-08", category="numeric", topic="equilibrium",
       question="298.15 K 下某反应 ΔG°=−5.708 kJ/mol，在某非标准组成下反应商 Q=0.10，"
                "求该组成下的 ΔG（用 ΔG=ΔG°+RT ln Q）。",
       inputs={"delta_g_kj": -5.708, "K": None, "Q": 0.10, "T_K": 298.15},
       method="compute_dG_at_composition",
       expected={"kind": "value", "value": -11.42, "unit": "kJ mol^-1",
                 "abs_tol": 0.05},
       criteria="ΔG=ΔG°+RT ln Q，绝对误差 ≤0.05 kJ/mol 判正确（正确值 ≈−11.42）。",
       source="标准热力学关系 ΔG=ΔG°+RT ln Q"),

    # ===================== 4. 方法选择 method_selection (6) ====================
    _t(id="MS-01", category="method_selection", topic="equilibrium",
       question="反应尚未达到平衡，已知各组分瞬时浓度，应使用「质量作用定律的平衡常数 K」"
                "还是「反应商 Q」来描述当前状态？",
       inputs={"at_equilibrium": False, "known": "瞬时浓度"},
       method="select_law",
       expected={"kind": "choice", "value": "reaction_quotient_Q"},
       criteria="未平衡 ⇒ 用反应商 Q，判正确。",
       source="IUPAC 化学平衡定义；复评示例（平衡位置 ≠ 速率判断）"),

    _t(id="MS-02", category="method_selection", topic="equilibrium",
       question="已知标准态 ΔG° 与某非标准组成下的反应商 Q，求该组成下的 ΔG，应使用哪个"
                "关系式？",
       inputs={"known": ["delta_g_std", "Q"]},
       method="select_gibbs_relation",
       expected={"kind": "choice", "value": "dG_equals_dG0_plus_RT_lnQ"},
       criteria="非标准组成 ⇒ ΔG=ΔG°+RT ln Q，判正确（不是 ΔG°=−RT ln K）。",
       source=SRC_THERMO),

    _t(id="MS-03", category="method_selection", topic="math",
       question="pH 定义式 pH=−log10 a(H⁺) 在使用「对数」时，图谱把它连到了哪个数学对象？"
                "该连接应以 uses_operator 还是 derived_from 表达？",
       inputs={"formula": "CE:fo:ph", "math_object": "MA:fo:log_product"},
       method="select_operator_bridge",
       expected={"kind": "choice", "value": "uses_operator"},
       criteria="「使用某数学对象的算子」⇒ uses_operator（非 derived_from），判正确。",
       source="复评 R04：关系名与实际检查命题一致", graph_anchor=["CE:fo:ph",
                                                             "MA:fo:log_product"]),

    _t(id="MS-04", category="method_selection", topic="math",
       question="Arrhenius 方程 k=A exp(−Ea/(RT)) 使用的数学对象是哪个？",
       inputs={"formula": "CE:fo:arrhenius"},
       method="select_math_object",
       expected={"kind": "choice", "value": "MA:pq:euler_e"},
       criteria="指数 ⇒ 自然常数 e（Euler's number），判正确。",
       source="指数函数定义", graph_anchor=["CE:fo:arrhenius"]),

    _t(id="MS-05", category="method_selection", topic="equilibrium", hardness="hard",
       question="已知一个反应的平衡常数 K 很大，能否据此判断该反应进行得很快（动力学速率高）？",
       inputs={"K": "large"},
       method="concept_equilibrium_vs_rate",
       expected={"kind": "boolean", "value": False},
       criteria="答「不能：热力学平衡位置不决定动力学速率」判正确。",
       source="化学动力学与热力学分离（复评示例）"),

    _t(id="MS-06", category="method_selection", topic="math",
       question="公式 ΔG°=−RT ln K（CE:fo:gibbs_standard）与数学对象「对数之积」"
                "（MA:fo:log_product）之间的关系，正确的关系类型是什么？",
       inputs={"formula": "CE:fo:gibbs_standard", "math_object": "MA:fo:log_product"},
       method="select_operator_bridge",
       expected={"kind": "choice", "value": "uses_operator"},
       criteria="公式使用对数的算子 ⇒ uses_operator，判正确。",
       source="复评 R04", graph_anchor=["CE:fo:gibbs_standard", "MA:fo:log_product"]),

    # ===================== 5. 条件不足时拒答 refusal (8) =======================
    _t(id="RF-01", category="refusal", topic="equilibrium", hardness="hard",
       hard_example="missing_temperature",
       question="已知某反应 ΔG°=−5.708 kJ/mol，求平衡常数 K。（未给出温度）",
       inputs={"delta_g_kj": -5.708, "T_K": None},
       method="refuse_missing_T",
       expected={"kind": "refusal", "reason_class": "missing_temperature"},
       criteria="缺温度 ⇒ 无法用 ΔG°=−RT ln K 定 K，应拒答（不得默认 298.15 K 硬算）。",
       source=SRC_THERMO),

    _t(id="RF-02", category="refusal", topic="equilibrium", hardness="hard",
       hard_example="phase_confusion",
       question="求某物质在水溶液中的 pH，但该物质实际处于固相、且未声明溶解/电离平衡。"
                "能否直接给出溶液 pH？",
       inputs={"phase": "solid", "medium": None, "equilibrium_declared": False},
       method="refuse_missing_phase",
       expected={"kind": "refusal", "reason_class": "phase_confusion"},
       criteria="物相/介质不足 ⇒ 拒答（不得把固相物质当溶质算 pH）。",
       source="酸碱平衡需明确物相与溶解平衡（IUPAC）"),

    _t(id="RF-03", category="refusal", topic="equilibrium", hardness="hard",
       hard_example="protonation_state",
       question="已知某多元酸的总酸解离常数 Ka，求 pH，但未指明溶液 pH 落在哪个质子化态"
                "区间、也未给出各级 Ka。能否直接计算？",
       inputs={"Ka_given": "first_only", "protonation_state": None,
               "n_protons": 3},
       method="refuse_protonation",
       expected={"kind": "refusal", "reason_class": "protonation_state"},
       criteria="质子化态/分级常数不足 ⇒ 拒答（不得用一级 Ka 代多级酸）。",
       source=SRC_HH + "（多级酸需逐级处理）"),

    _t(id="RF-04", category="refusal", topic="chem", hardness="hard",
       hard_example="isomer",
       question="给出某化合物的分子式 C₂H₆O 与其分子量，问「该物质的摩尔标准生成吉布斯能」。"
                "能否给出唯一值？",
       inputs={"formula": "C2H6O", "same_formula_isomers": ["ethanol", "dimethyl ether"]},
       method="refuse_isomer",
       expected={"kind": "refusal", "reason_class": "isomer"},
       criteria="同分子式异构体（乙醇 vs 二甲醚）⇒ 无唯一身份，应拒答。",
       source="同分异构体热力学量不同（NIST 热化学）"),

    _t(id="RF-05", category="refusal", topic="math", hardness="hard",
       hard_example="log_domain",
       question="计算 ln(K)，其中 K=0。",
       inputs={"K": 0.0},
       method="refuse_log_domain",
       expected={"kind": "refusal", "reason_class": "log_domain"},
       criteria="K≤0 时 ln K 无定义 ⇒ 拒答（不得返回 0 或 −∞ 当数值答案）。",
       source="实数对数定义域 x>0"),

    _t(id="RF-06", category="refusal", topic="phys", hardness="hard",
       hard_example="same_dimension_diff_meaning",
       question="给一个数值 25 N·m。请判断它是功还是力矩。",
       inputs={"value": 25, "unit": "N*m", "dimension": "M*L^2*T^-2"},
       method="refuse_dimension_ambiguous",
       expected={"kind": "refusal", "reason_class": "same_dimension_diff_meaning"},
       criteria="量纲不足以区分功/力矩 ⇒ 拒答（不得任选其一）。", source=SRC_DIM),

    _t(id="RF-07", category="refusal", topic="thermo", hardness="hard",
       question="求某反应的标准吉布斯自由能变 ΔG°。但既未声明标准态（浓度/分压基准），"
                "也未给出温度。",
       inputs={"standard_state": None, "T_K": None},
       method="refuse_missing_standard_state",
       expected={"kind": "refusal", "reason_class": "missing_standard_state"},
       criteria="标准态与温度均未定 ⇒ 拒答（ΔG° 依赖标准态约定）。",
       source="标准态约定（IUPAC）：ΔG° 须绑定压力/浓度与温度"),

    _t(id="RF-08", category="refusal", topic="phys", hardness="hard",
       question="给出一个数「8.314」。请判断它是否是能量量纲的量。",
       inputs={"value": 8.314, "unit": None},
       method="refuse_missing_unit",
       expected={"kind": "refusal", "reason_class": "missing_unit"},
       criteria="无量纲单位信息 ⇒ 无法判定量纲 ⇒ 拒答。",
       source="量纲分析需单位或量纲信息"),
]


# ---------------------------------------------------------------------------
# 确定性分层切分（冻结）
# ---------------------------------------------------------------------------
def assign_split(tasks, seed=SEED):
    """按类别分层，基本题/困难题同比例分入 dev/test；确定性（固定 seed）。"""
    rng = random.Random(seed)
    by_cat = collections.defaultdict(list)
    for t in tasks:
        by_cat[t["category"]].append(t)
    for cat in CATEGORIES:
        group = sorted(by_cat[cat], key=lambda t: t["id"])
        # 分困难 / 基本两条流，各自对半交替，确保两集都含困难反例
        for hard in (False, True):
            stream = [t for t in group if (t["hardness"] == "hard") == hard]
            rng.shuffle(stream)
            for i, t in enumerate(stream):
                t["split"] = "dev" if i % 2 == 0 else "test"
    return tasks


def validate(tasks):
    """结构校验：id 唯一、类别合法、困难反例覆盖齐全、expected 形态正确。"""
    errs = []
    ids = [t["id"] for t in tasks]
    dup = [k for k, v in collections.Counter(ids).items() if v > 1]
    if dup:
        errs.append("重复 id: %s" % dup)
    for t in tasks:
        if t["category"] not in CATEGORIES:
            errs.append("%s 类别非法: %s" % (t["id"], t["category"]))
        if t["hard_example"] and t["hard_example"] not in HARD_EXAMPLES:
            errs.append("%s 困难反例类型非法: %s" % (t["id"], t["hard_example"]))
        if not t["method"]:
            errs.append("%s 缺 method" % t["id"])
        if not isinstance(t["expected"], dict) or "kind" not in t["expected"]:
            errs.append("%s expected 形态错误" % t["id"])
        if not t["source"]:
            errs.append("%s 缺 source（须锚定独立权威源）" % t["id"])
        if t["split"] not in ("dev", "test"):
            errs.append("%s split 未分配" % t["id"])
    covered = {t["hard_example"] for t in tasks if t["hard_example"]}
    missing = set(HARD_EXAMPLES) - covered
    if missing:
        errs.append("困难反例未全覆盖，缺: %s" % sorted(missing))
    return errs


def graph_facts():
    """**现算**权威图的规模与 sha256，供 `solution_space` 声明用。

    ★ 为什么必须用现算（D152）：`solution_space.graph` 原先写死
      「9657 节点 / 48712 边」。后果不是"数字旧了"，而是**这个字段永远无法自证新鲜** ——
      重跑评测（报告 mtime 会更新）也救不了它，因为它压根不从输入来。
      2026-10-28 实测：重跑后报告是新的，声明仍是旧图，而真实图已变成 9750/48813。
      **一份写着"我基于 X"、实际基于 Y 的报告，比没有声明更危险** —— 它会让人信它。

    ★ 只声明规模不够（两个不同的图可能撞规模）⇒ 同时给 sha256，
      让「陈旧报告哨兵」能做**强校验**。
    """
    # ★ 非通过出口的自述必须有**唯一实现**（铁律 #92）：本文件的 `[前置缺失]` 出口
    #   在加进来的同一轮就被 `interp_selfreport_check` 的 Q3 静态扫描咬住 ——
    #   **缺件分支在绿树上永不执行，只有静态扫描能咬住**（D107 的学费）。
    for _p in (ROOT, os.path.join(ROOT, "06_PoC")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    import interp_note as IN                             # noqa: E402

    if not os.path.exists(NORM_GRAPH):
        print("[前置缺失] 找不到权威图 %s（RC=2）—— 拒绝用写死的旧值冒充当前输入"
              % os.path.relpath(NORM_GRAPH).replace("\\", "/"))
        print("        原因：%s" % IN.skip_detail("规范图缺件（先跑 `bash sttp.sh export`）"))
        print("        ★ 为什么不给默认值：写死的「我基于 X」会让人信它（D152）。")
        raise SystemExit(2)
    h = hashlib.sha256()
    with open(NORM_GRAPH, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    with open(NORM_GRAPH, encoding="utf-8") as f:
        g = json.load(f)
    return {"nodes": len(g["nodes"]), "edges": len(g["edges"]),
            "sha256": h.hexdigest()}


def build():
    tasks = [dict(t) for t in TASKS]
    tasks = assign_split(tasks)
    errs = validate(tasks)
    if errs:
        raise SystemExit("任务集校验失败：\n  " + "\n  ".join(errs))

    gf = graph_facts()          # ★ 现算（D152），不写死

    doc = {
        "schema": SCHEMA,
        "description": "STTP R11 固定任务评测集：热力学-化学平衡-数学方法专题",
        "seed": SEED,
        "split_rule": "按类别分层；困难题与基本题各自对半交替分入 dev/test（固定 seed=%d，确定性）"
                      % SEED,
        "answer_policy": "expected 锚定独立权威源（CODATA/SI/IUPAC/标准热力学关系），"
                         "不由被测图生成；学科人员外部审查待进行。",
        "solution_space": {
            # ★ 现算，不写死（D152）：写死的声明字段**永远无法自证新鲜**。
            "graph": "06_PoC/etl/normalized.json（权威规范图，%d 节点 / %d 边）"
                     % (gf["nodes"], gf["edges"]),
            "graph_sha256": gf["sha256"],
            "truth_tables": ["11_真实数据/element_reference.py",
                             "11_真实数据/dimension_table.py"],
            "note": "基线只读上述候选空间；不访问网络、不使用 LLM。",
        },
        "hard_examples": list(HARD_EXAMPLES),
        "categories": list(CATEGORIES),
        "counts": {
            "total": len(tasks),
            "by_category": dict(collections.Counter(t["category"] for t in tasks)),
            "by_split": dict(collections.Counter(t["split"] for t in tasks)),
            "by_hardness": dict(collections.Counter(t["hardness"] for t in tasks)),
            "hard_examples": dict(collections.Counter(
                t["hard_example"] for t in tasks if t["hard_example"])),
        },
        "tasks": sorted(tasks, key=lambda t: t["id"]),
    }
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验，不写盘")
    a = ap.parse_args()
    doc = build()
    if a.check:
        print("✅ 任务集校验通过：%d 题（%s）"
              % (doc["counts"]["total"], doc["counts"]["by_category"]))
        return
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    c = doc["counts"]
    print("已写入 %s" % os.path.relpath(OUT).replace("\\", "/"))
    print("  题数      : %d" % c["total"])
    print("  按类别    : %s" % c["by_category"])
    print("  按切分    : %s" % c["by_split"])
    print("  困难反例  : %s" % c["hard_examples"])


if __name__ == "__main__":
    main()
