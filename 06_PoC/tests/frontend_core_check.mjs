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
 * STTP · 前端纯逻辑断言（零依赖；`node 06_PoC/tests/frontend_core_check.mjs`）
 *
 * 直接 import `06_PoC/js/core.js` —— 该文件不含 DOM，因此可被 node 断言。
 * ★ 本脚本**自证可红**（见末尾 META 段）：故意注入一条必然失败的检查，
 *   断言报告器确实把它记为 FAIL，否则报「检测器恒真」并 RC=1（铁律 #38「PASS≠无红」、
 *   铁律 #43「审计器可报错」）。
 *
 * 退出码：0 = 全过；1 = 有 FAIL / 检测器恒真。
 */

import * as C from "../js/core.js";
import { Api } from "../js/api.js";
import { detectMode } from "../js/modes.js";
import { renderStatusLine, renderBanner } from "../js/panels.js";
import { FIDELITY_CLASSES, FIDELITY_BADGE, FIDELITY_UNKNOWN, fidelityBadge,
         EVIDENCE_STATUSES, STALE_EVIDENCE_STATUSES, EVIDENCE_STATUS_RANK,
         EVIDENCE_STATUS_BADGE, EVIDENCE_STATUS_ABSENT, EVIDENCE_STATUS_UNKNOWN,
         evidenceStatusBadge } from "../js/style.js";
import { renderEdgeDetail } from "../js/evidence.js";

let pass = 0, fail = 0;
const rows = [];

function check(name, cond, detail) {
  if (cond) { pass += 1; rows.push(["PASS", name, detail || ""]); }
  else { fail += 1; rows.push(["FAIL", name, detail || ""]); }
  return !!cond;
}

function hex(rgb) {
  return "#" + rgb.map((x) => x.toString(16).padStart(2, "0")).join("");
}

// ---------------------------------------------------------------------------
// F1–F5 五态视觉编码（验收 V8 的逻辑面）
// ---------------------------------------------------------------------------
const states = C.STATUS_ORDER.slice();
check("F1 五态集合与 STATUS_ORDER 一致",
      states.length === 5
      && states.every((s) => C.STATUS_STYLE[s])
      && Object.keys(C.STATUS_STYLE).length === 5,
      states.join("/"));

const colors = states.map((s) => C.STATUS_STYLE[s].color);
const labels = states.map((s) => C.STATUS_STYLE[s].label);
const icons  = states.map((s) => JSON.stringify(C.STATUS_STYLE[s].icon));

check("F2 每态均有 icon(非空)/label(非空)/color(合法 hex)/cls(非空)",
      states.every((s) => {
        const v = C.STATUS_STYLE[s];
        return Array.isArray(v.icon) && v.icon.length > 0
            && v.icon.every((d) => typeof d === "string" && d.trim().length > 0)
            && typeof v.label === "string" && v.label.trim().length > 0
            && C.hexToRgb(v.color) !== null
            && typeof v.cls === "string" && v.cls.length > 0;
      }),
      "五态均齐备");

check("F3 五态 color 两两不同",
      new Set(colors).size === colors.length, colors.join(" "));

check("F3b 五态 label 两两不同（★ 不只靠颜色识别）",
      new Set(labels).size === labels.length, labels.join("/"));

check("F3c 五态 icon 两两不同（★ 三重编码的图标面）",
      new Set(icons).size === icons.length, "");

const contrasts = states.map((s) => {
  const r = C.contrastRatio(C.STATUS_STYLE[s].color, C.SURFACE_BG);
  return [s, r === null ? null : Math.round(r * 100) / 100];
});
check("F4 五态前景色对深色底对比度 ≥ 4.5:1",
      contrasts.every(([, r]) => r !== null && r >= 4.5),
      contrasts.map(([s, r]) => `${s}=${r}`).join(" "));

// ---------------------------------------------------------------------------
// F6 端点常量（单一来源；release 断言可读此文件）
// ---------------------------------------------------------------------------
const needEndpoints = ["search", "subgraph", "overview", "stats", "bundleGraph"];
check("F6 端点常量齐备且均为绝对路径",
      needEndpoints.every((k) => typeof C.ENDPOINTS[k] === "string"
                              && C.ENDPOINTS[k].startsWith("/")),
      needEndpoints.map((k) => C.ENDPOINTS[k]).join(" "));

// ---------------------------------------------------------------------------
// F7–F8 文案表覆盖齐全（防"只写一半"）
// ---------------------------------------------------------------------------
const NEED_CODES = ["BAD_REQUEST", "SNAPSHOT_MISMATCH", "EVIDENCE_STALE",
                    "SUBGRAPH_LIMIT_REACHED", "GRAPH_QUERY_TIMEOUT",
                    "TOOL_EXECUTION_ERROR", "ENTITY_AMBIGUOUS", "MISSING_CONDITION"];
const missingCodes = NEED_CODES.filter((c) => !C.ERROR_CODE_TEXT[c]);
check("F7 ERROR_CODE_TEXT 覆盖后端声明的 8 个 error_code",
      missingCodes.length === 0, missingCodes.length ? "缺: " + missingCodes.join(",") : "8/8");

