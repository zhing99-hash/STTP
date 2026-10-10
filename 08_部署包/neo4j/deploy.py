# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""
公式知识图谱 · Neo4j 部署编排器（deploy.py）
================================================================

跨平台一键部署 Neo4j + 图谱数据 + 索引/约束 + 校验。

功能
----
1. 检测 Docker（缺失 → 打印安装指引 + exit 0，不崩）
2. 复制 .env.example → .env（如无 .env）
3. docker compose up -d 起容器
4. 轮询容器 health status，超时 120s 给出排查提示
5. python load_neo4j.py 在线写入
6. cypher-shell -f /deploy/load.cypher 建索引/约束 + 6 类校验
7. python verify_deploy.py 最终校验并打印节点/边总数
8. 打印成功小结（Browser URL、Bolt URL、如何接前端、如何改密码）

离线模式（--offline）
----
仅打印 neo4j-admin import 完整命令，不起容器、不写库。

前置条件
--------
- Docker 20.10+ & Docker Compose V2
- Python 3.11+（仅 load_neo4j.py / verify_deploy.py 需要 neo4j 驱动）
- 4GB+ 可用内存

运行
----
    cd 08_部署包/neo4j
    python deploy.py                    # 在线一键部署
    python deploy.py --offline          # 仅打印离线导入命令
    python deploy.py --env .env.custom  # 使用自定义 .env 文件
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# 跨平台 stdout UTF-8：Windows CMD/PowerShell 默认 GBK，强制 UTF-8 避免编码错误
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# ── 可移植路径解析（相对于本脚本所在目录）─────────────────────────
PKG_DIR = Path(__file__).parent.resolve()
DOCKER_COMPOSE_FILE = PKG_DIR / "docker-compose.yml"
ENV_EXAMPLE = PKG_DIR / ".env.example"
ENV_FILE = PKG_DIR / ".env"
LOAD_PY = PKG_DIR / "load_neo4j.py"
VERIFY_PY = PKG_DIR / "verify_deploy.py"
LOAD_CYPHER = PKG_DIR / "load.cypher"
CONTAINER_NAME = "formula-graph-neo4j"

HEALTH_TIMEOUT = 120   # 秒：等待容器变 healthy 的超时


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def run(cmd: list[str], cwd: Optional[Path] = None,
        capture: bool = True, check: bool = False,
        env: Optional[dict] = None) -> subprocess.CompletedProcess:
    """跨平台 subprocess.run 封装（Windows/Mac/Linux 通用）。

    FileNotFoundError（命令不存在）返回 returncode=127，不抛异常。
    """
    try:
        return subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=capture,
            text=True,
            encoding="utf-8",
            errors="replace",  # 替换无法编码的字符，避免 Windows GBK 终端崩溃
            check=check,
            env=env,
        )
    except FileNotFoundError:
        # 命令不存在（Windows/Linux/macOS 均抛此异常），返回等价于 returncode=127
        import io
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=127,
            stdout="" if capture else None,
            stderr=f"[FileNotFoundError] command not found: {cmd[0]}"
        )
    except OSError as e:
        # Windows 上 docker.exe 在 PATH 但加载失败等
        import io
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout="" if capture else None,
            stderr=f"[OSError] {e}"
        )


