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

import { WHY_SHOWN_LABEL, deriveEra, ERA_NONE } from "./core.js";
import { verificationBadge, EVIDENCE_FIELDS, edgeTypeColor, eoiStyle,
         fidelityBadge, evidenceStatusBadge, gradeStyle } from "./style.js";

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

// ---------------------------------------------------------------------------
// Phase 64 · M3：变量表（G1）与适用条件三态（G3）
//
// ★ 两处都遵守同一条纪律：**空位是信息**。
//   没有内容时必须说清「为什么没有」，不能渲染成空白 ——
//   空白会让用户以为是**我们漏了**，而实际可能是「这件事对它无从谈起」。
//   这与 `whyRow` 对未识别值「显式暴露、不静默吞」是同一族。
// ---------------------------------------------------------------------------

/** `symbol_table` 条目的来源标签（G1 的核心口径：源声明 ≠ 沿边反推） */
export const SYMBOL_SRC_LABEL = {
  pb_symbols_column: { label: "源声明", note: "PhysicsBabel 的 symbols 列里原文给出的字形" },
  edge_target:       { label: "沿边反推", note: "由 has_symbol 边指向的量反推 —— 不是公式原文里写明的字形" },
};

/** `symbols_unresolved`：变量表**为什么没能生成**（数据侧已给出原因，前端不得丢弃） */
export const SYMBOL_UNRESOLVED_LABEL = {
  no_has_symbol_edge:   "这条公式在图里没有 <code>has_symbol</code> 边 ⇒ 无从得知它用了哪些量。",
  targets_carry_no_glyph: "边指向的量没有字形 ⇒ 知道涉及哪个量，但不知道原文里写成什么符号。",
  glyphs_only_from_edges: "没有源声明字形，表中符号<b>全部</b>由边反推得到。",
};

/**
 * `applicability_status` 三态（G3）：语义各不相同，**不得合并显示**。
 *
 * ★ `note` 是 **HTML 片段**（含 `<b>`），直接插入不转义 —— 它来自代码常量，非用户数据。
 *   第一版把 `**加粗**` 写在里面，结果星号原样显示在详情卡上（详情卡是 HTML，不跑 Markdown）。
 */
export const APPLICABILITY_STATE = {
  extracted: {
    label: "已抽取", cls: "ok",
    note: "从原文里抽到了适用条件，每条都附原文片段可核验。",
  },
  no_referent: {
    label: "无指称", cls: "",
    note: "这条公式是把基本量纲拼出来的<b>齐次合成式</b>，不对应具体物理过程 ⇒ "
        + "<b>谈不上</b>适用条件。这不是「漏抽」：抽不出来是因为它本来就没有指称对象。",
  },
  not_extracted: {
    label: "未抽出", cls: "warn",
    note: "这条公式<b>有</b>自然语言说明，但没从里面抽出适用条件 —— "
        + "属于<b>待办</b>，与「无指称」不是一回事。",
  },
};

/**
 * 拆 `symbol_table` 条目的「符号 / 量名」。
 *
 * 数据里有两种形态（实测：无括号 24,111 / 有括号 191）：
 *   · PB 侧    `{sym:"m",   quantity:"Mass"}`         → 符号与量名分列存
 *   · 策展侧   `{sym:"P",   quantity:"P (pressure)"}`  → quantity 把符号和量名拼在一起
 * 另有 20 条 `sym` 为 null（`{sym:null, quantity:"Q (charge)"}`）——
 * 此时符号只能从 quantity 括号前取，**不能留空**。
 */
export function splitSymbolQuantity(entry) {
  const q = String((entry && entry.quantity) || "").trim();
  const m = /^(.+?)\s*\(([^)]+)\)\s*$/.exec(q);
  const symRaw = String((entry && entry.sym) || "").trim();
  if (m) {
    return { sym: symRaw || m[1].trim(), qty: m[2].trim(), symFromQuantity: !symRaw };
  }
  return { sym: symRaw, qty: q, symFromQuantity: false };
}

