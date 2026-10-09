# STTP · 跨学科公式知识图谱
## 项目开发指南（WorkBuddy 接手手册）
> 适用对象：接手本项目继续开发的 Agent 或工程师
> 生成时间：2026-10-08
> 项目根目录：`C:\Users\Administrator\WorkBuddy\2026-10-08-10-51-20\STTP`

---

## 一、项目愿景与目标

**一句话目标**：把数学公式、物理规律、化学物质与物理世界连成一张可推理的知识图谱。

**长期愿景**：用户想看到一条完整的跨域推导链——
```
E=mc² (Formula)
    ↓
能量 (PhysicalQuantity)  ←  has_symbol
    ↓
CH₄ + 2O₂ → CO₂ + 2H₂O  (Reaction)
    ↓
甲烷分子 (Molecule) ← composed_of ← 碳原子 / 氢原子 (Element)
```
最终实现：从纯数学定理出发，一路推理到真实化学分子的物理化学性质，全链路可符号验证。

**当前进度**：四层架构（数据层 / 知识层 / 推理生成层 / 交互层）均已 MVP 落地，图谱规模 **7339 节点 / 40249 边**（Aura 云端集合级对账零差异），已开源至 GitHub。**北极星指标「跨学科连通性」已于 2026-10-09 首次仪器化**：连通分量 **1**、孤立节点 **0**、跨域边 **869 = 2.16%**（结构风险：物理占比 **71.5%**、`has_quantity` 单一类型占跨域边 **90%**、跨学科实质只在「化学 ↔ 物理」**93%**）。

---

## 二、当前运行状态

### 2.1 云端（Neo4j Aura）
```
URI:        neo4j+ssc://853a33bc.databases.neo4j.io
Database:   853a33bc
Username:   853a33bc
Password:   （见 环境变量 / .env，禁止明文）
总计:       7339 节点 / 40249 边（与本地集合级对账零差异）
```

> ⚠️ Aura 免费实例约 5 万节点上限，当前 **7339 节点 / 40249 边**（云端与本地集合级对账零差异），连接池紧张时注意分批操作。

### 2.2 本地可视化服务
```
地址:   http://127.0.0.1:8765/
数据:   06_PoC/graph_data_phase24.json（7339 节点 / 40249 边）—— 由 .env 的 GRAPH_DATA_FILE 指定
前端:   06_PoC/graph_view.html（Cytoscape.js + MathJax，**依赖已本地 vendored**，可完全离线）
```

**启动命令**（在项目根目录，**推荐**）：
```bash
bash sttp.sh viz          # 自动读 .env：GRAPH_DATA_FILE / VIZ_PORT / STTP_PYTHON
```
> 长驻请后台启动并重定向日志，例如：`bash sttp.sh viz > .runlog/viz.log 2>&1 &`
> 手动等价形式：`GRAPH_DATA_FILE=06_PoC/graph_data_phase24.json python 06_PoC/viz_server.py`
> ⚠️ 必须用系统 Python（`.env` 的 `STTP_PYTHON`）；托管 3.13 是空环境，缺依赖。

### 2.3 GitHub 仓库
```
仓库:   https://github.com/zhing99-hash/STTP
分支:   main
最新:   8c3a448（Aura 属性消毒 + 边对账工具）—— 已 push，本地领先 0
```
> ⚠️ 直连 GitHub 被出口白名单拦（0/3），**必须走出口代理**（见 §8.3）。代理端口**会变**，用前先 `netstat -ano | grep LISTEN` 确认。
> ⚠️ **切勿设 `GIT_TERMINAL_PROMPT=0`** —— 会让 `git-credential-manager` 拒绝供凭据。历史成功率约 1/3，脚本里请带重试循环。

---

## 三、目录结构

```
01tuopu/
├── 00_项目管理/           # 看板、进度、团队分工（团队分工与进度.md）
├── 01_架构与数据模型/     # Schema v0.1 定义、数据模型文档
├── 02_数据层/             # 各数据源适配器（MathXiv、PhysicsBabel…）
├── 03_知识层/             # queries.py（图查询）、load_neo4j.py（灌库）
│   └── etl/               # normalized.json（权威原始格式，viz 后端源）
├── 04_验证闭环/           # 符号校验（R-PHY / R-CHEM / R-MATH）
├── 05_可视化/             # D3 / Cytoscape 相关资源
├── 06_PoC/               # ★核心产出目录
│   ├── graph_view.html    # 可视化前端（Cytoscape.js）
│   ├── viz_server.py     # 可视化后端（标准库 http.server，端口 8765）
│   ├── graph_export.py   # ★权威 raw → viz 格式转换器（必须用此函数）
│   ├── build_phase*.py   # 各 Phase 构建脚本（自动合并 seed*.json）
│   ├── build_full_graph.py
│   ├── robust_aura_loader.py  # ★健壮加载器（分批 + 重连重试）
│   ├── split_aura_delta.py
│   ├── export_normalized.py    # 从 Aura 导出原始格式（修复 viz 后端源）
│   ├── etl/              # normalized.json / phase*_aura_delta.json
│   └── graph_data*.json   # 各 Phase viz 格式快照（graph_view 直接读）
├── 07_交付物/             # 各阶段交付报告
├── 08_部署包/neo4j/      # Docker 本地部署包（docker-compose.yml 等）
├── 09_科研扩展/
│   ├── 9_inference/      # ★推理层核心脚本
│   │   ├── gnn_infer.py              # 桥接导向 GraphSAGE 链接预测
│   │   ├── llm_hypotheses.py        # LLM 语义假设生成
│   │   ├── llm_review_85.py         # 85 条假设 LLM 复核
│   │   ├── phase9_deepen.py          # Phase 9 深化（摩尔质量锚点 + 反应角色桥）
│   │   ├── export_aura.py           # 从 Aura 反向导出权威图
│   │   ├── sync_aura_phase9b.py     # Phase 9b 同步到 Aura
│   │   ├── sync_aura_review.py      # 85 假设复核结果同步到 Aura
│   │   ├── phase9_deepen_edges.json # Phase 9 深化产出（188 边增量）
│   │   ├── phase9_refined_raw.json  # Phase 9 精筛产出
│   │   ├── phase9_candidates.json   # Phase 9 GNN 候选边
│   │   ├── _recon.py                # 环境侦察脚本
│   │   └── _probe_graph.py          # 图谱字段探查脚本
│   ├── 5A_wikidata/     # Wikidata same_as 边接入（19 条）
│   ├── 5B_gnn/         # GraphSAGE 推断（20 条 GNN 候选）
│   └── 5C_entity_linker/ # 跨源实体对齐（7 条高置信）
├── 10_种子数据/          # ★13 个垂直切片 seed JSON（手工策划骨架）
│   ├── seed_common.py   # 共享构造器（CamelCase / domain 着色 / pint 校验）
│   ├── build_*.py      # 各切片构建脚本（14 个，共 13 个 seed JSON）
│   └── seed_*.json      # 各切片策划 JSON（骨架桥接基础库）
├── 11_真实数据/         # ★真实数据适配器
│   ├── elementkg_ingest.py    # ElementKG OWL → raw（118 元素 + same_family/same_period）
│   ├── elementkg10m_ingest.py # ElementKG 10M CSV → 有界子图（800 反应 / 713 分子）
│   ├── pubchem_mol_ingest.py  # PubChem PUG-REST → 14 真实分子 + composed_of
│   ├── physicsbabel_ingest.py  # PhysicsBabel parquet → 600~5000 方程 + 物理量
│   ├── elementkg.owl          # ElementKG OWL 文件（~4 MB，真实元素数据）
│   ├── elementkg_raw.json     # ElementKG OWL 解析结果（118 节点 / 2044 边）
│   ├── physicsbabel_raw.json   # PhysicsBabel 解析结果（已生成，待推）
│   └── 公共数据集/             # ElementKG 10M CSV（858 MB，勿入 git）
│       └── 10m_elementkg_release.csv
├── README.md
├── .gitignore
├── Phase*.md                    # 各阶段归档报告
└── PROJECT_DEVELOPMENT_GUIDE.md  # 本文件
```

