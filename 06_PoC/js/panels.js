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
 * STTP · 面板层 —— 查询面板（★ 两组维度分列）/ 全局搜索 / **五态状态行** / 截断横幅
 *
 * ★ 验收 V8：五态在 UI 上必须**颜色 + 图标 + 文字三重编码**，
 *   根元素带 `data-state="<status>"` 与 `class="st-*"`（供 DOM 断言），
 *   内层必有 `<svg class="st-icon">` 与 `<span class="st-label">`。
 *   ★ **不得**只靠颜色区分（颜色只是三个通道之一）。
 */

import {
  STATUS_STYLE, statusStyle, ERROR_CODE_TEXT, errorCodeSeverity, BUDGET, HARD, SESSION_LIMIT,
  MIN_QUERY_LEN, truncationText, sessionText, WHY_SHOWN_LABEL,
} from "./core.js";
import {
  EDGE_TYPE_COLOR, NODE_TYPE_ORDER, SUBJECT_ORDER, VERIFICATION_BADGE, EOI_STYLE,
} from "./style.js";
import { emptyDiagOf, emptyDiagHTML } from "./empty.js";

const EDGE_TYPES = Object.keys(EDGE_TYPE_COLOR);   // ★ 恰好 24 种（与规范图实测集合相等）

export function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** 五态图标（内联 SVG，**不用 emoji**） */
export function statusIconSvg(paths, size = 14) {
  return `<svg class="st-icon" viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true"`
    + ` fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">`
    + (paths || []).map((d) => `<path d="${d}"/>`).join("")
    + "</svg>";
}

/**
 * 渲染五态状态行。
 * 产出结构（供 V8 的 DOM 断言）：
 *   <div id="statusline" data-state="truncated">
 *     <span class="stchip st-truncated"><svg class="st-icon">…</svg><span class="st-label">已截断</span></span>
 *     …若干 .pill…
 *   </div>
 */
/**
 * 尚未发起任何查询（`env` 为空）时的**中性**状态描述符。
 * `status` 刻意用 `"ok"`：与 `graph_view.html` 的初始 `data-state="ok"` 一致，
 * 且**不得**把「还没查」渲染成「错误」。可见文字是「待查询」，配色取中性灰。
 */
const IDLE_STATUS = Object.freeze({
  status: "ok", cls: "st-no-result", color: "#8b949e", label: "待查询",
  icon: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18", "M12 8v5", "M12 16.5h.01"],
  hint: "尚未发起查询 —— 顶部搜索实体 / 公式，或用左栏条件生成子图",
  unknown: null,
});

