# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""把元素层修复落到本地权威快照，并产出待推 Aura 的增量。

背景（详见 00_项目管理/下一步开发计划_20261008.md）：
  缺陷 1  EK2 符号解析忽略权威 NAME_IS，改用 HASATOMIC 反查。ElementKG 对
          Oxygen 的 HASATOMIC=16 实为「族号」(16 族)，反查得 "S" → 与 Sulfur
          撞 id → **氧元素整条丢失**，且 EK2:el:S 名称/质量被污染成氧。
  缺陷 2  14 个元素缺原子量（IC:el:{Na,Cl,Fe} / BC:el:N / EK:el: 超重 10 个）。

本脚本（幂等）：
  1. 用修复后的 elementkg10m_raw.json **整体替换**快照里的 EK2:el:* 节点与相关边；
  2. 用修复后的 elementkg_raw.json 同步 EK:el:* 的原子量/序数/名称；
  3. 按权威参考表补全其余 Element 节点的原子量，并统一 atomic_mass / atomic_weight 双键；
  4. 输出新 viz 快照 graph_data_phase13.json；
  5. 产出增量 phase13_elementfix_delta.json（含待删边），供联网后推送。

用法：python 11_真实数据/apply_element_fix.py
"""
import json
import os
import shutil
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "06_PoC"))

from element_reference import by_symbol, by_atomic_number   # noqa: E402
import graph_export                                          # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
NORM_BAK = os.path.join(ROOT, "06_PoC", "etl", "normalized.before_elementfix.json")
EK10M = os.path.join(HERE, "elementkg10m_raw.json")
EKOWL = os.path.join(HERE, "elementkg_raw.json")
OUT_VIZ = os.path.join(ROOT, "06_PoC", "graph_data_phase13.json")
OUT_DELTA = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase13_elementfix_delta.json")

MASS_KEYS = ("atomic_weight", "atomic_mass", "weight")


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def existing_mass(props):
    for k in MASS_KEYS:
        v = props.get(k)
        if v not in (None, "", 0):
            return v
    return None


def is_element(n):
    return "Element" in (n.get("labels") or [])


def to_snapshot_edge(e):
    """把适配器 raw 边转成快照（=Aura 导出）约定：

    快照里 id = `{type}|{source}|{target}`，props 内含 `kind`；
    适配器 raw 边却是 id = `{前缀}:{src}->{tgt}`。不转换会与全图 32897 条
    边 id 约定不一致，导致 delta 出现"全删全加"的假差异。
    """
    typ, src, tgt = e["type"], e["source"], e["target"]
    props = dict(e.get("props") or {})
    props.setdefault("kind", e.get("kind"))
    return {"id": f"{typ}|{src}|{tgt}", "source": src, "target": tgt,
            "type": typ, "kind": e.get("kind"), "props": props}


def main():
    norm = load(NORM)
    nodes, edges = norm["nodes"], norm["edges"]
    print(f"[in] 快照: {len(nodes)} 节点 / {len(edges)} 边")

    ek10m = load(EK10M)
    ekowl = load(EKOWL)

    new_ek2_nodes = [n for n in ek10m["nodes"] if str(n["id"]).startswith("EK2:el:")]
    new_ek2_edges = [to_snapshot_edge(e) for e in ek10m["edges"]
                     if str(e.get("source", "")).startswith("EK2:el:")
                     or str(e.get("target", "")).startswith("EK2:el:")]
    print(f"[in] 修复后 EK2 元素节点 {len(new_ek2_nodes)} 个 / 相关边 {len(new_ek2_edges)} 条")

    # ---------- 1. 替换 EK2:el:* 节点 ----------
    old_ek2_nodes = {n["id"]: n for n in nodes if str(n.get("id", "")).startswith("EK2:el:")}
    old_ek2_edges = [e for e in edges
                     if str(e.get("source", "")).startswith("EK2:el:")
                     or str(e.get("target", "")).startswith("EK2:el:")]
    old_edge_ids = {e.get("id") for e in old_ek2_edges}

    nodes = [n for n in nodes if not str(n.get("id", "")).startswith("EK2:el:")]
    edges = [e for e in edges if e.get("id") not in old_edge_ids]
    nodes.extend(new_ek2_nodes)
    edges.extend(new_ek2_edges)

    added_nodes = [n["id"] for n in new_ek2_nodes if n["id"] not in old_ek2_nodes]
    changed_nodes = []
    for n in new_ek2_nodes:
        old = old_ek2_nodes.get(n["id"])
        if old and old.get("props") != n.get("props"):
            changed_nodes.append(n["id"])
    new_edge_ids = {e.get("id") for e in new_ek2_edges}
    added_edges = [e for e in new_ek2_edges if e.get("id") not in old_edge_ids]
    removed_edges = [e for e in old_ek2_edges if e.get("id") not in new_edge_ids]

    print(f"  EK2 新增节点: {added_nodes}")
    print(f"  EK2 属性变更: {changed_nodes}")
    print(f"  EK2 新增边: {len(added_edges)}   待删边: {len(removed_edges)}")

    # ---------- 2. 同步 EK:el:* 属性 ----------
    owl_props = {n["id"]: (n.get("props") or {}) for n in ekowl["nodes"]
                 if str(n["id"]).startswith("EK:el:")}
    ek_synced = []
    for n in nodes:
        nid = str(n.get("id", ""))
        if nid in owl_props and is_element(n):
            src = owl_props[nid]
            dst = n.setdefault("props", {})
            touched = []
            for k in ("atomic_weight", "atomic_number", "name"):
                if dst.get(k) in (None, "") and src.get(k) not in (None, ""):
                    dst[k] = src[k]
                    touched.append(k)
            if touched:
                ek_synced.append((nid, touched))
    print(f"[2] EK:el:* 补全 {len(ek_synced)} 个: {ek_synced[:6]}{' ...' if len(ek_synced) > 6 else ''}")

    # ---------- 3. 按权威表补全其余元素原子量 + 双键统一 ----------
    filled, mirrored, conflicts = [], [], []
    for n in nodes:
        if not is_element(n):
            continue
        props = n.setdefault("props", {})
        sym = props.get("symbol")
        ref = by_symbol(sym) or by_atomic_number(props.get("atomic_number"))
        cur = existing_mass(props)
        if cur in (None, ""):
            if ref:
                props["atomic_weight"] = ref["atomic_weight"]
                props["atomic_mass"] = ref["atomic_weight"]
                filled.append((n["id"], ref["atomic_weight"]))
            continue
        # 双键统一：缺失的补上（不覆盖已有值）
        for k in ("atomic_weight", "atomic_mass"):
            if props.get(k) in (None, ""):
                props[k] = cur
                mirrored.append(n["id"])
        # 记录两键不一致（仅提示，不做覆盖）
        if props.get("atomic_weight") not in (None, "") and props.get("atomic_mass") not in (None, ""):
            if abs(float(props["atomic_weight"]) - float(props["atomic_mass"])) > 1e-9:
                conflicts.append((n["id"], props["atomic_weight"], props["atomic_mass"]))

    print(f"[3] 按权威表补全原子量 {len(filled)} 个: {filled}")
    print(f"    双键补齐 {len(set(mirrored))} 个；两键不一致 {len(conflicts)} 个")

    # ---------- 4. 写回快照 ----------
    norm["nodes"], norm["edges"] = nodes, edges
    if not os.path.exists(NORM_BAK):
        shutil.copy2(NORM, NORM_BAK)
        print(f"[4] 已备份原快照 -> {os.path.relpath(NORM_BAK, ROOT)}")
    with open(NORM, "w", encoding="utf-8") as f:
        json.dump(norm, f, ensure_ascii=False)
    print(f"[4] 快照已更新: {len(nodes)} 节点 / {len(edges)} 边")

    # ---------- 5. 新 viz 快照 ----------
    viz = graph_export.build_graph_data(NORM)
    with open(OUT_VIZ, "w", encoding="utf-8") as f:
        json.dump(viz, f, ensure_ascii=False)
    print(f"[5] viz 快照 -> {os.path.relpath(OUT_VIZ, ROOT)}: "
          f"{len(viz['nodes'])} 节点 / {len(viz['edges'])} 边")

    # ---------- 6. 增量 delta ----------
    upsert_nodes = []
    by_id = {n["id"]: n for n in nodes}
    for nid in added_nodes + changed_nodes:
        upsert_nodes.append(by_id[nid])
    # 属性被补全/同步的元素节点（仅节点属性变更，无涉及边改动）
    for nid, in [(i,) for i, _ in ek_synced] + [(i,) for i, _ in filled]:
        n = by_id.get(nid)
        if n and n not in upsert_nodes:
            upsert_nodes.append(n)

    delta = {
        "meta": {
            "phase": "element-fix",
            "source": "apply_element_fix.py",
            "reason": "修复 EK2 符号解析致氧元素丢失 + 补全 14 个元素原子量",
            "note": "节点属性更新需用 push_element_fix.py（robust_aura_loader 只写边）",
        },
        "nodes": upsert_nodes,
        "edges": added_edges,
        "delete_edges": [{"source": e["source"], "target": e["target"],
                          "type": e["type"], "kind": e.get("kind")} for e in removed_edges],
    }
    os.makedirs(os.path.dirname(OUT_DELTA), exist_ok=True)
    with open(OUT_DELTA, "w", encoding="utf-8") as f:
        json.dump(delta, f, ensure_ascii=False, indent=1)
    print(f"[6] delta -> {os.path.relpath(OUT_DELTA, ROOT)}: "
          f"{len(delta['nodes'])} 节点 upsert / {len(delta['edges'])} 新增边 / "
          f"{len(delta['delete_edges'])} 待删边")

    # ---------- 7. 自检 ----------
    print("\n=== 自检 ===")
    els = [n for n in nodes if is_element(n)]
    no_mass = [n["id"] for n in els if existing_mass(n.get("props", {})) in (None, "")]
    print(f"  Element 节点 {len(els)} 个，缺原子量 {len(no_mass)} 个 {no_mass[:8]}")
    syms = Counter((n.get("props") or {}).get("symbol") for n in els if str(n.get("id", "")).startswith("EK2:el:"))
    print(f"  EK2 元素符号: {sorted(k for k in syms if k)}")
    print(f"  氧节点存在: {'EK2:el:O' in by_id}")
    s = by_id.get("EK2:el:S", {}).get("props", {})
    print(f"  EK2:el:S => name={s.get('name')} Z={s.get('atomic_number')} w={s.get('atomic_weight')}")


if __name__ == "__main__":
    main()
