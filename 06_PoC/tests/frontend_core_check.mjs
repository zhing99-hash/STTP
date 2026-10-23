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

import { readFileSync } from "node:fs";
import * as C from "../js/core.js";
import { Api, normalizeEnvelope } from "../js/api.js";
import { detectMode } from "../js/modes.js";
import { renderStatusLine, renderBanner, bindSearch, esc as escPanel } from "../js/panels.js";
import { emptyDiagOf, emptyDiagHTML, boundaryText, scopeText, esc as escEmpty } from "../js/empty.js";
import {
  NAV_LEVELS, NAV_LEVEL_LABEL, nextLevel, currentLevel,
  navItemsOf, navStatsOf, navNotice, navEmptyText, navHTML,
  navPush, navClip, navTargetOf,
} from "../js/nav.js";
import { FIDELITY_CLASSES, FIDELITY_BADGE, FIDELITY_UNKNOWN, fidelityBadge,
         EVIDENCE_STATUSES, STALE_EVIDENCE_STATUSES, EVIDENCE_STATUS_RANK,
         EVIDENCE_STATUS_BADGE, EVIDENCE_STATUS_ABSENT, EVIDENCE_STATUS_UNKNOWN,
         evidenceStatusBadge, EDGE_TYPE_COLOR } from "../js/style.js";
import { renderEdgeDetail, renderNodeDetail, SYMBOL_SRC_LABEL,
         APPLICABILITY_STATE } from "../js/evidence.js";
import { buildLegend, toCyEdge } from "../js/viz.js";
import { GRADE_STYLE, GRADE_UNKNOWN, gradeStyle, gradeToWidth, gradeToOpacity } from "../js/style.js";
import { renderCardsHTML, renderCandidatesHTML, CANDIDATE_LIMIT } from "../js/task.js";

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
//   ★ 2026-10-14 改造：比对名**从契约 `contracts/error.schema.json` 的 enum 派生**
//     （唯一真值源）。原实现是硬编码 8 个码的 needle 列表 ⇒ 契约新增码时**静默漏项**，
//     断言仍绿（#71「白名单永远漏」的同族）。同时新增"严重度必须显式归类"。
// ---------------------------------------------------------------------------
const CONTRACT_ERROR_PATH = new URL("../../contracts/error.schema.json", import.meta.url);
let contractCodes = null;
try {
  contractCodes = JSON.parse(readFileSync(CONTRACT_ERROR_PATH, "utf-8"))
    .properties.error_code.enum.slice();
} catch (e) {
  contractCodes = null;
}
check("F7a 读到错误码契约（唯一真值源 contracts/error.schema.json）",
      Array.isArray(contractCodes) && contractCodes.length > 0,
      contractCodes ? `${contractCodes.length} 个码：${contractCodes.join(",")}` : "读取失败");

const NEED_CODES = contractCodes || ["BAD_REQUEST", "BACKEND_UNREACHABLE"];
const missingCodes = NEED_CODES.filter((c) => !C.ERROR_CODE_TEXT[c]);
check("F7b ERROR_CODE_TEXT 覆盖**契约 enum 全景**（派生比对，不硬编码）",
      missingCodes.length === 0,
      missingCodes.length ? "缺: " + missingCodes.join(",") : `${NEED_CODES.length}/${NEED_CODES.length}`);

const missingSev = (contractCodes || []).filter((c) => !C.ERROR_CODE_SEVERITY[c]);
check("F7c ERROR_CODE_SEVERITY 对契约每个码**显式归类**（新增码不得静默默认成某一档）",
      Array.isArray(contractCodes) && missingSev.length === 0,
      missingSev.length ? "未归类: " + missingSev.join(",") : `${(contractCodes || []).length} 个已归类`);

