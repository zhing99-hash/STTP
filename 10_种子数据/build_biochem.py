# -*- coding: utf-8 -*-
"""Phase 7m — 生物化学垂直切片（桥接有机/无机与基础分子库）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_common as sc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "seed_biochem.json")
nodes, edges = [], []

# 元素：不再自建 ``BC:el:nitrogen``（与元素层 canonical ``EK:el:N`` 重复，
# 且实测图谱中该批 composed_of 边早已指向 EK:el:N）→ 统一引用元素层。
# 分子（co2/h2o/o2 为镜像基础库节点）
nodes += [
    sc.n("BC:mo:glucose", "molecule", "Glucose", "chem.molecule", formula="C6H12O6", smiles="C(C1C(C(C(C(O1)O)O)O)O)O"),
    sc.n("BC:mo:atp", "molecule", "ATP", "chem.molecule", formula="C10H16N5O13P3", smiles="Nc1ncnc2c1ncn2[C@@H]1O[C@H](COP(O)(O)=O)[C@@H](O)[C@H]1O"),
    sc.n("BC:mo:amino_acid", "molecule", "Generic amino acid", "chem.molecule", formula="C2H5NO2", smiles="N[C@@H](C)C(O)=O"),
    sc.n("BC:mo:protein", "molecule", "Protein (polymer)", "chem.molecule", formula="(C2H3NO)n"),
    sc.n("BC:mo:dna", "molecule", "DNA (abstract)", "chem.molecule", formula="(C10H14N5O7P)n"),
    sc.n("BC:mo:co2", "molecule", "Carbon dioxide", "chem.molecule", formula="CO2", smiles="O=C=O"),
    sc.n("BC:mo:h2o", "molecule", "Water", "chem.molecule", formula="H2O", smiles="O"),
    sc.n("BC:mo:o2", "molecule", "Oxygen", "chem.molecule", formula="O2", smiles="O=O"),
]
# 反应
nodes += [
    sc.n("BC:rx:photosynthesis", "reaction", "Photosynthesis", "biology.metabolism", equation="6 CO2 + 6 H2O -> C6H12O6 + 6 O2"),
    sc.n("BC:rx:respiration", "reaction", "Cellular respiration", "biology.metabolism", equation="C6H12O6 + 6 O2 -> 6 CO2 + 6 H2O", exothermic=True),
    sc.n("BC:rx:atp_hydrolysis", "reaction", "ATP hydrolysis", "biology.energy", equation="ATP + H2O -> ADP + Pi", exothermic=True),
]
# 公式
nodes += [
    sc.n("BC:fo:photosynthesis_eq", "formula", "Photosynthesis stoichiometry", "chem.stoich", latex=r"6CO_2+6H_2O\rightarrow C_6H_{12}O_6+6O_2"),
    sc.n("BC:fo:atp_hydrolysis_eq", "formula", "ATP hydrolysis", "chem.energy", latex=r"ATP+H_2O\rightarrow ADP+P_i"),
]
# 符号
nodes += [
    sc.n("BC:sy:C", "symbol", "C (carbon)", "chem.symbol", latex="C"),
    sc.n("BC:sy:N", "symbol", "N (nitrogen)", "chem.symbol", latex="N"),
    sc.n("BC:sy:P", "symbol", "P (phosphorus)", "chem.symbol", latex="P"),
]
# 桥接基础库分子
edges += [
    sc.e("BC:mo:co2", "MO:co2", "same_as", "seed_to_existing", alignment="manual_curation"),
    sc.e("BC:mo:h2o", "MO:h2o", "same_as", "seed_to_existing", alignment="manual_curation"),
    sc.e("BC:mo:o2", "MO:o2", "same_as", "seed_to_existing", alignment="manual_curation"),
]
# 组成（引用元素层 canonical 元素 EK:el:C/H/O + 本切片氮）
# ⚠ 2026-10-09 连通性审计修正：原引用 ``EL:c/h/o``，该命名空间在 A5 去重后已不存在，
#   20 条 composed_of 边因此从未进图（被悬空断言静默丢弃）→ 改为元素层 id。
for mo, *parts in [
    ("BC:mo:glucose", ("EK:el:C", 6), ("EK:el:H", 12), ("EK:el:O", 6)),
    ("BC:mo:atp", ("EK:el:C", 10), ("EK:el:H", 16), ("EK:el:N", 5), ("EK:el:O", 13)),
    ("BC:mo:amino_acid", ("EK:el:C", 2), ("EK:el:H", 5), ("EK:el:N", 1), ("EK:el:O", 2)),
    ("BC:mo:protein", ("EK:el:C", 2), ("EK:el:H", 3), ("EK:el:N", 1), ("EK:el:O", 1)),
    ("BC:mo:dna", ("EK:el:C", 10), ("EK:el:H", 14), ("EK:el:N", 5), ("EK:el:O", 7)),
    ("BC:mo:co2", ("EK:el:C", 1), ("EK:el:O", 2)),
    ("BC:mo:h2o", ("EK:el:H", 2), ("EK:el:O", 1)),
    ("BC:mo:o2", ("EK:el:O", 2)),
]:
    for el, cnt in parts:
        edges.append(sc.e(mo, el, "composed_of", "molecule_element", count=cnt))
# 反应 产物/反应物
edges += [
    sc.e("BC:mo:co2", "BC:rx:photosynthesis", "reactant_of", "co2_reactant"),
    sc.e("BC:mo:h2o", "BC:rx:photosynthesis", "reactant_of", "h2o_reactant"),
    sc.e("BC:mo:glucose", "BC:rx:photosynthesis", "product_of", "glucose_product"),
    sc.e("BC:mo:o2", "BC:rx:photosynthesis", "product_of", "o2_product"),
    sc.e("BC:mo:glucose", "BC:rx:respiration", "reactant_of", "glucose_reactant"),
    sc.e("BC:mo:o2", "BC:rx:respiration", "reactant_of", "o2_reactant"),
    sc.e("BC:mo:co2", "BC:rx:respiration", "product_of", "co2_product"),
    sc.e("BC:mo:h2o", "BC:rx:respiration", "product_of", "h2o_product"),
    sc.e("BC:mo:atp", "BC:rx:atp_hydrolysis", "reactant_of", "atp_reactant"),
    sc.e("BC:mo:h2o", "BC:rx:atp_hydrolysis", "reactant_of", "h2o_reactant"),
]
# 具体反应 -> 一般公式
edges.append(sc.e("BC:rx:photosynthesis", "BC:fo:photosynthesis_eq", "derived_from", "stoich_from_rx"))
# 2026-10-09 连通性审计补齐：
#   `BC:fo:atp_hydrolysis_eq` 自建库起无任何边（deg=0）→ 与 photosynthesis 对称补 derived_from
#   三个元素符号节点 BC:sy:C/N/P 亦无人引用 → 由该公式声明其符号（ATP=C10H16N5O13P3，含 C/N/P）
edges.append(sc.e("BC:rx:atp_hydrolysis", "BC:fo:atp_hydrolysis_eq", "derived_from", "stoich_from_rx"))
for sym in ("C", "N", "P"):
    edges.append(sc.e("BC:fo:atp_hydrolysis_eq", "BC:sy:%s" % sym, "has_symbol", "formula_symbol"))
sc.build_and_write(OUT, "7m", "Biochemistry vertical slice", nodes, edges,
                   extra_external={"EK:el:N"})
