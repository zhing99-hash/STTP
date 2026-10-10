# -*- coding: utf-8 -*-
"""幂等追加 Phase 33 冻结反例（T9-i 证据独立攻坚）。"""
import json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "06_PoC", "frozen_counterexamples.json")

NEW = [
    {
        "id": "P1-den-main-level-scope-reproducible",
        "p0": "P1-den",
        "kind": "graph_scan",
        "desc": ("全图每条边**落库**的 `verification_level`/`verification_scope`/`verifier` 必须与"
                 "`verification_model.classify_all` **重算**逐条一致（铁律 #25：落库档位必须能被重算重现）。"
                 "这是「落库 = 模型产物」的唯一全局守卫 —— 防止手改存档 / 判级漂移造成"
                 "「落库 ≠ 重算」的静默不一致（第 19 轮实测 157 条，铁律 #23）。Phase 33 把 T4/T6"
                 "的 2791 条升档落库后，此断言证明 48712 条全部可重算重现。"),
        "scan": {"level_scope_reproducible": {}},
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
    d["meta"]["phase"] = 33
    d["meta"]["ref"] = "07_交付物/Phase33_T9i证据独立攻坚报告_20261010.md"
    json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("cases: %d（新增 %d：%s）" % (len(d["cases"]), len(added), added))
    n = sum(1 for c in d["cases"] if c.get("kind") == "graph_scan")
    print("graph_scan 类: %d" % n)


if __name__ == "__main__":
    main()