export function renderStatusLine(el, env, extra = {}) {
  if (!el) return;
  // ★ 尚未发起任何查询（`env` 为空）⇒ **中性「待查询」**（2026-10-14 实测修正）：
  //   原实现回落 `statusStyle("error")` ⇒ 页面刚打开、什么都没查时就显示红色「错误」，
  //   且与 HTML 的初始 `data-state="ok"` 自相矛盾。`status` 保持 `"ok"`（不谎报错误），
  //   配色用中性灰（`st-no-result` 类）。
  const st = (env && env.statusInfo) ? env.statusInfo : IDLE_STATUS;
  const stats = (env && env.stats) || {};
  const pills = [];

  if (env) {
    if (env.snapshot_id) pills.push(`<span class="pill" title="查询源文件 sha256[:12]">快照 <code>${esc(env.snapshot_id)}</code></span>`);
    if (env.query_hash) pills.push(`<span class="pill" title="规范化参数 sha256[:16]，可复现同一次查询">query <code>${esc(env.query_hash)}</code></span>`);
    if (stats.elapsed_ms != null) pills.push(`<span class="pill">耗时 ${esc(stats.elapsed_ms)} ms</span>`);
    if (stats.n_nodes != null || stats.n_edges != null) {
      pills.push(`<span class="pill">本次 ${esc(stats.n_nodes == null ? "?" : stats.n_nodes)} 节点 / ${esc(stats.n_edges == null ? "?" : stats.n_edges)} 边</span>`);
    }
    const hid = (env.hidden_by_trust != null) ? env.hidden_by_trust : stats.hidden_by_trust;
    if (hid) pills.push(`<span class="pill warn" title="本次查询中被可信范围排除的**不同**边数（按边 id 去重；不是遍历判定次数）">已隐藏 ${esc(hid)} 条</span>`);
    // ★ 证据字节忠实度**分列**（Phase 44 · D1）：必须在状态行可见 ——
    //   否则「stale 很小」会被误读成「证据都能字节复算」。全图口径优先（那是真规模）。
    const fidAll = stats.evidence_fidelity_global || stats.evidence_fidelity;
    if (fidAll && typeof fidAll === "object") {
      const bits = [`一致 ${fidAll.verified == null ? "?" : fidAll.verified}`];
      if (fidAll.changed) bits.push(`已变更 ${fidAll.changed}`);
      if (fidAll.unverifiable_no_source) bits.push(`不可判定 ${fidAll.unverifiable_no_source}`);
      if (fidAll.unverifiable_not_in_repo) bits.push(`需上游 ${fidAll.unverifiable_not_in_repo}`);
      const scope = stats.evidence_fidelity_global ? "全图" : "本次";
      pills.push(`<span class="pill ${fidAll.changed ? "warn" : ""}" title="证据字节忠实度分列（${scope}）：一致=locator 在仓库内且哈希相符；已变更=需重验；不可判定=无来源（模型/规则源）；需上游=locator 未入库、需上游原始数据（已知边界，**并非**变更，不计入 stale）">`
        + `证据字节 ${scope}：${esc(bits.join(" / "))}</span>`);
    }
    // ★ A04 验证依赖状态（Phase 45）—— 与字节忠实度**并列的第二层**：
    //   上一行只答「来源那个字节文件变了吗」；本行还答「它依赖的参考表 / 验证器变了吗」。
    //   `needs_review`（本环境不可判定）**不在** exclude_stale 里，但**必须显示** ——
    //   否则会被读成「全都是 valid」。
    const stAll = stats.evidence_status_global || stats.evidence_status;
    if (stAll && typeof stAll === "object") {
      const sbits = [];
      if (stAll.valid) sbits.push(`有效 ${stAll.valid}`);
      if (stAll.stale) sbits.push(`已变更 ${stAll.stale}`);
      if (stAll.needs_review) sbits.push(`待复核 ${stAll.needs_review}`);
      if (stAll.rejected) sbits.push(`已否决 ${stAll.rejected}`);
      if (stAll.no_record) sbits.push(`无记录 ${stAll.no_record}`);
      if (!sbits.length) sbits.push("无");
      const sscope = stats.evidence_status_global ? "全图" : "本次";
      pills.push(`<span class="pill ${(stAll.stale || stAll.rejected) ? "warn" : ""}" title="A04 验证依赖状态分列（${sscope}）：有效=来源·参考表·验证器·依赖版本均与当前一致；已变更=可复算依赖不符（需重验）；待复核=本环境不可判定（缺数据本身，**并非**变更，不计入「排除 stale」）；已否决=命题被显式否决；无记录=该图上还没有 A04 记录（缺记录 ≠ 已验证）">`
        + `验证依赖 ${sscope}：${esc(sbits.join(" / "))}</span>`);
    }
    if (env.error_code) {
      const txt = ERROR_CODE_TEXT[env.error_code] || "（未登记的 error_code）";
      pills.push(`<span class="pill ${errorCodeSeverity(env.error_code)}" title="${esc(txt)}">error_code <code>${esc(env.error_code)}</code></span>`);
    }
    if (env.client_aborted) pills.push('<span class="pill err">客户端中止（服务端结果未知）</span>');
    if (env.unknownStatus != null) pills.push(`<span class="pill err">未识别的状态值「${esc(env.unknownStatus)}」</span>`);
  }
  if (extra.sessionText) pills.push(`<span class="pill ${extra.sessionFull ? "err" : ""}">${esc(extra.sessionText)}</span>`);
  if (extra.modeText) pills.push(`<span class="pill">${esc(extra.modeText)}</span>`);

  el.setAttribute("data-state", st.status);
  el.innerHTML =
    `<span class="stchip ${st.cls}" title="${esc(st.hint)}">${statusIconSvg(st.icon)}`
    + `<span class="st-label">${esc(st.label)}</span></span>`
    + `<span class="st-hint">${esc(st.hint)}</span>`
    + pills.join("");
  return el;
}

