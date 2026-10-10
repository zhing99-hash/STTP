# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""追加 Phase 31 冻结反例（幂等：先按 id 去重）。"""
import json, os, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "06_PoC", "frozen_counterexamples.json")
d = json.load(open(P, encoding="utf-8"))
cs = d["cases"]
have = {c["id"] for c in cs}

NEW = [
    # ---------------- level_check：A10 符号表达式复算 ----------------
    {
        "id": "P1-level-symbol-expr-recompute-ok", "p0": "P1-symbol",
        "kind": "level_check",
        "desc": "**正对照（Phase 31）**：人工策划的 `has_symbol` —— 目标符号 `E` 字面出现在源公式 latex `E=mc^2` 中 → 必须 `rule_checked` / `symbol_expr_recompute`（把 A4 从 PhysicsBabel 推广到任意来源）",
        "input": {
            "nodes": [
                {"id": "T:fo:emc2", "labels": ["Formula", "Entity"],
                 "props": {"latex": "E=mc^2"}},
                {"id": "T:sy:E", "labels": ["Symbol", "Entity"],
                 "props": {"symbol": "E", "name": "E (energy)"}},
            ],
            "edges": [
                {"id": "has_symbol|T:fo:emc2|T:sy:E", "source": "T:fo:emc2",
                 "target": "T:sy:E", "type": "has_symbol", "kind": "formula_symbol",
                 "props": {"source": "curated_seed/energy"}},
            ],
        },
        "expect": {"edge": 0, "level": "rule_checked", "scope": "symbol_expr_recompute"},
    },
    {
        "id": "P1-level-symbol-expr-absent-not-upgraded", "p0": "P1-symbol",
        "kind": "level_check",
        "desc": "**反例（Phase 31）**：`has_symbol` 断言目标符号 `Q`，但源公式 latex `F=ma` 中并无 `Q` → **不得**升为 `rule_checked`（判否**不足以反驳**，但绝不可当作证据升级）",
        "input": {
            "nodes": [
                {"id": "T:fo:fma", "labels": ["Formula", "Entity"],
                 "props": {"latex": "F=ma"}},
                {"id": "T:sy:Q", "labels": ["Symbol", "Entity"],
                 "props": {"symbol": "Q", "name": "Q (charge)"}},
            ],
            "edges": [
                {"id": "has_symbol|T:fo:fma|T:sy:Q", "source": "T:fo:fma",
                 "target": "T:sy:Q", "type": "has_symbol", "kind": "formula_symbol",
                 "props": {"source": "curated_seed/energy"}},
            ],
        },
        "expect": {"edge": 0, "level": "source_asserted", "scope": "manual_curation"},
    },
    # ---------------- level_check：A11 模型产物语义边反驳 ----------------
    {
        "id": "P1-level-model-semantic-target-absent-rejected", "p0": "P1-symbol",
        "kind": "level_check",
        "desc": "**反例（Phase 31）**：GNN 边 `流形 --derived_from--> 二项式`，目标标识 `(a+b)²` 不在源的 latex `M` 中 → 必须 `unverified` / `semantic_target_absent`（撤回）",
        "input": {
            "nodes": [
                {"id": "T:def:manifold", "labels": ["Definition", "Entity"],
                 "props": {"latex": "M"}},
                {"id": "T:math:binomial", "labels": ["MathConcept", "Entity"],
                 "props": {"name": "(a+b)²"}},
            ],
            "edges": [
                {"id": "derived_from|T:def:manifold|T:math:binomial",
                 "source": "T:def:manifold", "target": "T:math:binomial",
                 "type": "derived_from", "kind": "gnn_typed_inferred",
                 "props": {"source": "Phase13.GNN.typed"}},
            ],
        },
        "expect": {"edge": 0, "level": "unverified", "scope": "semantic_target_absent"},
    },
    {
        "id": "P1-level-model-semantic-target-present-not-upgraded", "p0": "P1-symbol",
        "kind": "level_check",
        "desc": "**正对照（Phase 31）**：同一模型边但目标标识**确实**在源 latex 中（`T_p M` 含 `p`）→ **不得**被撤（`unverified`），也**不得**升档（同式共现 ≠ 派生）—— 应停在 `model_inferred`",
        "input": {
            "nodes": [
                {"id": "T:def:tangent", "labels": ["Definition", "Entity"],
                 "props": {"latex": "T_p M"}},
                {"id": "T:sy:p", "labels": ["Symbol", "Entity"],
                 "props": {"symbol": "p", "name": "p"}},
            ],
            "edges": [
                {"id": "derived_from|T:def:tangent|T:sy:p",
                 "source": "T:def:tangent", "target": "T:sy:p",
                 "type": "derived_from", "kind": "gnn_typed_inferred",
                 "props": {"source": "Phase13.GNN.typed"}},
            ],
        },
        "expect": {"edge": 0, "level": "model_inferred", "scope": "model_link_prediction"},
    },
    # ---------------- dim_table：ALIAS 扩充的正/负对照 ----------------
    {"id": "P1-dim-alias-speed-of-light-eq-light-speed", "p0": "P1-dim",
     "kind": "dim_table",
     "desc": "**正对照（Phase 31）**：命名缺口收口 —— 展示名 `Speed of light` 与规范名 `light_speed` 量纲必须相等（关闭缺口才谈得上复算）",
     "input": {"a": "Speed of light", "b": "light_speed"}, "expect": "DIM_EQUAL"},
    {"id": "P1-dim-alias-electric-charge-eq-charge", "p0": "P1-dim",
     "kind": "dim_table",
     "desc": "**正对照（Phase 31）**：`Electric charge` == `charge`",
     "input": {"a": "Electric charge", "b": "charge"}, "expect": "DIM_EQUAL"},
    {"id": "P1-dim-alias-electromotive-force-eq-voltage", "p0": "P1-dim",
     "kind": "dim_table",
     "desc": "**正对照（Phase 31）**：`Electromotive force` == `voltage`（电动势与电压同量纲）",
     "input": {"a": "Electromotive force", "b": "voltage"}, "expect": "DIM_EQUAL"},
    {"id": "P1-dim-alias-focal-length-eq-length", "p0": "P1-dim",
     "kind": "dim_table",
     "desc": "**正对照（Phase 31）**：`Focal length` == `length`",
     "input": {"a": "Focal length", "b": "length"}, "expect": "DIM_EQUAL"},
    {"id": "P1-dim-alias-gibbs-free-energy-eq-energy", "p0": "P1-dim",
     "kind": "dim_table",
     "desc": "**正对照（Phase 31）**：`Gibbs free energy` == `energy`",
     "input": {"a": "Gibbs free energy", "b": "energy"}, "expect": "DIM_EQUAL"},
    {"id": "P1-dim-alias-reduced-planck-eq-planck", "p0": "P1-dim",
     "kind": "dim_table",
     "desc": "**正对照（Phase 31）**：`Reduced Planck constant`（ħ）与 `Planck constant`（h）量纲相等",
     "input": {"a": "Reduced Planck constant", "b": "Planck constant"}, "expect": "DIM_EQUAL"},
    {"id": "P1-dim-not-efield-eq-voltage", "p0": "P1-dim",
     "kind": "dim_table",
     "desc": "**负对照（Phase 31，冻结真实缺陷）**：`Electric field`(MLT⁻³I⁻¹) **≠** `voltage`(ML²T⁻³I⁻¹) —— 电场单位是 V/m 而非 V。这条对照把「ALIAS 关闭缺口后新抓出的第 6 条策划错误」冻结下来，防它被重新引入",
     "input": {"a": "Electric field", "b": "voltage"}, "expect": "DIM_NOT_EQUAL"},
    # ---------------- graph_scan：三条新不变量 ----------------
    {"id": "P1-den-main-symbol-expr-evidenced", "p0": "P1-den",
     "kind": "graph_scan",
     "desc": "凡 `scope == symbol_expr_recompute`（Phase 31 升级用）的边，目标符号**必须真在**源的结构化表达式中；且条数 ≥196（防证据链被清空而静默归零）",
     "scan": {"symbol_expr_evidenced": {"min_count": 196}}, "expect": "ZERO"},
    {"id": "P1-den-main-model-semantic-target-present", "p0": "P1-den",
     "kind": "graph_scan",
     "desc": "**模型产物**的 `defines`/`has_symbol`/`derived_from` 边，目标标识不得在源的结构化表达式中缺席（Phase 30 `semantic_target_present` 的同族扩展；与 A11 共用同一谓词）",
     "scan": {"model_semantic_target_present": {}}, "expect": "ZERO"},
    {"id": "P1-den-main-dim-consistent-recompute", "p0": "P1-den",
     "kind": "graph_scan",
     "desc": "`dimensionally_consistent` 边凡两端真量纲可查者必须严格相等 —— T3（全图最短板任务族）首次有仪器",
     "scan": {"dim_consistent_recompute": {}}, "expect": "ZERO"},
]

added = 0
for c in NEW:
    if c["id"] in have:
        continue
    cs.append(c)
    added += 1

m = d.setdefault("meta", {})
m["phase"] = 31
m["ref"] = "07_交付物/Phase31_复算维度全覆盖报告_20261010.md"
print("新增 %d 条；总条数 %d" % (added, len(cs)))
print("kind 分布：", dict(collections.Counter(c["kind"] for c in cs)))
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("-> 已写回")
