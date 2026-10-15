// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 zhing
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

/**
 * STTP · 局部图谱浏览 —— **纯逻辑核心**（零 DOM / 零 fetch / 零依赖）
 *
 * 为什么要把这些函数单独放一个文件：
 *   前端的「五态映射 / 参数规范化 / 合并去重 / 会话记账 / 模式判定」是**可被断言的真值**，
 *   不是"看起来对"。把它们与 DOM 隔离后，`node` 可以直接 import 本文件做**可失败断言**
 *   （注入一处改动即变红），无需引入任何第三方测试框架（铁律 #26「可复算即证据」、
 *   铁律 #55「同报规模/非空」、项目 §0.1「拒绝构建链」）。
 *
 * ★ 本文件**不得** import 任何浏览器专有 API；`api.js` 反向依赖本文件，不是反过来。
 */

// ---------------------------------------------------------------------------
// 端点常量（**单一来源**）
// ---------------------------------------------------------------------------
// 新前端一律走统一信封端点；`/api/path` 的 legacy 扁平信封**不使用**（见报告 §6-D9）。
export const ENDPOINTS = Object.freeze({
  search: "/api/search",
  subgraph: "/api/subgraph",
  overview: "/api/overview",
  stats: "/api/stats",
  /**
   * ★ Phase 49 新增。**探活专用**：不触碰图谱数据 ⇒ 毫秒级回答。
   *
   * 为什么不能用 `/api/stats` 探活（外部评审 P0-2，Phase 49 实测坐实）：
   *   权威图 67.7 MB ⇒ 冷进程首次 `/api/stats` = **4.91 s**，而前端探活预算
   *   **2 s** ⇒ 浏览器 abort ⇒ 判「后端不可用」⇒ 退 bundle ⇒ 首次打开就像坏了。
   *   `/api/healthz` 只读进程内计数器 ⇒ 冷启动期间也能立即作答。
   */
  healthz: "/api/healthz",
  /** 仅在 mode=bundle（离线样例/小图）下读取；live 模式**永不**请求它（验收 V1）。 */
  bundleGraph: "/graph_data.json",
});

/**
 * `bundle`（离线整图）体积上限 = **8 MB**（铁律 #66「规模是设计约束：达到即拒绝」）。
 *
 * 依据（实测）：发布样例 `sample_graph.json` ≈ **373 KB**；权威全图
 * `graph_data_phase41.json` ≈ **48.7 MB** —— 相差两个数量级，用 `Content-Length`
 * 在**下载前**即可可靠区分。`bundle` 一旦读到全图，会解析 + 渲染 9657 节点/48712 边
 * 并**卡死浏览器**，所以这里是**拒绝**而不是「静默截断」。
 */
export const BUNDLE_MAX_BYTES = 8 * 1024 * 1024;

// 后端 `queries.py` 的五态（铁律 #67：「空」与「失败」必须分列）
export const STATUS_ORDER = Object.freeze(["ok", "no_result", "truncated", "timeout", "error"]);

// 深色底（= 既有页面 `--bg`）；状态色的对比度以此为准。
export const SURFACE_BG = "#0f1419";

/**
 * 五态视觉编码 —— **颜色 + 图标 + 文字三重编码**（验收 V8；方案 §6.2-4）。
 * ★ 任何一态都不得只靠颜色被识别：`icon` 与 `label` 是必填，缺一即被自检判红。
 * 图标 = Lucide 风格 24×24 描边路径（`fill:none; stroke:currentColor`），**不用 emoji**。
 */
export const STATUS_STYLE = Object.freeze({
  ok: {
    cls: "st-ok",
    color: "#3fb950",          // 绿（通过）
    label: "正常",
    icon: ["M22 11.08V12a10 10 0 1 1-5.93-9.14", "M22 4 12 14.01l-3-3"],
    hint: "查询在预算内完成",
  },
  no_result: {
    cls: "st-no-result",
    color: "#8b949e",          // 中性灰（**不是失败**）
    label: "无结果",
    icon: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18", "M8 12h8"],
    hint: "该条件下确实没有匹配；可放宽类型/学科或提高跳数",
  },
  truncated: {
    cls: "st-truncated",
    color: "#d29922",          // 黄（有结果但被预算截断）
    label: "已截断",
    icon: ["m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3",
           "M12 9v4", "M12 17h.01"],
    hint: "显示 X / 实有 Y；可提高预算或缩小过滤",
  },
  timeout: {
    cls: "st-timeout",
    color: "#f0883e",          // 橙红（**非空、非失败**：预算到期即退）
    label: "超时",
    icon: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18", "M12 7v5l3.5 2"],
    hint: "已到 deadline_ms 即返回；可降低跳数/提高超时",
  },
  error: {
    cls: "st-error",
    color: "#f85149",          // 真红（系统故障）
    label: "错误",
    icon: ["m15.5 2-8 0L2 7.5v9L7.5 22h9l5.5-5.5v-9L15.5 2",
           "m15 9-6 6", "m9 9 6 6"],
    hint: "查看 error_code 与 reason",
  },
});

/** `why_shown` 六个枚举（与后端 `queries.WHY_SHOWN_VALUES` 必须一致，自检 C 段交叉断言） */
export const WHY_SHOWN_LABEL = Object.freeze({
  used_in_step: "本步使用",
  evidence_path: "证据路径",
  user_specified: "用户指定",
  task_anchor: "任务锚点",
  verified_context: "已验上下文",
  expansion: "由展开引入",
});

