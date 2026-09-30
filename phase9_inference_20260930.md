# Phase 9 推理生成层规模化 · 归档（2026-09-30）

## 目标
补全原四层架构的第四层（推理生成层），在真实 2077 节点图上重跑 GNN 推断 + LLM 假设 + 符号校验，发现并验证新跨域边。

## 做法
- `09_科研扩展/9_inference/gnn_infer.py`：桥接导向 GraphSAGE 链接预测（132 跨域正样本 / 3000 跨域负样本），枚举 55.5 万跨域候选对。
- 符号门控：R-PHY(pint 量纲) / R-CHEM(composed_of+reaction.equation，不依赖 RDKit) / R-MATH(sympy)。
- 显式挖掘可校验桥：跨域量纲一致 + 分子↔质量物理量摩尔质量桥。

## 结果
- 180 条跨域边 = 60 VERIFIED + 120 NEEDS_REVIEW（0 悬空、0 id 冲突）。
- Aura：2077 节点 / 7773 边（7593+180），校验 6/6 全绿。
- 本地 `06_PoC/graph_data_phase11.json`（2077/7777），viz 已切该图。

## 关键教训
- GNN 不能"全边训练"：跨域边仅占 1.7%，被淹没导致 top 候选是孤立节点退化向量（cosine=1.0 伪影）。必须桥接导向训练（正=跨域边）。
- 真实数据已填进大多数可符号证实的跨域关系，故纯符号可证新桥有限；其余诚实标 NEEDS_REVIEW（Phase 3 降权原则规模化落地）。
- RDKit 在 numpy 2.x 下不可用，化学边校验改用既有真实数据（composed_of / reaction.equation）。

## 文件
- 09_科研扩展/9_inference/gnn_infer.py, phase9_raw.json, phase9_candidates.json
- 06_PoC/build_phase11.py, graph_data_phase11.json
- 06_PoC/etl/neo4j/phase11_aura_delta.json
- 07_交付物/Phase9推理生成层报告_20260930.md
