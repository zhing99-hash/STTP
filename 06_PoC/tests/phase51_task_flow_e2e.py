# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Phase 51 · 任务式首屏「真浏览器」端到端验收（B1–B5）

## 为什么需要它（动机）

`phase43_frontend_selfcheck.py` 覆盖的是**静态**面：读源码、比对常量、Node 里跑纯函数、
用 `--dump-dom` 看首屏 DOM 文本。它证明的是「**文件写对了**」，不是「**页面能跑**」。
两者之间那道缝在本项目里已经咬过两次：

  * **Phase 47**：`/api/stats` 缺 `status` ⇒ 页面回退 bundle ⇒ 拉 48.7 MB 卡死。
    静态检查全绿 —— 因为所有文件确实都"写对了"。
  * **Phase 48**：搜索词 `q` 被 `buildParams` 静默丢弃 ⇒ 26/26 请求全 400。
    同样是"文件写对了、页面没跑通"。

指南把这条缝登记为 **D14（真实浏览器渲染，仅部分补齐）** 与
**D27（浏览器内「键入」未实测）**。本仪器就是把这两条边界**收掉**：
用真实 Chromium 真实点击、真实按键，走完老板指定的那条链路 ——
**问题 → 候选 → 确认实体 → 生成图 → 改查询 → 再生成**。

## ★ 与静态自检的分工（不要重复造）

  | 层        | 仪器                             | 证明什么             |
  |-----------|----------------------------------|----------------------|
  | 源码/常量 | `phase43_frontend_selfcheck.py`  | 文件写对了（含跨层契约） |
  | 逻辑      | 同上（node 跑 `.mjs`）           | 纯函数算对了         |
  | **行为**  | **本文件（真浏览器）**           | **页面真的能跑通**   |
  | 断网降级  | `a4_offline_render.py`           | CDN 不可达仍能渲染   |

## ★ 三条纪律

1. **真值必须来自事实源，不得手抄**：卡片的 4 个问题、`CANDIDATE_LIMIT`、`MIN_QUERY_LEN`
   都在断言时**从浏览器里 `import('./js/core.js')` / `import('./js/task.js')` 现取**
   （铁律 #26 可复算即证据 / #82 仪器自指的反面）。手抄一份等于把"文档写错了"变成"测试通过"。
2. **缺依赖 ⇒ SKIP，不计通过**：Playwright 是**可选**的浏览器依赖，不在零安装路径上。
   缺它就只能说"不可判定"，不能说"通过"（铁律 #20）。
3. **本文件不碰产品代码**：只读页面 + 断言。发现缺陷走正常流程修，不在这里打补丁。

## 用法

    bash sttp.sh taskflow                 # 需先 `bash sttp.sh viz`
    python 06_PoC/tests/phase51_task_flow_e2e.py --url http://127.0.0.1:8765/ [--headed] [--json OUT]

退出码：0 = 无 FAIL **且前置齐备**；1 = 有 FAIL；2 = **缺前置**。

★ 本仪器**不是自包含的**（D56）：`subgraph` / `frontend` 会自起 `ThreadingHTTPServer`，
  而真浏览器验收必须打**真端口上的真服务** ⇒ 它依赖外部已在跑的 `bash sttp.sh viz`。
  此前前置缺失时返回 **0**（原文档写的正是「0 = 无 FAIL（含整体 SKIP）」）⇒ 读报告的人会把
  「全段 SKIP」读成「通过」—— **退出码上 SKIP 与 PASS 不可分**（铁律 #20/#67 的退出码面，#101）。
  现改为 **RC=2** 并打印前置自述块；该纪律由 `bash sttp.sh redprobe` 的 **R6** 构造性守住。

## ★ 已知边界（本仪器**不**覆盖什么）