---

## 四、数据模型

### 4.1 节点类型（15 类，受控词表）

| 类型 | 数量 | 说明 | 典型 id 前缀 |
|------|------|------|------------|
| Formula | 5082 | 物理方程（PhysicsBabel 引入） | PB:fo:, MX:formula: |
| Reaction | 769 | 化学反应 | RX:, BC:rx:, OM:rx: |
| Molecule | 755 | 分子（含原子组成） | MO:, PC:mol:, BC:mo:, OM:mo:, IC:mo: |
| Paper | 181 | 文献（OpenAlex 引入，Schema v0.2） | PA:oa: |
| PhysicalQuantity | 144 | 物理量 | CM:pq:, MX:phy:, PB:pq:, TH:pq: |
| Element | 118 | 化学元素（含原子量/周期/族，已去重） | EK:el: |
| Symbol | 113 | 数学/科学符号 | MX:sym: |
| FunctionalGroup | 76 | 官能团（ElementKG 引入） | EK2:fg: |
| Unit | 33 | 国际单位 | UN: |
| WikidataEntity | 24 | Wikidata 对齐实体 | WD: |
| Constant | 23 | 物理常量（CODATA 2022 真实化） | TH:pq:, MX:const:, PB:pq: |
| MathConcept | 13 | 数学概念 | MX:math: |
| Definition | 4 | 数学定义 | MX:def: |
| Lemma | 2 | 数学引理 | MX:lemma: |
| Theorem | 2 | 数学定理 | MX:thm: |

> ⚠️ **类型判定必须用 `graph_export.pick_type(labels)`**（传 `labels`，不是节点对象）。
> 历史上用 `labels[0]` 统计，导致首标签恒为 `Entity` 的 1658 个节点被吞。
> **标签收敛**：`Physical_quantity`（拼写漂移）/ `ElementEntity` / `PhysicsEntity`（外部源自造）三个表外桶
> 已于 2026-10-09 归零（`03_知识层/normalize_labels.py`，词表 = `TYPE_PRIORITY` ∪ {Entity}）。

**每个节点的结构**（raw 格式，`normalized.json`）：
```json
{
  "id": "MX:chem:co2",
  "labels": ["Entity", "Molecule"],
  "props": {
    "id": "MX:chem:co2",
    "name": "Carbon dioxide",
    "formula": "CO2",
    "meaning": "化学式 CO2",
    "domain": "chem.molecule",
    "ntype": "molecule",
    "confidence": 1.0,
    "explicit_or_inferred": "explicit",
    "source": "curated_seed/energy_combustion"
  }
}
```

### 4.2 边类型（22 种）

| 边类型 | 数量 | 说明 | 门控 |
|--------|------|------|------|
| has_symbol | 24367 | 公式/物理量 → 符号 | — |
| has_quantity | 4385 | 分子/元素 → 物理量（摩尔质量、类药性描述符…） | R-PHY |
| composed_of | 2664 | 分子 → 元素（含 count） | R-CHEM |
| has_functionalgroup | 2350 | 分子 → 官能团 | — |
| reagent_of | 1807 | 试剂 → 反应 | R-CHEM |
| same_period | 1360 | 同周期元素 | — |
| dimensionally_consistent | 1200 | 量纲自洽（物理方程内） | R-PHY |
| reactant_of | 679 | 反应物 → 反应（已验证） | R-CHEM |
| same_family | 374 | 同族元素（f 区实为「同系列」） | — |
| discusses | 178 | 文献 → 概念 | — |
| cites | 176 | 文献引用 | — |
| defines | 138 | 定义者 | — |
| derived_from | 120 | 数学推导 / 常量派生 | R-MATH |
| has_element | 109 | 分子 → 元素 | R-CHEM |
| same_as | 102 | 跨源对齐（Wikidata / 基础库） | — |
| product_of | 99 | 产物 → 反应（已验证） | R-CHEM |
| has_unit | 78 | 物理量 → 单位 | R-PHY |
| related_to | 53 | GNN 待复核假设（NEEDS_REVIEW） | ⚠️ 未验证 |
| part_of | 5 | 部分-整体 | — |
| chemical_reaction | 2 | 反应类型标注 | — |
| proves | 2 | 证明关系 | R-MATH |
| releases | 1 | 放热标注 | — |

> **跨域边占比 = 869 / 40249 = 2.16%**（北极星指标）；其中 `has_quantity` 一种占 **784 = 90%**
> —— **跨域结构对单一关系类型高度依赖，是扩容器时的重点风险**。

**每条边的结构**（raw 格式）：
```json
{
  "id": "P9D:has_quantity:MO:ch4->PB:pq:molar_mass",
  "source": "MO:ch4",
  "target": "PB:pq:molar_mass",
  "type": "has_quantity",
  "kind": "molecule_molar_mass",
  "props": {
    "confidence": 1.0,
    "verified": true,
    "verification_gate": "R-PHY",
    "rationale": "molar mass = Σ atomic_weight × count",
    "explicit_or_inferred": "inferred",
    "value": 16.043,
    "unit": "g/mol",
    "source": "Phase9.deepen"
  }
}
```

