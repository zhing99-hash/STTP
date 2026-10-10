# -*- coding: utf-8 -*-
"""幂等追加 Phase 32 冻结反例（Claim/Evidence 对象化）。"""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "06_PoC", "frozen_counterexamples.json")

NEW = [
    {
        "id": "P1-den-main-evidence-traceable",
        "p0": "P1-den",
        "kind": "graph_scan",
        "desc": ("凡 `level >= rule_checked` 的边必须携带 ≥1 条**独立证据**"
                 "（`verification_evidence[].indep == True`），其 `impl` 必须与边的 `verifier` 一致、"
                 "`detail` 非空。这是北极星「结论正确且**证据可追溯**」的直接仪器 —— "
                 "没有它，`rule_checked` 只是**标签**（Phase 32）。"),
        "scan": {"evidence_traceable": {"min_ratio": 1.0}},
        "expect": "ZERO",
    },
    {
        "id": "P1-den-main-evidence-wellformed",
        "p0": "P1-den",
        "kind": "graph_scan",
        "desc": ("全图 `verification_evidence` 必须良构：① 非空列表；② `kind ∈ EVIDENCE_KINDS`；"
                 "③ `detail` 非空；④ `indep` 与 `kind` 语义配对（`recompute`/`cross_source` 必独立）。"
                 "并断言**证据覆盖率下限**（Phase 32 由 1.18% → 100%，防回退）。"),
        "scan": {"evidence_wellformed": {"min_ratio": 1.0}},
        "expect": "ZERO",
    },
    {
        "id": "P1-den-main-no-repr-residue",
        "p0": "P1-den",
        "kind": "graph_scan",
        "desc": ("全图任何 props 值不得是「Python repr 化的容器」—— 即形如 `[...]`/`{...}`、"
                 "**不是合法 JSON**、却能被 `ast.literal_eval` 还原为 list/dict 的字符串。"
                 "这是「属性形态 → 静默降级」的指纹（`str({'a':1})` 产出单引号 repr，"
                 "下游 `json.loads` 静默失败）。Phase 32 引入嵌套 `verification_evidence` 后，"
                 "任何一处 `str()` 强转都会立刻制造残留 → 必须有守卫钉死。"),
        "scan": {"no_repr_residue": {}},
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
    d["meta"]["phase"] = 32
    d["meta"]["ref"] = "07_交付物/Phase32_Claim_Evidence对象化报告_20261010.md"
    json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("cases: %d（新增 %d：%s）" % (len(d["cases"]), len(added), added))
    n = sum(1 for c in d["cases"] if c.get("kind") == "graph_scan")
    print("graph_scan 类: %d" % n)


if __name__ == "__main__":
    main()