/**
 * `error_code` 文案表 —— **唯一真值源**是 `contracts/error.schema.json` 的 `enum`；
 * 自检（F7 / X6）**从契约派生**比对名，不再硬编码列表（硬编码会在新增码时静默漏项）。
 *
 * 9 个 = 后端可产生的 8 个 + **传输层 1 个**（`BACKEND_UNREACHABLE`，只由前端合成：
 * 连接根本建立不起来时后端**没有**参与，故不得记成后端执行异常 —— 见铁律候选 #86）。
 */
export const ERROR_CODE_TEXT = Object.freeze({
  BAD_REQUEST: "参数不合法（请检查查询条件）",
  BACKEND_UNREACHABLE: "无法连接后端（本地服务未启动或已退出，请运行 bash sttp.sh viz）",
  SNAPSHOT_MISMATCH: "图快照与请求不一致（页面数据已过期，请刷新）",
  EVIDENCE_STALE: "证据已过期（源文件与登记哈希不一致）",
  SUBGRAPH_LIMIT_REACHED: "本次会话预算已用尽（请返回上一层或重置）",
  GRAPH_QUERY_TIMEOUT: "查询超时（已到预算上限）",
  TOOL_EXECUTION_ERROR: "后端执行异常（非空结果，属失败）",
  ENTITY_AMBIGUOUS: "实体名有歧义（需指定命名空间）",
  MISSING_CONDITION: "缺少必要条件",
});

/**
 * `error_code` 的 UI 严重度：`err` = 真故障（红药丸）/ `warn` = 提示（黄药丸）。
 *
 * ★ **显式归类，不设"其它一律"回落**（铁律候选 #84）：契约 `enum` 里每个码都必须
 *   在此出现；自检 F7c 从 `contracts/error.schema.json` **派生**比对名，新增码时
 *   若忘了归类就会红 —— 而不是被静默当成某一档。
 */
export const ERROR_CODE_SEVERITY = Object.freeze({
  BAD_REQUEST: "err", SNAPSHOT_MISMATCH: "err", TOOL_EXECUTION_ERROR: "err",
  BACKEND_UNREACHABLE: "err",
  EVIDENCE_STALE: "warn", SUBGRAPH_LIMIT_REACHED: "warn", GRAPH_QUERY_TIMEOUT: "warn",
  ENTITY_AMBIGUOUS: "warn", MISSING_CONDITION: "warn",
});

/**
 * 取严重度。**未登记 → `err`（往响处失败）**：未知错误码更该显眼，
 * 不得静默降级成黄色提示（铁律 #38「PASS≠无红」的同族取向）。
 */
export function errorCodeSeverity(code) {
  return ERROR_CODE_SEVERITY[code] === "warn" ? "warn" : "err";
}

// ---------------------------------------------------------------------------
// 颜色工具
// ---------------------------------------------------------------------------

/** `#rrggbb` → [r,g,b]（0–255）；非法输入返回 null（不抛，由调用方判定）。 */
export function hexToRgb(hex) {
  if (typeof hex !== "string") return null;
  const m = /^#?([0-9a-fA-F]{6})$/.exec(hex.trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** WCAG 相对亮度。 */
export function relLuminance(hex) {
  const rgb = hexToRgb(hex);
  if (!rgb) return null;
  const f = (c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]);
}

/** WCAG 对比度（1–21）。任一参数非法返回 null ⇒ 调用方须显式处理（不可判定 ≠ 通过，铁律 #30）。 */
export function contrastRatio(fg, bg) {
  const a = relLuminance(fg);
  const b = relLuminance(bg);
  if (a === null || b === null) return null;
  const hi = Math.max(a, b);
  const lo = Math.min(a, b);
  return (hi + 0.05) / (lo + 0.05);
}

// ---------------------------------------------------------------------------
// 状态归一化
// ---------------------------------------------------------------------------

/**
 * 把后端返回的 `status` 归一化到五态。
 * ★ 未知值**不得**被静默当作 `ok`（铁律 #30：不可判定 ≠ 放过）——
 *   返回 `{status:"error", unknown:原始值}`，由 UI 显式暴露。
 */
export function normalizeStatus(raw) {
  const s = String(raw == null ? "" : raw).trim().toLowerCase();
  if (STATUS_ORDER.indexOf(s) >= 0) return { status: s, unknown: null };
  return { status: "error", unknown: raw == null ? "(缺失)" : String(raw) };
}

/** 状态描述符（含颜色/图标/文字/hint）；未知态落入 `error` 并保留原始值。 */
export function statusStyle(raw) {
  const { status, unknown } = normalizeStatus(raw);
  const st = STATUS_STYLE[status];
  return {
    status,
    cls: st.cls,
    color: st.color,
    label: st.label,
    icon: st.icon,
    hint: unknown ? `未识别的状态值「${unknown}」` : st.hint,
    unknown,
  };
}

// ---------------------------------------------------------------------------
// 参数规范化
// ---------------------------------------------------------------------------

/** 这些键**不参与** `query_hash`（与后端 `viz_server._qhash` 的剔除口径一致）。 */
export const HASH_EXCLUDED_KEYS = Object.freeze(["session_id", "reset", "expand_token"]);

