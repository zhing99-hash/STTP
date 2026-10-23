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
import shutil
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
                "**一条没人愿意跑的构造性证据等于没有**。"
                "★ D71 起 ⑨ 的断言名改为 `LIN15 …`（原来以 `⑨` 开头）—— 整套自检改成"
                "`rep.add` 登记后，名字必须与 `names` 采集器口径对齐。`expect_red` 随之改"
                "为 `LIN15`：**改名必须同步锚点**，否则探针会静默退化成「永远匹配不上」"
                "（看起来仍在守，实际什么也没测 —— #90 的镜像）。"),
        "file": "11_真实数据/lineage_invalidation.py",
        "old": ('    body = sr.repo_blob(rel, ROOT)\n'
                '    if body is None:\n'
                '        return False, ("dependency_not_in_repo: %s 不在 HEAD 中 ⇒ 无法基于**仓库字节**"\n'
                '                       "构造变异（先提交该参考表，再跑本自检）" % rel)'),
        "new": "    body = sr.repo_blob(rel, ROOT)      # redprobe: 去掉 None 守卫（回到旧写法）",
        "cmd": "PY 11_真实数据/lineage_invalidation.py --selfcheck-guard",
        "cwd": ROOT,
        "expect_red": ["LIN15"],
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
        "id": "R22",
        "kind": "mutate",
        "why": ("学科分类 S3：**人工已有细分的 89 个节点绝不覆盖**。"
                "把「人工值优先」这条短路掉 ⇒ 那 89 个（math.DG / chem.acidbase …）"
                "会被规则重算覆盖成派生值 ⇒ S3 必须变红。"
                "★ 这一改读起来像「统一口径、更一致」，只有实测逐条比对能抓住它。"),
        "file": "11_真实数据/phase62_domain_delta.py",
        "old": "    if (not managed) and cur and cur != \"phys\":\n        return cur, [cur], None, \"curated\", None",
        "new": "    if False and (not managed) and cur and cur != \"phys\":   # redprobe: 人工值不再优先（S3）\n        return cur, [cur], None, \"curated\", None",
        "cmd": "PY 11_真实数据/phase62_domain_delta.py --dry",
        "cwd": ROOT,
        "expect_red": ["S3"],
        "expect_rc": 1,
        "requires": ["authority"],
    },
    {
        "id": "R23",
        "kind": "mutate",
        "why": ("学科分类 Y3：**`domain_tags` 换算的领域组必须等于 dim_exponents 重算的**。"
                "把这条判据改成恒假（`if False`）⇒ 合成图里那条「标签说 em、参与量只有 mass」的"
                "违规节点不再被告发 ⇒ `--selfcheck` 的『Y3:bad 能被踩红』必须变红。"
                "★ 用 `--selfcheck`：不依赖权威图，干净检出里也能跑（与 R17–R19/R21 同纪律）。"),
        "file": "06_PoC/domain_report.py",
        "old": "            if g_tag != g_dim:\n                problems[\"Y3\"].append(n[\"id\"])",
        "new": "            if False and g_tag != g_dim:   # redprobe: 参与量一致性判据被架空（Y3）\n                problems[\"Y3\"].append(n[\"id\"])",
        "cmd": "PY 06_PoC/domain_report.py --selfcheck",
        "cwd": ROOT,
        "expect_red": ["Y3"],
        "expect_rc": 1,
        "requires": [],
    },
    {
        "id": "R24",
        "kind": "mutate",
        "why": ("适用条件 S3：**未经人工裁决的候选一律拒写**。"
                "往词典里加一个「三角形」（它确实出现在 MG:fo:law_cosines / law_sines 的"
                "描述里，但核对表没裁决过）⇒ 出现 unverified ⇒ S3 必须变红。"
                "★ 这一改读起来像「扩充词典、提高召回」，很正当；"
                "只有 S3 能拦住它——否则新增文本会悄悄混进未经核对的条件，准确率变成假数。"),
        "file": "11_真实数据/phase63_applicability_delta.py",
        "old": "         \"smooth\", \"prime\", \"coprime\"]",
        "new": "         \"smooth\", \"prime\", \"coprime\", \"三角形\"]   # redprobe: 未裁决候选（S3）",
        "cmd": "PY 11_真实数据/phase63_applicability_delta.py --dry",
        "cwd": ROOT,
        "expect_red": ["S3"],
        "expect_rc": 1,
        "requires": ["authority"],
    },
    {
        "id": "R25",
        "kind": "mutate",
        "why": ("适用条件 S4：**无指称的公式不得有任何适用条件**。"
                "让 `derive()` 给 no_referent 分支凭空返回一个条件「量纲齐次」⇒ S4 必须变红。"
                "★ 这正是 R4 判否的那条歧路：拿恒真性质冒充前提。")
        ,
        "file": "11_真实数据/phase63_applicability_delta.py",
        "old": "        return \"no_referent\", [], [], [], []",
        "new": ("        return \"no_referent\", [\"量纲齐次\"], [], [], []"
                "   # redprobe: 给无指称的编一条（S4）"),
        "cmd": "PY 11_真实数据/phase63_applicability_delta.py --dry",
        "cwd": ROOT,
        "expect_red": ["S4"],
        "expect_rc": 1,
        "requires": ["authority"],
    },
    {
        "id": "R26",
        "kind": "mutate",
        "why": ("D71 的**回归**守卫：把某一条断言从 `rep.add(...)` 改回**裸 `print`** "
                "⇒ 铁律 #87 的采集器就看不见它（静态采集里 `LIN4` 消失）⇒ ⑩ 自指必须红。"
                "★ 为什么这条必须存在：D71 的原始缺陷正是「自检走 print ⇒ `names` 采到 0 条"
                "⇒ 删掉任何一条断言都无人发现」。只把 print 换成 rep.add 是**手段**；"
                "没有这条探针，将来谁再改回 print 也不会有人知道 —— 那就是 D71 复发。"
                "★ 为什么挑 `LIN4`（③ 未变异图无变更）：它在本文件里**只有一处** `rep.add`"
                "（`LIN15` 有两处：正常分支 + `except` 分支，只替换一处时静态采集仍采得到、"
                "判据不会红 —— 第一版就踩了这个坑）。要模拟「这条断言从可采集集合里彻底消失」，"
                "必须挑单点登记的那一条。"
                "★ 为什么期望红的是 ⑩ 而不是 ③：把 `rep.add` 换成 `print` 后 ③ 自己"
                "**根本不再产生条目**（不是 FAIL 而是消失）—— 这正是 D71 的形状，"
                "只有 ⑩ 的绝对量清单（缺 LIN4）能把它抓住。"
                "★ 为什么用 `--selfcheck-guard` 而不是 `--selfcheck`：后者 3 分 18 秒，"
                "构造性证据必须便宜到有人愿意跑（R13 立下的纪律）。"
                "⑩ 是纯静态判据 ⇒ 不跑 ③ 也能验到它。"),
        "file": "11_真实数据/lineage_invalidation.py",
        "old": ('    rep.add(clean, "LIN4 ③ 未变异图无变更（无假阳性基线）",\n'
                '            "受影响 %d ｜ 直接变更 %s" % (len(a0), d0["changed_direct"]))'),
        "new": ('    print("  [PASS] LIN4 ③ 未变异图无变更 —— redprobe: 改回裸 print（D71 复发）")\n'
                '    # redprobe: 此处原有的 rep.add 被移除，采集器将看不到 LIN4'),
        "cmd": "PY 11_真实数据/lineage_invalidation.py --selfcheck-guard",
        "cwd": ROOT,
        "expect_red": ["LIN16"],
        "expect_rc": 1,
        "requires": [],
    },
    {
        "id": "R27",
        "kind": "mutate",
        "why": ("RC1 · A0c：`release.phase` 曾**硬编码 37** 并连续漂移 28 个开发阶段无人发现 —— "
                "因为 `verify_release` 从来不读它。**没有判据的字段不是低优先级，"
                "是必然会说谎的字段。** 本探针把 manifest 的开发阶段改小 1 ⇒ A0c 必须红。"
                "★ 为什么改 manifest 而不是改 VERSION：VERSION 是**事实源**，"
                "改它等于换掉了被测对象的前提（测出来的是「文件不同」，不是「判据有效」）；"
                "改**清单**才是模拟「清单与事实源漂移」这个真实故障形状。"
                "★ 锚点会随版本号漂移而失效：`anchor_report()` 在开跑前静态检查全表锚点，"
                "命中 ≠ 1 次会**大声报错**（#74），不会静默；届时随 VERSION 一起改这一行。"),
        "file": "06_PoC/release/manifest.json",
        "old": '  "phase": 65,',
        # ★ 注入值必须是**合法 JSON**：第一版写成 `"phase": 64,   # 注释`，
        #   清单当场解析失败 ⇒ verify 走 RC=3（manifest 不可读）而不是 A0c 变红，
        #   探针报「注入缺陷后没变红」—— **测到的是解析器，不是判据**。
        "new": '  "phase": 64,',
        "cmd": "PY 06_PoC/release/verify_release.py",
        "cwd": ROOT,
        "expect_red": ["A0c"],
        "expect_rc": 1,
        "requires": [],
    },
    {
        "id": "R28",
        "kind": "mutate",
        "why": ("RC1 · A0d：第三条独立来源是**产物文件名**（`graph_data_phase41.json`）—— "
                "它由构建产生、不随改字而变。把清单里的 `snapshot_phase` 改错 ⇒ A0d 必须红。"
                "★ 为什么这一条与 R27 分开：两条断言咬的是**不同的一对来源**"
                "（清单↔VERSION / 清单↔产物名）。只测前者，"
                "「清单与数据实际由哪个阶段导出」这层仍然无人校验（通律③）。"),
        "file": "06_PoC/release/manifest.json",
        "old": '  "snapshot_phase": 41,',
        "new": '  "snapshot_phase": 40,',
        "cmd": "PY 06_PoC/release/verify_release.py",
        "cwd": ROOT,
        "expect_red": ["A0d"],
        "expect_rc": 1,
        "requires": [],
    },
    {
        "id": "R29",
        "kind": "expect",
        "why": ("Phase 68 · S5b/D96：**输入不完整必须拒绝产出**。喂一份 65/80 条未真正检索的"
                "候选池（2026-10-21 真实劣化产物，做成夹具 ⇒ 不联网可复现）⇒ delta 必须 RC=2。"
                "★ 这条守的是最容易被忽略的一种造假：没查到的记成「确认没有」。"
                "整张图上 5,090 个节点只要有一批被这么处理，"
                "对外口径就会变成「99% 公式在文献里没有提及」—— 而真相是那天额度用完了。"
                "★ 用 expect 而非 mutate：若日后有人删掉这道闸门，本探针会立刻失败"
                "（RC 变成 0），而不是等下一次真的撞上配额。"),
        "cmd": ("PY 11_真实数据/phase68_paper_mention_delta.py --dry "
                "--pool 06_PoC/tests/fixtures/phase68_pool_degraded.json"),
        "cwd": ROOT,
        "expect_rc": 2,
        "expect_text": ["候选池不完整", "not_extracted"],
        "requires": [],
    },
    {
        "id": "R30",
        "kind": "mutate",
        "why": ("Phase 68 · S5b：把「抓取失败」的白名单分支改成计入 `no_referent` —— "
                "这是最省事、也让覆盖率最好看的一种退化（失败瞬间全变成\"已确认无\"），"
                "绿字却能继续报出来。注入后 S5b 必须变红。"
                "★ 为什么值得单独立一条探针：它跟 D72「恒真判据什么都守不住」是同一族，"
                "但这里退化得更隐蔽 —— 计数对得上、三态都在、**每个数都变了，没人看得见**。"),
        "file": "11_真实数据/phase68_paper_mention_delta.py",
        # ★ 锚点必须是**单行**：本文件是 CRLF 行尾，而 `read_text()` 故意不做行尾
        #   翻译（`newline=""`，为了还原逐字节一致）⇒ 多行锚点里的 `\n` 永远匹配不上，
        #   表现为"锚点命中 0 次"（那时报红的是 RS 而不是 R30，容易误诊）。
        "old": '            newp = {"papers_status": "not_extracted", "papers": []}',
        "new": '            newp = {"papers_status": "no_referent"}   # redprobe: 抓取失败写成已确认无（S5b）',
        "cmd": ("PY 11_真实数据/phase68_paper_mention_delta.py --dry --allow-partial "
                "--pool 06_PoC/tests/fixtures/phase68_pool_degraded.json"),
        "cwd": ROOT,
        "expect_red": ["S5b"],
        "expect_rc": 1,
        "requires": [],
    },
    {
        "id": "R31",
        "kind": "mutate",
        "why": ("Phase 68 · S5b 的另一半：只**去掉未检索计数**、状态留着不动。"
                "这样 daily 汇总里三态数量照样齐整（65 行落在 not_extracted），"
                "但「这 65 条是因为抓取失败才未知」这件事就**不再可被读出** —— "
                "下一次有人问「为什么这么多未抽取」，答案是空的。"
                "★ 同一条断言、两种死法，所以配两条探针（R30 死语义／R31 死计数）。"),
        "file": "11_真实数据/phase68_paper_mention_delta.py",
        "old": '            st["unsearched"] += 1',
        "new": '            pass   # redprobe: 未检索行不再计数（S5b）',
        "cmd": ("PY 11_真实数据/phase68_paper_mention_delta.py --dry --allow-partial "
                "--pool 06_PoC/tests/fixtures/phase68_pool_degraded.json"),
        "cwd": ROOT,
        "expect_red": ["S5b"],
        "expect_rc": 1,
        "requires": [],
    },
    {
        "id": "R32",
        "kind": "mutate",
        "why": ("Phase 68 · D100：**报告里的数字**必须有判据 —— 判据此前覆盖了代码、覆盖了数据，"
                "唯独没覆盖我们自己写下的结论。"
                "起因（2026-10-21 实测）：RC1 报告写「symbols 5,090/5,090 = 100%」，"
                "图里现算 98.39%，phase61 脚本自报也是 98.39% ⇒ **错的是报告**，"
                "而那句话没有任何判据咬住（脚本的判据是「非空才计数、门槛 ≥90%」，从没说过 100%）。"
                "把报告数字改回 100% ⇒ docnums 的 S2 必须变红。"
                "★ 与 D96 同族：一句没有判据的陈述必然会说谎。这一次说谎的不是代码，是报告；"
                "而且它特别容易发生在覆盖率上 —— 「100%」听起来像终点，"
                "写的人会觉得「差不多就是全覆盖了」，而 82 条空列表（E=mc²/F=ma）在 90% 门槛下是绿的。"),
        "file": "07_交付物/RC1_整体状态评估_20261020.md",
        # ★ 单行锚点（本仓库多个文件是 CRLF，`read_text()` 不做行尾翻译 ⇒ 多行锚点永不命中）。
        # ★ 锚点必须跟着**报告的真实数字**走：Phase 69 把 98.39% 提成 99.82% 时，
        #   这里不改就会「锚点未命中」⇒ R32 以「没变红」的形式假红（RS 会先报锚点）。
        "old": "**5,081 / 5,090 = 99.82%**",
        "new": "**5,090 / 5,090 = 100%**   # redprobe: 报告数字写满（D100）",
        "cmd": "PY 06_PoC/report_number_check.py --selfcheck",
        "cwd": ROOT,
        "expect_red": ["S2"],
        "expect_rc": 1,
        "requires": [],
    },
    {
        "id": "R33",
        "kind": "mutate",
        "why": ("Phase 68 · D101：三态的硬线是「**有没有真正检索过**」，不是「有没有候选」。"
                "旧分类把 curated 里『检索过、但裁决为无』的 35 条一律记成 not_extracted，"
                "于是 80 条明明全查过，报告却要写『35 条未检索』——"
                "把「已确认无」降级成「未知」，是 D96 所防方向的**镜像**，同样是歪的。"
                "（35 条里 34 条是 0 候选，1 条是候选非指该式：Periodic law 命中的是生态学的 periodic。）"
                "把 no_referent 改回 not_extracted ⇒ S5/S6 必须变红。"
                "★ 起因很具体：旧版 S5 写的是「curated 一律不许记 no_referent」，"
                "为了不红，代码只能把它们塞进 not_extracted —— **是判据先把分类逼歪的**。"),
        "file": "11_真实数据/phase68_paper_mention_delta.py",
        # ★ 单行锚点（本文件是 CRLF，多行锚点永不命中）
        # ★ 锚点**必须带上行尾逗号**：注释放逗号之前会把逗号也注释掉 ⇒ SyntaxError，
        #   脚本崩溃返回 RC=1 却没有任何断言名变红 —— 那不是「判据抓到了缺陷」，
        #   是「探针自己把被测程序弄崩了」，expect_red 依然落空（R33 第一版就是这么红的）。
        "old": "\"papers_status\": \"no_referent\", \"papers\": [],",
        "new": ("\"papers_status\": \"not_extracted\", \"papers\": [],"
                "  # redprobe: 查过确认无记成未知（D101）"),
        "cmd": ("PY 11_真实数据/phase68_paper_mention_delta.py --dry --allow-partial "
                "--pool 06_PoC/tests/fixtures/phase68_pool_clean_min.json"),
        "cwd": ROOT,
        "expect_red": ["S5c"],
        "expect_rc": 1,
        "requires": [],
    },
    {
        "id": "R34",
        "kind": "mutate",
        "why": ("Phase 69 · A1：`symbols` 派生自**本公式自己的** symbol_table，一个都不许多。"
                "侦察已证明 82 条空 symbols 里 78 条是「推出来了没写回」（symbol_table 有值、"
                "symbols_rule_version=s2）⇒ 补的时候最容易顺手多补几个把覆盖率做上去。"
                "在派生结果里凭空加一个符号 ⇒ S1（排序规则重现历史 5,000 条）必须变红。"
                "★ 为什么用 S1 而不是 C1：图已落盘后重跑是幂等的（本轮不再产出 symbols），"
                "C1 无从触发；而 S1 对历史 5,000 条重算，**不依赖图当前状态**，离线可复跑。"),
        "file": "11_真实数据/phase69_chem_delta.py",
        "old": "    ordered = sorted(syms, key=key)",
        "new": ("    ordered = sorted(syms, key=key) + [\"ZZZ\"]"
                "  # redprobe: 凭空多补一个符号（D103）"),
        "cmd": "PY 11_真实数据/phase69_chem_delta.py --selfcheck",
        "cwd": ROOT,
        "expect_red": ["S1"],
        "expect_rc": 1,
        "requires": [],
    },
    {
        "id": "R35",
        "kind": "mutate",
        "why": ("Phase 69 · B：反应名**只**从该反应自己的 reactant/product 边拼，拼不出就标"
                "`unnamed_no_evidence`，不编造。这是 F4 里唯一能诚实拿到的部分（59 条）；"
                "给拼不出的 699 条随便起个名字，就能把覆盖率从 59% 抬到 100% ——"
                "正是侦察判定「≥95% 不可达」时要防的那件事。"
                "给无依据的反应编一个名字 ⇒ S5/S7 必须变红。"),
        "file": "11_真实数据/phase69_chem_delta.py",
        "old": "\"name\": None, \"status\": ST_UNNAMED,",
        "new": ("\"name\": \"unknown → unknown\", \"status\": ST_UNNAMED,"
                "  # redprobe: 给无依据的反应编名（D103）"),
        "cmd": "PY 11_真实数据/phase69_chem_delta.py --selfcheck",
        "cwd": ROOT,
        "expect_red": ["S5", "S7"],
        "expect_rc": 1,
        "requires": [],
    },
    {
        "id": "R36",
        "kind": "mutate",
        "why": ("Phase 70 · F5：随包校验器的 V2（逐文件 sha256）是「下载后仍是同一份」的唯一判据。"
                "把 `real_sha/real_n` 直接取自清单（等于拿清单跟自己比）就能让它恒绿 —— "
                "此时**改一个字节也不会被发现**，而所有下游（wget 测试、发布资产）都会认为校验通过。"
                "⇒ 架空 V2 后，校验器自检的 VS2 必须变红。"),
        "file": "06_PoC/dataset/verify_dataset.py",
        "old": "        real_sha, real_n = sha256_file(fp), os.path.getsize(fp)",
        "new": ("        real_sha, real_n = f.get(\"sha256\"), f.get(\"bytes\")"
                "  # redprobe: V2 判据架空（拿清单比清单）"),
        "cmd": "PY 06_PoC/dataset/verify_dataset.py --selftest",
        "cwd": ROOT,
        "expect_red": ["VS2"],
        "requires": [],
    },
    {
        "id": "R37",
        "kind": "mutate",
        "why": ("Phase 70 · F5：数据集**对外**包必须在构建侧按来源三态剔除不可再分发内容。"
                "把 profile 写死成 internal_research（或任何「不过滤」）会让 43,396 条政策上不能外发的"
                "边混进包里 —— 而 verify_dataset 的 V5 只核对「记录与来源表一致」，两边同一个错就都绿。"
                "所以这条只能由**构建侧独立重算**的 S2（audit_redistribution，不听分区函数自述）抓住。"),
        "file": "06_PoC/dataset/export_dataset.py",
        "old": "    pnodes, pedges, lic = ms.license_partition(nodes, edges, profile)",
        "new": ("    pnodes, pedges, lic = ms.license_partition(nodes, edges, \"internal_research\")"
                "  # redprobe: 许可过滤被写死"),
        "cmd": ("PY 06_PoC/dataset/export_dataset.py --out .runlog/redprobe-ds "
                "--skip-determinism"),
        "cwd": ROOT,
        "expect_red": ["S2"],
        "expect_rc": 1,
        "requires": ["authority"],
    },
    {
        "id": "R38",
        "kind": "mutate",
        "why": ("Phase 71：Element 节点里 27 个物性字段来自 ElementKG 1.0（条款未明示），"
                "必须剥离才谈得上「Element 层可再分发」。文件头写着「不删数据、只把不可外发的挪走」，"
                "靠的就是 `rebuild_element` 只从 ER_TRUTH_FIELDS 重建 —— 一旦顺手写成"
                "`new.update(其余字段)`（看起来很像「保留更多信息」），物性就悄悄回到了节点上，"
                "`audit_redistribution` 会把 118 个元素判成 internal_only，包又回到 composed_of=0。"
                "这个过程**没有任何报错**：图能建、包能出、测试全绿，只是少了一半边。"
                "★ 探针走 `--selfcheck` 而不是 `--dry`：重建一旦成功，`build` 会对 118 个元素全部"
                "   走 `elem_already` 分支、产不出 delta —— 那时 `--dry` 的 E5 看的是**已合规的图**，"
                "   必然恒绿。判据必须落在**纯函数**上（S5 用合成 props 测 rebuild_element），"
                "   才不依赖生产数据此刻长什么样（#115）。"),
        "file": "11_真实数据/phase71_element_delta.py",
        # ★ 目标文件是 LF（本文件是 CRLF）—— old/new 里的换行必须写 \n，
        #   写成 \r\n 会永远命中不了（RS 锚点自检会报，但那时已经绕了一圈）
        "old": ("    dropped = {k: v for k, v in (p or {}).items() if k not in KEEP_FIELDS}\n"
                "    return new, dropped"),
        "new": ("    new.update({k: v for k, v in (p or {}).items() if k not in KEEP_FIELDS})"
                "  # redprobe: 物性回填\n"
                "    dropped = {}\n"
                "    return new, dropped"),
        "cmd": "PY 11_真实数据/phase71_element_delta.py --selfcheck",
        "cwd": ROOT,
        "expect_red": ["S5"],
        "expect_rc": 1,
        "requires": ["authority"],
    },
    {
        "id": "R39",
        "kind": "mutate",
        "why": ("Phase 71：`same_period` 改成 `source=element_reference` 的唯一依据是"
                "「用权威表重算，结果与图上现有集合逐对一致」。若把判据键从 `period_of_z`"
                "换成 `group_of_z`（同族而非同周期），生成集就会与现有集大幅错位 ——"
                "此时若还照样改来源，等于拿一个**没核对过**的推导去冒充自建资产。"
                "⇒ E2（重算集 == 现有集）必须变红，脚本必须拒绝执行。"),
        "file": "11_真实数据/phase71_element_delta.py",
        "old": "        return er.period_of_z((props or {}).get(\"atomic_number\"))",
        "new": "        return er.group_of_z((props or {}).get(\"atomic_number\"))  # redprobe: 键错位",
        "cmd": "PY 11_真实数据/phase71_element_delta.py --dry",
        "cwd": ROOT,
        "expect_red": ["E2"],
        "expect_rc": 1,
        "requires": ["authority"],
    },
    {
        "id": "R40",
        "kind": "mutate",
        "why": ("Phase 71：`DATA_LICENSES.md` 里那句「离线样例实为 200 节点 / 360 边」是**手写**的，"
                "而它随 `bash sttp.sh release` 的抽样结果变化 —— 上一版就漂过：文档写 170/255，"
                "`release` 实际打出 200/360，两边差了 30 个节点、105 条边，"
                "而**没有任何判据咬住这个数字**（docnums 当时只登记了对外包规模与分子覆盖率）。"
                "把它登记进 `DECLARATIONS` 之后，本探针把文档数字改回旧值 ⇒ S2 必须变红。"
                "★ 漂移的成因值得记：Phase 71 让 Element 节点与 `composed_of` 进入可分发集合，"
                "   抽样池变大 ⇒ 同样抽 200 个节点，边数从 255 涨到 360。"
                "   也就是说这类数字**会被上游的正确改动带偏**，不登记就必然滞后一次。"),
        "file": "DATA_LICENSES.md",
        # 单行锚点（本文件是 LF；`read_text()` 不做行尾翻译，多行锚点易踩行尾坑）
        "old": "实为 **200 节点 / 360 边**",
        "new": "实为 **170 节点 / 255 边**   <!-- redprobe: 样例规模漂移（数值已过期）-->",
        "cmd": "PY 06_PoC/report_number_check.py --selfcheck",
        "cwd": ROOT,
        "expect_red": ["S2"],
        "expect_rc": 1,
        "requires": [],
    },
    {
        "id": "R41",
        "kind": "mutate",
        "why": ("Phase 72 · D108：`gatecheck [3/3]` 红了四轮的根因是**判级模型里没有 m1 这一档** ——"
                "101 条 `mentioned_in` 落库 `rule_checked`，重算却落到 B8「无来源无判据」。"
                "处置是**把规则登记进 `verification_model`**（不是把落库改小去迁就重算）。"
                "把 A11 的 kind 判据改掉 ⇒ m1 重新「没登记」⇒ **E8 必须变红**。"
                "★ 为什么不是指望 E3：E3 判「落库 == 重算」的最终态，而 delta 的作用正是"
                "  「把落库改成重算」⇒ 重算被改坏时落库会跟着一起坏，两边仍**一致** ⇒ E3 恒绿。"
                "  能抓住「m1 被撤」的只有直接查重算 scope 的 E8（自洽 ≠ 登记）。"
                "★ 与 R38 的坑（#115）不同：E3 判的是「合并态 vs 重算函数」，注入改的是**重算侧**，"
                "   落库侧不受影响 ⇒ 判据不依赖「本轮有没有跑过」，可以安全用 `--dry`。"),
        "file": "11_真实数据/verification_model.py",
        # ★ 目标文件是 LF
        "old": "    if t == \"mentioned_in\" and kind == \"openalex_name_mention\":",
        "new": ("    if t == \"mentioned_in\" and kind == \"openalex_name_mention_x\":"
                "  # redprobe: m1 重新「没登记」"),
        "cmd": "PY 11_真实数据/phase72_mention_delta.py --dry",
        "cwd": ROOT,
        "expect_red": ["E8"],
        "expect_rc": 1,
        "requires": ["authority"],
    },
    {
        "id": "R42",
        "kind": "mutate",
        "why": ("Phase 72：`name_mention_in_text` 是 m1 重算的**唯一实现**。"
                "写成恒真（看起来像「更宽容地接受历史数据」）会让 101 条边**无论文本里有没有这个公式名**"
                "都判 `rule_checked` —— 于是 (q) 的「独立证据」变成一张可以随便填的合格证，"
                "而 (t) 的「落库必须等于重算」形同虚设（两者一起恒真）。"
                "★ 这条**没有报错也没有失败计数**：图能建、包能出，只是「证据」这个词失去了意义。"
                "⇒ S2（snippet 里没有该名字 ⇒ 必须判否）必须变红。"),
        "file": "11_真实数据/verification_model.py",
        "old": "    if find_span is None:\n        return None, \"m1 实现不可用",
        "new": ("    return True, \"redprobe: 恒真\"\n"
                "    if find_span is None:\n        return None, \"m1 实现不可用"),
        "cmd": "PY 11_真实数据/phase72_mention_delta.py --selfcheck",
        "cwd": ROOT,
        "expect_red": ["S2"],
        "expect_rc": 1,
        "requires": [],
    },
    {
        "id": "R43",
        "kind": "mutate",
        "why": ("Phase 73 · D109：`rva` 是证据与「代码等价」证明之间的**唯一**连接。"
                "如果 `_attestation_ok` 不再核对 `to_code_hash`（看起来像「少比一个字段，宽容点」），"
                "证明就退化成一张**永久通行证**：验证器以后再改一百次，3.2 万条证据仍然 valid。"
                "★ 这条没有任何报错、没有失败计数 —— 图能建、包能出，只是「重验」这个词失去了意义。"
                "⇒ 判据 E6（伪造 to ⇒ 必须回落 stale）必须变红。"),
        "file": "11_真实数据/evidence_deps.py",
        # ★ 目标文件是 LF
        "old": "    if att.get(\"to_code_hash\") is None or att.get(\"to_code_hash\") != cur_ch:\n        return False",
        "new": "    if False:  # redprobe: 证明的 to 不再核对\n        return False",
        "cmd": "PY 11_真实数据/phase73_reverify_delta.py --dry",
        "cwd": ROOT,
        "expect_red": ["E6"],
        "expect_rc": 2,
        "requires": ["authority"],
    },
    {
        "id": "R44",
        "kind": "mutate",
        "why": ("Phase 73：等价性证明的全部力量都在 P5 —— **插入的分支必须正向点名被排除的边类型**。"
                "放宽成「只要插入了就算」（`if True`）看起来只是少一条检查，"
                "实际后果是：任何一次改验证器都能被自己证明成「不影响老证据」，"
                "3.2 万条 stale 会被**毫无依据地**清掉。"
                "⇒ 自检 S4（守卫不含排除类型 ⇒ 必须拒）必须变红。"),
        "file": "11_真实数据/reverify_attest.py",
        "old": "        if _mentions(stmt.test, exclude_types) and not _has_negative(stmt.test):",
        "new": "        if True:  # redprobe: 守卫不再点名排除类型",
        "cmd": "PY 11_真实数据/reverify_attest.py --selfcheck",
        "cwd": ROOT,
        "expect_red": ["S4"],
        "expect_rc": 1,
        "requires": [],
    },
    {
        "id": "R45",
        "kind": "mutate",
        "why": ("Phase 75 · F12/D69：「一键放宽」的全部价值在于**它会停**。"
                "孤立节点（全图 degree=0）必须**没有**放宽动作 —— 因为放宽必然无效。"
                "如果这里也给一个「含全部可信档重试」（看起来更友好、更「总比没有强」），"
                "用户点下去会拿到**一模一样的空图**，而界面还显得「已经帮你想过办法了」。"
                "★ 这条没有任何报错、没有失败计数：按钮在、点击有反应、结果照旧空。"
                "⇒ 判据 V21a（孤立 ⇒ action 必须为 None）必须变红。"),
        "file": "03_知识层/queries.py",
        "old": '                "why": "isolated", "action": None,',
        # ★ 注入体首行必须是**带 `redprobe:` 的注释**：`residue_report()` 用
        #   「new 的第一行是否已在文件里」判残留 —— 若首行是 `"why": "isolated",`
        #   这种正常代码，它**本来就该在文件里**，于是每次都误报"上次没还原干净"。
        "new": ('                # redprobe: 孤立也给放宽按钮（点了照样空）\n'
                '                "why": "isolated",\n'
                '                "action": {"key": "trust", "to": TRUST_ALL, "gain": 0,\n'
                '                           "label": "含全部可信档重试"},'),
        "cmd": "PY 06_PoC/tests/phase43_subgraph_selfcheck.py",
        "cwd": ROOT,
        "expect_red": ["V21a"],
        "expect_rc": 1,
        "requires": ["authority"],
    },
    {
        "id": "R46",
        "kind": "mutate",
        "why": ("Phase 76 · F13：导航存在的理由是**不许漏人** —— 界面上的「各学科之和」"
                "必须等于全图节点数，否则那几个谁都不收的节点从来没人会发现。"
                "这里把「没有 domain 的节点」从兜底桶里静默丢掉（看起来很合理："
                "空字段不该单独占一格），于是 domain 层 `sum_items` 比 `scanned` 少 1，"
                "而界面上只会显示「物理 2 格 / 化学 1 格」，看不出少了人。"
                "★ 这条也没有任何报错：每一格计数都「看起来对」，只有对账式能看见它。"
                "⇒ 判据 V22c（无领域节点必须进显式兜底桶、conserved 必须为 True）必须变红。"),
        "file": "03_知识层/queries.py",
        # ★ 注入体首行带 `redprobe:` 注释：`residue_report()` 靠它区分「注入残留」与
        #   「本来就该在文件里的正常代码」（R45 踩过这个坑）。
        "new": ('                # redprobe: 无领域节点被静默丢掉（conserved 必须变红）\n'
                '                keys = [dom] if dom else []'),
        "old": '                keys = [dom or "(无领域)"]',
        "cmd": "PY 06_PoC/tests/phase43_subgraph_selfcheck.py",
        "cwd": ROOT,
        "expect_red": ["V22c"],
        "expect_rc": 1,
        "requires": ["authority"],
    },
    {
        "id": "R47",
        "kind": "mutate",
        "why": ("Phase 76 · F13：导航里「分类名不存在」与「这一类里没有东西」是**两件事**，"
                "而它们的界面表现几乎一样（列表空）。这里把 `parent_exists === false` 的"
                "红字分支短路掉（看起来很合理：空就空着，别吓用户），于是「物理x」这种打错的"
                "名字会显示成「物理x 里没有领域」—— 用户会去改筛选条件，而真相是名字打错了。"
                "★ 这条也没有任何报错：列表本来就是空的。"
                "⇒ 判据 F46l（分类不存在必须明说「不是没有结果，是这个分类名不存在」）必须变红。"),
        "file": "06_PoC/js/nav.js",
        "new": ("  // redprobe: 分类不存在也当普通空（把「名字打错」显示成「这一类里没东西」）\n"
                "  if (false) {"),
        "old": "  if (st.parent_exists === false) {",
        "cmd": "NODE tests/frontend_core_check.mjs",
        "cwd": POC,
        "expect_red": ["F46l"],
        "expect_rc": 1,
        "requires": ["authority"],
    },
    {
        "id": "R48",
        "kind": "mutate",
        "why": ("Phase 76 · F13：导航栈里每一项都记着「这个 key 属于哪一层」，回退时靠它复位。"
                "这里把层名记成**下一层**（写的时候看起来更'顺手'：反正要下钻了），"
                "于是「物理 → 电磁学 → 回退一格」会回到 `level=tags&parent=物理` —— "
                "请求合法、返回空、页面显示「物理里没有领域标签」。"
                "★ 它不报错，只在**回退**这一条路径上错；而回退是最少被手点的路径。"
                "⇒ 判据 F46z（push 必须记**当前**层）必须变红。"),
        "file": "06_PoC/js/nav.js",
        "new": ("  // redprobe: 把「这一格属于哪一层」记成下一层（回退时层级错位）\n"
                "  return s.concat([{ level: nextLevel(lv) || lv, key: String(key) }]);"),
        "old": "  return s.concat([{ level: lv, key: String(key) }]);",
        "cmd": "NODE tests/frontend_core_check.mjs",
        "cwd": POC,
        "expect_red": ["F46z"],
        "expect_rc": 1,
        "requires": ["node"],
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
                      "R17", "R18", "R19", "R20", "R21", "R22", "R23",
                      "R24", "R25", "R26", "R27", "R28",
                      "R29", "R30", "R31", "R32", "R33", "R34", "R35", "R36", "R37", "R38", "R39",
                      "R40", "R41", "R42", "R43", "R44", "R45", "R46", "R47", "R48", "RN")

