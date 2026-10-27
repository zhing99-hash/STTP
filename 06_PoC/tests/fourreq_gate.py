#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""四项要求门禁 —— 交付物缺任一段即阻断。

★ 为什么需要一台机器来管这件事
------------------------------
2026-10-26 的裁决是「四项要求只作精神、不新增 gatecheck」。那天之后的经验是：
**只作精神的规则，两周内必然消失** —— 没人会让流水线停下来执行一次性的自觉。
2026-10-27 改口径：四条升级为硬要求，**由本脚本咬住**。

★ 判定口径（刻意设计，避免「天天红」）
--------------------------------------
1. **受管范围** = 最后变更日期 ≥ 生效日(2026-10-27) 的 `00_项目管理/*.md`。
   为什么按日期而不是全目录：全目录会让存量 15 份一次性全红，
   而天天红的判据会被人当背景忽略（D122 同族）—— 宁可只管新东西，也要让红灯有意义。
   ★ 这条同时也是真实的记账：本84以前的文档**确实**没有这四段，
   它们的结论在新口径下应当被重新核对，而不是自动获得合规认定。
2. **四段标记** —— 必须同时出现四种规范短语，且每段后得有实质内容（≥ 80 个非空白字符），
   防止只写标题不写内容的勾选式合规。
3. **反问条件** —— 第四段之后必须写出「反驳条件」，写不出来就是还没验证过。

★ 它自己也必须难红不了
----------------------
`--red` 模式：临时造一份缺段的文档 ⇒ 本门禁必须 FAIL。
这一步不是装饰 —— 「注册 ≠ 生效」的 D135 已经发生过一次。

用法： bash sttp.sh fourreq          （已挂在 `sttp.sh gatecheck` 链上）
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DOC_DIR = os.path.join(ROOT, "00_项目管理")

EFFECTIVE = "2026-10-27"

# ★ 显式豁免：**外部评审输入**不套我们的交付格式。
#   它们是别人写的评审意见，是**被审阅对象**而不是我方交付物 ——
#   强行要求第三方文档满足自己的四段格式，只会逼出一堆「看起来合规」的改写。
#   ★ 为什么写死在这里而不是配置文件：改它必须改代码 ⇒ 会进 git diff ⇒ 有人看得见。
#     静默跳过列表一旦可以随手加，它就会变成第二个垃圾回收站。
EXEMPT_INPUTS = {
    "STTP_科学家研究辅助系统方向调整建议_20261026.md":
        "外部评审输入（codex 2026-10-26 原件），非我方交付物",
    "项目评估与研发四项要求_20261026.md":
        "外部评审输入（codex 2026-10-26 原件），非我方交付物",
}

# 四段规范短语 —— 交付物里必须原样出现（也在 `四项要求_执行制式_20261027.md` §5 模板里）
REQUIRED = (
    ("①", "专家补做项"),
    ("②", "最原始素材"),
    ("③", "方法与工具"),
    ("④", "独立验证"),
)
MIN_BODY = 80          # 段落标题后至少要有这么多非空白字符
AFTER_KEY = "反驳条件"   # 第四段之后必须给出的东西


def _last_change_date(path):
    """最后的变更日期：优先 git 提交日期；未跟踪则退回文件 mtime。"""
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ad", "--date=short", "--", rel],
                             cwd=ROOT, capture_output=True, text=True, timeout=30)
        d = (out.stdout or "").strip().splitlines()
        if d and d[-1]:
            return d[-1]
    except Exception:
        pass
    return dt.date.fromtimestamp(os.path.getmtime(path)).isoformat()


def check_doc(path):
    """返回缺失/不合格的段清单。空列表 = 合格。"""
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    bad = []
    for mark, key in REQUIRED:
        i = text.find(key)
        if i < 0:
            bad.append("%s %s（缺）" % (mark, key))
            continue
        tail = text[i + len(key): i + len(key) + 1200]
        body = "".join(tail.split())
        if len(body) < MIN_BODY:
            bad.append("%s %s（只写了标题，%d 字 < %d）" % (mark, key, len(body), MIN_BODY))
    j = text.rfind("④")
    if j >= 0 and AFTER_KEY not in text[j:]:
        bad.append("④ 之后未写「%s」（没写＝没验证）" % AFTER_KEY)
    return bad


