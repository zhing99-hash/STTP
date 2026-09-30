# Phase 8.B 真实数据接入报告 · ElementKG 2.0 全量 10M CSV（2026-09-30）

## 数据源（用户下载的全量官方数据）
- 路径：`01tuopu\公共数据集\`
- `10m_elementkg_release.csv`（858 MB / 10,150,994 行）—— **ElementKG 2.0 真实「元素-官能团-分子-反应-实验」知识图谱**，三元组格式 `head_type, head_value, relation, tail_type, tail_value`
- `elementkg_synthetic_corpus_40w/`（合成语料，1.9GB+340MB JSON，偏 LLM 训练，本期未纳入 KG 结构）

## 全量规模 profiling（10.15M 行）
| head_type | 行数 | 性质 |
|---|---|---|
| ExperimentReagent / Experiment / Procedure | ~7.3M | 实验方案文本（描述/结论，literal，对公式图价值低）|
| **Molecule** | 1,222,144 | ✅ 真实分子 + PUBCHEM 全属性 |
| **Reaction** | 628,165 | ✅ 真实反应（原子映射 SMILES/温度/产率）|
| Reactant/Reagent/Product | ~96万 | 真实 SMILES（反应角色）|
| functionalGroup | 373 | 官能团 |
| element | 221 | 元素属性（与 Phase 8 OWL 重叠）|

**约束**：Aura 免费实例约 5 万节点上限；全量 2.8M 实体远超限。故只抽**有结构的真实化学核心子集**。

## 真实化学核心子集（有界、连到骨架）
`11_真实数据/elementkg10m_ingest.py` 两遍流式扫描（避免内存爆）：
- 取前 **800 真实反应**，沿 `PRODUCES`/`PARTICIPATES_IN`/`USED_IN` + `IS_MOLECULE` 解析出真实分子
- 产物 **1602 节点 / 4992 边**：
  - 真实分子 **713**（带 PUBCHEM 分子式/分子量/标准SMILES/InChIKey/精确质量）
  - 真实反应 **800**（reactant_of 636 / product_of 71 / reagent_of 1807）
  - 官能团 **76**（has_functionalgroup 2350）
  - 元素 **13**（has_element 109，由官能团回溯；same_as 桥接 Phase 8 的 EK:el:* 13 条）
  - 骨架分子 **6 条 same_as 桥接**（水/CO₂/甲烷/O₂/葡萄糖/ATP 等，按分子式挂入真实反应网）

## 上云结果（Neo4j Aura 免费实例）
- `load_neo4j.py --input phase10_aura_delta.json` 幂等 MERGE：节点 1602/1602、边 4992/4992
- Aura 现 **2077 节点 / 7593 边**
- 类型统计新增：reaction 809 / molecule 753 / functional_group 76 / element 137（=125+13）
- 校验 6/6 全绿（exit=0）：Theorem 2、低置信推断边 45 条、gauss_bonnet→manifold 真实路径、跨源 same_as 全在

## 可视化
- 本地全量图 `graph_data_full.json`（2077/7597）起 viz（http://127.0.0.1:8765/，pid 11192）
- 前端补：`TYPE_STYLE` + FunctionalGroup（hexagon）；`EDGE_TYPE_COLOR` + reagent_of / has_functionalgroup / has_element
- 真实「反应 → 分子 → 官能团 → 元素」链路可在前端点选查看真实 PUBCHEM 属性与原子映射 SMILES

## 关键踩坑
1. **顺序依赖连接 bug**：单遍顺序连接会因反应行晚于反应物/产物/分子行而解析出 0 分子 → 改为**顺序无关连接**（缓冲全部 反应↔实体、实体↔分子 映射，再按反应出现序取前 N）。
2. element 行在 CSV 中仅 ~13 个真实元素（其余 118 来自 Phase 8 OWL），故 Phase 8.B 元素桥接仅 13 条，与 Phase 8 互补而非重复。
3. 18 个「悬空端点」全为预期桥接（same_as 指向 Aura 已有节点 EK:el:*/PC:mol:*），推上去后自动解析。
4. GBK 控制台乱码（数据正确）；两遍扫描 818MB 共 ~2 分钟，内存峰值可控（机器 7.8GB 空闲）。

## 文件清单
- 适配器：`11_真实数据/elementkg10m_ingest.py`
- 中间产物：`11_真实数据/elementkg10m_raw.json`（1602/4992）
- 构建：`06_PoC/build_phase10.py`、`06_PoC/graph_data_phase10.json`、`06_PoC/etl/neo4j/phase10_aura_delta.json`
- 全量图：`06_PoC/graph_data_full.json`（2077/7597，`build_full_graph.py` 已纳入 phase10）
- 前端：`06_PoC/graph_view.html`（FunctionalGroup + 3 新边配色）
- 看板：`00_项目管理/团队分工与进度.md`（Phase 8.B 行 + 详情段）

## 后续可选的「真实数据」扩展
- (B1) 扩反应规模：N 从 800 → 5000~10000（仍在 Aura 上限内），覆盖更多真实反应/分子
- (B2) 接入 `elementkg_synthetic_corpus_40w` 做 LLM 边推断训练/校验语料
- (B3) 真实反应 → 骨架物理量（如反应焓/活化能）跨域链，强化「化学↔物理」连接
- (B4) 解析全部 118 元素 + 全部官能团（CSV 仅部分），补全元素层
