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
 * STTP · 学科 / 领域 导航抽屉（F13）—— 纯逻辑 + HTML 片段，可被 node 直接断言
 *
 * 四条纪律：
 *  1. **层级顺序是唯一真相**：`NAV_LEVELS` 必须与后端 `queries.NAV_LEVELS` **逐值相等**
 *     （X18a 求差）。前端自己排一套顺序 ⇒ 下钻到后端不认的层，表现为「点了没反应」。
 *  2. **核对得上才算导航**：前两层单归属 ⇒ `sum_items == scanned`；第三层多值 ⇒
 *     `tagged + untagged == scanned`。两个式子哪个不成立都得**报出来** ——
 *     「多值」不是免检牌（D118）。导航最容易犯的错是**漏人**，而漏人从不报错。
 *  3. **0 条与没扫过不同形**（D115）：`scanned === 0` 是「这一格 0 个节点」；
 *     `scanned > 0 且 untagged === scanned` 是「这 N 个节点都没打标签」；
 *     `parent_exists === false` 是「这个分类名不存在」。三句不能合并成「无结果」。
 *  4. **中文只有一份**（D114）：层名（学科/领域/领域标签）在前端只有这一份；
 *     条目名由后端 `items[].label` 给，前端只做转义与排版，不加工。
 *
 * ★ `esc` 复用 `empty.js`（不本地再写一份）：那边已有 F45s 与 `panels.esc` 逐字比对；
 *   本模块 import `empty.js`，`empty.js` 不 import 本模块 ⇒ 无环。
 */

import { esc } from "./empty.js";

/**
 * 导航层级（顺序即下钻顺序）。
 * ★ 与 `03_知识层/queries.py` 的 `NAV_LEVELS` 逐值相等，由 `phase43_frontend_selfcheck.py`
 *   的 X18a 求差钉住 —— 这里写错或那边改顺序，判据必红。
 */
export const NAV_LEVELS = Object.freeze(["subject", "domain", "tags"]);

/** 层名的**唯一**一份中文（D114）。后端只给 key。 */
export const NAV_LEVEL_LABEL = Object.freeze({
  subject: "学科",
  domain: "领域",
  tags: "领域标签",
});

/**
 * 超过这么多个条目时，**后面的收进可展开的尾部**。
 *
 * ★ 为什么需要（实测，不是假设）：真实快照里「化学」下有 **28** 个领域，其中 **14 个各只有
 *   1 个节点**（`chem`、`chemistry`、`chemistry.aq` …）。全部铺开是一屏 28 行，重要的
 *   （chem.reaction 1,697）被淹没在一堆 1 里。
 * ★ 但**不隐藏**：用 `<details>` 折叠并写明「另有 N 个（合计 M 个节点）」——
 *   用户想看得见就得看得到（D110b：看不见的条目等于没有条目）。
 */
export const NAV_COLLAPSE_AFTER = 10;

/** 下一层；已是最后一层 ⇒ `null`（叶子层不再给「下钻」按钮）。 */
export function nextLevel(level) {
  const i = NAV_LEVELS.indexOf(String(level || ""));
  return (i < 0 || i + 1 >= NAV_LEVELS.length) ? null : NAV_LEVELS[i + 1];
}

/**
 * 当前要请求的层。
 * `stack` = 已下钻的路径（`[{level, key}, …]`）；空栈 ⇒ 顶层 `subject`。
 * ★ 用 `stack.length` 而不是 `stack[last].level` 推层：栈长度与层序**一一对应**，
 *   这样「路径写坏了」不会静默变成另一层（写坏了最多停在顶层，看得见）。
 */
export function currentLevel(stack) {
  const n = Array.isArray(stack) ? stack.length : 0;
  const i = Math.max(0, Math.min(n, NAV_LEVELS.length - 1));
  return NAV_LEVELS[i];
}

