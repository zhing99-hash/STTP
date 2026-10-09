# GitHub 开源上传 — STTP 跨学科公式知识图谱

## 目标
将 01tuopu（STTP 公式图谱库）开源上传到 https://github.com/zhing99-hash/STTP.git

## 关键步骤
1. **凭据清理**：6 个 .py 脚本改为读环境变量（Aura 密码 pmpgcOdW...），报告 .md 密码改 `***REDACTED***`，`.env.example` 实例 id 改占位符。
2. **大文件排除**（.gitignore）：818MB CSV、108MB parquet、1.8GB/324MB 语料 JSON、graph_data_*.json、*_aura_delta.json、elementkg.owl、pb_chunk_*.json 等。
3. **提交**：2 个提交（开源发布 202 文件 + 清理 8 文件）→ 后因推送需要拆为 14 批 → 最终为 196 文件。
4. **推送踩坑**：
   - 403 → 细粒度 PAT 需授权 STTP 仓库 Contents: Read&Write（用户已在 GitHub 调整令牌）。
   - HTTP 408 大包超时 → 本机到 GitHub 上行质量差，大包在 GitHub 请求超时中断。
   - 解决：将历史拆为**每批 5 文件的小提交（共 31 批）**，逐个**增量推送**（每次只传小 delta 包），失败重试（3 次/15s 退避）兜底链路抖动。
   - 极小区（1 文件）探针推送成功，确认链路能传、只是大包超时。

## 结果
- 远端 `main` = `e9062a0` = 本地 HEAD，完全一致。
- 远端仅 `main` 分支；探测分支 tmp-probe/tmp2 已清理。
- 已跟踪 196 文件（195 内容 + LICENSE）。
- 明文密码扫描 0 命中。

## 备注
- 推送用一次性 `git push https://<PAT>@...` 直传，令牌未写入任何文件/git config；建议用户在 GitHub 撤销该 PAT。
- 本机无 gh/凭据助手/SSH key，HTTPS + 细粒度 PAT 为唯一可行路径。
