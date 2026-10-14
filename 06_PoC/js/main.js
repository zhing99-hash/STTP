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
 * STTP · 编排层 —— 引导 / 模式判定 / 交互串联
 *
 * 交互四条入口（方案 §4）：
 *   ① 找点：搜索 → ego     ② 展开：选中节点 → 增量并入
 *   ③ 问路径：起点/终点 → 高亮   ④ 筛一块：查询面板 → filter
 *   ⑤（降级）概览：聚合元图 → 下钻
 */

import {
  MODE_BADGE, BUDGET, HARD, sessionText, statusStyle, STATUS_STYLE,
} from "./core.js";
import { Api } from "./api.js";
import { detectMode, modeFromLocation } from "./modes.js";
import { createViz, buildLegend, shortId } from "./viz.js";
import { SubgraphStore } from "./subgraph.js";
import { QueryPanel, renderStatusLine, renderBanner, bindSearch, esc } from "./panels.js";
import { renderNodeDetail, renderEdgeDetail, renderOverviewDetail } from "./evidence.js";

const $ = (id) => document.getElementById(id);

const state = {
  mode: "live",
  api: null,
  store: null,
  viz: null,
  panel: null,
  selNode: null,
  selEdge: null,
  pathSrc: null,
  pathDst: null,
  lastEnv: null,
  overview: false,
  pathList: [],
};

// ---------------------------------------------------------------------------
// 工具
// ---------------------------------------------------------------------------
function download(blobOrUri, filename) {
  const a = document.createElement("a");
  const uri = typeof blobOrUri === "string" ? blobOrUri : URL.createObjectURL(blobOrUri);
  a.href = uri; a.download = filename;
  document.body.appendChild(a); a.click();
  setTimeout(() => {
    document.body.removeChild(a);
    if (typeof uri === "string" && uri.startsWith("blob:")) URL.revokeObjectURL(uri);
  }, 0);
}

function setDsStatus(msg) { const el = $("ds-status"); if (el) el.textContent = msg; }

function showFallback(reason) {
  const fb = $("fallback");
  if (!fb) return;
  fb.style.display = "block";
  fb.textContent = reason;
}

// ---------------------------------------------------------------------------
// 状态同步
// ---------------------------------------------------------------------------
function refreshStatus(env) {
  const st = env || state.lastEnv;
  renderStatusLine($("statusline"), st, {
    sessionText: state.store ? sessionText(state.store.session) : "",
    sessionFull: state.store ? state.store.session.full : false,
    modeText: state.mode === "bundle" ? MODE_BADGE.bundle.text : MODE_BADGE.live.text,
  });
  renderBanner($("banner"), st, {
    onAction: (act) => {
      if (act === "raise2") { state.panel.raiseBudget(2); runFilter(); }
      else if (act === "raise5") { state.panel.raiseBudget(5); runFilter(); }
      else if (act === "shrink") { focusPanel(true); }
      else if (act === "relax") { state.panel.clear(); focusPanel(true); }
      else if (act === "overview") { showOverview(); }
      else if (act === "lower-hops") {
        const h = $("f-hops"); if (h) h.value = String(Math.max(1, (parseInt(h.value, 10) || 3) - 1));
        runFilter();
      } else if (act === "raise-deadline") {
        const d = $("f-deadline"); if (d) d.value = String(Math.min(HARD.deadline_ms, (parseInt(d.value, 10) || 3000) * 2));
        runFilter();
      }
    },
  });
  if (state.panel && state.store) state.panel.setSession(state.store.session);
  syncSubstack();
}

