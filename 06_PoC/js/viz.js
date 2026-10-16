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
 * STTP · 渲染层 —— Cytoscape 实例 / 局部布局 / 图例 / 导出
 *
 * ★ 视觉编码分列（方案 §6.2-7）：
 *   节点：填充色 = 学科，形状 = 类型（描边色同类型），`verification_level` **不**上色（走卡片徽标）
 *   边：  颜色 = 语义关系 `type`，线型 = explicit(实线)/inferred(虚线)
 *
 * ★ 增量渲染：新元素做**局部** fcose（`randomize:false`），已有节点位置不动 ⇒ 不整图重排、不闪烁。
 */

import {
  subjectColor, nodeTypeStyle, edgeTypeColor, eoiStyle,
  confToWidth, confToOpacity,
  SUBJECT_COLOR, SUBJECT_ORDER, NODE_TYPE_STYLE, NODE_TYPE_ORDER,
  EDGE_TYPE_COLOR, EOI_STYLE, fidelityBadge, evidenceStatusBadge,
} from "./style.js";
// ★ Phase 54：年代分桶的**唯一实现**在 `core.js`（铁律 #88），这里只调用。
//   `core.js` 是零依赖纯逻辑层，不反向依赖本文件 ⇒ 无循环依赖。
import { deriveEra, eraSortKey } from "./core.js";