/** 变量表区块；无表且无「为什么没有」时返回 ""（不是渲染成空行） */
export function symbolTableBlock(attrs) {
  const a = attrs || {};
  const tbl = Array.isArray(a.symbol_table) ? a.symbol_table.filter(Boolean) : [];

  // 无表：若数据侧说明了原因，就把原因显示出来 —— 「没抽到」与「无从谈起」是两件事，
  // 整行消失会让用户以为是我们漏了（G3 同一条纪律，用在变量表上）。
  if (!tbl.length) {
    const why = SYMBOL_UNRESOLVED_LABEL[a.symbols_unresolved];
    if (!why) return "";
    return `<div class="detail-block"><div class="lbl">变量表 `
         + `<span class="pill warn">未生成</span></div>`
         + `<div class="val"><span class="note">${why}</span></div></div>`;
  }

  const rows = tbl.map((e) => splitSymbolQuantity(e));
  const nEdge = tbl.filter((e) => e.src === "edge_target").length;
  const nDecl = tbl.length - nEdge;

  const head = `<span class="pill">${tbl.length} 个符号</span>`
    + (nDecl ? ` <span class="pill">源声明 ${nDecl}</span>` : "")
    + (nEdge ? ` <span class="pill warn">沿边反推 ${nEdge}</span>` : "");

  const body = tbl.map((e, i) => {
    const { sym, qty } = rows[i];
    const src = SYMBOL_SRC_LABEL[e.src];
    // 源声明是常态 ⇒ 不打扰；沿边反推是例外 ⇒ 标出来（G1 的整条纪律都在这一个 pill 上）
    const srcPill = (e.src === "edge_target")
      ? ` <span class="pill warn" title="${esc(src ? src.note : "来源未知")}">${esc(src ? src.label : e.src)}</span>`
      : (src ? "" : ` <span class="pill err" title="未识别的来源值 —— 显式暴露，不静默当空">${esc(String(e.src))}</span>`);
    const symCell = sym
      ? `<code class="sym">${esc(sym)}</code>`
      : `<code class="sym missing" title="该条目没有字形（sym 为空，且 quantity 无括号可拆）">—</code>`;
    return `<div class="symrow">${symCell}<span class="qty">${qty ? esc(qty) : '<span class="note">（未标明指称的量）</span>'}</span>`
         + `<span class="symsrc">${srcPill}</span></div>`;
  }).join("");

  // 全反推时额外说明：否则用户看到「沿边反推 5」会以为是混合，其实一个都没源声明
  const allEdge = (nEdge === tbl.length) ? SYMBOL_UNRESOLVED_LABEL.glyphs_only_from_edges : "";
  const note = allEdge
    || "「源声明」= 公式原文写明的字形；「沿边反推」= 由 <code>has_symbol</code> 边指向的量反推，"
     + "原文里未必有该字形。";

  return `<div class="detail-block"><div class="lbl">变量表 ${head}</div>`
       + `<div class="val symtab">${body}</div>`
       + `<div class="note">${note}</div></div>`;
}

