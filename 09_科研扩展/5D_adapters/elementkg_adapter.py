# -*- coding: utf-8 -*-
"""
ElementKG 2.0 适配器（科研级化学数据源）

ElementKG 是以"反应超图"建模的化学知识图谱：
  - 一个 Reaction 节点包含多个 Reactant / Product / Catalyst（超边）
  - 数据格式为 JSON-LD，规模 >100 万反应 / >1000 万物质

本适配器负责：
  1. 解析 ElementKG JSON-LD 片段
  2. 归一化到本项目 Schema v0.1 的节点/边（Molecule / Reaction / reactant_of / product_of）
  3. 输出可直接被 ETL 管道消费的 (nodes, edges) 列表

真实数据（100+GB）沙箱下不去，本文件提供"接口 + 最小样本"；
真实 dump 下载与字段映射见 README.md。
"""
from __future__ import annotations
import json
from typing import Any, Dict, List


class ElementKGAdapter:
    """把 ElementKG JSON-LD 反应记录转为 Schema 节点/边。"""

    SOURCE = "EK"

    def __init__(self, schema_version: str = "0.1"):
        self.schema_version = schema_version

    # ---------- 单条反应归一化 ----------
    def normalize_reaction(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        """rec 形如：
        {
          "reaction_id": "R000123",
          "reaction_smiles": "O=C=O.[H]O[H]>>OC(=O)O",
          "reactants": [{"id":"CO2","smiles":"O=C=O","name":"carbon dioxide"}],
          "products":  [{"id":"H2CO3","smiles":"OC(=O)O","name":"carbonic acid"}],
          "catalysts": [],
          "conditions": {"temp":298.15,"solvent":"water","yield":0.95}
        }
        """
        rid = f"{self.SOURCE}:rxn:{rec['reaction_id']}"
        node = {
            "id": rid,
            "labels": ["Reaction"],
            "props": {
                "reaction_smiles": rec.get("reaction_smiles", ""),
                "conditions": rec.get("conditions", {}),
                "source": "ElementKG",
            },
        }
        edges: List[Dict[str, Any]] = []
        for role, etype in (("reactants", "reactant_of"),
                            ("products", "product_of"),
                            ("catalysts", "catalyst_of")):
            for sp in rec.get(role, []) or []:
                mid = f"{self.SOURCE}:mol:{sp['id']}"
                edges.append({
                    "source": mid,
                    "target": rid,
                    "type": etype,
                    "kind": "elementkg_extracted",
                    "confidence": 1.0,
                    "explicit_or_inferred": "explicit",
                    "data_source": "ElementKG",
                })
        return {"node": node, "edges": edges}

    # ---------- 批量 ----------
    def convert(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        seen_mol: set = set()
        rc = 0
        for rec in records:
            out = self.normalize_reaction(rec)
            nodes.append(out["node"])
            edges.extend(out["edges"])
            # 物质节点去重
            for e in out["edges"]:
                mid = e["source"]
                if mid not in seen_mol:
                    seen_mol.add(mid)
                    mol_id = mid.split(":", 2)[-1]
                    nodes.append({
                        "id": mid,
                        "labels": ["Molecule"],
                        "props": {"name": mol_id, "source": "ElementKG"},
                    })
            rc += 1
        return {
            "schema_version": self.schema_version,
            "source": "ElementKG",
            "reaction_count": rc,
            "molecule_count": len(seen_mol),
            "nodes": nodes,
            "edges": edges,
        }


# ---------- 合成样本生成 ----------
def synthetic_elementkg_sample(n: int = 30, seed: int = 0) -> List[Dict[str, Any]]:
    """生成 n 条合成反应（不依赖真实大库，演示 ETL 接口）。"""
    import random
    random.seed(seed)
    # 常见反应模板 (smiles, 反应物, 产物, 名称)
    templates = [
        ("O=C=O.[H]O[H]>>OC(=O)O", [("CO2","O=C=O","carbon dioxide"),("H2O","O","water")],
         [("H2CO3","OC(=O)O","carbonic acid")], "hydration"),
        ("C.O>>C=O", [("CH4","C","methane"),("O2","O=O","oxygen")],
         [("CO2","O=C=O","carbon dioxide")], "combustion"),
        ("HCl.NaOH>>NaCl.O", [("HCl","Cl","hydrogen chloride"),("NaOH","[Na+].[OH-]","sodium hydroxide")],
         [("NaCl","[Na+].[Cl-]","sodium chloride")], "neutralization"),
        ("H2.O2>>H2O", [("H2","[H][H]","hydrogen"),("O2","O=O","oxygen")],
         [("H2O","O","water")], "synthesis"),
        ("N2.3H2>>2NH3", [("N2","N#N","nitrogen"),("H2","[H][H]","hydrogen")],
         [("NH3","N","ammonia")], "haber"),
    ]
    records = []
    for i in range(n):
        t = templates[i % len(templates)]
        rid = f"SYN{i:05d}"
        records.append({
            "reaction_id": rid,
            "reaction_smiles": t[0],
            "reactants": [{"id": a, "smiles": b, "name": c} for a, b, c in t[1]],
            "products": [{"id": a, "smiles": b, "name": c} for a, b, c in t[2]],
            "catalysts": [],
            "conditions": {"temp": random.uniform(273, 400), "solvent": "water", "yield": round(random.uniform(0.5, 0.99), 2)},
        })
    return records


if __name__ == "__main__":
    recs = synthetic_elementkg_sample(30)
    out = ElementKGAdapter().convert(recs)
    print(f"[OK] ElementKG 合成样本: {out['reaction_count']} 反应 / {out['molecule_count']} 物质")
    print(f"     导出节点 {len(out['nodes'])} / 边 {len(out['edges'])}")
    print("[END] exit=0")
