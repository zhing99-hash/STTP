# -*- coding: utf-8 -*-
"""Phase 7c — 热力学与理想气体垂直切片（理想气体定律/热力学第一定律/气体定律/卡诺效率）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_common as sc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "seed_thermo.json")
nodes, edges = [], []

# 复用基础概念（镜像 + same_as）
mirror = [
    ("TH:pq:energy", "PQ:energy", "physical_quantity", "Energy", "phys.quantity", {"symbol": "E"}),
    ("TH:un:joule", "UN:j", "unit", "joule", "phys.unit", {"symbol": "J"}),
    ("TH:un:pascal", "CM:un:pascal", "unit", "pascal", "phys.unit", {"symbol": "Pa"}),
]
for nid, base, nt, name, dom, props in mirror:
    nodes.append(sc.n(nid, nt, name, dom, **props))
    edges.append(sc.e(nid, base, "same_as", "seed_to_existing", alignment="manual_curation"))

# 公式
nodes += [
    sc.n("TH:fo:ideal_gas", "formula", "Ideal gas law", "physics.thermo", latex="PV=nRT",
         informal="Pressure times volume equals moles times gas constant times temperature."),
    sc.n("TH:fo:first_law", "formula", "First law of thermodynamics", "physics.thermo",
         latex=r"\Delta U = Q - W", informal="Change in internal energy equals heat added minus work done."),
    sc.n("TH:fo:boyle", "formula", "Boyle's law", "physics.thermo", latex="P_1 V_1 = P_2 V_2"),
    sc.n("TH:fo:charles", "formula", "Charles's law", "physics.thermo", latex=r"\frac{V_1}{T_1}=\frac{V_2}{T_2}"),
    sc.n("TH:fo:gay_lussac", "formula", "Gay-Lussac's law", "physics.thermo", latex=r"\frac{P_1}{T_1}=\frac{P_2}{T_2}"),
    sc.n("TH:fo:carnot", "formula", "Carnot efficiency", "physics.thermo", latex=r"\eta = 1 - \frac{T_c}{T_h}",
         informal="Maximum possible efficiency of a heat engine."),
]
# 物理量
nodes += [
    sc.n("TH:pq:pressure", "physical_quantity", "Pressure", "phys.quantity", symbol="P", dimension="M*L^-1*T^-2"),
    sc.n("TH:pq:volume", "physical_quantity", "Volume", "phys.quantity", symbol="V", dimension="L^3"),
    sc.n("TH:pq:temperature", "physical_quantity", "Temperature", "phys.quantity", symbol="T", dimension="K"),
    sc.n("TH:pq:gas_const", "constant", "Gas constant", "phys.constant", symbol="R", value=8.314,
         dimension="M*L^2*T^-2*mol^-1*K^-1"),
    sc.n("TH:pq:internal_energy", "physical_quantity", "Internal energy", "phys.quantity", symbol="U", dimension="M*L^2*T^-2"),
    sc.n("TH:pq:heat", "physical_quantity", "Heat", "phys.quantity", symbol="Q", dimension="M*L^2*T^-2"),
    sc.n("TH:pq:n_moles", "physical_quantity", "Amount of substance", "phys.quantity", symbol="n", dimension="mol"),
    sc.n("TH:pq:entropy", "physical_quantity", "Entropy", "phys.quantity", symbol="S", dimension="M*L^2*T^-2*K^-1"),
]
# 单位
nodes += [
    sc.n("TH:un:kelvin", "unit", "kelvin", "phys.unit", symbol="K", dimension="K", si_base=True),
    sc.n("TH:un:cubic_meter", "unit", "cubic meter", "phys.unit", symbol="m^3", dimension="L^3"),
    sc.n("TH:un:mole", "unit", "mole", "phys.unit", symbol="mol", dimension="mol", si_base=True),
    sc.n("TH:un:j_per_k", "unit", "joule per kelvin", "phys.unit", symbol="J/K", dimension="M*L^2*T^-2*K^-1", derived_from="J/K"),
    sc.n("TH:un:j_per_mol_k", "unit", "joule per mole kelvin", "phys.unit", symbol="J/(mol*K)",
         dimension="M*L^2*T^-2*mol^-1*K^-1", derived_from="J/(mol*K)"),
]
# 符号
nodes += [
    sc.n("TH:sy:P", "symbol", "P (pressure)", "math.symbol", latex="P"),
    sc.n("TH:sy:V", "symbol", "V (volume)", "math.symbol", latex="V"),
    sc.n("TH:sy:T", "symbol", "T (temperature)", "math.symbol", latex="T"),
    sc.n("TH:sy:R", "symbol", "R (gas constant)", "math.symbol", latex="R"),
    sc.n("TH:sy:U", "symbol", "U (internal energy)", "math.symbol", latex="U"),
    sc.n("TH:sy:Q", "symbol", "Q (heat)", "math.symbol", latex="Q"),
    sc.n("TH:sy:n", "symbol", "n (amount)", "math.symbol", latex="n"),
    sc.n("TH:sy:S", "symbol", "S (entropy)", "math.symbol", latex="S"),
    sc.n("TH:sy:eta", "symbol", "η (efficiency)", "math.symbol", latex=r"\eta"),
]

# 公式 -> 符号
for fo, syms in [
    ("TH:fo:ideal_gas", ["TH:sy:P", "TH:sy:V", "TH:sy:n", "TH:sy:R", "TH:sy:T"]),
    ("TH:fo:first_law", ["TH:sy:U", "TH:sy:Q"]),
    ("TH:fo:boyle", ["TH:sy:P", "TH:sy:V"]),
    ("TH:fo:charles", ["TH:sy:V", "TH:sy:T"]),
    ("TH:fo:gay_lussac", ["TH:sy:P", "TH:sy:T"]),
    ("TH:fo:carnot", ["TH:sy:eta", "TH:sy:T"]),
]:
    for s in syms:
        edges.append(sc.e(fo, s, "has_symbol", "formula_symbol"))

# 公式 -> 物理量
for fo, pqs in [
    ("TH:fo:ideal_gas", ["TH:pq:pressure", "TH:pq:volume", "TH:pq:n_moles", "TH:pq:gas_const", "TH:pq:temperature"]),
    ("TH:fo:first_law", ["TH:pq:internal_energy", "TH:pq:heat"]),
    ("TH:fo:boyle", ["TH:pq:pressure", "TH:pq:volume"]),
    ("TH:fo:charles", ["TH:pq:volume", "TH:pq:temperature"]),
    ("TH:fo:gay_lussac", ["TH:pq:pressure", "TH:pq:temperature"]),
    ("TH:fo:carnot", ["TH:pq:temperature"]),
]:
    for p in pqs:
        edges.append(sc.e(fo, p, "defines", "formula_quantity"))

# 物理量 -> 单位
for pq, un in [
    ("TH:pq:pressure", "TH:un:pascal"), ("TH:pq:volume", "TH:un:cubic_meter"),
    ("TH:pq:temperature", "TH:un:kelvin"), ("TH:pq:gas_const", "TH:un:j_per_mol_k"),
    ("TH:pq:n_moles", "TH:un:mole"), ("TH:pq:internal_energy", "TH:un:joule"),
    ("TH:pq:heat", "TH:un:joule"), ("TH:pq:entropy", "TH:un:j_per_k"),
]:
    edges.append(sc.e(pq, un, "has_unit", "quantity_unit"))

# 量纲一致
for a, b, note in [
    ("TH:pq:internal_energy", "TH:pq:energy", "U is energy"),
    ("TH:pq:heat", "TH:pq:energy", "Q is energy"),
]:
    edges.append(sc.e(a, b, "dimensionally_consistent", "derived", note=note))

# 推导
edges += [
    sc.e("TH:fo:ideal_gas", "TH:pq:pressure", "derived_from", "P_from_PV"),
    sc.e("TH:fo:ideal_gas", "TH:pq:volume", "derived_from", "V_from_PV"),
    sc.e("TH:fo:first_law", "TH:pq:internal_energy", "derived_from", "U_from_first_law"),
    sc.e("TH:fo:carnot", "TH:pq:temperature", "derived_from", "eta_from_T"),
]

sc.build_and_write(OUT, "7c", "Thermodynamics & ideal gas vertical slice", nodes, edges,
    extra_external={"CM:un:pascal"},  # 跨切片引用力学切片的 pascal 单位节点
    pint_checks=lambda u: [
        ("PV=nRT", (u.Pa * u.m ** 3).dimensionality == (u.mol * (u.J / (u.mol * u.K)) * u.K).dimensionality),
        ("ΔU=Q−W", (u.kg * u.m ** 2 / u.s ** 2).dimensionality == (u.kg * u.m ** 2 / u.s ** 2).dimensionality),
        ("S dim", (u.kg * u.m ** 2 / (u.s ** 2 * u.K)).dimensionality == (u.J / u.K).dimensionality),
    ])
