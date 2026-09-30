# -*- coding: utf-8 -*-
"""Phase 7b — 有机化学基础垂直切片（真实 SMILES 分子 + 燃烧反应，引用基础图谱元素/分子节点）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_common as sc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "seed_organic_chem.json")
nodes, edges = [], []

# 分子（真实 SMILES）
nodes += [
    sc.n("OM:mo:methane", "molecule", "Methane", "chem.molecule", formula="CH4", smiles="C"),
    sc.n("OM:mo:ethane", "molecule", "Ethane", "chem.molecule", formula="C2H6", smiles="CC"),
    sc.n("OM:mo:ethylene", "molecule", "Ethylene", "chem.molecule", formula="C2H4", smiles="C=C"),
    sc.n("OM:mo:benzene", "molecule", "Benzene", "chem.molecule", formula="C6H6", smiles="c1ccccc1"),
    sc.n("OM:mo:ethanol", "molecule", "Ethanol", "chem.molecule", formula="C2H5OH", smiles="CCO"),
]
# 通用烃类燃烧公式
nodes.append(sc.n("OM:fo:combustion", "formula", "Hydrocarbon combustion", "chem.thermo",
                  latex=r"C_xH_y + (x+\tfrac{y}{4})O_2 \rightarrow xCO_2 + \tfrac{y}{2}H_2O",
                  informal="General combustion of a hydrocarbon."))
# 元素符号
nodes += [
    sc.n("OM:sy:C", "symbol", "C (carbon)", "math.symbol", latex="C"),
    sc.n("OM:sy:H", "symbol", "H (hydrogen)", "math.symbol", latex="H"),
    sc.n("OM:sy:O", "symbol", "O (oxygen)", "math.symbol", latex="O"),
]
# 反应
nodes += [
    sc.n("OM:rx:ethane_comb", "reaction", "Ethane combustion", "chemistry.thermo",
         equation="2 C2H6 + 7 O2 -> 4 CO2 + 6 H2O", exothermic=True),
    sc.n("OM:rx:ethanol_comb", "reaction", "Ethanol combustion", "chemistry.thermo",
         equation="C2H5OH + 3 O2 -> 2 CO2 + 3 H2O", exothermic=True),
]

# 桥：甲烷 same_as Phase6 甲烷
edges.append(sc.e("OM:mo:methane", "MO:ch4", "same_as", "seed_to_existing", alignment="manual_curation"))

# 通用燃烧公式 -> 符号
for s in ["OM:sy:C", "OM:sy:H", "OM:sy:O"]:
    edges.append(sc.e("OM:fo:combustion", s, "has_symbol", "formula_symbol"))

# 分子 -> 元素组成（引用基础图谱 EL:*）
comp = [
    ("OM:mo:methane", ("EL:c", 1), ("EL:h", 4)),
    ("OM:mo:ethane", ("EL:c", 2), ("EL:h", 6)),
    ("OM:mo:ethylene", ("EL:c", 2), ("EL:h", 4)),
    ("OM:mo:benzene", ("EL:c", 6), ("EL:h", 6)),
    ("OM:mo:ethanol", ("EL:c", 2), ("EL:h", 6), ("EL:o", 1)),
]
for mo, *parts in comp:
    for el, cnt in parts:
        edges.append(sc.e(mo, el, "composed_of", "molecule_element", count=cnt))

# 反应 产物/反应物（引用基础图谱 MO:co2/MO:h2o/MO:o2）
edges += [
    sc.e("OM:mo:ethane", "OM:rx:ethane_comb", "reactant_of", "combustion_reactant"),
    sc.e("MO:o2", "OM:rx:ethane_comb", "reactant_of", "combustion_reactant"),
    sc.e("MO:co2", "OM:rx:ethane_comb", "product_of", "combustion_product"),
    sc.e("MO:h2o", "OM:rx:ethane_comb", "product_of", "combustion_product"),
    sc.e("OM:mo:ethanol", "OM:rx:ethanol_comb", "reactant_of", "combustion_reactant"),
    sc.e("MO:o2", "OM:rx:ethanol_comb", "reactant_of", "combustion_reactant"),
    sc.e("MO:co2", "OM:rx:ethanol_comb", "product_of", "combustion_product"),
    sc.e("MO:h2o", "OM:rx:ethanol_comb", "product_of", "combustion_product"),
    # 具体反应 -> 一般公式
    sc.e("OM:rx:ethane_comb", "OM:fo:combustion", "derived_from", "generalize"),
    sc.e("OM:rx:ethanol_comb", "OM:fo:combustion", "derived_from", "generalize"),
]

sc.build_and_write(OUT, "7b", "Organic chemistry basics vertical slice", nodes, edges)
