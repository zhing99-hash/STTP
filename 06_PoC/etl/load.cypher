# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

// ============================================================
// 公式知识图谱 · Neo4j 导入脚本 (load.cypher)
// 由 etl_pipeline.py 自动生成 ｜ 节点 22 条 / 边 44 条
// Schema 版本：v0.1
// ============================================================

// ------------------------------------------------------------
// (A) 首建：neo4j-admin database import（离线、大批量、性能最佳）
//     需停库后执行；CSV 表头为 :ID / :LABEL / :START_ID / :END_ID / :TYPE。
// ------------------------------------------------------------
// neo4j-admin database import full formula-graph \
//   --nodes=Formula=nodes.csv \
//   --nodes=Symbol=nodes.csv \
//   --relationships=relationships.csv \
//   --delimiter=, \
//   --id-type=STRING \
//   --skip-duplicate-nodes=true \
//   --overwrite-destination=true

// ------------------------------------------------------------
// (B) 唯一性约束（必须先建，MERGE 才能高效防重）
// ------------------------------------------------------------
CREATE CONSTRAINT formula_id IF NOT EXISTS FOR (n:Formula) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT symbol_id  IF NOT EXISTS FOR (n:Symbol)  REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT def_id     IF NOT EXISTS FOR (n:Definition) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT pq_id      IF NOT EXISTS FOR (n:PhysicalQuantity) REQUIRE n.id IS UNIQUE;

// ------------------------------------------------------------
// (C) 增量写入：APOC 批量 MERGE 节点（幂等）
// ------------------------------------------------------------
// 读完 normalized.json 后由 load_neo4j.py 逐批调用；等价示意：
CALL apoc.periodic.iterate(
  "UNWIND $rows AS row RETURN row",
  "CALL apoc.merge.node(row.labels, {id: row.id}, row.props) YIELD node RETURN node",
  {batchSize: 500, params: {rows: $rows}}
);

// 无 APOC 时的等价写法（按标签分别 MERGE）：
// UNWIND $rows AS row
// MERGE (n:Formula {id: row.id})
// SET n += row.props;

// ------------------------------------------------------------
// (D) 增量写入：边（动态类型用 apoc.merge.relationship）
// ------------------------------------------------------------
MATCH (a {id: $start}), (b {id: $end})
CALL apoc.merge.relationship(
  a, $type, {}, $props, b
) YIELD rel
SET rel += $props
RETURN rel;

// ------------------------------------------------------------
// (E) Align 后按 wikidata_qid 去重（Dedupe 阶段）
// ------------------------------------------------------------
// 同 Q-id 的 Symbol / MathConcept 合并为一节点，保留多来源溯源：
MATCH (n)
WHERE n.wikidata_qid IS NOT NULL
WITH n.wikidata_qid AS qid, COLLECT(n) AS nodes
WHERE SIZE(nodes) > 1
CALL apoc.refactor.mergeNodes(nodes,
     {properties: 'discard', mergeRels: true}) YIELD node
RETURN count(node) AS merged;

// 去重前可先确认候选簇：
// MATCH (n) WHERE n.wikidata_qid IS NOT NULL
// RETURN n.wikidata_qid AS qid, count(*) AS c ORDER BY c DESC LIMIT 20;

// ------------------------------------------------------------
// (F) 示例查询
// ------------------------------------------------------------
// F1. 某公式的显式引用依赖
MATCH (f:Formula {id: 'MX:thm:riemann_curvature'})-[r:derived_from]->(g)
WHERE r.explicit_or_inferred = 'explicit'
RETURN g.id, r.confidence ORDER BY r.confidence DESC;

// F2. 只看 LLM 推断边（需人工/符号校验）
MATCH (a)-[r]->(b) WHERE r.kind = 'llm_inferred'
RETURN a.id, b.id, r.confidence ORDER BY r.confidence ASC;

// F3. 公式用到的符号
MATCH (f:Formula {id: 'MX:thm:gauss_bonnet'})-[:has_symbol]->(s:Symbol)
RETURN s.name, s.meaning;
