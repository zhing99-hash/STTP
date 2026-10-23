# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""仓库卫生自检（Repo Hygiene Self-Check）—— Phase 74
======================================================
目的（F15 / F16 治理的**固化**，不做一次性清理就收工）：
    Phase 74 手工删掉了 2.9 GB 冗余产物、把一个"顶着一次性前缀的常设门禁步骤"
    搬回 `tests/`。这两件事都**只做了一次**——而仓库的熵是单调递增的：
    `apply_delta` 每跑一轮就多留一份 78 MB 的 `normalized.before_*.json`，
    `merge_seed_delta` 每跑一轮就多一份 62 MB 的 `graph_data_phase*.json`。
    **没有判据咬住的治理，等于把垃圾扫到地毯下面，六个月后再扫一次。**

三条判据（每条都回答"不挂上去会怎样"）：
    H1 命名不撒谎 —— `06_PoC/_*.py` 是"一次性复盘脚本"的声明，**不得被活引用**
        （`sttp.sh` / 生产代码 / 活文档）。不挂：门禁步骤顶着一次性前缀，
        没人敢删也没人知道它在门禁里（Phase 74 实测：`_gate_selfcheck_phase31.py`
        正是门禁的常设步骤，却被 12 处引用当成临时脚本）。
    H2 大产物不堆积 —— 被 git 忽略的 > 50 MB 文件必须在**显式白名单**里，
        且条数受限。不挂：仓库长到 6.8 GB 无人报警（Phase 74 实测）。
    H3 引用不腐烂 —— 活文件里写的 `06_PoC/**.py` 路径必须真实存在。
        不挂：搬走一个脚本后，引用它的文档全部静默失效。

★ 关于"历史报告"：**不扫 `07_交付物/**`**。那里写的路径是**当时的事实**
（报告是历史记录，改它就是篡改历史）。故 H1/H3 只管**活引用**；
一次性脚本搬走后，历史报告里的旧路径由脚本自身的 docstring 说明去向。

用法：
    python 06_PoC/tests/repo_hygiene_selfcheck.py            # 判据（RC≠0 即红）
    python 06_PoC/tests/repo_hygiene_selfcheck.py --selfcheck # 自检 S0–S5（含 3 个负对照）
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))            # 06_PoC/tests
POC = os.path.dirname(HERE)                                  # 06_PoC
ROOT = os.path.dirname(POC)                                  # STTP
sys.path.insert(0, POC)

import interp_note as interp                                 # noqa: E402

BIG = 50 * 1024 * 1024                                       # 50 MB

# ---------------------------------------------------------------------------
# 白名单：**显式列出**允许存在的被忽略大产物，并给出条数上限与理由。
#   目录白名单不限条数（它们各自的生命周期另有规则）；
#   模式白名单按条数上限 —— 这两个数字就是 Phase 74 清理后**实测**留下的数量，
#   再长一个就红（而不是等它长到 44 个）。
# ---------------------------------------------------------------------------
DIR_WHITELIST = (
    ("公共数据集/", "上游原始语料，删了取不回来（2.9 GB）"),
    ("06_PoC/etl/", "权威图 normalized.json（82 MB）+ 各轮 delta（回滚依据）"),
    ("06_PoC/release/", "对外发布包"),
    ("06_PoC/dataset/", "对外数据集"),
)

PAT_WHITELIST = (
    # (glob 正则, 上限, 理由)
    (r"^06_PoC/graph_data_phase\d+\.json$", 2,
     "前端投影快照：1 份 `.env` 指向的当前服役版 + 1 份最新生成版；"
     "Phase 74 前有 44 份共 1.2 GB"),
    (r"^06_PoC/etl/normalized\.before_\w+\.json$", 3,
     "`apply_delta` 的 apply 前备份，只留最近 3 次；Phase 74 前有 44 份共 1.7 GB"),
    (r"^11_真实数据/\w+\.parquet$", 5,
     "上游抓取的原始语料快照（删了要重抓，不是本地可再生产物）；现有 1 份 103 MB"),
)

# ---------------------------------------------------------------------------
# H3 的占位符豁免：**示例 / 故意不存在的探针名**，不是引用腐烂。
#   每条必须写明理由（否则豁免表自己会腐烂 —— 与 interp_selfreport_check 的
#   EXEMPT 表同一条纪律）。
# ---------------------------------------------------------------------------
PLACEHOLDER = (
    (r"^06_PoC/tests/xxx\.py$",
     "`assert_name_diff.py` / `sttp.sh` 用法示例里的**占位文件名**，`xxx` 即明示其为示例。"),
    (r"^11_真实数据/__phase56_not_in_repo_probe__\.py$",
     "`lineage_invalidation.py` **故意**构造的「不在仓库里」的路径，"
     "用来测「不在仓库里」这条分支；它必须不存在，判成腐烂就错了。"),
)

