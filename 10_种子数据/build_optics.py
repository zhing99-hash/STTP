# -*- coding: utf-8 -*-
"""Phase 7l — 光学垂直切片（桥接相对论切片 RT 的光速）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_common as sc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "seed_optics.json")
nodes, edges = [], []

nodes += [
    sc.n("OP:fo:snell", "formula", "Snell's law", "physics.optics", latex=r"n_1\sin\theta_1=n_2\sin\theta_2"),
    sc.n("OP:fo:lens", "formula", "Thin lens equation", "physics.optics", latex=r"\frac{1}{f}=\frac{1}{u}+\frac{1}{v}"),
    sc.n("OP:fo:mirror", "formula", "Mirror equation", "physics.optics", latex=r"\frac{1}{f}=\frac{1}{d_o}+\frac{1}{d_i}"),
    sc.n("OP:fo:refr_speed", "formula", "Speed of light in medium", "physics.optics", latex=r"v=\frac{c}{n}"),
]
nodes += [
    sc.n("OP:pq:refractive_index", "physical_quantity", "Refractive index", "phys.quantity", symbol="n", dimension="dimensionless"),
    sc.n("OP:pq:focal_length", "physical_quantity", "Focal length", "phys.quantity", symbol="f", dimension="L"),
    sc.n("OP:pq:object_dist", "physical_quantity", "Object distance", "phys.quantity", symbol="u", dimension="L"),
    sc.n("OP:pq:image_dist", "physical_quantity", "Image distance", "phys.quantity", symbol="v", dimension="L"),
]
nodes += [
    sc.n("OP:un:meter", "unit", "meter", "phys.unit", symbol="m", dimension="L", si_base=True),
]
nodes += [
    sc.n("OP:sy:n", "symbol", "n (refractive index)", "phys.symbol", latex="n"),
    sc.n("OP:sy:f", "symbol", "f (focal length)", "phys.symbol", latex="f"),
    sc.n("OP:sy:u", "symbol", "u (object distance)", "phys.symbol", latex="u"),
    sc.n("OP:sy:v", "symbol", "v (image distance)", "phys.symbol", latex="v"),
    sc.n("OP:sy:theta", "symbol", "θ (angle)", "phys.symbol", latex=r"\theta"),
    sc.n("OP:sy:c", "symbol", "c (speed of light)", "phys.symbol", latex="c"),
]
for fo, syms in [
    ("OP:fo:snell", ["OP:sy:n", "OP:sy:theta"]),
    ("OP:fo:lens", ["OP:sy:f", "OP:sy:u", "OP:sy:v"]),
    ("OP:fo:mirror", ["OP:sy:f", "OP:sy:u", "OP:sy:v"]),
    ("OP:fo:refr_speed", ["OP:sy:c", "OP:sy:n"]),
]:
    for s in syms:
        edges.append(sc.e(fo, s, "has_symbol", "formula_symbol"))
for fo, pqs in [
    ("OP:fo:snell", ["OP:pq:refractive_index"]),
    ("OP:fo:lens", ["OP:pq:focal_length", "OP:pq:object_dist", "OP:pq:image_dist"]),
    ("OP:fo:mirror", ["OP:pq:focal_length", "OP:pq:object_dist", "OP:pq:image_dist"]),
    ("OP:fo:refr_speed", ["OP:pq:refractive_index"]),
]:
    for p in pqs:
        edges.append(sc.e(fo, p, "defines", "formula_quantity"))
for pq, un in [("OP:pq:focal_length", "OP:un:meter"), ("OP:pq:object_dist", "OP:un:meter"), ("OP:pq:image_dist", "OP:un:meter")]:
    edges.append(sc.e(pq, un, "has_unit", "quantity_unit"))
# 桥接相对论切片（RT）与基础库米
edges += [
    sc.e("OP:un:meter", "UN:m", "same_as", "seed_to_existing", alignment="manual_curation"),
    sc.e("OP:sy:c", "RT:sy:c", "same_as", "seed_to_existing", alignment="manual_curation"),
    sc.e("OP:fo:refr_speed", "RT:pq:light_speed", "derived_from", "speed_in_medium"),
]
sc.build_and_write(OUT, "7l", "Optics vertical slice", nodes, edges,
    extra_external={"RT:sy:c", "RT:pq:light_speed", "UN:m"})