function syncSubstack() {
  const s = state.store;
  const info = $("substack-info");
  if (info && s) {
    const sz = s.size;
    info.textContent = `当前子图 ${sz.nodes} 节点 / ${sz.edges} 边 · 栈深 ${s.depth}`;
  }
  const back = $("btn-back");
  if (back && s) back.disabled = s.depth <= 1;
  const ex = $("btn-expand");
  if (ex && s) {
    const can = !!state.selNode && s.canExpand;
    ex.disabled = !can;
    ex.title = !state.selNode ? "先选中一个节点"
      : (!s.canExpand ? "本次会话预算已用尽，请返回上一层或重置" : "展开该节点的邻居（+1 跳）");
  }
  const hint = $("expand-hint");
  if (hint && s) {
    hint.textContent = !s.canExpand
      ? "会话预算已用尽：请「返回上一层」或「重置会话」"
      : (state.selNode ? `选中：${shortId(state.selNode.id)}` : "在画布上点一个节点，然后点「展开邻居」");
  }
}

function refreshLegend() {
  if (!state.viz) return;
  const types = Array.from(new Set(state.viz.cy.edges().map((e) => e.data("type")))).sort();
  const nodeTypes = Array.from(new Set(state.viz.cy.nodes().map((n) => n.data("type")))).sort();
  buildLegend($("legend"), { edgeTypes: types, nodeTypes });
  if (state.panel) {
    const kinds = Array.from(new Set(state.viz.cy.edges().map((e) => e.data("kind")))).sort();
    state.panel.setKindHints(kinds);
  }
}

function focusPanel(on) {
  const p = $("left");
  if (!p) return;
  p.classList.toggle("collapsed", !on);
  document.body.classList.toggle("panel-collapsed", !on);
  const b = $("btn-toggle-panel");
  if (b) b.setAttribute("aria-expanded", String(on));
}

/**
 * 把 `core.js` 的五态色写入 CSS 变量 —— **单一来源**：
 * 页面里 `--st-*` 的静态默认值只是"离线第一帧"的兜底，
 * 权威值来自 JS（自检会断言两者一致，不一致即变红）。
 */
function applyStatusVars() {
  const root = document.documentElement;
  Object.keys(STATUS_STYLE).forEach((k) => {
    root.style.setProperty(`--st-${k.replace(/_/g, "-")}`, STATUS_STYLE[k].color);
  });
}

// ---------------------------------------------------------------------------
// 入口动作
// ---------------------------------------------------------------------------
function onEnvError(env) {
  state.lastEnv = env;
  refreshStatus(env);
}

async function runEgo(seed, { label } = {}) {
  if (!seed) return;
  state.overview = false;
  const params = { mode: "ego", seeds: seed, hops: 1, max_nodes: BUDGET.nodes, max_edges: BUDGET.edges, trust: "verified" };
  const r = await state.store.run(params, { label: label || `找点 ${seed}` });
  if (!r.ok) { onEnvError(r.env || { status: "error", statusInfo: { status: "error", cls: "st-error", color: "#f85149", label: "错误", icon: [], hint: "" }, reason: r.error || "无结果", stats: {} }); return; }
  state.viz.add(r.env.nodes, r.env.edges);
  state.viz.layout(state.viz.cy.nodes().map((n) => n.id()));
  state.lastEnv = r.env;
  refreshStatus(r.env);
  refreshLegend();
  afterDataChange();
}

async function runFilter() {
  const p = state.panel.collect();
  state.overview = false;
  const r = await state.store.run(p, { label: "筛选" });
  if (!r.ok) { onEnvError(r.env); return; }
  state.viz.add(r.env.nodes, r.env.edges);
  state.viz.layout(r.env.nodes.map((n) => n.id));
  state.lastEnv = r.env;
  refreshStatus(r.env);
  refreshLegend();
  afterDataChange();
}

async function expandSelected() {
  if (!state.selNode) return;
  const r = await state.store.expand(state.selNode.id, { hops: 1 });
  if (!r.ok) { onEnvError(r.env || { status: "error", reason: r.error, stats: {} }); return; }
  state.viz.add(r.env.nodes, r.env.edges);
  state.viz.layout(r.env.nodes.map((n) => n.id));
  state.lastEnv = r.env;
  refreshStatus(r.env);
  refreshLegend();
  afterDataChange();
}

