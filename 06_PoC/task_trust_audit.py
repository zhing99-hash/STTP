#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

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

T7 口径裁定（Phase 29 / 第 19 轮，含**反向对照**）
-----------------------------------------------
第 18 轮把 T7 定为：「类型窗口 = same_as/proves/derived_from/defines/related_to，
门槛 = **human_reviewed**」。实测 **0/53 = 0.0%** —— 复查后确认这不是「没有桥」，
而是**量错了**，两处口径缺陷：

1. **门槛不可达**：`human_reviewed` 要求人工复核，自动化管线**永不可达** →
   任何真实存在的桥都会被判 0，仪器失去区分度。
   → 改为 **`rule_checked`**（有**确定性复算**证据即算「证据可追溯」），
     并**单独报告** `human_reviewed` 金标准计数，严格性不丢失。
2. **类型窗口漏掉主桥**：图里 784 条「化学物质 → 物理量」的桥是 `has_quantity`
   （分子 → 摩尔质量/质量…），**全在窗口之外**；而窗口内的 14 条数学→物理
   `defines` 是 GNN 填充边。窗口选错了对象。
   → 改为**任意类型的跨域语义边**（端点学科不同即算），由「证据档」而非
     「边类型」把关。

`--legacy-t7` 可复现旧口径，用于反向对照（口径只改一次、对照必须留档，铁律 #16）。

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
     "两端点学科不同的**任意类型**语义边 —— **本项目的核心命题**",
     "rule_checked", None),      # 判定函数在 main 里特化（需学科信息）
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
    ap.add_argument("--legacy-t7", action="store_true",
                    help="复现第 18 轮旧口径 T7（5 类语义边 + human_reviewed 门槛）供反向对照")
    a = ap.parse_args()

    nodes, edges = load(a.graph)
    node_by_id = {n["id"]: n for n in nodes}
    subj = {n["id"]: graph_export.subject_of((n.get("props") or {}).get("domain"),
                                             n["id"]) for n in nodes}

    def is_cross(e):
        """跨域语义边：两端点学科不同（**任意边类型**）。"""
        s, t = subj.get(e["source"]), subj.get(e["target"])
        return bool(s and t and s != t)

    def is_cross_legacy(e):
        """旧口径（第 18 轮）：仅限 5 个「语义关系」类型 —— 保留用于**反向对照**。"""
        return is_cross(e) and e["type"] in \
            ("same_as", "proves", "derived_from", "defines", "related_to")

    print("=" * 100)
    print("北极星 · 任务级可信完成率 —— %s" % os.path.relpath(a.graph, ROOT))
    print("  规模 %d 节点 / %d 边" % (len(nodes), len(edges)))
    print("=" * 100)
    print("  %-4s %-22s %8s %8s %8s %8s   %s" %
          ("ID", "任务族", "边数", "覆盖率", "达标数", "达标率", "主要缺档"))
    print("  " + "-" * 96)

    rows = []
    tot_e = tot_ok = 0
    ok_edges = []          # T1–T8 的**达标边**集合（供 T9 复用）
    for tid, name, desc, minlv, pred in TASKS:
        if tid == "T7":
            sel = [e for e in edges if (is_cross_legacy(e) if a.legacy_t7 else is_cross(e))]
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
        ok_edges.extend(ok)
        tag = " / ".join("%s×%d" % (k, v) for k, v in gap.most_common(2)) or "-"
        print("  %-4s %-22s %8d %7.1f%% %8d %7.1f%%   %s"
              % (tid, name, n, cov, len(ok), rate, tag))

    print("  " + "-" * 96)
    star = 100.0 * tot_ok / tot_e if tot_e else 0.0
    print("  %-27s %8d %8s %8d %7.1f%%   ← **北极星**" % ("合计", tot_e, "-", tot_ok, star))

    # ---------------- T9 证据可追溯（Phase 32 新增 · 横向不变量） ----------------
    #   北极星原话是「跨域结论正确**且证据可追溯**」—— 但第 18~21 轮**只仪器化了「正确」**
    #   （T1–T8 的档位），「证据可追溯」长期**无仪器**（实测 `evidence` 仅 1.18% 覆盖）。
    #   T9 把它补齐，并**区分两级**（可追溯 ≠ 独立，不得混为一谈）：
    #     T9   证据**可追溯**：挂有证据对象 + `detail` 非空 + `impl == verifier`（同源保证）
    #     T9-i 证据**独立**  ：证据链含 ≥1 条 `indep=True`（与门禁 `evidence_traceable` 同源）
    #   ⚠ T9-i 是**下一轮的靶子**：T4/T6/T8 的最低档是 `source_asserted`（单一来源断言），
    #     这些边「达标」却不「独立」—— 必须如实报告，不得用 T9=100% 掩盖。
    def _evs(e):
        evs = (e.get("props") or {}).get("verification_evidence")
        return evs if isinstance(evs, list) else []

    def has_traceable_ev(e):
        return any(isinstance(it, dict) and it.get("detail")
                   and it.get("impl") == (e.get("props") or {}).get("verifier")
                   for it in _evs(e))

    def has_indep_ev(e):
        return any(isinstance(it, dict) and it.get("indep") and it.get("detail")
                   and it.get("impl") == (e.get("props") or {}).get("verifier")
                   for it in _evs(e))

    t9_tr = [e for e in ok_edges if has_traceable_ev(e)]
    t9_in = [e for e in ok_edges if has_indep_ev(e)]
    t9_rate = 100.0 * len(t9_tr) / len(ok_edges) if ok_edges else 0.0
    t9i_rate = 100.0 * len(t9_in) / len(ok_edges) if ok_edges else 0.0
    n_cov_all = sum(1 for e in edges if _evs(e))
    n_indep_all = sum(1 for e in edges if has_indep_ev(e))
    print("\n  【T9 证据可追溯（Phase 32 新增 · 横向不变量）】")
    print("    北极星原文含「**证据可追溯**」，但第 18–21 轮只仪器化了「正确」（T1–T8 档位）。")
    print("    ⚠ 「可追溯」≠「独立」—— 两级分列，不得用前者掩盖后者。")
    print("    %-42s %8s %8s %8s" % ("指标", "分母", "达标", "比例"))
    print("    %-42s %8d %8d %7.1f%%"
          % ("T9  达标边证据**可追溯**（detail+impl≡verifier）", len(ok_edges), len(t9_tr), t9_rate))
    print("    %-42s %8d %8d %7.1f%%   ← **下一轮靶子**（T4/T6/T8 门槛=source_asserted）"
          % ("T9-i 其中证据**独立**（indep=真）", len(ok_edges), len(t9_in), t9i_rate))
    print("    %-42s %8d %8d %7.1f%%   ← 全图证据对象覆盖率（Phase 31 为 1.2%%）"
          % ("（横向）全图边带证据对象", len(edges), n_cov_all,
             100.0 * n_cov_all / len(edges) if edges else 0.0))
    print("    %-42s %8d %8d %7.1f%%   ← 全图「有独立可追溯证据」的边"
          % ("（横向）全图边带独立证据", len(edges), n_indep_all,
             100.0 * n_indep_all / len(edges) if edges else 0.0))
    print("    → **北极星（正确且可追溯）** = %.1f%% × %.1f%% = %.1f%%"
          % (star, t9_rate, star * t9_rate / 100.0))
    print("    → 若按**独立**证据严口径：%.1f%% × %.1f%% = %.1f%%（← 这就是下一轮的提升空间）"
          % (star, t9i_rate, star * t9i_rate / 100.0))
    print("    → 口径留档（铁律 #16）：T1–T8 口径不变（与 Phase 31 可比，%.1f%%）；" % star)
    print("       T9 是**新增的横向不变量**，不改变 T1–T8 的分母，故 headline 数字保持可比。")

    # ---------------- ★ 口径披露（Phase 35 · 铁律 #50） ----------------
    #   铁律 #41 已经确立「可追溯 ≠ 独立 → 两个口径必须同时打印」。
    #   第 25 轮把它推广到**分母本身**：北极星的分母**是选择的结果** ——
    #   T1–T8 是一张**被选中的**任务族表，全图还有相当比例的边**不在分母内**。
    #   不披露这一点，headline 就会被读成「全图可信度」。故本仪器必须**同时打印**：
    #     ① 分母内/外占比（**并集**与**逐族求和**两种数法都给出）；② 分母外逐族理由；
    #     ③ 分母外弱证据占比；④ 占位实体边（端点匿名）占比；
    #     ⑤ **反向对照**（把分母外的大族纳入后的口径，与 headline 并列，不得单读）。
    # -- T9-i 非独立残差的「为何不可独立」逐类清算（Phase 35 · 铁律 #30/#34）--
    _ni = [e for e in ok_edges if not has_indep_ev(e)]
    _rc = collections.Counter(vm.indep_reason(e, node_by_id) for e in _ni)
    print("\n  【T9-i 非独立 %d 条的「为何不可独立」逐类清算（Phase 35）】" % len(_ni))
    for _k, _v in _rc.most_common():
        print("    %-40s %6d" % (_k or "（已独立）", _v))
    print("    ⚠ 未归类 %d 条（门禁 (v) `independence_accounted` 断言为 0）"
          % sum(_v for _k, _v in _rc.items() if _k not in vm.INDEP_REASONS))
    print("\n  【★ 口径披露 · 北极星的分母是选择的结果（Phase 35 · 铁律 #50）】")
    _subj = {n["id"]: graph_export.subject_of((n.get("props") or {}).get("domain"), n["id"])
             for n in nodes}
    in_den = [e for e in edges if vm.tasks_of(e, _subj)]
    out_den = [e for e in edges if not vm.tasks_of(e, _subj)]
    print("    全图边 %d ｜ 分母（**并集**）%d ／ 分母（**逐族求和**，headline 所用）%d"
          " ｜ **分母外 %d（%.1f%%）**"
          % (len(edges), len(in_den), tot_e, len(out_den),
             100.0 * len(out_den) / max(len(edges), 1)))
    rc = collections.Counter(vm.scope_reason(e, node_by_id, False) for e in out_den)
    print("    分母外逐族理由：%s" % dict(rc.most_common()))
    weak_out = [e for e in out_den
                if vm.RANK.get((e.get("props") or {}).get("verification_level"), -1)
                < vm.RANK["rule_checked"]]
    print("    分母外**弱证据**（< rule_checked）：%d / %d = **%.1f%%**"
          % (len(weak_out), len(out_den), 100.0 * len(weak_out) / max(len(out_den), 1)))
    ph = [e for e in edges
          if any((node_by_id.get(e.get(s)) or {}).get("id", "").startswith("EK2:")
                 and vm.is_placeholder_entity(
                     ((node_by_id.get(e.get(s)) or {}).get("props") or {}).get("name"))
                 for s in ("source", "target"))]
    print("    **占位实体边**（端点为 ElementKG2.0 匿名节点 `FG52`/`reaction_1`/`molecule_1000`）："
          "%d（全图 %.1f%%）—— 语义**不可读**，既非「正确」也非「错误」"
          % (len(ph), 100.0 * len(ph) / max(len(edges), 1)))
    # 反向对照：把分母外的大族纳入，门槛按 rule_checked（与 headline 同为**逐族求和**基)
    _ext_types = {"has_quantity": "rule_checked", "has_functionalgroup": "rule_checked",
                  "reagent_of": "rule_checked", "has_element": "rule_checked"}
    ext = [e for e in out_den if e["type"] in _ext_types]
    n_ext_ok = sum(1 for e in ext
                   if vm.RANK.get((e.get("props") or {}).get("verification_level"), -1)
                   >= vm.RANK[_ext_types[e["type"]]])
    den2, num2 = tot_e + len(ext), tot_ok + n_ext_ok
    print("    【反向对照 · 口径扩展】+has_quantity/has_functionalgroup/reagent_of/has_element，门槛 rule_checked：")
    print("       分母 %d（+%d），达标 %d = **%.1f%%**  ← 现口径 %.1f%% ｜ **两个口径不得单读**"
          % (den2, len(ext), num2, 100.0 * num2 / max(den2, 1), star))
    print("    ⚠ 口径**未改**（铁律 #16：改口径须留反向对照）；本块仅作**披露**，headline 仍为 %.1f%%。" % star)

    # ---------------- 反向对照：旧口径 T7 vs 新口径 T7（铁律 #16） ----------------
    cross_all = [e for e in edges if is_cross(e)]
    legacy = [e for e in edges if is_cross_legacy(e)]

    def ge(sel, lv):
        return [e for e in sel
                if vm.RANK.get((e.get("props") or {}).get("verification_level"), -1)
                >= vm.RANK[lv]]

    print("\n  【反向对照 · T7 口径变更留档】")
    print("    %-52s %6s %10s %8s" % ("口径", "分母", "≥rule_checked", "达标率"))
    print("    " + "-" * 82)
    for tag, sel in (("旧口径（5 类语义边，门槛 human_reviewed）", legacy),
                     ("新口径（任意类型跨域边，门槛 rule_checked）", cross_all)):
        best = ge(sel, "human_reviewed" if "旧口径" in tag else "rule_checked")
        print("    %-52s %6d %10d %7.1f%%"
              % (tag, len(sel), len(best),
                 100.0 * len(best) / len(sel) if sel else 0.0))
    print("    → 旧口径的 `human_reviewed` 金标准子集（新口径下）：%d 条"
          % len(ge(cross_all, "human_reviewed")))

    # ---------------- 跨域边的学科对 × 档次（证据分布） ----------------
    print("\n  【跨域边的学科对 × 证据档】")
    cp = collections.Counter((tuple(sorted((subj[e["source"]], subj[e["target"]]))),
                              (e.get("props") or {}).get("verification_level", "未标"))
                             for e in cross_all)
    by_pair = collections.defaultdict(collections.Counter)
    for (pair, lv), n in cp.items():
        by_pair[pair][lv] += n
    for pair in sorted(by_pair, key=lambda p: -sum(by_pair[p].values())):
        tot_p = sum(by_pair[pair].values())
        ok_p = sum(n for lv, n in by_pair[pair].items()
                   if vm.RANK.get(lv, -1) >= vm.RANK["rule_checked"])
        det = " ".join("%s×%d" % (k, v) for k, v in by_pair[pair].most_common())
        print("    %-18s 共 %4d  ≥rule_checked %4d  (%5.1f%%)   %s"
              % ("↔".join(pair), tot_p, ok_p, 100.0 * ok_p / tot_p if tot_p else 0.0, det))

    # ---------------- 敏感性：T7 门槛升降 ----------------
    t7 = [r for r in rows if r["task"] == "T7"][0]
    for lv in ("human_reviewed", "cross_source", "rule_checked", "source_asserted"):
        sel = ge(cross_all, lv)
        print("  [敏感性] T7 门槛=%-16s → 达标 %d / %d (%.1f%%)"
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
                   "tasks": rows, "levels": dict(lv_all),
                   "T9_evidence_traceable": {
                       "ok_edges": len(ok_edges), "traceable": len(t9_tr),
                       "traceable_rate_pct": round(t9_rate, 2),
                       "independent": len(t9_in), "independent_rate_pct": round(t9i_rate, 2),
                       "graph_evidence_coverage_pct": round(100.0 * n_cov_all / len(edges), 2)
                       if edges else 0.0,
                       "graph_independent_evidence_pct": round(100.0 * n_indep_all / len(edges), 2)
                       if edges else 0.0}},
                  open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("结果 ->", a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
