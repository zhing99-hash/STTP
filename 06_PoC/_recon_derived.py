# -*- coding: utf-8 -*-
"""侦察 6：derived_from 的 42 条 model_inferred —— 「目标是否出现在源的结构化字段里」
（严格口径：只读 latex/symbols/formula）vs （宽口径：也算 informal/meaning/proof/statement）。
只读。"""
import json, os, re
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
g = json.load(open(os.path.join(ROOT, "06_PoC", "etl", "normalized.json"), encoding="utf-8"))
N = {n["id"]: n for n in g["nodes"]}

STRICT = ("latex", "formula", "symbols")
LOOSE = STRICT + ("informal", "meaning", "proof", "statement", "definition", "name", "description")

_CMD = re.compile(r"\\([A-Za-z]+)")
_STRIP = re.compile(r"[\\{}$\s]+")
def norm(s):
    return _STRIP.sub("", _CMD.sub(r"\1", str(s)))

def fields(nid, keys):
    p = (N.get(nid) or {}).get("props") or {}
    out = []
    for k in keys:
        v = p.get(k)
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            out.extend(str(x) for x in v)
        else:
            out.append(str(v))
    return out

def ident(nid):
    """目标的标识候选（latex / name）。"""
    p = (N.get(nid) or {}).get("props") or {}
    cands = []
    for k in ("latex", "name", "local_id", "symbol"):
        v = p.get(k)
        if isinstance(v, str) and v.strip():
            cands.append(v)
    return cands

d = [e for e in g["edges"] if e.get("type") == "derived_from"
     and (e.get("props") or {}).get("verification_level") == "model_inferred"]
print("model_inferred derived_from：%d 条\n" % len(d))
print("%-20s -> %-18s | strict | loose | 目标标识" % ("源", "目标"))
print("-" * 100)
cnt = {"s_ok": 0, "l_ok": 0, "both_no": 0}
for e in d:
    sc = norm("".join(fields(e["source"], STRICT)))
    lc = norm("".join(fields(e["source"], LOOSE)))
    cands = ident(e["target"])
    s_hit = any(norm(c) and norm(c) in sc for c in cands)
    l_hit = any(norm(c) and norm(c) in lc for c in cands)
    if s_hit: cnt["s_ok"] += 1
    if l_hit: cnt["l_ok"] += 1
    if not s_hit and not l_hit: cnt["both_no"] += 1
    print("%-20s -> %-18s | %-6s | %-5s | %s" % (
        (N.get(e["source"]) or {}).get("props", {}).get("name", e["source"])[:20],
        (N.get(e["target"]) or {}).get("props", {}).get("name", e["target"])[:18],
        "HIT" if s_hit else "-", "HIT" if l_hit else "-",
        " / ".join(cands)[:40]))

print()
print("严格口径命中(不该撤)：%d" % cnt["s_ok"])
print("宽松口径命中        ：%d" % cnt["l_ok"])
print("两口径皆不命中(可撤) ：%d" % cnt["both_no"])
