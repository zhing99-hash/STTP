// ============================================================
// 公式知识图谱 · 查询语句集（queries.cypher）
// 对应 queries.py 中的 CYPHER 字典，二者保持一致。
// 参数用 $name 占位；在 Neo4j Browser 中可改为字面量调试。
// ============================================================

// ------------------------------------------------------------
// 1) get_node(id) —— 取单个节点
// ------------------------------------------------------------
MATCH (n {id:$id})
RETURN n.id AS id, labels(n) AS labels, properties(n) AS props;


// ------------------------------------------------------------
// 2) get_neighbors(id) —— 取邻居（含方向 / 边类型 / 属性）
//    direction: 'out' 表示从 id 指出的边，'in' 表示指入的边
// ------------------------------------------------------------
MATCH (n {id:$id})-[r]-(m)
RETURN m.id AS id, TYPE(r) AS type, properties(r) AS props,
       CASE WHEN startNode(r).id = $id THEN 'out' ELSE 'in' END AS direction;


// ------------------------------------------------------------
// 3) subgraph_by_confidence(threshold) —— 取置信度 >= 阈值的边构成的子图
// ------------------------------------------------------------
MATCH (a)-[r]->(b)
WHERE r.confidence >= $threshold
RETURN a.id AS start, b.id AS end, TYPE(r) AS type, properties(r) AS props;


// ------------------------------------------------------------
// 4) paths_between(a, b) —— 两点间的有向路径（最长 5 跳）
// ------------------------------------------------------------
MATCH p = (a {id:$a})-[:*1..5]->(b {id:$b})
RETURN [n IN nodes(p) | n.id] AS path
LIMIT 20;


// ------------------------------------------------------------
// 5) list_by_type(t) —— 按标签或 type 属性列节点
// ------------------------------------------------------------
MATCH (n)
WHERE $t IN labels(n) OR n.type = $t
RETURN n.id AS id, labels(n) AS labels, properties(n) AS props;


// ------------------------------------------------------------
// 6) filter_edges_by_kind(kind) —— 按 kind 过滤边
// ------------------------------------------------------------
MATCH (a)-[r]->(b)
WHERE r.kind = $kind
RETURN a.id AS start, b.id AS end, TYPE(r) AS type, properties(r) AS props;


// ============================================================
// 附：常用分析查询（非接口，供调试）
// ============================================================

// A. 显式依赖链：某定理一路 derived_from 到定义
MATCH p = (t:Formula {id:$a})-[:derived_from*1..6]->(d:Definition)
RETURN [n IN nodes(p) | n.id] AS chain, length(p) AS hops
ORDER BY hops;

// B. 高风险边清单（LLM 推断且置信度偏低，待 SymPy 校验）
MATCH (a)-[r]->(b)
WHERE r.kind = 'llm_inferred' AND r.confidence < 0.85
RETURN a.id AS a, b.id AS b, r.confidence AS conf
ORDER BY conf ASC;

// C. 符号共享簇（哪些公式共用同一符号）
MATCH (f:Formula)-[:has_symbol]->(s:Symbol)<-[:has_symbol]-(g:Formula)
WHERE f.id < g.id
RETURN s.name AS symbol, collect(DISTINCT f.id) AS formulas;

// D. 按来源统计节点与边
MATCH (n) RETURN n.source AS source, count(*) AS nodes ORDER BY nodes DESC;
MATCH ()-[r]->() RETURN r.source AS source, count(*) AS edges ORDER BY edges DESC;

// E. 对齐去重后校验：同 wikidata_qid 的实体簇
MATCH (n) WHERE n.wikidata_qid IS NOT NULL
RETURN n.wikidata_qid AS qid, collect(n.id) AS members, count(*) AS c
ORDER BY c DESC;