def _collect_subjects():
    subjects, skipped, exempt = [], [], []
    for p in sorted(glob.glob(os.path.join(DOC_DIR, "*.md"))):
        base = os.path.basename(p)
        d = _last_change_date(p)
        if base in EXEMPT_INPUTS:
            exempt.append((p, EXEMPT_INPUTS[base]))
        elif d >= EFFECTIVE:
            subjects.append((p, d))
        else:
            skipped.append((p, d))
    return subjects, skipped, exempt


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if "--red" in argv:
        return red_mode()

    print("=" * 78)
    print("  四项要求门禁：① 专家补做项 ② 最原始素材 ③ 方法与工具 ④ 独立验证")
    print("=" * 78)
    print("生效日 %s ｜ 受管目录 %s" % (EFFECTIVE, os.path.relpath(DOC_DIR, ROOT)))

    if not os.path.isdir(DOC_DIR):
        print("[FATAL] 目录不存在：%s" % DOC_DIR)
        return 1

    subjects, skipped, exempt = _collect_subjects()

    # ★ 负对照：如果受管集合为空，合格永远是空集上的恒真命题。
    if not subjects:
        print("[FAIL] 受管集合为空 ⇒ 门禁失明（抽取逻辑失效），按失败处理")
        return 1

    print("受管 %d 份（变更 ≥ %s）" % (len(subjects), EFFECTIVE))
    print("豁免 %d 份（外部评审输入，逐份打印理由）—— 明示豁免不是静默跳过：" % len(exempt))
    for p, why in exempt:
        print("        %s：%s" % (os.path.basename(p), why))
    print("存量 %d 份不适用新口径（见 docstring 口径说明）" % len(skipped))
    print("-" * 78)

    offenders = 0
    for p, d in subjects:
        bad = check_doc(p)
        if bad:
            offenders += 1
            print("[FAIL] %s（%s）" % (os.path.basename(p), d))
            for b in bad:
                print("         %s" % b)
        else:
            print("[PASS] %s（%s）四段齐备且含%s" % (os.path.basename(p), d, AFTER_KEY))

    print("-" * 78)
    ok = (offenders == 0)
    print("fourreq_gate: 合格 %d / 不合格 %d" % (len(subjects) - offenders, offenders))
    if not ok:
        print("⇒ 缺任一段的交付物按未完成处理，不构成阶段出口。")
    return 0 if ok else 1


def red_mode() -> int:
    """自证：造一份缺段的临时文档，门禁必须判 FAIL。"""
    print("=" * 78)
    print("  四项要求门禁 --red：验证它真会红")
    print("=" * 78)
    tmpdir = tempfile.mkdtemp(prefix="_fourreq_red_")
    cases = {
        "缺④独立验证": ("① 专家补做项\n" + "内容" * 60 + "\n② 最原始素材\n" + "内容" * 60
                     + "\n③ 方法与工具\n" + "内容" * 60 + "\n"),
        "只写标题不写内容": ("① 专家补做项\n② 最原始素材\n③ 方法与工具\n④ 独立验证\n"
                        + "反驳条件\n"),
        "写了四段但缺反驳条件": ("① 专家补做项\n" + "内容" * 60 + "\n② 最原始素材\n" + "内容" * 60
                          + "\n③ 方法与工具\n" + "内容" * 60 + "\n④ 独立验证\n" + "内容" * 60 + "\n"),
    }
    wrong_as_green = []
    for name, body in cases.items():
        p = os.path.join(tmpdir, "t.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
        bad = check_doc(p)
        caught = bool(bad)
        print("[%s] %s" % ("CAUGHT" if caught else "MISSED", name))
        if not caught:
            wrong_as_green.append(name)
    # 正对照：合格样本不得被错杀
    good = ("① 专家补做项\n" + "内容" * 60 + "\n② 最原始素材\n" + "内容" * 60
            + "\n③ 方法与工具\n" + "内容" * 60 + "\n④ 独立验证\n" + "内容" * 60
            + "\n反驳条件：若外部库同不同意，结论作废\n")
    p = os.path.join(tmpdir, "good.md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(good)
    gbad = check_doc(p)
    print("[%s] 正对照：合格文档不得被错杀" % ("OK" if not gbad else "WRONG"))
    if gbad:
        wrong_as_green.append("正对照被错杀")

    try:
        for f in os.listdir(tmpdir):
            os.remove(os.path.join(tmpdir, f))
        os.rmdir(tmpdir)
    except OSError:
        pass

    print("-" * 78)
    ok = not wrong_as_green
    print("fourreq_gate --red: %s" % ("OK 门禁可红" if ok else "失明！" + "；".join(wrong_as_green)))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
