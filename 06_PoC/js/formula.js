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
 * STTP · 公式详情抽屉（F6）—— 纯逻辑 + HTML 片段，可被 node 直接断言
 *
 * ★ 这个抽屉存在的理由：**把「这一段为什么是空的」说清楚**。
 *   实测（权威图 9,750 节点，本次现算）：5,090 个 Formula 里
 *   `applicability_status` = extracted **30** / not_extracted 59 / no_referent 5,001。
 *   ⇒ 若把「无指称对象（量纲合成式）」也显示成「暂无数据」，使用者会以为是**漏抽**，
 *     而真相是**它本来就没有指称对象**。两种「空」必须说成两句话（D115）。
 *
 * 三条纪律：
 *  1. **定性文案只有一份**：每段的中文定性由**后端** `APPLICABILITY_KIND` 给出
 *     （`sections[].kind`），本模块**不自带**任何状态说明文案 ——
 *     否则与 `evidence.js` 的 `APPLICABILITY_STATE` 形成第二份中文，改一处漏一处
 *     （D114）。本模块只负责**排版与转义**。
 *     ⚠ 已知待办（E9）：`evidence.js` 的 `APPLICABILITY_STATE[].note` 与后端
 *       `APPLICABILITY_KIND` 目前**各写一份**中文说明，属同源重复；收口方向是让
 *       子图节点抽屉也改用后端口径，本轮未做（会动到既有的 V1/V12 相关断言）。
 *  4. **`kind` 一律转义后插入**（与 `evidence.js` 的 `note` 不同 —— 那边是前端常量
 *     含 `<b>`；这边是**后端返回的字符串**，按"来自数据即不可信"处理更安全）。
 *     后端口径目前是纯中文常量 ⇒ 转义不损失可读性。
 *  2. **「不适用」≠「无数据」**：`status=no_result` 时**不渲染五段空壳**，
 *     而是原样呈现后端给的原因（「找不到节点」／「不是公式（主类型 X）」）。
 *  3. **所有来自数据的字符串一律 `esc()`**；`kind`/`note` 等常量含 `<b>` 的，
 *     只在正文里插入（与 `evidence.js` 同纪律），`title` 属性里必须去标签。
 */

import { esc } from "./empty.js";

/**
 * 五段（顺序即展示顺序）。
 * ★ 与 `03_知识层/queries.py` 的 `FORMULA_SECTIONS` 逐值相等，由
 *   `phase43_frontend_selfcheck.py` 的 X19a 求差钉住。
 */
export const FORMULA_SECTIONS = Object.freeze([
  "applicability", "symbols", "dimension", "provenance", "equivalents",
]);

/** 段名 → 中文（**只有这一份**；后端不带中文段名）。 */
export const FORMULA_SECTION_LABEL = Object.freeze({
  applicability: "适用条件",
  symbols: "符号",
  dimension: "量纲",
  provenance: "来源",
  equivalents: "等价式",
});

/**
 * 许可三态 → 中文。
 * ★ `null` 表示**这一段不适用**（没有来源值），**不是**「禁止」——
 *   两者处理不同，文案绝不能合并（D115）。
 */
export const REDISTRIBUTION_LABEL = Object.freeze({
  redistributable: "可随包再分发",
  internal_only: "仅内部使用（不得随包）",
  forbidden: "禁止再分发",
});

/** 许可三态 → 着色 class（与 `evidence.js` 的 pill 体系一致）。 */
export const REDISTRIBUTION_CLS = Object.freeze({
  redistributable: "ok",
  internal_only: "warn",
  forbidden: "err",
});

/**
 * 是否带 Formula 标签 —— **门禁判据**，与后端 `formula_detail` 的门禁同一口径。
 *
 * ★ 为什么判 `labels` 而不是主类型 `type`：实测
 *   `MX:lemma:partition_of_unity` 的 labels = ['Lemma','Formula','Entity']，
 *   带 Formula，但 `graph_export.pick_type` 选出的主类型是 Lemma。
 *   ⇒ 用 type 判断会把这些**确实是公式**的条目误判成非公式，
 *     用户点了它们却"什么都没有"，且无从得知原因。
 */
export function isFormulaLabels(labels) {
  const arr = Array.isArray(labels) ? labels : [];
  return arr.indexOf("Formula") >= 0;
}


/** 去标签（用于 `title` 属性：属性里出现 `<b>` 会被当成字面量显示）。 */
export function stripTags(s) {
  return String(s || "").replace(/<[^>]+>/g, "");
}

