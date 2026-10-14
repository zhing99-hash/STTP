#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""sttp_cli.cli —— STTP 命令行入口（Phase 45 · A05「第三人可安装入口」）

设计约束（三条，都可由 `bash sttp.sh install` 自检复核）
--------------------------------------------------------
① **无机器绝对路径**：本文件**不含**任何 Windows 盘符路径、也不含任何 POSIX
   家目录路径。根目录靠 `$STTP_ROOT` / 逐级上溯 / 包安装位置三路探测；
   解释器用 `sys.executable`（即「运行本命令的那一个」），不读任何硬编码路径。
   —— 这是复评 A05「`.env` 硬编码他机 Python 路径」在**入口侧**的根治。
② **不复制逻辑**：所有子命令都转发到既有脚本（`sttp.sh` ／
   `06_PoC/release/verify_release.py`）。本包是**薄封装**，不是第二套实现。
   未安装本包时，`bash sttp.sh <cmd>` 完全等价。
③ **两级可复现等级可判定**：`repro_level` 把「随包成品样例重放（L1）」与
   「原始源 → 规范图重建（L2）」**分开报告**，并明确写出
   **L1 通过不代表 L2 可复现**（复评 A05 要求二者不得混为一谈）。

退出码约定（与项目其余部分一致）
--------------------------------
    0 成功 ／ 1 断言/验收失败（由被转发脚本给出） ／ 2 环境或前置缺失
    （如：找不到仓库根、缺 bash、`--release` 所需入库产物不全）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

__all__ = [
    "L1_ARTIFACTS",
    "L2_AUTHORITY",
    "find_root",
    "main",
    "release_verify",
    "reproducibility",
    "repro_level",
    "sample_build",
    "sample_serve",
    "sample_verify",
]

# ---------------------------------------------------------------------------
# 仓库根探测（★ 只用相对标记，无任何机器绝对路径）
# ---------------------------------------------------------------------------
_ROOT_MARKERS = (
    "sttp.sh",
    os.path.join("06_PoC", "release", "verify_release.py"),
    os.path.join("contracts", "subgraph.schema.json"),
)

# L1 · 随包成品样例重放：**全部入库**、无需权威图、无需联网
L1_ARTIFACTS = (
    "06_PoC/release/manifest.json",
    "06_PoC/release/sample/sample_normalized.json",
    "06_PoC/release/sample/sample_graph.json",
    "06_PoC/release/sample/sample_queries.json",
)

# L2 · 原始源 → 规范图重建：需**不入库**的权威图（+ 数据源）
L2_AUTHORITY = (
    "06_PoC/etl/normalized.json",
)


def _is_root(path: str) -> bool:
    return all(os.path.exists(os.path.join(path, m)) for m in _ROOT_MARKERS)


def find_root(start: str | None = None) -> str:
    """定位 STTP 仓库根。

    顺序：`$STTP_ROOT`（须通过标记校验） → 从 `start`（默认当前目录）逐级上溯
    → 从本包的安装位置逐级上溯。

    Raises:
        LookupError: 三路都没有通过标记校验的目录。
    """
    cands: list[str] = []
    env = (os.environ.get("STTP_ROOT") or "").strip().strip('"').strip("'")
    if env:
        cands.append(env)

    for seed in (os.path.abspath(start or os.getcwd()),
                 os.path.abspath(os.path.dirname(__file__))):
        cur = seed
        while True:
            cands.append(cur)
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent

    seen: set[str] = set()
    for c in cands:
        c = os.path.normpath(c)
        if c in seen:
            continue
        seen.add(c)
        if _is_root(c):
            return c

    raise LookupError(
        "找不到 STTP 仓库根（判定标记：%s）。\n"
        "  请任选一种方式指定：\n"
        "    ① 设环境变量 STTP_ROOT 指向仓库根；\n"
        "    ② 在仓库内的任意子目录下运行（会自动上溯）；\n"
        "    ③ 直接使用 `bash sttp.sh <cmd>`（零安装路径，不需要本命令）。"
        % "、".join(_ROOT_MARKERS)
    )


