# 公式知识图谱 · Neo4j 部署手册（neo4j_deploy.md）

> 角色：Neo4j 部署专家 ｜ 日期：2026-09-28
> 配套数据集：`06_PoC/etl/neo4j/`（36 节点 / 52 边，引用完整，0 悬空）
> 配套脚本：`build_neo4j_ready.py` · `preflight_neo4j.py` · `load_neo4j.py` · `verify_deploy.py` · `load.cypher` · `docker-compose.yml`

---

## 0. 本手册解决的问题

Phase 3 的 8 条 LLM 验证边中，有 7 条引用了 14 个**外部实体**
（`MX:phy:*` 物理量 / `MX:chem:*` 分子·元素 / `MX:math:*` 数学概念 /
`MX:sym:*` 符号），它们未被收录进节点表，直接用 `neo4j-admin import` 会因悬空
`:END_ID/:START_ID` 失败。

`build_neo4j_ready.py` 已把这些缺失端点**注册为正式节点**（type 由 id 前缀推断），
产出真正可导入的 Neo4j 数据集：**36 节点 / 52 边 / 14 合成 / 0 悬空**。
`preflight_neo4j.py` 已静态验证该数据集（详见本报告 §4 证据）。

---

## 1. 前提条件（部署主机）

- **Java 17**（Neo4j 5.x 要求；离线 `neo4j-admin import` 与运行 Neo4j 都需要 JVM）。
- **Docker 20.10+**（推荐容器化部署；也可本机直接装 Neo4j 5.x）。
- Python 3.11 + `neo4j` 驱动（仅在线 `bolt` 写库/校验需要）：
  `pip install "neo4j>=5.0"`。
- 本仓库已就绪：`06_PoC/etl/neo4j/{nodes.csv,relationships.csv,neo4j_ready.json}`、
  `03_知识层/{load_neo4j.py,load.cypher,docker-compose.yml,.env.example}`。

> 沙箱（本环境）**无 Java / Docker**，故上述产物已做齐并通过静态校验，但真实
> Neo4j 实例的“实际连接验证”需在有 JVM 的部署主机上执行（见 §6 冒烟）。

---

## 2. 步骤 (1) 起容器

```bash
cd 01tuopu/03_知识层
cp .env.example .env            # 按需修改 NEO4J_PASSWORD 等
docker compose up -d            # 启动 Neo4j 5.21 + APOC
```

容器名：`formula-graph-neo4j`；端口：`7474`（Browser/HTTP）、`7687`（Bolt 驱动）。

---

## 3. 步骤 (2) 等 healthy

```bash
docker compose ps               # STATUS 变为 healthy 后再继续
docker compose logs -f neo4j    # 可选：观察启动日志，看到 "Started" 即可
```

`docker-compose.yml` 已内置 healthcheck（用容器内 `cypher-shell` 探活，
`start_period: 90s`）。首次启动建库可能需要 30–60 秒。

---

## 4. 步骤 (3) 导入数据

### 方式 A（推荐，离线大批量）：`neo4j-admin import`

1. 把 CSV 复制到 Neo4j 的 import 目录：

   ```bash
   # 容器 import 目录默认挂载在容器内 /var/lib/neo4j/import
   docker cp 06_PoC/etl/neo4j/nodes.csv          formula-graph-neo4j:/var/lib/neo4j/import/
   docker cp 06_PoC/etl/neo4j/relationships.csv  formula-graph-neo4j:/var/lib/neo4j/import/
   ```

2. 进入容器执行离线导入（community 需先停库；容器单机可直接 import 到新库）：

   ```bash
   docker exec -it formula-graph-neo4j \
     neo4j-admin database import full formula-graph \
       --nodes=import/nodes.csv \
       --relationships=import/relationships.csv \
       --delimiter=, --array-delimiter=";" --id-type=STRING \
       --skip-duplicate-nodes=true --overwrite-destination=true
   docker exec -it formula-graph-neo4j \
     neo4j-admin database set-default formula-graph   # 5.x 单机设为默认库
   ```

   说明：`nodes.csv` 的 `:LABEL` 用 `;` 分隔多 Label（含通用 `Entity`）；
   关系 `:START_ID/:END_ID` 为 STRING 且已保证 **0 悬空**，可直接导入。