/**
 * 下钻一层：把**当前层**的 `key` 记进栈。
 *
 * ★ 为什么栈里要存 `level`：栈是「我点过哪些格」的**历史**，而 `level` 是
 *   「这个 key 属于哪一层」。二者必须一起存 —— 只存 key 的话，回退到第 1 层时
 *   浏览器没法知道第 1 层是学科还是领域（只能靠 `stack.length` 反推，一旦将来
 *   层级从三条变四条，历史栈就全部错位）。
 * ★ 末层 / 空 key ⇒ **原样返回**（不是"返回下一层"）：末层没有下一层，点了不该有反应。
 */
export function navPush(stack, key) {
  const s = Array.isArray(stack) ? stack : [];
  const lv = currentLevel(s);
  if (!key || nextLevel(lv) === null) return s.slice();
  return s.concat([{ level: lv, key: String(key) }]);
}

/** 回退到第 `n` 层（0 = 顶层）；越界**夹住**而不是抛错（点坏一个按钮不该白屏）。 */
export function navClip(stack, n) {
  const s = Array.isArray(stack) ? stack : [];
  const k = Math.floor(Number(n));
  if (!Number.isFinite(k)) return s.slice();
  return s.slice(0, Math.max(0, Math.min(k, s.length)));
}

/**
 * 由栈推出**这次要发的请求**：`{ level, parent }`。
 * ★ 与 `currentLevel` 同源（`level` 一律由栈长推出）—— 两处各推一次迟早分叉，
 *   症状就是「标题写按领域浏览、请求发的是 level=subject」。
 */
export function navTargetOf(stack) {
  const s = Array.isArray(stack) ? stack : [];
  return {
    level: currentLevel(s),
    parent: s.length ? String(s[s.length - 1].key) : null,
  };
}

/**
 * 取条目；非法项**剔除**（不猜、不补默认值）。
 * @returns {Array<{key:string,label:string,n:number,samples:Array}>}
 */
export function navItemsOf(env) {
  const raw = env && Array.isArray(env.items) ? env.items : [];
  const out = [];
  raw.forEach((it) => {
    if (!it || typeof it !== "object") return;
    const key = typeof it.key === "string" ? it.key : "";
    if (!key) return;
    const n = Number(it.n);
    if (!Number.isFinite(n) || n < 0) return;      // 计数读不出来 ⇒ 这格不可信，宁可不显示
    const samples = (Array.isArray(it.samples) ? it.samples : [])
      .filter((s) => s && typeof s === "object" && typeof s.id === "string" && s.id)
      .map((s) => ({
        id: s.id,
        label: typeof s.label === "string" && s.label ? s.label : s.id,
        type: typeof s.type === "string" ? s.type : "",
        degree: Number.isFinite(Number(s.degree)) ? Number(s.degree) : null,
      }));
    out.push({
      key,
      label: typeof it.label === "string" && it.label ? it.label : key,
      n: Math.round(n),
      samples,
    });
  });
  return out;
}

/** 计数元信息；字段缺失 ⇒ `null`（**不臆造** 0：「没有」与「不知道」必须分得开）。 */
export function navStatsOf(env) {
  const s = (env && env.stats && typeof env.stats === "object") ? env.stats : {};
  const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const bool = (v) => (typeof v === "boolean" ? v : null);
  return {
    scanned: num(s.scanned),
    sum_items: num(s.sum_items),
    conserved: bool(s.conserved),
    multi_valued: s.multi_valued === true,
    tagged: num(s.tagged),
    untagged: num(s.untagged),
    parent_exists: bool(s.parent_exists),
    full_nodes: num(s.full_nodes),
    elapsed_ms: num(s.elapsed_ms),
  };
}

/**
 * 数据面自相矛盾的提示（**只报已有字段能证明的事**）。
 * @returns {{kind:"bad"|"info", text:string}|null}
 */
