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
 * STTP · 空结果解释（F12 / D69）—— 纯逻辑 + HTML 片段，可被 node 直接断言
 *
 * 三条纪律：
 *  1. **不猜**：只渲染服务端 `stats.empty_diagnosis` 里**已有**的字段。字段缺失 ⇒
 *     返回 `null`，调用方退回通用文案。「宁可不说，也不编一个原因」——
 *     编出来的原因会让用户按错误的方向改条件（D64 的同族）。
 *  2. **不重复中文**：条件名（「可信档」等）与动作名由服务端给
 *     （`counts[].what` / `action.label`），前端只排版。两张表各维护一份 ⇒
 *     迟早一边改了一边没改（#88：同一事实两处记载，静默走样）。
 *  3. **动作必须可执行、且有边界**：`action` 只说「改哪个键、改成什么」，
 *     真正改面板控件的是 `panels.applyPatch()`（单一真相源：面板 → 请求）。
 *     没有按钮时必须在界面上说清**为什么没有** —— 「放宽」若没有终点，
 *     就退化成「永远能搜到东西」的假动作（铁律 #30）。
 */

/**
 * 本地 `esc`（与 `panels.esc` 逐字等价）。
 * ★ 为什么不 import `panels.esc`：`panels.js` 会 import 本模块（渲染横幅），
 *   互相 import 虽然靠函数声明提升能跑，但那条依赖关系**看不见**、改起来容易崩。
 *   一致性由 `frontend_core_check.mjs` 的 F45s 用刁钻输入逐字比对守住（测试代替耦合）。
 */
export function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** 诊断里**必须**有的字段（缺一个就不渲染：半份诊断比没有更危险）。 */
const REQUIRED = ["why", "scope"];

/**
 * 从响应信封里取出可渲染的诊断；不合法 ⇒ `null`。
 * @param {object} env `/api/subgraph` 的响应信封
 * @returns {object|null}
 */
export function emptyDiagOf(env) {
  const raw = env && env.stats ? env.stats.empty_diagnosis : null;
  if (!raw || typeof raw !== "object") return null;
  if (REQUIRED.some((k) => !raw[k])) return null;
  const counts = Array.isArray(raw.counts)
    ? raw.counts.filter((x) => x && typeof x === "object" && x.key && x.what
                               && Number.isFinite(Number(x.n)))
    : [];
  const a = raw.action && typeof raw.action === "object" ? raw.action : null;
  // `to` 允许是 null（"清空某条件"就是 null）⇒ 必须用 hasOwnProperty 判存在，
  // 否则 `{key:"kinds", to:null}` 会被当成"没有目标值"而丢弃（一类安静的错位）。
  const action = (a && a.key && a.label && Object.prototype.hasOwnProperty.call(a, "to"))
    ? { key: a.key, to: a.to, label: a.label,
        gain: Number.isFinite(Number(a.gain)) ? Number(a.gain) : null }
    : null;
  return {
    ...raw,
    counts,
    action,
    title: typeof raw.title === "string" ? raw.title : "",
    text: typeof raw.text === "string" ? raw.text : "",
  };
}

/** 诊断的「一句边界」：没有按钮时**必须**说清为什么没有。 */
export function boundaryText(diag) {
  if (!diag) return "";
  if (diag.why === "missing_seed") {
    return "边界：名字不存在 —— 任何条件放宽都不会让它出现，所以这里没有放宽按钮。";
  }
  if (diag.why === "isolated") {
    return "边界：不加任何条件时也是 0 条（全图复算 degree=0）⇒ 放宽必然无效，故不给按钮。";
  }
  if (diag.why === "hops_zero") {
    return "边界：跳数为 0 时任何边都不会被展开，唯一有效动作是增加跳数。";
  }
  if (diag.why === "undecided") {
    return "边界：诊断没算完 ⇒ 按纪律不给建议（把「没算完」说成「没有」比不给建议更糟）。";
  }
  if (diag.why === "unreachable") {
    return "边界：诊断口径与候选集合不同源，这是缺陷，不是「图里没有」；已如实登记。";
  }
  if (diag.counterfactual === false) {
    return "边界：本模式不做反事实计数（路径连通性不由单条件决定）⇒ 只给真实可执行的步骤，"
      + "不承诺放宽后一定有结果。";
  }
  if (diag.why === "filtered") {
    return "边界：这是唯一挡住它的条件（其余条件都保持不动）；服务端已按同一口径复算放宽后的条数。";
  }
  if (diag.why === "combined") {
    return "边界：单放宽任一项都不够 —— 需要按顺序逐步放宽，每一步都会重新诊断。";
  }
  return "";
}

/** 「判定依据」行：扫描范围必须露出来（「0 条」与「没扫过」在读数上不可区分）。 */
export function scopeText(diag) {
  if (!diag) return "";
  if (diag.counterfactual === false) return "";
  if (diag.why === "undecided") {
    const n = Number(diag.scanned);
    return Number.isFinite(n) ? `判定依据：候选 ${n} 条边（未算完）` : "判定依据：未算完";
  }
  const n = Number(diag.scanned);
  if (!Number.isFinite(n)) return "";
  const deg = Number(diag.degree);
  // 只在两者**不同**时才报 degree：相同的话「扫描 N 条，其中不加条件 N 条」是同义反复，
  // 读者会以为藏了什么（而且它确实也没提供信息）。
  const tail = (Number.isFinite(deg) && deg !== n)
    ? `，其中不加任何条件时 ${deg} 条` : "";
  return `判定依据：扫描 ${n} 条候选边${tail}`;
}

/**
 * 渲染空结果解释块（HTML 片段；不含外层 banner 容器）。
 * @param {object} diag `emptyDiagOf()` 的返回
 * @param {{withOverview?: boolean}} [opts]
 */
export function emptyDiagHTML(diag, opts = {}) {
  if (!diag) return "";
  const head = diag.title ? esc(diag.title) : `空结果（${esc(diag.why)}）`;
  const text = diag.text ? `<div class="note">${esc(diag.text)}</div>` : "";
  const scope = scopeText(diag);
  const scopeLine = scope ? `<div class="note ed-scope">${esc(scope)}</div>` : "";

  const rows = diag.counts.map((c) => {
    const n = Number(c.n);
    const verdict = n > 0 ? `只放宽这一项 → ${n} 条边`
      : "只放宽这一项 → 仍 0 条（无效）";
    const cls = n > 0 ? "ok" : "dead";
    return `<li class="ed-row ${cls}"><span class="ed-k">${esc(c.what)}</span>`
      + `<span class="ed-v">${esc(verdict)}</span></li>`;
  }).join("");
  const list = rows ? `<ul class="ed-cnt">${rows}</ul>` : "";

  const bound = boundaryText(diag);
  const boundLine = bound ? `<div class="note ed-bound">${esc(bound)}</div>` : "";

  const acts = [];
  if (diag.action) {
    acts.push(`<button class="toolbtn primary" data-act="relax-diag"`
      + ` data-relax-key="${esc(diag.action.key)}">${esc(diag.action.label)}</button>`);
  }
  if (opts.withOverview !== false) {
    acts.push('<button class="toolbtn" data-act="overview">改看概览</button>');
  }
  const actLine = acts.length ? `<div class="bact">${acts.join("")}</div>` : "";
  return `<div class="bt">${head}</div>${text}${scopeLine}${list}${boundLine}${actLine}`;
}
