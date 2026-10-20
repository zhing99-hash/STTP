#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""make_manifest.py —— 发布清单生成器（Phase 37 · GPT6 复评 R06）

复评原文（R06 完成定义）：**第三人从文档独立运行，页面和查询属于同一数据版本**，
并要求完整快照「附**源版本、下载许可、校验和、构建命令、依赖版本与预期统计**」。

本脚本把这些**一次性钉死**为 `06_PoC/release/manifest.json`：

    code          仓库版本（git HEAD / 是否脏）+ 许可证
    build         构建命令 + 管线 + Python 版本
    dependencies  从 requirements.lock 读的**目标**版本（pip 分发）+ 本机实测版本 + 漂移清单
                  ＋ `interpreter`（解释器约束，转录自 pyproject.toml —— Phase 45 A05b：
                    解释器约束**不**写在 requirements.lock，那是 pip 分发表）
    artifacts     每个产物的 sha256 / 字节数 / 节点边数 / 是否入库
    expected_stats 预期统计（可复算的口径，供 verify_release 现算比对）
    licenses      代码许可 + 数据许可登记文件

★ 铁律 #44：`expected_stats` 里凡是能从**入库产物**复算的，一律标 `recomputable: sample`；
  只有完整快照（不入库）的图级统计标 `recomputable: full_snapshot`，并注明获取方式。
★ 不写时间戳进样例文件；manifest 自己可以有 `created`。

用法
----
    python 06_PoC/release/make_manifest.py                 # 生成/刷新 manifest.json
    python 06_PoC/release/make_manifest.py --no-stats      # 跳过仪器现算（快速）
    python 06_PoC/release/make_manifest.py --pack          # 额外打包完整快照到 dist/
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
POC = os.path.dirname(HERE)
ROOT = os.path.dirname(POC)
sys.path.insert(0, POC)
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))   # ★ E9：复用 source_registry.rmtree_force

import graph_export as gx                                                       # noqa: E402
import source_registry as sr                                                     # noqa: E402
import pipeline_outputs as PO                                                    # noqa: E402

MANIFEST = os.path.join(HERE, "manifest.json")
SAMPLE_DIR = os.path.join(HERE, "sample")
LOCK = os.path.join(ROOT, "requirements.lock")
PYPROJECT = os.path.join(ROOT, "pyproject.toml")
DIST = os.path.join(ROOT, "dist")
VERSION_FILE = os.path.join(ROOT, "VERSION")

# 版本号形状：`MAJOR.MINOR.PATCH[-PRE]`。MINOR 即**开发阶段号**（Phase N ⇒ 0.N.0）。
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?$")

# 需要登记为「发布产物」的文件 —— ★ 派生自**唯一事实源**（Phase 55 · D64 / #88）。
#   原先这里是一份**手工副本**，与 `make_sample.py` 里三处字面量构成"两处各写一套、
#   没人核对是否相同"的形态。谁生成产物谁声明产物；本文件只消费。
SAMPLE_FILES = PO.SAMPLE_FILENAMES


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                             text=True, encoding="utf-8", errors="replace")
        return (out.stdout or "").strip()
    except Exception:                                                           # noqa: BLE001
        return ""


def read_lock(path: str) -> dict:
    """解析 requirements.lock 的 `name==version` 行（含 python）。"""
    if not os.path.exists(path):
        return {}
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        k, _, v = line.partition("==")
        out[k.strip()] = v.strip()
    return out


def installed_versions(names) -> dict:
    import platform
    got = {"python": platform.python_version()}
    for m in names:
        if m == "python":
            continue
        try:
            mod = importlib.import_module(m)
            got[m] = str(getattr(mod, "__version__", "") or "")
        except Exception:                                                       # noqa: BLE001
            got[m] = None
    return got


