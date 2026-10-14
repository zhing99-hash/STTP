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
 * STTP · 契约客户端 —— 统一信封 / 五态 / `error_code` / `snapshot_id` / `query_hash`
 * / `AbortController`（按 `deadline_ms` 中止）。
 *
 * ★ 纪律（铁律 #67）：**「空」与「失败」必须分列**。
 *   本模块绝不把异常转成"空结果"；网络/解析失败一律产出 `status="error"` 的信封，
 *   由 UI 显式呈现。唯一的 4xx 是后端对**参数非法**返回的 `BAD_REQUEST`。
 *
 * ★ 客户端超时与服务端超时**不是一回事**：服务端 `timeout` 表示"预算到期即退（有部分结果）"；
 *   客户端 abort 表示"我没等到"，此时**不知道**服务端结果 —— 故标记 `client_aborted=true`
 *   并在 UI 上如实说明（不得冒充服务端 timeout）。
 */

import {
  ENDPOINTS, BUNDLE_MAX_BYTES, buildParams, toQueryString, statusStyle,
} from "./core.js";

export class ApiError extends Error {
  constructor(message, info = {}) {
    super(message);
    this.name = "ApiError";
    this.httpStatus = info.httpStatus || 0;
    this.errorCode = info.errorCode || "TOOL_EXECUTION_ERROR";
    this.payload = info.payload || null;
  }
}

/** 把任意后端响应补齐成前端统一结构（字段缺失不臆造，缺就留 undefined/null）。 */
export function normalizeEnvelope(raw) {
  const r = raw && typeof raw === "object" ? raw : {};
  const st = statusStyle(r.status);
  return {
    status: st.status,                 // ok | no_result | truncated | timeout | error
    statusInfo: st,                    // {cls,color,label,icon,hint,unknown}
    unknownStatus: st.unknown,         // ★ 非 null ⇒ 后端给了听不懂的状态（显式暴露）
    reason: r.reason == null ? "" : String(r.reason),
    error_code: r.error_code == null ? null : String(r.error_code),
    snapshot_id: r.snapshot_id == null ? null : String(r.snapshot_id),
    query_hash: r.query_hash == null ? null : String(r.query_hash),
    seed: r.seed == null ? null : r.seed,
    query: r.query && typeof r.query === "object" ? r.query : null,
    hidden_by_trust: typeof r.hidden_by_trust === "number" ? r.hidden_by_trust : null,
    nodes: Array.isArray(r.nodes) ? r.nodes : [],
    edges: Array.isArray(r.edges) ? r.edges : [],
    expandable: Array.isArray(r.expandable) ? r.expandable : [],
    hits: Array.isArray(r.hits) ? r.hits : [],
    meta: r.meta && typeof r.meta === "object" ? r.meta : null,
    stats: r.stats && typeof r.stats === "object" ? r.stats : {},
    raw: r,
    client_aborted: false,
  };
}

/** 本地合成一个失败信封（网络异常 / 客户端中止 / 解析失败）。 */
export function localErrorEnvelope({ status = "error", errorCode = "TOOL_EXECUTION_ERROR",
                                     reason = "", clientAborted = false } = {}) {
  const st = statusStyle(status);
  return {
    status: st.status, statusInfo: st, unknownStatus: st.unknown,
    reason, error_code: errorCode, snapshot_id: null, query_hash: null, seed: null,
    query: null, hidden_by_trust: null, nodes: [], edges: [], expandable: [],
    hits: [], meta: null, stats: {}, raw: null, client_aborted: clientAborted,
  };
}

export class Api {
  /**
   * @param {{base?:string, deadlineMs?:number, fetchImpl?:Function}} opts
   *   `base` 默认为 `"."`（同源相对，既适配 `http://host:8765/` 也适配子路径部署）。
   */
  constructor(opts = {}) {
    this.base = opts.base == null ? "." : String(opts.base).replace(/\/$/, "");
    this.deadlineMs = opts.deadlineMs || 3000;
    this.fetchImpl = opts.fetchImpl || (typeof fetch === "function" ? fetch.bind(globalThis) : null);
    /** 最近一次请求的元信息（供状态行显示耗时与 URL，便于复现） */
    this.last = null;
  }

  _url(path, params) {
    const qs = params ? toQueryString(params) : "";
    return `${this.base}${path}${qs ? "?" + qs : ""}`;
  }

