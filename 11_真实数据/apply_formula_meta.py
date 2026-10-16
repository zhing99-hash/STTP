# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Phase 54 · 把 81 条公式的人工策展元数据落到本地权威快照，并产出待推 Aura 的增量。

背景：公式节点此前只有 `latex` / `domain` 等，没有「发现时间 / 发现人 / 介绍 / 使用描述」。
上游 PhysicsBabel 的 5000 条**确实没有**任何年代或作者字段（逐字段核过
`11_真实数据/physicsbabel_raw.json`），且本机 Wikidata 被网络拦截
（`11_真实数据/probe_sources.py` 登记为「已知被拦·对照」）⇒ **没有自动补的路径**。
故本轮采取「机制 + 人工策展 81 条」：81 条内容写在 `10_种子数据/formula_meta.json`，
其余一律显式写「暂无」（用户决策：数据层显式写入，便于前端直接渲染）。

本脚本（**幂等**）：
  1. 读 `10_种子数据/formula_meta.json`（81 条权威内容源）；
  2. 给快照里**全部 Formula 节点**写 `discovery_time` / `discoverer`（缺内容 ⇒ `"暂无"`）；
  3. 给命中 81 条的节点写 `description` / `usage` / `meta_source`（**无值则不写该键**）；
  4. 逐项断言 M1–M6（fail-closed，任一不满足即非零退出、**不落盘**）；
  5. 输出新 viz 快照 `graph_data_phase41.json`（不入库）；
  6. 产出增量 `phase54_formula_meta_delta.json`（**不入库**：完整节点 upsert 约数 MB，
     属「大体积派生中间产物」，与本仓库 phase41/44/45 的 delta 同处理，可由本脚本确定性重算）。

用法：
  python 11_真实数据/apply_formula_meta.py            # dry-run（默认，只报改动面，不写任何文件）
  python 11_真实数据/apply_formula_meta.py --apply    # 落盘

★ 拓扑不变：本脚本**只写节点属性**，不动 labels、不动边 ⇒ 门禁（frozen_gate 108 条）/
  量纲 / 可信度全线不受影响（M4 断言守住）。