const NEED_WHY = ["used_in_step", "evidence_path", "user_specified",
                  "task_anchor", "verified_context", "expansion"];
const missingWhy = NEED_WHY.filter((w) => !C.WHY_SHOWN_LABEL[w]);
check("F8 WHY_SHOWN_LABEL 覆盖 6 个 why_shown 枚举",
      missingWhy.length === 0, missingWhy.length ? "缺: " + missingWhy.join(",") : "6/6");

// ---------------------------------------------------------------------------
// F9–F10 状态归一化（铁律 #30：不可判定 ≠ 放过）
// ---------------------------------------------------------------------------
check("F9 已知五态原样返回且 unknown 为空",
      states.every((s) => {
        const r = C.normalizeStatus(s);
        return r.status === s && r.unknown === null;
      }), "");
const weird = C.normalizeStatus("weird_state");
check("F9b 未知状态 ⇒ error 且保留原始值（不得静默当 ok）",
      weird.status === "error" && weird.unknown === "weird_state",
      `status=${weird.status} unknown=${weird.unknown}`);
const st = C.statusStyle("weird_state");
check("F10 未知态的描述符落入 error 且 hint 含原始值",
      st.status === "error" && st.label === C.STATUS_STYLE.error.label
      && st.hint.includes("weird_state"), st.hint);

// ---------------------------------------------------------------------------
// F11–F13 参数规范化
// ---------------------------------------------------------------------------
const sample = {
  mode: "filter", types: ["has_symbol", "composed_of"], kinds: ["real"],
  subject: "物理", trust: "verified", max_nodes: 500, max_edges: 2000,
  min_confidence: "", exclude_stale: true, session_id: "s-1", reset: true,
  expand_token: "TKN", empty_thing: null,
};
const p1 = C.buildParams(sample);
const p2 = C.buildParams(sample);
check("F11 buildParams 确定性：同输入两次序列化相同",
      JSON.stringify(p1) === JSON.stringify(p2), C.toQueryString(p1));
check("F11b 键已排序（对象键序 = 排序序）",
      JSON.stringify(Object.keys(p1)) === JSON.stringify(Object.keys(p1).slice().sort()),
      Object.keys(p1).join(","));
check("F12 空值剔除、数组以逗号连接",
      p1.min_confidence === undefined && p1.empty_thing === undefined
      && p1.types === "has_symbol,composed_of",
      `types=${p1.types} min_confidence=${p1.min_confidence}`);
check("F12b exclude_stale 布尔以 '1' 表达",
      p1.exclude_stale === "1", String(p1.exclude_stale));
const sig = C.paramsSignature(p1);
check("F13 paramsSignature 剔除 session_id/reset/expand_token",
      !/session_id|reset=|expand_token/.test(sig), sig);

// ---------------------------------------------------------------------------
// F14–F15 合并去重（幂等）
// ---------------------------------------------------------------------------
const g0 = { nodes: [{ id: "A" }], edges: [{ id: "A|r|B" }] };
const inc = { nodes: [{ id: "A" }, { id: "B" }], edges: [{ id: "A|r|B" }, { id: "B|s|C" }] };
const m1 = C.mergeGraph(g0, inc);
check("F14 mergeGraph 首次并入 added 正确、重复计入 dup",
      m1.added.nodes === 1 && m1.added.edges === 1
      && m1.dup.nodes === 1 && m1.dup.edges === 1,
      `added=${JSON.stringify(m1.added)} dup=${JSON.stringify(m1.dup)}`);
const m2 = C.mergeGraph(m1, inc);
check("F14b 第二次并入同一响应 added 全 0（幂等）",
      m2.added.nodes === 0 && m2.added.edges === 0
      && m2.nodes.length === m1.nodes.length && m2.edges.length === m1.edges.length,
      `added=${JSON.stringify(m2.added)}`);
check("F15 mergeGraph 不修改入参（纯函数）",
      g0.nodes.length === 1 && g0.edges.length === 1, "");

// ---------------------------------------------------------------------------
// F16–F17 会话记账
// ---------------------------------------------------------------------------
const b = new C.SessionBudget({ nodes: 3, edges: 10 });
b.add(3, 5);
check("F16 会话累计 add 累加", b.nodes === 3 && b.edges === 5, C.sessionText(b));
check("F16b 恰好等于上限即 full（避免「可点但必失败」）", b.full === true, C.sessionText(b));
const rejected = b.add(1, 1);
check("F16c 超限 add 返回 ok:false 且不改状态",
      rejected.ok === false && b.nodes === 3 && b.edges === 5, JSON.stringify(rejected));