/**
 * ★ 默认预算（Phase 49 收敛）：**40 节点 / 70 边 / 2 跳 / 10 s / 5 条路径**。
 *
 * 为什么不再是 500/2000/3 s（Phase 43 旧默认）——三条**实测**理由：
 *   ① `has_symbol` 占 **24 341/48 712** 条边（全图 50%），500 节点的 ego 子图
 *      必然是一团「毛线团」，首屏不可读（外部评审 §6.1）；
 *   ② 权威图 67.7 MB ⇒ 后端冷启动首查 **4.91 s**（Phase 49 实测），
 *      3 s 预算**必然 timeout** ⇒ 首次体验直接看到失败态；
 *   ③ 外部评审实测「50 节点 / 100 边 / 10 s ⇒ 约 63 ms 返回」，故 40/70/10 s 留有余量。
 *
 * ★ 这**只是默认值**，用户可在范围区或左侧面板提高（硬上限见 `HARD`）。
 * ★ `TASK_SCOPE_DEFAULT` 从本常量派生 ⇒ 两处默认**同源**，不会各写一套。
 */
export const BUDGET = Object.freeze({
  nodes: 40, edges: 70, hops: 2, deadline_ms: 10000, paths: 5,
});
export const HARD = Object.freeze({
  nodes: 5000, edges: 20000, hops: 5, deadline_ms: 30000, paths: 200,
});
/** 会话级累计（铁律 #66：规模是设计约束；达到即**拒绝**，不静默截断） */
export const SESSION_LIMIT = Object.freeze({ nodes: 120, edges: 200 });

/**
 * 文本搜索的最短查询长度 —— **必须等于** `queries.MIN_QUERY_LEN`（跨层断言 **X5b**）。
 * 前端据此**不发**明知会被 400 拒绝的请求（否则"第一次按键"就制造一次 `BAD_REQUEST`，
 * 并把 400 误显示成"没有匹配"）。★ 与后端同源，不各自硬编码。
 */
export const MIN_QUERY_LEN = 2;

function clampInt(v, lo, hi, dflt) {
  const n = parseInt(v, 10);
  if (!Number.isFinite(n)) return dflt;
  return Math.max(lo, Math.min(hi, n));
}

/**
 * UI 状态 → 查询参数（**键排序、剔除哈希排除键** ⇒ 与后端 `query_hash` 口径一致）。
 * 返回普通对象；空值键**不出现**（避免 `a=&b=` 这种噪声进入哈希）。
 */
export function buildParams(opts = {}) {
  const o = opts || {};
  const out = {};
  const put = (k, v) => {
    if (v === null || v === undefined) return;
    if (typeof v === "string" && v.trim() === "") return;
    if (Array.isArray(v)) {
      const arr = v.filter((x) => x !== null && x !== undefined && String(x).trim() !== "");
      if (!arr.length) return;
      out[k] = arr.map((x) => String(x)).join(",");
      return;
    }
    out[k] = typeof v === "number" ? String(v) : String(v);
  };

  // ★ `q` 是 `/api/search` 的**唯一必填**参数（后端 `viz_server._api_search`：
  //   `if not q: raise ValueError("缺少参数 q")` ⇒ HTTP 400）。
  //   本函数**曾经漏掉它** ⇒ 搜索词被静默丢弃 ⇒ 请求退化成 `limit=20&mode=ego`
  //   ⇒ 400 ⇒ 搜索框"没反应"（2026-10-14 实测旧实例日志 26 次 400）。
  //   ★ 教训（铁律候选 #85）：**序列化漏字段不会自己报错**，只会在对端表现为
  //   "参数非法"；故此处必须显式列举，且自检要断言**真正发出去的 URL**含 `q=`，
  //   而不是只断言"函数是确定性的"（F11 那种形状断言抓不到本类缺陷 —— 见 #83）。
  put("q", o.q);                    // 文本搜索（/api/search）
  // `mode === null` ⇒ **显式抑制**：`/api/search` 不认 `mode`，发出去纯属噪声；
  // 未传（undefined）⇒ 与后端一致地默认 `ego`（`/api/subgraph` 用）。
  if (o.mode !== null) put("mode", o.mode || "ego");
  put("seeds", o.seeds);
  // ★ `hops` 与 `max_hops` 是**两个不同的后端参数**：
  //   `hops`     = 本次要走的跳数（`/api/subgraph` 读它；未给时 ego 默认 1、path 默认 DEFAULT_PATH_HOPS）
  //   `max_hops` = 上限钳制（`hops` 会被 `min(hops, max_hops)` 截断）
  //   本函数**曾经漏掉 `hops`** ⇒ 前端传的跳数被静默丢弃 ⇒ ego 永远 1 跳。
  //   与 `q` 同族（铁律候选 #85：**序列化漏字段不会自己报错**，只会让对端用默认值）。
  //   断言：X7 系列（`/api/subgraph` 真的认 `hops`）+ H8e（跳数改变结果规模）。
  put("hops", o.hops);
  put("src", o.src);
  put("dst", o.dst);
  put("types", o.types);            // 维度一：语义关系（24 种）
  put("kinds", o.kinds);            // 维度二：来源/管线（115 种）★ 与 types 不混用
  put("node_types", o.node_types);
  // ★ 学科：`/api/subgraph` 读的是**复数** `subjects`，`/api/search` 读的是**单数** `subject`
  //   —— 这是后端既有的命名不一致（实测：`subjects=化学` 生效 400→20 节点，
  //   `subject=化学` 被**完全忽略**）。前端两个都发，各自送到认得它的端点，
  //   并把这一不对称**写成断言**（X7b/X7c）而不是假装统一。
  put("subject", o.subject);        // /api/search 用（单数）
  put("subjects", o.subjects);      // /api/subgraph 用（复数）
  put("trust", o.trust);
  put("min_confidence", o.min_confidence);
  put("max_nodes", o.max_nodes);
  put("max_edges", o.max_edges);
  put("max_hops", o.max_hops);
  put("deadline_ms", o.deadline_ms);
  put("max_paths", o.max_paths);
  put("limit", o.limit);
  // ★ 后端读的是 `exclude_model_inferred`（不是 `exclude_inferred`）。
  //   本函数**曾经**发 `exclude_inferred` ⇒ 「排除推断边」勾选框**完全无效**
  //   （实测：`exclude_inferred=1` 仍返回 5 条含 inferred 的边；
  //    `exclude_model_inferred=1` 才生效）。同族第 3 例，见铁律候选 #85。
  if (o.exclude_model_inferred) put("exclude_model_inferred", "1");
  if (o.exclude_stale) put("exclude_stale", "1");
  if (o.session_id) put("session_id", o.session_id);
  if (o.reset) put("reset", "1");
  if (o.expand_token) put("expand_token", o.expand_token);

  // 键排序 ⇒ 序列化确定（同一 UI 状态必得同一字符串）
  const sorted = {};
  Object.keys(out).sort().forEach((k) => { sorted[k] = out[k]; });
  return sorted;
}