async function runPath() {
  const src = state.pathSrc, dst = state.pathDst;
  if (!src || !dst) { $("path-status").textContent = "请先设置起点与终点（可点「取选中」）"; return; }
  state.overview = false;
  // ★ 新前端一律走统一信封端点（/api/path 仍是 legacy 扁平信封，见报告 §6-D9）
  const env = await state.api.subgraph({
    mode: "path", src, dst, max_paths: 20, max_hops: 5, deadline_ms: 3000,
  });
  state.lastEnv = env;
  refreshStatus(env);
  const st = $("path-status");
  const paths = env.meta && Array.isArray(env.meta.paths) ? env.meta.paths : (Array.isArray(env.raw && env.raw.paths) ? env.raw.paths : []);
  state.pathList = paths;
  if (env.status === "ok" && paths.length) {
    st.textContent = `找到 ${paths.length} 条路径`;
    state.viz.add(env.nodes, env.edges);
    state.viz.layout(env.nodes.map((n) => n.id));
    state.viz.clearPathHighlight();
    state.viz.highlightPath(paths[0]);
    renderPathList(paths);
    refreshLegend();
  } else {
    st.textContent = env.status === "no_result" ? "两点之间确实没有路径（no_result，不是失败）"
      : env.status === "timeout" ? "路径查询超时（非失败）：可降低跳数或提高超时"
      : `路径查询：${env.statusInfo ? env.statusInfo.label : env.status}${env.reason ? " — " + env.reason : ""}`;
    if (paths.length) { state.viz.add(env.nodes, env.edges); renderPathList(paths); }
  }
  afterDataChange();
}

function renderPathList(paths) {
  const el = $("path-list");
  if (!el) return;
  el.innerHTML = paths.slice(0, 8).map((p, i) =>
    `<div class="pathcard ${i === 0 ? "active" : ""}" data-idx="${i}">
       <div class="phead">路径 ${i + 1}（${p.length} 跳）</div>
       <div class="chain">${p.map((id, j) => `<span class="pn ${j === 0 ? "pn-src" : j === p.length - 1 ? "pn-dst" : "pn-mid"}">${esc(shortId(id))}</span>`).join('<span class="ph">→</span>')}</div>
     </div>`).join("");
  el.querySelectorAll(".pathcard").forEach((c) => {
    c.addEventListener("click", () => {
      el.querySelectorAll(".pathcard").forEach((x) => x.classList.remove("active"));
      c.classList.add("active");
      const p = state.pathList[parseInt(c.getAttribute("data-idx"), 10)];
      state.viz.clearPathHighlight();
      state.viz.highlightPath(p);
    });
  });
}

async function showOverview() {
  state.overview = true;
  const env = await state.api.overview({ mode: "aggregate" });
  state.lastEnv = env;
  refreshStatus(env);
  if (env.status !== "ok") { onEnvError(env); return; }
  state.viz.clear();
  // 概览节点没有 label，补齐为「学科 :: 类型」
  const nodes = env.nodes.map((n) => ({
    id: n.id, label: `${n.subject}::${n.type}`, type: n.type, subject: n.subject,
    why_shown: "task_anchor", _overview: n, n_nodes: n.n_nodes,
  }));
  const edges = (env.edges || []).map((e) => ({
    id: e.id, source: e.source, target: e.target, type: e.type || "aggregate",
    kind: "aggregate", confidence: 1, explicit_or_inferred: "explicit",
    why_shown: "task_anchor", weight: e.weight,
  }));
  state.viz.add(nodes, edges);
  state.viz.layout(nodes.map((n) => n.id));
  refreshLegend();
  afterDataChange();
  const b = $("banner");
  if (b) {
    b.hidden = false; b.className = "banner st-ok";
    b.innerHTML = `<span class="bicon"></span><div class="bbody"><div class="bt">概览（聚合元图，<b>不是全图</b>）：${esc(env.stats && env.stats.n_nodes)} 个聚合块 / 全图 ${esc(env.stats && env.stats.full_nodes)} 节点。</div>
      <div class="note">点击任一聚合节点 ⇒ 下钻为一次按该「学科 × 类型」的筛选查询。</div></div>`;
  }
}