b.reset();
check("F17 reset 清零", b.nodes === 0 && b.edges === 0 && b.full === false, C.sessionText(b));
b.add(2, 2); b.rollback(2, 2);
check("F17b rollback 回到零且不为负", b.nodes === 0 && b.edges === 0, C.sessionText(b));
b.rollback(5, 5);
check("F17c rollback 超量不产出负数", b.nodes === 0 && b.edges === 0, C.sessionText(b));
const b2 = new C.SessionBudget({ nodes: 3, edges: 10 });
b2.add(1, 1);
b2.syncFrom({ session: { n_nodes: 9, n_edges: 40 }, expansions: 4 });
check("F17d syncFrom 以服务端为准",
      b2.nodes === 9 && b2.edges === 40, C.sessionText(b2));

// ---------------------------------------------------------------------------
// F18 模式真值表（含反例）
// ---------------------------------------------------------------------------
const mt = [
  [{ apiOk: true,  bundleOk: true,  forced: "" },       "live"],
  [{ apiOk: true,  bundleOk: true,  forced: "live" },   "live"],
  [{ apiOk: true,  bundleOk: true,  forced: "bundle" }, "bundle"],
  [{ apiOk: false, bundleOk: true,  forced: "" },       "bundle"],
  [{ apiOk: false, bundleOk: true,  forced: "live" },   "bundle"],
  [{ apiOk: true,  bundleOk: false, forced: "bundle" }, "live"],
  [{ apiOk: false, bundleOk: false, forced: "" },       "offline"],
  [{ apiOk: false, bundleOk: false, forced: "live" },   "offline"],
  [{}                                               , "offline"],
];
const badMode = mt.filter(([i, want]) => C.chooseMode(i) !== want)
                    .map(([i, want]) => `${JSON.stringify(i)}→${C.chooseMode(i)}≠${want}`);
check("F18 chooseMode 真值表逐格正确（9 格）",
      badMode.length === 0, badMode.length ? badMode.join(" | ") : "9/9");
check("F18b 后端不可用且无样例 ⇒ offline（★ 不得假装 live）",
      C.chooseMode({ apiOk: false, bundleOk: false }) === "offline", "");
check("F18c 三种模式均有角标文案",
      ["live", "bundle", "offline"].every((m) => C.MODE_BADGE[m] && C.MODE_BADGE[m].text),
      "");

// ---------------------------------------------------------------------------
// F19 截断文案不得编造"实有"
// ---------------------------------------------------------------------------
const t1 = C.truncationText({ n_nodes: 500, n_edges: 1998, truncated_by: "nodes" });
check("F19 pre_truncation 缺失 ⇒ 明说「实有规模未知」",
      t1.pre === null && t1.text.includes("实有规模未知"), t1.text);
const t2 = C.truncationText({ n_nodes: 500, n_edges: 1998, truncated_by: "edges",
                              pre_truncation: { n_nodes: 3012, n_edges: 12340 } });
check("F19b 有 pre_truncation ⇒ 显示「显示 X / 实有 Y」",
      t2.text.includes("显示") && t2.text.includes("实有") && t2.text.includes("12340"), t2.text);

// ---------------------------------------------------------------------------
// F20–F21 颜色工具：不可判定必须可辨（返回 null 而非 0/1）
// ---------------------------------------------------------------------------
check("F20 contrastRatio 对非法输入返回 null（不得当作通过）",
      C.contrastRatio("nope", "#0f1419") === null
      && C.contrastRatio("#0f1419", "xyz") === null, "");
check("F20b 同色对比度 = 1、黑白对比度 ≈ 21",
      Math.abs(C.contrastRatio("#000000", "#000000") - 1) < 1e-9
      && Math.abs(C.contrastRatio("#000000", "#ffffff") - 21) < 1e-6, "");
check("F21 hexToRgb 非法返回 null、合法解析正确",
      C.hexToRgb("zzz") === null
      && hex(C.hexToRgb("#0f1419")) === "#0f1419", "");