/** 参数 → 查询串（键已排序）。 */
export function toQueryString(params) {
  return Object.keys(params)
    .map((k) => `${encodeURIComponent(k)}=${encodeURIComponent(params[k])}`)
    .join("&");
}

/** 用于缓存键/复现的本地哈希口径（仅用于前端展示与去重，**不冒充**服务端 `query_hash`）。 */
export function paramsSignature(params) {
  const p = { ...params };
  HASH_EXCLUDED_KEYS.forEach((k) => { delete p[k]; });
  return Object.keys(p).sort().map((k) => `${k}=${p[k]}`).join("&");
}

// ---------------------------------------------------------------------------
// 任务式入口（Phase 49）—— 示例卡 / 范围 / 请求构造（**纯数据 + 纯函数**）
// ---------------------------------------------------------------------------
//
// 背景（外部评审 §7 阶段 B/C 与《标准用户使用与演示流程》§3）：
//   现有前端以「图浏览器」为中心 —— 打开就是筛选面板 + 空画布，用户不知道该输入什么。
//   新的**首屏**是任务式入口：一个输入框 + 四张示例卡 ⇒ 先返回候选实体 ⇒
//   **用户确认实体后**才发查询（避免自然语言歧义直接污染结果）⇒ 生成局部图 ⇒
//   不满意可「修改查询」回到入口（保留上次输入与已确认实体）。
//
// ★ 为什么这些数据/函数放在 `core.js`（而不是 `task.js`）：
//   `task.js` 要碰 DOM，无法用裸 node 断言。而「卡片是否指向真实存在的实体」
//   「请求参数是否与后端契约一致」是**可被判定的真值**，必须能被断言（铁律 #26/#39）。
//   ⇒ 纯逻辑留在这里，`task.js` 只做「渲染 + 收事件」。

/**
 * 四张示例卡 —— **文案来自《标准用户使用与演示流程》§3 的推荐首页**。
 *
 * ★ 每张卡必须绑定**本快照下真实存在**的实体与关系类型（铁律 #39「命名缺口架空验证器」：
 *   卡片写了但查不到的成语，等于把「演示失败」伪装成「图谱没有数据」）。
 *   实测值（trust=verified，40 节点/70 边/10 s 预算，Phase 49 探针）：
 *     card1 `CE:pq:gibbs_energy, PB:pq:entropy` → **truncated 8 节点 / 11 边**（hidden 31）
 *     card2 `PQ:energy` + dim/has_unit     → **ok 15 / 29**
 *     card3 `PQ:energy` + unit/dim/…       → **ok 15 / 29**（hidden 30）
 *     card4 `OM:mo:ethanol` + 组成关系      → **ok 4 / 3**（元素 H/C/O）
 *   ★ `note` 字段是**如实披露**：卡片问的是自然语言问题，系统实际执行的是图中确实存在的
 *     那一类查询。**不把「做不到的解析」写进卡片**（铁律 #30「不可判定≠放过」）。
 */
const CARD_SEED = (c) => Object.freeze({
  ...c, types: Object.freeze(c.types || []),
  // 卡片自带的**推荐范围**（Phase 51/D49）；冻结以防被就地改写
  scope: c.scope ? Object.freeze({ ...c.scope }) : null,
});

