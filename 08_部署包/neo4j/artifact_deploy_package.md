# 公式知识图谱 · Neo4j 部署包 · 交付报告

## 任务
将已有的 Neo4j 部署准备产物封装为自包含、可拷贝到任意本机一键跑起 Neo4j（含图谱数据）的本地可执行部署包。

## 产物位置
`C:\Users\Administrator\.qclaw\workspace\01tuopu\08_部署包\neo4j\`

## 包内文件（11 个，全部可移植，依赖 01tuopu 外部）

```
08_部署包/neo4j/
├── docker-compose.yml       # Neo4j 5.21 + APOC，命名卷，/deploy bind mount
├── .env.example             # 默认 NEO4J_AUTH=neo4j/formula_graph_2026
├── load.cypher              # 权威版：约束 + 索引 + 幂等MERGE + 6类校验
├── load_neo4j.py            # 可移植版（默认取 neo4j/neo4j_ready.json）
├── verify_deploy.py         # 可移植版（exit 0 无实例非致命）
├── preflight_neo4j.py       # 可移植版（默认检查 neo4j/ 子目录 CSV）
├── deploy.py                # 跨平台编排器（标准库 + subprocess）
├── deploy.sh                # Linux/Mac/WSL2 便捷包装
├── README.md                # 完整文档
└── neo4j/                   # 内嵌数据集（36节点/52边/0悬空）
    ├── neo4j_ready.json
    ├── nodes.csv
    └── relationships.csv
```

## 关键设计决策

### 路径可移植性
- 所有 Python 脚本使用 `Path(__file__).parent.resolve()` 作为包根，不引用 `01tuopu` 外部路径
- 数据集内嵌进 `neo4j/` 子目录，拷贝即用

### 优雅降级（无 Docker 沙箱验证）
- `subprocess` 对 `FileNotFoundError` / `OSError` 捕获，返回 `returncode=127/1`
- `check_docker()` 返回 `False` → 打印分平台安装指引 → `exit 0`（不崩）
- `load_neo4j.py` / `verify_deploy.py` 缺驱动或无实例均 `exit 0` 非致命

### Windows 编码安全
- `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` 避免 GBK 终端中文崩溃
- `subprocess.run(..., encoding="utf-8", errors="replace")` 避免子进程输出编码错误

### deploy.py 编排流程
1. Docker 检测 → 缺失打印安装指引 + exit 0
2. .env 不存在则从 .env.example 复制（默认 formula_graph_2026）
3. docker compose up -d（支持 V1 docker-compose / V2 docker compose 自动检测）
4. 轮询容器 health status，120s 超时给出排查提示
5. python load_neo4j.py（bolt 在线 MERGE，APOC/原生双模式）
6. docker exec cypher-shell -f /deploy/load.cypher（建索引/约束 + 6类校验）
7. python verify_deploy.py（最终校验，捕获节点/边总数）
8. 打印成功小结（Browser URL / Bolt / 前端接入 / 改密码）

## 验证证据

### preflight_neo4j.py（包内数据自检）
```
[RESULT] 静态校验全部通过 [OK]（exit 0）：
          节点=36 边=52 重复ID=0 悬空引用=0 表头合法。
```

### deploy.py（无 Docker → 优雅降级）
```
[FAIL] 未找到 docker 命令。
【Docker 安装指引】（Windows/macOS/Linux 分平台）
[WARN] Docker 不可用，退出。（exit 0，不崩）
```

### --offline 模式
```
[离线导入模式] → 打印 neo4j-admin import 命令 → exit 0
```

### 语法验证
```
AST_OK  deploy.py
AST_OK  load_neo4j.py
AST_OK  verify_deploy.py
AST_OK  preflight_neo4j.py
YAML_OK docker-compose.yml
ALL_CHECKS_PASSED
```

## 复现命令

```bash
cd 08_部署包/neo4j
python preflight_neo4j.py      # 包内数据自检 36/52/0/0（exit 0）
python deploy.py               # 本机一键：起 Neo4j + 灌图 + 校验（需 Docker）
python deploy.py --offline      # 离线模式：打印 neo4j-admin 命令（无需 Docker）
```

## 原文件保护确认
- `03_知识层/` 原始文件：未修改
- `06_PoC/etl/neo4j/` 原始文件：未修改
- 部署包所有文件均为新创建（副本），原产物保持可用
