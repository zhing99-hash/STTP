# -*- coding: utf-8 -*-
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
for fo, pqs in [
    ("MC:fo:power_rule", ["MC:pq:derivative"]),
    ("MC:fo:ftc", ["MC:pq:integral", "MC:pq:derivative"]),
    ("MC:fo:chain_rule", ["MC:pq:derivative"]),
    ("MC:fo:taylor", ["MC:pq:derivative"]),
    ("MC:fo:limit_def", ["MC:pq:limit", "MC:pq:derivative"]),
]:
    for p in pqs:
        edges.append(sc.e(fo, p, "defines", "formula_quantity"))
sc.build_and_write(OUT, "7i", "Math: calculus vertical slice", nodes, edges)
