#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_recon_phase33_align.py —— 第 23 轮只读侦察 3：T4/T6 独立复算的真实覆盖率。

思路（跨列/跨源，非同一推导路径）：
  T4 独立复算候选：Rhea `Equation` 列（物种文本名） vs 参与物节点 label（来自 ChEBI 本体，
     与 Rhea 是**不同数据库**）。校验「被指派为 reactant 的实体，其 label 是否出现在方程左侧」。
  T6 独立复算候选：codata_derivation 的 definition（如 R = N_A·k_B）用两端常量 value 数值代入校验。
**只读，不改任何数据。**
"""
from __future__ import annotations
import collections, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NORM = os.path.join(HERE, "etl", "normalized.json")

# 独立实现的方程切分（不 import rhea_ingest，避免同源）
def split_eq(eq):
    if " = " not in eq:
        return None, None
    lhs, rhs = eq.split(" = ", 1)
    f = lambda s: [x.strip() for x in s.split(" + ") if x.strip()]
    return f(lhs), f(rhs)


def main():
    d = json.load(open(NORM, encoding="utf-8"))
    nodes, edges = d["nodes"], d["edges"]
    N = {n["id"]: n for n in nodes}

    # ================= T4 =================
    print("=" * 100)
    print("【T4】equation(文本名) vs 参与物 label(ChEBI 本体) —— 跨列/跨源对齐覆盖率")
    print("=" * 100)
    t4 = [e for e in edges if e["type"] in ("reactant_of", "product_of")]
    rxn_cache = {}
    hit = miss_noname = miss_nomatch = noeq = 0
    samples = []
    for e in t4:
        rid = e["target"]
        r = rxn_cache.get(rid)
        if r is None:
            r = (N.get(rid) or {}).get("props") or {}
            rxn_cache[rid] = r
        eq = r.get("equation")
        if not eq:
            noeq += 1
            continue
        lhs, rhs = split_eq(eq)
        if lhs is None:
            noeq += 1
            continue
        part = (N.get(e["source"]) or {}).get("props") or {}
        nm = part.get("name")
        if not nm:
            miss_noname += 1
            continue
        side = lhs if e["type"] == "reactant_of" else rhs
        # 归一化比较
        def norm(x):
            return re.sub(r"\s+", " ", str(x)).strip().lower()
        nmset = {norm(x) for x in side}
        if norm(nm) in nmset:
            hit += 1
            if len(samples) < 5:
                samples.append((e["id"], eq, nm, e["type"]))
        else:
            miss_nomatch += 1
    tot = len(t4)
    print("总 %d ；无 equation %d ；参与物无 name %d" % (tot, noeq, miss_noname))
    print("**命中（label 出现在正确侧）= %d (%.1f%%)** ；不匹配 = %d (%.1f%%)"
          % (hit, 100.0 * hit / tot, miss_nomatch, 100.0 * miss_nomatch / tot))
    print("\n命中样例：")
    for s in samples:
        print("   %-46s | %s | %s" % (s[0][:46], s[2], s[3]))

    # 分析不匹配形态
    print("\n不匹配形态抽样（前 12，看是「泛称」还是「真错」）：")
    cnt = 0
    for e in t4:
        r = (N.get(e["target"]) or {}).get("props") or {}
        eq = r.get("equation")
        if not eq:
            continue
        lhs, rhs = split_eq(eq)
        if lhs is None:
            continue
        part = (N.get(e["source"]) or {}).get("props") or {}
        nm = part.get("name")
        side = lhs if e["type"] == "reactant_of" else rhs
        def norm(x):
            return re.sub(r"\s+", " ", str(x)).strip().lower()
        if nm and norm(nm) not in {norm(x) for x in side}:
            print("   label=%-26s | 侧名=%s" % (str(nm)[:26], [x[:22] for x in side]))
            cnt += 1
            if cnt >= 12:
                break

    # ================= T6 =================
    print("\n" + "=" * 100)
    print("【T6】codata_derivation 数值代入复算可行性")
    print("=" * 100)
    t6 = [e for e in edges if e["type"] == "derived_from"
          and (e.get("props") or {}).get("verification_scope") == "codata_derivation"]
    print("codata_derivation 条数 =", len(t6))
    for e in t6:
        p = e["props"] or {}
        s = (N.get(e["source"]) or {}).get("props") or {}
        t = (N.get(e["target"]) or {}).get("props") or {}
        print("   %-40s def=%-16s | %s=%s | %s=%s"
              % (e["id"][:40], str(p.get("definition"))[:16],
                 s.get("symbol") or s.get("name"), s.get("value"),
                 t.get("symbol") or t.get("name"), t.get("value")))

    # ================= T8 =================
    print("\n" + "=" * 100)
    print("【T8】discusses 名称对齐可行性（目标概念名 是否出现在 论文 title/topic）")
    print("=" * 100)
    t8 = [e for e in edges if e["type"] == "discusses"]
    ok = 0
    for e in t8:
        src = (N.get(e["source"]) or {}).get("props") or {}
        tgt = (N.get(e["target"]) or {}).get("props") or {}
        blob = " ".join(str(src.get(k) or "") for k in ("title", "primary_topic", "subfield", "field")).lower()
        nm = str(tgt.get("name") or "").lower().replace("_", " ")
        toks = [w for w in re.split(r"\W+", nm) if len(w) > 3]
        if toks and all(w in blob for w in toks):
            ok += 1
    print("discusses 总 %d ；目标概念名(全部词)出现在 title/topic = %d" % (len(t8), ok))


if __name__ == "__main__":
    main()
