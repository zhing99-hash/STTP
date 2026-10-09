/* A3 无头功能测试台
 * 用真实图谱快照 + 模拟 Cytoscape 环境，
 * 直接执行 graph_view.html 里 A3 新增的路径函数，验证语义正确性。
 * 用法: node 06_PoC/tests/a3_path_harness.cjs   （cwd 任意）
 *       STTP_GRAPH=06_PoC/graph_data_phaseNN.json 可指定快照，默认取最新 phase。
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

// 从脚本所在目录向上找项目根（含 06_PoC/graph_view.html 的那一层）
function findRoot(start) {
  let d = start;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, "06_PoC", "graph_view.html"))) return d;
    const up = path.dirname(d);
    if (up === d) break;
    d = up;
  }
  throw new Error("未找到项目根（缺 06_PoC/graph_view.html）");
}
const ROOT = findRoot(__dirname);
const HTML = path.join(ROOT, "06_PoC", "graph_view.html");

// 快照选择：STTP_GRAPH 环境变量 > .env 里的 GRAPH_DATA_FILE > 目录中最新的 phaseN
function pickGraph() {
  const env = process.env.STTP_GRAPH || process.env.GRAPH_DATA_FILE;
  if (env && fs.existsSync(path.join(ROOT, env))) return path.join(ROOT, env);
  const dir = path.join(ROOT, "06_PoC");
  const cands = fs.readdirSync(dir)
    .map(f => (/^graph_data_phase(\d+)\.json$/.exec(f) || [])[1])
    .filter(Boolean).map(Number).sort((a, b) => b - a);
  if (cands.length) return path.join(dir, `graph_data_phase${cands[0]}.json`);
  throw new Error("未找到任何 graph_data_phaseN.json 快照");
}
const GRAPH = pickGraph();

// ---- 1. 抽取 <script> 内联块 ----
const html = fs.readFileSync(HTML, "utf8");
const blocks = [...html.matchAll(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/g)]
  .map(m => m[1]);
const script = blocks.join("\n;\n");

// ---- 2. 模拟浏览器环境 ----
const fakeEl = () => ({
  style: {}, className: "", innerHTML: "", textContent: "", value: "",
  checked: false, addEventListener() {}, appendChild() {},
  querySelectorAll: () => [], querySelector: () => null,
  getAttribute: () => null, classList: { add() {}, remove() {} },
  setAttribute() {}, closest: () => null,
});
// A4 起页面用 "本地 vendor 优先 + document.write 回退 CDN" 的写法，
// 这里给出 write 桩（真实浏览器天然具备；vm 沙箱没有）。
// 顺带把写入内容收集起来，可用于断言兜底 URL 是否指向预期 CDN。
const writeLog = [];
const documentMock = {
  getElementById: () => fakeEl(),
  querySelectorAll: () => [],
  querySelector: () => null,
  createElement: () => fakeEl(),
  addEventListener() {},
  write: (s) => { writeLog.push(String(s)); },
};
const windowMock = { addEventListener() {}, MathJax: undefined };
const ctx = {
  console, document: documentMock, window: windowMock,
  MathJax: undefined, fetch: () => Promise.reject(new Error("offline")),
  setTimeout, clearTimeout, Promise, JSON, Number, String, Math, Set, Map, Array, Object, RegExp,
};
ctx.globalThis = ctx;
vm.createContext(ctx);

// ---- 3. 载入真实数据到脚本作用域，并注入测试逻辑 ----
const data = JSON.parse(fs.readFileSync(GRAPH, "utf8"));

const testCode = `
/* ===== 测试注入 ===== */
NODES = ${JSON.stringify(data.nodes.map(n => ({ id: n.id, label: n.label, subject: n.subject })))};
EDGES = ${JSON.stringify(data.edges.map(e => ({ id: e.id, source: e.source, target: e.target, type: e.type, kind: e.kind, confidence: e.confidence })))};

