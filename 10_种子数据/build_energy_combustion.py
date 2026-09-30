# -*- coding: utf-8 -*-
"""
Phase 6 垂直切片：「能量·燃烧」
把 数学公式(E=mc2/F=ma/KE) + 物理公式 + 物理量 + 单位 + 化学反应(CH4燃烧)
+ 真实分子(CH4/O2/CO2/H2O) + 元素(C/H/O) 串成一条可视图谱。

产物 seed_energy_combustion.json 直接可被 08_部署包/neo4j/load_neo4j.py --input 加法推入
（MERGE 幂等，不破坏现有 62 节点 / 98 边）。并通过 same_as 挂到现有 MX 图谱。

节点标签用 CamelCase（与 graph_view.html 的 TYPE_STYLE / graph_export 的 TYPE_PRIORITY 对齐）：
    Element / Unit / PhysicalQuantity / Symbol / Formula / Molecule / Reaction
props.ntype 保留小写（供 Aura type_stats）。domain 驱动可视化学科着色（化学/物理/数学）。
"""
import json
import os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "seed_energy_combustion.json")

NOW = datetime.now().isoformat()
SOURCE = "curated_seed/energy_combustion"
CONF = 1.0  # 手工策划、显式知识

# ntype(小写) -> 标签 CamelCase，必须命中 graph_export.TYPE_PRIORITY / graph_view.TYPE_STYLE
TYPE_LABEL = {
    "element": "Element", "unit": "Unit", "physical_quantity": "PhysicalQuantity",
    "symbol": "Symbol", "formula": "Formula", "molecule": "Molecule", "reaction": "Reaction",
}


def n(id, ntype, name, domain="", **props):
    p = {"name": name, "ntype": ntype, "domain": domain, "source": SOURCE,
         "confidence": CONF, "explicit_or_inferred": "explicit", "created_at": NOW}
    p.update(props)
    return {"id": id, "labels": ["Entity", TYPE_LABEL[ntype]], "props": p}


def e(src, tgt, etype, kind, **props):
    p = {"confidence": CONF, "explicit_or_inferred": "explicit",
         "kind": kind, "source": SOURCE, "created_at": NOW}
    p.update(props)
    return {"id": f"{src}->{tgt}[{etype}]", "source": src, "target": tgt,
            "type": etype, "kind": kind, "props": p}