export const TASK_CARDS = Object.freeze([
  CARD_SEED({
    id: "relation",
    question: "吉布斯自由能与熵之间有什么关系？",
    mode: "path",
    start: "gibbs",              // 预填搜索词 → 用户从中确认起点实体
    end: "entropy",
    types: [],                   // 路径查询不加类型过滤（否则容易人为切断通路）
    // ★ Phase 51（D49）：卡片自带的**推荐范围**。
    //   动机：本卡注释承诺「可连通（8 节点/11 边）」，但那份数据是 **3 跳**下测的，
    //   而任务入口的全局默认跳数是 **2**（`BUDGET.hops`，为 ego 卡的邻域浏览而定）。
    //   两者不一致 ⇒ 用户点了第一张卡、确认两端、点「生成子图」，得到的是 `no_result`
    //   —— **发布了一个自己默认参数达不到的承诺**（铁律 #52 家族）。
    //   实测（`CE:pq:gibbs_energy,PB:pq:entropy`，verified）：
    //     hops=2 ⇒ no_result（hidden=12）｜hops=3/4/5 ⇒ **truncated 8 节点 / 11 边 / 5 条路径**
    //   故：卡片显式预填它承诺的那个跳数（会**回写进输入框**，用户看得见、改得动，
    //   不是静默修正 —— 铁律 #84：不得静默回落）。
    //   全局默认仍是 2：**不给所有卡统一加跳数**（那会把 ego 卡的邻域也撑大）。
    scope: { hops: 3 },
    note: "先确认两端实体，再看它们之间的**有限条**路径。"
      + "★ 同名实体来自不同来源时结论会不同：本快照下 `Gibbs free energy ↔ entropy(PB)`"
      + "在 **3 跳起**可连通（8 节点 / 11 边 / 5 条路径，本卡已把跳数预填为 3），"
      + "而 `↔ entropy(TH)` 在 5 跳内**确实没有路径** —— 这正是「先确认实体」的意义。"
      + "（把跳数调回 2，PB 那一支也会如实变成 no_result：通路长度是真的。）",
  }),
  CARD_SEED({
    id: "dimension",
    question: "这个公式的量纲是否一致？",
    mode: "ego",
    start: "energy",
    end: null,
    types: ["dimensionally_consistent", "has_unit"],
    note: "★ 已知边界：本图中量纲关系挂在**物理量**之间"
      + "（`dimensionally_consistent` 共 47 条，全部是「量↔量」），**公式节点没有量纲边**。"
      + "故本卡实际执行的是「与所选量**量纲一致**的量有哪些、它的单位是什么」，"
      + "**不是**公式符号级的量纲演算。",
  }),
  CARD_SEED({
    id: "units",
    question: "某个物理量有哪些单位与相关公式？",
    mode: "ego",
    start: "energy",
    end: null,
    types: ["has_unit", "dimensionally_consistent", "derived_from", "same_as", "defines"],
    note: "「单位」取 `has_unit`；「相关公式/量」取量纲一致、派生来源、同义与定义关系。"
      + "★ 有意**不含** `has_symbol`（全图 24 341 条，占一半）—— 带上它必然截断成一团毛线。",
  }),
  CARD_SEED({
    id: "composition",
    question: "某个分子由哪些元素或官能团组成？",
    mode: "ego",
    start: "ethanol",
    end: null,
    types: ["composed_of", "has_functionalgroup", "has_element", "related_to"],
    note: "`composed_of` ⇒ 组成元素，`has_functionalgroup` ⇒ 官能团。"
      + "1 跳即答案（本快照下 ethanol ⇒ H / C / O 三个元素）。",
  }),
]);

/** 任务入口的**默认范围**（从 `BUDGET` 派生 ⇒ 两处默认同源）。 */
export const TASK_SCOPE_DEFAULT = Object.freeze({
  hops: BUDGET.hops,
  max_nodes: BUDGET.nodes,
  max_edges: BUDGET.edges,
  deadline_ms: BUDGET.deadline_ms,
  trust: "verified",
});

/** 可信度档位（与后端 `queries.TRUST_LEVELS` 同名，X 段交叉断言）。 */
export const TRUST_LEVELS = Object.freeze(["verified", "verified_strict", "all"]);

/** 归一化范围：数字钳到 `HARD` 内、trust 白名单化（**未知档 ⇒ 往严处回落 `verified`**）。 */
export function normalizeScope(scope = {}) {
  const s = { ...TASK_SCOPE_DEFAULT, ...(scope || {}) };
  const trust = TRUST_LEVELS.indexOf(s.trust) >= 0 ? s.trust : TASK_SCOPE_DEFAULT.trust;
  return {
    hops: clampInt(s.hops, 1, HARD.hops, TASK_SCOPE_DEFAULT.hops),
    max_nodes: clampInt(s.max_nodes, 1, HARD.nodes, TASK_SCOPE_DEFAULT.max_nodes),
    max_edges: clampInt(s.max_edges, 1, HARD.edges, TASK_SCOPE_DEFAULT.max_edges),
    deadline_ms: clampInt(s.deadline_ms, 1, HARD.deadline_ms, TASK_SCOPE_DEFAULT.deadline_ms),
    trust,
  };
}

/**
 * 任务 → `/api/subgraph` 请求参数。
 *
 * ★ **失败必须显式**（铁律 #34「负结果优先」/ #62「fail-closed」）：
 *   `mode=path` 需要 **2 个**存在的种子，后端缺一个就回 `no_result`
 *   （「path 模式需要 2 个存在的种子（起点/终点）」）。
 *   与其发出一个**明知会被拒**的请求再把它渲染成「没有结果」，
 *   不如返回 `null` 让 UI 说清「还缺哪一端」。
 *
 * @returns {object|null} 可直接交给 `Api.subgraph()` 的参数；缺必要条件时 `null`
 */
