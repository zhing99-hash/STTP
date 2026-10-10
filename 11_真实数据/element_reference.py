# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""权威元素参考表（周期表 1–118）。

用途：
  1. 补全图谱中缺失的原子量（`atomic_weight` / `atomic_mass`）；
  2. 校正外部数据源的符号 / 原子序数（如 ElementKG 的 `HASATOMIC` 对部分元素
     给的是「族号」而非原子序数——典型如 Oxygen 记为 16）；
  3. 供各适配器统一引用，避免散落的硬编码表。

数据来源：IUPAC 2021 标准原子量（常规值）；
          无稳定同位素的放射性元素取「最长寿同位素质量数」（整数）。

用法：
    from element_reference import BY_SYMBOL, BY_Z, by_symbol, by_atomic_number
    by_symbol("O")   -> {"symbol": "O", "name": "Oxygen", "atomic_number": 8, "atomic_weight": 15.999}
"""

# (symbol, name, atomic_number, standard_atomic_weight)
ELEMENTS = [
    ("H", "Hydrogen", 1, 1.008),
    ("He", "Helium", 2, 4.002602),
    ("Li", "Lithium", 3, 6.94),
    ("Be", "Beryllium", 4, 9.0121831),
    ("B", "Boron", 5, 10.81),
    ("C", "Carbon", 6, 12.011),
    ("N", "Nitrogen", 7, 14.007),
    ("O", "Oxygen", 8, 15.999),
    ("F", "Fluorine", 9, 18.998403163),
    ("Ne", "Neon", 10, 20.1797),
    ("Na", "Sodium", 11, 22.98976928),
    ("Mg", "Magnesium", 12, 24.305),
    ("Al", "Aluminium", 13, 26.9815385),
    ("Si", "Silicon", 14, 28.085),
    ("P", "Phosphorus", 15, 30.973761998),
    ("S", "Sulfur", 16, 32.06),
    ("Cl", "Chlorine", 17, 35.45),
    ("Ar", "Argon", 18, 39.948),
    ("K", "Potassium", 19, 39.0983),
    ("Ca", "Calcium", 20, 40.078),
    ("Sc", "Scandium", 21, 44.955908),
    ("Ti", "Titanium", 22, 47.867),
    ("V", "Vanadium", 23, 50.9415),
    ("Cr", "Chromium", 24, 51.9961),
    ("Mn", "Manganese", 25, 54.938044),
    ("Fe", "Iron", 26, 55.845),
    ("Co", "Cobalt", 27, 58.933194),
    ("Ni", "Nickel", 28, 58.6934),
    ("Cu", "Copper", 29, 63.546),
    ("Zn", "Zinc", 30, 65.38),
    ("Ga", "Gallium", 31, 69.723),
    ("Ge", "Germanium", 32, 72.630),
    ("As", "Arsenic", 33, 74.921595),
    ("Se", "Selenium", 34, 78.971),
    ("Br", "Bromine", 35, 79.904),
    ("Kr", "Krypton", 36, 83.798),
    ("Rb", "Rubidium", 37, 85.4678),
    ("Sr", "Strontium", 38, 87.62),
    ("Y", "Yttrium", 39, 88.90584),
    ("Zr", "Zirconium", 40, 91.224),
    ("Nb", "Niobium", 41, 92.90637),
    ("Mo", "Molybdenum", 42, 95.95),
    ("Tc", "Technetium", 43, 98.0),
    ("Ru", "Ruthenium", 44, 101.07),
    ("Rh", "Rhodium", 45, 102.90550),
    ("Pd", "Palladium", 46, 106.42),
    ("Ag", "Silver", 47, 107.8682),
    ("Cd", "Cadmium", 48, 112.414),
    ("In", "Indium", 49, 114.818),
    ("Sn", "Tin", 50, 118.710),
    ("Sb", "Antimony", 51, 121.760),
    ("Te", "Tellurium", 52, 127.60),
    ("I", "Iodine", 53, 126.90447),
    ("Xe", "Xenon", 54, 131.293),
    ("Cs", "Caesium", 55, 132.90545196),
    ("Ba", "Barium", 56, 137.327),
    ("La", "Lanthanum", 57, 138.90547),
    ("Ce", "Cerium", 58, 140.116),
    ("Pr", "Praseodymium", 59, 140.90766),
    ("Nd", "Neodymium", 60, 144.242),
    ("Pm", "Promethium", 61, 145.0),
    ("Sm", "Samarium", 62, 150.36),
    ("Eu", "Europium", 63, 151.964),
    ("Gd", "Gadolinium", 64, 157.25),
    ("Tb", "Terbium", 65, 158.92535),
    ("Dy", "Dysprosium", 66, 162.500),
    ("Ho", "Holmium", 67, 164.93033),
    ("Er", "Erbium", 68, 167.259),
    ("Tm", "Thulium", 69, 168.93422),
    ("Yb", "Ytterbium", 70, 173.045),
    ("Lu", "Lutetium", 71, 174.9668),
    ("Hf", "Hafnium", 72, 178.49),
    ("Ta", "Tantalum", 73, 180.94788),
    ("W", "Tungsten", 74, 183.84),
    ("Re", "Rhenium", 75, 186.207),
    ("Os", "Osmium", 76, 190.23),
    ("Ir", "Iridium", 77, 192.217),
    ("Pt", "Platinum", 78, 195.084),
    ("Au", "Gold", 79, 196.966569),
    ("Hg", "Mercury", 80, 200.592),
    ("Tl", "Thallium", 81, 204.38),
    ("Pb", "Lead", 82, 207.2),
    ("Bi", "Bismuth", 83, 208.98040),
    ("Po", "Polonium", 84, 209.0),
    ("At", "Astatine", 85, 210.0),
    ("Rn", "Radon", 86, 222.0),
    ("Fr", "Francium", 87, 223.0),
    ("Ra", "Radium", 88, 226.0),
    ("Ac", "Actinium", 89, 227.0),
    ("Th", "Thorium", 90, 232.0377),
    ("Pa", "Protactinium", 91, 231.03588),
    ("U", "Uranium", 92, 238.02891),
    ("Np", "Neptunium", 93, 237.0),
    ("Pu", "Plutonium", 94, 244.0),
    ("Am", "Americium", 95, 243.0),
    ("Cm", "Curium", 96, 247.0),
    ("Bk", "Berkelium", 97, 247.0),
    ("Cf", "Californium", 98, 251.0),
    ("Es", "Einsteinium", 99, 252.0),
    ("Fm", "Fermium", 100, 257.0),
    ("Md", "Mendelevium", 101, 258.0),
    ("No", "Nobelium", 102, 259.0),
    ("Lr", "Lawrencium", 103, 266.0),
    ("Rf", "Rutherfordium", 104, 267.0),
    ("Db", "Dubnium", 105, 268.0),
    ("Sg", "Seaborgium", 106, 269.0),
    ("Bh", "Bohrium", 107, 270.0),
    ("Hs", "Hassium", 108, 269.0),
    ("Mt", "Meitnerium", 109, 278.0),
    ("Ds", "Darmstadtium", 110, 281.0),
    ("Rg", "Roentgenium", 111, 282.0),
    ("Cn", "Copernicium", 112, 285.0),
    ("Nh", "Nihonium", 113, 286.0),
    ("Fl", "Flerovium", 114, 289.0),
    ("Mc", "Moscovium", 115, 290.0),
    ("Lv", "Livermorium", 116, 293.0),
    ("Ts", "Tennessine", 117, 294.0),
    ("Og", "Oganesson", 118, 294.0),
]

# ---------------------------------------------------------------- 周期表位置
# 周期：原子序数区间 → 周期号（IUPAC 标准周期表）
PERIOD_RANGES = (
    (1, 2, 1), (3, 10, 2), (11, 18, 3), (19, 36, 4),
    (37, 54, 5), (55, 86, 6), (87, 118, 7),
)

# 族：IUPAC 18 族。3 族含 La / Ac（镧系 / 锕系首元素），
#     其余 14 个 f 区成员不占族号（group=None），只由 `series` 表达。
GROUPS = {
    1: "H Li Na K Rb Cs Fr",
    2: "Be Mg Ca Sr Ba Ra",
    3: "Sc Y La Ac",
    4: "Ti Zr Hf Rf",
    5: "V Nb Ta Db",
    6: "Cr Mo W Sg",
    7: "Mn Tc Re Bh",
    8: "Fe Ru Os Hs",
    9: "Co Rh Ir Mt",
    10: "Ni Pd Pt Ds",
    11: "Cu Ag Au Rg",
    12: "Zn Cd Hg Cn",
    13: "B Al Ga In Tl Nh",
    14: "C Si Ge Sn Pb Fl",
    15: "N P As Sb Bi Mc",
    16: "O S Se Te Po Lv",
    17: "F Cl Br I At Ts",
    18: "He Ne Ar Kr Xe Rn Og",
}

LANTHANIDES = "La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu"
ACTINIDES = "Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr"

_GROUP_BY_SYMBOL = {sym: g for g, syms in GROUPS.items() for sym in syms.split()}
_SERIES_BY_SYMBOL = {}
for _sym in LANTHANIDES.split():
    _SERIES_BY_SYMBOL[_sym] = "lanthanide"
for _sym in ACTINIDES.split():
    _SERIES_BY_SYMBOL[_sym] = "actinide"


def _period_of(atomic_number):
    for lo, hi, p in PERIOD_RANGES:
        if lo <= atomic_number <= hi:
            return p
    return None


def _block_of(symbol, group):
    """s / p / d / f 区块。He 归 s（1s²），f 区按 series 判定。"""
    if symbol == "He":
        return "s"
    if group in (1, 2):
        return "s"
    if group is not None and 13 <= group <= 18:
        return "p"
    if group is not None and 3 <= group <= 12:
        return "d"
    if symbol in _SERIES_BY_SYMBOL:
        return "f"
    return None


BY_SYMBOL = {
    s: {
        "symbol": s,
        "name": n,
        "atomic_number": z,
        "atomic_weight": w,
        "period": _period_of(z),
        "group": _GROUP_BY_SYMBOL.get(s),
        "block": _block_of(s, _GROUP_BY_SYMBOL.get(s)),
        "series": _SERIES_BY_SYMBOL.get(s),
    }
    for s, n, z, w in ELEMENTS
}
BY_Z = {z: rec for z, rec in ((r["atomic_number"], r) for r in BY_SYMBOL.values())}


def by_symbol(symbol):
    """按符号取权威记录；未知返回 None。大小写按 '首大其余小' 规整。"""
    if not symbol:
        return None
    s = str(symbol).strip()
    if s in BY_SYMBOL:
        return BY_SYMBOL[s]
    s2 = s[:1].upper() + s[1:].lower()
    return BY_SYMBOL.get(s2)


def by_atomic_number(z):
    """按原子序数取权威记录；未知返回 None。"""
    try:
        return BY_Z.get(int(z))
    except (TypeError, ValueError):
        return None


def weight_of(symbol):
    r = by_symbol(symbol)
    return r["atomic_weight"] if r else None


# ---------------------------------------------------------------- 周期表位置查询
def period_of_z(z):
    """周期号 1–7；未知返回 None。"""
    r = by_atomic_number(z)
    return r["period"] if r else None


def group_of_z(z):
    """族号 1–18；镧系 / 锕系（La / Ac 除外）与未知均返回 None。"""
    r = by_atomic_number(z)
    return r["group"] if r else None


def block_of_z(z):
    """区块 s / p / d / f；未知返回 None。"""
    r = by_atomic_number(z)
    return r["block"] if r else None


def series_of_z(z):
    """系列 lanthanide / actinide；非 f 区返回 None。"""
    r = by_atomic_number(z)
    return r["series"] if r else None


def series_symbols(series):
    """返回某系列的符号列表（保持原子序数序）。series ∈ {'lanthanide','actinide'}。"""
    return [s for s in LANTHANIDES.split()] if series == "lanthanide" else (
        [s for s in ACTINIDES.split()] if series == "actinide" else [])


def members_of_group(group):
    """返回某族（1–18）的符号列表，按原子序数排序。"""
    syms = (GROUPS.get(group) or "").split()
    return sorted(syms, key=lambda s: BY_SYMBOL[s]["atomic_number"])


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"元素条目: {len(ELEMENTS)}")
    assert len(BY_SYMBOL) == 118, "符号应唯一且 118 个"
    assert sorted(BY_Z) == list(range(1, 119)), "原子序数应 1..118"
    for s in ("H", "C", "N", "O", "Na", "Cl", "Fe", "S", "Og"):
        print(" ", by_symbol(s))
    # ---- 周期表位置自检 ----
    assert all(r["period"] in (1, 2, 3, 4, 5, 6, 7) for r in BY_SYMBOL.values())
    assert len([r for r in BY_SYMBOL.values() if r["group"]]) == 118 - 28, "18 族应覆盖 90 个元素"
    assert sum(1 for r in BY_SYMBOL.values() if r["series"] == "lanthanide") == 15
    assert sum(1 for r in BY_SYMBOL.values() if r["series"] == "actinide") == 15
    from collections import Counter
    pc = Counter(r["period"] for r in BY_SYMBOL.values())
    assert [pc[i] for i in range(1, 8)] == [2, 8, 8, 18, 18, 32, 32], f"周期员数异常: {pc}"
    bc = Counter(r["block"] for r in BY_SYMBOL.values())
    assert bc["f"] == 28, f"f 区应 28 个（Ce–Lu + Th–Lr）: {bc}"
    print(" 周期员数:", [pc[i] for i in range(1, 8)])
    print(" 区块分布:", dict(bc))
    print(" 第 17 族:", members_of_group(17))
    print(" 镧系:", series_symbols("lanthanide"))
    print("自检通过")
