#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""phase29_bridge_delta.py —— 第 19 轮 · 跨域桥专项（只读生成 delta）

背景（一手勘察结论，详见 `07_交付物/Phase29_跨域桥专项报告_20261010.md`）
------------------------------------------------------------------------
第 18 轮把北极星最短板标为 **T7 跨域桥 0/53 = 0.0%**。本轮逐条复查后确认：
**T7=0 不是「没有桥」，而是「量错了」**（口径缺陷见 `task_trust_audit.py` 的裁定），
但**内容侧也确有水分**：

* 784 条「化学物质 → 物理量」的 `has_quantity` 边里，**700 条指向 `PB:pq:molar_mass`**，
  其 `value` 本可由「原子量 × 化学式计数」**确定性复算** —— 却被第 18 轮分类器
  误判为 `model_inferred`（它只认 rationale 的模板，不认「可复算」本身）；
* 其余 84 条中 **42 条自相矛盾**：rationale 写「属**质量**类物理量」，
  目标却是 动能 / 能量 / 内能（同一条理由被原样粘贴到 8 个互斥目标上）；
* 独立复算还抓出 **18 条陈旧值**：`CO3-2` 记成 28.01（=CO）、`MnO4-` 记成 70.937（=MnO）…
  根因是旧版电荷剥离正则 `\\d*[+-]\\d*$` 贪婪吞掉「末尾元素下标」（`gnn_infer.py`
  已修，但**存量 value 从未重算**）。

本脚本做四件事
--------------
1) **升级 700 条摩尔质量桥**：独立复算通过 → `rule_checked`；再与 PubChem
   `pubchem_molecular_weight` 一致 → `cross_source`（**两个独立来源一致**）。
2) **修正 18 条陈旧值**：值改为「独立复算 ≡ PubChem」共同确认的正确值，并留痕
   `verification_note`；等级记为 `cross_source`。
3) **撤回 42 条自相矛盾边**（删边 + meta 留档）。
4) **新建 NIST WebBook 热化学桥**：`Molecule --has_quantity--> <物理量表节点>`，
   证据 = ① 单位经量纲表独立复算一致（`rule_checked`）；
   ② WebBook 页面内 **≥2 条独立文献**在容差内吻合（`cross_source`）。

用法
----
    python 11_真实数据/phase29_bridge_delta.py                 # 只读，打印统计
    python 11_真实数据/phase29_bridge_delta.py --out <p>       # 生成 delta
    python 11_真实数据/phase29_bridge_delta.py --dry           # 不落盘
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import statistics
import sys

# 聚合体 / 抽象类分子式简写：`(C2H3NO)n`
_POLYMER = re.compile(r"\(\s*[A-Za-z0-9]+\s*\)\s*n", re.I)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import dimension_table as dm              # noqa: E402
import element_reference as er            # noqa: E402
import verification_model as vm           # noqa: E402
import webbook_ingest as wb               # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
WB_RAW = os.path.join(HERE, "webbook_raw.json")
OUT_DEFAULT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase29_bridge_delta.json")

# 热化学量 → （目标物理量节点, 期望量纲, 中文名）
# 期望量纲**由量纲表自身的量派生**（不外部硬编码）：
#   molar_energy  = energy / amount
#   molar_entropy = (energy / temperature) / amount = entropy / amount
def _expected_dims():
    me = dict(dm.dim_of("molar_energy") or {})
    en = dict(dm.dim_of("entropy") or {})
    ent = dict(en)
    ent["N"] = ent.get("N", 0.0) - 1.0
    return {"std_enthalpy_of_formation": me, "std_entropy": ent}


QUANT = {
    "std_enthalpy_of_formation": ("PB:pq:molar_energy", "标准摩尔生成焓 ΔfH°(g)"),
    "std_entropy": ("PQ:molar_entropy", "标准摩尔熵 S°(g)"),
}
EXPECT_DIM = _expected_dims()


def _norm_dim(d):
    return {k: float(v) for k, v in (d or {}).items() if abs(float(v)) > 1e-9}


def _year_of(ref):
    """从 `作者, 1998` 形态中取年份（取不到记 0，用于排序当"最旧"处理）。"""
    m = re.search(r"(\d{4})", str(ref or ""))
    return int(m.group(1)) if m else 0


