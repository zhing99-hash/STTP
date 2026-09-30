# 公式知识图谱 · Neo4j 部署包

> **角色**：Neo4j 部署专家  
> **日期**：2026-09-28  
> **配套数据集**：`neo4j/`（36 节点 / 52 边，引用完整，0 悬空）

---

## 目录

- [前置条件](#前置条件)
- [一键部署](#一键部署)
- [目录说明](#目录说明)
- [改密码](#改密码)
- [离线模式](#离线模式)
- [常见问题](#常见问题)
- [与 Phase 0–4 的关系](#与-phase-0--4-的关系)
- [如何接前端](#如何接前端)

---

## 前置条件

| 依赖 | 版本 | 说明 |
|------|------|------|
| Docker | 20.10+ | [安装指引](https://docs.docker.com/get-docker/) |
| Docker Compose | V2（内置）或 V1 | `docker compose version` 验证 |
| Python | 3.11+ | 仅 `load_neo4j.py` / `verify_deploy.py` 需要 neo4j 驱动 |
| 内存 | 4GB+ | Neo4j 堆 + 页缓存推荐配置 |

> **Windows**：安装 [Docker Desktop](https://docs.docker.com/desktop/install/windows-install/)（内置 WSL2 后端）后，PowerShell / CMD 直接运行 `python deploy.py`。

---

## 一键部署

```bash
# 1. 进入部署包目录
cd 08_部署包/neo4j

# 2. 预检验证（包内数据集完整性，36/52/0/0 → exit 0）
python preflight_neo4j.py

# 3. 一键部署（自动处理 Docker 检测 / .env 创建 / 容器启动 / 数据导入 / 校验）
python deploy.py
```

预期最终输出：

```
[RESULT] 静态校验全部通过 [OK]（exit 0）：
          节点=36 边=52 重复ID=0 悬空引用=0 表头合法。

部署成功 🎉
  🔗 Browser UI  : http://localhost:7474
  🔗 Bolt 驱动  : bolt://localhost:7687
  🔗 用户名     : neo4j
  🔗 密码       : formula_graph_2026（请改！）
```

---

## 目录说明

```
08_部署包/neo4j/
├── docker-compose.yml       # Neo4j 5.21 + APOC，命名卷，/deploy 挂载
│                            #   端口：7474（Browser）/ 7687（Bolt）
│
├── .env.example             # 环境变量样例
│                            #   默认密码：formula_graph_2026
│
├── load.cypher              # 索引/约束 + 6 类校验（Cypher，幂等）
│                            #   由 deploy.py 通过 docker exec 执行
│
├── neo4j/                   # 内嵌数据集（直接可导入，无需 01tuopu 其它目录）
│   ├── neo4j_ready.json    # 生产就绪 JSON（36 节点 / 52 边）
│   ├── nodes.csv           # neo4j-admin import 用节点表
│   └── relationships.csv   # neo4j-admin import 用关系表
│
├── load_neo4j.py            # 在线 bolt MERGE 写入（APOC/原生双模式）
│   # 缺驱动/不可达 → 友好提示 + exit 0（非致命）
│
├── verify_deploy.py         # 6 类部署后校验
│   # 无实例 → 待部署 + exit 0（非致命）
│
├── preflight_neo4j.py       # 部署前静态校验（CSV 引用完整性）
│   # 检查项：表头/重复ID/悬空引用；exit 0 才通过
│
├── deploy.py                # 跨平台一键编排器（仅标准库 + subprocess）
│                            #   Docker 检测 → .env 创建 → up -d →
│                            #   等待 healthy → 灌数据 → 建索引/约束 → 校验
│
├── deploy.sh                # Linux/Mac/WSL2 便捷包装
│
└── README.md                # 本文档
```

---

## 改密码

### 方式 1：重启容器（推荐）

```bash
# 编辑 .env
nano .env
# 修改：NEO4J_AUTH=neo4j/your_new_password
# 修改：NEO4J_PASSWORD=your_new_password

# 重启容器
docker compose down && docker compose up -d
```

### 方式 2：连上后用 Cypher 改

```cypher
-- Neo4j Browser（http://localhost:7474）执行：
ALTER CURRENT USER SET PASSWORD FROM 'formula_graph_2026' TO 'your_new_password';
```

---

## 离线模式

在没有 Docker 的机器上，用 `--offline` 验证包完整性 + 获取离线导入命令：

```bash
python deploy.py --offline
```

输出示例（neo4j-admin 命令）：

```bash
neo4j-admin database import full formula-graph \
  --nodes=neo4j/nodes.csv \
  --relationships=neo4j/relationships.csv \
  --delimiter=, \
  --array-delimiter=";" \
  --id-type=STRING \
  --skip-duplicate-nodes=true \
  --overwrite-destination=true
```

---

## 常见问题

### Q: 部署到 Neo4j Aura（云端免费实例）？

Aura 免费实例用**自签名证书**，Python driver 必须用 `ssc` 后缀 URI：

```bash
# 修改 .env
NEO4J_URI=neo4j+ssc://<your-instance-id>.databases.neo4j.io
NEO4J_USER=<your-instance-id>      # Aura 免费实例的 username == instance ID
NEO4J_PASSWORD=<从下载的 .txt 获取>
NEO4J_DATABASE=<your-instance-id>  # Aura 免费实例的 database == instance ID

# 灌数据 + 校验
python load_neo4j.py
python verify_deploy.py
```

> ⚠️ 如果用 `neo4j+s://`（无 ssc），driver 会因证书验证失败返回 `Unable to retrieve routing information`，但 Aura Browser 能正常访问——因为 Browser 走 Aura WebSocket gateway 不验证客户端证书。

### Q: `docker: command not found`

请先安装 Docker Desktop（Windows/macOS）或 docker.io（Linux）。详见[前置条件](#前置条件)。

### Q: `docker compose up` 卡住

可能是在拉取镜像，耐心等待。如超过 5 分钟可 Ctrl+C 取消，手动先拉镜像：

```bash
docker pull neo4j:5.21
docker compose up -d
```

### Q: 容器一直是 `starting` 状态

内存不足是常见原因。请确保 Docker Desktop 分配了 4GB+ 内存（Docker Desktop → Settings → Resources）。

### Q: `python load_neo4j.py` 报 `SKIP`

正常行为。表示 neo4j Python 驱动未安装或实例不可达（exit 0，不影响其它脚本）。在容器健康后重跑即可：

```bash
python load_neo4j.py
```

### Q: 端口 7474 / 7687 被占用

```bash
# Windows
netstat -ano | findstr "7474 7687"

# Linux/macOS
lsof -i :7474 -i :7687
```

修改 `docker-compose.yml` 的端口映射后重新 `up` 即可。

### Q: 验证查询 gauss_bonnet→manifold 路径为空

可能是边未完全加载。重新执行：

```bash
python load_neo4j.py
docker exec formula-graph-neo4j cypher-shell -u neo4j -p formula_graph_2026 -f /deploy/load.cypher
```

---

## 与 Phase 0–4 的关系

| Phase | 产出 | 与部署包的关系 |
|-------|------|---------------|
| Phase 0 | `AGENTS.md` / `MEMORY.md` | Agent 身份与工具链 |
| Phase 1 | `02_结构层/`（实体类型 / JSON Schema） | 图谱节点类型体系 |
| Phase 2 | `03_知识层/normalized.json`（22 节点） | 原始图谱数据 |
| Phase 3 | `05_验证层/`（原子守恒 / 量纲齐次 / 符号等式验证） | 8 条 LLM 推断边的 R-CHEM / R-PHY / R-MATH 验证 |
| Phase 3.5 | `06_PoC/etl/neo4j/`（nodes.csv / relationships.csv） | 直接导入 Neo4j 的 CSV 数据 |
| **Phase 3.7** | **`08_部署包/neo4j/`**（本包） | **自包含一键部署包，内嵌完整数据集** |
| Phase 4 | `queries.py` / `viz_server.py` | 前端可视化，接 Neo4j 后端 |

---

## 如何接前端

前端 `06_PoC/viz_server.py` 通过 `queries.get_backend("auto")` 自动选后端：

```
Neo4j 可达（bolt://localhost:7687）
  → 走 Cypher 查询（精准、批量）
  → 优先展示

Neo4j 不可达
  → 回退 NetworkX 兜底（与 Neo4j 数据同源，来自 normalized.json）
```

**部署 Neo4j 后，前端无需任何改动**：

```bash
cd 01tuopu/06_PoC
python viz_server.py --port 8765
# 浏览器打开 http://127.0.0.1:8765/
```

`queries.py` 中的 6 个查询接口（`get_node` / `get_neighbors` / `subgraph_by_confidence` / `paths_between` / `list_by_type` / `filter_edges_by_kind`）已与 Neo4j 后端对齐。

---

## 复现

```bash
cd 08_部署包/neo4j
python preflight_neo4j.py      # 包内数据自检 36/52/0/0（exit 0）
python deploy.py               # 本机一键：起 Neo4j + 灌图 + 校验（需 Docker）
```

---

*部署包由 公式知识图谱项目 · Neo4j 部署专家 生成 | 2026-09-28*