/**
 * 渲染横幅（截断 / 无结果 / 超时 / 错误）。
 * ★ 截断必须显式给出「显示 X / 实有 Y」与 `truncated_by`；无 `pre_truncation` 时明说"实有规模未知"。
 * ★ `pending_frontier` **只是下界**，不得渲染成"还有 N 个"。
 */
export function renderBanner(el, env, opts = {}) {
  if (!el) return;
  // ★ F12（D69）：`ok` + 0 边（单节点/空子图）以前**根本不出现横幅** ——
  //   用户看到一张空画布，没有任何文字说明发生了什么。现在只要服务端给了
  //   `stats.empty_diagnosis` 就必须渲染，哪怕五态是 `ok`。
  const diag = emptyDiagOf(env);
  if (!env || (env.status === "ok" && !diag)) { el.innerHTML = ""; el.hidden = true; return; }
  el.hidden = false;
  const st = env.statusInfo || statusStyle(env.status);
  const stats = env.stats || {};
  let body = "";

  if (env.status === "truncated") {
    const t = truncationText(stats);
    const pf = (stats.pending_frontier != null)
      ? `<div class="note">pending_frontier（下界，中断时已知未展开的节点数）= ${esc(stats.pending_frontier)}。${esc(stats.pending_frontier_note || "")}</div>`
      : "";
    body = `<div class="bt">${esc(t.text)}</div>${pf}`
      + '<div class="bact">'
      + '<button class="toolbtn" data-act="raise2">提高预算 ×2</button>'
      + '<button class="toolbtn" data-act="raise5">提高预算 ×5</button>'
      + '<button class="toolbtn" data-act="shrink">缩小过滤</button>'
      + "</div>";
  } else if (env.status === "no_result" || (env.status === "ok" && diag)) {
    if (diag) {
      // ★ 「无结果 ≠ 失败」这句**必须**保留（铁律 #67）：诊断说的是原因，
      //   不是"这次查询出错了"。`ok` + 0 边时同样要说清"查询正常完成、只是没有边"。
      const lead = env.status === "no_result"
        ? '<div class="note ed-not-fail">无结果 ≠ 失败：查询已正常执行完，是这个条件下确实没有匹配。</div>'
        : '<div class="note ed-not-fail">查询正常完成（五态＝ok），但结果里没有边：'
          + '下面是它为什么会是空的。</div>';
      body = lead + emptyDiagHTML(diag);
    } else {
      // 服务端没给诊断（老快照 / 非本仓路径）⇒ **不编原因**，只给可执行的中性动作。
      body = `<div class="bt">该条件下<b>确实没有匹配</b>（不是失败）。</div>`
        + '<div class="note">本次没有拿到结构化诊断（服务端未给 empty_diagnosis）⇒ '
        + '不猜原因。可先清空筛选条件重试。</div>'
        + '<div class="bact"><button class="toolbtn" data-act="clear-filters">清空筛选条件</button>'
        + '<button class="toolbtn" data-act="overview">改看概览</button></div>';
    }
  } else if (env.status === "timeout") {
    body = `<div class="bt">查询在预算内未完成（<b>非空、非失败</b>；已返回能找到的部分）。</div>`
      + `<div class="note">${esc(env.reason || "")}</div>`
      + '<div class="bact"><button class="toolbtn" data-act="lower-hops">降低跳数</button>'
      + '<button class="toolbtn" data-act="raise-deadline">提高超时</button></div>';
  } else {
    body = `<div class="bt">${esc(env.reason || "执行失败")}</div>`
      + `<div class="note">error_code = <code>${esc(env.error_code || "—")}</code>`
      + `${env.error_code && ERROR_CODE_TEXT[env.error_code] ? "（" + esc(ERROR_CODE_TEXT[env.error_code]) + "）" : ""}</div>`;
  }

  const emptyish = !!(diag && env.status === "ok");
  const cls = emptyish ? "st-empty" : st.cls;
  const icon = emptyish ? STATUS_STYLE.no_result.icon : st.icon;
  el.className = `banner ${cls}`;
  el.setAttribute("data-state", emptyish ? "empty" : (env.status || ""));
  el.innerHTML = `<span class="bicon">${statusIconSvg(icon, 16)}</span><div class="bbody">${body}</div>`;
  if (typeof opts.onAction === "function") {
    el.querySelectorAll("[data-act]").forEach((b) => {
      // ★ 第二个参数把"点的是哪个按钮"传出去：放宽动作必须与**当前**诊断对得上，
      //   否则会拿着上一帧的按钮去改这一帧的面板（竞态型假动作）。
      b.addEventListener("click", () => opts.onAction(b.getAttribute("data-act"), b));
    });
  }
  return el;
}