"""
import argparse
import json
import os
import re
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "06_PoC"))

import graph_export  # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
NORM_BAK = os.path.join(ROOT, "06_PoC", "etl", "normalized.before_formula_meta.json")
META = os.path.join(ROOT, "10_种子数据", "formula_meta.json")
OUT_VIZ = os.path.join(ROOT, "06_PoC", "graph_data_phase41.json")
OUT_DELTA = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase54_formula_meta_delta.json")

DEFAULT_TEXT = "暂无"
META_KEYS = ("discovery_time", "discoverer", "description", "usage", "meta_source")

#: `discovery_time` 的**唯一合法形态**（规划 §2.1.1，4 种）。
#: ★ 必须与前端 `deriveEra()` 的识别口径**逐条对应**（`06_PoC/js/core.js`）。
RE_INT4 = re.compile(r"^\d{4}$")            # 1000–9999
RE_AD = re.compile(r"^公元 \d{1,3}$")        # 公元 1–999
RE_BC = re.compile(r"^公元前 \d{1,4}$")      # 公元前 1–9999
SENTINEL = DEFAULT_TEXT


class Fail(Exception):
    """断言失败（fail-closed：非零退出，不落盘）。"""


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def is_formula(n):
    return "Formula" in (n.get("labels") or [])


def dump_props(p):
    return json.dumps(p, sort_keys=True, ensure_ascii=False)


def ok_discovery_time(v):
    if not isinstance(v, str) or not v:
        return False
    return v == SENTINEL or bool(RE_INT4.match(v) or RE_AD.match(v) or RE_BC.match(v))


def is_year(v):
    """是否为**真实年份**（非哨兵）。"""
    return v != SENTINEL and bool(RE_INT4.match(v) or RE_AD.match(v) or RE_BC.match(v))


# ---------------------------------------------------------------------------
# M1–M3：内容源自身的断言（在动图谱之前就该拦下来）
# ---------------------------------------------------------------------------
def check_meta(entries, formula_ids):
    bad = []
    for k, v in entries.items():
        if k not in formula_ids:
            bad.append((k, "M1", "id 在快照的 Formula 集合里不存在"))
        extra = set(v) - set(META_KEYS)
        if extra:
            bad.append((k, "M2", f"出现白名单外的键 {sorted(extra)}"))
        if not ok_discovery_time(v.get("discovery_time")):
            bad.append((k, "M2", f"discovery_time 形态非法：{v.get('discovery_time')!r}"))
        dv = v.get("discoverer")
        if not isinstance(dv, str) or not dv.strip():
            bad.append((k, "M2", f"discoverer 为空：{dv!r}"))
        for f in ("description", "usage"):
            if f in v:
                if not isinstance(v[f], str) or not v[f].strip():
                    bad.append((k, "M2", f"{f} 存在但为空串"))
                elif v[f].strip() == SENTINEL:
                    bad.append((k, "M2", f"{f} 不得写哨兵值「{SENTINEL}」"))
        has_value = is_year(v.get("discovery_time")) or v.get("discoverer") != SENTINEL
        if has_value and not (v.get("meta_source") or "").strip():
            bad.append((k, "M3", "discovery_time/discoverer 有值却缺 meta_source（凭空断言）"))
    return bad


# ---------------------------------------------------------------------------
# 施加（纯属性写入，可重复执行）
# ---------------------------------------------------------------------------
def apply_once(nodes, entries):
    """给全部 Formula 写两个必填键；命中 81 条的再写三个可选键。返回 (写入条数, 逐节点新 props 串)。"""
    touched = 0
    snap = {}
    for n in nodes:
        if not is_formula(n):
            continue
        p = n.setdefault("props", {})
        e = entries.get(n["id"])
        p["discovery_time"] = e["discovery_time"] if e else SENTINEL
        p["discoverer"] = e["discoverer"] if e else SENTINEL
        if e:
            for f in ("description", "usage", "meta_source"):
                v = e.get(f)
                if isinstance(v, str) and v.strip():
                    p[f] = v
                else:
                    p.pop(f, None)      # 无值 ⇒ 不写该键（若历史遗留则清掉）
        touched += 1
        snap[n["id"]] = dump_props(p)
    return touched, snap


def main():
    ap = argparse.ArgumentParser(description="Phase 54 公式元数据施加（默认 dry-run）")
    ap.add_argument("--apply", action="store_true", help="真正落盘（默认只 dry-run）")
    args = ap.parse_args()

    meta = load(META)
    entries = meta["entries"]
    norm = load(NORM)
    nodes, edges = norm["nodes"], norm["edges"]
    formula_ids = {n["id"] for n in nodes if is_formula(n)}
    print(f"[in] 快照 {len(nodes)} 节点 / {len(edges)} 边；其中 Formula {len(formula_ids)} 个")
    print(f"[in] 内容源 {len(entries)} 条（{os.path.relpath(META, ROOT)}）")

    # ---------- M1–M3 ----------
    bad = check_meta(entries, formula_ids)
    if bad:
        print("\n=== 内容源断言 ===")
        for k, tag, why in bad:
            print(f"  [FAIL] {tag} {k}: {why}")
        raise Fail(f"内容源未通过 M1–M3（{len(bad)} 项）")
    print("[M1] 81 个 id 全部命中快照的 Formula 集合 ✓")
    print("[M2] discovery_time 形态 4 种之内 / discoverer 非空 / 无白名单外键 / 无哨兵混用 ✓")
    print("[M3] 有值必有依据（meta_source）✓")

    # ---------- 拓扑基线（M4 前置） ----------
    ids_before = [n["id"] for n in nodes]
    eids_before = [e.get("id") for e in edges]
    labels_before = {n["id"]: tuple(n.get("labels") or []) for n in nodes}

    # ---------- 施加 ----------
    touched, snap1 = apply_once(nodes, entries)

    # ---------- M4 拓扑不变 ----------
    if [n["id"] for n in nodes] != ids_before:
        raise Fail("M4 节点 id 序列发生变化（拓扑被扰动）")
    if [e.get("id") for e in edges] != eids_before:
        raise Fail("M4 边集合发生变化（拓扑被扰动）")
    if {n["id"]: tuple(n.get("labels") or []) for n in nodes} != labels_before:
        raise Fail("M4 节点 labels 发生变化（类型被扰动）")
    print(f"[M4] 拓扑不变：{len(nodes)} 节点 / {len(edges)} 边，labels 逐节点相等 ✓")

    # ---------- M5 幂等 ----------
    _, snap2 = apply_once(nodes, entries)
    diff = [k for k in snap1 if snap1[k] != snap2.get(k)]
    if diff:
        raise Fail(f"M5 非幂等：第二次施加改变了 {len(diff)} 个节点的 props，例 {diff[:3]}")
    print("[M5] 幂等：连跑两次逐节点 props 相同 ✓")

    # ---------- M6 哨兵值 ----------
    all_formula = [n for n in nodes if is_formula(n)]
    no_dt = [n["id"] for n in all_formula if n["props"].get("discovery_time") != DEFAULT_TEXT
             and not is_year(n["props"].get("discovery_time"))]
    if no_dt:
        raise Fail(f"M6 出现既非年份也非哨兵的 discovery_time：{no_dt[:5]}")
    n_sent = sum(1 for n in all_formula if n["props"].get("discovery_time") == SENTINEL)
    n_year = len(all_formula) - n_sent
    if n_sent == 0:
        raise Fail("M6 全图没有任何「暂无」⇒ 哨兵路径从未被走到（测量器存疑）")
    if n_year != sum(1 for v in entries.values() if is_year(v["discovery_time"])):
        raise Fail("M6 有年份的节点数 ≠ 内容源里非「暂无」的条数")
    polluted = [n["id"] for n in nodes if not is_formula(n) and set(n.get("props") or {}) & set(META_KEYS)]
    if polluted:
        raise Fail(f"M6 非 Formula 节点被写入元数据键：{polluted[:5]}")
    print(f"[M6] 哨兵值：discovery_time 有年份 {n_year} 条 / 「暂无」{n_sent} 条；"
          f"非 Formula 节点零污染 ✓")

    # ---------- 统计 ----------
    no_disc = sum(1 for n in all_formula if n["props"].get("discoverer") == SENTINEL)
    n_desc = sum(1 for n in all_formula if "description" in n["props"])
    n_use = sum(1 for n in all_formula if "usage" in n["props"])
    n_src = sum(1 for n in all_formula if "meta_source" in n["props"])
    print(f"[统计] Formula {len(all_formula)} 个：discoverer「暂无」{no_disc}；"
          f"带 description {n_desc} / usage {n_use} / meta_source {n_src}")

    if not args.apply:
        print("\n[dry-run] 未写任何文件。加 --apply 落盘。")
        return 0

    # ---------- 落盘 ----------
    norm["nodes"], norm["edges"] = nodes, edges
    if not os.path.exists(NORM_BAK):
        shutil.copy2(NORM, NORM_BAK)
        print(f"[1/3] 已备份原快照 -> {os.path.relpath(NORM_BAK, ROOT)}")
    with open(NORM, "w", encoding="utf-8") as f:
        json.dump(norm, f, ensure_ascii=False)
    print(f"[1/3] 快照已更新：{len(nodes)} 节点 / {len(edges)} 边")

    viz = graph_export.build_graph_data(NORM)
    with open(OUT_VIZ, "w", encoding="utf-8") as f:
        json.dump(viz, f, ensure_ascii=False)
    print(f"[2/3] viz 快照 -> {os.path.relpath(OUT_VIZ, ROOT)}："
          f"{len(viz['nodes'])} 节点 / {len(viz['edges'])} 边")

    delta = {
        "meta": {
            "phase": "54-formula-meta",
            "source": "11_真实数据/apply_formula_meta.py",
            "reason": "给 81 条策展公式写 discovery_time/discoverer/description/usage/meta_source；"
                      "其余 Formula 显式写「暂无」",
            "note": "大体积派生中间产物（完整节点 upsert）⇒ 不入库，可由本脚本幂等重算",
        },
        "nodes": [n for n in nodes if is_formula(n)],
        "edges": [],
        "delete_edges": [],
    }
    os.makedirs(os.path.dirname(OUT_DELTA), exist_ok=True)
    with open(OUT_DELTA, "w", encoding="utf-8") as f:
        json.dump(delta, f, ensure_ascii=False, indent=1)
    print(f"[3/3] delta -> {os.path.relpath(OUT_DELTA, ROOT)}："
          f"{len(delta['nodes'])} 节点 upsert（{os.path.getsize(OUT_DELTA) // 1024} KB，不入库）")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Fail as e:
        print(f"\n[FAIL] {e}")
        sys.exit(1)
