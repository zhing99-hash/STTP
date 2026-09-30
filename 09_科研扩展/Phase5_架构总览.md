# Phase 5 科研级扩展 — 架构总览

> 在 Phase 0–4 已建成的 36 节点 / 52 边图谱 + Aura 云端部署基础上，引入科研级方法学：跨源对齐、GNN 推断、真实数据源接入。

## 一、Phase 5 子任务矩阵

| 子阶段 | 负责 | 核心方法 | 关键依赖 | 沙箱可行性 |
|---|---|---|---|---|
| **5.A Wikidata 实时对齐** | 适配器 agent | SPARQL 端点查询 → `same_as` 边 | SPARQLWrapper | ✅ 端点可达，真实数据 |
| **5.B GNN 依赖推断** | 模型 agent | GraphSAGE-style 链接预测 | torch 2.14 CPU | ✅ CPU 可训练 |
| **5.C 跨源实体对齐** | 链接 agent | 多特征加权相似度 | rapidfuzz + sklearn | ✅ 纯本地 |
| **5.D ElementKG/ReactionAtlas 适配器** | 适配器 agent | 真实 schema + 合成样本 | 仅 schema（无真数据） | ✅ 接口完整 |
| **5.E 集成 PoC** | 主协调方 | 三路合并 + 可视化 | 前 4 项产出 | ✅ |

## 二、数据流向

```
现有 36节点/52边 (Aura 已部署)
       │
       ├── 5.A Wikidata ── same_as 边 (WD:Qxxxx) ─┐
       ├── 5.B GNN ──────── inferred 边 (proves/derived_from) ├─→ phase5_integration.py
       ├── 5.C Entity Linker ── cross-source same_as 边 ──────┘        │
       │                                                             ▼
       └── 5.D Adapters ── ElementKG/ReactionAtlas 接口（接入点预留）   合并后 phase5_neo4j_ready.json
                                                                              │
                                                                              ▼
                                                                   graph_data_phase5.json (可视化)
                                                                              │
                                                                              ▼
                                                                   viz_server.py (端口 8765, get_backend("auto"))
```

## 三、集成产物（Phase 5.E 输出）

| 文件 | 用途 |
|---|---|
| `06_PoC/etl/neo4j/phase5_neo4j_ready.json` | 合并后完整数据集（不改原文件） |
| `06_PoC/graph_data_phase5.json` | 前端可视化用 |
| `09_科研扩展/5E_integration/phase5_metrics.json` | 集成指标（节点/边增量） |

## 四、与 Phase 0–4 的关系

- **Phase 3** 的 LLM 推断边（8 VERIFIED + 8 REJECTED）已导入 52 边，是 Phase 5 的起点
- **Phase 4** 可视化的 `get_backend("auto")` 自动切后端，Phase 5 数据集可直接对接
- **Aura 部署**的 `same_as` / `proves` / `derived_from` 边类型在 Cypher 25 中全小写，5.A/5.B 产出的边类型需遵守此约定

## 五、已知约束（沙箱）

1. 无 Java/Docker → 真实 Neo4j 服务器跑不了；Aura 云端 / Kuzu 嵌入式 / NetworkX 兜底三条路线并存
2. 真实数据集（ElementKG 100+GB / ReactionAtlas 26GB / Wikidata 130GB dump）沙箱下不去 → 用 SPARQL API + 合成样本代替
3. torch 仅 CPU（无 CUDA）→ GNN 训练规模受限，证明方法学可行即可，不是生产级训练
4. 网络礼貌节流：Wikidata SPARQL 每次查询间隔 ≥ 3s