def print_step(msg: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {msg}")
    print('=' * 60)


def print_ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def print_warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def print_fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


# ═══════════════════════════════════════════════════════════════
# 步骤 0：检测 Docker
# ═══════════════════════════════════════════════════════════════

def check_docker() -> bool:
    """检测 docker 与 docker compose 是否可用。不可用 → 打印安装指引 + return False。"""
    print_step("检测 Docker 环境")

    # 检测 docker CLI
    r_docker = run(["docker", "--version"], capture=True)
    if r_docker.returncode != 0:
        print_fail("未找到 docker 命令。")
        print_install_guide()
        return False
    print_ok(f"Docker CLI: {r_docker.stdout.strip()}")

    # 检测 docker compose（V2 内置插件）或 docker-compose（V1 独立工具）
    r_compose = run(["docker", "compose", "version"], capture=True)
    if r_compose.returncode != 0:
        # 尝试旧版 docker-compose
        r_compose = run(["docker-compose", "--version"], capture=True)
        if r_compose.returncode != 0:
            print_fail("未找到 docker compose 命令（Docker Compose V2 或 docker-compose V1）。")
            print_install_guide()
            return False
        print_ok(f"Docker Compose V1: {r_compose.stdout.strip()}")
    else:
        print_ok(f"Docker Compose V2: {r_compose.stdout.strip()}")

    return True


def print_install_guide() -> None:
    print("""
  ═══════════════════════════════════════════════════════
  【Docker 安装指引】
  ═══════════════════════════════════════════════════════

  Windows:
    1. 安装 WSL2（管理员 PowerShell）：wsl --install
    2. 安装 Docker Desktop：https://docs.docker.com/desktop/install/windows-install/
    3. 启动 Docker Desktop，确保 WSL2 后端已启用
    4. 重启终端后再次运行 python deploy.py

  macOS:
    1. 安装 Docker Desktop：https://docs.docker.com/desktop/install/mac-install/
    2. 启动 Docker Desktop
    3. 终端里再次运行 python deploy.py

  Linux (Ubuntu/Debian):
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
    newgrp docker        # 使组成员资格生效
    # 或安装 docker-compose：
    sudo apt install docker-compose

  完成后验证：
    docker --version
    docker compose version
    docker run --rm hello-world

  ═══════════════════════════════════════════════════════
  提示：如暂时无法安装 Docker，可用离线模式验证部署包完整性：
    python deploy.py --offline
  ═══════════════════════════════════════════════════════
""")


# ═══════════════════════════════════════════════════════════════
# 步骤 1：确保 .env 存在
# ═══════════════════════════════════════════════════════════════

def ensure_env(env_path: Optional[Path] = None) -> None:
    """无 .env 则从 .env.example 复制（提供默认账号密码）。"""
    target = env_path or ENV_FILE
    if target.exists():
        print_ok(f".env 已存在（{target}），跳过复制。")
        return
    if not ENV_EXAMPLE.exists():
        print_warn(f".env.example 不存在，跳过自动创建。请手动创建 {target}。")
        return
    shutil.copy2(ENV_EXAMPLE, target)
    print_ok(f"已从 .env.example 复制为 {target}")
    print("  默认账号：neo4j / formula_graph_2026")
    print("  请编辑 .env 改为您自己的密码（至少 6 位）：")
    print(f"    NEO4J_AUTH=neo4j/your_new_password")
    print(f"    NEO4J_PASSWORD=your_new_password")


# ═══════════════════════════════════════════════════════════════
# 步骤 2：拉起容器
# ═══════════════════════════════════════════════════════════════

def docker_compose_up(env_file: Optional[Path] = None) -> None:
    """docker compose up -d。返回是否成功。"""
    print_step("启动 Neo4j 容器")

    # 检测使用 V1 还是 V2 compose 命令
    r = run(["docker", "compose", "version"], capture=True)
    if r.returncode == 0:
        compose_cmd = ["docker", "compose"]
    else:
        compose_cmd = ["docker-compose"]

    env_arg: Optional[list[str]] = None
    if env_file and env_file.exists():
        env_arg = ["--env-file", str(env_file)]

    cmd = compose_cmd + ["-f", str(DOCKER_COMPOSE_FILE), "up", "-d"]
    if env_arg:
        cmd = cmd[:1] + env_arg + cmd[1:]

    print(f"  执行：{' '.join(cmd)}")
    r = run(cmd, capture=True)
    if r.returncode != 0:
        print_fail(f"docker compose up 失败：\n{r.stderr}")
        sys.exit(1)
    print_ok("容器已启动（docker compose up -d 成功）")
    if r.stdout:
        print(r.stdout.strip())


def docker_compose_down() -> None:
    """docker compose down（清理）。"""
    r = run(["docker", "compose", "-f", str(DOCKER_COMPOSE_FILE), "down"],
            capture=True)
    if r.returncode == 0:
        print_ok("容器已停止并清理（docker compose down 成功）")


# ═══════════════════════════════════════════════════════════════
# 步骤 3：等待健康
# ═══════════════════════════════════════════════════════════════

def wait_healthy(timeout: int = HEALTH_TIMEOUT) -> bool:
    """轮询容器 health status，直到 healthy 或超时。"""
    print_step("等待容器变为 healthy（约 30-90 秒，请耐心等待……）")

    interval = 5
    elapsed = 0
    while elapsed < timeout:
        time.sleep(interval)
        elapsed += interval
        r = run(["docker", "inspect", "--format",
                 "{{.State.Health.Status}}", CONTAINER_NAME], capture=True)
        if r.returncode == 0:
            status = r.stdout.strip()
            print(f"  [{elapsed}s] 容器状态: {status}")
            if status == "healthy":
                print_ok("容器已就绪！")
                return True
        else:
            print_warn(f"[{elapsed}s] 容器尚未启动……")

    # 超时：给出排查提示
    print_fail(f"等待超时（>{timeout}s）")
    print("""
  排查步骤：
    1) docker compose -f docker-compose.yml logs neo4j   # 查看启动日志
    2) docker compose ps                                  # 查看容器状态
    3) 确认机器有 4GB+ 内存，Docker Desktop 已启动
    4) 若端口 7474/7687 被占用：
         netstat -ano | findstr "7474 7687"
         # 或改 docker-compose.yml 的端口映射
    5) 若首次启动拉取镜像过慢，可先手动：
         docker pull neo4j:5.21
""")
    return False


# ═══════════════════════════════════════════════════════════════
# 步骤 4：灌数据（load_neo4j.py）
# ═══════════════════════════════════════════════════════════════

def load_data(env_vars: dict) -> None:
    """运行 load_neo4j.py 在线写入图谱数据。"""
    print_step("写入图谱数据（load_neo4j.py）")

    # 读取 .env 中的密码
    local_env = dict(os.environ)
    local_env.update(env_vars)

    r = run([sys.executable, str(LOAD_PY)], cwd=PKG_DIR,
            capture=True, env=local_env)
    # 非致命：驱动缺失或实例不可达也 exit 0
    if r.stdout:
        print(r.stdout)
    if r.returncode != 0 and "SKIP" not in r.stdout:
        print_warn(f"load_neo4j.py 返回 {r.returncode}，继续执行后续步骤……")
    else:
        print_ok("图谱数据写入完成（load_neo4j.py 退出 0）")


# ═══════════════════════════════════════════════════════════════
# 步骤 5：建索引/约束 + 校验（cypher-shell via docker exec）
# ═══════════════════════════════════════════════════════════════

def run_load_cypher(password: str) -> None:
    """docker exec cypher-shell -f /deploy/load.cypher 建索引/约束 + 校验。"""
    print_step("建索引/约束 + 6 类校验（cypher-shell -f /deploy/load.cypher）")

    cmd = [
        "docker", "exec", "-i", CONTAINER_NAME,
        "cypher-shell", f"-u", "neo4j", f"-p", password,
        "-f", "/deploy/load.cypher"
    ]
    print(f"  执行：docker exec ... cypher-shell -f /deploy/load.cypher")
    r = run(cmd, capture=True)
    if r.stdout:
        print(r.stdout)
    if r.returncode != 0:
        print_warn(f"cypher-shell 返回 {r.returncode}")
        if r.stderr:
            print_warn(f"stderr: {r.stderr}")
    else:
        print_ok("load.cypher 执行成功（索引/约束已建立，6 类校验查询已打印）")


# ═══════════════════════════════════════════════════════════════
# 步骤 6：最终校验（verify_deploy.py）
# ═══════════════════════════════════════════════════════════════

def verify(env_vars: dict) -> None:
    """运行 verify_deploy.py 并捕获节点/边总数。"""
    print_step("最终校验（verify_deploy.py）")

    local_env = dict(os.environ)
    local_env.update(env_vars)

    r = run([sys.executable, str(VERIFY_PY)], cwd=PKG_DIR,
            capture=True, env=local_env)
    if r.stdout:
        print(r.stdout)
    if r.returncode != 0:
        print_warn(f"verify_deploy.py 返回 {r.returncode}")
    else:
        print_ok("verify_deploy.py 校验完成")


# ═══════════════════════════════════════════════════════════════
# 步骤 7：打印成功小结
# ═══════════════════════════════════════════════════════════════

def print_success(password: str) -> None:
    """打印部署成功信息。"""
    print_step("部署成功 🎉")
    print("""
  ═══════════════════════════════════════════════════════════
  【Neo4j 图数据库已就绪】
  ═══════════════════════════════════════════════════════════

  🔗 连接信息
     Browser UI  : http://localhost:7474
     Bolt 驱动  : bolt://localhost:7687
     用户名     : neo4j
     密码       : （您在 .env 中设置的密码）

  📊 图谱数据
     节点总数   : 36（含 14 个合成节点：物理量/分子/数学概念）
     边总数     : 52（含 8 条 LLM 推断 + 原子守恒/量纲齐次验证边）
     标签类型   : Entity, Formula, Definition, Theorem, Lemma,
                  Symbol, PhysicalQuantity, Molecule, MathConcept
     关系类型   : derived_from, defines, proves, has_symbol,
                  dimensionally_consistent, chemical_reaction

  🧠 校验查询示例（在 Browser 执行）
     MATCH (n:Entity) RETURN count(n) AS 节点数;
     MATCH (a:Entity {id:'MX:thm:gauss_bonnet'})-[:*1..6]->(b:Entity)
              RETURN [n IN nodes(p) | n.id] AS path LIMIT 5;

  🚀 如何接前端（viz_server.py，无需改动）
     cd STTP/06_PoC
     python viz_server.py --port 8765
     # queries.py 的 get_backend("auto") 会自动检测 Neo4j，
     # 可达则走 Cypher；不可达则回退 NetworkX 兜底。

  🔐 如何改密码
     # 方式 1：改 .env 后重启容器
     #   编辑 .env:  NEO4J_AUTH=neo4j/newpassword
     #   docker compose down && docker compose up -d

     # 方式 2：连上后用 Cypher 改
     #   :server user password   # Neo4j Browser 交互式
     #   ALTER CURRENT USER SET PASSWORD FROM 'old' TO 'new';

  🛠  常用运维命令
     docker compose ps            # 查看容器状态
     docker compose logs -f      # 实时日志
     docker compose down         # 停止并清理
     docker compose up -d         # 重新启动
     docker exec -it formula-graph-neo4j cypher-shell -u neo4j -p xxx
                               # 进入容器交互式 Cypher

  📦 部署包结构
     08_部署包/neo4j/
     ├── docker-compose.yml      # Neo4j 5.21 + APOC，命名卷，/deploy 挂载
     ├── .env.example            # 环境变量样例（默认密码 formula_graph_2026）
     ├── load.cypher             # 索引/约束 + 6 类校验（Cypher）
     ├── neo4j/                  # 内嵌数据集（36 节点 / 52 边）
     │   ├── neo4j_ready.json    # 生产就绪 JSON（来源：06_PoC/etl/）
     │   ├── nodes.csv           # neo4j-admin import 用节点表
     │   └── relationships.csv   # neo4j-admin import 用关系表
     ├── load_neo4j.py           # 在线 bolt MERGE 写入（APOC/原生双模式）
     ├── verify_deploy.py        # 6 类校验（缺驱动/无实例 exit 0）
     ├── preflight_neo4j.py      # 部署前静态校验（CSV 引用完整性）
     ├── deploy.py               # 一键编排器（本文件）
     └── deploy.sh               # Linux/Mac 便捷包装
  ═══════════════════════════════════════════════════════════
""")


# ═══════════════════════════════════════════════════════════════
# 离线模式
# ═══════════════════════════════════════════════════════════════

def offline_mode() -> None:
    """仅打印离线导入命令，不起容器。"""
    print_step("离线导入模式（--offline）")
    r = run([sys.executable, str(LOAD_PY), "--admin-import"], cwd=PKG_DIR,
            capture=True)
    if r.stdout:
        print(r.stdout)
    if r.returncode != 0:
        print_warn(f"load_neo4j.py --admin-import 返回 {r.returncode}")
    print("""
  ═════════════════════════════════════════════════════════════
  【离线部署步骤】
  1) 在有 JVM 的主机上安装 Neo4j 5.x Community/Enterprise
  2) 把部署包拷贝到该主机，解压
  3) 参考上方命令执行 neo4j-admin import
  4) 启动 Neo4j 服务
  5) 执行 cypher-shell -f load.cypher 建索引/约束
  6) pip install neo4j && python verify_deploy.py 校验
  ═════════════════════════════════════════════════════════════
""")
    sys.exit(0)


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(
        description="公式知识图谱 · Neo4j 一键部署编排器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--offline", action="store_true",
                    help="仅打印 neo4j-admin 离线导入命令，不起容器")
    ap.add_argument("--env", type=str,
                    help="指定 .env 文件路径（默认使用包内 .env）")
    args = ap.parse_args()

    print_step("公式知识图谱 · Neo4j 部署包")
    print(f"  包目录 : {PKG_DIR}")
    print(f"  Python : {sys.version.split()[0]}")

    # ── 离线模式 ──
    if args.offline:
        offline_mode()

    # ── 步骤 0：Docker 检测 ──
    if not check_docker():
        print_warn("Docker 不可用，退出。")
        print("提示：可用 python deploy.py --offline 验证包内数据集完整性。")
        sys.exit(0)

    # ── 步骤 1：.env ──
    env_path = Path(args.env) if args.env else None
    ensure_env(env_path)

    # ── 读取 .env 中的连接信息 ──
    env_vars: dict = {}
    env_file_to_read = env_path or ENV_FILE
    if env_file_to_read.exists():
        for line in env_file_to_read.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                env_vars[key.strip()] = val.strip()

    default_password = env_vars.get("NEO4J_PASSWORD", "formula_graph_2026")
    for k in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE"):
        if k not in env_vars and k in os.environ:
            env_vars[k] = os.environ[k]

    # ── 步骤 2：起容器 ──
    docker_compose_up(env_path)

    # ── 步骤 3：等待健康 ──
    if not wait_healthy():
        print_warn("容器未达到 healthy 状态，但继续尝试写入……")

    # ── 步骤 4：灌数据 ──
    load_data(env_vars)

    # ── 步骤 5：建索引/约束 + 校验 ──
    run_load_cypher(default_password)

    # ── 步骤 6：最终校验 ──
    verify(env_vars)

    # ── 步骤 7：成功小结 ──
    print_success(default_password)


if __name__ == "__main__":
    main()
