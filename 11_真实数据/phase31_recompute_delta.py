#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""phase31_recompute_delta.py —— 第 21 轮 · 复算维度全覆盖（Recompute Coverage）

本轮命题
--------
把「**确定性复算**」从「只对**模型产物**」（Phase 30 的 R1/R2）推广到**所有来源**的语义边。
Phase 30 已建立「证据优先于提出者」，但**只对 GNN/LLM 边执行**；结果是**人工策划边被
「信任」而非「验证」**（铁律 #33：人工策划同样是一片「从未被检验过的断言」）。

四条新增/扩展复算（全部在 `verification_model.py`）
--------------------------------------------------
  · **A10** `has_symbol`   —— 目标符号**字面出现于源的结构化表达式**（latex/formula/symbols）
                              → `rule_checked`（把 A4 从 PhysicsBabel 推广到任意来源）
  · **A3 扩展** `composed_of` —— 边无式串时用**源节点 `formula`** 兜底复算计数
                              → `rule_checked`，scope 单列 `formula_count_node_recheck`
  · **A1 落地** `dimensionally_consistent` —— 扩充 `dimension_table.ALIAS` 关闭**系统性命名缺口**
                              （168 个物理量里 84 个查不到量纲 → 复算静默退化为不可判定）
  · **A11** `derived_from` —— **仅模型产物**：目标在源的结构化表达式里**毫无支撑** → 撤

产物
----
  06_PoC/etl/neo4j/phase31_recompute_delta.json   主 delta
  06_PoC/etl/neo4j/phase31_withdrawn.json         逐条撤回记录（可追溯）

用法
----
  python 11_真实数据/phase31_recompute_delta.py [--dry]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "06_PoC"))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import verification_model as vm              # noqa: E402
import graph_export                          # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase31_recompute_delta.json")
WITHDRAWN = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase31_withdrawn.json")

# 被复算**判否**（新档位 = unverified）的 scope → 撤回归因。未列者一律**不撤**（不猜）。
REBUT_SCOPES = {
    "semantic_target_absent": "R6 模型产物语义边（defines/has_symbol/derived_from）目标在源结构化表达式中缺席",
    "dimensional_mismatch": "R7 真量纲不符（A1 复算，命名缺口关闭后才可判定）",
    "unit_dimension_mismatch": "R8 单位量纲 ≠ 物理量真量纲（A8 复算）",
}


def eid_of(e):
    return e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))


def subj_of(n):
    p = (n or {}).get("props") or {}
    return graph_export.subject_of(p.get("domain"), str((n or {}).get("id") or ""))