function afterDataChange() {
  const fb = $("fallback"); if (fb) fb.style.display = "none";
  syncSubstack();
}

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------
async function boot() {
  applyStatusVars();
  const { forced, base } = modeFromLocation(location.search);
  state.api = new Api({ base, deadlineMs: 3000 });

  const det = await detectMode(state.api, { forced, base });
  state.mode = det.mode;
  const badge = $("mode-badge");
  if (badge) {
    const mb = MODE_BADGE[det.mode];
    badge.textContent = mb.text;
    badge.title = `${mb.note}（${det.detail}）`;
    badge.setAttribute("data-mode", det.mode);
    badge.className = `badge mode-badge mode-${det.mode}`;
  }
  setDsStatus(det.detail);

  if (det.mode === "offline") {
    showFallback("后端 /api/* 与 /graph_data.json 均不可用。\n"
      + "请用 `bash sttp.sh viz`（或 `python 06_PoC/viz_server.py`）启动后访问；\n"
      + "file:// 直接打开会被浏览器 CORS 拦截。");
    renderStatusLine($("statusline"), {
      status: "error", statusInfo: statusStyle("error"), reason: det.detail,
      error_code: "TOOL_EXECUTION_ERROR", stats: {},
    });
    return;
  }

  // Cytoscape 可用性（缺失 ⇒ 明确降级，不静默）
  if (typeof window.cytoscape === "undefined") {
    showFallback("Cytoscape 未加载：请确认 vendor/cytoscape.min.js 可访问"
      + "（纯离线环境需有本地副本）。");
    renderStatusLine($("statusline"), {
      status: "error", statusInfo: statusStyle("error"),
      reason: "渲染内核缺失", error_code: "TOOL_EXECUTION_ERROR", stats: {},
    });
    return;
  }

  state.store = new SubgraphStore(state.api, { onChange: () => { syncSubstack(); refreshStatus(); } });
  state.viz = createViz({
    container: $("cy"),
    tooltipEl: $("tooltip"),
    onSelectNode: (n) => {
      state.selNode = n; state.selEdge = null;
      renderNodeDetail($("detail"), n, { ghost: !!(n && n.ghost) });
      syncSubstack();
    },
    onSelectEdge: (d) => {
      state.selEdge = d; state.selNode = null;
      renderEdgeDetail($("detail"), d);
      syncSubstack();
    },
    onBackground: () => {
      state.selNode = null; state.selEdge = null;
      renderNodeDetail($("detail"), null); syncSubstack();
    },
  });

  if (det.mode === "bundle") {
    // 离线样例 / 小图：整图加载（角标已标「样例全量」）
    const env = await state.api.bundle();
    const raw = env.raw || {};
    if (raw && Array.isArray(raw.nodes)) {
      state.viz.add(raw.nodes, raw.edges || []);
      state.viz.layout();
      refreshLegend();
      afterDataChange();
      renderStatusLine($("statusline"), {
        status: "ok", statusInfo: statusStyle("ok"), reason: "", error_code: null,
        snapshot_id: (raw.meta && raw.meta.snapshot_id) || null,
        stats: { n_nodes: raw.nodes.length, n_edges: (raw.edges || []).length },
      }, { modeText: MODE_BADGE.bundle.text });
    } else {
      showFallback("bundle 模式：/graph_data.json 读取失败或无数据。");
    }
  } else {
    state.panel = new QueryPanel($("panel-query"), {
      onRun: () => runFilter(),
      onChanged: () => {},
    }).mount();
    renderNodeDetail($("detail"), null);
    refreshStatus(null);
  }

  bindSearch($("search-input"), $("search-results"), state.api, (id) => runEgo(id));

  // 头部/工具条
  $("btn-expand") && $("btn-expand").addEventListener("click", expandSelected);
  $("btn-back") && $("btn-back").addEventListener("click", () => {
    const r = state.store.back();
    if (!r.ok) return;
    state.viz.clear();
    state.viz.add(state.store.graph.nodes, state.store.graph.edges);
    state.viz.layout();
    state.lastEnv = r.top ? r.top.env : null;
    refreshStatus(state.lastEnv); refreshLegend();
  });
  $("btn-reset-view") && $("btn-reset-view").addEventListener("click", () => {
    state.store.reset(); state.viz.clear(); state.pathList = [];
    if ($("path-list")) $("path-list").innerHTML = "";
    renderNodeDetail($("detail"), null);
    refreshStatus(null); refreshLegend(); syncSubstack();
  });
  $("btn-fit") && $("btn-fit").addEventListener("click", () => state.viz.fit());
  $("btn-overview") && $("btn-overview").addEventListener("click", showOverview);
  $("btn-toggle-panel") && $("btn-toggle-panel").addEventListener("click", () => {
    const p = $("left"); const on = p.classList.contains("collapsed");
    focusPanel(on);
  });

  // 路径栏
  const setEnd = (which, id) => {
    if (which === "src") state.pathSrc = id; else state.pathDst = id;
    const el = $(which === "src" ? "path-src" : "path-dst");
    if (el) el.value = id || "";
  };
  $("btn-pick-src") && $("btn-pick-src").addEventListener("click", () => setEnd("src", state.selNode ? state.selNode.id : null));
  $("btn-pick-dst") && $("btn-pick-dst").addEventListener("click", () => setEnd("dst", state.selNode ? state.selNode.id : null));
  $("btn-path-swap") && $("btn-path-swap").addEventListener("click", () => {
    const a = state.pathSrc; setEnd("src", state.pathDst); setEnd("dst", a);
  });
  $("btn-path") && $("btn-path").addEventListener("click", runPath);
  $("btn-path-clear") && $("btn-path-clear").addEventListener("click", () => {
    state.viz.clearPathHighlight(); state.pathList = [];
    if ($("path-list")) $("path-list").innerHTML = "";
    if ($("path-status")) $("path-status").textContent = "已清除路径高亮";
  });
  $("path-src") && $("path-src").addEventListener("input", (e) => { state.pathSrc = e.target.value.trim() || null; });
  $("path-dst") && $("path-dst").addEventListener("input", (e) => { state.pathDst = e.target.value.trim() || null; });

  // 导出（「可带走」，零新依赖）
  $("btn-export-png") && $("btn-export-png").addEventListener("click", () => {
    const r = state.viz.exportPNG({ watermark: (state.lastEnv && state.lastEnv.snapshot_id || "").replace(/[^a-z0-9]/gi, "") });
    download(r.uri, r.filename);
  });
  $("btn-export-svg") && $("btn-export-svg").addEventListener("click", () => {
    try {
      const r = state.viz.exportSVG({ watermark: (state.lastEnv && state.lastEnv.snapshot_id || "").replace(/[^a-z0-9]/gi, "") });
      download(new Blob([r.svg], { type: "image/svg+xml" }), r.filename);
    } catch (e) { setDsStatus(`SVG 导出不可用：${e.message}`); }
  });
  $("btn-export-json") && $("btn-export-json").addEventListener("click", () => {
    const snap = state.store.jsonSnapshot({ positions: state.viz.exportPositions() });
    download(new Blob([JSON.stringify(snap, null, 2)], { type: "application/json" }), "sttp-subgraph-snapshot.json");
  });

  // 布局切换
  $("sel-layout") && $("sel-layout").addEventListener("change", (e) => {
    const name = e.target.value;
    try { state.viz.cy.layout({ name, animate: false, fit: true, padding: 40 }).run(); } catch (err) { setDsStatus(`布局 ${name} 不可用`); }
  });

  setDsStatus(`${det.detail}｜就绪`);
}

if (typeof window !== "undefined") {
  window.addEventListener("DOMContentLoaded", () => {
    boot().catch((e) => {
      showFallback(`初始化失败：${e && e.message ? e.message : e}`);
      setDsStatus(`初始化失败：${e && e.message ? e.message : e}`);
    });
  });
  // 便于自动化验收（不做任何隐藏行为）
  window.__sttp = state;
}
