# Phase 5.C 跨源实体链接器 (Entity Linker)

> 跨学科公式知识图谱 · 科研级数据融合核心组件
> 给定一个源节点，从其他来源（MG / PB / EK / WD）找出可能是同一实体的候选，给出 **rank + 置信度**，产出 `same_as` 边候选。

---

## 1. 算法说明

实体对齐 = 把不同来源描述「同一个对象」的节点归并。本链接器用 **加权多特征相似度** 打分，不做任何网络请求（纯本地数据 + 算法；Phase 5.A 的 Wikidata 在线查询在另一模块处理）。

### 1.1 特征（每个节点有 label / type / symbol / definition / domain）

| 特征 | 计算方式 | 取值 |
|---|---|---|
| 标签相似度 `label` | `rapidfuzz.token_sort_ratio`（归一化小写+去标点后比较，抵消词序差异）→ /100 | 0–1 |
| 符号匹配 `symbol` | 精确相等（归一化后）→ 1.0，否则 0 | 0 / 1 |
| 定义重叠 `definition` | `sklearn.TfidfVectorizer`（ngram 1–2）余弦相似度，全池一次性 fit | 0–1 |
| 域一致 `domain` | 粗域（math/physics/chemistry/cross）相同 → 1，否则 0 | 0 / 1 |

### 1.2 总分（可调超参）

```
score = w_label*label + w_symbol*symbol + w_def*definition + w_domain*domain
```

默认权重（和为 1.0，集中在 `entity_linker.DEFAULT_WEIGHTS`）：

```
label=0.4, symbol=0.1, definition=0.3, domain=0.2
```

> 关于「域」特征：数据字典 §2 描述里提到「同域 +0.3」，但评分公式要求权重和为 1.0，
> 因此本实现把 `domain` 作为 **二值特征（0/1）** 以权重 `0.2` 计入；如需恢复「+0.3」语义，
> 可把 `domain` 权重调到 0.3 并相应下调其它权重（保持和为 1）。权重通过构造参数即可覆盖。

### 1.3 阈值与输出

- **阈值** `score >= 0.7` → 标记为 `high_confidence`（高置信 `same_as` 候选），对应图 Schema 中 `same_as` 边的 `confidence`。
- 每个 query 返回 `top_k=10` 候选，按分数降序。
- `matched_by` 记录哪些特征显著命中（`label>=0.5`、`symbol==1.0`、`definition>=0.3`、`domain==1.0`），用于可解释性与审计。

### 1.4 same_as 边映射

对齐结果可直接落库为图 Schema 的 `same_as` 边：

```
(:Node)-[:same_as {confidence: <score>,
                   source: 'entity_linker',
                   explicit_or_inferred: 'inferred',
                   evidence: <matched_by>}]->(:Node)
```

高置信（≥0.7）候选建议 `confidence` 取 `score` 并经人工/规则复核；阈值之下仅作候选不直接落库。

---

## 2. 文件结构

```
5C_entity_linker/
├── entity_linker.py     # EntityLinker 类 + 三个相似度函数 + 权重配置 + 自测
├── linker_demo.py       # 构建模拟多源池 + 选 5~6 个 MX 节点跑链接 + 落盘
├── data/
│   ├── nodes_pool.json      # 模拟多源节点池（MX+MG+PB+EK+WD，68 个）
│   └── sample_nodes.json    # 小样本（20 个，3 个源：MX/MG/PB）
├── aligned_candidates.json  # 输出：source_node / target_source / target_id / score / matched_by
├── README.md
└── phase5C_report.md
```

---

## 3. 怎么跑

```powershell
# 1) 验证三个相似度函数独立正确
python entity_linker.py

# 2) 实跑链接，产出 ≥10 条候选、≥3 条高置信，并落盘 aligned_candidates.json
python linker_demo.py
```

> 依赖（已就绪）：`rapidfuzz 3.14.6`、`scikit-learn 1.9.1`、`numpy 2.3.2`。
> 若 PATH 找不到 python，请用全路径：`C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe`。

---

## 4. 模拟多源节点池（怎么构建）

`linker_demo.py` 的 `build_other_sources()` 把现有数据贴上不同 `source` 标签，构造多源池：

- **MX**（36 个）：直接取自 `06_PoC/etl/neo4j/neo4j_ready.json`，标签统一为 `MX`。
- **MG**（4 个）：取自 `06_PoC/etl/samples/mathgraph/statement_informal.csv`（如 Fundamental theorem of calculus、Euler number e）。
- **PB**（3 个）：取自 `06_PoC/etl/samples/physicsbabel_sample.json`（Newton's second law、Work、Gravitation）。
- **EK**（7 个）：取自 `06_PoC/etl/samples/elementkg_sample.json`（H/O/H2/O2/H2O/water_formation）+ 一个镜像 `CO2`。
- **WD**（18 个）：为关键 MX 概念手工构造的跨域镜像实体（制造「真实匹配」），如 `wd:Q_manifold`、`wd:Q_pi`、`wd:Q_carbon_dioxide` 等，id 统一加 `wd:` 前缀并唯一。

> 说明：WD 节点是本阶段**模拟**出来的（Phase 5.A 才做真实 Wikidata 在线查询）。
> 这里用它们占位，仅为验证链接器算法本身能在「同义跨源」实体上打出高分。

---

## 5. 怎么接 Phase 5.A（Wikidata）

`linker_demo.py` 里的 WD 节点是本地占位。**接入真实 Wikidata** 时：

1. 在 Phase 5.A 模块用 SPARQL / Wikidata API 按 `label` 或 `aliases` 检索候选实体，
   取回 `id`（如 `Q...`）、`label`、`description`、`aliases`、`domain` 推断。
2. 把取回的 WD 实体统一映射成本链接器的实体 dict（`source='WD'`），
   注入到 `build_other_sources()` 的 WD 列表（或直接喂给 `EntityLinker.link(candidates=...)`）。
3. 复用本模块的 `label_similarity / symbol_match / definition_overlap` 做打分，
   WD 的 `description` 字段即作为 `definition` 输入 TF-IDF。
4. 真实查询受速率限制（429/Retry-After），建议批量检索 + 本地缓存，
   把网络结果落盘为 `data/wikidata_cache.json` 后再离线跑链接（本模块天然离线友好）。

接口契约保持一致：`EntityLinker.fit(pool)` → `linker.link(query, candidates=wd_nodes)`，
输出格式不变，可直接并入 `aligned_candidates.json`。

---

## 6. 已知边界 / 改进方向

- **重复 id 风险**：`link()` 内部已改为按「实体对象本身」做 TF-IDF transform，不再依赖 pool 下标，
  因此即使出现重复 id 也不会错配定义（演示早期曾因占位 `wd:Q?` 重复 id 触发，已修复）。
- **符号歧义**：`symbol` 为精确匹配，单字母符号（如 `M`）易在多节点间误命中，权重仅 0.1 已做抑制；
  后续可加「符号 + 域」联合约束。
- **中文支持**：归一化保留 CJK，但 `token_sort_ratio` 对无空格中文按整串比较，效果有限；
  中文实体建议额外做 jieba 分词或字符级 n-gram。
- **阈值可调**：不同学科域噪声不同，可训练/网格搜索最佳权重与阈值（见 `DEFAULT_WEIGHTS`）。