/**
 * 顶层 `missing` 是否**等于**各段 `present === false` 的集合。
 *
 * ★ 为什么必须由**前端**断言：这是**跨字段一致性**（后端在两个地方分别写
 *   `sections[].present` 与顶层 `missing`），JSON Schema 表达不了 ⇒ 只能靠断言。
 *   两者一旦脱节，UI 会一边显示「有数据」、一边提示「这一段没数据」。
 * @returns {boolean|null}  null = 不适用（no_result，无 sections）
 */
export function formulaMissingConsistent(env) {
  const secs = (env && env.sections && typeof env.sections === "object") ? env.sections : null;
  if (!secs || !Object.keys(secs).length) return null;
  const computed = FORMULA_SECTIONS.filter((k) => {
    const s = secs[k];
    return !(s && s.present === true);
  });
  const declared = Array.isArray(env && env.missing) ? env.missing.slice() : null;
  if (declared === null) return false;
  const a = computed.slice().sort().join("|");
  const b = declared.slice().sort().join("|");
  return a === b;
}

/**
 * 单段的展示视图。
 * @returns {{key:string,label:string,present:boolean,kind:string,rows:Array<[string,string]>}}
 */
export function formulaSectionView(sec, key) {
  const s = sec || {};
  const present = s.present === true;
  const kind = s.kind == null ? "" : String(s.kind);
  const rows = [];
  const push = (k, v) => {
    if (v === null || v === undefined) return;
    const t = String(v).trim();
    if (t === "") return;
    rows.push([k, t]);
  };

  if (key === "applicability") {
    push("状态", s.status);
    if (present) {
      const terms = Array.isArray(s.terms) ? s.terms.filter(Boolean) : [];
      push("条件", terms.length ? terms.join("；") : "");
      push("条件条数", s.terms_total);
    }
    push("抽取来源", s.source);
    push("规则版本", s.rule_version);
  } else if (key === "symbols") {
    push("符号", (Array.isArray(s.symbols) ? s.symbols.filter(Boolean) : []).join("、"));
    push("符号总数", s.symbols_total);
    push("变量表条目", s.table_total);
  } else if (key === "dimension") {
    push("方程", s.equation);
    if (s.exponents && typeof s.exponents === "object") {
      const kv = Object.keys(s.exponents).sort()
        .map((k) => `${k}=${s.exponents[k]}`).join(" , ");
      push("量纲指数", kv);
    }
    push("齐次", s.homogeneous === true ? "是" : (s.homogeneous === false ? "否" : ""));
    push("核验方式", s.verified_by);
  } else if (key === "provenance") {
    push("来源", s.source);
    push("来源引用", s.source_ref);
    const st = s.redistribution;
    if (st) {
      push("再分发", REDISTRIBUTION_LABEL[st] || st);
    } else if (s.license_available === true) {
      // ★ 有来源、判定器也在，却没给出立场 ⇒ 三态表有暗角（登记表未覆盖），显式报出
      push("再分发", "（登记表未覆盖该来源 —— 按 fail-closed 不应出现，请检查 source_registry）");
    } else {
      push("再分发", "（许可判定器不可用 ⇒ 无从判定，不等于「禁止」）");
    }
  } else if (key === "equivalents") {
    const items = Array.isArray(s.items) ? s.items.filter(Boolean) : [];
    push("等价对象", items.map((x) => x.peer_label || x.peer).join("、"));
    push("条数", s.count);
  }
  return { key, label: FORMULA_SECTION_LABEL[key] || key,
           present, kind: kind, rows };
}

/** 段缺失时的说明（**只用后端给的 kind**，不本地造文案；没有就如实说没有）。 */
export function formulaSectionEmptyText(view) {
  if (view && view.kind) return view.kind;
  return "这一段在响应里没有给出定性说明（不是「空」，是**不知道为什么空**）。";
}

/**
 * 抽屉正文 HTML。
 * @param {object|null} env  `/api/formula` 响应信封
 * @param {{loading?:boolean, error?:string}} [opts]
 */