const _ids = new Set(NODES.map(n => n.id));
const _lab = new Map(NODES.map(n => [n.id, n.label || n.id]));
const _sub = new Map(NODES.map(n => [n.id, n.subject || "跨学科"]));
const emptyNode = id => ({
  empty: () => true, nonempty: () => false, id: () => id,
  data: () => undefined, hasClass: () => false,
  addClass() { return this; }, removeClass() { return this; },
});
const mkNode = id => ({
  empty: () => false, nonempty: () => true, id: () => id,
  data: k => (k === "label" ? (_lab.get(id) || id) : k === "subject" ? (_sub.get(id) || "跨学科") : undefined),
  hasClass: () => false,
  addClass() { return this; }, removeClass() { return this; },
});
cy = { getElementById: id => (_ids.has(id) ? mkNode(id) : emptyNode(id)) };

/* --- 用脚本自身的 bfsDirected 找真实跨域链 --- */
const bySubj = {};
NODES.forEach(n => { (bySubj[n.subject] = bySubj[n.subject] || []).push(n.id); });
const chem = bySubj["化学"] || [], phys = (bySubj["物理"] || []).slice(0, 400);

const found = [];
for (const c of chem.slice(0, 900)) {
  for (const p of phys) {
    const r = bfsDirected(c, p, 4);
    if (r.length) { found.push(r.slice().sort((a, b) => a.length - b.length)[0]); break; }
  }
  if (found.length >= 8) break;
}

const strip = h => h.replace(/<[^>]+>/g, " ").replace(/\\s+/g, " ").trim();
const out = [];
out.push("== 用脚本自身 bfsDirected 找到的真实跨域链 ==");
found.forEach((p, i) => {
  const hops = buildHops(p);
  const cross = countCross(p);
  out.push("");
  out.push("链 " + (i + 1) + "  节点 " + p.length + " · 跳数 " + (p.length - 1) + " · 跨域跳 " + cross);
  hops.forEach(h => {
    out.push("   " + (h.forward ? "→" : "←") + " " + h.type + "  conf=" + h.conf +
             "  " + (h.cross ? "[跨域]" : "[同域]") + "  " + h.from + " -> " + h.to +
             "  edgeId=" + (h.eid ? "有" : "无"));
  });
  out.push("   渲染: " + strip(renderChain(p)));
});

/* --- 反例 / 边界 --- */
out.push("");
out.push("== 边界用例 ==");
// (a) 同域链
const sameDom = bfsDirected(chem[0], chem[1], 3);
out.push("(a) 同域链 " + (sameDom.length ? sameDom[0].join(" -> ") + " | 跨域跳=" + countCross(sameDom[0]) : "未找到（" + chem[0] + "）"));
// (b) 自环
out.push("(b) src==dst: " + JSON.stringify(bfsDirected(chem[0], chem[0], 3)) + " | 跨域跳=" + countCross([chem[0]]));
// (c) 不可达
out.push("(c) 不可达对: " + JSON.stringify(bfsDirected(chem[0], "NO:SUCH:NODE", 3)));
// (d) 跨域 vs 跨学科(哨兵) 不应算跨域
const sentinel = NODES.filter(n => n.subject === "跨学科").map(n => n.id);
out.push("(d) 哨兵'跨学科'节点数=" + sentinel.length + "（不应被判为跨域对）");
// (e) pickEdge 方向优先
const e1 = pickEdge("PQ:energy", "PQ:mass"), e2 = pickEdge("PQ:mass", "PQ:energy");
out.push("(e) pickEdge energy->mass=" + (e1 ? e1.source + "=>" + e1.target : "null") +
         " ; mass->energy=" + (e2 ? e2.source + "=>" + e2.target : "null") + "（同一条边，方向由查询决定）");

out.join("\\n");
`;

let result;
try {
  result = vm.runInContext(script + "\n;\n" + testCode, ctx, { filename: "graph_view.inline.js" });
  console.log(result);
} catch (e) {
  console.error("[FAIL]", e && e.stack ? e.stack : e);
  process.exit(1);
}
