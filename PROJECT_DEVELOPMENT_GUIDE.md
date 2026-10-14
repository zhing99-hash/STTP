# STTP · 跨学科公式知识图谱
## 项目开发指南（WorkBuddy 接手手册）
> 适用对象：接手本项目继续开发的 Agent 或工程师
> 生成时间：2026-10-08
> 项目根目录：`C:\Users\Administrator\WorkBuddy\2026-10-08-10-51-20\STTP`

---

## 〇、项目定位与外部评审须知（2026-10-13 裁决 · 外部评审请先读本节）

### 0.1 定位：**A 研究原型（收敛版）—— 不做产品化**

**本项目的主目标 = 研究原型**（跨学科公式知识图谱的构建方法论 + 可审计验证纪律）。
**已裁决（2026-10-13）**：主目标**维持不变**，按既有路线图推进；**不转向**「有限专题任务助手 / 可交付产品」。
据此，以下事项**明确不做**（除非未来另行裁决）：

- ❌ 产品化 / 用户试用驱动的功能收敛（C01）
- ❌ 为提升连通率而补无证据的边（真孤岛 29 节点**不补边**）
- ❌ 新学科大规模摄入 · GNN 大规模调参 · 3D 全图重写
- ❌ 把「演示/交付」当作评价标准

**唯一例外**：与定位**无关**的工程纪律类缺陷（发布链、判分器、许可、可安装性、查询有限性等）
一律**照修** —— 它们是把**已有纪律补齐**，不改变研究原型定位。

### 0.2 给外部评审者（GPT6 / codex / 人类专家）的约定

> 目的：让复评**可直接比较、可累积**，避免把「方向分歧」误报为「缺陷」。

1. **请先读 §0.1 定位**。建议若**与定位冲突**（例如「应转为产品/专题助手」「应做用户试用」），
   请标注为**选项（Option）而非缺陷（Defect）**，并单列一节「方向建议」。**不要**把它计入缺陷计数。
2. **请按三档归类**：`P0 工程缺陷`（可注入反例、与定位无关、必须修）／
   `P1 工程缺口`（补齐纪律，与定位无关）／`P2 方向建议`（需裁决）。
   **只有 P0/P1 才计入「本轮指摘数」**。
3. **请声明评估环境**：Python 版本、关键依赖版本（SymPy / RDKit / pint / NetworkX / NumPy）、
   被评副本的 `.git` HEAD 与 `manifest.json` 的 `git_head_at_build`。
   ★ 复评环境与本项目锁定环境（Python **3.11.9**，见 `requirements.lock`）**不同**时，
   其 PASS **不代表**本项目干净安装已验证 —— 请显式说明。
4. **请区分「未复核」与「不成立」**：未重跑的数字（如本项目存档的 GNN/MRR 结果）应标注为
   「引用项目已有结果，未独立重跑」，而非直接采纳或直接否定。
5. **判据必须非真空**：如需断言「某机制有效」，请给出**注入反例**或**消融对照**（本项目铁律 #14/#43/#65）。
   例：本项目已自查出「**删除全图所有边后，40 题仍 37/40 通过**」——该题集因此只能算回归集（见 §0.3）。
6. **归档约定**：复评正文与证据请归档到 `07_交付物/外部评审/`；本项目会保存原件并保留引用路径。

### 0.3 已知的、已被承认的边界（请勿重复报告为「新发现」）

- **40 题评测集是「规则回归集」，不是端到端能力证据**：删掉全图所有边仍 37/40、题面文本换成无意义句仍 40/40
  ⇒ 现有题集对**关系结构不敏感**（该结论已由 codex 复评 2026-10-12 首先给出，本项目**接受**）。
- **无独立学科审题**：本项目**无学科专家资源**，审题独立性等级为 **L1**（自出题 + 预注册冻结 + 外部权威锚点 + AI 红队），
  详见 `00_项目管理/codex复评响应与下一阶段规划_20261013.md` §6.2。任何跨域能力结论须带此限定。
- **证据血缘缺口 116 条**（6 个模型来源训练快照版本未记录）：**如实计数、不伪造历史**。
- **5 个真孤岛（29 节点）**：回退假边所致，是「恢复真相」而非退化，**不补边**。

#### 0.3.1 Phase 43（局部图谱查询**与前端**）已承认的边界 —— 更新于 2026-10-14

- 后端（步骤 A）详见 `07_交付物/Phase43_局部图谱查询与后端_20261013.md` §6（**D1–D13**）
- 前端（步骤 B）详见 `07_交付物/Phase43_局部图谱查询与前端_20261014.md` §5（**D14–D25**）
- **Phase 44（D1 根因修复）详见 `07_交付物/Phase44_D1证据字节忠实度根因修复_20261014.md`（**D1 已修 + 新增 E1–E7**）

- ✅ **【D1 根因已修 · 2026-10-14】** 证据哈希依赖的字节在干净检出下不可复现 → **权威字节源改为仓库 blob**。
  - 原状：`stale` 判据依赖「worktree 字节 == 仓库 blob」，实测 **7/21** 个已入库 `locator` 不成立（CRLF vs LF），
    另有 **13/34** 个 `locator` **未入库**（`.gitignore`）⇒ 干净检出下 `stale` **假阳**（实测牵连 4 个来源）。
  - **修法**：`stale` 的当前哈希一律取 `git cat-file blob HEAD:<rel>`（与检出/行尾**无关**）；`git` 不可用时
    **返回 `None`（fail-closed）而不是空字节**；缓存键从「`mtime/size`」改为「`(HEAD sha, 每源 lineage_recorded)`」。
  - **实测**：`changed 760 → 0`；干净 clone 与主工作区**逐值相同**（四分类 {verified 1536, changed 0,
    unverifiable_no_source 121, unverifiable_not_in_repo 47055}，Σ = 48 712 = 边总数）。
  - **★ 四分类（`FIDELITY_CLASSES`）取代原来的二态**：`verified / changed / unverifiable_no_source /
    unverifiable_not_in_repo`，**可直接加总回边总数**；`stale` = `changed + unverifiable_no_source`（**口径与 Phase 43 完全一致**，
    仍为 121 条）。`unverifiable_not_in_repo` **不计入 `stale`** —— 那 12 个来源**并未变更**，是「大体积派生对象不入库」的
    **已知边界**；若并入会把 47 055 条正常边淹没掉真信号，使 `exclude_stale` 退化为「什么都别显示」。
  - `exclude_stale` **默认仍为 `false`**（保持 Phase 43 对外行为），但四分类**始终**随 `/api/stats`、`/api/subgraph`
    与边属性 `fidelity`/`fidelity_reasons` 暴露，前端状态行与证据卡**强制显示**（自检 V18 / X8 / X9 / F28 / F29 断死）。
  - **残余（如实登记 · E1–E5，详见 Phase 44 报告 §6）**：`unverifiable_not_in_repo` 47 055 条 / 96.6% 的边**仍不可从仓库复算**
    （须上游原始数据）；`no_locator` 的 9 个模型/规则来源其 `src_hash` 为 `null`（**契约已放宽为 `["string","null"]`**）。
- **默认 `trust=verified` 隐藏 28.6% 的边**（13 932 / 48 712，V14 实测；两档差 11 = `by_construction`）：
  `has_symbol` 这类未定级边被整体排除（例：`MX:def:manifold hops=1` 由 9 条边降到 3 条）。**已回显 `hidden_by_trust` 且可加总回全量**；
  ✅ **前端已落地**：可信范围两档单选 + 状态行「已隐藏 N 条」提示（`F27` 断言）。
- **`stats.pending_frontier` 只是下界**（中断时 BFS 队列长度），**不得**渲染成"还有 N 个"；字段旁已附 `pending_frontier_note`。
  ✅ 前端已由 `F26b` 断死（横幅必须标"下界"，且不得出现「还有 N 个」）。
- **`mode=filter&trust=all`（无其他条件）⇒ HTTP 400** 是**刻意**设计（防用 filter 倒出全图，全图请走 `/api/overview`）。
  ✅ 前端 `validateParams` **先行拦截**并给可读文案（「请至少选一个筛选项…全貌请看「概览」」）——不让用户看到"系统故障"。
- **响应体积**：默认预算下最坏 **2.4 MB**（`filter` 500/2000）；硬上限下 **10.3 MB**（5000 节点 / 30 s）。默认入口不得请求硬上限。
- **`path` 模式在 hub 端点必超时**（`PQ:mass → MX:def:manifold`，hops≥3 ⇒ 3.1–3.3 s `timeout`）：
  单向无启发 BFS 前沿爆炸。返回 `timeout`（非空、非失败）是**正确行为**；双向 BFS/剪枝为**待办**。
- **`/api/path` 保留 legacy 扁平信封**（旧页面兼容），**不合统一信封** ⇒ 新代码一律用 `/api/subgraph?mode=path`。
  ✅ 前端已如此（`main.js` 的路径入口走统一信封）。
- **Neo4j 双后端契约一致**：本环境不可达 ⇒ 自检 N1 记 **SKIP（不计通过）**。语义一致由「NX/Neo4j 共用 `Backend` 基类全部查询逻辑、
  子类只给 3 个原语」**架构保证**，属论证**非实测**。
- **`error_code` 中 `ENTITY_AMBIGUOUS` / `MISSING_CONDITION` 是接口预留**，本轮**无产生路径**（属科学任务层，未接入查询层）。
- **契约校验器是 JSON-Schema 子集**（12 个关键字，见 `contracts/README.md`）；自检 **C5b** 断言 schema 不得使用未实现关键字
  ⇒ 不存在"写了但不检查"的静默失效。校验器自身可红性由 C5 注入反例证明。

**—— 以下为步骤 B（前端）新增的已知边界 ——**

- ★★ **真实浏览器渲染未实测（D14，前端报告 §5）**：本项目**零第三方依赖**（§0.1 拒构建链）⇒ 未装 Playwright / jsdom，
  因此**布局、像素、真实交互链路、浏览器计算样式**均**未实测**，`bash sttp.sh frontend` 内记 **SKIP 且不计通过**。
  替代证据链 = **纯逻辑 `node` 断言 66 条**（含五态三重编码的**运行时渲染**与请求序列） + 静态结构 + **跨层一致性** + HTTP MIME
  + **真实响应字段对照**。★ 要补强须引入**测试用**无头浏览器（属**新依赖**，须另起裁决）。
- **V8 的口径**：五态 = **颜色 + 图标 + 文字**三重编码（颜色只是三通道之一），五态色对深色底实测 **≥4.5:1**（5.52–7.33）。
  ★ `timeout` 用**橙红**、`error` 用真红，使二者在色觉上也可分。
- **旧前端配色表已漂移，本轮校正（D15）**：旧 `EDGE_TYPE_COLOR` 含已不存在的 `depends_on` / `same_latex_normalized`、
  缺 `uses_operator`。现以**规范图实测 24 种 `type`** 为准，并由自检 **X1** 断死（前端配色键集合 == 实测集合）。
- **前端验证档 `rank` 曾写错，被跨层断言抓出（D16）**：现已改为 `queries._RANK` **7 档、逐值相等**（自检 **X7**）。
- **`/api/overview` 的 `nodes[]` 没有 `label`（D20）**：前端自行补齐 `学科::类型`；若将来要统一投影口径需改后端。
- **`bundle` 模式探测用 `HEAD`，但服务端仍会构建整个 JSON（D19）**：46 MB 图下探测仍有一次内存/时间开销（未优化）。
- **前端会话计数是提示性的（D21）**：权威在服务端（`stats.session`）；`filter`/`path` 被前端视为"新视图"重置本地计数，
  二者**可能短暂不一致**，状态行一律显示服务端回传值。
- **`bash sttp.sh frontend` 与 `X1` 依赖不入库的规范图（D23）**：干净检出下 `X1` **必然 SKIP**（同 D13a 口径）；
  其余断言只依赖已入库的前端文件与 Python 源码 ⇒ 在干净检出下仍可运行。
- **前端静态资源的行尾可复现性（D24，已修 + 已实证）**：本机 `core.autocrlf=true` 且前端原**无** `.gitattributes` 规则 ⇒
  `git checkout`（含 clone）会把 `06_PoC/js/*.js` 与 `graph_view.html` 的 LF 改写成 CRLF（实测 `git checkout -- 06_PoC/js`
  写入 **2446 处 CRLF**）⇒「本机验过的字节 ≠ 检出字节」。**已加** `06_PoC/js/*.js` / `graph_view.html` 的 `text eol=lf`。
  ★ **两件事分开陈述**：① **自检本身对行尾鲁棒**——把 9 个 js 全转 CRLF 后 `frontend` 仍 **PASS / FAIL 0**、`node` 仍全过（实测）；
  ② 加锁是**字节可复现**口径的统一，**不是**修一个会导致自检失败的行尾缺陷。
- ✅ **【D26 行尾策略覆盖不全 · 2026-10-14 已修】**（原为「逐路径白名单」的必然漏网）
  白名单只锁了 `*.json`/`11_真实数据/*.py`/`06_PoC/js/*.js`/`graph_view.html` ⇒ 干净 clone 里**其余文本被写成 CRLF**，
  而仓库 blob 是 LF ⇒ `sha256(clone) ≠ sha256(blob)`（铁律 #61 在这些文件上不成立）。实测漏网 **17 个文件**
  （`.gitignore`、`LICENSE`、`NOTICE`、`env.ps1`、`*.env.example`×2、`*.cjs`、`06_PoC/vendor/*.js`×4、`*.bak`，以及
  `03_知识层/queries.py`、`06_PoC/viz_server.py`、`06_PoC/tests/phase43_*.py`×2、`sttp.sh`）。
  **已修**：置顶兜底 `* text=auto eol=lf`，例外（`*.json -text`、`*.lock -text`、`07_交付物/外部评审/** -text`）按
  「后匹配者胜」继续覆盖。**只读预演**（445 个入库文件）：
  **仅 `.gitattributes` 自身变化，其余 444 个零改写**。本机工作区 83 个遗留 CRLF 文件已按 HEAD blob 归一化 ⇒
  **实测 445/445 入库文件「工作区字节 == 仓库 blob」**（终测 HEAD `f173eae` 复测 **446/446**，多出的 1 个是本轮新增的报告自身），
  且干净 clone 与本机**逐字节一致**。
  ★★ **教训（勿重犯）**：`git add --renormalize .` 在本仓库**不安全** —— 实测它改写了 **51 个 `-text` 的 JSON blob 与工作区文件**
  （`-text` 并未阻止 renormalize）。已全部按 HEAD 逐字节还原（51/51 校验一致）。核对一律用**只读** `hash-object` 预演，
  **不要**用 `--renormalize`。
  ★★ **两个命令回答两个不同问题，且不能同时用**：
  - 「**工作区原始字节** == 仓库 blob?」 ⇒ `git hash-object --no-filters <f>` vs `git rev-parse HEAD:<f>`。**D1/D26 要的是这个。**
  - 「**`git add` 会存成什么** == 仓库 blob?」 ⇒ `git hash-object --path=<f> <f>`（过滤后）。
  - ★ **`--path` 与 `--no-filters` 互斥**：同时传会直接 `error: Can't use --path with --no-filters` 且 **stdout 为空**
    ⇒ 脚本若不查退出码就会拿到**空哈希**，把**全部文件**判成「不一致」（本轮实测：446/446 全红 → 修正后 446/446 全绿）。
    这是铁律 #38「PASS ≠ 无红」的**镜像**：**全红也可能是测量器坏了**，先自证测量器能给出「一个已知一致的样本」。
  - ★★ **改写入库文件必须显式锁行尾（本轮实测踩到）**：在本机（Windows + `core.autocrlf=true`）用
    **Python 文本模式**写文件（`open(p,"w",encoding="utf-8")`）会把 `\n` 翻译成 `\r\n` ⇒ 工作区 CRLF ≠ blob LF，
    **正是 D26 的病**（实测一次改写把 460 处变 CRLF）。处方：`open(p,"w",encoding="utf-8",newline="")`
    或**二进制写**（`open(p,"wb")`）；`Edit` 工具与 `cat >>` heredoc 不会引入。**提交前跑字节对账**（见上一条命令）。
  **残余**：`*.json`/`*.lock` 仍 `-text`（26 个 JSON 的 blob 本身含 CRLF，按「产物逐字节冻结」有意不改写）；归档目录内 12 个文件同此。
- ✅ **【E9 临时目录静默泄漏 · 2026-10-14 已修】**：本机临时目录里实测残留 **91 个**
  `sttp_*` 目录（`lineage_sc1_*` 34 ／ `manifest_*` 33 ／ `verify_*` 24）。**根因**：Windows 上
  **git 的 loose object 是只读文件** ⇒ `shutil.rmtree(p, ignore_errors=True)` 抛
  `PermissionError [WinError 5]` 并被**静默吞掉**（实测样本 42/42 文件全只读）；
  另有两处（`make_manifest.py` / `verify_release.py`）**根本没有清理代码**。
  **已修**：`source_registry.rmtree_force()` —— 先清只读位再删、**删不掉就抛**（fail-closed，铁律 #62），
  接入 5 处 + 两处补 `try/finally`；新增自检 **⑮（构造性）**：造「只读文件目录」⇒
  **朴素删法必须失败**（缺陷坐实）且**力删必须成功**（POSIX 下该分支如实记 SKIP，不假装通过）。
  实测：`source_registry --check` **15/15**；连跑 6 个套件后临时目录**新增 0 个**。
