# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""「非通过」判据的**自述** —— 唯一实现（铁律 #88 / #92 / #93；D57 / D58 族）。

问题
----
一台仪器因为「环境缺件」而**不通过**时，读者看到的是「缺 networkx」这类消息，
于是去查依赖 —— 但**真实原因**可能是**解释器选错**：`STTP_PYTHON` 记在**被 gitignore
的 `.env`** 里 ⇒ 裸 clone 下 `PY="${STTP_PYTHON:-python}"` 回落到 PATH 上的 `python`
（本机实测落到**无依赖**的解释器）⇒ 依赖判定集体变红，而报错的长相指向**产品缺陷**。
两种情形**外观完全相同**，唯一能把它们分开的信息就是「这些判定是在**哪个解释器**下做的」。

纪律（铁律 #92：同一条纪律须覆盖链上**每个**入口）
--------------------------------------------------
链上所有「缺依赖 / 前置不满足 ⇒ **非通过**」的出口，都必须自述解释器：

    verify_release.py                 G1/G2/G3（D57 修）
    frozen_gate.py                    门禁依赖缺失 SKIP（D58）
    tests/a4_offline_render.py        前置不满足块（D56）
    tests/phase51_task_flow_e2e.py    前置不满足块（D56）
    tests/phase45_install_selfcheck.py 构建后端缺失 SKIP
    dataset/export_dataset.py         `[前置缺失]` 规范图缺件（D107，Phase 71 补）
    dataset/wget_offline_e2e.py       `[前置缺失]` 数据集产物缺件（D107，Phase 71 补）

★ 末两条的来历值得写下来：它们**是 Phase 70 加的，漏了这条纪律**，而当时所有测试都绿 ——
  因为缺件分支在绿树上永不执行。抓出它们的不是测试，是 `gatecheck` 的静态扫描（Q3）。
  这就是「同族未改」（D58）的真实复发形态：**不是忘了改，是根本没想到这也算一个入口**。

★ 为什么把实现**收在一处**：Phase 53 只修了 `verify_release` 一处、把其余登记为
  「同族未改」（D58）。「同族未改」的真正危险不是那几行没改，而是**将来新增第 6 个
  入口时，没有任何东西会提醒你**。故本轮做三件事：
    ① 实现收在一处（本模块）；
    ② 入口清单**声明**为 `ENTRY_POINTS`（供扫描仪核对，不散落在各处注释里）；
    ③ 由 `bash sttp.sh interpcheck` 静态扫描全仓，**防新增入口漏网**（#92 的真解）。

不使用任何第三方依赖 —— 本模块本身就是一台诊断工具，**不能因为缺依赖而无法
自述「缺依赖」**（铁律 #101 的同族要求：非自包含仪器不得静默）。
"""
from __future__ import annotations

import sys

# ---------------------------------------------------------------------------
# 入口清单（**声明**，非枚举副产物）
#
# ★ 其正确性由 `06_PoC/tests/interp_selfreport_check.py` **静态核对**：
#   ① 每个路径必须真实存在；
#   ② 每个文件必须引用本模块（`interp_note`）；
#   ③ 扫描全仓时凡出现「依赖/前置缺失」类措辞的仪器文件，必须 ⊆ 本清单 ∪ 豁免表；
#   ④ 本清单与豁免表均**非空**（否则检查会退化为真空 —— 铁律 #98 的同族）。
# ★ 加一个入口就改这里一行；忘了改 ⇒ 扫描仪变红（这正是本清单存在的意义）。
# ---------------------------------------------------------------------------
ENTRY_POINTS = (
    "06_PoC/release/verify_release.py",
    "06_PoC/frozen_gate.py",
    "06_PoC/tests/a4_offline_render.py",
    "06_PoC/tests/phase51_task_flow_e2e.py",
    "06_PoC/tests/phase45_install_selfcheck.py",
    # ★ Phase 70 新增、Phase 71 才被 Q3 抓出来的两个（D107）：
    #   它们新增了 `[前置缺失]` 出口，但当时没人想到「这也是第 6/7 个入口」。
    #   这正是本清单存在的理由 —— **新增出口时的漏网，只有静态扫描能发现**，
    #   因为缺件分支在绿树上永不执行，跑测试也看不到它。
    "06_PoC/dataset/export_dataset.py",
    "06_PoC/dataset/wget_offline_e2e.py",
    # ★ Phase 73：`reverify_attest.py --check` 在**证明表缺失/取不到 blob** 时无法判定。
    #   本轮是**主动登记的**（不是等 Q3 来报）—— 上一轮 D107 的教训是"新增出口时的漏网
    #   只有静态扫描能发现"，那就别等它发现。
    "11_真实数据/reverify_attest.py",
    # ★ Phase 74：仓库卫生自检。它有 `[前置缺失] git ls-files 失败` 出口 ——
    #   同样是**主动登记**（D107 的学费：缺件分支在绿树上永不执行，等人来报就晚了）。
    "06_PoC/tests/repo_hygiene_selfcheck.py",
)


def note() -> str:
    """解释器自述（唯一实现）。"""
    return "python %s @ %s" % (sys.version.split()[0], sys.executable)


def dep_detail(missing, ready_hint=None) -> str:
    """「必要依赖就绪」类断言的 detail —— G1 与 G3 **同源**，不得各写一份（#88）。"""
    if missing:
        return "缺失 %s · 解释器 %s" % (missing, sys.executable)
    tail = "全就绪" if not ready_hint else ready_hint
    return "%s；%s" % (note(), tail)


def skip_detail(reason, extra="") -> str:
    """「缺依赖 / 前置不满足 ⇒ **非通过**」出口的通用 detail（唯一实现）。

    ★ 与 `dep_detail` **刻意分开**：语义不同。`dep_detail` 描述"检查了哪些依赖"，
      本函数描述"某一步**没有真正执行**、原因是环境/前置不满足"。
      二者都必须自述解释器，但混用一个函数会让两处 detail 长成一模一样，
      读者分不清「检查过且齐备」与「根本没执行」。
    """
    s = "%s · 解释器 %s" % (reason, sys.executable)
    if extra:
        s += "；%s" % extra
    return s


# 常驻守卫的断言名（**字面量**在各调用点重复书写，供 `bash sttp.sh names` 采集 —— #98）
GUARD_NAME = ("P0 缺件/前置诊断自述解释器（铁律 #93 · D57/D58 族）："
              "合成缺件分支的 detail 须含 sys.executable")


def guard():
    """常驻守卫：用**合成输入**触发"缺件分支"，返回 `(ok, 文本)`。

    ★ 为什么必须用合成输入：绿树下真实缺件分支**永不执行**，只校验真实路径 ⇒
      这条纪律就**没有常驻守卫**（D57 的 G3 就是这么做的，本模块把它推广到全链）。
    ★ 其**可红性**（把 `sys.executable` 从 detail 里删掉 ⇒ 必须变红）由
      `bash sttp.sh redprobe` 的构造性探针证明（铁律 #100）。
    """
    probe = "__probe_missing__"
    ok = (sys.executable in note()
          and sys.executable in dep_detail([probe])
          and sys.executable in skip_detail(probe))
    return ok, "%s ｜ 三处 detail 均含解释器 = %s" % (note(), ok)
