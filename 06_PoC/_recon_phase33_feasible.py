#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_recon_phase33_feasible.py —— 第 23 轮只读侦察 2：可独立复算子集与天花板。

对 T4(reactant_of/product_of) / T8(cites/discusses) / T6(derived_from/has_unit)
的 source_asserted 边，检查**是否存在独立于提出方的确定性复算**所需的数据。
**只读，不改任何数据。**

判据来源（铁律 #26）：「能被独立于提出方的实现确定性复算 → 授 rule_checked」。
"""
from __future__ import annotations
import collections, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NORM = os.path.join(HERE, "etl", "normalized.json")


def show_props(tag, obj, limit=14):
    props = obj.get("props") or {}
    print("  [%s] id=%s" % (tag, obj.get("id")))
    for k in sorted(props.keys()):
        v = props[k]
        s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
        print("       %-24s = %s" % (k, s[:110]))
    print()


def main():
    d = json.load(open(NORM, encoding="utf-8"))
    nodes, edges = d["nodes"], d["edges"]
    N = {n["id"]: n for n in nodes}

    # ---------- T4: reactant_of / product_of ----------
    print("=" * 100)
    print("【T4】reactant_of / product_of —— 方程方向是否可独立复算？")
    print("=" * 100)
    t4 = [e for e in edges if e["type"] in ("reactant_of", "product_of")]
    print("总条数 =", len(t4))
    # 取一条 equation_sidedness（Rhea）与一条 source_assertion（ElementKG2.0）
    for scope in ("equation_sidedness", "source_assertion"):
        ex = next((e for e in t4
                   if (e.get("props") or {}).get("verification_scope") == scope), None)
        if ex:
            print("\n--- 样例 scope=%s ---" % scope)
            show_props("EDGE", ex)
            rxn = N.get(ex["target"])
            if rxn:
                show_props("TARGET(反应节点)", rxn, limit=20)
            src = N.get(ex["source"])
            if src:
                show_props("SOURCE(参与物节点)", src)
            break

    # 反应节点是否带 equation / formula？覆盖率
    def rxn_has(e, keys):
        r = N.get(e["target"]) or {}
        p = r.get("props") or {}
        return any(k in p and p[k] for k in keys)
    print("  反应节点带 equation 的比例: %d/%d" % (
        sum(1 for e in t4 if rxn_has(e, ["equation", "smiles", "reaction_equation"])), len(t4)))
    print("  反应节点带 'equation' 键的比例: %d/%d" % (
        sum(1 for e in t4 if "equation" in ((N.get(e['target']) or {}).get('props') or {})), len(t4)))
    # 反应节点全部键名统计
    keyset = collections.Counter()
    for e in t4:
        r = N.get(e["target"]) or {}
        for k in (r.get("props") or {}):
            keyset[k] += 1
    print("  反应节点键名分布:", dict(keyset.most_common(20)))

    # ---------- T8: cites / discusses ----------
    print("\n" + "=" * 100)
    print("【T8】cites / discusses —— 文献元数据是否可独立复算？")
    print("=" * 100)
    t8 = [e for e in edges if e["type"] in ("cites", "discusses")]
    print("总条数 =", len(t8))
    for ty in ("cites", "discusses"):
        ex = next((e for e in t8 if e["type"] == ty), None)
        if ex:
            print("\n--- 样例 type=%s ---" % ty)
            show_props("EDGE", ex)
            print("    SOURCE 节点 props:", list(((N.get(ex['source']) or {}).get('props') or {}).keys()))
            print("    TARGET 节点 props:", list(((N.get(ex['target']) or {}).get('props') or {}).keys()))

    # ---------- T6: derived_from / has_unit ----------
    print("\n" + "=" * 100)
    print("【T6】derived_from / has_unit —— 派生/单位是否可独立复算？")
    print("=" * 100)
    t6 = [e for e in edges if e["type"] in ("derived_from", "has_unit")
          and (e.get("props") or {}).get("verification_level") == "source_asserted"]
    print("source_asserted 条数 =", len(t6))
    byscope = collections.Counter((e.get("props") or {}).get("verification_scope") for e in t6)
    print("  scope 分布:", dict(byscope))
    for scope in ("codata_derivation", "codata_unit", "manual_curation"):
        ex = next((e for e in t6 if (e.get("props") or {}).get("verification_scope") == scope), None)
        if ex:
            print("\n--- 样例 scope=%s ---" % scope)
            show_props("EDGE", ex)
            print("    SOURCE props:", list(((N.get(ex['source']) or {}).get('props') or {}).keys()))
            print("    TARGET props:", list(((N.get(ex['target']) or {}).get('props') or {}).keys()))


if __name__ == "__main__":
    main()