- ★ **9 个命令在裸 clone 里抛裸 traceback（D25，已修）**：`stats`/`gatecheck`/`t7`/`scopeaudit`/`eval`/`gnneval`/`dimaudit`/`lineage`/`noise`
  的断言以**不入库**的权威输入为前提却无前置守卫 ⇒ 裸 clone 里 **RC=1 + 裸 `FileNotFoundError`**，易被误读成「代码坏了」，
  且与已做对的 `subgraph`/`make_sample`（明确原因 + RC=2）**口径不一致**。**已修**：`sttp.sh` 新增
  `require_authority <命令> <路径…>`（9 处挂载；明确原因 + 恢复方式 + 替代验证 + **RC=2**）；防回退断言 **G1–G4**（含可红性自证）。
  ★ 这条是「干净检出实测」本身才暴露出来的 —— 铁律 #53 的价值实证。
- **未覆盖且不计通过**：**真实浏览器渲染（D14）**、**V8c 浏览器计算样式**、**Neo4j（N1）**、
  **`pip install .` 全链路与装后入口端到端（E25）**；
  A04 验证依赖绑定 + `valid/stale/needs_review/rejected`
  生命周期**已于 2026-10-14 完成**（见 §0.3.2 的 E10–E22：记录级绑定、`code_import` 过近似、`needs_review` 不可重验消除、
  **A04 `stale` 通道无自然正例**等边界）；
  **A05 第三人可安装入口已于 2026-10-14 完成**（见 §0.3.3 的 **E23–E28**）。
- ★ **干净检出（clone）的能力与限制**（2026-10-13 首测；**2026-10-14 步骤 B 复测**，见
  `07_交付物/Phase43_局部图谱查询与前端_20261014.md` **§7** 的完整矩阵）：
  ① `contracts/*.json`、两份 `Phase43_*selfcheck.json` 留痕、`06_PoC/release/manifest.json`
  以及（2026-10-14 起）**`06_PoC/js/*.js` ×9 与 `graph_view.html`** 均**逐字节一致**；发布样例三产物 sha256 与 manifest 一致；
  ② **`python 06_PoC/release/verify_release.py --release`（步骤 A 的"路径 A"）⇒ 28/0/0 RC=0**
  （`--full` ⇒ 31 条 FAIL 0 / **WARN 3**）；**`make_sample.py`（"路径 B"）缺权威图 ⇒ RC=2**（刻意）。
  ★ **不要把 ② 读成"整条 `sttp.sh release` 可在裸 clone 验证"** —— 实测 `bash sttp.sh release` = **RC=2**
  （第 1 步 `make_sample` 即需不入库的权威图），`release-full` = **RC=1**（fail-closed FAIL 3）。裸 clone 里要跑发布链，
  **只能直接调 `verify_release.py`**。
  ③ ★ **`bash sttp.sh subgraph` 在裸 clone 不能运行**（`06_PoC/etl/normalized.json` 不入库）——
  已改为**明确原因 + RC=2**（修复前是 `KeyError` 崩溃）；**契约目前只能在下游产物层被 clone 验证**，
  若要"契约也可从 clone 验证"需入库小基线图 + 样例口径断言子集（**未做，有意取舍**）。
  ④ **`.gitattributes` 现为「兜底 + 例外」**（2026-10-14 起）：置顶 `* text=auto eol=lf`，例外为
  `*.json -text`、`*.lock -text`、`07_交付物/外部评审/** -text`（归档原件逐字节冻结）。
  ⇒ **实测干净 clone 与本机工作区 445/445 个入库文件逐字节一致**（见 D26）；**终测 HEAD `f173eae` 复测 446/446**。
  残余：26 个 `-text` 的 JSON 其 blob 本身含 CRLF（**有意**不改写，稳定且可复现）；归档目录内 12 个文件同此。
  ★★ **不要**用 `git add --renormalize` 核对（会改写 `-text` blob，实测 51 个）；改用只读的
  `git hash-object --no-filters <f>` 预演（★ 它**不能**与 `--path` 同时传 —— 见上文 D26 的两条命令之别）。
  ⑤ **`env.sh` 依赖不入库的 `.env`** ⇒ 裸 clone 里 `$STTP_PYTHON` 为空，请**直接指定解释器**。
  ⑥ **裸 clone 命令可用性矩阵（2026-10-14 复测）**：✅ 可运行 = `check` / `frontend`（**PASS 56 / FAIL 0 / SKIP 4**，
  `X1`·`H6` 降级 SKIP 而**不假通过**）/ `verify` / `verify_release.py --release`（**28/0/0 RC=0**）；
  ⛔ **结构性不可用但给明确原因 + RC=2** = `subgraph` / `release` / `stats` / `gatecheck` / `t7` / `scopeaudit` /
  `eval` / `gnneval` / `dimaudit` / `lineage` / `noise`（后 9 个为 **D25** 修复项，原为 RC=1 裸 traceback）；
  ⛔ `release-full` = RC=1（fail-closed FAIL 3，带逐条 ❌ 原因）。
  ★ **本轮修正的指引错误**：`bash sttp.sh release` 会先**重建**样例、需权威图 ⇒ 裸 clone 下**必定失败**，
  **不能**当作「干净检出可用的下游校验路径」；正确命令是 `python 06_PoC/release/verify_release.py --release`。
  该指引已同步修正在 `phase43_subgraph_selfcheck.py` 的「三种可用做法」第 ③ 条。

#### 0.3.2 Phase 45（A04 验证依赖绑定）已承认的边界 —— 更新于 2026-10-14

- 全文见 `07_交付物/Phase45_A04验证依赖绑定_20261014.md`（**E10–E22** + §9 未覆盖项）。
- ★ **两层判据，不可互相替代**（这是本轮最重要的口径）：
  - `edge.fidelity`（Phase 44）= **边级字节忠实度**：来源那一个字节文件变了吗；
  - `edge.evidence_status`（Phase 45）= **证据级验证依赖状态**（`valid/stale/needs_review/rejected` | `null`）：
    它依赖的**参考表 / 验证器 / 依赖版本**变了吗、命题是否被否决、哈希来路是否明。
  - 二者**并列披露**；`exclude_stale` 的**唯一**判据是 `queries.edge_is_stale()`（两层并集）。
    **无 A04 记录时完全回落到字节层** ⇒ A04 未落图的图（发布样例 / 干净 clone 中期图）行为与 Phase 44 **逐位一致**。
- **落盘实测分列（边级）**：`valid 1038 ｜ stale 0 ｜ needs_review 34145 ｜ rejected 0 ｜ no_record 13529`（Σ = 48712）；
  四分类不变 `{verified 1536, changed 0, unverifiable_no_source 121, unverifiable_not_in_repo 47055}`。
  `no_record 13529` 是**信息性**的（这些边的证据本就不依赖参考表/验证器，原因码 `no_reference_declared`），
  **不是缺陷**，但**单列**（缺记录 ≠ 已验证）。
- ★★ **A04 的 `stale` 通道目前没有真实数据触发的正例**（最诚实的薄弱点）：仓库内**无**已登记来源与记录哈希不符
  （D1 修复的直接结果）⇒ `stale = 0`。其正确性由**构造性断言**（把字节层 `verified` 的边标成 A04 `stale` ⇒ 立即被排除，V19h/V19i）
  与**受控小图重验流程**（自检 ⑧a–⑧e）保证。**这是"该图上无可触发样本"，不是"已验证"**。
- **记录级依赖绑定（`record_level`）尚未实现（E13）**：`input_refs[].binding` 契约已预留枚举，
  落库**恒为 `input_source`** ⇒ "某条记录被改、只有读它的证据失效"**做不到**，只能退化为整源失效。
- **`code_import` 是保守过近似（E14，幅度已量化）**：`impl_declared` **3127** vs `code_import` **65151**。
  `verification_model.py` 代码级 import 了 `dimension_table` / `element_reference` ⇒ **每一条** `verification_model.*`
  证据都被判依赖这两张表，即使该函数运行时并不读它们。这是任务单明确允许的「**先按整文件保守失效**」；
  两档在 `--report`、接口 `ref_bindings` 与前端 tooltip 里**分列报出**，**不把过近似说成精确**。
- **`needs_review` 的 34145 条边不可通过重验消除（E15）**：`--reverify` 默认 `only_stale=True`，
  **有意不重验** `needs_review` —— 它缺的是**数据本身**（来源未入库 / 无 locator），重验解决不了；
  强行重验会把 4 万余条证据无意义改造一遍，属**假动作**（铁律 #30）。
- **`FIDELITY_OF_STATUS` 是粗粒度映射（E16）**：`needs_review → unverifiable_no_source`，**不再区分**
  `no_locator` / `not_in_repo`；**对外披露仍以边级四分类 `edge_fidelity` 为准**，本映射只用于快速对照。
- **改写入库文件的"搬运"必须完整（E10，已修 · 静默错位）**：本轮实测两处 —— `dependencies_for()` **硬编码 `base=ROOT`**
  （临时仓库里算出的参考表哈希其实取自主仓库）、`recorded_deps()` **漏透传 `_src_hash_rewritten`**
  （记号在图上、却在裁决里消失 ⇒「原地改哈希即复活」的假象重现）。两者都是「取值口径一致、但**搬运**漏了一格」。
- ★★ **【E11 · 本轮踩到 · 已修】同一把不可重入锁的自死锁（铁律候选 #79）**：
  `viz_server.stale_registry()` 在 `with _STALE_LOCK:`（`threading.Lock`）里调用 `evidence_status_registry()`，
  后者又要拿**同一把**锁 ⇒ **自死锁**。**症状：CPU ≈ 0、无子进程、永久挂起**（实测挂 10 分钟无任何输出、无异常）
  —— 极易被误判为"图太大 / 磁盘慢 / 网络不通"。**处方**：① 计算移出锁外（锁只保护缓存读写）；
  ② 改 `threading.RLock()` 作双保险。**诊断口诀**：**CPU 时间与子进程数都低 ⇒ 是锁/等待，不是计算**。
- **【E12 · 已修】契约校验器曾**静默跳过** `additionalProperties`**：A04 契约用它约束 `evidence[].refs` 的**动态键**
  （`{ref_id: 哈希}`，无法用 `properties` 表达），而自检内置子集校验器**不实现**该关键字 ⇒ 该约束**根本没被检查**。
  ★ 自检 **C5b 确实报了红** —— 说明这条防线**有效**（它正是为防这类问题而写）。
  **已修**：补齐实现 + 反例/正例各一条（C5b2/C5b3）证其**真的被执行**（不只加进"支持集"）。
  ⇒ **D12 的"未实现关键字"这一项由"已知边界"转为"已修"**；其余子集限制仍存在。
- ★ **【E20 · 本轮踩到 · 测量污染，非代码缺陷】后台回归与前台编辑并发 ⇒ 伪语法错误 / RC=2**：
  全量回归里 `bash sttp.sh lineage` 报 `sttp.sh: line 397/398`，**而该文件当时／现在都只有 397 行**（line 398 不存在，
  `bash -n` RC=0、无控制字符）；**顺序重跑 RC=0、八段全过**。根因是**时间线竞态**：`13:31:11` 回归启动读取 396 行的
  `sttp.sh` ⇒ `13:31:36` 我在前台把注释改了一行（文件**变长 1 行**）⇒ bash 执行完 `lineage` 分支后**继续读剩余字节**，
  读到新版多出的尾部 ⇒ **行错位**。**判据（铁律候选 #80）**：**报错行号 > 文件行数 ⇒ 文件在读取期间被替换（并发写竞态），
  不是语法缺陷**。**纪律**：后台回归运行期间**不得修改被其读取的任何文件**；改了就该轮**整轮重跑**。
- ★ **【E21 · 本轮发现 · 真缺陷 · P1 · 未修】发布清单 `dirty_sample` 依赖不可信的 `git status` ⇒ 脏工作区下清单非幂等**：
  `make_manifest.py:304–305,327` 用 `git status --porcelain` 的**前 8 条**作 `dirty_sample`，而该命令在本仓**不可信**
  （**铁律 #72**：索引 stat 缓存）。实测**连续两次** `release`（工作区未变）产出**不同**的 `manifest.json`
  （md5 `6caa01cf…` → `7b2357fc…`），**而两个样例图逐字节不变** ⇒ 变化只在清单。
  **危害**：① 脏工作区下清单不可复现（**干净检出下** `porcelain` 为空 ⇒ 幂等，宣称仍成立）；
  ② `working_tree_dirty_at_build` 同源 ⇒ 理论上可能**假阴性**。
  **处方（Option）**：① 推荐把"必须干净工作区"写成 `make_manifest.py` **前置断言**（非空即 RC≠0）；
  ② 脏检测改确定性口径（`git diff --name-only` ∪ `--cached`，**排序**后取样）；③ 至少标注该字段非确定。
  ★ **边界声明**：本条**不**否定可复现性 —— `artifacts[*].sha256` 与样例图是**确定性**的（本轮两次运行逐字节相同）。
- ★ **【E22 · 干净检出实测发现 · 真缺陷 · P1 · 已修】`evdeps --check` ⑦ 段在干净检出里假红**：
  缺不入库的 `elementkg_raw.json` 时 ⑦ 段 FAIL（`基线应为 valid，实得 needs_review / unverifiable_source:ElementKG2.0`），
  而 `sttp.sh` 注释声称该部分「在干净检出里可运行」⇒ **注释与实现不符**。根因两处叠加：
  ① 输入源取的是**不入库**的大文件；② `src_hashes` 的 key 用条件式 `"ElementKG2.0"|"X"`，而 `src_sid` **硬编码**
  `"ElementKG2.0"` ⇒ 文件不存在时 `.get()` **恒为 None**（即便判断对了也算不出来）。
  性质：把「**环境缺数据**」判成 FAIL，**违反项目自身铁律 #20/#37**（缺依赖 ⇒ SKIP，不假通过也不假失败）。
  **已修**：⑦ 段输入源改为**临时仓库内可控构造**（`_probe_input.json`）⇒ 与「大文件是否入库」解耦；
  统一 key；新增**防回退断言**（探针 blob 哈希取不到 ⇒ FAIL + 明确原因，不得静默通过）。
  **验证**：主仓库**七段全过**；**真·缺数据环境**（干净检出 `ls elementkg_raw.json` = No such file）同样**七段全过**。
- **仪器总账（本轮）**：`evdeps --check` **七段**｜`lineage` **八段**（⑥ 33238/33238、⑦ 2431 中 0 误牵连）｜
  `subgraph` **89 / 0 / 3**（V1–V19）｜`frontend` + **X10–X15**｜`node` **85 / 0**（+F30/F31 共 19 条）｜
  delta 幂等（复跑 **0 改动**）｜临时目录残留 **0**。

#### 0.3.3 Phase 45（A05 第三人可安装入口）已承认的边界 —— 更新于 2026-10-14

> 全文见 `07_交付物/Phase45_A05第三人可安装入口_20261014.md`（**E23–E28** + §9 未覆盖项）。

- ✅ **复评 A05 三条现场全部坐实并已修**（`00_项目管理/codex复评响应与下一阶段规划_20261013.md` §2）：
  **A05a** 无 `pyproject.toml` ｜ **A05b** `requirements.lock` 里 `python==3.11.9` 与 pip 分发**混列**
  （`pip install -r` 会去找名为 `python` 的**分发**）｜ **A05c** root `.env` 硬编码他机 Python 路径、
  且**无根级模板**（只有两个 Neo4j 专用 `.env.example`）。
- ★ **自查出比复评更严重的现场**：全仓 **171 个入库 `.py`** 实际用到 **19 个**第三方 import，
  其中 **7 个在**此前的**任何**依赖清单里都**没有声明**（`sklearn`/`rapidfuzz`/`duckdb`/`polars`/`kuzu`/`openai`/`playwright`）
  ⇒ 复评说「无可安装入口」；实际是**连依赖声明本身都不完整**（照抄 `requirements.txt` 补了 `pyproject.toml` 也跑不了这几条路径）。
  ★ **口径须连范围一起写**：上句是**开工时**的测量（171 个 `.py`）；收官时为 **174 个**（+3 = Phase 45 自身新增）、
  **20 个**第三方 import（多出的是**构建期** `setuptools`，见 **E28**）。
- ★ **「零构建链」口径的精确化（请勿据旧表述判为违约）**：§5.2.1 的「零构建链」是关于**前端**的
  （`06_PoC/js/` 原生 ES Module，无 npm/打包器/转译），D14 的「零第三方依赖」是关于**不引入前端测试依赖**。
  **两者都不禁止一个 Python 打包元数据文件**；且 §0.1 的「**唯一例外**」明确点名「**可安装性**」照修。
  ⇒ `pyproject.toml` **只**声明元数据 + 入口，仓库至今**无 `package.json`**，前端零构建**不变**；
  **安装可选**，`bash sttp.sh <cmd>` 仍是零安装路径与唯一效果权威；**回退成本 ≈ 0**（删两个新文件即可）。
- ★ **解释器约束与 pip 依赖分离**（A05b 的根因处置）：解释器约束（机器可读）→ `pyproject.toml` 的
  `requires-python = ">=3.11,<3.12"`；pip 分发 → `requirements.lock`/`.txt`，**不含**解释器伪包。
  自检 A05-3 用版本比较器**实际求值**（`3.11.9` 通过、`3.13.12` **必须不通过**）；A05-6 断死并带反例。
