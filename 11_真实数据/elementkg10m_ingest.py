# -*- coding: utf-8 -*-
"""
Phase 8.B — ElementKG 2.0 全量 10M CSV 接入（真实化学核心子集）
=================================================================
数据: 公共数据集/10m_elementkg_release.csv  (10.15M 行, 818MB, 列: head_type,head_value,relation,tail_type,tail_value)
约束: Aura 免费实例 ~5万节点上限 -> 只抽有结构的真实化学子集:
  1) 全部 118 元素 (element 行) -> 桥接 Phase 8 EK:el:*
  2) 有界真实反应子图: 取 N 个 Reaction, 沿 PRODUCES/PARTICIPATES_IN/USED_IN + IS_MOLECULE
     解析出真实分子(Molecule, 带 PUBCHEM 全属性) + 官能团 + 元素组成
  3) 骨架分子(PC:mol:*) 按分子式 same_as 挂到真实分子
两遍流式扫描(避免内存爆): pass1 建反应子图索引, pass2 抽取属性写 raw JSON。
"""
import sys, io, csv, json, os
from collections import defaultdict, Counter
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
csv.field_size_limit(10**9)

SRC = r"C:\Users\Administrator\WorkBuddy\2026-10-08-10-51-20\STTP\公共数据集\10m_elementkg_release.csv"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "elementkg10m_raw.json")
N_REACTIONS = 800

# 骨架分子(Phase 8.A) 分子式 -> PC id, 用于 same_as 桥接
SKELETON_FORMULA = {
    "H2O": "PC:mol:962", "CO2": "PC:mol:280", "CH4": "PC:mol:297", "O2": "PC:mol:977",
    "C2H6": "PC:mol:6324", "C2H4": "PC:mol:6325", "C6H6": "PC:mol:241", "C2H6O": "PC:mol:702",
    "NACL": "PC:mol:5234", "CLH": "PC:mol:313", "CL2": "PC:mol:24526",
    "C6H12O6": "PC:mol:5793", "C10H16N5O13P3": "PC:mol:5957", "C2H5NO2": "PC:mol:750",
}

# 权威元素参考表（同一目录）：符号 / 名称 / 原子序数 / 标准原子量
from element_reference import BY_Z as _EL_BY_Z, by_symbol as _el_by_symbol

# 原子序数 -> 符号（兜底用；注意 ElementKG 的 HASATOMIC 对个别元素给的是族号而非序数）
ATOMIC_TO_SYMBOL = {z: r["symbol"] for z, r in _EL_BY_Z.items()}



def pass1():
    rxn_entities = defaultdict(lambda: {"reactants": [], "products": [], "reagents": []})
    entity2mol = {}
    rxn_order = []
    seen_rxn = set()
    with open(SRC, encoding="utf-8", newline="") as f:
        r = csv.reader(f); next(r)
        for row in r:
            if len(row) != 5:
                continue
            h, hv, rel, tt, tv = row
            if h == "Reaction" and rel == "PRODUCES" and tt == "Product":
                rxn_entities[hv]["products"].append(tv)
            elif h == "Reactant" and rel == "PARTICIPATES_IN" and tt == "Reaction":
                rxn_entities[tv]["reactants"].append(hv)
            elif h == "Reagent" and rel == "USED_IN" and tt == "Reaction":
                rxn_entities[tv]["reagents"].append(hv)
            elif h in ("Reactant", "Product", "Reagent") and rel == "IS_MOLECULE" and tt == "Molecule":
                entity2mol[hv] = tv
            elif h == "Reaction" and hv not in seen_rxn:
                seen_rxn.add(hv); rxn_order.append(hv)
    # 选前 N 个**可解析出分子**的 reaction（顺序无关）
    # ⚠ 原实现只要求「有参与物实体引用」，但边要靠 `entity2mol` 才能连出：
    #   参与物只有 `SMILES_IS` 而无 `IS_MOLECULE`（如 product_9991）时，
    #   反应解析不出任何分子 → 节点 **deg=0 成孤立死节点**（实测 800 个中 42 个如此）。
    #   故把「可解析分子数 ≥ 1」提升为硬约束，从源头杜绝零边反应入图。
    selected = []
    skipped_no_mol = 0
    for rxn in rxn_order:
        ent = rxn_entities.get(rxn)
        if not ent:
            continue
        ents = ent["reactants"] + ent["products"] + ent["reagents"]
        if not ents:
            continue
        if not any(e in entity2mol for e in ents):
            skipped_no_mol += 1
            continue
        selected.append(rxn)
        if len(selected) >= N_REACTIONS:
            break
    print(f"[pass1] 跳过 {skipped_no_mol} 个「参与物无 IS_MOLECULE 映射」的 reaction"
          f"（否则会成孤立节点）")
    keep = set(selected)
    for k in list(rxn_entities.keys()):
        if k not in keep:
            del rxn_entities[k]
    need_mols = set()
    for rxn in selected:
        for e in rxn_entities[rxn]["reactants"] + rxn_entities[rxn]["products"] + rxn_entities[rxn]["reagents"]:
            if e in entity2mol:
                need_mols.add(entity2mol[e])
    print(f"[pass1] 选定 reaction: {len(selected)}, 解析出分子: {len(need_mols)}")
    return set(selected), rxn_entities, entity2mol, need_mols