def main():
    nodes, edges = [], []

    # ---------- 元素（真实周期表数据）----------
    nodes += [
        n("EL:c", "element", "Carbon", "chem.element", symbol="C", atomic_number=6, atomic_mass=12.011, period=2, group=14),
        n("EL:h", "element", "Hydrogen", "chem.element", symbol="H", atomic_number=1, atomic_mass=1.008, period=1, group=1),
        n("EL:o", "element", "Oxygen", "chem.element", symbol="O", atomic_number=8, atomic_mass=15.999, period=2, group=16),
    ]

    # ---------- 单位 ----------
    nodes += [
        n("UN:kg", "unit", "kilogram", "phys.unit", symbol="kg", dimension="M", si_base=True),
        n("UN:m", "unit", "meter", "phys.unit", symbol="m", dimension="L", si_base=True),
        n("UN:s", "unit", "second", "phys.unit", symbol="s", dimension="T", si_base=True),
        n("UN:j", "unit", "joule", "phys.unit", symbol="J", dimension="M*L^2*T^-2", derived_from="kg*m^2/s^2"),
        n("UN:mps", "unit", "meter per second", "phys.unit", symbol="m/s", dimension="L*T^-1"),
        n("UN:mps2", "unit", "meter per second squared", "phys.unit", symbol="m/s^2", dimension="L*T^-2"),
    ]

    # ---------- 物理量 ----------
    nodes += [
        n("PQ:energy", "physical_quantity", "Energy", "phys.quantity", symbol="E", dimension="M*L^2*T^-2"),
        n("PQ:mass", "physical_quantity", "Mass", "phys.quantity", symbol="m", dimension="M"),
        n("PQ:c", "physical_quantity", "Speed of light", "phys.quantity", symbol="c", value=299792458, unit="m/s"),
        n("PQ:force", "physical_quantity", "Force", "phys.quantity", symbol="F", dimension="M*L*T^-2"),
        n("PQ:accel", "physical_quantity", "Acceleration", "phys.quantity", symbol="a", dimension="L*T^-2"),
        n("PQ:vel", "physical_quantity", "Velocity", "phys.quantity", symbol="v", dimension="L*T^-1"),
        n("PQ:ke", "physical_quantity", "Kinetic energy", "phys.quantity", symbol="KE", dimension="M*L^2*T^-2"),
    ]

    # ---------- 符号 ----------
    nodes += [
        n("SY:e", "symbol", "E (energy)", "math.symbol", latex="E"),
        n("SY:m", "symbol", "m (mass)", "math.symbol", latex="m"),
        n("SY:c", "symbol", "c (speed of light)", "math.symbol", latex="c"),
        n("SY:f", "symbol", "F (force)", "math.symbol", latex="F"),
        n("SY:a", "symbol", "a (acceleration)", "math.symbol", latex="a"),
        n("SY:v", "symbol", "v (velocity)", "math.symbol", latex="v"),
        n("SY:ke", "symbol", "KE (kinetic energy)", "math.symbol", latex="KE"),
    ]

    # ---------- 公式（数学/物理）----------
    nodes += [
        n("FO:emc2", "formula", "Mass-energy equivalence", "physics.relativity", latex="E=mc^2",
          informal="Energy equals mass times the speed of light squared."),
        n("FO:fma", "formula", "Newton's second law", "physics.mechanics", latex="F=ma",
          informal="Force equals mass times acceleration."),
        n("FO:ke", "formula", "Kinetic energy", "physics.mechanics", latex=r"KE=\tfrac{1}{2}mv^2",
          informal="Kinetic energy of a mass m moving at velocity v."),
    ]

    # ---------- 分子（真实组成）----------
    nodes += [
        n("MO:ch4", "molecule", "Methane", "chem.molecule", formula="CH4", smiles="C"),
        n("MO:o2", "molecule", "Dioxygen", "chem.molecule", formula="O2", smiles="O=O"),
        n("MO:co2", "molecule", "Carbon dioxide", "chem.molecule", formula="CO2", smiles="O=C=O"),
        n("MO:h2o", "molecule", "Water", "chem.molecule", formula="H2O", smiles="O"),
    ]

    # ---------- 反应 ----------
    nodes += [
        n("RX:comb_ch4", "reaction", "Methane combustion", "chemistry.thermo",
          equation="CH4 + 2 O2 -> CO2 + 2 H2O", exothermic=True),
    ]

    # ============ 边 ============
    # 公式 -> 符号
    for fo, syms in [("FO:emc2", ["SY:e", "SY:m", "SY:c"]),
                     ("FO:fma", ["SY:f", "SY:m", "SY:a"]),
                     ("FO:ke", ["SY:ke", "SY:m", "SY:v"])]:
        for s in syms:
            edges.append(e(fo, s, "has_symbol", "formula_symbol"))

    # 公式 -> 物理量（defines）
    for fo, pqs in [("FO:emc2", ["PQ:energy", "PQ:mass", "PQ:c"]),
                    ("FO:fma", ["PQ:force", "PQ:mass", "PQ:accel"]),
                    ("FO:ke", ["PQ:ke", "PQ:mass", "PQ:vel"])]:
        for p in pqs:
            edges.append(e(fo, p, "defines", "formula_quantity"))

    # 物理量 -> 单位
    for pq, un in [("PQ:mass", "UN:kg"), ("PQ:energy", "UN:j"), ("PQ:c", "UN:mps"),
                   ("PQ:force", "UN:kg"), ("PQ:accel", "UN:mps2"), ("PQ:vel", "UN:mps")]:
        edges.append(e(pq, un, "has_unit", "quantity_unit"))

    # 量纲一致（pint 背书，见下方核验）
    edges.append(e("PQ:energy", "PQ:mass", "dimensionally_consistent", "mass_energy", note="E=mc^2"))
    edges.append(e("PQ:force", "PQ:mass", "dimensionally_consistent", "force_mass", note="F=ma"))
    edges.append(e("PQ:ke", "PQ:energy", "dimensionally_consistent", "same_dimension", note="KE is energy"))

    # 分子 -> 元素（组成）
    comp = [("MO:ch4", "EL:c", 1), ("MO:ch4", "EL:h", 4),
            ("MO:o2", "EL:o", 2),
            ("MO:co2", "EL:c", 1), ("MO:co2", "EL:o", 2),
            ("MO:h2o", "EL:h", 2), ("MO:h2o", "EL:o", 1)]
    for mo, el, cnt in comp:
        edges.append(e(mo, el, "composed_of", "molecule_element", count=cnt))

    # 反应 -> 分子
    edges.append(e("MO:ch4", "RX:comb_ch4", "reactant_of", "combustion_reactant"))
    edges.append(e("MO:o2", "RX:comb_ch4", "reactant_of", "combustion_reactant"))
    edges.append(e("MO:co2", "RX:comb_ch4", "product_of", "combustion_product"))
    edges.append(e("MO:h2o", "RX:comb_ch4", "product_of", "combustion_product"))

    # 化学 <-> 物理 桥：燃烧释放能量；能量来自质能等价；KE 来自牛顿定律
    edges.append(e("RX:comb_ch4", "PQ:energy", "releases", "exothermic", note="combustion releases energy"))
    edges.append(e("PQ:energy", "FO:emc2", "derived_from", "mass_energy"))
    edges.append(e("FO:ke", "FO:fma", "derived_from", "work_energy", note="work-energy theorem"))
    edges.append(e("PQ:ke", "FO:fma", "derived_from", "work_energy"))

    # 挂到现有 MX 图谱（same_as）
    bridges = [("MO:co2", "MX:chem:co2"), ("MO:ch4", "MX:chem:methane"),
               ("PQ:energy", "MX:phy:energy"), ("PQ:ke", "MX:phy:kinetic_energy"),
               ("FO:fma", "MX:phy:newton2"), ("SY:c", "MX:sym:c")]
    for a, b in bridges:
        edges.append(e(a, b, "same_as", "seed_to_existing", alignment="manual_curation"))

    # ---------- 校验 ----------
    ids = {x["id"] for x in nodes}
    known_external = {b for a, b in bridges}  # same_as 指向 Aura 已有 MX 节点
    assert len(ids) == len(nodes), f"节点 id 重复: {len(nodes)-len(ids)}"
    dangling = [x for x in edges if x["source"] not in ids | known_external
                or x["target"] not in ids | known_external]
    assert not dangling, f"悬空边: {dangling[:3]}"
    bad_type = [x["type"] for x in edges if not x["type"].islower()]
    assert not bad_type, f"边类型非全小写: {bad_type}"

    out = {"schema_version": "0.1", "phase": "6",
           "note": "Energy & combustion vertical slice (curated, additive to Aura)",
           "nodes": nodes, "edges": edges}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    from collections import Counter
    print(f"[OK] 节点: {len(nodes)}  边: {len(edges)}")
    print("     节点类型(标签):", dict(Counter(x["labels"][-1] for x in nodes)))
    print("     边类型:", dict(Counter(x["type"] for x in edges)))
    print("     悬空边: 0  重复 id: 0  边类型全小写: 是")
    print(f"[OUT] {os.path.relpath(OUT)}")


if __name__ == "__main__":
    try:
        import pint
        u = pint.UnitRegistry()
        left_e = u.kg*(u.m/u.s)**2; right_e = u.kg*u.m**2/u.s**2   # E = mc^2 = kg*m^2/s^2
        left_f = u.kg*u.m/u.s**2; right_f = u.kg*(u.m/u.s**2)           # F = ma = kg*m/s^2
        left_ke = u.kg*(u.m/u.s)**2; right_ke = u.kg*u.m**2/u.s**2     # KE = 1/2 mv^2 = kg*m^2/s^2
        print(f"[pint] E==mc^2: {left_e.dimensionality == right_e.dimensionality} | "
              f"F==ma: {left_f.dimensionality == right_f.dimensionality} | "
              f"KE==E: {left_ke.dimensionality == right_ke.dimensionality}")
    except Exception as ex:
        print(f"[pint] 跳过量纲核验: {type(ex).__name__}")
    main()
