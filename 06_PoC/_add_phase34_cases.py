# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""幂等追加 Phase 34 冻结反例（T4 残差跨源名称对齐 + 残差清算）。"""
import json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "06_PoC", "frozen_counterexamples.json")

NEW = [
    {
        "id": "P1-den-main-residual-accounted",
        "p0": "P1-den",
        "kind": "graph_scan",
        "desc": ("T4 残差（`reactant_of`/`product_of` 且非 cross_source/mismatch 的边）必须**清算到位**："
                 "每条边「不可独立复算的理由」须落在受控枚举 "
                 "`same_source_elementkg|same_source_curated|generic_class|polymer_residue|"
                 "placeholder_complex|naming_variant_no_second_source` 内 —— **零未归类**。"
                 "落地铁律 #30（不可判定 ≠ 可以放过）与 #34（负结果也是交付物）："
                 "Phase 34 把 1218 条 T4 残差升为跨源独立证据后，余下残差必须**逐类有据可查**，"
                 "不允许有「说不清为什么没验证」的边。判据由 `verification_model.residual_reason` "
                 "单一提供（铁律 #36），独立审计器 R12 用自带实现重算同一口径（铁律 #43）。"),
        "scan": {"residual_accounted": {}},
        "expect": "ZERO",
    },
]


def main():
    d = json.load(open(P, encoding="utf-8"))
    have = {c["id"] for c in d["cases"]}
    added = []
    for c in NEW:
        if c["id"] in have:
            continue
        d["cases"].append(c)
        added.append(c["id"])
    d.setdefault("meta", {})
    d["meta"]["phase"] = 34
    d["meta"]["ref"] = "07_交付物/Phase34_T4跨源名称对齐报告_20261010.md"
    json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("cases: %d（新增 %d：%s）" % (len(d["cases"]), len(added), added))
    n = sum(1 for c in d["cases"] if c.get("kind") == "graph_scan")
    print("graph_scan 类: %d" % n)


if __name__ == "__main__":
    main()
