#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""task_trust_audit.py —— 北极星仪器：**任务级可信完成率**（第 18 轮 · A 路线）

口径（2026-10-10 老板裁定，取代「跨域边计数 / 学科占比」）
--------------------------------------------------------
    北极星 = 固定任务集上，**跨域结论正确且证据可追溯**的比例

落地方式：把图谱要支撑的**任务族**显式列出，每个任务族声明
「完成该任务所依赖的边」+「可接受的**最低证据档**」；再按
`verification_level` 统计达标率。

铁律 #18（成对读）：同时给出
    · **覆盖率** = 该任务族的边中带明确等级标签的比例
    · **达标率** = 达标边 / 该任务族边
    单读达标率会被「只标了少数边」骗过。

用法
----
    python task_trust_audit.py                     # 读 06_PoC/etl/normalized.json
    python task_trust_audit.py --graph <path>
    python task_trust_audit.py --json <out>        # 落盘结果
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
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import graph_export                                   # noqa: E402
import verification_model as vm                       # noqa: E402

NORM = os.path.join(HERE, "etl", "normalized.json")

# --------------------------------------------------------------------------- 任务族
# (id, 名称, 说明, 最低可接受档, 判定函数(e, node_by_id) -> bool)
TASKS = [
    ("T1", "元素周期律",
     "同周期 / 同族 —— 可用权威周期表复算",
     "rule_checked",
     lambda e, N: e["type"] in ("same_period", "same_family")),
    ("T2", "分子组成（元素计数）",
     "分子↔元素 及化学计量数 —— 需可由分子式独立复算",
     "rule_checked",
     lambda e, N: e["type"] == "composed_of"),
    ("T3", "量纲一致性",
     "物理量↔物理量 同量纲", "rule_checked",
     lambda e, N: e["type"] == "dimensionally_consistent"),
    ("T4", "化学方程式方向",
     "反应物/生成物 归属 —— 方向由源方程唯一确定", "source_asserted",
     lambda e, N: e["type"] in ("reactant_of", "product_of")),
    ("T5", "公式—参与量",
     "公式↔参与物理量/符号", "rule_checked",
     lambda e, N: e["type"] == "has_symbol"),
    ("T6", "常量溯源与单位",
     "常量派生 / 单位挂靠", "source_asserted",
     lambda e, N: e["type"] in ("derived_from", "has_unit")),
    ("T7", "跨域桥（跨学科端点）",
     "两端点学科不同的 same_as/proves/derived_from/defines —— **本项目的核心命题**",
     "human_reviewed", None),      # 判定函数在 main 里特化（需学科信息）
    ("T8", "文献元数据",
     "论文↔主题/引用", "source_asserted",
     lambda e, N: e["type"] in ("cites", "discusses")),
]


def load(p):
    d = json.load(open(p, encoding="utf-8"))
    return d["nodes"], d["edges"]


def main():
    ap = argparse.ArgumentParser(description="北极星仪器：任务级可信完成率")
    ap.add_argument("--graph", default=NORM)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    nodes, edges = load(a.graph)
    node_by_id = {n["id"]: n for n in nodes}
    subj = {n["id"]: graph_export.subject_of((n.get("props") or {}).get("domain"),
                                             n["id"]) for n in nodes}

    def is_cross(e):
        s, t = subj.get(e["source"]), subj.get(e["target"])
        return bool(s and t and s != t and e["type"] in
                    ("same_as", "proves", "derived_from", "defines", "related_to"))

    print("=" * 100)
    print("北极星 · 任务级可信完成率 —— %s" % os.path.relpath(a.graph, ROOT))
    print("  规模 %d 节点 / %d 边" % (len(nodes), len(edges)))
    print("=" * 100)
    print("  %-4s %-22s %8s %8s %8s %8s   %s" %
          ("ID", "任务族", "边数", "覆盖率", "达标数", "达标率", "主要缺档"))
    print("  " + "-" * 96)

    rows = []
    tot_e = tot_ok = 0
    for tid, name, desc, minlv, pred in TASKS:
        if tid == "T7":
            sel = [e for e in edges if is_cross(e)]
        else:
            sel = [e for e in edges if pred(e, node_by_id)]
        n = len(sel)
        labeled = [e for e in sel
                   if (e.get("props") or {}).get("verification_level")]
        ok = [e for e in labeled
              if vm.RANK.get((e["props"] or {}).get("verification_level"), -1)
              >= vm.RANK[minlv]]
        gap = collections.Counter((e["props"] or {}).get("verification_level", "未标")
                                  for e in sel if e not in ok)
        cov = 100.0 * len(labeled) / n if n else 0.0
        rate = 100.0 * len(ok) / n if n else 0.0
        rows.append({"task": tid, "name": name, "min_level": minlv,
                     "edges": n, "coverage_pct": round(cov, 2),
                     "ok": len(ok), "rate_pct": round(rate, 2),
                     "gaps": dict(gap.most_common(3)),
                     "desc": desc})
        tot_e += n
        tot_ok += len(ok)
        tag = " / ".join("%s×%d" % (k, v) for k, v in gap.most_common(2)) or "-"
        print("  %-4s %-22s %8d %7.1f%% %8d %7.1f%%   %s"
              % (tid, name, n, cov, len(ok), rate, tag))

    print("  " + "-" * 96)
    star = 100.0 * tot_ok / tot_e if tot_e else 0.0
    print("  %-27s %8d %8s %8d %7.1f%%   ← **北极星**" % ("合计", tot_e, "-", tot_ok, star))

    # 参考：**宽口径**跨域边（任意边类型，端点学科不同）—— 与旧北极星口径接续对照
    cross_all = [e for e in edges
                 if subj.get(e["source"]) and subj.get(e["target"])
                 and subj[e["source"]] != subj[e["target"]]]
    cok = [e for e in cross_all
           if vm.RANK.get((e.get("props") or {}).get("verification_level"), -1)
           >= vm.RANK["rule_checked"]]
    print("  [参考] 宽口径跨域边（任意类型）%d 条；其中 ≥rule_checked 仅 %d 条（%.1f%%）"
          % (len(cross_all), len(cok), 100.0 * len(cok) / len(cross_all) if cross_all else 0))

    # ---------------- 敏感性：T7 门槛下调 ----------------
    t7 = [r for r in rows if r["task"] == "T7"][0]
    for lv in ("cross_source", "rule_checked", "source_asserted"):
        sel = [e for e in edges if is_cross(e)
               and vm.RANK.get((e["props"] or {}).get("verification_level"), -1)
               >= vm.RANK[lv]]
        print("  [敏感性] T7 门槛降为 %-16s → 达标 %d / %d (%.1f%%)"
              % (lv, len(sel), t7["edges"], 100.0 * len(sel) / t7["edges"] if t7["edges"] else 0))

    # ---------------- 档位总览 ----------------
    lv_all = collections.Counter((e.get("props") or {}).get("verification_level", "未标")
                                 for e in edges)
    print("\n  【全图档位分布】")
    for k in vm.LEVELS + ["未标"]:
        if lv_all.get(k):
            print("    %-16s %6d  %5.1f%%" % (k, lv_all[k], 100.0 * lv_all[k] / len(edges)))
    print("=" * 100)

    if a.json:
        json.dump({"graph": os.path.relpath(a.graph, ROOT), "north_star_pct": round(star, 2),
                   "tasks": rows, "levels": dict(lv_all)},
                  open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("结果 ->", a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
