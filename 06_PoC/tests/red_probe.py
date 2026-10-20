# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""**构造性可红性**探针 —— 注入已知缺陷，断言必须**真的会红**（Phase 53 · D55 / #87 / #100）。

问题（D55 原文）
----------------
`bash sttp.sh names` 只能核对**名字集合**：它能证明"没有断言被删"，但**证明不了
"还活着的断言仍在断言它声称的语义"**。一次静默退化可以长成这样：

    rep.add(True, "F37k 端点客户端预算 == 契约 client_budget_ms", "")   # 判据被换成恒真

名字没变、总数没变、`names` 报"删除 0" —— 而这条断言从此**永远绿**。

做法
----
对**关键断言**逐个注入"已知缺陷"，然后断言**目标断言必须真的变红**：

    突变事实源（产品/工具） → 跑该断言所在仪器 → 目标断言必须 [FAIL] → **逐字节还原**

若某条断言的判据被换掉 / 被短路 / 被删（只是名字还在），注入缺陷后它**不会变红**
⇒ 本仪器 FAIL。**第一个报出来的人是它，而不是三个月后的评审。**

★ 这是"仪器的仪器"的第三种：`gatecheck` 注入假**边**证明不变量会红；
  `assert_name_diff --selfcheck` 注入已知**答案**证明核对器会数；
  **本仪器注入已知缺陷，证明断言会红**（#38「PASS 只说明当前没红，不说明有能力红」）。

注册表（两类探针，都是"注入已知坏东西，看判据真不真会红"）
------------------------------------------------------------
* `mutate`：改**事实源**里的一处（锚点须**恰好命中一次**），跑命令，
  期望 `expect_red` 里的断言出现在 `[FAIL]` / `❌` 行上；
  `expect_red == []` 表示**负对照**：要求命令零 FAIL 且 RC=0
  （证明"变红"检测不是恒真 —— 否则整台仪器的绿都是假的）。
* `expect`：**不改文件**，跑命令并期望指定**退出码**与文本
  （用于**退出码纪律**：外部前置缺失必须 RC=2，**不得返回 0**，D56）。

安全（铁律 #97：不许弄脏工作树）
--------------------------------
* 每条 mutate 探针**独立备份 + 逐字节还原 + sha256 校验**（`try/finally`）；
  还原失败 ⇒ **立刻中止并 RC=3**（不静默继续，不让污染扩散）。
* 全部跑完再整体核对一遍（`RR`）。

退出码
------
`0` = 全部探针都**真的变红**；`1` = 有探针没变红（**断言可能是死的 / 判据被换掉**）；
`2` = 缺前置（node / playwright 等；该探针记 SKIP，**不计通过**，铁律 #20）；
`3` = 还原失败或自检失败（**工作树可能已被污染**，须人工处置）。

用法
----
    python 06_PoC/tests/red_probe.py [--only R1,R4] [--list] [--timeout S] [--json OUT]
    bash sttp.sh redprobe [--only …]
    # ★ 已挂进 `gatecheck`，且刻意放在 `require_authority` **之前**：
    #   本仪器不依赖权威图 ⇒ **干净检出里也能跑**（与 `assert_name_diff --selfcheck` 同理）。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
POC = os.path.dirname(HERE)
ROOT = os.path.dirname(POC)

# ★ 探针一律用**当前解释器**跑子命令。理由：`sttp.sh` 已按 `$STTP_PYTHON` 选好了解释器；
#   若在这里另起 `python`，本仪器会自己踩 D57（**解释器静默换人**）——
#   把"选错解释器"读成"产品缺陷"，正是 D57 要防的事。
PY = sys.executable


