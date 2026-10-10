# -*- coding: utf-8 -*-
"""对比「落库档位」vs「新模型复算档位」，输出逐类差异（只读，不写）。"""
import sys, os, json, collections
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
sys.path.insert(0, os.path.join(ROOT, "06_PoC"))
import verification_model as vm
import graph_export as ge

nodes, edges = vm.load_norm()
N = {n["id"]: n for n in nodes}
subj = {n["id"]: ge.subject_of((n.get("props") or {}).get("domain"), n["id"]) for n in nodes}
res, _ = vm.classify_all(nodes, edges)
new = {r[0]["id"]: r[1] for r in res}

RANK = vm.RANK
trans = collections.Counter()
detail = collections.defaultdict(list)
for e in edges:
    eid = e.get("id")
    old = (e.get("props") or {}).get("verification_level")
    nw = (new.get(eid) or {}).get("verification_level")
    if old == nw:
        continue
    up = RANK.get(nw, -1) > RANK.get(old, -1)
    trans[(old, nw, "↑" if up else "↓")] += 1
    if not up:
        detail[(old, nw, "↓")].append((e.get("type"), eid))

print("== 档位迁移（旧 -> 新）==")
for (o, n, d), v in sorted(trans.items(), key=lambda x: (x[0][2], -x[1])):
    print("   %-18s -> %-18s %s  %5d" % (o, n, d, v))
print()
print("== ★ 下降的（须逐条确认非误伤）==")
if not detail:
    print("   （无）")
for k, v in detail.items():
    print("   %s -> %s : %d 例：%s" % (k[0], k[1], len(v), v[:6]))
print()
# 跨域影响
ch = [e for e in edges if (e.get("props") or {}).get("verification_level")
      != (new.get(e.get("id")) or {}).get("verification_level")]
cross_ch = [e for e in ch if subj.get(e["source"]) and subj.get(e["target"]) and subj[e["source"]] != subj[e["target"]]]
print("变更边 %d 条，其中跨域边 %d 条" % (len(ch), len(cross_ch)))
for e in cross_ch:
    print("   %s(%s) -> %s(%s) : %s -> %s" % (
        e["source"], subj[e["source"]], e["target"], subj[e["target"]],
        (e.get("props") or {}).get("verification_level"),
        (new.get(e["id"]) or {}).get("verification_level")))
