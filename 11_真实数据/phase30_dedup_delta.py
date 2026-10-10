#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""phase30_dedup_delta.py —— 第 20 轮 · 跨域桥去伪存真（De-noise & Rebuild）

三件事
------
  ① **去伪**：撤回可被**确定性反驳**的语义边（判据见 `verification_model.py` A8 与
     `symbol_in_source`）——
        · `defines`  / model 产物：目标量的符号**不在**源公式表达式中
        · `has_symbol` / model 产物：目标符号**不在**源对象表达式/声明中
        · `has_unit`：单位量纲 **≠** 物理量真量纲（**全量**执行，无命名歧义）
     不可判定者（源无表达式 / 量纲未知）**一律不撤**（不猜）。
  ② **存真（单位）**：被撤的物理量若**再无正确单位边**、且存在**量纲严格匹配**的既有
     单位节点 → 补一条 `has_unit`（`rule_checked`）。
  ③ **存真（数学桥）**：源公式**含**目标数学对象所辖的算子 → 建 `derived_from`/`has_symbol`
     桥（`rule_checked`，scope=`*_usage`）。这是「数学桥」从 0 条**真桥**起步的地方。
  ④ **全量重标**：落盘前对**结果图**整体重算档位，与已存值不符者一并写入（铁律 #23：
     落库档位必须能被重算重现）。

产物
----
  06_PoC/etl/neo4j/phase30_dedup_delta.json      主 delta
  06_PoC/etl/neo4j/phase30_withdrawn.json        逐条撤回记录（可追溯，供报告引用）

用法
----
  python 11_真实数据/phase30_dedup_delta.py [--out <p>] [--dry]
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
OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase30_dedup_delta.json")
WITHDRAWN = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase30_withdrawn.json")

MODEL_KINDS = set(vm.MODEL_KINDS)


def is_model(e):
    k = (e.get("props") or {}).get("kind") or e.get("kind") or ""
    s = (e.get("props") or {}).get("source") or ""
    return (k in MODEL_KINDS) or ("GNN" in s) or ("LLM" in s) or ("gnn" in k) or ("llm" in k)


def subj_of(n):
    p = (n or {}).get("props") or {}
    return graph_export.subject_of(p.get("domain"), str((n or {}).get("id") or ""))


def eid_of(e):
    return e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))


