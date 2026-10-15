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
 * STTP · 任务式入口（Phase 49）—— 首屏「问题 → 候选 → 确认实体 → 生成局部图」
 *
 * 设计依据：《标准用户使用与演示流程》§3（RC1 推荐首页）与外部评审 §7 阶段 B/C。
 *
 * 为什么要有这一层（而不是继续用「图浏览器」当首屏）：
 *   旧首屏 = 左侧筛选面板 + 空画布 + 顶部搜索框。用户打开后**不知道该输入什么**，
 *   而搜索框又直接吃自然语言 ⇒ 歧义立刻污染结果（"熵" 到底是 `TH:pq:entropy`
 *   还是 `PB:pq:entropy`？两者在本快照下**一个有路径、一个没有**）。
 *   本模块把这段歧义**显式化**：先给候选、由人确认，再查图。
 *
 * ★ 职责边界：本文件只做「渲染 + 收集意图」。
 *   一切**可被判定的真值**（卡片数据、范围钳制、请求构造、候选截断）都在
 *   `core.js` 的纯函数里，由 `node` 直接断言（铁律 #26「可复算即证据」）。
 *   这样 DOM 层再出错，也不会把"参数构造错了"伪装成"图谱没有数据"。
 */

import {
  TASK_CARDS, TASK_SCOPE_DEFAULT, TRUST_LEVELS, normalizeScope,
  taskRequest, taskBlockedReason, takeCandidates, MIN_QUERY_LEN,
} from "./core.js";
import { esc } from "./panels.js";

/** 候选列表展示条数上限（外部评审 §7-C 建议「最多 5 个候选」）。 */
export const CANDIDATE_LIMIT = 5;

/** 纯函数：卡片区 HTML（可被 node 断言，不需要 DOM）。 */
export function renderCardsHTML(cards = TASK_CARDS) {
  return cards.map((c) => `
    <button type="button" class="tcard" data-card="${esc(c.id)}"
            title="${esc(c.note)}">
      <span class="tcard-q">${esc(c.question)}</span>
      <span class="tcard-h">${esc(c.mode === "path" ? "两点关系路径" : "局部关系图")}</span>
    </button>`).join("");
}

/** 纯函数：候选列表 HTML。`which` ∈ {main, second}。 */
export function renderCandidatesHTML(list, which, selectedId = null) {
  const { items, shown, total, truncated, status, error_code: code, reason } = list;
  if (status === "error") {
    // ★ 铁律 #67：**「空」与「失败」分列** —— 400 / 断连不得渲染成「没有匹配」
    return `<div class="cand-head" data-which="${esc(which)}">候选实体</div>
      <div class="cand-empty err">查询失败（<code>${esc(code || "error")}</code>）：${esc(reason || "无原因")}</div>`;
  }
  if (!items.length) {
    return `<div class="cand-head" data-which="${esc(which)}">候选实体</div>
      <div class="cand-empty">没有匹配（no_result）。可换关键词，或改用更常见的英文名 / 公式。</div>`;
  }
  const head = truncated
    ? `显示前 ${shown} 条 / 共 ${total} 条（已截断）—— 可缩短关键词`
    : `共 ${total} 条命中 —— 请确认其中一个`;
  return `<div class="cand-head" data-which="${esc(which)}">候选实体 · ${esc(head)}
      <span class="note">（自然语言有歧义时，同名实体来自不同来源，结论会不同）</span></div>
    <div class="cand-list">`
    + items.map((h) => `
      <div class="cand-item${selectedId && h.id === selectedId ? " sel" : ""}"
           data-which="${esc(which)}" data-id="${esc(h.id)}" tabindex="0" role="button"
           aria-pressed="${selectedId && h.id === selectedId ? "true" : "false"}">
        <span class="cand-label">${esc(h.label || h.id)}</span>
        <span class="badge">${esc(h.type || "?")}</span>
        <span class="badge">${esc(h.subject || "?")}</span>
        <code>${esc(h.id)}</code>
        <span class="pill">score ${esc(h.score)}</span>
      </div>`).join("")
    + "</div>";
}