# ---------------------------------------------------------------------------
# 转发
# ---------------------------------------------------------------------------
def _interpreter() -> str:
    """当前解释器 —— 绝不从配置文件读路径（A05c 的入口侧根治）。"""
    return sys.executable or "python"


def _bash() -> str | None:
    return shutil.which("bash")


def _py_cmd(root: str, rel: str, args: list[str]) -> list[str]:
    return [_interpreter(), os.path.join(root, rel), *args]


def _sh_cmd(root: str, args: list[str]) -> list[str] | None:
    b = _bash()
    if not b:
        return None
    return [b, os.path.join(root, "sttp.sh"), *args]


def _forward(rep_label: str, cmd: list[str] | None, root: str, dry: bool) -> int:
    if cmd is None:
        print("⛔ 找不到 `bash`：本项目的编排入口 `sttp.sh` 需要它。", file=sys.stderr)
        print("   （Windows 上请用 Git Bash；或直接调 Python 侧脚本，例如 "
              "`python 06_PoC/release/verify_release.py --release`。）", file=sys.stderr)
        return 2
    shown = " ".join(('"%s"' % c) if (" " in c) else c for c in cmd)
    print("[%s] cwd=%s" % (rep_label, os.path.basename(root) or root))
    print("        %s" % shown)
    if dry:
        print("[dry-run] 未执行（本分支只展示将要执行的命令）")
        return 0
    try:
        return int(subprocess.call(cmd, cwd=root))
    except KeyboardInterrupt:
        return 130


def _common(prog: str, desc: str) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=prog, description=desc)
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将要执行的命令，不执行（供自检与人工核对）")
    return ap


