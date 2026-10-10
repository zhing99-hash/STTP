# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Phase 7a — 经典力学垂直切片（复用基础图谱中的 mass/force/accel/vel/energy 等节点，same_as 桥接）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_common as sc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "seed_classical_mechanics.json")
nodes, edges = [], []

# 复用基础概念（本切片镜像节点 + same_as 桥到 canonical）
mirror = [
    ("CM:pq:mass", "PQ:mass", "physical_quantity", "Mass", "phys.quantity", {"symbol": "m"}),
    ("CM:pq:force", "PQ:force", "physical_quantity", "Force", "phys.quantity", {"symbol": "F"}),
    ("CM:pq:accel", "PQ:accel", "physical_quantity", "Acceleration", "phys.quantity", {"symbol": "a"}),
    ("CM:pq:vel", "PQ:vel", "physical_quantity", "Velocity", "phys.quantity", {"symbol": "v"}),
    ("CM:pq:energy", "PQ:energy", "physical_quantity", "Energy", "phys.quantity", {"symbol": "E"}),
    ("CM:un:joule", "UN:j", "unit", "joule", "phys.unit", {"symbol": "J"}),
]
for nid, base, nt, name, dom, props in mirror:
    nodes.append(sc.n(nid, nt, name, dom, **props))
    edges.append(sc.e(nid, base, "same_as", "seed_to_existing", alignment="manual_curation"))

# 公式（新）
nodes += [
    sc.n("CM:fo:momentum", "formula", "Momentum", "physics.mechanics", latex="p=mv",
         informal="Momentum equals mass times velocity."),
    sc.n("CM:fo:work", "formula", "Work", "physics.mechanics", latex="W=Fd",
         informal="Work done by a constant force over a displacement."),
    sc.n("CM:fo:power", "formula", "Power", "physics.mechanics", latex=r"P=\frac{W}{t}",
         informal="Rate of doing work."),
    sc.n("CM:fo:gravity", "formula", "Newton's law of gravitation", "physics.gravitation",
         latex=r"F=G\frac{m_1 m_2}{r^2}", informal="Gravitational force between two masses."),
    sc.n("CM:fo:centripetal", "formula", "Centripetal force", "physics.mechanics",
         latex=r"F=\frac{mv^2}{r}", informal="Force required for uniform circular motion."),
    sc.n("CM:fo:impulse", "formula", "Impulse-momentum", "physics.mechanics",
         latex=r"J=F\Delta t=\Delta p", informal="Impulse equals change in momentum."),
]
# 物理量（新）
nodes += [
    sc.n("CM:pq:momentum", "physical_quantity", "Momentum", "phys.quantity", symbol="p", dimension="M*L*T^-1"),
    sc.n("CM:pq:work", "physical_quantity", "Work", "phys.quantity", symbol="W", dimension="M*L^2*T^-2"),
    sc.n("CM:pq:power", "physical_quantity", "Power", "phys.quantity", symbol="P", dimension="M*L^2*T^-3"),
    sc.n("CM:pq:grav_const", "constant", "Gravitational constant", "phys.constant", symbol="G",
         value=6.67430e-11, dimension="M^-1*L^3*T^-2"),
    sc.n("CM:pq:angular_momentum", "physical_quantity", "Angular momentum", "phys.quantity", symbol="L", dimension="M*L^2*T^-1"),
    sc.n("CM:pq:torque", "physical_quantity", "Torque", "phys.quantity", symbol="tau", dimension="M*L^2*T^-2"),
    sc.n("CM:pq:impulse", "physical_quantity", "Impulse", "phys.quantity", symbol="J", dimension="M*L*T^-1"),
]
# 单位（新）
nodes += [
    sc.n("CM:un:newton", "unit", "newton", "phys.unit", symbol="N", dimension="M*L*T^-2", derived_from="kg*m/s^2"),
    sc.n("CM:un:watt", "unit", "watt", "phys.unit", symbol="W", dimension="M*L^2*T^-3", derived_from="J/s"),
    sc.n("CM:un:pascal", "unit", "pascal", "phys.unit", symbol="Pa", dimension="M*L^-1*T^-2", derived_from="N/m^2"),
]
# 符号（新）
nodes += [
    sc.n("CM:sy:p", "symbol", "p (momentum)", "phys.symbol", latex="p"),
    sc.n("CM:sy:W", "symbol", "W (work)", "phys.symbol", latex="W"),
    sc.n("CM:sy:P", "symbol", "P (power)", "phys.symbol", latex="P"),
    sc.n("CM:sy:G", "symbol", "G (gravitational constant)", "phys.symbol", latex="G"),
    sc.n("CM:sy:L", "symbol", "L (angular momentum)", "phys.symbol", latex="L"),
    sc.n("CM:sy:tau", "symbol", "τ (torque)", "phys.symbol", latex=r"\tau"),
    sc.n("CM:sy:J", "symbol", "J (impulse)", "phys.symbol", latex="J"),
]

