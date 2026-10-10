# -*- coding: utf-8 -*-
"""Phase 31 · 门禁「非真空」自检（Non-vacuous Gate Self-Check）
==============================================================
目的（铁律 #14 / #16 / #20）：
    「一条**永远不会红**的断言不是断言」—— 必须证明新增的 3 条 `graph_scan`
    不变量在**注入缺陷时真的会 FAIL**，否则它就是 SKIP 的伪装（假通过）。

做法（**独立于校验器实现**）：
    不修改 `frozen_gate.py`，而是**直接调用** `run_case(case, graph)`，
    把**在内存中人工注入了一条已知假边**的图喂进去，断言判为 FAIL；
    同时用**未注入的原图**做正对照，断言判为 PASS。

覆盖：
    (n) symbol_expr_evidenced        —— 注入 scope=symbol_expr_recompute 但源无该符号
    (o) model_semantic_target_present—— 注入 GNN 假 `has_symbol`（目标符号缺席）
    (p) dim_consistent_recompute     —— 注入量纲互斥的假 `dimensionally_consistent`

用法：python 06_PoC/_gate_selfcheck_phase31.py
退出码：0 = 全部符合预期（真空即 1）。
"""
from __future__ import annotations

import copy
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import frozen_gate as fg                                     # noqa: E402

CASES = os.path.join(HERE, "frozen_counterexamples.json")

fails = []


def _case(cid):
    d = json.load(open(CASES, encoding="utf-8"))
    for c in d["cases"]:
        if c["id"] == cid:
            return c
    raise KeyError(cid)


def _check(name, got, want):
    ok = got == want
    print("%s %-46s got=%-6s want=%s" % ("✅" if ok else "❌", name, got, want))
    if not ok:
        fails.append(name)


graph0 = fg.load_graph()
nodes = graph0.get("nodes") or []
vm = fg.vm
dt = fg.dt


def _node(i):
    return {n["id"]: n for n in nodes}.get(i)


# ---------------------------------------------------------------- 0) 正对照
print("== 0) 正对照：未注入的原图必须全部 PASS ==")
for cid, scan in (("P1-den-main-symbol-expr-evidenced", "n"),
                  ("P1-den-main-model-semantic-target-present", "o"),
                  ("P1-den-main-dim-consistent-recompute", "p")):
    st, det = fg.run_case(_case(cid), graph0)
    _check("baseline(%s) %s" % (scan, cid), st, "PASS")

# ------------------------------------------------- 1) 找一对「符号必然缺席」的节点
pair_absent = None
for a in nodes:
    la = vm.expr_of_node(a)
    if not la:
        continue
    for t in nodes:
        ts = vm.target_symbol(t)
        if not ts:
            continue
        if a["id"] == t["id"]:
            continue
        if vm.symbol_in_source(a, ts) is False:
            pair_absent = (a, t, ts)
            break
    if pair_absent:
        break
if not pair_absent:
    print("❌ 找不到「符号缺席」的节点对，无法注入")
    fails.append("find_absent_pair")
else:
    a, t, ts = pair_absent
    print("   注入素材：源=%s（latex=%r） 目标=%s 目标符号=%r"
          % (a["id"], (vm.expr_of_node(a) or "")[:40], t["id"], ts))

# ------------------------------------------------------- 2) (o) 模型假 has_symbol
print("\n== 2) (o) 注入 GNN 假 `has_symbol`（目标符号缺席）→ 必须 FAIL ==")
if pair_absent:
    g = copy.deepcopy(graph0)
    g["edges"].append({
        "id": "TEST|has_symbol|%s|%s" % (a["id"], t["id"]),
        "type": "has_symbol", "source": a["id"], "target": t["id"], "kind": "gnn_prediction",
        "props": {"kind": "gnn_prediction", "source": "GNN", "verification_level": "model_inferred"},
    })
    st, det = fg.run_case(_case("P1-den-main-model-semantic-target-present"), g)
    _check("inject(o) 假 has_symbol -> FAIL", st, "FAIL")
    print("      detail: %s" % det)

# ---------------------------------------------------------- 3) (n) 假「已复算」
print("\n== 3) (n) 注入 scope=symbol_expr_recompute 但源无符号 → 必须 FAIL ==")
if pair_absent:
    g = copy.deepcopy(graph0)
    g["edges"].append({
        "id": "TEST|has_symbol|%s|%s" % (a["id"], t["id"]),
        "type": "has_symbol", "source": a["id"], "target": t["id"], "kind": "manual",
        "props": {"kind": "manual", "source": "TEST",
                  "verification_scope": "symbol_expr_recompute",
                  "verification_level": "rule_checked"},
    })
    st, det = fg.run_case(_case("P1-den-main-symbol-expr-evidenced"), g)
    _check("inject(n) 无证据却标 claimed 复算 -> FAIL", st, "FAIL")
    print("      detail: %s" % det)

# ------------------------------------------- 4) (p) 量纲互斥的假 dc 边
print("\n== 4) (p) 注入量纲互斥的假 `dimensionally_consistent` → 必须 FAIL ==")
pair_dim = None
seen = []
for a in nodes:
    qa = vm.qname_of_node(a)
    if not qa or dt.dim_of(qa) is None:
        continue
    seen.append((a, qa, dt.dim_of(qa)))
for i in range(len(seen)):
    for j in range(i + 1, len(seen)):
        (a, qa, da), (b, qb, db) = seen[i], seen[j]
        if da != db:
            pair_dim = (a, qa, b, qb)
            break
    if pair_dim:
        break
if not pair_dim:
    print("❌ 找不到「量纲互斥」的节点对，无法注入")
    fails.append("find_dim_pair")
else:
    a, qa, b, qb = pair_dim
    print("   注入素材：%s(%s, dim=%s)  vs  %s(%s, dim=%s)" % (a["id"], qa, None, b["id"], qb, None))
    g = copy.deepcopy(graph0)
    g["edges"].append({
        "id": "TEST|dimensionally_consistent|%s|%s" % (a["id"], b["id"]),
        "type": "dimensionally_consistent", "source": a["id"], "target": b["id"],
        "kind": "manual",
        "props": {"kind": "manual", "source": "TEST", "verification_level": "rule_checked"},
    })
    st, det = fg.run_case(_case("P1-den-main-dim-consistent-recompute"), g)
    _check("inject(p) 量纲互斥假 dc -> FAIL", st, "FAIL")
    print("      detail: %s" % det)

# --------------------------------------------------------------------- 汇总
print("\n" + "-" * 74)
if fails:
    print("汇总：❌ 非真空自检未通过 %d 项：%s" % (len(fails), fails))
    sys.exit(1)
print("汇总：✅ 3 条不变量全部**可红**（正对照 PASS + 注入缺陷 FAIL）—— 非真空，门禁有效")
sys.exit(0)