def _root_or_exit() -> str | None:
    try:
        return find_root()
    except LookupError as e:
        print("⛔ %s" % e, file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# 子命令（全部是既有脚本的薄封装）
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    """`sttp <cmd> [args…]` ⇒ 原样转发给 `bash sttp.sh <cmd> [args…]`。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    root = _root_or_exit()
    if root is None:
        return 2
    if not argv:
        print("用法：sttp <cmd> [args…]（等价于 `bash sttp.sh <cmd> [args…]`）")
        return _forward("sttp", _sh_cmd(root, []), root, False)
    return _forward("sttp", _sh_cmd(root, argv), root, False)


def sample_verify(argv=None) -> int:
    """L1：校验**已入库**的随包样例发布包（不需要权威图、不需要联网）。"""
    ap = _common("sttp-sample-verify",
                 "L1 随包成品样例重放：校验已入库的发布包（fail-closed）")
    ap.add_argument("--json", action="store_true", help="机器可读结果（转发）")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    root = _root_or_exit()
    if root is None:
        return 2
    args = ["--release"] + (["--json"] if a.json else [])
    return _forward("sample-verify", _py_cmd(root, "06_PoC/release/verify_release.py", args),
                    root, a.dry_run)


def sample_serve(argv=None) -> int:
    """L1：只用**离线样例**起可视化（页面与查询指向同一份切片）。"""
    ap = _common("sttp-sample-serve", "L1 用离线样例起可视化服务")
    ap.add_argument("--port", type=int, default=None,
                    help="监听端口（默认取 VIZ_PORT / 8765）")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    root = _root_or_exit()
    if root is None:
        return 2
    args = ["sample"] + (["--port", str(a.port)] if a.port else [])
    return _forward("sample-serve", _sh_cmd(root, args), root, a.dry_run)


def sample_build(argv=None) -> int:
    """L2：重建随包样例（**需要**不入库的权威图；缺输入即 fail-closed）。"""
    ap = _common("sttp-sample-build",
                 "L2 原始源 → 规范图重建并重建样例（需不入库的权威图）")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    root = _root_or_exit()
    if root is None:
        return 2
    missing = [p for p in L2_AUTHORITY if not os.path.exists(os.path.join(root, p))]
    if missing:
        print("⛔ L2 不可用：缺权威图 %s（被 .gitignore 排除 ⇒ 干净检出下**不可能**重建）。"
              % "、".join(missing), file=sys.stderr)
        print("   这是**刻意的 fail-closed**，不是缺陷；纯净检出请改用 L1："
              "`sttp-sample-verify`。", file=sys.stderr)
        return 2
    return _forward("sample-build", _sh_cmd(root, ["release"]), root, a.dry_run)


def release_verify(argv=None) -> int:
    """L1（+ 有完整快照时的附加项）：发布校验；`--full` 追加完整快照校验。"""
    ap = _common("sttp-release-verify", "发布校验器（--full 追加完整快照校验）")
    ap.add_argument("--full", action="store_true", help="追加完整快照校验（默认开启）")
    ap.add_argument("--release", action="store_true",
                    help="fail-closed：缺依赖/缺主图亦非零（默认开启）")
    ap.add_argument("--json", action="store_true", help="机器可读结果（转发）")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    root = _root_or_exit()
    if root is None:
        return 2
    args = ["--release", "--full"] + (["--json"] if a.json else [])
    return _forward("release-verify",
                    _py_cmd(root, "06_PoC/release/verify_release.py", args),
                    root, a.dry_run)


# ---------------------------------------------------------------------------
# 两级可复现等级
# ---------------------------------------------------------------------------
def reproducibility(root: str) -> dict:
    """把「两级可复现等级」变成**机器可判**的结构（复评 A05 的显式要求）。

    * **L1 · 随包成品样例重放**：只依赖**入库**产物 ⇒ 任何拿到仓库的人都能跑。
    * **L2 · 原始源 → 规范图重建**：依赖**不入库**的权威图（+ 数据源）。

    ★ **L1 通过不代表 L2 可复现** —— 二者是不同等级，不得混为一谈。
    """
    l1_missing = [p for p in L1_ARTIFACTS if not os.path.exists(os.path.join(root, p))]
    l2_missing = [p for p in L2_AUTHORITY if not os.path.exists(os.path.join(root, p))]
    return {
        "root": root,
        "L1_artifact_replay": {
            "ok": not l1_missing,
            "missing": l1_missing,
            "needs_authority_graph": False,
            "needs_network": False,
            "what": "随包成品样例重放（全部为**已入库**产物）",
            "how": "sttp-sample-verify　或　python 06_PoC/release/verify_release.py --release",
        },
        "L2_full_rebuild": {
            "ok": not l2_missing,
            "missing": l2_missing,
            "needs_authority_graph": True,
            "needs_network": True,
            "what": "原始源 → 规范图重建（需不入库的权威图与数据源）",
            "how": "bash sttp.sh export && bash sttp.sh release",
        },
        "note": "L1 通过**不代表** L2 可复现：二者是不同等级，不得混为一谈。",
    }


def repro_level(argv=None) -> int:
    """打印两级可复现等级的可判定状态（L1 不可用 ⇒ 退码 2，fail-closed）。"""
    ap = _common("sttp-repro-level", "报告两级可复现等级（L1 样例重放 / L2 全图重建）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON（默认亦为 JSON）")
    ap.parse_args(sys.argv[1:] if argv is None else argv)
    root = _root_or_exit()
    if root is None:
        return 2
    info = reproducibility(root)
    l1 = info["L1_artifact_replay"]
    l2 = info["L2_full_rebuild"]
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print("—— 摘要：L1 %s%s ｜ L2 %s%s"
          % ("可用" if l1["ok"] else "不可用",
             "" if l1["ok"] else "（缺 %s）" % "、".join(l1["missing"]),
             "可用" if l2["ok"] else "不可用",
             "" if l2["ok"] else "（缺 %s —— 刻意不入库）" % "、".join(l2["missing"])))
    if not l1["ok"]:
        print("⛔ L1 的入库产物不全 ⇒ 该副本连「样例重放」都无法验证（fail-closed）。",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
