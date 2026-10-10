#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_recon_phase33_indep.py —— 第 23 轮只读侦察：T9-i 非独立证据的全量构成。

复现 task_trust_audit.py 的 ok_edges（T1–T8 达标边），拆出「缺独立证据」的子集，
按 type / scope / kind / level 分解，定位「非独立」的根因。**只读，不改任何数据。**
"""
from __future__ import annotations
import collections, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import graph_export                                   # noqa: E402
import verification_model as vm                       # noqa: E402

NORM = os.path.join(HERE, "etl", "normalized.json")


def main():
    d = json.load(open(NORM, encoding="utf-8"))
    nodes, edges = d["nodes"], d["edges"]
    node_by_id = {n["id"]: n for n in nodes}
    subj = {n["id"]: graph_export.subject_of((n.get("props") or {}).get("domain"), n["id"])
            for n in nodes}

    def is_cross(e):
        s, t = subj.get(e["source"]), subj.get(e["target"])
        return bool(s and t and s != t)

    TASKS = [
        ("T1", "rule_checked", lambda e, N: e["type"] in ("same_period", "same_family")),
        ("T2", "rule_checked", lambda e, N: e["type"] == "composed_of"),
        ("T3", "rule_checked", lambda e, N: e["type"] == "dimensionally_consistent"),
        ("T4", "source_asserted", lambda e, N: e["type"] in ("reactant_of", "product_of")),
        ("T5", "rule_checked", lambda e, N: e["type"] == "has_symbol"),
        ("T6", "source_asserted", lambda e, N: e["type"] in ("derived_from", "has_unit")),
        ("T7", "rule_checked", None),
        ("T8", "source_asserted", lambda e, N: e["type"] in ("cites", "discusses")),
    ]

    def _evs(e):
        evs = (e.get("props") or {}).get("verification_evidence")
        return evs if isinstance(evs, list) else []

    def has_indep_ev(e):
        ver = (e.get("props") or {}).get("verifier")
        return any(isinstance(it, dict) and it.get("indep") and it.get("detail")
                   and it.get("impl") == ver for it in _evs(e))

    ok_by_id = {}     # id -> (edge, set(tasks))
    for tid, minlv, pred in TASKS:
        if tid == "T7":
            sel = [e for e in edges if is_cross(e)]
        else:
            sel = [e for e in edges if pred(e, node_by_id)]
        labeled = [e for e in sel if (e.get("props") or {}).get("verification_level")]
        ok = [e for e in labeled
              if vm.RANK.get((e["props"] or {}).get("verification_level"), -1) >= vm.RANK[minlv]]
        for e in ok:
            rec = ok_by_id.setdefault(e["id"], [e, set()])
            rec[1].add(tid)

    ok_edges = list(ok_by_id.values())
    non_indep = [(e, t) for e, t in ok_edges if not has_indep_ev(e)]
    print("=" * 100)
    print("T9-i 侦察：ok_edges(去重) = %d ；缺独立证据 = %d (%.1f%%)"
          % (len(ok_edges), len(non_indep),
             100.0 * len(non_indep) / len(ok_edges) if ok_edges else 0))
    print("=" * 100)

    def dist(keyfn, title, topn=25):
        c = collections.Counter(keyfn(e, t) for e, t in non_indep)
        print("\n-- %s --" % title)
        for k, v in c.most_common(topn):
            print("   %-46s %6d" % (str(k)[:46], v))
        return c

    # 1. 边类型
    dist(lambda e, t: e["type"], "按边类型 (type)")
    # 2. 所属任务族（一个边可属多族）
    dist(lambda e, t: "/".join(sorted(t)), "按所属任务族 (tasks)")
    # 3. verification_scope
    dist(lambda e, t: (e.get("props") or {}).get("verification_scope", "?"),
         "按 verification_scope")
    # 4. verification_level
    dist(lambda e, t: (e.get("props") or {}).get("verification_level", "?"),
         "按 verification_level")
    # 5. 证据对象 kind 组合
    def kinds(e, t):
        ks = [it.get("kind") for it in _evs(e) if isinstance(it, dict)]
        return "+".join(sorted(set(ks))) if ks else "（无证据）"
    dist(kinds, "按证据对象 kind 组合")
    # 6. verifier
    dist(lambda e, t: (e.get("props") or {}).get("verifier", "?"), "按 verifier", topn=20)

    # 7. 交叉：type × scope
    print("\n-- 交叉 边类型 × scope（非独立 top 15）--")
    c2 = collections.Counter(
        (e["type"], (e.get("props") or {}).get("verification_scope", "?"))
        for e, t in non_indep)
    for (ty, sc), v in c2.most_common(15):
        print("   %-34s %-38s %6d" % (ty[:34], sc[:38], v))

    # 8. 抽样 detail
    print("\n-- 抽样（type, scope → 第一条证据 detail）--")
    seen = set()
    for e, t in non_indep:
        key = (e["type"], (e.get("props") or {}).get("verification_scope", "?"))
        if key in seen:
            continue
        seen.add(key)
        evs = _evs(e)
        det = evs[0].get("detail", "")[:70] if evs and isinstance(evs[0], dict) else "（无）"
        print("   %-30s | %-30s | %s" % (key[0][:30], str(key[1])[:30], det))
        if len(seen) >= 22:
            break


if __name__ == "__main__":
    main()
