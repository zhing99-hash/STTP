# Formulagraph Phase 5.C 执行记录（2026-09-29）

- **任务**：Phase 5.C 跨源实体对齐 — 实现 Entity Linker，给定源节点找出其它源同实体候选（rank+置信度），产出 `same_as` 候选。
- **位置**：`09_科研扩展/5C_entity_linker/`

## 做了什么
- `entity_linker.py`：`EntityLinker` 类 + 三相似度函数（`label_similarity`/`symbol_match`/`definition_overlap_pair`）+ 可配权重（0.4/0.1/0.3/0.2）+ 独立单测。
- `linker_demo.py`：从 `06_PoC/etl/neo4j/neo4j_ready.json` 提取 36 个 MX 节点，贴源标签构模拟多源池（MX+MG+PB+EK+WD，68 个）；选 6 个 MX 节点跑链接。
- `data/nodes_pool.json`（68）、`data/sample_nodes.json`（20，3 源）、`aligned_candidates.json`（字段：source_node/target_source/target_id/score/matched_by）。
- `README.md`、`phase5C_report.md`。

## 实跑结果
- `python entity_linker.py`：三函数单测 ALL PASS。
- `python linker_demo.py`：60 条候选（≥10 ✅），7 条 high-confidence ≥0.7（≥3 ✅），VERIFY PASS。
- 真实匹配示例：`MX:sym:pi`↔`wd:Q_pi`(0.97)、`MX:def:manifold`↔`wd:Q_manifold`(0.87)、`MX:chem:co2`↔`ek:molecule:CO2`(0.80) 等。

## 约束
- 未改 00–08 任何文件；纯本地数据+算法，无网络请求（WD 节点为本地占位，真实 Wikidata 查询留待 Phase 5.A，README §5 已写明接入方式）。
- 修复一处踩坑：早期 WD 占位 id 重复（`wd:Q?`）导致定义错配，已改为按实体对象直接 transform 并赋唯一 id。

## 后续
- Phase 5.A 接入真实 Wikidata 后，复用本模块打分，输出格式不变。
