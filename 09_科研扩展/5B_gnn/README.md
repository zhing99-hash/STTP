# Phase 5.B — GNN 依赖推断 (GraphSAGE Link Prediction)

> 跨学科公式知识图谱 · 科研扩展 Phase 5.B
> 目标：用纯 PyTorch 实现的 GraphSAGE 风格 GNN，在现有 36 节点 / 52 边图上
> 训练链接预测（link prediction），推断可能缺失的边，作为科研方法学验证。

---

## 1. 架构 (Architecture)

```
                         ┌─────────────────────────────────────┐
   neo4j_ready.json  ──▶ │  Feature Extraction (gnn_model.py)  │
   (36 nodes/52 edges)   │   · type one-hot      (12 dim)      │
                         │   · label-hash emb    (16 dim)      │
                         │   · normalized degree ( 1 dim)      │
                         │   => 29-dim node feature x          │
                         └───────────────┬─────────────────────┘
                                         │ x, undirected adjacency
                                         ▼
                         ┌─────────────────────────────────────┐
                         │  GraphSAGE (2 layers, pure torch)   │
                         │   Layer l:                          │
                         │    h_v = ReLU( W_l · concat(        │
                         │        h_v, mean_{u∈N(v)} h_u ) )    │
                         │   dropout = 0.2                     │
                         │   => 32-dim node embedding h        │
                         └───────────────┬─────────────────────┘
                                         │ h (N×32)
                  ┌──────────────────────┴───────────────────────┐
                  ▼                                               ▼
        TRAIN (train.py)                              INFER (predict.py)
   positives = 52 existing edges               score(a,b) = cosine(h_a, h_b)
   negatives = 52 sampled non-edges            conf       = sigmoid(score)
   loss = BCEWithLogitsLoss(dot)               rank ALL 586 candidate pairs
   -> checkpoint.pt, loss_curve.png            -> phase5_gnn_edges.json (top-20)
```

**关键设计**
- **不使用 torch_geometric / DGL / cudf**（环境约束）。消息传递与邻居均值聚合全部用
  纯 `torch` 实现（`GraphSAGELayer.forward` 中对邻接表做 `x[neighbors].mean(0)`）。
- **训练用 raw dot-product 打分**（`LinkPredictor.score`），使 loss 能稳定降到 < 0.5；
  **推断用 cosine 相似度**（`LinkPredictor.score_cosine`，L2 归一化），把分数限制在
  [-1,1]，避免 ReLU 非负 embedding 导致 sigmoid 全部饱和成 1.0，保证置信度分布有区分度。
- 图按**无向**处理：`C(36,2)=630` 个节点对，扣掉 44 个已有无向边（52 条有向边中
  有 8 条是平行/多重边，0 条自环），得 **586 个候选非边**。

---

## 2. 节点特征 (29-dim)

| 分量 | 维度 | 说明 |
|---|---|---|
| 类型 one-hot | 12 | 任务枚举的 12 类（Formula/MathConcept/Symbol/Definition/Theorem/Lemma/Element/Molecule/Reaction/Unit/PhysicalQuantity/Entity）。取最具体的 label 作为主类型（Definition > Symbol > Formula > … > Entity）。 |
| 标签 hash 嵌入 | 16 | 由节点 id 经 `md5` 确定性播种的 `np.random.default_rng` 生成（与进程随机盐无关，可复现）。 |
| 归一化度数 | 1 | `deg / max_deg ∈ [0,1]`。 |

> 注：任务文本写 "11+16+1=28"，但显式枚举了 12 类（0..11），故采用 12 维 one-hot，
> 实际特征维度 **29**。这是有意的取舍并已记录。

---

## 3. 运行命令 (Commands)

```powershell
# 1) 训练（CPU ~3s）。输出 checkpoint.pt + loss_curve.png
cd 09_科研扩展\5B_gnn
& "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe" train.py

# 2) 推断（加载 checkpoint，输出 top-20 候选边）
& "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe" predict.py
```

> 必须在 `5B_gnn` 目录下运行，以便 `import gnn_model` 解析到同目录模块。
> 数据路径在 `gnn_model.py` 中按 `REPO_ROOT/06_PoC/etl/neo4j/neo4j_ready.json` 自动推导。

---

## 4. 超参数 (Hyperparameters)

| 参数 | 值 | 说明 |
|---|---|---|
| `EPOCHS` | 200 | 每 50 epoch 打印一次 loss（CPU < 1 分钟跑完） |
| `LR` | 0.01 | Adam |
| `WEIGHT_DECAY` | 1e-4 | 轻微 L2，抑制过拟合 |
| `HIDDEN_DIM` | 32 | 两层 GraphSAGE 输出 embedding 维度 |
| `DROPOUT` | 0.2 | 每层后 Dropout（小图防过拟合） |
| `NEG_RATIO` | 1.0 | 负采样 1:1（52 负样本） |
| `FEAT_DIM` | 29 | 节点输入特征维度 |
| `SEED` | 20260928 | 全局随机种子（torch + numpy） |
| `TOP_K` | 20 | 输出候选边数（高置信子集 `HIGH_CONF_K=10`） |
| 损失 | BCEWithLogitsLoss | 训练用 raw dot；推断用 cosine+sigmoid |

---

## 5. 输出文件 (Deliverables)

| 文件 | 说明 |
|---|---|
| `gnn_model.py` | `GraphSAGE` / `GraphSAGELayer` / `LinkPredictor` 类 + 节点特征提取 + 边类型启发式 |
| `train.py` | 训练脚本：加载数据、正负采样、训练、画 loss 曲线、保存 checkpoint |
| `predict.py` | 推断脚本：加载 checkpoint、对全部 586 候选打分、输出 top-20 JSON |
| `checkpoint.pt` | 模型权重 + 超参 + 节点类型/索引 |
| `loss_curve.png` | 训练 loss 曲线（英文标题，避免中文乱码） |
| `phase5_gnn_edges.json` | 候选边：`id/source/target/type/kind/confidence/explicit_or_inferred/data_source/rationale` |
| `phase5B_report.md` | 产出清单 + 实跑证据 + 曲线描述 + 候选边合理性分析 |
| `README.md` | 本文件 |

---

## 6. 输出边格式 (Schema v0.1 compliant)

任务示例 JSON 中 `source` 键出现两次（节点 id 与数据出处冲突）。本实现遵循
Schema v0.1：顶层 `source`/`target` 为节点 id，数据出处放在 **`data_source`**
字段（`"Phase5.GNN"`），避免非法重复键。

```json
{
  "id": "GN:inf_edge_007",
  "source": "MX:thm:gauss_bonnet",
  "target": "MX:sym:rho_alpha",
  "type": "has_symbol",
  "kind": "llm_inferred_gnn",
  "confidence": 0.7185,
  "explicit_or_inferred": "inferred",
  "data_source": "Phase5.GNN",
  "rationale": "GraphSAGE link-prediction: cosine-score=0.9370, sigmoid confidence=0.7185 (src_type=Theorem, tgt_type=Symbol)"
}
```

候选边类型由 `infer_edge_type(src_type, tgt_type)` 启发式给出
（`has_symbol` / `derived_from` / `proves` / `dimensionally_consistent` 等），
属"最佳猜测"，仍需人工/符号校验确认。

---

## 7. 复现备注 (Reproducibility)

- 固定 `SEED=20260928`，`md5`-based 标签 hash 与进程哈希盐无关 → 跨运行可复现。
- CPU（torch 2.14.0+cpu，无 CUDA）。embedding 32 维、epoch 200 下训练约 2.7s。
- 负采样用 `np.random.default_rng` 确定性采样，训练集固定。