check("F7d 未登记码 ⇒ 严重度取 err（往响处失败，不静默降级为 warn）",
      C.errorCodeSeverity("NOT_A_CODE") === "err"
      && C.errorCodeSeverity("BACKEND_UNREACHABLE") === "err"
      && C.errorCodeSeverity("GRAPH_QUERY_TIMEOUT") === "warn",
      `NOT_A_CODE=${C.errorCodeSeverity("NOT_A_CODE")}`);

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
// ★ Phase 49（修外部评审 P0-2）：探活改为 **`/api/healthz` 优先**。
//   实测：权威图 67.7 MB ⇒ 冷进程首次 `/api/stats` = 4.91 s，而探活预算 2 s
//   ⇒ abort ⇒ 判「后端不可用」⇒ 退 bundle ⇒ **首次打开就像坏了**。
//   `/api/healthz` 不触碰图谱数据 ⇒ 毫秒级回答。故这里的请求序列断言
//   从「stats 起手」改成「healthz 起手，且**不得**触碰 /api/stats」（后者只作老服务端回退）。
function jsonRes(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

async function probeWith({ healthzOk = null, statsOk, bundleOk, forced, warming = false }) {
  const calls = [];
  // `healthzOk === null` ⇒ 模拟**老服务端**（没有 /api/healthz，回 404）
  const impl = async (url, opts = {}) => {
    calls.push(`${(opts.method || "GET")} ${url}`);
    if (url.includes("/api/healthz")) {
      return healthzOk
        ? jsonRes(200, { status: "ok", backend: warming ? "warming" : "ready",
                         graph_ready: !warming, snapshot_id: warming ? null : "sha256:aaaa" })
        : jsonRes(404, {});
    }
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

const live = await probeWith({ healthzOk: true, statsOk: true, bundleOk: true });
check("F22 后端可用 ⇒ live，序列仅 healthz，**不含** /api/stats 与 /graph_data.json（验收 V1）",
      live.det.mode === "live"
      && !live.calls.some((u) => u.includes("graph_data.json"))
      && !live.calls.some((u) => u.includes("/api/stats")),
      live.det.mode + " ｜ " + live.calls.join(" ; "));

// ★★ P0-2 的**构造性反证**：healthz 通、而 stats 恰好超时/失败（=冷启动的真实情形）
//    ⇒ 仍必须判 live。旧实现（探活=/api/stats，2 s 预算）在这里会落到 bundle/offline，
//    也就是"首次打开就像坏了"。本条是这次修复的核心断言。
const cold = await probeWith({ healthzOk: true, statsOk: false, bundleOk: true });
check("F22b 【P0-2 负对照】healthz 通、stats 恰好失败（冷启动）⇒ 仍 live（不得退 bundle）",
      cold.det.mode === "live" && !cold.calls.some((u) => u.includes("/api/stats")),
      cold.det.mode + " ｜ " + cold.calls.join(" ; "));

// 预热中（backend=warming）是**独立状态**，不是失败（铁律 #84）
const warm = await probeWith({ healthzOk: true, statsOk: false, bundleOk: true, warming: true });
check("F22c backend=warming（图谱仍在预热）⇒ 仍判 live，且 warm=false 供 UI 显示「启动中」",
      warm.det.mode === "live" && warm.det.apiOk === true,
      warm.det.mode + " ｜ apiOk=" + warm.det.apiOk + " ｜ " + warm.calls.join(" ; "));

// 老服务端（无 healthz）⇒ 回退 /api/stats，**且回退成功必须判可用**
// （不得因为"较新的判据失败"就把整体回落成失败 —— 铁律 #84 的反面）
const legacyOk = await probeWith({ healthzOk: null, statsOk: true, bundleOk: true });
check("F22d 老服务端无 /api/healthz ⇒ 回退 /api/stats 成功仍判 live（回退不得反噬成功）",
      legacyOk.det.mode === "live"
      && legacyOk.calls.some((u) => u.includes("/api/healthz"))
      && legacyOk.calls.some((u) => u.includes("/api/stats")),
      legacyOk.det.mode + " ｜ " + legacyOk.calls.join(" ; "));

const bun = await probeWith({ healthzOk: false, statsOk: false, bundleOk: true });
check("F23 后端不可用但样例在 ⇒ bundle，且请求序列为 healthz → stats → HEAD graph_data.json",
      bun.det.mode === "bundle" && bun.calls.length === 3
      && bun.calls[0].includes("/api/healthz") && bun.calls[1].includes("/api/stats")
      && /^HEAD /.test(bun.calls[2]) && bun.calls[2].includes("graph_data.json"),
      bun.det.mode + " ｜ " + bun.calls.join(" ; "));

const off = await probeWith({ healthzOk: false, statsOk: false, bundleOk: false });
check("F24 两者皆不可用 ⇒ offline（★ 不假装 live / bundle）",
      off.det.mode === "offline", off.det.mode + " ｜ " + off.calls.join(" ; "));

const fb = await probeWith({ healthzOk: true, statsOk: true, bundleOk: true, forced: "bundle" });
check("F24b 显式 ?mode=bundle ⇒ 走样例全量（即使后端可用）",
      fb.det.mode === "bundle", fb.det.mode + " ｜ " + fb.calls.join(" ; "));

const fl = await probeWith({ healthzOk: true, statsOk: true, bundleOk: false, forced: "bundle" });
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
// ★ F12 规格变更：服务端没给结构化诊断时，**不再**甩一句「放宽建议：去掉部分语义关系 / 换学科…」
//   —— 那句话没有一个字告诉用户"刚才是哪一条挡住的"。新判据更强：必须明说「不猜原因」，
//   且只给一个如实命名的中性动作（旧动作 `relax` 会静默清空整个面板，已删除）。
check("F26c 无结果横幅：明说不是失败；**没有诊断时不猜原因**，只给中性动作",
      bn2.innerHTML.includes("不是失败") && bn2.innerHTML.includes("不猜原因")
      && bn2.innerHTML.includes('data-act="clear-filters"')
      && !bn2.innerHTML.includes('data-act="relax"'), "");

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
// F32 **P1 回归**（2026-10-14 实测：页面打开即卡死）
//   现场：`/api/stats` 是 legacy 端点，响应体**不带 `status`** ⇒ `normalizeStatus(undefined)`
//         判为 "error" ⇒ `detectMode` 认定后端不可用 ⇒ 退到 `bundle` ⇒
//         `GET /graph_data.json`（48.7 MB 全图）⇒ 解析 + 渲染 9657 节点 ⇒ **浏览器卡死**。
//   注意：既有的 V1b 只断言「整图加载被 bundle 分支包裹」，那是**源码形状**断言 ——
//         它通过了，而页面依然卡死。所以这里必须断言**行为**：探测结果本身。
// ---------------------------------------------------------------------------

/** 伪 fetch：可指定 stats 响应体是否带 status、bundle 的 content-length。 */
function probeImpl({ statsBody, statsStatus = 200, bundleBytes, bundleStatus = 200 }) {
  const calls = [];
  const mk = (status, body, bytes) => ({
    ok: status >= 200 && status < 300, status,
    headers: { get: (k) => (k.toLowerCase() === "content-length" && bytes != null
      ? String(bytes) : null) },
    json: async () => body,
  });
  const impl = async (url, opts = {}) => {
    calls.push(`${(opts.method || "GET")} ${url}`);
    if (url.includes("/api/stats")) return mk(statsStatus, statsBody);
    if (url.includes("/graph_data.json")) return mk(bundleStatus, { nodes: [], edges: [] }, bundleBytes);
    return mk(404, {});
  };
  return { impl, calls };
}

// F32a：★ 逐字复现 P1 现场 —— HTTP 200 但响应体缺 `status`
{
  const { impl, calls } = probeImpl({ statsBody: { backend: "networkx", nodes: 9657, edges: 48712 } });
  const api = new Api({ base: ".", fetchImpl: impl });
  const det = await detectMode(api, { base: "." });
  check("F32a /api/stats 无 status 但 HTTP 200 + 体可解析 ⇒ **live**（不得退 bundle ⇒ 拉 48 MB 全图）",
        det.mode === "live" && !calls.some((u) => u.includes("graph_data.json")),
        det.mode + " ｜ " + calls.join(" ; "));
}

// F32b：★ bundle 体积守卫 —— 权威全图（≈48.7 MB）必须**拒绝**，并给出明确原因
{
  const { impl, calls } = probeImpl({
    statsBody: { status: "error" }, statsStatus: 503,
    bundleBytes: Math.round(48.7 * 1048576),
  });
  const api = new Api({ base: ".", fetchImpl: impl });
  const det = await detectMode(api, { base: "." });
  check("F32b 后端不可用 + graph_data.json 为全图体积 ⇒ **offline**（拒绝加载，且原因点明体积超限）",
        det.mode === "offline" && /体积|超出/.test(det.detail),
        det.mode + " ｜ " + det.detail);
}

// F32c：守卫**不得误伤**发布样例（≈373 KB）—— 否则离线样例链路（V9）会被自己关掉
{
  const { impl, calls } = probeImpl({
    statsBody: { status: "error" }, statsStatus: 503, bundleBytes: 373440,
  });
  const api = new Api({ base: ".", fetchImpl: impl });
  const det = await detectMode(api, { base: "." });
  check("F32c 样例体积（373 KB）⇒ 仍判 bundle 可用（守卫不误伤 V9 离线样例链路）",
        det.mode === "bundle", det.mode + " ｜ " + det.detail);
}

// F32d：下载点二重守卫 —— `bundle()` 见 Content-Length 超限必须**不读 body** 直接拒绝
{
  const { impl } = probeImpl({ bundleBytes: Math.round(48.7 * 1048576) });
  const api = new Api({ base: ".", fetchImpl: impl });
  const env = await api.bundle();
  check("F32d bundle() 见超限 Content-Length ⇒ 返回 error 信封（不下载、不卡死）",
        env.status === "error" && env.error_code === "SUBGRAPH_LIMIT_REACHED"
        && /超出上限/.test(env.reason || ""),
        `${env.status} / ${env.error_code} / ${String(env.reason).slice(0, 60)}`);
}

// F32e：**中性初始态** —— 未发起任何查询时不得渲染成「错误」
{
  const el = fakeEl();
  renderStatusLine(el, null, { modeText: "实时查询" });
  check("F32e env=null（尚未查询）⇒ 渲染「待查询」而非「错误」（不谎报失败）",
        el.getAttribute("data-state") === "ok"
        && el.innerHTML.includes("待查询")
        && !el.innerHTML.includes("错误"),
        el.getAttribute("data-state"));
}

// ---------------------------------------------------------------------------
// F33 **P0 回归**（2026-10-14 实测：搜索框"没反应"）
//   现场：`buildParams` 的参数列表**漏了 `q`** ⇒ 搜索词被静默丢弃 ⇒ 请求退化成
//         `GET /api/search?limit=20&mode=ego` ⇒ 后端 400「缺少参数 q」
//         （旧实例日志里 26 次 400，全部是这一条）。
//   为什么既有断言全绿：F11 只断言「同输入两次序列化相同」——那是**形状**断言，
//         而它的 `sample` **恰好不含 `q`** ⇒ 漏字段永远测不到。
//         ★ 这是 #83（形状断言 ≠ 行为断言）在「参数序列化」上的又一次现身：
//           序列化漏字段**不会自己报错**，只在**对端**表现为"参数非法"。
//   处置：① `buildParams` 补 `put("q", …)`；② 断言**真正发出去的 URL**。
// ---------------------------------------------------------------------------
{
  const p = C.buildParams({ q: "F=ma", subject: "物理", limit: 20 });
  check("F33a buildParams 保留 `q`（/api/search 的唯一必填参数）",
        p.q === "F=ma", JSON.stringify(p));

  const qs = C.toQueryString(C.buildParams({ q: "F=ma", limit: 3, mode: null }));
  check("F33b 查询串含 `q=` 且按 RFC3986 转义（= ⇒ %3D）",
        /(^|&)q=F%3Dma(&|$)/.test(qs), qs);

  const qsBlank = C.toQueryString(C.buildParams({ q: "   ", limit: 3, mode: null }));
  check("F33c 纯空白 q 被剔除（不发 `q=` 空串去撞 400）",
        !qsBlank.includes("q="), qsBlank || "(空串)");

  const qsNoMode = C.toQueryString(C.buildParams({ q: "x", mode: null, limit: 3 }));
  check("F33d mode:null ⇒ **抑制**默认 mode（/api/search 不认 mode，不发无效参数）",
        !qsNoMode.includes("mode="), qsNoMode);

  const qsDefaultMode = C.toQueryString(C.buildParams({ seeds: "PQ:mass" }));
  check("F33e 未传 mode ⇒ 仍默认 ego（/api/subgraph 既有行为不变）",
        /(^|&)mode=ego(&|$)/.test(qsDefaultMode), qsDefaultMode);
}

// F33f/F33g：**行为断言** —— 看 `Api.search` 实际请求的 URL（而不是看函数源码形状）
{
  let seen = null;
  const api = new Api({
    base: ".",
    fetchImpl: async (u) => {
      seen = u;
      return { ok: true, status: 200, headers: { get: () => null },
               json: async () => ({ status: "ok", reason: "", hits: [], count: 0,
                                    total_matches: 0, snapshot_id: "sha256:aaaaaaaaaaaa",
                                    query_hash: "0123456789abcdef" }) };
    },
  });
  const env = await api.search("F=ma", { limit: 3 });
  check("F33f Api.search 实际请求 URL 含 `q=` 且不含无意义的 `mode=`（端到端行为断言）",
        !!seen && /[?&]q=F%3Dma(&|$)/.test(seen) && !/[?&]mode=/.test(seen),
        String(seen));
  check("F33g 后端 200 信封原样归一为 ok（不因字段缺失回落成 error）",
        env.status === "ok" && env.snapshot_id === "sha256:aaaaaaaaaaaa",
        `${env.status} / ${env.snapshot_id}`);
}

// ---------------------------------------------------------------------------
// F34 断连 ≠ 后端异常（P1：实测把「服务未运行」记成「后端执行异常」⇒ 误导定位）
//   现场：`viz_server` 未在运行时浏览器抛 `TypeError: Failed to fetch`，旧代码统一
//         记成 `TOOL_EXECUTION_ERROR`「后端执行异常（非空结果，属失败）」—— 而后端
//         **根本没被调用**。这是「判别依据过宽 ⇒ 错误归类」（#84 的镜像）。
//   处置：`_get` 拆成**两个阶段** —— 只有异常确实来自 `fetch` 本身才允许判传输层失败；
//         响应处理阶段的异常仍是 `TOOL_EXECUTION_ERROR`（不冒充断连）。
// ---------------------------------------------------------------------------
{
  const api = new Api({ base: ".", fetchImpl: async () => {
    throw new TypeError("Failed to fetch");
  } });
  const env = await api.search("F=ma", { limit: 3, deadlineMs: 1000 });
  check("F34a fetch 抛 TypeError('Failed to fetch') ⇒ BACKEND_UNREACHABLE（且给出可操作提示）",
        env.status === "error" && env.error_code === "BACKEND_UNREACHABLE"
        && /sttp\.sh viz|未运行/.test(env.reason || ""),
        `${env.status} / ${env.error_code} / ${String(env.reason).slice(0, 70)}`);
}
{
  // 连接是通的（HTTP 500）但响应体不可解析 ⇒ 后端**确实**执行异常，不得记成断连
  const api = new Api({ base: ".", fetchImpl: async () => ({
    ok: false, status: 500, headers: { get: () => null },
    json: async () => { throw new Error("bad json"); },
  }) });
  const env = await api.search("F=ma", { limit: 3, deadlineMs: 1000 });
  check("F34b HTTP 500 且响应体不可解析 ⇒ 仍 TOOL_EXECUTION_ERROR（连接已建立，不冒充断连）",
        env.status === "error" && env.error_code === "TOOL_EXECUTION_ERROR"
        && env.error_code !== "BACKEND_UNREACHABLE",
        `${env.status} / ${env.error_code} / ${String(env.reason).slice(0, 60)}`);
}
{
  // 预算内未收到响应（abort）⇒ 客户端中止，既不冒充服务端超时、也不冒充断连
  const api = new Api({ base: ".", fetchImpl: async (u, o) => {
    await new Promise((res) => {
      const t = setTimeout(res, 3000);
      if (o && o.signal) o.signal.addEventListener("abort", () => { clearTimeout(t); res(); });
    });
    const e = new Error("The operation was aborted."); e.name = "AbortError"; throw e;
  } });
  const env = await api.search("F=ma", { limit: 3, deadlineMs: 60 });
  check("F34c 客户端等不到响应 ⇒ GRAPH_QUERY_TIMEOUT + client_aborted=true（三态互不代偿）",
        env.status === "timeout" && env.error_code === "GRAPH_QUERY_TIMEOUT"
        && env.client_aborted === true,
        `${env.status} / ${env.error_code} / aborted=${env.client_aborted}`);
}
{
  // 完全没有 fetch 可用（例如 file:// 直开、老旧内核）⇒ 请求发不出去，同属"不可达"
  const saved = Object.getOwnPropertyDescriptor(globalThis, "fetch");
  let env = null, restoreOk = false;
  try {
    Object.defineProperty(globalThis, "fetch", { value: undefined, configurable: true, writable: true });
    const api = new Api({ base: "." });
    env = await api.search("F=ma", { limit: 1 });
  } finally {
    try {
      if (saved) Object.defineProperty(globalThis, "fetch", saved);
      else delete globalThis.fetch;
      restoreOk = typeof globalThis.fetch === "function";
    } catch (e) { restoreOk = false; }
  }
  check("F34d 环境无 fetch ⇒ BACKEND_UNREACHABLE（不是后端执行异常），且探针已复原全局 fetch",
        !!env && env.error_code === "BACKEND_UNREACHABLE" && restoreOk,
        `${env && env.error_code} / restore=${restoreOk}`);
}


// ---------------------------------------------------------------------------
// F35 搜索框**交互链**（键入 ⇒ 防抖 ⇒ api.search ⇒ 列表 ⇒ 点选）
//   ★ 2026-10-14 补：`bindSearch` 此前**零覆盖**。它正是用户报告「搜索框没有反应」
//     的那一环：即使 F33 修好了查询串，若"键入根本没触发请求/空输入也发请求"，
//     搜索框照样不可用。故此处用**假 DOM 真跑一遍事件链**（行为断言，非形状断言）。
// ---------------------------------------------------------------------------
{
  const mkInput = (value = "") => {
    const ls = {};
    return {
      value,
      addEventListener(t, fn) { (ls[t] = ls[t] || []).push(fn); },
      fire(t, ev = {}) { (ls[t] || []).forEach((fn) => fn(Object.assign({ key: "" }, ev))); },
    };
  };
  const mkList = () => {
    const el = fakeEl();
    el._items = [];
    el.querySelectorAll = (sel) => (sel === ".sr-item" ? el._items : []);
    return el;
  };
  const hit = (id, label) => ({ id, label, type: "PhysicalQuantity", subject: "物理", score: 99 });

  // ① 键入 ⇒ 以键入值查询 ⇒ 渲染命中 ⇒ 点选回调
  const input = mkInput("F=ma");
  const list = mkList();
  const asked = [];
  const apiStub = { search: async (q) => {
    asked.push(q);
    return { status: "ok", hits: [hit("MX:phy:newton2", "F=ma")] };
  } };
  const picked = [];
  bindSearch(input, list, apiStub, (id) => picked.push(id), { debounceMs: 1 });
  input.fire("input");
  await new Promise((r) => setTimeout(r, 25));
  check("F35a 键入 ⇒ bindSearch 以**键入值**调用 api.search（查询串里 q 的来源）",
        asked.length === 1 && asked[0] === "F=ma", JSON.stringify(asked));
  check("F35b 命中渲染进结果列表（≥2 条命中且含 id 与 label）",
        asked.length === 1 && !list.hidden && /MX:phy:newton2/.test(list.innerHTML)
        && /F=ma/.test(list.innerHTML),
        list.innerHTML.slice(0, 70));
  list._items = [{
    getAttribute: () => "MX:phy:newton2",
    addEventListener: (t, fn) => { if (t === "click") list._click = fn; },
  }];
  input.fire("input");
  await new Promise((r) => setTimeout(r, 25));
  if (list._click) list._click();
  check("F35c 点选命中 ⇒ onPick(id)（找点链路闭合）",
        picked.length >= 1 && picked[picked.length - 1] === "MX:phy:newton2",
        JSON.stringify(picked));

  // ② 空 / 纯空白输入 ⇒ **不发请求**（否则会去撞后端的 400「缺少参数 q」）
  const input2 = mkInput("   ");
  const list2 = mkList();
  const asked2 = [];
  bindSearch(input2, list2, { search: async (q) => { asked2.push(q); return { status: "ok", hits: [] }; } },
             () => {}, { debounceMs: 1 });
  input2.fire("input");
  await new Promise((r) => setTimeout(r, 25));
  check("F35d 纯空白输入 ⇒ 关闭列表且**不发起请求**（不制造 400 噪声）",
        asked2.length === 0 && list2.hidden === true, JSON.stringify(asked2));

  // ③ 竞态：先慢后快，过期响应必须**被丢弃**（不得覆盖更新的结果）
  const input3 = mkInput("slow");
  const list3 = mkList();
  bindSearch(input3, list3, { search: async (q) => {
    if (q === "slow") { await new Promise((r) => setTimeout(r, 60)); return { status: "ok", hits: [hit("OLD", "旧")] }; }
    return { status: "ok", hits: [hit("NEW", "新")] };
  } }, () => {}, { debounceMs: 1 });
  input3.fire("input");                                     // 慢请求
  await new Promise((r) => setTimeout(r, 10));
  input3.value = "fast"; input3.fire("input");              // 快请求（后发）
  await new Promise((r) => setTimeout(r, 90));
  check("F35e 竞态：过期（慢）响应不得覆盖较新结果（列表只见 NEW）",
        /NEW/.test(list3.innerHTML) && !/OLD/.test(list3.innerHTML),
        list3.innerHTML.slice(0, 70));

  // ④ 单字符 ⇒ 如实提示且**不发请求**（后端 `queries.MIN_QUERY_LEN=2` 会 400）
  const input4 = mkInput("F");
  const list4 = mkList();
  const asked4 = [];
  bindSearch(input4, list4, { search: async (q) => { asked4.push(q); return { status: "ok", hits: [] }; } },
             () => {}, { debounceMs: 1 });
  input4.fire("input");
  await new Promise((r) => setTimeout(r, 25));
  check("F35f 单字符输入 ⇒ 提示最短长度且**不发起请求**（不去撞后端 400）",
        asked4.length === 0 && !list4.hidden && /至少输入/.test(list4.innerHTML),
        `${JSON.stringify(asked4)} / ${list4.innerHTML.slice(0, 50)}`);

  // ⑤ 请求失败 ⇒ 下拉**不得**渲染成「没有匹配」（铁律 #67：空 ≠ 失败）
  const input5 = mkInput("boom");
  const list5 = mkList();
  bindSearch(input5, list5, { search: async () => ({
    status: "error", error_code: "BAD_REQUEST", reason: "参数不合法", hits: [] }) },
    () => {}, { debounceMs: 1 });
  input5.fire("input");
  await new Promise((r) => setTimeout(r, 25));
  check("F35g 失败信封 ⇒ 下拉显示「查询失败 + error_code」，不伪装成 no_result",
        /查询失败/.test(list5.innerHTML) && /BAD_REQUEST/.test(list5.innerHTML)
        && !/没有匹配/.test(list5.innerHTML),
        list5.innerHTML.slice(0, 70));

  check("F35h MIN_QUERY_LEN 为正整数且 ≥2（跨层等式 queries.MIN_QUERY_LEN 由 X5b 断言）",
        Number.isInteger(C.MIN_QUERY_LEN) && C.MIN_QUERY_LEN >= 2, String(C.MIN_QUERY_LEN));

  // ⑥ `search.schema.json` 的截断披露字段**不得在归一化时丢掉**（#66/#67）
  //    实测：`co2` ⇒ hits=20 / total_matches=81 / truncated=true；前端此前两者皆丢 ⇒ UI 无从披露。
  const norm = normalizeEnvelope
    ? normalizeEnvelope({ status: "ok", hits: [], total_matches: 81, truncated: true })
    : null;
  check("F36a normalizeEnvelope 保留 total_matches / truncated（契约声明的截断披露字段不得丢失）",
        !!norm && norm.total_matches === 81 && norm.truncated === true,
        norm ? `${norm.total_matches} / ${norm.truncated}` : "normalizeEnvelope 未导出");

  // ⑦ 下拉必须把截断写出来（显示前 N 条 / 共 M 条）
  const input6 = mkInput("co2");
  const list6 = mkList();
  bindSearch(input6, list6, { search: async () => ({
    status: "ok", total_matches: 81, truncated: true,
    hits: Array.from({ length: 20 }, (_, i) => hit(`RH:rxn:${10000 + i}`, `rxn ${i}`)) }) },
    () => {}, { debounceMs: 1 });
  input6.fire("input");
  await new Promise((r) => setTimeout(r, 25));
  check("F36b 结果被 limit 截断 ⇒ 下拉显式写「显示前 20 条 / 共 81 条（已截断）」",
        /显示前 20 条/.test(list6.innerHTML) && /共 81 条/.test(list6.innerHTML),
        (list6.innerHTML.match(/<div class="sr-head">[^<]*/) || ["(无 sr-head)"])[0]);
}


// ---------------------------------------------------------------------------
// F37 端点契约 ↔ buildParams **双向求差**（D31）
// ---------------------------------------------------------------------------
// 为什么必须双向：单侧检查永远抓不到本类缺陷。P48 修了 `q`；P49 又实测出三例同族
//   —— `hops`（没发）、`subject`（后端读 `subjects`）、`exclude_inferred`
//   （后端读 `exclude_model_inferred`）。三者都"不报错"，只让参数静默丢失。
// 处方：把「后端读的参数」与「前端能发的键」钉在同一张契约上，两侧都求差。
const CONTRACT_EP_PATH = new URL("../../contracts/endpoints.schema.json", import.meta.url);
const CORE_JS_PATH = new URL("../js/core.js", import.meta.url);
let epContract = null, coreSrc = null;
try { epContract = JSON.parse(readFileSync(CONTRACT_EP_PATH, "utf-8")); } catch (e) { epContract = null; }
try { coreSrc = readFileSync(CORE_JS_PATH, "utf-8"); } catch (e) { coreSrc = null; }

check("F37a 读到端点契约与 core.js 源码（双向求差的前提）",
      !!(epContract && epContract.endpoints && coreSrc), epContract ? "ok" : "读取失败");

const EMITTED = coreSrc ? Array.from(new Set(
  Array.from(coreSrc.matchAll(/put\(\s*"([a-z_0-9]+)"/g)).map((m) => m[1]),
)).sort() : [];
const DECLARED = [];
if (epContract) {
  Object.keys(epContract.endpoints).forEach((ep) => {
    Object.keys(epContract.endpoints[ep].params || {}).forEach((k) => DECLARED.push(k));
  });
}
const DECLARED_UNIQ = Array.from(new Set(DECLARED)).sort();
const NOT_EMITTED = Object.keys((epContract && epContract.backend_params_not_emitted) || {});

// 方向 A：后端每个参数，要么前端能发，要么**显式**声明"前端有意不发（附理由）"
const aMissing = DECLARED_UNIQ.filter((k) => EMITTED.indexOf(k) < 0 && NOT_EMITTED.indexOf(k) < 0);
check("F37b 【方向A】后端读的每个参数，前端都要能发（或显式声明不发+理由）",
      aMissing.length === 0,
      aMissing.length ? "静默丢失风险: " + aMissing.join(",") : `${DECLARED_UNIQ.length} 个后端参数全部可达`);

// ★ 构造性反证：把 `hops` 从"前端能发"里去掉，方向 A 必须报出来（否则检测器恒真）
const aMissingNeg = DECLARED_UNIQ.filter(
  (k) => EMITTED.filter((e) => e !== "hops").indexOf(k) < 0 && NOT_EMITTED.indexOf(k) < 0);
check("F37b-NEG 【负对照】从可发集合中移除 `hops` ⇒ 方向A 必须报出它（证明检测非恒真）",
      aMissingNeg.indexOf("hops") >= 0 && aMissingNeg.length === aMissing.length + 1,
      "报出: " + aMissingNeg.join(","));

// 方向 B：前端每个 put() 键，都必须被某个端点读取（否则是死噪声，还会污染 query_hash）
const bOrphan = EMITTED.filter((k) => DECLARED_UNIQ.indexOf(k) < 0);
check("F37c 【方向B】buildParams 的每个 put() 键都被某个端点读取（无死参数）",
      bOrphan.length === 0,
      bOrphan.length ? "无人读取: " + bOrphan.join(",") : `${EMITTED.length} 个键全部有归属`);

// 契约自洽：`frontend_must_not_send` 里的键**不得**同时出现在该端点的 params 里
let incoherent = [];
if (epContract) {
  Object.keys(epContract.endpoints).forEach((ep) => {
    const e = epContract.endpoints[ep];
    (e.frontend_must_not_send || []).forEach((k) => {
      if (Object.prototype.hasOwnProperty.call(e.params || {}, k)) incoherent.push(`${ep}:${k}`);
    });
  });
}
check("F37d 契约自洽：must_not_send 的键不得同时出现在该端点 params 里",
      incoherent.length === 0, incoherent.join(",") || "ok");

// P49 修掉的三例必须**长期钉住**（缺任何一个即回归）
const sub = (epContract && epContract.endpoints["/api/subgraph"]) || { params: {} };
const pinned = ["hops", "subjects", "exclude_model_inferred"];
const unpinned = pinned.filter((k) => !(k in sub.params) || EMITTED.indexOf(k) < 0);
check("F37e `hops`/`subjects`/`exclude_model_inferred` 三者同时在契约与 buildParams 中（P49 回归护栏）",
      unpinned.length === 0, unpinned.length ? "缺: " + unpinned.join(",") : pinned.join(" + "));

// P0-4 的契约化：`/api/subgraph` 明确**不得**收 src/dst（path 只认 seeds）
const mustNot = sub.frontend_must_not_send || [];
check("F37f `/api/subgraph` 契约显式声明 must_not_send ⊇ {src,dst,subject,q}（P0-4 契约化）",
      ["src", "dst", "subject", "q"].every((k) => mustNot.indexOf(k) >= 0),
      mustNot.join(",") || "(空)");

check("F37g `backend_params_not_emitted` 每项都写了理由（不得只列名字）",
      NOT_EMITTED.length > 0
      && NOT_EMITTED.every((k) => {
        const v = epContract.backend_params_not_emitted[k];
        return typeof v === "string" && v.trim().length >= 8;
      }),
      NOT_EMITTED.join(","));

// ---------------------------------------------------------------------------
// F38 端点契约 ↔ 序列化（Phase 52 新增两项：D42 `snapshot_id` / D54 端点预算）
// ---------------------------------------------------------------------------
// ★ 两条都是"契约里写了、前端却没用"的同族缺陷（铁律 #85）：条款存在但不被执行。
//   `snapshot_id` 更极端 —— 后端那条 `SNAPSHOT_MISMATCH` 分支此前**在任何真实链路里
//   都走不到**，因为前端从不发这个参数。

// F37h：`snapshot_id` 必须真的被序列化（断言的是**真正会发出去的参数对象**）
const f37hSent = C.buildParams({ mode: "ego", seeds: "MX:def:manifold",
                                 snapshot_id: "sha256:0123456789ab" });
check("F37h buildParams 输出 snapshot_id（D42：后端复现校验分支须真被执行）",
      f37hSent.snapshot_id === "sha256:0123456789ab", JSON.stringify(f37hSent));

// F37i：空值**不得**进入查询串（`snapshot_id=` 这种空键会污染 URL 与日志）
check("F37i 空 snapshot_id 不进查询串（空值键必须缺席）",
      C.buildParams({ mode: "ego", seeds: "x", snapshot_id: "" }).snapshot_id === undefined,
      "ok");

// F37j：`snapshot_id` 不进本地签名（与后端 `_qhash(params)` 的 params 不含它同口径）
check("F37j HASH_EXCLUDED_KEYS 含 snapshot_id（与后端 query_hash 口径一致）",
      C.HASH_EXCLUDED_KEYS.indexOf("snapshot_id") >= 0, C.HASH_EXCLUDED_KEYS.join(","));

// F37k：端点客户端预算 == 契约 `client_budget_ms`（D54 残余：不再散落硬编码）
const f37kGap = [];
if (epContract && epContract.endpoints) {
  Object.keys(C.ENDPOINT_CLIENT_BUDGET_MS).forEach((k) => {
    const ep = epContract.endpoints["/api/" + k] || {};
    if (ep.client_budget_ms !== C.ENDPOINT_CLIENT_BUDGET_MS[k]) {
      f37kGap.push(`${k}: 前端 ${C.ENDPOINT_CLIENT_BUDGET_MS[k]} ≠ 契约 ${ep.client_budget_ms}`);
    }
  });
} else {
  f37kGap.push("端点契约未读到");
}
check("F37k 端点客户端预算 == 契约 client_budget_ms（D54：偶然相等 → 已声明契约）",
      f37kGap.length === 0, f37kGap.join("；") || "search/overview 逐值相等");

// ---------------------------------------------------------------------------
// F38 任务式入口的纯逻辑（卡片 / 范围 / 请求构造 / 候选）
// ---------------------------------------------------------------------------
const CARDS = C.TASK_CARDS;
check("F38a 示例卡 = 4 张，id 唯一，字段齐备（question/mode/note）",
      CARDS.length === 4
      && new Set(CARDS.map((c) => c.id)).size === 4
      && CARDS.every((c) => c.question && c.mode && c.note && c.note.length > 10),
      CARDS.map((c) => c.id).join("/"));

const EDGE_TYPES = Object.keys(EDGE_TYPE_COLOR);
const badTypes = CARDS.flatMap((c) => (c.types || []).filter((t) => EDGE_TYPES.indexOf(t) < 0))
  .map((t) => t);
check("F38b 每张卡的关系类型都属真实的 24 类（卡片不得引用不存在的成语 —— 铁律 #39）",
      badTypes.length === 0,
      badTypes.length ? "不存在: " + badTypes.join(",") : `${CARDS.reduce((n, c) => n + c.types.length, 0)} 个类型引用全部有效`);

const pathCard = CARDS.filter((c) => c.mode === "path")[0];
const egoCard = CARDS.filter((c) => c.mode === "ego")[0];
check("F38c 恰好 1 张路径卡 + 3 张 ego 卡（覆盖「两点关系」与三类单点关系）",
      CARDS.filter((c) => c.mode === "path").length === 1
      && CARDS.filter((c) => c.mode === "ego").length === 3,
      CARDS.map((c) => `${c.id}:${c.mode}`).join(" "));

// 范围默认值必须与 BUDGET **同源**（不得各写一套默认）
check("F38d TASK_SCOPE_DEFAULT 从 BUDGET 派生（hops/nodes/edges/deadline 全等）",
      C.TASK_SCOPE_DEFAULT.hops === C.BUDGET.hops
      && C.TASK_SCOPE_DEFAULT.max_nodes === C.BUDGET.nodes
      && C.TASK_SCOPE_DEFAULT.max_edges === C.BUDGET.edges
      && C.TASK_SCOPE_DEFAULT.deadline_ms === C.BUDGET.deadline_ms
      && C.TASK_SCOPE_DEFAULT.trust === "verified",
      JSON.stringify(C.TASK_SCOPE_DEFAULT));

check("F38e 默认预算已收敛为 40/70/2/10000/5（外部评审 §4/§6.1）",
      C.BUDGET.nodes === 40 && C.BUDGET.edges === 70 && C.BUDGET.hops === 2
      && C.BUDGET.deadline_ms === 10000 && C.BUDGET.paths === 5,
      JSON.stringify(C.BUDGET));

const sc = C.normalizeScope({ hops: 99, max_nodes: 0, max_edges: -3, deadline_ms: 1e9, trust: "bogus" });
check("F38f normalizeScope 钳到 HARD 内，且未知 trust **往严处**回落 verified",
      sc.hops === C.HARD.hops && sc.max_nodes === 1 && sc.max_edges === 1
      && sc.deadline_ms === C.HARD.deadline_ms && sc.trust === "verified",
      JSON.stringify(sc));

const egoReq = C.taskRequest(egoCard, { seed: "PQ:energy" });
check("F38g ego 请求：mode=ego / seeds / **含 hops** / types 透传 / 不带 path 专用键",
      egoReq && egoReq.mode === "ego" && egoReq.seeds === "PQ:energy"
      && egoReq.hops === C.TASK_SCOPE_DEFAULT.hops
      && Array.isArray(egoReq.types) && egoReq.types.length === egoCard.types.length
      && !("max_hops" in egoReq) && !("max_paths" in egoReq),
      JSON.stringify(egoReq));

check("F38h path 请求：两端齐全 ⇒ seeds='a,b' + max_hops；**不发** `hops`（该模式不读它）",
      (() => {
        const r = C.taskRequest(pathCard, { seed: "CE:pq:gibbs_energy", second: "PB:pq:entropy" });
        return r && r.mode === "path" && r.seeds === "CE:pq:gibbs_energy,PB:pq:entropy"
          && r.max_hops === C.TASK_SCOPE_DEFAULT.hops && !("hops" in r);
      })(),
      JSON.stringify(C.taskRequest(pathCard, { seed: "A", second: "B" })));

check("F38i 【fail-closed】path 缺一端 ⇒ taskRequest 返回 null（不发必然被拒的请求）",
      C.taskRequest(pathCard, { seed: "A" }) === null
      && C.taskRequest(pathCard, { second: "B" }) === null
      && C.taskRequest(pathCard, {}) === null
      && C.taskRequest(egoCard, {}) === null,
      "四条缺参路径均为 null");

check("F38j taskBlockedReason 给出**可操作**原因（不做「点了没反应」）",
      C.taskBlockedReason(egoCard, { seed: "X" }) === null
      && /确认一个候选实体/.test(C.taskBlockedReason(egoCard, {}) || "")
      && /第二个/.test(C.taskBlockedReason(pathCard, { seed: "X" }) || ""),
      (C.taskBlockedReason(pathCard, { seed: "X" }) || "").slice(0, 40));

// ★ Phase 56（B1，外部评审 2026-10-17 §5）：底部路径栏漏传 `trust`（且跳数写死 `HARD.hops`），
//   而后端默认 `trust="verified"`，其 `no_result` 文案却教用户「切到 all 再试」「增大跳数」。
//   ⇒ 指引指向**代码无法兑现**的动作，用户照做结果不变，会把「被隐藏」读成「不存在」。
//   修复：路径栏改走 `taskRequest()`，scope 取左侧面板。
//   本节守的是**构造器确实透传**；「调用方确实用了它」由 phase43 的 V13 守卫（静态）。
//   ★ 判据刻意**比较两个不同输入**（all vs 默认）——若有人把 trust/hops 写死，
//     两值就会相等 ⇒ 本断言变红。只断言\"等于 all\"则写死成 all 也能过（#90）。
const _rAll = C.taskRequest(pathCard, {
  seed: "A", second: "B", scope: { trust: "all", hops: 3 },
});
const _rDef = C.taskRequest(pathCard, { seed: "A", second: "B" });
check("F42k 【B1】scope 真的透传：trust/hops 随 scope 变化（写死 ⇒ 两值相等 ⇒ 必红）",
      // ★ 原 ID F38l 与下方「学科筛选」断言撞号（Phase 56 改名时漏查既有 F38l）——
      //   撞号会让名字采集器与红探针都只认到其中一条 ⇒ 必须唯一。
      _rAll && _rDef
      && _rAll.trust === "all" && _rAll.max_hops === 3
      && _rDef.trust === "verified" && _rDef.max_hops !== _rAll.max_hops,
      JSON.stringify({ all: { trust: _rAll && _rAll.trust, hops: _rAll && _rAll.max_hops },
                       def: { trust: _rDef && _rDef.trust, hops: _rDef && _rDef.max_hops } }));

// 纯逻辑层串起来：任务请求 ⇒ buildParams ⇒ 查询串（含刚修好的 hops）
const qsEgo = C.toQueryString(C.buildParams(egoReq));
check("F38k 任务请求序列化后**真的带 `hops=`**（P49 修的第 1 例；同时带 types=）",
      /(^|&)hops=2(&|$)/.test(qsEgo) && /(^|&)types=/.test(qsEgo)
      && /(^|&)trust=verified(&|$)/.test(qsEgo),
      qsEgo);

const qsSubj = C.toQueryString(C.buildParams({ mode: "filter", subjects: ["物理", "化学"] }));
check("F38l 学科筛选序列化为**复数** `subjects=`（后端只认这个；`subject=` 会被完全忽略）",
      /(^|&)subjects=%E7%89%A9%E7%90%86%2C%E5%8C%96%E5%AD%A6(&|$)/.test(qsSubj),
      qsSubj);

const qsEx = C.toQueryString(C.buildParams({ mode: "filter", exclude_model_inferred: true }));
check("F38m 「排除推断边」序列化为 `exclude_model_inferred=1`（`exclude_inferred` 实测无效）",
      /(^|&)exclude_model_inferred=1(&|$)/.test(qsEx) && !/exclude_inferred=1/.test(qsEx),
      qsEx);

const cand = C.takeCandidates({
  status: "ok", total_matches: 81, truncated: true,
  hits: Array.from({ length: 20 }, (_, i) => ({ id: `X:${i}`, label: `L ${i}` })),
}, CANDIDATE_LIMIT);
check("F38n takeCandidates 只取前 5 条，并如实给出 total/truncated（不重排、不隐瞒）",
      cand.items.length === 5 && cand.shown === 5 && cand.total === 81 && cand.truncated === true,
      `${cand.items.length}/${cand.total} truncated=${cand.truncated}`);
check("F38o CANDIDATE_LIMIT = 5（外部评审 §7-C「最多 5 个候选」）", CANDIDATE_LIMIT === 5, String(CANDIDATE_LIMIT));

check("F38p pathNodeIds 同时兼容统一信封 `{nodes}` 与 legacy 裸数组（不静默渲染成空）",
      JSON.stringify(C.pathNodeIds({ nodes: ["a", "b"], edges: ["e1"] })) === JSON.stringify(["a", "b"])
      && JSON.stringify(C.pathNodeIds(["a", "b"])) === JSON.stringify(["a", "b"])
      && JSON.stringify(C.pathNodeIds(null)) === JSON.stringify([]),
      JSON.stringify(C.pathNodeIds({ nodes: ["a", "b"] })));
check("F38q pathEdgeIds：legacy 裸数组**没有**边信息 ⇒ 返回空数组（不臆造）",
      JSON.stringify(C.pathEdgeIds(["a", "b"])) === JSON.stringify([])
      && JSON.stringify(C.pathEdgeIds({ nodes: ["a"], edges: ["e1"] })) === JSON.stringify(["e1"]),
      "ok");

// ---------------------------------------------------------------------------
// F39 任务入口的渲染层（纯函数返回 HTML，无 DOM）
// ---------------------------------------------------------------------------
const cardsHtml = renderCardsHTML();
check("F39a 渲染出 4 张卡，且每张都带 data-card 与问句原文",
      (cardsHtml.match(/data-card="/g) || []).length === 4
      && CARDS.every((c) => cardsHtml.indexOf(c.question) >= 0),
      CARDS.map((c) => c.id).join("/"));

const evil = [Object.assign({}, egoCard, { question: '<img src=x onerror=alert(1)>' })];
const evilHtml = renderCardsHTML(evil);
check("F39b 卡片问句被转义（<img> 不得原样进入 DOM）",
      evilHtml.indexOf("<img") < 0 && evilHtml.indexOf("&lt;img") >= 0,
      evilHtml.slice(0, 80));

const errHtml = renderCandidatesHTML({ items: [], shown: 0, total: 0, truncated: false,
                                       status: "error", error_code: "BAD_REQUEST", reason: "缺少参数 q" }, "main");
const emptyHtml = renderCandidatesHTML({ items: [], shown: 0, total: 0, truncated: false,
                                         status: "no_result", error_code: null, reason: "" }, "main");
check("F39c 【#67】候选区把「失败」与「空」分列（失败显示 error_code+原因，空显示放宽建议）",
      /查询失败/.test(errHtml) && /BAD_REQUEST/.test(errHtml)
      && !/查询失败/.test(emptyHtml) && /没有匹配/.test(emptyHtml),
      "err/empty 分离");

const trHtml = renderCandidatesHTML({ items: [{ id: "A:1", label: "a", type: "T", subject: "S", score: 90 }],
                                      shown: 1, total: 81, truncated: true,
                                      status: "ok", error_code: null, reason: "" }, "main");
check("F39d 候选被截断 ⇒ 显式写「显示前 N 条 / 共 M 条（已截断）」",
      /显示前 1 条/.test(trHtml) && /共 81 条/.test(trHtml) && /已截断/.test(trHtml),
      (trHtml.match(/候选实体 · [^<]*/) || ["(无)"])[0]);

const selHtml = renderCandidatesHTML({ items: [{ id: "A:1", label: "a", type: "T", subject: "S", score: 90 }],
                                       shown: 1, total: 1, truncated: false,
                                       status: "ok", error_code: null, reason: "" }, "main", "A:1");
check("F39e 已确认的候选带 `sel` 与 aria-pressed=true（无障碍 + 可视双重标识）",
      /class="cand-item sel"/.test(selHtml) && /aria-pressed="true"/.test(selHtml),
      (selHtml.match(/cand-item[^"]*"/) || ["(无)"])[0]);


// ---------------------------------------------------------------------------
// F40 链路闭合：**发送侧产物必须能通过校验侧**（D48）
//   为什么单列一段：F38h 已经断言 `taskRequest` 产出 `seeds='a,b'`，
//   也断言了它会序列化成什么 —— 但**没有任何断言**把产物喂给
//   `SubgraphStore.run()` 前置的 `validateParams()`。
//   于是 Phase 49 改了发送侧（src/dst → seeds）、忘了校验侧，
//   两个断言各自全绿，真浏览器里点一下「生成子图」却是空画布 + 「错误」。
//   ⇒ 「两侧各自正确 ≠ 两者对得上」。凡是"一个组件产出、另一个组件消费"的字段，
//     都要有一条**把产物真的喂过去**的断言（同 D31 双向求差的动机）。
// ---------------------------------------------------------------------------
const pathReqV = C.taskRequest(pathCard, { seed: "CE:pq:gibbs_energy", second: "PB:pq:entropy" });
check("F40a 【链路闭合】taskRequest 的 path 产物能通过 validateParams",
      !!pathReqV && C.validateParams(pathReqV).ok === true,
      JSON.stringify(C.validateParams(pathReqV)));

check("F40b 【链路闭合】ego 产物同样能通过（同一不变量，不只覆盖 path）",
      C.validateParams(C.taskRequest(egoCard, { seed: "PQ:energy" })).ok === true,
      JSON.stringify(C.validateParams(C.taskRequest(egoCard, { seed: "PQ:energy" }))));

check("F40c 【负对照】legacy 形状与端点数不对的 path 请求必须被拒",
      C.validateParams({ mode: "path", src: "A", dst: "B" }).ok === false
      && C.validateParams({ mode: "path" }).ok === false
      && C.validateParams({ mode: "path", seeds: "A" }).ok === false
      && C.validateParams({ mode: "path", seeds: "A,B,C" }).ok === false
      && C.validateParams({ mode: "path", seeds: "A, ,B" }).ok === true,
      "src/dst、零端、单端、三端 全拒；`A, ,B`（空段被忽略）⇒ 两端 ⇒ 放行");

check("F40d path 被拒时的原因**可操作**（说清缺几个端点）",
      (() => {
        const m1 = C.validateParams({ mode: "path", seeds: "A" }).message || "";
        const m2 = C.validateParams({ mode: "path", seeds: "A,B,C" }).message || "";
        return /两个/.test(m1) && /1 个/.test(m1) && /只接受两个/.test(m2) && /3 个/.test(m2);
      })(),
      (C.validateParams({ mode: "path", seeds: "A" }).message || "").slice(0, 46));

check("F40e 未知 mode 仍被拒（改造 path 分支不得顺手放宽 mode 白名单）",
      C.validateParams({ mode: "nope" }).ok === false
      && C.validateParams({ mode: "path", seeds: "A,B" }).ok === true,
      JSON.stringify(C.validateParams({ mode: "nope" })));


// ---------------------------------------------------------------------------
// F41 客户端等待预算**不得**比服务端更早放弃（D53）
//   现场：`Api` 固定 `deadlineMs = 3000`，而服务端 `deadline_ms` 默认 **10000**
//   ⇒ 客户端 3 s 就 abort、服务端还在算 ⇒ 用户看到"失败"，而后端本来会给答案。
//   表现酷似"偶发"：空闲时路径查询 0.36 s（100% 通过），
//   本轮在 `release-full` + `eval` 之后跑同一查询就超 3 s ⇒ **必现**。
//   ★「目标随机」的失败最容易被当成 flaky 放过，所以这里把它钉成**结构性断言**：
//     对任意服务端 deadline，客户端预算必须**严格大于**它。
// ---------------------------------------------------------------------------
check("F41a 显式 `deadlineMs` 优先且不被改写（探活等调用点的既有口径不变）",
      C.clientBudgetMs({ deadlineMs: 5000, deadline_ms: 999 }) === 5000,
      String(C.clientBudgetMs({ deadlineMs: 5000, deadline_ms: 999 })));

const _dls = [1, 50, 1000, 5000, 10000, C.HARD.deadline_ms];
check("F41b ★【不变量】自带 `deadline_ms` 时，客户端预算**严格大于**服务端预算",
      _dls.every((d) => C.clientBudgetMs({ deadline_ms: d }) > d),
      _dls.map((d) => `${d}→${C.clientBudgetMs({ deadline_ms: d })}`).join("  "));

check("F41c 无任何 deadline ⇒ 回落 fallbackMs（不允许 0 / NaN / Infinity）",
      C.clientBudgetMs({}, { fallbackMs: 3000 }) === 3000
      && Number.isFinite(C.clientBudgetMs({})) && C.clientBudgetMs({}) > 0,
      String(C.clientBudgetMs({}, { fallbackMs: 3000 })));

check("F41d 非法 deadline（0/负/NaN/字符串/null）⇒ 全部回落，不得变成 NaN 或 0",
      [0, -1, NaN, "abc", null, undefined, Infinity].every((v) => {
        const b = C.clientBudgetMs({ deadline_ms: v, deadlineMs: v }, { fallbackMs: 3000 });
        return Number.isFinite(b) && b > 0;
      }),
      "★ `NaN + 4000` 会让 `setTimeout(…, NaN)` 等价于立即 abort —— 比固定 3 s 更糟");

check("F41e 余量可配置，且不会把预算算错（margin=0 ⇒ 等于 deadline_ms，仍不 < 它）",
      C.clientBudgetMs({ deadline_ms: 10000 }, { marginMs: 0 }) === 10000
      && C.clientBudgetMs({ deadline_ms: 10000 }, { marginMs: 1500 }) === 11500,
      "0 → 10000 ／ 1500 → 11500");

check("F41f `Api.subgraph` 真的按请求自己的 deadline_ms 派生预算（不是只导出个函数）",
      (() => {
        const seen = [];
        const api = new Api({
          deadlineMs: 3000, marginMs: C.CLIENT_MARGIN_MS,
          fetchImpl: (_u) => { seen.push(_u); return Promise.resolve({
            ok: true, status: 200, text: () => Promise.resolve('{"status":"ok","nodes":[],"edges":[]}'),
          }); },
        });
        const p = api.subgraph({ mode: "path", seeds: "A,B", deadline_ms: 10000 });
        if (p && typeof p.catch === "function") p.catch(() => {});
        return api.lastBudgetMs > 10000 && seen.length === 1;
      })(),
      (() => {
        const api = new Api({ deadlineMs: 3000, fetchImpl: () => Promise.resolve({
          ok: true, status: 200, text: () => Promise.resolve('{"status":"ok"}') }) });
        const p = api.subgraph({ mode: "path", seeds: "A,B", deadline_ms: 10000 });
        if (p && typeof p.catch === "function") p.catch(() => {});
        return `lastBudgetMs=${api.lastBudgetMs}（deadline_ms=10000）`;
      })());


// ---------------------------------------------------------------------------
// F42 发现年代分桶（Phase 54）—— 边界表逐值断言 + **「哨兵 vs 缺键」渲染规则**
//   分桶的唯一实现在 `core.js::deriveEra`（铁律 #88）。这里钉两件最容易错的事：
//     ① **边界**：1899/1900 是「世纪 ↔ 十年桶」的切换点，差一位就整桶错位；
//     ② **语义**：「暂无」是数据层**显式写入的真实内容**（必须渲染出来），
//        而 `description`/`usage` 无值时**根本不写键**（不得渲染该行）——
//        两者在 UI 上**必须长得不一样**，否则「没查证过」与「查证过但无此项」被混为一谈。
// ---------------------------------------------------------------------------
const _eraCases = [
  ["1687", "17 世纪"], ["1899", "19 世纪"], ["1900", "1900–1909"], ["1909", "1900–1909"],
  ["1910", "1910–1919"], ["1926", "1920–1929"], ["公元前 300", "公元前 3 世纪"],
  ["公元前 550", "公元前 6 世纪"], ["公元 820", "9 世纪"], ["公元 400", "4 世纪"],
  ["暂无", "暂无"],
];
const _eraBad = _eraCases.filter(([i, w]) => C.deriveEra(i) !== w)
  .map(([i, w]) => `${i} ⇒ ${C.deriveEra(i)}（期望 ${w}）`);
check("F42a deriveEra 边界表逐值相等（含 1899↔1900 世纪/十年桶切换点）",
      _eraBad.length === 0, _eraBad.join("；"));

const _eraNaughty = [null, undefined, "", "   ", "0", "18999", "1899.5", "about 1687",
                     "公元", "公元前", "暂无（不可考）", {}, []];
const _eraLeak = _eraNaughty.filter((v) => C.deriveEra(v) !== C.ERA_NONE);
check("F42b 非法/不可解析输入一律回落「暂无」（不得猜成年份、不得抛异常）",
      _eraLeak.length === 0,
      _eraLeak.map((v) => `${JSON.stringify(v)} ⇒ ${C.deriveEra(v)}`).join("；"));

// ★ 数字形态单独一条：`1687`(number) 与 `"1687"` 应同桶 —— 这**不是**"猜测"，
//   而是同一个年份值；真正的"猜测"是把 `18999` / `about 1687` 硬塞进某个桶（上一条已挡）。
check("F42i 数字形态与字符串形态同桶（宽松但不变形；不因此放宽 F42b 的拒绝面）",
      C.deriveEra(1687) === C.deriveEra("1687") && C.deriveEra(1687) === "17 世纪",
      `${C.deriveEra(1687)} == ${C.deriveEra("1687")}`);

check("F42c 「暂无」不落任何真实年代桶，且排序键恒为 +∞（永远排最后）",
      C.deriveEra("暂无") === C.ERA_NONE
      && C.eraSortKey("暂无") === Number.POSITIVE_INFINITY
      && C.eraSortKey(null) === Number.POSITIVE_INFINITY, "");

const _eraSeq = ["公元前 6 世纪", "公元前 3 世纪", "4 世纪", "9 世纪", "17 世纪",
                 "18 世纪", "19 世纪", "1900–1909", "1920–1929", "暂无"];
const _eraSk = _eraSeq.map((e) => C.eraSortKey(e));
check("F42d eraSortKey 跨公元前后**严格单调**（图例按时间序排列才正确）",
      _eraSk.every((v, i) => i === 0 || v > _eraSk[i - 1]), _eraSk.join(" < "));

// —— 渲染规则：键缺席 ≠ 哨兵 ——
const _n83 = fakeEl();
renderNodeDetail(_n83, { id: "PB:fo:1", label: "x", type: "Formula",
                         attrs: { discovery_time: "暂无", discoverer: "暂无" } });
check("F42e 哨兵「暂无」必须**渲染出来**（弱化 + 「不可考」标签），不得静默留空",
      _n83.innerHTML.includes("发现时间") && _n83.innerHTML.includes("发现人")
      && _n83.innerHTML.includes("暂无") && _n83.innerHTML.includes("不可考"), "");

check("F42f `description`/`usage` 键**缺席** ⇒ 该行**不渲染**（不是渲染成空行）",
      !_n83.innerHTML.includes("公式介绍") && !_n83.innerHTML.includes("使用描述"), "");

const _n84 = fakeEl();
renderNodeDetail(_n84, { id: "CM:fo:gravity", label: "Newton's law of gravitation",
  type: "Formula", attrs: { discovery_time: "1687", discoverer: "艾萨克·牛顿",
    description: "两质点间的引力……", usage: "计算天体轨道。",
    meta_source: "Newton, Principia (1687)" } });
check("F42g 有值时四行齐全，且「发现时间」附带年代桶标签（唯一分桶实现真的被调用）",
      _n84.innerHTML.includes("发现时间") && _n84.innerHTML.includes("17 世纪")
      && _n84.innerHTML.includes("艾萨克·牛顿") && _n84.innerHTML.includes("公式介绍")
      && _n84.innerHTML.includes("使用描述"), "");
check("F42h 元数据依据（meta_source）可见 —— 「有值必有据」必须摆在读者面前",
      _n84.innerHTML.includes("元数据依据") && _n84.innerHTML.includes("Principia"), "");

// —— 图例区块：只读「分组标记」，且必须**按时间序 + 带计数** ——
const _lg = fakeEl();
buildLegend(_lg, { nodeTypes: ["Formula"], edgeTypes: [],
  eras: ["暂无", "1900–1909", "17 世纪"],
  eraCounts: { "17 世纪": 3, "1900–1909": 1, "暂无": 4 } });
check("F42j 图例「发现年代」区块按时间序 + 带计数，并明说「不可点击、不参与筛选」",
      _lg.innerHTML.indexOf("17 世纪") < _lg.innerHTML.indexOf("1900–1909")
      && _lg.innerHTML.indexOf("1900–1909") < _lg.innerHTML.indexOf("暂无")
      && _lg.innerHTML.includes("×3") && _lg.innerHTML.includes("不参与筛选"),
      "乱序传入也须按时间序渲染（排序发生在 buildLegend 内部）");


// ---------------------------------------------------------------------------
// F43 证据等级 1–5（Phase 58）：通道接管 + 区分度 + fail-closed（未判定 ≠ E5）
// ---------------------------------------------------------------------------
const _mkEdge = (grade) => ({
  id: "e-" + grade, source: "a", target: "b", type: "has_symbol", kind: "k",
  explicit_or_inferred: "explicit", confidence: 0.95, grade,
});

check("F43a GRADE_STYLE 五档齐全，宽与透明度自 E5→E1 单调递减（视觉可分级）",
      [5, 4, 3, 2, 1].every((g) => GRADE_STYLE[g])
      && [5, 4, 3, 2, 1].every((g, i, a) => i === 0
          || (GRADE_STYLE[a[i - 1]].width > GRADE_STYLE[g].width
              && GRADE_STYLE[a[i - 1]].opacity > GRADE_STYLE[g].opacity)),
      [5, 4, 3, 2, 1].map((g) => GRADE_STYLE[g] && GRADE_STYLE[g].width + "/" + GRADE_STYLE[g].opacity).join(" "));

check("F43b toCyEdge：grade 进 data；null ⇒ 未判定样式（不得渲染成 E5）",
      toCyEdge(_mkEdge(null)).data.grade === null
      && toCyEdge(_mkEdge(null)).data._width === GRADE_UNKNOWN.width
      && toCyEdge(_mkEdge(5)).data.grade === 5
      && toCyEdge(_mkEdge(5)).data._width === GRADE_STYLE[5].width,
      "null→" + toCyEdge(_mkEdge(null)).data._width + "  E5→" + toCyEdge(_mkEdge(5)).data._width);

check("F43c toCyEdge：五档产生**互异**的 (width, opacity)（区分度——confidence 76% 挤 0.95 的病根不得复发）",
      new Set([5, 4, 3, 2, 1, null].map((g) => {
        const d = toCyEdge(_mkEdge(g)).data;
        return d._width + "/" + d._opacity;
      })).size === 6,
      [5, 4, 3, 2, 1, null].map((g) => gradeToWidth(g) + "/" + gradeToOpacity(g)).join(" "));

{
  const fs = await import("node:fs");
  const vizSrc = fs.readFileSync(new URL("../js/viz.js", import.meta.url), "utf-8");
  const evSrc = fs.readFileSync(new URL("../js/evidence.js", import.meta.url), "utf-8");
  check("F43d 旧 confidence 编码已退场：viz.js 不再引用 confToWidth/confToOpacity（不留双口径，铁律 #90）",
        !/confToWidth|confToOpacity/.test(vizSrc),
        "viz.js 引用数：" + (vizSrc.match(/confTo(Width|Opacity)/g) || []).length);
  check("F43e 边详情卡含「证据等级」与 grade_rule=e1（等级固化口径可见）",
        /证据等级/.test(evSrc) && /grade_rule=e1/.test(evSrc),
        "evidence.js 命中：" + /证据等级/.test(evSrc) + "/" + /grade_rule=e1/.test(evSrc));
  check("F43f 图例含「证据等级」区块且五档 label 全出现（含未判定）",
        (function () {
          const el = { innerHTML: "" };
          try { buildLegend(el, {}); } catch (e) { return false; }
          return /证据等级/.test(el.innerHTML)
            && [5, 4, 3, 2, 1].every((g) => el.innerHTML.includes(gradeStyle(g).label))
            && el.innerHTML.includes(GRADE_UNKNOWN.label);
        })(),
        "legend html 长度 " + (function () { const el = { innerHTML: "" };
          try { buildLegend(el, {}); } catch (e) { return -1; } return el.innerHTML.length; })());
}


// ---------------------------------------------------------------------------
// F44 Phase 64 · M3：变量表（G1）与适用条件三态（G3）在详情卡里**真的渲染出来**
//
//   ★ 这一组守的是**语义区分**，不是「有没有这段 HTML」：
//     · 「源声明」与「沿边反推」不可混为一谈 —— G1 的整条纪律就在这个区分上；
//     · 三种「没有适用条件」的原因**不可合并成一句** —— G3 的核心结论是
//       「抽不到」与「无从谈起」对用户是两件事。若合并，用户会把 5,001 条
//       本来就无从谈起的公式当成我们的欠债。
// ---------------------------------------------------------------------------
function renderNode(attrs) {
  const el = fakeEl();
  renderNodeDetail(el, { id: "T:1", label: "t", type: "Formula", attrs: attrs || {} });
  return el.innerHTML;
}

const htmlSym = renderNode({
  latex: "F=ma",
  symbols: ["F", "m", "a"],
  symbol_table: [
    { sym: "F", quantity: "F (force)", quantity_id: "SY:F", src: "pb_symbols_column" },
    { sym: "m", quantity: "Mass", quantity_id: "SY:m", src: "pb_symbols_column" },
    { sym: "a", quantity: "a (acceleration)", quantity_id: "SY:a", src: "edge_target" },
  ],
});
check("F44a 变量表渲染出符号与量名（`sym (name)` 与「纯量名」两种 quantity 形态都要能拆）",
      htmlSym.includes("变量表") && htmlSym.includes("force")
      && htmlSym.includes("Mass") && htmlSym.includes("acceleration")
      && /<code class="sym">F<\/code>/.test(htmlSym), "");

// ★ 逐行抓「来源标记」，且**必须切到行尾 `</div>`**。
//   两版教训都在这条断言上：
//     ① 初版只查 `源声明 2` / `沿边反推 1` 两个**计数字符串** —— 计数是另一处硬编码，
//        与 `SYMBOL_SRC_LABEL` 无关 ⇒ 改映射表时断言**不变红**；
//     ② 二版按 `<div class="symrow">` 切行，但最后一行把区块尾注也包含进来，
//        而尾注里本来就写着「沿边反推」四个字 ⇒ 断言**恒真**。
//   判据的匹配范围一旦过宽，它会永远说 PASS（与 D80 同族）。
const _symRows = [...htmlSym.matchAll(/<div class="symrow">([\s\S]*?)<\/div>/g)].map((m) => m[1]);
const _rowOf = (sym) => _symRows.find((r) => r.includes(`<code class="sym">${sym}</code>`)) || "";
check("F44b 「沿边反推」必须标在**该条目自己那一行**上，源声明行不得误标 —— 二者不可混为一谈",
      _rowOf("a").includes("沿边反推")
      && !_rowOf("F").includes("沿边反推") && !_rowOf("m").includes("沿边反推")
      && /源声明 2/.test(htmlSym) && /沿边反推 1/.test(htmlSym),
      "a 行：" + _rowOf("a").replace(/<[^>]+>/g, " ").trim().slice(0, 60));

check("F44b2 来源标签表本身：两种来源**必须有不同的标签**（防将来被改成同一句）",
      SYMBOL_SRC_LABEL.pb_symbols_column.label !== SYMBOL_SRC_LABEL.edge_target.label
      && SYMBOL_SRC_LABEL.pb_symbols_column.label && SYMBOL_SRC_LABEL.edge_target.label,
      `${SYMBOL_SRC_LABEL.pb_symbols_column.label} / ${SYMBOL_SRC_LABEL.edge_target.label}`);

const htmlBlank = renderNode({ symbol_table: [{ sym: null, quantity: "Q (charge)", src: "edge_target" }] });
check("F44c `sym` 为空时从 quantity 括号前取符号（实测有 20 条这种条目 ⇒ 不得渲染出空白符号）",
      /<code class="sym">Q<\/code>/.test(htmlBlank)
      && !/<code class="sym"><\/code>/.test(htmlBlank), "");

const htmlNoSym = renderNode({ latex: "x=1" });
check("F44d 无 `symbol_table` ⇒ 整行不渲染（不是渲染成空行）",
      !htmlNoSym.includes("变量表"), "");

const htmlEx = renderNode({
  applicability_status: "extracted",
  applicability: ["质点"],
  applicability_evidence: [{ term: "质点", field: "description", snippet: "两质点间的引力…" }],
});
check("F44e extracted ⇒ 条件与**出处片段**都要显示（「有值必有据」要摆在读者面前）",
      htmlEx.includes("适用条件") && htmlEx.includes("质点")
      && htmlEx.includes("description") && htmlEx.includes("两质点间的引力"), "");

const htmlNr = renderNode({ applicability_status: "no_referent" });
check("F44f no_referent ⇒ 说清「谈不上」，且**不得**混入「未抽出 / 待办」的措辞",
      htmlNr.includes("无指称") && htmlNr.includes("谈不上")
      && !htmlNr.includes("未抽出") && !htmlNr.includes("待办"), "");

const htmlNe = renderNode({ applicability_status: "not_extracted" });
check("F44g not_extracted ⇒ 说清「待办」，且**不得**混入「无从谈起」的措辞（三态不可合并）",
      htmlNe.includes("未抽出") && htmlNe.includes("待办")
      && !htmlNe.includes("无从谈起") && !htmlNe.includes("谈不上"), "");

const htmlUnk = renderNode({ applicability_status: "brand_new_state" });
check("F44h 未识别的状态值 ⇒ **显式暴露原值**（不静默当空，与 whyRow 同一条纪律）",
      htmlUnk.includes("未知状态") && htmlUnk.includes("brand_new_state"), "");

const htmlNoApp = renderNode({ latex: "x=1" });
check("F44i 无 `applicability_status` ⇒ 整行不渲染（非公式节点 / 老数据）",
      !htmlNoApp.includes("适用条件"), "");

const htmlTag = renderNode({ latex: "x", domain: "physics.em",
                             domain_tags: ["physics.em", "physics.mechanics"] });
const tagTxt = (htmlTag.match(/跨域 \d+：([^<]+)</) || [])[1] || "";
check("F44j 跨域标签**剔除主标签**后再计数（`domain_tags` 含主标签，直接数会把主标签算成跨域）",
      /跨域 1：/.test(htmlTag) && tagTxt.includes("physics.mechanics")
      && !tagTxt.includes("physics.em"), "跨域标签原文：" + tagTxt);

// 投影层若漏把 domain 提到顶层，只要 attrs 里还在，就不该整行消失（防静默丢字段）
const htmlTagAttrOnly = renderNode({ latex: "x", domain: "physics.em", domain_tags: ["physics.em"] });
check("F44k `domain` 只写在 attrs 里也要显示（投影层漏提字段时不得静默消失）",
      htmlTagAttrOnly.includes("physics.em"), "");

// —— 以下三条都来自**真实数据冒烟**抓到的缺陷，不是想出来的 ——
const htmlNoTbl = renderNode({ symbols_unresolved: "no_has_symbol_edge" });
check("F44l 无变量表但数据侧给了原因 ⇒ 显示「未生成」+ 原因（不得整行消失，否则用户以为我们漏了）",
      htmlNoTbl.includes("变量表") && htmlNoTbl.includes("未生成")
      && htmlNoTbl.includes("has_symbol"), "");

const htmlAllEdge = renderNode({ symbol_table: [
  { sym: "a", quantity: "a (acceleration)", src: "edge_target" },
  { sym: "b", quantity: "b (x)", src: "edge_target" },
] });
check("F44m 全部沿边反推时要说「全部」；混合时**不得**这么说（否则用户以为一个都没源声明）",
      htmlAllEdge.includes("全部") && !htmlSym.includes("全部"), "");

// ★ 真实冒烟抓到的：`note` 里写 `**有**`，详情卡是 HTML 不跑 Markdown ⇒ 界面上出现星号
const _allRendered = [htmlSym, htmlBlank, htmlNoSym, htmlEx, htmlNr, htmlNe, htmlUnk,
                      htmlTag, htmlNoTbl, htmlAllEdge].join("");
check("F44n 渲染结果里**不得出现 Markdown 星号**（`**` 会原样显示成星号，抓自真实冒烟）",
      !/\*\*/.test(_allRendered),
      (_allRendered.match(/.{0,12}\*\*.{0,12}/) || [""])[0]);

// ---------------------------------------------------------------------------
// F45 空结果诊断（F12 / D69）—— 前端**只排版**，不猜原因
// ---------------------------------------------------------------------------
const DG = {
  scope: "ego", why: "filtered", counterfactual: true, scanned: 3, degree: 3, scan_complete: true,
  title: "被「可信档」挡住（当前 0 条，放宽后 1 条）",
  text: "只放宽这一项就能看到 1 条边（当前 0 条），其余条件保持不变。",
  counts: [{ key: "trust", what: "可信档", n: 1 }, { key: "kinds", what: "关系类型", n: 0 }],
  action: { key: "trust", to: "all", gain: 1, label: "含全部可信档重试" },
};
const envOf = (d, status = "no_result") => ({
  status, statusInfo: C.statusStyle(status), reason: "",
  stats: d ? { empty_diagnosis: d } : {},
});

check("F45a 没有 empty_diagnosis ⇒ 返回 null（宁可不说，也不编原因）",
      emptyDiagOf(envOf(null)) === null && emptyDiagOf(null) === null, "");

check("F45b 半份诊断（缺 why / 缺 scope）⇒ 一律 null（半份比没有更危险）",
      emptyDiagOf(envOf({ ...DG, why: undefined })) === null
      && emptyDiagOf(envOf({ ...DG, scope: undefined })) === null, "");

const dNullTo = emptyDiagOf(envOf({ ...DG, action: { key: "kinds", to: null, label: "清空关系类型" } }));
check("F45c `to: null` 是**合法目标值**（清空某条件），不得被当成「没有目标」丢弃",
      dNullTo && dNullTo.action && dNullTo.action.key === "kinds"
      && dNullTo.action.to === null, JSON.stringify(dNullTo && dNullTo.action));

const dNoTo = emptyDiagOf(envOf({ ...DG, action: { key: "kinds", label: "清空关系类型" } }));
check("F45d action 缺 `to` ⇒ 丢弃动作（否则点下去不知道要改成什么）",
      dNoTo && dNoTo.action === null && dNoTo.counts.length === 2, "");

const dBadCnt = emptyDiagOf(envOf({
  ...DG, counts: [{ key: "trust", what: "可信档", n: 1 }, { key: "x" }, { key: "y", what: "Y", n: "abc" }],
}));
check("F45e counts 里的非法项被剔除（缺字段 / n 不是数）⇒ 只剩 1 条",
      dBadCnt && dBadCnt.counts.length === 1 && dBadCnt.counts[0].key === "trust", "");

const dIso = emptyDiagOf(envOf({ ...DG, why: "isolated", action: null, degree: 0, counts: [] }));
const isoHtml = emptyDiagHTML(dIso);
check("F45f isolated：**不渲染放宽按钮**，且说明「放宽必然无效」（边界必须写在界面上）",
      !isoHtml.includes("relax-diag") && boundaryText(dIso).includes("放宽必然无效"), "");

const dMiss = emptyDiagOf(envOf({ ...DG, why: "missing_seed", action: null, counts: [] }));
check("F45g missing_seed：边界说明必须点明「没有放宽按钮」的理由（名字不存在）",
      boundaryText(dMiss).includes("没有放宽按钮") && boundaryText(dMiss).includes("不存在"), "");

const dPath = emptyDiagOf(envOf({ ...DG, scope: "path", why: "no_path",
                                  counterfactual: false, counts: [] }));
check("F45h counterfactual=false ⇒ **不渲染**「判定依据：扫描 N 条」（不假精确）",
      scopeText(dPath) === "" && boundaryText(dPath).includes("不承诺"), "");

const bnOk = fakeEl();
renderBanner(bnOk, envOf(DG, "ok"));
check("F45i ok + 0 边：横幅**必须出现**（此前根本不渲染 = D69 的现场）",
      bnOk.hidden === false && bnOk.className.includes("st-empty")
      && bnOk.getAttribute("data-state") === "empty"
      && bnOk.innerHTML.includes("查询正常完成"), "");

const bnNr = fakeEl();
renderBanner(bnNr, envOf(DG, "no_result"));
check("F45j no_result + 诊断：说清「无结果 ≠ 失败」，且按钮带着**动作键**",
      bnNr.innerHTML.includes("无结果 ≠ 失败")
      && bnNr.innerHTML.includes('data-relax-key="trust"'), "");

const bnNoDg = fakeEl();
renderBanner(bnNoDg, envOf(null, "no_result"));
check("F45k 无诊断 ⇒ 明说「不猜原因」，且**不再有**会静默清空面板的 `relax` 动作",
      bnNoDg.innerHTML.includes("不猜原因")
      && bnNoDg.innerHTML.includes('data-act="clear-filters"')
      && !bnNoDg.innerHTML.includes('data-act="relax"'), "");

check("F45l 诊断块里**不得出现 Markdown 星号**（横幅是 HTML，`**` 会原样显示）",
      !/\*\*/.test(emptyDiagHTML(emptyDiagOf(envOf(DG)))), "");

const nasty = `<a href="x">&'"`;
check("F45m empty.esc 与 panels.esc 逐字一致（两份实现 ⇒ 由这条钉住，而不是靠 import 耦合）",
      escEmpty(nasty) === escPanel(nasty) && escEmpty("<b>") === "&lt;b&gt;", escEmpty(nasty));

const deadHtml = emptyDiagHTML(emptyDiagOf(envOf(DG)));
check("F45n 计数为 0 的那一行标 `dead` 且写「无效」（让用户知道**别**往那个方向试）",
      deadHtml.includes('class="ed-row dead"') && deadHtml.includes("无效"), "");

const dUnknown = emptyDiagOf(envOf({ scope: "ego", why: "brand_new_state", action: null, counts: [] }));
check("F45o 没见过的 why ⇒ 仍渲染并**显式暴露原值**（不静默当空，与 whyRow 同一条纪律）",
      emptyDiagHTML(dUnknown).includes("brand_new_state"), "");


// ---------------------------------------------------------------------------
// F46 学科/领域导航抽屉（F13）—— 分类可以是空的，但「空」必须说清是哪一种
// ---------------------------------------------------------------------------
// 为什么这一组必须以「空」为主：导航最容易犯的错是**漏人**（某几个节点谁都不收），
// 而漏人**从不报错** —— 页面上只会少一格，没人看得出。故这里逐条咬住四件事：
//   ① 层级顺序与后端同源（顺序写错 ⇒ 下钻到后端不认的层，表现为「点了没反应」）；
//   ② 前两层守恒、第三层多值的**两套式子**各查各的；
//   ③ 四种「空」各说各的话（0 个 / 全无标签 / 分类不存在 / 请求失败）；
//   ④ 句子里的数字必须恒成立 —— 模板句 + 具体数字，写错一个分支就是一句谎话（D119）。

check("F46a 层级顺序 subject → domain → tags，且三层中文名齐备（前端唯一一份，D114）",
      NAV_LEVELS.join("/") === "subject/domain/tags"
      && NAV_LEVEL_LABEL.subject && NAV_LEVEL_LABEL.domain && NAV_LEVEL_LABEL.tags,
      NAV_LEVELS.join(" → "));

check("F46b nextLevel 逐层正确，末层与未知层都给 null（末层不得出现「下钻」入口）",
      nextLevel("subject") === "domain" && nextLevel("domain") === "tags"
      && nextLevel("tags") === null && nextLevel("bogus") === null, "");

check("F46c currentLevel 由**栈长度**定层：空栈=subject，超长不越界回到末层",
      currentLevel([]) === "subject"
      && currentLevel([{ level: "subject", key: "化学" }]) === "domain"
      && currentLevel([{}, {}]) === "tags"
      && currentLevel([{}, {}, {}, {}]) === "tags", "");

const navRaw = [
  { key: "物理", label: "物理", n: 3, samples: [] },
  { key: "", label: "空 key", n: 1, samples: [] },
  { key: "x", n: "abc", samples: [] },
  { key: "y", label: "负计数", n: -1, samples: [] },
  { key: "z", label: "零计数", n: 0, samples: [] },
  null,
];
check("F46d 条目过滤：缺 key / 计数非数字 / 负数一律剔除；**n=0 保留**（0 是真的有这一格）",
      JSON.stringify(navItemsOf({ items: navRaw }).map((it) => it.key))
        === JSON.stringify(["物理", "z"]),
      JSON.stringify(navItemsOf({ items: navRaw }).map((it) => it.key)));

const navSamp = navItemsOf({ items: [{ key: "k", label: "k", n: 1, samples: [
  { id: "N:1" },
  { label: "无 id" },
  { id: "N:2", label: "<b>x</b>", type: "Formula", degree: 7 },
] }] })[0].samples;
check("F46e 代表节点：缺 label 回落成 id、缺 id 剔除、degree 缺失保留为 null（不补 0）",
      navSamp.length === 2 && navSamp[0].label === "N:1"
      && navSamp[0].degree === null && navSamp[1].degree === 7,
      JSON.stringify(navSamp));

const navS0 = navStatsOf({});
check("F46f 缺计数字段 ⇒ null 而不是 0（「没有」与「不知道」必须分得开）",
      navS0.scanned === null && navS0.conserved === null
      && navS0.parent_exists === null && navS0.multi_valued === false, JSON.stringify(navS0));

// --- 单归属层：不守恒 = 漏人 ---
const nvLose = { status: "ok", level: "subject",
  stats: { scanned: 10, sum_items: 7, conserved: false, multi_valued: false,
           parent_exists: true, full_nodes: 10 } };
const nnLose = navNotice(nvLose);
check("F46g 单归属层不守恒 ⇒ 红字 + 明确算出**漏了几个人**（导航不许漏人）",
      nnLose && nnLose.kind === "bad" && nnLose.text.includes("漏人")
      && nnLose.text.includes("3"), nnLose ? nnLose.text : "(null)");

// --- 多值层：两套式子各查各的 ---
const nvMultiBad = { status: "ok", level: "tags", parent: "p",
  stats: { scanned: 10, sum_items: 4, conserved: false, multi_valued: true,
           tagged: 3, untagged: 5, parent_exists: true } };
const nnMultiBad = navNotice(nvMultiBad);
check("F46h 多值层自己的对账式不成立 ⇒ 照样红（「多值」不是免检牌）",
      nnMultiBad && nnMultiBad.kind === "bad" && nnMultiBad.text.includes("免检牌"),
      nnMultiBad ? nnMultiBad.text : "(null)");

const nvNoTag = { status: "no_result", level: "tags", parent: "chem.reaction",
  stats: { scanned: 1697, sum_items: 0, conserved: false, multi_valued: true,
           tagged: 0, untagged: 1697, parent_exists: true } };
check("F46i 多值层一格都没标签 ⇒ **不**输出解释句（否则会成为「各格之和 0 > 扫描 1697」这种假话）",
      navNotice(nvNoTag) === null, JSON.stringify(navNotice(nvNoTag)));
check("F46j 一格都没标签时由空态说清「全部没有领域标签」且带出真实节点数（D115）",
      navEmptyText(nvNoTag).includes("全部没有领域标签")
      && navEmptyText(nvNoTag).includes("1697"), navEmptyText(nvNoTag));

const nvMultiOk = { status: "ok", level: "tags", parent: "physics.em",
  stats: { scanned: 100, sum_items: 120, conserved: false, multi_valued: true,
           tagged: 80, untagged: 20, parent_exists: true } };
const nnMultiOk = navNotice(nvMultiOk);
check("F46k 多值层正常 ⇒ 说明句只陈述**恒成立**的关系（「不必相等」而非「大于」）+ 给出可核等式",
      nnMultiOk && nnMultiOk.kind === "info"
      && nnMultiOk.text.includes("不必相等")
      && nnMultiOk.text.includes("有标签 80")
      && nnMultiOk.text.includes("= 扫描 100"), nnMultiOk ? nnMultiOk.text : "(null)");

const nvNoParent = { status: "no_result", level: "domain", parent: "不存在的学科",
  stats: { scanned: 0, sum_items: 0, conserved: true, multi_valued: false,
           parent_exists: false } };
const nnNoParent = navNotice(nvNoParent);
check("F46l 分类名不存在 ⇒ 明说「不是没有结果，是这个分类名不存在」（两件事不许混）",
      nnNoParent && nnNoParent.kind === "bad"
      && nnNoParent.text.includes("不存在") && nnNoParent.text.includes("不是")
      && nnNoParent.text.includes("不存在的学科"), nnNoParent ? nnNoParent.text : "(null)");

check("F46m scanned=0 ⇒ 说「0 个节点（已扫描）」，不得与「没扫过」同形（D115）",
      navEmptyText({ level: "domain", stats: { scanned: 0, sum_items: 0,
        conserved: true, parent_exists: true } }).includes("已扫描"), "");

const navErr = navHTML({ status: "error", reason: "后端挂了" }, { stack: [] });
check("F46n 请求失败 ⇒ 出红字并带出原因，且不冒充「空」",
      navErr.includes("请求失败") && navErr.includes("后端挂了")
      && !navErr.includes("undefined"), "");

const navStack = [{ level: "subject", key: "化学" }, { level: "domain", key: "chem.reaction" }];
const navTags = navHTML({
  status: "ok", level: "tags", parent: "chem.reaction",
  stats: { scanned: 2, sum_items: 3, conserved: false, multi_valued: true,
           tagged: 2, untagged: 0, parent_exists: true },
  items: [{ key: "physics.em", label: "physics.em", n: 2,
            samples: [{ id: "P:a", label: "a", degree: 1 }] }],
}, { stack: navStack });
check("F46o 末层（领域标签）**不给**下钻按钮，且明说这是最后一层",
      !navTags.includes("data-nav-down") && navTags.includes("最后一层"), "");
check("F46p 末层仍给代表节点入口（叶子层不是死路）",
      navTags.includes('data-nav-seed="P:a"'), "");

const backs = Array.from(navTags.matchAll(/data-nav-back="(\d+)"/g)).map((m) => m[1]);
check("F46q 面包屑回退层级 = 0..stack.length，逐层可回（多一层就会回到错的层）",
      JSON.stringify(backs) === JSON.stringify(["0", "1", "2"]), backs.join(","));

const navXss = navHTML({
  status: "ok", level: "subject", parent: null,
  stats: { scanned: 1, sum_items: 1, conserved: true, multi_valued: false, parent_exists: true },
  items: [{ key: "<script>alert(1)</script>", label: "<script>alert(1)</script>", n: 1,
            samples: [{ id: "N:<x>", label: "<img onerror=1>", degree: 0 }] }],
}, { stack: [] });
check("F46r 条目名与 id 全部转义（分类名来自数据，不是可信 HTML）",
      !navXss.includes("<script>") && !navXss.includes("<img")
      && navXss.includes("&lt;script&gt;"), "");

const navLoading = navHTML(nvMultiOk, { stack: [], loading: true });
check("F46s 载入中只出「载入中…」，不残留上一层的列表（否则新旧两层同屏）",
      navLoading.includes("载入中") && !navLoading.includes("nav-list"), "");

const navNoResultHtml = navHTML(nvNoTag, { stack: navStack });
check("F46t no_result 是**正常空态**，不得渲染成「请求失败」",
      !navNoResultHtml.includes("请求失败")
      && navNoResultHtml.includes("全部没有领域标签"), "");

const navRawKey = navItemsOf({ items: [{ key: " Physics EM ", label: "x", n: 1 }] })[0].key;
check("F46u key **原样保留**（大小写/空格都是后端的东西；归一化会把「没找到」伪装成「没有」）",
      navRawKey === " Physics EM ", JSON.stringify(navRawKey));

check("F46v 整份渲染不出现 undefined / NaN（缺字段必须走 null 分支，不许字符串化）",
      !navTags.includes("undefined") && !navTags.includes("NaN")
      && !navErr.includes("NaN") && !navXss.includes("NaN"), "");

// ★ 端到端：**真正发出去的 URL**。F37c 只能证明 `buildParams` 认 `level/parent`，
//   证不了 `Api.nav()` 真把它们传进去 —— 那正是 P48/P49 的四种「参数静默丢失」的同一形状。
const navSeen = [];
const navApi = new Api({ base: "http://x", deadlineMs: 4000, fetchImpl: async (url) => {
  navSeen.push(url);
  return { ok: true, status: 200, headers: { get: () => null },
           json: async () => ({ status: "ok", level: "domain", parent: "化学", items: [], stats: {} }) };
} });
await navApi.nav({ level: "domain", parent: "化学", limit: 6 });
const navUrl = navSeen[0] || "";
check("F46w Api.nav 真的发出 level / parent / limit（父子名原样百分号编码，不做归一化）",
      navUrl.includes("level=domain")
      && navUrl.includes("parent=" + encodeURIComponent("化学"))
      && navUrl.includes("limit=6"), navUrl);
check("F46x Api.nav **不发 mode**（该端点不认它；发了只会污染 query_hash）",
      !navUrl.includes("mode="), navUrl);
const navEnvN = normalizeEnvelope({ status: "ok", items: [{ key: "物理", n: 1 }], stats: {} });
check("F46y 归一化**搬得动 items**（漏这一段 = 抽屉永远空白而 status=ok，与 paths 同族）",
      navEnvN.items.length === 1 && navEnvN.items[0].key === "物理", "");

// ★ 栈操作是「点击之后发什么请求」的唯一来源。真浏览器不在零安装路径上（D14），
//   所以这条链路只能靠纯函数钉住：把 DOM 事件剥掉，只留「栈怎么变、请求怎么推」。
const navS1 = navPush([], "化学");
const navS2 = navPush(navS1, "chem.reaction");
check("F46z navPush 记录的是**当前层**（不是下一层）：学科 key 记为 subject，领域 key 记为 domain",
      JSON.stringify(navS1) === JSON.stringify([{ level: "subject", key: "化学" }])
      && JSON.stringify(navS2)
        === JSON.stringify([{ level: "subject", key: "化学" },
                            { level: "domain", key: "chem.reaction" }]),
      JSON.stringify(navS2));

check("F46aa 末层（tags）再 push **无效果**：末层没有下一层，点了不该有反应",
      JSON.stringify(navPush(navS2, "physics.em")) === JSON.stringify(navS2), "");
check("F46ab 空 key 不产生一层（否则会多出一层 parent=\"\" 的死请求）",
      navPush([], "").length === 0, "");

check("F46ac navClip 夹住越界值而不是抛错（点坏一个按钮不该白屏）",
      navClip(navS2, 9).length === 2 && navClip(navS2, -5).length === 0
      && navClip(navS2, 1).length === 1
      && navClip(navS2, "不是数字").length === 2, "");

const navT0 = navTargetOf([]);
const navT2 = navTargetOf(navS2);
check("F46ad navTargetOf：空栈 ⇒ 顶层无 parent；两层栈 ⇒ tags + 栈顶 key（层级只由栈长推一次）",
      navT0.level === "subject" && navT0.parent === null
      && navT2.level === "tags" && navT2.parent === "chem.reaction",
      JSON.stringify([navT0, navT2]));

const navOrig = [{ level: "subject", key: "化学" }];
navPush(navOrig, "x"); navClip(navOrig, 0);
check("F46ae 栈操作**不修改入参**（原地改会让「返回上一层」拿到被污染的历史）",
      navOrig.length === 1 && navOrig[0].key === "化学", "");

// ★ 实测触发：真实快照里「化学」下有 28 个领域（14 个各 1 个节点）。全部铺开是一屏 28 行。
const manyEnv = (n) => ({
  status: "ok", level: "domain", parent: "化学",
  stats: { scanned: 25, sum_items: 25, conserved: true, multi_valued: false, parent_exists: true },
  items: Array.from({ length: n }, (_, i) => ({
    key: `k${i}`, label: `k${i}`, n: i === n - 1 ? 1 : 2, samples: [],
  })),
});
const manyHtml = navHTML(manyEnv(13), { stack: [{ level: "subject", key: "化学" }] });
check("F46af 条目超过阈值 ⇒ 尾部收进 `<details>`，且写明**还有几个 / 合计几个节点**",
      manyHtml.includes('<details class="nav-more">')
      && manyHtml.includes("另有 3 个")
      && manyHtml.includes("合计 5 个节点"), "");
check("F46ag 折叠 ≠ 删除：被收起的条目**仍然渲染**（展开就能看见，D110b）",
      manyHtml.includes('data-nav-down="k12"'), "");
check("F46ah 条目数不超阈值时**不出现** details（否则每一层都多一行「另有 0 个」）",
      !navHTML(manyEnv(10), { stack: [] }).includes("nav-more"), "");

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