### 4.3 id 命名约定

所有节点 id 必须带源前缀，不允许裸 id：

| 前缀 | 来源 | 示例 |
|------|------|------|
| MX: | MathXiv 公式图谱 | MX:thm:gauss_bonnet, MX:phy:newton2 |
| MG: | math-graph 数据集 | MG:pq:force |
| PB: | PhysicsBabel | PB:fo:0, PB:pq:molar_mass |
| EK: / EK2: | ElementKG OWL / 10M CSV | EK:el:Fe, EK2:mol:molecule_977 |
| PC: | PubChem | PC:mol:280 |
| PA: | OpenAlex（文献层） | PA:oa:W2005936791 |
| WD: | Wikidata | WD:Q742833 |
| ~~EL:~~ | **已废弃**（A5 去重并入 `EK:el:<Symbol>`） | ~~EL:h~~ → `EK:el:H` |
| MO: | 手工种子（分子） | MO:ch4, MO:co2 |
| RX: | 手工种子（反应） | RX:comb_ch4 |
| CM: | 经典力学切片 | CM:pq:mass, CM:un:pascal |
| TH: | 热力学切片 | TH:pq:temperature |
| EM: | 电磁学切片 | EM:pq:voltage |
| BC: | 生物化学切片 | BC:rx:respiration |
| OM: | 有机化学切片 | OM:rx:ethane_comb |
| IC: | 无机化学切片 | IC:rx:neutralization |
| CE: | 化学平衡切片 | CE:fo:mass_action |
| NT: | 数论切片 | NT:thm:euler |

> ⚠️ **`EL:*` 已于 2026-10-09 全面退出**（`10_种子数据/seed_common.py` 的 `BASE_IDS` 同步改为 `EK:el:*`）。
> 此前 `BASE_IDS` 仍写 `EL:c/h/o`，导致 42 条 `composed_of` 边**被静默丢弃**（引用不存在的 id，悬空即被过滤）。

---

## 五、核心脚本说明

### 5.1 `06_PoC/graph_export.py` — ★权威格式转换器
**必须使用此函数**生成供 viz 和 Neo4j 使用的 JSON，禁止手工 merge 或直接写 JSON。

```python
import graph_export
viz = graph_export.build_graph_data(raw_json_path)  # 返回 viz 格式 dict
# 输出字段：nodes[id/label/type/labels/subject/domain/formula/confidence/attrs]
#           edges[id/source/target/type/kind/confidence/explicit_or_inferred/verified/gate/evidence/rationale/domain]
```

**type 优先级**（`TYPE_PRIORITY`，**须用 `pick_type(labels)` 判定**，勿用 `labels[0]`）：
```
Symbol > Element > Molecule > Reaction > FunctionalGroup > Constant > PhysicalQuantity > Unit >
Paper > WikidataEntity > Definition > Theorem > Lemma > Equation > MathConcept > Formula > （兜底 Entity）
```
> `Paper`（Phase 8 OpenAlex）与 `WikidataEntity`（Phase 9）是后补登记的 —— 在登记之前，
> 这 181 + 24 个节点只能靠 `labels[0]` 的偶然排列顺序被「碰巧」识别，属**静默依赖**。

**domain / 命名空间 → subject 着色**（`subject_of(domain, node_id)`，优先级：**显式 domain > id 命名空间兜底**）：
- **物理**：`phys` / `phy` / `pb`；命名空间 `PQ` `PB` `CM` `QM` `TH` `EM` `OP` `SM` `RT` `UN` `SY` `CO` `FO`
- **化学**：`chem` / `ek` / `bio*`；命名空间 `EL` `EK` `EK2` `IC` `BC` `RX` `OM` `MO` `PC` `CE`
- **数学**：`math` / `mx` / `mg`；命名空间 `MX` `MG` `MA` `MC` `NT` `PA`（本批 Paper 是数学文献切片）
- **跨学科**：`WD` / `wd`（Wikidata 真实世界实体）
- 其他 → 跨学科

> ⚠️ **口径变更史（2026-10-09）**：`_NS_SUBJECT` 曾把 **`CM`/`QM`/`TH`/`EM` 标成「数学」**，
> 直接**污染跨学科判定**（凭空造出 63 条假跨域边）。修正后跨域边从 1054 降到 991（修数据前）→ 再降到 869（修数据后）。
> **改学科口径一定要做反向对照**，否则会把「剔除假数据」误读成「连通性退化」。

### 5.2 `06_PoC/viz_server.py` — 可视化后端
```
端口：     8765（默认）
数据源：   graph_data.json 或 GRAPH_DATA_FILE 环境变量
API 端点：
  GET /                        → graph_view.html
  GET /graph_data.json         → 完整图谱（JSON）
  GET /api/neighbors?node=<id>→ 邻接节点
  GET /api/path?src=<id>&dst=<id> → 两节点间路径
  GET /api/stats              → 图谱统计
启动：     python 06_PoC/viz_server.py
参数：     --port 8080 / --host 0.0.0.0
```
> ⚠️ 后端依赖 `06_PoC/etl/normalized.json`（Aura 原始格式）。该文件已被 `export_normalized.py` 维护，若缺失 viz 可正常启动但 API 降级。

### 5.3 `06_PoC/robust_aura_loader.py` — ★健壮加载器
```powershell
# 基本用法（幂等 MERGE，不重复写入）
python 06_PoC/robust_aura_loader.py --input <delta.json>

# 纯边加载（节点已在，只补边）
python 06_PoC/robust_aura_loader.py --input <delta.json> --edges-only

# 分批大小（默认 2000 条/批，可调）
python 06_PoC/robust_aura_loader.py --input <delta.json> --batch 500
```
> ⚠️ Aura 免费实例连接池有限，大批量（>5000 边）必须分批；单大事务在 flaky 连接下会整体回滚（静默失败）。必须验证云端边数确认写入成功。

### 5.4 `06_PoC/build_full_graph.py` — 全量图构建
```powershell
# 合并所有 seed + 各 Phase 真实数据，输出 graph_data_full.json
python 06_PoC/build_full_graph.py
```
> 需在 `06_PoC/` 目录下运行，依赖 `10_种子数据/seed_*.json` 和各 Phase 产出。