// ---------------------------------------------------------------------------
// 查询面板
// ---------------------------------------------------------------------------

function chkGroup(items, name, { checked = false, colorOf = null, limit = null, moreLabel = null } = {}) {
  const shown = limit ? items.slice(0, limit) : items;
  const rest = limit ? items.slice(limit) : [];
  const one = (v) => {
    const c = colorOf ? colorOf(v) : null;
    const dot = c ? `<span class="cdot" style="background:${c}"></span>` : "";
    return `<label class="chkline"><input type="checkbox" class="chk" name="${esc(name)}" value="${esc(v)}"${checked ? " checked" : ""}>${dot}<span>${esc(v)}</span></label>`;
  };
  let html = shown.map(one).join("");
  if (rest.length) {
    html += `<div class="more" hidden>${rest.map(one).join("")}</div>`
      + `<button type="button" class="toolbtn tiny" data-more="${esc(name)}">显示其余 ${rest.length} 项</button>`;
  }
  return html;
}

/**
 * 查询面板：**语义关系（type）与来源管线（kind）两组维度必须分列**（评审 §2-M1）。
 * ★ `type` = 24 种（与规范图实测集合相等）；`kind` = 115 种（默认全部 ⇒ 不填即不过滤）。
 */
export class QueryPanel {
  constructor(root, opts = {}) {
    this.root = root;
    this.opts = opts;
    this.onRun = opts.onRun || (() => {});
    this.onChanged = opts.onChanged || (() => {});
    this.onExpandToggle = opts.onExpandToggle || (() => {});
  }

