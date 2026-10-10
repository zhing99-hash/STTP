# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

// ============================================================
// 公式知识图谱 · Neo4j 加载与校验脚本（load.cypher）
// 部署包版本 · 配合 neo4j/ 目录（36 节点 / 52 边 / 0 悬空）
// ============================================================
// 版本：2026-09-28
//
// 【两种导入方式】
//
// (A) 离线 neo4j-admin import（推荐，大批量、首次部署）
//     先用 neo4j-admin 将 CSV 批量导入，再用本文件建索引/约束 + 校验：
//
//     # 1. 停止 Neo4j 服务
//     neo4j stop
//
//     # 2. 离线导入（CSV 路径指向 neo4j/ 目录）
//     neo4j-admin database import full formula-graph \
//       --nodes=neo4j/nodes.csv \
//       --relationships=neo4j/relationships.csv \
//       --delimiter=, \
//       --array-delimiter=";" \
//       --id-type=STRING \
//       --skip-duplicate-nodes=true \
//       --overwrite-destination=true
//
//     # 3. 设为默认库（可选，单机 5.x）
//     neo4j-admin database set-default formula-graph
//
//     # 4. 启动 Neo4j 后执行本文件建索引/约束并校验
//     cypher-shell -u neo4j -p formula_graph_2026 -f load.cypher
//
// (B) 在线 bolt 导入（load_neo4j.py 已完成 MERGE 后的校验）
//     若已通过 python load_neo4j.py 在线写入，则本文件用于补建索引/约束 + 校验：
//
//     cypher-shell -u neo4j -p formula_graph_2026 -f load.cypher
//
// ============================================================
// 标签清单（与 nodes.csv :LABEL 列一致，分号分隔多 Label）：
//   Entity（通用，所有节点均含）, Formula, Definition, Theorem, Lemma,
//   Symbol, PhysicalQuantity, Molecule, MathConcept
//
// 关系类型清单（与 relationships.csv :TYPE 列一致）：
//   derived_from, defines, proves, has_symbol,
//   dimensionally_consistent, chemical_reaction
// ============================================================


// ------------------------------------------------------------
// 1) 唯一性约束（幂等，IF NOT EXISTS）
//    Entity 为通用 Label，所有节点均带此标签，可建立全局 id 唯一约束。
// ------------------------------------------------------------

CREATE CONSTRAINT entity_id_unique IF NOT EXISTS
FOR (n:Entity) REQUIRE n.id IS UNIQUE;


// ------------------------------------------------------------
// 2) 索引（幂等，加速按类型/域/来源/置信度查询）
// ------------------------------------------------------------

CREATE INDEX entity_ntype IF NOT EXISTS FOR (n:Entity) ON (n.ntype);
CREATE INDEX entity_domain IF NOT EXISTS FOR (n:Entity) ON (n.domain);
CREATE INDEX entity_source IF NOT EXISTS FOR (n:Entity) ON (n.source);
CREATE INDEX entity_confidence IF NOT EXISTS FOR (n:Entity) ON (n.confidence);

// 关系属性索引（Neo4j 5.x 支持关系属性索引）
CREATE INDEX rel_confidence IF NOT EXISTS FOR ()-[r:derived_from]-() ON (r.confidence);
CREATE INDEX rel_kind IF NOT EXISTS FOR ()-[r:derived_from]-() ON (r.kind);


// ------------------------------------------------------------
// 3) 幂等 MERGE 载入（在线模式补充保障）
//    若已通过 neo4j-admin import 或 load_neo4j.py 写入，
//    以下 MERGE 不会重复创建（幂等），可安全执行。
//    按 id 匹配节点，设属性，按 ntype/type 设标签。
//    完整全量导入请用 (A) 离线 neo4j-admin 或 python load_neo4j.py。
// ------------------------------------------------------------

