# 本项目脚本用 os.environ 读取凭据，不自动加载 .env。
# 因此提供两个加载器，先 source 再运行脚本。
#
# --- 先建 .env（Phase 45 · A05 起提供根级模板）---
#   cp .env.example .env      # 根级模板：Neo4j / GRAPH_DATA_FILE / VIZ_PORT / STTP_PYTHON
#                             # ★ 模板内不含任何机器绝对路径；STTP_PYTHON 留空即用 PATH 里的 python
#
# --- bash / Git Bash ---
#   source env.sh
#   python 08_部署包/neo4j/verify_deploy.py
#
# --- PowerShell ---
#   . .\env.ps1
#   python 08_部署包/neo4j/verify_deploy.py
#
# --- 不加载环境变量也可以 ---
#   bash sttp.sh check     # 内置加载 + 连通性自检
#   bash sttp.sh viz       # 内置加载 + 启动可视化
#   bash sttp.sh install   # 校验可安装入口与依赖声明（A05 自检）
