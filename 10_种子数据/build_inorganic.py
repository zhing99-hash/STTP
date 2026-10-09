# -*- coding: utf-8 -*-
"""Phase 7g — 无机化学垂直切片（元素 Na/Cl/Fe、酸碱/氧化还原/合成反应、pH、周期律）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_common as sc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "seed_inorganic.json")
nodes, edges = [], []

# 元素（Na/Cl/Fe 一律引用元素层 canonical 节点 EK:el:<符号>，**不在本切片自建**）
# ⚠ 2026-10-09 连通性审计修正：原写法自建 ``IC:el:sodium/chlorine/iron``，与 A5 元素去重
#   的结论（全图元素统一为 EK:el:<符号>，118 个）冲突 —— 入图会重新制造重复元素节点。
# 分子（h2o/o2 镜像基础库 MO:h2o/MO:o2）
nodes += [
    sc.n("IC:mo:nacl", "molecule", "Sodium chloride", "chem.molecule", formula="NaCl", smiles="[Na+].[Cl-]"),
    sc.n("IC:mo:hcl", "molecule", "Hydrogen chloride", "chem.molecule", formula="HCl", smiles="Cl"),
    sc.n("IC:mo:cl2", "molecule", "Chlorine", "chem.molecule", formula="Cl2", smiles="ClCl"),
    sc.n("IC:mo:na", "molecule", "Sodium", "chem.molecule", formula="Na", smiles="[Na]"),
    sc.n("IC:mo:h2o", "molecule", "Water", "chem.molecule", formula="H2O", smiles="O"),
    sc.n("IC:mo:o2", "molecule", "Oxygen", "chem.molecule", formula="O2", smiles="O=O"),
]
# 反应
nodes += [
    sc.n("IC:rx:neutralization", "reaction", "Neutralization", "chemistry.aq", equation="HCl + NaOH -> NaCl + H2O", exothermic=True),
    sc.n("IC:rx:redox", "reaction", "Hydrogen combustion", "chemistry.redox", equation="2 H2 + O2 -> 2 H2O", exothermic=True),
    sc.n("IC:rx:synthesis", "reaction", "Sodium chloride synthesis", "chemistry.synthesis", equation="2 Na + Cl2 -> 2 NaCl"),
]
# 公式
nodes += [
    sc.n("IC:fo:ph", "formula", "pH definition", "chem.acidbase", latex=r"pH=-\log_{10}[H^+]", informal="pH is minus log of hydrogen ion concentration."),
    sc.n("IC:fo:periodic", "formula", "Periodic law", "chem.periodic", latex=r"f(Z)", informal="Properties are periodic functions of atomic number."),
]
# 符号
nodes += [
    sc.n("IC:sy:Na", "symbol", "Na (sodium)", "chem.symbol", latex="Na"),
    sc.n("IC:sy:Cl", "symbol", "Cl (chlorine)", "chem.symbol", latex="Cl"),
    sc.n("IC:sy:Fe", "symbol", "Fe (iron)", "chem.symbol", latex="Fe"),
    sc.n("IC:sy:H", "symbol", "H (hydrogen)", "chem.symbol", latex="H"),
]

# 桥接基础库分子
edges.append(sc.e("IC:mo:h2o", "MO:h2o", "same_as", "seed_to_existing", alignment="manual_curation"))
edges.append(sc.e("IC:mo:o2", "MO:o2", "same_as", "seed_to_existing", alignment="manual_curation"))

# 公式 -> 符号
edges.append(sc.e("IC:fo:ph", "IC:sy:H", "has_symbol", "formula_symbol"))

# 元素 -> 符号（2026-10-09 连通性审计补齐）
# Na/Cl/Fe 三个符号节点自建库起无人引用（deg=0）。符号是**元素的记号**，
# 故由元素层（EK:el:*）声明「该元素的符号是 …」，与 A5 的元素命名空间统一同源。
for sym in ("Na", "Cl", "Fe"):
    edges.append(sc.e("EK:el:%s" % sym, "IC:sy:%s" % sym, "has_symbol", "element_symbol"))

# 组成（全部引用元素层 canonical 元素 EK:el:<符号>）
# ⚠ 2026-10-09 连通性审计修正：原引用 ``EL:h/EL:o`` 与自建 ``IC:el:sodium/chlorine``
for mo, *parts in [
    ("IC:mo:nacl", ("EK:el:Na", 1), ("EK:el:Cl", 1)),
    ("IC:mo:hcl", ("EK:el:H", 1), ("EK:el:Cl", 1)),
    ("IC:mo:cl2", ("EK:el:Cl", 2)),
    ("IC:mo:na", ("EK:el:Na", 1)),
    ("IC:mo:h2o", ("EK:el:H", 2), ("EK:el:O", 1)),
    ("IC:mo:o2", ("EK:el:O", 2)),
]:
    for el, cnt in parts:
        edges.append(sc.e(mo, el, "composed_of", "molecule_element", count=cnt))

# 反应 产物/反应物
edges += [
    sc.e("IC:mo:hcl", "IC:rx:neutralization", "reactant_of", "acid_reactant"),
    sc.e("IC:mo:nacl", "IC:rx:neutralization", "product_of", "salt_product"),
    sc.e("IC:mo:h2o", "IC:rx:neutralization", "product_of", "water_product"),
    sc.e("IC:mo:na", "IC:rx:synthesis", "reactant_of", "metal_reactant"),
    sc.e("IC:mo:cl2", "IC:rx:synthesis", "reactant_of", "halogen_reactant"),
    sc.e("IC:mo:nacl", "IC:rx:synthesis", "product_of", "salt_product"),
    sc.e("IC:mo:h2o", "IC:rx:redox", "product_of", "water_product"),
    sc.e("IC:mo:o2", "IC:rx:redox", "reactant_of", "oxygen_reactant"),
]
# 具体反应 -> 一般公式
edges += [
    sc.e("IC:rx:neutralization", "IC:fo:ph", "derived_from", "ph_from_neutralization"),
    sc.e("IC:rx:synthesis", "IC:fo:periodic", "derived_from", "periodic_from_synthesis"),
]
sc.build_and_write(OUT, "7g", "Inorganic chemistry vertical slice", nodes, edges,
                   # EK:el:* 是元素层（Phase 8 A5 统一后的规范命名空间），本切片只引用不自建
                   # （seed_common.BASE_IDS 已含 EK:el:C/H/O，此处补本切片用到的 Na/Cl/Fe）
                   extra_external={"EK:el:Na", "EK:el:Cl", "EK:el:Fe"})
