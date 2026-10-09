# -*- coding: utf-8 -*-
import json, os, collections, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"C:\Users\Administrator\WorkBuddy\2026-10-08-10-51-20\STTP"
raw = json.load(open(os.path.join(ROOT,"06_PoC","etl","normalized.json"),encoding="utf-8"))
nodes = raw["nodes"]; edges = raw["edges"]
by_id = {n["id"]: n for n in nodes}
el_nodes=[n for n in nodes if "Element" in n["labels"]]
mol_nodes=[n for n in nodes if "Molecule" in n["labels"]]
rx_nodes=[n for n in nodes if "Reaction" in n["labels"]]
pq_nodes=[n for n in nodes if "PhysicalQuantity" in n["labels"]]

def amass(n):
    p=n.get("props",{})
    for k in ("atomic_mass","atomic_weight","hasWeight","hasAtomic","weight"):
        if k in p and isinstance(p[k],(int,float)): return float(p[k])
    return None

print("元素原子量可用率:", sum(1 for n in el_nodes if amass(n)), "/", len(el_nodes))
# EK:el 样本
for n in el_nodes:
    if n["id"].startswith("EK:el:"):
        print("  EK 元素样本:", n["id"], {k:n['props'].get(k) for k in ('atomic_mass','atomic_weight','hasWeight','hasAtomic','name')})
        break

comp=[e for e in edges if e["type"] in ("composed_of","has_element")]
print(f"\n组成边总数: {len(comp)}; 涉及分子: {len(set(e['source'] for e in comp))}")
# 看一个分子的完整组成（含边上的 count 属性）
mid=next((e["source"] for e in comp), None)
parts=[(e["target"], e.get("props",{})) for e in edges if e["source"]==mid and e["type"] in ("composed_of","has_element")]
print(f"\n分子 {mid} 的组成边（目标, 边属性）:")
for t,p in parts[:10]:
    print("   ->", t, "props=", p)
print("  该分子 props 关键字段:", {k:by_id[mid]['props'].get(k) for k in ('formula','molecular_formula','name','meaning','composition')})

print("\n=== 反应字段抽样 ===")
for n in rx_nodes[:5]:
    p=n.get("props",{})
    print(" ", n["id"], {k:p.get(k) for k in ('equation','temperature','pressure','yield','yield_percent','name','conditions','reactants','products')})

print("\n=== 物理量 symbol/domain 抽样（前 14）===")
for n in pq_nodes[:14]:
    p=n.get("props",{})
    print(" ", n["id"], "sym=",p.get("symbol"), "dom=",p.get("domain"), "name=",p.get("name"))

mm=[n["id"] for n in pq_nodes if "mass" in (n["props"].get("name") or "").lower()]
tp=[n["id"] for n in pq_nodes if "temp" in (n["props"].get("name") or "").lower() or "temp" in (n["props"].get("symbol") or "").lower()]
print("\n质量类 PQ:", mm, " 温度类 PQ:", tp)