export function taskRequest(card, { seed = null, second = null, scope = {} } = {}) {
  const sc = normalizeScope(scope);
  const has = (v) => v != null && String(v).trim() !== "";
  const types = (card && Array.isArray(card.types) && card.types.length) ? card.types.slice() : null;
  const common = {
    trust: sc.trust, max_nodes: sc.max_nodes, max_edges: sc.max_edges,
    deadline_ms: sc.deadline_ms, types,
  };
  const isPath = !!(card && card.mode === "path");
  if (isPath) {
    if (!has(seed) || !has(second)) return null;      // 明确「还缺一端」，不伪装成 no_result
    return {
      mode: "path", seeds: `${String(seed).trim()},${String(second).trim()}`,
      max_hops: sc.hops, max_paths: BUDGET.paths, ...common,
      // path 模式下后端的 `hops` 无意义（它用 `max_hops` 作为搜索深度）⇒ 不发送无意义参数
    };
  }
  if (!has(seed)) return null;
  return { mode: "ego", seeds: String(seed).trim(), hops: sc.hops, ...common };
}

/** 任务是否可执行（用于按钮 `disabled` 与**禁用原因**文案，铁律 #63「拒绝分支显式」）。 */
export function taskBlockedReason(card, { seed = null, second = null } = {}) {
  const has = (v) => v != null && String(v).trim() !== "";
  if (!has(seed)) return "请先输入关键词并**确认一个候选实体**（点候选行即可）";
  if (card && card.mode === "path" && !has(second)) return "本卡查「两点之间的路径」，还需要确认**第二个**实体";
  return null;
}

/** 候选实体取前 N（后端已按分数排序；前端**不重排**，只截断并如实披露总数）。 */
export function takeCandidates(env, n = 5) {
  const hits = (env && Array.isArray(env.hits)) ? env.hits : [];
  return {
    items: hits.slice(0, Math.max(0, n)),
    shown: Math.min(hits.length, Math.max(0, n)),
    total: (env && typeof env.total_matches === "number") ? env.total_matches : hits.length,
    truncated: !!(env && (env.truncated === true
      || (typeof env.total_matches === "number" && env.total_matches > hits.length))),
    status: (env && env.status) || "error",
    error_code: (env && env.error_code) || null,
    reason: (env && env.reason) || "",
  };
}

/**
 * 路径项 → **节点 id 序列**。同时兼容两种形状：
 *   · 统一信封（Phase 49 起）：`{nodes: [id…], edges: [eid…]}`
 *   · legacy `/api/path`：裸数组 `[id…]`
 * ★ 归一化放在这里（而不是在每个消费点各写一次 branch）：旧实现按裸数组解码，
 *   统一信封一到手就整条路径渲染不出来 —— 而**没有任何地方报错**。
 */
export function pathNodeIds(p) {
  if (Array.isArray(p)) return p.map(String);
  if (p && Array.isArray(p.nodes)) return p.nodes.map(String);
  return [];
}

/** 路径项 → 边 id 序列（legacy 裸数组没有边信息 ⇒ 返回空数组，不臆造）。 */
export function pathEdgeIds(p) {
  if (p && Array.isArray(p.edges)) return p.edges.map(String);
  return [];
}

// ---------------------------------------------------------------------------
// 合并去重（幂等）
// ---------------------------------------------------------------------------

/**
 * 把 `incoming` 并入 `cur`，按 `node.id` / `edge.id` 去重。
 * ★ 返回 `added` 计数：**第二次合并同一响应必须是 0**（幂等，自检断言）。
 */
export function mergeGraph(cur, incoming) {
  const base = cur || { nodes: [], edges: [] };
  const inc = incoming || { nodes: [], edges: [] };
  const haveN = new Set((base.nodes || []).map((n) => n && n.id));
  const haveE = new Set((base.edges || []).map((e) => e && e.id));
  const nodes = (base.nodes || []).slice();
  const edges = (base.edges || []).slice();
  let addN = 0, addE = 0, dupN = 0, dupE = 0;

  (inc.nodes || []).forEach((n) => {
    if (!n || n.id === undefined || n.id === null) return;
    if (haveN.has(n.id)) { dupN += 1; return; }
    haveN.add(n.id); nodes.push(n); addN += 1;
  });
  (inc.edges || []).forEach((e) => {
    if (!e || e.id === undefined || e.id === null) return;
    if (haveE.has(e.id)) { dupE += 1; return; }
    haveE.add(e.id); edges.push(e); addE += 1;
  });

  return { nodes, edges, added: { nodes: addN, edges: addE }, dup: { nodes: dupN, edges: dupE } };
}

// ---------------------------------------------------------------------------
// 会话记账（与服务端 `stats.session` 对齐；前端只做**提示与禁用**，判定权在服务端）
// ---------------------------------------------------------------------------

/** 会话累计计数器。服务端是唯一权威；前端这份用于**即时禁用按钮**，不得用它放宽预算。 */
export class SessionBudget {
  constructor(limit = SESSION_LIMIT) {
    this.limit = { nodes: limit.nodes, edges: limit.edges };
    this.reset();
  }

  reset() { this.nodes = 0; this.edges = 0; this.expansions = 0; return this; }

