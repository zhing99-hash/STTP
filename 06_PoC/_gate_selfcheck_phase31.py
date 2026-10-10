# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""门禁「非真空」自检（Non-vacuous Gate Self-Check）—— Phase 31 ~ Phase 35
==========================================================================
目的（铁律 #14 / #16 / #20 / #38）：
    「一条**永远不会红**的断言不是断言」—— 必须证明新增的 `graph_scan`
    不变量在**注入缺陷时真的会 FAIL**，否则它就是 SKIP 的伪装（假通过）。

做法（**独立于校验器实现**）：
    不修改 `frozen_gate.py`，而是**直接调用** `run_case(case, graph)`，
    把**在内存中人工注入了一条已知假边**的图喂进去，断言判为 FAIL；
    同时用**未注入的原图**做正对照，断言判为 PASS。

覆盖：
    Phase 31 —— (n) symbol_expr_evidenced / (o) model_semantic_target_present /
                (p) dim_consistent_recompute
    Phase 32 —— (q) evidence_traceable（rule_checked 边无独立证据 → FAIL）
                (r) evidence_wellformed（非法 kind / 覆盖率跌破下限 → FAIL）
                (s) no_repr_residue（Python repr 容器残留 → FAIL）
    Phase 33 —— (t) level_scope_reproducible（落库档位 ≠ 重算 → FAIL）
    Phase 34 —— (u) residual_accounted（T4 残差**未归类** → FAIL）
    Phase 35 —— (v) independence_accounted（**达标但非独立**且理由未登记 → FAIL）
                (w) scope_accounted（**不在任务族**的边类型理由未登记 → FAIL）

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
                  ("P1-den-main-dim-consistent-recompute", "p"),
                  ("P1-den-main-evidence-traceable", "q"),
                  ("P1-den-main-evidence-wellformed", "r"),
                  ("P1-den-main-no-repr-residue", "s"),
                  ("P1-den-main-level-scope-reproducible", "t"),
                  ("P1-den-main-residual-accounted", "u")):
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

# --------------------------------- 5) (q) rule_checked 边缺独立证据
print("\n== 5) (q) 注入 level=rule_checked 但**无证据链**的边 → 必须 FAIL ==")
if pair_absent:
    g = copy.deepcopy(graph0)
    g["edges"].append({
        "id": "TEST|has_symbol|%s|%s" % (a["id"], t["id"]),
        "type": "has_symbol", "source": a["id"], "target": t["id"], "kind": "manual",
        "props": {"kind": "manual", "source": "TEST",
                  "verification_level": "rule_checked",
                  "verification_scope": "symbol_expr_recompute",
                  "verifier": "verification_model.symbol_in_source"},
    })
    st, det = fg.run_case(_case("P1-den-main-evidence-traceable"), g)
    _check("inject(q) rule_checked 无证据链 -> FAIL", st, "FAIL")
    print("      detail: %s" % det)

print("\n== 5b) (q) 注入**有证据但 indep=假**的 rule_checked 边 → 必须 FAIL ==")
if pair_absent:
    g = copy.deepcopy(graph0)
    g["edges"].append({
        "id": "TEST|has_symbol|%s|%s" % (a["id"], t["id"]),
        "type": "has_symbol", "source": a["id"], "target": t["id"], "kind": "manual",
        "props": {"kind": "manual", "source": "TEST",
                  "verification_level": "rule_checked",
                  "verification_scope": "symbol_expr_recompute",
                  "verifier": "verification_model.symbol_in_source",
                  "verification_evidence": [
                      {"kind": "source_assertion", "impl": "verification_model.symbol_in_source",
                       "detail": "自称复算但实为来源自述", "indep": False}]},
    })
    st, det = fg.run_case(_case("P1-den-main-evidence-traceable"), g)
    _check("inject(q) 证据非独立 -> FAIL", st, "FAIL")
    print("      detail: %s" % det)

print("\n== 5c) (q) 注入证据 impl 与 verifier **不一致**的边 → 必须 FAIL ==")
if pair_absent:
    g = copy.deepcopy(graph0)
    g["edges"].append({
        "id": "TEST|has_symbol|%s|%s" % (a["id"], t["id"]),
        "type": "has_symbol", "source": a["id"], "target": t["id"], "kind": "manual",
        "props": {"kind": "manual", "source": "TEST",
                  "verification_level": "rule_checked",
                  "verification_scope": "symbol_expr_recompute",
                  "verifier": "verification_model.symbol_in_source",
                  "verification_evidence": [
                      {"kind": "recompute", "impl": "someone.else.entirely",
                       "detail": "证据实现与判级实现不符", "indep": True}]},
    })
    st, det = fg.run_case(_case("P1-den-main-evidence-traceable"), g)
    _check("inject(q) impl 不符 -> FAIL", st, "FAIL")
    print("      detail: %s" % det)