// 3.1 定义类节点（Entity + Formula + Definition）
MERGE (n:Entity {id: 'MX:def:manifold'})
  SET n:Formula:Definition,
      n.ntype = 'definition', n.name = 'M', n.domain = 'math.DG',
      n.confidence = 1.0, n.explicit_or_inferred = 'explicit', n.source = 'mathxiv',
      n.type = 'definition', n.local_id = 'def:manifold', n.meaning = '流形',
      n.created_at = '2026-09-28T15:33:30+08:00', n.version = 'v0.1';

MERGE (n:Entity {id: 'MX:def:tangent_space'})
  SET n:Formula:Definition,
      n.ntype = 'definition', n.name = 'T_p M', n.domain = 'math.DG',
      n.confidence = 1.0, n.explicit_or_inferred = 'explicit', n.source = 'mathxiv',
      n.type = 'definition', n.local_id = 'def:tangent_space', n.meaning = '切空间',
      n.created_at = '2026-09-28T15:33:30+08:00', n.version = 'v0.1';

MERGE (n:Entity {id: 'MX:def:riemannian_metric'})
  SET n:Formula:Definition,
      n.ntype = 'definition', n.name = 'g_{ij}', n.domain = 'math.DG',
      n.confidence = 1.0, n.explicit_or_inferred = 'explicit', n.source = 'mathxiv',
      n.type = 'definition', n.local_id = 'def:riemannian_metric', n.meaning = '黎曼度量',
      n.created_at = '2026-09-28T15:33:30+08:00', n.version = 'v0.1';

MERGE (n:Entity {id: 'MX:def:levi_civita'})
  SET n:Formula:Definition,
      n.ntype = 'definition', n.name = '\\nabla', n.domain = 'math.DG',
      n.confidence = 1.0, n.explicit_or_inferred = 'explicit', n.source = 'mathxiv',
      n.type = 'definition', n.local_id = 'def:levi_civita', n.meaning = 'Levi-Civita 联络',
      n.created_at = '2026-09-28T15:33:30+08:00', n.version = 'v0.1';

// 3.2 定理类节点（Entity + Formula + Theorem）
MERGE (n:Entity {id: 'MX:thm:gauss_bonnet'})
  SET n:Formula:Theorem,
      n.ntype = 'theorem', n.name = '∫ K dA = 2πχ(M)', n.domain = 'math.DG',
      n.confidence = 1.0, n.explicit_or_inferred = 'explicit', n.source = 'mathxiv',
      n.type = 'theorem', n.local_id = 'thm:gauss_bonnet', n.meaning = 'Gauss-Bonnet 定理',
      n.created_at = '2026-09-28T15:33:30+08:00', n.version = 'v0.1';

// 3.3 引理类节点（Entity + Formula + Lemma）
MERGE (n:Entity {id: 'MX:lem:partition_unity'})
  SET n:Formula:Lemma,
      n.ntype = 'lemma', n.name = 'partition of unity', n.domain = 'math.DG',
      n.confidence = 1.0, n.explicit_or_inferred = 'explicit', n.source = 'mathxiv',
      n.type = 'lemma', n.local_id = 'lem:partition_unity', n.meaning = '单位分解引理',
      n.created_at = '2026-09-28T15:33:30+08:00', n.version = 'v0.1';

// 3.4 符号类节点（Entity + Symbol）
MERGE (n:Entity {id: 'MX:sym:chi'})
  SET n:Symbol,
      n.ntype = 'symbol', n.name = 'χ', n.domain = 'math.DG',
      n.confidence = 1.0, n.explicit_or_inferred = 'explicit', n.source = 'mathxiv',
      n.type = 'symbol', n.local_id = 'sym:chi', n.meaning = 'Euler 示性数',
      n.created_at = '2026-09-28T15:33:30+08:00', n.version = 'v0.1';

