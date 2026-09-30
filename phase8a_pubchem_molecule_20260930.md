# Phase 8.A · PubChem 真实分子层接入（2026-09-30）

## Objective
执行选项 A（喂实分子/反应层真实数据）。原始目标 ElementKG 2.0 完整数据 / ReactionAtlas 被凭证卡住
（SCP API 401 无 Key、OpenKG SSL 过期+需填表、ReactionAtlas 需授权），改用免费无 Key 的真实源
**PubChem PUG-REST** 喂实分子层，与 Phase 8 元素层同构。

## Key Reasoning
- 分子层现状是手搓种子（MO:/BC:/IC:/OM:*），缺真实属性与真实组成。
- PubChem PUG-REST 免费、限流宽松、返回真实分子式/分子量/SMILES/IUPAC/CID，是最可达的真实分子源。
- 由真实分子式解析元素配比 → 派生 composed_of 边连到 Phase 8 的 118 真实元素(EK:el:*)，
  从而把"真实分子层"与"真实元素层"连通，形成 公式→分子→元素 闭环。
- 复用既有管线：PubChem 拉取 → 权威 raw → build_graph_data → load_neo4j --input 幂等 MERGE → viz。

## Conclusions / Outcomes
- `11_真实数据/pubchem_mol_ingest.py` 拉取 14 个真实分子（水/CO₂/CH₄/O₂/乙烷/乙烯/苯/乙醇/NaCl/HCl/Cl₂/葡萄糖/ATP/甘氨酸）
  → `pubchem_mol_raw.json`：**14 真实分子节点(PC:mol:<CID>) + 20 same_as 桥接种子 + 33 真实 composed_of 边**。
- Aura 幂等推送：节点 14/14、边 53/53 → Aura 现 **476 节点 / 2606 边**，molecule 类 40（原 26 + 真实 14）。
  校验 6/6 全绿（exit=0），gauss_bonnet→manifold 路径 / 45 低置信边 / 跨源 same_as 全在。
- 本地全量图 `graph_data_full.json`（476/2610）起 viz（PID 17356，http://127.0.0.1:8765/）；
  前端 composed_of/same_as 边过滤 + 真实属性查看已生效。
- 看板 + 报告 + 记忆日志均已更新。

## Key Pitfalls
- PubChem 离子化合物返回字母序分子式（NaCl→"ClNa"、HCl→"ClH"），聚合解析不影响配比。
- phase9 子集 27 悬空端点为预期（same_as 目标=种子、composed_of 目标=真实元素均不在此子集，但都在 Aura 全量图内）。

## 受阻项（需用户提供）
- SCP-HUB-API-KEY 或 OpenKG ElementKG 2.0 申请 → 接入完整元素-官能团-分子-反应 KG
- ReactionAtlas / Open Reaction Database 真实反应访问 → 喂实 reaction 层
