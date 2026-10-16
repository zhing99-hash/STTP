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
        "why": "G3（缺依赖诊断必须自述解释器 —— D57 的常驻守卫）",
        "file": "06_PoC/release/verify_release.py",
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
DECLARED_PROBE_IDS = ("R1", "R2", "R3", "R4", "R5", "R6", "RN")

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
    avail = {"node": bool(node), "playwright": importlib.util.find_spec("playwright") is not None}
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
