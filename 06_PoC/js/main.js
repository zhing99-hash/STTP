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
  MODE_BADGE, BUDGET, HARD, sessionText, statusStyle, STATUS_STYLE, pathNodeIds, eraSortKey,
  taskRequest,
} from "./core.js";
import { Api } from "./api.js";
import { detectMode, modeFromLocation } from "./modes.js";
import { createViz, buildLegend, shortId } from "./viz.js";
import { SubgraphStore } from "./subgraph.js";
import { QueryPanel, renderStatusLine, renderBanner, bindSearch, esc } from "./panels.js";
import { renderNodeDetail, renderEdgeDetail, renderOverviewDetail } from "./evidence.js";
import { TaskEntry } from "./task.js";
import { emptyDiagOf } from "./empty.js";

const $ = (id) => document.getElementById(id);

const state = {
  mode: "live",
  api: null,
  store: null,
  viz: null,
  panel: null,
  task: null,            // ★ Phase 49：任务式首屏
  taskUsed: false,       // 是否已用它生成过图（决定「← 修改查询」是否出现）
  selNode: null,
  selEdge: null,
  pathSrc: null,
  pathDst: null,
  lastEnv: null,
  overview: false,
  pathList: [],
  // ★ F12：上一次**真正发出**的查询（模式 + 种子 + 跳数）。「一键放宽」必须
  //   用同一模式重发，否则会把"给这个节点找个邻域"变成"全图筛一块"，结果面目全非。
  lastQuery: null,
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
    onAction: (act, btn) => {
      if (act === "raise2") { state.panel.raiseBudget(2); runFilter(); }
      else if (act === "raise5") { state.panel.raiseBudget(5); runFilter(); }
      else if (act === "shrink") { focusPanel(true); }
      // ★ F12（D69）：旧的 `relax` 只是 `panel.clear()` —— 把用户的所有条件一并丢掉，
      //   既没说"刚才是谁挡住的"，也不保证清空后能有结果。现在按**服务端诊断**放宽
      //   **一个**维度后重发；清空面板另设一个如实命名的动作。
      else if (act === "relax-diag") { relaxByDiagnosis(btn); }
      else if (act === "clear-filters") { state.panel.clear(); focusPanel(true); runFilter(); }
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
  // ★ Phase 54：当前子图内**实际出现**的发现年代桶，按时间序（跨公元前后单调）。
  //   纯展示 —— 用户决策「不参与筛选」⇒ 这里既不生成控件，也不改变任何查询参数。
  //   计数只统计**当前已在画布上**的节点（随增量子图自然刷新，不是全图统计）。
  const eraCounts = {};
  state.viz.cy.nodes().forEach((n) => {
    const e = n.data("era");
    if (e) eraCounts[e] = (eraCounts[e] || 0) + 1;
  });
  const eras = Object.keys(eraCounts).sort((a, b) => eraSortKey(a) - eraSortKey(b));
  buildLegend($("legend"), { edgeTypes: types, nodeTypes, eras, eraCounts });
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
  // ★ Phase 49：走统一的「执行参数」入口 —— 默认预算/档位来自 core.BUDGET（已收敛为
  //   40/70/2/10 s），不再是硬编码的 500/2000/trust=verified。
  return runSubgraph({
    mode: "ego", seeds: seed, hops: BUDGET.hops,
    max_nodes: BUDGET.nodes, max_edges: BUDGET.edges, deadline_ms: BUDGET.deadline_ms,
    trust: "verified",
  }, { label: label || `找点 ${seed}` });
}

/**
 * 执行一次 `/api/subgraph` 并并入画布（ego / path / filter 共用一条路径）。
 *
 * ★ 单点：**所有**子图请求都必须经这里 ⇒ 「成功必须清掉上一个错误横幅」（外部评审 §6.2）
 *   只需修一处；也不会再出现"某条入口忘了清 banner"。
 */
async function runSubgraph(params, { label } = {}) {
  state.overview = false;
  // ★ F12：记下**这次真正发出的**查询形态 —— 「一键放宽」必须用同一模式重发
  //   （把 ego 悄悄变成 filter 会换掉整个语义，用户看到的图将不是他刚才那张）。
  state.lastQuery = { mode: (params.mode || "ego"), seeds: params.seeds || null,
                      hops: params.hops };
  const r = await state.store.run(params, { label: label || params.mode });
  if (!r.ok) {
    onEnvError(r.env || {
      status: "error", statusInfo: statusStyle("error"), cls: "st-error",
      reason: r.error || "无结果", stats: {},
    });
    return null;
  }
  state.viz.add(r.env.nodes, r.env.edges);
  state.viz.layout((r.env.nodes || []).map((n) => n.id));
  state.lastEnv = r.env;
  refreshStatus(r.env);                 // ★ 成功 ⇒ renderBanner 内部清空旧错误横幅
  refreshLegend();
  afterDataChange();
  return r.env;
}

/** ★ Phase 49：任务式首屏的「生成子图」回调。 */
async function runTask({ params, card }) {
  state.taskUsed = true;
  maybeShowReopenTask();
  const label = card && card.mode === "path" ? `关系路径 ${card.question}` : `任务 ${params.seeds}`;
  const env = await runSubgraph(params, { label });
  // ★ Phase 51（D50b）：**失败态的结论也必须写出来**。
  //   `runSubgraph` 在 `no_result` / 空载荷 `timeout` 时返回 `null`
  //   （`store.run` 的 `!hasPayload` 分支），信封只留在 `store.lastEnvelope` 里。
  //   旧实现于是 `return` 掉 —— `#path-status` 会**保留上一次查询的结论**。
  //   那比空白更糟：用户以为这是本次的结果（「上次说有 5 条路径，这次还是 5 条？」）。
  //   故以 store 里的信封兜底，把本次的五态如实写出来。
  const settled = env || (state.store && state.store.lastEnvelope) || null;
  if (params.mode === "path" && settled) {
    // 路径模式：把「有哪几条路径」也画出来（后端 Phase 49 起透传 `paths`）
    state.pathList = Array.isArray(settled.paths) ? settled.paths : [];
    if (state.pathList.length) {
      state.viz.clearPathHighlight();
      state.viz.highlightPath(state.pathList[0]);
      renderPathList(state.pathList);
    }
    const el = $("path-status");
    // ★ 文案与底部路径栏**同源**（`pathOutcomeText`），见该函数的说明。
    if (el) el.textContent = "任务卡路径查询："
      + pathOutcomeText(settled, state.pathList,
                        (state.task && state.task.state) ? state.task.state.scope : null);
  }
}

/**
 * 路径查询结论 → **唯一**文案源（`runPath` 与任务卡共用）。
 *
 * ★ 为什么必须收敛到一处：这条文案承担的是**认识论责任** ——
 *   "有没有路径"这个结论**对可信档与跳数都敏感**。实测（2026-10-15）：
 *     · `MX:chem:co2 → MX:chem:combustion`：verified ⇒ no_result；all ⇒ 找到 3 条（随后 timeout）
 *     · `CE:pq:gibbs_energy → PB:pq:entropy`：2 跳 ⇒ no_result（两档都是）；
 *       3 跳 ⇒ truncated 8 节点/11 边
 *   ⇒ 只说"确实没有路径"，会把「被可信度档隐藏」或「跳数不够」**报告成「不存在」**
 *     （铁律 #52：验证的命题须＝被发布的命题；#67：五态须分列且说清边界）。
 *   两处各写一套，就等于把这条责任复制两份、迟早只修一份。
 */
function pathOutcomeText(env, paths, ctx) {
  const n = (paths || []).length;
  // ★ Phase 56（B1）：把**本次实际用的**可信档与跳数写进结论。
  //   起因：本函数的注释早就写明"这个结论对可信档与跳数都敏感"，但**从不报出这两个值**——
  //   于是"没路径"到底是"真没有"还是"档/跳数不够"，用户只能猜。
  //   路径栏改走界面参数后，默认跳数从写死的 5 变成面板的 2：
  //   若不报出实际值，这个变化就是**静默的**（用户会以为"图退化"了）。
  const used = (ctx && ctx.hops != null)
    ? `（本次参数：可信档 ${ctx.trust || "verified"} ｜ ${ctx.hops} 跳）` : "";
  if (env.status === "ok") return `找到 ${n} 条路径${used}`;
  if (env.status === "truncated") {
    return `找到 ${n} 条路径（已达 max_paths，属**截断**而非全部）${used}`;
  }
  if (env.status === "no_result") {
    const hid = Number(env.hidden_by_trust || 0);
    return (hid > 0
      ? `在当前**可信度档（verified）**与预算内没有路径 —— 本次已隐藏 ${hid} 条低可信度边`
        + `（按边 id 去重、已探索范围内）；通路可能正被它们连接。`
        + `可把「可信度」切到 all 再试；**若切档后仍无结果，更可能是跳数不够**，可增大跳数。`
        + `（no_result ≠ 失败）`
      : "在当前可信度档与跳数内没有路径（no_result，不是失败）—— 可增大跳数或放宽关系类型。")
      + used;
  }
  if (env.status === "timeout") {
    return "路径查询超时（非失败）：可降低跳数或提高超时"
      + (n ? `（已找到 ${n} 条，属**截断**而非全部）` : "") + used;
  }
  return `路径查询：${env.statusInfo ? env.statusInfo.label : env.status}`
    + (env.reason ? " — " + env.reason : "") + used;
}

function maybeShowReopenTask() {
  const b = $("btn-reopen-task");
  if (!b) return;
  // 只有 live 模式才有任务入口可回；且必须**已经生成过图**才显示（否则首屏本来就在）
  const hasGraph = !!(state.store && (state.taskUsed || state.store.depth > 0));
  b.hidden = !(state.task && state.mode === "live" && hasGraph);
}

async function runFilter() {
  const p = state.panel.collect();
  return runSubgraph(p, { label: "筛选" });
}

/** 在**当前**横幅追加一行说明（不覆盖原诊断；下一次 refresh 自然消失）。 */
function bannerNote(msg) {
  const el = $("banner");
  const body = el && el.querySelector(".bbody");
  if (!body) return;
  let n = body.querySelector(".ed-note");
  if (!n) {
    n = document.createElement("div");
    n.className = "note ed-note";
    body.appendChild(n);
  }
  n.textContent = msg;
}

/**
 * 「一键放宽」—— 按服务端诊断**只改一个维度**后，用同一模式重发。
 *
 * 四条纪律（每一条都对应一次踩过的坑）：
 *  1. 只改诊断点名的那**一个**键。整块清空（旧实现）等于把用户的其他选择一起丢掉，
 *     而且下一轮又不知道是谁挡的 —— 放宽失去"可解释"。
 *  2. 改**面板控件**（单一真相源）而不是请求参数：否则界面条件与实际生效条件分叉，
 *     用户下次手点「执行」时又回到旧条件（"点了没反应"）。
 *  3. 面板若**什么都没变** ⇒ 不重发。重发一次注定同样空的查询 = 假动作（铁律 #30）。
 *  4. 按钮必须与**当前**诊断对得上（横幅可能已刷新）—— 否则拿旧按钮改新维度。
 */
async function relaxByDiagnosis(btn) {
  const diag = emptyDiagOf(state.lastEnv);
  if (!diag || !diag.action) {
    bannerNote("当前没有可执行的放宽动作（诊断未给出）。");
    return;
  }
  const key = btn ? btn.getAttribute("data-relax-key") : null;
  if (key && key !== diag.action.key) {
    bannerNote(`诊断已更新（按钮对应「${key}」，当前建议「${diag.action.key}」）⇒ 请按当前诊断操作。`);
    return;
  }
  if (!state.panel || typeof state.panel.applyPatch !== "function") {
    bannerNote("查询面板不可用，无法放宽。");
    return;
  }
  const changed = state.panel.applyPatch(diag.action);
  if (!changed.length) {
    bannerNote("面板上没有可改的东西（该条件已是最宽）⇒ 未重发请求，"
      + "避免一次注定同样空的查询。");
    return;
  }
  const last = state.lastQuery || { mode: "filter" };
  if (last.mode === "path") { await runPath(); return; }
  if (last.mode === "ego" && last.seeds) {
    const p = state.panel.collect();
    const hops = diag.action.key === "hops" ? diag.action.to : last.hops;
    await runSubgraph({ ...p, mode: "ego", seeds: last.seeds, hops }, { label: "放宽后重试" });
    return;
  }
  await runFilter();
}

async function expandSelected() {
  if (!state.selNode) return;
  const r = await state.store.expand(state.selNode.id, { hops: 1 });
  if (!r.ok) { onEnvError(r.env || { status: "error", statusInfo: statusStyle("error"), reason: r.error, stats: {} }); return; }
  state.viz.add(r.env.nodes, r.env.edges);
  state.viz.layout((r.env.nodes || []).map((n) => n.id));
  state.lastEnv = r.env;
  refreshStatus(r.env);                 // 成功 ⇒ 清掉旧错误横幅
  refreshLegend();
  afterDataChange();
}

async function runPath() {
  const src = state.pathSrc, dst = state.pathDst;
  if (!src || !dst) { $("path-status").textContent = "请先设置起点与终点（可点「取选中」）"; return; }
  state.overview = false;
  // ★ Phase 49 修 P0-4（外部评审 §5-P0-4）：
  //   `mode=path` 在后端**只读 `seeds``**（需 2 个：起点/终点）。
  //   旧实现发的是 `src=`/`dst=` ⇒ 该端点根本不读 ⇒ **必然** no_result
  //   「path 模式需要 2 个存在的种子（起点/终点）」—— 路径栏从来没有真正工作过。
  //   （`/api/path` 才是读 src/dst 的 legacy 端点；新前端统一走 `/api/subgraph`。）
  // ★ Phase 56（B1，外部评审 2026-10-17 §5「新发现问题」）：
  //   本函数曾**自组请求参数**，只发 `mode/seeds/max_paths/max_hops/deadline_ms`：
  //     · 不读左侧「可信度」⇒ 后端 `viz_server.py` 用默认 `trust="verified"`；
  //     · `max_hops` 写死 `HARD.hops`（=5）⇒ 不读左侧跳数设置。
  //   后果**不是"少一个参数"**：紧邻的 `pathOutcomeText()` 在 `no_result` 分支里
  //   明确教用户「可把可信度切到 all 再试」「若仍无结果…可增大跳数」——
  //   而这两件事**路径栏都做不到**，请求一个字节都不会变。
  //   用户照做、结果照旧 ⇒ 会把「被可信度档隐藏」报告成「图里确实没有路径」。
  //   与 Phase 55 D64 同族：**指引指向代码无法兑现的动作**。
  //   处方：统一走 `taskRequest()`（与任务卡**同一个构造器**，D48/D50 的教训是
  //   "两条代码路径各写一套 ⇒ 迟早只修一份"），scope 取左侧面板当前值。
  const _pf = (state.panel && typeof state.panel.collect === "function")
    ? state.panel.collect() : {};
  const req = taskRequest({ mode: "path" }, {
    seed: src, second: dst,
    scope: {
      trust: _pf.trust, hops: _pf.max_hops, max_nodes: _pf.max_nodes,
      max_edges: _pf.max_edges, deadline_ms: _pf.deadline_ms,
    },
  });
  // `taskRequest` 对缺端点返回 null（fail-closed）。此处两端已在上方校验过，
  // 但**不假设**它不会为 null：宁可显式说清，也不发一个明知被拒的请求。
  if (!req) {
    $("path-status").textContent = "请先设置起点与终点（两端都要有）";
    return;
  }
  state.lastQuery = { mode: "path", seeds: [src, dst], hops: req.max_hops };
  const env = await state.api.subgraph(req);
  state.lastEnv = env;
  refreshStatus(env);
  const st = $("path-status");
  // ★ 统一信封自 Phase 49 起透传 `paths`（`{nodes, edges}`）；`pathNodeIds` 同时兼容
  //   旧 shape（裸 id 数组）⇒ 两种来源都不会整条渲染不出来而不报错。
  const paths = Array.isArray(env.paths) ? env.paths : [];
  state.pathList = paths;
  // ★ Phase 51（D50）：文案取自**唯一来源** `pathOutcomeText()`（与任务卡共用）。
  //   旧实现此处内联一大段、任务卡那边又另写一段 ⇒ 同一个结论两种解释，
  //   且改动只可能落在其中一处（D48 的教训：两条代码路径口径必须收敛）。
  // ★ 传 `{trust, hops}` 而非直接传 `req`：path 模式下请求键是 `max_hops`
  //   （后端该模式不读 `hops`），而文案想说的是"几跳"，两者不能混用键名。
  st.textContent = pathOutcomeText(env, paths, { trust: req.trust, hops: req.max_hops });
  const drew = (env.status === "ok" || env.status === "truncated") && paths.length > 0;
  if (drew) {
    state.viz.add(env.nodes, env.edges);
    state.viz.layout((env.nodes || []).map((n) => n.id));
    state.viz.clearPathHighlight();
    state.viz.highlightPath(pathNodeIds(paths[0]));
    renderPathList(paths);
    refreshLegend();
  } else if (paths.length) {
    // timeout 也可能带部分结果 ⇒ 有就画，但状态仍如实显示（铁律 #67）
    state.viz.add(env.nodes, env.edges);
    renderPathList(paths);
  }
  afterDataChange();
}

function renderPathList(paths) {
  const el = $("path-list");
  if (!el) return;
  el.innerHTML = paths.slice(0, 8).map((p, i) => {
    const ids = pathNodeIds(p);
    return `<div class="pathcard ${i === 0 ? "active" : ""}" data-idx="${i}">
       <div class="phead">路径 ${i + 1}（${Math.max(0, ids.length - 1)} 跳）</div>
       <div class="chain">${ids.map((id, j) => `<span class="pn ${j === 0 ? "pn-src" : j === ids.length - 1 ? "pn-dst" : "pn-mid"}">${esc(shortId(id))}</span>`).join('<span class="ph">→</span>')}</div>
     </div>`;
  }).join("");
  el.querySelectorAll(".pathcard").forEach((c) => {
    c.addEventListener("click", () => {
      el.querySelectorAll(".pathcard").forEach((x) => x.classList.remove("active"));
      c.classList.add("active");
      const p = state.pathList[parseInt(c.getAttribute("data-idx"), 10)];
      state.viz.clearPathHighlight();
      state.viz.highlightPath(pathNodeIds(p));
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
  maybeShowReopenTask();     // ★ 只要有图，「← 修改查询」就该在
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

    // ★ Phase 49：**首屏 = 任务入口**（问题 → 候选 → 确认实体 → 生成局部图）。
    //   只有 live 模式才挂：bundle/offline 下没有 /api/search，挂上去也查不出候选
    //   （宁可不出入口，也不给一个点了没反应的框）。
    state.task = new TaskEntry($("task-entry"), {
      api: state.api,
      onRun: (payload) => runTask(payload),
    }).mount();
    state.task.open();
  }

  function openTaskEntry() {
    if (!state.task) return;
    state.task.open({
      query: state.task.state.query, second: state.task.state.second,
      seed: state.task.state.seed, seedLabel: state.task.state.seedLabel,
      seed2: state.task.state.seed2, seed2Label: state.task.state.seed2Label,
      cardId: state.task.state.card ? state.task.state.card.id : null,
      scope: state.task.state.scope,
    });
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
  // ★ Phase 49：「← 修改查询」—— 回到任务入口，保留上次输入与已确认实体
  $("btn-reopen-task") && $("btn-reopen-task").addEventListener("click", openTaskEntry);
  // 键盘直达（Esc 关闭入口；不隐藏数据，只是回到画布）
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.task && state.task.isOpen && state.taskUsed) state.task.close();
  });
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
