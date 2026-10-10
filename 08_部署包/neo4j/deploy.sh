#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

# ============================================================================
# 公式知识图谱 · Neo4j 部署便捷脚本（deploy.sh）
# Linux / macOS / WSL2 使用
# ============================================================================
# 用法：
#   bash deploy.sh           # 在线一键部署
#   bash deploy.sh --offline # 仅打印离线导入命令
#   bash deploy.sh --env .env.custom  # 指定 .env
#
# Windows：直接运行 python deploy.py（无需 bash）
# ============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "【公式知识图谱 · Neo4j 部署包】"
echo "  工作目录: $SCRIPT_DIR"
echo "  Python  : $(python3 --version 2>&1 || python --version 2>&1)"
echo ""

# 把 PYTHONIOENCODING 设为 utf-8，避免中文输出乱码
export PYTHONIOENCODING=utf-8

cd "$SCRIPT_DIR"

python3 deploy.py "$@" 2>&1
