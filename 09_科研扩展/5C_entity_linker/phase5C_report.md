# Phase 5.C 实体对齐 · 产出报告

- **阶段**：Phase 5.C 跨源实体对齐（Entity Linker）
- **目录**：`01tuopu/09_科研扩展/5C_entity_linker/`
- **日期**：2026-09-29
- **目标**：实现科研级跨源实体链接器，给定源节点找出其它源中的同一实体候选（rank + 置信度），产出 `same_as` 边候选。

---

## 1. 产出清单

| 文件 | 说明 |
|---|---|
| `entity_linker.py` | `EntityLinker` 类 + 三个独立相似度函数（`label_similarity` / `symbol_match` / `definition_overlap_pair`）+ 可配置权重 `DEFAULT_WEIGHTS` + 独立单测 |
| `linker_demo.py` | 构建模拟多源节点池（MX+MG+PB+EK+WD）、选 6 个 MX 节点跑链接、落盘结果 |
| `data/nodes_pool.json` | 模拟多源节点池（68 个：MX 36 / MG 4 / PB 3 / EK 7 / WD 18） |
| `data/sample_nodes.json` | 小样本（20 个，3 个源 MX / MG / PB），从 samples 提取 |
| `aligned_candidates.json` | 对齐候选输出：`source_node / target_source / target_id / score / matched_by` + 分量 |
| `README.md` | 算法说明、权重、运行方式、如何接 Phase 5.A Wikidata |
| `phase5C_report.md` | 本报告 |

> 约束遵守：未修改 `01tuopu` 根目录下 `00`–`08` 任何文件；纯本地数据 + 算法，未发起任何网络请求。

---

## 2. 实跑证据

### 2.1 `python entity_linker.py` — 三个相似度函数独立单测

```
[1] label_similarity (rapidfuzz.token_sort_ratio)
  OK  'manifold' <> 'manifold' = 1.000
  OK  'Manifold' <> 'smooth manifold' = 0.696
  OK  'gauss_bonnet' <> 'gauss bonnet theorem' = 0.750
  OK  'hydrogen' <> 'oxygen' = 0.571
  OK  '' <> 'anything' = 0.000
[2] symbol_match (精确相等)
  OK  'pi' == 'pi' -> 1.0
  OK  '\\pi' == 'pi' -> 1.0   (归一化后一致)
  OK  'R' == 'r' -> 1.0       (大小写归一)
  OK  'M' == 'manifold' -> 0.0
  OK  None == 'x' -> 0.0
[3] definition_overlap (TF-IDF cosine)
  similar texts cosine = 0.580  (应较高, > 0.5)
  unrelated texts cosine = 0.000  (应较低, < 0.5)
  OK
RESULT: ALL PASS
```

### 2.2 `python linker_demo.py` — 实际产生对齐候选

```
pool size              : 68
queries               : 6
aligned candidates    : 60  (要求 >= 10)   ✅
high-confidence(>=0.7): 7  (要求 >= 3)     ✅
VERIFY: PASS
```

top-10 候选中每条都带有 `matched_by`（命中了哪些特征），例如：

```
QUERY  MX:sym:pi  [MX]  label='pi'  domain=math
1    wd:Q_pi       WD  0.9661  ★ label,symbol,definition,domain

QUERY  MX:def:manifold  [MX]  label='manifold'  domain=math
1    wd:Q_manifold  WD  0.8745  ★ label,definition,domain

QUERY  MX:chem:co2  [MX]  label='carbon dioxide'  domain=chemistry
1    ek:molecule:CO2      EK  0.8011  ★ label,definition,domain
2    wd:Q_carbon_dioxide  WD  0.7805  ★ label,definition,domain
```

---

## 3. 真实匹配样例（≥3 条高置信）

| 源节点 (MX) | 命中目标 | 源 | 分数 | 命中特征 |
|---|---|---|---|---|
| `MX:sym:pi` | `wd:Q_pi` | WD | **0.966** | label, symbol, definition, domain |
| `MX:def:manifold` | `wd:Q_manifold` | WD | **0.875** | label, definition, domain |
| `MX:phy:newton2` | `wd:Q_newton_second` | WD | **0.852** | label, definition, domain |
| `MX:def:tangent_space` | `wd:Q_tangent_space` | WD | **0.808** | label, definition, domain |
| `MX:chem:co2` | `ek:molecule:CO2` | EK | **0.801** | label, definition, domain |
| `MX:phy:newton2` | `pb:newton_second` | PB | **0.783** | label, definition, domain |
| `MX:chem:co2` | `wd:Q_carbon_dioxide` | WD | **0.781** | label, definition, domain |

> 这正是任务要求的「真实匹配」示例：`MX:def:manifold` ↔ Wikidata 风格 `wd:Q_manifold` 高分命中；
> 以及跨域对齐 `MX:chem:co2` 同时命中 EK 与 WD 的二氧化碳实体（多源收敛）。

### 3.1 阈值如何工作（边界样例）

`MX:thm:gauss_bonnet` 的 top 候选 `wd:Q_gauss_bonnet` 得分 **0.624**（rank #1，实体正确），
但因 label 为 `gauss bonnet` ↔ `gauss bonnet theorem`（非完全相等）且定义重叠未达最高，
落在 0.7 阈值之下 → 不标 `high_confidence`。这恰好演示了阈值对「接近但不确定」的配对起过滤作用。

---

## 4. 算法要点回顾

- 加权：`score = 0.4*label + 0.1*symbol + 0.3*definition + 0.2*domain`（权重集中可配置）。
- 标签：`rapidfuzz.token_sort_ratio`，先归一化（小写+去标点+压缩空白）以兼容中英文混合。
- 符号：精确相等（归一化），单字母符号权重低（0.1）以抑制歧义误命中。
- 定义：`TfidfVectorizer(ngram_range=(1,2))` 余弦，全池一次性 fit，候选直接 transform，避免下标错配。
- 域：粗域（math/physics/chemistry/cross）一致 +1 特征，权重 0.2。
- 阈值 `0.7` → `high_confidence` 候选，对应图 `same_as` 边的 `confidence`（建议 `explicit_or_inferred='inferred'`）。

---

## 5. 接入 Phase 5.A（Wikidata）路线（详见 README §5）

本阶段 WD 节点为本地占位。真实接入时，Phase 5.A 按 `label/aliases` 在线检索 Wikidata，
取回 `id/label/description` 映射为实体 dict（`source='WD'`）注入链接器即可；
本模块接口 `fit(pool)` → `link(query, candidates=wd_nodes)` 与输出格式保持不变，天然离线友好。

---

## 6. 踩坑记录

1. **rapidfuzz 新 API**：`from rapidfuzz import fuzz; fuzz.token_sort_ratio(a,b)`（非 `process` 模块）。
2. **TF-IDF 需 ≥2 文档**：全池 68 个定义一次性 fit，候选直接 transform，无 per-pair fit 性能问题。
3. **重复 id 导致定义错配**：早期 WD 占位 id 用 `wd:Q?` 重复，使 `_score_one` 按 id 找回错误定义，
   gauss_bonnet 误比 levi_civita 定义（分数异常 0.50）。已改为按实体对象本身 transform 修复，并给 WD 节点唯一 id。
4. **中英文混合**：统一小写 + 去标点（`re.UNICODE` 保留 CJK）后再比。
5. **环境**：Python 3.11.10 须用全路径 `C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe`（PATH 不可靠）。
