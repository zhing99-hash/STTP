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
    // ★ `search.schema.json` 声明的**截断披露**字段：此前归一化时被静默丢掉 ⇒
    //   搜索结果被 limit 截过（`co2`: hits=20 / total=81 / truncated=true）而 UI 无从知道
    //   ⇒ **UI 层把后端已经说清楚的截断又回吞了一次**（#66/#67：截断必须显式披露）。
    total_matches: typeof r.total_matches === "number" ? r.total_matches : null,
    truncated: typeof r.truncated === "boolean" ? r.truncated : null,
    nodes: Array.isArray(r.nodes) ? r.nodes : [],
    edges: Array.isArray(r.edges) ? r.edges : [],
    expandable: Array.isArray(r.expandable) ? r.expandable : [],
    hits: Array.isArray(r.hits) ? r.hits : [],
    /**
     * ★ Phase 49：`mode=path` 的**路径链**。后端在 `nodes`/`edges` 之外另给
     *   `paths: [{nodes:[id…], edges:[id…]}, …]`。此前归一化只搬 `nodes`/`edges`，
     *   前端 `runPath()` 去读 `env.meta.paths`（不存在）⇒ **路径条数永远显示不出来**
     *   （子图能画出来，但"3 条路径"的清单是空的 —— 又一处"后端给了、UI 回吞"）。
     */
    paths: Array.isArray(r.paths) ? r.paths : [],
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
    hits: [], paths: [], meta: null, stats: {}, raw: null, client_aborted: clientAborted,
    total_matches: null, truncated: null,
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
      // 没有 fetch ⇒ 请求**根本发不出去**（与"连接不上"同族，而非"后端执行异常"）
      return localErrorEnvelope({
        errorCode: "BACKEND_UNREACHABLE",
        reason: "当前环境不提供 fetch（用 file:// 直接打开页面时浏览器会禁止请求本地服务）"
          + " —— 请先运行 bash sttp.sh viz，再经 http://127.0.0.1:8765/ 打开页面",
      });
    }
    const url = this._url(path, params);
    const budget = deadlineMs || this.deadlineMs;
    const ctl = typeof AbortController === "function" ? new AbortController() : null;
    let timer = null;
    if (ctl && budget > 0) timer = setTimeout(() => ctl.abort(), budget + 250);
    const t0 = (typeof performance !== "undefined" && performance.now) ? performance.now() : Date.now();
    const elapsed = () => Math.round(
      ((typeof performance !== "undefined" && performance.now) ? performance.now() : Date.now()) - t0);

    // ---- 阶段 ①：**只包住 fetch 调用本身** -------------------------------------
    // ★ 铁律候选 #86：只有「异常确实来自 fetch」才允许判**传输层失败**。
    //   若把整段（含响应处理）罩进同一个 catch，我们自己的 TypeError 也会被
    //   误标成「后端连不上」—— 那是**判别依据过宽**（#84 的镜像）。
    //   实测教训：viz_server 未在运行时，浏览器抛 `TypeError: Failed to fetch`，
    //   旧代码把它记成 `TOOL_EXECUTION_ERROR`「后端执行异常（非空结果，属失败）」，
    //   把排查方向**引向后端内部**（而后端那时根本没有被调用）。
    let res;
    try {
      res = await this.fetchImpl(url, {
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal: ctl ? ctl.signal : undefined,
      });
    } catch (e) {
      if (timer) clearTimeout(timer);
      const ms = elapsed();
      this.last = { url, http: 0, ms, error: String((e && e.message) || e) };
      const aborted = !!(e && (e.name === "AbortError" || /abort/i.test(String((e && e.message) || ""))));
      if (aborted) {
        // ★ 客户端等不到 ≠ 服务端超时：如实标注，不冒充
        return { ...localErrorEnvelope({
          status: "timeout", errorCode: "GRAPH_QUERY_TIMEOUT",
          reason: `客户端在 ${Math.round(budget)} ms 内未收到响应（服务端可能仍在计算，结果未知）`,
          clientAborted: true }), http: 0, elapsed_ms: ms };
      }
      return { ...localErrorEnvelope({
        status: "error", errorCode: "BACKEND_UNREACHABLE",
        reason: `无法连接后端（${(e && e.message) || e}）`
          + " —— 后端未运行 / 端口不对 / 被代理拦截；本地请确认 bash sttp.sh viz 已启动",
      }), http: 0, elapsed_ms: ms };
    }

    // ---- 阶段 ②：响应处理（连接已建立 ⇒ 此处的异常**不是**传输层问题）--------
    try {
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
      const ms = elapsed();
      this.last = { url, http: res.status, ms };

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
      const ms = elapsed();
      this.last = { url, http: 0, ms, error: String((e && e.message) || e) };
      return { ...localErrorEnvelope({
        status: "error", errorCode: "TOOL_EXECUTION_ERROR",
        reason: `请求失败：${(e && e.message) ? e.message : e}` }), http: 0, elapsed_ms: ms };
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
  /**
   * 探活：**只用 `/api/healthz`**（毫秒级，不碰图谱数据）。
   *
   * ★ 为什么不用 `/api/stats`（Phase 49 实测坐实的外部评审 P0-2）：
   *   冷进程首次 `/api/stats` = **4.91 s**（拆开看：忠实度四分类冷算 4.27 s
   *   + A04 状态计数 0.57 s + 逐边迭代 0.31 s —— **不只是**加载 67.7 MB 权威图）；
   *   探活预算 2 s ⇒ abort ⇒ `detectMode` 判「后端不可用」⇒ 退 bundle ⇒
   *   **首次打开就像坏了**（用户第一印象）。
   *   `/api/healthz` 由服务端在**不加载图谱**的前提下回答，冷启动期间也是毫秒级
   *   （实测 listen→首个 healthz 16 ms）。
   *
   * ★ `backend: "warming"` 是**独立状态**，不是失败（铁律 #84：判别依据不足 ⇒ 不得回落成失败态）：
   *   探到它说明「后端进程活着、正在预热**整条读路径**（图谱 + 统计缓存）」，
   *   前端应显示「启动中」并允许用户继续输入，而不是宣布离线。
   * ★ 服务端的 `ready` 当且仅当**整条预热完成**（不只是"图装好"）—— 否则这里会误报就绪、
   *   用户第一次看统计仍要等 4.8 s（Phase 49 · F4，服务端自检 H8g2 钉住）。
   */
  async probe() {
    const env = await this._get(ENDPOINTS.healthz, null, { deadlineMs: 5000 });
    const httpOk = env.http_status === 200 || env.http === 200;
    const bodyUsable = !!(env.raw && typeof env.raw === "object"
      && Object.keys(env.raw).length > 0);
    if (httpOk && bodyUsable) {
      const warm = env.raw.backend === "ready";
      return {
        ...env,
        status: "ok",
        statusInfo: statusStyle("ok"),
        unknownStatus: null,
        warm,
        probe_note: warm
          ? "后端已就绪（/api/healthz）"
          : "后端进程在跑、**读路径仍在预热**（/api/healthz 回答 backend="
            + `${env.raw.backend}）—— 首次查询会比之后慢，不是故障`,
      };
    }
    // 老服务端（无 /api/healthz）⇒ **才**回退到 /api/stats（旧行为，仍可能被冷启动拖过预算）
    const legacy = await this._get(ENDPOINTS.stats, null, { deadlineMs: 2000 });
    const lhttpOk = legacy.http_status === 200 || legacy.http === 200;
    const lbodyUsable = !!(legacy.raw && typeof legacy.raw === "object"
      && Object.keys(legacy.raw).length > 0);
    if (lhttpOk && lbodyUsable) {
      // ★ 回退成功 ⇒ 必须是**可用**结论。绝不能因为 healthz 失败就把这里的成功丢掉
      //   （那是"较新的判据失败 ⇒ 整个判定回落成失败"，正是铁律 #84 的反面）。
      return {
        ...legacy,
        status: "ok",
        statusInfo: statusStyle("ok"),
        unknownStatus: null,
        warm: true,
        probe_note: `回退探活：/api/healthz 不可用，但 /api/stats HTTP `
          + `${legacy.http_status || legacy.http} 且响应体可解析 ⇒ 判「后端可用」`,
      };
    }
    // ★ 两条路都不通 ⇒ 返回 **healthz 的失败**：它才是「连不上」的原始证据
    //   （1 次请求、无图谱依赖，语义最干净）。stats 的失败原因仅是补充。
    return env.status === "error" ? env : legacy;
  }

  /**
   * GET /api/search?q=…
   *
   * ★ `q` 必填（后端缺它即 400「缺少参数 q」）。`mode: null` 用于**抑制** `buildParams`
   *   的默认 `mode=ego` —— 该端点不认 `mode`，发出去只是噪声（也曾在日志里误导定位）。
   */
  search(q, opts = {}) {
    const p = buildParams({ q, types: opts.types, subject: opts.subject,
                            limit: opts.limit || 20, mode: null });
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
