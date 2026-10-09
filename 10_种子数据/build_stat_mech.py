# -*- coding: utf-8 -*-
"""Phase 7k — 统计力学垂直切片（桥接热力学切片 TH）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_common as sc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "seed_stat_mech.json")
nodes, edges = [], []

nodes += [
    sc.n("SM:fo:boltzmann", "formula", "Boltzmann entropy", "physics.statmech", latex=r"S=k_B\ln W", informal="Entropy equals Boltzmann constant times log of microstates."),
    sc.n("SM:fo:maxwell", "formula", "Maxwell-Boltzmann distribution", "physics.statmech", latex=r"f(v)\propto v^2 e^{-mv^2/(2k_B T)}"),
    sc.n("SM:fo:partition", "formula", "Partition function", "physics.statmech", latex=r"Z=\sum_i e^{-E_i/(k_B T)}"),
    sc.n("SM:fo:ideal_gas_kin", "formula", "Ideal gas (kinetic theory)", "physics.statmech", latex=r"PV=\tfrac{2}{3}N\langle E_k\rangle"),
]
nodes += [
    sc.n("SM:pq:boltz_const", "constant", "Boltzmann constant", "phys.constant", symbol="k_B", value=1.380649e-23, dimension="M*L^2*T^-2*K^-1"),
    sc.n("SM:pq:microstates", "physical_quantity", "Number of microstates", "phys.quantity", symbol="W", dimension="dimensionless"),
    sc.n("SM:pq:partition_fn", "physical_quantity", "Partition function", "phys.quantity", symbol="Z", dimension="dimensionless"),
    sc.n("SM:pq:stat_entropy", "physical_quantity", "Statistical entropy", "phys.quantity", symbol="S", dimension="M*L^2*T^-2*K^-1"),
]
nodes += [
    sc.n("SM:un:j_per_k", "unit", "joule per kelvin", "phys.unit", symbol="J/K", dimension="M*L^2*T^-2*K^-1", derived_from="J/K"),
]
nodes += [
    sc.n("SM:sy:k_B", "symbol", "k_B (Boltzmann constant)", "phys.symbol", latex="k_B"),
    sc.n("SM:sy:W", "symbol", "W (microstates)", "phys.symbol", latex="W"),
    sc.n("SM:sy:Z", "symbol", "Z (partition)", "phys.symbol", latex="Z"),
    sc.n("SM:sy:T", "symbol", "T (temperature)", "phys.symbol", latex="T"),
    sc.n("SM:sy:S", "symbol", "S (entropy)", "phys.symbol", latex="S"),
]
for fo, syms in [
    ("SM:fo:boltzmann", ["SM:sy:k_B", "SM:sy:W", "SM:sy:S"]),
    ("SM:fo:maxwell", ["SM:sy:k_B", "SY:v"]),
    ("SM:fo:partition", ["SM:sy:Z", "SM:sy:k_B", "SM:sy:T"]),
    ("SM:fo:ideal_gas_kin", ["SY:v"]),
]:
    for s in syms:
        edges.append(sc.e(fo, s, "has_symbol", "formula_symbol"))
for fo, pqs in [
    ("SM:fo:boltzmann", ["SM:pq:stat_entropy", "SM:pq:microstates", "SM:pq:boltz_const"]),
    ("SM:fo:partition", ["SM:pq:partition_fn"]),
    ("SM:fo:maxwell", ["SM:pq:boltz_const"]),
]:
    for p in pqs:
        edges.append(sc.e(fo, p, "defines", "formula_quantity"))
for pq, un in [("SM:pq:stat_entropy", "SM:un:j_per_k"), ("SM:pq:boltz_const", "SM:un:j_per_k")]:
    edges.append(sc.e(pq, un, "has_unit", "quantity_unit"))
# 桥接热力学切片（TH）
edges += [
    sc.e("SM:pq:stat_entropy", "TH:pq:entropy", "same_as", "seed_to_existing", alignment="manual_curation"),
    sc.e("SM:un:j_per_k", "TH:un:j_per_k", "same_as", "seed_to_existing", alignment="manual_curation"),
    sc.e("SM:fo:boltzmann", "TH:fo:ideal_gas", "derived_from", "stat_to_thermo"),
]
sc.build_and_write(OUT, "7k", "Statistical mechanics vertical slice", nodes, edges,
    extra_external={"TH:pq:entropy", "TH:un:j_per_k", "TH:fo:ideal_gas"},
    pint_checks=lambda u: [
        ("mv^2/(kT) dimensionless", (u.kg * (u.m / u.s) ** 2 / ((u.J / u.K) * u.K)).dimensionality == (u.dimensionless).dimensionality),
    ])