def pass2(selected, rxn_entities, entity2mol, need_mols):
    nodes, edges = [], []
    mols = {}            # molecule_id -> props
    mol_fg = defaultdict(list)
    fg_elem = defaultdict(list)
    need_fg = set()
    elem_props = {}      # element_id -> props
    rxn_props = {}
    rxn_lit = {"TEMPERATURE_IS": "temperature", "YIELD_IS": "yield",
               "REACTION_MAPPED_IS": "mapped_smiles", "NAME_IS": "name"}

    with open(SRC, encoding="utf-8", newline="") as f:
        r = csv.reader(f); next(r)
        for row in r:
            if len(row) != 5:
                continue
            h, hv, rel, tt, tv = row
            if h == "Molecule" and hv in need_mols and tt == "literal":
                mols.setdefault(hv, {}); _set_mol_prop(mols[hv], rel, tv)
            elif h == "Molecule" and rel == "HAS_FUNCTIONALGROUP" and hv in need_mols and tt == "functionalGroup":
                mol_fg[hv].append(tv); need_fg.add(tv)
            elif h == "functionalGroup" and rel == "HAS_ELEMENT" and hv in need_fg and tt == "element":
                fg_elem[hv].append(tv)
            elif h == "Reaction" and hv in selected and rel in rxn_lit and tt == "literal":
                rxn_props.setdefault(hv, {})[rxn_lit[rel]] = tv[:400] if rel == "REACTION_MAPPED_IS" else tv
            elif h == "element" and tt == "literal":
                elem_props.setdefault(hv, {}); _set_elem_prop(elem_props[hv], rel, tv)

    # 符号解析：优先用权威 NAME_IS（_set_elem_prop 已写入）；
    # 仅当源数据没有 NAME_IS 时才回退到原子序数反查，并显式告警。
    for eid, p in elem_props.items():
        if not p.get("symbol"):
            if "atomic" in p:
                p["symbol"] = ATOMIC_TO_SYMBOL.get(p["atomic"])
                print(f"[WARN] 元素 {eid} 缺 NAME_IS，回退按 HASATOMIC={p['atomic']} "
                      f"推断符号 {p.get('symbol')!r}（该字段可能是族号，须人工复核）")

    # 节点
    for mid, p in mols.items():
        nodes.append({"id": f"EK2:mol:{mid}", "labels": ["Entity", "Molecule"],
                      "props": {"domain": "chem.molecule", "ntype": "molecule", "source": "ElementKG2.0",
                                "name": p.get("name"), "formula": p.get("formula"),
                                "molecular_weight": p.get("mw"), "canonical_smiles": p.get("smiles"),
                                "inchikey": p.get("inchikey"), "exact_mass": p.get("exact_mass"),
                                "ek_molecule_id": mid}})
    for rxn, p in rxn_props.items():
        nodes.append({"id": f"EK2:rxn:{rxn}", "labels": ["Entity", "Reaction"],
                      "props": {"domain": "chem.reaction", "ntype": "reaction", "source": "ElementKG2.0",
                                "name": p.get("name"), "temperature": p.get("temperature"),
                                "yield": p.get("yield"), "mapped_smiles": p.get("mapped_smiles"),
                                "ek_reaction_id": rxn}})
    for fg in need_fg:
        nodes.append({"id": f"EK2:fg:{fg}", "labels": ["Entity", "FunctionalGroup"],
                      "props": {"domain": "chem.molecule", "ntype": "functional_group",
                                "source": "ElementKG2.0", "name": fg}})
    for eid, p in elem_props.items():
        sym = p.get("symbol")
        if not sym:
            continue
        ref = _el_by_symbol(sym)
        # ElementKG 的 HASATOMIC 对个别元素给的是「族号」而非原子序数
        # （如 Oxygen 记 16 = 16 族）。故原子序数/名称优先取权威参考表，
        # 源值原样保留在 ek_atomic_raw / ek_name_raw 以便追溯。
        z = ref["atomic_number"] if ref else p.get("atomic")
        w = p.get("weight")
        if w in (None, 0) and ref:
            w = ref["atomic_weight"]
        nodes.append({"id": f"EK2:el:{sym}", "labels": ["Entity", "Element"],
                      "props": {"domain": "chem.element", "ntype": "element", "source": "ElementKG2.0",
                                "name": (ref["name"] if ref else None) or p.get("name"),
                                "atomic_number": z,
                                "atomic_weight": w, "weight": w, "symbol": sym,
                                "ek_element_id": eid,
                                "ek_atomic_raw": p.get("atomic"),
                                "ek_name_raw": p.get("name")}})
        edges.append({"id": f"same_as:EK2:el:{sym}->EK:el:{sym}", "source": f"EK2:el:{sym}",
                      "target": f"EK:el:{sym}", "type": "same_as", "kind": "elementkg_bridge",
                      "props": {"confidence": 0.95, "explicit_or_inferred": "inferred",
                                "source": "ElementKG2.0", "alignment": "atomic_number"}})

    # 反应-分子边 (reactant/product/reagent 折叠为 molecule 角色)
    role_type = {"reactants": "reactant_of", "products": "product_of", "reagents": "reagent_of"}
    for rxn, ent in rxn_entities.items():
        for role, etype in role_type.items():
            seen = set()
            for e in ent[role]:
                mid = entity2mol.get(e)
                if not mid:
                    continue
                key = (rxn, mid, etype)
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"id": f"{etype}:EK2:rxn:{rxn}->{mid}", "source": f"EK2:rxn:{rxn}",
                              "target": f"EK2:mol:{mid}", "type": etype, "kind": "real_reaction",
                              "props": {"confidence": 0.98, "explicit_or_inferred": "explicit",
                                        "source": "ElementKG2.0"}})
    # 分子-官能团 / 官能团-元素
    for mid, fgs in mol_fg.items():
        for fg in fgs:
            edges.append({"id": f"has_fg:EK2:mol:{mid}->{fg}", "source": f"EK2:mol:{mid}", "target": f"EK2:fg:{fg}",
                          "type": "has_functionalgroup", "kind": "real",
                          "props": {"confidence": 0.95, "explicit_or_inferred": "explicit", "source": "ElementKG2.0"}})
    for fg, elems in fg_elem.items():
        for eid in elems:
            sym = elem_props.get(eid, {}).get("symbol")
            if not sym:
                continue
            edges.append({"id": f"has_elem:EK2:fg:{fg}->EK2:el:{sym}", "source": f"EK2:fg:{fg}", "target": f"EK2:el:{sym}",
                          "type": "has_element", "kind": "real",
                          "props": {"confidence": 0.9, "explicit_or_inferred": "explicit", "source": "ElementKG2.0"}})
    # 骨架桥接
    for mid, p in mols.items():
        f = (p.get("formula") or "").replace(" ", "").upper()
        if f in SKELETON_FORMULA:
            pc = SKELETON_FORMULA[f]
            edges.append({"id": f"same_as:{pc}->EK2:mol:{mid}", "source": pc, "target": f"EK2:mol:{mid}",
                          "type": "same_as", "kind": "skeleton_bridge",
                          "props": {"confidence": 0.9, "explicit_or_inferred": "inferred",
                                    "source": "ElementKG2.0", "alignment": "formula"}})

    raw = {"nodes": nodes, "edges": edges}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print(f"[pass2] 节点 {len(nodes)} / 边 {len(edges)}")
    print("  节点类型:", dict(Counter(n["props"]["ntype"] for n in nodes)))
    print("  边类型:", dict(Counter(e["type"] for e in edges)))
    print(f"  骨架桥接(按分子式): {sum(1 for e in edges if e['kind']=='skeleton_bridge')}")


