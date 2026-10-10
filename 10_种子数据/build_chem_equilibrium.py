# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Phase 7h — 化学平衡垂直切片（路线图 Phase 7 清单中最后一个化学子领域）。

覆盖：质量作用定律 / 反应商 / ΔG–K 关系 / Le Chatelier / 酸碱（pH·pOH·Kw·Ka·Henderson）/ 溶度积 /
Nernst 方程 / Arrhenius 方程，并置入一个真实平衡反应（哈伯法）与水的自偶电离。

设计要点 —— **跨学科桥是主角**（本项目北极星是「跨学科」）：
  * 化学 ↔ 热力学：ΔG°=−RT lnK 直接引用 TH 切片的 R / T / gas_const / entropy
  * 化学 ↔ 数学　：pH / Henderson 建立在 MA 切片的对数（log_product / euler_e）之上
  * 化学 ↔ 电化学：Nernst 方程引用 EM 切片的 voltage
  * 化学 ↔ 物理量库：浓度锚到 PB:pq:concentration（PhysicsBabel 真实数据侧）
  * 化学 ↔ 元素层：哈伯法反应引用 EK:el:N / EK:el:H（A5 统一后的规范元素节点）

外部引用一律通过 build_and_write(extra_external=...) 显式声明 —— 参照既有切片的
「same_as 桥 + 直接引用」约定，不重复建同义节点。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_common as sc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "seed_chem_equilibrium.json")
nodes, edges = [], []

# --------------------------------------------------------------------------
# 公式
# --------------------------------------------------------------------------
nodes += [
    sc.n("CE:fo:mass_action", "formula", "Law of mass action (equilibrium constant)",
         "chem.equilibrium", latex=r"K=\frac{[C]^{c}[D]^{d}}{[A]^{a}[B]^{b}}",
         informal="对 aA+bB ⇌ cC+dD，平衡常数等于产物浓度幂之积比反应物浓度幂之积。"),
    sc.n("CE:fo:reaction_quotient", "formula", "Reaction quotient",
         "chem.equilibrium", latex=r"Q=\frac{[C]^{c}[D]^{d}}{[A]^{a}[B]^{b}}",
         informal="与 K 同形，但用任意时刻浓度；平衡时 Q=K，Q<K 反应正向、Q>K 逆向。"),
    sc.n("CE:fo:gibbs_standard", "formula", "Standard Gibbs energy and equilibrium constant",
         "chem.equilibrium", latex=r"\Delta G^{\circ}=-RT\ln K",
         informal="标准吉布斯自由能变与平衡常数的定量关系（热力学与化学的桥梁）。"),
    sc.n("CE:fo:gibbs_reaction", "formula", "Gibbs energy at arbitrary composition",
         "chem.equilibrium", latex=r"\Delta G=\Delta G^{\circ}+RT\ln Q",
         informal="任意组成下的反应吉布斯自由能；ΔG<0 自发正向。"),
    sc.n("CE:fo:le_chatelier", "formula", "Le Chatelier's principle",
         "chem.equilibrium", latex=r"\text{system shifts to oppose the disturbance}",
         informal="体系受扰动时平衡向抵消该扰动的方向移动（浓度/压强/温度）。"),
    sc.n("CE:fo:ph", "formula", "pH definition",
         "chem.acidbase", latex=r"pH=-\log_{10}[H^{+}]",
         informal="pH 为氢离子浓度的负常用对数。"),
    sc.n("CE:fo:poh", "formula", "pOH definition",
         "chem.acidbase", latex=r"pOH=-\log_{10}[OH^{-}]",
         informal="pOH 为氢氧根浓度的负常用对数。"),
    sc.n("CE:fo:kw", "formula", "Water autoionization constant",
         "chem.acidbase", latex=r"K_w=[H^{+}][OH^{-}]=1.0\times10^{-14}",
         informal="25 ℃ 纯水中 Kw=1.0×10⁻¹⁴，故 pH+pOH=14。"),
    sc.n("CE:fo:ka", "formula", "Acid dissociation constant",
         "chem.acidbase", latex=r"K_a=\frac{[H^{+}][A^{-}]}{[HA]}",
         informal="弱酸解离常数，表征酸强度。"),
    sc.n("CE:fo:henderson", "formula", "Henderson–Hasselbalch equation",
         "chem.acidbase", latex=r"pH=pK_a+\log_{10}\frac{[A^{-}]}{[HA]}",
         informal="缓冲溶液 pH 计算式，同样建立在对数之上。"),
    sc.n("CE:fo:ksp", "formula", "Solubility product",
         "chem.solubility", latex=r"K_{sp}=[A^{+}]^{m}[B^{-}]^{n}",
         informal="难溶电解质饱和溶液中离子浓度幂之积，与溶解度互为换算。"),
    sc.n("CE:fo:nernst", "formula", "Nernst equation",
         "chem.electrochem", latex=r"E=E^{\circ}-\frac{RT}{nF}\ln Q",
         informal="电池电动势与浓度/分压的定量关系（化学↔电化学↔热力学）。"),
    sc.n("CE:fo:arrhenius", "formula", "Arrhenius equation",
         "chem.kinetics", latex=r"k=A\,e^{-E_a/(RT)}",
         informal="速率常数随温度呈指数增长（化学↔数学指数↔热力学）。"),
]