# --------------------------------- 6) (r) 证据对象不良构
print("\n== 6) (r) 注入**非法 kind** 的证据对象 → 必须 FAIL ==")
if pair_absent:
    g = copy.deepcopy(graph0)
    g["edges"].append({
        "id": "TEST|has_symbol|%s|%s" % (a["id"], t["id"]),
        "type": "has_symbol", "source": a["id"], "target": t["id"], "kind": "manual",
        "props": {"kind": "manual", "source": "TEST", "verification_level": "model_inferred",
                  "verification_evidence": [
                      {"kind": "bogus_kind", "impl": "x", "detail": "y", "indep": True}]},
    })
    st, det = fg.run_case(_case("P1-den-main-evidence-wellformed"), g)
    _check("inject(r) 非法 kind -> FAIL", st, "FAIL")
    print("      detail: %s" % det)

print("\n== 6b) (r) 注入**覆盖率跌破下限**（新增无边证据的边）→ 必须 FAIL ==")
if pair_absent:
    g = copy.deepcopy(graph0)
    g["edges"].append({
        "id": "TEST|has_symbol|%s|%s" % (a["id"], t["id"]),
        "type": "has_symbol", "source": a["id"], "target": t["id"], "kind": "manual",
        "props": {"kind": "manual", "source": "TEST", "verification_level": "model_inferred"},
    })
    st, det = fg.run_case(_case("P1-den-main-evidence-wellformed"), g)
    _check("inject(r) 覆盖跌破下限 -> FAIL", st, "FAIL")
    print("      detail: %s" % det)

print("\n== 6c) (r) 注入 indep 与 kind **不配对** 的证据 → 必须 FAIL ==")
if pair_absent:
    g = copy.deepcopy(graph0)
    g["edges"].append({
        "id": "TEST|has_symbol|%s|%s" % (a["id"], t["id"]),
        "type": "has_symbol", "source": a["id"], "target": t["id"], "kind": "manual",
        "props": {"kind": "manual", "source": "TEST", "verification_level": "source_asserted",
                  "verification_evidence": [
                      {"kind": "source_assertion", "impl": "x", "detail": "z", "indep": True}]},
    })
    st, det = fg.run_case(_case("P1-den-main-evidence-wellformed"), g)
    _check("inject(r) indep/kind 不配对 -> FAIL", st, "FAIL")
    print("      detail: %s" % det)

# --------------------------------- 7) (s) repr 串残留
print("\n== 7) (s) 注入 **Python repr 化的容器**（`str(dict)`）→ 必须 FAIL ==")
if pair_absent:
    g = copy.deepcopy(graph0)
    g["edges"].append({
        "id": "TEST|has_symbol|%s|%s" % (a["id"], t["id"]),
        "type": "has_symbol", "source": a["id"], "target": t["id"], "kind": "manual",
        "props": {"kind": "manual", "source": "TEST", "verification_level": "model_inferred",
                  "poisoned": str({"a": 1, "b": [2, 3]})},   # ← str() 强转的正确写法=repr 串
    })
    st, det = fg.run_case(_case("P1-den-main-no-repr-residue"), g)
    _check("inject(s) repr 残留 -> FAIL", st, "FAIL")
    print("      detail: %s" % det)

print("\n== 7b) (s) 正对照：**合法 JSON 串** 不得误报 ==")
if pair_absent:
    g = copy.deepcopy(graph0)
    g["edges"].append({
        "id": "TEST|has_symbol|%s|%s" % (a["id"], t["id"]),
        "type": "has_symbol", "source": a["id"], "target": t["id"], "kind": "manual",
        "props": {"kind": "manual", "source": "TEST", "verification_level": "model_inferred",
                  "legit": json.dumps({"a": 1, "b": [2, 3]})},   # ← 合法 JSON，不应误报
    })
    st, det = fg.run_case(_case("P1-den-main-no-repr-residue"), g)
    _check("ctrl(s) 合法 JSON 不误报 -> PASS", st, "PASS")
    print("      detail: %s" % det)

# ------------------------- 8) (t) 落库档位与重算不一致
print("\n== 8) (t) 注入「落库 level ≠ 重算 level」的边 → 必须 FAIL ==")
if graph0.get("edges"):
    g = copy.deepcopy(graph0)
    tgt = g["edges"][0]
    tgt.setdefault("props", {})["verification_level"] = "human_reviewed"   # 全图无此档 → 必与重算不符
    print("   注入素材：%s（落库 level 篡改为 human_reviewed）" % tgt.get("id"))
    st, det = fg.run_case(_case("P1-den-main-level-scope-reproducible"), g)
    _check("inject(t) 落库≠重算 -> FAIL", st, "FAIL")
    print("      detail: %s" % det)

