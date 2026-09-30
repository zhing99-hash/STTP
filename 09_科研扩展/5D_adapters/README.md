# Phase 5.D — ElementKG 2.0 / ReactionAtlas 适配器

> 化学科研级真实数据源接入骨架。沙箱无法下载 100+GB/26GB 真实数据集，本目录提供**完整接口 + 合成样本**，生产态只需替换数据源连接串。

## 一、ElementKG 2.0 适配器

**数据形态**：以"反应超图"建模的化学知识图谱，JSON-LD 格式。
**规模**：>100 万反应 / >1000 万物质。

**文件**：`elementkg_adapter.py`
- `ElementKGAdapter.normalize_reaction(rec)` — 单条反应 → Schema 节点 + 超边
- `ElementKGAdapter.convert(records)` — 批量 → `{nodes, edges}`
- `synthetic_elementkg_sample(n)` — 合成 n 条反应（演示/测试用例）

**Schema 映射**：
| ElementKG 字段 | 本项目 Schema |
|---|---|
| reaction | `Reaction` 节点（labels=["Reaction"]） |
| reactant / product / catalyst | `Molecule` 节点 + `reactant_of` / `product_of` / `catalyst_of` 边 |
| reaction_smiles | `Reaction.props.reaction_smiles` |
| conditions | `Reaction.props.conditions{temp,solvent,yield}` |

**真实数据下载**：`https://github.com/zhangtao0209/ElementKG`（需大容量存储 + 解析 JSON-LD `@graph`）

## 二、ReactionAtlas 适配器

**数据形态**：PostgreSQL 关系库（~26GB），反应 + 条件 + 组件三表。
**演示用**：`sqlite3`（Python 标准库）内存库等价实现。

**文件**：`reactionatlas_adapter.py`
- `ReactionAtlasAdapter.load_reaction(rec)` — 写反应 + 组件
- `query_reaction(rid)` — 查完整反应
- `reaction_components(rid, role)` — 查某角色组件
- 生产态：把 `sqlite3.connect(...)` 换成 `psycopg2.connect(...)` 即可

**Schema 映射**：
| ReactionAtlas 表 | 本项目 Schema |
|---|---|
| reactions | `Reaction` 节点 + 条件属性 |
| components(role=reactant/product) | `Molecule` 节点 + `reactant_of` / `product_of` 边 |
| components(role=catalyst) | `Molecule` 节点 + `catalyst_of` 边 |

## 三、运行演示

```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
& $py -u synthetic_elementkg_demo.py        # 30 合成反应 → data/elementkg_synthetic.json
& $py -u synthetic_reactionatlas_demo.py    # sqlite 内存库 25 反应 → data/reactionatlas_synthetic.json
```

## 四、与 ETL 集成点

产出的 `{nodes, edges}` 可直接喂给 Phase 2 的 `etl_pipeline.py` 的 `load_kuzu` / `load_neo4j` 步骤，仅需：
1. 把 `Reaction` / `Molecule` 标签加入 Schema 节点枚举（已在 Schema v0.1）
2. 把 `reactant_of` / `product_of` / `catalyst_of` 加入边类型枚举（已定义）
3. 全局 id 用 `<EK:...>` / `<RA:...>` 前缀

## 五、已知约束

- 真实大库沙箱下不去 → 全部用合成样本验证接口正确性
- 未做反应 SMILES 合法性校验（真实库用 RDKit 校验，见 Phase 3 门禁 R-CHEM）
- 数据已 Aura 部署的 36 节点图不含化学 Reaction 节点（待真实 ElementKG 灌入后扩展）