# --------------------------------------------------------------------------
# 物理量 / 常数
# --------------------------------------------------------------------------
nodes += [
    sc.n("CE:pq:equilibrium_constant", "physical_quantity", "Equilibrium constant",
         "chem.equilibrium", symbol="K", dimension="1",
         informal="无量纲（严格为活度商，理想稀溶液近似为浓度商）。"),
    sc.n("CE:pq:reaction_quotient", "physical_quantity", "Reaction quotient",
         "chem.equilibrium", symbol="Q", dimension="1"),
    sc.n("CE:pq:gibbs_energy", "physical_quantity", "Gibbs free energy",
         "chem.equilibrium", symbol="G", dimension="M*L^2*T^-2"),
    sc.n("CE:pq:gibbs_std", "physical_quantity", "Standard Gibbs free energy change",
         "chem.equilibrium", symbol="G°", dimension="M*L^2*T^-2"),
    sc.n("CE:pq:ph", "physical_quantity", "pH", "chem.acidbase", symbol="pH", dimension="1"),
    sc.n("CE:pq:ka", "physical_quantity", "Acid dissociation constant",
         "chem.acidbase", symbol="Ka", dimension="1"),
    sc.n("CE:pq:pka", "physical_quantity", "pKa", "chem.acidbase", symbol="pKa", dimension="1"),
    sc.n("CE:pq:ksp", "physical_quantity", "Solubility product",
         "chem.solubility", symbol="Ksp", dimension="1"),
    sc.n("CE:pq:concentration", "physical_quantity", "Molar concentration",
         "chem.quantity", symbol="c", dimension="N*L^-3"),
    sc.n("CE:pq:cell_potential", "physical_quantity", "Cell potential",
         "chem.electrochem", symbol="E", dimension="M*L^2*T^-3*I^-1"),
    sc.n("CE:pq:rate_constant", "physical_quantity", "Reaction rate constant",
         "chem.kinetics", symbol="k"),
    sc.n("CE:pq:activation_energy", "physical_quantity", "Activation energy",
         "chem.kinetics", symbol="Ea", dimension="M*L^2*T^-2"),
    sc.n("CE:pq:faraday_const", "constant", "Faraday constant",
         "chem.constant", symbol="F", value=96485.33212, unit="C/mol",
         dimension="I*T*N^-1"),
]

# --------------------------------------------------------------------------
# 单位
# --------------------------------------------------------------------------
nodes += [
    sc.n("CE:un:molar", "unit", "mole per litre", "chem.unit",
         symbol="mol/L", dimension="N*L^-3", derived_from="mol/dm^3"),
]

# --------------------------------------------------------------------------
# 符号
# --------------------------------------------------------------------------
nodes += [
    sc.n("CE:sy:K", "symbol", "K (equilibrium constant)", "chem.symbol", latex="K"),
    sc.n("CE:sy:Q", "symbol", "Q (reaction quotient)", "chem.symbol", latex="Q"),
    sc.n("CE:sy:G", "symbol", "G (Gibbs energy)", "chem.symbol", latex="G"),
    sc.n("CE:sy:pH", "symbol", "pH", "chem.symbol", latex=r"\mathrm{pH}"),
    sc.n("CE:sy:Ka", "symbol", "K_a (acid constant)", "chem.symbol", latex=r"K_a"),
    sc.n("CE:sy:Ksp", "symbol", "K_sp (solubility product)", "chem.symbol", latex=r"K_{sp}"),
    sc.n("CE:sy:c", "symbol", "c (molar concentration)", "chem.symbol", latex="c"),
    sc.n("CE:sy:k", "symbol", "k (rate constant)", "chem.symbol", latex="k"),
    sc.n("CE:sy:Ea", "symbol", "E_a (activation energy)", "chem.symbol", latex=r"E_a"),
    sc.n("CE:sy:F", "symbol", "F (Faraday constant)", "chem.symbol", latex="F"),
    sc.n("CE:sy:conc", "symbol", "[ ] (molarity bracket)", "chem.symbol", latex=r"[\,]"),
]

