import sys, os, json
sys.path.insert(0, r"C:\Users\Administrator\WorkBuddy\2026-10-08-10-51-20\STTP\06_PoC")
import graph_export

ROOT = r"C:\Users\Administrator\WorkBuddy\2026-10-08-10-51-20\STTP"
ready = json.load(open(os.path.join(ROOT, r"06_PoC\etl\neo4j\phase5_neo4j_ready.json"), encoding="utf-8"))
seed  = json.load(open(os.path.join(ROOT, r"10_种子数据\seed_energy_combustion.json"), encoding="utf-8"))

rn, re_ = len(ready["nodes"]), len(ready["edges"])
sn, se_ = len(seed["nodes"]), len(seed["edges"])
print(f"phase5 ready: {rn} 节点 / {re_} 边")
print(f"seed        : {sn} 节点 / {se_} 边")

# 合并（id 应不相交）
ids = {x["id"] for x in ready["nodes"]}
overlap = [x["id"] for x in seed["nodes"] if x["id"] in ids]
assert not overlap, f"节点 id 重叠: {overlap[:5]}"

merged = {
    "nodes": ready["nodes"] + seed["nodes"],
    "edges": ready["edges"] + seed["edges"],
}
# 写临时 raw 文件供 graph_export 读取
tmp = os.path.join(ROOT, r"06_PoC\etl\neo4j\_phase6_raw.json")
json.dump(merged, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

data = graph_export.build_graph_data(tmp)
out = os.path.join(ROOT, r"06_PoC\graph_data_phase6.json")
json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
m = data["meta"]
print(f"\n[OK] 已生成 {os.path.relpath(out)}")
print(f"  节点: {m['node_count']}   边: {m['edge_count']}")
print(f"  节点类型: {m['node_types']}")
print(f"  边类型:   {m['edge_types']}")
print(f"  学科分布: {m['subjects']}")
print(f"  explicit/inferred: {m['explicit_or_inferred']}")
print(f"  悬空端点: {len(m['dangling_endpoints'])}")
print(f"  verified 边: {m['verified_edges']}")
if m['warnings']:
    print("  [WARN]", m['warnings'][:3])

# 抽样校验新切片节点
def find(nid):
    return next((x for x in data["nodes"] if x["id"] == nid), None)
for nid in ["EK:el:C", "UN:j", "PQ:energy", "FO:emc2", "MO:co2", "RX:comb_ch4", "MX:thm:gauss_bonnet"]:
    nd = find(nid)
    if nd:
        print(f"  {nid:18} type={nd['type']:<18} subject={nd['subject']:<4} label={nd['label']!r}")
    else:
        print(f"  {nid:18} 缺失!")
os.remove(tmp)