export function formulaHTML(env, opts = {}) {
  const o = opts || {};
  const parts = [];

  parts.push('<div class="fd-title">公式详情'
    + '<span class="fd-hint">五段各自说明「有/没有，以及为什么」</span></div>');

  if (o.loading) {
    parts.push('<div class="fd-load">载入中…</div>');
    return parts.join("");
  }
  if (!env) {
    parts.push('<div class="fd-empty">尚未载入。</div>');
    return parts.join("");
  }

  const st = env.status;

  // ★ 纪律②：不适用 ⇒ 不渲染五段空壳，只说它是什么
  if (st === "no_result") {
    parts.push('<div class="fd-na"><b>这条不是公式条目</b>'
      + '<div class="fd-na-reason">' + esc(env.reason || "（后端未给出原因）")
      + '</div></div>');
    const labels = env.node && Array.isArray(env.node.labels) ? env.node.labels : [];
    if (labels.length) {
      parts.push('<div class="fd-na-labels">它的标签：'
        + labels.map((l) => `<code>${esc(l)}</code>`).join(" ") + '</div>');
    }
    return parts.join("");
  }
  if (st !== "ok") {
    parts.push('<div class="fd-na"><b>查询未成功</b>'
      + '<div class="fd-na-reason">状态：' + esc(String(st))
      + '　' + esc(env.reason || "") + '</div></div>');
    return parts.join("");
  }

  const node = env.node || {};
  parts.push('<div class="fd-head">'
    + '<div class="fd-name">' + esc(node.label || node.id || "?") + '</div>'
    + '<div class="fd-sub">'
    + (node.type ? `<span class="pill">${esc(String(node.type))}</span>` : "")
    + (node.subject ? `<span class="pill">${esc(String(node.subject))}</span>` : "")
    + (node.domain ? `<span class="pill">${esc(String(node.domain))}</span>` : "")
    + `<span class="pill">度 ${esc(String(node.degree == null ? 0 : node.degree))}</span>`
    + '</div>');
  // ★ label_is_typeword：上游把「条目类型」写进了 name（实测 8 条 MX: 公式）
  if (node.label_is_typeword === true) {
    parts.push('<div class="fd-warn">这条的 <code>name</code> 字段存的是'
      + ' <b>条目类型</b>（如 lemma / definition），不是公式名 —— '
      + '显示成标题会误导，请以 LaTeX 为准。</div>');
  }
  if (node.latex) {
    parts.push('<div class="fd-tex">\\(' + esc(String(node.latex)) + '\\)</div>');
  }

  const secs = (env.sections && typeof env.sections === "object") ? env.sections : {};
  FORMULA_SECTIONS.forEach((key) => {
    const sec = secs[key];
    if (!sec) return;                       // 段整体缺失 ⇒ 不臆造
    const v = formulaSectionView(sec, key);
    const cls = v.present ? "fd-sec" : "fd-sec fd-sec-empty";
    parts.push(`<div class="${cls}">`);
    parts.push('<div class="lbl">' + esc(v.label)
      + (v.present ? '<span class="pill">有</span>'
                   : '<span class="pill warn">无数据</span>')
      + '</div>');
    if (v.present) {
      v.rows.forEach(([k, val]) => {
        parts.push('<div class="fd-row"><span class="fd-k">' + esc(k) + '</span>'
          + '<span class="fd-v">' + esc(val) + '</span></div>');
      });
    } else {
      // ★ 只用后端给的 kind（纪律①），没有就如实说"不知道为什么"
      parts.push('<div class="fd-row"><span class="fd-v note">'
        + esc(formulaSectionEmptyText(v)) + '</span></div>');
    }
    parts.push('</div>');
  });

  // 许可：三态单独成行（不许把「判定器不可用」显示成「禁止」）
  const prov = secs.provenance || {};
  const rd = prov.redistribution;
  if (rd) {
    parts.push('<div class="fd-lic"><span class="pill '
      + esc(REDISTRIBUTION_CLS[rd] || "") + '">'
      + esc(REDISTRIBUTION_LABEL[rd] || rd) + '</span></div>');
  } else if (prov.license_available === false && prov.present) {
    parts.push('<div class="fd-lic note">许可判定器不可用 ⇒ '
      + '<b>无从判定</b>（不是「禁止」）</div>');
  }

  const stats = (env.stats && typeof env.stats === "object") ? env.stats : {};
  const cov = (stats.coverage && typeof stats.coverage === "object") ? stats.coverage : {};
  const covTxt = Object.keys(cov).sort().map((k) => `${k} ${cov[k]}`).join(" / ");
  const nFormula = stats.formula_nodes == null ? "?" : String(stats.formula_nodes);
  parts.push('<div class="fd-foot">全图 Formula ' + esc(nFormula)
    + (covTxt ? " · 适用条件状态分布：" + esc(covTxt) : "")
    + "</div>");

  return parts.join("");
}

/** 抽屉顶部的状态行文案（成功 / 缺段 / 不适用）。 */
export function formulaStatusText(env) {
  if (!env) return "尚未载入";
  const st = env.status;
  if (st === "no_result") return "不适用（不是公式）";
  if (st !== "ok") return "查询未成功（" + String(st) + "）";
  const miss = Array.isArray(env.missing) ? env.missing : [];
  if (!miss.length) return "五段齐全";
  return "五段齐全 " + (FORMULA_SECTIONS.length - miss.length) + "/"
    + FORMULA_SECTIONS.length + "，缺：" + miss.map(
      (k) => FORMULA_SECTION_LABEL[k] || k).join("、");
}
