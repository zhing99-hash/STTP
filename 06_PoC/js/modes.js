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
 * STTP · 运行模式（`live` / `bundle` / `offline`）
 *
 * ★ `live`（默认）：查询面板 + 子图 API。**绝不请求 `/graph_data.json`**（验收 V1）。
 * ★ `bundle`：离线单文件整图（发布样例 / 小图）——保住 Phase 37 已验收的发布样例链路（验收 V9）。
 * ★ `offline`：后端与样例都不可用 ⇒ **如实说"没有图数据"**，不得伪装成可查询。
 *
 * 探测顺序很重要：**先探 API，能用就立即返回 live**，
 * 这样 live 模式下连一次 `/graph_data.json` 的 HEAD 都不会发生。
 */

import { chooseMode, MODE_BADGE, ENDPOINTS, BUNDLE_MAX_BYTES } from "./core.js";

/**
 * 探测 `/graph_data.json` 是否可读（**HEAD**：只要存在性 + 体积，不下载 body）。
 *
 * ★ 体积守卫（2026-10-14 实测的 P1 根因 · 铁律 #66「规模是设计约束」）：
 *   `bundle` 语义是「**离线样例 / 小图**」；把权威全图（48.7 MB / 9657 节点）当样例读，
 *   会**下载 + 解析 + 渲染**全图 ⇒ 浏览器卡死。而发布样例只有 ≈373 KB，
 *   两者体量差两个数量级 ⇒ 用 `Content-Length` 在**下载前**就能可靠区分。
 *   超限即判「不可用」并给出**明确原因**（不静默、不假装没有这一路）。
 */
export async function probeBundle(base = ".", { timeoutMs = 4000, fetchImpl } = {}) {
  const f = fetchImpl || (typeof fetch === "function" ? fetch.bind(globalThis) : null);
  if (!f) return { ok: false, reason: "无 fetch" };
  const ctl = typeof AbortController === "function" ? new AbortController() : null;
  let timer = null;
  if (ctl && timeoutMs > 0) timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await f(`${base}${ENDPOINTS.bundleGraph}`, {
      method: "HEAD", cache: "no-store", signal: ctl ? ctl.signal : undefined,
    });
    const cl = parseInt(res.headers && res.headers.get
      ? (res.headers.get("content-length") || "") : "", 10);
    const bytes = Number.isFinite(cl) ? cl : null;
    if (res.ok && bytes != null && bytes > BUNDLE_MAX_BYTES) {
      return {
        ok: false, http: res.status, bytes,
        reason: `文件体积 ${(bytes / 1048576).toFixed(1)} MB 超出离线样例上限 `
          + `${(BUNDLE_MAX_BYTES / 1048576).toFixed(0)} MB —— 这是权威全图（整图浏览），`
          + "不是样例；按设计**拒绝加载**（改用 /api/* 局部查询）",
      };
    }
    return { ok: !!res.ok, http: res.status, bytes };
  } catch (e) {
    return { ok: false, reason: String((e && e.message) || e) };
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/**
 * 判定运行模式。
 * @param {Api} api 契约客户端（用 `probe()` 判断后端是否在跑）
 * @param {{forced?:string, base?:string, allowBundleProbe?:boolean}} opts
 *        `forced` 来自 URL `?mode=live|bundle`；
 *        `allowBundleProbe=false` ⇒ 即使 API 不可用也不去探 bundle（用于"宁可 offline"的严格场景）。
 * @returns {{mode:string, badge:object, apiOk:boolean, bundleOk:boolean|null, detail:string}}
 */
export async function detectMode(api, opts = {}) {
  const forced = (opts.forced || "").trim().toLowerCase();
  const base = opts.base == null ? "." : String(opts.base).replace(/\/$/, "");
  const allowBundleProbe = opts.allowBundleProbe !== false;

  // ① 先探后端。API 可用 ⇒ 立即 live（**不触碰** /graph_data.json）。
  const pr = await api.probe();
  const apiOk = pr.status === "ok" && pr.raw != null;
  if (apiOk && forced !== "bundle") {
    return {
      mode: "live", badge: MODE_BADGE.live, apiOk: true, bundleOk: null,
      detail: `后端可用（/api/stats ${pr.http || 200}，${pr.http_status || pr.http || 200}）`,
    };
  }

  // ② 后端不可用（或用户强制 bundle）⇒ 探样例单文件
  // ★ 必须把 Api 的 fetchImpl **透传**下去：否则探测会静默回落到全局 fetch，
  //   在「依赖被注入」的场景（测试/替代实现）里会测出一个假的"样例不可用"。
  let bundleOk = null;
  let detail = apiOk ? "用户强制 bundle" : `后端不可用（${pr.error_code || pr.status}：${pr.reason || "无响应"}）`;
  if (allowBundleProbe) {
    const bb = await probeBundle(base, { fetchImpl: api && api.fetchImpl });
    bundleOk = !!bb.ok;
    detail += bb.ok ? "；样例单文件可用" : `；样例单文件不可用（${bb.reason || ("HTTP " + bb.http)}）`;
  }

  const mode = chooseMode({ apiOk, bundleOk: !!bundleOk, forced });
  return {
    mode, badge: MODE_BADGE[mode], apiOk, bundleOk, detail,
    probe: { apiStatus: pr.status, apiError: pr.error_code, http: pr.http || 0 },
  };
}

/** 从 URL 读取 `?mode=` 与 `?base=`（便于把页面挂到别的前缀/端口）。 */
export function modeFromLocation(search, def = {}) {
  let forced = def.forced || "";
  let base = def.base || ".";
  try {
    const sp = new URLSearchParams(search || "");
    if (sp.get("mode")) forced = sp.get("mode");
    if (sp.get("base")) base = sp.get("base");
  } catch (e) { /* 无 location 时用默认值 */ }
  return { forced, base };
}