const $ = (id) => document.getElementById(id);

export class TaskEntry {
  /**
   * @param {HTMLElement} root `#task-entry`
   * @param {{api:object, onRun:Function, debounceMs?:number}} opts
   *   `onRun({params, card, state})` —— 由编排层执行查询（本模块**不**直接发图查询）
   */
  constructor(root, opts = {}) {
    this.root = root;
    this.api = opts.api || null;
    this.onRun = opts.onRun || (() => {});
    this.debounceMs = opts.debounceMs == null ? 180 : opts.debounceMs;
    this.state = {
      card: null, query: "", second: "",
      seed: null, seedLabel: "", seed2: null, seed2Label: "",
      scope: { ...TASK_SCOPE_DEFAULT },
      lastMain: null, lastSecond: null,
    };
    this._timer = null;
    this._seq = 0;
  }

  get isOpen() { return !!(this.root && !this.root.hidden); }

  mount() {
    const r = this.root;
    if (!r) return this;
    r.innerHTML = `
      <div class="tp-scrim"></div>
      <section class="tp-panel" role="region" aria-label="任务入口">
        <div class="tp-head">
          <h2>先说你要看什么</h2>
          <p class="tp-sub">系统先给出<b>候选实体</b>，<b>你确认之后</b>才去查图 ——
            避免自然语言歧义直接污染结果。生成图之后若想改，随时点右上角「← 修改查询」回来。</p>
        </div>

        <div class="tp-q">
          <label class="minilbl" for="task-q">问题或关键词<span class="note">（也可直接点下面的示例卡）</span></label>
          <div class="tp-row">
            <input type="text" id="task-q" placeholder="例如：吉布斯自由能 / entropy / F=ma / 电负性"
                   autocomplete="off" aria-label="问题或关键词">
            <button class="toolbtn" id="task-search" type="button">查候选</button>
          </div>
        </div>

        <div class="tp-q tp-q2" id="task-second-wrap" hidden>
          <label class="minilbl" for="task-q2">第二个实体<span class="note">（本卡查两点之间的路径，必须填）</span></label>
          <input type="text" id="task-q2" placeholder="例如：entropy" autocomplete="off" aria-label="第二个实体">
        </div>

        <div class="tp-cards" id="task-cards">${renderCardsHTML()}</div>

        <div class="tp-cands" id="task-cands" hidden></div>

        <div class="tp-scope">
          <h3>局部图范围<span class="note">（默认 40 节点 / 70 边 / 2 跳 / 10 秒；超预算会如实报告截断）</span></h3>
          <div class="tp-scope-grid">
            <label class="minilbl">跳数<input type="number" id="task-hops" min="1" max="5" value="${TASK_SCOPE_DEFAULT.hops}"></label>
            <label class="minilbl">节点上限<input type="number" id="task-nodes" min="1" value="${TASK_SCOPE_DEFAULT.max_nodes}"></label>
            <label class="minilbl">边上限<input type="number" id="task-edges" min="1" value="${TASK_SCOPE_DEFAULT.max_edges}"></label>
            <label class="minilbl">超时 ms<input type="number" id="task-deadline" min="50" step="50" value="${TASK_SCOPE_DEFAULT.deadline_ms}"></label>
            <label class="minilbl">可信档
              <select id="task-trust">${TRUST_LEVELS.map((t) =>
                `<option value="${esc(t)}"${t === TASK_SCOPE_DEFAULT.trust ? " selected" : ""}>${esc(t)}</option>`).join("")}</select>
            </label>
          </div>
        </div>

        <div class="tp-run">
          <button class="toolbtn primary" id="task-run" type="button" disabled>生成子图</button>
          <span class="pill" id="task-run-note"></span>
        </div>
        <div class="note" id="task-status"></div>
      </section>`;

    $("task-cards").addEventListener("click", (e) => {
      const b = e.target.closest("[data-card]");
      if (b) this.applyCard(b.getAttribute("data-card"));
    });
    $("task-q").addEventListener("input", (e) => {
      this.state.query = e.target.value;
      this.state.seed = null; this.state.seedLabel = "";
      this._debounced("main");
      this._refreshRun();
    });
    $("task-q").addEventListener("keydown", (e) => { if (e.key === "Enter") this._run("main"); });
    $("task-q2").addEventListener("input", (e) => {
      this.state.second = e.target.value;
      this.state.seed2 = null; this.state.seed2Label = "";
      this._debounced("second");
      this._refreshRun();
    });
    $("task-q2").addEventListener("keydown", (e) => { if (e.key === "Enter") this._run("second"); });
    $("task-search").addEventListener("click", () => this._run("main"));
    $("task-run").addEventListener("click", () => this.run());
    r.querySelectorAll(".tp-scope input, .tp-scope select").forEach((el) => {
      el.addEventListener("change", () => { this._readScope(); this._refreshRun(); });
    });
    return this;
  }