class RestoreError(RuntimeError):
    """还原失败 —— 工作树可能已被污染，必须显式告警（不得静默继续）。"""


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
PROBES = [
    {
        "id": "R1",
        "kind": "mutate",
        "why": "F37k（前端端点预算 == 契约 client_budget_ms）",
        "file": "06_PoC/js/core.js",
        "old": "  search: 3000,",
        "new": "  search: 9999,",
        "cmd": "NODE tests/frontend_core_check.mjs",
        "cwd": POC,
        "expect_red": ["F37k"],
        "requires": ["node"],
    },
    {
        "id": "R2",
        "kind": "mutate",
        "why": "F37h（参数序列化真的含 snapshot_id，D42）",
        "file": "06_PoC/js/core.js",
        "old": '  put("snapshot_id", o.snapshot_id);',
        "new": "  /* redprobe: 去掉 snapshot_id 序列化 */",
        "cmd": "NODE tests/frontend_core_check.mjs",
        "cwd": POC,
        "expect_red": ["F37h"],
        "requires": ["node"],
    },
    {
        "id": "R3",
        "kind": "mutate",
        "why": "S2（#87 的原始动机场景：总数不变但确实删了断言）",
        "file": "06_PoC/tests/assert_name_diff.py",
        "old": '        "removed": sorted(so - sn),',
        "new": '        "removed": [],',
        "cmd": "PY 06_PoC/tests/assert_name_diff.py --selfcheck",
        "cwd": ROOT,
        "expect_red": ["S2"],
        "requires": [],
    },
    {
        "id": "R4",
        "kind": "mutate",
        "why": ("G3（缺依赖诊断必须自述解释器 —— D57 的常驻守卫）。"
                "★ Phase 55（D58）：事实源已从 `verify_release.py` **上移到唯一实现** "
                "`06_PoC/interp_note.py`（实现收在一处，#88）⇒ 探针必须**跟着事实源走**。"
                "留在旧位置会变成『锚点命中 0 次』的空转 —— 那正是 `RS` 专门要抓的东西。"),
        "file": "06_PoC/interp_note.py",
        "old": '        return "缺失 %s · 解释器 %s" % (missing, sys.executable)',
        "new": '        return "缺失 %s" % missing',
        "cmd": "PY 06_PoC/release/verify_release.py --release",
        "cwd": ROOT,
        "expect_red": ["G3"],
        "requires": [],
    },
    {
        "id": "R5",
        "kind": "expect",
        "why": "D56：页面不可达（外部前置缺失）⇒ offline 必须 RC=2 且自述前置，不得 RC=0",
        "cmd": "PY 06_PoC/tests/a4_offline_render.py --url http://127.0.0.1:1/",
        "cwd": ROOT,
        "expect_rc": 2,
        "expect_text": ["目标页面不可达"],
        "requires": ["playwright"],
    },
    {
        "id": "R6",
        "kind": "expect",
        "why": "D56：viz 未跑（外部前置缺失）⇒ taskflow 必须 RC=2 且自述前置，不得 RC=0",
        "cmd": "PY 06_PoC/tests/phase51_task_flow_e2e.py --url http://127.0.0.1:1/",
        "cwd": ROOT,
        "expect_rc": 2,
        "expect_text": ["前置"],
        "requires": ["playwright"],
    },
    {
        "id": "R7",
        "kind": "mutate",
        "why": "F42a（Phase 54 年代分桶的 **1900 边界**：世纪桶 ↔ 十年桶的切换点）",
        "file": "06_PoC/js/core.js",
        "old": "    if (y < 1900) return `${Math.ceil(y / 100)} 世纪`;",
        "new": "    if (y < 1910) return `${Math.ceil(y / 100)} 世纪`;",
        "cmd": "NODE tests/frontend_core_check.mjs",
        "cwd": POC,
        "expect_red": ["F42a"],
        "requires": ["node"],
    },
    {
        "id": "R8",
        "kind": "mutate",
        "why": "F42f（「哨兵 vs 缺键」必须可分辨：`description` 缺键时**不得**渲染该行）",
        "file": "06_PoC/js/evidence.js",
        "old": '  if (attrs.description) parts.push(kv("公式介绍", esc(attrs.description)));',
        "new": '  parts.push(kv("公式介绍", esc(attrs.description || "")));',
        "cmd": "NODE tests/frontend_core_check.mjs",
        "cwd": POC,
        "expect_red": ["F42f"],
        "requires": ["node"],
    },
    {
        "id": "R9",
        "kind": "mutate",
        "why": "F42j（图例「发现年代」区块的**时间序**真的在工作）",
        "file": "06_PoC/js/viz.js",
        "old": "  const eraList = (eras || []).slice().sort((a, b) => eraSortKey(a) - eraSortKey(b));",
        "new": "  const eraList = (eras || []).slice();",
        "cmd": "NODE tests/frontend_core_check.mjs",
        "cwd": POC,
        "expect_red": ["F42j"],
        "requires": ["node"],
    },
    {
        "id": "R10",
        "kind": "mutate",
        "why": ("G4（**门禁缺依赖 SKIP** 必须自述解释器 —— D58 的常驻守卫，Phase 55 新增）。"
                "与 R4 是**同一纪律的两半**：R4 守「检查过哪些依赖」的自述，"
                "R10 守「某一步**没有真正执行**」的自述（`skip_detail`）。"
                "两半分开守，是因为它们**语义不同**、将来可能各自被改坏。"),
        "file": "06_PoC/interp_note.py",
        "old": '    s = "%s · 解释器 %s" % (reason, sys.executable)',
        "new": '    s = "%s（解释器信息已移除）" % reason',
        "cmd": "PY 06_PoC/release/verify_release.py --release",
        "cwd": ROOT,
        "expect_red": ["G4"],
        "requires": [],
    },
    {
        "id": "R11",
        "kind": "mutate",
        "why": ("D64（Phase 55 新登记）：`--check-regenerated` 是「本流水线产物是否已提交」的"
                "前置断言。★ 注入的退化是**最阴的一种**：把差异判据换成**恒真空**"
                "（`regenerated()` 永远返回 []）—— 它在输出上与「真的没有差异」"
                "**完全不可分**，只有 `M9` 的构造性守卫能抓到它。"),
        "file": "06_PoC/release/pipeline_outputs.py",
        "old": "    return [p for p in paths if blob_or_none(p) != read_bytes_of(p)]",
        "new": "    return []                      # redprobe: 恒真空（判据被换掉）",
        "cmd": "PY 06_PoC/release/make_manifest.py --check-regenerated",
        "cwd": ROOT,
        "expect_red": ["M9"],
        "requires": [],
    },
    {
        "id": "R12",
        "kind": "mutate",
        "why": ("V13（Phase 56 / B1）：底部路径栏**自组请求参数** ⇒ 不读左侧 trust/跳数。"
                "外部评审 2026-10-17 实测：切到 `all` 后再查，请求里**仍没有** `trust`，"
                "而后端默认 `verified`；可它紧邻的文案正写着「可把可信度切到 all 再试」。"
                "★ 注入的是**原缺陷的形状**（写死 `trust:\"verified\"` + 不用 `taskRequest`），"
                "而不是随便改坏一行 —— 探针要能代表「这个 bug 回来了」。"),
        "file": "06_PoC/js/main.js",
        "old": '  const req = taskRequest({ mode: "path" }, {',
        "new": ('  const req = { mode: "path", trust: "verified", '
                'seeds: src + "," + dst };   /* redprobe: 回到自组参数的老写法 */'),
        "cmd": "PY 06_PoC/tests/phase43_frontend_selfcheck.py",
        "cwd": ROOT,
        "expect_red": ["V13"],
        "requires": [],
    },
    {
        "id": "R13",
        "kind": "mutate",
        "why": ("⑨（Phase 56 / B2）：`repo_blob()` 契约是「不在仓库 ⇒ None」，"
                "旧自检直接 `None + bytes` ⇒ TypeError。评审那次全量冻结门禁 107/108，"
                "现场只有 traceback、没有一句可读判定。"
                "★ 用 `--selfcheck-guard` 单跑 ⑨（0.4 s）而不是整套 `--selfcheck`（3 min）："
                "**一条没人愿意跑的构造性证据等于没有**。"),
        "file": "11_真实数据/lineage_invalidation.py",
        "old": ('    body = sr.repo_blob(rel, ROOT)\n'
                '    if body is None:\n'
                '        return False, ("dependency_not_in_repo: %s 不在 HEAD 中 ⇒ 无法基于**仓库字节**"\n'
                '                       "构造变异（先提交该参考表，再跑本自检）" % rel)'),
        "new": "    body = sr.repo_blob(rel, ROOT)      # redprobe: 去掉 None 守卫（回到旧写法）",
        "cmd": "PY 11_真实数据/lineage_invalidation.py --selfcheck-guard",
        "cwd": ROOT,
        "expect_red": ["⑨"],
        "requires": [],
    },
    {
        "id": "R14",
        "kind": "mutate",
        "why": ("V18h（D72）：`unverifiable_not_in_repo` 的边**不得**被判 stale（未入库 ≠ 变更）。"
                "★ 旧判据是「总数比总数」：`ind_stale == changed + no_source` 两边在 A04 层贡献 0 时"
                "**恒等** ⇒ 什么都不守；改成构造性判据（逐条验真实 not_in_repo 边）后，"
                "本探针才测得出「口径真的在守这条边界」。"),
        "file": "03_知识层/queries.py",
        "old": "    if edge_fidelity(e, stale_registry) in STALE_FIDELITIES:\n        return True",
        "new": ('    if edge_fidelity(e, stale_registry) in STALE_FIDELITIES + ("unverifiable_not_in_repo",):\n'
                "        return True      # redprobe: 把 not_in_repo 也算 stale（D72）"),
        "cmd": "PY 06_PoC/tests/phase43_subgraph_selfcheck.py",
        "cwd": ROOT,
        "expect_red": ["V18h"],
        "requires": [],
    },
    {
        "id": "R15",
        "kind": "mutate",
        "why": ("证据等级（Phase 58）：边的**线宽 + 透明度**必须由 grade 驱动。"
                "★ 若写死成常量，五档塌成一档 ⇒ 又退回 confidence 时代的病（76% 挤在 0.95、"
                "宽度无区分度），而这是 Phase 58 要解决的正是这个问题。"
                "本探针把两行都写成常量 ⇒ F43b / F43c 必须变红（F43c 的「六档互异」直接被消灭）。"),
        "file": "06_PoC/js/viz.js",
        "old": ("      _width: gradeToWidth((typeof e.grade === \"number\") ? e.grade : null),\n"
                "      _opacity: gradeToOpacity((typeof e.grade === \"number\") ? e.grade : null),"),
        "new": "      _width: 2,      // redprobe: 写死（等级编码退场）\n"
               "      _opacity: 0.6,   // redprobe: 写死",
        "cmd": "NODE tests/frontend_core_check.mjs",
        "cwd": POC,
        "expect_red": ["F43b", "F43c"],
        "expect_rc": 1,
        "requires": ["node"],
    },
    {
        "id": "R16",
        "kind": "mutate",
        "why": ("证据等级**不得**让「未判定」冒充最高档：登记表不可用时 grade 为 null，"
                "前端若把 null 当 5 分渲染（`grade === null ? 5 : grade`），"
                "就会把「不知道」画成「最强证据」—— 与 D72「恒真判据什么都不守」同族。"
                "本探针把 null 兜底成 5 ⇒ F43b 必须变红。"),
        "file": "06_PoC/js/style.js",
        "old": "export function gradeStyle(g) {\n  return GRADE_STYLE[g] || GRADE_UNKNOWN;",
        "new": "export function gradeStyle(g) {\n  return GRADE_STYLE[g] || GRADE_STYLE[5];   // redprobe: null 冒充 E5",
        "cmd": "NODE tests/frontend_core_check.mjs",
        "cwd": POC,
        "expect_red": ["F43b"],
        "expect_rc": 1,
        "requires": ["node"],
    },
    {
        "id": "R17",
        "kind": "mutate",
        "why": ("证据等级 G2：5 分必须**可字节复算**（`fidelity == verified`）。"
                "把 `unverifiable_not_in_repo` 从 2 档改判成 5 档 ⇒ 合成图里那条 "
                "「cross_source + 来源未入库」的靶边会变成 5 分 ⇒ G2 必须变红。"
                "★ 用 `--selfcheck`：**不依赖权威图** ⇒ 干净检出里也能跑（与 R13 同纪律）。"),
        "file": "03_知识层/queries.py",
        "old": '"unverifiable_not_in_repo": 2,',
        "new": '"unverifiable_not_in_repo": 5,   # redprobe: 未入库改判可复算（G2）',
        "cmd": "PY 06_PoC/grade_report.py --selfcheck",
        "cwd": ROOT,
        "expect_red": ["G2"],
        "expect_rc": 1,
        "requires": [],
    },
    {
        "id": "R18",
        "kind": "mutate",
        "why": ("证据等级 G3：**1 分不得由「可复算的来源断言」退化而来**（构造性反例）。"
                "把 `source_asserted` 从 3 档降到 1 档 ⇒ 那条「可复算的 source_asserted」边"
                "变成 1 分 ⇒ G3 必须变红。这条守的是**映射表本身被改**这一类退化："
                "改一个数字就能让 13 797 条边集体掉两档，而名字集合上完全看不见。"),
        "file": "03_知识层/queries.py",
        # ★ 锚点必须**包含到该行末尾**（`"model_inferred": 1}`）：锚点后面还剩半行代码，
        #   行尾注释会把它们一起吞掉 ⇒ SyntaxError ⇒ 命令根本跑不起来
        #   （RC 非零但**没有 FAIL 行** ⇒ 本探针会误报"断言没变红"——
        #    这是本探针唯一一次"看起来是断言死了、其实是突变写错"，故记在此处）。
        "old": '"source_asserted": 3, "model_inferred": 1}',
        "new": ('"source_asserted": 1, "model_inferred": 1}\n'
                "            # redprobe: 来源断言降档（G3）"),
        "cmd": "PY 06_PoC/grade_report.py --selfcheck",
        "cwd": ROOT,
        "expect_red": ["G3"],
        "expect_rc": 1,
        "requires": [],
    },
    {
        "id": "R19",
        "kind": "mutate",
        "why": ("证据等级 G4：对外口径**不得高于内部**（对外不可能比内部更可复算）。"
                "★ 这条不变量是靠 `min(内部 F, 再分发折扣)` 的**表达式**守住的 —— "
                "早前写成 `if/else` 分支时，5 条「来源未登记」的边出现了对外 2 分 > 内部 1 分，"
                "是 `bash sttp.sh grade` 实测抓出来的（不是断言设计的功劳）。"
                "本探针把折扣写成恒 5 ⇒ 对外全面高于内部 ⇒ G4 必须变红。"),
        "file": "03_知识层/queries.py",
        "old": "    f = min(f_int, discount)",
        "new": "    f = 5   # redprobe: 去掉再分发折扣（G4）",
        "cmd": "PY 06_PoC/grade_report.py --selfcheck",
        "cwd": ROOT,
        "expect_red": ["G4"],
        "expect_rc": 1,
        "requires": [],
    },
    {
        "id": "R20",
        "kind": "mutate",
        "why": ("符号回填 S2：**推导出的字形集必须等于源自己声明的字形集**。"
                "把「优先取源声明（parquet `symbols` 列）」改成「优先取目标节点自带 symbol」，"
                "70 条 `light_speed` 的边就会把 `c₀`（源写法）换成 `c`（策展写法）⇒ S2 必须变红。"
                "★ 这一改读起来像「少绕一层、更直接」，只有实测集合比对能抓住它。"),
        "file": "11_真实数据/phase61_symbol_backfill_delta.py",
        "old": "        if is_pb:\n            key = target2key.get(tid)\n            if key and name2glyph.get(key):\n                sym, src = name2glyph[key], \"pb_symbols_column\"",
        "new": "        if is_pb:\n            key = target2key.get(tid)\n            if False and key and name2glyph.get(key):   # redprobe: 改用策展写法（S2）\n                sym, src = name2glyph[key], \"pb_symbols_column\"",
        "cmd": "PY 11_真实数据/phase61_symbol_backfill_delta.py --dry",
        "cwd": ROOT,
        "expect_red": ["S2"],
        "expect_rc": 1,
        "requires": ["authority"],
    },
    {
        "id": "R21",
        "kind": "mutate",
        "why": ("符号回填 Y3：**写进 symbols 的字形必须在该节点原文里出现**。"
                "把这条判据改成恒假（`if False`）⇒ 合成图里那条「字形 q 不在 latex 中」的"
                "违规节点不再被告发 ⇒ `--selfcheck` 的『Y3:bad 能被踩红』必须变红。"
                "★ 用 `--selfcheck`：不依赖权威图，干净检出里也能跑（与 R17–R19 同纪律）。"),
        "file": "06_PoC/symbol_report.py",
        "old": "        bad = [s for s in (syms or []) if s and s not in hay]",
        "new": "        bad = []   # redprobe: 字形可查性判据被架空（Y3）",
        "cmd": "PY 06_PoC/symbol_report.py --selfcheck",
        "cwd": ROOT,
        "expect_red": ["Y3"],
        "expect_rc": 1,
        "requires": [],
    },
    {
        "id": "RN",
        "kind": "mutate",
        "why": "负对照：**纯注释**级改动不得让任何断言变红（证明「变红」检测非恒真）",
        "file": "06_PoC/js/core.js",
        "old": "export const CLIENT_MARGIN_MS = 4000;",
        "new": ("export const CLIENT_MARGIN_MS = 4000;\n"
                "// redprobe 负对照：纯注释改动，不得改变任何判定"),
        "cmd": "NODE tests/frontend_core_check.mjs",
        "cwd": POC,
        "expect_red": [],
        "expect_rc": 0,
        "requires": ["node"],
    },
]

