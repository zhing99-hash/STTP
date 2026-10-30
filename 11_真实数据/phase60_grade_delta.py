#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
"""phase60_grade_delta.py —— 第 60 轮 · 证据等级（1—5）**落盘**

本轮命题
--------
`00_项目管理/证据分级规范与抓取优先级_20261019.md` §7 要求：分数**在图数据生成阶段写入**，
不在查询期现算。理由是 D64 同族 —— 现算的分数会随 HEAD 漂移（来源文件一变，
同一条公式昨天 5 分今天 1 分），使用者会把它读成系统故障。

Phase 58 已把规则 `e1` 做成服务端函数（`queries.edge_grade`），Phase 60 补上后半段：
**把现算结果写进权威图快照**，使「分数」成为快照的一部分，而不是每次请求的产物。

落盘字段（边 props）
--------------------
  evidence_grade_internal  内部口径（含本机全部 *_raw.json）—— 抓取优先级 / 内部看板用
  evidence_grade_public    对外口径（F 维换成「可否随包分发」）—— **永不与前者合并**（#90）
  grade_rule_version       内部规则版本（`e1`）
  grade_public_rule_version 对外规则版本（`p1`）
  graded_at_commit         落盘时的**完整** HEAD sha（换 commit 即失效 ⇒ G5 会红）

★ 非破坏性：只加字段，不改任何既有 props、不改判级、不删边。
★ 幂等：同一 commit 上重跑得到**逐字节相同**的 delta（分数只由规则 + 当前字节决定）。
★ 双口径**分列**：两个字段各写各的，脚本里没有任何一处把它们平均或择优。

用法
----
  python 11_真实数据/phase60_grade_delta.py [--dry] [--out PATH]
  bash sttp.sh gradepersist            # = 本脚本 + apply_delta（--apply 才真写）
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "03_知识层"))
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import queries as q                                    # noqa: E402
import source_registry as sr                           # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase60_grade_delta.json")


def head_full():
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:                                  # noqa: BLE001
        return None


def eid_of(e):
    return e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))


def main():
    ap = argparse.ArgumentParser(description="Phase 60 证据等级落盘 delta")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    if not os.path.isfile(NORM):
        print("[不可用] 缺少权威图 06_PoC/etl/normalized.json（被 .gitignore 刻意排除）")
        print("  恢复：bash sttp.sh export，或从 Release 资产下载同名快照。")
        return 2

    d = json.load(open(NORM, encoding="utf-8"))
    nodes, edges = d["nodes"], d["edges"]
    print("=" * 92)
    print("Phase 60 delta —— 证据等级（1—5）落盘")
    print("  输入 %d 节点 / %d 边" % (len(nodes), len(edges)))
    print("=" * 92)

    reg = q.build_stale_registry(sr.ids(), sr.hash_map(base=ROOT), sr.repo_status_map(base=ROOT))
    b = q.NXBackend(NORM)
    all_edges = b.all_edges()
    # ★ Phase 82：落盘**也**要接第五档 —— 否则落盘算的是四分类口径（703 条掉到 1 分），
    #   而 `grade_report` 现算认 proven（5 分）⇒ G5「落盘 == 现算」立刻红 703 条。
    #   ★ 这是 D140（注册 ≠ 生效）的**第四次**现身：落盘、报告、服务端、切片
    #     四处各自构造 registry，四处都得接上，漏一处就是两套数。
    try:
        import fidelity_proven as fp                          # noqa: PLC0415
        fp.attach_proven(reg, all_edges, base=ROOT)
    except Exception as _ex:                                 # noqa: BLE001
        print("[WARN] 第五档 proven 不可用（%s: %s）⇒ 落盘按四分类口径（会掉档）"
              % (type(_ex).__name__, _ex))
    if len(all_edges) != len(edges):
        print("[FATAL] NXBackend 边数 %d != JSON 边数 %d —— 拒绝生成半张图的 delta"
              % (len(all_edges), len(edges)))
        return 3

    commit = head_full()
    stamp = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")

    out_edges = []
    ci = collections.Counter()
    cp = collections.Counter()
    null_i = null_p = 0
    for e in all_edges:
        gi = q.edge_grade(e, reg)
        gp = q.edge_grade_public(e, reg, redist_of=sr.is_redistributable)
        ci["null" if gi is None else str(gi)] += 1
        cp["null" if gp is None else str(gp)] += 1
        null_i += 1 if gi is None else 0
        null_p += 1 if gp is None else 0
        out_edges.append({
            "id": eid_of(e), "source": e.get("source"), "target": e.get("target"),
            "type": e.get("type"), "kind": e.get("kind") or "",
            "props": {
                "evidence_grade_internal": gi,
                "evidence_grade_public": gp,
                "grade_rule_version": q.GRADE_RULE_VERSION,
                "grade_public_rule_version": q.GRADE_PUBLIC_RULE_VERSION,
                "graded_at_commit": commit,
            },
        })

    print("\n【内部口径 e1】%s" % dict(sorted(ci.items())))
    print("【对外口径 p1】%s" % dict(sorted(cp.items())))
    print("\n【落盘自检】")
    print("  不可判定（null）：内部 %d / 对外 %d  —— 须为 0，否则是**把猜的写进了快照**"
          % (null_i, null_p))
    print("  graded_at_commit : %s" % commit)
    # ★ 单调性在这里也自检一遍：**写入前**就挡住「对外 > 内部」（G4 的表达式级守卫）
    violations = sum(1 for x in out_edges
                     if x["props"]["evidence_grade_internal"] is not None
                     and x["props"]["evidence_grade_public"] is not None
                     and x["props"]["evidence_grade_public"]
                     > x["props"]["evidence_grade_internal"])
    print("  对外 > 内部的边   : %d（须 0 —— 对外不可能比内部更可复算）" % violations)
    if null_i or null_p or violations:
        print("\n[FATAL] 自检未过 ⇒ **拒绝产出 delta**（宁可不落盘，也不把错的固化进快照）")
        return 1

    payload = {
        "meta": {
            "phase": 60, "tag": "evidence-grade-persist",
            "generator": "phase60_grade_delta.py",
            "theme": "证据等级 1—5 落盘：把 e1（内部）/ p1（对外）固化进权威图快照",
            "base_graph": os.path.relpath(NORM, ROOT),
            "grade_rule": q.GRADE_RULE_VERSION,
            "grade_public_rule": q.GRADE_PUBLIC_RULE_VERSION,
            "graded_at_commit": commit,
            "stamp": stamp,
            "internal_distribution": dict(sorted(ci.items())),
            "public_distribution": dict(sorted(cp.items())),
            "n_edges": len(out_edges),
        },
        "nodes": [],
        "delete_nodes": [],
        "edges": out_edges,
        "delete_edges": [],
    }
    print("\n【delta 汇总】edges=%d  delete_edges=0（非破坏性，只加字段）" % len(out_edges))
    if a.dry:
        print("[DRY] 未落盘")
        return 0
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print("-> %s (%.1f MB)" % (os.path.relpath(a.out, ROOT), os.path.getsize(a.out) / 1e6))
    print("   落盘：python 06_PoC/apply_delta.py --delta %s --phase 60 --tag grade --apply"
          % os.path.relpath(a.out, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