  /** 显示入口（可在生成图之后再次打开）。`prefill` 用于回填上次的输入与已确认实体。 */
  open(prefill = {}) {
    const r = this.root;
    if (!r) return this;
    if (prefill.query != null) this.state.query = String(prefill.query);
    if (prefill.second != null) this.state.second = String(prefill.second);
    if (prefill.seed !== undefined) this.state.seed = prefill.seed;
    if (prefill.seedLabel !== undefined) this.state.seedLabel = prefill.seedLabel || "";
    if (prefill.seed2 !== undefined) this.state.seed2 = prefill.seed2;
    if (prefill.seed2Label !== undefined) this.state.seed2Label = prefill.seed2Label || "";
    if (prefill.cardId !== undefined) {
      this.state.card = TASK_CARDS.filter((c) => c.id === prefill.cardId)[0] || null;
    }
    if (prefill.scope) this.state.scope = normalizeScope(prefill.scope);

    r.hidden = false;
    document.body.classList.add("task-open");
    this._fillForm();
    this._applyCardVisibility();
    // 回填后若已有输入 ⇒ 自动重搜一次，让用户看到同一批候选（而不是空白面板）
    if (this.state.query && this.state.query.trim().length >= MIN_QUERY_LEN) this._run("main");
    if (this.state.second && this.state.second.trim().length >= MIN_QUERY_LEN) this._run("second");
    this._refreshRun();
    const q = $("task-q");
    if (q) q.focus();
    return this;
  }

  close() {
    if (this.root) this.root.hidden = true;
    if (typeof document !== "undefined") document.body.classList.remove("task-open");
    return this;
  }

  _fillForm() {
    const q = $("task-q"); if (q) q.value = this.state.query;
    const q2 = $("task-q2"); if (q2) q2.value = this.state.second;
    const sc = this.state.scope;
    const set = (id, v) => { const el = $(id); if (el) el.value = String(v); };
    set("task-hops", sc.hops); set("task-nodes", sc.max_nodes);
    set("task-edges", sc.max_edges); set("task-deadline", sc.deadline_ms);
    const t = $("task-trust"); if (t) t.value = sc.trust;
  }

  _readScope() {
    const num = (id, dflt) => {
      const el = $(id); const v = el ? parseInt(el.value, 10) : NaN;
      return Number.isFinite(v) ? v : dflt;
    };
    const t = $("task-trust");
    this.state.scope = normalizeScope({
      hops: num("task-hops", TASK_SCOPE_DEFAULT.hops),
      max_nodes: num("task-nodes", TASK_SCOPE_DEFAULT.max_nodes),
      max_edges: num("task-edges", TASK_SCOPE_DEFAULT.max_edges),
      deadline_ms: num("task-deadline", TASK_SCOPE_DEFAULT.deadline_ms),
      trust: t ? t.value : TASK_SCOPE_DEFAULT.trust,
    });
    this._fillForm();   // 钳制结果回写给用户看（不静默修正）
  }