// ---------------------------------------------------------------------------
// F22–F24 模式探测的**请求序列**（验收 V1：首屏不得请求全图）
// ---------------------------------------------------------------------------
function jsonRes(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

async function probeWith({ statsOk, bundleOk, forced }) {
  const calls = [];
  const impl = async (url, opts = {}) => {
    calls.push(`${(opts.method || "GET")} ${url}`);
    if (url.includes("/api/stats")) {
      return statsOk ? jsonRes(200, { status: "ok", nodes: 1, edges: 1 }) : jsonRes(503, {});
    }
    if (url.includes("/graph_data.json")) {
      return bundleOk ? jsonRes(200, { nodes: [], edges: [] }) : jsonRes(404, {});
    }
    return jsonRes(404, {});
  };
  const api = new Api({ base: ".", fetchImpl: impl });
  const det = await detectMode(api, { base: ".", forced });
  return { det, calls };
}

const live = await probeWith({ statsOk: true, bundleOk: true });
check("F22 后端可用 ⇒ live，且请求序列**不含** /graph_data.json（验收 V1）",
      live.det.mode === "live" && !live.calls.some((u) => u.includes("graph_data.json")),
      live.det.mode + " ｜ " + live.calls.join(" ; "));

const bun = await probeWith({ statsOk: false, bundleOk: true });
check("F23 后端不可用但样例在 ⇒ bundle，且请求序列为 stats → HEAD graph_data.json",
      bun.det.mode === "bundle" && bun.calls.length === 2
      && bun.calls[0].includes("/api/stats") && /^HEAD /.test(bun.calls[1])
      && bun.calls[1].includes("graph_data.json"),
      bun.det.mode + " ｜ " + bun.calls.join(" ; "));

const off = await probeWith({ statsOk: false, bundleOk: false });
check("F24 两者皆不可用 ⇒ offline（★ 不假装 live / bundle）",
      off.det.mode === "offline", off.det.mode + " ｜ " + off.calls.join(" ; "));

const fb = await probeWith({ statsOk: true, bundleOk: true, forced: "bundle" });
check("F24b 显式 ?mode=bundle ⇒ 走样例全量（即使后端可用）",
      fb.det.mode === "bundle", fb.det.mode + " ｜ " + fb.calls.join(" ; "));

const fl = await probeWith({ statsOk: true, bundleOk: false, forced: "bundle" });
check("F24c ?mode=bundle 但样例缺失、后端可用 ⇒ 回落 live（不白屏）",
      fl.det.mode === "live", fl.det.mode + " ｜ " + fl.calls.join(" ; "));

// ---------------------------------------------------------------------------
// F25–F27 **运行时渲染**断言（验收 V8 的实质：五态在 UI 里真的产出了三重编码）
//   `panels.js` 的渲染函数只在 `el.innerHTML` / `el.setAttribute` 上落脚 ⇒
//   用最小假元素即可在 node 里跑真实渲染逻辑（无需 DOM 库）。
// ---------------------------------------------------------------------------
function fakeEl() {
  return {
    _attrs: {}, innerHTML: "", className: "", hidden: false, textContent: "",
    setAttribute(k, v) { this._attrs[k] = v; },
    getAttribute(k) { return this._attrs[k]; },
    querySelectorAll() { return []; },
    querySelector() { return null; },
  };
}

const rendered = C.STATUS_ORDER.map((s) => {
  const el = fakeEl();
  renderStatusLine(el, {
    status: s, statusInfo: C.statusStyle(s), stats: {}, reason: "",
    error_code: null, snapshot_id: null, query_hash: null,
  });
  return el;
});

check("F25a 五态渲染的 data-state 与输入一一对应",
      rendered.every((el, i) => el.getAttribute("data-state") === C.STATUS_ORDER[i]),
      rendered.map((el) => el.getAttribute("data-state")).join("/"));

const hasIcon = rendered.map((el) => el.innerHTML.includes('class="st-icon"') && /<path d="/.test(el.innerHTML));
const hasLabel = rendered.map((el) => el.innerHTML.includes('class="st-label"'));
check("F25b 五态**每个**都渲染出内联 SVG 图标（非 emoji）",
      hasIcon.every(Boolean), hasIcon.map((v, i) => `${C.STATUS_ORDER[i]}=${v}`).join(" "));
check("F25c 五态**每个**都渲染出文字标签（★ 不只靠颜色）",
      hasLabel.every(Boolean), hasLabel.map((v, i) => `${C.STATUS_ORDER[i]}=${v}`).join(" "));

const chipClasses = rendered.map((el) => (el.innerHTML.match(/class="stchip ([\w-]+)"/) || [])[1]);
check("F25d 五态的 chip class 两两不同",
      new Set(chipClasses).size === 5 && chipClasses.every(Boolean), chipClasses.join(" "));

const iconPaths = rendered.map((el) => (el.innerHTML.match(/<path d="[^"]+"/g) || []).join("|"));
check("F25e 五态的图标路径集合两两不同（可盲辨）",
      new Set(iconPaths).size === 5 && iconPaths.every((s) => s.length > 0), "");

const labelTexts = rendered.map((el) => (el.innerHTML.match(/class="st-label">([^<]+)</) || [])[1]);
check("F25f 五态标签文字两两不同",
      new Set(labelTexts).size === 5 && labelTexts.every(Boolean), labelTexts.join("/"));

// F26 横幅：截断必须给出「显示 / 实有」，且不得把 pending_frontier 说成"还有 N 个"
const bn1 = fakeEl();
renderBanner(bn1, {
  status: "truncated", statusInfo: C.statusStyle("truncated"), reason: "",
  stats: { n_nodes: 500, n_edges: 1998, truncated_by: "nodes",
           pre_truncation: { n_nodes: 3012, n_edges: 12340 },
           pending_frontier: 7, pending_frontier_note: "下界：中断时已知但未展开的节点数" },
});
check("F26 截断横幅含「显示 … / 实有 …」与 truncated_by",
      bn1.innerHTML.includes("3012") && bn1.innerHTML.includes("12340")
      && bn1.innerHTML.includes("nodes") && bn1.hidden === false,
      "已含实有规模");
check("F26b pending_frontier 被标为「下界」，且**不**渲染成「还有 N 个」",
      bn1.innerHTML.includes("下界") && !/还有\s*\d+\s*个/.test(bn1.innerHTML),
      "");

const bn2 = fakeEl();
renderBanner(bn2, { status: "no_result", statusInfo: C.statusStyle("no_result"), stats: {}, reason: "" });
check("F26c 无结果横幅给「放宽建议」，且明说不是失败",
      bn2.innerHTML.includes("放宽") && bn2.innerHTML.includes("不是失败"), "");

const bn3 = fakeEl();
renderBanner(bn3, { status: "ok", statusInfo: C.statusStyle("ok"), stats: {} });
check("F26d ok 状态**不**渲染横幅（隐藏且清空）",
      bn3.hidden === true && bn3.innerHTML === "", "");

const bn4 = fakeEl();
renderBanner(bn4, { status: "error", statusInfo: C.statusStyle("error"), reason: "参数不合法",
                    error_code: "BAD_REQUEST", stats: {} });
check("F26e 错误横幅显示 error_code 及其中文文案",
      bn4.innerHTML.includes("BAD_REQUEST") && bn4.innerHTML.includes("参数不合法"), "");

// F27 状态行内的 error_code 文案与「已隐藏 N 条」提示
const sl1 = fakeEl();
renderStatusLine(sl1, {
  status: "truncated", statusInfo: C.statusStyle("truncated"),
  stats: { n_nodes: 500, hidden_by_trust: 13932, elapsed_ms: 155, truncated_by: "nodes" },
  error_code: "SUBGRAPH_LIMIT_REACHED", snapshot_id: "sha256:abcdef123456",
  query_hash: "0123456789abcdef", reason: "",
});
check("F27 状态行回显 snapshot_id / query_hash / 已隐藏 N 条 / error_code",
      sl1.innerHTML.includes("sha256:abcdef123456") && sl1.innerHTML.includes("0123456789abcdef")
      && sl1.innerHTML.includes("13932") && sl1.innerHTML.includes("SUBGRAPH_LIMIT_REACHED"), "");

// ---------------------------------------------------------------------------
// F28 证据字节忠实度四分类（Phase 44 · D1）—— 纯逻辑面
//   ★ 与 `phase43_frontend_selfcheck.py: X8` 分工：X8 守「前端常量 == 后端常量」，
//     本节守「徽标解析行为本身」—— 尤其「未知值**不得**被渲染成 `verified`」。
// ---------------------------------------------------------------------------
check("F28 忠实度四分类：名称齐全且与徽标键集合一一对应",
      FIDELITY_CLASSES.length === 4
      && FIDELITY_CLASSES.every((k) => Object.prototype.hasOwnProperty.call(FIDELITY_BADGE, k)),
      FIDELITY_CLASSES.join(" "));

const fidColors = FIDELITY_CLASSES.map((k) => FIDELITY_BADGE[k].color);
const fidLabels = FIDELITY_CLASSES.map((k) => FIDELITY_BADGE[k].label);
check("F28b 四档颜色两两不同（★ 不只靠文字区分）",
      new Set(fidColors).size === 4, fidColors.join(" "));
check("F28c 四档短标签两两不同（★ 图注/图例可读）",
      new Set(FIDELITY_CLASSES.map((k) => FIDELITY_BADGE[k].short)).size === 4,
      FIDELITY_CLASSES.map((k) => FIDELITY_BADGE[k].short).join(" "));

check("F28d fidelityBadge(已知档) 原样返回同一对象（零转换、零降级）",
      FIDELITY_CLASSES.every((k) => fidelityBadge(k) === FIDELITY_BADGE[k]),
      "");

// ★ 关键否定断言：登记表不可用 ⇒ `fidelity === null` ⇒ 必须落 `未判定`，
//   绝不能默认成 `verified`（那会把「无法判定」伪装成「已复算一致」）。
check("F28e fidelityBadge(未知值) ⇒ FIDELITY_UNKNOWN，且**不**等于 verified 徽标",
      fidelityBadge(null) === FIDELITY_UNKNOWN
      && fidelityBadge(undefined) === FIDELITY_UNKNOWN
      && fidelityBadge("bogus") === FIDELITY_UNKNOWN
      && fidelityBadge(undefined) !== FIDELITY_BADGE.verified,
      "null/undefined/bogus 三路均落未判定");

check("F28f FIDELITY_UNKNOWN 的颜色/标签与任何已定义档位均不同",
      !fidColors.includes(FIDELITY_UNKNOWN.color) && !fidLabels.includes(FIDELITY_UNKNOWN.label),
      `${FIDELITY_UNKNOWN.color} ${FIDELITY_UNKNOWN.label}`);

// 文字对比度：徽标色作前景、深色面板作底 —— 与 F4 同口径（≥4.5:1）
const fidContrast = FIDELITY_CLASSES.map((k) => {
  const r = C.contrastRatio(FIDELITY_BADGE[k].color, C.SURFACE_BG);
  return [k, r === null ? null : Math.round(r * 100) / 100];
});
check("F28g 四档徽标色对深色底对比度 ≥ 4.5:1",
      fidContrast.every(([, r]) => r !== null && r >= 4.5),
      fidContrast.map(([k, r]) => `${k}=${r}`).join(" "));

check("F28h 每档都给出 note（解释「为何」—— 尤其 not_in_repo 的「并未变更」）",
      FIDELITY_CLASSES.every((k) => typeof FIDELITY_BADGE[k].note === "string"
                                    && FIDELITY_BADGE[k].note.length >= 8),
      "");
check("F28i unverifiable_not_in_repo 的 note 明说「并未变更」（防止误报为 stale）",
      FIDELITY_BADGE.unverifiable_not_in_repo.note.includes("并未变更"),
      FIDELITY_BADGE.unverifiable_not_in_repo.note.slice(0, 40) + "…");

// ---------------------------------------------------------------------------
// F29 证据卡片：`src_hash === null`（无 locator 的模型/规则源）必须**显式**说明
//   ★ 契约（subgraph.schema.json）把 `src_hash` 声明为 `["string","null"]`，
//     且字段**始终存在**（在 required 内）。若前端只是「空则不渲染哈希」，
//     读者会把「不可复算」误读成「哈希恰好没显示」。故要求落一个可见徽标。
// ---------------------------------------------------------------------------
const evEl = fakeEl();
renderEdgeDetail(evEl, {
  type: "uses_operator", kind: "Phase13.GNN.typed", source: "MX:def:x", target: "MX:def:y",
  fidelity: "unverifiable_no_source", stale: true, fidelity_reasons: ["来源无 locator（不可复算）"],
  evidence: [{ kind: "model", src: "Phase13.GNN.typed", src_ver: "v1", src_hash: null,
               impl: "gnn_eval_v2", impl_ver: "v1", indep: false }],
});
check("F29 src_hash=null ⇒ 渲染可见的「无 locator · 不可复算」徽标（不是静默留空）",
      evEl.innerHTML.includes("无 locator · 不可复算"),
      "");
check("F29b src_hash=null 时**不得**把 null 字面量当作哈希打印",
      !evEl.innerHTML.includes("nullnull") && !/>null</.test(evEl.innerHTML), "");

const evEl2 = fakeEl();
renderEdgeDetail(evEl2, {
  type: "uses_operator", kind: "Phase13.GNN.typed", source: "MX:def:x", target: "MX:def:y",
  fidelity: "verified", stale: false,
  evidence: [{ kind: "recompute", src: "NIST.CODATA", src_ver: "2022", src_hash: "abcdef0123456789",
               impl: "verification_model", impl_ver: "1", indep: true }],
});
check("F29c src_hash 有值 ⇒ 显示哈希前缀且**不**出现「无 locator」徽标",
      evEl2.innerHTML.includes("abcdef012345") && !evEl2.innerHTML.includes("无 locator · 不可复算"), "");

const evEl3 = fakeEl();
renderEdgeDetail(evEl3, {
  type: "uses_operator", kind: "Phase13.GNN.typed", source: "MX:def:x", target: "MX:def:y",
  fidelity: "unverifiable_no_source", stale: true,
  evidence: [{ kind: "model", src: "Phase13.GNN.typed", impl: "gnn", indep: false }],
});
check("F29d src_hash 键**缺失**（旧响应）⇒ 不加徽标也不崩（向后兼容）",
      !evEl3.innerHTML.includes("无 locator · 不可复算")
      && evEl3.innerHTML.includes("Phase13.GNN.typed"), "",
);

// ---------------------------------------------------------------------------
// F30 A04 验证依赖状态（Phase 45）—— 纯逻辑面
//   ★ 与 `phase43_frontend_selfcheck.py: X10–X15` 分工：那边守「前端常量 == 后端常量」，
//     本节守「徽标解析行为本身」—— 尤其
//       ① **缺记录**（`null`）**不得**被渲染成「有效」；
//       ② `needs_review` **不得**被渲染成「已变更」（那是两回事）。
// ---------------------------------------------------------------------------
check("F30 A04 五态：名称齐全且与徽标键集合一一对应",
      EVIDENCE_STATUSES.length === 4
      && EVIDENCE_STATUSES.every((k) => Object.prototype.hasOwnProperty.call(EVIDENCE_STATUS_BADGE, k)),
      EVIDENCE_STATUSES.join(" "));

const esColors = EVIDENCE_STATUSES.map((k) => EVIDENCE_STATUS_BADGE[k].color);
check("F30b 四档颜色两两不同（★ 不只靠文字区分）",
      new Set(esColors).size === 4, esColors.join(" "));
check("F30c 四档短标签两两不同（图注/徽标可读）",
      new Set(EVIDENCE_STATUSES.map((k) => EVIDENCE_STATUS_BADGE[k].short)).size === 4,
      EVIDENCE_STATUSES.map((k) => EVIDENCE_STATUS_BADGE[k].short).join(" "));

check("F30d severity 表与语义一致：valid < needs_review < stale < rejected",
      EVIDENCE_STATUS_RANK.valid < EVIDENCE_STATUS_RANK.needs_review
      && EVIDENCE_STATUS_RANK.needs_review < EVIDENCE_STATUS_RANK.stale
      && EVIDENCE_STATUS_RANK.stale < EVIDENCE_STATUS_RANK.rejected,
      JSON.stringify(EVIDENCE_STATUS_RANK));

check("F30e 排除集 == {stale, rejected}（needs_review **不**在其中）",
      STALE_EVIDENCE_STATUSES.length === 2
      && STALE_EVIDENCE_STATUSES.includes("stale")
      && STALE_EVIDENCE_STATUSES.includes("rejected")
      && !STALE_EVIDENCE_STATUSES.includes("needs_review"),
      STALE_EVIDENCE_STATUSES.join(" "));

check("F30f evidenceStatusBadge(已知态) 原样返回同一对象（零转换、零降级）",
      EVIDENCE_STATUSES.every((k) => evidenceStatusBadge(k) === EVIDENCE_STATUS_BADGE[k]), "");

// ★ 关键否定断言 1：缺记录 ⇒ 独立兜底「无 A04 记录」，**绝不**默认成 valid
check("F30g evidenceStatusBadge(null/undefined/'') ⇒ EVIDENCE_STATUS_ABSENT，"
      + "且**不**等于 valid 徽标（缺记录 ≠ 已验证）",
      evidenceStatusBadge(null) === EVIDENCE_STATUS_ABSENT
      && evidenceStatusBadge(undefined) === EVIDENCE_STATUS_ABSENT
      && evidenceStatusBadge("") === EVIDENCE_STATUS_ABSENT
      && evidenceStatusBadge(null) !== EVIDENCE_STATUS_BADGE.valid,
      "null/undefined/空串 三路均落「无 A04 记录」");

check("F30h evidenceStatusBadge(未知值) ⇒ EVIDENCE_STATUS_UNKNOWN（不静默吞新状态）",
      evidenceStatusBadge("bogus") === EVIDENCE_STATUS_UNKNOWN
      && evidenceStatusBadge("bogus") !== EVIDENCE_STATUS_BADGE.valid,
      "");

check("F30i ABSENT / UNKNOWN 的颜色/标签与任何已定义档位均不同（可分辨）",
      !esColors.includes(EVIDENCE_STATUS_ABSENT.color)
      && !esColors.includes(EVIDENCE_STATUS_UNKNOWN.color)
      && !EVIDENCE_STATUSES.map((k) => EVIDENCE_STATUS_BADGE[k].label)
          .includes(EVIDENCE_STATUS_ABSENT.label),
      `${EVIDENCE_STATUS_ABSENT.color} / ${EVIDENCE_STATUS_UNKNOWN.color}`);

// 文字对比度：与 F4 / F28g 同口径（徽标色作前景、深色面板作底，≥4.5:1）
const esContrast = EVIDENCE_STATUSES.map((k) => {
  const r = C.contrastRatio(EVIDENCE_STATUS_BADGE[k].color, C.SURFACE_BG);
  return [k, r === null ? null : Math.round(r * 100) / 100];
});
check("F30j 四档徽标色对深色底对比度 ≥ 4.5:1",
      esContrast.every(([, r]) => r !== null && r >= 4.5),
      esContrast.map(([k, r]) => `${k}=${r}`).join(" "));

// ★ 关键否定断言 2：`needs_review` 的 note **必须**点明「并非已变更」
check("F30k needs_review 的 note 明说「并非」已变更（不可判定 ≠ 已变更，铁律 #70）",
      EVIDENCE_STATUS_BADGE.needs_review.note.includes("并非"),
      EVIDENCE_STATUS_BADGE.needs_review.note.slice(0, 48) + "…");
check("F30l rejected 的 note 说明它会与 stale 一同被排除（口径可预期）",
      EVIDENCE_STATUS_BADGE.rejected.note.includes("排除"),
      EVIDENCE_STATUS_BADGE.rejected.note.slice(0, 48) + "…");

// ---------------------------------------------------------------------------
// F31 证据卡片：A04 状态 / 依赖参考表 / 绑定强度 的渲染行为
// ---------------------------------------------------------------------------
const evA = fakeEl();
renderEdgeDetail(evA, {
  type: "uses_operator", kind: "Phase45.deps", source: "MX:def:x", target: "MX:def:y",
  fidelity: "changed", stale: true, evidence_status: "stale",
  evidence_status_source: "recorded",
  evidence_status_reasons: ["ref_changed:dimension_table", "dep_version_changed:dimension_table.py"],
  evidence_status_n: { evidence: 1, with_deps: 1 },
  evidence: [{
    kind: "recompute", src: "NIST.CODATA", src_ver: "2022", src_hash: "abcdef0123456789",
    impl: "verification_model.quantity_dimension", impl_ver: "100148c147cb", indep: true,
    evd: "evd:0123456789abcdef", vfr: "verification_model", vch: "43845b11d643880c",
    refs: { dimension_table: "ab9c5bcde8932ae5" },
    ref_bindings: { dimension_table: "impl_declared" },
    status: "stale", status_reasons: ["ref_changed:dimension_table"],
  }],
});
check("F31 有 A04 记录 ⇒ 渲染「验证依赖」banner 与状态原因",
      evA.innerHTML.includes("验证依赖") && evA.innerHTML.includes("依赖已变更")
      && evA.innerHTML.includes("ref_changed:dimension_table"),
      "");
check("F31b 证据卡显示 evidence_id / 验证器身份 / 依赖参考表（可追溯到具体表）",
      evA.innerHTML.includes("evd:0123456789abcdef")
      && evA.innerHTML.includes("43845b11d643")
      && evA.innerHTML.includes("dimension_table"),
      "");
check("F31c 旧 impl_ver 被标注为「全库单一值、区分不出」（不把它当验证器版本用）",
      evA.innerHTML.includes("100148c147cb")
      && evA.innerHTML.includes("全库为单一值")
      && evA.innerHTML.includes("A04a"),
      "");
check("F31d impl_declared 绑定被明确标出（与 code_import 过近似区分开）",
      evA.innerHTML.includes("impl") && !evA.innerHTML.includes("code_import"), "");

const evB = fakeEl();
renderEdgeDetail(evB, {
  type: "uses_operator", kind: "Phase45.deps", source: "MX:def:x", target: "MX:def:y",
  fidelity: "verified", stale: false, evidence_status: "valid",
  evidence_status_source: "recorded", evidence_status_n: { evidence: 1, with_deps: 1 },
  evidence: [{
    kind: "recompute", src: "NIST.CODATA", src_hash: "abcdef0123456789",
    impl: "verification_model.x", indep: true, evd: "evd:ffffffffffffffff",
    vfr: "verification_model", vch: "43845b11d643880c",
    refs: { element_reference: "3a4e6d035f109462" },
    ref_bindings: { element_reference: "code_import" },
    status: "valid", status_reasons: [],
  }],
});
check("F31e code_import 绑定显示为短标签「· import」且 tooltip 说明「保守过近似」（不假装强绑定）",
      evB.innerHTML.includes("· import")
      && evB.innerHTML.includes("保守过近似")
      && !evB.innerHTML.includes("· impl<"),
      "");

const evC = fakeEl();
renderEdgeDetail(evC, {
  type: "uses_operator", kind: "Phase45.deps", source: "MX:def:x", target: "MX:def:y",
  fidelity: "verified", stale: false, evidence_status: null,   // ★ 缺记录
  evidence: [{ kind: "src", src: "NIST.CODATA", src_hash: "abcdef0123456789",
               impl: "x", indep: true }],
});
check("F31f evidence_status=null ⇒ 显式写「无 A04 记录」，**不**渲染成「有效」",
      evC.innerHTML.includes("无 A04 记录")
      && !evC.innerHTML.includes("验证依赖：依赖一致"),
      "");

const evD = fakeEl();
renderEdgeDetail(evD, {
  type: "uses_operator", kind: "Phase45.deps", source: "MX:def:x", target: "MX:def:y",
  fidelity: "verified", stale: false, evidence_status: "needs_review",
  evidence_status_source: "recorded",
  evidence_status_reasons: ["unverifiable_source:ElementKG2.0"],
  evidence_status_n: { evidence: 1, with_deps: 1 },
  evidence: [{ kind: "model", src: "ElementKG2.0", src_hash: null, impl: "x", indep: false,
               evd: "evd:0000000000000000", status: "needs_review",
               status_reasons: ["unverifiable_source:ElementKG2.0"] }],
});
check("F31g needs_review ⇒ 渲染「待复核（不可判定）」而**非**「依赖已变更」（不误报）",
      evD.innerHTML.includes("待复核")
      && !evD.innerHTML.includes("依赖已变更")
      && !evD.innerHTML.includes("命题已否决"),
      "");

// ---------------------------------------------------------------------------
// META：证明报告器**可红**（不是恒真）
// ---------------------------------------------------------------------------
check("META 注入的必然失败项（应被记为 FAIL）", false, "（注入反例）");
const metaInjected = rows[rows.length - 1][0];
if (metaInjected !== "FAIL") {
  rows.pop(); pass -= 1;
  rows.push(["FAIL", "META 报告器恒真（注入反例未被记为 FAIL）",
             "断言框架本身失效，全部 PASS 不可信"]);
  fail += 1;
} else {
  rows.pop(); fail -= 1;   // 扣掉注入项，不计入最终统计
  rows.push(["PASS", "META 报告器可红（注入反例确实被记为 FAIL）", "自证有效"]);
  pass += 1;
}

// ---------------------------------------------------------------------------
// 输出
// ---------------------------------------------------------------------------
rows.forEach(([s, n, d]) => {
  console.log(`  [${s}] ${n}${d ? "  —— " + d : ""}`);
});
console.log(`\n  frontend_core_check: PASS ${pass} / FAIL ${fail}（共 ${pass + fail}）`);
process.exit(fail === 0 ? 0 : 1);