# ★ 这份**字面量**列表与 `emit()` 里写死的断言名一一对应。
#   为什么两处都写死：`bash sttp.sh names` 的采集器取「第 2 个实参的**首个字符串常量**」
#   （f-string 只在**开头是字面量**时可用）⇒ 断言名必须写成字面量才进得了名字集合。
#   两处的一致性由 `R0` 断言（否则"注册表改了、断言行忘改"会静默）。
DECLARED_PROBE_IDS = ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9",
                      "R10", "R11", "R12", "R13", "R14", "R15", "R16",
                      "R17", "R18", "R19", "R20", "R21", "RN")

# 与 emit() 一一对应的键（含仪器自身的三条自检 + 注册表自检）。
ALL_ROW_IDS = DECLARED_PROBE_IDS + ("RS", "RR", "R0")


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------
def read_text(path):
    """**不做行尾翻译**地读（`newline=""`）—— 还原必须逐字节一致（CRLF 也不能被改写）。"""
    return io.open(path, "r", encoding="utf-8", newline="").read()


def write_text(path, s):
    io.open(path, "w", encoding="utf-8", newline="").write(s)


def sha256_text(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def find_node():
    """定位可用的 node（候选序与 `phase43_frontend_selfcheck.find_node()` 一致）。

    ★ 此处**不 import** 同目录的那个模块：`bash sttp.sh install` 的 A05-7a
      「依赖声明覆盖全仓实际 import」会扫 `ast` import 节点，届时要额外论证
      "本地模块不算第三方依赖"。12 行重复换掉一次评审成本，值得；
      候选序**同源**（`STTP_NODE` → managed 目录 → PATH），改一处须改两处（记 D61）。
    """
    cands = []
    env = os.environ.get("STTP_NODE")
    if env:
        cands.append(env)
    base = os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries", "node", "versions")
    if os.path.isdir(base):
        for d in sorted(os.listdir(base), reverse=True):
            exe = os.path.join(base, d, "node.exe" if os.name == "nt" else "node")
            if os.path.isfile(exe):
                cands.append(exe)
    for c in cands:
        if os.path.isfile(c):
            return c
    from shutil import which
    return which("node")


_FAIL_LINE = re.compile(r"^\s*(?:\[FAIL\]|❌)\s+(.+?)\s*$")


def failed_names(out):
    """从仪器输出里取**被判 FAIL 的断言名**。

    覆盖本仓两种打印形状：`  [FAIL] NAME  —— detail`（phase43_* / node）
    与 `❌ NAME detail`（verify_release）。★ 只认"被判失败"的行 ——
    不能拿"名字出现过"当判据（那会把 PASS 也数进来 ⇒ 恒真）。
    """
    return [m.group(1) for m in (_FAIL_LINE.match(ln) for ln in out.splitlines()) if m]


def starts_with_id(name, eid):
    """`F37k` 必须作为**记号**命中（防止 `F37k` 命中 `F37k2`）。"""
    if not name.startswith(eid):
        return False
    rest = name[len(eid):]
    return rest == "" or not (rest[0].isalnum() or rest[0] == "_")


class Rep:
    """极简报告器（与 `phase43_*_selfcheck.py` / `a4_offline_render.py` **同形**）。

    ★ 同形是硬要求：`bash sttp.sh names` 的采集器认的是**调用形状**
      （`attr in {add, skip, check, ok}`）+ **字面量断言名**。
      本文件是"防判据被换掉"的仪器，它自己的断言若采集不到，
      它就可以悄悄退化而无人知晓 —— 自指的失败（铁律 #98）。
    """

    def __init__(self):
        self.rows = []
        self.n_pass = 0
        self.n_fail = 0
        self.n_skip = 0

    def add(self, cond, name, detail=""):
        """**三态**报告：`True` ⇒ PASS；`False` ⇒ FAIL；**`None` ⇒ SKIP**（不可判定）。

        ★ 为什么把 `None` 放进 `add` 而不是另开一条 `skip()` 调用：
          `bash sttp.sh names` 的采集器只认调用形状 + **字面量**断言名。
          "探针未运行"（缺前置 / 被 `--only` 排除）既不是通过也不是失败（铁律 #20/#67），
          若为此另写一批 `rep.skip("<字面量>")`，同一批断言名就要在源码里写两遍
          ⇒ 两处必然漂移（#88）。把"不可判定"编码进**同一个调用**里，
          调用形状与名字就只有一份。
        """
        if cond is None:
            self.n_skip += 1
            self.rows.append(("SKIP", name, detail))
            return False
        if cond:
            self.n_pass += 1
            self.rows.append(("PASS", name, detail))
        else:
            self.n_fail += 1
            self.rows.append(("FAIL", name, detail))
        return bool(cond)

    def skip(self, name, detail=""):
        self.n_skip += 1
        self.rows.append(("SKIP", name, detail))

    def dump(self):
        for kind, name, detail in self.rows:
            print("  [%s] %s" % (kind, name))
            if detail:
                print("         %s" % detail)
        print("\n  red_probe: PASS %d / FAIL %d / SKIP %d"
              % (self.n_pass, self.n_fail, self.n_skip))


# ---------------------------------------------------------------------------
# 跑一条探针
# ---------------------------------------------------------------------------
def run_cmd(probe, timeout, node):
    argv = []
    for c in probe["cmd"].split(" "):
        argv.append(node if c == "NODE" else (PY if c == "PY" else c))
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    r = subprocess.run(argv, cwd=probe["cwd"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def probes_missing(probe, avail):
    return [k for k in probe.get("requires", []) if not avail.get(k)]


def run_one(probe, avail, timeout, node):
    """返回 (ok, detail)。`ok is None` 表示**不可判定**（缺前置 ⇒ 记 SKIP，不计通过）。"""
    miss = probes_missing(probe, avail)
    if miss:
        return None, "缺前置：%s" % "/".join(miss)

    if probe["kind"] == "expect":
        rc, out = run_cmd(probe, timeout, node)
        bad_txt = [t for t in probe.get("expect_text", []) if t not in out]
        want = probe["expect_rc"]
        ok = (rc == want) and not bad_txt
        detail = "RC=%d（期望 %d）" % (rc, want)
        if bad_txt:
            detail += "；输出缺：%s" % "、".join(bad_txt)
        else:
            detail += "；前置自述已打印"
        if rc != want:
            detail += (" ★ 前置缺失却返回 %d ⇒ **SKIP 与 PASS 在退出码上不可分**（#101）" % rc)
        return ok, detail

    # ---- mutate ----
    path = os.path.join(ROOT, probe["file"])
    if not os.path.isfile(path):
        return False, "事实源不存在：%s" % probe["file"]
    before = read_text(path)
    h0 = sha256_text(before)
    occ = before.count(probe["old"])
    if occ != 1:
        return False, ("锚点命中 %d 次（须恰好 1 次）⇒ **探针自身已过期**，"
                       "不是被测对象的问题（须重写探针，铁律 #74）" % occ)
    write_text(path, before.replace(probe["old"], probe["new"], 1))
    try:
        rc, out = run_cmd(probe, timeout, node)
    finally:
        write_text(path, before)
    if sha256_text(read_text(path)) != h0:
        raise RestoreError(probe["file"])

    fails = failed_names(out)
    if probe["expect_red"]:
        unseen = [e for e in probe["expect_red"] if not any(starts_with_id(n, e) for n in fails)]
        if unseen:
            return False, ("注入缺陷后 **%s 没有变红**（RC=%d）⇒ 该断言的判据可能已被换掉/短路"
                           "（#100）" % ("、".join(unseen), rc))
        return (rc != 0 and True), "RC=%d；目标断言均已变红：%s" % (rc, "、".join(probe["expect_red"]))

    ok = (rc == probe.get("expect_rc", 0)) and not fails
    return ok, ("负对照：RC=%d（期望 %d）、FAIL 行 %d 条（须 0）"
                % (rc, probe.get("expect_rc", 0), len(fails)))


def anchor_report():
    """静态自检：注册表里每条 mutate 探针的锚点**在当前事实源里恰好命中一次**。

    ★ 覆盖**整个注册表**（不受 `--only` 影响）：锚点会随事实源演进而漂移，
      而"探针过期"与"断言死了"必须分开报（#74）。
    """
    bad = []
    for p in PROBES:
        if p["kind"] != "mutate":
            continue
        path = os.path.join(ROOT, p["file"])
        if not os.path.isfile(path):
            bad.append("%s：事实源不存在 %s" % (p["id"], p["file"]))
            continue
        n = read_text(path).count(p["old"])
        if n != 1:
            bad.append("%s：锚点命中 %d 次（须 1）" % (p["id"], n))
    return bad


def registry_report():
    ids = [p["id"] for p in PROBES]
    bad = []
    if tuple(ids) != DECLARED_PROBE_IDS:
        bad.append("ID 序列 %s ≠ 声明 %s（注册表与断言行须同步）" % (tuple(ids), DECLARED_PROBE_IDS))
    if len(set(ids)) != len(ids):
        bad.append("ID 重复")
    for p in PROBES:
        if p["kind"] == "mutate":
            if not p.get("old") or not p.get("new"):
                bad.append("%s：mutate 缺 old/new" % p["id"])
        elif p["kind"] == "expect":
            if "expect_rc" not in p:
                bad.append("%s：expect 缺 expect_rc" % p["id"])
        else:
            bad.append("%s：未知 kind %s" % (p["id"], p["kind"]))
    return bad


# ---------------------------------------------------------------------------
def emit(rep, res):
    """把结果写成**字面量断言名**（见 DECLARED_PROBE_IDS 的说明）。"""
    # ★ 下面每一行的名字都是**字面量**，不得改成 f-string：
    #   采集器只取"首个字符串常量"，前缀非字面量就采不到 ⇒ 本仪器失去 #87 保护（#98）。
    rep.add(res["R1"][0], "R1 core.js 端点预算改错（search 3000→9999）⇒ F37k 必须变红", res["R1"][1])
    rep.add(res["R2"][0], "R2 core.js 去掉 snapshot_id 序列化 ⇒ F37h 必须变红", res["R2"][1])
    rep.add(res["R3"][0], "R3 名字核对工具判据被换掉（removed 恒空）⇒ S2 必须变红", res["R3"][1])
    rep.add(res["R4"][0], "R4 缺依赖诊断丢掉解释器 ⇒ G3 必须变红", res["R4"][1])
    rep.add(res["R5"][0], "R5 页面不可达 ⇒ offline 必须 RC=2 且自述前置（D56）", res["R5"][1])
    rep.add(res["R6"][0], "R6 viz 未跑 ⇒ taskflow 必须 RC=2 且自述前置（D56）", res["R6"][1])
    rep.add(res["R7"][0], "R7 core.js 分桶的 1900 边界改错 ⇒ F42a 必须变红", res["R7"][1])
    rep.add(res["R8"][0], "R8 详情面板对缺键也渲染 ⇒ F42f 必须变红", res["R8"][1])
    rep.add(res["R9"][0], "R9 图例年代区块去掉时间序 ⇒ F42j 必须变红", res["R9"][1])
    rep.add(res["R10"][0], "R10 门禁缺依赖 SKIP（skip_detail）丢掉解释器 ⇒ G4 必须变红（D58）",
            res["R10"][1])
    rep.add(res["R11"][0], "R11 产物差异判据被换成恒真空（regenerated ⇒ []）⇒ M9 必须变红（D64）",
            res["R11"][1])
    rep.add(res["R12"][0], "R12 路径栏回到自组参数（trust 写死）⇒ V13 必须变红（B1）",
            res["R12"][1])
    rep.add(res["R13"][0], "R13 谱系自检去掉 None 守卫 ⇒ ⑨ 必须变红且不得抛 traceback（B2）",
            res["R13"][1])
    rep.add(res["R14"][0], "R14 把 not_in_repo 也算 stale ⇒ V18h 必须变红（D72 构造性判据）",
            res["R14"][1])
    rep.add(res["R15"][0], "R15 边宽/透明度写死常量 ⇒ F43b/F43c 必须变红（等级编码退场即无区分度）",
            res["R15"][1])
    rep.add(res["R16"][0], "R16 grade=null 兜底成 E5 ⇒ F43b 必须变红（未判定不得冒充最高档）",
            res["R16"][1])
    rep.add(res["R17"][0], "R17 未入库改判 5 档 ⇒ G2 必须变红（满分必须可复算；合成图自检）",
            res["R17"][1])
    rep.add(res["R18"][0], "R18 source_asserted 降档 ⇒ G3 必须变红（低分构造性反例；合成图自检）",
            res["R18"][1])
    rep.add(res["R19"][0], "R19 去掉再分发折扣 ⇒ G4 必须变红（对外不得高于内部；合成图自检）",
            res["R19"][1])
    rep.add(res["R20"][0], "R20 符号改用策展写法 ⇒ S2 必须变红（推导字形集须等于源声明）",
            res["R20"][1])
    rep.add(res["R21"][0], "R21 字形可查性判据被架空 ⇒ Y3 必须变红（合成图自检）",
            res["R21"][1])
    rep.add(res["RN"][0], "RN 负对照：纯注释改动不得让任何断言变红（证明「变红」非恒真）", res["RN"][1])
    rep.add(res["RS"][0], "RS 锚点自检：注册表每条 mutate 探针的锚点在当前事实源里恰好命中一次",
            res["RS"][1])
    rep.add(res["RR"][0], "RR 还原自检：被碰过的文件逐字节等于初始内容（#97 不得弄脏工作树）",
            res["RR"][1])
    rep.add(res["R0"][0], "R0 注册表自检：ID 序列/唯一性/字段齐备与声明一致", res["R0"][1])


def main(argv=None):
    ap = argparse.ArgumentParser(description="构造性可红性探针（D55 / #87 / #100）")
    ap.add_argument("--only", default="", help="只跑指定探针（逗号分隔 ID，如 R1,R4）")
    ap.add_argument("--list", action="store_true", help="只列出注册表，不执行")
    ap.add_argument("--timeout", type=int, default=600, help="单条探针超时（秒）")
    ap.add_argument("--json", dest="json_out", default=None, help="把结构化结果写到该路径")
    a = ap.parse_args(argv)

    if a.list:
        for p in PROBES:
            print("%-3s %-7s %-34s %s" % (p["id"], p["kind"], p.get("file") or p["cmd"].split(" ", 1)[1],
                                          p["why"]))
        return 0

    node = find_node()
    # `authority` = 权威图快照在不在（它被 .gitignore 排除 ⇒ 干净检出里没有）。
    # ★ 需要它的探针（R20）在缺失时记 SKIP，**不得**记 FAIL —— 与 R5/R6 的「外部前置缺失」同口径。
    avail = {"node": bool(node), "playwright": importlib.util.find_spec("playwright") is not None,
             "authority": os.path.isfile(os.path.join(ROOT, "06_PoC", "etl", "normalized.json"))}
    only = [s.strip() for s in a.only.split(",") if s.strip()]
    selected = [p for p in PROBES if not only or p["id"] in only]
    selected_ids = {p["id"] for p in selected}

    print("=" * 78)
    print("  构造性可红性探针（D55 / 铁律 #87 · #100）—— 注入已知缺陷，断言必须真会红")
    print("=" * 78)
    print("  解释器 : python %s @ %s" % (sys.version.split()[0], sys.executable))
    print("  node   : %s" % (node or "（未找到）"))
    print("  注册表 : %d 条（%d 条真探针 + 1 条负对照）｜本轮选中 %d 条"
          % (len(PROBES), len(PROBES) - 1, len(selected)))
    print("=" * 78)

    # 默认：全部"未选中"（--only 排除）⇒ 记 SKIP，**不得记 FAIL**（否则 --only 会假红）
    res = {pid: (None, "本轮未选中（--only 排除）") for pid in DECLARED_PROBE_IDS}
    touched = {}
    restore_error = None
    for p in selected:
        if p["kind"] == "mutate":
            fp = os.path.join(ROOT, p["file"])
            if os.path.isfile(fp):
                touched[p["file"]] = sha256_text(read_text(fp))
        try:
            res[p["id"]] = run_one(p, avail, a.timeout, node)
        except RestoreError as e:
            res[p["id"]] = (None, "还原失败，已中止")
            restore_error = str(e)
            break
        except subprocess.TimeoutExpired:
            res[p["id"]] = (False, "超时（%ds）—— 命令未在预算内结束" % a.timeout)

    # ---- RR 还原自检（**逐字节**）----
    dirty = [f for f, h in touched.items()
             if (not os.path.isfile(os.path.join(ROOT, f)))
             or sha256_text(read_text(os.path.join(ROOT, f))) != h]
    if restore_error:
        dirty.append("还原异常：%s" % restore_error)
    res["RR"] = (not dirty, "被碰过 %d 个文件；%s"
                 % (len(touched), "全部逐字节一致" if not dirty else "★ 不一致：" + "、".join(dirty)))

    # ---- RS 锚点自检（覆盖整个注册表，与 --only 无关）----
    bad_anchor = anchor_report()
    res["RS"] = (not bad_anchor, "注册表 %d 条；%s"
                 % (len(PROBES), "锚点全部恰好命中一次" if not bad_anchor else "★ " + "；".join(bad_anchor)))

    # ---- R0 注册表自检 ----
    bad_reg = registry_report()
    res["R0"] = (not bad_reg, "%s"
                 % ("ID 与声明一致、字段齐备" if not bad_reg else "★ " + "；".join(bad_reg)))

    rep = Rep()
    emit(rep, res)

    # ★ SKIP 行已由 `Rep.add(None, …)` **逐条**给出（三态），此处只决定退出码：
    #   只有**选中了却缺前置**才把退出码抬到 2（铁律 #20：SKIP 不是 PASS）；
    #   `--only` 排除的探针是正常用法，不抬高退出码。
    skip_missing = [(pid, res[pid][1]) for pid in selected_ids if res[pid][0] is None]
    print()
    rep.dump()

    if a.json_out:
        payload = {
            "schema": "sttp-red-probe/v1",
            "generated_by": "06_PoC/tests/red_probe.py",
            "interpreter": sys.executable,
            "node": node,
            "selected": sorted(selected_ids),
            "summary": {"pass": rep.n_pass, "fail": rep.n_fail, "skip": rep.n_skip},
            "rows": [{"kind": k, "name": n, "detail": d} for k, n, d in rep.rows],
        }
        os.makedirs(os.path.dirname(os.path.abspath(a.json_out)), exist_ok=True)
        with io.open(a.json_out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print("  （留痕已写入 %s）" % a.json_out)

    if restore_error:
        print("\n" + "!" * 78)
        print("  ★★ 还原失败：被突变的事实源**可能已被污染** —— 请立刻 `git status` 人工处置。")
        print("     涉及文件：%s" % restore_error)
        print("!" * 78)
        return 3
    if rep.n_fail:
        print("\n  结论：有断言**注入了已知缺陷却不变红** ⇒ 它的判据可能已被换掉（#100）。")
        return 1
    if skip_missing:
        print("\n  结论：选中探针均按预期变红，但有 %d 条**因缺前置未运行** ⇒ 不构成「通过」。"
              % len(skip_missing))
        return 2
    print("\n  结论：全部探针均按预期变红 —— 关键断言**有能力红**（不只是「当前没红」）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