  /** 卡片：预填输入 + 设定期望关系类型 + 决定是否需要第二实体。 */
  applyCard(cardId) {
    const card = TASK_CARDS.filter((c) => c.id === cardId)[0];
    if (!card) return this;
    this.state.card = card;
    this.state.query = card.start || "";
    this.state.second = card.end || "";
    this.state.seed = null; this.state.seedLabel = "";
    this.state.seed2 = null; this.state.seed2Label = "";
    this._fillForm();
    this._applyCardVisibility();
    this._setStatus(`已载入示例卡：${card.question}　${card.note}`);
    this._run("main");
    if (this.state.second) this._run("second");
    this._refreshRun();
    return this;
  }

  _applyCardVisibility() {
    const isPath = !!(this.state.card && this.state.card.mode === "path");
    const w = $("task-second-wrap");
    if (w) w.hidden = !isPath;
    const t = $("task-trust");
    // 路径查询在 `verified` 档下常常「确实没有路径」；卡片自带的关系类型过滤只在 ego 用
    if (t && TASK_CARDS.length) t.title = "verified = 只保留已构建/已校验的边；all = 不过滤可信度";
  }

  _debounced(which) {
    if (this._timer) clearTimeout(this._timer);
    this._timer = setTimeout(() => this._run(which), this.debounceMs);
  }

  async _run(which) {
    const raw = which === "second" ? this.state.second : this.state.query;
    const s = (raw == null ? "" : String(raw)).trim();
    const box = $("task-cands");
    if (!box) return;
    if (!s) {
      this._setCandidates(which, { items: [], shown: 0, total: 0, truncated: false,
                                   status: "no_result", error_code: null, reason: "" });
      this._setStatus("");
      return;
    }
    // ★ 过短不发请求（与后端 `queries.MIN_QUERY_LEN` 同源，跨层断言 X5b）：
    //   明知会被 400 拒绝，就不该去撞它、更不该把 400 渲染成「没有匹配」。
    if (s.length < MIN_QUERY_LEN) {
      this._setCandidates(which, {
        items: [], shown: 0, total: 0, truncated: false, status: "no_result",
        error_code: null, reason: "",
      });
      this._setStatus(`至少输入 ${MIN_QUERY_LEN} 个字符（与后端约束同源），当前 ${s.length} 个 ⇒ 不发请求。`);
      return;
    }
    if (!this.api) { this._setStatus("查询客户端未就绪"); return; }
    const mine = ++this._seq;
    this._setStatus(`正在查「${s}」的候选…`);
    const env = await this.api.search(s, { limit: CANDIDATE_LIMIT * 4 });
    if (mine !== this._seq) return;                 // 过期响应丢弃（防竞态覆盖）
    const list = takeCandidates(env, CANDIDATE_LIMIT);
    this._setCandidates(which, list);
    this._setStatus(list.status === "error"
      ? `查询失败：${list.error_code || "error"} —— ${list.reason || "无原因"}`
      : `${s} ⇒ ${list.total} 条候选（显示前 ${list.shown} 条），请点一个确认。`);
  }