- ★ **两级可复现等级（A05 要点，不得混为一谈）**：**L1 随包成品样例重放**（全部入库；不需权威图/联网）
  vs **L2 原始源 → 规范图重建**（需**不入库**的 `06_PoC/etl/normalized.json`）。
  自检把它**结构化**：`L1.needs_authority_graph=False` / `L2.needs_authority_graph=True`，并**构造性**断言
  「缺 manifest ⇒ L1 判为**不可用**」。⇒ 报告路径 A / B 与 `sttp-repro-level` 三处口径一致。
- ★ **新仪器 `bash sttp.sh install`**（`06_PoC/tests/phase45_install_selfcheck.py`，**零第三方依赖**）：
  **PASS 25 / FAIL 0 / SKIP 0**（RC=0，≈4.7 s）；★ **可在干净检出运行**（只依赖已入库文件）。
  **退出码分档**：`0` 全过 ／ `1` 有 FAIL ／ `2` 缺前置 ／ **`3` 仪器自证失败（≠1）**。
  实测反例：改 `requires-python` ⇒ A05-3 红；删 `rdkit` ⇒ A05-4/A05-7a 红（A05-7b 仍 PASS ⇒ 负对照与仓库状态**解耦**）；
  塞回 `python==3.11.9` ⇒ A05-6a 红；打坏比较器 ⇒ **RC=3**；全量还原后 `pyproject.toml` sha256 **逐字节回到基线**。
- **E23（测量器陷阱 · 本轮踩到）**：`grep -c $'\r'` 在本环境**恒等于总行数**（`$'\r'` 退化为空模式），
  而 `awk '/\r/'` / `tr -cd '\r' | wc -c` / `od -c` 一致给出真值。**它已成功误导过本轮判断一次。**
  ⇒ **行尾一律用后三者判定**（铁律候选 **#81**）。★ 连带复核建议：D24/D26 里的 CRLF 计数若当年用
  `grep -c $'\r'` 得来，**该数字需重测**（本指南不对旧数字下断言）。
- **E24**：`verify_release.REQUIRED_MODULES` 只是 **L1 最小集**；`pyproject` 核心集**刻意与之相等**（不加码）
  ⇒ `pip install sttp-kg`（无 extras）装完**只能跑 L1**。这是设计意图，非缺陷。
- **E25（未覆盖，不计通过）**：**未实测 `pip install .`** 与依赖可获取性（本环境零依赖且不保证网络）。
  代偿 = A05-13 在**临时目录**做**离线 wheel 构建**（实测 `sttp_kg-0.45.0-py3-none-any.whl`，**21.0 KB**，
  `cli.py` 在内、6 个入口点齐全），**不污染仓库**；构建后端缺失时记 **SKIP 并写明原因**。
- **E26（如实披露）**：7 个补声明的依赖中 **5 个（duckdb/polars/kuzu/openai/rapidfuzz）本项目环境未安装、
  对应路径从未运行** ⇒ 声明里**刻意给裸名（无版本）**：**不伪造「实测」版本**。
- **刻意不声明 `playwright`**：`06_PoC/tests/a4_offline_render.py` import 它，但 §0.1/D14 明确不装
  ⇒ 改为在自检的**显式 ACK 白名单**登记原因。★ 该白名单是 **fail-closed**（未声明且未 ACK ⇒ **FAIL**），
  与铁律 #71「白名单永远漏」（针对**静默**漏网）不冲突；另断死「已声明却仍在 ACK 里」以防白名单腐烂。
- **E27（隐蔽耦合 · 已修）**：`make_manifest.read_lock()` 原会把 `python==3.11.9` 解析进
  `dependencies.target` ⇒ 只按 A05b 字面删掉那行会**静默丢掉「目标解释器」**与解释器漂移报告。
  **已修**：`make_manifest.py` 新增 `read_requires_python()`，从 `pyproject.toml` 转录为
  `dependencies.interpreter{constraint, constraint_source, running}`；`target` 不再含解释器伪包；
  新增断言 **A05-14** 断死「**只有一个权威**」（`manifest.interpreter.constraint == pyproject.requires-python`）。
  ★ 已核对 `verify_release.py` **不读** `dependencies.target`（它独立 `importlib` 判 G1/G2）
  ⇒ 拆分不影响发布门禁口径（实测 `release` 仍 **28/0/0**）。
  ★★ **教训（与 E10 同族）**：**「删掉一个错误来源」之前，先查谁在读它** ——
  否则修好一处、弄丢一处，而两处都不会报错。
- **E28（仪器自指 · 已修）**：`phase45_install_selfcheck.py` **未跟踪**时 `install` = **24/0/0（绿）**，
  `git add` 之后 = **23/1（红）**，失败行 `A05-7a … 未声明：['setuptools(1处)']`。
  **两段根因**：① 探针 `wheel_probe()` 自己 `import setuptools`；② 文件枚举用 `git ls-files`
  （**只含已入库**）⇒ 该文件未跟踪时**不在扫描范围**、入库后**突然进入** ⇒ 其 import 立刻暴露。
  **两处修复**：`declared` 由「核心 + 可选」扩为「核心 + 可选 + **构建期** `[build-system].requires`」，
  并新增 **A05-7c**（从 `build-backend` **派生**比对名，不硬编码 `setuptools`）；
  枚举改为 `git ls-files`（入库）**∪** `--others --exclude-standard`（未跟踪，仍尊重 `.gitignore`），
  NOTE 里**分列「入库 N + 未跟踪 M」**（实测 `174 + 0`）。
  ★★ **教训（铁律候选 #82）**：**仪器一旦入库，它自己就是被测对象** ——
  任何「全仓静态扫描」型自检，**文件枚举必须包含未跟踪文件**，否则「**提交前绿、提交后红**」；
  且改探针 = 改被测集合。**性质**：与 #76「全红也可能是测量器坏了」同族，方向相反 ——
  这里坏的是「**绿**」（扫描范围窄于被测范围 ⇒ **假绿**）。
  ★ 收尾值：`install` **25/0/0 RC=0**；**全量回归 13/13 全绿**（`release` 28/0/0、
  `release-full` 108/0/0、`subgraph` 89/0/3、`frontend` 70/0/2、`node` 85/0）。
  副产品：**E21 再次被实测坐实** —— 同一份清单在**脏树**非幂等（`working_tree_dirty_at_build: true`）、
  在**干净树**幂等（`dirty_sample: []`）。

---

## 一、项目愿景与目标

**一句话目标**：把数学公式、物理规律、化学物质与物理世界连成一张可推理的知识图谱。

**长期愿景**：用户想看到一条完整的跨域推导链——
```
E=mc² (Formula)
    ↓
能量 (PhysicalQuantity)  ←  has_symbol
    ↓
CH₄ + 2O₂ → CO₂ + 2H₂O  (Reaction)
    ↓
甲烷分子 (Molecule) ← composed_of ← 碳原子 / 氢原子 (Element)
```
最终实现：从纯数学定理出发，一路推理到真实化学分子的物理化学性质，全链路可符号验证。

**当前进度**：四层架构（数据层 / 知识层 / 推理生成层 / 交互层）均已 MVP 落地，图谱规模 **9657 节点 / 48712 边**（Aura 云端集合级对账零差异），已开源至 GitHub。
**北极星指标「跨学科连通性」已仪器化（`06_PoC/task_trust_audit.py`，8 任务族 T1–T8 成对读）**：⚠ **连通分量 6**（最大 9628 + **5 个真孤岛 / 29 节点**，见下）、孤立节点(deg=0) **0**；任务级可信完成率 **40482 / 40628 = 99.6%**。
**Phase 26 治理学科均衡**（物理占比 **71.5% → 54.3%**）；**Phase 29 跨域桥专项**把最短板 **T7 从 0/53 = 0.0% 拉到 861/962 = 89.5%**
（根因＝**双口径缺陷**：门槛 `human_reviewed` 自动化不可达 + 类型窗口漏掉主桥 `has_quantity`；另有 **700 条真桥被误判为模型预测**）；
**Phase 30「去伪存真」**：**撤回 81 条可被确定性反驳的语义边**（含 **5 条策划数据错误** `力→kg` 等）+ **建起 14 条真桥**（数学 13 + 单位 1）
→ **T7 879/949 = 92.6%**、**数学↔物理 3.4% → 72.7%**、**化学↔数学 0.0% → 83.3%**。
**Phase 31「复算维度全覆盖」**：把**确定性复算从「只对模型产物」推广到「所有来源」** —— 关闭**命名缺口**
（168 个物理量中 **84 个（50%）查不到量纲** → 扩 `dimension_table.ALIAS` 22 条）+ **A10 / A3扩展 / A1落地 / A11**
→ **升级 263 条 / 撤回 48 条**（含**第 6 条策划数据自身的错误** `Electric field --has_unit--> volt`，应为 **V/m**）
→ **T3 量纲一致性 56.4% → 80.9%**、**T6 常量溯源 76.1% → 95.7%**、**T5 99.1% → 99.9%**、**T7 890/945 = 94.2%**；
门禁 **53 → 67 条（PASS 67 / FAIL 0 / SKIP 0）**；新增**门禁「非真空」自检**（`sttp.sh gatecheck`：注入已知假边断言真会红）。
⚠ **两轮共同的副产物**：撤边后**连通分量 1 → 5 → 6**（**5 个真孤岛 / 29 节点**：微积分簇 16 / 几何三角簇 5 / **代数概念簇 3** / 电感 3 / 电容 2）——
**这些节点与主图的连接此前全靠假边** → **回退是「恢复真相」而非退化**；已把孤岛集合冻结进
`06_PoC/known_islands.json` 并纳入门禁（新增孤岛即 FAIL），**拒绝为了让指标好看而补无证据的边**。
⚠ **跨域桥已非第一短板**；但**每个新维度都可能是一片从未被检验的空白**（「单位」维一加复算即冒出 29 条违规；
**人工策划 / 来源断言的边同样「从未被检验过」**）—— 下一步转向 **Claim/Evidence 完整对象化**（详见 `00_项目管理/下一步开发计划_20261008.md`）。

---

## 二、当前运行状态

### 2.1 云端（Neo4j Aura）
```
URI:        neo4j+ssc://853a33bc.databases.neo4j.io
Database:   853a33bc
Username:   853a33bc
Password:   （见 环境变量 / .env，禁止明文）
总计:       9657 节点 / 48712 边（与本地集合级对账零差异）
```

> ⚠️ Aura 免费实例约 5 万节点上限，当前 **9657 节点 / 48712 边**（云端与本地集合级对账零差异）；扩容（如 Rhea 全库 1.9 万条反应、或 WebBook 全量 1093 分子）前先估算增量并分批推。

### 2.2 本地可视化服务
```
地址:   http://127.0.0.1:8765/
数据:   06_PoC/graph_data_phase34.json（9657 节点 / 48712 边）—— 由 .env 的 GRAPH_DATA_FILE 指定
前端:   06_PoC/graph_view.html（Cytoscape.js + MathJax，**依赖已本地 vendored**，可完全离线）
```

**启动命令**（在项目根目录，**推荐**）：
```bash
bash sttp.sh viz          # 自动读 .env：GRAPH_DATA_FILE / VIZ_PORT / STTP_PYTHON
```
> 长驻请后台启动并重定向日志，例如：`bash sttp.sh viz > .runlog/viz.log 2>&1 &`
> 手动等价形式：`GRAPH_DATA_FILE=06_PoC/graph_data_phase34.json python 06_PoC/viz_server.py`
> ⚠️ 必须用系统 Python（`.env` 的 `STTP_PYTHON`）；托管 3.13 是空环境，缺依赖。

### 2.3 GitHub 仓库
```
仓库:   https://github.com/zhing99-hash/STTP
分支:   main
最新:   9498fe0（跨学科连通性审计：仪器化 + 7 类源头修复 + 2 处推送基础设施修复）—— 已 push，本地 == 远端
上游:   8c3a448 → fb5f402 → 9498fe0
```
> ⚠️ 直连 GitHub 被出口白名单拦（0/3），**必须走出口代理**（见 §9.5）。代理端口**会变**，用前先 **逐端口实测枚举**
> （`for p in $(netstat -ano | grep -i listening | grep 127.0.0.1: | sed 's/.*://' | sort -n -u); do curl -x http://127.0.0.1:$p -s -o /dev/null -w "$p %{http_code}\n" -m 8 https://github.com/; done`）。
> 本轮可用端口 **10808**（历史 `50515`→`53307`→`51347`→`10808`）。
> ⚠️ **切勿设 `GIT_TERMINAL_PROMPT=0`** —— 会让 `git-credential-manager` 拒绝供凭据。历史成功率约 1/3，脚本里**务必带重试循环**（大提交单次推送可能 >2 分钟，**请后台跑**）。

---

## 三、目录结构

```
01tuopu/
├── 00_项目管理/           # 看板、进度、团队分工（团队分工与进度.md）
├── 01_架构与数据模型/     # Schema v0.1 定义、数据模型文档
├── 02_数据层/             # 各数据源适配器（MathXiv、PhysicsBabel…）
├── 03_知识层/             # queries.py（图查询）、load_neo4j.py（灌库）
│   └── etl/               # normalized.json（权威原始格式，viz 后端源）
├── 04_验证闭环/           # 符号校验（R-PHY / R-CHEM / R-MATH）
├── 05_可视化/             # D3 / Cytoscape 相关资源
├── 06_PoC/               # ★核心产出目录
│   ├── graph_view.html    # 可视化前端壳（Cytoscape.js）
│   ├── js/               # ★前端 ES Module（Phase 43，原生 import，零构建）—— 见 §5.2.1
│   ├── tests/            # 自检：phase43_frontend_selfcheck.py / frontend_core_check.mjs
│   ├── viz_server.py     # 可视化后端（标准库 http.server，端口 8765）
│   ├── graph_export.py   # ★权威 raw → viz 格式转换器（必须用此函数）
│   ├── build_phase*.py   # 各 Phase 构建脚本（自动合并 seed*.json）
│   ├── build_full_graph.py
│   ├── robust_aura_loader.py  # ★健壮加载器（分批 + 重连重试）
│   ├── split_aura_delta.py
│   ├── export_normalized.py    # 从 Aura 导出原始格式（修复 viz 后端源）
│   ├── etl/              # normalized.json / phase*_aura_delta.json
│   └── graph_data*.json   # 各 Phase viz 格式快照（graph_view 直接读）
├── 07_交付物/             # 各阶段交付报告
├── 08_部署包/neo4j/      # Docker 本地部署包（docker-compose.yml 等）
├── 09_科研扩展/
│   ├── 9_inference/      # ★推理层核心脚本
│   │   ├── gnn_infer.py              # 桥接导向 GraphSAGE 链接预测
│   │   ├── llm_hypotheses.py        # LLM 语义假设生成
│   │   ├── llm_review_85.py         # 85 条假设 LLM 复核
│   │   ├── phase9_deepen.py          # Phase 9 深化（摩尔质量锚点 + 反应角色桥）
│   │   ├── export_aura.py           # 从 Aura 反向导出权威图
│   │   ├── sync_aura_phase9b.py     # Phase 9b 同步到 Aura
│   │   ├── sync_aura_review.py      # 85 假设复核结果同步到 Aura
│   │   ├── phase9_deepen_edges.json # Phase 9 深化产出（188 边增量）
│   │   ├── phase9_refined_raw.json  # Phase 9 精筛产出
│   │   ├── phase9_candidates.json   # Phase 9 GNN 候选边
│   │   ├── _recon.py                # 环境侦察脚本
│   │   └── _probe_graph.py          # 图谱字段探查脚本
│   ├── 5A_wikidata/     # Wikidata same_as 边接入（19 条）
│   ├── 5B_gnn/         # GraphSAGE 推断（20 条 GNN 候选）
│   └── 5C_entity_linker/ # 跨源实体对齐（7 条高置信）
├── 10_种子数据/          # ★13 个垂直切片 seed JSON（手工策划骨架）
│   ├── seed_common.py   # 共享构造器（CamelCase / domain 着色 / pint 校验）
│   ├── build_*.py      # 各切片构建脚本（14 个，共 13 个 seed JSON）
│   └── seed_*.json      # 各切片策划 JSON（骨架桥接基础库）
├── 11_真实数据/         # ★真实数据适配器
│   ├── elementkg_ingest.py    # ElementKG OWL → raw（118 元素 + same_family/same_period）
│   ├── elementkg10m_ingest.py # ElementKG 10M CSV → 有界子图（800 反应 / 713 分子）
│   ├── pubchem_mol_ingest.py  # PubChem PUG-REST → 14 真实分子 + composed_of
│   ├── physicsbabel_ingest.py  # PhysicsBabel parquet → 600~5000 方程 + 物理量
│   ├── elementkg.owl          # ElementKG OWL 文件（~4 MB，真实元素数据）
│   ├── elementkg_raw.json     # ElementKG OWL 解析结果（118 节点 / 2044 边）
│   ├── physicsbabel_raw.json   # PhysicsBabel 解析结果（已生成，待推）
│   └── 公共数据集/             # ElementKG 10M CSV（858 MB，勿入 git）
│       └── 10m_elementkg_release.csv
├── README.md
├── .gitignore
├── .env.example                 # ★ 根级环境变量模板（Phase 45 A05；**无机器绝对路径**）
├── pyproject.toml               # ★ 打包元数据 + 入口声明（Phase 45 A05；安装**可选**，前端零构建不变）
├── sttp_cli/                    # ★ 入口薄封装（find_root / repro_level / 4 个子命令，委托既有脚本）
├── requirements.txt / .lock     # 松约束 / 精确锁（★ 解释器约束在 pyproject 的 requires-python）
├── sttp.sh                      # ★ 一体化入口（含 `install`：A05 可安装性自检）
├── Phase*.md                    # 各阶段归档报告
└── PROJECT_DEVELOPMENT_GUIDE.md  # 本文件
```