# 与 emit() 一一对应的键（含仪器自身的三条自检 + 注册表自检）。
ALL_ROW_IDS = DECLARED_PROBE_IDS + ("RS", "RR", "RT", "R0")


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


def residue_report():
    """残留污染自检：**上一次被中断的运行的注入内容**是否已留在事实源里。

    ★ 2026-10-21 真事：`redprobe` 在前台被 SIGTERM 掐断（默认超时 120s），
      某条探针的注入内容留在 `queries.py` 里；下一次运行便以此时的内容为基准，
      ⇒ 锚点命中 0 次，而 RR「还原自检」却显示"全部逐字节一致"
      —— **它证明的是"还原回了运行前的样子"，而运行前就已经是脏的**。
      铁律 #97 只说"不许弄脏"，没说"脏了要能发现"，这条补的就是后半句。

    判据很简单：任一 mutate 探针的**被注入文本**（`new`）若已在目标文件里出现，
    那它只可能来自一次没还原干净的运行（正常文件内容不会带 `redprobe:` 注入体）。
    """
    residue = []
    for p in PROBES:
        if p["kind"] != "mutate":
            continue
        fp = os.path.join(ROOT, p["file"])
        if not os.path.isfile(fp):
            continue
        body = p["new"]
        # 取注入体里最具标识的一行（通常带 `redprobe:` 注释或新增的赋值）
        probe_line = next((l.strip() for l in body.splitlines()
                           if l.strip() and ("redprobe" in l or l.startswith("+"))),
                          body.strip().splitlines()[0].strip())
        if probe_line and probe_line in read_text(fp):
            residue.append("%s 的注入内容仍残留在 %s（= 上次运行被中断且未还原）"
                           % (p["id"], p["file"]))
    return residue


