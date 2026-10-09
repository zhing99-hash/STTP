# 本项目脚本用 os.environ 读取凭据，不自动加载 .env。
# 因此提供两个加载器，先 source 再运行脚本。
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