---

## 四、数据模型

### 4.1 节点类型（15 类，受控词表）

| 类型 | 数量 | 说明 | 典型 id 前缀 |
|------|------|------|------------|
| Formula | 5082 | 物理方程（PhysicsBabel 引入） | PB:fo:, MX:formula: |
| **Molecule** | **2133** | 分子（含原子组成）｜Phase 26 +1378 ChEBI 化合物 | MO:, PC:mol:, **CH:cpd:**, EK2:mol:, BC:mo:, OM:mo:, IC:mo: |
| **Reaction** | **1708** | 化学反应｜Phase 26 +939 Rhea 反应（带 EC 号） | **RH:rxn:**, RX:, EK2:rxn:, BC:rx:, OM:rx: |
| Paper | 181 | 文献（OpenAlex 引入，Schema v0.2） | PA:oa: |
| PhysicalQuantity | 144 | 物理量 | CM:pq:, MX:phy:, PB:pq:, TH:pq:, PQ: |
| Element | 118 | 化学元素（含原子量/周期/族，已去重） | EK:el: |
| Symbol | 113 | 数学/科学符号 | MX:sym: |
| FunctionalGroup | 76 | 官能团（ElementKG 引入） | EK2:fg: |
| Unit | 33 | 国际单位 | UN: |
| WikidataEntity | 24 | Wikidata 对齐实体 | WD: |
| Constant | 23 | 物理常量（CODATA 2022 真实化） | TH:pq:, MX:const:, PB:pq: |
| MathConcept | 13 | 数学概念 | MX:math: |
| Definition | 4 | 数学定义 | MX:def: |
| Lemma | 2 | 数学引理 | MX:lemma: |
| Theorem | 2 | 数学定理 | MX:thm: |

> ⚠️ **类型判定必须用 `graph_export.pick_type(labels)`**（传 `labels`，不是节点对象）。
> 历史上用 `labels[0]` 统计，导致首标签恒为 `Entity` 的 1658 个节点被吞。
> **标签收敛**：`Physical_quantity`（拼写漂移）/ `ElementEntity` / `PhysicsEntity`（外部源自造）三个表外桶
> 已于 2026-10-09 归零（`03_知识层/normalize_labels.py`，词表 = `TYPE_PRIORITY` ∪ {Entity}）。
> 上表 15 类**全部 ∈ 受控词表**；Phase 26 新增的 `RH:`/`CH:` 节点复用 `Reaction`/`Molecule` 两个既有类型（**零 schema 改动**）。

**每个节点的结构**（raw 格式，`normalized.json`）：
```json
{
  "id": "MX:chem:co2",
  "labels": ["Entity", "Molecule"],
  "props": {
    "id": "MX:chem:co2",
    "name": "Carbon dioxide",
    "formula": "CO2",
    "meaning": "化学式 CO2",
    "domain": "chem.molecule",
    "ntype": "molecule",
    "confidence": 1.0,
    "explicit_or_inferred": "explicit",
    "source": "curated_seed/energy_combustion"
  }
}
```

### 4.2 边类型（22 种）

| 边类型 | 数量 | 说明 | 门控 |
|--------|------|------|------|
| has_symbol | 24367 | 公式/物理量 → 符号 | — |
| **composed_of** | **7756** | 分子 → 元素（含 count；Phase 26 +5092 ChEBI 分子式） | R-CHEM |
| has_quantity | 4385 | 分子/元素 → 物理量（摩尔质量、类药性描述符…） | R-PHY |
| **reactant_of** | **2753** | 反应物 → 反应（Phase 26 +2074 Rhea） | R-CHEM |
| **product_of** | **2559** | 产物 → 反应（Phase 26 +2460 Rhea） | R-CHEM |
| has_functionalgroup | 2350 | 分子 → 官能团 | — |
| reagent_of | 1807 | 试剂 → 反应 | R-CHEM |
| same_period | 1360 | 同周期元素 | — |
| dimensionally_consistent | 56 | 量纲严格相等（真值表判定，P0-1 修复） | R-PHY |
| same_formula_as | 6 | 同分子式（**非同一实体**，P0-4 修复，`verification_scope=formula_only`） | R-CHEM |
| same_latex_normalized | 0 | LaTeX **字符串**规范化相同（**非数学等价**，P0-2 修复，NEEDS_REVIEW） | R-MATH |
| same_family | 374 | 同族元素（f 区实为「同系列」） | — |
| discusses | 178 | 文献 → 概念 | — |
| cites | 176 | 文献引用 | — |
| defines | 138 | 定义者 | — |
| derived_from | 120 | 数学推导 / 常量派生 | R-MATH |
| has_element | 109 | 分子 → 元素 | R-CHEM |
| **same_as** | **108** | 跨源对齐（Wikidata / 基础库 / **ChEBI 名称桥**） | — |
| has_unit | 78 | 物理量 → 单位 | R-PHY |
| related_to | 53 | GNN 待复核假设（NEEDS_REVIEW） | ⚠️ 未验证 |
| part_of | 5 | 部分-整体 | — |
| chemical_reaction | 2 | 反应类型标注 | — |
| proves | 2 | 证明关系 | R-MATH |
| releases | 1 | 放热标注 | — |

> **跨域边 = 869 条 / 49881 边 = 1.74%**（北极星指标，**须与绝对数成对读**：分子未变、占比被 Phase 26 化学侧分母摊薄）；其中 `has_quantity` 一种占 **784 = 90%**
> —— **跨域结构对单一关系类型高度依赖，是扩容器时的重点风险**。

**每条边的结构**（raw 格式）：
```json
{
  "id": "P9D:has_quantity:MO:ch4->PB:pq:molar_mass",
  "source": "MO:ch4",
  "target": "PB:pq:molar_mass",
  "type": "has_quantity",
  "kind": "molecule_molar_mass",
  "props": {
    "confidence": 1.0,
    "verified": true,
    "verification_gate": "R-PHY",
    "rationale": "molar mass = Σ atomic_weight × count",
    "explicit_or_inferred": "inferred",
    "value": 16.043,
    "unit": "g/mol",
    "source": "Phase9.deepen"
  }
}
```

### 4.3 id 命名约定

所有节点 id 必须带源前缀，不允许裸 id：

| 前缀 | 来源 | 示例 |
|------|------|------|
| MX: | MathXiv 公式图谱 | MX:thm:gauss_bonnet, MX:phy:newton2 |
| MG: | math-graph 数据集 | MG:pq:force |
| PB: | PhysicsBabel | PB:fo:0, PB:pq:molar_mass |
| EK: / EK2: | ElementKG OWL / 10M CSV | EK:el:Fe, EK2:mol:molecule_977 |
| PC: | PubChem | PC:mol:280 |
| **RH:** | **Rhea（EBI 反应库，Phase 26）** | **RH:rxn:10000** |
| **CH:** | **ChEBI（化学本体，Phase 26）** | **CH:cpd:15377**（水） |
| PA: | OpenAlex（文献层） | PA:oa:W2005936791 |
| WD: | Wikidata | WD:Q742833 |
| ~~EL:~~ | **已废弃**（A5 去重并入 `EK:el:<Symbol>`） | ~~EL:h~~ → `EK:el:H` |
| MO: | 手工种子（分子） | MO:ch4, MO:co2 |
| RX: | 手工种子（反应） | RX:comb_ch4 |
| CM: | 经典力学切片 | CM:pq:mass, CM:un:pascal |
| TH: | 热力学切片 | TH:pq:temperature |
| EM: | 电磁学切片 | EM:pq:voltage |
| BC: | 生物化学切片 | BC:rx:respiration |
| OM: | 有机化学切片 | OM:rx:ethane_comb |
| IC: | 无机化学切片 | IC:rx:neutralization |
| CE: | 化学平衡切片 | CE:fo:mass_action |
| NT: | 数论切片 | NT:thm:euler |

> ⚠️ **`EL:*` 已于 2026-10-09 全面退出**（`10_种子数据/seed_common.py` 的 `BASE_IDS` 同步改为 `EK:el:*`）。
> 此前 `BASE_IDS` 仍写 `EL:c/h/o`，导致 42 条 `composed_of` 边**被静默丢弃**（引用不存在的 id，悬空即被过滤）。

---

## 五、核心脚本说明

### 5.1 `06_PoC/graph_export.py` — ★权威格式转换器
**必须使用此函数**生成供 viz 和 Neo4j 使用的 JSON，禁止手工 merge 或直接写 JSON。

```python
import graph_export
viz = graph_export.build_graph_data(raw_json_path)  # 返回 viz 格式 dict
# 输出字段：nodes[id/label/type/labels/subject/domain/formula/confidence/attrs]
#           edges[id/source/target/type/kind/confidence/explicit_or_inferred/verified/gate/evidence/rationale/domain]
```

**type 优先级**（`TYPE_PRIORITY`，**须用 `pick_type(labels)` 判定**，勿用 `labels[0]`）：
```
Symbol > Element > Molecule > Reaction > FunctionalGroup > Constant > PhysicalQuantity > Unit >
Paper > WikidataEntity > Definition > Theorem > Lemma > Equation > MathConcept > Formula > （兜底 Entity）
```
> `Paper`（Phase 8 OpenAlex）与 `WikidataEntity`（Phase 9）是后补登记的 —— 在登记之前，
> 这 181 + 24 个节点只能靠 `labels[0]` 的偶然排列顺序被「碰巧」识别，属**静默依赖**。

**domain / 命名空间 → subject 着色**（`subject_of(domain, node_id)`，优先级：**显式 domain > id 命名空间兜底**）：
- **物理**：`phys` / `phy` / `pb`；命名空间 `PQ` `PB` `CM` `QM` `TH` `EM` `OP` `SM` `RT` `UN` `SY` `CO` `FO`
- **化学**：`chem` / `ek` / `bio*`；命名空间 `EL` `EK` `EK2` `IC` `BC` `RX` `OM` `MO` `PC` `CE`
- **数学**：`math` / `mx` / `mg`；命名空间 `MX` `MG` `MA` `MC` `NT` `PA`（本批 Paper 是数学文献切片）
- **跨学科**：`WD` / `wd`（Wikidata 真实世界实体）
- 其他 → 跨学科

> ⚠️ **口径变更史（2026-10-09）**：`_NS_SUBJECT` 曾把 **`CM`/`QM`/`TH`/`EM` 标成「数学」**，
> 直接**污染跨学科判定**（凭空造出 63 条假跨域边）。修正后跨域边从 1054 降到 991（修数据前）→ 再降到 869（修数据后）。
> **改学科口径一定要做反向对照**，否则会把「剔除假数据」误读成「连通性退化」。

### 5.2 `06_PoC/viz_server.py` — 可视化后端
```
端口：     8765（默认）
数据源：   graph_data.json 或 GRAPH_DATA_FILE 环境变量
API 端点：
  GET /                        → graph_view.html（★ 支持 `?mode=live|bundle`；见下）
  GET /graph_data.json         → 完整图谱（JSON）★ 46.5 MB，前端默认【不】请求（铁律 #66）
  ★ GET /js/<file>.js          → 前端 ES Module（Phase 43；★ 必须 `text/javascript`，MIME 不对浏览器拒载 module）
  GET /api/neighbors?node=<id>→ 邻接节点（legacy）
  GET /api/stats              → 图谱统计
  ★ GET /api/search?q=<文本>&limit=N      → 文本搜索（命中 + 分数）
  ★ GET /api/subgraph?...                 → 局部子图（ego / path / filter 三模式，五态 + 预算）
  ★ GET /api/path?src=<id>&dst=<id>&max_paths=20&max_hops=5&deadline_ms=3000
                                          → 有界路径（legacy 扁平信封；新代码请用 mode=path）
  ★ GET /api/overview?mode=aggregate|sample&k=N → 概览元图（替代「全图浏览」）
启动：     python 06_PoC/viz_server.py      ★ 一律用 `bash sttp.sh viz`（长驻须后台 + 重定向）
参数：     --port 8080 / --host 0.0.0.0
```
> ⚠️ 后端依赖 `06_PoC/etl/normalized.json`（Aura 原始格式）。该文件已被 `export_normalized.py` 维护，若缺失 viz 可正常启动但 API 降级。

#### 5.2.1 前端（Phase 43 步骤 B）—— 原生 ES Module，**零构建链**
`06_PoC/js/` 共 9 个模块，浏览器 `import` 原生加载，**无打包器 / 无 npm / 无转译**（§0.1）。
`graph_view.html` 仅作壳：样式 + `<script type="module" src="js/main.js">` + 容器节点。

| 模块 | 职责 | 依赖 |
|---|---|---|
| `js/core.js` | 纯逻辑：五态归一化、参数构造、状态合并、会话预算、模式真值表、WCAG 对比度。**零 DOM / 零 fetch** | 无 |
| `js/style.js` | 视觉常量（纯数据）：学科色、19 种节点类型、**24 种边 type 配色**、7 档验证徽章（`rank` == `queries._RANK`） | 无 |
| `js/api.js` | HTTP 层：`class Api`（`fetchImpl` 可注入 / `AbortController` 超时 / 400⇒`BAD_REQUEST`） | `core.js` |
| `js/modes.js` | 模式判定：**先探 `/api/stats`**，可用 ⇒ `live` 且**不触碰** `/graph_data.json`（铁律 #66） | `core.js` |
| `js/subgraph.js` | `class SubgraphStore`：`run/expand/back/reset/jsonSnapshot` | `core.js`,`api.js` |
| `js/viz.js` | Cytoscape 封装：样式数组、增量 `add`、**只对新元素跑布局**、导出 PNG/SVG | vendor cy |
| `js/panels.js` | 查询表单 / 状态行 / 横幅（★ 边**语义关系 `type`** 与**来源管线 `kind`** **两组分列**）；`bindSearch` 防抖 + 过期响应丢弃 | `core.js`,`style.js` |
| `js/evidence.js` | 节点/边证据卡（MathJax + 复制 LaTeX；证据 `src`/`impl` 分列、`src_hash[:12]`、**四档忠实度横幅** `fidelity`、`src_hash=null` ⇒「无 locator · 不可复算」徽标） | `style.js` |
| `js/main.js` | 编排：`applyStatusVars` 把 `STATUS_STYLE` 写入 CSS 变量（单一来源）、五个入口、布局切换、键盘可访问 | 全部 |

**自检（两条，互不替代）**：
```bash
bash sttp.sh frontend                                  # 59 PASS / 0 FAIL / 2 SKIP（含 HTTP 层 H1–H6g、跨层 X1–X7、守卫 G1–G4）
node 06_PoC/tests/frontend_core_check.mjs              # 53 PASS / 0 FAIL（纯逻辑 + panels 真实渲染，零依赖）
```
> 留痕 `07_交付物/Phase43_frontend_selfcheck.json`；报告 `07_交付物/Phase43_局部图谱查询与前端_20261014.md`（含 D14–D25 + §7 干净检出实测）。
> **已知边界见 §0.3.1** —— 尤其 **D14 无头浏览器渲染未实测**（零依赖环境无 Playwright/jsdom，自检内 SKIP 且**不计通过**）。
> ★ **Phase 43 新增端点的契约**：`contracts/*.schema.json`（+ `contracts/README.md`）；
> 自检 `bash sttp.sh subgraph`（**51 / 0 / 3**，留痕 `07_交付物/Phase43_selfcheck.json`）；
> 五态 = `ok / no_result / truncated / timeout / error`（**业务态一律 HTTP 200**，只有参数非法才 400）；
> 会话预算 `120 节点 / 200 边`（无 `session_id` ⇒ 不记账；`reset=1` 清空）；`expand_token` 服务端 HMAC 签发。
> ★ **已知边界（务必先读）**：`PROJECT_DEVELOPMENT_GUIDE.md` §0.3.1 与
> `07_交付物/Phase43_局部图谱查询与后端_20261013.md` §6（D1–D12）。

### 5.3 `06_PoC/robust_aura_loader.py` — ★健壮加载器
```powershell
# 基本用法（幂等 MERGE，不重复写入）
python 06_PoC/robust_aura_loader.py --input <delta.json>

# 纯边加载（节点已在，只补边）
python 06_PoC/robust_aura_loader.py --input <delta.json> --edges-only

# 分批大小（默认 2000 条/批，可调）
python 06_PoC/robust_aura_loader.py --input <delta.json> --batch 500
```
> ⚠️ Aura 免费实例连接池有限，大批量（>5000 边）必须分批；单大事务在 flaky 连接下会整体回滚（静默失败）。必须验证云端边数确认写入成功。

