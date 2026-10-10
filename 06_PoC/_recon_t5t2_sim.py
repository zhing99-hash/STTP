# -*- coding: utf-8 -*-
"""侦察 8：T5(has_symbol manual_curation) 与 T2(composed_of manual_curation) 的复算影响模拟。只读。"""
import json, sys, os, collections
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
import verification_model as vm

g = json.load(open(os.path.join(ROOT, "06_PoC", "etl", "normalized.json"), encoding="utf-8"))
N = {n["id"]: n for n in g["nodes"]}

# ---------- T5 has_symbol ----------
hs = [e for e in g["edges"] if e.get("type") == "has_symbol"]
print("== T5 has_symbol 复算模拟（目标符号是否出现在源的结构化表达式）==")
res = collections.Counter()
bad_rebut = []
for e in hs:
    lv = (e.get("props") or {}).get("verification_level")
    if lv in ("rule_checked", "cross_source"):
        continue
    src, tgt = N.get(e["source"]), N.get(e["target"])
    sym = vm.target_symbol(tgt)
    hit = vm.symbol_in_source(src, sym)
    scope = (e.get("props") or {}).get("verification_scope")
    key = ("%s/%s" % (lv, scope), "HIT" if hit is True else ("MISS" if hit is False else "UNK"))
    res[key] += 1
    if hit is False and lv in ("source_asserted",):
        bad_rebut.append((e["source"], sym, str((src or {}).get("props", {}).get("latex"))[:20]))
for k, v in sorted(res.items()):
    print("   %-46s %-5s %4d" % (k[0], k[1], v))
print("   其中「可升 rule_checked」(source_asserted+by_construction 且 HIT)：%d"
      % sum(v for k, v in res.items() if k[1] == "HIT" and k[0].split("/")[0] in ("source_asserted", "by_construction")))
print("   其中「可撤」(source_asserted 且 MISS)：%d  -> %s" % (len(bad_rebut), bad_rebut[:6]))

# ---------- T2 composed_of ----------
print()
print("== T2 composed_of manual_curation 复算模拟（用源节点 formula 兜底）==")
co = [e for e in g["edges"] if e.get("type") == "composed_of"
      and (e.get("props") or {}).get("verification_scope") == "manual_curation"]
ok = bad = unk = 0
detail = []
for e in co:
    sym = vm.sym_of_el_id(e["target"])
    p = e.get("props") or {}
    f = p.get("from_formula") or ((N.get(e["source"]) or {}).get("props") or {}).get("formula")
    cnt = p.get("count")
    if not sym or cnt is None or not f:
        unk += 1
        continue
    try:
        comp = vm.parse_formula_independent(f)
        got = comp.get(sym)
        if got is not None and float(got) == float(cnt):
            ok += 1
        else:
            bad += 1
            detail.append((e["source"], f, sym, cnt, got))
    except Exception as ex:
        unk += 1
print("   可复算且一致(升 rule_checked)：%d" % ok)
print("   可复算但不一致(撤/降)      ：%d  -> %s" % (bad, detail[:5]))
print("   不可判定                   ：%d" % unk)
