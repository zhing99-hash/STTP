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
 * STTP · 证据层 —— 节点详情 / 边证据卡片 / `why_shown` 固定行 / stale 徽标
 *
 * ★ 铁律 #59：证据里 **`impl` = 谁验的**，**`src` = 读的是哪个源/哪一版**，
 *   两者**必须分列显示**，不得合并成"来源"一个笼统字段。
 * ★ `why_shown` 在每个节点/边抽屉**顶部固定一行**（方案 §6.2-6）。
 */

import { WHY_SHOWN_LABEL } from "./core.js";
import { verificationBadge, EVIDENCE_FIELDS, edgeTypeColor, eoiStyle,
         fidelityBadge } from "./style.js";

export function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** `why_shown` 固定行（未识别值**显式暴露**，不静默吞） */
export function whyRow(why) {
  if (!why) {
    return '<div class="whyyrow unknown"><span class="lbl">为什么显示</span><span class="val">（未提供 why_shown）</span></div>';
  }
  const label = WHY_SHOWN_LABEL[why];
  const cls = label ? "whyyrow" : "whyyrow unknown";
  return `<div class="${cls}"><span class="lbl">为什么显示</span><span class="val">${esc(label || why)}`
       + (label ? ` <span class="pill">${esc(why)}</span>` : ' <span class="pill">未识别的 why_shown 值</span>')
       + "</span></div>";
}

function badgeOf(level) {
  const b = verificationBadge(level);
  return `<span class="vbadge" style="border-color:${b.color};color:${b.color}">${esc(b.label)}`
       + (level ? ` <span class="pill">${esc(level)}</span>` : "") + "</span>";
}

function kv(lbl, val) {
  return `<div class="detail-block"><div class="lbl">${esc(lbl)}</div><div class="val">${val}</div></div>`;
}

/** 渲染一个节点到右栏（保留 MathJax 公式渲染、ghost 标注、置信度） */
export function renderNodeDetail(el, node, opts = {}) {
  if (!el) return;
  if (!node) {
    el.innerHTML = '<div class="empty">未选择节点。左栏可搜索实体、或点「取选中」设为路径端点。</div>';
    return;
  }
  const n = node.raw || node;
  const attrs = n.attrs || {};
  const parts = [];
  parts.push(whyRow(n.why_shown));
  parts.push(`<div class="detail-title">${esc(n.label || n.id)}</div>`);
  parts.push(`<div><span class="badge">${esc(n.type || "Unknown")}</span>`
           + `<span class="badge">${esc(n.subject || "跨学科")}</span>`
           + (opts.ghost ? '<span class="badge">外部节点</span>' : "")
           + `</div>`);
  parts.push(kv("id", `<code>${esc(n.id)}</code>`));
  if (n.domain) parts.push(kv("domain", esc(n.domain)));
  if (typeof n.confidence === "number") parts.push(kv("置信度", esc(n.confidence)));
  const lv = attrs.verification_level || n.verification_level;
  if (lv) parts.push(kv("验证档", badgeOf(lv)));

  if (n.formula || attrs.latex) {
    parts.push(kv("公式（LaTeX）",
      `<div id="mj" class="latex">\\[${esc(n.formula || attrs.latex)}\\]</div>`
      + `<button class="toolbtn" id="btn-copy-latex" data-tex="${esc(n.formula || attrs.latex)}">复制 LaTeX</button>`));
  }
  if (attrs.informal) parts.push(kv("说明", esc(attrs.informal)));
  if (attrs.source) parts.push(kv("来源（节点属性）", `<code>${esc(attrs.source)}</code>`));

  el.innerHTML = parts.join("");

  // MathJax（若可用；不可用则保持原始 LaTeX 可读）
  const mj = el.querySelector("#mj");
  if (mj && typeof window !== "undefined" && window.MathJax && window.MathJax.typesetPromise) {
    try { window.MathJax.typesetPromise([mj]).catch(() => {}); } catch (e) { /* 忽略 */ }
  }
  const cp = el.querySelector("#btn-copy-latex");
  if (cp) {
    cp.addEventListener("click", () => {
      const tex = cp.getAttribute("data-tex") || "";
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(tex).then(
          () => { cp.textContent = "已复制"; setTimeout(() => { cp.textContent = "复制 LaTeX"; }, 1200); },
          () => { cp.textContent = "复制失败（请手动选中）"; });
      } else {
        cp.textContent = "浏览器不支持剪贴板";
      }
    });
  }
  return el;
}