  async _get(path, params, { deadlineMs, maxBytes } = {}) {
    if (!this.fetchImpl) {
      return localErrorEnvelope({
        errorCode: "TOOL_EXECUTION_ERROR",
        reason: "当前环境不支持 fetch（请用 python viz_server.py 打开页面）",
      });
    }
    const url = this._url(path, params);
    const budget = deadlineMs || this.deadlineMs;
    const ctl = typeof AbortController === "function" ? new AbortController() : null;
    let timer = null;
    if (ctl && budget > 0) timer = setTimeout(() => ctl.abort(), budget + 250);
    const t0 = (typeof performance !== "undefined" && performance.now) ? performance.now() : Date.now();
    try {
      const res = await this.fetchImpl(url, {
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal: ctl ? ctl.signal : undefined,
      });
      // ★ 体积守卫（下载点，铁律 #66）：`bundle` 语义是「离线样例 / 小图」。
      //   在**读 body 之前**按 `Content-Length` 拒绝，避免把权威全图（≈48.7 MB）
      //   拉进内存再解析 ⇒ 卡死浏览器。**拒绝**并给出明确原因，不静默截断。
      if (maxBytes) {
        const _cl = parseInt(res.headers && res.headers.get
          ? (res.headers.get("content-length") || "") : "", 10);
        if (Number.isFinite(_cl) && _cl > maxBytes) {
          return { ...localErrorEnvelope({
            status: "error", errorCode: "SUBGRAPH_LIMIT_REACHED",
            reason: `响应体积 ${(_cl / 1048576).toFixed(1)} MB 超出上限 `
              + `${(maxBytes / 1048576).toFixed(0)} MB —— 按设计拒绝加载（改用 /api/* 局部查询）`,
          }), http: res.status, http_status: res.status, bytes: _cl };
        }
      }
      let body = null;
      try { body = await res.json(); } catch (e) { body = null; }
      const ms = ((typeof performance !== "undefined" && performance.now) ? performance.now() : Date.now()) - t0;
      this.last = { url, http: res.status, ms: Math.round(ms) };

      if (res.status === 400) {
        // 唯一的 4xx 业务场景：参数非法（后端 error.schema.json）
        return { ...normalizeEnvelope(body || {}), status: "error",
                 statusInfo: statusStyle("error"),
                 error_code: (body && body.error_code) || "BAD_REQUEST",
                 reason: (body && body.reason) || "参数不合法", http: 400, http_status: res.status };
      }
      if (!res.ok && !body) {
        throw new ApiError(`HTTP ${res.status} 且响应体不可解析`, {
          httpStatus: res.status, errorCode: "TOOL_EXECUTION_ERROR",
        });
      }
      const env = normalizeEnvelope(body || {});
      env.http = res.status;
      env.http_status = res.status;
      return env;
    } catch (e) {
      const ms = ((typeof performance !== "undefined" && performance.now) ? performance.now() : Date.now()) - t0;
      this.last = { url, http: 0, ms: Math.round(ms), error: String(e && e.message || e) };
      const aborted = !!(e && (e.name === "AbortError" || /abort/i.test(String(e.message || ""))));
      if (aborted) {
        // ★ 客户端等不到 ≠ 服务端超时：如实标注，不冒充
        return { ...localErrorEnvelope({
          status: "timeout", errorCode: "GRAPH_QUERY_TIMEOUT",
          reason: `客户端在 ${Math.round(budget)} ms 内未收到响应（服务端可能仍在计算，结果未知）`,
          clientAborted: true }), http: 0, elapsed_ms: Math.round(ms) };
      }
      return { ...localErrorEnvelope({
        status: "error", errorCode: "TOOL_EXECUTION_ERROR",
        reason: `请求失败：${e && e.message ? e.message : e}` }), http: 0, elapsed_ms: Math.round(ms) };
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  /**
   * GET /api/stats —— 兼作「后端是否在跑」的探测。
   *
   * ★ 加固（2026-10-14 实测的 P1 根因）：探测的目标是「**后端活着吗**」，
   *   不是「五态信封是否合法」。legacy 端点可能**不带 `status`**；若把它当成
   *   `error`，`detectMode` 会误判后端不可用 ⇒ 退到 `bundle` ⇒ 拉 48 MB 全图 ⇒ 卡死。
   *   因此：**HTTP 200 + 响应体可解析 ⇒ 视为可用**，并把「缺 status」作为独立提示暴露
   *   （`probe_note`），不静默吞掉。
   *   注意：这**不放松**其他端点的五态判据 —— 只作用于「探活」这一个用途。
   */
  async probe() {
    const env = await this._get(ENDPOINTS.stats, null, { deadlineMs: 2000 });
    const httpOk = env.http_status === 200 || env.http === 200;
    const bodyUsable = !!(env.raw && typeof env.raw === "object"
      && Object.keys(env.raw).length > 0);
    if (httpOk && bodyUsable && env.status !== "ok") {
      return {
        ...env,
        status: "ok",
        statusInfo: statusStyle("ok"),
        unknownStatus: null,
        probe_note: `HTTP ${env.http_status || env.http} 且响应体可解析；`
          + `响应体未带合法 status${env.unknownStatus ? `（原值「${env.unknownStatus}」）` : ""}`
          + " —— 探活按「后端可用」处理（不据此判定 offline）",
      };
    }
    return env;
  }

  /** GET /api/search?q=… */
  search(q, opts = {}) {
    const p = buildParams({ q, types: opts.types, subject: opts.subject, limit: opts.limit || 20 });
    return this._get(ENDPOINTS.search, p, { deadlineMs: opts.deadlineMs || 3000 });
  }

  /** GET /api/subgraph —— 三模式（ego / path / filter） */
  subgraph(opts = {}) {
    const p = buildParams(opts);
    return this._get(ENDPOINTS.subgraph, p, { deadlineMs: opts.deadlineMs || this.deadlineMs });
  }

  /** GET /api/overview */
  overview(opts = {}) {
    const p = buildParams(opts);
    return this._get(ENDPOINTS.overview, p, { deadlineMs: opts.deadlineMs || 5000 });
  }

  /** GET /graph_data.json —— **仅** bundle 模式调用（验收 V1 断言的静态面） */
  bundle() {
    return this._get(ENDPOINTS.bundleGraph, null,
      { deadlineMs: 15000, maxBytes: BUNDLE_MAX_BYTES });
  }
}
