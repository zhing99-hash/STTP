# Phase 5.B 执行记录 — GNN 依赖推断

**日期**：2026-09-29 · **Agent**：Phase 5.B 子任务（GNN）· **状态**：✅ 完成

## 一句话结论
用纯 PyTorch（无 torch_geometric）实现 2 层 GraphSAGE，在 36 节点 / 52 边图上训练链接预测，
loss 从 0.73 降到 **0.37**，输出 **20 条**互异置信度的候选边，含语义合理边
`gauss_bonnet↔rho_alpha`（has_symbol）、`manifold↔gauss_bonnet`（proves）。

## 产出（均在 `09_科研扩展/5B_gnn/`）
- `gnn_model.py` / `train.py` / `predict.py` — 模型、训练、推断
- `checkpoint.pt`、`loss_curve.png`、`phase5_gnn_edges.json`
- `README.md`、`phase5B_report.md`

## 实跑结果
- 训练：epoch 200，loss 0.7285→0.3713（best 0.3637），acc 0.955，CPU 2.7s
- 推断：586 候选非边打分，top-20，置信度 0.6940–0.7311，**20/20 互异**
- 验收：loss<0.5 ✅ / ≥10 边且置信度全不同 ✅ / 含语义合理边 ✅

## 关键设计
- 特征 29 维：类型 one-hot(12) + 标签 hash 嵌入(16, md5 确定性) + 归一化度数(1)
- 训练 raw dot 打分（loss 可降 <0.5）；推断 cosine 打分（避免 sigmoid 饱和成常数）
- 图按无向处理：C(36,2)=630 − 44 已有无向边（52 有向含 8 平行边）= 586 候选
- 输出边 `source`/`target` 为节点 id，数据出处放 `data_source`（修复任务示例重复键）

## 备注
- 任务示例 `manifold↔tangent_space` 已为显式边（derived_from），改用 `manifold↔gauss_bonnet` 作等效语义示例。
- 未修改 `00`–`08` 任何文件；未安装禁用依赖。