  mount() {
    const r = this.root;
    if (!r) return this;

    const types = chkGroup(EDGE_TYPES, "ftype", { colorOf: (v) => EDGE_TYPE_COLOR[v] });
    const ntypes = chkGroup(NODE_TYPE_ORDER, "fntype", { limit: 10 });
    const subjects = SUBJECT_ORDER.map((s) =>
      `<label class="chkline"><input type="checkbox" class="chk" name="fsubject" value="${esc(s)}"><span>${esc(s)}</span></label>`).join("");

    r.innerHTML = `
      <div class="section">
        <h3>① 语义关系（边 type，24 种）</h3>
        <div class="hint">这是<b>关系语义</b>维度。与下一组的「来源/管线」是<b>两个不同维度</b>，不可混用。</div>
        <div class="chkbox" id="f-types">${types}</div>
      </div>
      <div class="section">
        <h3>② 来源 / 管线（边 kind，115 种；留空 = 全部）</h3>
        <div class="hint">按<b>数据来源与生成管线</b>过滤（如 real / chebi_composition / gnn_typed_verified）。
          ★ 把关系名填到这里会得到 0 条 —— 关系名属上一组。</div>
        <input type="text" id="f-kinds" placeholder="留空 = 全部；多个用逗号分隔">
        <div class="chips" id="f-kinds-hints"></div>
      </div>
      <div class="section">
        <h3>③ 学科</h3>
        <div class="chkbox">${subjects}</div>
      </div>
      <div class="section">
        <h3>④ 节点类型</h3>
        <div class="chkbox" id="f-ntypes">${ntypes}</div>
      </div>
      <div class="section">
        <h3>⑤ 可信范围</h3>
        <label class="chkline"><input type="radio" class="chk" name="ftrust" value="verified" checked><span>verified（≥ by_construction，默认）</span></label>
        <label class="chkline"><input type="radio" class="chk" name="ftrust" value="verified_strict"><span>verified_strict（≥ rule_checked，更严）</span></label>
        <label class="chkline"><input type="radio" class="chk" name="ftrust" value="all"><span>all（不过滤可信度）</span></label>
        <div class="hint" id="f-trust-note">切换档位后请点「生成子图」——被排除的边数会在状态行显示「已隐藏 N 条」。</div>
      </div>
      <div class="section">
        <h3>⑥ 置信度 ≥ <span id="conf-val">0.00</span></h3>
        <input type="range" id="f-conf" min="0" max="1" step="0.05" value="0">
        <label class="chkline"><input type="checkbox" class="chk" id="f-exclude-inferred"><span>排除推断边（explicit_or_inferred = inferred）</span></label>
        <label class="chkline"><input type="checkbox" class="chk" id="f-exclude-stale"><span>排除 stale 边</span></label>
        <div class="hint">★ 「排除 stale」<b>默认关闭</b>（有意）。命中条件是<b>两层任一</b>（Phase 45 起）：
          <br>① <b>字节忠实度</b>：已变更 + 无来源的模型/规则源（全图实测 <b>121</b> 条）；
          <br>② <b>验证依赖（A04）</b>：依赖已变更 或 <b>命题已否决</b>。
          <br><b>不</b>排除：「locator 未入库 ⇒ 需上游原始数据」的 47 055 条（它们<b>并未变更</b>，见报告 D1）；
          也不排除「待复核 needs_review」—— 那是本环境<b>不可判定</b>（缺的正是数据本身），
          若算作 stale 会把「排除 stale」退化成「几乎清空全图」（铁律 #70）。
          它始终出现在状态行「验证依赖」分列里。</div>
      </div>
      <div class="section">
        <h3>⑦ 预算</h3>
        <div class="grid2">
          <label class="minilbl">节点上限<input type="number" id="f-maxnodes" value="${BUDGET.nodes}" min="1" max="${HARD.nodes}"></label>
          <label class="minilbl">边上限<input type="number" id="f-maxedges" value="${BUDGET.edges}" min="1" max="${HARD.edges}"></label>
          <label class="minilbl">跳数上限<input type="number" id="f-hops" value="${BUDGET.hops}" min="1" max="${HARD.hops}"></label>
          <label class="minilbl">超时 ms<input type="number" id="f-deadline" value="${BUDGET.deadline_ms}" min="50" max="${HARD.deadline_ms}" step="50"></label>
        </div>
        <div class="hint">硬上限：${HARD.nodes} 节点 / ${HARD.edges} 边 / ${HARD.hops} 跳 / ${HARD.deadline_ms} ms。
          会话累计 <b>${SESSION_LIMIT.nodes} 节点 / ${SESSION_LIMIT.edges} 边</b>，达到即禁止继续展开（不会静默截断）。</div>
      </div>
      <div class="section">
        <button class="toolbtn primary" id="btn-run-filter">生成子图（按条件筛选）</button>
        <button class="toolbtn" id="btn-clear-filter">清空条件</button>
        <div class="hint" id="session-badge"></div>
      </div>`;

    // 事件绑定
    r.querySelectorAll("[data-more]").forEach((b) => {
      b.addEventListener("click", () => {
        const name = b.getAttribute("data-more");
        const box = b.parentElement.querySelector(".more");
        if (box) { box.hidden = !box.hidden; b.textContent = box.hidden ? b.textContent.replace("收起", "显示") : `收起`; }
      });
    });
    const conf = r.querySelector("#f-conf");
    if (conf) conf.addEventListener("input", () => {
      r.querySelector("#conf-val").textContent = Number(conf.value).toFixed(2);
    });
    const run = r.querySelector("#btn-run-filter");
    if (run) run.addEventListener("click", () => this.onRun(this.collect(), "filter"));
    const clr = r.querySelector("#btn-clear-filter");
    if (clr) clr.addEventListener("click", () => this.clear());
    r.addEventListener("change", () => this.onChanged(this.collect()));
    return this;
  }