def unit_dim(unit: str):
    key = unit.lower().replace(" ", "")
    for k, v in wb.UNIT_DIM.items():
        if k.replace(" ", "") == key:
            return v
    return None


def main():
    ap = argparse.ArgumentParser(description="Phase 29 跨域桥 delta 生成器（只读）")
    ap.add_argument("--graph", default=NORM)
    ap.add_argument("--webbook", default=WB_RAW)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--no-webbook", action="store_true", help="跳过 WebBook 建桥")
    ap.add_argument("--agree-tol", type=float, default=0.005,
                    help="多条文献吻合判定的相对容差（默认 0.5%）")
    a = ap.parse_args()

    nodes, edges = vm.load_norm(a.graph)
    node_by_id = {n["id"]: n for n in nodes}
    print("=" * 94)
    print("Phase 29 · 跨域桥专项 delta —— 基准 %s" % os.path.relpath(a.graph, ROOT))
    print("  %d 节点 / %d 边" % (len(nodes), len(edges)))
    print("=" * 94)

    res, ctx = vm.classify_all(nodes, edges)
    lv = collections.Counter(r["verification_level"] for _, r in res)
    print("\n[1] 重跑分层（扩展后）")
    for k in vm.LEVELS:
        print("    %-16s %6d  %5.1f%%" % (k, lv.get(k, 0), 100.0 * lv.get(k, 0) / len(res)))

    # ---------------- 2. 撤回自相矛盾的跨域边 ----------------
    del_edges, del_keys = [], set()
    for e, r in res:
        eid = e.get("id") or "%s|%s|%s" % (e["type"], e["source"], e["target"])
        if e["type"] == "has_quantity" and ctx["hq_class_ok"].get(eid) is not None \
                and ctx["hq_class_ok"][eid][0] is False:
            del_keys.add((e["source"], e["type"], e["target"]))
            del_edges.append({"source": e["source"], "target": e["target"], "type": e["type"],
                              "kind": (e.get("props") or {}).get("kind") or ""})
    print("\n[2] 撤回自相矛盾的跨域边（rationale 声称的类 ≠ 目标真量纲）：%d 条" % len(del_edges))
    c = collections.Counter((d["target"], (node_by_id.get(d["target"], {}).get("props") or {}).get("name"))
                            for d in del_edges)
    for k, v in c.most_common():
        print("      -> %-22s %s  ×%d" % (k[0], k[1], v))

    # ---------------- 2b. 撤回「抽象类节点」的摩尔质量桥 ----------------
    # `Protein (polymer)` / `DNA (abstract)` 的 `formula` 是聚合体简写 `(C2H3NO)n`，
    # **本就没有单一摩尔质量**；两条边却都写 `value = 43.025`（同一占位常数）→ 是假话。
    # 判据是确定性的：独立复算不可行 **且** 分子式是聚合体/抽象简写。
    del_abstract = []
    for e, r in res:
        eid = e.get("id") or "%s|%s|%s" % (e["type"], e["source"], e["target"])
        st = ctx["mass_ok"].get(eid)
        if st is None or st[0] is not None:
            continue
        sp = (node_by_id.get(e["source"], {}) or {}).get("props") or {}
        fs = str(sp.get("formula") or "")
        nm = str(sp.get("name") or "")
        if _POLYMER.search(fs) or "abstract" in nm.lower() or "polymer" in nm.lower():
            del_keys.add((e["source"], e["type"], e["target"]))
            del_abstract.append({"source": e["source"], "target": e["target"], "type": e["type"],
                                 "kind": (e.get("props") or {}).get("kind") or "",
                                 "reason": "abstract_class_no_molar_mass",
                                 "note": "抽象类节点（%s，式 %s）无单一摩尔质量，原值 %s 为占位常数"
                                         % (nm, fs, (e.get("props") or {}).get("value"))})
    del_edges.extend(del_abstract)
    print("[2b] 撤回「抽象类节点」的摩尔质量桥：%d 条 %s"
          % (len(del_abstract), [d["source"] for d in del_abstract]))

    # ---------------- 3. 修正 18 条陈旧摩尔质量值 ----------------
    repaired = {}
    for e, r in res:
        eid = e.get("id") or "%s|%s|%s" % (e["type"], e["source"], e["target"])
        st = ctx["mass_ok"].get(eid)
        if st is None or st[0] is not False:
            continue
        sp = (node_by_id.get(e["source"], {}) or {}).get("props") or {}
        f = sp.get("pubchem_formula") or sp.get("formula")
        comp = vm.parse_formula_independent(f)
        tot = round(sum(float(er.weight_of(s)) * float(n) for s, n in comp.items()), 4)
        pmm = sp.get("pubchem_molecular_weight")
        if pmm is None or abs(float(pmm) - tot) / max(tot, 1e-9) > 0.01:
            continue                    # 仅修复「独立复算 ≡ PubChem」共同确认的错误值
        old = (e.get("props") or {}).get("value")
        repaired[eid] = (tot, old)
    print("\n[3] 修正陈旧摩尔质量值（独立复算 ≡ PubChem 共同确认）：%d 条" % len(repaired))
    for eid, (new, old) in list(repaired.items())[:6]:
        print("      %-58s %s -> %s" % (eid[:58], old, new))

    # ---------------- 4. 边重标 ----------------
    edge_rows, changed = [], 0
    for e, r in res:
        key = (e["source"], e["type"], e["target"])
        if key in del_keys:
            continue
        eid = e.get("id") or "%s|%s|%s" % (e["type"], e["source"], e["target"])
        props = {"verification_level": r["verification_level"],
                 "verification_scope": r["verification_scope"],
                 "verifier": r["verifier"],
                 "verified": vm.is_verified(r["verification_level"])}
        if r.get("note"):
            props["verification_note"] = r["note"]
        if eid in repaired:                     # 陈旧 value 修复（见 docstring 第 2 点）
            new, old = repaired[eid]
            props.update({
                "value": new, "unit": "g/mol",
                "verification_level": "cross_source",
                "verification_scope": "molar_mass_cross_source",
                "verifier": "element_reference.py × PubChem(pubchem_molecular_weight)",
                "verified": True,
                "verification_note": ("2026-10-10 修正陈旧摩尔质量 %s -> %s g/mol："
                                      "旧值由已废弃的贪婪电荷剥离正则（`\\d*[+-]\\d*$`）"
                                      "吞掉末尾元素下标所得；新值经独立复算与 PubChem 双确认"
                                      % (old, new)),
                "evidence": ["独立复算 Σ(atomic_weight × count) = %.4f g/mol" % new,
                             "PubChem pubchem_molecular_weight = %s" % sp_mw(node_by_id, e["source"])],
            })
        # 只写**真正变化**的边，保持 delta 精简。
        # ⚠ 判定**只看 4 个核心字段**：`verification_note` 不是判据 ——
        #   否则「本轮新算出的复算说明」会**覆盖**上一轮写入的**更丰富留痕**
        #   （如 18 条陈旧值的修正说明），等于抹掉可追溯的证据（铁律 #22）。
        old_p = e.get("props") or {}
        _core = ("verification_level", "verification_scope", "verifier", "verified")
        if all(old_p.get(k) == props.get(k) for k in _core):
            if props.get("verification_note") and not old_p.get("verification_note"):
                edge_rows.append({
                    "id": e.get("id") or "%s|%s|%s" % (e["type"], e["source"], e["target"]),
                    "source": e["source"], "target": e["target"], "type": e["type"],
                    "kind": old_p.get("kind") or e.get("kind") or "",
                    "props": {"verification_note": props["verification_note"]}})
                changed += 1
            continue
        if any(old_p.get(k) != v for k, v in props.items()):
            edge_rows.append({
                "id": e.get("id") or "%s|%s|%s" % (e["type"], e["source"], e["target"]),
                "source": e["source"], "target": e["target"], "type": e["type"],
                "kind": old_p.get("kind") or e.get("kind") or "",
                "props": props})
            changed += 1
    print("\n[4] 待重标边：%d 条（仅写发生变化者）" % changed)
    by_scope = collections.Counter(row["props"].get("verification_scope") for row in edge_rows)
    for k, v in by_scope.most_common(8):
        print("      %-38s %5d" % (k, v))

    # ---------------- 5. WebBook 跨域热化学桥 ----------------
    new_nodes, new_edges, wbstat = [], [], collections.Counter()
    if not a.no_webbook and os.path.exists(a.webbook):
        new_nodes, new_edges, wbstat = build_webbook_bridges(
            a.webbook, node_by_id, ag_tol=a.agree_tol)
        print("\n[5] NIST WebBook 热化学桥：新建边 %d 条 / 新节点 %d 个"
              % (len(new_edges), len(new_nodes)))
        for k, v in wbstat.most_common():
            print("      %-34s %d" % (k, v))
    else:
        print("\n[5] 跳过 WebBook 建桥（--no-webbook 或源文件不存在）")

    # ---------------- 6. delta ----------------
    delta = {
        "meta": {
            "phase": 29, "tag": "bridge", "generator": "phase29_bridge_delta.py",
            "base_graph": os.path.relpath(a.graph, ROOT),
            "theme": "跨域桥专项（T7）：清理伪桥 + 升级真桥 + 接入 NIST WebBook",
            "edges_relabeled": len(edge_rows),
            "edges_withdrawn": len(del_edges),
            "edges_withdrawn_contradictory": len(del_edges) - len(del_abstract),
            "edges_withdrawn_abstract": len(del_abstract),
            "molar_mass_values_repaired": len(repaired),
            "webbook_new_edges": len(new_edges),
            "webbook_new_nodes": len(new_nodes),
            "level_distribution": dict(lv),
        },
        "nodes": new_nodes,
        "delete_nodes": [],
        "edges": edge_rows + new_edges,
        "delete_edges": del_edges,
    }

    print("\n[6] delta 规模：节点 %d / 边重标 %d / 新建边 %d / 删边 %d"
          % (len(new_nodes), len(edge_rows), len(new_edges), len(del_edges)))
    if a.dry:
        print("    [DRY] 未落盘；紧凑 JSON 约 %.2f MB"
              % (len(json.dumps(delta, ensure_ascii=False).encode("utf-8")) / 1048576.0))
        return 0
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(delta, f, ensure_ascii=False)
    print("    已写 %s（%.2f MB）" % (os.path.relpath(a.out, ROOT),
                                      os.path.getsize(a.out) / 1048576.0))
    return 0