# 说明性提及豁免（file, path, reason）：**不是调用**，只是写明"它原来叫什么"。
#   ★ 每条必须写明理由；由自检 S5 核对「被豁免的文件仍然存在」——
#     文件删了而豁免还挂着 ⇒ 豁免腐烂（与 interp_selfreport_check 的 Q4 同族）。
MENTION_ONLY = (
    ("PROJECT_DEVELOPMENT_GUIDE.md", "06_PoC/_gate_selfcheck_phase31.py",
     "Phase 74 迁移说明：必须写明**旧名**，否则读者按旧路径找不到文件；"
     "该行是说明而非命令（文件已迁至 `06_PoC/tests/gate_selfcheck_phase31.py`）。"),
    ("PROJECT_DEVELOPMENT_GUIDE.md", "11_真实数据/wikidata_adapter.py",
     "Phase 74 核对注：该行**正是在声明这个脚本从未落地**（并附 `git log --all` "
     "为空的判据）。写成不带目录的裸名反而让读者无从核对 —— 这是「指名道姓地"
     "声明它不存在」，与「引用它」是相反的语义。"),
)

# 活引用载体：这些目录/文件里的路径引用是**当下要能跑**的。
LIVE_DIRS = ("06_PoC/", "11_真实数据/", "03_知识层/", "sttp_cli/")
LIVE_FILES_EXTRA = ("sttp.sh", "PROJECT_DEVELOPMENT_GUIDE.md")
LIVE_SUFFIX = (".py", ".sh", ".js", ".md", ".json", ".yaml", ".yml")
# 历史记录：**只读**，不参与活引用判定（报告写的是当时的事实，改它就是篡改历史）。
#   ★ `00_项目管理/` 也在内：那是**决策与计划的历史记录**（如 20261008 的"下一步
#   开发计划"写的是当时打算用哪个脚本），不是当下要能跑的命令清单。
HISTORY_DIRS = ("07_交付物/", "00_项目管理/")

_RE_PATH = re.compile(
    r"(?:06_PoC|11_真实数据|03_知识层|sttp_cli)/[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-]+)*\.py")
_RE_ONESHOT = re.compile(r"^06_PoC/_[A-Za-z0-9_]+\.py$")


def _git(*args):
    """跑 git 并取行列表。★ `-c core.quotepath=false` **不能省** —— 默认会
    给非 ASCII 路径加引号，122/212 个 .py 会被静默跳过（D110b，Phase 73 实测）。"""
    p = subprocess.run(["git", "-c", "core.quotepath=false"] + list(args),
                       cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        return None
    out = p.stdout.decode("utf-8", "replace")
    return [l for l in out.split("\n") if l.strip()]


def live_files():
    """活引用载体集合（被 git 跟踪的、可能写下路径引用的文件）。"""
    tracked = _git("ls-files")
    if tracked is None:
        return None
    out = []
    for f in tracked:
        if any(f.startswith(d) for d in HISTORY_DIRS):
            continue
        if not (f.endswith(LIVE_SUFFIX) or f in LIVE_FILES_EXTRA):
            continue
        if not any(f.startswith(d) for d in LIVE_DIRS) and f not in LIVE_FILES_EXTRA:
            continue
        out.append(f)
    return out


def ignored_files():
    """被 .gitignore 忽略的产物（大文件堆积的温床）。"""
    return _git("ls-files", "--others", "--ignored", "--exclude-standard")


def _refs_in_py(path):
    """`.py` 里的路径引用 —— **只取代码里的**，排除 docstring 与注释。

    ★ 这是判据能否成立的关键：第一版按"文本里出现过"扫，9 条命中里有 7 条
      是文档字符串/注释里的**说明性提及**（"侦察见 `_recon_domain.py`"），
      不是调用。把它们判成违规，作者唯一能做的就是把说明文字删掉 ——
      **判据逼人破坏文档，说明判据本身错了**（而不是放宽它）。
      故改用 AST 取字符串常量并显式排除 docstring（与
      `interp_selfreport_check.py` 第 25 行同一条教训）。
    """
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
    except (OSError, SyntaxError, ValueError):
        return set()
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr):
                v = node.body[0].value
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    docs.add(id(v))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docs:
            out.update(_RE_PATH.findall(node.value))
    return out


def _refs_in_text(path):
    """`.sh` / `.md` / `.json` 里的路径引用 —— 排除以 `#` 开头的注释行。"""
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().split("\n")
    except OSError:
        return set()
    out = set()
    for ln in lines:
        if ln.lstrip().startswith("#"):
            continue
        out.update(_RE_PATH.findall(ln))
    return out


