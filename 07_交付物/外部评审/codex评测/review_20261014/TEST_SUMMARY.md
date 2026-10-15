# STTP 测试证据摘要

**执行日期：** 2026-10-14  
**工作目录：** `F:\codex\sttp\STTP-review`  
**候选状态：** 非干净 Git 基线（272 modified + 128 untracked）

## 环境

- OS：Windows，PowerShell
- Python：Codex bundled Python 3.12.14
- 项目声明：Python `>=3.11,<3.12`
- 测试依赖：`F:\codex\sttp\review-deps`
- Node：v24.19.0
- 浏览器：Codex in-app browser，真实 DOM/canvas/HTTP 操作
- Neo4j：本次未配置

Python 3.12 只用于代码与数据检查。安装自检中与 `requires-python` 有关的失败属于已知环境不匹配，不能代替 Python 3.11 的发布验证。

## 结果索引

| 项目 | 结果 | 证据文件 |
|---|---|---|
| Release verify | 28 PASS / 0 FAIL / 0 WARN | `release_verify.log` |
| Scientific eval | 40/40；selfcheck 40/40 + 22/22 | `eval.log` |
| Frontend selfcheck | 72 PASS / 0 FAIL / 2 SKIP | `frontend.log`, `frontend.json` |
| Subgraph selfcheck | 86 PASS / 3 FAIL / 3 SKIP | `subgraph.log`, `subgraph.json` |
| Install selfcheck | 24 PASS / 1 FAIL | `install_selfcheck.log`, `install_selfcheck.json` |
| Source registry | 15 checks passed | `source_registry.log` |
| Evidence dependencies | 1 section failed; 4 refs lack repo hash | `evidence_deps.log` |
| Dimension checks | 61 checked, 60 agree, 1 convention difference | `dimension.log` |
| Lineage selfcheck | failed; baseline dirty and untracked dependency caused TypeError | `lineage_selfcheck.log` |
| Live stats sample | 9,657 nodes / 48,712 edges | `api_stats.json` |
| Search API sample | `manifold` returned 2 hits | `api_search_manifold.json` |
| Path API sample | 3 paths, status `truncated` | `api_path_example.json` |

## 浏览器冒烟测试

| 操作 | 结果 | 观察 |
|---|---|---|
| 首次打开全图模式 | FAIL | `/api/stats` 超过 2 秒，前端中止并误判离线；刷新后因缓存预热恢复 |
| 概览 | PASS | 26 个聚合节点、62 条聚合边，canvas 正常显示 |
| 搜索 `manifold` | FAIL | 浏览器请求缺少 `q`，界面错误显示为 `no_result`；直接 API 有 2 条命中 |
| 默认筛选 `has_quantity` | FAIL | 500/2000/3s 默认预算超时 |
| 筛选 50 节点/100 边/10s | PASS | 约 63 ms 返回 50 节点/72 边；原始候选 731/855 后受预算截断 |
| 路径 UI | FAIL | 前端发送 `src/dst` 到只读取 `seeds` 的 `/api/subgraph` |
| 路径 API | PASS | `/api/path` 对示例节点返回 3 条路径，状态 `truncated` |

## 判读限制

- 完整 frozen gate 的本次尝试被人工终止，未形成结论，部分输出保存在 `frozen_gate_INCOMPLETE.log`；`release_verify --release` 内含的冻结样例子集为 78 PASS / 0 FAIL / 0 SKIP。
- 前端自检自身跳过真实浏览器渲染；本次人工浏览器检查补充了冷启动、搜索、筛选和路径证据，但还不是可自动重复的 E2E 套件。
- 科学任务集是同源规则回归集。项目已有消融结果表明删除全部图边仍可通过 37/40，不能据此证明开放问题的图推理能力。
- 当前 Git 状态会直接影响以仓库 blob 为权威字节的谱系、stale 和 A04 结论。应在干净 RC1 提交上重跑全部测试。