def read_requires_python() -> tuple:
    """读 `pyproject.toml` 的 `project.requires-python` —— 解释器约束的**单一来源**。

    ★ Phase 45 · A05b：解释器约束自本轮起**不再写在 `requirements.lock`** —— 那是
      **pip 分发**清单，而 `python==3.11.9` 会让 `pip install -r` 去找一个名为
      `python` 的**分发**（PyPI 上确有一个长期无人维护的同名包）。
      本函数把约束从 pyproject 取回，保证发布清单**仍然如实记录目标解释器**，
      且与 `bash sttp.sh install`（A05-3）**同源**（同一字段，不是第二份副本）。

    返回 `(constraint_or_None, source_note)`。
    """
    if not os.path.exists(PYPROJECT):
        return None, "pyproject.toml 不存在（A05 之前的副本）"
    try:
        import tomllib
        with open(PYPROJECT, "rb") as f:
            pp = tomllib.load(f)
        c = (pp.get("project") or {}).get("requires-python")
        if c:
            return str(c), "pyproject.toml:project.requires-python"
        return None, "pyproject.toml 未声明 requires-python"
    except Exception as e:                                                       # noqa: BLE001
        return None, "pyproject.toml 不可解析：%s" % e


class VersionError(RuntimeError):
    """`VERSION` 缺失或不可解析。**不得降级为 "unknown"**（见 `read_version` 注释）。"""


def read_version() -> dict:
    """从仓库根的 `VERSION`（唯一事实源）解析版本号。

    ★ 为什么必须有一个文件来当唯一事实源：`release.phase` 原本**硬编码 `37`**，
      连续漂移 28 个开发阶段没人发现 —— 因为 `verify_release.py` **从不读它**。
      没有判据的字段不是"低优先级"，是**必然会说谎的字段**（通律：宽一格的判据恒真）。
      现在三处独立来源（VERSION 文件 / manifest / 快照产物文件名）由 `verify_release`
      交叉断言，任意一处漂移必红。
    ★ 缺失或格式不符 ⇒ 抛 `VersionError`（fail-closed），不写 "unknown"：
      清单的用途是让第三人对得上账，一个假版本号比没有更糟。
    """
    if not os.path.exists(VERSION_FILE):
        raise VersionError("缺少 %s（版本号唯一事实源；内容形如 `0.65.0-rc1`）"
                           % os.path.relpath(VERSION_FILE, ROOT))
    raw = open(VERSION_FILE, encoding="utf-8").read().strip()
    m = _VERSION_RE.match(raw)
    if not m:
        raise VersionError("VERSION 内容 `%s` 不符合 `MAJOR.MINOR.PATCH[-PRE]`" % raw)
    return {"version": raw, "major": int(m.group(1)), "minor": int(m.group(2)),
            "patch": int(m.group(3)), "prerelease": m.group(4) or ""}


def resolve_snapshot(explicit: str | None) -> str | None:
    """定位完整快照：显式 > .env 的 GRAPH_DATA_FILE > 最大 phaseN。"""
    if explicit:
        return explicit if os.path.exists(explicit) else None
    env = os.path.join(ROOT, ".env")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            m = re.match(r"\s*GRAPH_DATA_FILE\s*=\s*(\S+)", line)
            if m:
                p = os.path.join(ROOT, m.group(1).replace("/", os.sep))
                if os.path.exists(p):
                    return p
    cands = []
    for fn in os.listdir(POC):
        m = re.match(r"graph_data_phase(\d+)\.json$", fn)
        if m:
            cands.append((int(m.group(1)), os.path.join(POC, fn)))
    return max(cands)[1] if cands else None


def norm_stats(path: str) -> dict:
    """规范图的节点/边/学科统计。"""
    import collections
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    nodes, edges = d.get("nodes") or [], d.get("edges") or []
    subj = collections.Counter()
    for n in nodes:
        subj[gx.subject_of((n.get("props") or {}).get("domain"), str(n.get("id") or ""))] += 1
    return {"nodes": len(nodes), "edges": len(edges), "subjects": dict(sorted(subj.items()))}


