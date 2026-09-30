# Phase 5 科研级扩展 — 任务归档

**时间**：2026-09-29
**目标**：在 Phase 0–4 + Aura 部署（36 节点/52 边）基础上，完成科研级扩展（跨源对齐 + GNN 推断 + 真实数据源接入），并集成验证。

## 完成内容

### 五路扩展全部落地
1. **5.A Wikidata 实时对齐**（`09_科研扩展/5A_wikidata/`）
   - 适配器 `wikidata_adapter.py`（SPARQL + wbsearch + 缓存 + 排歧义）
   - 产出 `same_as_edges.json`：**19 条 same_as 边，全部真实 QID**（28 SPARQL 成功 / 10 失败后 wbsearch 兜底）
   - 样例：gauss_bonnet→Q742833、manifold→Q3552958、co2→Q1997、energy→Q11379

2. **5.B GNN 依赖推断**（`09_科研扩展/5B_gnn/`）
   - 纯 torch GraphSAGE（无 torch_geometric），2 层消息传递 + dot-product decoder
   - 训练 loss 0.7285→0.3713（acc 0.955，CPU 2.6s），checkpoint 已存
   - 推断 586 候选 → **20 条互异新边，置信 0.6940–0.7311**，source=Phase5.GNN

3. **5.C 跨源实体对齐**（`09_科研扩展/5C_entity_linker/`）
   - 多特征加权（label 0.4 + symbol 0.1 + definition 0.3 + domain 0.2，rapidfuzz + TF-IDF）
   - 68 节点池 → **60 候选 / 7 高置信 ≥0.7**，matched_by 可解释

4. **5.D ElementKG / ReactionAtlas 适配器**（`09_科研扩展/5D_adapters/`）
   - 5.A+5.D 子 agent 被系统熔断（检测潜在循环，非逻辑错误，其 5.A 已正确交付）；**5.D 由主协调方直接补全**
   - 修 bug：边 dict 重复 `source` 键（"ElementKG" 覆盖了 mid），改 `data_source`
   - `elementkg_adapter.py`：合成 **30 反应 / 11 物质 / 90 边** demo 跑通
   - `reactionatlas_adapter.py`：sqlite3 内存库等价 PostgreSQL，**25 反应 + 查询** demo 跑通

5. **5.E 集成 + 可视化**（`09_科研扩展/5E_integration/` + `06_PoC/`）
   - `phase5_integration.py` 三路合并 → **36 节点 / 98 边（Δ+46）**，无悬空
   - 输出 `06_PoC/etl/neo4j/phase5_neo4j_ready.json` 与 `06_PoC/graph_data_phase5.json`
   - `viz_server.py` 加 `GRAPH_DATA_FILE` + `PORT` 环境变量；实测返回 36/98，边类型分布合理

## 验证
- 5.A 适配器 import + 缓存加载 ✅
- 5.B / 5.C / 5.D 全部 demo 实跑通过 ✅
- 5.E 集成稳定（重跑一致）✅
- viz_server 加载 Phase 5 图 36/98 ✅

## 交付物
- `07_交付物/Phase5科研级扩展报告_20260929.md`（5.3KB 主报告）
- 看板 `00_项目管理/团队分工与进度.md` Phase 5.A–E 全标 ✅
- `09_科研扩展/` 完整 5A–5E 代码 + 数据

## 追加：Phase 5 图谱已推上 Aura 云（执行选项 A）

**触发**：用户确认「可以直接执行 A」。

**关键风险与处理**：
- Aura 现有 52 边；若重推完整 98 边且 `load_neo4j.py` 关系用 CREATE，会重复基线 52 边。
- 故采用「加法性 delta」：只推 46 条新边。
- **发现集成 bug**：`phase5_integration.py` 把 5.A/5.B/5.C 新增边的 `source` 错写成管线名（`Phase5.Wikidata` 等），且 WD 目标节点未入节点列表 → `phase5_neo4j_ready.json` / `graph_data_phase5.json` 的新增边指向不存在的节点。
- **解法**：新建 `build_aura_delta.py`，直接从 5.A/5.B/5.C **原始源文件**（id 正确）重建 delta：36 基线 MX 节点(幂等 MERGE) + 26 新增节点(19 Wikidata QID + 5 wd + 1 ek + 1 pb) + 46 新增边(19 same_as + 20 GNN + 7 跨源)，0 悬空。

**执行证据**：
- 推送前 Aura：36 节点 / 52 边
- 推送后 Aura：**62 节点 / 98 边**（Δ +26 节点 +46 边）
- 类型统计：Entity 22 / wikidata 19 / math_concept 6 / wd 5 / physical_quantity 4 / molecule 3 / symbol 1 / ek 1 / pb 1
- 验证查询：`gauss_bonnet→manifold` 真实路径仍存在
- 命令：`load_neo4j.py --input _aura_delta.json`（URI `neo4j+ssc://853a33bc.databases.neo4j.io`，DB `853a33bc`）

**遗留待修（非阻塞）**：`phase5_integration.py` 的 source 映射 bug 导致 `phase5_neo4j_ready.json` 与 `graph_data_phase5.json` 本地文件里新增边 id 错误。需修脚本后重生成这两个本地文件，使本地可视化与 Aura 一致。

## 追加 2：集成 bug 已修复（同会话后续）

**根因**：`phase5_integration.py` 三处 `edges.append({...})` 都写了**重复 `"source"` 键**——先 `"source": src`（正确节点 id），后又 `"source": "Phase5.Wikidata/GNN/EntityLinker"`（管线名），Python dict 字面量后者覆盖前者 → 边 source 变成管线名；且 WD/EK/PB 目标节点未加入 `nodes` 列表。与 `elementkg_adapter.py` 的重复键问题同源。

**修复**：
1. 删除三处重复 `"source"` 键，管线名改为 `"data_source"`（保留溯源信息）
2. 在 5.A / 5.C 块中，对 WD/EK/PB 目标节点调用新建逻辑，补入 `nodes` 列表（含 labels=`[Entity, WikidataEntity/ElementEntity/PhysicsEntity]` + props）
3. 同步修可视化导出块里同源重复键（`"source"`→`"data_source"`）；补 `SRC_LABEL` 定义

**重跑验证**（exit 0）：
- `phase5_neo4j_ready.json`：62 节点 / 98 边，`Phase5.` 伪 source/target = 0，悬空边 = 0
- `graph_data_phase5.json`：62 节点 / 98 边，悬空边 = 0，伪 source = 0
- 26 个新增节点（WD:Q... 等外部实体）正确入列
- **本地文件现已与 Aura（62/98）完全一致**，bug 闭环

## 建议后续（未执行，待确认）
- A. 将 98 边推送到 Aura（复用 load_neo4j.py + neo4j+ssc://，加法性、非破坏）
- B. GNN 真实训练（等 ElementKG 真实数据）
- C. 19 个 Wikidata QID 作种子扩展跨语言标签
- D. Cytoscape/MathJax 离线 vendored 以便无网预览
