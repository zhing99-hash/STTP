# STTP 可复现发布包（Phase 37 · GPT6 复评 R06）

> **一句话**：本目录让**第三人从文档独立运行**，且**页面与查询属于同一数据版本**。
>
> 复评原文：*「当前下载副本仍无法复现完整图和全部验收」* —— 因为 `normalized.json`（48 MB）
> 与阶段快照被 `.gitignore` 排除。本发布包给出**自包含、可入库、可离线运行**的替代路径。

---

## 1. 快速开始（两条命令）

```bash
# ① 构建 + 校验发布包（构建离线样例 → 生成清单 → fail-closed 校验）
bash sttp.sh release

# ② 只用离线样例起可视化（页面与查询指向同一份切片）
bash sttp.sh sample            # → http://127.0.0.1:8765/
```

不需要 48 MB 的 `normalized.json`，也不需要联网。若只想复验：

```bash
python 06_PoC/release/verify_release.py --release   # 25 条断言，全过才退 0
```

---

## 2. 这个包里有什么

| 文件 | 作用 | 谁读它 |
|---|---|---|
| `sample/sample_normalized.json` | 跨域桥核心样例（**规范图**格式） | 查询后端 `/api/*`、`frozen_gate.py` |
| `sample/sample_graph.json` | 同一份切片的**前端投影**（`schema=formula-graph-view/v1`） | 页面 `/graph_data.json` |
| `sample/sample_queries.json` | 查询契约（由切片**生成**，非手写） | `verify_release.py` |
| `manifest.json` | 发布清单：**源版本 / 校验和 / 构建命令 / 依赖版本 / 预期统计 / 许可** | 人 + `verify_release.py` |
| `make_sample.py` | 样例构建器（确定性；同输入 ⇒ 同 sha256） | 构建 |
| `make_manifest.py` | 清单生成器（含仪器现算的预期统计） | 构建 |
| `verify_release.py` | 发布校验器（fail-closed） | 验收 |

样例规模（当前）：**380 节点 / 701 边**，其中**跨学科边 154 条**；
学科分布 化学 237 / 物理 80 / 数学 38 / 跨学科 25；**覆盖全部 6 个学科对**
（化学↔数学、化学↔物理、化学↔跨学科、数学↔物理、数学↔跨学科、物理↔跨学科）。

样例怎么选的：以**跨学科边**为种子 → 按 (是否 `verified_strict`, type, id) 稳定排序 →
**按学科对分层轮转**取种子（保证每个学科对都有代表）→ 按语义优先级 BFS 扩展
（跨学科边 > 计量/定义类边 > 其他，`composed_of` 最后），节点/边数封顶。
全程无随机数、无时间戳 ⇒ 逐字节可复现。

---

## 3. 「页面与查询同一数据版本」是怎么**被保证**的

这是 R06 的核心验收点，本包把它做成**机器可判的不变量**：

1. 样例构建器从**同一份切片**同时导出两种投影；
2. `viz_server.py` 支持 `GRAPH_DATA_FILE`（页面）与 `GRAPH_SOURCE_FILE`（查询）**分别显式指定**
   —— `bash sttp.sh sample` 把两者都指到本目录；
3. `verify_release.py` 断言 **两种投影的节点 id 集合与边 id 集合逐元素相等**（B1），
   并用真正的查询后端（`03_知识层/queries.py`）加载样例、核对计数（E1）与查询契约（D1）。

> 修复前：页面读 `GRAPH_DATA_FILE`，查询后端**只认** `with_inferred.json` / `normalized.json`
> —— 两者可被各自指向不同版本。这正是复评所指的缺口，现已关闭。

---

## 4. 已知反例（回归门禁）

`06_PoC/frozen_counterexamples.json` 共 **90** 条冻结用例。其中：

| 类别 | 条数 | 样例模式可跑？ | 说明 |
|---|---|---|---|
| `dim_table` / `gate_chem` / `gate_math` / `pq_quantity_gate` / `attrib_check` / `level_check` | **65** | ✅ 可跑（自包含） | 纯函数 / 自带节点与边 |
| `graph_scan` | 25 | ❌ 仅完整图 | 断言的是**全图不变量**（连通分量、分母外计数等），在样例上无意义 |

```bash
# 样例模式：自包含子集，要求 PASS 全过 / FAIL 0 / SKIP 0
python 06_PoC/frozen_gate.py \
  --graph 06_PoC/release/sample/sample_normalized.json \
  --kinds dim_table,gate_chem,gate_math,pq_quantity_gate,attrib_check,level_check --release
# → PASS 65 / FAIL 0 / SKIP 0，退码 0

# 完整图模式：全 101 条
python 06_PoC/frozen_gate.py --release
# → PASS 90 / FAIL 0 / SKIP 0
```

---

## 5. 完整快照（不入库，按清单获取）

原始大数据继续**不入库**（`.gitignore` 排除）。`manifest.json` 对完整快照登记：
路径、**sha256**、字节数、节点/边数、预期统计（北极星 / T9 / T9-i / 门禁 / 连通分量）。

获取方式（三选一）：

```bash
bash sttp.sh export                              # 从 Aura/本地重建同名快照
python 06_PoC/release/make_manifest.py --pack    # 打包到 dist/ 并写 .sha256（供 Release 资产上传）
# 或从 Release 资产直接下载同名快照
```

拿到快照后复验：

```bash
bash sttp.sh release-full    # manifest 现算比对（H1/H2）+ 主图 fail-closed 门禁
```

---

## 6. 依赖

- `requirements.lock`（仓库根）—— **精确锁定**；`requirements.txt` —— 松约束。
- `verify_release.py` 会**报告版本漂移**（不阻塞），但**缺件会非零退出**（R07）。
- ★ 口径披露：锁记录的是本项目运行环境版本；外部复评环境为
  `rdkit 2026.3.6 / pint 0.26.1 / networkx 3.7 / numpy 2.5.3`，与本锁**不一致** ——
  化学与量纲结果建议在锁定版本上复核后再引用。

---

## 7. 本包**不**主张什么（边界）

- 样例是**跨域桥核心切片**，不是完整图；它不是「全量复现」，而是「可独立运行的验收入口」。
- 北极星 99.6% 是**规则门槛达标率**（分母 T1–T8），**不是**跨域任务正确率；
  报它必须同时披露分母构造、分母外占比与反向对照（见根 README 与 Phase 35/36 报告）。
- 完整快照需另行获取；未获取时 `verify_release.py --full` 的 H1 记为 **WARN**（不算通过）。
- 依赖漂移不影响运行，但可能影响化学/量纲类结论的数值细节。

---

## 8. 许可

代码 **Apache-2.0**（根 `LICENSE` + `NOTICE` + 第一方源码 SPDX 头）。
第三方前端依赖在 `06_PoC/vendor/licenses/`（cytoscape 系 = MIT，MathJax = Apache-2.0）。
数据许可逐源登记见根 `DATA_LICENSES.md`；原始数据集不随本包分发。
