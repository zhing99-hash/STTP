# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""元素周期性趋势推理（STTP · A7 阶段 2）。

把元素性质从「节点属性」升级为「图结构 + 可推理规律」：

  1. **物理量化**：为 12 项元素性质建物理量节点 `PQ:el:<property>`
     （`domain=chem.element_property`），并新增
     `Element -[has_quantity]-> PQ:el:<property>` 边，数值 / 单位落在边上
     —— 与既有 `Molecule -[has_quantity]-> PhysicalQuantity` 同构。
  2. **趋势推理**：在同周期 / 同族内沿原子序数做 Spearman 秩相关，
     检验教科书周期律（电负性沿周期递增、沿族递减；原子半径反之 …），
     结论写回物理量节点的 `trend_in_period` / `trend_in_group` 属性。

单位说明：ElementKG 的 OWL 未标注单位，下表由数值量级 + 化学常识推定，
并经已知值核验（Os 密度 22590→22.59 g/cm³；Ag 热导率 429 W/(m·K)；
H 比热 14300 J/(kg·K)），写入节点的 `unit_inferred=True` 以标记来源。

用法：
    python 09_科研扩展/9_inference/periodic_trends.py            # 只分析，出报告
    python 09_科研扩展/9_inference/periodic_trends.py --apply    # 写回图谱 + 出 delta
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))

from element_reference import BY_SYMBOL  # noqa: E402

NORMALIZED = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
TRENDS_OUT = os.path.join(ROOT, "06_PoC", "etl", "periodic_trends.json")
DELTA_OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase16_trends_delta.json")

MIN_SAMPLES = 3          # 至少 3 个元素才做秩相关
RHO_THRESHOLD = 0.60     # |ρ| ≥ 阈值才判为「显著方向」

# 性质 → (中文名, 单位, 类别)
PROPERTIES = {
    "electronegativity": ("电负性", "Pauling", "atomic"),
    "atomic_radius": ("原子半径", "pm", "atomic"),
    "ionization_energy": ("第一电离能", "kJ/mol", "atomic"),
    "electron_affinity": ("电子亲和能", "kJ/mol", "atomic"),
    "melting_point": ("熔点", "°C", "bulk"),
    "boiling_point": ("沸点", "°C", "bulk"),
    "density": ("密度", "kg/m³", "bulk"),
    "conductivity": ("热导率", "W/(m·K)", "bulk"),
    "heat_capacity": ("比热容", "J/(kg·K)", "bulk"),
    "hardness": ("硬度（维氏）", "MPa", "bulk"),
    "bulk_modulus": ("体积模量", "GPa", "bulk"),
    "atomic_weight": ("相对原子质量", "g/mol", "atomic"),
}

# 教科书周期律：性质沿周期 / 族（随原子序数增大）的方向期望
#   +1 递增 / -1 递减 / 0 无定论（不参与校验）
THEORY = {
    ("electronegativity", "period"): +1,
    ("electronegativity", "group"): -1,
    ("atomic_radius", "period"): -1,
    ("atomic_radius", "group"): +1,
    ("ionization_energy", "period"): +1,
    ("ionization_energy", "group"): -1,
    ("electron_affinity", "period"): +1,
    ("density", "group"): +1,
    ("atomic_weight", "period"): +1,
}

DELTA_NOUN = {"period": "周期", "group": "族"}