export function navNotice(env) {
  const st = navStatsOf(env);
  const lv = env && typeof env.level === "string" ? env.level : "";
  const L = NAV_LEVEL_LABEL[lv] || "这一格";

  // ① 分类名不存在 —— 与「没有结果」是两件事
  if (st.parent_exists === false) {
    const p = env && typeof env.parent === "string" && env.parent ? `「${env.parent}」` : "";
    return {
      kind: "bad",
      text: `图里没有这个${L}${p} —— 不是「没有结果」，是这个分类名在快照里不存在。`,
    };
  }

  // ② 多值层：先查**它自己的**对账式，再解释为什么「不守恒是正常的」
  if (st.multi_valued) {
    if (st.scanned !== null && st.tagged !== null && st.untagged !== null
        && st.tagged + st.untagged !== st.scanned) {
      return {
        kind: "bad",
        text: `多值层的对账式不成立：有标签 ${st.tagged} + 无标签 ${st.untagged}`
          + ` ≠ 扫描 ${st.scanned} ⇒ 计数漏项（多值不是免检牌）。`,
      };
    }
    // ★ 一格都没有标签时（实测 `chem.reaction` 的 1,697 个节点全无标签）**不**输出这句：
    //   交给 `navEmptyText` 说清「全部没打标签」。否则会生成「各格之和 0 > 扫描 1697」——
    //   模板句 + 具体数字 = 一条随数据变真假的口号（D119）。这里只陈述**恒成立**的关系。
    if (st.sum_items === 0) return null;
    return {
      kind: "info",
      text: "领域标签是多值的：同一节点可计入多个标签，所以「各格之和」与「扫描数」"
        + `本来就不必相等（本次 ${st.sum_items} / ${st.scanned}）；可核的式子是`
        + `「有标签 ${st.tagged} + 无标签 ${st.untagged} = 扫描 ${st.scanned}」。`,
    };
  }

  // ③ 单归属层必须守恒 —— 不守恒就是**漏人**
  if (st.conserved === false && st.sum_items !== null && st.scanned !== null) {
    return {
      kind: "bad",
      text: `计数对不上：各格之和 ${st.sum_items} ≠ 扫描 ${st.scanned}`
        + ` ⇒ 有 ${st.scanned - st.sum_items} 个节点未被任何一格收录（导航漏人，属缺陷）。`,
    };
  }
  return null;
}

/** 条目为空时的说明 —— 四种「空」必须说成四句话（D115）。 */
export function navEmptyText(env) {
  const st = navStatsOf(env);
  if (st.parent_exists === false) return "";                 // 已有更准确的红字提示，不重复
  if (st.scanned === 0) return "这一格 0 个节点（已扫描，不是没扫）。";
  if (st.multi_valued && st.untagged !== null && st.scanned !== null
      && st.untagged === st.scanned) {
    return `这一格的 ${st.scanned} 个节点全部没有领域标签 —— 不是查询失败，是这份数据没打标签。`;
  }
  if (st.scanned !== null) return `这一格有 ${st.scanned} 个节点，但没有可展示的条目。`;
  return "没有可展示的条目（响应里没有扫描计数，无法判断是「0 个」还是「没扫」）。";
}

/**
 * 抽屉正文 HTML。
 * @param {object|null} env   `/api/nav` 响应信封
 * @param {{stack?:Array, loading?:boolean}} [opts]
 */