  /** 从服务端 `stats.session` 同步（服务端说的算）。 */
  syncFrom(stats) {
    const s = (stats && stats.session) || null;
    if (!s) return this;
    if (typeof s.n_nodes === "number") this.nodes = s.n_nodes;
    if (typeof s.n_edges === "number") this.edges = s.n_edges;
    if (typeof stats.expansions === "number") this.expansions = stats.expansions;
    return this;
  }

  /** 试算：+n/+m 后是否超限（**不修改**状态）。 */
  wouldExhaust(n, m) {
    return (this.nodes + (n || 0) > this.limit.nodes)
        || (this.edges + (m || 0) > this.limit.edges);
  }

  add(n, m) {
    if (this.wouldExhaust(n, m)) return { ok: false, exhausted: true };
    this.nodes += (n || 0); this.edges += (m || 0); this.expansions += 1;
    return { ok: true, exhausted: false, nodes: this.nodes, edges: this.edges };
  }

  /** 返回上一层 / 撤销时的回滚（不允许为负）。 */
  rollback(n, m) {
    this.nodes = Math.max(0, this.nodes - (n || 0));
    this.edges = Math.max(0, this.edges - (m || 0));
    this.expansions = Math.max(0, this.expansions - 1);
    return this;
  }

  /**
   * 是否**已用尽**（含恰好等于）——恰好等于上限时也必须禁用继续展开，
   * 否则下一次请求必被服务端拒绝，UI 会出现「按钮可点但一定失败」的坏状态。
   */
  get full() { return this.nodes >= this.limit.nodes || this.edges >= this.limit.edges; }
}

// ---------------------------------------------------------------------------
// 模式判定（live | bundle | offline）
// ---------------------------------------------------------------------------

/**
 * 由探测结果决定运行模式。
 *   apiOk    —— `/api/stats` 或 `/api/subgraph` 可用（真后端在跑）
 *   bundleOk —— `/graph_data.json` 可读（发布样例 / 小图）
 *   forced   —— URL `?mode=live|bundle` 强制
 * ★ 真值表由自检逐格断言（含反例：两者皆不可用 ⇒ `offline`，**不得**假装成 live）。
 */
export function chooseMode({ apiOk, bundleOk, forced } = {}) {
  const f = (forced || "").trim().toLowerCase();
  if (f === "live") return apiOk ? "live" : (bundleOk ? "bundle" : "offline");
  if (f === "bundle") return bundleOk ? "bundle" : (apiOk ? "live" : "offline");
  if (apiOk) return "live";
  if (bundleOk) return "bundle";
  return "offline";
}

export const MODE_BADGE = Object.freeze({
  live: { text: "实时查询", note: "子图按需拉取（不加载全图）" },
  bundle: { text: "样例全量", note: "离线单文件图（发布样例 / 小图）" },
  offline: { text: "离线降级", note: "后端不可用：仅静态提示，无图数据" },
});

// ---------------------------------------------------------------------------
// 客户端等待预算（★ **不得**比服务端更早放弃）
// ---------------------------------------------------------------------------

/** 客户端等待预算相对服务端 `deadline_ms` 的默认余量（毫秒）。 */
export const CLIENT_MARGIN_MS = 4000;

/**
 * 由请求自身的参数推出**客户端**的等待预算。
 *
 * ★ 为什么必须这样算（Phase 51 / D53 的现场）：
 *   旧实现给 `Api` 一个**固定** `deadlineMs = 3000`，而服务端允许的
 *   `deadline_ms` 默认就是 **10000**（`queries.DEFAULT_DEADLINE_MS`）。
 *   ⇒ 客户端在 3 s 就 `abort`，而**服务端还在算** ⇒ 用户看到"失败"，
 *     而后端**本来会给出答案**。
 *   现场表现酷似"偶发"：空闲时 100% 通过（实测路径查询 0.36 s），
 *   一旦机器忙（本轮在 `release-full` + `eval` 之后跑）同一查询就超 3 s ⇒ 必现。
 *   ★「目标随机」的失败最容易被当成 flaky 放过 —— 但它是**真缺陷**。
 *
 * 判据顺序（**显式覆盖 > 由查询参数派生 > 兜底**）：
 *   1. `opts.deadlineMs` —— 调用点显式指定（如 healthz 探活 5 s），原样采用，不改写；
 *   2. `opts.deadline_ms` —— 查询自己的服务端超时 ⇒ **`deadline_ms + marginMs`**
 *      （★ 必须 **严格大于** `deadline_ms`：客户端先放弃 = 白等）；
 *   3. 都没有 ⇒ `fallbackMs`（**不允许**回落到 0 / NaN / Infinity）。
 *
 * 非法输入（0 / 负数 / NaN / 字符串 / null）一律走兜底 —— 否则一个 `NaN + 4000`
 * 会让预算变成 `NaN`，`setTimeout(…, NaN)` 等价于**立即 abort**（比固定 3 s 更糟）。
 *
 * @param {{deadlineMs?:number, deadline_ms?:number}} opts
 * @param {{fallbackMs?:number, marginMs?:number}} [defaults]
 * @returns {number} 正整数毫秒
 */