# ---------------------------------------------------------------------------
def _cleanup_tmp():
    """清理探针导出的临时数据集（`.runlog/redprobe-ds*`）—— 用完即删。

    ★ 与 RR（还原自检）**互不干涉**：RR 管的是"被突变的**事实源**有没有还原"，
      这里删的是"探针**产物**"。产物每次运行重新生成，删掉不影响可复现性。
    """
    d = os.path.join(ROOT, ".runlog")
    if not os.path.isdir(d):
        return
    removed = []
    for name in sorted(os.listdir(d)):
        if not name.startswith("redprobe-ds"):
            continue
        p = os.path.join(d, name)
        try:
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
            removed.append(name)
        except OSError:
            pass
    if removed:
        print("  （已清理探针临时产物：%s）" % "、".join(removed))


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
    rep.add(res["R22"][0], "R22 人工已有细分不再优先 ⇒ S3 必须变红（89 个 curated 不得被覆盖）",
            res["R22"][1])
    rep.add(res["R23"][0], "R23 参与量一致性判据被架空 ⇒ Y3 必须变红（合成图自检）",
            res["R23"][1])
    rep.add(res["R24"][0], "R24 词典扩充出未裁决候选 ⇒ S3 必须变红（禁止未经核对的条件落盘）",
            res["R24"][1])
    rep.add(res["R25"][0], "R25 给无指称公式编一条适用条件 ⇒ S4 必须变红（恒真性质不得冒充前提）",
            res["R25"][1])
    rep.add(res["R26"][0], "R26 断言改回裸 print ⇒ LIN16 ⑩ 自指必须变红（D71 的回归守卫）",
            res["R26"][1])
    rep.add(res["R27"][0], "R27 清单开发阶段与 VERSION 漂移 ⇒ A0c 必须变红（RC1 版本元数据守卫）",
            res["R27"][1])
    rep.add(res["R28"][0], "R28 清单快照阶段与产物文件名不符 ⇒ A0d 必须变红（第三条来源）",
            res["R28"][1])
    rep.add(res["R29"][0], "R29 候选池不完整（65/80 未检索）⇒ delta 必须拒绝产出 RC=2（D96）",
            res["R29"][1])
    rep.add(res["R30"][0], "R30 抓取失败改记 no_referent ⇒ S5b 必须变红（不许把未知写成已确认无）",
            res["R30"][1])
    rep.add(res["R31"][0], "R31 同样内容是「未检索」但不再计数 ⇒ S5b 必须变红（失败行可识别）",
            res["R31"][1])
    rep.add(res["R32"][0], "R32 报告数字写满（100% vs 98.39%）⇒ S2 必须变红（D100：报告也要有判据）",
            res["R32"][1])
    rep.add(res["R33"][0], "R33 把「查过确认无」记回「未检索」⇒ S5c 必须变红（D101：三态硬线是检索过没有）",
            res["R33"][1])
    rep.add(res["R34"][0], "R34 派生 symbols 时凭空多补一个 ⇒ S1 必须变红（D103：符号只来自自己的 symbol_table）",
            res["R34"][1])
    rep.add(res["R35"][0], "R35 给无依据的反应编一个名字 ⇒ S5/S7 必须变红（D103：F4 不凑 95%）",
            res["R35"][1])
    rep.add(res["R36"][0], "R36 校验器 V2 拿清单比清单（哈希判据架空）⇒ VS2 必须变红（F5）",
            res["R36"][1])
    rep.add(res["R37"][0], "R37 数据构建把许可 profile 写死成不过滤 ⇒ S2 必须变红（F5：政策由程序执行）",
            res["R37"][1])
    rep.add(res["R38"][0], "R38 元素物性不剥离（rebuild 时顺手 update 其余字段）⇒ S5 必须变红（Phase 71）",
            res["R38"][1])
    rep.add(res["R39"][0], "R39 元素边判据键错位（族号冒充周期）⇒ E2 必须变红（Phase 71）",
            res["R39"][1])
    rep.add(res["R40"][0], "R40 离线样例规模写旧值（170/255 冒充 200/360）⇒ S2 必须变红（D100 补登记）",
            res["R40"][1])
    rep.add(res["R41"][0], "R41 m1 从判级模型里「消失」（重算落回未分类）⇒ E8 必须变红（D108）",
            res["R41"][1])
    rep.add(res["R42"][0], "R42 m1 重算恒真（不看文本里有没有）⇒ S2 必须变红（证据不得是空白合格证）",
            res["R42"][1])
    rep.add(res["R43"][0], "R43 证明的 to 不再核对（等价性退化成永久通行证）⇒ E6 必须变红（D109）",
            res["R43"][1])
    rep.add(res["R44"][0], "R44 守卫不再点名被排除类型（任何改动都能自证无害）⇒ S4 必须变红（D109）",
            res["R44"][1])
    rep.add(res["R45"][0], "R45 孤立节点也给放宽按钮（点了照样空）⇒ V21a 必须变红（F12/D69）",
            res["R45"][1])
    rep.add(res["R46"][0], "R46 无领域节点被静默丢掉（看起来每格都对）⇒ V22c 必须变红（F13）",
            res["R46"][1])
    rep.add(res["R47"][0], "R47 分类名不存在被当成普通空 ⇒ F46l 必须变红（F13）", res["R47"][1])
    rep.add(res["R48"][0], "R48 导航栈把层级记错（回退才暴露）⇒ F46z 必须变红（F13）",
            res["R48"][1])
    rep.add(res["RN"][0], "RN 负对照：纯注释改动不得让任何断言变红（证明「变红」非恒真）", res["RN"][1])
    rep.add(res["RS"][0], "RS 锚点自检：注册表每条 mutate 探针的锚点在当前事实源里恰好命中一次",
            res["RS"][1])
    rep.add(res["RR"][0], "RR 还原自检：被碰过的文件逐字节等于初始内容（#97 不得弄脏工作树）",
            res["RR"][1])
    rep.add(res["RT"][0], "RT 残留自检：上次被中断的运行没有把注入留在工作树里",
            res["RT"][1])
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

    # ---- RT 残留自检：上一次被中断的运行是否把注入留在了工作树里 ----
    bad_residue = residue_report()
    res["RT"] = (not bad_residue, "%s" % (
        "无残留（每个 mutate 目标的注入体都不在文件里）"
        if not bad_residue else "★ " + "；".join(bad_residue)
        + " ⇒ 先 git checkout -- <file> 再跑，否则本次会以脏内容为基准"))

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

    # ★ Phase 74：探针导出过一份 110 MB 的临时数据集（`.runlog/redprobe-ds.tmp/`）
    #   且**跑完不删**。门禁里 `red_probe` 之后紧接着就是 `[4/8] 仓库卫生自检`，
    #   这条残留会让它变红（实测）—— 这不是判据太严，是**临时产物没清理**。
    #   用完即删：下次运行会重新生成，留着只会 stale，且占用的是被 git 忽略的空间
    #   （没人会去看它，但每个人都得为它付磁盘）。
    _cleanup_tmp()

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
