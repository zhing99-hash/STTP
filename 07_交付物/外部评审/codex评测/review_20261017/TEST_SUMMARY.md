# STTP 2026-10-17 复评测试摘要

**工作目录：** `F:\codex\sttp\STTP-review`  
**HEAD：** `8a5c977f3ec3e4790e7b59599a75e2f81268b7f6`  
**Git 状态（写入本次评审产物前）：** 422 项（272 modified / 150 untracked）  
**环境：** Windows；Python 3.12.14；Node 24.19.0；Neo4j 未配置；Playwright 未安装

## 自动化结果

| 测试 | 结果 | 日志 |
|---|---|---|
| 发布样例 | 30 PASS / 0 FAIL / 0 WARN | `release_verify.log` |
| 发布前置 | dirty RC=2；regenerated RC=2 | `release_preconditions.log` |
| 前端综合（隔离提升运行） | 109 PASS / 0 FAIL / 2 SKIP | `frontend_elevated.log`, `frontend_elevated.json` |
| 前端核心 Node | 172 PASS / 0 FAIL | 终端原始输出；综合日志含摘要 |
| 子图 | 94 PASS / 3 FAIL / 3 SKIP | `subgraph.log`, `subgraph.json` |
| 冻结全量门禁 | 107 PASS / 1 FAIL / 0 SKIP | `frozen_gate.log` |
| 科学任务 | 40/40；变异 40/40；边界 22/22 | `eval.log` |
| 来源注册 | 15 项全过 | `source_registry.log` |
| 验证依赖 | 6 段通过 / 1 段失败 | `evidence_deps.log` |
| 量纲 | 60 agree / 1 convention / 0 conflict | `dimension.log` |
| 谱系 | RC=1，基线失败并抛 TypeError | `lineage.log` |
| 安装 | 24 PASS / 1 FAIL / 0 SKIP | `install.log`, `install.json` |
| 可红性 | 13 PASS / 0 FAIL / 2 SKIP | `redprobe.log`, `redprobe.json` |
| 官方任务浏览器 | 0 PASS / 0 FAIL / 6 SKIP，RC=2 | `taskflow.log`, `taskflow.json` |
| 官方断网渲染 | SKIP，RC=2 | `offline.log` |

## 子图三项失败

1. `V16c`：基线已经有大量 stale，翻转哈希后 stale 总数没有按断言方向增加。
2. `V18d`：全图 `changed=1274`，要求为 0。
3. `V18h`：断言使用包含 A04 状态的 `edge_is_stale()`，却与纯字节层 `changed + unverifiable_no_source` 比较；当前 A04 stale 非零，口径需要重审。

## 真实浏览器补充验证

- `/api/healthz` 探活与 warming 页面正常。
- `mass` 候选确认流程正常；确认后生成 1 节点/0 边，隐藏 12 条。
- 顶部 `manifold` 搜索返回 2 条。
- 两点任务卡确认 `CE:pq:gibbs_energy` 与 `PB:pq:entropy` 后，返回 5 条路径、8 节点、11 边，约 31 ms。
- 底部路径栏切换左侧 `trust=all` 后，请求仍不发送 `trust`，状态仍显示 verified；这是本次新增发现。
- 控制台只有 Cytoscape wheel sensitivity 警告，无业务异常。

## 环境判读

- 安装测试唯一失败由当前 Python 3.12.14 不满足项目 `>=3.11,<3.12` 引起；必须在 Python 3.11 复验。
- 官方浏览器脚本因 Playwright 未声明/未安装而 SKIP；本次用 Codex in-app Chromium 完成了人工真实浏览器检查，不能替代持续 E2E。
- 沙箱内并行运行前端自检时，Node 与本地端口出现环境性失败；隔离、提升运行后得到 109/0/2，作为最终前端自检结果。
- Neo4j 双后端一致性未覆盖。