### 5.5 `09_科研扩展/9_inference/phase9_deepen.py` — Phase 9 深化
```python
# 生成两类真实跨域桥（无需 RDKit）：
#   1. 摩尔质量锚点（molecule → PB:pq:molar_mass，R-PHY 量纲校验）
#   2. 反应角色桥（reaction → molecule，R-CHEM 方程解析）
python 09_科研扩展/9_inference/phase9_deepen.py
# 输出：phase9_deepen_edges.json（nodes=[], edges=188）
```

### 5.6 `09_科研扩展/9_inference/export_aura.py` — 从 Aura 反向导出 viz 快照
```bash
# 从 Aura 拉取全量图 → viz 格式 → 06_PoC/graph_data_aura.json（默认）
bash sttp.sh export
python 09_科研扩展/9_inference/export_aura.py --out 06_PoC/xxx.json   # 自定义输出
```
> 用于：Aura 更新后同步本地快照；排查云端与本地是否一致。
> ⚠️ **输出必须避开 `graph_data_phase<N>.json` 命名** —— 那是历史快照，反向导出覆盖它们会让历史不可回溯。
> 脚本**内置守卫**：目标形如 `graph_data_phase<N>.json` 时直接拒绝（除非 `--force`）。
> ⚠️ 本脚本产出 **viz 格式**（含 `schema`/`meta`），与权威 raw 图 `06_PoC/etl/normalized.json`（仅 `nodes`/`edges`）**不是同一格式，不可互相覆盖**。

### 5.7 `06_PoC/neo4j_props.py` — DB 边界属性消毒（**推 Aura 前必过**）
```python
import neo4j_props
props = neo4j_props.sanitize_props(raw_props, where="edge")   # 嵌套容器 → json.dumps（无损）
```
> Neo4j 属性值只接受 primitive / 同质 primitive 数组。本地 NetworkX **不校验**，
> 所以 `dict` / `list[dict]` 会在本地一路无事、**只在推 Aura 时让整批事务失败**。
> 已接入 `11_真实数据/push_element_merge.py` 与 `06_PoC/robust_aura_loader.py`。
> ⚠️ 新增推送器时**务必同样接入**，否则会重演 2026-10-09「exit=0 但静默丢 600 边」的事故。

### 5.8 `06_PoC/reconcile_aura_edges.py` — 云端/本地边对账
```bash
bash sttp.sh reconcile --report-only   # 只报告差异
bash sttp.sh reconcile                 # 实际清理（使云端严格 == 本地）
```
> 推送**只能加**：历史上被替换掉的旧方案残留（如 Phase 11 的 `kind=reaction_role`）不会被自动清除。
> 以本地 `06_PoC/etl/normalized.json` 为基准：删云端独有三元组 / 删同三元组 kind 变体 /
> **补本地独有边**（含「删后需按本地 kind 补回」兜底）。已并入推送编排的步骤 12。

### 5.9 `06_PoC/connectivity_audit.py` — ★北极星连通性仪器（**2026-10-09 新增**）
```bash
python 06_PoC/connectivity_audit.py                    # 人读报告
python 06_PoC/connectivity_audit.py --json out.json    # 机器读
python 06_PoC/connectivity_audit.py --input 06_PoC/etl/normalized.json   # 指定权威 raw 图
python 06_PoC/connectivity_audit.py --strict           # 有异常（分量>1 / 孤立>0 / 悬空 / 自环）时退码 2
```
> **四块输出**：① 连通分量 / 孤立节点（含每分量节点数列出）② **跨域边矩阵**（学科对 × 边类型）
> ③ 学科分布 ④ 命名空间 × 学科一致性（`_NS_SUBJECT` 与显式 domain 是否冲突）。
> `MX` / `PQ` 为**枢纽命名空间**（有意跨学科，白名单内不做「混标」警告）。
> ⚠️ **指标口径会随 `graph_export.subject_of()` 变更而变** → 改口径时**必须做反向对照**，
> 否则无法区分「连通性退化」与「假跨域边被剔除」。详见 `07_交付物/跨学科连通性审计报告_20261009.md`。

### 5.10 `06_PoC/apply_delta.py` — ★通用本地 delta 应用器
```bash
python 06_PoC/apply_delta.py --delta <delta.json> --phase <N>            # dry-run（默认）
python 06_PoC/apply_delta.py --delta <delta.json> --phase <N> --apply    # 实际写入
```
> delta schema 四段：`{meta, nodes, delete_nodes, edges, delete_edges}`。
> 支持 upsert / 删除 / **merge** / 删边；**默认 dry-run**；执行前**自动备份** `normalized.json`；
> 内置 **6 项自检**（悬空 / 自环 / id 唯一 / 计数 / 已存在节点 labels 不变 / 备份可回滚）。
> **`normalized.json` 是权威基准**，改数据必须先在此 apply 通过，再谈推 Aura。

### 5.11 `06_PoC/sync_seed_delta.py` — seed → 权威图**差量回灌**（**2026-10-09 新增**）
```bash
python 06_PoC/sync_seed_delta.py                 # 计算 seed 与权威图的差量（dry-run）
python 06_PoC/sync_seed_delta.py --delta-only <备份.json>   # 据备份重建 delta
```
> 用途：`10_种子数据/` 的 `build_*.py` 改动后，**不必全量重建**，只把差量并回权威图。
> **三类硬保护**（缺一不可）：① `VOLATILE`（`created_at` 等时间戳）**排除出 diff**，
> 否则每次重跑都产生几百条假阳性 upsert；② 已存在节点**只改 props、不碰 labels**（种子标签是简写，会覆盖图谱权威标签）；
> ③ **权威性守卫** —— 外部权威来源（`nist_codata`/`openalex`/`pubchem`/`chembl`/…）的属性
> **禁止被种子降级覆盖**（实测拦住 9 处越权覆盖，如 `TH:pq:gas_const` 的 `8.314` 会被种子的粗值覆写）。
> ⚠️ `delete_nodes` 必须输出**纯 id 字符串**，不可输出 `[{"id":...}]`（见 §9.1 静默空操作）。
> `PAYLOAD_KEYS`（`value`/`unit`/`uncertainty`/…）走专用比对，避免把「数值+单位」误判为变更。

### 5.12 `03_知识层/normalize_labels.py` — 标签受控词表收敛（**2026-10-09 新增**）
```bash
python 03_知识层/normalize_labels.py --dry-run    # 默认
python 03_知识层/normalize_labels.py --apply
python 03_知识层/normalize_labels.py --delta-only <备份.json>   # 据备份重建 delta
```
> 把 `Physical_quantity`（拼写漂移）/ `ElementEntity` / `PhysicsEntity`（外部源自造类型）
> 收敛到 `graph_export.TYPE_PRIORITY` 的**受控词表 ∪ {Entity}**。
> **词表封闭性断言**必须用**归一化后**的 labels 判定（用原始 labels 会把「待改的」误报成「遗留的」）。
> delta 节点须带 `"props": {}`（防 `SET n += null` 报错）。

