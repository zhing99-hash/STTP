# -*- coding: utf-8 -*-
"""幂等追加 Phase 35 冻结反例（非独立证据清算 + 北极星分母记账）。"""
import json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "06_PoC", "frozen_counterexamples.json")

NEW = [
    {
        "id": "P1-den-main-independence-accounted",
        "p0": "P1-den",
        "kind": "graph_scan",
        "desc": ("T1–T8 **达标边**（= 北极星与 T9 的分母）中，凡**证据不独立**者，其"
                 "「为什么拿不到独立证据」必须落在受控枚举 "
                 "`same_source_elementkg|same_source_curated|generic_class|polymer_residue|"
                 "placeholder_complex|naming_variant_no_second_source|"
                 "no_independent_citation_index|no_second_subject_index|source_text_only|"
                 "unit_name_not_in_reference` 内 —— **零未归类**。"
                 "第 25 轮穷举证明：T4（Rhea 方程侧别）与 T8（cites/discusses）**根本没有可达的"
                 "第二源**（KEGG/Reactome/SABIO-RK/MetaNetX/Wikidata 全部负结果）—— 属**结构性**缺口。"
                 "负结果既然无法消灭，就必须**记账**，否则会随新源引入静默漂移。"
                 "判据由 `verification_model.indep_reason` 单一提供（铁律 #36），"
                 "独立审计器 R13 用自带实现重算同一口径（铁律 #43）。"),
        "scan": {"independence_accounted": {}},
        "expect": "ZERO",
    },
    {
        "id": "P1-den-main-scope-accounted",
        "p0": "P1-den",
        "kind": "graph_scan",
        "desc": ("北极星的分母**是选择的结果**（铁律 #50）：全图**每一条边**要么属 T1–T8 任务族"
                 "（在分母内），要么其「为何不在分母」必须能在 `verification_model.SCOPE_TYPE_REASONS` "
                 "里找到理由 —— **零未归类类型**。没有这条不变式，新引入的边族会**静默地**不进分母"
                 "（分母外比例上升却无人知晓）。本断言把「分母的选择」变成**可见、可审、新增即红**；"
                 "门禁 detail 同时打印 分母内/分母外占比 与 占位实体边数。"),
        "scan": {"scope_accounted": {}},
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
    d["meta"]["phase"] = 35
    d["meta"]["ref"] = "07_交付物/Phase35_北极星口径自审与T9i天花板报告_20261010.md"
    json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("cases: %d（新增 %d：%s）" % (len(d["cases"]), len(added), added))
    n = sum(1 for c in d["cases"] if c.get("kind") == "graph_scan")
    print("graph_scan 类: %d" % n)


if __name__ == "__main__":
    main()
