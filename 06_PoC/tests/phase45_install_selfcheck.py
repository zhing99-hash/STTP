# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""
Phase 45 · A05「第三人可安装入口」**非真空自检**（A05-0 … A05-14）
================================================================================

目的
----
把复评 A05 的三条现场变成**机器可判的断言**（`00_项目管理/codex复评响应与下一阶段规划_20261013.md`
§2 复评表 + §4.2 的 A05 要点）：

    A05a  仓库无 `pyproject.toml`（无可安装入口）
    A05b  `requirements.lock` 含 `python==3.11.9` —— 解释器约束与 pip 依赖**混列**
    A05c  `.env` 硬编码他机 Python 路径，且**没有根级配置模板**

外加 A05 要点里要求的另两条（本轮补齐）：
    ① **核心 / 可选分组**；② **「随包成品样例重放」与「原始源 → 规范图重建」
       分成两个可复现等级、不得混为一谈**。

做法（为什么这样写）
--------------------
* **零第三方依赖**：只用 `tomllib`(3.11+ 标准库) / `ast` / `re` / `zipfile` / `subprocess`
  —— 自检本身**不得**成为「为了验证零依赖而引入依赖」的笑话。
* **仪器自证（铁律 #76 的镜像）**：先跑 A05-0（三步已知答案），确认版本比较器
  **能给正例也给反例**；若自证失败 ⇒ **退出码 3**，与「有 FAIL」（退出码 1）**分开**。
  —— 这样「全红」永远不会被误读成「全坏」。
* **反例可注入**：A05-6 / A05-7 / A05-8 / A05-10 各有一个**构造性负对照**
  （注入一个假包名 / 假机器路径），证明断言不是真空（铁律 #14 / #43 / #75）。
* **可在干净检出运行**：本自检只依赖**已入库**文件（`pyproject.toml` / `requirements*.txt` /
  `.env.example` / `sttp_cli/**` / `verify_release.py`）⇒ 第三人 clone 后即可复核（铁律 #53）。

★ 如实披露（**未覆盖项，不计通过**）
------------------------------------
* **`pip install .` 未在本环境实测**（需构建后端；本机零第三方依赖、且无网络保证）。
  代偿：A05-13 在**临时目录**里做**离线 wheel 构建探针**
  （`pip wheel --no-deps --no-build-isolation`），并校验 wheel 内含 `sttp_cli/cli.py`
  与 `entry_points.txt` 的 5 个入口。若构建后端缺失 ⇒ 记 **SKIP 并写明原因**，**不假通过**。
* **`pip download` / 依赖可获取性**未验证（需联网到 PyPI）。
* 未验证「装完之后 5 个入口都真的能跑通全链路」——那需要权威图（不入库）。
  已由 `repro_level` 把这件事显式分成 L1/L2 两级（见 A05-12）。

退出码
------
    0 全过 ／ 1 有 FAIL ／ 2 缺关键前置文件 ／ **3 仪器自证失败（区别于 1）**
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))           # 06_PoC/tests
POC = os.path.dirname(HERE)                                 # 06_PoC
ROOT = os.path.dirname(POC)                                 # STTP

PYPROJECT = os.path.join(ROOT, "pyproject.toml")
REQ_LOCK = os.path.join(ROOT, "requirements.lock")
REQ_TXT = os.path.join(ROOT, "requirements.txt")
ENV_EXAMPLE = os.path.join(ROOT, ".env.example")
GITIGNORE = os.path.join(ROOT, ".gitignore")
CLI_DIR = os.path.join(ROOT, "sttp_cli")
CLI_PY = os.path.join(CLI_DIR, "cli.py")
VERIFY_RELEASE = os.path.join(ROOT, "06_PoC", "release", "verify_release.py")

EXIT_OK, EXIT_FAIL, EXIT_PREREQ, EXIT_INSTRUMENT = 0, 1, 2, 3

# ---------------------------------------------------------------------------
# 机器绝对路径的判定（★ 本自检文件自身不在扫描范围内，故模式可以显式写）
# ---------------------------------------------------------------------------
MACHINE_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/](?:Users|Program\sFiles|Windows)[\\/]"
    r"|/(?:Users|home)/[A-Za-z0-9_.\-]+/"
    r"|AppData[\\/](?:Local|Roaming))",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 仪器：PEP 440 **子集**版本比较器