# --------------------------------------------------------------------------
# 分子 / 反应（真实化学）
# --------------------------------------------------------------------------
nodes += [
    sc.n("CE:mo:nh3", "molecule", "Ammonia", "chem.molecule",
         formula="NH3", smiles="N"),
    sc.n("CE:rx:haber", "reaction", "Haber process (equilibrium)",
         "chem.equilibrium", equation="N2 + 3 H2 <=> 2 NH3", reversible=True,
         exothermic=True, note="工业合成氨；升温不利正向（放热）、加压有利（体积减小）。"),
    sc.n("CE:rx:water_autoionization", "reaction", "Water autoionization",
         "chem.acidbase", equation="2 H2O <=> H3O+ + OH-", reversible=True),
]

# --------------------------------------------------------------------------
# 边：公式 -> 符号
# --------------------------------------------------------------------------
SYM = {
    "CE:fo:mass_action": ["CE:sy:K", "CE:sy:conc"],
    "CE:fo:reaction_quotient": ["CE:sy:Q", "CE:sy:conc"],
    "CE:fo:gibbs_standard": ["CE:sy:G", "TH:sy:R", "TH:sy:T"],
    "CE:fo:gibbs_reaction": ["CE:sy:G", "CE:sy:Q", "TH:sy:R", "TH:sy:T"],
    "CE:fo:ph": ["CE:sy:pH", "CE:sy:conc"],
    "CE:fo:poh": ["CE:sy:pH", "CE:sy:conc"],
    "CE:fo:kw": ["CE:sy:K", "CE:sy:conc"],
    "CE:fo:ka": ["CE:sy:Ka", "CE:sy:conc"],
    "CE:fo:henderson": ["CE:sy:pH", "CE:sy:Ka"],
    "CE:fo:ksp": ["CE:sy:Ksp", "CE:sy:conc"],
    "CE:fo:nernst": ["CE:sy:F", "TH:sy:R", "TH:sy:T"],
    "CE:fo:arrhenius": ["CE:sy:k", "CE:sy:Ea", "TH:sy:T", "MA:sy:e"],
}
for fo, syms in SYM.items():
    for s in syms:
        edges.append(sc.e(fo, s, "has_symbol", "formula_symbol"))

# 物理量 -> 符号（2026-10-09 连通性审计补齐）：CE:sy:c（物质的量浓度）原为孤立节点
edges.append(sc.e("CE:pq:concentration", "CE:sy:c", "has_symbol", "quantity_symbol"))

# 公式 -> 物理量（defines）
DEF = [
    ("CE:fo:mass_action", "CE:pq:equilibrium_constant"),
    ("CE:fo:reaction_quotient", "CE:pq:reaction_quotient"),
    ("CE:fo:gibbs_standard", "CE:pq:gibbs_std"),
    ("CE:fo:gibbs_reaction", "CE:pq:gibbs_energy"),
    ("CE:fo:ph", "CE:pq:ph"),
    ("CE:fo:ka", "CE:pq:ka"),
    ("CE:fo:henderson", "CE:pq:pka"),
    ("CE:fo:ksp", "CE:pq:ksp"),
    ("CE:fo:nernst", "CE:pq:cell_potential"),
    ("CE:fo:arrhenius", "CE:pq:rate_constant"),
]
for fo, pq in DEF:
    edges.append(sc.e(fo, pq, "defines", "formula_quantity"))

# 物理量 -> 单位（含跨切片单位）
for pq, un in [
    ("CE:pq:concentration", "CE:un:molar"),
    ("CE:pq:gibbs_energy", "TH:un:joule"),
    ("CE:pq:gibbs_std", "TH:un:joule"),
    ("CE:pq:activation_energy", "TH:un:joule"),
]:
    edges.append(sc.e(pq, un, "has_unit", "quantity_unit"))

