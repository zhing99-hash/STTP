# Phase 8.A 真实数据接入报告 · PubChem 分子层（2026-09-30）

## 背景 / 为何不是 ElementKG 2.0 完整数据
选项 A 原意："拉 ElementKG 2.0 完整数据（OpenKG 申请/SCP API）或 ReactionAtlas 真实反应"。
可行性探测结果：
- **SCP API**：401（需 `SCP-HUB-API-KEY`，当前环境无）→ 阻塞，需你提供 Key
- **OpenKG ElementKG 2.0**：页面 SSL 证书过期 + 需填表申请（人工审核 3–5 工作日）→ 阻塞
- **ReactionAtlas 真实源**：需另行定位/授权 → 暂未接入

因此改用**当前可达、免费、无需密钥**的真实分子数据源 **PubChem PUG-REST** 来"喂实分子层"，
达成选项 A 的实质目标（真实分子/反应数据喂实骨架），且方法学与 Phase 8 元素层完全同构。

## 数据源
- **PubChem PUG-REST**：`https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/<name>/property/.../JSON`
- 免费、无需 API Key（限流 ~5 req/s，14 次请求轻松通过）
- 返回真实：MolecularFormula / MolecularWeight / CanonicalSMILES / IsomericSMILES / InChIKey / IUPACName / CID

## 覆盖的真实物种（14 个，骨架去重后的所有非聚合物物种）
水(H₂O)、二氧化碳(CO₂)、甲烷(CH₄)、氧气(O₂)、乙烷(C₂H₆)、乙烯(C₂H₄)、苯(C₆H₆)、
乙醇(C₂H₆O)、氯化钠(NaCl)、氯化氢(HCl)、氯气(Cl₂)、葡萄糖(C₆H₁₂O₆)、ATP(C₁₀H₁₆N₅O₁₃P₃)、甘氨酸(C₂H₅NO₂)

## 抽取结果（pubchem_mol_ingest.py）
- **14 个真实分子节点** `PC:mol:<CID>`，每个带真实属性：
  name / common_name / formula / molecular_weight / canonical_smiles / pubchem_cid / composition
- **20 条 same_as 桥接**：连到现有种子节点（MO:/BC:/IC:/OM:*，如 `PC:mol:962 → MO:h2o`）
- **33 条真实 composed_of 边**：由真实分子式解析元素配比，连到 Phase 8 真实元素节点 `EK:el:*`：
  如 `PC:mol:280(CO₂) → EK:el:C (×1)`、`→ EK:el:O (×2)`；`PC:mol:5957(ATP) → C×10,H×16,N×5,O×13,P×3`
  - 这些端点（EK:el:C/H/O/N/P/Na/Cl）全部来自 Phase 8 的 118 真实元素 → **真实分子层 ↔ 真实元素层 已连通**

## 产物
| 文件 | 说明 |
|---|---|
| `11_真实数据/pubchem_mol_ingest.py` | PubChem PUG-REST 适配器（拉取+解析分子式→组合）|
| `11_真实数据/pubchem_mol_raw.json` | 中间产物（14 节点 / 53 边，权威 raw 格式）|
| `06_PoC/build_phase9.py` | 生成 viz 图 + Aura 增量（同 build_phase8.py 结构）|
| `06_PoC/graph_data_phase9.json` | 前端友好（PubChem 子集，14/53）|
| `06_PoC/etl/neo4j/phase9_aura_delta.json` | Aura 增量（load_neo4j 直接消费）|
| `06_PoC/graph_data_full.json` | 全量集成图（骨架+真实元素+真实分子 = 476/2610）|

## 上云结果（Neo4j Aura 免费实例）
- `load_neo4j.py --input phase9_aura_delta.json` 幂等 MERGE：节点 14/14、边 53/53
- Aura 现 **476 节点 / 2606 边**
- molecule 类 **40**（原 26 手搓 + 真实 14）
- 校验 6/6 全绿（exit=0）：Theorem 2、低置信推断边 45、gauss_bonnet→manifold 真实路径、跨源 same_as 全在

## 可视化
- 本地全量图 viz 服务：`GRAPH_DATA_FILE=graph_data_full.json python 06_PoC/viz_server.py`
  → http://127.0.0.1:8765/ （476 节点 / 2610 边，PID 17356）
- 前端已支持：Molecule 橙绿着色、composed_of / same_as 边过滤、点击查看真实属性（真实分子式/分子量/SMILES）
- 真实分子通过 composed_of 直接挂到真实元素（Phase 8）节点，形成"公式→分子→元素"闭环

## 关键踩坑
1. PubChem 对离子化合物返回**字母序**分子式（NaCl→"ClNa"、HCl→"ClH"），解析时按原子符号聚合即得正确配比，不影响结果。
2. phase9 子集内 27 个"悬空端点"是预期的：same_as 目标（种子节点）与 composed_of 目标（真实元素）均不在该子集内，但它们都已在 Aura 全量图中存在，推上去后 0 悬空。
3. 沿用 GBK 控制台乱码规避（UTF-8 重定向）与 PubChem 限流（每个请求后 sleep 0.25s）。

## 下一步
- (A1) 提供 SCP API Key 或完成 OpenKG ElementKG 2.0 申请 → 接入**完整**元素-官能团-分子-反应 KG
- (A2) 接入 ReactionAtlas / Open Reaction Database (ORD) 真实**反应**数据，喂实 reaction 层
- (B) 真实分子 same_as 对齐 Wikidata 化合物节点去重
- (C) 由真实分子式自动补全/校验骨架中手搓的 composed_of 边（交叉验证）