// 3.5 物理量节点（Entity + PhysicalQuantity，合成节点）
MERGE (n:Entity {id: 'MX:phy:newton2'})
  SET n:PhysicalQuantity,
      n.ntype = 'physical_quantity', n.name = 'F=ma', n.domain = 'physics',
      n.confidence = 1.0, n.explicit_or_inferred = 'inferred', n.source = 'llm_hypothesis',
      n.type = 'physical_quantity', n.local_id = 'newton2', n.meaning = '牛顿第二定律',
      n.created_at = '2026-09-28T15:33:30+08:00', n.version = 'v0.1';

// 3.6 分子节点（Entity + Molecule，合成节点）
MERGE (n:Entity {id: 'MX:chem:co2'})
  SET n:Molecule,
      n.ntype = 'molecule', n.name = 'CO₂', n.domain = 'chemistry',
      n.confidence = 1.0, n.explicit_or_inferred = 'inferred', n.source = 'llm_hypothesis',
      n.type = 'molecule', n.local_id = 'co2', n.meaning = '二氧化碳（分子）',
      n.created_at = '2026-09-28T15:33:30+08:00', n.version = 'v0.1';

// 3.7 数学概念节点（Entity + MathConcept，合成节点）
MERGE (n:Entity {id: 'MX:math:binomial'})
  SET n:MathConcept,
      n.ntype = 'math_concept', n.name = '(a+b)²', n.domain = 'math',
      n.confidence = 1.0, n.explicit_or_inferred = 'inferred', n.source = 'llm_hypothesis',
      n.type = 'math_concept', n.local_id = 'binomial', n.meaning = '二项式展开',
      n.created_at = '2026-09-28T15:33:30+08:00', n.version = 'v0.1';

// 3.8 关系 MERGE（典型边示例；完整全量请用 neo4j-admin 或 load_neo4j.py）
MERGE (a:Entity {id: 'MX:def:tangent_space'})-[:derived_from]->(b:Entity {id: 'MX:def:manifold'});
MERGE (a:Entity {id: 'MX:def:riemannian_metric'})-[:derived_from]->(b:Entity {id: 'MX:def:tangent_space'});
MERGE (a:Entity {id: 'MX:def:levi_civita'})-[:derived_from]->(b:Entity {id: 'MX:def:riemannian_metric'});
MERGE (a:Entity {id: 'MX:thm:gauss_bonnet'})-[:proves]->(b:Entity {id: 'MX:lem:partition_unity'});

// 完整在线 MERGE 请通过 python load_neo4j.py 执行（支持 APOC 批量写入）。


// ------------------------------------------------------------
// 4) 校验查询（6 类，与 verify_deploy.py / load_neo4j.py 口径一致）
// ------------------------------------------------------------

// 4.1 节点总数（预期 36）
MATCH (n:Entity) RETURN count(n) AS node_count;

// 4.2 边总数（预期 52）
MATCH ()-[r]->() RETURN count(r) AS edge_count;

// 4.3 Theorem 列表（按 ntype 或 Label 判定）
MATCH (n:Entity)
WHERE n.ntype = 'theorem' OR 'Theorem' IN labels(n)
RETURN n.id AS id, n.name AS name
ORDER BY n.id;

// 4.4 低置信推断边（confidence < 0.85 且 inferred/llm_inferred）
MATCH (a)-[r]->(b)
WHERE r.explicit_or_inferred IN ['inferred', 'llm_inferred']
  AND r.confidence < 0.85
RETURN a.id AS source, b.id AS target, type(r) AS rel_type,
       r.confidence AS confidence, r.kind AS kind
ORDER BY confidence ASC;

// 4.5 路径：gauss_bonnet → manifold（最长 6 跳）
MATCH p = (a:Entity {id: 'MX:thm:gauss_bonnet'})-[:*1..6]->(b:Entity {id: 'MX:def:manifold'})
RETURN [n IN nodes(p) | n.id] AS path, length(p) AS hops
LIMIT 5;

// 4.6 按类型统计节点
MATCH (n:Entity)
RETURN COALESCE(n.ntype, head(labels(n))) AS type, count(*) AS count
ORDER BY count DESC;

// ============================================================
// END OF FILE
// ============================================================