def _set_mol_prop(p, rel, tv):
    if rel == "PUBCHEM_MOLECULAR_FORMULA_IS":
        p["formula"] = tv
    elif rel == "PUBCHEM_MOLECULAR_WEIGHT_IS":
        try: p["mw"] = float(tv)
        except ValueError: pass
    elif rel == "PUBCHEM_CONNECTIVITY_SMILES_IS":
        p["smiles"] = tv
    elif rel == "PUBCHEM_IUPAC_INCHIKEY_IS":
        p["inchikey"] = tv
    elif rel == "PUBCHEM_EXACT_MASS_IS":
        try: p["exact_mass"] = float(tv)
        except ValueError: pass
    elif rel in ("PUBCHEM_IUPAC_NAME_IS", "PUBCHEM_IUPAC_TRADITIONAL_NAME_IS", "PUBCHEM_IUPAC_SYSTEMATIC_NAME_IS"):
        p.setdefault("name", tv)
    elif rel == "PUBCHEM_XLOGP3_AA_IS":
        try: p["logp"] = float(tv)
        except ValueError: pass


def _set_elem_prop(p, rel, tv):
    if rel == "NAME_IS":
        # ★ 权威符号字段。此前漏读，导致改用 HASATOMIC 反查：ElementKG 对 Oxygen
        #   的 HASATOMIC=16 实为「族号」(16 族)，反查得 "S"，与 Sulfur(E93) 撞 id，
        #   氧元素整条丢失。见 00_项目管理/下一步开发计划_20261008.md 缺陷 1。
        p["symbol"] = tv.strip()
    elif rel == "HASNAME":
        p["name"] = tv
    elif rel == "HASATOMIC":
        try: p["atomic"] = int(float(tv))
        except ValueError: pass
    elif rel == "HASWEIGHT":
        try: p["weight"] = float(tv)
        except ValueError: pass
    elif rel == "HASSTATE":
        p["state"] = tv
    elif rel == "HASELECTRONEGATIVITY":
        try: p["electronegativity"] = float(tv)
        except ValueError: pass
    elif rel == "HASABUNDANCE":
        try: p["abundance"] = float(tv)
        except ValueError: pass


if __name__ == "__main__":
    sel, rxn_ent, e2m, need = pass1()
    pass2(sel, rxn_ent, e2m, need)