def view_stats(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return {"nodes": len(d.get("nodes") or []), "edges": len(d.get("edges") or []),
            "schema": d.get("schema")}


def run_tool_file(base_cmd, out_path, timeout=900):
    """跑一个把结构化结果**写到文件**的仪器（task_trust_audit / connectivity_audit）。"""
    try:
        subprocess.run([*base_cmd, "--json", out_path], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if os.path.exists(out_path):
            with open(out_path, encoding="utf-8") as f:
                return json.load(f)
    except Exception:                                                           # noqa: BLE001
        pass
    return None


def run_tool_stdout(base_cmd, timeout=900):
    """跑一个把 JSON **打印到 stdout** 的仪器（frozen_gate --json）。"""
    try:
        r = subprocess.run(base_cmd, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return json.loads(r.stdout)
    except Exception:                                                           # noqa: BLE001
        return None


def dirty_report(allow_flag: bool):
    """统一判定「构建时工作树是否干净」—— **唯一实现**（Phase 52 · E21 残余）。

    ★ 为什么收敛到一处：`sttp.sh` 的 `release`（生成清单）与 `release-full`（校验发布链）
      都需要这道判定。若两边各写一套「什么算脏」，就会重演铁律 #92 的形态 ——
      **两处各自都对，合起来是缝**。故判定与「排除清单自身」这条口径只保留一份，
      `release-full` 经 `--check-dirty` 复用（sttp.sh 只负责传播退出码，铁律 #62）。

    返回 `(porcelain, allow_dirty, dirty)`。
    脏检查**排除本清单自身**：否则「生成清单 → 工作区脏 → 再提交 → 又脏」形成循环，
    每次生成的 `working_tree_dirty` 都恒为真。清单不参与「被描述的工作区」判定。
    """
    env_allow = str(os.environ.get("STTP_ALLOW_DIRTY", "")).strip().lower()
    allow_dirty = bool(allow_flag) or env_allow in ("1", "true", "yes", "on")
    porcelain = [ln for ln in git("status", "--porcelain").splitlines()
                 if "06_PoC/release/manifest.json" not in ln]
    return porcelain, allow_dirty, bool(porcelain)


def print_dirty_refusal(porcelain):
    print("=" * 70)
    print("[拒绝] 构建时工作树不干净 —— 发布链按**前置断言**处理（E21，fail-closed）")
    print("  `git status --porcelain` 报告 %d 项（已排除清单自身）：" % len(porcelain))
    for ln in porcelain[:10]:
        print("    %s" % ln)
    if len(porcelain) > 10:
        print("    …（其余 %d 项略）" % (len(porcelain) - 10))
    print("  正确次序：① 提交代码/文档 → ② 清空未跟踪项 → ③ bash sttp.sh release")
    print("            ★ ③ 会**重建离线样例**；若它报了「产物尚未提交」，")
    print("              按那里的指示提交 `06_PoC/release/sample/` 后**重跑 ③**")
    print("            → ④ 提交 release/manifest.json（此时清单才是「干净树口径」）")
    print("  若确要在脏树上生成（仅限本地排查），显式加 --allow-dirty（清单会记 dirty_sample）。")
    print("=" * 70)


class _CheckRep:
    """极简报告器 —— 形状**必须**是 `rep.add(cond, NAME, detail)`。

    ★ 为什么不能写成 `rows.append((cond, name, detail))`（第一版就是这么写的，已订正）：
      `06_PoC/tests/assert_name_diff.py` 的采集器认的是**调用形状**
      （`func.attr ∈ {add, skip, check, ok}`）**+ 字面量断言名**。
      换个写法，`M7/M8/M9` 在 `bash sttp.sh names` 里就**完全隐形** ——
      将来被删掉也没人知道。这正是 Phase 52 在 `assert_name_diff --selfcheck` 上
      踩过的坑（**铁律 #98：执法工具自身必须可被执法**）。
    """

    def __init__(self) -> None:
        self.rows: list[tuple[bool, str, str]] = []

    def add(self, cond, name, detail="") -> None:
        self.rows.append((bool(cond), name, str(detail)))


def check_regenerated(allow_dirty: bool) -> int:
    """`--check-regenerated`：**本流水线产物是否已提交**（Phase 55 · D64，fail-closed）。

    它解决的自锁（Phase 54 实测复现）
    ---------------------------------
    `bash sttp.sh release` 的次序是「**重建样例** → 生成清单（洁净度＝前置断言）」，
    而样例是**被跟踪**文件 ⇒ 图谱一变，重建就必然让工作树变脏 ⇒ 清单被拒 RC=2。
    更糟的是旧拒绝文案写的「正确次序 ③ `bash sttp.sh release`」**正是刚失败的那一步** ——
    按字面执行必然死循环，而"先单独重建样例并提交"这一步**从来没人写下来过**。

    本步位于**重建之后、生成清单之前**，职责只有一条：
    把"该先提交样例"变成**机器判定的前置断言 + 可执行的下一步指令**。

    ★ 为什么**不**改用"把样例从脏判定里排除"来图省事（一跑就过的写法）：
      那样做的实质是**放宽 fail-closed** —— 清单将声称"工作树干净"而样例其实未提交，
      而 `working_tree_dirty_at_build` 的真实含义（"这份清单描述的是已提交的树"）会被掏空。
      保留两趟、把次序讲清楚，比悄悄弱化判据诚实（铁律 #20 的同族）。
    ★ `--allow-dirty` / `STTP_ALLOW_DIRTY=1` 同样放行本步（它本就是"我知道树不干净"的口子）。

    断言：`M7` 声明自证 ／ `M9` ★判据非真空（构造性）／ `M8` 产物已提交。
    """
    paths = PO.sample_paths(ROOT)
    rep = _CheckRep()

    under = [p for p in paths if p.startswith(PO.SAMPLE_DIR_REL + "/")]
    missing = [p for p in paths if not os.path.exists(os.path.join(ROOT, p))]
    ok_decl = bool(paths) and len(under) == len(paths) and not missing
    rep.add(ok_decl, "M7 产物声明自证：非空 / 全在发布目录内 / 全部真实存在",
            "%d 项声明；目录外 %d；缺文件 %s"
            % (len(paths), len(paths) - len(under), missing or "无"))

    # ★ M9 判据非真空（构造性）：合成输入 ⇒ "确实不同"必须被报出、"确实相同"必须不被报出。
    #   没有它，「把判据换成恒真空」的退化与「真的没有差异」在输出上完全不可分
    #   —— 这正是 `redprobe` 的 R11 注入的那个退化。
    _same = {"a.json": b"X", "b.json": b"Y"}
    _diff = {"a.json": b"X", "b.json": b"Z"}
    got_diff = PO.regenerated(ROOT, ["a.json", "b.json"], blob_of=_same.get, read_of=_diff.get)
    got_same = PO.regenerated(ROOT, ["a.json", "b.json"], blob_of=_same.get, read_of=_same.get)
    ok_guard = (got_diff == ["b.json"]) and (got_same == [])
    rep.add(ok_guard,
            "M9 ★判据非真空：合成的「不同」必须被报出、「相同」必须不被报出",
            "不同 ⇒ %s（期望 ['b.json']）｜相同 ⇒ %s（期望 []）" % (got_diff, got_same))

    changed = PO.regenerated(ROOT, paths) if ok_decl else []
    rep.add(not changed and ok_decl, "M8 发布链产物已提交：与 HEAD 逐字节一致（D64）",
            "与 HEAD 不同 %d 项：%s" % (len(changed), changed or "无"))

    print("=" * 74)
    print("  发布链产物前置断言（D64）：本流水线重建的产物必须**已提交**后再出清单")
    print("=" * 74)
    for cond, name, detail in rep.rows:
        print("  %s      %s" % ("✅" if cond else "❌", name))
        if detail:
            print("          %s" % detail)
    print("-" * 74)

    if not ok_decl:
        print("  [RC=2] 产物**缺失**：先跑 `bash sttp.sh release`（它会重建），或检查图谱输入。")
        return 2
    if not ok_guard:
        print("  [RC=3] **仪器自证失败**：差异判据不可信（M9 红） —— 先修仪器，别信 M8 的结论。")
        return 3
    if changed:
        if allow_dirty:
            print("  [放行] --allow-dirty/STTP_ALLOW_DIRTY=1 ⇒ 未提交的产物不作为阻塞"
                  "（清单会记 `dirty_sample`，放行是**被声明**的）")
            return 0
        print("  [RC=2] 产物**已重建但与 HEAD 不同** ⇒ 发布清单**不出**（fail-closed）。")
        print("         下一步（照抄即可）：")
        print("           git add %s && git commit -m 'chore(release): 重建离线样例'" % PO.SAMPLE_DIR_REL)
        print("           bash sttp.sh release        # ★ **重跑**：此时它会生成并提交清单")
        print("         为什么必须分两趟：清单必须描述**已提交的树**；样例未提交就出清单，")
        print("         `working_tree_dirty_at_build` 的语义会被掏空（本项目拒绝这种弱化）。")
        return 2
    print("  ✅ 产物与 HEAD 一致 ⇒ 可以生成清单。")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="STTP 发布清单生成器（Phase 37 R06）")
    ap.add_argument("--snapshot", default=None, help="完整快照路径（默认取 .env GRAPH_DATA_FILE）")
    ap.add_argument("--no-stats", action="store_true", help="跳过仪器现算（快照统计记 null）")
    ap.add_argument("--pack", action="store_true", help="把完整快照打包到 dist/ 并记录校验和")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="显式放行「脏树」（默认 fail-closed：脏 ⇒ RC=2 且不写清单，E21）。"
                         "也可用环境变量 STTP_ALLOW_DIRTY=1。")
    ap.add_argument("--check-dirty", action="store_true",
                    help="**只做**洁净度前置断言、不生成清单：干净 RC=0 / 脏 RC=2。"
                         "供 `sttp.sh release-full` 复用同一判定（E21 残余）。")
    ap.add_argument("--check-regenerated", action="store_true",
                    help="**只做**「本流水线产物是否已提交」的前置断言、不生成清单："
                         "一致 RC=0 ／ 已重建但未提交 RC=2 ／ 仪器自证失败 RC=3（Phase 55 · D64）。"
                         "供 `sttp.sh release` 在**重建样例之后、生成清单之前**调用。")
    ap.add_argument("--out", default=MANIFEST)
    a = ap.parse_args(argv)

    # ---- 版本号前置断言（fail-closed，先于一切计算）----
    #   ★ 早失败：版本号读不到就不该先跑几分钟仪器再报错。
    try:
        ver = read_version()
    except VersionError as e:
        print("  ❌ 版本号不可用（fail-closed，不写清单）：%s" % e)
        return 2

    # ---- 洁净度前置断言（唯一实现；E21 残余）----
    porcelain, allow_dirty, dirty = dirty_report(a.allow_dirty)
    if a.check_dirty:
        if dirty and not allow_dirty:
            print_dirty_refusal(porcelain)
            return 2
        if dirty:
            # ★ 放行也要**说清放行了什么**：不得打印「工作树干净」（那是不实陈述，
            #   与「脏即拒绝」同属"如实"的一部分）
            print("[洁净度] 工作树**不干净**（%d 项，已排除 release/manifest.json 自身）"
                  "—— `--allow-dirty`/`STTP_ALLOW_DIRTY=1` 显式放行" % len(porcelain))
        else:
            print("[洁净度] 工作树干净（已排除 release/manifest.json 自身）")
        return 0

    # ---- 产物已提交前置断言（唯一实现；Phase 55 · D64）----
    if a.check_regenerated:
        return check_regenerated(allow_dirty)

    py = sys.executable
    created = _dt.datetime.now().astimezone().isoformat(timespec="seconds")

    # ---------------- artifacts ----------------
    artifacts = {"sample": {}, "full": {}}
    for fn in SAMPLE_FILES:
        p = os.path.join(SAMPLE_DIR, fn)
        if not os.path.exists(p):
            print("[ERROR] 缺少样例产物：%s（先跑 make_sample.py）" % p)
            return 2
        st = norm_stats(p) if fn == "sample_normalized.json" else (
            view_stats(p) if fn == "sample_graph.json" else
            {"queries": len(json.load(open(p, encoding="utf-8")).get("queries") or [])})
        artifacts["sample"][fn] = {
            "path": os.path.relpath(p, ROOT).replace("\\", "/"),
            "sha256": sha256_file(p), "bytes": os.path.getsize(p),
            "tracked_in_git": True, **st,
        }

    # ---------------- license profile（Phase 42 · A03）----------------
    #  从**样例产物自身**读出许可 profile 与剔除计数（读产物，不信构建器内存 —— 铁律 #14）。
    _sm = json.load(open(os.path.join(SAMPLE_DIR, "sample_normalized.json"), encoding="utf-8"))
    _met = _sm.get("meta") or {}
    license_block = {
        "profile": _met.get("license_profile"),
        "policy": _met.get("license_policy"),
        "excluded_edges": _met.get("license_excluded_edges"),
        "excluded_nodes": _met.get("license_excluded_nodes"),
        "excluded_by_source": _met.get("license_excluded_by_source"),
        "audit_violations": _met.get("license_audit_violations"),
        "redistributable_sources": _met.get("license_redistributable_sources"),
        "note": ("★ 铁律 #64：许可政策由程序执行 —— 发布样例必须 public_demo（默认）；"
                 "verify_release 会**独立重算**样例内每条边/每个节点的来源血缘闭包并断言 0 违规，"
                 "且断言剔除计数非真空（0 边 ⇒ 白名单形同虚设）。"),
    }

    snap = resolve_snapshot(a.snapshot)
    if snap:
        artifacts["full"]["snapshot"] = {
            "path": os.path.relpath(snap, ROOT).replace("\\", "/"),
            "sha256": sha256_file(snap), "bytes": os.path.getsize(snap),
            "tracked_in_git": False, **view_stats(snap),
            "obtain": "原始大数据不入库：本地 `bash sttp.sh export` 重建，或从 Release 资产下载同名快照",
        }
    norm = os.path.join(POC, "etl", "normalized.json")
    if os.path.exists(norm):
        artifacts["full"]["normalized"] = {
            "path": os.path.relpath(norm, ROOT).replace("\\", "/"),
            "sha256": sha256_file(norm), "bytes": os.path.getsize(norm),
            "tracked_in_git": False, **norm_stats(norm),
        }

    # ---------------- dependencies ----------------
    lock = read_lock(LOCK)
    inst = installed_versions(list(lock))
    drift = {}
    for k, v in lock.items():
        got = inst.get(k)
        if got != v:
            drift[k] = {"lock": v, "installed": got}
    req_py, req_src = read_requires_python()
    deps = {
        "lock_file": os.path.relpath(LOCK, ROOT).replace("\\", "/"),
        "target": lock,
        "installed": inst,
        "drift": drift,
        "drift_note": ("漂移**不阻塞**运行（缺件才阻塞）；但化学/量纲结果建议在锁定版本上复核。"
                       "外部复评环境为 rdkit 2026.3.6 / pint 0.26.1 / networkx 3.7 / numpy 2.5.3。"),
        # ★ Phase 45 · A05b：解释器约束与 pip 分发**分离** ——
        #   `target` 只含 pip 能装的分发（不再含 `python`）；解释器约束从 pyproject 转录。
        "interpreter": {
            "constraint": req_py,
            "constraint_source": req_src,
            "running": inst.get("python"),
            "note": ("约束的权威在 pyproject.toml 的 requires-python（**不在** requirements.lock）；"
                     "求值由 `bash sttp.sh install`（A05-3）负责，本清单只如实转录。"),
        },
    }

    # ---------------- expected stats ----------------
    exp = {
        "sample": {
            "recomputable": "sample（入库产物，verify_release 现算比对）",
            "nodes": artifacts["sample"]["sample_normalized.json"]["nodes"],
            "edges": artifacts["sample"]["sample_normalized.json"]["edges"],
            "subjects": artifacts["sample"]["sample_normalized.json"].get("subjects"),
        },
        "full_snapshot": None,
    }
    if snap and not a.no_stats:
        import tempfile
        tmp = tempfile.mkdtemp(prefix="sttp_manifest_")
        # ★ E9：本脚本原先**从不清理**该临时目录（实测泄漏 33 个 `sttp_manifest_*`）。
        #   用 `sr.rmtree_force` 先清只读位再删（Windows 的 git loose object 是只读的），
        #   删不掉即抛（fail-closed，铁律 #62），不再静默。
        try:
            ta = run_tool_file([py, os.path.join(POC, "task_trust_audit.py"),
                                "--graph", norm if os.path.exists(norm) else snap],
                               os.path.join(tmp, "astro.json"))
            fg = run_tool_stdout([py, os.path.join(POC, "frozen_gate.py"), "--json"])
            ca = run_tool_file([py, os.path.join(POC, "connectivity_audit.py"), "--strict"],
                               os.path.join(tmp, "conn.json"))
            exp["full_snapshot"] = {
                "recomputable": "full_snapshot（不入库；`bash sttp.sh release-full` 现算校验）",
                "nodes": artifacts["full"]["snapshot"]["nodes"],
                "edges": artifacts["full"]["snapshot"]["edges"],
                "task_trust": ta,
                "frozen_gate": ({"tally": fg.get("tally")} if isinstance(fg, dict) else None),
                "connectivity": (ca if isinstance(ca, dict) else None),
            }
        finally:
            sr.rmtree_force(tmp)          # ★ 失败即抛，由调用方决定是否致命

    # ---------------- pack ----------------
    packed = None
    if a.pack and snap:
        import tarfile
        os.makedirs(DIST, exist_ok=True)
        phase = re.search(r"phase(\d+)", os.path.basename(snap))
        name = "sttp_snapshot_phase%s.tar.gz" % (phase.group(1) if phase else "unknown")
        tarp = os.path.join(DIST, name)
        with tarfile.open(tarp, "w:gz") as tf:
            tf.add(snap, arcname=os.path.basename(snap))
        packed = {"path": os.path.relpath(tarp, ROOT).replace("\\", "/"),
                  "bytes": os.path.getsize(tarp), "sha256": sha256_file(tarp),
                  "note": "dist/ 不入库；上传为 Release 资产后即成为下载通道"}
        with open(tarp + ".sha256", "w", encoding="utf-8", newline="\n") as f:
            f.write("%s  %s\n" % (packed["sha256"], name))

    # ---------------- assemble ----------------
    # 工作树洁净度：**前置断言**（Phase 52 · E21）
    # ★ 原实现把 `git status --porcelain` 的结果**如实记进** `dirty_sample`，
    #   等于承认「构建时工作树可以是脏的」。但发布链的正确次序是
    #   「先提交代码/文档 → 清空未跟踪项 → 再 release」（Phase 51 实测复现两次）；
    #   若此时仍脏，那是**异常**，应当**拒绝出清单**，而不是把未跟踪项记成一句说明。
    #   更深一层：`git status` 本身不是可信事实源（铁律 #72）⇒ 更不该把它当成"事实"入库。
    # ⇒ 默认 fail-closed：脏 ⇒ RC=2 且**不写清单**；`--allow-dirty`（或 STTP_ALLOW_DIRTY=1）
    #   显式放行时才记录，并把这次放行**声明**在清单里（不得静默）。
    # ★ 判定本身已上移到 `dirty_report()`（唯一实现），此处只消费结果。
    if dirty and not allow_dirty:
        print_dirty_refusal(porcelain)
        return 2

    # 快照的**生成阶段**（数据侧）与**开发阶段**（代码侧）是两个不同的数，不得混用：
    #   * `phase`         = VERSION 的 MINOR（当前开发到第几个 Phase）
    #   * `snapshot_phase`= 快照文件名里的 phaseN（这份数据由哪个阶段的管线导出）
    # 原实现把 `phase` 硬编码 37 且不校验 ⇒ 连续漂移 28 个阶段无人发现（见 `read_version`）。
    snap_phase = None
    if snap:
        _m = re.search(r"phase(\d+)\.json$", os.path.basename(snap))
        snap_phase = int(_m.group(1)) if _m else None

    manifest = {
        "schema": "sttp-release-manifest/v1",
        "release": {
            "name": "STTP 研究快照",
            "version": ver["version"],
            "phase": ver["minor"],
            "snapshot_phase": snap_phase,
            "created": created,
            "phase_note": ("`phase`=VERSION 的 MINOR（开发阶段，唯一事实源 VERSION 文件）；"
                           "`snapshot_phase`=完整快照文件名里的阶段（数据导出阶段，可为 null）。"
                           "两者由 verify_release 交叉校验，漂移即 FAIL。"),
            "positioning": "路线 A：研究原型（收敛版）—— 可复现研究 / 真实跨学科任务 / 方法比较",
            "code_license": "Apache-2.0",
            "data_license_doc": "DATA_LICENSES.md",
        },
        "code": {
            # ★ 权威锚点是 artifacts[*].sha256（与 git 提交无关）。
            #   git_head_at_build 只作**上下文**：本次发布提交是该提交的后继，
            #   清单本身即落在发布提交内 —— 故不把它写成"当前提交"（会自指）。
            "git_head_at_build": git("rev-parse", "HEAD"),
            "git_head_at_build_short": git("rev-parse", "--short", "HEAD"),
            "git_branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "working_tree_dirty_at_build": dirty,
            "dirty_declared_by_flag": bool(dirty and allow_dirty),
            "authority_note": ("产物完整性以 artifacts[*].sha256 为权威；git_head_at_build 仅描述"
                               "清单生成时刻的仓库上下文（发布提交为其后继）。"
                               "★ E21：工作树洁净度是**前置断言** —— 默认「脏即 RC=2、不出清单」；"
                               "`dirty_declared_by_flag=true` 表示这次是显式 --allow-dirty 放行的。"),
            "dirty_sample": porcelain[:8],
        },
        "build": {
            "python": sys.version.split()[0],
            "commands": [
                "bash sttp.sh export                 # 从 Aura/本地重建完整快照（大数据，不入库）",
                "python 06_PoC/release/make_sample.py # 从规范图确定性抽取离线样例",
                "python 06_PoC/release/make_manifest.py --pack",
                "python 06_PoC/release/verify_release.py --release",
            ],
            "pipeline": [
                "11_真实数据/*_raw.json",
                "→ 06_PoC/etl/normalized.json（权威规范图）",
                "→ graph_export.build_graph_data()",
                "→ 06_PoC/graph_data_phase<N>.json（前端快照，.env GRAPH_DATA_FILE）",
                "→ 06_PoC/viz_server.py（页面 /graph_data.json + 查询 /api/*）",
            ],
        },
        "dependencies": deps,
        "artifacts": artifacts,
        "license": license_block,
        "expected_stats": exp,
        "counterexamples": {
            "path": "06_PoC/frozen_counterexamples.json",
            "cases": len(json.load(open(os.path.join(POC, "frozen_counterexamples.json"),
                                        encoding="utf-8")).get("cases") or []),
            "sample_runnable_kinds": ["dim_table", "gate_chem", "gate_math",
                                      "pq_quantity_gate", "attrib_check", "level_check",
                                      "dim_field_semantics", "dim_consistency",
                                      "src_registered", "lineage_acyclic"],
            "full_only_kinds": ["graph_scan"],
            "note": ("`graph_scan` 断言的是**全图不变量**（连通分量/分母外计数等），"
                     "在样例上无意义 → 样例模式只跑自包含子集（SKIP=0）。"),
        },
        "licenses": {
            "code": "Apache-2.0（根 LICENSE + NOTICE + 全部第一方源码 SPDX 头）",
            "vendor": "06_PoC/vendor/licenses/（cytoscape/cose-base/layout-base/cytoscape-fcose=MIT，MathJax=Apache-2.0）",
            "data": "DATA_LICENSES.md（逐源登记 + 打包纪律三态）",
            "raw_data_excluded": "公共数据集/ 与 *_raw.json 不入库；见 DATA_LICENSES.md 的对分发限制",
        },
        "packed_snapshot": packed,
    }

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")

    print("=" * 70)
    print("  发布清单（Phase 37 · R06）")
    print("=" * 70)
    print("  写入        : %s" % os.path.relpath(a.out, ROOT).replace("\\", "/"))
    print("  版本        : %s（开发阶段 %d ／ 数据快照阶段 %s）"
          % (ver["version"], ver["minor"], snap_phase if snap_phase is not None else "—"))
    print("  git HEAD    : %s%s" % (manifest["code"]["git_head_at_build_short"],
                                   "（生成时工作区另有未提交改动）" if dirty else ""))
    for fn, info in artifacts["sample"].items():
        print("  样例 %-24s %8.1f KB  sha256=%s" % (fn, info["bytes"] / 1024.0, info["sha256"][:16]))
    if snap:
        f = artifacts["full"]["snapshot"]
        print("  快照 %-24s %8.1f MB  sha256=%s" % (os.path.basename(snap),
                                                   f["bytes"] / 1048576.0, f["sha256"][:16]))
    print("  依赖漂移    : %s" % (", ".join("%s(%s≠%s)" % (k, v["installed"], v["lock"])
                                          for k, v in drift.items()) or "无"))
    print("  解释器      : 约束 %s（%s）／ 运行 %s"
          % (req_py, req_src, inst.get("python")))
    if packed:
        print("  打包快照    : %s (%.1f MB)" % (packed["path"], packed["bytes"] / 1048576.0))
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
