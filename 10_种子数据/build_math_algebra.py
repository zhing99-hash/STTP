# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Phase 7h — 数学·代数与数论垂直切片。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_common as sc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "seed_math_algebra.json")
nodes, edges = [], []

nodes += [
    sc.n("MA:fo:quadratic", "formula", "Quadratic formula", "math.algebra", latex=r"x=\frac{-b\pm\sqrt{b^2-4ac}}{2a}"),
    sc.n("MA:fo:binomial", "formula", "Binomial theorem", "math.algebra", latex=r"(a+b)^n=\sum_{k=0}^{n}\binom{n}{k}a^{n-k}b^k"),
    sc.n("MA:fo:euler_identity", "formula", "Euler's identity", "math.analysis", latex=r"e^{i\pi}+1=0", informal="Links e, i, and pi."),
    sc.n("MA:fo:pythagoras", "formula", "Pythagorean theorem", "math.geometry", latex=r"a^2+b^2=c^2"),
    sc.n("MA:fo:log_product", "formula", "Logarithm of a product", "math.algebra", latex=r"\log(xy)=\log x+\log y"),
]
nodes += [
    sc.n("MA:pq:discriminant", "physical_quantity", "Discriminant", "math.quantity", symbol="Delta"),
    sc.n("MA:pq:roots", "physical_quantity", "Roots of polynomial", "math.quantity", symbol="x"),
    sc.n("MA:pq:binomial_coeff", "physical_quantity", "Binomial coefficient", "math.quantity", symbol=r"\binom{n}{k}"),
    sc.n("MA:pq:euler_e", "constant", "Euler's number", "math.constant", symbol="e", value=2.718281828459045, dimension="dimensionless"),
]
nodes += [
    sc.n("MA:sy:e", "symbol", "e (Euler's number)", "math.symbol", latex="e"),
    sc.n("MA:sy:i", "symbol", "i (imaginary unit)", "math.symbol", latex="i"),
    sc.n("MA:sy:pi", "symbol", "π (pi)", "math.symbol", latex=r"\pi"),
    sc.n("MA:sy:a", "symbol", "a (coefficient)", "math.symbol", latex="a"),
    sc.n("MA:sy:b", "symbol", "b (coefficient)", "math.symbol", latex="b"),
    sc.n("MA:sy:x", "symbol", "x (variable)", "math.symbol", latex="x"),
    sc.n("MA:sy:n", "symbol", "n (integer)", "math.symbol", latex="n"),
]
for fo, syms in [
    ("MA:fo:quadratic", ["MA:sy:a", "MA:sy:b", "MA:sy:x"]),
    ("MA:fo:binomial", ["MA:sy:a", "MA:sy:b", "MA:sy:n"]),
    ("MA:fo:euler_identity", ["MA:sy:e", "MA:sy:i", "MA:sy:pi"]),
    ("MA:fo:pythagoras", ["MA:sy:a", "MA:sy:b", "MA:sy:x"]),
    ("MA:fo:log_product", ["MA:sy:x"]),
]:
    for s in syms:
        edges.append(sc.e(fo, s, "has_symbol", "formula_symbol"))
for fo, pqs in [
    ("MA:fo:quadratic", ["MA:pq:discriminant", "MA:pq:roots"]),
    ("MA:fo:binomial", ["MA:pq:binomial_coeff"]),
    ("MA:fo:euler_identity", ["MA:pq:euler_e"]),
]:
    for p in pqs:
        edges.append(sc.e(fo, p, "defines", "formula_quantity"))
# 桥接基础库数学概念
edges += [
    sc.e("MA:sy:pi", "MX:sym:pi", "same_as", "seed_to_existing", alignment="manual_curation"),
    sc.e("MA:fo:pythagoras", "MX:math:pythagorean_identity", "same_as", "seed_to_existing", alignment="manual_curation"),
    sc.e("MA:fo:euler_identity", "MA:pq:euler_e", "derived_from", "e_base"),
]
sc.build_and_write(OUT, "7h", "Math: algebra & number theory vertical slice", nodes, edges,
    extra_external={"MX:sym:pi", "MX:math:pythagorean_identity"})