---

## 六、数据源接入方式

### 6.1 数据源一览

| 数据源 | 类型 | 规模 | 接入脚本 | 已入 Aura |
|--------|------|------|----------|----------|
| MathXiv (手工策划) | 公式图谱 | 36 节点 / 52 边 | —（直接写入） | ✅ |
| 13 个种子切片 | 垂直领域骨架 | 13 个 seed JSON | `10_种子数据/build_*.py` | ✅ |
| ElementKG OWL | 真实元素 KG | 118 元素 / 2044 边 | `11_真实数据/elementkg_ingest.py` | ✅ |
| ElementKG 10M CSV | 真实化学 KG（子集） | 800 反应 / 713 分子 | `11_真实数据/elementkg10m_ingest.py` | ✅ |
| PhysicsBabel | 真实物理方程 | ~600 方程（~5000 待推） | `11_真实数据/physicsbabel_ingest.py` | ⚠️ 部分 |
| Wikidata | 跨源对齐 | 19 same_as 边 | `11_真实数据/wikidata_adapter.py` | ✅ |
| GNN 推断 | 链接预测 | 20 条候选边 | `09_科研扩展/9_inference/gnn_infer.py` | ✅ |
| Phase 9 深化 | 符号校验桥 | 188 条 | `09_科研扩展/9_inference/phase9_deepen.py` | ✅ |
| **OpenAlex**（Phase 8 首步） | 真实文献层 | 181 篇 Paper + 176 `cites` + 178 `discusses` | `11_真实数据/openalex_ingest.py` | ✅ |
| **NIST CODATA 2022**（Phase 8 二期） | 真实物理常量 | 355 条解析 → 常量 13→23（含 10 个跨学科桥） | `11_真实数据/codata_ingest.py` | ✅ |
| **PubChem 精确反查**（Phase 8 二期） | 真实分子属性 | `inchikey` 反查 713/713 命中 | `11_真实数据/pubchem_ingest.py` | ✅ |
| **Crossref / DataCite**（Phase 8 三期） | 文献多源校验 | 163/163 DOI 核验命中 + 39 条一致性 flag | `11_真实数据/literature_validate.py` | ✅ |
| **ChEMBL**（Phase 8 三期） | 真实药学维度 | 332/713 命中，8 个类药性描述符 → 2512 条 `has_quantity` | `11_真实数据/chembl_ingest.py` | ✅ |

> **源可达性先探测**：换源前一律先跑 `python 11_真实数据/probe_sources.py`（TCP → TLS → 真实 HTTP 读响应体，多轮 × 双通道）。
> **白名单按「域 **+ 路径**」放行，TLS 握手成功 ≠ HTTP 可达，且不可跨路径外推** ——
> 例：`www.ebi.ac.uk/europepmc/*` 直连 3/3 通，而**同主机**的 `/chembl/*` 直连 0/3、需走代理 2/3。

### 6.2 新增数据源的标准流程

```
1. 编写适配器脚本（如 physicsbabel_ingest.py）
   → 输出 raw JSON（{nodes, edges}，节点带 id/labels/props，边带 id/source/target/type/kind/props）

2. 用 graph_export.build_graph_data() 转换为 viz 格式
   → 生成 graph_data_*.json（用于 viz）

3. 构造 Aura 增量 JSON
   → {nodes: [...], edges: [...], delete_nodes: [...], delete_edges: [...]}
   → 保存为 06_PoC/etl/neo4j/phase*_delta.json
   → ⚠️ 属性值只能是 primitive / 同质 primitive 数组；嵌套容器先过 `neo4j_props.sanitize_props`

4. 先在本地 apply 并验收（**改数据前必做**）
   → python 06_PoC/apply_delta.py --delta <p> --phase <N> [--apply]
   → 本地 normalized.json 是**权威基准**；对账/快照都以它为准

5. 推送到 Aura
   → bash sttp.sh push <delta.json>          # 单 delta
   → bash sttp.sh pushall --execute          # 全量重放（**十六步**，含对账 + 导出）

6. 对账（**推送后必做**）
   → bash sttp.sh reconcile --report-only    # 先看差异
   → bash sttp.sh reconcile                  # 清理到云端 == 本地

7. 反向导出 viz 快照
   → bash sttp.sh export                     # 默认 → 06_PoC/graph_data_aura.json（**勿覆盖 graph_data_phase<N>.json**）

8. 重启 viz
   → bash sttp.sh viz                         # 自动读 .env 的 GRAPH_DATA_FILE

9. 北极星自检（**新增/删除节点后必做**）
   → python 06_PoC/connectivity_audit.py --strict   # 分量应=1、孤立=0、悬空/自环=0（退码 0）
```

---

## 七、推理生成层工作流

### 7.1 三道符号门控

| 门控 | 触发条件 | 校验方法 | 结果 |
|------|---------|---------|------|
| **R-PHY** | 物理量量纲 | `pint` 库比较量纲 | dimensionally_consistent / has_unit / has_quantity |
| **R-CHEM** | 化学组成/反应角色 | `composed_of` 边（含 count）/ `equation` 解析 | composed_of / reactant_of / product_of / has_element |
| **R-MATH** | 数学推导 | `SymPy` 符号化简 | derived_from / proves |

> ⚠️ RDKit 不可用（numpy 2.x 不兼容）。化学校验改用 ground-truth 数据（composed_of 边 / equation 字符串解析）。

### 7.2 置信度规则

| 验证结果 | 置信度变化 |
|---------|-----------|
| VERIFIED | max(初始, 0.9) |
| REJECTED | × 0.2 |
| NEEDS_REVIEW | × 0.5 |

所有推断边必须带 `confidence` 和 `verification_gate` 属性，不可无标注推送。

### 7.3 GNN 推断流程（`gnn_infer.py`）

1. **正样本** = 已有跨域边（`same_period` / `composed_of` 等），约 132 条
2. **负样本** = 跨域非边，3000 条
3. **特征** = 节点属性 one-hot / 图结构度统计，约 29 维
4. **模型** = 2 层 GraphSAGE + dot-product 解码
5. **训练** = 桥接导向（防止 1.7% 跨域边被淹没导致退化嵌入）
6. **输出** = 跨域候选边 top 120，标注 `gnn_score`，kind=`llm_inferred_gnn`