# ------------------------------------------------------------------ 基本统计
def spearman(xs, ys):
    """Spearman 秩相关（含并列秩平均）。样本不足或方差为 0 返回 None。"""
    n = len(xs)
    if n < MIN_SAMPLES:
        return None

    def rank(a):
        order = sorted(range(n), key=lambda i: a[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and a[order[j + 1]] == a[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = sum((v - mx) ** 2 for v in rx) ** 0.5
    dy = sum((v - my) ** 2 for v in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def sign_of(rho):
    if rho is None:
        return 0
    if rho >= RHO_THRESHOLD:
        return 1
    if rho <= -RHO_THRESHOLD:
        return -1
    return 0


def pct_monotone(xs, ys):
    """同向对比例：相邻（按 x 排序）对中 y 也同向的比例。"""
    pairs = sorted(zip(xs, ys))
    inc = dec = tot = 0
    for i in range(len(pairs) - 1):
        dy = pairs[i + 1][1] - pairs[i][1]
        if dy > 0:
            inc += 1
            tot += 1
        elif dy < 0:
            dec += 1
            tot += 1
    if tot == 0:
        return None
    return max(inc, dec) / tot


# ------------------------------------------------------------------ 主流程
def build_matrix(elements):
    """符号 → {property: value}（只保留数值）。"""
    mat = {}
    for n in elements:
        p = n.get("props") or {}
        sym = p.get("symbol")
        if not sym:
            continue
        mat[sym] = {k: v for k, v in p.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    return mat


def analyse(mat):
    """按周期 / 族分组，逐性质做秩相关，返回结构化结论。"""
    scopes = {}   # scope -> {key -> [symbols]}
    scopes["period"] = defaultdict(list)
    scopes["group"] = defaultdict(list)
    for sym in mat:
        ref = BY_SYMBOL.get(sym)
        if not ref:
            continue
        if ref["period"]:
            scopes["period"][ref["period"]].append(sym)
        if ref["group"] is not None:
            scopes["group"][ref["group"]].append(sym)
        elif ref["series"]:
            scopes["group"]["series:" + ref["series"]].append(sym)

    detail = {}       # (prop, scope, key) -> 统计
    summary = {}      # prop -> 跨分组汇总
    for prop in PROPERTIES:
        per_scope = {"period": [], "group": []}
        for scope in ("period", "group"):
            for key, syms in scopes[scope].items():
                pairs = [(BY_SYMBOL[s]["atomic_number"], mat[s].get(prop))
                         for s in syms if isinstance(mat[s].get(prop), (int, float))]
                if len(pairs) < MIN_SAMPLES:
                    continue
                pairs.sort()
                xs = [a for a, _ in pairs]
                ys = [b for _, b in pairs]
                rho = spearman(xs, ys)
                if rho is None:
                    continue
                sg = sign_of(rho)
                expect = THEORY.get((prop, scope), 0)
                verdict = ("NO_THEORY" if expect == 0
                           else "VERIFIED" if sg == expect
                           else "WEAK" if sg == 0 else "CONTRADICTED")
                rec = {"scope": scope, "key": key, "n": len(pairs), "rho": round(rho, 4),
                       "sign": sg, "expected": expect, "verdict": verdict,
                       "pct_monotone": round(pct_monotone(xs, ys) or 0, 4),
                       "members": sorted(syms, key=lambda s: BY_SYMBOL[s]["atomic_number"])}
                detail[(prop, scope, key)] = rec
                if sg != 0:
                    per_scope[scope].append(rec)

        # 汇总：显著分组中方向与理论一致的比例
        summ = {}
        for scope in ("period", "group"):
            recs = per_scope[scope]
            expect = THEORY.get((prop, scope), 0)
            if expect == 0 or not recs:
                summ[scope] = {
                    "direction": (sign_of(sum(r["rho"] for r in recs) / len(recs))
                                  if recs else 0),
                    "n_groups": len(recs), "consistent": None}
                continue
            ok = sum(1 for r in recs if r["sign"] == expect)
            avg = sum(r["rho"] for r in recs) / len(recs)
            summ[scope] = {
                "direction": expect if ok / len(recs) >= 0.5 else -expect,
                "avg_rho": round(avg, 4),
                "n_groups": len(recs),
                "consistent": round(ok / len(recs), 4),
                "verdict": "VERIFIED" if ok / len(recs) >= 0.6 else "WEAK",
            }
        summary[prop] = summ
    return detail, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写回图谱并产出 Aura delta")
    ap.add_argument("--input", default=NORMALIZED)
    args = ap.parse_args()

    data = json.load(open(args.input, encoding="utf-8"))
    elements = [n for n in data["nodes"]
                if "Element" in (n.get("labels") or []) or n.get("type") == "Element"]
    mat = build_matrix(elements)

    detail, summary = analyse(mat)

    print("=" * 74)
    print("A7 · 元素周期性趋势推理   [%s]" % ("APPLY" if args.apply else "ANALYSE"))
    print("=" * 74)
    print("元素 %d 个；性质 %d 项\n" % (len(mat), len(PROPERTIES)))

    # ---------------------------------------------------- 核心周期律校验
    print("[1] 教科书周期律校验（同周期 / 同族，随原子序数）")
    for prop, (cn, unit, _) in PROPERTIES.items():
        line = []
        for scope in ("period", "group"):
            s = summary[prop].get(scope) or {}
            if s.get("consistent") is None:
                continue
            arrow = "↑" if s["direction"] > 0 else "↓" if s["direction"] < 0 else "—"
            mark = "✅" if s.get("verdict") == "VERIFIED" else "⚠"
            line.append("%s内 %s (ρ̄=%+.2f, %d 组, 一致率 %.0f%%) %s"
                        % (DELTA_NOUN[scope], arrow, s.get("avg_rho", 0),
                           s["n_groups"], s["consistent"] * 100, mark))
        if line:
            print("  %-9s %s" % (cn, " | ".join(line)))
    print()

    # ---------------------------------------------------- 反例
    print("[2] 与教科书规律相悖的分组（CONTRADICTED）")
    bad = [(p, r) for (p, sc, k), r in detail.items() if r["verdict"] == "CONTRADICTED"]
    if not bad:
        print("  无 ✅")
    for p, r in sorted(bad, key=lambda x: -abs(x[1]["rho"]))[:10]:
        print("  %-9s %s内 %-8s n=%-3d ρ=%+.2f  (期望 %+d)"
              % (PROPERTIES[p][0], DELTA_NOUN[r["scope"]], r["key"], r["n"], r["rho"], r["expected"]))
    print()

    # ---------------------------------------------------- 明细样例
    print("[3] 明细样例（同周期内电负性）")
    for key in sorted(k for (p, s, k) in detail if p == "electronegativity" and s == "period"):
        r = detail[("electronegativity", "period", key)]
        print("  周期 %-6s n=%-3d ρ=%+.3f  单调率=%.2f  %s" %
              (key, r["n"], r["rho"], r["pct_monotone"], r["verdict"]))

    # ---------------------------------------------------- 输出
    out = {
        "meta": {
            "phase": "A7-periodic-trends",
            "source": "09_科研扩展/9_inference/periodic_trends.py",
            "truth_source": "11_真实数据/element_reference.py",
            "min_samples": MIN_SAMPLES, "rho_threshold": RHO_THRESHOLD,
            "note": "单位由数值量级 + 化学常识推定（OWL 未标注）",
        },
        "tail_rules": {  # 每条周期律的「规律」表述
            "%s@%s" % (p, s): {"direction": v}
            for (p, s), v in THEORY.items()
        },
        "properties": {p: {"cn": c, "unit": u, "category": k,
                           "trend_in_period": summary[p]["period"],
                           "trend_in_group": summary[p]["group"]}
                       for p, (c, u, k) in PROPERTIES.items()},
        "detail": [{"property": p, **r}
                   for (p, sc, k), r in sorted(detail.items(),
                                               key=lambda x: (x[0][0], x[0][1], str(x[0][2])))],
    }
    os.makedirs(os.path.dirname(TRENDS_OUT), exist_ok=True)
    json.dump(out, open(TRENDS_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n趋势分析 → %s" % os.path.relpath(TRENDS_OUT, ROOT))

    if not args.apply:
        print("未写回图谱。加 --apply 执行。")
        return

    # ------------------------------------------------------------ 写回图谱
    nodes, edges = data["nodes"], data["edges"]
    eid_of = {n["id"] for n in nodes}
    existing_edge_ids = {e["id"] for e in edges}          # 幂等：重复 --apply 不重复加边
    existing_prop_node = {}
    new_nodes, new_edges = [], []
    for prop, (cn, unit, cat) in PROPERTIES.items():
        pid = "PQ:el:%s" % prop
        sp = summary[prop]
        props = {
            "name": cn, "en_name": prop, "symbol": prop,
            "domain": "chem.element_property", "ntype": "physical_quantity",
            "unit": unit, "unit_inferred": True, "category": cat,
            "source": "ElementKG2.0",
            "trend_in_period": sp["period"].get("direction", 0),
            "trend_in_group": sp["group"].get("direction", 0),
        }
        if sp["period"].get("avg_rho") is not None:
            props["trend_in_period_rho"] = sp["period"]["avg_rho"]
            props["trend_in_period_consistency"] = sp["period"].get("consistent")
        if sp["group"].get("avg_rho") is not None:
            props["trend_in_group_rho"] = sp["group"]["avg_rho"]
            props["trend_in_group_consistency"] = sp["group"].get("consistent")
        if pid in eid_of:
            existing_prop_node[prop] = pid   # 已存在则只更新属性
        else:
            new_nodes.append({"id": pid, "labels": ["Entity", "PhysicalQuantity"], "props": props})
            eid_of.add(pid)

        # has_quantity 边：Element → PQ:el:<prop>
        cnt = 0
        for n in elements:
            p = n.get("props") or {}
            sym = p.get("symbol")
            val = p.get(prop)
            if sym is None or not isinstance(val, (int, float)) or isinstance(val, bool):
                continue
            src = n["id"]
            eid = "has_quantity|%s|%s" % (src, pid)
            if eid in existing_edge_ids:
                continue
            existing_edge_ids.add(eid)
            new_edges.append({
                "id": eid, "source": src, "target": pid, "type": "has_quantity",
                "kind": "element_property",
                "props": {"value": val, "unit": unit, "element_symbol": sym,
                          "explicit_or_inferred": "explicit", "confidence": 0.9,
                          "source": "ElementKG2.0", "unit_inferred": True},
            })
            cnt += 1
        print("  %-18s → %-26s %3d 条 has_quantity" % (prop, pid, cnt))

    # 写回前备份原始输入（仅在首次执行时落盘）
    backup = args.input.replace(".json", ".before_trends.json")
    if not os.path.exists(backup):
        json.dump(data, open(backup, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("备份 → %s" % os.path.relpath(backup, ROOT))

    nodes.extend(new_nodes)
    edges.extend(new_edges)
    json.dump(data, open(args.input, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n已写回 %s（新增 %d 节点 / %d 边；当前 %d 节点 / %d 边）"
          % (os.path.relpath(args.input, ROOT), len(new_nodes), len(new_edges),
             len(nodes), len(edges)))

    # delta 从全量筛出 A7 产物（保证重复执行时仍完整、幂等）
    delta_nodes = [n for n in nodes if n["id"].startswith("PQ:el:")]
    delta_edges = [e for e in edges
                   if e.get("type") == "has_quantity"
                   and str(e.get("target", "")).startswith("PQ:el:")]
    delta = {
        "meta": {"phase": "A7-element-properties", "source": "periodic_trends.py",
                 "reason": "元素性质物理量化：PQ:el:<prop> 节点 + Element-[has_quantity]-> 边",
                 "node_count": len(delta_nodes), "edge_count": len(delta_edges)},
        "nodes": delta_nodes, "delete_nodes": [], "edges": delta_edges, "delete_edges": [],
    }
    os.makedirs(os.path.dirname(DELTA_OUT), exist_ok=True)
    json.dump(delta, open(DELTA_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("Aura delta → %s（节点 %d · 边 %d）"
          % (os.path.relpath(DELTA_OUT, ROOT), len(delta_nodes), len(delta_edges)))


if __name__ == "__main__":
    main()