# ---------------------------------------------------------------------------
_SPEC_RE = re.compile(r"^(>=|<=|==|!=|>|<)\s*([0-9][0-9A-Za-z.]*)$")


def _vt(s: str) -> tuple:
    out: list[int] = []
    for p in str(s).split("."):
        if p.isdigit():
            out.append(int(p))
        else:
            break
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def spec_ok(spec: str, version: str) -> bool:
    """PEP 440 **子集**：支持 `>= <= == != > <`（逗号 = 与）。

    ★ 不支持的写法（含 `~=`）**显式抛错**，不静默放过（铁律 #63：拒绝分支必须显式）。
    `~=X.Y` 的上界语义随段数变化，手写易错 ⇒ 本项目一律用 `>=X,<Y` 显式表达。
    """
    v = _vt(version)
    for raw in str(spec).split(","):
        raw = raw.strip()
        if not raw:
            continue
        m = _SPEC_RE.match(raw)
        if not m:
            raise ValueError("不支持的版本约束写法：%r（本自检只支持 >= <= == != > <）" % raw)
        op, rhs = m.group(1), _vt(m.group(2))
        ok = {">=": v >= rhs, "<=": v <= rhs, "==": v == rhs,
              "!=": v != rhs, ">": v > rhs, "<": v < rhs}[op]
        if not ok:
            return False
    return True


# ---------------------------------------------------------------------------
# requirements 行解析（只取**分发名**；零第三方）
# ---------------------------------------------------------------------------
_REQ_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$")

# 分发名 → import 名（仅列**不一致**的）
DIST_TO_IMPORT = {"scikit-learn": "sklearn", "pillow": "PIL", "pyyaml": "yaml"}

# 「解释器伪包」：写进 requirements 会让 pip 去找同名**分发**（PyPI 上确有同名陈旧包）
# ★ 只把**解释器**计入；`pip`/`setuptools`/`wheel` 是**真实分发**，在 requirements 里合法。
INTERPRETER_PSEUDO = ("python", "python3", "python2", "cpython")

# 第三方 import 的**显式 ACK 白名单**（必须写原因；★ fail-closed：未列即 FAIL）
ACK_UNDECLARED = {
    "playwright": "06_PoC/tests/a4_offline_render.py 的离线渲染试验；§0.1 / D14 明确"
                  "「零第三方依赖 ⇒ 不装 Playwright / jsdom」⇒ **只登记不声明**，"
                  "避免给第三人「受支持路径」的错觉。",
}


def _req_name(line: str):
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    if s.startswith(("-", "--")) or "://" in s:
        return None
    m = _REQ_RE.match(s)
    return m.group(1).lower() if m else None


def _req_spec(line: str) -> str:
    s = line.strip()
    m = _REQ_RE.match(s)
    return (m.group(2) or "").strip() if m else ""


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _req_names(path: str) -> dict:
    out = {}
    for ln in _read(path).splitlines():
        n = _req_name(ln)
        if n:
            out[n] = _req_spec(ln)
    return out


# ---------------------------------------------------------------------------
# 报告器
# ---------------------------------------------------------------------------
class Rep:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []
        self.notes: list[str] = []

    def add(self, ok, name, detail=""):
        self.rows.append(("PASS" if ok else "FAIL", name, detail))
        print("  [%s] %-46s %s" % ("PASS" if ok else "FAIL", name, detail))
        return bool(ok)

    def skip(self, name, note):
        self.rows.append(("SKIP", name, note))
        print("  [SKIP] %-46s %s" % (name, note))

    def note(self, text):
        self.notes.append(text)
        print("  [NOTE] %s" % text)

    def counts(self):
        c = {"PASS": 0, "FAIL": 0, "SKIP": 0}
        for s, _, _ in self.rows:
            c[s] += 1
        return c


