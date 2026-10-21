#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""交付物报告里的**数字声明** ⇄ 现算真值 对账（Phase 68 · D100）。

为什么需要它
------------
2026-10-21 做整体评估时发现：`RC1_整体状态评估_20261020.md` 写着
「`symbols` 5,090 / 5,090 = **100%**」，而从图里现算是 **98.39%**，
`phase61_symbol_backfill_delta.py --dry` 自报也是 98.39%。

**三方里错的是报告。** 而那句 100% 从来没有任何判据咬住它 ——
脚本的判据是「非空才计数，门槛 ≥90%」，它自己从没说过 100%。

这类错误的形状值得说清楚：

* 它不是「代码算错了」，是「**有人替代码把话说满了**」；
* 它不是一次性的笔误 —— 只要「报告数字」这件事没有判据，
  下一份报告会以同样的方式再错一次，而且同样安静；
* 它特别容易发生在**覆盖率**这种数字上：因为「100%」听起来像终点，
  写的人会觉得「差不多就是全覆盖了」，而 82 条空列表（`E=mc²`/`F=ma`）在 90% 门槛下是绿的。

> 与 D96 同族：**一句没有判据的陈述，必然会说谎。**
> 本模块就是把「报告里的数字」也纳入判据覆盖范围 ——
> 在此之前，判据覆盖了代码、覆盖了数据，**唯独没有覆盖我们自己写下的结论**。

它怎么工作
----------
1. `DECLARATIONS` 登记「哪份报告的哪一行、声明的是哪个量」；
2. 真值一律**从图里现算**（`06_PoC/etl/normalized.json`），不读缓存、不读历史报告；
3. 报告里那一行**必须恰好匹配 1 处** —— 匹配不到也算失败
   （报告改版或数字被删 ⇒ 红，而不是「没找到就跳过」那种静默放过）；
4. 数字不等 ⇒ 红，并打印「报告写的 / 现算的」两侧。

判据可红（铁律 #100）
--------------------
S3 是**运行时负对照**：把报告文本里的数字换成假的，喂给同一套比对逻辑，
它必须判为不一致 —— 证明「绿」不是因为比对根本没生效。

用法
----
    python 06_PoC/report_number_check.py              # 对账（RC=0 通过 / RC=1 不一致）
    python 06_PoC/report_number_check.py --selfcheck   # 自检 S1–S4（含负对照）
    python 06_PoC/report_number_check.py --show        # 打印每条声明与现算值
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPH = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")

# ---------------------------------------------------------------- 真值：一律现算


def _load_graph():
    """权威图（**不入库**，缺件时按 RC=2 报「不适用」而非裸 traceback —— 铁律 #53）。"""
    if not os.path.exists(GRAPH):
        print("[RC=2] 权威图不可用：%s" % GRAPH)
        print("      本仪器的真值必须现算；在裸 clone 里请改用 `bash sttp.sh release`（样例口径）。")
        sys.exit(2)
    with io.open(GRAPH, encoding="utf-8") as fh:
        return json.load(fh)


def _formulas(graph):
    # 口径与 phase62/phase63 一致：`labels` 含 Formula。
    # ★ 别用 `props.latex` 判断 —— 那样会数出 5,201（口径不同 ⇒ 数字不同）。
    return [n for n in graph["nodes"] if "Formula" in (n.get("labels") or [])]


def _ratio(nodes, key):
    got = sum(1 for n in nodes
              if (n.get("props") or {}).get(key) not in (None, "", []))
    total = len(nodes)
    return (got, total, round(got * 100.0 / total, 2) if total else 0.0)


def truth_symbols():
    return _ratio(_formulas(_load_graph()), "symbols")


def truth_symbol_table():
    return _ratio(_formulas(_load_graph()), "symbol_table")


def truth_domain():
    return _ratio(_formulas(_load_graph()), "domain")


def truth_applicability():
    return _ratio(_formulas(_load_graph()), "applicability_status")


def truth_nodes_edges():
    g = _load_graph()
    return (len(g["nodes"]), len(g["edges"]), None)


# ---------------------------------------------------------------- 声明登记
#
# 每条声明 = 「报告里的哪一行」+「真值从哪来」。
# ★ `row` 用来定位那一行（匹配 0 处或 >1 处都算失败 —— 报告改版必须被发现）；
#   `pat` 从该行里取出 (分子, 分母, 百分比)。
# ★ 百分比一律与现算值**逐字**比对（不看「差不多」）：报告写 100% 而现算 98.39% ⇒ 红。

RC1 = "07_交付物/RC1_整体状态评估_20261020.md"
ASSESS = "07_交付物/整体评估_20261021.md"

_NUM = r"([\d,]+)\s*/\s*([\d,]+)\s*=\s*\*{0,2}([\d.]+)%"

DECLARATIONS = [
    dict(key="symbols 覆盖率", file=RC1, row="| `symbols` |", pat=_NUM, truth=truth_symbols),
    dict(key="symbol_table 覆盖率", file=RC1, row="| `symbol_table` |", pat=_NUM, truth=truth_symbol_table),
    dict(key="domain 覆盖率", file=RC1, row="| `domain` |", pat=_NUM, truth=truth_domain),
    dict(key="applicability 覆盖率", file=RC1, row="| `applicability_status` |", pat=_NUM,
         truth=truth_applicability),
    dict(key="symbols 覆盖率（评估报告）", file=ASSESS, row="| **`symbols`** |", pat=_NUM,
         truth=truth_symbols),
]