### 5.4 `06_PoC/build_full_graph.py` — 全量图构建
```powershell
# 合并所有 seed + 各 Phase 真实数据，输出 graph_data_full.json
python 06_PoC/build_full_graph.py
```
> 需在 `06_PoC/` 目录下运行，依赖 `10_种子数据/seed_*.json` 和各 Phase 产出。

### 5.5 `09_科研扩展/9_inference/phase9_deepen.py` — Phase 9 深化
```python
# 生成两类真实跨域桥（无需 RDKit）：
#   1. 摩尔质量锚点（molecule → PB:pq:molar_mass，R-PHY 量纲校验）
#   2. 反应角色桥（reaction → molecule，R-CHEM 方程解析）
python 09_科研扩展/9_inference/phase9_deepen.py
# 输出：phase9_deepen_edges.json（nodes=[], edges=188）
```

### 5.6 `09_科研扩展/9_inference/export_aura.py` — 从 Aura 反向导出 viz 快照
```bash
# 从 Aura 拉取全量图 → viz 格式 → 06_PoC/graph_data_aura.json（默认）
bash sttp.sh export
python 09_科研扩展/9_inference/export_aura.py --out 06_PoC/xxx.json   # 自定义输出
```
> 用于：Aura 更新后同步本地快照；排查云端与本地是否一致。
> ⚠️ **输出必须避开 `graph_data_phase<N>.json` 命名** —— 那是历史快照，反向导出覆盖它们会让历史不可回溯。
> 脚本**内置守卫**：目标形如 `graph_data_phase<N>.json` 时直接拒绝（除非 `--force`）。
> ⚠️ 本脚本产出 **viz 格式**（含 `schema`/`meta`），与权威 raw 图 `06_PoC/etl/normalized.json`（仅 `nodes`/`edges`）**不是同一格式，不可互相覆盖**。

### 5.7 `06_PoC/neo4j_props.py` — DB 边界属性消毒（**推 Aura 前必过**）
```python
import neo4j_props
props = neo4j_props.sanitize_props(raw_props, where="edge")   # 嵌套容器 → json.dumps（无损）
```
> Neo4j 属性值只接受 primitive / 同质 primitive 数组。本地 NetworkX **不校验**，
> 所以 `dict` / `list[dict]` 会在本地一路无事、**只在推 Aura 时让整批事务失败**。
> 已接入 `11_真实数据/push_element_merge.py` 与 `06_PoC/robust_aura_loader.py`。
> ⚠️ 新增推送器时**务必同样接入**，否则会重演 2026-10-09「exit=0 但静默丢 600 边」的事故。

### 5.8 `06_PoC/reconcile_aura_edges.py` — 云端/本地边对账
```bash
bash sttp.sh reconcile --report-only   # 只报告差异
bash sttp.sh reconcile                 # 实际清理（使云端严格 == 本地）
```
> 推送**只能加**：历史上被替换掉的旧方案残留（如 Phase 11 的 `kind=reaction_role`）不会被自动清除。
> 以本地 `06_PoC/etl/normalized.json` 为基准：删云端独有三元组 / 删同三元组 kind 变体 /
> **补本地独有边**（含「删后需按本地 kind 补回」兜底）。已并入推送编排的步骤 12。

### 5.9 `06_PoC/connectivity_audit.py` — ★北极星连通性仪器（**2026-10-09 新增**）
```bash
python 06_PoC/connectivity_audit.py                    # 人读报告
python 06_PoC/connectivity_audit.py --json out.json    # 机器读
python 06_PoC/connectivity_audit.py --input 06_PoC/etl/normalized.json   # 指定权威 raw 图
python 06_PoC/connectivity_audit.py --strict           # 有异常（分量>1 / 孤立>0 / 悬空 / 自环）时退码 2
```
> **四块输出**：① 连通分量 / 孤立节点（含每分量节点数列出）② **跨域边矩阵**（学科对 × 边类型）
> ③ 学科分布 ④ 命名空间 × 学科一致性（`_NS_SUBJECT` 与显式 domain 是否冲突）。
> `MX` / `PQ` 为**枢纽命名空间**（有意跨学科，白名单内不做「混标」警告）。
> ⚠️ **指标口径会随 `graph_export.subject_of()` 变更而变** → 改口径时**必须做反向对照**，
> 否则无法区分「连通性退化」与「假跨域边被剔除」。详见 `07_交付物/跨学科连通性审计报告_20261009.md`。

### 5.10 `06_PoC/apply_delta.py` — ★通用本地 delta 应用器
```bash
python 06_PoC/apply_delta.py --delta <delta.json> --phase <N>            # dry-run（默认）
python 06_PoC/apply_delta.py --delta <delta.json> --phase <N> --apply    # 实际写入
```
> delta schema 四段：`{meta, nodes, delete_nodes, edges, delete_edges}`。
> 支持 upsert / 删除 / **merge** / 删边；**默认 dry-run**；执行前**自动备份** `normalized.json`；
> 内置 **6 项自检**（悬空 / 自环 / id 唯一 / 计数 / 已存在节点 labels 不变 / 备份可回滚）。
> **`normalized.json` 是权威基准**，改数据必须先在此 apply 通过，再谈推 Aura。

### 5.11 `06_PoC/sync_seed_delta.py` — seed → 权威图**差量回灌**（**2026-10-09 新增**）
```bash
python 06_PoC/sync_seed_delta.py                 # 计算 seed 与权威图的差量（dry-run）
python 06_PoC/sync_seed_delta.py --delta-only <备份.json>   # 据备份重建 delta
```
> 用途：`10_种子数据/` 的 `build_*.py` 改动后，**不必全量重建**，只把差量并回权威图。
> **三类硬保护**（缺一不可）：① `VOLATILE`（`created_at` 等时间戳）**排除出 diff**，
> 否则每次重跑都产生几百条假阳性 upsert；② 已存在节点**只改 props、不碰 labels**（种子标签是简写，会覆盖图谱权威标签）；
> ③ **权威性守卫** —— 外部权威来源（`nist_codata`/`openalex`/`pubchem`/`chembl`/…）的属性
> **禁止被种子降级覆盖**（实测拦住 9 处越权覆盖，如 `TH:pq:gas_const` 的 `8.314` 会被种子的粗值覆写）。
> ⚠️ `delete_nodes` 必须输出**纯 id 字符串**，不可输出 `[{"id":...}]`（见 §9.1 静默空操作）。
> `PAYLOAD_KEYS`（`value`/`unit`/`uncertainty`/…）走专用比对，避免把「数值+单位」误判为变更。

### 5.12 `03_知识层/normalize_labels.py` — 标签受控词表收敛（**2026-10-09 新增**）
```bash
python 03_知识层/normalize_labels.py --dry-run    # 默认
python 03_知识层/normalize_labels.py --apply
python 03_知识层/normalize_labels.py --delta-only <备份.json>   # 据备份重建 delta
```
> 把 `Physical_quantity`（拼写漂移）/ `ElementEntity` / `PhysicsEntity`（外部源自造类型）
> 收敛到 `graph_export.TYPE_PRIORITY` 的**受控词表 ∪ {Entity}**。
> **词表封闭性断言**必须用**归一化后**的 labels 判定（用原始 labels 会把「待改的」误报成「遗留的」）。
> delta 节点须带 `"props": {}`（防 `SET n += null` 报错）。

---

### 5.13 `11_真实数据/dimension_table.py` — ★物理量量纲真值表（**2026-10-10 新增**）

**背景**：`physicsbabel_ingest.py` 曾把「方程维度自洽（整式齐次）」误写为「两量量纲一致」（P0-1），
沉淀 1130 条脏边。PhysicsBabel 的 `exponents` 是**方程级齐次系数**（`∏Qᵢ^expᵢ = 无量纲`），
把它当作关于「各量量纲向量」的**线性方程组**、以 SI 基本量为边界条件，用**消元法**即可反解真量纲。

**接口**：`dim_of(name)` / `dim_equal(a,b)` / `is_homogeneous(keys,exponents)`（**三态**：True / False / **None=不可判定**）
/ `same_dimension_pairs(names)`；`ALIAS` 把其它命名空间的量名（`CM:pq:work`、`MX:phy:kinetic_energy`）对齐到规范名。
产物 `dimension_table.json`（59 个量，实测 `force=MLT⁻²` / `voltage=ML²T⁻³I⁻¹` / `permittivity=M⁻¹L⁻³T⁴I²` 全对）。

**用法**：`$PY 11_真实数据/dimension_table.py`（重建）。

### 5.14 `06_PoC/frozen_gate.py` — ★冻结反例集门禁（**2026-10-10 新增**，与 `connectivity_audit.py` **并列**）

**动机**：铁律 #14 —— 自检输入若与被检对象同源会一起静默通过。故反例集**独立于校验器实现**，
且**每条含正对照**（应当成立者必须成立），防门禁过严。

**组成**：`frozen_counterexamples.json`（**72 条**，覆盖 5 条 P0 + Phase 28~34 新增不变量）
+ `frozen_gate.py`（区分「主图结果层扫描」与「校验器层动态调用」；**门禁因缺依赖降级时记 SKIP 而非 PASS** —— 铁律 #13）。

**不变量分组（截至 Phase 34）**：a–h（Phase 27 P0 修复）/ i–p（Phase 28~31 分层可信性、桥证据、跨字段自洽）
/ **(q) `evidence_traceable`**（`is_strict` 边须有独立证据链，Phase 32）/ **(r) `evidence_wellformed`**
（全图证据对象良构、`kind ∈ EVIDENCE_KINDS`、`indep` 与 `kind` 配对）/ **(s) `no_repr_residue`**
（全图 props 无「Python repr 化容器」残留，Phase 32）/ **(t) `level_scope_reproducible`**
（全图落库 `level`/`scope`/`verifier` 必须与 `classify_all` 重算逐条一致，Phase 33 · 铁律 #25）
/ **(u) `residual_accounted`**（T4 残差「不可独立复算的理由」必须落在受控枚举内，**零未归类**，
Phase 34 · 铁律 #30/#34）/ `island_freeze`（已知孤岛冻结核对，Phase 30）。

**非真空自检**：`bash sttp.sh gatecheck` → `06_PoC/_gate_selfcheck_phase31.py`（Phase 31~34），
**不改门禁代码**、注入已知缺陷，断言 8 条不变量（n/o/p/q/r/s/t/u）**真的会红**（正对照 PASS + 注入 FAIL）。

**用法**：`$PY 06_PoC/frozen_gate.py [--json]`；退出码 0 = 全通过，1 = 有回归。

> ★ **Phase 33/34 独立审计器交叉确认**：`06_PoC/semantic_noise_audit.py` 新增 **R10/R11/R12** ——
> 用**自带实现**（不 import 判级模型）独立重算 T4 跨源侧别（**3991 条**，含 Phase 34 同义词通道）与
> T6 CODATA 定义式（18 条），并对 **T4 残差做清算**（**未归类 0**）；配**双向正对照**。
> **独立审计器必须允许它「报错」**（铁律 #43）。

---

## 六、数据源接入方式

### 6.1 数据源一览

| 数据源 | 类型 | 规模 | 接入脚本 | 已入 Aura |
|--------|------|------|----------|----------|
| MathXiv (手工策划) | 公式图谱 | 36 节点 / 52 边 | —（直接写入） | ✅ |
| 13 个种子切片 | 垂直领域骨架 | 13 个 seed JSON | `10_种子数据/build_*.py` | ✅ |
| ElementKG OWL | 真实元素 KG | 118 元素 / 2044 边 | `11_真实数据/elementkg_ingest.py` | ✅ |
| ElementKG 10M CSV | 真实化学 KG（子集） | 800 反应 / 713 分子 | `11_真实数据/elementkg10m_ingest.py` | ✅ |
| PhysicsBabel | 真实物理方程 | ~600 方程（~5000 待推） | `11_真实数据/physicsbabel_ingest.py` | ⚠️ 部分 |
| Wikidata | 跨源对齐 | 19 same_as 边 | `11_真实数据/wikidata_adapter.py` | ✅ |
| GNN 推断 | 链接预测 | 20 条候选边 | `09_科研扩展/9_inference/gnn_infer.py` | ✅ |
| Phase 9 深化 | 符号校验桥 | 188 条 | `09_科研扩展/9_inference/phase9_deepen.py` | ✅ |
| **OpenAlex**（Phase 8 首步） | 真实文献层 | 181 篇 Paper + 176 `cites` + 178 `discusses` | `11_真实数据/openalex_ingest.py` | ✅ |
| **NIST CODATA 2022**（Phase 8 二期） | 真实物理常量 | 355 条解析 → 常量 13→23（含 10 个跨学科桥） | `11_真实数据/codata_ingest.py` | ✅ |
| **PubChem 精确反查**（Phase 8 二期） | 真实分子属性 | `inchikey` 反查 713/713 命中 | `11_真实数据/pubchem_ingest.py` | ✅ |
| **Crossref / DataCite**（Phase 8 三期） | 文献多源校验 | 163/163 DOI 核验命中 + 39 条一致性 flag | `11_真实数据/literature_validate.py` | ✅ |
| **ChEMBL**（Phase 8 三期） | 真实药学维度 | 332/713 命中，8 个类药性描述符 → 2512 条 `has_quantity` | `11_真实数据/chembl_ingest.py` | ✅ |
| **Rhea + ChEBI**（**Phase 26**） | **真实化学·反应层（控物理失衡）** | **939 反应（带 EC 号）+ 1378 ChEBI 化合物** → `RH:rxn:*` / `CH:cpd:*`，`reactant_of` 2074 + `product_of` 2460 + `composed_of` 5092 + `same_as` 5 | **`11_真实数据/rhea_ingest.py`** | ✅ |

> **Phase 26 设计要点**：Rhea 方程以 `=` 明确分隔反应物/产物 → 复用既有 `reactant_of`/`product_of`，**零 schema 改动**；
> 额外按 **20 个核心代谢物**（水/ATP/NAD⁺/葡萄糖…）检索，保证化学子图锚定既有分子层；
> 内置**防孤岛守卫**（只留「至少一个参与物能锚定主图」的反应）与**部分结构**区分（含 R 占位符的分子式仍建 `composed_of`，标 `partial_structure`）。
> 实测踩坑：**Rhea 的 `chebi-id` 列按首次出现顺序去重**；**ChEBI 的 `obo_xref` 不含 PubChem CID**（改用人工策划名称桥）。

> **源可达性先探测**：换源前一律先跑 `python 11_真实数据/probe_sources.py`（TCP → TLS → 真实 HTTP 读响应体，多轮 × 双通道）。
> **白名单按「域 **+ 路径**」放行，TLS 握手成功 ≠ HTTP 可达，且不可跨路径外推** ——
> 例：`www.ebi.ac.uk/europepmc/*` 直连 3/3 通，而**同主机**的 `/chembl/*` 直连 0/3、需走代理 2/3。

### 6.2 新增数据源的标准流程

```
1. 编写适配器脚本（如 physicsbabel_ingest.py）
   → 输出 raw JSON（{nodes, edges}，节点带 id/labels/props，边带 id/source/target/type/kind/props）

2. 用 graph_export.build_graph_data() 转换为 viz 格式
   → 生成 graph_data_*.json（用于 viz）

3. 构造 Aura 增量 JSON
   → {nodes: [...], edges: [...], delete_nodes: [...], delete_edges: [...]}
   → 保存为 06_PoC/etl/neo4j/phase*_delta.json
   → ⚠️ 属性值只能是 primitive / 同质 primitive 数组；嵌套容器先过 `neo4j_props.sanitize_props`

4. 先在本地 apply 并验收（**改数据前必做**）
   → python 06_PoC/apply_delta.py --delta <p> --phase <N> [--apply]
   → 本地 normalized.json 是**权威基准**；对账/快照都以它为准

5. 推送到 Aura
   → bash sttp.sh push <delta.json>          # 单 delta
   → bash sttp.sh pushall --execute          # 全量重放（**十六步**，含对账 + 导出）

6. 对账（**推送后必做**）
   → bash sttp.sh reconcile --report-only    # 先看差异
   → bash sttp.sh reconcile                  # 清理到云端 == 本地

7. 反向导出 viz 快照
   → bash sttp.sh export                     # 默认 → 06_PoC/graph_data_aura.json（**勿覆盖 graph_data_phase<N>.json**）

8. 重启 viz
   → bash sttp.sh viz                         # 自动读 .env 的 GRAPH_DATA_FILE

9. 北极星自检（**新增/删除节点后必做**）
   → python 06_PoC/connectivity_audit.py --strict   # 分量应=1、孤立=0、悬空/自环=0（退码 0）
```

---

## 七、推理生成层工作流

### 7.1 三道符号门控

| 门控 | 触发条件 | 校验方法 | 结果 |
|------|---------|---------|------|
| **R-PHY** | 物理量量纲 | `pint` 库比较量纲 | dimensionally_consistent / has_unit / has_quantity |
| **R-CHEM** | 化学组成/反应角色 | `composed_of` 边（含 count）/ `equation` 解析 | composed_of / reactant_of / product_of / has_element |
| **R-MATH** | 数学推导 | `SymPy` 符号化简 | derived_from / proves |

> ⚠️ RDKit 不可用（numpy 2.x 不兼容）。化学校验改用 ground-truth 数据（composed_of 边 / equation 字符串解析）。

### 7.2 置信度规则

| 验证结果 | 置信度变化 |
|---------|-----------|
| VERIFIED | max(初始, 0.9) |
| REJECTED | × 0.2 |
| NEEDS_REVIEW | × 0.5 |