# ---------------------------------------------------------------------------
# 源文件扫描
# ---------------------------------------------------------------------------
def _iter_tracked_py(rep: Rep) -> list[str]:
    """枚举**已入库**的 .py（用 git；git 不可用时退化为目录遍历并**如实声明**）。

    ★ 恒**排除** `07_交付物/外部评审/**`：那是**第三方评审归档**（含复评方自己的脚本），
      不是本项目代码；把它算进「声明必须覆盖的 import」是口径错误。
    """
    ext_dir = os.path.normpath(os.path.join(ROOT, "07_交付物", "外部评审"))

    def _keep(rel: str) -> bool:
        return not os.path.normpath(os.path.join(ROOT, rel)).startswith(ext_dir)

    try:
        r = subprocess.run(["git", "ls-files", "-z", "--", "*.py"],
                           cwd=ROOT, capture_output=True, check=False)
        if r.returncode == 0 and r.stdout:
            names = [p for p in r.stdout.decode("utf-8", "replace").split("\0")
                     if p and _keep(p)]
            rep.note("文件枚举：git ls-files（%d 个入库 .py，已排除 07_交付物/外部评审）"
                     "—— 与干净检出同口径。" % len(names))
            return [os.path.join(ROOT, p) for p in names]
        rep.note("git ls-files 返回 rc=%d ⇒ 退化为目录遍历。" % r.returncode)
    except OSError as e:  # noqa: BLE001
        rep.note("git 不可用（%s）⇒ 退化为目录遍历。" % e)

    out = []
    skip_dirs = {".git", "__pycache__", ".runlog", "node_modules", "build", "dist"}
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in skip_dirs]
        if os.path.normpath(dp).startswith(ext_dir):
            continue
        for f in fn:
            if f.endswith(".py"):
                out.append(os.path.join(dp, f))
    return out


def scan_third_party(rep: Rep) -> dict:
    """全仓实际 import 的第三方模块（AST 解析；剔除 stdlib 与本地模块）。

    ★ 用 AST 而非正则：正则会把文档字符串里以 `import ` 开头的行也算进来。
    """
    files = _iter_tracked_py(rep)
    local = {os.path.basename(p)[:-3] for p in files}
    std = set(sys.stdlib_module_names)
    found: dict[str, list[str]] = {}
    bad_syntax = []
    for p in files:
        try:
            tree = ast.parse(_read(p))
        except SyntaxError as e:
            bad_syntax.append("%s（%s）" % (os.path.relpath(p, ROOT), e.msg))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]] if node.level == 0 else []
            else:
                continue
            for n in names:
                if not n or n in std or n in local:
                    continue
                found.setdefault(n, []).append(os.path.relpath(p, ROOT))
    if bad_syntax:
        rep.note("AST 解析失败（已跳过，如实披露）：%s" % "；".join(bad_syntax))
    return found


# ---------------------------------------------------------------------------
# 构造性负对照
# ---------------------------------------------------------------------------
def undeclared_of(imports, declared_import: set, ack: dict):
    """返回 (缺失声明集, 陈旧 ACK 集)。★ 纯函数 ⇒ 可被负对照直接调用。"""
    missing = sorted(set(imports) - set(declared_import) - set(ack))
    stale = sorted(set(ack) & set(declared_import))
    return missing, stale