# ------------------------- 8b) (t) 正对照：篡改 scope 也可红
print("\n== 8b) (t) 注入「落库 scope ≠ 重算 scope」→ 必须 FAIL ==")
if graph0.get("edges"):
    g = copy.deepcopy(graph0)
    tgt = g["edges"][1]
    tgt.setdefault("props", {})["verification_scope"] = "bogus_scope_xyz"
    print("   注入素材：%s（落库 scope 篡改为 bogus_scope_xyz）" % tgt.get("id"))
    st, det = fg.run_case(_case("P1-den-main-level-scope-reproducible"), g)
    _check("inject(t) scope 不一致 -> FAIL", st, "FAIL")
    print("      detail: %s" % det)

# ------------------------- 9) (u) T4 残差「未归类」
print("\n== 9) (u) 注入「说不清为何未验证」的 T4 残差边（未知来源）→ 必须 FAIL ==")
g = copy.deepcopy(graph0)
g["nodes"].append({"id": "TEST:rx:unknown", "type": "reaction",
                   "props": {"source": "TEST_UNKNOWN_SRC", "equation": "A + B = C"}})
g["nodes"].append({"id": "TEST:cpd:x", "type": "molecule",
                   "props": {"source": "TEST_UNKNOWN_SRC", "name": "x"}})
g["edges"].append({
    "id": "TEST|reactant_of|TEST:cpd:x|TEST:rx:unknown",
    "type": "reactant_of", "source": "TEST:cpd:x", "target": "TEST:rx:unknown",
    "kind": "manual",
    "props": {"kind": "manual", "source": "TEST_SRC",
              "verification_level": "source_asserted",
              "verification_scope": "source_assertion"},
})
st, det = fg.run_case(_case("P1-den-main-residual-accounted"), g)
_check("inject(u) T4 残差未归类 -> FAIL", st, "FAIL")
print("      detail: %s" % det)

# ------------------------- 10) (v) 非独立证据「未归类」
print("\n== 10) (v) 注入「拿不到独立证据却说不清为什么」的达标边 → 必须 FAIL ==")
g = copy.deepcopy(graph0)
g["nodes"].append({"id": "TEST:cpd:p1", "type": "molecule",
                   "props": {"source": "TEST_UNKNOWN_SRC", "name": "p1"}})
g["nodes"].append({"id": "TEST:cpd:p2", "type": "molecule",
                   "props": {"source": "TEST_UNKNOWN_SRC", "name": "p2"}})
# same_period 属 T1（门槛 rule_checked）；证据存在但 indep=False，且该类型的理由未登记
g["edges"].append({
    "id": "TEST|same_period|TEST:cpd:p1|TEST:cpd:p2",
    "type": "same_period", "source": "TEST:cpd:p1", "target": "TEST:cpd:p2",
    "kind": "manual",
    "props": {"kind": "manual", "source": "TEST_SRC",
              "verification_level": "rule_checked",
              "verification_scope": "TODO_UNREGISTERED",
              "verifier": "test_runner",
              "verification_evidence": [{"kind": "recompute", "indep": False,
                                         "detail": "注入用例", "impl": "test_runner"}]},
})
st, det = fg.run_case(_case("P1-den-main-independence-accounted"), g)
_check("inject(v) 非独立未归类 -> FAIL", st, "FAIL")
print("      detail: %s" % det)

# ------------------------- 11) (w) 分母外类型「未归类」
print("\n== 11) (w) 注入「不在任务族、理由也未登记」的新边类型 → 必须 FAIL ==")
g = copy.deepcopy(graph0)
g["nodes"].append({"id": "TEST:a", "type": "molecule", "props": {"name": "a"}})
g["nodes"].append({"id": "TEST:b", "type": "molecule", "props": {"name": "b"}})
g["edges"].append({
    "id": "TEST|bogus_relation|TEST:a|TEST:b",
    "type": "bogus_relation", "source": "TEST:a", "target": "TEST:b",
    "kind": "manual",
    "props": {"kind": "manual", "verification_level": "source_asserted",
              "verification_scope": "source_assertion"},
})
st, det = fg.run_case(_case("P1-den-main-scope-accounted"), g)
_check("inject(w) 分母外类型未归类 -> FAIL", st, "FAIL")
print("      detail: %s" % det)

# --------------------------------------------------------------------- 汇总
print("\n" + "-" * 74)
if fails:
    print("汇总：❌ 非真空自检未通过 %d 项：%s" % (len(fails), fails))
    sys.exit(1)
print("汇总：✅ 10 条不变量（n/o/p/q/r/s/t/u/v/w）全部**可红**（正对照 PASS + 注入缺陷 FAIL）"
      "—— 非真空，门禁有效")
sys.exit(0)
