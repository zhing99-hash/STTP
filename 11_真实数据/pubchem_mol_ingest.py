# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""
Phase 8.A — PubChem 真实分子数据接入（分子层喂实）
==================================================
免费、无需密钥的数据源: PubChem PUG-REST (https://pubchem.ncbi.nlm.nih.gov/rest/pug)
为骨架现有分子(去重 14 个真实物种)拉取真实: 分子式 / 分子量 / 标准 SMILES / IUPAC / CID，
- 新建真实分子节点 PC:mol:<CID>（带真实属性）
- same_as 桥接到现有种子节点(MO:/BC:/IC:/OM:*)
- 由真实分子式派生 composed_of 边 → 连到 Phase 8 真实元素节点 EK:el:<SYM>
产出: 11_真实数据/pubchem_mol_raw.json (既有权威 raw 格式)
设计同 Phase 8 元素层: 真实源 + same_as 桥接 + 真实组合关系。
"""
import json
import os
import re
import sys
import time
import io
import urllib.request
import urllib.error

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "pubchem_mol_raw.json")

# 查询名 -> 现有种子节点 id 列表（去重后的真实物种）
MOL_MAP = [
    ("water",            ["MO:h2o", "BC:mo:h2o", "IC:mo:h2o"]),
    ("carbon dioxide",   ["MO:co2", "BC:mo:co2"]),
    ("methane",          ["MO:ch4", "OM:mo:methane"]),
    ("dioxygen",         ["MO:o2", "BC:mo:o2", "IC:mo:o2"]),
    ("ethane",           ["OM:mo:ethane"]),
    ("ethylene",         ["OM:mo:ethylene"]),
    ("benzene",          ["OM:mo:benzene"]),
    ("ethanol",          ["OM:mo:ethanol"]),
    ("sodium chloride",  ["IC:mo:nacl"]),
    ("hydrogen chloride",["IC:mo:hcl"]),
    ("chlorine",         ["IC:mo:cl2"]),
    ("glucose",          ["BC:mo:glucose"]),
    ("ATP",              ["BC:mo:atp"]),
    ("glycine",          ["BC:mo:amino_acid"]),
]

ELEM_RE = re.compile(r"([A-Z][a-z]?)(\d*)")


def parse_formula(formula: str):
    """CO2 -> {C:1, O:2}; C10H16N5O13P3 -> {C:10,H:16,N:5,O:13,P:3}

    先剥离电荷后缀：`CHO2-` / `Cr2O7-2` / `Al+3`。**不可**用 `\d*[+-]\d*$` 一把剥 ——
    其开头的 `\d*` 会吞掉末尾元素下标（`CHO2-` -> `CHO`，O 退化为 1）。
    """
    s = str(formula or "").strip()
    s = re.sub(r"\^\d*[+\-\u2212]+\d*$", "", s)
    s = re.sub(r"[+\-\u2212]\d*$", "", s)
    comp = {}
    for sym, num in ELEM_RE.findall(s):
        n = int(num) if num else 1
        comp[sym] = comp.get(sym, 0) + n
    return comp


def pubchem_props(name: str):
    url = ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
           + urllib.parse.quote(name)
           + "/property/MolecularFormula,MolecularWeight,CanonicalSMILES,IsomericSMILES,InChIKey,IUPACName/JSON")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.loads(r.read().decode("utf-8"))
    props = data["PropertyTable"]["Properties"][0]
    return props


import urllib.parse


def main():
    nodes, edges = [], []
    stats = []
    for name, seeds in MOL_MAP:
        try:
            p = pubchem_props(name)
        except Exception as e:
            print(f"[WARN] {name} 拉取失败: {e}")
            continue
        cid = p["CID"]
        formula = p.get("MolecularFormula", "")
        mw = p.get("MolecularWeight", "")
        smi = p.get("CanonicalSMILES") or p.get("IsomericSMILES") or ""
        iupac = p.get("IUPACName", name)
        comp = parse_formula(formula)
        pcid = f"PC:mol:{cid}"
        nodes.append({
            "id": pcid,
            "labels": ["Entity", "Molecule"],
            "props": {
                "domain": "chem.molecule", "ntype": "molecule", "source": "PubChem",
                "name": iupac, "common_name": name, "formula": formula,
                "molecular_weight": float(mw) if mw else None,
                "canonical_smiles": smi, "pubchem_cid": cid,
                "composition": comp,
            },
        })
        # 桥接现有种子
        for s in seeds:
            edges.append({
                "id": f"same_as:{pcid}->{s}", "source": pcid, "target": s,
                "type": "same_as", "kind": "pubchem_bridge",
                "props": {"confidence": 0.95, "explicit_or_inferred": "inferred",
                          "source": "PubChem", "alignment": "name_match"},
            })
        # 真实组成 -> Phase 8 元素节点
        for sym, cnt in comp.items():
            ek = f"EK:el:{sym}"
            edges.append({
                "id": f"composed_of:{pcid}->{ek}:{cnt}", "source": pcid, "target": ek,
                "type": "composed_of", "kind": "pubchem_composition",
                "props": {"count": cnt, "confidence": 0.98,
                          "explicit_or_inferred": "explicit", "source": "PubChem",
                          "from_formula": formula},
            })
        stats.append((name, formula, mw, smi, comp, len(seeds)))
        print(f"[OK] {name:16} CID={cid} {formula} MW={mw} bridges={len(seeds)} elements={list(comp)}")
        time.sleep(0.25)

    raw = {"nodes": nodes, "edges": edges}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print(f"\n[OUT] {OUT}: 节点 {len(nodes)} / 边 {len(edges)} (same_as {sum(1 for e in edges if e['type']=='same_as')}, composed_of {sum(1 for e in edges if e['type']=='composed_of')})")


if __name__ == "__main__":
    main()
