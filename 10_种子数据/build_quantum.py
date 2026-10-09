# -*- coding: utf-8 -*-
"""Phase 7e — 量子力学垂直切片（普朗克/德布罗意/薛定谔/不确定性/光电效应）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_common as sc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "seed_quantum.json")
nodes, edges = [], []

nodes += [
    sc.n("QM:fo:planck", "formula", "Planck relation", "physics.qm", latex=r"E=h\nu", informal="Photon energy equals Planck constant times frequency."),
    sc.n("QM:fo:de_broglie", "formula", "de Broglie wavelength", "physics.qm", latex=r"\lambda=\frac{h}{p}", informal="Matter waves: wavelength inversely proportional to momentum."),
    sc.n("QM:fo:schrodinger", "formula", "Schrödinger equation (time-independent)", "physics.qm", latex=r"-\frac{\hbar^2}{2m}\nabla^2\psi + V\psi = E\psi"),
    sc.n("QM:fo:uncertainty", "formula", "Heisenberg uncertainty", "physics.qm", latex=r"\Delta x\,\Delta p \geq \frac{\hbar}{2}"),
    sc.n("QM:fo:photoelectric", "formula", "Photoelectric effect", "physics.qm", latex=r"E_{ph}=h\nu-\phi"),
]
nodes += [
    sc.n("QM:pq:planck_const", "constant", "Planck constant", "phys.constant", symbol="h", value=6.62607015e-34, dimension="M*L^2*T^-1"),
    sc.n("QM:pq:reduced_planck", "constant", "Reduced Planck constant", "phys.constant", symbol="hbar", value=1.054571817e-34, dimension="M*L^2*T^-1"),
    sc.n("QM:pq:frequency", "physical_quantity", "Frequency", "phys.quantity", symbol="nu", dimension="T^-1"),
    sc.n("QM:pq:wavelength", "physical_quantity", "Wavelength", "phys.quantity", symbol="lambda", dimension="L"),
    sc.n("QM:pq:work_function", "physical_quantity", "Work function", "phys.quantity", symbol="phi", dimension="M*L^2*T^-2"),
    sc.n("QM:pq:action", "physical_quantity", "Action", "phys.quantity", symbol="S", dimension="M*L^2*T^-1"),
]
nodes += [
    sc.n("QM:un:hertz", "unit", "hertz", "phys.unit", symbol="Hz", dimension="T^-1"),
    sc.n("QM:un:ev", "unit", "electronvolt", "phys.unit", symbol="eV", dimension="M*L^2*T^-2"),
]
nodes += [
    sc.n("QM:sy:h", "symbol", "h (Planck)", "phys.symbol", latex="h"),
    sc.n("QM:sy:hbar", "symbol", "ħ (reduced Planck)", "phys.symbol", latex=r"\hbar"),
    sc.n("QM:sy:nu", "symbol", "ν (frequency)", "phys.symbol", latex=r"\nu"),
    sc.n("QM:sy:lambda", "symbol", "λ (wavelength)", "phys.symbol", latex=r"\lambda"),
    sc.n("QM:sy:psi", "symbol", "ψ (wavefunction)", "phys.symbol", latex=r"\psi"),
    sc.n("QM:sy:phi", "symbol", "φ (work function)", "phys.symbol", latex=r"\phi"),
]

for fo, syms in [
    ("QM:fo:planck", ["QM:sy:h", "QM:sy:nu"]),
    ("QM:fo:de_broglie", ["QM:sy:lambda", "QM:sy:h"]),
    ("QM:fo:schrodinger", ["QM:sy:hbar", "SY:m", "QM:sy:psi"]),
    ("QM:fo:uncertainty", ["QM:sy:hbar"]),
    ("QM:fo:photoelectric", ["QM:sy:h", "QM:sy:nu", "QM:sy:phi"]),
]:
    for s in syms:
        edges.append(sc.e(fo, s, "has_symbol", "formula_symbol"))

for fo, pqs in [
    ("QM:fo:planck", ["QM:pq:planck_const", "QM:pq:frequency"]),
    ("QM:fo:de_broglie", ["QM:pq:wavelength", "QM:pq:planck_const"]),
    ("QM:fo:schrodinger", ["QM:pq:reduced_planck", "QM:pq:work_function"]),
    ("QM:fo:uncertainty", ["QM:pq:reduced_planck"]),
    ("QM:fo:photoelectric", ["QM:pq:planck_const", "QM:pq:frequency", "QM:pq:work_function"]),
]:
    for p in pqs:
        edges.append(sc.e(fo, p, "defines", "formula_quantity"))

for pq, un in [("QM:pq:frequency", "QM:un:hertz"), ("QM:pq:work_function", "QM:un:ev"), ("QM:pq:action", "QM:un:ev")]:
    edges.append(sc.e(pq, un, "has_unit", "quantity_unit"))

# 桥接基础库 + 跨切片（动量来自经典力学切片）
edges += [
    sc.e("QM:fo:planck", "PQ:energy", "derived_from", "energy_from_hnu"),
    sc.e("QM:fo:photoelectric", "PQ:energy", "derived_from", "energy_from_photoelectric"),
    sc.e("QM:fo:schrodinger", "PQ:mass", "derived_from", "mass_in_potential"),
    sc.e("QM:fo:de_broglie", "CM:pq:momentum", "derived_from", "momentum_in_dispersion"),
]
sc.build_and_write(OUT, "7e", "Quantum mechanics vertical slice", nodes, edges,
    extra_external={"CM:pq:momentum"},
    pint_checks=lambda u: [
        ("E=hν", u.J.dimensionality == (u.J * u.s * u.Hz).dimensionality),
        ("λ=h/p", u.m.dimensionality == (u.J * u.s / (u.kg * u.m / u.s)).dimensionality),
    ])