### 方式 B（在线，小批量 / 调试）：`load_neo4j.py`

```bash
# 已设 NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD（来自 .env）
python load_neo4j.py                       # 默认载入 neo4j_ready.json（36/52）
# 或显式指定：
python load_neo4j.py --input ../06_PoC/etl/neo4j/neo4j_ready.json
```

脚本会：建约束/索引 → APOC 可用则 `apoc.merge.*`、否则原生 `MERGE` → 跑校验查询。
驱动缺失 / 实例不可达时**非致命退出（exit 0）**，不崩。

> 仅打印离线命令（不连接）可用：`python load_neo4j.py --admin-import`

---

## 5. 步骤 (4) 建索引 / 约束

离线导入（方式 A）只建了图，还需约束/索引与可选在线 MERGE 补全。用
`cypher-shell` 执行 `load.cypher`（含幂等 `CREATE CONSTRAINT/INDEX` + 校验查询）：

```bash
# 若用方式 A，先把 CSV 复制进 import 目录后执行（C 段 LOAD CSV 可选）：
docker exec -i formula-graph-neo4j \
  cypher-shell -u neo4j -p "$NEO4J_PASSWORD" -d formula-graph < load.cypher
# 若用方式 B，则只跑约束/索引与校验（C 段可跳过，因数据已由 load_neo4j.py 写入）：
docker exec -i formula-graph-neo4j \
  cypher-shell -u neo4j -p "$NEO4J_PASSWORD" -d formula-graph < load.cypher
```

`load.cypher` 含：`entity_id` 唯一约束、`entity_ntype` / `entity_domain` 索引，
以及 6 类校验查询（节点/边总数、Theorem 列表、低置信推断边、gauss_bonnet→manifold
路径、类型统计）。

---

## 6. 步骤 (5) 冒烟 + 校验查询

```bash
python verify_deploy.py          # 6 类校验（需 NEO4J_URI 可达）
```

预期输出：`[1] 节点总数: 36`、`[2] 边总数: 52`、Theorem 列表非空、
低置信推断边若干、`gauss_bonnet→manifold` 路径非空、类型统计含
`theorem/definition/lemma/symbol/molecule/physical_quantity/math_concept` 等。

也可在 Neo4j Browser（`http://localhost:7474`）直接跑 `load.cypher` 的 D 段查询。

---

## 7. 步骤 (6) 前端对接（viz_server）

前端可视化服务 `06_PoC/viz_server.py` 通过 `queries.get_backend("auto")` 自动选后端：
Neo4j 可达时走 Cypher，否则回退 NetworkX 兜底。部署 Neo4j 后**前端无需改动**：

```bash
cd 01tuopu/06_PoC
python viz_server.py --port 8765
# 浏览器打开 http://127.0.0.1:8765/
```

`queries.py` / `queries.cypher` 中的 6 个查询接口（`get_node` / `get_neighbors`
`subgraph_by_confidence` / `paths_between` / `list_by_type` / `filter_edges_by_kind`）
已与 Neo4j 后端对齐，可直接服务前端。

---

## 8. 复现（给部署主机的一行流）

```bash
cd 01tuopu/03_知识层
python build_neo4j_ready.py      # 生成 06_PoC/etl/neo4j/*.csv（36 节点/52 边）
python preflight_neo4j.py        # 静态校验：0 重复 / 0 悬空
# 在含 JVM 主机：
docker compose up -d             # 起 Neo4j
python load_neo4j.py             # 在线 bolt 写入（或用 --admin-import 离线命令）
cypher-shell -f load.cypher      # 建索引/约束 + 校验
python verify_deploy.py          # 部署后校验
```

---

## 9. 已知限制

1. 本沙箱无 Java / Docker，无法实跑真实 Neo4j；**所有产物已静态校验**，真实连接
   验证（§6）待部署主机执行。
2. `MX:chem:combustion` 按前缀规则标为 `Molecule`（语义上是一个“反应”）；
   若后续采用 Schema §4 的 `Reaction` 超边建模，可将其重分类为 `Reaction`。
3. 离线 `neo4j-admin import` 不直接执行 `load.cypher` 的约束/索引，需额外跑 §5。
4. 全文/向量检索、`same_as` 跨源对齐为后续阶段（本阶段已预留 `same_as` 属性与约束位）。