  /** 收集 filter 参数（不含 mode；由调用方补 `mode: "filter"`） */
  collect() {
    const r = this.root;
    if (!r) return {};
    const vals = (name) => Array.from(r.querySelectorAll(`input[name="${name}"]:checked`)).map((x) => x.value);
    const kindsRaw = (r.querySelector("#f-kinds") || {}).value || "";
    const kinds = kindsRaw.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean);
    const num = (id, dflt) => {
      const el = r.querySelector("#" + id);
      const v = el ? parseInt(el.value, 10) : NaN;
      return Number.isFinite(v) ? v : dflt;
    };
    const trustEl = r.querySelector('input[name="ftrust"]:checked');
    return {
      mode: "filter",
      types: vals("ftype"),
      kinds,
      node_types: vals("fntype"),
      // ★ `/api/subgraph` 读的是**复数** `subjects`（`/api/search` 才用单数 `subject`）。
      //   旧实现返回单数 `subject` ⇒ 学科复选框实测**完全不生效**
      //   （`subject=化学` 被忽略：400 节点；`subjects=化学` 才生效：20 节点）。
      subjects: vals("fsubject"),
      trust: trustEl ? trustEl.value : "verified",
      min_confidence: (() => {
        const c = r.querySelector("#f-conf");
        const v = c ? parseFloat(c.value) : 0;
        return Number.isFinite(v) && v > 0 ? v : null;
      })(),
      max_nodes: num("f-maxnodes", BUDGET.nodes),
      max_edges: num("f-maxedges", BUDGET.edges),
      max_hops: num("f-hops", BUDGET.hops),
      deadline_ms: num("f-deadline", BUDGET.deadline_ms),
      // ★ 键名必须是后端认的 `exclude_model_inferred`（DOM id 保留 `f-exclude-inferred`）。
      //   旧实现返回 `exclude_inferred` ⇒ 与 `buildParams` 的旧键名"对上了"，
      //   但**两端都错**：后端读的是 `exclude_model_inferred` ⇒ 勾选框实测无效。
      exclude_model_inferred: !!(r.querySelector("#f-exclude-inferred") || {}).checked,
      exclude_stale: !!(r.querySelector("#f-exclude-stale") || {}).checked,
    };
  }

  clear() {
    const r = this.root;
    if (!r) return;
    r.querySelectorAll('input[type="checkbox"]').forEach((c) => { c.checked = false; });
    const t = r.querySelector('input[name="ftrust"][value="verified"]');
    if (t) t.checked = true;
    const k = r.querySelector("#f-kinds");
    if (k) k.value = "";
    const c = r.querySelector("#f-conf");
    if (c) { c.value = "0"; r.querySelector("#conf-val").textContent = "0.00"; }
    ["f-maxnodes", "f-maxedges", "f-hops", "f-deadline"].forEach((id) => {
      const el = r.querySelector("#" + id);
      if (el) el.value = String(id === "f-maxnodes" ? BUDGET.nodes : id === "f-maxedges" ? BUDGET.edges
        : id === "f-hops" ? BUDGET.hops : BUDGET.deadline_ms);
    });
    this.onChanged(this.collect());
  }

  /**
   * 按**诊断给出的**单个键把控件改到目标值（供「一键放宽」用）。
   *
   * 为什么必须改**控件**而不是直接改请求参数：面板是参数的**单一真相源**
   * （`collect()` 既是执行入口也是「执行参数」的唯一构造点）。绕过面板改请求，
   * 界面显示的条件与实际生效的条件就会分叉 —— 用户下一次手点「执行」，
   * 条件又悄悄回到旧的，表现为"点了没反应"。
   *
   * @param {{key:string, to:any}} patch 服务端 `empty_diagnosis.action`
   * @returns {string[]} **实际发生变化**的键；空数组 ⇒ 没有可改的东西，
   *   调用方**不得**重发请求（重发一次必然同样空的请求 = 假动作）
   */
  applyPatch(patch) {
    const r = this.root;
    if (!r || !patch || !patch.key) return [];
    const key = String(patch.key);
    const changed = [];
    const uncheckAll = (name) => {
      let n = 0;
      r.querySelectorAll(`input[name="${name}"]:checked`).forEach((c) => {
        c.checked = false; n += 1;
      });
      if (n) changed.push(key);
    };
    switch (key) {
      case "trust": {
        const t = r.querySelector(`input[name="ftrust"][value="${patch.to}"]`);
        if (t && !t.checked) { t.checked = true; changed.push(key); }
        break;
      }
      case "kinds": {
        const el = r.querySelector("#f-kinds");
        if (el && el.value !== "") { el.value = ""; changed.push(key); }
        break;
      }
      case "types": uncheckAll("ftype"); break;
      case "node_types": uncheckAll("fntype"); break;
      case "subjects": uncheckAll("fsubject"); break;
      case "exclude_stale": {
        const el = r.querySelector("#f-exclude-stale");
        if (el && el.checked) { el.checked = false; changed.push(key); }
        break;
      }
      case "exclude_model_inferred": {
        const el = r.querySelector("#f-exclude-inferred");
        if (el && el.checked) { el.checked = false; changed.push(key); }
        break;
      }
      case "min_confidence": {
        const c = r.querySelector("#f-conf");
        if (c && c.value !== "0") {
          c.value = "0";
          const lab = r.querySelector("#conf-val");
          if (lab) lab.textContent = "0.00";
          changed.push(key);
        }
        break;
      }
      case "hops": {
        const el = r.querySelector("#f-hops");
        if (el && patch.to != null && String(el.value) !== String(patch.to)) {
          el.value = String(patch.to);
          changed.push(key);
        }
        break;
      }
      default:
        // 不认识的键 ⇒ 什么都不改。**不猜**（猜错会改到用户没同意的维度）。
        break;
    }
    if (changed.length) this.onChanged(this.collect());
    return changed;
  }

  /** 截断横幅的「提高预算」动作 */
  raiseBudget(factor) {
    const r = this.root;
    if (!r) return;
    const mn = r.querySelector("#f-maxnodes");
    const me = r.querySelector("#f-maxedges");
    if (mn) mn.value = String(Math.min(HARD.nodes, Math.max(1, (parseInt(mn.value, 10) || BUDGET.nodes) * factor)));
    if (me) me.value = String(Math.min(HARD.edges, Math.max(1, (parseInt(me.value, 10) || BUDGET.edges) * factor)));
    this.onChanged(this.collect());
  }

  /** 用当前子图里出现的 kind 生成快捷 chips（避免把 115 项全铺开） */
  setKindHints(list) {
    const el = this.root && this.root.querySelector("#f-kinds-hints");
    if (!el) return;
    const arr = (list || []).slice(0, 14);
    el.innerHTML = arr.length
      ? '<span class="note">本子图出现的来源/管线：</span>'
        + arr.map((k) => `<button type="button" class="chip" data-kind="${esc(k)}">${esc(k)}</button>`).join("")
      : "";
    el.querySelectorAll("[data-kind]").forEach((b) => {
      b.addEventListener("click", () => {
        const inp = this.root.querySelector("#f-kinds");
        if (!inp) return;
        const cur = inp.value.split(/[,，\s]+/).filter(Boolean);
        const v = b.getAttribute("data-kind");
        if (cur.indexOf(v) < 0) cur.push(v);
        inp.value = cur.join(",");
        this.onChanged(this.collect());
      });
    });
  }

  setSession(budget) {
    const el = this.root && this.root.querySelector("#session-badge");
    if (!el) return;
    el.innerHTML = `<span class="${budget.full ? "warn" : ""}">${esc(sessionText(budget))}</span>`;
  }
}

