# Phase 8 · ElementKG 2.0 真实数据接入（2026-09-30）

## Objective
把公式知识图谱的化学骨架（Phase 6/7 手搓种子）喂实：用真实、权威的 ElementKG 知识图谱
（Nature MI 2023 KANO「元素-官能团-分子」KG）替换手搓元素，让周期表/元素成为真实数据。

## Key Reasoning
- 骨架的物理世界锚点（Element/分子/反应）此前全是 curated 种子，缺真实属性。
- ElementKG 开源 `elementkg.owl`（4MB RDF/OWL）提供 118 元素的真实字面属性 +
  同周期/同族/同物态关系层，正是喂实骨架所需。
- 复用既有端到端管线：解析 OWL → 权威 raw JSON → `graph_export.build_graph_data` →
  `load_neo4j.py --input` 幂等 MERGE 推 Aura → viz 着色。
- 取舍：同物态是近全连通团（~4500 边毛球）→ 改节点属性；数值相似属性过密 → MVP 跳过；
  只保留 same_period / same_family 两条有结构的周期表关系边。

## Conclusions / Outcomes
- 适配器 `11_真实数据/elementkg_ingest.py` 解析 52413 三元组 →
  **118 真实元素节点（18 属性）+ 7 same_as 桥接 EL:* + 2037 真实关系边**。
- Aura 幂等推送：节点 118/118、边 2044/2044 → Aura 现 **462 节点 / 2553 边**，
  element 类 125（原 7 + 真实 118）。校验 6/6 全绿（exit=0）。
- 本地全量图 `graph_data_full.json`（462/2557）起 viz（http://127.0.0.1:8765/），
  前端补 same_period/same_family 配色。
- 看板 + 报告 + 记忆日志均已更新。

## Key Pitfalls
- rdflib 对象属性宾语是 `URIRef`，必须用 `isinstance(o, Literal)` 判定，
  `o.__class__.__name__=='Literal'` 会误判使关系层全丢（曾 0 关系边）。
- 同周期/同族属性含元素自身引用 → 跳过自环、仅连真实元素端点，否则 frozenset 退化单元素 unpack 失败。