/** 适用条件区块；三态各有措辞，未知值显式暴露 */
export function applicabilityBlock(attrs) {
  const st = (attrs || {}).applicability_status;
  if (!st) return "";                      // 非公式节点 / 老数据：不渲染（不是渲染成空）
  const meta = APPLICABILITY_STATE[st];
  // `meta.note` 含 `<b>`：正文直接插入（代码常量，安全）；`title` 属性里必须去标签
  const plain = (s) => String(s || "").replace(/<[^>]+>/g, "");
  const label = `<div class="lbl">适用条件 `
    + `<span class="pill ${meta ? meta.cls : "err"}" title="${esc(meta ? plain(meta.note) : "未识别的状态值")}">`
    + `${esc(meta ? meta.label : "未知状态")}</span></div>`;

  if (!meta) {
    return `<div class="detail-block">${label}<div class="val">`
      + `<code>applicability_status = ${esc(String(st))}</code>`
      + `<div class="note">前端未识别的状态值 —— <b>显式暴露，不静默当空</b>。`
      + `若数据侧新增了状态，这里要同步补标签。</div></div></div>`;
  }

  if (st === "extracted") {
    const items = Array.isArray(attrs.applicability) ? attrs.applicability.filter(Boolean) : [];
    const evs = Array.isArray(attrs.applicability_evidence) ? attrs.applicability_evidence.filter(Boolean) : [];
    const evOf = (term) => evs.find((e) => e && e.term === term) || null;
    const body = items.map((t) => {
      const ev = evOf(t);
      // 出处必须可见：项目纪律是「有值必有据」，条件是从哪句话里读出来的要摆给读者
      const cite = ev
        ? `<div class="note">出处：<code>${esc(ev.field || "?")}</code>「${esc(ev.snippet || "")}」</div>`
        : `<div class="note">（该条件没有附出处 —— 这正是可追溯性的缺口，不应被隐藏）</div>`;
      return `<div class="appitem"><span class="appterm">${esc(t)}</span>${cite}</div>`;
    }).join("");
    return `<div class="detail-block">${label}<div class="val">${body}</div>`
         + `<div class="note">${meta.note}</div></div>`;
  }

  return `<div class="detail-block">${label}<div class="val"><span class="note">${meta.note}</span>`
       + `</div></div>`;
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
  // `domain` 由 `project_node` 提到节点顶层；同时兜住只写在 attrs 里的情况 ——
  // 少了这个兜底，投影层一旦漏提该字段，学科与跨域标签就整行消失而无人察觉。
  const domMain = n.domain || attrs.domain;
  if (domMain) {
    // Phase 62：`domain` 已落到二级学科，`domain_tags` 存全标签（跨域 84.3%）。
    // 只显示主标签会把「这条公式其实横跨两个学科」藏起来；`domain_tags` 含主标签本身，
    // 故先剔除再当「其余标签」显示。
    const tags = (Array.isArray(attrs.domain_tags) ? attrs.domain_tags : [])
      .filter((t) => t && t !== domMain);
    parts.push(kv("domain", esc(domMain)
      + (tags.length ? ` <span class="pill" title="该公式同时涉及这些学科 —— 合成式天生跨域，不是错分">跨域 ${tags.length}：${esc(tags.join(" / "))}</span>` : "")));
  }
  if (typeof n.confidence === "number") parts.push(kv("置信度", esc(n.confidence)));
  const lv = attrs.verification_level || n.verification_level;
  if (lv) parts.push(kv("验证档", badgeOf(lv)));

  if (n.formula || attrs.latex) {
    parts.push(kv("公式（LaTeX）",
      `<div id="mj" class="latex">\\[${esc(n.formula || attrs.latex)}\\]</div>`
      + `<button class="toolbtn" id="btn-copy-latex" data-tex="${esc(n.formula || attrs.latex)}">复制 LaTeX</button>`));
  }
  // ★ Phase 64 · M3：变量表紧跟公式（先看到式子、再看到每个符号是什么），
  //   适用条件紧随其后（什么时候能用）。两者都可能在数据里缺席 ——
  //   缺席时**不渲染该行**，而「有状态但无内容」时必须写明原因（函数内处理）。
  {
    const symBlock = symbolTableBlock(attrs);
    if (symBlock) parts.push(symBlock);
    const appBlock = applicabilityBlock(attrs);
    if (appBlock) parts.push(appBlock);
  }
  // ★ Phase 54：公式元数据 4 行（发现时间 / 发现人 / 公式介绍 / 使用描述）。
  //   渲染规则（用户确认）：
  //     · `discovery_time` / `discoverer` 在**数据层显式写入**「暂无」⇒ 必须**渲染出来**
  //       （弱化灰 + 「不可考」小标签），不得留空、不得静默吞掉；
  //     · `description` / `usage` 无值时**根本不写键** ⇒ **不渲染该行**（不是渲染成空）。
  //   「发现时间」行附带年代桶标签 —— 分桶的**唯一实现**在 `core.js::deriveEra`。
  //   末行「元数据依据」是 Phase 54 主动加的：项目纪律是「有值必有据」，
  //   把依据摆到用户面前才可核验（若不想显示，删掉下面 `meta_source` 那一行即可）。
  const NO_DATA = '<span class="pill">不可考</span>';
  const dim = (s) => `<span style="color:#8f9bb3">${esc(s)}</span>`;
  const dt = attrs.discovery_time;
  if (dt != null && dt !== "") {
    const era = deriveEra(dt);
    parts.push(kv("发现时间",
      (dt === ERA_NONE ? dim(dt) + " " + NO_DATA
                       : esc(dt) + (era !== ERA_NONE ? ` <span class="pill">${esc(era)}</span>` : ""))));
  }
  const dc = attrs.discoverer;
  if (dc != null && dc !== "") {
    parts.push(kv("发现人", dc === ERA_NONE ? dim(dc) + " " + NO_DATA : esc(dc)));
  }
  if (attrs.description) parts.push(kv("公式介绍", esc(attrs.description)));
  if (attrs.usage) parts.push(kv("使用描述", esc(attrs.usage)));
  if (attrs.meta_source) parts.push(kv("元数据依据", `<span class="note">${esc(attrs.meta_source)}</span>`));

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

  // ★ A04 · 验证依赖状态（Phase 45）—— **第二层**判据，与上面的字节忠实度并列显示。
  //   两者都可能是「不可判定」，但**原因不同**：忠实度缺的是来源字节，
  //   本项缺的是参考表 / 验证器 / 依赖记录。合并成一个徽标就是把两种缺口混为一谈。
  const sbadge = evidenceStatusBadge(d.evidence_status);
  const sAbsent = (d.evidence_status == null || d.evidence_status === "");
  if (!sAbsent) {
    const cls = (d.evidence_status === "valid") ? "fidelity-ok" : "stale-banner";
    const style = (cls === "stale-banner") ? ` style="border-left-color:${sbadge.color}"` : "";
    const inner = (cls === "stale-banner")
      ? `<b style="color:${sbadge.color}">验证依赖：${esc(sbadge.label)}</b>`
      : `<span style="color:${sbadge.color}">验证依赖：${esc(sbadge.label)}（${esc(sbadge.note)}）</span>`;
    parts.push(`<div class="${cls}"${style}>${inner}`
      + `<div class="hint">${esc(sbadge.note)}</div>`
      + (Array.isArray(d.evidence_status_reasons) && d.evidence_status_reasons.length
          ? `<div class="hint">原因：${esc(d.evidence_status_reasons.join("；"))}</div>` : "")
      + (d.evidence_status_n
          ? `<div class="hint">带依赖记录的证据 ${esc(d.evidence_status_n.with_deps)} / ${esc(d.evidence_status_n.evidence)} 条</div>` : "")
      + "</div>");
  } else {
    // ★ 缺 A04 记录 ⇒ 有**独立**兜底徽标（`EVIDENCE_STATUS_ABSENT` = 「无 A04 记录」），
    //   **不得**渲染成「有效」：缺记录 ≠ 已验证。
    parts.push(`<div class="note" style="margin:4px 0">验证依赖：${esc(sbadge.label)}`
      + `（${esc(sbadge.note)}）</div>`);
  }

  parts.push(`<div class="detail-title" style="font-size:14px">${esc(d.type)}</div>`);
  parts.push(`<div><span class="badge" style="border-color:${edgeTypeColor(d.type)}">语义关系 type=${esc(d.type)}</span>`
           + `<span class="badge">来源/管线 kind=${esc(d.kind)}</span></div>`);
  parts.push(kv("端点", `<code>${esc(d.source)}</code> → <code>${esc(d.target)}</code>`));
  parts.push(kv("来源性质", `${esc(eoi.label)}（explicit_or_inferred）`));
  // ★ 证据等级（Phase 58）：服务端固化下发；null = 未判定（登记表不可用），不得当 E5。
  {
    const gs = gradeStyle(d.grade);
    parts.push(kv("证据等级", `<span class="badge" style="border-color:${gs.color}">${esc(gs.label)}</span>`
      + ` <span class="pill">grade=${d.grade == null ? "null" : esc(String(d.grade))}</span>`
      + ` <span class="pill">grade_rule=e1</span>`));
  }
  if (typeof d.confidence === "number") parts.push(kv("置信度（旧口径）", esc(d.confidence)));
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
      const sb = (ev.status != null) ? evidenceStatusBadge(ev.status) : null;
      const refs = (ev.refs && typeof ev.refs === "object") ? ev.refs : null;
      const binds = (ev.ref_bindings && typeof ev.ref_bindings === "object") ? ev.ref_bindings : null;
      const refChips = refs ? Object.keys(refs).sort().map((rid) => {
        const b = binds ? binds[rid] : null;
        const h = refs[rid] ? String(refs[rid]).slice(0, 12) + "…" : "null";
        const bt = b === "impl_declared"
          ? "该参考表由 impl 串显式点名（强绑定）"
          : (b === "code_import"
             ? "由验证器模块的代码级 import 推出（保守过近似：该模块 import 了这张表，"
               + "不等于本次判定真的读了它 —— 任务单允许的「先按整文件保守失效」）"
             : "绑定强度未知");
        return `<span class="pill" title="${esc(bt)}（记录时刻内容哈希 ${esc(h)}）">`
             + `${esc(rid)}${b ? " · " + esc(b === "impl_declared" ? "impl" : "import") : ""}</span>`;
      }).join(" ") : "";
      parts.push(`<div class="evcard">
        <div class="evhead">证据 #${i + 1} <span class="pill">kind=${esc(ev.kind)}</span>`
        + (ev.indep === true ? '<span class="pill ok">独立</span>'
           : (ev.indep === false ? '<span class="pill warn">非独立</span>' : ""))
        + (sb ? `<span class="pill" style="border-color:${sb.color};color:${sb.color}"`
              + ` title="${esc(sb.note)}">${esc(sb.label)}</span>` : "")
        + `</div>`
        + (ev.evd ? `<div class="evrow"><span class="k">evidence_id</span>`
                  + `<span class="v"><code class="hash">${esc(ev.evd)}</code></span></div>` : "")
        + `<div class="evrow"><span class="k">src（读的源）</span><span class="v"><code>${esc(ev.src)}</code>`
        + (ev.src_ver ? ` <span class="pill">${esc(ev.src_ver)}</span>` : "")
        + (ev.src_hash ? ` <code class="hash">${esc(String(ev.src_hash).slice(0, 12))}…</code>`
           : ("src_hash" in ev
              ? ' <span class="pill warn" title="该来源无 locator（模型/规则源）⇒ 不可字节复算"'
                + '>无 locator · 不可复算</span>'
              : ""))
        + `</span></div>`
        + `<div class="evrow"><span class="k">impl（谁验的）</span><span class="v"><code>${esc(ev.impl)}</code>`
        + (ev.impl_ver ? ` <span class="pill" title="旧字段 impl_ver：实测全库为单一值（49572/49572），无法区分是哪个验证器版本产出了哪条证据（A04a 指摘）⇒ 请以下方 A04 的 code_hash 为准">`
                        + `${esc(ev.impl_ver)}</span>` : "")
        + `</span></div>`
        + (ev.vfr || ev.vch
           ? `<div class="evrow"><span class="k">验证器身份（A04）</span><span class="v">`
             + (ev.vfr ? `<code>${esc(ev.vfr)}</code>` : '<span class="pill warn">无验证器</span>')
             + (ev.vch ? ` <code class="hash" title="验证器实现代码哈希（记录时刻）">${esc(String(ev.vch).slice(0, 12))}…</code>`
                       : ' <span class="pill warn" title="缺 code_hash ⇒ 失效器判 needs_review（不可判定）">无 code_hash</span>')
             + `</span></div>` : "")
        + (refChips
           ? `<div class="evrow"><span class="k">依赖参考表（A04）</span><span class="v">${refChips}</span></div>`
           : (ev.status != null
              ? `<div class="evrow"><span class="k">依赖参考表（A04）</span>`
                + `<span class="v"><span class="pill">未声明参考表</span> `
                + `<span class="hint">no_reference_declared（信息性：该证据不依赖参考表）</span></span></div>`
              : ""))
        + (Array.isArray(ev.status_reasons) && ev.status_reasons.length
           ? `<div class="evrow"><span class="k">状态原因</span>`
             + `<span class="v">${esc(ev.status_reasons.join("；"))}</span></div>` : "")
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