def code_files(files):
    """H1 的载体：**常设流程** = `sttp.sh` 的命令 + 所有 `.py` 生产代码。

    ★ 不含 `.md`：指南里写 `python 06_PoC/_recon_domain.py` 是在告诉你
      "当年这个数字是用这条命令量出来的"——**一次性脚本被一次性地复现，正是它
      该有的用法**。H1 要禁的是「门禁/生产流程**长期**依赖一个自称临时的脚本」，
      那才是命名撒谎（Phase 74 实测：`sttp.sh` 的 `scopeaudit`/`mnxrecov`/
      `reach35` 三个常设命令 + `phase63_delta` 的 importlib 加载，共 4 处）。
    """
    return [f for f in files if f.endswith(".py") or f == "sttp.sh"]


def scan_refs(files):
    """扫出活文件里写下的仓库内 .py 路径引用 → {路径: [出处]}。"""
    refs = {}
    for f in files:
        p = os.path.join(ROOT, f)
        got = _refs_in_py(p) if f.endswith(".py") else _refs_in_text(p)
        for m in got:
            refs.setdefault(m, []).append(f)
    # 说明性提及豁免（显式声明 + 理由，由自检 S5 咬住"文件仍存在"）
    for f, path, _why in MENTION_ONLY:
        if path in refs and f in refs[path]:
            refs[path] = [x for x in refs[path] if x != f]
            if not refs[path]:
                del refs[path]
    return refs


# --------------------------------------------------------------------------- 判据
def check_h1(files):
    """H1：一次性脚本（`06_PoC/_*.py`）不得被活引用。"""
    bad = []
    for path, srcs in sorted(scan_refs(files).items()):
        if _RE_ONESHOT.match(path):
            bad.append((path, srcs))
    return bad


def check_h2(ignored, caps=None):
    """H2：被忽略的大产物必须在白名单内且条数受限。caps 可覆盖（自检用）。"""
    if caps is None:
        caps = {pat: lim for pat, lim, _ in PAT_WHITELIST}
    bad = []
    counts = {}
    if not ignored:
        return bad, counts
    for f in ignored:
        matched = False
        for pat, _lim, _why in PAT_WHITELIST:
            if re.match(pat, f):
                counts[pat] = counts.get(pat, 0) + 1
                matched = True
                break
        if matched:
            continue
        if any(f.startswith(d) for d, _ in DIR_WHITELIST):
            continue
        p = os.path.join(ROOT, f)
        try:
            sz = os.path.getsize(p)
        except OSError:
            continue
        if sz > BIG:
            bad.append(("%s（%.0f MB，不在白名单）" % (f, sz / 1048576.0), []))
    for pat, lim, _why in PAT_WHITELIST:
        lim2 = caps.get(pat, lim)
        if counts.get(pat, 0) > lim2:
            bad.append(("%s 共 %d 个 > 上限 %d" % (pat, counts.get(pat, 0), lim2), []))
    return bad, counts


def check_h3(files):
    """H3：活文件里写下的路径必须真实存在（引用不腐烂）。

    ★ 豁免 `PLACEHOLDER`：示例里的占位文件名、**故意**不存在的探针名。
      这两类"不存在"是**设计如此**，判成腐烂等于逼作者去造一个假文件。
    """
    bad = []
    for path, srcs in sorted(scan_refs(files).items()):
        if any(re.match(p, path) for p, _why in PLACEHOLDER):
            continue
        if not os.path.exists(os.path.join(ROOT, path)):
            bad.append((path, srcs))
    return bad


