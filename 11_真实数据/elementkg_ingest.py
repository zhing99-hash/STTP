# -*- coding: utf-8 -*-
"""
Phase 8 — ElementKG 2.0（真实化学知识图谱）接入适配器
=====================================================
数据源: GuanRuiYun/ElementKG 的 elementkg.owl（4MB，RDF/OWL），
        真实"元素-官能团-分子"知识图谱（Nature MI 2023, KANO）。
真实内容:
  - 118 个化学元素个体，每个含 18 项真实字面属性
    (原子序数/名称/原子量/密度/熔点/沸点/电负性/物态/硬度/电导率/电离能/比热/原子半径/丰度/发现年份/电子排布…)
  - 93 个对象属性把元素连到同周期/同族/同物态的其他元素（ElementKG 的关系层）
产出: 11_真实数据/elementkg_raw.json （既有权威 raw 格式，可直接喂 graph_export / load_neo4j）

设计:
  - 元素 → Element 节点 (id=EK:el:<SYMBOL>)，真实字面属性全量进 props.attrs
  - same_as 桥接现有骨架 EL:* 节点 (C/H/O/Na/Cl/Fe)，喂实骨架
  - 同周期/同族/同物态 → same_period / same_family / same_state 真实关系边（去重）
  - 数值相似属性 (hasWeight*/hasRadius* 等) 过密，MVP 跳过（留作后续）
"""
import json
import os
from collections import defaultdict
from rdflib import Graph, RDF, OWL, Namespace, Literal

# 权威元素参考表（同一目录），用于补全 OWL 缺失的字段
from element_reference import by_symbol as _el_by_symbol, BY_SYMBOL as _EL_BY_SYMBOL

HERE = os.path.dirname(os.path.abspath(__file__))
OWL_PATH = os.path.join(HERE, "elementkg.owl")
OUT = os.path.join(HERE, "elementkg_raw.json")
EK = Namespace("http://www.semanticweb.org/ElementKG#")

# 字面属性 → 干净字段名
LIT_MAP = {
    "hasName": "name", "hasAtomic": "atomic_number", "hasState": "state",
    "hasIonization": "ionization_energy", "hasHeat": "heat_capacity",
    "hasAbundance": "abundance", "hasElectronAffinity": "electron_affinity",
    "hasWeight": "atomic_weight", "hasBoilingPoint": "boiling_point",
    "hasMeltingPoint": "melting_point", "hasModulus": "bulk_modulus",
    "hasDensity": "density", "hasHardness": "hardness",
    "hasConductivity": "conductivity", "hasDiscoveredIn": "discovered_year",
    "hasEnergyLevels": "electron_config", "hasRadius": "atomic_radius",
    "hasElectronegativity": "electronegativity", "hasBondType": "bond_type",
}


def ln(s):
    s = str(s)
    return s.rsplit('#', 1)[-1].rsplit('/', 1)[-1]


def is_element(s, types):
    for t in types:
        if t == 'element':
            return True
        low = t.lower()
        if any(k in low for k in ['metal', 'gasse', 'nonmetal', 'earth', 'loid', 'actinite', 'lanthan']):
            return True
    return False


def family_key(sym):
    """IUPAC 族归属键：有族号用族号，f 区用 series。

    ElementKG 的 `hasFamily*` 把「3 族(Sc/Y/La/Ac) + 镧系(Ce–Lu) + 锕系(Th–Lr)」
    并成一个 32 元"族"，与 IUPAC 18 族口径不符——故 family 边一律按权威表校正。
    """
    r = _EL_BY_SYMBOL.get(sym) if sym else None
    if not r:
        return None
    if r["group"] is not None:
        return ("g", r["group"])
    if r["series"]:
        return ("series", r["series"])
    return None


def period_of(sym):
    r = _EL_BY_SYMBOL.get(sym) if sym else None
    return r["period"] if r else None