function cyLib() {
  const cy = (typeof window !== "undefined") ? window.cytoscape : null;
  if (!cy) throw new Error("Cytoscape 未加载（vendor/cytoscape.min.js 缺失且无 CDN）");
  return cy;
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** 边/节点 id 去前缀（与旧页面 `shortId` 同口径） */
export function shortId(id) {
  return String(id == null ? "" : id).replace(/^[A-Za-z]+:/, "");
}

/** 把后端投影节点 → Cytoscape element */
export function toCyNode(n) {
  const type = n.type || "Unknown";
  const ts = nodeTypeStyle(type);
  return {
    group: "nodes",
    data: {
      id: n.id,
      label: n.label || shortId(n.id),
      type,
      subject: n.subject || "跨学科",
      vl: n.verification_level || null,
      confidence: typeof n.confidence === "number" ? n.confidence : null,
      why: n.why_shown || null,
      ghost: !!n.ghost,
      degNum: 1,
      _fill: subjectColor(n.subject),
      _border: ts.color,
      _shape: ts.shape,
      formula: n.formula || null,
      // ★ Phase 54：发现年代桶（`"17 世纪"` / `"1900–1909"` / `"暂无"`）。
      //   **只存数据、不参与任何 style** —— 用户决策：「发现年份不放控件、不参与筛选，
      //   只是显示分组标记」。故 `style` 数组里**没有**一行读 `era`：节点填充色仍是学科、
      //   描边色与形状仍是类型。它只被 `buildLegend()`（图例分组区块）与
      //   `evidence.js`（详情面板）消费。
      era: deriveEra((n.attrs || {}).discovery_time),
      raw: n,
    },
  };
}

/** 把后端投影边 → Cytoscape element */
export function toCyEdge(e) {
  const eoi = eoiStyle(e.explicit_or_inferred);
  const c = typeof e.confidence === "number" ? e.confidence : 0.5;
  return {
    group: "edges",
    data: {
      id: e.id,
      source: e.source,
      target: e.target,
      type: e.type || "—",
      kind: e.kind || "—",
      vl: e.verification_level || null,
      confidence: typeof e.confidence === "number" ? e.confidence : null,
      why: e.why_shown || null,
      stale: !!e.stale,
      // ★ A04（Phase 45）：`stale` 已把两层合流（字节忠实度 ∪ 依赖状态）；
      //   这里另存状态本身，供 tooltip / 证据卡分辨「是来源变了还是依赖变了」。
      //   `null` = 该图上无 A04 记录（**不得**当作 valid）。
      status: (e.evidence_status == null) ? null : e.evidence_status,
      _color: edgeTypeColor(e.type),
      _line: eoi.line,
      _width: confToWidth(c),
      _opacity: confToOpacity(c),
    },
    classes: e.explicit_or_inferred === "inferred" ? "eoi-inferred" : "eoi-explicit",
  };
}

export function createViz(opts = {}) {
  const container = opts.container;
  const cy = cyLib();
  const hasFcose = !!(typeof window !== "undefined" && window.cytoscapeFcose);

  const tooltip = opts.tooltipEl || (() => {
    const el = document.createElement("div");
    el.id = "tooltip";
    container.appendChild(el);
    return el;
  })();

  const inst = cy({
    container,
    wheelSensitivity: 0.2,
    minZoom: 0.05,
    maxZoom: 6,
    selectionType: "single",
    boxSelectionEnabled: false,
    style: [
      {
        selector: "node",
        style: {
          "background-color": "data(_fill)",
          "border-color": "data(_border)",
          "border-width": 3,
          shape: "data(_shape)",
          width: "mapData(degNum, 1, 12, 26, 60)",
          height: "mapData(degNum, 1, 12, 26, 60)",
          label: "data(label)",
          "font-size": 11,
          color: "#e6edf3",
          "text-valign": "bottom",
          "text-margin-y": 4,
          "text-max-width": 90,
          "text-wrap": "ellipsis",
          "text-outline-color": "#0b0f15",
          "text-outline-width": 2,
        },
      },
      { selector: "node[?ghost]", style: { "border-style": "dashed", "border-width": 2, "font-size": 10 } },
      { selector: "node:selected", style: { "border-width": 5, "border-color": "#ffffff" } },
      {
        selector: "edge",
        style: {
          width: "data(_width)",
          "line-color": "data(_color)",
          "target-arrow-color": "data(_color)",
          "line-style": "data(_line)",
          opacity: "data(_opacity)",
          "curve-style": "bezier",
          "target-arrow-shape": "triangle",
          "arrow-scale": 0.9,
        },
      },
      { selector: "edge:selected", style: { opacity: 1, width: 6 } },
      // ★ stale 边：琥珀色虚线外描（与 eoi 线型正交，可同时读出）
      { selector: "edge.stale", style: { "line-color": "#d29922", "target-arrow-color": "#d29922" } },
      { selector: ".dim", style: { opacity: 0.12, "text-opacity": 0.15 } },
      { selector: ".hidden", style: { display: "none" } },
      { selector: "node.hl", style: { "border-color": "#ffd166", "border-width": 6 } },
      { selector: "edge.hl", style: { width: 6, "line-color": "#ffd166", "target-arrow-color": "#ffd166" } },
      // 路径高亮（沿用既有配色）
      { selector: "node.p-src", style: { "border-color": "#38d9a9", "border-width": 9, "overlay-color": "#38d9a9", "overlay-opacity": 0.2 } },
      { selector: "node.p-dst", style: { "border-color": "#ff8787", "border-width": 9, "overlay-color": "#ff8787", "overlay-opacity": 0.2 } },
      { selector: "node.p-mid", style: { "border-color": "#ffd166", "border-width": 7 } },
      { selector: "edge.p-hop", style: { "line-style": "solid", opacity: 1, width: 6, "line-color": "#ffd166", "target-arrow-color": "#ffd166", label: "data(type)", "font-size": 9, color: "#e6edf3", "text-outline-color": "#0b0f15", "text-outline-width": 2 } },
      { selector: "edge.p-cross", style: { "line-style": "solid", opacity: 1, width: 8, "line-color": "#e94bf0", "target-arrow-color": "#e94bf0", label: "data(type)", "font-size": 9, color: "#f39cf7", "text-outline-color": "#0b0f15", "text-outline-width": 2 } },
      // 新增元素的淡入（尊重 prefers-reduced-motion 由 CSS 兜底）
      { selector: "node.new, edge.new", style: { opacity: 0.001 } },
    ],
  });

  const api = {
    cy: inst,
    hasFcose,

    nodeCount() { return inst.nodes().length; },
    edgeCount() { return inst.edges().length; },

    /** 合并 elements（按 id 去重，天然幂等）。返回本次新增的 id 便于局部布局。 */
    add(nodes, edges) {
      const existN = new Set(inst.nodes().map((n) => n.id()));
      const existE = new Set(inst.edges().map((e) => e.id()));
      const add = [];
      const newIds = [];
      (nodes || []).forEach((n) => {
        if (!n || n.id == null || existN.has(n.id)) return;
        existN.add(n.id); newIds.push(n.id); add.push(toCyNode(n));
      });
      (edges || []).forEach((e) => {
        if (!e || e.id == null || existE.has(e.id)) return;
        // 悬挂边保护：两端节点不存在则不加入（否则 Cytoscape 会丢弃并告警）
        if (!existN.has(e.source) || !existN.has(e.target)) return;
        existE.add(e.id); add.push(toCyEdge(e));
      });
      if (add.length) {
        const eles = inst.add(add);
        eles.addClass("new");
        this._recountDegrees();
        // 触发淡入（下一帧去掉 new，让 opacity 回到数据值）
        const raf = (typeof requestAnimationFrame === "function")
          ? requestAnimationFrame : (f) => setTimeout(f, 16);
        raf(() => eles.removeClass("new"));
      }
      return { added: add.length, newIds };
    },

    _recountDegrees() {
      inst.nodes().forEach((n) => {
        n.data("degNum", Math.max(1, n.degree(true)));
      });
    },

    /** 局部布局：只对新增节点做 fcose（已有节点锁定），避免整图重排。 */
    layout(newIds) {
      const list = (newIds && newIds.length) ? inst.collection(newIds.map((id) => inst.getElementById(id))) : inst.collection();
      const targets = (newIds && newIds.length) ? list.union(list.neighborhood().nodes()) : inst.elements();
      if (!targets || targets.length === 0) return;
      const name = (this.hasFcose && inst.hasLayout && inst.hasLayout("fcose")) ? "fcose" : "cose";
      const locked = inst.nodes().difference(targets);
      locked.lock();
      try {
        inst.layout({
          name,
          eles: targets,
          randomize: false,
          animate: false,
          fit: !(newIds && newIds.length),  // 首次适配；增量不动视口
          padding: 40,
          nodeRepulsion: 8000,
          idealEdgeLength: 90,
          numIter: 1200,
        }).run();
      } catch (e) {
        try { inst.layout({ name: "concentric", eles: targets, animate: false, fit: !(newIds && newIds.length) }).run(); }
        catch (e2) { /* 布局失败不致命 */ }
      } finally {
        locked.unlock();
      }
    },

    fit() { if (inst.elements().length) inst.fit(undefined, 40); },
    clear() { inst.elements().remove(); },

    clearPathHighlight() {
      inst.elements().removeClass("p-src p-dst p-mid p-hop p-cross");
    },
    highlightPath(pathNodes) {
      const arr = pathNodes || [];
      if (!arr.length) return;
      arr.forEach((id, i) => {
        const n = inst.getElementById(id);
        if (!n || n.empty()) return;
        if (i === 0) n.addClass("p-src");
        else if (i === arr.length - 1) n.addClass("p-dst");
        else n.addClass("p-mid");
      });
      for (let i = 0; i < arr.length - 1; i += 1) {
        const es = inst.edges().filter((e) => e.data("source") === arr[i] && e.data("target") === arr[i + 1]);
        es.addClass("p-hop");
      }
    },

    /** 临时聚焦：只保留给定 id 集合（null ⇒ 全显）。**不改数据**，只改可见性。 */
    focusOnly(idSet) {
      if (!idSet) { inst.elements().removeClass("dim hidden"); return; }
      inst.nodes().forEach((n) => {
        const on = idSet.has(n.id());
        n.toggleClass("dim", !on);
      });
      inst.edges().forEach((e) => {
        const on = idSet.has(e.data("source")) && idSet.has(e.data("target"));
        e.toggleClass("dim", !on);
      });
    },

    // ---- 导出 ----
    exportPNG({ scale = 2, watermark = "" } = {}) {
      const uri = inst.png({ full: true, scale, bg: "#0b0f15" });
      return { uri, filename: `sttp-subgraph-${watermark || "snapshot"}.png`, mime: "image/png" };
    },
    exportSVG({ watermark = "" } = {}) {
      if (typeof inst.svg !== "function") throw new Error("当前 Cytoscape 构建未包含 svg 导出");
      const svg = inst.svg({ full: true, bg: "#0b0f15" });
      return { svg, filename: `sttp-subgraph-${watermark || "snapshot"}.svg` };
    },
    exportPositions() {
      const pos = {};
      inst.nodes().forEach((n) => { pos[n.id()] = n.position(); });
      return pos;
    },
  };

  // ---- tooltip：边悬浮显示 type/kind/置信度/验证档/why ----
  if (tooltip) {
    inst.on("mouseover", "edge", (evt) => {
      const d = evt.target.data();
      tooltip.innerHTML =
        `<b>${esc(d.type)}</b> <span class="pill">（语义关系 type）</span><br>`
        + `<span class="note">kind（来源/管线）：</span>${esc(d.kind)}<br>`
        + `<span class="note">置信度：</span>${d.confidence == null ? "—" : d.confidence}<br>`
        + `<span class="note">验证档：</span>${esc(d.vl || "—")}`
        + `<br><span class="note">explicit/inferred：</span>${d._line === "dashed" ? "inferred（推断）" : "explicit（显式）"}`
        + (d.stale ? `<br><span style="color:${fidelityBadge(d.fidelity).color}">证据忠实度：${esc(fidelityBadge(d.fidelity).label)}</span>` : "")
        + (d.status
           ? `<br><span class="note">验证依赖（A04）：</span><span style="color:${evidenceStatusBadge(d.status).color}">${esc(evidenceStatusBadge(d.status).label)}</span>`
           : '<br><span class="note">验证依赖（A04）：</span>无记录（缺记录 ≠ 已验证）')
        + (d.why ? `<br><span class="note">why_shown：</span>${esc(d.why)}` : "");
      tooltip.style.display = "block";
      // 定位到画布内（跟随鼠标）
      const r = container.getBoundingClientRect();
      const rel = evt.renderedPosition || null;
      if (rel) {
        tooltip.style.left = `${Math.min(rel.x + 14, r.width - 350)}px`;
        tooltip.style.top = `${Math.min(rel.y + 14, r.height - 120)}px`;
      }
    });
    inst.on("mouseout", "edge", () => { tooltip.style.display = "none"; });
  }

  if (typeof opts.onSelectNode === "function") {
    inst.on("tap", "node", (evt) => opts.onSelectNode(evt.target.data("raw") || evt.target.data()));
  }
  if (typeof opts.onSelectEdge === "function") {
    inst.on("tap", "edge", (evt) => opts.onSelectEdge(evt.target.data()));
  }
  if (typeof opts.onBackground === "function") {
    inst.on("tap", (evt) => { if (evt.target === inst) opts.onBackground(); });
  }

  return api;
}

/** 图例：学科填充 / 类型形状 / 发现年代（分组标记，含计数）/ 边 type 颜色 / eoi 线型（只列**当前子图内出现**的，避免长列表） */
export function buildLegend(el, { nodeTypes = [], edgeTypes = [], eras = [], eraCounts = null } = {}) {
  if (!el) return;
  const shapeCss = (shape) => {
    // 只用 border-radius / clip-path 近似主要形状（图例是提示，不是精确渲染）
    if (shape === "ellipse") return "border-radius:50%;";
    if (shape === "diamond") return "border-radius:2px;transform:rotate(45deg);";
    if (shape === "hexagon") return "border-radius:2px;clip-path:polygon(25% 0,75% 0,100% 50%,75% 100%,25% 100%,0 50%);";
    if (shape === "octagon") return "border-radius:2px;clip-path:polygon(30% 0,70% 0,100% 30%,100% 70%,70% 100%,30% 100%,0 70%,0 30%);";
    if (shape === "triangle") return "border-radius:0;clip-path:polygon(50% 0,100% 100%,0 100%);";
    if (shape === "star") return "border-radius:0;clip-path:polygon(50% 0,61% 35%,98% 35%,68% 57%,79% 91%,50% 70%,21% 91%,32% 57%,2% 35%,39% 35%);";
    if (shape === "tag") return "border-radius:2px 8px 8px 2px;";
    return "border-radius:3px;";
  };

  const parts = [];
  parts.push('<h3>学科（填充色）</h3>');
  SUBJECT_ORDER.forEach((s) => {
    parts.push(`<div class="legend-item"><span class="swatch" style="background:${SUBJECT_COLOR[s]};border:1px solid var(--line)"></span>${esc(s)}</div>`);
  });

  const nts = (nodeTypes && nodeTypes.length) ? nodeTypes : NODE_TYPE_ORDER.slice(0, 12);
  parts.push('<h3 style="margin-top:12px">节点类型（形状 + 描边）</h3>');
  nts.forEach((t) => {
    const st = NODE_TYPE_STYLE[t] || NODE_TYPE_STYLE.Unknown;
    parts.push(`<div class="legend-item"><span class="shape-chip" style="border-color:${st.color};${shapeCss(st.shape)}"></span>${esc(t)}</div>`);
  });

  // ★ Phase 54：发现年代 —— **分组标记**，不是筛选器（用户决策）。
  //   这里只**列现状**：当前子图内实际出现的桶，按时间序（跨公元前后也单调）。
  //   纯文本、不可点击、不触发任何查询；节点填充色仍是学科 ⇒ **年代不占用任何视觉通道**。
  parts.push('<h3 style="margin-top:12px">发现年代（分组标记）</h3>');
  const eraList = (eras || []).slice().sort((a, b) => eraSortKey(a) - eraSortKey(b));
  if (!eraList.length) {
    parts.push('<div class="note">当前子图内没有带「发现年代」的节点。</div>');
  } else {
    eraList.forEach((e) => {
      const n = (eraCounts && Number.isFinite(eraCounts[e])) ? ` <span class="note">×${eraCounts[e]}</span>` : "";
      parts.push(`<div class="legend-item"><code>${esc(e)}</code>${n}</div>`);
    });
  }
  parts.push('<div class="legend-item note">年代只作<b>分组标记</b>：不上色、不可点击、不参与筛选。'
    + '全量 5090 个公式中仅 <b>81</b> 条有人工策展的年代，其余为「暂无」；'
    + '点选节点可在右栏看到它的发现时间与发现人。</div>');

  parts.push('<h3 style="margin-top:12px">语义关系（边颜色）</h3>');
  const ets = edgeTypes || [];
  if (!ets.length) {
    parts.push(`<div class="note">当前子图无可见边。全量表含 ${Object.keys(EDGE_TYPE_COLOR).length} 种语义关系（type）。</div>`);
  } else {
    ets.forEach((t) => {
      parts.push(`<div class="legend-item"><span class="line-chip" style="border-top-color:${edgeTypeColor(t)}"></span>${esc(t)}</div>`);
    });
  }

  parts.push('<h3 style="margin-top:12px">来源性质（线型）</h3>');
  Object.keys(EOI_STYLE).forEach((k) => {
    const st = EOI_STYLE[k];
    parts.push(`<div class="legend-item"><span class="line-chip" style="border-top-color:${st.color};border-top-style:${st.line}"></span>${esc(st.label)}</div>`);
  });
  parts.push('<div class="legend-item"><span class="line-chip" style="border-top-color:#d29922"></span>证据判 stale（琥珀）＝ 两层任一命中：'
    + '<b>字节忠实度</b>「已变更 / 不可判定」<b>或</b> <b>验证依赖（A04）</b>「依赖已变更 / 命题已否决」</div>');
  parts.push('<div class="legend-item note">「locator 未入库 ⇒ 不可从仓库复算」'
    + '<b>不</b>着色（全图 47 055 条，着色会淹没真实信号）；'
    + '见状态行「证据字节」分列与右栏证据卡。</div>');
  parts.push('<div class="legend-item note">★ <b>「无可复算依赖（待复核 needs_review）」不等同「已变更」</b>：'
    + '它是本环境<b>不可判定</b>（缺的正是数据本身），故<b>不</b>着色也<b>不</b>被「排除 stale」排除 —— '
    + '但它始终出现在状态行「验证依赖」分列里。'
    + '<b>缺 A04 记录</b>（新图/旧图）同样<b>不</b>当作「有效」。</div>');

  el.innerHTML = parts.join("");
}
