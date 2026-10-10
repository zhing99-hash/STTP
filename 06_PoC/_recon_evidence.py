# -*- coding: utf-8 -*-
"""侦察 3：evidence[] / verification_scope / verifier 的当前覆盖。只读。"""
import json, collections, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
g = json.load(open(os.path.join(ROOT, "06_PoC", "etl", "normalized.json"), encoding="utf-8"))
nodes, edges = g["nodes"], g["edges"]

def has_nonempty(p, k):
    v = p.get(k)
    return v not in (None, "", [], {})

print("== 边：evidence / verification_scope / verifier 覆盖 ==")
tot = len(edges)
c_ev = sum(1 for e in edges if has_nonempty(e.get("props") or {}, "evidence"))
c_sc = sum(1 for e in edges if has_nonempty(e.get("props") or {}, "verification_scope"))
c_vf = sum(1 for e in edges if has_nonempty(e.get("props") or {}, "verifier"))
c_lv = sum(1 for e in edges if has_nonempty(e.get("props") or {}, "verification_level"))
print("  边总数 %d" % tot)
print("  有 evidence[]         %5d  (%.1f%%)" % (c_ev, 100*c_ev/tot))
print("  有 verification_scope  %5d  (%.1f%%)" % (c_sc, 100*c_sc/tot))
print("  有 verifier            %5d  (%.1f%%)" % (c_vf, 100*c_vf/tot))
print("  有 verification_level  %5d  (%.1f%%)" % (c_lv, 100*c_lv/tot))

print()
print("== 有 evidence 的边按 type 分布 ==")
tc = collections.Counter()
for e in edges:
    if has_nonempty(e.get("props") or {}, "evidence"):
        tc[e.get("type")] += 1
for k, v in tc.most_common(20):
    print("  %-28s %5d" % (k, v))

print()
print("== evidence 值形态（前 5 条）==")
n = 0
for e in edges:
    v = (e.get("props") or {}).get("evidence")
    if has_nonempty(e.get("props") or {}, "evidence"):
        print("  type=%s  -> %s" % (e.get("type"), str(v)[:200]))
        n += 1
        if n >= 5:
            break

print()
print("== 节点：同类字段 ==")
nk = collections.Counter()
for nd in nodes:
    for k in ("evidence", "verification_scope", "verifier", "verification_level"):
        if has_nonempty(nd.get("props") or {}, k):
            nk[k] += 1
print(" ", dict(nk))

print()
print("== claim 类字段是否存在 ==")
kc = collections.Counter()
for it in nodes + edges:
    for k in (it.get("props") or {}):
        if "claim" in k.lower():
            kc[k] += 1
print(" ", dict(kc) or "（无 claim 字段）")