def main():
    g = Graph()
    g.parse(OWL_PATH, format="xml")
    print(f"[INGEST] 解析 {OWL_PATH}: {len(g)} 三元组")

    inds = list(g.subjects(RDF.type, OWL.NamedIndividual))
    types = {s: set(ln(o) for o in g.objects(s, RDF.type) if o != OWL.NamedIndividual) for s in inds}
    elements = [s for s in inds if is_element(s, types[s])]
    print(f"[INGEST] 元素个体: {len(elements)}")

    nodes, edges = [], []
    # 元素符号 -> EK id，便于关系边 & 桥接
    sym2ek = {}
    # 关系边去重
    rel_pairs = defaultdict(set)  # (reltype) -> set(frozenset({a,b}))

    for s in elements:
        sym = ln(s)                      # 元素符号, 如 Sc / C / Fe
        ek_id = f"EK:el:{sym}"
        sym2ek[sym] = ek_id
        props = {"domain": "chem.element", "symbol": sym, "ntype": "element", "source": "ElementKG2.0",
                 "families": sorted(types[s])}
        # 字面属性
        for p, o in g.predicate_objects(s):
            if p == RDF.type:
                continue
            pn = ln(p)
            if isinstance(o, Literal):
                if pn in LIT_MAP:
                    val = o
                    try:
                        if pn in ("atomic_number", "discovered_year"):
                            val = int(float(o))
                        elif pn in ("abundance",):
                            val = str(o)
                        else:
                            f = float(o); val = f
                    except Exception:
                        val = str(o)
                    props[LIT_MAP[pn]] = val
            else:
                # 对象属性 → 关系层（仅连接真实存在的元素，跳过自环/幽灵）
                pn = ln(p)
                tgt = sym2ek.get(ln(o))
                if tgt and tgt != ek_id:
                    if pn.startswith("hasPeriod"):
                        # 周期号统一由权威表给出（见下方 ref 段）；此处仅收敛边
                        if period_of(sym) is not None and period_of(sym) == period_of(ln(o)):
                            rel_pairs["same_period"].add(frozenset({ek_id, tgt}))
                    elif pn.startswith("hasFamily"):
                        # 按 IUPAC 18 族校正：跨族（含 f 区与 3 族混淆）不入边
                        ku, kv = family_key(sym), family_key(ln(o))
                        if ku is not None and ku == kv:
                            rel_pairs["same_family"].add(frozenset({ek_id, tgt}))
                    # hasState* 过密(近全连通团)，改为节点属性 state，不入边

        # 权威参考表对齐：OWL 对超重元素（Cn/Ds/Fl/Lv/Mc/Mt/Nh/Og/Rg/Ts）缺
        # hasWeight / hasAtomic / hasName 字面量；且 hasAtomic 对个别元素是
        # 「族号」而非原子序数（典型如 Oxygen 记为 16）——故原子序数一律以
        # 权威表为准，并顺带补全 period / group / block / series。
        ref = _el_by_symbol(sym)
        if ref:
            filled = []
            for key, val in (("atomic_weight", ref["atomic_weight"]),
                             ("name", ref["name"])):
                if props.get(key) in (None, ""):
                    props[key] = val
                    filled.append(key)
            if props.get("atomic_number") != ref["atomic_number"]:
                if props.get("atomic_number") is not None and "ek_atomic_raw" not in props:
                    props["ek_atomic_raw"] = props["atomic_number"]
                props["atomic_number"] = ref["atomic_number"]
                filled.append("atomic_number")
            # 周期表位置（IUPAC）：period / group / block / series
            props["period"] = ref["period"]
            if ref["group"] is not None:
                props["group"] = ref["group"]
            else:
                props.pop("group", None)
            props["block"] = ref["block"]
            if ref["series"]:
                props["series"] = ref["series"]
            else:
                props.pop("series", None)
            props["periodic_source"] = "element_reference_iupac"
            if filled:
                props["reference_filled"] = ",".join(filled)
        nodes.append({"id": ek_id, "labels": ["Entity", "Element"], "props": props})

    # same_as 桥接现有骨架 EL:* 节点（按符号大小写不敏感）
    bridges = [("C", "EL:c"), ("H", "EL:h"), ("O", "EL:o"),
               ("Na", "EL:na"), ("Cl", "EL:cl"), ("Fe", "EL:fe"), ("N", "EL:n")]
    bridge_count = 0
    for sym, target in bridges:
        if sym in sym2ek and target:
            edges.append({
                "id": f"same_as:{sym2ek[sym]}->{target}",
                "source": sym2ek[sym], "target": target, "type": "same_as",
                "kind": "ek_bridge",
                "props": {"confidence": 0.95, "explicit_or_inferred": "inferred",
                          "source": "ElementKG2.0", "alignment": "symbol_match"},
            })
            bridge_count += 1
    print(f"[INGEST] same_as 桥接骨架 EL:* : {bridge_count} 条")

    # 关系边（去重）
    rel_kind = {"same_period": "ek_same_period", "same_family": "ek_same_family", "same_state": "ek_same_state"}
    rel_conf = {"same_period": 0.85, "same_family": 0.85, "same_state": 0.8}
    rel_total = 0
    for reltype, pairs in rel_pairs.items():
        for pair in pairs:
            a, b = tuple(pair)
            if a == b:
                continue
            edges.append({
                "id": f"{reltype}:{a}::{b}",
                "source": a, "target": b, "type": reltype,
                "kind": rel_kind[reltype],
                "props": {"confidence": rel_conf[reltype], "explicit_or_inferred": "inferred",
                          "source": "ElementKG2.0"},
            })
            rel_total += 1
    print(f"[INGEST] 关系边(同周期/同族/同物态): {rel_total} 条")

    raw = {"nodes": nodes, "edges": edges}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print(f"[OK] 写出 {OUT}: 节点 {len(nodes)} / 边 {len(edges)}")


if __name__ == "__main__":
    main()