# --------------------------------------------------------------------------
# 跨学科桥（本切片的重点）
# --------------------------------------------------------------------------
edges += [
    # 化学 ↔ 热力学：ΔG° = −RT lnK 是热力学量 R/T 的直接使用
    sc.e("CE:fo:gibbs_standard", "TH:pq:gas_const", "derived_from", "thermo_gas_const",
         note="ΔG°=−RT lnK 使用摩尔气体常数 R"),
    sc.e("CE:fo:gibbs_standard", "TH:pq:entropy", "derived_from", "gibbs_from_entropy",
         note="G=H−TS：吉布斯自由能源于熵/焓"),
    sc.e("CE:pq:gibbs_energy", "PQ:energy", "same_as", "gibbs_is_energy",
         alignment="manual_curation", note="吉布斯自由能是一种能量，锚到物理能量量"),
    # 化学 ↔ 物理量库（PhysicsBabel 真实数据侧）
    sc.e("CE:pq:concentration", "PB:pq:concentration", "same_as", "chem_to_physicsbabel",
         alignment="manual_curation", note="化学浓度与 PhysicsBabel 的 concentration 同义"),
    sc.e("CE:pq:concentration", "TH:pq:n_moles", "derived_from", "conc_from_moles",
         note="c=n/V：浓度由物质的量与体积定义"),
    sc.e("CE:pq:concentration", "TH:pq:volume", "derived_from", "conc_from_volume"),
    # 化学 ↔ 数学（对数 / 指数）
    sc.e("CE:fo:ph", "MA:fo:log_product", "derived_from", "log_definition",
         note="pH/pOH 的 p 函数即 −log₁₀，建立在对数运算之上"),
    sc.e("CE:fo:henderson", "MA:fo:log_product", "derived_from", "log_definition_buffer"),
    sc.e("CE:fo:arrhenius", "MA:pq:euler_e", "derived_from", "exp_base_e",
         note="Arrhenius 的指数项以自然常数 e 为底"),
    # 化学 ↔ 电化学
    sc.e("CE:fo:nernst", "EM:pq:voltage", "derived_from", "nernst_potential",
         note="Nernst 方程给出电池电动势（电位）"),
    # 去重：与无机切片已有的 pH 定义同义
    sc.e("CE:fo:ph", "IC:fo:ph", "same_as", "same_concept",
         alignment="manual_curation", note="两切片均定义 pH，此处显式归一为同一概念"),
    sc.e("CE:fo:le_chatelier", "CE:fo:mass_action", "related_to", "principle_of_equilibrium"),
    # 反应 -> 支配它的公式
    sc.e("CE:rx:haber", "CE:fo:mass_action", "derived_from", "equilibrium_reaction"),
    sc.e("CE:rx:haber", "CE:fo:le_chatelier", "derived_from", "le_chatelier_haber"),
    sc.e("CE:rx:water_autoionization", "CE:fo:kw", "derived_from", "kw_from_autoionization"),
]

# --------------------------------------------------------------------------
# 元素层桥（哈伯法 + 氨的组成）
# --------------------------------------------------------------------------
edges += [
    sc.e("EK:el:N", "CE:rx:haber", "reactant_of", "nitrogen_reactant"),
    sc.e("EK:el:H", "CE:rx:haber", "reactant_of", "hydrogen_reactant"),
    sc.e("CE:mo:nh3", "CE:rx:haber", "product_of", "ammonia_product"),
    sc.e("CE:mo:nh3", "EK:el:N", "composed_of", "molecule_element", count=1),
    sc.e("CE:mo:nh3", "EK:el:H", "composed_of", "molecule_element", count=3),
    sc.e("MO:h2o", "CE:rx:water_autoionization", "reactant_of", "water_reactant"),
]

# 量纲一致
for a, b, note in [
    ("CE:pq:gibbs_energy", "CE:pq:activation_energy", "同能量量纲 M*L^2*T^-2"),
    ("CE:pq:gibbs_energy", "PQ:energy", "吉布斯自由能与能量同量纲"),
    ("CE:pq:equilibrium_constant", "CE:pq:reaction_quotient", "K 与 Q 同形同量纲"),
]:
    edges.append(sc.e(a, b, "dimensionally_consistent", "derived", note=note))

# 本切片显式引用的外部（跨切片）节点
EXTERNAL = {
    "TH:sy:R", "TH:sy:T", "TH:pq:gas_const", "TH:pq:entropy", "TH:pq:n_moles",
    "TH:pq:volume", "TH:un:joule",
    "MA:sy:e", "MA:pq:euler_e", "MA:fo:log_product",
    "EM:pq:voltage",
    "IC:fo:ph",
    "PB:pq:concentration",
    "PQ:energy",
    "MO:h2o",
    "EK:el:N", "EK:el:H",
}

if __name__ == "__main__":
    sc.build_and_write(OUT, "7h", "Chemical equilibrium vertical slice", nodes, edges,
                       extra_external=EXTERNAL)
