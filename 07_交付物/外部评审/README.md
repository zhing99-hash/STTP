# 外部评审归档索引

> 本目录存放**外部评审原件**（不修改、不删减），供后续复评对照与溯源。
> 本项目的**响应/裁决/规划**另见 `00_项目管理/`。
> ★ **外部评审者请先读**：`PROJECT_DEVELOPMENT_GUIDE.md` **§0 项目定位与外部评审须知**。

---

## 1. `STTP_复评与Apache2.0许可意见_20261011.md`

| 项 | 值 |
|---|---|
| 评审方 | GPT6（外部） |
| 日期 | 2026-10-11 |
| 主题 | 复评 + Apache-2.0 许可意见 |
| 本项目响应 | R01–R08 已全修（Phase 36–37）；R09 → Phase 40；R10 → Phase 41；R11 → Phase 38–39 |

## 2. `codex评测/`（codex GPT6 两次评测原件）

### 2.1 `codex评测/STTP_全面评估与研发规划_20261009.md`

| 项 | 值 |
|---|---|
| 评审方 | codex GPT6（外部） |
| 日期 | 2026-10-09 |
| 主题 | 全面评估与研发规划（首评） |
| 本项目响应 | 5 条 P0 全部坐实并已修复（Phase 26–31 一线） |

### 2.2 `codex评测/review_20261012/`（复评，**当前响应对象**）

| 项 | 值 |
|---|---|
| 评审方 | codex GPT6（外部） |
| 评估日 | 2026-10-12 |
| 被评副本 | `F:\codex\sttp\STTP-review`（`.git` 停在 `8a5c977`，manifest `git_head_at_build=1e07ad2`） |
| 评审环境 | Python **3.12.14**；SymPy 1.14.0 / RDKit 2026.03.6 / pint 0.26.1 / NetworkX 3.7 / NumPy 2.5.3 |
| 本项目锁定环境 | Python **3.11.9**（`requirements.lock`）⇒ **其 PASS 不代表本项目干净安装已验证** |

**原件清单**

| 文件 | 性质 |
|---|---|
| `STTP_完整副本复评与八周研发路线_20261012.md` | 复评正文（10 项任务 A01–A06 / B01–B03 / C01 + 八周路线） |
| `下一轮研发任务单_20261012.md` | 逐项工单（依赖 + 完成定义） |
| `check_results.json` | 检查执行记录（机器可读） |
| `snapshot_inventory.json` | 数据与源码指纹（机器可读） |
| `targeted_evidence.json` | 针对性实验证据（机器可读） |
| `targeted_probes.py` / `run_checks.py` / `inspect_snapshot.py` | 复现脚本 |
| `connectivity.log` / `eval.log` / `frozen_gate.log` / `inventory.log` / `lineage.log` / `release_full.log` / `source_registry.log` / `task_trust.log` | 各步原始日志 |
| `connectivity_rerun.json` / `eval_rerun.json` / `lineage_rerun.json` / `trust_rerun.json` | 重跑结果 |

**本项目响应**：`00_项目管理/codex复评响应与下一阶段规划_20261013.md`
（10 项复核表 + 三项裁决结论 + Phase 42–45 映射 + 拟增铁律 #62–#68 + §6.2 学科审题替代方案）。

---

## 3. 归档规则

1. **原件只读**：本目录文件**不修改、不删减**；若有更正意见，写入 `00_项目管理/` 的响应文档，不动原件。
2. **可溯源**：响应文档引用本目录原件时给**相对路径**；若原件来自他机副本，须注明副本路径与其 `.git` HEAD。
3. **环境分离声明**：评审方的语言/依赖版本若与本项目锁定环境（Python 3.11.9）不同，**须在响应文档中显式声明**。
4. **判据非真空**：若评审结论涉及「某机制有效/无效」，响应须给出**注入反例**或**消融对照**（本项目铁律 #14/#43/#65）。
