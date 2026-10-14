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
 * STTP · 子图状态层 —— 合并去重 / 子图栈 / 会话记账 / expand_token 透传
 *
 * 设计要点（方案 §6.2-2/3/10）：
 *  1. **合并去重**：每次响应按 id 并入累积图（`mergeGraph`，幂等）。
 *  2. **子图栈**：栈元素 = `{label, params, env, added}`；「返回上一层」= 弹栈 + **回滚会话计数** +
 *     由剩余栈项**重放合并**重建视图（不缓存整图，避免内存随操作数膨胀）。
 *  3. **会话记账**：`ego` 增量累加；`filter` / `path` 视为**新视图**（先清零）。
 *     前端这份计数只用于**即时禁用按钮**，判定权威在服务端（`stats.session`）。
 *  4. **硬门**：达到 `120 节点 / 200 边` ⇒ 禁用展开（**不静默截断、不自动放宽**）。
 */

import {
  mergeGraph, SessionBudget, SESSION_LIMIT, validateParams,
} from "./core.js";

/** 合法 hop 范围（与后端 HARD 对齐；超出 ⇒ 前端先拒绝，避免无谓请求） */
const HOP_MIN = 1;
const HOP_MAX = 5;

export class SubgraphStore {
  constructor(api, opts = {}) {
    this.api = api;
    this.limit = opts.limit || SESSION_LIMIT;
    this.session = new SessionBudget(this.limit);
    this.graph = { nodes: [], edges: [] };
    this.stack = [];              // [{label, params, env, added:{nodes,edges}, ts}]
    this.snapshotId = null;
    this.lastEnvelope = null;
    this.onChange = typeof opts.onChange === "function" ? opts.onChange : () => {};
  }

  get size() { return { nodes: this.graph.nodes.length, edges: this.graph.edges.length }; }
  get canExpand() { return !this.session.full; }
  get depth() { return this.stack.length; }

  reset() {
    this.session.reset();
    this.graph = { nodes: [], edges: [] };
    this.stack = [];
    this.lastEnvelope = null;
    this.onChange({ type: "reset", store: this });
    return this;
  }

  /** 由服务端 `stats.session` 校准本地计数（服务端是权威）。 */
  _sync(envelope) {
    if (envelope && envelope.stats) this.session.syncFrom(envelope.stats);
    if (envelope && envelope.snapshot_id) this.snapshotId = envelope.snapshot_id;
  }

  _rebuild() {
    let g = { nodes: [], edges: [] };
    this.stack.forEach((s) => {
      g = mergeGraph(g, { nodes: (s.env && s.env.nodes) || [], edges: (s.env && s.env.edges) || [] });
    });
    this.graph = g;
  }

  /**
   * 执行一次子图查询并入栈。
   * @returns {Promise<{ok:boolean, env?:object, added?:object, error?:string}>}
   */
  async run(params, { label = "查询", silent = false } = {}) {
    const v = validateParams(params);
    if (!v.ok) return { ok: false, error: v.message, field: v.field };

    const mode = (params.mode || "ego").toLowerCase();
    const env = await this.api.subgraph(params);
    this.lastEnvelope = env;
    this._sync(env);

    // ★ 五态：只有拿到图的两种态（ok / truncated）才合并；timeout 也可能带部分结果，
    //   后端在 timeout 时会返回已找到的部分 ⇒ 若有 nodes/edges 就并入，但状态仍如实显示。
    const hasPayload = env.nodes.length > 0 || env.edges.length > 0;
    if (!hasPayload) {
      return { ok: false, env, error: env.reason || env.statusInfo.label };
    }

    const merged = mergeGraph(this.graph, { nodes: env.nodes, edges: env.edges });
    const added = merged.added;
    this.graph = { nodes: merged.nodes, edges: merged.edges };

    // 会话记账：ego 增量 / 其他模式先清零（视为新视图）
    if (mode === "ego") {
      const r = this.session.add(added.nodes, added.edges);
      if (!r.ok) {
        // 服务端也会拒；此处仅提示（已合并的数据保留，避免"点了没反应"）
        this.session.syncFrom(env.stats);
      }
    } else {
      this.session.reset();
      this.session.syncFrom(env.stats);
    }

    this.stack.push({
      label, params: { ...params }, env, added, ts: Date.now(),
      sessionNodes: this.session.nodes, sessionEdges: this.session.edges,
    });

    if (!silent) this.onChange({ type: "run", store: this, added, env, label });
    return { ok: true, env, added, merged };
  }

  /** 展开某节点的 1 跳邻域（优先使用服务端签发的 `expand_token`）。 */
  async expand(nodeId, opts = {}) {
    if (this.session.full) {
      return { ok: false, error: "本次会话预算已用尽，请返回上一层或重置", field: "session" };
    }
    const hops = Math.max(HOP_MIN, Math.min(HOP_MAX, parseInt(opts.hops, 10) || 1));
    const token = opts.token || this._tokenFor(nodeId);
    const params = token
      ? { mode: "expand", seeds: nodeId, expand_token: token, hops }
      : { mode: "ego", seeds: nodeId, hops, trust: opts.trust };
    if (opts.trust) params.trust = opts.trust;
    if (opts.max_nodes) params.max_nodes = opts.max_nodes;
    if (opts.max_edges) params.max_edges = opts.max_edges;
    return this.run(params, { label: `展开 ${nodeId}` });
  }

  _tokenFor(nodeId) {
    const last = this.lastEnvelope;
    if (!last || !Array.isArray(last.expandable)) return null;
    // ★ 后端 `expandable[]` 的元素形如 {node_id, estimated_neighbors, token}
    const hit = last.expandable.find((x) => x && (x.node_id === nodeId || x.id === nodeId || x.node === nodeId));
    return hit ? (hit.token || null) : null;
  }

  /** 「返回上一层」：弹栈 + 回滚会话计数 + 由剩余栈项重建视图。 */
  back() {
    if (this.stack.length <= 1) return { ok: false, error: "已经是第一层" };
    const popped = this.stack.pop();
    this.session.rollback(popped.added ? popped.added.nodes : 0,
                          popped.added ? popped.added.edges : 0);
    this._rebuild();
    const top = this.stack[this.stack.length - 1];
    this.lastEnvelope = top ? top.env : null;
    this.onChange({ type: "back", store: this, popped, top });
    return { ok: true, popped, top };
  }

  /** 导出 JSON 快照（§6.5-3）：子图 + 边证据 + snapshot_id + query_hash，可离线复现同一次查询。 */
  jsonSnapshot(extra = {}) {
    const env = this.lastEnvelope || {};
    return {
      schema: "sttp-frontend-subgraph/v1",
      exported_at: new Date().toISOString(),
      snapshot_id: this.snapshotId,
      query_hash: env.query_hash || null,
      sessions: this.stack.map((s) => ({ label: s.label, params: s.params, query_hash: (s.env || {}).query_hash || null })),
      session_budget: { n_nodes: this.session.nodes, n_edges: this.session.edges, limit: this.limit },
      stats: env.stats || {},
      nodes: this.graph.nodes,
      edges: this.graph.edges,
      ...extra,
    };
  }
}
