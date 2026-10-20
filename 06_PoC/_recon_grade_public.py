#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""_recon_grade_public.py —— 侦察：`grade_public`（对外口径）到底该怎么算

背景
----
`00_项目管理/证据分级规范与抓取优先级_20261019.md` §5 给了一张双口径表：

    内部 5 分 702 ／ 2 分 47 032
    对外 5 分   0 ／ 2 分 47 053

但**规范没有写对外口径的判据**，只写了「外部分发时那 12 个 `*_raw.json` 不在包里」。
这张表里的两个数字（0 / 47 053）因此**无法从规则反推** —— 本脚本的目的就是：
把「可复算性」换成一个**有登记依据**的判据（A03 再分发三态闭包），实测它给出什么。

候选判据（都基于 `source_registry` 现成的字段，不新造第二套真相，铁律 #87）
------------------------------------------------------------------------
  P1  `F_public = 2` 当且仅当该边**任一**证据来源的 `redistribution_closure != redistributable`
      （其余沿用内部 F）。语义：对外包里没有这个来源 ⇒ 对外不可复算。
  P2  P1 + 多源加成也要求来源可再分发（否则「两个来源」在包里只剩一个）。
  P3  `F_public = min(F_internal, 2)`（全部封顶 2）—— 最保守，等于放弃区分度。

判定标准
--------
- 哪个候选能复现 §5 的表 ⇒ 采用它；
- **都不能** ⇒ 如实记录偏差（这是本项目的一贯做法：数字由仪器现算，文档数字是历史），
  并把 §5 表标注为「无判据，不可复算」。
"""
from __future__ import annotations

import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "03_知识层"))
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import queries as q                      # noqa: E402
import source_registry as sr             # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")


def head_sha():
    import subprocess
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:                    # noqa: BLE001
        return None


def srcs_of(e):
    p = e.get("props") or {}
    evs = [x for x in (p.get("verification_evidence") or []) if isinstance(x, dict)]
    return [x.get("src") for x in evs if x.get("src")], evs


def main():
    print("=" * 92)
    print("侦察：grade_public（对外口径）候选判据实测    HEAD =", head_sha())
    print("  输入", os.path.relpath(NORM, ROOT))
    print("=" * 92)

    reg = q.build_stale_registry(sr.ids(), sr.hash_map(base=ROOT), sr.repo_status_map(base=ROOT))
    b = q.NXBackend(NORM)
    edges = b.all_edges()
    print("边总数 %d" % len(edges))

    # ---- 1. 来源的再分发立场分布（按边出现的 src 计） -----------------------
    src_counter = collections.Counter()
    redist_of_src = {}
    for e in edges:
        ss, _ = srcs_of(e)
        for s in ss:
            src_counter[s] += 1
        if not ss:
            src_counter["<无 src>"] += 1
    print("\n【证据 src 出现次数 TOP15】")
    for s, n in src_counter.most_common(15):
        st = sr.redistribution_closure(s) if s != "<无 src>" else "-"
        redist_of_src[s] = st
        print("   %-28s %7d   redistribution_closure=%s" % (s, n, st))

    # ---- 2. 内部口径（对照组，应与 §2.2 逐数一致） -------------------------
    internal = q.grade_counts(edges, reg)
    print("\n【内部口径 grade_internal】", internal)

    # ---- 3. P1 / P2 / P3 实测 ---------------------------------------------
    def grade_public(e, variant):
        p = e.get("props") or {}
        v = q.GRADE_VL.get(p.get("verification_level"), 1)
        fi = q.edge_fidelity(e, reg)
        ss, evs = srcs_of(e)
        if not ss:
            f = 1
        elif variant == "P3":
            f = min(q.GRADE_FD.get(fi, 1), 2)
        else:
            ok = all(sr.is_redistributable(s) for s in ss)
            f = q.GRADE_FD.get(fi, 1) if ok else 2
        g = min(v, f)
        if g >= 3:
            n_src = len(set(ss))
            n_indep = sum(1 for x in evs if x.get("indep"))
            if variant == "P2":
                n_src = len({s for s in ss if sr.is_redistributable(s)})
                n_indep = sum(1 for x in evs
                              if x.get("indep") and sr.is_redistributable(x.get("src")))
            if n_src >= 2 or n_indep >= 2:
                g = min(5, g + 1)
        return g

    for variant in ("P1", "P2", "P3"):
        c = collections.Counter()
        for e in edges:
            g = grade_public(e, variant)
            c["null" if g is None else str(g)] += 1
        print("\n【对外口径 %s】%s" % (variant, dict(sorted(c.items()))))
        print("   §5 文档写的是 5分=0 / 2分=47 053 —— 命中则 ✅，否则记偏差")

    # ---- 4. 交叉：内部 5 分边的来源构成（为什么对外会掉档） ----------------
    five = [e for e in edges if q.edge_grade(e, reg) == 5]
    bad = 0
    for e in five:
        ss, _ = srcs_of(e)
        if not all(sr.is_redistributable(s) for s in ss):
            bad += 1
    print("\n【内部 5 分的 %d 条边】其中含有**不可再分发**来源的：%d 条" % (len(five), bad))
    noredis = [s for s, st in redist_of_src.items() if st != "redistributable"]
    print("   不可再分发的 src（出现在边上）：%d 个" % len(noredis))
    print("   ", noredis[:12])
    return 0


if __name__ == "__main__":
    sys.exit(main())
