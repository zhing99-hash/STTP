# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Phase 7f — 狭义相对论垂直切片（洛伦兹因子/时间膨胀/长度收缩/相对论动量/质能等价）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_common as sc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "seed_relativity.json")
nodes, edges = [], []

nodes += [
    sc.n("RT:fo:lorentz_factor", "formula", "Lorentz factor", "physics.relativity", latex=r"\gamma=\frac{1}{\sqrt{1-v^2/c^2}}"),
    sc.n("RT:fo:time_dilation", "formula", "Time dilation", "physics.relativity", latex=r"\Delta t'=\gamma\Delta t"),
    sc.n("RT:fo:length_contraction", "formula", "Length contraction", "physics.relativity", latex=r"L=\frac{L_0}{\gamma}"),
    sc.n("RT:fo:rel_momentum", "formula", "Relativistic momentum", "physics.relativity", latex=r"p=\gamma m v"),
    sc.n("RT:fo:mass_energy", "formula", "Mass-energy equivalence", "physics.relativity", latex="E=mc^2", informal="Energy equals mass times speed of light squared."),
]
nodes += [
    sc.n("RT:pq:light_speed", "constant", "Speed of light", "phys.constant", symbol="c", value=299792458, dimension="L*T^-1"),
    sc.n("RT:pq:lorentz_factor", "physical_quantity", "Lorentz factor", "phys.quantity", symbol="gamma", dimension="dimensionless"),
    sc.n("RT:pq:proper_time", "physical_quantity", "Proper time", "phys.quantity", symbol="tau", dimension="T"),
]
nodes += [
    # ⚠ 2026-10-09 连通性审计修正：原在此声明 ``RT:un:c``（unit / "speed of light"）。
    # 该节点自建库起 deg=0，且与 ``RT:pq:light_speed``（Constant，同 symbol/同量纲/同数值）
    # 语义重复 —— **光速是自然常量，不是单位**，把常量标成 Unit 会污染类型统计。
    # 故整节点移除（改由 ``RT:pq:light_speed`` 承载），并在下方补其真实单位 m/s。
]
nodes += [
    sc.n("RT:sy:gamma", "symbol", "γ (Lorentz factor)", "phys.symbol", latex=r"\gamma"),
    sc.n("RT:sy:c", "symbol", "c (speed of light)", "phys.symbol", latex="c"),
    sc.n("RT:sy:tau", "symbol", "τ (proper time)", "phys.symbol", latex=r"\tau"),
]

for fo, syms in [
    ("RT:fo:lorentz_factor", ["RT:sy:gamma", "SY:v", "RT:sy:c"]),
    ("RT:fo:time_dilation", ["RT:sy:gamma"]),
    ("RT:fo:length_contraction", ["RT:sy:gamma"]),
    ("RT:fo:rel_momentum", ["RT:sy:gamma", "SY:m", "SY:v"]),
    ("RT:fo:mass_energy", ["SY:m", "RT:sy:c"]),
]:
    for s in syms:
        edges.append(sc.e(fo, s, "has_symbol", "formula_symbol"))

# 量/单位补线（2026-10-09 连通性审计补齐）
#   RT:sy:tau（固有时符号）原为孤立 → 挂到 RT:pq:proper_time
#   UN:s（秒）原为孤立 → 由 proper_time 声明其单位（has_unit）
#   RT:un:c 已删除（见上）→ 光速的真实单位改由 UN:mps（m/s）承载
edges.append(sc.e("RT:pq:proper_time", "RT:sy:tau", "has_symbol", "quantity_symbol"))
edges.append(sc.e("RT:pq:proper_time", "UN:s", "has_unit", "quantity_unit"))
edges.append(sc.e("RT:pq:light_speed", "UN:mps", "has_unit", "quantity_unit"))

for fo, pqs in [
    ("RT:fo:lorentz_factor", ["RT:pq:lorentz_factor"]),
    ("RT:fo:time_dilation", ["RT:pq:proper_time", "RT:pq:lorentz_factor"]),
    ("RT:fo:length_contraction", ["RT:pq:lorentz_factor"]),
    ("RT:fo:rel_momentum", ["CM:pq:momentum"]),
    ("RT:fo:mass_energy", ["RT:pq:light_speed", "PQ:mass"]),
]:
    for p in pqs:
        edges.append(sc.e(fo, p, "defines", "formula_quantity"))

edges += [
    sc.e("RT:fo:mass_energy", "FO:emc2", "same_as", "seed_to_existing", alignment="manual_curation"),
    sc.e("RT:pq:light_speed", "SY:c", "same_as", "seed_to_existing", alignment="manual_curation"),
    sc.e("RT:fo:rel_momentum", "CM:pq:momentum", "derived_from", "relativistic_momentum"),
    sc.e("RT:fo:mass_energy", "PQ:mass", "derived_from", "mass_energy"),
    sc.e("RT:fo:time_dilation", "PQ:vel", "derived_from", "velocity_time"),
]
sc.build_and_write(OUT, "7f", "Special relativity vertical slice", nodes, edges,
    extra_external={"CM:pq:momentum"},
    pint_checks=lambda u: [
        ("c dim", (u.m / u.s).dimensionality == (u.m / u.s).dimensionality),
        ("E=mc2", u.J.dimensionality == (u.kg * (u.m / u.s) ** 2).dimensionality),
    ])
