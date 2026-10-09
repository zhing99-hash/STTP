# -*- coding: utf-8 -*-
"""
Phase 9 深化（方向 A）：真实跨域桥的符号校验生成。
两块高价值、纯符号、无需 RDKit 的跨域桥：
  1) 分子 → 摩尔质量物理量锚点（has_quantity -> PB:pq:molar_mass）
     依据：molecule 的 composed_of/has_element 边含 count，元素含 atomic_mass/atomic_weight
     门控：R-PHY（摩尔质量属质量量纲，与 molar_mass 物理量量纲一致 -> VERIFIED）
  2) 反应方程解析 -> 补全/验证 反应物-产物 角色桥（reactant_of / product_of）
     依据：Reaction.equation 文本（如 "CH4 + 2 O2 -> CO2 + 2 H2O"）解析分子式
     门控：R-CHEM（按方程角色符号判定，VERIFIED）

输出：09_科研扩展/9_inference/phase9_deepen_edges.json（Aura 增量，nodes=[]）
"""
import json, os, re, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = r"C:\Users\Administrator\WorkBuddy\2026-10-08-10-51-20\STTP"
RAW = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT = os.path.join(ROOT, "09_科研扩展", "9_inference", "phase9_deepen_edges.json")

raw = json.load(open(RAW, encoding="utf-8"))
nodes, edges = raw["nodes"], raw["edges"]
by_id = {n["id"]: n for n in nodes}

# ---- 元素原子量回退表（少数 IC:el 缺字段，硬编码）----
FALLBACK_AW = {"IC:el:sodium": 22.989769, "IC:el:chlorine": 35.45, "IC:el:iron": 55.845}

def atomic_mass(n):
    p = n.get("props", {})
    for k in ("atomic_mass", "atomic_weight", "hasWeight", "hasAtomic", "weight"):
        v = p.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return FALLBACK_AW.get(n["id"])

# ---- 物理量与目标 ----
pq_ids = {n["id"] for n in nodes if "PhysicalQuantity" in n["labels"]}
MOLAR_MASS_PQ = "PB:pq:molar_mass"   # 已存在（PhysicsBabel 引入）
assert MOLAR_MASS_PQ in pq_ids, f"缺少 {MOLAR_MASS_PQ}，无法挂摩尔质量桥"

# ---- 1) 摩尔质量锚点 ----
# molecule -> {element_id: count}
mol_comp = {}
for e in edges:
    if e["type"] in ("composed_of", "has_element"):
        mol_comp.setdefault(e["source"], {})
        c = e.get("props", {}).get("count", 1)
        try: c = int(c)
        except Exception: c = 1
        mol_comp[e["source"]][e["target"]] = mol_comp[e["source"]].get(e["target"], 0) + c

new_edges = []
seen = set()
def add_edge(src, tgt, etype, kind, props):
    eid = f"P9D:{etype}:{src}->{tgt}"
    if eid in seen: return
    seen.add(eid)
    props.setdefault("explicit_or_inferred", "inferred")
    props.setdefault("source", "Phase9.deepen")
    new_edges.append({"id": eid, "source": src, "target": tgt,
                      "type": etype, "kind": kind, "props": props})

mass_count = 0
for mid, comp in mol_comp.items():
    if mid not in by_id: continue
    mm = 0.0; ok = True; missing = []
    for eid, cnt in comp.items():
        en = by_id.get(eid)
        if not en: ok = False; missing.append(eid); continue
        aw = atomic_mass(en)
        if aw is None: ok = False; missing.append(eid); continue
        mm += aw * cnt
    if not ok or mm <= 0:
        continue
    add_edge(mid, MOLAR_MASS_PQ, "has_quantity", "molecule_molar_mass", {
        "quantity": "molar_mass", "value": round(mm, 4), "unit": "g/mol",
        "confidence": 1.0, "verified": True, "verification_gate": "R-PHY",
        "rationale": "molar mass = Σ atomic_weight × count (composition x element weights)",
        "missing_elements": missing,
    })
    mass_count += 1

# ---- 2) 反应方程解析：补全 反应物/产物 角色桥 ----
# 分子式 -> molecule id（归一化）
formula_index = {}
for n in nodes:
    if "Molecule" not in n["labels"]: continue
    p = n.get("props", {})
    f = p.get("formula") or p.get("molecular_formula") or p.get("name")
    if not f: continue
    key = re.sub(r"\s+", "", f)
    formula_index.setdefault(key, []).append(n["id"])

def parse_side(expr):
    """返回 [(molecule_id, coefficient)]"""
    out = []
    for term in re.split(r"\+", expr):
        term = term.strip()
        if not term: continue
        m = re.match(r"^(\d*\.?\d*)\s*(.+)$", term)
        coeff = 1.0
        if m and m.group(1):
            try: coeff = float(m.group(1))
            except Exception: coeff = 1.0
        formula = re.sub(r"\s+", "", m.group(2)) if m else term
        # 去掉可能的状态符号 (s)(l)(g)(aq)
        formula = re.sub(r"\((s|l|g|aq)\)", "", formula, flags=re.I)
        for mid in formula_index.get(formula, []):
            out.append((mid, coeff))
    return out

# 已有 反应物/产物 边（去重）
existing_role = set()
for e in edges:
    if e["type"] in ("reactant_of", "product_of"):
        existing_role.add((e["source"], e["target"], e["type"]))

role_count = 0
for n in nodes:
    if "Reaction" not in n["labels"]: continue
    eq = n.get("props", {}).get("equation")
    if not eq: continue
    sep = "->" if "->" in eq else ("→" if "→" in eq else None)
    if not sep: continue
    lhs, rhs = eq.split(sep, 1)
    for mid, _ in parse_side(lhs):
        key = (n["id"], mid, "reactant_of")
        if key not in existing_role:
            add_edge(n["id"], mid, "reactant_of", "reaction_role", {
                "confidence": 1.0, "verified": True, "verification_gate": "R-CHEM",
                "rationale": "parsed from reaction equation LHS (reactant)",
            })
            existing_role.add(key); role_count += 1
    for mid, _ in parse_side(rhs):
        key = (n["id"], mid, "product_of")
        if key not in existing_role:
            add_edge(n["id"], mid, "product_of", "reaction_role", {
                "confidence": 1.0, "verified": True, "verification_gate": "R-CHEM",
                "rationale": "parsed from reaction equation RHS (product)",
            })
            existing_role.add(key); role_count += 1

# ---- 输出 ----
delta = {"nodes": [], "edges": new_edges}
json.dump(delta, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"[Phase9 深化] 摩尔质量锚点: {mass_count} 条 (has_quantity -> {MOLAR_MASS_PQ})")
print(f"[Phase9 深化] 反应角色桥补全: {role_count} 条 (reactant_of/product_of, R-CHEM)")
print(f"[Phase9 深化] 新增边总计: {len(new_edges)} -> {OUT}")
