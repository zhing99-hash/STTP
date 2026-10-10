# -*- coding: utf-8 -*-
"""侦察 7：ALIAS 扩充的**影响模拟**（不写任何文件）。
把候选别名代入 dm.canon/dim_of，重算 T3/T6 的每条边，统计 撤/升/不可判定。
"""
import json, sys, os, collections
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
import dimension_table as dm

# ---- 候选别名（只收语义确定的展示名 → 真值表规范名）----
CAND = {
    "speed_of_light": "light_speed",
    "gravitational_constant": "grav_const",
    "amount_of_substance": "amount",
    "electric_charge": "charge",
    "electric_current": "current",
    "electric_field": "efield",
    "magnetic_field": "bfield",
    "vacuum_permittivity": "permittivity",
    "vacuum_permeability": "permeability",
    "electromotive_force": "voltage",
    "magnetic_flux": "mag_flux",
    "planck_constant": "planck",
    "reduced_planck_constant": "planck",
    "wavelength": "length",
    "proper_time": "time",
    "focal_length": "length",
    "object_distance": "length",
    "image_distance": "length",
    "radius": "length",
    "boltzmann_constant": "boltzmann",
    "statistical_entropy": "entropy",
    "lum_intensity": "luminous_intensity",
}
# 影子改造：不动真文件，注入后再跑
_orig_canon = dm.canon
def canon2(name):
    s = str(name)
    if s in CAND: return CAND[s]
    k = dm._display_key(s)
    if k in CAND: return CAND[k]
    return _orig_canon(name)
def dim_of2(name):
    return dm.load().get(canon2(name))

g = json.load(open(os.path.join(ROOT, "06_PoC", "etl", "normalized.json"), encoding="utf-8"))
N = {n["id"]: n for n in g["nodes"]}
def qn(i):
    p = (N.get(i) or {}).get("props") or {}
    return p.get("name") or i.split(":")[-1]

def sim(edges, label, use_new):
    dof = dim_of2 if use_new else dm.dim_of
    res = collections.Counter()
    detail = []
    for e in edges:
        lv = (e.get("props") or {}).get("verification_level")
        na, nb = qn(e["source"]), qn(e["target"])
        da, db = dof(na), dof(nb)
        if da is None or db is None:
            res["不可判定"] += 1
            if lv != "model_inferred": res["不可判定(非model)"] += 1
        elif dm._norm(da) == dm._norm(db):
            res["量纲相同(升)"] += 1
            if lv != "rule_checked": detail.append(("升", na, nb, lv))
        else:
            res["量纲不同(撤)"] += 1
            if lv != "unverified": detail.append(("撤", na, nb, lv))
    print("---- %s [%s] ----" % (label, "新ALIAS" if use_new else "现ALIAS"))
    for k, v in res.most_common(): print("   %-18s %d" % (k, v))
    return res, detail

t3 = [e for e in g["edges"] if e.get("type") == "dimensionally_consistent"]
o1, d1 = sim(t3, "T3 dimensionally_consistent", False)
n1, d1n = sim(t3, "T3 dimensionally_consistent", True)
print("   变动明细：")
for r in d1n: print("      %s %s <-> %s (原 %s)" % r)

print()
hu = [e for e in g["edges"] if e.get("type") == "has_unit"]
o2, _ = sim(hu, "T6 has_unit", False)
n2, _ = sim(hu, "T6 has_unit", True)
