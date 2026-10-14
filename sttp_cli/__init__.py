# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""STTP 命令行入口薄封装（Phase 45 · A05）。

★ 本包**不包含任何图谱逻辑**：它只做三件事 ——
① 定位仓库根（**无任何机器绝对路径**）；
② 用**当前解释器**（`sys.executable`）与 `bash` 转发到既有脚本；
③ 报告「两级可复现等级」（随包样例重放 / 原始源重建）的可判定状态。

唯一的**效果权威**仍是 `sttp.sh` 与 `06_PoC/**` 下的既有脚本；
本包只提供「装了包之后有命令可用」这一层便利（未安装时用 `bash sttp.sh` 完全等价）。
"""

from .cli import (  # noqa: F401
    find_root,
    main,
    release_verify,
    reproducibility,
    repro_level,
    sample_build,
    sample_serve,
    sample_verify,
)

__all__ = [
    "find_root",
    "main",
    "release_verify",
    "reproducibility",
    "repro_level",
    "sample_build",
    "sample_serve",
    "sample_verify",
]

__version__ = "0.45.0"