  _setCandidates(which, list) {
    const box = $("task-cands");
    if (!box) return;
    const sel = which === "second" ? this.state.seed2 : this.state.seed;
    if (which === "second") this.state.lastSecond = list; else this.state.lastMain = list;
    const html = renderCandidatesHTML(list, which, sel);
    if (which === "second") {
      // 第二实体的候选插在主候选之后，避免覆盖
      let sec = box.querySelector('[data-slot="second"]');
      if (!sec) {
        sec = document.createElement("div");
        sec.setAttribute("data-slot", "second");
        box.appendChild(sec);
      }
      sec.innerHTML = html;
    } else {
      let main = box.querySelector('[data-slot="main"]');
      if (!main) {
        main = document.createElement("div");
        main.setAttribute("data-slot", "main");
        box.insertBefore(main, box.firstChild);
      }
      main.innerHTML = html;
    }
    box.hidden = false;
    box.querySelectorAll(".cand-item").forEach((it) => {
      const pick = () => this._pick(it.getAttribute("data-which"), it.getAttribute("data-id"));
      it.addEventListener("click", pick);
      it.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") pick(); });
    });
  }

  _pick(which, id) {
    const box = $("task-cands");
    const list = which === "second" ? this.state.lastSecond : this.state.lastMain;
    const hit = (list && list.items || []).filter((h) => h.id === id)[0];
    const label = hit ? (hit.label || hit.id) : id;
    if (which === "second") { this.state.seed2 = id; this.state.seed2Label = label; }
    else { this.state.seed = id; this.state.seedLabel = label; }
    if (box) {
      const slot = box.querySelector(which === "second" ? '[data-slot="second"]' : '[data-slot="main"]');
      if (slot) slot.innerHTML = renderCandidatesHTML(list, which, id);
      box.querySelectorAll(".cand-item").forEach((it) => {
        if (it.getAttribute("data-which") !== which) return;
        const pick = () => this._pick(it.getAttribute("data-which"), it.getAttribute("data-id"));
        it.addEventListener("click", pick);
        it.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") pick(); });
      });
    }
    this._setStatus(`已确认${which === "second" ? "第二个" : ""}实体：${label}（${id}）`);
    this._refreshRun();
  }

  /** 按钮可用性 + **禁用原因**（铁律 #63：拒绝分支显式，不做"点了没反应"）。 */
  _refreshRun() {
    this._readScopeQuiet();
    const blocked = taskBlockedReason(this.state.card, this.state);
    const btn = $("task-run");
    if (btn) btn.disabled = !!blocked;
    const note = $("task-run-note");
    if (note) {
      note.textContent = blocked || (this.state.seed
        ? `将查询 ${this.state.card && this.state.card.mode === "path"
            ? `${this.state.seed} → ${this.state.seed2 || "?"}` : this.state.seed}`
        : "");
    }
    return blocked;
  }

  _readScopeQuiet() {
    const num = (id, dflt) => {
      const el = $(id); const v = el ? parseInt(el.value, 10) : NaN;
      return Number.isFinite(v) ? v : dflt;
    };
    const t = $("task-trust");
    this.state.scope = normalizeScope({
      hops: num("task-hops", TASK_SCOPE_DEFAULT.hops),
      max_nodes: num("task-nodes", TASK_SCOPE_DEFAULT.max_nodes),
      max_edges: num("task-edges", TASK_SCOPE_DEFAULT.max_edges),
      deadline_ms: num("task-deadline", TASK_SCOPE_DEFAULT.deadline_ms),
      trust: t ? t.value : TASK_SCOPE_DEFAULT.trust,
    });
  }

  /** 用户点「生成子图」。**参数构造失败 ⇒ 明确说明缺什么**，不发必然被拒的请求。 */
  run() {
    const blocked = this._refreshRun();
    if (blocked) { this._setStatus(blocked); return null; }
    const params = taskRequest(this.state.card, {
      seed: this.state.seed, second: this.state.seed2, scope: this.state.scope,
    });
    if (!params) {   // 双保险：taskRequest 的 fail-closed 契约
      this._setStatus("查询条件不完整，未发出请求（这是刻意的：不发明知会被后端拒绝的请求）。");
      return null;
    }
    this.close();
    this.onRun({ params, card: this.state.card, state: this.state });
    return params;
  }

  _setStatus(msg) {
    const el = $("task-status");
    if (el) el.textContent = msg || "";
  }
}
