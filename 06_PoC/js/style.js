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
 * STTP · 视觉常量层（**纯数据 / 零 DOM**）—— 编码映射的单一来源。
 *
 * ★ 设计纪律（方案 §6.2-7）：**可信度与优先分不得混成一个视觉编码**。
 *     节点：**填充色 = 学科**、**形状 = 类型**、**`verification_level` 用徽标**
 *     边：  **颜色 = 语义关系 `type`**、**线型 = explicit/inferred**、**`verification_level` 用徽标**
 *
 * ★ `EDGE_TYPE_COLOR` 的键集合**必须恰好等于**规范图里实测的 `type` 取值集合（24 种）。
 *   该等式由 `06_PoC/tests/phase43_frontend_selfcheck.py` 做**跨层断言**
 *   （读本文件 + 读 `etl/normalized.json` 实测），图源不可用时记 SKIP。
 *   —— 旧版 `graph_view.html` 的配色表已漂移（多 `depends_on`/`same_latex_normalized`、
 *      少 `uses_operator`），本文件即以实测为准重建。
 */

// ---------------------------------------------------------------------------
// 学科（节点填充色）
// ---------------------------------------------------------------------------
export const SUBJECT_COLOR = Object.freeze({
  "数学": "#4C72B0",
  "物理": "#DD8452",
  "化学": "#55A868",
  "跨学科": "#8C8C9A",
});
export const SUBJECT_UNKNOWN = "#6b7a90";
export const SUBJECT_ORDER = Object.freeze(["数学", "物理", "化学", "跨学科"]);

// ---------------------------------------------------------------------------
// 节点类型（描边色 + 形状）—— 形状是类型的**主**编码（色盲可辨）
// ---------------------------------------------------------------------------
export const NODE_TYPE_STYLE = Object.freeze({
  "Formula":          { color: "#1f77b4", shape: "round-rectangle" },
  "Definition":       { color: "#17becf", shape: "round-rectangle" },
  "Theorem":          { color: "#9467bd", shape: "round-rectangle" },
  "Lemma":            { color: "#bcbd22", shape: "round-rectangle" },
  "Equation":         { color: "#1f77b4", shape: "round-rectangle" },
  "MathConcept":      { color: "#9467bd", shape: "ellipse" },
  "Paper":            { color: "#4e79a7", shape: "tag" },
  "WikidataEntity":   { color: "#b07aa1", shape: "ellipse" },
  "Symbol":           { color: "#8c564b", shape: "diamond" },
  "PhysicalQuantity": { color: "#e377c2", shape: "hexagon" },
  "Molecule":         { color: "#2ca02c", shape: "triangle" },
  "Element":          { color: "#ff7f0e", shape: "octagon" },
  "Unit":             { color: "#17becf", shape: "diamond" },
  "Constant":         { color: "#c49c00", shape: "round-rectangle" },
  "Reaction":         { color: "#d62728", shape: "star" },
  "FunctionalGroup":  { color: "#7e7f9a", shape: "hexagon" },
  "Entity":           { color: "#6b7a90", shape: "ellipse" },
  "External":         { color: "#5a6a80", shape: "ellipse" },
  "Unknown":          { color: "#888888", shape: "ellipse" },
});
export const NODE_TYPE_ORDER = Object.freeze([
  "Formula", "Definition", "Theorem", "Lemma", "Equation", "MathConcept", "Paper",
  "WikidataEntity", "Symbol", "PhysicalQuantity", "Molecule", "Element", "Unit",
  "Constant", "Reaction", "FunctionalGroup", "Entity", "External", "Unknown",
]);

export function nodeTypeStyle(t) {
  return NODE_TYPE_STYLE[t] || NODE_TYPE_STYLE.Unknown;
}
export function subjectColor(s) {
  return SUBJECT_COLOR[s] || SUBJECT_UNKNOWN;
}

// ---------------------------------------------------------------------------
// 语义关系 `type`（边颜色）—— ★ 24 种，与规范图实测集合**逐项相等**
// ---------------------------------------------------------------------------
export const EDGE_TYPE_COLOR = Object.freeze({
  has_symbol:                 "#7f7f7f",
  composed_of:                "#2ca02c",
  has_quantity:               "#1abc9c",
  reactant_of:                "#d62728",
  product_of:                 "#9467bd",
  has_functionalgroup:        "#c5b0d5",
  reagent_of:                 "#ff9896",
  same_period:                "#ff7f0e",
  same_family:                "#bcbd22",
  discusses:                  "#8c6bb1",
  cites:                      "#9c755f",
  defines:                    "#8c564b",
  has_element:                "#ffbb78",
  same_as:                    "#17becf",
  derived_from:               "#1f77b4",
  related_to:                 "#e67e22",
  has_unit:                   "#9467bd",
  dimensionally_consistent:   "#2ca02c",
  uses_operator:              "#56b4e9",   // ★ 旧表缺失，本轮补（实测 11 条）
  same_formula_as:            "#17becf",
  part_of:                    "#b07aa1",
  chemical_reaction:          "#d62728",
  proves:                     "#e377c2",
  releases:                   "#d62728",
});
export const EDGE_TYPE_FALLBACK = "#8f9bb3";

export function edgeTypeColor(t) {
  return EDGE_TYPE_COLOR[t] || EDGE_TYPE_FALLBACK;
}