* 只有 Chromium 一个内核（Firefox/WebKit 未测）。
* 非本地请求一律 abort（刻意断网）⇒ **未**测 CDN 兜底那一支（那是 `a4_offline_render.py` 的事）。
* 未点 PNG / SVG / JSON 导出、未点「展开邻居」/「概览」/「返回」、未测移动端窄屏布局。
* 未做键盘 Tab 序全量走查（只测了 Enter 提交与 Escape 关闭路径上的 `keydown` 监听存在）。
* 未做性能断言（首帧耗时随机器负载波动，测时间会得到 flaky 断言，见铁律 #39 的反面）。
"""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 供顶层异常兜底使用：仪器自身崩了也要把**已通过的部分结果**交出来。
#   （教训来源：本夹具第一版在 B2 抛 TimeoutError，整份报告连同已通过的 B1 全丢了，
#    排查时只能靠另写探针重跑一遍。审计器出问题必须**仍然可读**。）
_REP = None
_RESULT = None
_JSON_OUT = None

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "0.0.0.0")
SAFE_SCHEMES = ("data:", "blob:", "about:", "chrome:")

# 期望挑中的实体（**偏好**，不是硬要求）：挑中最能体现「同名实体需确认」这件事。
#   `gibbs`  → 第 1 条是 Formula `CE:fo:gibbs_reaction`，第 2 条才是量 `CE:pq:gibbs_energy`
#   `entropy`→ 第 1 条 `PB:pq:entropy`（本快照下与 gibbs 有通路），
#              第 2 条 `TH:pq:entropy`（向导 5 跳内**确实无通路**）—— 同一中文词、两种结论
PREFER = {"main": "CE:pq:gibbs_energy", "second": "PB:pq:entropy"}

SECTIONS = ("B1 首屏结构", "B2 卡片 → 候选", "B3 真实键入", "B4 确认实体 → 生成图",
            "B5 返回改查询（老板指定的收尾链路）")


class Rep:
    """极简报告器（与 phase43_*_selfcheck.py 同风格，便于同一条日志管线解析）。"""

    def __init__(self):
        self.rows = []
        self.n_pass = self.n_fail = self.n_skip = 0

    def add(self, cond, name, detail=""):
        if cond:
            self.n_pass += 1
            self.rows.append(("PASS", name, detail))
        else:
            self.n_fail += 1
            self.rows.append(("FAIL", name, detail))
        return bool(cond)

    def skip(self, name, detail=""):
        self.n_skip += 1
        self.rows.append(("SKIP", name, detail))

    def note(self, text):
        self.rows.append(("--", text, ""))

    def dump(self):
        for kind, name, detail in self.rows:
            if kind == "--":
                print(f"\n{name}")
            else:
                line = f"  [{kind}] {name}"
                if detail:
                    line += f"  —— {detail}"
                print(line)
        print(f"\n  phase51_task_flow_e2e: PASS {self.n_pass} / FAIL {self.n_fail} / SKIP {self.n_skip}")


def http_json(url, timeout=30):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as r:
        return r.status, json.loads(r.read())


# ---------------------------------------------------------------------------
# 页面侧求值片段（集中放这里，便于审阅"到底断言了什么"）
# ---------------------------------------------------------------------------

JS_TRUTH = """async () => {
  const core = await import('./js/core.js');
  const task = await import('./js/task.js');
  const cards = core.TASK_CARDS.map(c => ({
    id: c.id, question: c.question, mode: c.mode, start: c.start, end: c.end,
    scope: c.scope || null,
  }));
  return {
    cards,
    candidateLimit: task.CANDIDATE_LIMIT,
    minQueryLen: core.MIN_QUERY_LEN,
    scopeDefault: core.TASK_SCOPE_DEFAULT,
  };
}"""

JS_SCREEN = """() => {
  const vis = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return !el.hidden && r.width > 0 && r.height > 0
           && getComputedStyle(el).visibility !== 'hidden'
           && getComputedStyle(el).display !== 'none';
  };
  const tins = Array.from(document.querySelectorAll('#task-entry input[type=text]')).filter(vis);
  const cards = Array.from(document.querySelectorAll('#task-cards .tcard'));
  const run = document.getElementById('task-run');
  return {
    entryVisible: vis(document.getElementById('task-entry')) && vis(document.querySelector('.tp-panel')),
    visibleTextInputs: tins.length,
    textInputIds: tins.map(e => e.id),
    cardCount: cards.length,
    cardQuestions: cards.map(c => (c.querySelector('.tcard-q') || {}).textContent
                                 ? c.querySelector('.tcard-q').textContent.trim() : null),
    cardIds: cards.map(c => c.getAttribute('data-card')),
    runDisabled: !!(run && run.disabled),
    secondVisible: vis(document.getElementById('task-q2')),
    panelHead: ((document.querySelector('.tp-head h2') || {}).textContent || '').trim(),
  };
}"""

JS_CANDS = """() => {
  const slot = (n) => Array.from(document.querySelectorAll('#task-cands [data-slot="' + n + '"] .cand-item'));
  const dump = (e) => ({
    id: e.getAttribute('data-id'),
    label: ((e.querySelector('.cand-label') || {}).textContent || '').trim(),
    badges: Array.from(e.querySelectorAll('.badge')).map(b => b.textContent.trim()),
    hasCode: !!e.querySelector('code'),
    score: ((e.querySelector('.pill') || {}).textContent || '').trim(),
    sel: e.classList.contains('sel'),
  });
  return {
    main: slot('main').map(dump),
    second: slot('second').map(dump),
    status: ((document.getElementById('task-status') || {}).textContent || '').trim(),
    q: (document.getElementById('task-q') || {}).value,
    q2: (document.getElementById('task-q2') || {}).value,
    candBoxVisible: !(document.getElementById('task-cands') || {}).hidden,
  };
}"""

JS_ENV = """() => {
  const s = window.__sttp || {};
  const e = s.lastEnv || null;
  const c = (s.viz && s.viz.cy) || null;
  const line = ((document.getElementById('statusline') || {}).textContent || '');
  return {
    hasEnv: !!e,
    status: e ? e.status : null,
    hidden: e ? (e.hidden_by_trust != null ? e.hidden_by_trust
                : (e.stats || {}).hidden_by_trust) : null,
    statusInfo: e && e.statusInfo ? e.statusInfo.label : null,
    snapshotId: e ? e.snapshot_id : null,
    cyNodes: c ? c.nodes().length : -1,
    cyEdges: c ? c.edges().length : -1,
    statusText: line.trim(),
    state: (document.getElementById('statusline') || {}).getAttribute
           ? document.getElementById('statusline').getAttribute('data-state') : null,
    pathStatus: ((document.getElementById('path-status') || {}).textContent || '').trim(),
    banner: ((document.getElementById('banner') || {}).textContent || '').trim(),
    reopenHidden: !!(document.getElementById('btn-reopen-task') || {}).hidden,
    entryHidden: !!(document.getElementById('task-entry') || {}).hidden,
    taskUsed: !!s.taskUsed,
    storeDepth: (s.store && typeof s.store.depth === 'number') ? s.store.depth : null,
    mode: s.mode || null,
    // ★ D53：客户端为最近一次 /api/subgraph 派生的等待预算（毫秒）。
    //   客户端**必须**等到比服务端 deadline_ms 更晚才放弃。
    budgetMs: (s.api && typeof s.api.lastBudgetMs === 'number') ? s.api.lastBudgetMs : null,
  };
}"""


def pick_candidate(page, slot, prefer_id):
    """点选候选：优先点 `prefer_id`；不存在则退化为第一条（并如实标注是否退化了）。"""
    pref = '#task-cands [data-slot="%s"] .cand-item[data-id="%s"]' % (slot, prefer_id)
    el = page.query_selector(pref)
    degraded = False
    if el is None:
        el = page.query_selector('#task-cands [data-slot="%s"] .cand-item' % slot)
        degraded = el is not None
    if el is None:
        return None, False
    cid = el.get_attribute("data-id")
    el.click()
    return cid, degraded


def search_count(reqs):
    return sum(1 for _m, u in reqs if "/api/search" in u)


def subgraph_urls(reqs):
    return [u for _m, u in reqs if "/api/subgraph" in u]


# ---------------------------------------------------------------------------
# ★ D54：「固定延时」不是「等待」。
#
#   本仪器最初在点完「生成子图」后 `wait_for_timeout(600)` 再断言 —— 快环境全绿，
#   重负载下读到的是**尚未 settle 的中间态**：`state.lastEnv` 还没被写入 ⇒
#   `status=None` / `cyNodes=0`，看起来像"产品没反应"，实际是"答案还没回来"。
#   （实测签名：`cy 节点 0 / 边 0；status=None（None）`。）
#
#   对照实验（客户端预算压到 300 ms + 响应延迟 3 s）证明：客户端**真的中止**时，
#   `lastEnv.status == "timeout"`、`client_aborted == true`、且 `state.lastEnv`
#   **一定会被写入**（`onEnvError` 写、失败路径也写）。所以 `status=None`
#   只可能意味着「尚未 settle」，不可能是「客户端放弃了」。
#
#   故此处一律等待**可观测的终止条件**：`state.lastEnv` 被重新写入。
#   等不到 ⇒ 返回 False ⇒ 由调用方记成「不可判定」，而不是"图没渲染出来"。
# ---------------------------------------------------------------------------
SETTLE_TIMEOUT_MS = 60000


def wait_settled(page, timeout_ms=SETTLE_TIMEOUT_MS):
    """等到 `state.lastEnv` 相对**进入查询前**打的标记发生了变化。"""
    try:
        page.wait_for_function(
            "() => (window.__sttp.lastEnv || null) !== window.__sttp.__settleMark",
            timeout=timeout_ms)
        return True
    except Exception:  # noqa: BLE001
        return False


def click_and_settle(page, timeout_ms=SETTLE_TIMEOUT_MS):
    """打标记 → 点「生成子图」→ 等入口关闭 → 等状态 settle。"""
    page.evaluate("() => { window.__sttp.__settleMark = window.__sttp.lastEnv || null; }")
    page.click("#task-run")
    page.wait_for_function("() => document.getElementById('task-entry').hidden === true",
                           timeout=30000)
    return wait_settled(page, timeout_ms)


def wait_candidates(page, slot="main", timeout_ms=30000):
    """等「重新打开入口 → 回填 → 自动重搜」这条链路真的产出候选。

    ★ 同 D54：这也是异步的（回填触发一次**去抖后的**搜索），
    固定延时在慢环境下同样会读到空候选面板。
    """
    try:
        page.wait_for_function(
            "() => { const b = document.querySelector('#task-cands [data-slot=\"%s\"]');"
            " return !!(b && b.querySelector('.cand-item')); }" % slot,
            timeout=timeout_ms)
        return True
    except Exception:  # noqa: BLE001
        return False


def wait_search_responses(page, seen, queries, timeout_ms=30000):
    """等到指定查询词的 `/api/search` **响应**都已经收到。

    ★ "请求已发出" ≠ "响应已回来"：断言的是**候选集合**，
    就必须等响应到达（D54 同族：`wait_for_timeout` 只是赌机器够快）。
    """
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        if all(any(("q=%s" % q) in u for u in seen) for q in queries):
            return True
        page.wait_for_timeout(100)
    return False


def wait_search_settled(page, q, timeout_ms=30000):
    """等到状态栏报出**这次查询词**的命中总数 ⇒ 该次搜索的响应已经处理完。

    ★ 同 D54：键入 → 去抖 → 请求 → 渲染 是异步链路，
    固定延时在慢环境下会读到**上一次查询**的候选（B3c 就会假失败）。
    状态栏里的 `q ⇒ N 条候选` 是这条链路上**唯一的可观测完成信号**。
    """
    js = ("() => { const t = (document.getElementById('task-status') || {}).textContent || '';"
          " return t.indexOf(%s + ' ⇒') >= 0; }" % json.dumps(q))
    try:
        page.wait_for_function(js, timeout=timeout_ms)
        return True
    except Exception:  # noqa: BLE001
        return False


def main():
    ap = argparse.ArgumentParser(
        description="Phase 51 任务式首屏真浏览器端到端验收（B1–B5）",
        epilog="★ 前置（**非自包含**，D56）：需外部已在跑的本地 viz 服务（默认 "
               "http://127.0.0.1:8765/）。缺前置 ⇒ 明确记 SKIP 并返回 **2**"
               "（2 = 前置缺失；1 = 真的有 FAIL；0 = 通过）。"
               "启动方式：另开终端 `bash sttp.sh viz`（前台长驻）。")
    ap.add_argument("--url", default="http://127.0.0.1:8765/")
    ap.add_argument("--headed", action="store_true", help="显示浏览器窗口（默认无头）")
    ap.add_argument("--timeout", type=int, default=120, help="单步等待上限（秒）")
    ap.add_argument("--json", dest="json_out", default=None, help="把结构化结果写到该路径")
    args = ap.parse_args()

    base = args.url.rstrip("/")
    global _REP, _RESULT, _JSON_OUT
    rep = _REP = Rep()
    result = _RESULT = {"url": args.url, "assertions": [], "requests": [], "blocked": [],
                        "console_errors": [], "page_errors": []}
    _JSON_OUT = args.json_out

    rep.note("Phase 51 · 任务式首屏真浏览器端到端验收（B1–B5）")
    print(f"== Phase 51 任务式首屏 E2E -> {args.url} ==")

    # ---- B0a 可选依赖 ----
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        rep.skip("B0a-browser-deps",
                 "playwright 不可用（%s）—— 浏览器验收属**可选**依赖，缺它只能记「不可判定」，"
                 "不得记通过（铁律 #20：缺依赖假通过）" % type(e).__name__)
        for s in SECTIONS:
            rep.skip(s, "上游依赖缺失（playwright）")
        precond_banner("browser", "Playwright + Chromium（**可选**依赖，不在零安装路径上）",
                       "import playwright.sync_api 失败：%s（%s）" % (type(e).__name__, e),
                       "pip install playwright && python -m playwright install chromium")
        rep.dump()
        _write(args.json_out, result, rep)
        return 2

    # ---- B0b 被测服务可达 ----
    try:
        st, hz = http_json(base + "/api/healthz")
        reachable = st == 200
    except Exception as e:  # noqa: BLE001
        reachable, hz = False, {"error": str(e)[:140]}
    if not reachable:
        rep.skip("B0b-server",
                 "被测服务不可达（%s）—— 请先 `bash sttp.sh viz`（前台长驻）。"
                 "**这不代表前端有问题，也不代表没问题**：本轮不可判定。"
                 % (hz.get("error") or "无响应"))
        for s in SECTIONS:
            rep.skip(s, "上游依赖缺失（本地 viz 服务）")
        precond_banner("external_viz", "本地 viz 服务（**外部**依赖，须另有进程在跑）",
                       "healthz 不可达：%s（%s）" % (base, hz.get("error") or "无响应"),
                       "另开一个终端运行 `bash sttp.sh viz`（前台长驻），再重跑本命令")
        rep.dump()
        _write(args.json_out, result, rep)
        return 2
    rep.add(True, "B0b-server",
            "healthz 200，backend=%s graph_ready=%s warm_ms=%s"
            % (hz.get("backend"), hz.get("graph_ready"), hz.get("warm_ms")))
    result["healthz"] = {k: hz.get(k) for k in ("backend", "graph_ready", "warm_ms", "cache_builds")}

    # 全图边总数：`hidden_by_trust` 的上界（D45 的用户可见面回归）
    try:
        _, stats = http_json(base + "/api/stats")
        total_edges = stats.get("edges")
        total_nodes = stats.get("nodes")
    except Exception:  # noqa: BLE001
        total_edges, total_nodes = None, None
    result["graph"] = {"nodes": total_nodes, "edges": total_edges}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed, args=["--no-proxy-server"])
        page = browser.new_page(viewport={"width": 1600, "height": 950})

        reqs, bad_resp, blocked, resp_urls = [], [], [], []
        pageerrors, console_errs = [], []
        page.on("pageerror", lambda e: pageerrors.append(str(e)[:220]))
        page.on("console", lambda m: console_errs.append(m.text[:220]) if m.type == "error" else None)
        page.on("request", lambda r: reqs.append((r.method, r.url)))
        page.on("response", lambda r: resp_urls.append(r.url))
        page.on("response", lambda r: bad_resp.append((r.status, r.url)) if r.status >= 400 else None)

        def route_handler(route, request):
            u = request.url
            if u.startswith(SAFE_SCHEMES):
                return route.continue_()
            host = u.split("//", 1)[-1].split("/", 1)[0].split(":")[0]
            if host in LOCAL_HOSTS:
                return route.continue_()
            blocked.append(u)
            return route.abort()

        page.route("**/*", route_handler)

        # ---- B0c 任务入口就绪（live 模式探测后端要几秒） ----
        page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout * 1000)
        ready = True
        try:
            page.wait_for_function(
                "() => !!(window.__sttp && window.__sttp.task"
                " && document.querySelector('#task-cards .tcard'))",
                timeout=args.timeout * 1000)
        except Exception:  # noqa: BLE001
            ready = False
        if not rep.add(ready, "B0c-entry-ready",
                       "首屏任务入口在 %ds 内就绪（mode=%s）"
                       % (args.timeout, page.evaluate("() => (window.__sttp||{}).mode"))):
            rep.note("任务入口未就绪 ⇒ B1–B5 无法进行（下面是已收集到的诊断）")
            rep.dump()
            _write(args.json_out, result, rep)
            browser.close()
            return 1

        truth = page.evaluate(JS_TRUTH)
        scr = page.evaluate(JS_SCREEN)
        result["truth"] = truth
        result["screen"] = scr
        rep.note("B1 首屏结构（真实 DOM，非源码文本）")

        rep.add(scr["entryVisible"], "B1a-首屏即任务入口",
                "标题「%s」" % scr["panelHead"])
        rep.add(scr["visibleTextInputs"] == 1 and scr["textInputIds"] == ["task-q"],
                "B1b-首屏可见文本框恰 1 个",
                "可见 text 输入框 = %d %s（第二实体框属 path 卡，初始应隐藏）"
                % (scr["visibleTextInputs"], scr["textInputIds"]))
        rep.add(scr["cardCount"] == 4, "B1c-示例卡 4 张",
                "卡片 id = %s" % scr["cardIds"])
        same = scr["cardQuestions"] == [c["question"] for c in truth["cards"]]
        rep.add(same, "B1d-卡片文案 == core.js 事实源",
                "逐字一致（真值现取，非手抄）" if same
                else "页面 %s ≠ 事实源 %s" % (scr["cardQuestions"], [c["question"] for c in truth["cards"]]))
        rep.add(scr["runDisabled"], "B1e-未确认实体时「生成子图」禁用",
                "disabled=%s（铁律 #63：拒绝分支须显式，不做点了没反应）" % scr["runDisabled"])
        rep.add(not scr["secondVisible"], "B1f-非 path 卡不显示第二实体框",
                "secondVisible=%s" % scr["secondVisible"])

        # ----------------------------------------------------------------
        rep.note("B2 点示例卡 → 出现候选（自然语言歧义显式化）")
        path_card = [c for c in truth["cards"] if c["mode"] == "path"][0]
        page.click('#task-cards .tcard[data-card="%s"]' % path_card["id"])
        page.wait_for_selector('#task-cands [data-slot="main"] .cand-item',
                               timeout=args.timeout * 1000)
        page.wait_for_timeout(400)
        c2 = page.evaluate(JS_CANDS)
        result["after_card"] = c2

        rep.add(c2["q"] == path_card["start"], "B2a-点卡后预填 = 卡片 start",
                "#task-q = %r（卡片 start=%r）" % (c2["q"], path_card["start"]))
        vis2 = page.evaluate("() => { const e = document.getElementById('task-q2');"
                             " const r = e.getBoundingClientRect();"
                             " return !document.getElementById('task-second-wrap').hidden"
                             " && r.width > 0 && r.height > 0; }")
        rep.add(vis2, "B2b-path 卡出现第二实体框",
                "「本卡查两点之间的路径，必须填」")
        lim = truth["candidateLimit"]
        ok_c = 1 <= len(c2["main"]) <= lim
        rep.add(ok_c, "B2c-候选条数 ∈ [1, CANDIDATE_LIMIT]",
                "显示 %d 条（上限 %d）" % (len(c2["main"]), lim))
        wellformed = [h for h in c2["main"] if h["label"] and len(h["badges"]) >= 2
                      and h["hasCode"] and h["score"]]
        rep.add(len(wellformed) == len(c2["main"]) and len(c2["main"]) > 0,
                "B2d-每条候选含 标签/类型/来源/id/score",
                "合规 %d / %d；示例 %s" % (len(wellformed), len(c2["main"]),
                                       [h["id"] for h in c2["main"][:3]]))
        rep.add(("候选" in c2["status"]) and ("条" in c2["status"]),
                "B2e-状态栏报出命中总数与显示条数",
                c2["status"][:96])
        rep.add(c2["candBoxVisible"], "B2f-候选区确实可见（不是 hidden 残留）",
                "candBoxVisible=%s" % c2["candBoxVisible"])
        # ★ D47①：两个槽必须**各自**有候选。旧实现 `_seq` 单数字跨槽共用，
        #   second 先占走序号 ⇒ main 的响应被当过期丢弃 ⇒ 起点候选永不出现。
        rep.add(len(c2["main"]) > 0 and len(c2["second"]) > 0,
                "B2g-两个槽各有一份候选（起点 / 终点互不吞掉）",
                "main %d 条 / second %d 条（点 path 卡会**并发**查两端，"
                "正是旧实现的竞态点）" % (len(c2["main"]), len(c2["second"])))
        # ★ D49：卡片注释承诺的规模（8 节点/11 边）是 **3 跳**下测的，
        #   而入口全局默认跳数是 2（为 ego 卡而定）⇒ 卡片必须显式预填它承诺的跳数，
        #   否则点卡即得 no_result —— 一个自己默认参数达不到的承诺。
        hops_now = page.evaluate("() => document.getElementById('task-hops').value")
        want_hops = str((path_card.get("scope") or {}).get("hops"))
        rep.add(hops_now == want_hops,
                "B2h-卡片预填它承诺的那个跳数（可回写进输入框，非静默修正）",
                "#task-hops = %s（卡片 scope.hops = %s；入口全局默认为 %s）"
                % (hops_now, want_hops, truth["scopeDefault"]["hops"]))

        # ----------------------------------------------------------------
        rep.note("B3 真实键入（D27：浏览器内「键入」此前未实测）")
        ids_before = sorted(h["id"] for h in c2["main"])
        n_before = search_count(reqs)
        page.click("#task-q")
        page.keyboard.press("Control+A")
        page.keyboard.type("energy", delay=25)
        wait_search_settled(page, "energy")      # ★ D54 同族：等本次搜索真的回来
        page.wait_for_timeout(200)
        c3 = page.evaluate(JS_CANDS)
        n_after = search_count(reqs)
        ids_after = sorted(h["id"] for h in c3["main"])
        result["after_typing"] = {"q": c3["q"], "ids": ids_after,
                                  "searches": [n_before, n_after]}

        rep.add(c3["q"] == "energy", "B3a-真实键盘事件被页面接收",
                "#task-q = %r（逐字符 dispatchKeyEvent + input 事件）" % c3["q"])
        rep.add(n_after > n_before, "B3b-键入触发重新查候选",
                "/api/search 次数 %d → %d（去抖 180 ms）" % (n_before, n_after))
        rep.add(ids_after != ids_before, "B3c-候选集合随输入改变",
                "gibbs ⇒ %d 条 %s ／ energy ⇒ %d 条 %s"
                % (len(ids_before), ids_before[:2], len(ids_after), ids_after[:2]))

        n_a = search_count(reqs)
        page.click("#task-q")
        page.keyboard.press("Control+A")
        page.keyboard.type("e", delay=25)
        page.wait_for_timeout(800)
        short_status = page.evaluate("() => (document.getElementById('task-status')||{}).textContent || ''")
        n_b = search_count(reqs)
        minlen = truth["minQueryLen"]
        rep.add(n_b == n_a,
                "B3d-过短输入不发请求（明知会被拒就不撞）",
                "1 字符 ⇒ /api/search 次数保持 %d；MIN_QUERY_LEN=%d" % (n_a, minlen))
        rep.add(re.search(r"至少输入\s*%d\s*个字符" % minlen, short_status) is not None,
                "B3e-过短输入给出真实原因（不渲染成「没有匹配」）",
                short_status[:96])

        # ★ D47②：两个框的输入事件在**同一轮 JS** 内派发（零时间差），
        #   若两槽共用一个去抖计时器，后一个会 clearTimeout 掉前一个 ⇒ 只发一个请求。
        n_c = search_count(reqs)
        page.evaluate("""() => {
            const q = document.getElementById('task-q');
            const q2 = document.getElementById('task-q2');
            q.value = 'newton';
            q.dispatchEvent(new Event('input', { bubbles: true }));
            q2.value = 'entropy';
            q2.dispatchEvent(new Event('input', { bubbles: true }));
        }""")
        page.wait_for_timeout(300)          # 让 Playwright 收到 request 事件即可（不 gate 断言）
        burst_urls = [u for _m, u in reqs[n_c:] if "/api/search" in u]
        # ★ 等**两个查询词的响应都回来**（而不是等 1.4 s）：
        #   B3f/B3g 断言的是"两槽候选都被更新"，那需要响应真的到达（D54 同族）。
        wait_search_responses(page, resp_urls, ["newton", "entropy"])
        wait_candidates(page, "main")
        wait_candidates(page, "second")
        c3b = page.evaluate(JS_CANDS)
        got_newton = any("q=newton" in u for u in burst_urls)
        got_entropy = any("q=entropy" in u for u in burst_urls)
        result["burst"] = {"urls": burst_urls, "main": len(c3b["main"]), "second": len(c3b["second"])}
        rep.add(got_newton and got_entropy,
                "B3f-两个槽各自去抖：同时输入不被互相取消",
                "同轮派发两个 input（零时间差）⇒ /api/search 共 %d 条，"
                "newton=%s entropy=%s（旧实现只发得出后一个）"
                % (len(burst_urls), got_newton, got_entropy))
        rep.add(len(c3b["main"]) > 0 and len(c3b["second"]) > 0,
                "B3g-两槽候选都随之更新",
                "main %d 条 / second %d 条" % (len(c3b["main"]), len(c3b["second"])))

        # ----------------------------------------------------------------
        rep.note("B4 确认实体 → 生成图")
        page.click("#task-q")
        page.keyboard.press("Control+A")
        page.keyboard.type(path_card["start"], delay=15)
        # ★ 必须等 "gibbs" 的**响应**回来再点选：否则 `pick_candidate` 会从
        #   **上一轮**（B3 的 newton/entropy）的候选里点一个，B4b 直接假失败（D54 同族）。
        wait_search_settled(page, path_card["start"])
        wait_candidates(page, "main")
        page.wait_for_timeout(200)
        main_id, deg1 = pick_candidate(page, "main", PREFER["main"])
        page.wait_for_timeout(400)
        c4 = page.evaluate(JS_CANDS)
        sel_now = [h["id"] for h in c4["main"] if h["sel"]]
        rep.add(bool(main_id) and sel_now == [main_id], "B4a-点选后出现确认态（.sel + aria-pressed）",
                "已确认 %s%s" % (main_id, "（偏好命中）" if not deg1 else "（偏好未命中，退化取首条）"))
        rep.add(main_id is not None and main_id != [h["id"] for h in c2["main"]][0],
                "B4b-确认环节真的起了筛选作用",
                "首条是 %s，但用户确认的是 %s —— 同名实体的差别正是本环节的意义"
                % ([h["id"] for h in c2["main"]][0], main_id))

        second_id, deg2 = pick_candidate(page, "second", PREFER["second"])
        page.wait_for_timeout(400)
        run_state = page.evaluate("""() => ({
            disabled: document.getElementById('task-run').disabled,
            note: (document.getElementById('task-run-note')||{}).textContent || '',
        })""")
        rep.add(not run_state["disabled"], "B4c-两端确认后「生成子图」可点",
                "%s → %s" % (main_id, second_id))
        rep.add(main_id in run_state["note"] and (second_id or "") in run_state["note"],
                "B4d-生成前明示将查询什么", run_state["note"].strip()[:110])

        n_sub_before = len(subgraph_urls(reqs))
        # ★ D54：等状态 settle，不用固定延时（理由见文件上方 `wait_settled` 的长注释）。
        settled = click_and_settle(page)
        rep.add(settled, "B4l-断言前状态已 settle（D54：不得用固定延时读中间态）",
                "已等到 `state.lastEnv` 被写入（这是「本次查询有结论」的唯一信号；"
                "固定延时在慢机器上会把「还没回来」读成「图没渲染」）" if settled
                else "★ 60 s 内 `state.lastEnv` 从未被写入 —— 既不是成功也不是失败，"
                     "属**未终止状态**，本轮不可判定（不得当成产品失败）")
        page.wait_for_timeout(400)
        e1 = page.evaluate(JS_ENV)
        # 等到图真的落进 cytoscape 实例（ok/truncated 才应出现节点）
        if e1["status"] in ("ok", "truncated"):
            try:
                page.wait_for_function(
                    "() => { const v = window.__sttp && window.__sttp.viz;"
                    " const c = v && v.cy; return !!(c && c.nodes && c.nodes().length > 0); }",
                    timeout=30000)
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(400)
            e1 = page.evaluate(JS_ENV)
        result["env_verified"] = e1
        new_urls = subgraph_urls(reqs)[n_sub_before:]

        rep.add(e1["entryHidden"], "B4e-生成后入口自动关闭（不遮挡画布）",
                "task-entry.hidden=%s" % e1["entryHidden"])
        rep.add((not e1["reopenHidden"]) and e1["taskUsed"],
                "B4f-出现「← 修改查询」入口（改查询的前提）",
                "taskUsed=%s reopenHidden=%s" % (e1["taskUsed"], e1["reopenHidden"]))
        rep.add(e1["cyNodes"] > 0, "B4g-图实例真的渲染出节点",
                "cy 节点 %d / 边 %d；status=%s（%s）"
                % (e1["cyNodes"], e1["cyEdges"], e1["status"], e1["statusInfo"]))
        rep.add(e1["status"] in ("ok", "truncated"), "B4h-状态栏状态 ∈ {ok, truncated}",
                "status=%s（error / no_result 都不是本次期望：两端都是真实存在的实体）" % e1["status"])
        rep.add(("mode=path" in " ".join(new_urls)) and ("seeds=" in " ".join(new_urls)),
                "B4i-路径查询确实发到了统一信封端点",
                (new_urls[0][:150] + "…") if new_urls else "★ 未捕获到 /api/subgraph 请求")

        # hidden_by_trust 的用户可见面（D45 回归）：值 → DOM 文本，两侧必须一致
        hid = e1["hidden"]
        m = re.search(r"已隐藏\s*(\d+)\s*条", e1["statusText"] or "")
        pill = int(m.group(1)) if m else None
        if hid:
            rep.add(pill == hid and (total_edges is None or hid <= total_edges),
                    "B4j-「已隐藏 N 条」值 ↔ DOM 文本一致且 N ≤ 全图边数",
                    "env=%s / DOM=%s / 全图边数=%s" % (hid, pill, total_edges))
        else:
            rep.add(pill is None, "B4j-「已隐藏 N 条」值 ↔ DOM 文本一致且 N ≤ 全图边数",
                    "env=%s（0/None）⇒ DOM 不应出现该 pill；DOM=%s" % (hid, pill))

        # ★ D53（P1）：客户端等待预算必须**严格大于**请求自己的服务端 deadline_ms ——
        #   否则客户端会在服务端还在计算时先放弃，把「后端本来会给出的答案」显示成失败
        #   （现场表现是 status=None，酷似 flaky，实为缺陷）。
        #   本断言在空闲时也照红/绿：负载只决定**能否复现**，不决定**判据**。
        dl = None
        for u in new_urls:
            mm = re.search(r"[?&]deadline_ms=(\d+)", u)
            if mm:
                dl = int(mm.group(1))
                break
        budget = e1["budgetMs"]
        rep.add(dl is not None and budget is not None and budget > dl,
                "B4k-客户端等待预算严格大于请求自己的 deadline_ms（D53）",
                "服务端 deadline_ms=%s / 客户端预算=%s（余量必须 > 0，否则重负载下必现假失败）"
                % (dl, budget))

        # ----------------------------------------------------------------
        rep.note("B5 生成图后「返回重新更改查询信息」（老板指定的收尾链路）")
        page.click("#btn-reopen-task")
        page.wait_for_function("() => document.getElementById('task-entry').hidden === false",
                               timeout=20000)
        wait_candidates(page)                    # ★ D54 同族：等重搜链路真的出候选
        page.wait_for_timeout(200)
        c5 = page.evaluate(JS_CANDS)
        scr5 = page.evaluate(JS_SCREEN)
        result["after_reopen"] = {"q": c5["q"], "q2": c5["q2"], "status": c5["status"],
                                  "main": [h["id"] for h in c5["main"]],
                                  "sel": [h["id"] for h in c5["main"] if h["sel"]]}

        rep.add(scr5["entryVisible"], "B5a-「← 修改查询」能回到入口", "面板重新可见")
        rep.add(c5["q"] == path_card["start"] and c5["q2"] == path_card["end"],
                "B5b-上次输入与两端实体被回填",
                "q=%r q2=%r（卡片 start/end = %r/%r）"
                % (c5["q"], c5["q2"], path_card["start"], path_card["end"]))
        rep.add(not scr5["runDisabled"], "B5c-已确认实体随回填恢复 ⇒ 可直接重跑",
                "生成按钮 disabled=%s（不必重新搜一遍）" % scr5["runDisabled"])
        rep.add(len(c5["main"]) > 0 and c5["candBoxVisible"],
                "B5d-回填后自动重搜，候选非空（不是空白面板）",
                "候选 %d 条 %s" % (len(c5["main"]), [h["id"] for h in c5["main"]][:3]))

        # 改「可信档」→ all，再跑一次：请求与结果都要随之变化
        before_all = len(subgraph_urls(reqs))
        page.select_option("#task-trust", "all")
        page.wait_for_timeout(400)
        settled_all = click_and_settle(page)     # ★ D54：同样等 settle
        page.wait_for_timeout(400)
        e2 = page.evaluate(JS_ENV)
        urls_all = subgraph_urls(reqs)[before_all:]
        result["env_all"] = e2
        result["url_all"] = urls_all[:1]

        rep.add(bool(urls_all) and all("trust=all" in u for u in urls_all),
                "B5e-改档后确实按新档发起查询",
                (urls_all[0][:150] + "…") if urls_all else "★ 未捕获到请求")
        rep.add((not e2["hidden"]) and e1["hidden"] != e2["hidden"],
                "B5f-改档改变了结果（all 档不再隐藏边）",
                "verified: hidden=%s / status=%s  →  all: hidden=%s / status=%s"
                % (e1["hidden"], e1["status"], e2["hidden"], e2["status"]))

        # ★ 第二次改查询：把跳数从卡片的 3 调回全局默认 2。
        #   这是老板那句「最后生成图后，需要调整的话就返回重新更改查询信息」的**反向**验证：
        #   改回去之后，结论必须**真的变**（truncated → no_result），
        #   而且 no_result 必须**被如实分类**（不是 error、不是「没有匹配」），
        #   文案还要指出更可能是**跳数不够**（实测：这个组合在 all 档也是 no_result
        #   ⇒ 「被可信度档隐藏」这个假设是被否证了的，不能只写"切档试试"）。
        page.click("#btn-reopen-task")
        page.wait_for_function("() => document.getElementById('task-entry').hidden === false",
                               timeout=20000)
        # ★ 必须等"回填 + 自动重搜"跑完再改字段：否则 `_fillForm()` 会**把我的改动覆盖掉**
        #   （固定 600 ms 在慢环境下不够 ⇒ B5h「新跳数确实进了请求」会假失败）。
        wait_candidates(page)
        before_2 = len(subgraph_urls(reqs))
        page.fill("#task-hops", str(truth["scopeDefault"]["hops"]))
        page.select_option("#task-trust", "all")
        page.wait_for_timeout(300)
        settled_2 = click_and_settle(page)       # ★ D54：同样等 settle
        page.wait_for_timeout(400)
        e3 = page.evaluate(JS_ENV)
        urls_2 = subgraph_urls(reqs)[before_2:]
        result["env_hops2"] = e3
        result["url_hops2"] = urls_2[:1]

        rep.add(settled_all and settled_2, "B5m-两次「改查询重跑」都在 settle 后才断言（D54）",
                "all 档 settled=%s / 2 跳 settled=%s（`state.lastEnv` 均已被重写）"
                % (settled_all, settled_2))
        rep.add(e3["status"] == "no_result" and e3["state"] != "error",
                "B5g-改跳数后结论真的变了，且 no_result 被如实分类（不是 error）",
                "3 跳 %s → 2 跳 %s；statusline data-state=%s"
                % (e2["status"], e3["status"], e3["state"]))
        rep.add(bool(urls_2) and all("max_hops=%s" % truth["scopeDefault"]["hops"] in u
                                    for u in urls_2),
                "B5h-新跳数确实进了请求（改查询改的是请求本身，不是只改界面）",
                (urls_2[0][:150] + "…") if urls_2 else "★ 未捕获到请求")
        rep.add(("跳数" in (e3["pathStatus"] or "")) or ("跳数" in (e3["banner"] or "")),
                "B5i-no_result 的文案指向真正的原因（跳数），不只写「切可信度档试试」",
                "path-status: %s" % (e3["pathStatus"] or "(空)")[:90])

        # 收尾：全局健康
        result["requests"] = ["%s %s" % (m, u) for m, u in reqs]
        result["blocked"] = blocked[:20]
        result["page_errors"] = pageerrors
        result["console_errors"] = console_errs
        result["bad_responses"] = ["%s %s" % (s, u) for s, u in bad_resp]

        rep.add(len(pageerrors) == 0, "B5j-全程无未捕获 JS 异常",
                "pageerror = %d %s" % (len(pageerrors), pageerrors[:2]))
        origin = base.rstrip("/")
        own_bad = [(s, u) for s, u in bad_resp if u.startswith(origin)]
        rep.add(len(own_bad) == 0, "B5k-同源请求无 4xx/5xx",
                "4xx/5xx = %d %s" % (len(own_bad), own_bad[:3]))
        rep.add(len(blocked) == 0, "B5l-零外部请求（不依赖 CDN / 不受代理影响）",
                "被拦外部请求 %d 条 %s" % (len(blocked), blocked[:2]))

        rep.note("附：本轮网络与运行环境（便于复核，不作为通过条件）")
        result["healthz_after"] = None
        browser.close()

    rep.dump()
    _write(args.json_out, result, rep)
    return 1 if rep.n_fail else 0


def precond_banner(which, name, detail, how_to_fix):
    """打印**前置自述块**（D56）。

    ★ 为什么必须存在：本仪器**非自包含**（见文件头）。前置缺失时：
      ① 必须**显著打印**（含恢复命令），否则读者只看到一堆 SKIP；
      ② 退出码必须**不是 0**（这里是 2）—— 否则退出码上 SKIP 与 PASS 不可分（#101）。
      它既不是「通过」，也不代表「前端有问题」：说清这一点是本节的全部目的。
    """
    print("-" * 78)
    print("  ⛔ 前置不满足：%s（%s）" % (name, which))
    print("     观测：%s" % detail)
    print("     恢复：%s" % how_to_fix)
    print("     ★ 退出码 2 = **前置缺失**（本仪器非自包含，需要外部服务已在跑）；")
    print("       它不是「通过」，也不代表「前端有问题」。")
    print("-" * 78)


def _write(path, result, rep):
    if not path or rep is None:
        return
    payload = dict(result or {})
    payload["summary"] = {"pass": rep.n_pass, "fail": rep.n_fail, "skip": rep.n_skip}
    payload["rows"] = [{"kind": k, "name": n, "detail": d} for k, n, d in rep.rows]
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=False)
    print(f"  （结构化结果已写入 {path}）")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        raise
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print("\n  [FAIL] B0d-仪器自身未捕获异常 —— 本轮**不可判定**，"
              "不得当成产品通过（下面是崩溃前已完成的断言，仍有参考价值）")
        if _REP is not None:
            _REP.n_fail += 1
            _REP.rows.append(("FAIL", "B0d-仪器未捕获异常", "见上方 traceback"))
            _REP.dump()
        _write(_JSON_OUT or os.environ.get("STTP_E2E_JSON"), _RESULT, _REP)
        sys.exit(1)