def main():
    ap = argparse.ArgumentParser(description="Phase 31 delta 生成器")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--withdrawn-out", default=WITHDRAWN)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    d = json.load(open(NORM, encoding="utf-8"))
    nodes, edges = d["nodes"], d["edges"]
    N = {n["id"]: n for n in nodes}
    subj = {n["id"]: subj_of(n) for n in nodes}
    print("=" * 92)
    print("Phase 31 delta —— 输入 %d 节点 / %d 边" % (len(nodes), len(edges)))
    print("=" * 92)

    res, _ctx = vm.classify_all(nodes, edges)
    stored = {eid_of(e): e for e in edges}

    relabel, withdrawn = [], collections.defaultdict(list)
    del_keys = set()
    for e, r in res:
        eid = eid_of(e)
        p = (stored.get(eid) or {}).get("props") or {}
        old_lv, old_sc = p.get("verification_level"), p.get("verification_scope")
        new_lv, new_sc = r["verification_level"], r["verification_scope"]

        # ---- ① 反驳（判否）→ 撤边 ----
        if new_lv == "unverified" and new_sc in REBUT_SCOPES:
            withdrawn[REBUT_SCOPES[new_sc]].append({
                "id": eid, "type": e.get("type"), "kind": p.get("kind") or e.get("kind"),
                "source": e["source"], "target": e["target"],
                "source_subject": subj.get(e["source"]), "target_subject": subj.get(e["target"]),
                "old_level": old_lv, "old_scope": old_sc,
                "new_scope": new_sc, "note": (r.get("note") or "")[:200],
            })
            del_keys.add((e["source"], e.get("type"), e["target"]))
            continue

        # ---- ② 档位/范围变化 → 重标（只向上；需落库可重算重现） ----
        if old_lv == new_lv and old_sc == new_sc:
            continue
        if vm.RANK.get(new_lv, -1) < vm.RANK.get(old_lv, -1):
            print("  ⚠ 非预期降级：%s %s -> %s（跳过，交人工）" % (eid, old_lv, new_lv))
            continue
        relabel.append({
            "id": eid, "source": e["source"], "target": e["target"], "type": e.get("type"),
            "kind": p.get("kind") or e.get("kind") or "",
            "props": {
                "verification_level": new_lv,
                "verification_scope": new_sc,
                "verifier": r["verifier"],
                "verified": vm.is_verified(new_lv),
                "verification_note": "Phase31 复算：%s（原 %s/%s）" % (
                    (r.get("note") or new_sc)[:120], old_lv, old_sc),
            },
        })

    print("\n【① 反驳撤回】%d 条" % len(del_keys))
    for k, v in sorted(withdrawn.items(), key=lambda x: -len(x[1])):
        print("   %-46s %4d" % (k, len(v)))
        byt = collections.Counter(x["type"] for x in v)
        print("        " + " / ".join("%s×%d" % (t, n) for t, n in byt.most_common()))
    xd = [x for v in withdrawn.values() for x in v
          if x["source_subject"] and x["target_subject"] and x["source_subject"] != x["target_subject"]]
    print("   其中**跨域假桥** %d 条（%s）" % (len(xd), ["%s->%s" % (x["source"], x["target"]) for x in xd]))

    print("\n【② 复算升档】%d 条" % len(relabel))
    sc = collections.Counter(x["props"]["verification_scope"] for x in relabel)
    for k, v in sc.most_common(12):
        print("     %-42s %5d" % (k, v))
    mig = collections.Counter()
    for x in relabel:
        old = ((stored.get(x["id"]) or {}).get("props") or {}).get("verification_level")
        mig[(old, x["props"]["verification_level"])] += 1
    for k, v in mig.most_common():
        print("     %-18s -> %-18s %5d" % (k[0], k[1], v))

    # ---------------- 汇总 ----------------
    lv_after = collections.Counter()
    for e, r in res:
        if eid_of(e) in {x["id"] for x in relabel}:
            lv_after[r["verification_level"]] += 1
        elif (e["source"], e.get("type"), e["target"]) in del_keys:
            continue
        else:
            lv_after[r["verification_level"]] += 1

    payload = {
        "meta": {
            "phase": 31, "tag": "recompute-coverage", "generator": "phase31_recompute_delta.py",
            "base_graph": os.path.relpath(NORM, ROOT),
            "theme": "复算维度全覆盖：把确定性复算从「仅模型产物」推广到所有来源的语义边",
            "rebut_rules": REBUT_SCOPES,
            "edges_withdrawn": len(del_keys),
            "withdrawn_breakdown": {k: len(v) for k, v in withdrawn.items()},
            "edges_relabeled": len(relabel),
            "level_distribution_after": {k: lv_after.get(k, 0) for k in vm.LEVELS},
        },
        "nodes": [],
        "delete_nodes": [],
        "edges": relabel,
        "delete_edges": [{"source": s, "type": t, "target": g,
                          "kind": next((x.get("kind") for x in edges
                                        if x["source"] == s and x["type"] == t and x["target"] == g), "")}
                         for (s, t, g) in sorted(del_keys)],
    }
    print("\n【delta 汇总】edges=%d  delete_edges=%d" % (len(payload["edges"]), len(payload["delete_edges"])))
    print("  预测分层：" + " ".join("%s=%d" % (k, lv_after.get(k, 0)) for k in vm.LEVELS))
    print("  预测规模：%d 节点 / %d 边" % (len(nodes), len(edges) - len(del_keys)))

    if a.dry:
        print("[DRY] 未落盘")
        return 0
    json.dump(payload, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(withdrawn, open(a.withdrawn_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("-> %s" % os.path.relpath(a.out, ROOT))
    print("-> %s" % os.path.relpath(a.withdrawn_out, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