def sp_mw(node_by_id, src):
    return ((node_by_id.get(src) or {}).get("props") or {}).get("pubchem_molecular_weight")


def build_webbook_bridges(wb_path, node_by_id, ag_tol=0.005):
    """由 webbook_raw.json 生成（新节点, 新边, 统计）。"""
    payload = json.load(open(wb_path, encoding="utf-8"))
    recs = payload.get("records") or []
    stat = collections.Counter()
    new_nodes = {}
    new_edges = []
    existing = set()
    for nid in node_by_id:
        pass

    # 目标量节点（molar_entropy 需新建；molar_energy 已存在）
    for q, (tid, label) in QUANT.items():
        if tid not in node_by_id:
            new_nodes[tid] = {
                "id": tid, "labels": ["PhysicalQuantity", "Entity"],
                # domain 与既有 `PQ:*` 物理量节点保持同一口径（`phys.quantity` → 学科「物理」），
                # 否则学科判定会走 id 命名空间兜底，前端配色与跨域路径判定不再同源
                "props": {"id": tid, "name": tid.split(":")[-1], "domain": "phys.quantity",
                          "label_zh": label,
                          "dimension_si": _norm_dim(EXPECT_DIM[q]),
                          "dimension_si_str": "*".join(
                              "%s^%g" % (k, v) for k, v in sorted(_norm_dim(EXPECT_DIM[q]).items())),
                          "source": "dimension_table(algebra: entropy/amount) 派生",
                          "note": "由 dimension_table 的量纲代数派生（第 19 轮），供热化学桥锚定"}}

    for rec in recs:
        src = rec["node"]
        if src not in node_by_id:
            stat["skip_node_absent"] += 1
            continue
        for q, (tid, label) in QUANT.items():
            rows = [r for r in rec["rows"]
                    if wb.normalize_quantity(r["quantity"]) == q]
            if not rows:
                stat["no_rows_%s" % q] += 1
                continue
            # --- 证据①：单位量纲独立复算 ---
            udims = {r["unit"] for r in rows}
            dim_ok = all(_norm_dim(unit_dim(u)) == _norm_dim(EXPECT_DIM[q]) for u in udims)
            if not dim_ok:
                stat["unit_dim_mismatch"] += 1
                continue
            # --- 证据②：多条独立文献吻合 ---
            # ⚠ 第 19 轮修（真实缺陷）：旧版无条件取**全体**文献的**中位数**。
            #   实测 16 条边的多条文献**分歧超出容差**（如 CH$_4$ 熵：Colwell1963=188.66±0.42
            #   vs Chase1998=186.25，差 1.2 > 0.42），于是「吻合集」为空，
            #   却仍写出一条**取两者中位数 187.455**、`n_references=0` 的边 ——
            #   **值既不来自任何单一文献，也不满足"多源一致"，是静默编造**。
            #   正确做法：① 吻合集 ≥2 → 中位数 + `cross_source`；
            #             ② 吻合集 =1 → 该条的值 + `rule_checked`；
            #             ③ 吻合集 =0（**文献分歧**）→ 采用**单条权威文献**（优先 Review 综述、
            #                其次最新年份），`n_references=1`，并在证据里**显式记录分歧**。
            vals = [float(r["value"]) for r in rows]
            med = statistics.median(vals)
            tol = max(ag_tol * abs(med), 1e-9)
            agree = [r for r in rows
                     if abs(float(r["value"]) - med) <= max(tol,
                                                            float(r.get("uncertainty") or 0.0))]
            refs = sorted({r["reference"] for r in agree})
            if len(refs) >= 2:
                level, value = "cross_source", med
            elif agree:
                level, value = "rule_checked", float(agree[0]["value"])
            else:                                   # 文献分歧 → 单条权威，不取中位数
                adopt = max(rows, key=lambda r: (r.get("method") == "Review",
                                                 _year_of(r.get("reference"))))
                level, value = "rule_checked", float(adopt["value"])
                refs = [adopt["reference"]]
                stat["multi_reference_disagreement"] += 1
            scope = ("webbook_multi_reference_agreement" if level == "cross_source"
                     else "webbook_unit_dimension_recompute")
            verifier = ("NIST WebBook(≥2 独立文献) × dimension_table" if level == "cross_source"
                        else "NIST WebBook × dimension_table(单位量纲)")
            ev = [
                "单位量纲独立复算：%s -> %s ≡ %s 的量纲"
                % (rows[0]["unit"], _norm_dim(unit_dim(rows[0]["unit"])), tid),
                "独立文献 %d 条：%s" % (len(refs), "; ".join(refs)),
                "各文献值：" + "; ".join(
                    "%s=%s%s(%s)" % (r["reference"], r["value"],
                                     ("±%s" % r["uncertainty"]) if r.get("uncertainty") else "",
                                     r["method"]) for r in rows),
            ]
            if level == "rule_checked" and len(rows) > 1 and not agree:
                ev.append("⚠ 多条文献**未达吻合容差**（±%.2f%%）→ **不升 cross_source**，"
                          "采用单条权威文献（%s）；分歧未消除，不得视为多源一致"
                          % (100.0 * ag_tol, refs[0]))
            eid = "has_quantity|%s|%s" % (src, tid)
            new_edges.append({
                "id": eid, "source": src, "target": tid, "type": "has_quantity",
                "kind": "webbook_thermochemistry",
                "props": {
                    "property": q, "phase": "gas", "temperature": 298.15,
                    "value": round(value, 4), "unit": rows[0]["unit"],
                    "n_references": len(refs),
                    "multi_source_agreement": bool(len(refs) >= 2),
                    "source": "NIST Chemistry WebBook",
                    "cas": rec.get("cas"), "url": rec.get("url"),
                    "explicit_or_inferred": "explicit",
                    "verification_level": level, "verification_scope": scope,
                    "verifier": verifier, "verified": True,
                    "evidence": ev,
                    "rationale": "%s（NIST Chemistry WebBook 气相热化学；采用值 %s %s，%d 条独立文献%s）"
                                 % (label, round(value, 4), rows[0]["unit"], len(refs),
                                    "，多源一致" if len(refs) >= 2 else "（单源权威）"),
                }})
            stat["bridge_%s_%s" % (q, level)] += 1
    stat["bridge_total"] = len(new_edges)
    return list(new_nodes.values()), new_edges, stat


if __name__ == "__main__":
    sys.exit(main())
