# -*- coding: utf-8 -*-
"""Phase 7j — 数学·几何垂直切片。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_common as sc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "seed_math_geometry.json")
nodes, edges = [], []

nodes += [
    sc.n("MG:fo:law_cosines", "formula", "Law of cosines", "math.geometry", latex=r"c^2=a^2+b^2-2ab\cos C"),
    sc.n("MG:fo:law_sines", "formula", "Law of sines", "math.geometry", latex=r"\frac{a}{\sin A}=\frac{b}{\sin B}=\frac{c}{\sin C}"),
    sc.n("MG:fo:circle_area", "formula", "Circle area", "math.geometry", latex=r"A=\pi r^2"),
    sc.n("MG:fo:sphere_vol", "formula", "Sphere volume", "math.geometry", latex=r"V=\tfrac{4}{3}\pi r^3"),
    sc.n("MG:fo:euler_poly", "formula", "Euler's polyhedron formula", "math.geometry", latex=r"V-E+F=2"),
]
nodes += [
    sc.n("MG:pq:area", "physical_quantity", "Area", "math.quantity", symbol="A", dimension="L^2"),
    sc.n("MG:pq:volume", "physical_quantity", "Volume", "math.quantity", symbol="V", dimension="L^3"),
    sc.n("MG:pq:angle", "physical_quantity", "Angle", "math.quantity", symbol="theta", dimension="dimensionless"),
    sc.n("MG:pq:radius", "physical_quantity", "Radius", "math.quantity", symbol="r", dimension="L"),
]
nodes += [
    sc.n("MG:un:sq_meter", "unit", "square meter", "math.unit", symbol="m^2", dimension="L^2"),
    sc.n("MG:un:cubic_meter", "unit", "cubic meter", "math.unit", symbol="m^3", dimension="L^3"),
    sc.n("MG:un:radian", "unit", "radian", "math.unit", symbol="rad", dimension="dimensionless"),
]
nodes += [
    sc.n("MG:sy:A", "symbol", "A (area)", "math.symbol", latex="A"),
    sc.n("MG:sy:V", "symbol", "V (volume)", "math.symbol", latex="V"),
    sc.n("MG:sy:r", "symbol", "r (radius)", "math.symbol", latex="r"),
    sc.n("MG:sy:theta", "symbol", "θ (angle)", "math.symbol", latex=r"\theta"),
    sc.n("MG:sy:pi", "symbol", "π (pi)", "math.symbol", latex=r"\pi"),
]
for fo, syms in [
    ("MG:fo:law_cosines", ["MG:sy:theta"]),
    ("MG:fo:law_sines", ["MG:sy:theta"]),
    ("MG:fo:circle_area", ["MG:sy:pi", "MG:sy:r"]),
    ("MG:fo:sphere_vol", ["MG:sy:pi", "MG:sy:r"]),
    ("MG:fo:euler_poly", ["MG:sy:V"]),
]:
    for s in syms:
        edges.append(sc.e(fo, s, "has_symbol", "formula_symbol"))
for fo, pqs in [
    ("MG:fo:law_cosines", ["MG:pq:angle"]),
    ("MG:fo:circle_area", ["MG:pq:area", "MG:pq:radius"]),
    ("MG:fo:sphere_vol", ["MG:pq:volume", "MG:pq:radius"]),
    ("MG:fo:euler_poly", ["MG:pq:volume"]),
]:
    for p in pqs:
        edges.append(sc.e(fo, p, "defines", "formula_quantity"))
for pq, un in [("MG:pq:area", "MG:un:sq_meter"), ("MG:pq:volume", "MG:un:cubic_meter"), ("MG:pq:angle", "MG:un:radian")]:
    edges.append(sc.e(pq, un, "has_unit", "quantity_unit"))
edges.append(sc.e("MG:sy:pi", "MX:sym:pi", "same_as", "seed_to_existing", alignment="manual_curation"))
sc.build_and_write(OUT, "7j", "Math: geometry vertical slice", nodes, edges,
    extra_external={"MX:sym:pi"})