/** 全局搜索（带简单防抖 + 候选列表） */
export function bindSearch(inputEl, listEl, api, onPick, { debounceMs = 180 } = {}) {
  if (!inputEl || !listEl) return { close() {} };
  let timer = null;
  let seq = 0;

  const close = () => { listEl.innerHTML = ""; listEl.hidden = true; };

  const run = async (q) => {
    const mine = ++seq;
    const s = (q == null ? "" : String(q)).trim();
    // ★ 空 ⇒ 静默关闭；**过短 ⇒ 如实提示且不发请求**（后端 `queries.MIN_QUERY_LEN`
    //   会以 400 拒绝，前端不该去撞它、更不该把 400 渲染成"没有匹配"）。
    if (!s) { close(); return; }
    if (s.length < MIN_QUERY_LEN) {
      listEl.hidden = false;
      listEl.innerHTML = `<div class="sr-empty">至少输入 ${MIN_QUERY_LEN} 个字符（与后端约束同源）</div>`;
      return;
    }
    const env = await api.search(s, { limit: 20 });
    if (mine !== seq) return;                       // 过期响应丢弃（防竞态覆盖）
    if (env.status === "error") {
      // ★ 铁律 #67：**「空」与「失败」必须分列** —— 400/断连不得渲染成"没有匹配"
      //   （那会把"请求根本没成功"伪装成"查了但没结果"，与 Phase 47 的误判同族）。
      listEl.hidden = false;
      listEl.innerHTML = `<div class="sr-empty">查询失败（<code>${esc(env.error_code || "error")}</code>）：`
        + `${esc(env.reason || "无原因")}</div>`;
      return;
    }
    if (env.status === "no_result" || !env.hits.length) {
      listEl.hidden = false;
      listEl.innerHTML = `<div class="sr-empty">没有匹配（no_result）。可缩短关键词、或去掉学科限定。</div>`;
      return;
    }
    listEl.hidden = false;
    // ★ 截断必须显式披露（铁律 #66/#67）：搜索被 `limit` 截过时写「显示前 N 条 / 共 M 条」。
    //   实测 `co2` ⇒ `hits=20 / total_matches=81 / truncated=true`；旧实现把后两个字段
    //   一起丢掉 ⇒ **UI 层把后端已经说清楚的截断又静默回吞了一次**。
    const total = env.total_matches;
    const capped = env.truncated === true || (total != null && total > env.hits.length);
    const head = capped
      ? `<div class="sr-head">显示前 ${env.hits.length} 条 / 共 ${total == null ? "未知" : total} 条（已截断）—— 可缩短关键词</div>`
      : `<div class="sr-head">共 ${total == null ? env.hits.length : total} 条命中</div>`;
    listEl.innerHTML = head + env.hits.map((h) =>
      `<div class="sr-item" data-id="${esc(h.id)}" tabindex="0">
         <div class="sr-label">${esc(h.label || h.id)}</div>
         <div class="sr-meta"><span class="badge">${esc(h.type || "?")}</span><span class="badge">${esc(h.subject || "?")}</span><code>${esc(h.id)}</code>
         <span class="pill">score ${esc(h.score)}</span></div>
       </div>`).join("");
    listEl.querySelectorAll(".sr-item").forEach((it) => {
      const pick = () => { onPick(it.getAttribute("data-id")); close(); };
      it.addEventListener("click", pick);
      it.addEventListener("keydown", (e) => { if (e.key === "Enter") pick(); });
    });
  };

  inputEl.addEventListener("input", () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => run(inputEl.value), debounceMs);
  });
  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
    if (e.key === "Enter") { if (timer) clearTimeout(timer); run(inputEl.value); }
  });
  return { close, run };
}
