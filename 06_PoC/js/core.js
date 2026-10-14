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
  /** 仅在 mode=bundle（离线样例/小图）下读取；live 模式**永不**请求它（验收 V1）。 */
  bundleGraph: "/graph_data.json",
});

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

/** `error_code` 文案表 —— 覆盖后端声明的 **8 个**（自检断言覆盖齐全，防"只写一半"） */
export const ERROR_CODE_TEXT = Object.freeze({
  BAD_REQUEST: "参数不合法（请检查查询条件）",
  SNAPSHOT_MISMATCH: "图快照与请求不一致（页面数据已过期，请刷新）",
  EVIDENCE_STALE: "证据已过期（源文件与登记哈希不一致）",
  SUBGRAPH_LIMIT_REACHED: "本次会话预算已用尽（请返回上一层或重置）",
  GRAPH_QUERY_TIMEOUT: "查询超时（已到预算上限）",
  TOOL_EXECUTION_ERROR: "后端执行异常（非空结果，属失败）",
  ENTITY_AMBIGUOUS: "实体名有歧义（需指定命名空间）",
  MISSING_CONDITION: "缺少必要条件",
});

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

export const BUDGET = Object.freeze({
  nodes: 500, edges: 2000, hops: 3, deadline_ms: 3000, paths: 20,
});
export const HARD = Object.freeze({
  nodes: 5000, edges: 20000, hops: 5, deadline_ms: 30000, paths: 200,
});
/** 会话级累计（铁律 #66：规模是设计约束；达到即**拒绝**，不静默截断） */
export const SESSION_LIMIT = Object.freeze({ nodes: 120, edges: 200 });

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

  put("mode", o.mode || "ego");
  put("seeds", o.seeds);
  put("src", o.src);
  put("dst", o.dst);
  put("types", o.types);            // 维度一：语义关系（24 种）
  put("kinds", o.kinds);            // 维度二：来源/管线（115 种）★ 与 types 不混用
  put("node_types", o.node_types);
  put("subject", o.subject);
  put("trust", o.trust);
  put("min_confidence", o.min_confidence);
  put("max_nodes", o.max_nodes);
  put("max_edges", o.max_edges);
  put("max_hops", o.max_hops);
  put("deadline_ms", o.deadline_ms);
  put("max_paths", o.max_paths);
  put("limit", o.limit);
  if (o.exclude_inferred) put("exclude_inferred", "1");
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
    if (!nonEmpty(o.src) || !nonEmpty(o.dst)) {
      return { ok: false, field: "src", message: "请给出起点与终点（可点「取选中」）" };
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
