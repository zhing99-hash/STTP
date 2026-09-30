# 03_知识层 · ETL 与查询层

> 公式知识图谱项目 Phase 2（多源接入 & 图库化）核心代码。
> 遵循《01_架构与数据模型/图Schema_v0.1.md》《02_数据层/ETL管道设计.md》
> 《02_数据层/数据源接入规范.md》三份既定设计。

本目录实现 ETL 管道（Extract → Transform → Load）、Neo4j 加载器与查询层，
把多源异构数据归一化为符合图 Schema 的 **nodes / edges**，写出 Neo4j 导入格式，
并提供「Neo4j 驱动 + NetworkX 兜底」双后端查询接口。

---

## 1. 文件清单

| 文件 | 作用 |
|---|---|
| `etl_pipeline.py` | **ETL 主脚本**：多源适配器框架 + `normalize()` + 产物写出（neo4j CSV / json / load.cypher） |
| `load_neo4j.py` | 用 neo4j 驱动以 **APOC MERGE** 方式写入 Neo4j（缺驱动/不可达时非致命退出） |
| `queries.py` | 查询接口（6 个函数），Neo4j 驱动路径 + **NetworkX 兜底** 双后端 |
| `queries.cypher` | 上述接口对应的 Cypher 语句（与 `queries.py` 内 `CYPHER` 字典一致） |
| `README.md` | 本文件 |

产物输出目录：`06_PoC/etl/`

| 产物 | 说明 |
|---|---|
| `06_PoC/etl/nodes.csv` | Neo4j 导入用节点表（`:ID`, `:LABEL` + 属性列） |
| `06_PoC/etl/relationships.csv` | Neo4j 导入用边表（`:START_ID`, `:END_ID`, `:TYPE` + 属性列） |
| `06_PoC/etl/load.cypher` | `neo4j-admin import` 用法 + MERGE/CREATE 示例 + 按 `wikidata_qid` 去重 |
| `06_PoC/etl/normalized.json` | 归一化结果（供 `load_neo4j.py` / `queries.py` 消费） |
| `06_PoC/etl/normalized.jsonl` | 标准化中间格式 JSON Lines（见接入规范 §6） |
| `06_PoC/etl/samples/` | 桩适配器的小样本 fixture（PhysicsBabel / ElementKG / math-graph） |

---

## 2. 依赖

**核心路径（MathXiv 端到端）仅依赖标准库**：`json / csv / re / argparse / datetime`。

| 依赖 | 是否必需 | 用途 | 缺失时行为 |
|---|---|---|---|
| `networkx` | 查询兜底需要 | `queries.py` 的 NetworkX 后端 | 提示安装，退出 |
| `polars` / `duckdb` | 可选 | math-graph 大文件惰性 1% 采样 | **try 守卫**，跳过该源并提示 |
| `neo4j` | 可选 | `load_neo4j.py` 写库、`queries.py` 直连路径 | **try 守卫**，非致命退出 |

```bash
# 最小（跑通 MathXiv + 查询兜底）
pip install networkx

# 可选增强
pip install neo4j==5.21.0 duckdb polars
```

Python 版本：3.11（≥3.10 均可）。

---

## 3. 运行方式

### 3.1 ETL 主脚本

```bash
cd 03_知识层

# 默认：对 06_PoC/sample_mathxiv.json 跑 MathXiv 适配器 → 打印统计 → 写出产物
python etl_pipeline.py

# 跑全部适配器（MathXiv + math-graph/PB/EK 桩）
python etl_pipeline.py --source all

# math-graph 1% 采样（需 duckdb 或 polars + 真实数据目录）
python etl_pipeline.py --source mathgraph --data-dir <math-graph 目录> --sample 0.01

# 自定义样例 / 输出目录
python etl_pipeline.py --sample-file path/to/xxx.json --out-dir 06_PoC/etl
```

### 3.2 写库（可选，需要 Neo4j）

```bash
# 连接参数（默认 bolt://localhost:7687 / neo4j / neo4j）
set NEO4J_URI=bolt://localhost:7687
set NEO4J_USER=neo4j
set NEO4J_PASSWORD=yourpassword

python load_neo4j.py
```

> 无 neo4j 驱动或数据库不可达时，脚本会打印友好提示并**非致命退出**（exit 0），
> 不影响其它脚本。

### 3.3 查询层

```bash
# 自动选择后端（优先 Neo4j，不可用则 NetworkX 兜底）并跑示例查询
python queries.py

# 强制 NetworkX 兜底（无需数据库）
python queries.py --backend nx
```