def main():
    ap = argparse.ArgumentParser(description="Phase 30 delta 生成器")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--withdrawn-out", default=WITHDRAWN)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    d = json.load(open(NORM, encoding="utf-8"))
    nodes, edges = d["nodes"], d["edges"]
    N = {n["id"]: n for n in nodes}
    print("=" * 92)
    print("Phase 30 delta —— 输入 %d 节点 / %d 边" % (len(nodes), len(edges)))
    print("=" * 92)

    withdrawn = {"R1_defines_target_symbol_absent": [], "R2_symbol_target_absent": [],
                 "R3_unit_dim_mismatch": [], "R5_unit_conflict_model_withdrawn": []}
    del_keys = set()

    # ---------------- ① 去伪 ----------------
    for e in edges:
        t = e.get("type")
        if t not in ("defines", "has_symbol"):
            continue
        if not is_model(e):
            continue
        tnode, snode = N.get(e["target"]), N.get(e["source"])
        if not tnode or not snode:
            continue
        sym = vm.target_symbol(tnode)
        ok = vm.symbol_in_source(snode, sym)
        if ok is False:
            tag = "R1_defines_target_symbol_absent" if t == "defines" \
                else "R2_symbol_target_absent"
            withdrawn[tag].append({
                "id": eid_of(e), "source": e["source"], "target": e["target"],
                "type": t, "kind": (e.get("props") or {}).get("kind") or e.get("kind"),
                "symbol": sym,
                "reason": "目标符号 %r 不在源表达式/声明中（%s）" % (
                    sym, vm.expr_of_node(snode)[:60]),
            })
            del_keys.add((e["source"], t, e["target"]))

    # has_unit：全量量纲复算
    unit_ok = {}
    for e in edges:
        if e.get("type") != "has_unit":
            continue
        un, qn = N.get(e["target"]), N.get(e["source"])
        if not un or not qn:
            continue
        up = un.get("props") or {}
        usym = up.get("symbol") or up.get("name")
        ud = vm.unit_dim_of_symbol(usym)
        qname = (qn.get("props") or {}).get("name") or e["source"].split(":")[-1]
        qd = vm.dm.dim_of(qname)
        if ud is None or qd is None:
            unit_ok[eid_of(e)] = None
            continue
        good = vm._drop(ud) == vm._drop(qd)
        unit_ok[eid_of(e)] = good
        if not good:
            withdrawn["R3_unit_dim_mismatch"].append({
                "id": eid_of(e), "source": e["source"], "target": e["target"], "type": "has_unit",
                "kind": (e.get("props") or {}).get("kind") or e.get("kind"),
                "quantity": qname, "unit": usym,
                "reason": "单位量纲 %s ≠ %s 真量纲 %s"
                          % (vm._drop(ud), qname, vm._drop(qd)),
            })
            del_keys.add((e["source"], "has_unit", e["target"]))

    # R5：同一量挂了**量纲互斥**的单位边时，撤回其中的**模型产物**边
    #     —— 模型猜测不得与已策划的断言冲突（若策划本身错，已由 R3 单独处理）。
    #     ⚠ 若冲突边**全是**模型产物 → **不猜**（留给门禁 `unit_unique_per_quantity` 报错）。
    by_q = collections.defaultdict(list)
    for e in edges:
        if e.get("type") != "has_unit":
            continue
        if (e["source"], "has_unit", e["target"]) in del_keys:
            continue
        un = N.get(e["target"])
        if not un:
            continue
        ud = vm.unit_dim_of_symbol((un.get("props") or {}).get("symbol"))
        if ud is not None:
            by_q[e["source"]].append((e, tuple(sorted(vm._drop(ud).items()))))
    for src, lst in by_q.items():
        if len({d for _, d in lst}) <= 1:
            continue
        if not any(not is_model(e) for e, _ in lst):
            continue                                # 全是模型边 → 不猜
        for e, _d in lst:
            if not is_model(e):
                continue
            withdrawn["R5_unit_conflict_model_withdrawn"].append({
                "id": eid_of(e), "source": e["source"], "target": e["target"], "type": "has_unit",
                "kind": (e.get("props") or {}).get("kind") or e.get("kind"),
                "reason": "与同量其它单位边**量纲互斥**，且存在策划断言 → 撤模型侧",
            })
            del_keys.add((e["source"], "has_unit", e["target"]))

    print("\n【① 去伪】")
    for k, v in withdrawn.items():
        print("  %-34s %3d" % (k, len(v)))
    print("  合计撤边 %d" % len(del_keys))
    # ---------------- 模拟结果图 ----------------
    edges_after = [e for e in edges
                   if (e["source"], e["type"], e["target"]) not in del_keys]

    # ---------------- ②③ 收集候选新边（**先收集，后统一分类**） ----------------
    #   ⚠ 不能在建边的循环里就地 classify：A8/A9 的证据来自 `ctx`，而 `ctx` 由**图**构建；
    #     新边尚未入图 → 复算不会触发 → 会被误降级为 `source_asserted`（实测：数学桥 0 条）。
    cand_edges = []

    # ---- ② 存真（单位） ----
    unit_nodes = [n for n in nodes if "Unit" in (n.get("labels") or [])]
    lost_srcs = {w["source"] for w in withdrawn["R3_unit_dim_mismatch"]}
    for src in sorted(lost_srcs):
        qn = N.get(src)
        qname = (qn.get("props") or {}).get("name") or src.split(":")[-1]
        qd = vm.dm.dim_of(qname)
        if qd is None:
            continue
        still = [e for e in edges_after if e.get("type") == "has_unit" and e["source"] == src]
        if still:                                  # 还有单位边 → 不补
            continue
        cands = []
        for un in unit_nodes:
            ud = vm.unit_dim_of_symbol((un.get("props") or {}).get("symbol"))
            if ud is not None and vm._drop(ud) == vm._drop(qd):
                cands.append(un["id"])
        if not cands:
            print("  [存真·单位] %-22s 无量纲匹配的既有单位节点 → 只撤不补" % src)
            continue
        pfx = src.split(":")[0].lower()
        cands.sort(key=lambda u: (0 if u.split(":")[0].lower() == pfx else
                                  (1 if u.startswith("CM:un:") else 2), u))
        tgt = cands[0]
        cand_edges.append({"id": "has_unit|%s|%s" % (src, tgt), "source": src, "target": tgt,
                           "type": "has_unit", "kind": "unit_dimension_recompute", "props": {}})

    # ---- ③ 存真（数学桥） ----
    existing = {(e["source"], e["type"], e["target"]) for e in edges_after}
    seen = set()
    for n in nodes:
        if "Formula" not in (n.get("labels") or []):
            continue
        if subj_of(n) == "数学":                   # 只建**跨域**桥（数学↔物理/化学）
            continue
        for scope in vm.math_op_of(vm.expr_of_node(n)):
            for tgt in sorted(vm.MATH_OP_RULES[scope][1]):
                if tgt not in N:
                    continue
                etype = "has_symbol" if scope in ("exp_base_e_usage", "pi_constant_usage") \
                    else "derived_from"
                key = (n["id"], etype, tgt)
                if key in existing or key in seen:
                    continue
                seen.add(key)
                cand_edges.append({"id": "%s|%s|%s" % (etype, n["id"], tgt), "source": n["id"],
                                   "target": tgt, "type": etype,
                                   "kind": "math_operator_bridge", "props": {}})

    # ---- 统一分类：把候选边并入图后再复算，只保留拿到确定性证据者 ----
    res_all, _ = vm.classify_all(nodes, edges_after + cand_edges)
    by_id = {eid_of(e): r for e, r in res_all}
    new_edges = []
    for c in cand_edges:
        r = by_id.get(c["id"])
        if not r or r["verification_level"] != "rule_checked":
            continue
        c["props"] = {
            "verification_level": r["verification_level"],
            "verification_scope": r["verification_scope"],
            "verifier": r["verifier"], "verified": True,
            "verification_note": "Phase30 存真：%s" % (r.get("note") or r["verification_scope"]),
        }
        new_edges.append(c)
    n_unit = sum(1 for e in new_edges if e["type"] == "has_unit")
    n_math = len(new_edges) - n_unit
    for e in new_edges:
        print("  [存真] %-30s --%s--> %-20s (%s)"
              % (e["source"], e["type"], e["target"], e["props"]["verification_scope"]))
    print("\n【② 存真·单位】%d 条   【③ 数学桥】%d 条" % (n_unit, n_math))

    # ---------------- ④ 全量重标 ----------------
    edges_final = edges_after + [dict(e) for e in new_edges]
    res, _ctx = vm.classify_all(nodes, edges_final)
    stored = {eid_of(e): e for e in edges}
    relabel, add_full = [], []
    new_ids = {e["id"] for e in new_edges}
    for e, r in res:
        eid = eid_of(e)
        if eid in new_ids:
            add_full.append(e)
            continue
        p = stored.get(eid)
        if not p:
            continue
        old = p.get("props") or {}
        if old.get("verification_level") == r["verification_level"] \
                and old.get("verification_scope") == r["verification_scope"]:
            continue
        relabel.append({
            "id": eid, "source": e["source"], "target": e["target"], "type": e["type"],
            "kind": old.get("kind") or e.get("kind") or "",
            "props": {"verification_level": r["verification_level"],
                      "verification_scope": r["verification_scope"],
                      "verifier": r["verifier"], "verified": vm.is_verified(r["verification_level"])},
        })
    print("【④ 重标】%d 条（其中因 A8/A9 新证据升级的见 scope 分布）" % len(relabel))
    sc = collections.Counter(r["props"]["verification_scope"] for r in relabel)
    for k, v in sc.most_common(12):
        print("     %-40s %5d" % (k, v))

    # ---------------- 汇总 ----------------
    lv = collections.Counter(r["verification_level"] for _, r in res)
    payload = {
        "meta": {
            "phase": 30, "tag": "dedup", "generator": "phase30_dedup_delta.py",
            "base_graph": os.path.relpath(NORM, ROOT),
            "theme": "跨域桥去伪存真：确定性反驳语义噪声边 + 重建单位/数学桥",
            "edges_withdrawn": len(del_keys),
            "withdrawn_breakdown": {k: len(v) for k, v in withdrawn.items()},
            "unit_edges_rebuilt": sum(1 for e in new_edges if e["type"] == "has_unit"),
            "math_bridges_built": n_math,
            "edges_relabeled": len(relabel),
            "level_distribution": {k: lv.get(k, 0) for k in vm.LEVELS},
        },
        "nodes": [],
        "delete_nodes": [],
        "edges": new_edges + relabel,
        "delete_edges": [{"source": s, "type": t, "target": g,
                          "kind": next((x.get("kind") for x in edges
                                        if x["source"] == s and x["type"] == t and x["target"] == g), "")}
                         for (s, t, g) in sorted(del_keys)],
    }
    print("\n【delta 汇总】nodes=%d edges=%d delete_edges=%d"
          % (len(payload["nodes"]), len(payload["edges"]), len(payload["delete_edges"])))
    print("  预测分层：" + " ".join("%s=%d" % (k, lv.get(k, 0)) for k in vm.LEVELS))
    print("  预测规模：%d 节点 / %d 边" % (len(nodes), len(edges) - len(del_keys) + len(new_edges)))

    if a.dry:
        print("[DRY] 未落盘")
        return 0
    json.dump(payload, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(withdrawn, open(a.withdrawn_out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("-> %s" % os.path.relpath(a.out, ROOT))
    print("-> %s" % os.path.relpath(a.withdrawn_out, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