### 7.4 LLM 语义假设流程（`llm_hypotheses.py`）

1. 取 120 条 `related_to` GNN 候选
2. 按规则重筛（分子 `formula` 是否精确出现在反应 `equation`）
3. 分三类：**UPGRADE**（可验证→ reactant_of/product_of）、**KEEP**（合理但不可证→ NEEDS_REVIEW）、**DROP**（GNN 伪影）
4. `llm_review_85.py` 用符号核验 + 领域白名单做第二轮判定

---

## 八、环境变量与凭据

### 8.1 必须配置的环境变量

```powershell
# Neo4j Aura 连接（Aura 连接必用）
$env:NEO4J_URI       = "neo4j+ssc://853a33bc.databases.neo4j.io"
$env:NEO4J_USER      = "853a33bc"
$env:NEO4J_PASSWORD  = "<从 MEMORY.md 或 .env 读取>"
$env:NEO4J_DATABASE  = "853a33bc"

# viz 服务（可选，也可直接 bash sttp.sh viz 自动读 .env）
$env:GRAPH_DATA_FILE = "06_PoC\graph_data_phase22.json"
$env:VIZ_PORT        = "8765"
$env:STTP_PYTHON     = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
```

> ⚠️ 密码必须从 MEMORY.md 或 `.env` 文件读取，**禁止硬编码**写入脚本。`08_部署包/neo4j/.env.example` 是模板，`08_部署包/neo4j/.env` 应含真实凭据（已从开源仓库移除）。

### 8.2 .env 文件位置

- 本地私用：`C:\Users\Administrator\WorkBuddy\2026-10-08-10-51-20\STTP\.env`（已 gitignore）
- 开源模板：`C:\Users\Administrator\WorkBuddy\2026-10-08-10-51-20\STTP\08_部署包\neo4j\.env.example`

---

## 九、已知问题与踩坑清单

### 9.1 图数据操作

- **viz 启动即退出**：后端依赖 `06_PoC/etl/normalized.json`，缺失时 `queries.get_backend` 内部 `sys.exit()` 杀进程。修复：`export_normalized.py` 从 Aura 重建。
- **单大事务整体回滚**：Aura 免费实例连接池有限，单次 >5000 边的 UNWIND 事务在 flaky 连接下会静默回滚（边未写入，节点正常）。必须用 `robust_aura_loader.py` 分小批（默认 2000/批）。
- **Aura 连接池耗尽**：多个进程同时连 Aura 时会出现 `ConnectionAcquisitionTimeoutError`。操作前关掉竞争进程，操作完成后导出快照。
- **Aura 唯一约束为 Entity.id**：同一 id 的节点重复写入会触发 `IndexEntryConflictException`。增量 JSON 中的节点若已存在于 Aura，须从 nodes 列表中排除（边仍按 id 引用，MATCH 到已有节点）。
- **apoc.merge.relationship 按 (起点, 终点, 类型, kind) 去重**：同一对节点在同一类型下重复推只会保留一条，属正确行为。
  ⚠️ 反之亦然：**同三元组但 `kind` 不同会各存一条边**（identProps 带 `kind`）。判断「边是否重复」时不能只看 `(source,type,target)`。
- **🔴 嵌套属性会让整批事务失败，且旧推送器会静默吞掉**（2026-10-09 事故）：Neo4j 属性值只接受 primitive / 同质 primitive 数组。
  delta 里若夹带 `dict` / `list[dict]`（本地 NetworkX 不校验），推送时**整批失败**，重试 6 次后放弃该批却仍 `exit 0`。
  实际损失：`phase13` 的 `gnn_type_probs`(list[dict]) 丢 **600 条边**、`phase15/16` 的 `alias_sources`(dict) 让 **118 个元素的 period/group 全未写入**。
  修复：新增 `06_PoC/neo4j_props.py`，在 DB 边界 `sanitize_props()`（嵌套容器 → `json.dumps`，**无损**，本地图谱不动）；
  并让「放弃批次」**返回非零退出码**（编排器会中止后续步骤）。**新写推送器必须接入。**
- **🔴 推送只能加，不会减 —— 必须对账**：历史上被替换掉的旧方案残留（Phase 11 的 `kind=reaction_role` 等）会一直留在云端，
  云端逐渐变成「本地 ∪ 历代废弃物」。用 `06_PoC/reconcile_aura_edges.py`（`bash sttp.sh reconcile`）以本地为基准清理。
- **🔴 云端与本地「计数相等」≠「内容相等」**：开工时云端 7127/33090 与本地旧 phase12 数字一致，
  但云端已含 155 条本地没有的残留边。**对账必须做集合级比对（三元组集合），不能只看 count。**
- **🔴 反向导出不要覆盖 `graph_data_phase<N>.json`**：`export_aura.py` 曾硬编码输出到 `graph_data_phase12.json`，
  而推送编排每次跑都调用它 → 历史快照被反复重写、不可回溯。现默认输出 `graph_data_aura.json`，
  并内置守卫拒绝写入 phase 快照命名（除非 `--force`）。
- **`getaddrinfo failed` 不等于「Aura 实例暂停」**（曾据此误报 7 轮）：先用公共解析器（UDP/53 直查 `8.8.8.8`/`1.1.1.1`）交叉验证。
  仅本机失败 → **DNS 陈旧负缓存**，`ipconfig /flushdns` 即可；仅瞬时失败 → 直接重试；公共解析器也 NXDOMAIN → 才是实例暂停（需 console.neo4j.io resume）。
- **Cypher 25 兼容**：`MATCH (a)-[:*1..6]->(b)` 通配符变长路径不支持，须改用显式 reltype 列表或定长 `[:REL*2]`。`MATCH (a)-[r]->(b) RETURN a.id + b.id` 字符串拼接用 `||` 而非 `+`。
- **🔴 删除可能是「静默空操作」，而且自检会与被检对象一起静默通过**（2026-10-09 发现）：
  `delete_nodes` 若写成 `[{"id": "X"}]`，推送器二次取 `x["id"]` 会得到 `{"id":"X"}`（一个 dict），
  拼进 Cypher 后 `MATCH` **落空但不报错** —— 结果「**Δ+0 且退码 0**」。
  更危险的是：**残留自检用的是同一份错误的 id**，于是自检也「通过」了，故障完全隐形。
  修复两侧：`push_element_merge.py` 加 `norm_del_ids()`（兼容 str / dict）**并新增「实删数断言」**（残留 >0 退码 3）；
  `sync_seed_delta.py` 的 `delete_nodes` 改为输出**纯 id 字符串**。
  **教训：自检的输入不能与被检对象是同一份来源，否则错误会「一起通过」。**