# ---------------------------------------------------------------------------
# 离线 wheel 构建探针
# ---------------------------------------------------------------------------
def wheel_probe(rep: Rep, tmp: str) -> tuple[str, dict]:
    """在**临时目录**里离线构建 wheel（不污染仓库），并校验其内容。"""
    src = os.path.join(tmp, "src")
    os.makedirs(src, exist_ok=True)
    for rel in ("pyproject.toml", "LICENSE", "README.md"):
        s = os.path.join(ROOT, rel)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(src, rel))
    shutil.copytree(CLI_DIR, os.path.join(src, "sttp_cli"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    out = os.path.join(tmp, "wheelout")
    os.makedirs(out, exist_ok=True)

    try:
        import setuptools  # noqa: F401
    except ImportError as e:  # noqa: BLE001
        return "SKIP", {"reason": "本环境无 setuptools ⇒ 无法离线构建（%s）" % e}

    cmd = [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
           "-w", out, src]
    try:
        r = subprocess.run(cmd, cwd=tmp, capture_output=True, timeout=600, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:  # noqa: BLE001
        return "SKIP", {"reason": "wheel 构建未能执行：%s" % e}
    log = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
    whls = [f for f in os.listdir(out) if f.endswith(".whl")]
    if r.returncode != 0 or not whls:
        return "FAIL", {"rc": r.returncode,
                        "tail": log.strip().splitlines()[-6:] if log.strip() else []}
    whl = os.path.join(out, whls[0])
    info = {"wheel": whls[0], "bytes": os.path.getsize(whl)}
    with zipfile.ZipFile(whl) as z:
        names = z.namelist()
        info["has_cli"] = "sttp_cli/cli.py" in names
        ep = [n for n in names if n.endswith("entry_points.txt")]
        scripts: list[str] = []
        if ep:
            body = z.read(ep[0]).decode("utf-8", "replace")
            scripts = re.findall(r"^([A-Za-z0-9._-]+)\s*=", body, re.M)
        info["entry_points"] = sorted(scripts)
    return "OK", info


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Phase 45 · A05 可安装入口自检（A05-0…A05-14）")
    ap.add_argument("--json", default=None, help="把报告写成 JSON（审计留痕）")
    ap.add_argument("--no-wheel", action="store_true", help="跳过离线 wheel 构建探针")
    args = ap.parse_args(argv)

    rep = Rep()
    t0 = time.time()
    print("=" * 78)
    print("  Phase 45 · A05「第三人可安装入口」自检（A05-0 … A05-14）")
    print("=" * 78)

    # ---- 前置：关键文件齐否（缺 ⇒ RC=2 + 明确原因，不让后续以小错误崩掉）----
    need = {"pyproject.toml": PYPROJECT, "requirements.lock": REQ_LOCK,
            "requirements.txt": REQ_TXT, ".env.example": ENV_EXAMPLE,
            "sttp_cli/cli.py": CLI_PY}
    miss = [k for k, v in need.items() if not os.path.exists(v)]
    if miss:
        print("-" * 78)
        print("  ⛔ 缺关键文件：%s" % "、".join(miss))
        print("     若这是**旧副本**（A05 之前）⇒ 属预期；请先取得含 Phase 45 的版本。")
        print("-" * 78)
        return EXIT_PREREQ

    # =====================================================================
    print("\n[A05-0] 仪器自证：版本比较器必须**既给正例也给反例**（铁律 #76 的镜像）")
    # =====================================================================
    cases = [(">=3.11,<3.12", "3.11.9", True, "本项目锁定环境"),
             (">=3.11,<3.12", "3.13.12", False, "本机空环境（明确不在范围内）"),
             (">=3.11,<3.12", "3.10.0", False, "过旧")]
    inst_ok = True
    for spec, ver, want, why in cases:
        try:
            got = spec_ok(spec, ver)
        except ValueError as e:  # noqa: BLE001
            got, why = None, "%s ｜ 抛错：%s" % (why, e)
        good = (got is want)
        inst_ok &= rep.add(good, "A05-0 比较器 %s vs %s" % (spec, ver),
                           "→ %s（期望 %s，%s）" % (got, want, why))
    try:
        spec_ok("~=3.11", "3.11.9")
        refused = False
    except ValueError:
        refused = True
    inst_ok &= rep.add(refused, "A05-0 不支持的写法必须**显式拒绝**",
                       "~=3.11 → 抛错（fail-closed，铁律 #63）")
    if not inst_ok:
        print("-" * 78)
        print("  ⛔ **仪器自证失败** ⇒ 后续「全红」可能是测量器坏了，而不是被测对象坏了。")
        print("     退出码 **3**（与「有 FAIL」的 1 分开；铁律 #76）。")
        print("-" * 78)
        return EXIT_INSTRUMENT

    # =====================================================================
    print("\n[A05-1..A05-5] pyproject.toml：可解析 / 元数据 / 解释器约束 / 依赖分组")
    # =====================================================================
    try:
        with open(PYPROJECT, "rb") as f:
            pp = tomllib.load(f)
        rep.add(True, "A05-1 pyproject.toml 可被 tomllib 解析", "TOML 合法")
    except (tomllib.TOMLDecodeError, OSError) as e:  # noqa: BLE001
        rep.add(False, "A05-1 pyproject.toml 可被 tomllib 解析", str(e))
        pp = {}

    proj = pp.get("project", {}) or {}
    req_fields = [k for k in ("name", "version", "description", "readme",
                              "requires-python", "license", "authors")
                  if not proj.get(k)]
    rep.add(not req_fields, "A05-2 [project] 必备元数据齐全",
            "缺 %s" % req_fields if req_fields else
            "%s %s" % (proj.get("name"), proj.get("version")))

    rp = proj.get("requires-python") or ""
    ok311 = ok313 = False
    if rp:
        ok311 = spec_ok(rp, "3.11.9")
        ok313 = spec_ok(rp, "3.13.12")
    rep.add(bool(rp) and ok311 and not ok313, "A05-3 requires-python 覆盖 3.11 且**排除 3.13**",
            "requires-python=%r → 3.11.9 通过=%s ／ 3.13.12 通过=%s（期望 T/F）" % (rp, ok311, ok313))

    core = {(_req_name(d) or "") for d in (proj.get("dependencies") or [])}
    core = {c for c in core if c}
    # ★ 与 verify_release.py 的 REQUIRED_MODULES **同源比对**（不在此重复维护第二份）
    try:
        vsrc = _read(VERIFY_RELEASE)
        m = re.search(r"REQUIRED_MODULES\s*=\s*\(([^)]*)\)", vsrc)
        req_mods = set(re.findall(r"[\"']([A-Za-z0-9_.\-]+)[\"']", m.group(1))) if m else set()
    except OSError:
        req_mods = set()
    if not req_mods:
        rep.add(False, "A05-4 核心依赖 ⊇ verify_release.REQUIRED_MODULES",
                "未能从 verify_release.py 读到 REQUIRED_MODULES（★ 不静默跳过）")
    else:
        lack = sorted(req_mods - core)
        nospec = sorted(d for d in (proj.get("dependencies") or [])
                        if not _req_spec(d))
        rep.add(not lack and not nospec,
                "A05-4 核心依赖 ⊇ verify_release.REQUIRED_MODULES（L1 最小集）",
                "缺 %s" % lack if lack else
                ("核心裸名（已安装却无下限 ⇒ 应给版本）：%s" % nospec if nospec else
                 "核心 %d 项 %s" % (len(core), sorted(core))))

    extras = proj.get("optional-dependencies") or {}
    want_extras = {"analysis", "etl_alt", "kuzu", "neo4j", "torch", "fetch", "llm",
                   "linker", "all"}
    lack_e = sorted(want_extras - set(extras))
    allvals = {(_req_name(d) or "") for d in (extras.get("all") or [])}
    union = set()
    for k, v in extras.items():
        if k == "all":
            continue
        union |= {(_req_name(d) or "") for d in v}
    union.discard("")
    allvals.discard("")
    rep.add(not lack_e and allvals == union,
            "A05-5 可选分组齐全 且 `all` == 其余分组之并",
            "缺分组 %s；all 与并集差 %s"
            % (lack_e, sorted(allvals ^ union)) if (lack_e or allvals != union)
            else "%d 个分组：%s" % (len(extras), sorted(extras)))

    # =====================================================================
    print("\n[A05-6] 解释器约束与 pip 依赖**分离**（复评 A05b）—— 含反例")
    # =====================================================================
    pseudo_hits = {}
    for tag, path in (("lock", REQ_LOCK), ("txt", REQ_TXT)):
        names = _req_names(path)
        hit = sorted(n for n in names if n in INTERPRETER_PSEUDO)
        if hit:
            pseudo_hits[tag] = hit
    rep.add(not pseudo_hits, "A05-6a 依赖清单内**无解释器伪包**",
            "命中 %s" % pseudo_hits if pseudo_hits else
            "requirements.lock/txt 均无 %s" % list(INTERPRETER_PSEUDO))

    def _pseudo_of(lines):
        return sorted({_req_name(ln) for ln in lines
                       if _req_name(ln) and _req_name(ln) in INTERPRETER_PSEUDO})

    bad = ["# 反例", "python==3.11.9", "networkx>=3.2"]
    rep.add(_pseudo_of(bad) == ["python"], "A05-6b 反例可注入（探针真能报红）",
            "注入 `python==3.11.9` → 命中 %s" % _pseudo_of(bad))

    # =====================================================================
    print("\n[A05-7] 声明必须覆盖全仓实际 import（复评 A05 的「可安装」前提）")
    # =====================================================================
    third = scan_third_party(rep)
    declared = set(core)
    for k, v in extras.items():
        declared |= {(_req_name(d) or "") for d in v}
    declared.discard("")
    declared_import = {DIST_TO_IMPORT.get(d, d) for d in declared}
    missing, stale = undeclared_of(third, declared_import, ACK_UNDECLARED)
    rep.add(not missing and not stale,
            "A05-7a 每个第三方 import 都已声明或已显式 ACK",
            ("未声明：%s" % ["%s(%d处)" % (n, len(third[n])) for n in missing]) if missing
            else ("陈旧 ACK（已声明却仍在白名单）：%s" % stale if stale
                  else "扫描 %d 个第三方 import，全部覆盖（ACK %d 个：%s）"
                       % (len(third), len(ACK_UNDECLARED), sorted(ACK_UNDECLARED))))
    if ACK_UNDECLARED:
        for k in sorted(ACK_UNDECLARED):
            rep.note("ACK 未声明依赖 `%s`：%s" % (k, ACK_UNDECLARED[k]))

    # ★ 负对照必须**与仓库当前状态无关**：只看「探针是否把假包名判为未声明、
    #   把已声明项判为已声明」。否则 A05-7a 一红，本条会被牵连一起红（信息坍缩）。
    neg_missing, _neg_stale = undeclared_of(set(third) | {"zzz_a05_negative_control"},
                                            declared_import, ACK_UNDECLARED)
    rep.add(("zzz_a05_negative_control" in neg_missing) and ("networkx" not in neg_missing),
            "A05-7b 负对照（假包名判为未声明、已声明项不误报）",
            "假包名命中=%s ／ networkx 未误报=%s"
            % ("zzz_a05_negative_control" in neg_missing, "networkx" not in neg_missing))

    # =====================================================================
    print("\n[A05-8..A05-10] 无机器绝对路径（复评 A05c）—— 含反例")
    # =====================================================================
    body_env = _read(ENV_EXAMPLE)
    hits = sorted({m.group(0) for m in MACHINE_PATH_RE.finditer(body_env)})
    rep.add(not hits, "A05-8a 根 `.env.example` 无机器绝对路径",
            "命中 %s" % hits if hits else
            "%d 字节，含 %d 个键，无盘符/家目录路径" % (len(body_env), len(
                [ln for ln in body_env.splitlines() if _req_name(ln)])))
    bad_txt = "STTP_PYTHON=C:\\" + "Users\\" + "someone\\python.exe\n"
    rep.add(bool(MACHINE_PATH_RE.search(bad_txt)), "A05-8b 反例可注入（探针真能报红）",
            "注入 Windows 盘符路径 → 命中 %s"
            % sorted({m.group(0) for m in MACHINE_PATH_RE.finditer(bad_txt)}))

    # .env 必须被忽略、且不得入库
    ig = _read(GITIGNORE) if os.path.exists(GITIGNORE) else ""
    ig_ok = any(ln.strip() == ".env" for ln in ig.splitlines())
    try:
        ci = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT,
                            capture_output=True, check=False)
        ci_ok = (ci.returncode == 0)
    except OSError:
        ci_ok = False
    try:
        tr = subprocess.run(["git", "ls-files", "--error-unmatch", ".env"], cwd=ROOT,
                            capture_output=True, check=False)
        tracked = (tr.returncode == 0)
    except OSError:
        tracked = False
    rep.add(ig_ok and ci_ok and not tracked, "A05-9 `.env` 被 git 忽略且未被跟踪",
            ".gitignore 含 `.env`=%s ／ check-ignore=%s ／ 已被跟踪=%s"
            % (ig_ok, ci_ok, tracked))

    cli_files = [os.path.join(CLI_DIR, f) for f in sorted(os.listdir(CLI_DIR))
                 if f.endswith(".py")]
    cli_hits = {}
    for p in cli_files:
        h = sorted({m.group(0) for m in MACHINE_PATH_RE.finditer(_read(p))})
        if h:
            cli_hits[os.path.basename(p)] = h
    rep.add(not cli_hits, "A05-10a `sttp_cli/**.py` 无机器绝对路径",
            "命中 %s" % cli_hits if cli_hits else "%d 个文件，零命中" % len(cli_files))
    rep.add(bool(MACHINE_PATH_RE.search("p = r'/home/" + "someone/x'")),
            "A05-10b 反例可注入（探针真能报红）",
            "注入 POSIX 家目录路径 → 命中")

    # =====================================================================
    print("\n[A05-11..A05-14] 入口可用 / 薄封装自足 / 两级可复现 / 清单单点一致 / 离线构建")
    # =====================================================================
    import importlib.util
    spec = importlib.util.spec_from_file_location("_sttp_cli_under_test", CLI_PY)
    cli_mod = None
    try:
        cli_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli_mod)
    except Exception as e:  # noqa: BLE001
        rep.add(False, "A05-11 `sttp_cli/cli.py` 可独立导入", "%s: %s" % (type(e).__name__, e))
    if cli_mod is not None:
        scripts = proj.get("scripts") or {}
        bad_ep = []
        for name, target in scripts.items():
            fn = target.split(":")[-1]
            if not callable(getattr(cli_mod, fn, None)):
                bad_ep.append("%s→%s" % (name, fn))
        rep.add(bool(scripts) and not bad_ep,
                "A05-11 [project.scripts] 入口全部可调用",
                "未实现 %s" % bad_ep if bad_ep else
                "%d 个入口：%s" % (len(scripts), sorted(scripts)))

    # --- 薄封装自足性：在**临时 cwd**、**剥掉 PYTHONPATH/STTP_*** 的环境里导入 ---
    with tempfile.TemporaryDirectory(prefix="sttp_a05_isolated_") as iso:
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONPATH", "STTP_ROOT", "STTP_PYTHON")}
        code = ("import importlib.util,sys;"
                "spec=importlib.util.spec_from_file_location('m',%r);"
                "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
                "print('SELFCONTAINED', callable(m.main))" % CLI_PY)
        try:
            r = subprocess.run([sys.executable, "-c", code], cwd=iso, env=env,
                               capture_output=True, timeout=120, check=False)
            out = (r.stdout or b"").decode("utf-8", "replace").strip()
            rep.add(r.returncode == 0 and "SELFCONTAINED True" in out,
                    "A05-12a `sttp_cli.cli` 不依赖仓库内模块（薄封装）",
                    "rc=%d out=%r" % (r.returncode, out.splitlines()[-1] if out else ""))
        except (OSError, subprocess.TimeoutExpired) as e:  # noqa: BLE001
            rep.add(False, "A05-12a `sttp_cli.cli` 不依赖仓库内模块（薄封装）", str(e))

    # --- 两级可复现等级：真实根 vs 构造的「缺 manifest」根 ---
    if cli_mod is not None:
        info = cli_mod.reproducibility(ROOT)
        l1, l2 = info["L1_artifact_replay"], info["L2_full_rebuild"]
        sep = (l1["needs_authority_graph"] is False) and (l2["needs_authority_graph"] is True)
        rep.add(l1["ok"] and sep,
                "A05-12b L1 可用 且 L1/L2 的**前置条件被显式区分**",
                "L1 ok=%s(缺 %s) ／ L2 ok=%s(缺 %s) ／ 需权威图 L1=%s L2=%s"
                % (l1["ok"], l1["missing"], l2["ok"], l2["missing"],
                   l1["needs_authority_graph"], l2["needs_authority_graph"]))
        with tempfile.TemporaryDirectory(prefix="sttp_a05_l1neg_") as fake:
            for rel in cli_mod.L1_ARTIFACTS[1:]:
                p = os.path.join(fake, rel)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    f.write("{}")
            f2 = cli_mod.reproducibility(fake)
            rep.add(f2["L1_artifact_replay"]["ok"] is False,
                    "A05-12c 构造性：缺 manifest ⇒ L1 **判为不可用**",
                    "→ ok=%s 缺 %s" % (f2["L1_artifact_replay"]["ok"],
                                       f2["L1_artifact_replay"]["missing"]))
        rep.note("★ L1 通过**不代表** L2 可复现：二者前置条件不同，报告见 `sttp-repro-level`。")

    # --- A05-14 发布清单与 pyproject 的**单点一致**（解释器约束只应有一个权威）---
    manifest_path = os.path.join(ROOT, "06_PoC", "release", "manifest.json")
    if not os.path.exists(manifest_path):
        rep.skip("A05-14 清单的 interpreter.constraint == pyproject 的 requires-python",
                 "manifest.json 不存在（二进制/裁剪副本）")
    else:
        try:
            with open(manifest_path, "rb") as f:
                mf = json.load(f)
            interp = (mf.get("dependencies") or {}).get("interpreter") or {}
            tgt = (mf.get("dependencies") or {}).get("target") or {}
            same = (interp.get("constraint") == (rp or None))
            no_python_in_target = "python" not in tgt
            rep.add(same and no_python_in_target and bool(interp.get("running")),
                    "A05-14 清单的 interpreter.constraint == pyproject 的 requires-python",
                    "清单 %r ／ pyproject %r ｜ target 不含解释器伪包=%s ｜ 运行 %s"
                    % (interp.get("constraint"), rp, no_python_in_target, interp.get("running")))
        except (json.JSONDecodeError, OSError) as e:  # noqa: BLE001
            rep.add(False, "A05-14 清单的 interpreter.constraint == pyproject 的 requires-python",
                    "清单不可读：%s" % e)

    # --- A05-13 离线 wheel 构建探针 ---
    if args.no_wheel:
        rep.skip("A05-13 离线 wheel 构建探针", "按 --no-wheel 跳过（不计通过）")
    else:
        with tempfile.TemporaryDirectory(prefix="sttp_a05_wheel_") as tmp:
            status, info = wheel_probe(rep, tmp)
        if status == "OK":
            want = set((proj.get("scripts") or {}).keys())
            got = set(info.get("entry_points") or [])
            rep.add(info.get("has_cli") and want <= got,
                    "A05-13 离线构建 wheel 成功 且 内含入口与入口点",
                    "%s（%.1f KB）｜ cli.py=%s ｜ entry_points ⊇ %s"
                    % (info.get("wheel"), info.get("bytes", 0) / 1024.0,
                       info.get("has_cli"), sorted(got)))
        elif status == "SKIP":
            rep.skip("A05-13 离线 wheel 构建探针", info.get("reason", ""))
        else:
            rep.add(False, "A05-13 离线构建 wheel 成功", "rc=%s tail=%s"
                    % (info.get("rc"), info.get("tail")))

    # =====================================================================
    c = rep.counts()
    print("-" * 78)
    print("  合计 PASS %d / FAIL %d / SKIP %d   （耗时 %.1f s）"
          % (c["PASS"], c["FAIL"], c["SKIP"], time.time() - t0))
    if c["SKIP"]:
        print("  ★ SKIP **不计入通过**：%s"
              % "；".join(n for s, n, _ in rep.rows if s == "SKIP"))
    print("-" * 78)
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w", encoding="utf-8", newline="") as f:
            json.dump({"schema": "sttp-phase45-install-selfcheck/v1",
                       "counts": c, "root": ROOT,
                       "third_party_imports": sorted(third),
                       "ack_undeclared": ACK_UNDECLARED,
                       "rows": [{"status": s, "name": n, "detail": d} for s, n, d in rep.rows],
                       "notes": rep.notes}, f, ensure_ascii=False, indent=2)
        print("  留痕：%s" % os.path.relpath(args.json, ROOT))
    return EXIT_FAIL if c["FAIL"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