# --------------------------------------------------------------------------- 自检
def selfcheck():
    ok = True

    def _c(name, cond, note=""):
        nonlocal ok
        print("  %s %s%s" % ("✅" if cond else "❌", name, (" —— " + note) if note else ""))
        if not cond:
            ok = False

    files = live_files()
    if not files:
        print("  ❌ S0 取不到活引用集合（git 不可用？）")
        print("      " + interp.skip_detail("无法执行 git ls-files", extra="仓库卫生自检未真正执行"))
        return 2
    ignored = ignored_files() or []

    print("自检 S0–S5（仓库卫生自检的自检；含 3 个负对照）")
    _c("S0 取集合非真空（活文件 %d、被忽略 %d）" % (len(files), len(ignored)),
       len(files) >= 20 and len(ignored) >= 1,
       "取不到集合 ⇒ 三条判据全部退化为真空（D110b）")

    # S1 负对照：**注入**一个对一次性脚本的活引用 ⇒ H1 必须抓到
    inj = list(files) + ["__injected__.py"]
    class _Fake(dict):
        pass
    real_scan = scan_refs
    try:
        globals()["scan_refs"] = lambda fs: dict(real_scan(fs),
                                                 **{"06_PoC/_recon_domain.py": ["__injected__"]})
        bad = check_h1(code_files(inj))
        _c("S1 负对照：注入一次性脚本引用 ⇒ H1 必须红",
           any("06_PoC/_recon_domain.py" in b[0] for b in bad))
    finally:
        globals()["scan_refs"] = real_scan

    # S2 负对照：把 graph_data 上限压到 1（现有 2 份）⇒ H2 必须红
    _, counts = check_h2(ignored)
    _c("S2 白名单计数非真空（%s）" % counts, len(counts) >= 1,
       "计数为 0 ⇒ 上限规则从未被真正执行")
    bad2, _ = check_h2(ignored, caps={PAT_WHITELIST[0][0]: 1})
    _c("S2b 负对照：上限压到 1 ⇒ H2 必须红", len(bad2) >= 1)

    # S3 负对照：注入一个不存在的路径 ⇒ H3 必须红
    try:
        globals()["scan_refs"] = lambda fs: dict(real_scan(fs),
                                                 **{"06_PoC/该脚本不存在.py": ["__injected__"]})
        bad3 = check_h3(inj)
        _c("S3 负对照：注入失效路径 ⇒ H3 必须红",
           any("该脚本不存在" in b[0] for b in bad3))
    finally:
        globals()["scan_refs"] = real_scan

    # S5：豁免表自身防腐烂 —— 被豁免的**文件**必须还在（与 interpcheck 的 Q4 同族）
    stale = [(f, p) for f, p, _w in MENTION_ONLY
             if not os.path.exists(os.path.join(ROOT, f))]
    _c("S5 豁免表未腐烂（%d 条，所指文件均存在）" % len(MENTION_ONLY),
       len(MENTION_ONLY) >= 1 and not stale,
       ("腐烂项：%s" % stale) if stale else "文件删了而豁免还挂着 ⇒ 豁免变成永久通行证")

    # S4：白名单非真空（否则 H2 退化成"一律报红"或"一律放行"）
    _c("S4 白名单非真空且每条有理由",
       len(DIR_WHITELIST) >= 2 and len(PAT_WHITELIST) >= 2 and
       all(lim >= 0 and why for _p, lim, why in PAT_WHITELIST) and
       all(why for _d, why in DIR_WHITELIST))

    print("\n%s" % ("汇总：✅ 自检 S0–S5 全过" if ok else "汇总：❌ 自检未通过"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="仓库卫生自检（Phase 74 · F15/F16 治理的固化）")
    ap.add_argument("--selfcheck", action="store_true", help="自检 S0–S5（含 3 个负对照）")
    a = ap.parse_args()
    if a.selfcheck:
        return selfcheck()

    print("仓库卫生自检 —— H1 命名不撒谎 / H2 大产物不堆积 / H3 引用不腐烂")
    print("=" * 74)
    files = live_files()
    if not files:
        print("[前置缺失] 无法取活引用集合（git ls-files 失败）")
        print("      " + interp.skip_detail("无法执行 git ls-files",
                                            extra="仓库卫生自检未真正执行"))
        return 2
    ignored = ignored_files() or []
    print("活引用载体 %d 个；被忽略产物 %d 个" % (len(files), len(ignored)))

    fails = []
    print("\n-- H1 一次性脚本（`06_PoC/_*.py`）不得被**常设流程**引用 --")
    bad = check_h1(code_files(files))
    if bad:
        for p, srcs in bad:
            print("  ❌ %s 被活引用：%s" % (p, ", ".join(sorted(set(srcs))[:4])))
            fails.append("H1:%s" % p)
    else:
        print("  ✅ 无活引用指向一次性脚本")

    print("\n-- H2 被忽略的大产物在白名单内且条数受限 --")
    bad2, counts = check_h2(ignored)
    for pat, lim, why in PAT_WHITELIST:
        print("  · %s 现有 %d / 上限 %d —— %s" % (pat, counts.get(pat, 0), lim, why))
    for d, why in DIR_WHITELIST:
        print("  · %s 目录白名单（不限条数）—— %s" % (d, why))
    if bad2:
        for p, _s in bad2:
            print("  ❌ %s" % p)
            fails.append("H2:%s" % p)
    else:
        print("  ✅ 无越界大产物")

    print("\n-- H3 活文件里的路径引用必须存在 --")
    bad3 = check_h3(files)
    if bad3:
        for p, srcs in bad3:
            print("  ❌ 引用了不存在的 %s（出自 %s）" % (p, ", ".join(sorted(set(srcs))[:3])))
            fails.append("H3:%s" % p)
    else:
        print("  ✅ %d 条路径引用全部存在" % len(scan_refs(files)))

    print("\n" + "-" * 74)
    if fails:
        print("汇总：❌ 仓库卫生未通过 %d 项：%s" % (len(fails), fails[:6]))
        return 1
    print("汇总：✅ H1/H2/H3 全过 —— 清理不是一次性的，是被判据咬住的")
    return 0


if __name__ == "__main__":
    sys.exit(main())