# 公式 -> 符号
for fo, syms in [
    ("CM:fo:momentum", ["CM:sy:p", "SY:m", "SY:v"]),
    ("CM:fo:work", ["CM:sy:W", "SY:f"]),
    ("CM:fo:power", ["CM:sy:P", "CM:sy:W"]),
    ("CM:fo:gravity", ["SY:f", "CM:sy:G", "SY:m"]),
    ("CM:fo:centripetal", ["SY:f", "SY:m", "SY:v"]),
    ("CM:fo:impulse", ["CM:sy:J", "SY:f"]),
]:
    for s in syms:
        edges.append(sc.e(fo, s, "has_symbol", "formula_symbol"))

# 物理量 -> 符号（2026-10-09 连通性审计补齐）
# 原实现只给「公式」挂符号，`CM:sy:L`（角动量）与 `CM:sy:tau`（力矩）声明后无人引用
# → 两个孤立节点（deg=0）。这里挂到对应物理量上（语义：该量的符号是 L / τ）。
for pq, s in [("CM:pq:angular_momentum", "CM:sy:L"), ("CM:pq:torque", "CM:sy:tau")]:
    edges.append(sc.e(pq, s, "has_symbol", "quantity_symbol"))

# 公式 -> 物理量
for fo, pqs in [
    ("CM:fo:momentum", ["CM:pq:momentum"]),
    ("CM:fo:work", ["CM:pq:work"]),
    ("CM:fo:power", ["CM:pq:power"]),
    ("CM:fo:gravity", ["CM:pq:grav_const"]),
    ("CM:fo:centripetal", ["CM:pq:torque"]),
    ("CM:fo:impulse", ["CM:pq:impulse"]),
]:
    for p in pqs:
        edges.append(sc.e(fo, p, "defines", "formula_quantity"))

# 物理量 -> 单位
for pq, un in [
    ("CM:pq:momentum", "CM:un:newton"), ("CM:pq:work", "CM:un:joule"),
    ("CM:pq:power", "CM:un:watt"), ("CM:pq:torque", "CM:un:joule"),
    ("CM:pq:impulse", "CM:un:newton"), ("CM:pq:angular_momentum", "CM:un:joule"),
]:
    edges.append(sc.e(pq, un, "has_unit", "quantity_unit"))

# 量纲一致（⚠ P0-1 修复 2026-10-10：原第 1 项 ("CM:pq:momentum","CM:pq:mass","p=mv")
# 是**笔误**——动量 M·L·T⁻¹ 与质量 M 量纲不同，不能建 dimensionally_consistent；
# "p=mv" 的语义已由下方 `CM:fo:momentum --derived_from--> PQ:mass/vel` 正确表达，故删除）
for a, b, note in [
    ("CM:pq:work", "CM:pq:torque", "work and torque share M*L^2*T^-2"),
    ("CM:pq:impulse", "CM:pq:momentum", "J=Δp"),
]:
    edges.append(sc.e(a, b, "dimensionally_consistent", "derived", note=note))

# 推导关系
edges += [
    sc.e("CM:fo:momentum", "PQ:mass", "derived_from", "mass_velocity", note="p=mv"),
    sc.e("CM:fo:momentum", "PQ:vel", "derived_from", "mass_velocity"),
    sc.e("CM:fo:work", "PQ:force", "derived_from", "force_displacement"),
    sc.e("CM:fo:power", "CM:pq:work", "derived_from", "power_from_work"),
    sc.e("CM:fo:gravity", "PQ:mass", "derived_from", "grav_mass"),
    sc.e("CM:fo:centripetal", "PQ:mass", "derived_from", "centripetal_mass"),
    sc.e("CM:fo:impulse", "PQ:force", "derived_from", "impulse_force"),
    sc.e("CM:pq:angular_momentum", "CM:pq:momentum", "derived_from", "r_cross_p"),
    sc.e("CM:pq:torque", "CM:pq:angular_momentum", "derived_from", "r_cross_F"),
    sc.e("CM:fo:work", "FO:ke", "derived_from", "work_energy"),
]

sc.build_and_write(OUT, "7a", "Classical mechanics vertical slice", nodes, edges,
    pint_checks=lambda u: [
        ("p=mv", (u.kg * u.m / u.s).dimensionality == (u.kg * (u.m / u.s)).dimensionality),
        ("W=Fd", (u.kg * u.m ** 2 / u.s ** 2).dimensionality == (u.kg * u.m / u.s ** 2 * u.m).dimensionality),
        ("P=W/t", (u.kg * u.m ** 2 / u.s ** 3).dimensionality == (u.kg * u.m ** 2 / u.s ** 2 / u.s).dimensionality),
        ("G dim", (u.m ** 3 / (u.kg * u.s ** 2)).dimensionality == ((u.kg * u.m / u.s ** 2) * u.m ** 2 / u.kg ** 2).dimensionality),
    ])