---

## 4. 数据模型与归一化约定

### 4.1 全局节点 id

```
<source_abbr>:<local_id>
```

| 源 | 缩写 | 示例 |
|---|---|---|
| MathXiv | `MX` | `MX:thm:gauss_bonnet`、`MX:sym:nabla` |
| math-graph | `MG` | `MG:stmt:1` |
| PhysicsBabel | `PB` | `PB:newton_second` |
| ElementKG | `EK` | `EK:molecule:H2O` |
| Wikidata | `WD` | `WD:Q11379` |

### 4.2 节点标签映射（适配器 → 图 Schema）

- MathXiv `theorem/lemma/definition/...` → `Formula` + 精化标签
  （`Theorem` / `Lemma` / `Definition`），一个节点可多标签：`Formula;Theorem`。
- `definition_bank` 的每条「符号→定义」→ `Symbol` 节点 + `defines` 边。
- 公式 `symbols[]` 中出现的符号 → `has_symbol` 边（确定性解析，`inferred`）。
- PhysicsBabel 方程 → `Formula;Equation`；共享符号 → `shares_symbol` 边。
- ElementKG 实体 → `Element` / `Molecule` / `Reaction`；关系 → `reactant_of` / `product_of`。

### 4.3 边属性（强制）

每条边必带 `confidence`（0~1）、`source`、`explicit_or_inferred`、`kind`。

`explicit_or_inferred` 由原始 `kind` 映射（见 `etl_pipeline.py: KIND_TO_FLAG`）：

| 原始 kind | explicit_or_inferred |
|---|---|
| `explicit_citation` | `explicit` |
| `llm_inferred` | `inferred` |
| `formal` / `has_symbol` / `same_as` / `shares_symbol` | `inferred` |
| `defines` / `reactant_of` / `product_of` / `part_of` | `explicit` |

> 说明：Schema §3 的三值枚举为 `explicit / inferred / llm_inferred`；
> 本管道按任务约定归并为 `explicit / inferred` 两值，**原始 `kind` 一并保留在边上**，
> 因此「大模型推断 vs 规则推断」可用 `kind = 'llm_inferred'` 精确还原、过滤与降权。

---

## 5. Neo4j 部署简述

> 完整环境搭建见 **`01_架构与数据模型/技术基线与环境.md`**。此处仅摘要。

1. **启动（Docker，推荐可复现）**：
   ```bash
   docker run -d --name formula-graph \
     -p 7474:7474 -p 7687:7687 \
     -e NEO4J_AUTH=neo4j/yourpassword \
     neo4j:5.21
   ```
   或使用 **Neo4j Desktop** 新建 Local DBMS（5.x）。
2. **浏览器**：http://localhost:7474 （登录初始化密码）。
3. **约束**：`load.cypher` 与 `load_neo4j.py` 均会创建 `id` 唯一性约束，
   MERGE 才能高效防重。
4. **两种导入方式**：
   - **首建 / 大批量**：`neo4j-admin database import full`（停库执行，见 `load.cypher` (A) 段）。
   - **增量 / 小批**：APOC `apoc.merge.node` / `apoc.merge.relationship`（见 `load_neo4j.py`）。
5. **对齐去重**：Align 阶段按 `wikidata_qid` 用 `apoc.refactor.mergeNodes` 合并
   （见 `load.cypher` (E) 段）。

---

## 6. 查询接口（`queries.py`）

| 函数 | 语义 | 对应 Cypher |
|---|---|---|
| `get_node(id)` | 取单个节点 | `queries.cypher` §1 |
| `get_neighbors(id)` | 取邻居（方向/类型/置信度） | §2 |
| `subgraph_by_confidence(threshold)` | 置信度 ≥ 阈值的子图 | §3 |
| `paths_between(a, b)` | 两点间有向路径（≤5 跳） | §4 |
| `list_by_type(t)` | 按标签/类型列节点 | §5 |
| `filter_edges_by_kind(kind)` | 按 kind 过滤边 | §6 |

双后端接口签名完全一致，业务代码无需感知底层是 Neo4j 还是 NetworkX。

---

## 7. 端到端验证（无数据库也可复现）

```bash
cd 03_知识层
python etl_pipeline.py        # 产出 06_PoC/etl/* ；MathXiv：22 节点 / 44 边
python queries.py --backend nx   # 载入 normalized.json，跑 6 类示例查询
```

即便本机没有运行中的 Neo4j，`queries.py` 也能把归一化结果载入 NetworkX
并执行全部查询，从而证明接口逻辑正确。
