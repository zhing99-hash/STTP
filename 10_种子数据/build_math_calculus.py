# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Phase 7i — 数学·微积分垂直切片。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_common as sc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "seed_math_calculus.json")
nodes, edges = [], []

nodes += [
    sc.n("MC:fo:power_rule", "formula", "Power rule", "math.calculus", latex=r"\frac{d}{dx}x^n=nx^{n-1}"),
    sc.n("MC:fo:ftc", "formula", "Fundamental theorem of calculus", "math.calculus", latex=r"\int_a^b f'(x)\,dx=f(b)-f(a)"),
    sc.n("MC:fo:chain_rule", "formula", "Chain rule", "math.calculus", latex=r"\frac{d}{dx}f(g(x))=f'(g(x))\,g'(x)"),
    sc.n("MC:fo:taylor", "formula", "Taylor series", "math.calculus", latex=r"f(x)=\sum_{n=0}^\infty \frac{f^{(n)}(a)}{n!}(x-a)^n"),
    sc.n("MC:fo:limit_def", "formula", "Derivative as a limit", "math.calculus", latex=r"f'(x)=\lim_{h\to0}\frac{f(x+h)-f(x)}{h}"),
]
nodes += [
    sc.n("MC:pq:derivative", "physical_quantity", "Derivative", "math.quantity", symbol="f'"),
    sc.n("MC:pq:integral", "physical_quantity", "Integral", "math.quantity", symbol=r"\int f"),
    sc.n("MC:pq:limit", "physical_quantity", "Limit", "math.quantity", symbol=r"\lim"),
    sc.n("MC:pq:order", "physical_quantity", "Order of derivative", "math.quantity", symbol="n"),
]
nodes += [
    sc.n("MC:sy:f", "symbol", "f (function)", "math.symbol", latex="f"),
    sc.n("MC:sy:g", "symbol", "g (function)", "math.symbol", latex="g"),
    sc.n("MC:sy:n", "symbol", "n (order)", "math.symbol", latex="n"),
    sc.n("MC:sy:dx", "symbol", "dx (differential)", "math.symbol", latex="dx"),
]
for fo, syms in [
    ("MC:fo:power_rule", ["MC:sy:n"]),
    ("MC:fo:ftc", ["MC:sy:f", "MC:sy:dx"]),
    ("MC:fo:chain_rule", ["MC:sy:f", "MC:sy:g"]),
    ("MC:fo:taylor", ["MC:sy:f", "MC:sy:n"]),
    ("MC:fo:limit_def", ["MC:sy:f"]),
]:
    for s in syms:
        edges.append(sc.e(fo, s, "has_symbol", "formula_symbol"))

# 物理量 -> 符号（2026-10-09 连通性审计补齐）：MC:pq:order（导数的阶）原为孤立节点
edges.append(sc.e("MC:pq:order", "MC:sy:n", "has_symbol", "quantity_symbol"))
for fo, pqs in [
    ("MC:fo:power_rule", ["MC:pq:derivative"]),
    ("MC:fo:ftc", ["MC:pq:integral", "MC:pq:derivative"]),
    ("MC:fo:chain_rule", ["MC:pq:derivative"]),
    ("MC:fo:taylor", ["MC:pq:derivative"]),
    ("MC:fo:limit_def", ["MC:pq:limit", "MC:pq:derivative"]),
]:
    for p in pqs:
        edges.append(sc.e(fo, p, "defines", "formula_quantity"))

# 桥接到 Aura 已有的 LLM 假设层（2026-10-09 连通性审计补齐）
# 审计发现 ``MX:math:derivative_power`` / ``MX:math:power_rule`` / ``WD:Q1190543``
# 构成一个 3 节点的**孤岛**（GNN 未对其提出候选边，故没有任何入边）。
# 此处按 ``build_math_algebra`` 里 ``MA:fo:pythagoras ↔ MX:math:pythagorean_identity``
# 的既有范式，用人工策划的 same_as 把「幂法则」接回主图。
edges.append(sc.e("MC:fo:power_rule", "MX:math:power_rule", "same_as", "seed_to_existing",
                  alignment="manual_curation"))
# 2026-10-09 追加（老板裁定）：``MX:math:derivative_power`` 名为 ``d/dx(xⁿ)``，是同一幂法则的
# **左侧表达式概念**；``MX:math:power_rule`` 名为 ``n·xⁿ⁻¹``，是**右侧结果概念**。二者同属
# LLM 假设层的两半（已由 ``proves`` 相连），此处各自与人工策划的 ``MC:fo:power_rule`` 建
# ``same_as``，构成完整三角对齐。
edges.append(sc.e("MC:fo:power_rule", "MX:math:derivative_power", "same_as", "seed_to_existing",
                  alignment="manual_curation",
                  alignment_note="同一幂法则的左侧表达式概念 d/dx(xⁿ)，与右半 MX:math:power_rule 成对"))
sc.build_and_write(OUT, "7i", "Math: calculus vertical slice", nodes, edges,
                   extra_external={"MX:math:power_rule", "MX:math:derivative_power"})
