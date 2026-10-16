# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""发布链**自身产物**的清单 —— 唯一事实源（铁律 #88；Phase 55 · D64）。

它解决的问题
------------
`bash sttp.sh release` 的次序是「**重建样例** → 生成清单（含洁净度前置断言）」。
样例是**被跟踪**文件 ⇒ 只要图谱变过，重建就会让工作树变脏 ⇒ 前置断言**必然拒绝出清单**。
而拒绝文案给出的「正确次序 ③ `bash sttp.sh release`」**就是刚刚失败的那一步** ——
按字面执行必然死循环。Phase 54 实测坐实（`[1/4]` 重建后 `[2/4]` RC=2）。

要正确区分「**本流水线自己重建的产物**」与「外部未提交改动」，两边**必须**用同一份清单。
Phase 55 之前，这份清单有**两份手工副本**且**无人核对它们是否相同**：
  * `make_sample.py` 里三处 `os.path.join(a.out_dir, "<字面名>")`；
  * `make_manifest.py::SAMPLE_FILES = (...)`。
这正是 #88 的形态（枚举须派生自事实源）。本模块把它收敛为**一份**。

★ 刻意**只用标准库**：`make_manifest.py` 会导入它，而 `make_manifest.py` 本不需要
  `graph_export` / `source_registry` 那一串重依赖 —— 若为了共享清单而把那些拉进来，
  就会"为了同一个事实源"制造一个新的耦合面（D61 的同族顾虑）。

用法
----
    from pipeline_outputs import SAMPLE_FILENAMES, sample_paths, regenerated
    paths = sample_paths(ROOT)                 # 仓库相对路径（清单/断言一律用相对路径）
    dirty = regenerated(ROOT, paths)           # 与 HEAD 逐字节不同的产物
"""
from __future__ import annotations

import io
import os
import subprocess

# ---------------------------------------------------------------------------
# 产物声明（**唯一事实源**）
#   ① `make_sample.py` 用它命名输出文件 —— 不再各写字面量；
#   ② `make_manifest.py` 用 `SAMPLE_FILENAMES` 登记 artifacts —— 不再各写一份元组；
#   ③ `--check-regenerated` 用它判断"哪些文件属于本流水线自己"。
#   ★ 三者一致性的**反向断言**在 `make_sample.py` 写完产物后当场执行
#     （声明集 ≠ 实际写出集 ⇒ RC=3）：光有"唯一事实源"不够，还要证明它**被用上了**。
# ---------------------------------------------------------------------------
SAMPLE_DIR_REL = "06_PoC/release/sample"
SAMPLE_FILENAMES = ("sample_normalized.json", "sample_graph.json", "sample_queries.json")
MANIFEST_REL = "06_PoC/release/manifest.json"


def sample_paths(repo_root: str, out_dir: str | None = None) -> list[str]:
    """产物的**仓库相对路径**（`out_dir` 给绝对/外部目录时会退化为相对路径串）。"""
    base = out_dir or os.path.join(repo_root, SAMPLE_DIR_REL)
    return [os.path.relpath(os.path.join(base, fn), repo_root).replace(os.sep, "/")
            for fn in SAMPLE_FILENAMES]


def git_blob(repo_root: str, relpath: str) -> bytes | None:
    """取 `HEAD:<relpath>` 的**字节**；不存在（新文件/未入库）⇒ `None`。"""
    try:
        r = subprocess.run(["git", "show", "HEAD:%s" % relpath], cwd=repo_root,
                           capture_output=True)
    except Exception:                                                        # noqa: BLE001
        return None
    return r.stdout if r.returncode == 0 else None


def read_bytes(repo_root: str, relpath: str) -> bytes | None:
    """取工作区文件字节；不存在 ⇒ `None`。"""
    try:
        with io.open(os.path.join(repo_root, relpath), "rb") as f:
            return f.read()
    except OSError:
        return None


def regenerated(repo_root: str, paths, blob_of=None, read_of=None) -> list[str]:
    """返回**与 HEAD 不同**的产物路径。

    ★ `blob_of` / `read_of` 是**为可测性而存在**的注入点：`--check-regenerated` 的
      "判据非真空"守卫（`M9`）用**合成输入**调用本函数，证明"确实不同的会被报出、
      确实相同的不会被报出" —— 否则把判据换成 `return []` 这种"恒真空"退化
      在输出上与"真的没有差异"**完全不可分**（`R11` 探针就是注入这个退化）。
    """
    blob_or_none = blob_of or (lambda rel: git_blob(repo_root, rel))
    read_bytes_of = read_of or (lambda rel: read_bytes(repo_root, rel))
    return [p for p in paths if blob_or_none(p) != read_bytes_of(p)]