- **🔴 `apoc.create.addLabels` 只增不减 → 云端标签会与本地分叉**：
  用它做标签「规范化」时，旧标签会永远留在云端，本地却已替换 → 集合级对账仍会报差异。
  推送器 `push_element_merge.py` 已新增 `--set-labels`（走 `apoc.create.setLabels`，**整体替换**），标签收敛轮必须带此开关。
- **🔴 跨源「看似重复」的多节点先查边，不要合并**：分子层有 8 套命名空间（`MO:`/`PC:mol:`/`IC:mo:`/`BC:mo:`…）
  看似重复，实则被 `same_as` 显式关联且带 provenance，属**有意的跨源对齐设计**。盲目合并会毁掉跨源网络。
  已知副作用：同一实体多节点会**稀释 GNN 嵌入**；若将来要缓解，应**在 GNN 特征中注入 `same_as` 邻接**或做**连通分量级嵌入池化**，而非删节点。

### 9.2 数据生成

- **🔴 种子不是权威，权威在图谱**（2026-10-09 实测）：`10_种子数据/` 是**手工策划的骨架**，其数值可能与真实源冲突。
  例：`TH:pq:gas_const` 种子里是 `8.314`，图谱（CODATA 2022）是 `8.314462618`；`EM:pq:permittivity`/`permeability` 种子里是 2018 旧值。
  **回灌种子差量时必须有权威性守卫**（`sync_seed_delta.py` 的 `EXTERNAL_AUTHORITY`），否则会把真实源数据降级成草稿值。
- **🔴 改数据前先改源头**：只修图谱不改适配器/生成器，重跑 ETL 会**重新长出同一个错误**。
  本轮 82 处学科 domain 错标、`EL:*` 悬空引用、42 个孤儿反应，全部是「改源头 + 改图谱」成对完成的。
- **外部字段语义先验证再用**：已 5 次踩中（ElementKG 的 `HASATOMIC` 实为原子序数 / PhysicsBabel / OpenAlex 年份 / ChEMBL / Crossref 引用计数口径）。
  **引用计数不可跨源比对或覆盖**（Crossref 口径远窄于 OpenAlex，中位比值 0.62）；**反向异常（子集 > 全集）才是真异常**。
- **新增切片 OUT 必须是 `.json`**：构建脚本 `OUT` 变量若误设为 `.py` 会覆盖自身源码且不入扫描管线。
- **same_as 桥接必须列白名单**：新切片 same_as 指向基础库已有节点时，悬空断言会误报。必须在 builder 中显式声明已知 id 列表。
- **权威转换器**：所有用于 viz 和 Neo4j 的 JSON 必须经 `graph_export.build_graph_data()` 生成，禁止手工 merge（会破坏 type 字段和 domain 着色）。
- **ElementKG 边 `source` 重复键**：`elementkg_adapter.py` 曾因边 dict 内 `source` 键被两次赋值（正确 id → 源名字面量）导致目标节点塌缩。修复：第二个键改名 `data_source`。

### 9.3 rdflib 解析

- **判 Literal 用 `isinstance(o, Literal)`**：勿用 `__class__.__name__`（URIRef 会被误判为字面量，导致对象属性边全部丢失）。
- **无向关系跳自环**：用 `frozenset([a, b])` 构造无向边 key 前须跳过 `a == b` 的自环，否则 unpack 报错。

### 9.4 PowerShell / 控制台

- **GBK 控制台编码**：`−`（U+2212）、下标字符（₂）打印会报 `UnicodeEncodeError: 'gbk' codec can't encode character`。数据本身正确，输出重定向到 UTF-8 文件可规避。脚本文件首行加 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`。
- **PowerShell 内联 `-c` 多语句**：含 `|`、`>`、多行字符串时会被解析为 ScriptBlock 而非命令串。改用临时脚本文件执行。
- **PowerShell Select-String 中文路径**：返回 0 是误报，扫描敏感内容应改用 Python。
- **git 中文路径**：默认对非 ASCII 路径做八进制转义显示。用 `git -c core.quotepath=false -z` 取得真实路径。

### 9.5 GitHub 推送

- **HTTPS 大包 408 超时**：本机 HTTPS 推大包（>1 万行）持续报 `HTTP 408`。解决：拆成每批 5 个文件的小提交，提交间 sleep 15s，重试 3 次。长期方案：SSH（`git@github.com:zhing99-hash/STTP.git`）。
- **细粒度 PAT**：`github_pat_` 前缀的细粒度 PAT 需在 GitHub 令牌设置中显式勾选目标仓库并授予 Contents: Read and write；经典 PAT 勾 `repo` 即可。
- **推送失败显示 "Everything up-to-date"**：这是误导输出，不代表成功；必须检查远端 commit SHA 是否推进。

### 9.6 前端可视化

- **默认 `cose` 力导布局卡死**：超过 5000 条边时，同步模拟卡死主线程导致页面"加载超时"。已修复：默认布局改为 `concentric`（瞬时）；大图（>5000 边）时力导入口加护栏；新增 `fcose` 快速力导（CDN 可用）。
- **CDN 离线降级**：本机无网时 Cytoscape.js / MathJax CDN 不可达，页面自动降级为纯文本。
- **新节点类型须同步配色**：`TYPE_PRIORITY`（`graph_export.py`）和 `TYPE_STYLE`（`graph_view.html`）须同时修改；新边类型须同时加 `EDGE_TYPE_COLOR` 和图例段。

---

## 十、下一步建议

> 按紧迫程度排序，WorkBuddy 可直接按序号推进。

### 🔴 高优先级（核心能力补全）

**1. Phase 8 收尾：ElementKG 2.0 真实 dump + Vashy / ReactionAtlas**
- 状态：Phase 8 三期满负荷落地（文献/常量/分子/药物四层），剩余候选为 **ElementKG 2.0 官方 dump**、
  **Vashy**（物理方程侧）、**ReactionAtlas**（反应侧）
- ⚠️ **首要约束：控物理失衡**。当前学科分布 **物理 71.5%**、跨域边 90% 依赖 `has_quantity`、
  跨学科实质只发生在「化学 ↔ 物理」（93%）。**接物理源前必须评估它对失衡的边际影响**，
  优先接**化学 / 数学 / 交叉学科**侧的源。

**2. 缓解分子层多节点对 GNN 嵌入的稀释**
- 现象：8 套分子命名空间（有意的跨源对齐）会让同一实体拆成多个节点，稀释嵌入质量
- 方案：**不删节点**，改为在 GNN 特征中**显式注入 `same_as` 邻接**，或做**连通分量级嵌入池化**

### 🟡 中优先级（数据质量与完整性）

**3. Phase 9 Wikidata 真实对齐（打通「公式 ↔ 世界」关键桥）**
- 状态：`query.wikidata.org` / `en.wikipedia.org` **全域 TLS TIMEOUT**（多轮探测确认被出口白名单拦截）
- 过渡方案（部分已落地）：用 **OpenAlex / Crossref / DataCite / PubChem** 的等价对齐能力替代
- 注意：若哪天真能通，SPARQL 查询间隔须 ≥3s

**4. 文献层 2 条待人工裁定**（Phase 8 三期已标 flag、未改数）
- `PA:oa:W2005936791` 年份（OpenAlex 2012 vs Crossref 2005，疑似 OpenAlex 错）
- `PA:oa:W2322753244` 引用计数**反向异常**（Crossref 1342 > OpenAlex 711，而 Crossref 本应是子集）

**5. 常量 / 单位 / 枚举收尾**
- `EM:pq:coulomb_const` 的 `unit` 补全（CODATA 无直接条目，由 `k = 1/(4πε₀)` 派生）
- `PB:pq:grav_const` / `PB:pq:planck` 的**合并**（现仅 `same_as` 对齐）—— 牵动 3000+ 条边重组
- `chembl_availability_type` 枚举语义补全（现仅存原值 int）
- 元素性质单位从 IUPAC 正式表核校（现为量级+常识推断，已标 `unit_inferred`）

### 🟢 低优先级（功能增强）

**6. PhysicsBabel 5000 方程全量补推**（**注意：物理侧，与风险 1 冲突，建议缓做**）
- 状态：已解析 `physicsbabel_raw.json`（5050 节点 / 29104 边），仅部分推入 Aura
- 操作：`python 06_PoC/robust_aura_loader.py --input <delta.json> --batch 1000`

**7. `same_series` 边类型（f 区语义严格化）**
- 现状：`same_family` 在 f 区其实表达的是「同系列」

**8. 本地 Neo4j 部署**
- 用户本机 Docker 因 CPU 虚拟化未开启不可用；若未来换机器，`08_部署包/neo4j/` 可一键部署
- `docker-compose.yml` + `load.cypher` + `deploy.py` 已就绪

---

## 十一、快速上手清单（WorkBuddy 第一天）

```powershell
# 1. 克隆仓库
git clone https://github.com/zhing99-hash/STTP.git
cd STTP