export function clientBudgetMs(opts = {}, defaults = {}) {
  const fallbackMs = defaults.fallbackMs == null ? 3000 : Number(defaults.fallbackMs);
  const marginMs = defaults.marginMs == null ? CLIENT_MARGIN_MS : Number(defaults.marginMs);
  const ok = (v) => typeof v === "number" && Number.isFinite(v) && v > 0;
  if (ok(opts.deadlineMs)) return opts.deadlineMs;
  if (ok(opts.deadline_ms)) return opts.deadline_ms + (ok(marginMs) ? marginMs : 0);
  return ok(fallbackMs) ? fallbackMs : 3000;
}

// ---------------------------------------------------------------------------
// 提交前校验（**先在前端拦住必然 400 的请求**，并给出可读原因）
// ---------------------------------------------------------------------------

/**
 * 校验一次查询是否具备必要条件。
 * ★ `mode=filter` **必须至少一个过滤条件** —— 后端对 `mode=filter&trust=all`（无任何条件）
 *   刻意返回 400（防全图倾倒，报告 §6-D5）。前端必须把它渲染成「请至少选一个筛选项」，
 *   而不是让用户看见一个系统故障。因此这里**先行拦截**。
 * @returns {{ok:boolean, field?:string, message?:string}}
 */
export function validateParams(o = {}) {
  const mode = (o.mode || "ego").trim().toLowerCase();
  const nonEmpty = (v) => {
    if (v === null || v === undefined) return false;
    if (Array.isArray(v)) return v.filter((x) => String(x).trim() !== "").length > 0;
    return String(v).trim() !== "";
  };
  if (mode === "ego") {
    if (!nonEmpty(o.seeds)) return { ok: false, field: "seeds", message: "请先选择一个起点（或在搜索框里搜索实体）" };
    return { ok: true };
  }
  if (mode === "path") {
    // ★ Phase 51 修（D48）：本端点（`/api/subgraph`）的 path 模式读的是 **`seeds`（2 个）**，
    //   不是 `src`/`dst`。Phase 49 已把**发送侧**（`taskRequest` / `main.runPath`）从
    //   `src`/`dst` 改成 `seeds`，但这里的**校验侧**漏改 ⇒ 任务卡的路径查询在本地就被判
    //   「请给出起点与终点」拒掉、请求**根本发不出去**。真浏览器实测：点第一张卡
    //   （`relation`）→ 确认两端 → 点「生成子图」⇒ `/api/subgraph` 请求数 **0**、
    //   状态栏「错误」、`store.depth = 0`、画布空 —— 而**没有任何 JS 异常**。
    //
    //   为什么此前没被发现：底部路径栏 `runPath()` 是**直接调 api**、不经本函数 ⇒
    //   它一直好用；只有任务卡这条新链路会撞上。**同一个功能有两条代码路径、
    //   只有一条经过校验** —— 与 E35「改了发送侧漏了校验侧」同族。
    //
    //   `src`/`dst` 属 legacy `/api/path`（D43：该端点甚至不做 trust 过滤），
    //   本函数服务于 `/api/subgraph` ⇒ **不得**把它判为合法，否则等于放出一个
    //   后端必然回 no_result 的请求（把「参数形状错了」伪装成「知识上不存在」）。
    const n = String(o.seeds == null ? "" : o.seeds)
      .split(",").map((x) => x.trim()).filter(Boolean).length;
    if (n !== 2) {
      return {
        ok: false, field: "seeds",
        message: n < 2
          ? `本查询需要**两个**实体（起点 / 终点），当前只给了 ${n} 个 —— 请确认两端`
          : `本查询只接受两个实体（起点 / 终点），当前给了 ${n} 个`,
      };
    }
    return { ok: true };
  }
  if (mode === "filter") {
    const any = ["types", "kinds", "node_types", "subject"].some((k) => nonEmpty(o[k]));
    if (!any) {
      return { ok: false, field: "types",
               message: "请至少选择一个过滤条件（语义关系 / 来源管线 / 节点类型 / 学科）——" +
                        "「全图浏览」已取消，全貌请看「概览」" };
    }
    return { ok: true };
  }
  return { ok: false, field: "mode", message: `未知查询模式「${mode}」` };
}

// ---------------------------------------------------------------------------
// 截断文案（**不得**让"已截断"看起来像"全部"；铁律 #12「计数≠内容」）
// ---------------------------------------------------------------------------

/** 生成「显示 X / 实有 Y」文案；`pre` 缺失时**明确**说"实有未知"，不得编造。 */
export function truncationText(stats) {
  const s = stats || {};
  const shown = `${s.n_nodes == null ? "?" : s.n_nodes} 节点 / ${s.n_edges == null ? "?" : s.n_edges} 边`;
  const by = s.truncated_by || "预算";
  if (s.pre_truncation == null) {
    return { text: `已截断（${by}）：${shown}（实有规模未知）`, shown, pre: null, by };
  }
  const pre = s.pre_truncation;
  const preTxt = `${pre.n_nodes == null ? "?" : pre.n_nodes} 节点 / ${pre.n_edges == null ? "?" : pre.n_edges} 边`;
  return { text: `已截断（${by}）：显示 ${shown}，实有 ${preTxt}`, shown, pre: preTxt, by };
}

/** 会话角标文案。 */
export function sessionText(b) {
  const used = `${b.nodes}/${b.limit.nodes} 节点 · ${b.edges}/${b.limit.edges} 边`;
  return b.full ? `会话预算已用尽（${used}）` : `会话累计 ${used}`;
}
