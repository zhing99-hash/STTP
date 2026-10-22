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
import functools
import io
import json
import os
import re
import sys

_lru_cache = functools.lru_cache

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


DIST = os.path.join(ROOT, "06_PoC", "dataset", "dist")


def _load_dist():
    """对外数据集产物（构建物、不入库）。缺件时 RC=2 报「不适用」，不裸 traceback（铁律 #53）。"""
    man = os.path.join(DIST, "manifest.json")
    if not os.path.exists(man):
        print("[RC=2] 数据集产物不可用：%s" % man)
        print("      请先 `bash sttp.sh dataset` 重建；本仪器的真值必须现算。")
        sys.exit(2)
    with io.open(man, encoding="utf-8") as fh:
        return json.load(fh)


def truth_public_size():
    """对外包节点/边 —— 读 manifest.counts（构建器写的，S3 已断言与分区自洽）。"""
    c = _load_dist()["counts"]
    return (c["nodes"], c["edges"], None)


def truth_molecule_coverage():
    """包内「有 composed_of 的分子 / 全部分子」—— 从 JSONL **逐行现算**，
    不读 manifest 里的派生统计（那等于拿构建器的自述当真值）。"""
    import json as _j
    nodes_p = os.path.join(DIST, "nodes.jsonl")
    edges_p = os.path.join(DIST, "edges.jsonl")
    mol = set()
    with io.open(nodes_p, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = _j.loads(line)
            if r.get("type") == "Molecule" or "Molecule" in (r.get("labels") or []):
                mol.add(r.get("id"))
    co = set()
    with io.open(edges_p, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = _j.loads(line)
            if r.get("type") == "composed_of":
                co.add(r.get("source"))
    got = len(mol & co)
    total = len(mol)
    return (got, total, round(got * 100.0 / total, 2) if total else 0.0)


def truth_nodes_edges():
    g = _load_graph()
    return (len(g["nodes"]), len(g["edges"]), None)


@_lru_cache(maxsize=1)
def _gate_baseline():
    """门禁基线的**现算**事实：`vm.classify_all` 全图重算一遍（慢，故整段缓存）。

    返回的三个量里，本模块目前只消费「提及边档位分布」——
    另两个（落库≠重算、strict 条数）**已有 `gatecheck (t)/(q)` 直接咬住**，
    此处不再登记一遍（同一件事两处判据 ⇒ 将来必有一处漂移，#88 的同族）。
    """
    for p in (os.path.join(ROOT, "06_PoC"), os.path.join(ROOT, "11_真实数据")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import verification_model as vm                       # noqa: E402
    g = _load_graph()
    res, _ctx = vm.classify_all(g["nodes"], g["edges"])
    title_n = snip_n = 0
    for _e, r in res:
        sc = (r or {}).get("verification_scope") or ""
        if sc == "name_mention_title_recompute":
            title_n += 1
        elif sc == "name_mention_snippet_recompute":
            snip_n += 1
    return (title_n, snip_n)


def truth_mention_scope():
    """`mentioned_in` 的重算档位分布（title 档 / snippet 档）—— 现算，不读报告。"""
    a, b = _gate_baseline()
    return (a, b, None)


RELEASE = os.path.join(ROOT, "06_PoC", "release", "manifest.json")


def truth_sample_size():
    """release 离线样例的节点/边 —— 现算自**两份**样例产物的计数，并要求二者一致。

    为什么不只读一份：样例包里有同一批节点/边的两个视图
    （`sample_graph.json` 是 formula-graph-view，`sample_normalized.json` 是 normalized 口径）。
    若两个数字打架，说明抽样器写出了不自洽的包 —— 此时「报告写 200」本身就无所谓对错，
    故按 RC=2 报「不适用」而不是随便挑一份当真值。

    ★ 与 `truth_public_size()` 的区别：那个读构建器的 `manifest.counts`（构建产物目录），
      这个读**发布清单**（`release/manifest.json`，入库存档）。DATA_LICENSES.md 讲的是
      「打出来的离线样例」，口径属于后者。
    """
    if not os.path.exists(RELEASE):
        print("[RC=2] 发布清单不可用：%s" % RELEASE)
        print("      请先 `bash sttp.sh release` 重建；本仪器的真值必须现算。")
        sys.exit(2)
    with io.open(RELEASE, encoding="utf-8") as fh:
        man = json.load(fh)
    art = (man.get("artifacts") or {}).get("sample") or {}
    sizes = []
    for name in ("sample_normalized.json", "sample_graph.json"):
        a = art.get(name) or {}
        if "nodes" not in a or "edges" not in a:
            print("[RC=2] 发布清单里 %s 缺 nodes/edges 计数" % name)
            sys.exit(2)
        sizes.append((int(a["nodes"]), int(a["edges"])))
    if len(set(sizes)) != 1:
        print("[RC=2] 两份样例产物的规模不一致：%s ⇒ 样例包不自洽，无法定真值" % (sizes,))
        sys.exit(2)
    return (sizes[0][0], sizes[0][1], None)


# ---------------------------------------------------------------- 声明登记
#
# 每条声明 = 「报告里的哪一行」+「真值从哪来」。
# ★ `row` 用来定位那一行（匹配 0 处或 >1 处都算失败 —— 报告改版必须被发现）；
#   `pat` 从该行里取出 (分子, 分母, 百分比)。
# ★ 百分比一律与现算值**逐字**比对（不看「差不多」）：报告写 100% 而现算 98.39% ⇒ 红。

RC1 = "07_交付物/RC1_整体状态评估_20261020.md"
ASSESS = "07_交付物/整体评估_20261021.md"
P71 = "07_交付物/Phase71_元素层重建_20261022.md"
PLAN = "00_项目管理/下一阶段规划_数据扩展与前端优化_20261019.md"
P72 = "07_交付物/Phase72_论文提及档位登记_20261022.md"

_NUM = r"([\d,]+)\s*/\s*([\d,]+)\s*=\s*\*{0,2}([\d.]+)%"

DECLARATIONS = [
    dict(key="symbols 覆盖率", file=RC1, row="| `symbols` |", pat=_NUM, truth=truth_symbols),
    dict(key="symbol_table 覆盖率", file=RC1, row="| `symbol_table` |", pat=_NUM, truth=truth_symbol_table),
    dict(key="domain 覆盖率", file=RC1, row="| `domain` |", pat=_NUM, truth=truth_domain),
    dict(key="applicability 覆盖率", file=RC1, row="| `applicability_status` |", pat=_NUM,
         truth=truth_applicability),
    dict(key="symbols 覆盖率（评估报告）", file=ASSESS, row="| **`symbols`** |", pat=_NUM,
         truth=truth_symbols),
    # ---- Phase 71：对外数据集的规模（这组数字以前没人咬，改了也不会有人知道）----
    dict(key="对外包节点/边（Phase71 报告）", file=P71,
         row="对外包最终规模：", pat=r"([\d,]+)\s*节点\s*/\s*([\d,]+)\s*边",
         truth=truth_public_size),
    dict(key="对外包节点/边（规划文档 F5）", file=PLAN, row="| **F5** |",
         pat=r"([\d,]+)\s*节点\s*/\s*([\d,]+)\s*边", truth=truth_public_size),
    dict(key="包内分子组成覆盖（Phase71 报告）", file=P71,
         row="| 包内分子有组成关系 |", pat=_NUM, truth=truth_molecule_coverage),
    # ---- 离线样例的规模：以前是手写，改完 release 就漂（170/255 那次的教训）----
    dict(key="release 离线样例节点/边（DATA_LICENSES）", file="DATA_LICENSES.md",
         row="实为 ", pat=r"([\d,]+)\s*节点\s*/\s*([\d,]+)\s*边", truth=truth_sample_size),
    # ---- Phase 72：m1 重算的档位分布（这行没有别的判据咬 ⇒ 必须登记在这里）----
    dict(key="提及边档位分布（Phase72 报告）", file=P72,
         row="| 提及边档位分布 |", pat=r"([\d,]+)\s*/\s*([\d,]+)", truth=truth_mention_scope),
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
    # ★ pattern 允许两种形态：`A / B = C%`（三组）与 `A 节点 / B 边`（两组）
    got = (int(m.group(1).replace(",", "")),
           int(m.group(2).replace(",", "")))
    want = decl["truth"]()
    if want[2] is None:      # 只比计数（如节点/边）
        ok = got == (want[0], want[1])
        return (ok, "报告 %d / %d ｜ 现算 %d / %d" % (got[0], got[1], want[0], want[1]))
    if m.lastindex is None or m.lastindex < 3:
        return (False, "pattern 缺百分比组，但真值是比率 —— %s" % lines[0][:80])
    got = got + (float(m.group(3)),)
    ok = got == want
    return (ok, "报告 %d / %d = %.2f%% ｜ 现算 %d / %d = %.2f%%"
            % (got[0], got[1], got[2], want[0], want[1], want[2]))


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
    # ★ 锚点必须**动态取**，不能写死。写死的两个失效场景都真实发生过：
    #   ① 报告数字随数据变（Phase 69 把它从 98.39% 提到 99.82%）⇒ 锚点落空；
    #   ② R32 探针注入的正是这一行 ⇒ 注入态下锚点必然落空，而落空后若 `return 1`，
    #      S2/S4 会被一起挡掉，R32 就变成「判据没红」的假红。
    #   故：从报告里解析出当前值，再改成**与它不同**的值（本来就是 100% 就改 99.99%）。
    mutated = None
    _lines = real.splitlines(True)
    for _i, _ln in enumerate(_lines):
        if _ln.startswith("| `symbols` |"):
            _m = re.search(_NUM, _ln)
            if _m:
                _n0 = int(_m.group(1).replace(",", ""))
                _d0 = int(_m.group(2).replace(",", ""))
                _p0 = float(_m.group(3))
                _n1 = _d0 if _n0 != _d0 else max(_n0 - 1, 0)
                _p1 = 100.0 if _p0 != 100.0 else 99.99
                _seg = "%s / %s = %.2f%%" % (format(_n1, ","), format(_d0, ","), _p1)
                _lines[_i] = _ln[:_m.start()] + _seg + _ln[_m.end():]
                mutated = "".join(_lines)
            break
    if mutated is None:
        rep.append(("S3 负对照：改掉报告数字 ⇒ 必须判不一致", False,
                    "无法在报告里定位并改写 symbols 行的数字（行前缀/格式已变？）"))
    elif mutated == real:
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