# 2. 读取凭据（从 MEMORY.md 或向用户询问）
#    必须设置以下环境变量（以 PowerShell 为例）：
$env:NEO4J_URI       = "neo4j+ssc://853a33bc.databases.neo4j.io"
$env:NEO4J_USER      = "853a33bc"
$env:NEO4J_PASSWORD  = "<用户提供的密码>"
$env:NEO4J_DATABASE  = "853a33bc"

# 3. 验证 Aura 连接
python -c "from neo4j import GraphDatabase; d=GraphDatabase.driver('$env:NEO4J_URI',auth=('$env:NEO4J_USER','$env:NEO4J_PASSWORD')); print(list(d.session().run('RETURN 1 as x'))); d.close()"

# 4. 启动本地可视化
bash sttp.sh viz
# 浏览器打开 http://127.0.0.1:8765/   （数据源 = .env 的 GRAPH_DATA_FILE = graph_data_phase24.json）

# 5. 从 Aura 反向导出 viz 快照（默认 graph_data_aura.json）
bash sttp.sh export

# 6. 查看图谱统计 / 北极星自检
bash sttp.sh stats
python 06_PoC/connectivity_audit.py --strict   # 分量=1 / 孤立=0 / 悬空=0 / 自环=0 → 退码 0

# 7. 推新数据到 Aura（推荐用编排器，自动消毒 + 对账 + 导出）
bash sttp.sh push <your_delta.json>       # 单 delta
bash sttp.sh pushall --execute            # **十六步**全量重放

# 8. 验证写入
python 08_部署包/neo4j/verify_deploy.py

# 9. 拉取最新 GitHub 更改
git fetch origin
git merge origin/main
```

---

## 十二、关键文件速查表

| 场景 | 文件 |
|------|------|
| 修改 viz 图谱配色 | `06_PoC/graph_view.html`（`TYPE_STYLE`/`EDGE_TYPE_COLOR`） |
| 新增节点类型 | `06_PoC/graph_export.py`（`TYPE_PRIORITY`）+ `06_PoC/graph_view.html`（`TYPE_STYLE`） |
| 新增边类型 | `06_PoC/graph_view.html`（`EDGE_TYPE_COLOR` + 图例段） |
| 新增种子切片 | `10_种子数据/build_*.py` → `10_种子数据/seed_*.json`，再走 `06_PoC/sync_seed_delta.py` 回灌差量 |
| 接入新数据源 | `11_真实数据/<source>_ingest.py`，用 `graph_export.build_graph_data()` 转换 |
| **量北极星连通性** | `06_PoC/connectivity_audit.py`（`--strict` 退码 2） |
| **本地应用 delta** | `06_PoC/apply_delta.py --delta <p> --phase <N> [--apply]` |
| **seed → 权威图回灌** | `06_PoC/sync_seed_delta.py` |
| **标签受控收敛** | `03_知识层/normalize_labels.py` |
| 从 Aura 拉全量图 | `09_科研扩展/9_inference/export_aura.py` |
| 健壮推 Aura | `06_PoC/robust_aura_loader.py --input <delta.json>` |
| 推送编排（16 步） | `09_科研扩展/9_inference/push_pending.py`（`bash sttp.sh pushall`） |
| 修复 viz 后端源 | `06_PoC/export_normalized.py` |
| 推理层新桥 | `09_科研扩展/9_inference/phase9_deepen.py` 或 `gnn_infer.py` |
| 更新看板 | `00_项目管理/团队分工与进度.md` |

---

*本文件由 QClaw 生成于 2026-10-08，建议在每次 Phase 完成后更新「当前运行状态」和「已知问题」两节。*
*最近更新：2026-10-09（跨学科连通性审计轮：新增 3 个工具、§0.1 北极星指标、§9 三项新踩坑、§10 优先级刷新）。*
