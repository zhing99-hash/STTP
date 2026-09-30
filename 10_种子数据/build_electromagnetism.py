# -*- coding: utf-8 -*-
"""Phase 7d — 电磁学垂直切片（库仑/欧姆/洛伦兹/法拉第/高斯/毕奥-萨伐尔）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_common as sc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "seed_electromagnetism.json")
nodes, edges = [], []

nodes += [
    sc.n("EM:fo:coulomb", "formula", "Coulomb's law", "physics.em", latex=r"F=k\frac{q_1 q_2}{r^2}", informal="Force between two point charges."),
    sc.n("EM:fo:ohm", "formula", "Ohm's law", "physics.em", latex="V=IR", informal="Voltage equals current times resistance."),
    sc.n("EM:fo:lorentz", "formula", "Lorentz force", "physics.em", latex=r"\vec{F}=q(\vec{E}+\vec{v}\times\vec{B})", informal="Force on a charge in EM fields."),
    sc.n("EM:fo:faraday", "formula", "Faraday's law", "physics.em", latex=r"\mathcal{E}=-\frac{d\Phi}{dt}", informal="Induced emf equals minus rate of flux change."),
    sc.n("EM:fo:gauss", "formula", "Gauss's law", "physics.em", latex=r"\nabla\cdot\vec{E}=\frac{\rho}{\varepsilon_0}"),
    sc.n("EM:fo:biot_savart", "formula", "Magnetic field of a wire", "physics.em", latex=r"B=\frac{\mu_0 I}{2\pi r}"),
]
nodes += [
    sc.n("EM:pq:charge", "physical_quantity", "Electric charge", "phys.quantity", symbol="Q", dimension="I*T"),
    sc.n("EM:pq:current", "physical_quantity", "Electric current", "phys.quantity", symbol="I", dimension="I"),
    sc.n("EM:pq:voltage", "physical_quantity", "Voltage", "phys.quantity", symbol="V", dimension="M*L^2*T^-3*I^-1"),
    sc.n("EM:pq:efield", "physical_quantity", "Electric field", "phys.quantity", symbol="E", dimension="M*L*T^-3*I^-1"),
    sc.n("EM:pq:bfield", "physical_quantity", "Magnetic field", "phys.quantity", symbol="B", dimension="M*T^-2*I^-1"),
    sc.n("EM:pq:capacitance", "physical_quantity", "Capacitance", "phys.quantity", symbol="C", dimension="M^-1*L^-2*T^4*I^2"),
    sc.n("EM:pq:resistance", "physical_quantity", "Resistance", "phys.quantity", symbol="R", dimension="M*L^2*T^-3*I^-2"),
    sc.n("EM:pq:inductance", "physical_quantity", "Inductance", "phys.quantity", symbol="L", dimension="M*L^2*T^-2*I^-2"),
    sc.n("EM:pq:permittivity", "constant", "Vacuum permittivity", "phys.constant", symbol="eps0", value=8.854187817e-12, dimension="M^-1*L^-3*T^4*I^2"),
    sc.n("EM:pq:permeability", "constant", "Vacuum permeability", "phys.constant", symbol="mu0", value=1.25663706212e-6, dimension="M*L*T^-2*I^-2"),
    sc.n("EM:pq:coulomb_const", "constant", "Coulomb constant", "phys.constant", symbol="k", value=8.9875517923e9, dimension="M*L^3*T^-4*I^-2"),
    sc.n("EM:pq:emf", "physical_quantity", "Electromotive force", "phys.quantity", symbol="eps", dimension="M*L^2*T^-3*I^-1"),
    sc.n("EM:pq:mag_flux", "physical_quantity", "Magnetic flux", "phys.quantity", symbol="Phi", dimension="M*L^2*T^-2*I^-1"),
]
nodes += [
    sc.n("EM:un:coulomb", "unit", "coulomb", "phys.unit", symbol="C", dimension="I*T"),
    sc.n("EM:un:volt", "unit", "volt", "phys.unit", symbol="V", dimension="M*L^2*T^-3*I^-1"),
    sc.n("EM:un:ampere", "unit", "ampere", "phys.unit", symbol="A", dimension="I", si_base=True),
    sc.n("EM:un:ohm", "unit", "ohm", "phys.unit", symbol="Ω", dimension="M*L^2*T^-3*I^-2"),
    sc.n("EM:un:farad", "unit", "farad", "phys.unit", symbol="F", dimension="M^-1*L^-2*T^4*I^2"),
    sc.n("EM:un:tesla", "unit", "tesla", "phys.unit", symbol="T", dimension="M*T^-2*I^-1"),
    sc.n("EM:un:henry", "unit", "henry", "phys.unit", symbol="H", dimension="M*L^2*T^-2*I^-2"),
    sc.n("EM:un:weber", "unit", "weber", "phys.unit", symbol="Wb", dimension="M*L^2*T^-2*I^-1"),
]
nodes += [
    sc.n("EM:sy:Q", "symbol", "Q (charge)", "math.symbol", latex="Q"),
    sc.n("EM:sy:I", "symbol", "I (current)", "math.symbol", latex="I"),
    sc.n("EM:sy:V", "symbol", "V (voltage)", "math.symbol", latex="V"),
    sc.n("EM:sy:E", "symbol", "E (electric field)", "math.symbol", latex="E"),
    sc.n("EM:sy:B", "symbol", "B (magnetic field)", "math.symbol", latex="B"),
    sc.n("EM:sy:R", "symbol", "R (resistance)", "math.symbol", latex="R"),
    sc.n("EM:sy:L", "symbol", "L (inductance)", "math.symbol", latex="L"),
    sc.n("EM:sy:eps", "symbol", "ε (emf)", "math.symbol", latex=r"\varepsilon"),
    sc.n("EM:sy:Phi", "symbol", "Φ (flux)", "math.symbol", latex=r"\Phi"),
    sc.n("EM:sy:k", "symbol", "k (Coulomb constant)", "math.symbol", latex="k"),
    sc.n("EM:sy:eps0", "symbol", "ε₀ (permittivity)", "math.symbol", latex=r"\varepsilon_0"),
    sc.n("EM:sy:mu0", "symbol", "μ₀ (permeability)", "math.symbol", latex=r"\mu_0"),
]

for fo, syms in [
    ("EM:fo:coulomb", ["EM:sy:k", "EM:sy:Q"]),
    ("EM:fo:ohm", ["EM:sy:V", "EM:sy:I", "EM:sy:R"]),
    ("EM:fo:lorentz", ["SY:f", "EM:sy:Q", "EM:sy:E", "SY:v", "EM:sy:B"]),
    ("EM:fo:faraday", ["EM:sy:eps", "EM:sy:Phi"]),
    ("EM:fo:gauss", ["EM:sy:E", "EM:sy:eps0"]),
    ("EM:fo:biot_savart", ["EM:sy:B", "EM:sy:mu0", "EM:sy:I"]),
]:
    for s in syms:
        edges.append(sc.e(fo, s, "has_symbol", "formula_symbol"))

for fo, pqs in [
    ("EM:fo:coulomb", ["EM:pq:charge", "EM:pq:coulomb_const"]),
    ("EM:fo:ohm", ["EM:pq:voltage", "EM:pq:current", "EM:pq:resistance"]),
    ("EM:fo:lorentz", ["EM:pq:charge", "EM:pq:efield", "EM:pq:bfield"]),
    ("EM:fo:faraday", ["EM:pq:emf", "EM:pq:mag_flux"]),
    ("EM:fo:gauss", ["EM:pq:efield", "EM:pq:permittivity"]),
    ("EM:fo:biot_savart", ["EM:pq:bfield", "EM:pq:permeability", "EM:pq:current"]),
]:
    for p in pqs:
        edges.append(sc.e(fo, p, "defines", "formula_quantity"))

for pq, un in [
    ("EM:pq:charge", "EM:un:coulomb"), ("EM:pq:current", "EM:un:ampere"),
    ("EM:pq:voltage", "EM:un:volt"), ("EM:pq:efield", "EM:un:volt"),
    ("EM:pq:resistance", "EM:un:ohm"), ("EM:pq:capacitance", "EM:un:farad"),
    ("EM:pq:bfield", "EM:un:tesla"), ("EM:pq:inductance", "EM:un:henry"),
    ("EM:pq:mag_flux", "EM:un:weber"), ("EM:pq:emf", "EM:un:volt"),
]:
    edges.append(sc.e(pq, un, "has_unit", "quantity_unit"))

# 桥接基础库（force/velocity 为 Phase6/基础 canonical 概念）
edges += [
    sc.e("EM:fo:coulomb", "PQ:force", "derived_from", "force_from_charges"),
    sc.e("EM:fo:lorentz", "PQ:force", "derived_from", "force_from_fields"),
    sc.e("EM:fo:lorentz", "PQ:vel", "derived_from", "velocity_field"),
]

sc.build_and_write(OUT, "7d", "Electromagnetism vertical slice", nodes, edges,
    pint_checks=lambda u: [
        ("V=IR", u.V.dimensionality == (u.ohm * u.A).dimensionality),
        ("F=qE", u.N.dimensionality == (u.C * u.V / u.m).dimensionality),
    ])