/** 渲染一条边到右栏（**证据卡片核心**） */
export function renderEdgeDetail(el, edge) {
  if (!el) return;
  if (!edge) {
    el.innerHTML = '<div class="empty">未选择边。点画布上的任一条边可查看它的证据与来源。</div>';
    return;
  }
  const d = edge.data ? edge.data() : edge;
  const raw = (d.raw && d.raw.evidence) ? d.raw : (edge.evidence ? edge : null);
  const evs = (raw && raw.evidence) || d.evidence || [];
  const eoi = eoiStyle(d.explicit_or_inferred || d.eoi || (d._line === "dashed" ? "inferred" : "explicit"));

  const parts = [];
  parts.push(whyRow(d.why || d.why_shown));
  // ★ 证据字节忠实度四分类（Phase 44 · D1）：**不得**把「不可从仓库复算」渲染成「已变更」——
  //   前者是已知边界（未入库的大体积拉取缓存，需上游原始数据），后者才是真待重验。
  const fid = d.fidelity || (d.stale ? "changed" : "verified");
  const fb = fidelityBadge(fid);
  if (fid !== "verified") {
    parts.push(`<div class="stale-banner" style="border-left-color:${fb.color}">`
      + `<b style="color:${fb.color}">证据忠实度：${esc(fb.label)}</b>`
      + `<div class="hint">${esc(fb.note)}</div>`
      + (Array.isArray(d.fidelity_reasons) && d.fidelity_reasons.length
          ? `<div class="hint">${esc(d.fidelity_reasons.join("；"))}</div>` : "")
      + "</div>");
  } else {
    parts.push(`<div class="fidelity-ok" style="color:${fb.color}">`
      + `证据忠实度：${esc(fb.label)}（${esc(fb.note)}）</div>`);
  }
  parts.push(`<div class="detail-title" style="font-size:14px">${esc(d.type)}</div>`);
  parts.push(`<div><span class="badge" style="border-color:${edgeTypeColor(d.type)}">语义关系 type=${esc(d.type)}</span>`
           + `<span class="badge">来源/管线 kind=${esc(d.kind)}</span></div>`);
  parts.push(kv("端点", `<code>${esc(d.source)}</code> → <code>${esc(d.target)}</code>`));
  parts.push(kv("来源性质", `${esc(eoi.label)}（explicit_or_inferred）`));
  if (typeof d.confidence === "number") parts.push(kv("置信度", esc(d.confidence)));
  if (d.verification_level) {
    parts.push(kv("验证档", badgeOf(d.verification_level)
      + (d.verification_scope ? ` <span class="pill">${esc(d.verification_scope)}</span>` : "")
      + ` <span class="pill">verified=${esc(String(!!d.verified))}</span>`
      + ` <span class="pill">strict=${esc(String(!!d.verified_strict))}</span>`));
  }
  if (d.verifier) parts.push(kv("验证器 verifier", `<code>${esc(d.verifier)}</code>`));
  if (d.rationale) parts.push(kv("理由 rationale", esc(d.rationale)));

  // ★ 证据分列（铁律 #59）
  parts.push('<div class="detail-block"><div class="lbl">证据（impl=谁验的 / src=读的是哪个源·哪一版）</div>');
  if (!evs.length) {
    parts.push('<div class="val"><span class="warn">该边没有 evidence 条目 —— 这正是可追溯性的缺口，不应被隐藏。</span></div>');
  } else {
    evs.forEach((ev, i) => {
      parts.push(`<div class="evcard">
        <div class="evhead">证据 #${i + 1} <span class="pill">kind=${esc(ev.kind)}</span>`
        + (ev.indep === true ? '<span class="pill ok">独立</span>'
           : (ev.indep === false ? '<span class="pill warn">非独立</span>' : ""))
        + `</div>`
        + `<div class="evrow"><span class="k">src（读的源）</span><span class="v"><code>${esc(ev.src)}</code>`
        + (ev.src_ver ? ` <span class="pill">${esc(ev.src_ver)}</span>` : "")
        + (ev.src_hash ? ` <code class="hash">${esc(String(ev.src_hash).slice(0, 12))}…</code>`
           : ("src_hash" in ev
              ? ' <span class="pill warn" title="该来源无 locator（模型/规则源）⇒ 不可字节复算"'
                + '>无 locator · 不可复算</span>'
              : ""))
        + `</span></div>`
        + `<div class="evrow"><span class="k">impl（谁验的）</span><span class="v"><code>${esc(ev.impl)}</code>`
        + (ev.impl_ver ? ` <span class="pill">${esc(ev.impl_ver)}</span>` : "")
        + `</span></div>`
        + (ev.detail ? `<div class="evrow"><span class="k">说明</span><span class="v">${esc(ev.detail)}</span></div>` : "")
        + `</div>`);
    });
  }
  parts.push(`<div class="note" style="margin-top:6px">字段口径：${EVIDENCE_FIELDS.map((f) => `${f.key}=${f.label}`).join(" · ")}</div>`);
  parts.push("</div>");

  el.innerHTML = parts.join("");
  return el;
}

/** 概览（聚合元图）下钻说明卡 */
export function renderOverviewDetail(el, node) {
  if (!el) return;
  if (!node) {
    el.innerHTML = '<div class="empty">概览是<b>聚合元图</b>（学科 × 类型），不是全图。点击任一聚合节点可下钻为一次筛选查询。</div>';
    return;
  }
  el.innerHTML = whyRow("task_anchor")
    + `<div class="detail-title" style="font-size:14px">${esc(node.subject)} :: ${esc(node.type)}</div>`
    + kv("该聚合块节点数", esc(node.n_nodes))
    + `<div class="note">点击画布上的该节点 ⇒ 以 <code>mode=filter&subject=${esc(node.subject)}&types=${esc(node.type)}</code> 下钻。</div>`;
  return el;
}