// ---------------------------------------------------------------------------
// explicit / inferred（边线型）—— 与颜色**正交**的第二个编码维度
// ---------------------------------------------------------------------------
export const EOI_STYLE = Object.freeze({
  explicit: { color: "#1f77b4", line: "solid",  label: "显式（源直接给出）" },
  inferred: { color: "#DD8452", line: "dashed", label: "推断（由规则/模型产生）" },
});
export const EOI_UNKNOWN = { color: "#8f9bb3", line: "dotted", label: "未标注" };

export function eoiStyle(v) {
  return EOI_STYLE[v] || EOI_UNKNOWN;
}

// ---------------------------------------------------------------------------
// `verification_level` 徽标（**不**参与填充色/形状，独立第三通道）
// ★ `rank` **必须**与后端 `queries._RANK` 逐值相等（7 档）——
//   由 `06_PoC/tests/phase43_frontend_selfcheck.py` 的 X7 断言（键集合 + rank 数值）。
//   实测分布只出现 5 档（rule_checked/source_asserted/cross_source/model_inferred/by_construction），
//   但**等级表**是 7 档口径 ⇒ 前端必须认全，否则未出现过的档位会被误显示成「未标注」。
// ---------------------------------------------------------------------------
export const VERIFICATION_BADGE = Object.freeze({
  unverified:      { color: "#8f9bb3", label: "未验证",   rank: 0 },
  model_inferred:  { color: "#d29922", label: "模型推断", rank: 1 },
  source_asserted: { color: "#58a6ff", label: "来源断言", rank: 2 },
  by_construction: { color: "#8b949e", label: "构造即真", rank: 3 },
  rule_checked:    { color: "#3fb950", label: "规则校验", rank: 4 },
  cross_source:    { color: "#a371f7", label: "跨源一致", rank: 5 },
  human_reviewed:  { color: "#56d4dd", label: "人工复核", rank: 6 },
});
export const VERIFICATION_UNKNOWN = { color: "#8f9bb3", label: "未标注", rank: -1 };

export function verificationBadge(v) {
  return VERIFICATION_BADGE[v] || VERIFICATION_UNKNOWN;
}

// ---------------------------------------------------------------------------
// 证据字节忠实度四分类（Phase 44 · D1）
// ★ 与 `03_知识层/queries.py:FIDELITY_CLASSES` **逐值一致**（自检 X8 守住）。
// ★ 关键：`unverifiable_not_in_repo` **不是**「已变更」——
//   它是**已知边界**（locator 未入库的大体积拉取缓存，需上游原始数据）。
//   前端若把两者渲染成同一个红徽标，就会把 47 055 条正常边误报成待重验。
// ---------------------------------------------------------------------------
export const FIDELITY_CLASSES = Object.freeze([
  "verified", "changed", "unverifiable_no_source", "unverifiable_not_in_repo",
]);
export const FIDELITY_BADGE = Object.freeze({
  verified: {
    color: "#3fb950", label: "已复算一致", short: "一致",
    note: "来源 locator 在仓库内，且 src_hash == 当前仓库字节哈希（与检出/行尾无关）",
  },
  changed: {
    color: "#f85149", label: "来源已变更", short: "已变更",
    note: "可从仓库复算但哈希不符 ⇒ 该断言需重验",
  },
  unverifiable_no_source: {
    color: "#d29922", label: "不可判定（无来源）", short: "不可判定",
    note: "无证据 / 来源未登记 / 来源无 locator（模型或规则源）⇒ fail-closed 视为 stale",
  },
  unverifiable_not_in_repo: {
    color: "#8b949e", label: "不可从仓库复算", short: "需上游",
    note: "locator 未入库（*_raw.json / *_cache.json 等大体积拉取缓存，按「大体积派生对象"
        + "不入库」有意排除）⇒ 需上游原始数据；**并未变更**，不计入 stale",
  },
});
export const FIDELITY_UNKNOWN = {
  color: "#8f9bb3", label: "未判定", short: "未判定",
  note: "登记表不可用 ⇒ 不可判定（不得渲染成「一致」）",
};

export function fidelityBadge(v) {
  return FIDELITY_BADGE[v] || FIDELITY_UNKNOWN;
}

// ---------------------------------------------------------------------------
// 置信度 → 线宽 / 不透明度（沿用既有口径，避免"改了视觉但没改语义"）
// ---------------------------------------------------------------------------
export function confToWidth(c) { return 1 + (Math.max(0, (c == null ? 0.5 : c)) - 0.5) * 8; }
export function confToOpacity(c) { return 0.35 + (c == null ? 0.5 : c) * 0.6; }

// ---------------------------------------------------------------------------
// 证据字段标签（证据卡片用；铁律 #59：`impl` = 谁验的，`src` = 读的是哪个源/哪版）
// ---------------------------------------------------------------------------
export const EVIDENCE_FIELDS = Object.freeze([
  { key: "src",      label: "源 id",        note: "读的是哪个源" },
  { key: "src_ver",  label: "源版本",       note: "该源的版本号" },
  { key: "src_hash", label: "源哈希",       note: "生成时刻的输入哈希；与当前**仓库字节**哈希比对判 fidelity" },
  { key: "impl",     label: "验证器",       note: "谁验的（= verifier）" },
  { key: "impl_ver", label: "验证器版本",   note: "验证实现版本" },
]);