def _read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def check_one(decl, text_map=None):
    """比对单条声明。返回 (ok, detail)。"""
    rel = decl["file"]
    text = (text_map or {}).get(rel)
    if text is None:
        text = _read(rel)
    lines = [ln for ln in text.splitlines() if decl["row"] in ln]
    if len(lines) != 1:
        return (False, "%s：定位行%s 命中 %d 处（须恰好 1）" % (rel, decl["row"], len(lines)))
    m = re.search(decl["pat"], lines[0])
    if not m:
        return (False, "%s：行内没取到数字 —— %s" % (rel, lines[0][:80]))
    got = (int(m.group(1).replace(",", "")),
           int(m.group(2).replace(",", "")),
           float(m.group(3)))
    want = decl["truth"]()
    if want[2] is None:      # 只比计数（如节点/边）
        ok = (got[0], got[1]) == (want[0], want[1])
    else:
        ok = got == want
    return (ok, "报告 %d / %d = %.2f%% ｜ 现算 %d / %d = %.2f%%"
            % (got[0], got[1], got[2], want[0], want[1], want[2] or 0.0))


def run_all(text_map=None):
    results = [check_one(d, text_map) for d in DECLARATIONS]
    return results


# ---------------------------------------------------------------- 自检

def selfcheck():
    rep = []

    # S1 现算真值可信（非空、分母 = 5,090）
    n, tot, pct = truth_symbols()
    rep.append(("S1 现算真值非真空", tot > 0 and 0.0 < pct <= 100.0,
                "symbols %d / %d = %.2f%%" % (n, tot, pct)))

    # S2 每条声明都能被定位且当前一致
    results = run_all()
    bad = [(DECLARATIONS[i]["key"], d) for i, (ok, d) in enumerate(results) if not ok]
    rep.append(("S2 报告数字 == 现算真值", not bad,
                ("不一致 %d 条：" % len(bad)) + "；".join(k for k, _ in bad) if bad
                else "%d/%d 条一致" % (len(DECLARATIONS), len(DECLARATIONS))))

    # S3 负对照（运行时，不写盘）：把报告里的数字改掉 ⇒ 必须判为不一致
    real = _read(RC1)
    #   ★ 只改**第一处数字**（symbols 那一行），且用「100%」这种**最容易手写错**的值
    mutated = real.replace("**5,008 / 5,090 = 98.39%**", "**5,090 / 5,090 = 100%**", 1)
    if mutated == real:
        rep.append(("S3 负对照：改掉报告数字 ⇒ 必须判不一致", False,
                    "注入失败 —— 没找到待替换的数字（报告已改版？）"))
    else:
        mut_results = run_all({RC1: mutated})
        sym = mut_results[0]            # symbols 被改 ⇒ 必须不一致
        table_ok = mut_results[1][0]    # symbol_table 没改 ⇒ 必须仍一致（ok=True）
        rep.append(("S3 负对照：改掉报告数字 ⇒ 必须判不一致",
                    (not sym[0]) and table_ok,
                    "改后 symbols 判不一致=%s（期望 True）；未改的 symbol_table 仍一致=%s（期望 True）"
                    % (not sym[0], table_ok)))

    # S4 登记表非真空 + 每条字段齐备
    need = {"key", "file", "row", "pat", "truth"}
    miss = [d.get("key", "?") for d in DECLARATIONS if not need.issubset(set(d))]
    rep.append(("S4 登记表非真空且字段齐备", len(DECLARATIONS) >= 2 and not miss,
                "%d 条声明；缺字段：%s" % (len(DECLARATIONS), miss or "无")))

    print("\n【报告数字对账 · 自检】")
    width = max(len(t) for t, _, _ in rep)
    bad_n = 0
    for title, ok, detail in rep:
        if not ok:
            bad_n += 1
        print("  [%s] %s  —— %s" % ("PASS" if ok else "FAIL", title.ljust(width), detail))
    print("-" * 72)
    print("  合计 %d 条，通过 %d，失败 %d" % (len(rep), len(rep) - bad_n, bad_n))
    return 1 if bad_n else 0


def main():
    ap = argparse.ArgumentParser(description="交付物报告数字 ⇄ 现算真值 对账")
    ap.add_argument("--selfcheck", action="store_true", help="自检 S1–S4（含 S3 负对照）")
    ap.add_argument("--show", action="store_true", help="打印每条声明与现算值")
    args = ap.parse_args()

    if args.selfcheck:
        return selfcheck()

    results = run_all()
    bad = 0
    print("\n【报告数字对账】")
    for decl, (ok, detail) in zip(DECLARATIONS, results):
        if not ok:
            bad += 1
        print("  [%s] %s\n         %s" % ("OK" if ok else "NG", decl["key"], detail))
    print("-" * 72)
    print("  %d 条声明，一致 %d，不一致 %d" % (len(results), len(results) - bad, bad))
    if bad:
        print("\n  ⇒ 报告里的数字与现算不符。**数字可以改，但只能是现算出来之后改**。")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