所有推断边必须带 `confidence` 和 `verification_gate` 属性，不可无标注推送。

### 7.3 GNN 推断流程（`gnn_infer.py`）

1. **正样本** = 已有跨域边（`same_period` / `composed_of` 等），约 132 条
2. **负样本** = 跨域非边，3000 条
3. **特征** = 节点属性 one-hot / 图结构度统计，约 29 维
4. **模型** = 2 层 GraphSAGE + dot-product 解码
5. **训练** = 桥接导向（防止 1.7% 跨域边被淹没导致退化嵌入）
6. **输出** = 跨域候选边 top 120，标注 `gnn_score`，kind=`llm_inferred_gnn`

### 7.4 LLM 语义假设流程（`llm_hypotheses.py`）

1. 取 120 条 `related_to` GNN 候选
2. 按规则重筛（分子 `formula` 是否精确出现在反应 `equation`）
3. 分三类：**UPGRADE**（可验证→ reactant_of/product_of）、**KEEP**（合理但不可证→ NEEDS_REVIEW）、**DROP**（GNN 伪影）
4. `llm_review_85.py` 用符号核验 + 领域白名单做第二轮判定

---

## 八、环境变量与凭据

### 8.1 必须配置的环境变量

```powershell
# Neo4j Aura 连接（Aura 连接必用）
$env:NEO4J_URI       = "neo4j+ssc://853a33bc.databases.neo4j.io"
$env:NEO4J_USER      = "853a33bc"
$env:NEO4J_PASSWORD  = "<从 MEMORY.md 或 .env 读取>"
$env:NEO4J_DATABASE  = "853a33bc"

# viz 服务（可选，也可直接 bash sttp.sh viz 自动读 .env）
$env:GRAPH_DATA_FILE = "06_PoC\graph_data_phase22.json"
$env:VIZ_PORT        = "8765"
$env:STTP_PYTHON     = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
```

> ⚠️ 密码必须从 MEMORY.md 或 `.env` 文件读取，**禁止硬编码**写入脚本。`08_部署包/neo4j/.env.example` 是模板，`08_部署包/neo4j/.env` 应含真实凭据（已从开源仓库移除）。

### 8.2 .env 文件位置

- 本地私用：`C:\Users\Administrator\WorkBuddy\2026-10-08-10-51-20\STTP\.env`（已 gitignore）
- 开源模板：`C:\Users\Administrator\WorkBuddy\2026-10-08-10-51-20\STTP\08_部署包\neo4j\.env.example`

---

## 九、已知问题与踩坑清单

### 9.1 图数据操作

- **viz 启动即退出**：后端依赖 `06_PoC/etl/normalized.json`，缺失时 `queries.get_backend` 内部 `sys.exit()` 杀进程。修复：`export_normalized.py` 从 Aura 重建。
- **单大事务整体回滚**：Aura 免费实例连接池有限，单次 >5000 边的 UNWIND 事务在 flaky 连接下会静默回滚（边未写入，节点正常）。必须用 `robust_aura_loader.py` 分小批（默认 2000/批）。
- **Aura 连接池耗尽**：多个进程同时连 Aura 时会出现 `ConnectionAcquisitionTimeoutError`。操作前关掉竞争进程，操作完成后导出快照。
- **Aura 唯一约束为 Entity.id**：同一 id 的节点重复写入会触发 `IndexEntryConflictException`。增量 JSON 中的节点若已存在于 Aura，须从 nodes 列表中排除（边仍按 id 引用，MATCH 到已有节点）。
- **apoc.merge.relationship 按 (起点, 终点, 类型, kind) 去重**：同一对节点在同一类型下重复推只会保留一条，属正确行为。
  ⚠️ 反之亦然：**同三元组但 `kind` 不同会各存一条边**（identProps 带 `kind`）。判断「边是否重复」时不能只看 `(source,type,target)`。
- **🔴 `apoc.merge.relationship` 的第 4 个参数是 `onCreateProps`，对「已存在的边」不写任何属性**（2026-10-10 事故）：
  `apoc.merge.relationship(a, type, identProps, props, b)` 5 参形式中，`props` **只在新建边时生效**；
  MERGE 命中**已有**边时它**什么也不做**。于是「**只更新边属性**」的 delta 会**静默零效果** ——
  日志照样报「边 MERGE 完成 48734/48734」、边计数不变、`reconcile_aura_edges` 也 **0 差异**
  （它只比 `(source,type,kind)` 三元组，**从不看属性**）→ 三重"假通过"。
  第 18 轮实测：Phase 28 的 48734 条边属性**一条都没落库**，Aura 上 `verification_level` 属性**根本不存在**，而脚本 `exit=0`。
  **修复（全仓 9 处已改）**：紧接 MERGE 后补 **`SET rel += row.props`**（与 APOC 版本无关，create/match 都生效）；
  并在 `push_element_merge.py` 增加 **[5/5] 边属性落地抽检**（抽样 300 条逐键比对，不符则 **exit 3**）。
  **教训**：`reconcile` 必须补一维「**属性**对账」的口径（或至少在推送器内置抽检），否则「计数相等 ≠ 内容相等」（铁律 #12）。
- **🔴 嵌套属性会让整批事务失败，且旧推送器会静默吞掉**（2026-10-09 事故）：Neo4j 属性值只接受 primitive / 同质 primitive 数组。
  delta 里若夹带 `dict` / `list[dict]`（本地 NetworkX 不校验），推送时**整批失败**，重试 6 次后放弃该批却仍 `exit 0`。
  实际损失：`phase13` 的 `gnn_type_probs`(list[dict]) 丢 **600 条边**、`phase15/16` 的 `alias_sources`(dict) 让 **118 个元素的 period/group 全未写入**。
  修复：新增 `06_PoC/neo4j_props.py`，在 DB 边界 `sanitize_props()`（嵌套容器 → `json.dumps`，**无损**，本地图谱不动）；
  并让「放弃批次」**返回非零退出码**（编排器会中止后续步骤）。**新写推送器必须接入。**
- **🔴 推送只能加，不会减 —— 必须对账**：历史上被替换掉的旧方案残留（Phase 11 的 `kind=reaction_role` 等）会一直留在云端，
  云端逐渐变成「本地 ∪ 历代废弃物」。用 `06_PoC/reconcile_aura_edges.py`（`bash sttp.sh reconcile`）以本地为基准清理。
- **🔴 云端与本地「计数相等」≠「内容相等」**：开工时云端 7127/33090 与本地旧 phase12 数字一致，
  但云端已含 155 条本地没有的残留边。**对账必须做集合级比对（三元组集合），不能只看 count。**
- **🔴 反向导出不要覆盖 `graph_data_phase<N>.json`**：`export_aura.py` 曾硬编码输出到 `graph_data_phase12.json`，
  而推送编排每次跑都调用它 → 历史快照被反复重写、不可回溯。现默认输出 `graph_data_aura.json`，
  并内置守卫拒绝写入 phase 快照命名（除非 `--force`）。
- **`getaddrinfo failed` 不等于「Aura 实例暂停」**（曾据此误报 7 轮）：先用公共解析器（UDP/53 直查 `8.8.8.8`/`1.1.1.1`）交叉验证。
  仅本机失败 → **DNS 陈旧负缓存**，`ipconfig /flushdns` 即可；仅瞬时失败 → 直接重试；公共解析器也 NXDOMAIN → 才是实例暂停（需 console.neo4j.io resume）。
- **Cypher 25 兼容**：`MATCH (a)-[:*1..6]->(b)` 通配符变长路径不支持，须改用显式 reltype 列表或定长 `[:REL*2]`。`MATCH (a)-[r]->(b) RETURN a.id + b.id` 字符串拼接用 `||` 而非 `+`。
- **🔴 删除可能是「静默空操作」，而且自检会与被检对象一起静默通过**（2026-10-09 发现）：
  `delete_nodes` 若写成 `[{"id": "X"}]`，推送器二次取 `x["id"]` 会得到 `{"id":"X"}`（一个 dict），
  拼进 Cypher 后 `MATCH` **落空但不报错** —— 结果「**Δ+0 且退码 0**」。
  更危险的是：**残留自检用的是同一份错误的 id**，于是自检也「通过」了，故障完全隐形。
  修复两侧：`push_element_merge.py` 加 `norm_del_ids()`（兼容 str / dict）**并新增「实删数断言」**（残留 >0 退码 3）；
  `sync_seed_delta.py` 的 `delete_nodes` 改为输出**纯 id 字符串**。
  **教训：自检的输入不能与被检对象是同一份来源，否则错误会「一起通过」。**
- **🔴 `apoc.create.addLabels` 只增不减 → 云端标签会与本地分叉**：
  用它做标签「规范化」时，旧标签会永远留在云端，本地却已替换 → 集合级对账仍会报差异。
  推送器 `push_element_merge.py` 已新增 `--set-labels`（走 `apoc.create.setLabels`，**整体替换**），标签收敛轮必须带此开关。
- **🔴 跨源「看似重复」的多节点先查边，不要合并**：分子层有 8 套命名空间（`MO:`/`PC:mol:`/`IC:mo:`/`BC:mo:`…）
  看似重复，实则被 `same_as` 显式关联且带 provenance，属**有意的跨源对齐设计**。盲目合并会毁掉跨源网络。
  已知副作用：同一实体多节点会**稀释 GNN 嵌入**；若将来要缓解，应**在 GNN 特征中注入 `same_as` 邻接**或做**连通分量级嵌入池化**，而非删节点。

### 9.2 数据生成

- **🔴 种子不是权威，权威在图谱**（2026-10-09 实测）：`10_种子数据/` 是**手工策划的骨架**，其数值可能与真实源冲突。
  例：`TH:pq:gas_const` 种子里是 `8.314`，图谱（CODATA 2022）是 `8.314462618`；`EM:pq:permittivity`/`permeability` 种子里是 2018 旧值。
  **回灌种子差量时必须有权威性守卫**（`sync_seed_delta.py` 的 `EXTERNAL_AUTHORITY`），否则会把真实源数据降级成草稿值。
- **🔴 改数据前先改源头**：只修图谱不改适配器/生成器，重跑 ETL 会**重新长出同一个错误**。
  本轮 82 处学科 domain 错标、`EL:*` 悬空引用、42 个孤儿反应，全部是「改源头 + 改图谱」成对完成的。
- **外部字段语义先验证再用**：已 5 次踩中（ElementKG 的 `HASATOMIC` 实为原子序数 / PhysicsBabel / OpenAlex 年份 / ChEMBL / Crossref 引用计数口径）。
  **引用计数不可跨源比对或覆盖**（Crossref 口径远窄于 OpenAlex，中位比值 0.62）；**反向异常（子集 > 全集）才是真异常**。
- **新增切片 OUT 必须是 `.json`**：构建脚本 `OUT` 变量若误设为 `.py` 会覆盖自身源码且不入扫描管线。
- **same_as 桥接必须列白名单**：新切片 same_as 指向基础库已有节点时，悬空断言会误报。必须在 builder 中显式声明已知 id 列表。
- **权威转换器**：所有用于 viz 和 Neo4j 的 JSON 必须经 `graph_export.build_graph_data()` 生成，禁止手工 merge（会破坏 type 字段和 domain 着色）。
- **ElementKG 边 `source` 重复键**：`elementkg_adapter.py` 曾因边 dict 内 `source` 键被两次赋值（正确 id → 源名字面量）导致目标节点塌缩。修复：第二个键改名 `data_source`。

### 9.3 rdflib 解析

- **判 Literal 用 `isinstance(o, Literal)`**：勿用 `__class__.__name__`（URIRef 会被误判为字面量，导致对象属性边全部丢失）。
- **无向关系跳自环**：用 `frozenset([a, b])` 构造无向边 key 前须跳过 `a == b` 的自环，否则 unpack 报错。

### 9.4 PowerShell / 控制台

