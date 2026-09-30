# -*- coding: utf-8 -*-
"""
ReactionAtlas 适配器（科研级化学数据源）

ReactionAtlas 是 PostgreSQL 关系型反应数据库（约 26GB），每条反应含：
  - reaction_id / reaction_smiles
  - 温度 / 溶剂 / 催化剂 / 产率等条件
  - 反应物 / 产物 / 催化剂组件

本适配器：
  - 定义标准 schema（与 ETL 管道兼容）
  - 默认用 sqlite3（stdlib）内存库演示（沙箱无 PostgreSQL 服务）
  - 生产态可切到真实 PostgreSQL（改 connect() 即可）

字段映射（真实 ReactionAtlas → 本项目 Schema）：见 README.md
"""
from __future__ import annotations
import sqlite3
from typing import Any, Dict, List, Optional


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS reactions (
    reaction_id   TEXT PRIMARY KEY,
    reaction_smiles TEXT,
    temp_c        REAL,
    solvent       TEXT,
    catalyst      TEXT,
    yield_pct     REAL
);
CREATE TABLE IF NOT EXISTS components (
    reaction_id   TEXT,
    role          TEXT,   -- reactant / product / catalyst
    species_id    TEXT,
    stoichiometry REAL
);
"""


class ReactionAtlasAdapter:
    """连接 ReactionAtlas（演示用 sqlite3，生产用 PostgreSQL）。"""

    SOURCE = "RA"

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA_SQL)

    # ---------- 写 ----------
    def load_reaction(self, rec: Dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO reactions VALUES (?,?,?,?,?,?)",
            (rec["reaction_id"], rec.get("reaction_smiles", ""),
             rec.get("temp_c"), rec.get("solvent"), rec.get("catalyst"), rec.get("yield_pct")),
        )
        for role in ("reactants", "products", "catalysts"):
            for sp in rec.get(role, []) or []:
                self.conn.execute(
                    "INSERT INTO components VALUES (?,?,?,?)",
                    (rec["reaction_id"], role[:-1], sp.get("id"), sp.get("stoich", 1.0)),
                )
        self.conn.commit()

    # ---------- 读 ----------
    def query_reaction(self, reaction_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM reactions WHERE reaction_id=?", (reaction_id,)
        ).fetchone()
        if not row:
            return None
        comps = self.conn.execute(
            "SELECT role, species_id, stoichiometry FROM components WHERE reaction_id=?",
            (reaction_id,),
        ).fetchall()
        return {
            "reaction_id": row["reaction_id"],
            "reaction_smiles": row["reaction_smiles"],
            "temp_c": row["temp_c"],
            "solvent": row["solvent"],
            "catalyst": row["catalyst"],
            "yield_pct": row["yield_pct"],
            "components": [dict(c) for c in comps],
        }

    def reaction_components(self, reaction_id: str, role: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM components WHERE reaction_id=?"
        args = [reaction_id]
        if role:
            sql += " AND role=?"
            args.append(role)
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def close(self) -> None:
        self.conn.close()


# ---------- 合成演示 ----------
def synthetic_reactionatlas_sample(n: int = 25, seed: int = 0) -> List[Dict[str, Any]]:
    import random
    random.seed(seed)
    templ = [
        ("O=C=O.[H]O[H]>>OC(=O)O", "water", "none", [("CO2",),("H2O",)], [("H2CO3",)], []),
        ("C.O>>C=O", "air", "Pt", [("CH4",),("O2",)], [("CO2",)], []),
        ("HCl.NaOH>>NaCl.O", "water", "none", [("HCl",),("NaOH",)], [("NaCl",)], []),
    ]
    out = []
    for i in range(n):
        t = templ[i % len(templ)]
        out.append({
            "reaction_id": f"RA{i:05d}",
            "reaction_smiles": t[0],
            "temp_c": round(random.uniform(20, 350), 1),
            "solvent": t[1],
            "catalyst": t[2],
            "yield_pct": round(random.uniform(40, 99), 1),
            "reactants": [{"id": s[0], "stoich": 1.0} for s in t[3]],
            "products": [{"id": s[0], "stoich": 1.0} for s in t[4]],
            "catalysts": [{"id": s[0], "stoich": 0.0} for s in t[5]],
        })
    return out


if __name__ == "__main__":
    ra = ReactionAtlasAdapter(":memory:")
    for rec in synthetic_reactionatlas_sample(25):
        ra.load_reaction(rec)
    q = ra.query_reaction("RA00000")
    print(f"[OK] ReactionAtlas 演示: 写入 25 反应, 查询 RA00000 -> {q['reaction_smiles']}")
    comps = ra.reaction_components("RA00000", role="reactant")
    print(f"     反应物组件: {[c['species_id'] for c in comps]}")
    ra.close()
    print("[END] exit=0")