export function navHTML(env, opts = {}) {
  const stack = Array.isArray(opts.stack) ? opts.stack : [];
  const level = currentLevel(stack);
  const L = NAV_LEVEL_LABEL[level] || level;
  const parts = [];

  // 面包屑：`data-nav-back="i"` = 保留前 i 项（0 = 回到全部学科）
  parts.push('<nav class="nav-crumb" aria-label="导航路径">');
  parts.push('<button class="nav-crumb-btn" data-nav-back="0">'
    + `全部${esc(NAV_LEVEL_LABEL.subject)}</button>`);
  stack.forEach((s, i) => {
    parts.push('<span class="nav-sep">/</span>');
    parts.push(`<button class="nav-crumb-btn" data-nav-back="${i + 1}">`
      + `${esc(s && s.key)}</button>`);
  });
  parts.push("</nav>");

  parts.push(`<div class="nav-title">按${esc(L)}浏览`
    + '<span class="nav-hint">只给计数与前几个代表，不下载全图</span></div>');

  if (opts.loading) {
    parts.push('<div class="nav-load">载入中…</div>');
    return parts.join("");
  }
  if (!env) {
    parts.push('<div class="nav-empty">尚未载入。</div>');
    return parts.join("");
  }
  // ★ `no_result` **不在**失败分支里：它是正常的导航空态（分类存在但没内容），
  //   下面会走 `navEmptyText` 说清是哪一种空。把它当失败是 D115 的反面。
  if (env.status === "error" || env.status === "timeout" || env.status === "truncated") {
    parts.push('<div class="nav-bad">'
      + `请求失败：${esc(env.reason || env.error_code || "未知原因")}</div>`);
    return parts.join("");
  }

  const notice = navNotice(env);
  if (notice) parts.push(`<div class="nav-${notice.kind}">${esc(notice.text)}</div>`);

  const items = navItemsOf(env);
  const st = navStatsOf(env);
  if (st.scanned !== null) {
    parts.push(`<div class="nav-count">扫描 ${st.scanned} 个节点`
      + ` · ${items.length} 个${esc(L)}`
      + (st.elapsed_ms !== null ? ` · ${st.elapsed_ms} ms` : "") + "</div>");
  }
  if (!items.length) {
    const t = navEmptyText(env);
    if (t) parts.push(`<div class="nav-empty">${esc(t)}</div>`);
    return parts.join("");
  }

  const nx = nextLevel(level);
  const row = (it) => {
    const out = ['<li class="nav-row">'];
    if (nx) {
      out.push(`<button class="nav-go" data-nav-down="${esc(it.key)}"`
        + ` title="下钻到${esc(NAV_LEVEL_LABEL[nx])}">`
        + `<span class="nav-key">${esc(it.label)}</span>`
        + `<span class="nav-n">${it.n}</span>`
        + '<span class="nav-chev" aria-hidden="true">›</span></button>');
    } else {
      out.push('<div class="nav-leaf">'
        + `<span class="nav-key">${esc(it.label)}</span>`
        + `<span class="nav-n">${it.n}</span></div>`);
    }
    if (it.samples.length) {
      out.push('<div class="nav-samples">');
      it.samples.forEach((s) => {
        const t = s.degree === null ? "" : `（${s.degree} 条边）`;
        out.push(`<button class="nav-sample" data-nav-seed="${esc(s.id)}"`
          + ` title="以 ${esc(s.id)} 为种子查它的邻域">`
          + `${esc(s.label)}${esc(t)}</button>`);
      });
      out.push("</div>");
    }
    out.push("</li>");
    return out.join("");
  };

  const nHead = items.length > NAV_COLLAPSE_AFTER ? NAV_COLLAPSE_AFTER : items.length;
  parts.push('<ul class="nav-list">' + items.slice(0, nHead).map(row).join("") + "</ul>");
  const tail = items.slice(nHead);
  if (tail.length) {
    const tn = tail.reduce((a, b) => a + b.n, 0);
    parts.push('<details class="nav-more"><summary>'
      + `另有 ${tail.length} 个${esc(L)}（合计 ${tn} 个节点）</summary>`
      + '<ul class="nav-list">' + tail.map(row).join("") + "</ul></details>");
  }
  if (!nx) {
    parts.push('<div class="nav-hint">'
      + `${esc(NAV_LEVEL_LABEL.tags)}是最后一层：点代表节点即可查它的邻域。</div>`);
  }
  return parts.join("");
}