- **GBK 控制台编码**：`−`（U+2212）、下标字符（₂）打印会报 `UnicodeEncodeError: 'gbk' codec can't encode character`。数据本身正确，输出重定向到 UTF-8 文件可规避。脚本文件首行加 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`。
- **PowerShell 内联 `-c` 多语句**：含 `|`、`>`、多行字符串时会被解析为 ScriptBlock 而非命令串。改用临时脚本文件执行。
- **PowerShell Select-String 中文路径**：返回 0 是误报，扫描敏感内容应改用 Python。
- **git 中文路径**：默认对非 ASCII 路径做八进制转义显示。用 `git -c core.quotepath=false -z` 取得真实路径。

### 9.5 GitHub 推送

- **HTTPS 大包 408 超时**：本机 HTTPS 推大包（>1 万行）持续报 `HTTP 408`。解决：拆成每批 5 个文件的小提交，提交间 sleep 15s，重试 3 次。长期方案：SSH（`git@github.com:zhing99-hash/STTP.git`）。
- **细粒度 PAT**：`github_pat_` 前缀的细粒度 PAT 需在 GitHub 令牌设置中显式勾选目标仓库并授予 Contents: Read and write；经典 PAT 勾 `repo` 即可。
- **推送失败显示 "Everything up-to-date"**：这是误导输出，不代表成功；必须检查远端 commit SHA 是否推进。

### 9.6 前端可视化

- **默认 `cose` 力导布局卡死**：超过 5000 条边时，同步模拟卡死主线程导致页面"加载超时"。已修复：默认布局改为 `concentric`（瞬时）；大图（>5000 边）时力导入口加护栏；新增 `fcose` 快速力导（CDN 可用）。
- **CDN 离线降级**：本机无网时 Cytoscape.js / MathJax CDN 不可达，页面自动降级为纯文本。
- **新节点类型须同步配色**：`TYPE_PRIORITY`（`graph_export.py`）和 `TYPE_STYLE`（`graph_view.html`）须同时修改；新边类型须同时加 `EDGE_TYPE_COLOR` 和图例段。
- **🔴 长驻服务读的是「启动那一刻」的图 —— 文件对了不等于接口对了**（2026-10-09 收尾复核发现）：
  `viz_server.py` 在启动时把 `normalized.json` 一次性载入内存。本轮把标签规范化写回文件后，
  `/api/stats` 仍在返回**旧标签**（仍能数出 `ElementEntity 1` / `PhysicsEntity 1` / `Physical_quantity 7` 三个表外桶），
  而**节点数/边数与新数据完全相同** —— 这种「**计数相同、内容不同**」最容易骗过「按计数验收」的习惯。
  **规则：① 数据一改就重启所有消费它的长驻服务；② 验收要打到接口层（`curl /api/stats`），不能只看文件。**

---

## 十、下一步建议

> 按紧迫程度排序，WorkBuddy 可直接按序号推进。

### 🔴 高优先级（核心能力补全）

**1. Phase 8 收尾：ElementKG 2.0 真实 dump + Vashy / ReactionAtlas**
- 状态：Phase 8 三期满负荷落地（文献/常量/分子/药物四层），剩余候选为 **ElementKG 2.0 官方 dump**、
  **Vashy**（物理方程侧）、**ReactionAtlas**（反应侧）
- ⚠️ **首要约束已从「产跨域桥」转为「去伪存真 + 新维度复算」**。物理占比已由 Phase 26（Rhea+ChEBI 化学侧）从 **71.5% 压到 54.3%**；
  但第 19~20 轮连续侦察证明：**跨域桥的瓶颈不是「缺桥」而是「图上有可被确定性反驳的假桥」** ——
  Phase 30 一次撤回 **81 条**假语义边（含 **5 条策划数据本身的错误**），并把数学桥从 **3.4%** 拉到 **72.7%**；
  Phase 31 再撤 **48 条**（含**第 6 条策划错误** `电场→V`，应为 **V/m**）+ **升级 263 条**有证据的低档边
  → **北极星 99.6%**、**T3 56.4%→80.9%**、**T6 76.1%→95.7%**。
  **「跨域桥」已不再是第一短板**；接新源前须先回答「这个源的每个维度是否已被复算检验过」。
  ⚠️ **本轮最重要的结构性结论**：**「被信任」不等于「被验证」** —— 人工策划 / 来源断言的边
  **同样是「从未被检验过的断言」**（策划 `has_symbol` 199 条、`derived_from` 127 条此前从未被复算）。

**1b. ✅ Claim / Evidence 完整对象化（Phase 32 · 第 22 轮，已完成）**
- `{claim, evidence[], level, verifier, evidence_at}` 已成**一等对象**，**全图覆盖 100%**（48712 条边 / 49635 条证据）。
- **核心洞察**：「`verified` 标签」≠「证据」—— `verification_level/scope/verifier` 只是「谁验的 / 怎么验的」的名字，
  **证据本身（判据轨迹）从未落库**，这正是北极星「且证据可追溯」长期**无仪器**的根因。
- **同源设计（铁律 #37）**：`kind`/`indep` 由 `scope` 唯一决定（`SCOPE_KIND`），`detail` 由判定现场经
  `R(..., detail=...)` 给出，`build_evidence()` **只搬用不重推** → 证据语义与档位构造上不可能分歧。
- 新增 **T9 证据可追溯（100.0%）** 与 **T9-i 证据独立（85.9%）** 两个**分列**口径；
  **T9=100% 绝不能掩盖 T9-i=85.9%**（下一轮靶子）。
- 门禁 **67 → 70**（+q/r/s）；`repr` 串生成链**已源头根治**（`_csv_encode`）。
- 相关工具：`11_真实数据/phase32_evidence_delta.py`；`sttp.sh evidence`。
- **下一轮建议**：**T9-i 证据独立攻坚**（对可复算子集补独立复算）或 `source_asserted` 攻坚（13795 条 / 28.3%）。
  → **已由 Phase 33 消化，见 1d**。

**1c. ★ 门禁「非真空」自检（Phase 31 新增，已固化）**
- `bash sttp.sh gatecheck` → `06_PoC/_gate_selfcheck_phase31.py`：**不改门禁代码**，
  直接 `run_case(case, mutated_graph)` 注入已知假边，断言不变量**真的会 FAIL**（正对照 PASS + 注入缺陷 FAIL）。
- **一条永远不会红的断言不是断言**；`PASS` 只说明「当前没红」，不说明「有能力红」。
  同理：`scan.get()` 遇**空字典**（假值）会让断言**静默退化为 SKIP** —— **SKIP 比 FAIL 更危险**（FAIL 有人查，SKIP 无人看）。
- **Phase 34 扩到 8 条不变量（n/o/p/q/r/s/t/u）**，**Phase 35 再扩到 10 条（n…w）**，
  全部经注入式自检证明**可红**。

**1d. ✅ T9-i 证据独立攻坚（Phase 33 · 第 23 轮，已完成）**
- **T9-i 由 85.9% → 92.8%**（37564/40484）；升档 **+2791**（T4 跨源 2773 + T6 复算 18）、**降级 0**；规模 **9657/48712 零变化**。
- **核心洞察**：T4「反应物/生成物归属」的朴素独立化「**重解析方程**」是**同源自证**
  （适配器正是由 `equation` 推 `n_left` 再切 `chebi-id`，`rhea_ingest.py` 第 355~378 行，铁律 #14 新实例）。
  **真·跨源**路径 = **ChEBI 本体 label/formula**（另一个数据库）校验 **Rhea 方程**侧别 → 判据分层：
  label **1784** / 仅 formula **989** / `both` 歧义 **22（不采信）** / **`wrong` = 0**。
- 新增 **A12** `reaction_side_cross_check`（`scope=equation_species_cross_source`，`cross_source`）与
  **A13** `codata_derivation_recheck`（`scope=codata_definition_recompute`，`rule_checked`）；**同源**（`SCOPE_KIND` +4）。
- 门禁 **70 → 71**（+**(t) `level_scope_reproducible`**，铁律 #25：落库档位必须能被重算重现）；
  独立审计器 +**R10/R11**（自带实现独立重算 + 双向正对照，铁律 #43）。
- 相关工具：`11_真实数据/phase33_indep_delta.py`；`sttp.sh indep`。

**1e. ✅ T4 残差「跨源名称对齐」（Phase 34 · 第 24 轮，已完成）**
- **T9-i 由 92.8% → 95.8%**（38782/40484）；升档 **+1218**、**降级 0**；规模 **9657/48712 零变化**。
- **核心洞察**：T4 残差根因是**命名**而非**缺源** —— Rhea 方程写 `pentanoate`，ChEBI 主名是 `valerate`。
  **真·第二「反应」源不可批量获取**（KEGG `link/rhea/*` 全空、FTP 不可达；Reactome 映射 404）；
  可达且真实的第二源 = **ChEBI 本体同义词（OLS4）**（Rhea 方程串 × ChEBI 本体，两个不同数据库）。
- **判据护栏**：同义词**归一后长度 ≥3**（挡掉单字母 `H` 误命中 `H(+)` 的假阳，铁律 #31 正对照）；
  **同义词只用于升档、不用于反驳**（宁缺勿滥）；label → formula → synonym **优先级**保证 Phase 33 的 2773 条**零降级**。
- 新增 **A14**（`reaction_side_cross_check` 增 synonym 通道）+ `chebi_alias_names()` + `_SYN_MIN_LEN`；
  数据获取 `11_真实数据/chebi_synonym_fetch.py`（**772/772 成功 / 3557 条同义词**）。
- 门禁 **71 → 72**（+**(u) `residual_accounted`**：T4 残差**零未归类**，铁律 #30/#34）；
  独立审计器 **R10 升级**（同义词通道，长度护栏**独立取 2**）+ **R12 残差清算** + **同义词正对照**。
- **新纪律**：#47 抽样不可替代全量预演（50 抽样估 19% vs 全量 69%，差 4 倍）；#48 先证「第二源可达且独立」；
  #49 命名对齐是一种合法的「跨源」。
- 相关工具：`11_真实数据/{phase34_alias_delta,chebi_synonym_fetch}.py`；`sttp.sh {alias,synfetch}`。
- **下一轮建议**：**SABIO-RK / MetaCyc 批量端点可达性侦察**（若可行则攻 T4 余 1321，⚠ 先侦察再投入）
  或 **T8 第二文献索引**（354 条）。
  → **已由 Phase 35 消化（侦察全部负结果 → 改轨），见 1f**。

**1f. ✅ 北极星口径自审 与 T9-i 天花板量化（Phase 35 · 第 25 轮，已完成 · 纯仪器轮）**
- **零数据变更**（规模 **9657/48712** 不变）；本轮的交付是**度量本身的诚实性**。
- **起点 → 改轨**：按 **#34/#48** 先做**只读穷举侦察**（`_recon_phase35_reachability.py`，20 探测点）
  → **六条第二「反应」源 + 四条第二「文献」索引全部负结果** → 改轨。
  - KEGG `link/rhea/ec` **400**；Reactome Rhea 映射 **404**；SABIO-RK 老 REST **已下线**（返回 SPA 外壳）；
    BioCyc/MetaCyc 返回首页；zbMATH/DBLP/OpenAIRE/DataCite **不可达**；Semantic Scholar 覆盖 **1/14**；
    Crossref **同源**（`cites` 取自 OpenAlex，Crossref 是其上游，#45）；Wikidata **严格零命中**。
  - **★ `HTTP 200` ≠ 数据可用**：SABIO-RK / zbMATH / BioCyc 三者状态码都是 200 而数据不可用 ⇒ **侦察必须看响应体**。
  - **★ 数字订正（#44）**：MNX 全库 Rhea 反应 **456 → 实测 1836**；但**结论不变** ——
    覆盖本图 Rhea **45/939 = 4.8%**、覆盖 T4 残差 **5/269 = 1.9%**（**「有第二个库」≠「有第二个源」**）。
- **★ 核心发现①（新铁律 #50）**：**北极星的分母是「选择」的结果** ——
  headline **99.6%** 的分母（并集 40590 / 逐族求和 **40628**，差 **38** = 38 条 T7∩学科族双边）
  **排除了 8122 条边（16.7%）**，其中 **99.6% 是弱证据**；同口径扩到全图（+`has_quantity`/
  `has_functionalgroup`/`reagent_of`/`has_element`，门槛 `rule_checked`）→ **83.5%**。
  → **报北极星必须同时披露**：① 分母构造 ② 分母外占比 ③ **反向对照**。
- **★ 核心发现②**：**T9-i 天花板可精确清算**（1702 条**零未归类**）——按类型 T4 **1284** / T8 **354** / T6 **64**；
  按理由 `same_source_elementkg` 707 / `placeholder_complex` 246 / `naming_variant_no_second_source` 220 /
  `no_second_subject_index` 178 / `no_independent_citation_index` 176 / … → **不是「工作量」而是「天花板」**。
- **★ 核心发现③（从未被测量过的维度）**：ElementKG2.0 **匿名占位实体 836 个 / 1547 节点**，
  **牵连 4979 条边（全图 10.2%）语义不可读**，其中 **分母内 713 条**（达标却无法表述结论）。
  判据**必须叠加 `EK2:` 命名空间约束**（否则把 `WD:` 真实体未存名误判为占位）。
- **仪器化**：`verification_model.py` 新增 **T1–T8 任务族唯一权威表**（`TASK_FAMILIES`/`tasks_of`/`tasks_passed`，
  铁律 #36 同源）+ `INDEP_REASONS`/`SCOPE_REASONS` + `indep_reason()`/`scope_reason()`/`is_placeholder_entity()`；
  门禁 **72 → 74**（+**(v) `independence_accounted`** / +**(w) `scope_accounted`**）；
  非真空自检 **8 → 10 条不变量**；独立审计器 +**R13/R14**（首跑报 **47** → 修实现 → **0**，铁律 #43 真捕获）；
  北极星仪器新增**「口径披露」块**。
- **新纪律**：#50（分母是选择的结果，见上）+ 三条补强：**可达性必须看响应体**、
  **「有第二个库」≠「有第二个源」**、**仪器必须用 `$STTP_PYTHON`**（否则门禁静默降级为 SKIP）。
- 相关工具：`06_PoC/_recon_phase35_{scope,reachability,mnx_recov}.py`；`sttp.sh {scopeaudit,mnxrecov,reach35}`。
- **下一轮建议**：**占位实体「补名」可行性侦察** → 攻 4979 条中的分母内 **713** 条
  （唯一能同时改善北极星**可表述性**与 T7/T9 的方向）。
  → **已由 Phase 36 改道消化（GPT6 复评：语义校正优先于补名），见 1g**。

**1g. ✅ 验证语义校正（Phase 36 · 第 26 轮，已完成 · GPT6 复评 R01–R05/R07/R08）**
- **输入**：GPT6 复评（`07_交付物/外部评审/STTP_复评与Apache2.0许可意见_20261011.md`）。
  三项可当场核实的指摘**全部属实**（Cypher 头 `#` 回归、P35 报告文字冲突、gate 退出码只看 FAIL）
  → 采信其合成输入类指摘，用户确认「按评估顺序 + R03 如实下调」。
- **包 1 快修**：4 个 `.cypher` `#`→`//` + `apply_license_headers.py` 分派修正（幂等 162 全跳过）；
  `frozen_gate --release` **fail-closed**（缺主图/任何 SKIP → rc=2，实测缺依赖环境 rc=2）；
  许可包补全（`vendor/licenses/` 5 份版本匹配 LICENSE、`DATA_LICENSES.md`、NOTICE/README 迁移与范围声明）。
- **R01 化学格式契约**：`smiles`→`useSmiles=True`（可 VERIFIED）；`smarts`（查询模式）氢不可判定
  → **绝不 VERIFIED**；复评两个配平正例 + 显式氢写法**同判**。
- **R02 判据下沉**：`vm.is_molar_mass_quantity`（能量类词一律排除；裸 mass/weight 不算），
  gnn_infer Part A/B **同源**；B5 裸词兜底删除。
- **R03（最重）共享词典重定**：Rhea 参与物由 ChEBI 描述 ⇒ 侧别匹配 = **数据一致性检查**（indep=False），
  3991 条升级记录重定（**降级 4032 / 升级 0**）；formula 通道收窄为仅候选（同分异构不排除）。
  **T9-i 95.8% → 85.9% 如实下调**（34780/40473；非独立 5693 零未归类，最大类 shared_dictionary 3002）。
- **R04 算子桥正名**：11 条 `derived_from → uses_operator`（残留回查 0）；A9 只服务 uses_operator；
  门禁 (l) 首获真实主体（13 桥全 rule_checked + 算子证据）。
- **R05 直通移除**：pubchem 键在库重算 / 缺键降 source_asserted「沿用」（carryover）；
  webbook n_references 无 references 明细不升 cross_source。
- **纪律**：carryover 初定 rule_checked → 3 处「落库≠重算」门禁立刻 FAIL → **改模型 + 重跑 delta**
  （不放宽门禁；fail-closed 首次实战）。
- **数字**：规模 9657/48712（净零）；北极星 **99.6%**（40473/40617）；T9 100.0%；
  门禁 **74 → 90**（+R01×6 +R02×8 +R04/R05×2）；审计器候选反驳 0；自检 10 条全可红；快照 phase36。
- **新铁律**：**#51**「不同数据库」≠「独立证据」（判独立先查血缘）；
  **#52** 验证的命题必须与被发布的命题一致（汇总计数 ≠ 逐条重算；沿用须明示降档）。
- 相关工具：`11_真实数据/phase36_rescope_delta.py`（`sttp.sh rescope`，默认 dry-run）；
  报告 `07_交付物/Phase36_验证语义校正报告_20261012.md`。
- **下一轮建议**：**R06 可复现发布包**（离线小样例 + 快照 manifest + 依赖锁 + `--release` 接入）
  → **R11 科学任务评测集**（30–50 题热力学-化学平衡-数学专题，拒答率/置信区间/测试集冻结）。

**2. 缓解分子层多节点对 GNN 嵌入的稀释**
- 现象：8 套分子命名空间（有意的跨源对齐）会让同一实体拆成多个节点，稀释嵌入质量
- 方案：**不删节点**，改为在 GNN 特征中**显式注入 `same_as` 邻接**，或做**连通分量级嵌入池化**

### 🟡 中优先级（数据质量与完整性）

**3. Phase 9 Wikidata 真实对齐（打通「公式 ↔ 世界」关键桥）**
- 状态：`query.wikidata.org` / `en.wikipedia.org` **全域 TLS TIMEOUT**（多轮探测确认被出口白名单拦截）
- 过渡方案（部分已落地）：用 **OpenAlex / Crossref / DataCite / PubChem** 的等价对齐能力替代
- 注意：若哪天真能通，SPARQL 查询间隔须 ≥3s

**4. 文献层 2 条待人工裁定**（Phase 8 三期已标 flag、未改数）
- `PA:oa:W2005936791` 年份（OpenAlex 2012 vs Crossref 2005，疑似 OpenAlex 错）
- `PA:oa:W2322753244` 引用计数**反向异常**（Crossref 1342 > OpenAlex 711，而 Crossref 本应是子集）

**5. 常量 / 单位 / 枚举收尾**
- `EM:pq:coulomb_const` 的 `unit` 补全（CODATA 无直接条目，由 `k = 1/(4πε₀)` 派生）
- `PB:pq:grav_const` / `PB:pq:planck` 的**合并**（现仅 `same_as` 对齐）—— 牵动 3000+ 条边重组
- `chembl_availability_type` 枚举语义补全（现仅存原值 int）
- 元素性质单位从 IUPAC 正式表核校（现为量级+常识推断，已标 `unit_inferred`）

### 🟢 低优先级（功能增强）

**6. PhysicsBabel 5000 方程全量补推**（**注意：物理侧，与风险 1 冲突，建议缓做**）
- 状态：已解析 `physicsbabel_raw.json`（5050 节点 / 29104 边），仅部分推入 Aura
- 操作：`python 06_PoC/robust_aura_loader.py --input <delta.json> --batch 1000`

**7. `same_series` 边类型（f 区语义严格化）**
- 现状：`same_family` 在 f 区其实表达的是「同系列」

**8. 本地 Neo4j 部署**
- 用户本机 Docker 因 CPU 虚拟化未开启不可用；若未来换机器，`08_部署包/neo4j/` 可一键部署
- `docker-compose.yml` + `load.cypher` + `deploy.py` 已就绪

---

## 十一、快速上手清单（WorkBuddy 第一天）

```powershell
# 1. 克隆仓库
git clone https://github.com/zhing99-hash/STTP.git
cd STTP

# 2. 读取凭据（从 MEMORY.md 或向用户询问）
#    必须设置以下环境变量（以 PowerShell 为例）：
$env:NEO4J_URI       = "neo4j+ssc://853a33bc.databases.neo4j.io"
$env:NEO4J_USER      = "853a33bc"
$env:NEO4J_PASSWORD  = "<用户提供的密码>"
$env:NEO4J_DATABASE  = "853a33bc"

# 3. 验证 Aura 连接
python -c "from neo4j import GraphDatabase; d=GraphDatabase.driver('$env:NEO4J_URI',auth=('$env:NEO4J_USER','$env:NEO4J_PASSWORD')); print(list(d.session().run('RETURN 1 as x'))); d.close()"

# 4. 启动本地可视化
bash sttp.sh viz
# 浏览器打开 http://127.0.0.1:8765/   （数据源 = .env 的 GRAPH_DATA_FILE = graph_data_phase34.json）

# 5. 从 Aura 反向导出 viz 快照（默认 graph_data_aura.json）
bash sttp.sh export

# 6. 查看图谱统计 / 北极星自检
bash sttp.sh stats
python 06_PoC/connectivity_audit.py --strict   # 分量=6 / 5 岛 / 29 节点 → FROZEN-OK 退码 0（已知岛已冻结）
python 06_PoC/frozen_gate.py                   # 冻结反例集门禁 → PASS 74 / FAIL 0 / SKIP 0
python 06_PoC/task_trust_audit.py              # 北极星：正确且可追溯 99.6%；T9 可追溯 100% / T9-i 独立 95.8%
python 06_PoC/semantic_noise_audit.py          # 独立实现的审计器 → 候选反驳 0（R13/R14 交叉核对）
bash sttp.sh scopeaudit                        # 北极星口径自审：分母外 16.7%；反向对照 83.5%；占位实体 4979 边
bash sttp.sh gatecheck                         # 门禁非真空自检（10 条不变量全部可红）

# 7. 推新数据到 Aura（推荐用编排器，自动消毒 + 对账 + 导出）
bash sttp.sh push <your_delta.json>       # 单 delta
bash sttp.sh pushall --execute            # **十六步**全量重放

# 8. 验证写入
python 08_部署包/neo4j/verify_deploy.py

# 9. 拉取最新 GitHub 更改
git fetch origin
git merge origin/main
```

---

## 十二、关键文件速查表

| 场景 | 文件 |
|------|------|
| 修改 viz 图谱配色 | `06_PoC/js/style.js`（`SUBJECT_COLOR`/`NODE_TYPE_STYLE`/`EDGE_TYPE_COLOR`）★ Phase 43 起已从 html 迁出 |
| 新增节点类型 | `06_PoC/graph_export.py`（`TYPE_PRIORITY`）+ `06_PoC/js/style.js`（`NODE_TYPE_STYLE`） |
| 新增边类型 | `06_PoC/js/style.js`（`EDGE_TYPE_COLOR`，★ 必须与规范图实测 `type` 集合逐值对齐，自检 `X1` 会判红） |
| **前端五态/参数/会话逻辑** | `06_PoC/js/core.js`（零 DOM 纯逻辑；`bash sttp.sh frontend` + `node 06_PoC/tests/frontend_core_check.mjs` 双向自检） |
| 新增种子切片 | `10_种子数据/build_*.py` → `10_种子数据/seed_*.json`，再走 `06_PoC/sync_seed_delta.py` 回灌差量 |
| 接入新数据源 | `11_真实数据/<source>_ingest.py`，用 `graph_export.build_graph_data()` 转换 |
| **量北极星连通性** | `06_PoC/connectivity_audit.py`（`--strict` 退码 2） |
| **量北极星口径（分母审）** | `06_PoC/_recon_phase35_scope.py`（`bash sttp.sh scopeaudit`） |
| **独立审计器（跨实现）** | `06_PoC/semantic_noise_audit.py`（`bash sttp.sh noise`） |
| **本地应用 delta** | `06_PoC/apply_delta.py --delta <p> --phase <N> [--apply]` |
| **seed → 权威图回灌** | `06_PoC/sync_seed_delta.py` |
| **标签受控收敛** | `03_知识层/normalize_labels.py` |
| 从 Aura 拉全量图 | `09_科研扩展/9_inference/export_aura.py` |
| 健壮推 Aura | `06_PoC/robust_aura_loader.py --input <delta.json>` |
| 推送编排（16 步） | `09_科研扩展/9_inference/push_pending.py`（`bash sttp.sh pushall`） |
| 修复 viz 后端源 | `06_PoC/export_normalized.py` |
| 推理层新桥 | `09_科研扩展/9_inference/phase9_deepen.py` 或 `gnn_infer.py` |
| 更新看板 | `00_项目管理/团队分工与进度.md` |

---

*本文件由 QClaw 生成于 2026-10-08，建议在每次 Phase 完成后更新「当前运行状态」和「已知问题」两节。*
*最近更新：2026-10-14（**Phase 45 · 第 37 轮：A04 验证依赖绑定 —— 从「来源绑定」升级到「验证依赖绑定」**。
- 两条指摘坐实并修复：**A04a**（`impl_ver` 全库**单值** `100148c147cb`（49572/49572），且旧失效器**从不比对**它 ⇒
  新增 `verifier{verifier_id, code_hash, dependency_versions}`，失效器**同时比对** `vch` + `dvs`）；
  **A04b**（旧失效器变异 `dimension_table` 只能定位 **28** 条边 ⇒ 依赖感知失效 **33238**，自检 ⑥ 期望/命中差 **0**）。
- ★ **两层判据并列、不可互相替代**：`edge.fidelity`（Phase 44，**边级字节**）与 `edge.evidence_status`
  （Phase 45，**证据级依赖**：`valid/stale/needs_review/rejected`）；`exclude_stale` 的**唯一**判据是
  `queries.edge_is_stale()`（两层并集），**无 A04 记录时完全回落到字节层** ⇒ 发布样例 / 裸 clone 中期图行为与 Phase 44 **逐位一致**。
- 落盘实测（边级）：`valid 1038 ｜ stale 0 ｜ needs_review 34145 ｜ rejected 0 ｜ no_record 13529`（Σ = 48 712）；
  `needs_review` **不**计入 `exclude_stale`（不可判定 ≠ 已变更，铁律 #70）但**始终披露**；
  `no_record 13529` 是信息性的（该证据本就不依赖参考表，原因码 `no_reference_declared`）。
- **「单纯重写哈希不能恢复 valid」做成两级程序化保证**：图级 `detect_inplace_rewrites()` 抓「绕过重验流程」这个**动作**
  + 记录级 `_src_hash_rewritten` 让**单看一份图**也能自证异常（自检 ⑧c 三例证明堵口**不依赖**「值不符」这一信号）。
- 新增/改：`contracts/evidence.schema.json`（证据 v2）、`11_真实数据/evidence_deps.py`、`phase45_evidence_deps_delta.py`、
  `reference_registry.json`；`lineage_invalidation.py` 升级为**依赖感知失效器**（`--reverify` **MUST** 给 `--old`，否则 **RC=2**）；
  `queries.py` / `viz_server.py` / `js/{style,evidence,panels,viz}.js` 接入披露；`sttp.sh evdeps`。
- 自检：`evdeps --check` **七段**｜`lineage` **八段**（⑥ 33238/33238、⑦ 2431 中 0 误牵连）｜`subgraph` **89/0/3**（V1–V19）｜
  `frontend` **70/0/2**（+X10–X15）｜`node` **85/0**（+F30/F31 共 19 条）｜delta 幂等（复跑 **0 改动**）｜临时目录残留 **0**。
- **全量回归（9 条命令）**：`evdeps --check / check / gatecheck / eval / dimaudit / noise / release / release-full` **RC=0**
  （`release` **28/0/0**、`release-full` **31/0/0** 含 H1b 规范图 sha256 `7d4f66bca0dc6e6b`、H2 北极星 99.65 == manifest）；
  `lineage` 首轮 RC=2 系**并发编辑竞态**（**E20**），**顺序重跑 RC=0**。
- **入库文件字节对账**（铁律 #72 口径）：`TOTAL=450 DIFF=18`，18 个 DIFF **精确等于**本轮改动集
  （15 手工 + 3 个 release 副产物）⇒ 其余 **432 个文件零行尾漂移**。
- ★ **顺带修三项**：**D12 的 `additionalProperties` 静默跳过**（C5b 报红 ⇒ 补齐实现 + 反例/正例 C5b2/C5b3）；
  **E11 同一把不可重入锁的自死锁**（`stale_registry()` 持锁调 `evidence_status_registry()` ⇒ **CPU ≈ 0、无子进程、永久挂起**；
  改「计算移出锁外 + `RLock`」⇒ **铁律候选 #79：持锁时不得调用可能持同一把锁的函数**）；
  **E18 仪器自身崩溃与断言失败在退出码上不可区分**（新增断言必须先自跑一次）。
- §0.3.2 新增 Phase 45 已知边界 **E10–E22**；★ 最诚实的一条：**A04 的 `stale` 通道目前无真实数据触发的正例**
  （仓库内无「已登记来源与记录哈希不符」），其正确性由**构造性断言**（V19h/V19i）与受控小图重验（⑧a–⑧e）保证。
  另三条：**E20 并发编辑竞态**（**铁律候选 #80：报错行号 > 文件行数 ⇒ 并发写竞态，非语法缺陷**；
  后台回归期间不得改被其读取的文件）、**E21 发布清单 `dirty_sample` 非幂等**（P1，未修，含三条 Option 处方）、
  **E22 `evdeps --check` ⑦ 段干净检出假红**（P1，**已修**：⑦ 段输入源改为临时仓库内可控构造 `_probe_input.json`，
  与「大文件是否入库」解耦 + 防回退断言；主仓库与真·缺数据环境均**七段全过**）。
- **干净检出实测（`git clone --depth 1`，HEAD `164a711` == 远端 `main`）**：
  | 命令 | RC | 结果 |
  |---|---|---|
  | `bash sttp.sh evdeps --check` | **0** | **七段全过**（首测 RC=1 ⇒ ⑦ 段假红 ⇒ **E22**，已修） |
  | `"$STTP_PYTHON" 06_PoC/release/verify_release.py --release` | **0** | **28/0/0**（F1 样例反例 78/0/0） |
  | `bash sttp.sh frontend` | **0** | **62/0/4**（= 上轮 56/0/4 +6 条 X10–X15；4 条 SKIP 如实登记） |
  | `bash sttp.sh subgraph` | **2** | **明确原因**（缺不入库权威图）+ 三种可用做法，**非裸 traceback** |
  ★ **解释器选型**：裸 `python`（托管 3.13 空环境）跑 `verify_release.py --release` 会 RC=1/6 FAIL
  （`G1/G2` 缺 networkx/sympy/pint/rdkit）—— **解释器问题，非缺陷**；而 `G1/G2` 正是为**分离这两种情形**而设的断言。)*
*最近更新（续）：2026-10-14（**Phase 45 · 第 37 轮续：A05「第三人可安装入口」**。
- 复评 A05 三条现场**全部坐实并已修**：**A05a** 无 `pyproject.toml`；**A05b** `requirements.lock` 里 `python==3.11.9`
  与 pip 分发**混列**（`pip install -r` 会去找名为 `python` 的**分发**）；**A05c** root `.env` 硬编码他机 Python 路径且无根级模板。
- ★ **自查出比复评更严重的现场**：全仓 **171 个入库 `.py`** 用到 **19 个**第三方 import，其中 **7 个**此前**任何**依赖清单
  都**没声明**（`sklearn`/`rapidfuzz`/`duckdb`/`polars`/`kuzu`/`openai`/`playwright`）⇒ 照抄 `requirements.txt` 补
  `pyproject.toml` 也**跑不了**这几条路径。已按「**已安装给下限 / 未安装给裸名（不伪造版本）**」补为 9 个可选分组。
- ★ **「零构建链」口径精确化**（防误判为违约）：§5.2.1 的零构建是关于**前端 ES Module**；§0.1 的**唯一例外**点名
  「**可安装性**」照修 ⇒ `pyproject.toml` **只**声明元数据 + 入口，仓库至今**无 `package.json`**、前端零构建**不变**；
  **安装可选**（`bash sttp.sh` 仍是零安装路径与唯一效果权威）；**回退成本 ≈ 0**。
- 新增：`pyproject.toml`｜`sttp_cli/{__init__,cli}.py`（薄封装：**无机器绝对路径**、`sys.executable`、root 三路探测、
  委托既有脚本）｜`.env.example`（根级可移植模板）｜`06_PoC/tests/phase45_install_selfcheck.py`｜`sttp.sh install`。
- **两级可复现等级结构化**（A05 要点）：**L1 随包样例重放**（全入库、不需权威图/联网）vs **L2 原始源→规范图重建**
  （需不入库 `normalized.json`）；断死 `L1.needs_authority_graph=False` / `L2.…=True`，并**构造性**断言「缺 manifest ⇒ L1 不可用」。
- **新仪器 `bash sttp.sh install`：PASS 25 / FAIL 0 / SKIP 0**（RC=0，≈4.7 s，**零第三方依赖**，**干净检出可运行**）；
  退出码分档 `0/1/2/**3(仪器自证失败)**`。实测反例：改 `requires-python` ⇒ A05-3 红；删 `rdkit` ⇒ A05-4/A05-7a 红
  （A05-7b 仍 PASS ⇒ 负对照与仓库状态**解耦**）；塞回 `python==3.11.9` ⇒ A05-6a 红；打坏比较器 ⇒ **RC=3**；
  还原后 `pyproject.toml` sha256 **逐字节回基线** `9c4f143e…`。
- ★ **离线 wheel 构建**（**临时目录**，不污染仓库）：`sttp_kg-0.45.0-py3-none-any.whl` **21.0 KB**，含 `sttp_cli/cli.py`
  与全部 6 个入口点。
- §0.3.3 新增 Phase 45 A05 已知边界 **E23–E28**：**E23 测量器陷阱**（`grep -c $'\r'` 在本环境**恒等于总行数**
  ⇒ 「全 CRLF」假象，**已误导本轮判断一次**；**铁律候选 #81：行尾只用 `od`/`tr`/`awk` 判定**）；E24 L1/L2 口径边界；
  **E25 未实测 `pip install .`**（不计通过）；E26 5 个依赖只声明未验证（刻意裸名）；**E27 隐蔽耦合（已修）**；
  **E28 仪器自指（已修）**——`install` 在自检脚本**未跟踪**时 **24/0/0（绿）**、`git add` 后 **23/1（红）**：
  探针自己 `import setuptools` 且枚举用 `git ls-files`（**只含已入库**）⇒ **扫描范围 ⊂ 被测范围 = 假绿**；
  修法 = `declared` 并入 `[build-system].requires` + 新增 A05-7c（从 `build-backend` 派生比对名）+
  枚举改「入库 ∪ 未跟踪（`--others --exclude-standard`）」。
  ★★ **教训（铁律候选 #82）：仪器一旦入库，它自己就是被测对象** —— 静态扫描型自检**必须**枚举未跟踪文件。
- ★ **全量回归 13/13 全绿**（逐条记 RC，不抽样）：`install` 25/0/0 ｜ `release` 28/0/0 ｜ `release-full` 108/0/0 ｜
  `subgraph` 89/0/3 ｜ `frontend` 70/0/2 ｜ `node` 85/0 ｜ `evdeps/lineage/check/gatecheck/eval/dimaudit/noise` 全 RC=0。
  顺带**再实证 E21**：同一份清单在**脏树**非幂等（`working_tree_dirty_at_build: true`）、干净树幂等（`dirty_sample: []`）。
- 报告：`07_交付物/Phase45_A05第三人可安装入口_20261014.md`。)*
*前次更新：2026-10-14（**Phase 44 · 第 36 轮：D1 根因修复 —— 证据字节忠实度以「仓库 blob」为准**。
- 权威字节源 `git cat-file blob HEAD:<rel>`（与检出/行尾无关）；`git` 不可用 ⇒ `None`（fail-closed 而非空字节）。
- 四分类 `verified / changed / unverifiable_no_source / unverifiable_not_in_repo` **取代二态**、**可加总回 48 712**；
  `stale` 口径**不变**（= changed + unverifiable_no_source = **121**）；`unverifiable_not_in_repo` 47 055 条
  **不计入 stale**（那些来源并未变更，属「大体积派生对象不入库」的已知边界），但**始终随响应与 UI 显示**。
- 实测 `changed 760 → 0`；干净 clone 与主工作区**逐值相同**。
- 自检：`bash sttp.sh subgraph` **69/0/3**（新增 V18 十六条 + V16 改写为「D1 性质 + 构造性非真空」）；
  `bash sttp.sh frontend` **64/0/2**（新增 X8/X9）；`node frontend_core_check.mjs` **66/0**（新增 F28/F29）；
  `source_registry --check` **15/15**（新增 ⑬⑭⑮）。
- 顺带修 **D26 行尾策略覆盖不全**（原白名单漏 17 个文件 ⇒ clone 字节 ≠ blob）：置顶 `* text=auto eol=lf` + 例外，
  只读预演零改写；实测 **445/445 入库文件「工作区 == 仓库 blob」**（终测 HEAD `f173eae` 复测 **446/446**）、clone 逐字节一致。
  ★ 登记教训：`git add --renormalize` 在本仓库会改写 `-text` blob（实测 51 个，已全部还原）；
  ★★ 并登记**测量口径陷阱**：`--path` 与 `--no-filters` **互斥**（同传 ⇒ 报错 + 空 stdout ⇒ 会把全部文件误判成不一致）。
- §0.3.1 更新 D1 为**已修根因**并登记 E1–E9 残余；`contracts/subgraph.schema.json` 的 `evidence.src_hash`
  放宽为 `["string","null"]`。
- **干净检出实测（公开仓库级）**：从 GitHub 整仓克隆 ⇒ 克隆内 **446/446「字节 == blob」**、
  本机 ↔ 克隆 sha256 **逐字节 446/446 一致**、克隆内 `subgraph` **69/0/3**（四分类断言与本机**逐行 diff 为空**）、
  `frontend` **56/0/4（裸）→ 64/0/2（补入权威图后，与主工作区完全相同）**、`verify_release.py --release` **28/0/0 RC=0**。
- 顺带修 **E9 临时目录静默泄漏**（Windows 只读 git object 让 `ignore_errors=True` 吞掉删除失败，实测泄漏 91 个目录）
  ⇒ `source_registry.rmtree_force()`（先清只读位、删不掉即抛）+ 5 处接入 + 2 处补 `try/finally` + 自检 ⑮（构造性）。)*
*前次更新：2026-10-14（Phase 43 步骤 B · 第 35 轮：前端「局部图谱查询浏览」落地 —— `06_PoC/js/` 9 个原生
ES Module（零构建）、`graph_view.html` 整页重写、`viz_server.py` 新增 `/js/` 路由、双自检
`bash sttp.sh frontend`（59/0/2）+ `node frontend_core_check.mjs`（53/0）；§0.3.1 增列前端已知边界 D14–D25；
§5.2.1 新增前端模块表）。*
*前次更新：2026-10-12（Phase 36 · 第 26 轮：新增 §1g 验证语义校正（GPT6 复评 R01–R05/R07/R08）；
T9-i 95.8%→85.9% 如实下调；门禁 74→90 + `--release` fail-closed；新铁律 #51/#52；
新增 `rescope` 命令；许可包补全 vendor/licenses + DATA_LICENSES.md）。*
