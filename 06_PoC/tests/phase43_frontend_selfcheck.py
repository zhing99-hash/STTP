#!/usr/bin/env python3
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

"""Phase 43 步骤 B（前端）自检 —— 静态结构 / 无障碍 / 模块图 / **跨层一致性** / Node 逻辑 / HTTP 层。

为什么要单独一套（而不是并进 `phase43_subgraph_selfcheck.py`）：
    后端自检断言的是"响应体"，前端自检断言的是"页面与它的契约是否对得上"。
    两者失败模式不同，混在一起会让"后端绿了"掩盖"前端引用了不存在的字段"。

★ 本自检**自身不引入第三方依赖**（不用 Playwright / jsdom）：
    可被断言的部分刻意做成了**纯逻辑模块**（`js/core.js`、`js/style.js`），
    `node` 直接 import 即可断言；其余部分用**静态结构 + 跨层比对**覆盖。

★ **静态面 ≠ 行为面**（2026-10-15 Phase 51 订正）：
    「文件都写对了」不代表「页面点得动」。本项目已被这条缝咬中三次
      Phase 47（`/api/stats` 缺 `status` ⇒ 页面回退 bundle ⇒ 拉 48.7 MB 卡死）、
      Phase 48（搜索词 `q` 被静默丢弃 ⇒ 26/26 请求全 400）、
      Phase 51（`validateParams` 与发送侧口径不一致 ⇒ 路径查询请求**发不出去**）。
    因此行为面已单独立仪器：**`bash sttp.sh taskflow`**（真浏览器端到端 B1–B5，
    需 viz 在跑 + 可选 Playwright；缺依赖记 SKIP 不计通过）。
    本脚本只负责静态面，**不得**把"静态全绿"读成"页面可用"。

退出码：0 = 无 FAIL；1 = 有 FAIL；2 = 前置不满足（缺文件 / 缺 node）。
"""

import contextlib
import io
import json
import os
import pathlib
import re
import struct
import subprocess
import sys
import threading
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
POC = os.path.dirname(HERE)
ROOT = os.path.dirname(POC)
JS_DIR = os.path.join(POC, "js")
HTML_PATH = os.path.join(POC, "graph_view.html")
NORMALIZED = os.path.join(POC, "etl", "normalized.json")

if POC not in sys.path:
    sys.path.insert(0, POC)
if os.path.join(ROOT, "03_知识层") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "03_知识层"))

# Emoji 图标检测（覆盖 Emoji 主区 / Dingbats / 杂项符号，**不含** ★ 这类装饰字符）
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2700-\u27BF\u2B00-\u2BFF\uFE0F\u20E3]")

# 页面必须存在的 DOM id（由 js/main.js 与 panels.js 引用）
REQUIRED_IDS = [
    "search-input", "search-results", "mode-badge", "ds-status",
    "btn-overview", "btn-export-png", "btn-export-svg", "btn-export-json",
    "left", "btn-toggle-panel", "panel-query", "legend",
    "canvas", "cy", "tooltip", "fallback",
    "substack-info", "btn-back", "btn-expand", "btn-fit", "btn-reset-view", "sel-layout", "expand-hint",
    "right", "detail",
    "bottom", "statusline", "banner",
    "path-src", "path-dst", "btn-pick-src", "btn-pick-dst", "btn-path-swap",
    "btn-path", "btn-path-clear", "path-status", "path-list",
]

# ★ 2026-10-15（Phase 49）改为**从磁盘派生**，不再手写白名单。
#   起因（实测）：新增 `js/task.js` 后只给 `main.js` 加了 import，忘了把它写进这份
#   手写列表 ⇒ H4 仍报「9/9 个模块均可 HTTP 取到」，而页面实际需要 **10** 个
#   —— 典型的**白名单永远漏**（#71）叠加**仪器自指**（#82：扫描范围 ⊂ 被测范围 ⇒ 假绿）。
#   现在：① 列表派生自 `js/*.js`（新增文件**自动**纳入，不再需要人记得改这里）；
#        ② 配一条**反向**断言 S15：磁盘上每个模块都必须在 `main.js` 的 import 闭包内
#          （防另一种漏法："文件加了、但没接进页面"）。两个方向都堵上。
MODULES = sorted(p.stem for p in pathlib.Path(JS_DIR).glob("*.js"))


class Rep:
    """极简报告器（与 phase43_subgraph_selfcheck.py 同风格）。"""

    def __init__(self):
        self.rows = []
        self.n_pass = 0
        self.n_fail = 0
        self.n_skip = 0

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

    def section(self, title):
        self.rows.append(("--", title, ""))

    def dump(self):
        for kind, name, detail in self.rows:
            if kind == "--":
                print(f"\n{name}")
            else:
                line = f"  [{kind}] {name}"
                if detail:
                    line += f"  —— {detail}"
                print(line)
        print(f"\n  phase43_frontend_selfcheck: PASS {self.n_pass} / FAIL {self.n_fail} / SKIP {self.n_skip}")


def read(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read()


def find_node():
    """定位可用的 node（managed 优先）；找不到返回 None（调用方须记 SKIP）。"""
    cands = []
    env = os.environ.get("STTP_NODE")
    if env:
        cands.append(env)
    base = os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries", "node", "versions")
    if os.path.isdir(base):
        for d in sorted(os.listdir(base), reverse=True):
            exe = os.path.join(base, d, "node.exe" if os.name == "nt" else "node")
            if os.path.isfile(exe):
                cands.append(exe)
    for c in cands:
        if os.path.isfile(c):
            return c
    from shutil import which
    return which("node")


# ---------------------------------------------------------------------------
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    json_out = None
    if "--json" in argv:
        i = argv.index("--json")
        json_out = argv[i + 1] if i + 1 < len(argv) else None

    rep = Rep()
    print("=" * 78)
    print("  Phase 43 · 步骤 B（前端「局部图谱查询浏览」）自检")
    print("=" * 78)
    print(f"  页面 : {os.path.relpath(HTML_PATH, ROOT)}")
    print(f"  模块 : {os.path.relpath(JS_DIR, ROOT)}/")
    print("=" * 78)

    if not os.path.isfile(HTML_PATH):
        print(f"  ⛔ 页面不存在：{HTML_PATH}")
        return 2
    html = read(HTML_PATH)
    js_src = {}
    for m in MODULES:
        p = os.path.join(JS_DIR, m + ".js")
        if os.path.isfile(p):
            js_src[m] = read(p)

    if len(js_src) != len(MODULES):
        missing = [m for m in MODULES if m not in js_src]
        print(f"  ⛔ 缺少前端模块：{missing}")
        return 2

    # ---------------------------------------------------------------- S 段
    rep.section("[S] 静态结构与无障碍（方案 §6.1.1 / §10 步骤 B-7）")
    rep.add('<script type="module" src="js/main.js">' in html,
            "S1 页面用**原生 ES Module**加载入口（零构建）",
            "js/main.js" if 'type="module"' in html else "未找到")
    rep.add("vendor/cytoscape.min.js" in html and "vendor/cytoscape-fcose.js" in html
            and "vendor/tex-svg.js" in html,
            "S2 渲染/布局/公式三个 vendor 依赖均本地优先加载", "")
    rep.add("cdn.jsdelivr.net" in html,
            "S2b CDN 仅作回退（离线优先 + 在线兜底）", "")

    missing_ids = [i for i in REQUIRED_IDS if f'id="{i}"' not in html]
    rep.add(not missing_ids, f"S3 必需 DOM id 齐备（{len(REQUIRED_IDS)} 个）",
            "缺: " + ",".join(missing_ids) if missing_ids else f"{len(REQUIRED_IDS)}/{len(REQUIRED_IDS)}")
    rep.add("SPDX-License-Identifier: Apache-2.0" in html, "S4 页面含 SPDX 许可头", "")

    emoji_hits = []
    for name, txt in [("graph_view.html", html)] + [(f"js/{k}.js", v) for k, v in js_src.items()]:
        for m in EMOJI_RE.finditer(txt):
            emoji_hits.append(f"{name}:U+{ord(m.group(0)):04X}")
    rep.add(not emoji_hits, "S5 **不用 emoji 当图标**（内联 SVG）",
            "、".join(emoji_hits[:6]) if emoji_hits else "0 处")

    inline_handlers = re.findall(r'\son(?:click|mouseover|mousedown|mousemove)\s*=', html, re.I)
    rep.add(not inline_handlers, "S6 无内联 onclick 等事件属性（用 addEventListener，可键盘操作）",
            f"{len(inline_handlers)} 处" if inline_handlers else "0 处")

    # 断点：`max-width: W-1px`（区间上界）或 `min-width: Wpx` 都算覆盖了断点 W
    bp = [w for w in (375, 768, 1024, 1440)
          if f"max-width: {w - 1}px" in html or f"max-width: {w}px" in html
          or f"min-width: {w}px" in html]
    rep.add(len(bp) == 4, "S7 响应式断点 375/768/1024/1440 齐备（含区间上界写法）",
            "/".join(map(str, bp)) if bp else "无")
    rep.add(html.count("cursor: pointer") >= 5, "S8 可点击元素有 cursor:pointer",
            f"{html.count('cursor: pointer')} 处")
    rep.add(":focus-visible" in html, "S9 键盘焦点可见（:focus-visible）", "")
    rep.add("prefers-reduced-motion" in html, "S10 尊重 prefers-reduced-motion", "")
    rep.add('aria-label' in html and 'aria-expanded' in html, "S11 关键控件有 aria 标注", "")

    # 五态 CSS 类与变量
    st_classes = [f".stchip.st-{s}" for s in ("ok", "no-result", "truncated", "timeout", "error")]
    miss_st = [c for c in st_classes if c not in html]
    rep.add(not miss_st, "S12 五态 CSS 类齐备（供 V8 的 DOM 断言）",
            "缺: " + ",".join(miss_st) if miss_st else "5/5")
    st_vars = [f"--st-{s}" for s in ("ok", "no-result", "truncated", "timeout", "error")]
    miss_var = [v for v in st_vars if f"{v}:" not in html]
    rep.add(not miss_var, "S13 五态 CSS 变量齐备", "缺: " + ",".join(miss_var) if miss_var else "5/5")
    rep.add("data-state" in html, "S14 状态行根元素带 data-state（供 DOM 断言）", "")

    # S15 ★ 反向：磁盘上每个模块都必须**被引用**（"文件加了但没接进页面"）
    #   与 H4 的正向（页面能取到）配成一对：正向查"缺文件"，反向查"死文件"。
    #   闭包从 `main.js` 出发，静态 `from "./x.js"` 与动态 `import("./x.js")` 都算。
    _dep_re = re.compile(r'''["']\./([A-Za-z0-9_\-]+)\.js["']''')
    _seen, _stack = set(), ["main"]
    while _stack:
        _n = _stack.pop()
        if _n in _seen:
            continue
        _seen.add(_n)
        _stack.extend(_dep_re.findall(js_src.get(_n, "")))
    _orphans = [m for m in MODULES if m not in _seen]
    rep.add(not _orphans,
            f"S15 全部 {len(MODULES)} 个模块都在 main.js 的 import 闭包内（无死文件、无漏接）",
            "未被引用：" + "、".join(_orphans) if _orphans
            else f"{len(_seen)}/{len(MODULES)} 可达")

    # ---------------------------------------------------------------- M 段
    rep.section("[M] 模块图完整性（防 404 静默失效）")
    import_re = re.compile(r'^\s*import\s+(?:[\s\S]*?)\s+from\s+["\']([^"\']+)["\']', re.M)
    # 注意 js 里只能用 import ... from "./x.js"（ESM 相对路径）；不用 from "x" 裸名
    for name, txt in js_src.items():
        refs = import_re.findall(txt)
        bad = [r for r in refs if not r.startswith("./")]
        rep.add(not bad, f"M1 js/{name}.js 的 import 均为显式相对路径", ",".join(bad) if bad else f"{len(refs)} 条")

    unresolved = []
    for name, txt in js_src.items():
        for r in import_re.findall(txt):
            if not r.startswith("./"):
                continue
            target = os.path.normpath(os.path.join(JS_DIR, r))
            if not os.path.isfile(target):
                unresolved.append(f"js/{name}.js → {r}")
    rep.add(not unresolved, "M2 所有 import 都指向**存在**的文件",
            "、".join(unresolved) if unresolved else "0 个悬空")

    no_spdx = [f"js/{k}.js" for k, v in js_src.items() if "SPDX-License-Identifier: Apache-2.0" not in v]
    rep.add(not no_spdx, "M3 全部前端模块含 SPDX 许可头", "、".join(no_spdx) if no_spdx else f"{len(js_src)}/{len(js_src)}")

    entry = re.search(r'<script\s+type="module"\s+src="([^"]+)"', html)
    rep.add(bool(entry) and os.path.isfile(os.path.join(POC, entry.group(1))),
            "M4 页面入口脚本存在", entry.group(1) if entry else "未找到")

    # ---------------------------------------------------------------- X 段
    rep.section("[X] 跨层一致性（前端常量 ↔ 后端契约，漂移即变红）")
    style_src = js_src["style"]
    core_src = js_src["core"]

    def js_hex_map(src, const_name):
        m = re.search(r"export\s+const\s+" + const_name + r"\s*=\s*Object\.freeze\(\{([\s\S]*?)\n\}\)", src)
        if not m:
            return None
        body = m.group(1)
        out = {}
        for km in re.finditer(r'^\s*(?:"([^"]+)"|([A-Za-z_][\w]*))\s*:\s*\{\s*color:\s*"(#[0-9a-fA-F]{6})"', body, re.M):
            key = km.group(1) or km.group(2)
            if km.group(3):
                out[key] = km.group(3).lower()
        return out

    # X1 style.js 的 EDGE_TYPE_COLOR 键集合 == 规范图实测 type 集合
    m = re.search(r"export\s+const\s+EDGE_TYPE_COLOR\s*=\s*Object\.freeze\(\{([\s\S]*?)\n\}\)", style_src)
    if not m:
        rep.add(False, "X1 解析 style.js 的 EDGE_TYPE_COLOR", "未找到常量")
    else:
        fe_types = set(re.findall(r"^\s*([a-z_][\w]*)\s*:", m.group(1), re.M))
        if not os.path.isfile(NORMALIZED):
            rep.skip("X1 前端边类型配色 == 规范图实测 type 集合",
                     "缺 06_PoC/etl/normalized.json（**不入库**，干净检出下必然 SKIP ⇒ 见报告 §6-D13a）")
        else:
            with io.open(NORMALIZED, "r", encoding="utf-8") as f:
                norm = json.load(f)
            be_types = set(e.get("type") for e in (norm.get("edges") or []) if e.get("type"))
            diff_a = sorted(be_types - fe_types)
            diff_b = sorted(fe_types - be_types)
            rep.add(not diff_a and not diff_b,
                    f"X1 前端边类型配色 == 规范图实测 type 集合（{len(be_types)} 种）",
                    f"前端缺 {diff_a} / 前端多 {diff_b}" if (diff_a or diff_b)
                    else f"{len(fe_types)} == {len(be_types)}（旧页面此处已漂移，本轮校正）")

    # X2 STATUS_STYLE 颜色 == 页面 CSS 变量默认值
    m = re.search(r"export\s+const\s+STATUS_STYLE\s*=\s*Object\.freeze\(\{([\s\S]*?)\n\}\);", core_src)
    css_ok, mism = True, []
    if not m:
        css_ok = False
        mism.append("未解析到 STATUS_STYLE")
    else:
        body = m.group(1)
        for sm in re.finditer(r'^\s{2}([a-z_]+):\s*\{([\s\S]*?)^\s{2}\},', body, re.M):
            key = sm.group(1)
            cm = re.search(r'color:\s*"(#[0-9a-fA-F]{6})"', sm.group(2))
            if not cm:
                continue
            varname = "--st-" + key.replace("_", "-")
            vm = re.search(re.escape(varname) + r":\s*(#[0-9a-fA-F]{6})", html)
            if not vm:
                css_ok = False
                mism.append(f"{varname} 缺失")
            elif vm.group(1).lower() != cm.group(1).lower():
                css_ok = False
                mism.append(f"{varname} CSS={vm.group(1)} ≠ JS={cm.group(1)}")
    rep.add(css_ok, "X2 五态色：页面 CSS 默认值 == core.js（单一来源）",
            "；".join(mism) if mism else "5/5 一致")

    # X3 WHY_SHOWN_LABEL 键集合 == queries.WHY_SHOWN_VALUES
    try:
        import queries as Q  # type: ignore
    except Exception as e:  # noqa: BLE001
        rep.skip("X3–X6 后端常量比对", f"import queries 失败：{e}")
        Q = None
    if Q is not None:
        m = re.search(r"export\s+const\s+WHY_SHOWN_LABEL\s*=\s*Object\.freeze\(\{([\s\S]*?)\n\}\)", core_src)
        fe_why = set(re.findall(r"^\s*([a-z_]+)\s*:", m.group(1), re.M)) if m else set()
        be_why = set(getattr(Q, "WHY_SHOWN_VALUES", ()) or ())
        rep.add(fe_why == be_why, "X3 why_shown 枚举：前端 == queries.WHY_SHOWN_VALUES",
                f"前端 {sorted(fe_why)} ≠ 后端 {sorted(be_why)}" if fe_why != be_why else f"{len(fe_why)} 个一致")

        # X4 预算：必须**分块**解析（BUDGET 与 HARD 都有 nodes/edges/hops…，
        #    直接 re.search 会永远命中先出现的 BUDGET —— 这正是本自检第一版踩的坑）
        def js_block(const_name):
            mm = re.search(r"export\s+const\s+" + const_name + r"\s*=\s*Object\.freeze\(\{([\s\S]*?)\}\);", core_src)
            return mm.group(1) if mm else ""

        def js_num(block, name):
            mm = re.search(re.escape(name) + r":\s*([0-9]+)", block)
            return int(mm.group(1)) if mm else None

        b_budget, b_hard = js_block("BUDGET"), js_block("HARD")
        rep.add(bool(b_budget) and bool(b_hard), "X4 解析到前端 BUDGET / HARD 两个块",
                f"BUDGET {len(b_budget)} 字符 / HARD {len(b_hard)} 字符")

        # ★ 2026-10-14（Phase 49）改造：原来 5 项"全等"的朴素断言会红，原因**不是**回归，
        #   而是我们**有意**把前端 `paths` 从 20 收到 5（任务卡一次只展示 3–5 条路径）。
        #   于是这里改成：**对称 4 项**（nodes/edges/hops/deadline_ms）+ **非对称 1 项**
        #   （`BUDGET.paths ≤ DEFAULT_MAX_PATHS`，前端是库默认的**收窄**，不得放宽）。
        #   这正是铁律 #15「改源头后必须改下游」：改的是断言，不是把红刷绿。
        pairs = [("nodes", "DEFAULT_NODES"), ("edges", "DEFAULT_EDGES"),
                 ("hops", "DEFAULT_HOPS"), ("deadline_ms", "DEFAULT_DEADLINE_MS")]
        bad = []
        for k, attr in pairs:
            fv, bv = js_num(b_budget, k), getattr(Q, attr, None)
            if fv != bv:
                bad.append(f"{k}: 前端 {fv} ≠ {bv}")
        rep.add(not bad, "X4a 默认预算：前端 BUDGET == queries.DEFAULT_*（nodes/edges/hops/deadline 4 项对称）",
                "；".join(bad) if bad else "4/4")

        # X4a-path：`paths` 是**唯一**允许非对称的项（前端只收窄、绝不放宽）
        fp, bp = js_num(b_budget, "paths"), getattr(Q, "DEFAULT_MAX_PATHS", None)
        path_rel_ok = (fp is not None and bp is not None and 0 < fp <= bp)
        rep.add(path_rel_ok, "X4a-path 前端 BUDGET.paths ≤ 库级 DEFAULT_MAX_PATHS（只收窄不放宽）",
                f"前端 {fp} · 库 {bp}" + ("" if path_rel_ok else " ⇒ 前端值必须为正且不得超过库默认"))

        hard = getattr(Q, "HARD", {}) or {}
        bad = []
        for k in ("nodes", "edges", "hops", "deadline_ms", "paths"):
            fv, bv = js_num(b_hard, k), hard.get(k)
            if fv != bv:
                bad.append(f"{k}: 前端 {fv} ≠ {bv}")
        rep.add(not bad, "X4b 硬上限：前端 HARD == queries.HARD", "；".join(bad) if bad else "5/5")

        # X4c ★ 第三处重复（Phase 49 消掉）：`viz_server._api_subgraph` 的**默认值**必须
        #   引用 `q.DEFAULT_*`，不得再写死字面量。断言方式是**源码层**（不是数值比对）：
        #   数值比对只能证明"这次恰好相等"，源码断言才能防住"下次各写一套"。
        #   ★ 作用域**必须**收窄到 `_api_subgraph` 处理体：第一版对全文件做正则，把
        #     legacy `/api/path` 的 `"max_paths","20"` / `"max_hops","5"` /
        #     `"deadline_ms","3000"` 一并报成"硬编码" —— 那是**误报**（legacy 端点
        #     **有意**冻结自己的默认值，改动会静默改变外部脚本行为）。收窄后又暴露
        #     真问题：这组分叉原先**没人声明**。⇒ 见契约 `legacy_defaults` + X16f。
        try:
            import viz_server as V  # type: ignore
            vs_src_x4 = read(os.path.join(POC, "viz_server.py"))
            m_sub = re.search(r"\n[ \t]*def _api_subgraph\(self, qs\)", vs_src_x4)
            body_sub = ""
            if m_sub:
                nxt = re.search(r"\n[ \t]*def ", vs_src_x4[m_sub.end():])
                body_sub = vs_src_x4[m_sub.end(): m_sub.end() + nxt.start()] if nxt else ""
            need_refs = ["str(q.DEFAULT_NODES)", "str(q.DEFAULT_EDGES)", "str(q.DEFAULT_HOPS)",
                         "str(q.DEFAULT_DEADLINE_MS)", "str(q.DEFAULT_MAX_PATHS)"]
            miss_refs = [r for r in need_refs if r not in body_sub]
            # 负向：本处理体内不得出现**硬编码数字**当作这些参数的默认
            hardcoded = re.findall(r'"max_(?:nodes|edges|hops|paths)"\s*,\s*"(\d+)"', body_sub)
            hardcoded += re.findall(r'"deadline_ms"\s*,\s*"(\d+)"', body_sub)
            x4c_ok = bool(body_sub) and (not miss_refs) and (not hardcoded)
            rep.add(x4c_ok, "X4c `_api_subgraph` 默认预算引用 q.DEFAULT_*（作用域=处理体，不硬编码）",
                    ("未解析到处理体" if not body_sub else "")
                    + ("缺引用：" + "、".join(miss_refs) if miss_refs else "")
                    + ("；发现硬编码默认：" + "、".join(hardcoded) if hardcoded else "")
                    or "5/5 引用 · 0 字面量")
        except Exception as e:  # noqa: BLE001
            rep.skip("X4c viz_server 默认预算同源", f"import viz_server 失败：{e}")

        se_block = js_block("SESSION_LIMIT")
        fsn, fse = js_num(se_block, "nodes"), js_num(se_block, "edges")
        try:
            import viz_server as V  # type: ignore
            qn = getattr(Q, "DEFAULT_SESSION_NODES", None)
            qe = getattr(Q, "DEFAULT_SESSION_EDGES", None)
            ok = (V.SESSION_NODES == qn and V.SESSION_EDGES == qe
                  and fsn == qn and fse == qe)
            rep.add(ok, "X5 会话预算三处一致（viz_server == queries == 前端 SESSION_LIMIT）",
                    f"server {V.SESSION_NODES}/{V.SESSION_EDGES} · queries {qn}/{qe} · 前端 {fsn}/{fse}")
        except Exception as e:  # noqa: BLE001
            rep.skip("X5 会话预算一致", f"import viz_server 失败：{e}")

        # X6 ERROR_CODE 文案表 / 严重度表 **从契约派生**比对（唯一真值源）
        #   ★ 2026-10-14 改造原因：原实现是硬编码 needle 列表 + 只用后端源码正则
        #     "捕已有"，**捕不出"应该有而没写"** —— 契约新增码时断言仍绿（#71）。
        m = re.search(r"export\s+const\s+ERROR_CODE_TEXT\s*=\s*Object\.freeze\(\{([\s\S]*?)\n\}\)", core_src)
        # ★ 解析**不依赖换行**：`KEY: "…"` 可以一行多对（首版只认行首键 ⇒ 一行多对时
        #   静默漏项，把"漏项"误报成"未归类" —— 测量器比被测对象更脆，正是 #76 家族）
        fe_codes = set(re.findall(r"([A-Z_][A-Z0-9_]*)\s*:\s*[\"']", m.group(1))) if m else set()
        try:
            import viz_server as V  # type: ignore
            vs_src = read(os.path.join(POC, "viz_server.py"))
        except Exception:  # noqa: BLE001
            vs_src = ""
        q_src = read(os.path.join(ROOT, "03_知识层", "queries.py"))
        try:
            with io.open(os.path.join(ROOT, "contracts", "error.schema.json"), "r",
                         encoding="utf-8") as f:
                contract_codes = set(((json.load(f).get("properties") or {})
                                      .get("error_code") or {}).get("enum") or [])
        except Exception:  # noqa: BLE001
            contract_codes = set()
        be_codes = set(re.findall(r'error_code["\']?\s*[:=]\s*["\']([A-Z_]+)["\']', vs_src + q_src))
        be_codes |= set(re.findall(r'"([A-Z_]{6,})"', vs_src))
        be_codes = {c for c in be_codes if c.endswith(("_REQUEST", "_MISMATCH", "_STALE", "_REACHED",
                                                       "_TIMEOUT", "_ERROR", "_AMBIGUOUS", "_CONDITION"))}
        miss_contract = sorted(contract_codes - fe_codes)
        rep.add(bool(contract_codes) and not miss_contract,
                "X6 error_code 文案表覆盖**契约 enum 全景**（派生比对，不硬编码）",
                (f"契约 {len(contract_codes)} 个；缺 " + ",".join(miss_contract)) if miss_contract
                else f"契约 {len(contract_codes)} 个全覆盖")
        miss_be = sorted(be_codes - fe_codes)
        rep.add(bool(be_codes) and not miss_be,
                "X6a 文案表同时覆盖后端**实际产生**的全部错误码",
                "缺: " + ",".join(miss_be) if miss_be else f"{len(be_codes)} 个全覆盖")
        # X6b 传输层码**只能由前端合成** —— 把契约 description 里的声明变成可执行断言（#64）
        TRANSPORT_CODES = {"BACKEND_UNREACHABLE"}
        leak = sorted(c for c in (contract_codes & TRANSPORT_CODES)
                      if re.search(r'["\']' + c + r'["\']', vs_src + q_src))
        rep.add(bool(contract_codes & TRANSPORT_CODES) and not leak,
                "X6b 传输层码 BACKEND_UNREACHABLE **只由前端合成**（后端源码不得出现）",
                ("后端出现: " + ",".join(leak)) if leak else "后端 0 处引用")
        # X6c 严重度必须**显式归类**（新增码不得静默默认成某一档 —— #84 的取向）
        sm = re.search(r"export\s+const\s+ERROR_CODE_SEVERITY\s*=\s*"
                       r"Object\.freeze\(\{([\s\S]*?)\n\}\)", core_src)
        sev_codes = set(re.findall(r"([A-Z_][A-Z0-9_]*)\s*:\s*[\"']", sm.group(1))) if sm else set()
        miss_sev = sorted(contract_codes - sev_codes)
        rep.add(bool(contract_codes) and not miss_sev,
                "X6c ERROR_CODE_SEVERITY 对契约每个码显式归类（防静默默认）",
                "未归类: " + ",".join(miss_sev) if miss_sev else f"{len(sev_codes)} 个已归类")

        # X5b 最短查询长度三处同源（queries.MIN_QUERY_LEN == 前端 MIN_QUERY_LEN）
        #   ★ 为什么必须断言：前端若把阈值写小（例如 1），"第一次按键"就会发出
        #     一次**明知会被 400 拒绝**的请求，并把 400 显示成"没有匹配" —— 与
        #     Phase 48 修的那类"错误被伪装成正常"同族；若写大（例如 3）则静默
        #     屏蔽掉合法的 2 字查询（如 `co2`）。两侧必须同源。
        mm = re.search(r"export\s+const\s+MIN_QUERY_LEN\s*=\s*([0-9]+)", core_src)
        fe_min = int(mm.group(1)) if mm else None
        be_min = getattr(Q, "MIN_QUERY_LEN", None) if Q is not None else None
        rep.add(mm is not None and be_min is not None and fe_min == be_min and fe_min >= 2,
                "X5b 最短查询长度同源（前端 MIN_QUERY_LEN == queries.MIN_QUERY_LEN ≥ 2）",
                f"前端 {fe_min} · 后端 {be_min}")

        # X7 验证档徽标 == queries._RANK：**键集合 + rank 数值**都必须相等
        m = re.search(r"export\s+const\s+VERIFICATION_BADGE\s*=\s*Object\.freeze\(\{([\s\S]*?)\n\}\)", style_src)
        fe_rank, fe_rank_vals = set(), {}
        if m:
            for km in re.finditer(r"^\s*([a-z_]+)\s*:\s*\{[^}]*?rank:\s*(-?[0-9]+)", m.group(1), re.M):
                fe_rank.add(km.group(1))
                fe_rank_vals[km.group(1)] = int(km.group(2))
        be_rank = dict(getattr(Q, "_RANK", {}) or {})
        same_keys = fe_rank == set(be_rank)
        bad_rank = [f"{k}: 前端 {fe_rank_vals.get(k)} ≠ {be_rank[k]}"
                    for k in sorted(be_rank) if fe_rank_vals.get(k) != be_rank[k]]
        rep.add(same_keys and not bad_rank,
                f"X7 验证档徽标 == queries._RANK（{len(be_rank)} 档，键集合 + rank 数值）",
                (f"键差 {sorted(fe_rank ^ set(be_rank))}；" if not same_keys else "")
                + ("；".join(bad_rank) if bad_rank else f"{len(be_rank)} 档逐值一致"))

        # X8 证据字节忠实度四分类 == queries.FIDELITY_CLASSES
        #    ★ D1 修复引入的新跨层契约：前端 FIDELITY_CLASSES **逐值 + 顺序**都要相等，
        #      且徽标键集合必须齐全（四档无多无少）。漏一档 ⇒ 后端能算出而前端渲染成
        #      「未判定」，恰好会把 `unverifiable_not_in_repo`（47 055 条**并未变更**的边）
        #      伪装成不可判定 —— 正是本次要防的误报。
        #    ★ 另断言 `FIDELITY_UNKNOWN` 不占用任何已定义档位的键，避免「未判定」被当成
        #      真实分类参与加总（那会让四分类合计 ≠ 边总数）。
        fm = re.search(r"export\s+const\s+FIDELITY_CLASSES\s*=\s*Object\.freeze\(\[([\s\S]*?)\]\)", style_src)
        fe_fid = re.findall(r'"([a-z_]+)"', fm.group(1)) if fm else []
        be_fid = list(getattr(Q, "FIDELITY_CLASSES", ()) or ())
        rep.add(bool(fm) and fe_fid == be_fid,
                f"X8a 忠实度四分类：前端 == queries.FIDELITY_CLASSES（{len(be_fid)} 档，逐值 + 顺序）",
                f"前端 {fe_fid} ≠ 后端 {be_fid}" if fe_fid != be_fid else "；".join(be_fid))
        if fm:
            bm = re.search(r"export\s+const\s+FIDELITY_BADGE\s*=\s*Object\.freeze\(\{([\s\S]*?)\n\}\)", style_src)
            badge_keys = set(re.findall(r"^\s*([a-z_]+)\s*:\s*\{", bm.group(1), re.M)) if bm else set()
            miss_badge = sorted(set(be_fid) - badge_keys)
            extra_badge = sorted(badge_keys - set(be_fid))
            # 每档必须给出 color/label/short/note 四项，缺 note 会让「为何不是已变更」无处解释
            thin = []
            if bm:
                for k in sorted(badge_keys):
                    kb = re.search(r"^\s*" + re.escape(k) + r"\s*:\s*\{([\s\S]*?)^\s{2}\},", bm.group(1),
                                   re.M)
                    seg = kb.group(1) if kb else ""
                    lack = [f for f in ("color", "label", "short", "note") if not re.search(f + r"\s*:", seg)]
                    if lack:
                        thin.append(f"{k} 缺 {lack}")
            um = re.search(r"export\s+const\s+FIDELITY_UNKNOWN\s*=\s*\{([\s\S]*?)\n\};", style_src)
            unknown_ok = bool(um) and not re.search(r"^\s*([a-z_]+)\s*:\s*\{", um.group(1), re.M)
            rep.add(not miss_badge and not extra_badge and not thin,
                    f"X8b 忠实度徽标键集合 == 四分类（{len(be_fid)} 档齐全，各带 color/label/short/note）",
                    (f"缺徽标 {miss_badge}；多徽标 {extra_badge}；" if (miss_badge or extra_badge) else "")
                    + ("；".join(thin) if thin else f"{len(badge_keys)} 档字段齐全"))
            rep.add(unknown_ok, "X8c FIDELITY_UNKNOWN 不占用任何已定义档位键（不参与加总）",
                    "FIDELITY_UNKNOWN 是独立兜底对象" if unknown_ok else "未解析到 FIDELITY_UNKNOWN 或含分类键")
            # stale 口径不变：STALE_FIDELITIES ⊂ FIDELITY_CLASSES
            stale_fid = tuple(getattr(Q, "STALE_FIDELITIES", ()) or ())
            sub_ok = bool(stale_fid) and set(stale_fid) <= set(be_fid)
            rep.add(sub_ok, "X8d STALE_FIDELITIES ⊆ FIDELITY_CLASSES（stale 仍是四分类的子集）",
                    f"{list(stale_fid)} ⊆ {len(be_fid)} 档" if sub_ok else f"{list(stale_fid)} ⊄ {be_fid}")

    # X9 契约 ↔ 前端：`evidence[].src_hash` 可为 null，前端必须**显式**说明（不静默）
    #   实测背景：9 个无 locator 的模型/规则源（GNN/schema-axiom/LLM-review/heuristic）
    #   共 126 条证据 / 121 条边，其 `src_hash` 为 `null`。
    #   ★ 塌陷风险：若契约把 `src_hash` 收紧回 `string`，前端那枚徽标就成了死代码
    #     （或反过来：契约放宽而前端没做提示 ⇒ 「不可复算」被静默吞掉）。故两侧同断言。
    sc_path = os.path.join(ROOT, "contracts", "subgraph.schema.json")
    try:
        with io.open(sc_path, "r", encoding="utf-8") as f:
            sc = json.load(f)
        ev_def = ((sc.get("$defs") or {}).get("evidence") or {})
        sh = (ev_def.get("properties") or {}).get("src_hash") or {}
        t = sh.get("type")
        types_ = t if isinstance(t, list) else [t]
        nullable = "null" in types_
        required_has = "src_hash" in (ev_def.get("required") or [])
        ev_src = js_src.get("evidence", "")
        fe_has_pill = "无 locator · 不可复算" in ev_src
        rep.add(nullable and required_has and fe_has_pill,
                "X9 契约 src_hash 可空 ⇔ 前端显式「无 locator · 不可复算」徽标（两侧同步）",
                f"schema.type={t}｜required={'src_hash' in (ev_def.get('required') or [])}"
                f"｜前端徽标={'有' if fe_has_pill else '缺'}")
    except Exception as e:  # noqa: BLE001
        rep.skip("X9 契约 src_hash 可空 ⇔ 前端显式徽标", f"读取契约失败：{e}")

    # X10–X14 A04 验证依赖状态跨层一致（Phase 45）
    #   为什么必须逐值断言：前端要按状态给徽标 / 决定哪些进图例说明。
    #   若后端加了第 5 个状态而前端徽标表没跟上，那条边就会落到「未判定」兜底 ——
    #   **看起来正常**，实则把新状态静默吞掉（正是 #38「PASS≠无红」的家族）。
    if Q is None:
        rep.skip("X10–X14 A04 状态跨层一致", "import queries 失败（见 X3）")
    else:
        # style_src 已在 X8 段取好（`js_src["style"]`），此处复用，避免两处各取一份
        m10 = re.search(r"export\s+const\s+EVIDENCE_STATUSES\s*=\s*"
                        r"Object\.freeze\(\[([\s\S]*?)\]\)", style_src)
        fe_stat = re.findall(r'"([a-z_]+)"', m10.group(1)) if m10 else []
        be_stat = list(getattr(Q, "EVIDENCE_STATUSES", ()) or ())
        rep.add(bool(fe_stat) and fe_stat == be_stat,
                f"X10 A04 五态：前端 EVIDENCE_STATUSES == queries.EVIDENCE_STATUSES"
                f"（{len(be_stat)} 态，逐值 + 顺序）",
                f"前端={fe_stat} / 后端={be_stat}")
        be_ses = list(getattr(Q, "STALE_EVIDENCE_STATUSES", ()) or ())
        m11 = re.search(r"export\s+const\s+STALE_EVIDENCE_STATUSES\s*=\s*"
                        r"Object\.freeze\(\[([\s\S]*?)\]\)", style_src)
        fe_ses = re.findall(r'"([a-z_]+)"', m11.group(1)) if m11 else []
        rep.add(bool(fe_ses) and fe_ses == be_ses,
                "X11 排除集：前端 STALE_EVIDENCE_STATUSES == 后端（needs_review 不在其中）",
                f"前端={fe_ses} / 后端={be_ses}")
        # X12 徽标表：键集合 == 五态，且每档带 color/label/short/note
        bm = re.search(r"export\s+const\s+EVIDENCE_STATUS_BADGE\s*=\s*Object\.freeze\(\{([\s\S]*?)\n\}\)",
                       style_src)
        badge_keys, thin = [], []
        if bm:
            blk = bm.group(1)
            badge_keys = re.findall(r"^\s{2}([a-z_]+)\s*:\s*\{", blk, re.M)
            for k in badge_keys:
                mm = re.search(r"^\s{2}%s\s*:\s*\{([\s\S]*?)^\s{2}\}" % re.escape(k), blk, re.M)
                body = mm.group(1) if mm else ""
                if ("color" not in body
                        or not all(x in body for x in ("label", "short", "note"))):
                    thin.append(k)
        rep.add(badge_keys == be_stat and not thin,
                f"X12 A04 徽标键集合 == 五态（{len(be_stat)} 档齐全，各带 color/label/short/note）",
                f"键={badge_keys}｜缺字段={thin}" if (badge_keys != be_stat or thin)
                else f"{len(badge_keys)} 档字段齐全")
        # X13 契约 enum == 五态 + null（既不缺也不能多）
        try:
            with io.open(sc_path, "r", encoding="utf-8") as f:
                sc2 = json.load(f)
            enum_es = (((sc2.get("$defs") or {}).get("evidence_status") or {})
                       .get("enum") or [])
            want_enum = set(be_stat) | {None}
            rep.add(set(enum_es) == want_enum and len(enum_es) == len(want_enum),
                    "X13 契约 $defs.evidence_status.enum == 五态 + null（不漏不多）",
                    f"contract={enum_es} / queries={be_stat}+null")
        except Exception as e:  # noqa: BLE001
            rep.skip("X13 契约 evidence_status enum", f"读取契约失败：{e}")
        # X14 ★ 缺记录必须有**独立**兜底徽标，且 evidence.js 显式写出「无 A04 记录」
        ev2 = js_src.get("evidence", "")
        absent_ok = ("EVIDENCE_STATUS_ABSENT" in style_src and "无 A04 记录" in style_src
                     and "无 A04 记录" in ev2)
        rep.add(absent_ok,
                "X14 缺记录独立兜底：EVIDENCE_STATUS_ABSENT + 「无 A04 记录」文案在 style/evidence 两侧",
                "已覆盖" if absent_ok else "缺：EVIDENCE_STATUS_ABSENT 或文案")
        # X15 A04 等级表 == queries.EVIDENCE_STATUS_RANK（数值也要相等：聚合取最坏靠它）
        rk = re.search(r"export\s+const\s+EVIDENCE_STATUS_RANK\s*=\s*Object\.freeze\(\{([\s\S]*?)\}\)",
                       style_src)
        fe_rank = {}
        if rk:
            for k, v in re.findall(r"([a-z_]+)\s*:\s*(\d+)", rk.group(1)):
                fe_rank[k] = int(v)
        be_rank = dict(getattr(Q, "EVIDENCE_STATUS_RANK", {}) or {})
        rep.add(bool(fe_rank) and fe_rank == be_rank,
                "X15 A04 严重度表：前端 EVIDENCE_STATUS_RANK == queries（聚合取最坏的口径一致）",
                f"前端={fe_rank} / 后端={be_rank}")

    # ------------------------------------------------- X16 端点契约双向求差（D31）
    # ★ 与 Node 侧 F37 的分工：F37 求差「契约 ↔ 前端序列化」；X16 求差「契约 ↔ **后端源码**」。
    #   两者缺一不可 —— 只查前端，会把"契约本身写错（登记了后端不读的参数）"放过去；
    #   只查后端，会把"前端改名"放过去。这正是 P48/P49 连续命中 4 例（q / hops /
    #   subject / exclude_inferred）的根因：**参数名漂移不报错，只静默丢失**。
    rep.section("[X16] 端点契约 ↔ 后端源码双向求差（`contracts/endpoints.schema.json`）")
    try:
        ep_contract = json.loads(read(os.path.join(ROOT, "contracts", "endpoints.schema.json")))
        vsx = read(os.path.join(POC, "viz_server.py"))
    except Exception as e:  # noqa: BLE001
        rep.skip("X16 端点契约双向求差", f"读取契约/后端源码失败：{e}")
        ep_contract, vsx = None, None

    if ep_contract:
        eps = ep_contract.get("endpoints", {})

        def _handler_body(src, name):
            """按**缩进**切出 `def name(self, qs)` 的函数体（下一个同缩进 def 之前）。"""
            m = re.search(r"\n([ \t]*)def " + re.escape(name) + r"\(self, qs\)", src)
            if not m:
                return None
            start, indent = m.end(), m.group(1)
            nxt = re.search(r"\n" + re.escape(indent) + r"def ", src[start:])
            return src[start: start + nxt.start()] if nxt else src[start:]

        def _reads(body):
            """后端**真实读取**的参数名：`self._p(qs,"X")` + `qs.get("X")`。"""
            if body is None:
                return None
            got = set(re.findall(r'self\._p\(qs,\s*"([a-z_0-9]+)"', body))
            got |= set(re.findall(r'qs\.get\(\s*"([a-z_0-9]+)"', body))
            return got

        # 端点 → 处理函数（内联在 do_GET 的两个端点无参数，跳过）
        HANDLER = {ep: e.get("handler", "") for ep, e in eps.items()
                   if str(e.get("handler", "")).startswith("_api_")}
        declared_by_ep, reads_by_ep, body_by_ep = {}, {}, {}
        for ep, hn in HANDLER.items():
            declared_by_ep[ep] = set(eps[ep].get("params", {}) or {})
            body_by_ep[ep] = _handler_body(vsx, hn)
            reads_by_ep[ep] = _reads(body_by_ep[ep])

        rep.add(bool(HANDLER) and all(b is not None for b in body_by_ep.values()),
                "X16a 解析到端点契约与全部 `_api_*` 处理函数体（求差前提）",
                f"{len(HANDLER)} 个端点"
                + ("；解析失败：" + "、".join(k for k, v in body_by_ep.items() if v is None)
                   if any(v is None for v in body_by_ep.values()) else ""))

        # ---- 方向 A：契约 params ⊇ 后端**实读**集合（后端读了却没登记的 ⇒ 契约漏项）
        a_gap = {ep: sorted(reads_by_ep[ep] - declared_by_ep[ep]) for ep in HANDLER}
        a_gap = {k: v for k, v in a_gap.items() if v}
        rep.add(not a_gap, "X16b 【方向A】契约 params ⊇ 后端实读参数（无漏登记）",
                "；".join(f"{k} 缺 {v}" for k, v in a_gap.items()) if a_gap
                else f"{len(HANDLER)} 端点无漏登记")

        # ★ 构造性反证：从契约副本里挖掉 `/api/subgraph` 的 `hops` ⇒ 方向 A **必须**报出它
        sub_ep = "/api/subgraph"
        if sub_ep in reads_by_ep and reads_by_ep[sub_ep] is not None:
            fake = declared_by_ep[sub_ep] - {"hops"}
            neg_gap = sorted(reads_by_ep[sub_ep] - fake)
            rep.add("hops" in neg_gap,
                    "X16b-NEG 【负对照】契约副本删掉 `hops` ⇒ 方向A 必须报出（检测非恒真）",
                    "报出：" + "、".join(neg_gap) if neg_gap else "未报出 ⇒ 检测器恒真")
        else:
            rep.skip("X16b-NEG 负对照", "`/api/subgraph` 处理体未解析到")

        # ---- 方向 B：契约 params ⊆ 后端**源码字面量**（凭空登记的"幽灵参数" ⇒ 契约假条目）
        phantom = {}
        for ep in HANDLER:
            miss = [k for k in sorted(declared_by_ep[ep]) if f'"{k}"' not in vsx]
            if miss:
                phantom[ep] = miss
        rep.add(not phantom, "X16c 【方向B】契约登记的每个参数都在后端源码中真实出现（无幽灵条目）",
                "；".join(f"{k} 幽灵 {v}" for k, v in phantom.items()) if phantom
                else f"{sum(len(v) for v in declared_by_ep.values())} 个登记项全部落地")

        # ---- X16d：`frontend_must_not_send` 必须与**后端行为**一致（P0-4 的后端侧证据）
        #     契约说"这些键发到本端点等于没发" ⇒ 该端点源码里就**不得**真去读它们。
        mn_viol = {}
        for ep in HANDLER:
            mn = set(eps[ep].get("frontend_must_not_send", []) or [])
            hit = sorted(mn & (reads_by_ep[ep] or set()))
            if hit:
                mn_viol[ep] = hit
        rep.add(not mn_viol,
                "X16d must_not_send 与后端行为一致（声明「发了没用」的键，后端确不读取）",
                "；".join(f"{k} 实际被读 {v}" for k, v in mn_viol.items()) if mn_viol
                else "4/4 端点一致")

        # ---- X16e：结构完整性 + `backend_params_not_emitted` 每项必须**带理由**（不得只列名字）
        struct_bad = []
        for ep, e in eps.items():
            for key in ("handler", "params", "frontend_uses"):
                if key not in e:
                    struct_bad.append(f"{ep}.{key}")
        nem = ep_contract.get("backend_params_not_emitted", {}) or {}
        thin = [k for k, v in nem.items() if not (isinstance(v, str) and len(v.strip()) >= 8)]
        rep.add(not struct_bad and not thin and bool(nem),
                "X16e 契约结构完整（handler/params/frontend_uses）+ 不发参数均写明理由",
                ("缺字段：" + "、".join(struct_bad) if struct_bad else "")
                + ("；理由过短：" + "、".join(thin) if thin else "")
                or f"{len(eps)} 端点结构齐备 · {len(nem)} 项不发参数均有理由")

        # ---- X16f：legacy 端点的**冻结默认值**必须与契约声明逐值相等 ----
        #   为什么需要：legacy `/api/path` 有意不跟随 `queries.DEFAULT_*`（deadline 3000≠10000、
        #   max_hops 5≠DEFAULT_HOPS 2）。这是**合理**的（改它会静默改变外部脚本行为），
        #   但"与库默认不一致"若**无人声明**，下一个人就会当成 bug 顺手"修齐"。
        #   断言把分歧钉成契约：源码字面量 == 契约 `legacy_defaults`（逐值）。
        p_ep = eps.get("/api/path", {})
        ld = p_ep.get("legacy_defaults")
        body_p = body_by_ep.get("/api/path")
        if isinstance(ld, dict) and body_p:
            real = {}
            for k in ld:
                mm = re.search(r'"' + re.escape(k) + r'"\s*,\s*"(\d+)"', body_p)
                real[k] = int(mm.group(1)) if mm else None
            diff = {k: (real.get(k), ld[k]) for k in ld if real.get(k) != ld[k]}
            rep.add(not diff, "X16f legacy `/api/path` 冻结默认值 == 契约 legacy_defaults（分歧须被声明）",
                    "；".join(f"{k}: 源码 {v[0]} ≠ 契约 {v[1]}" for k, v in diff.items()) if diff
                    else "、".join(f"{k}={ld[k]}" for k in ld))
        else:
            rep.skip("X16f legacy 默认值声明", "契约缺 legacy_defaults 或未解析到 `_api_path` 处理体")

        # ---- X16g：端点声明的**客户端等待预算** == 前端常量（Phase 52 · D54 残余收敛）----
        #   `/api/search` 与 `/api/overview` **不接受** `deadline_ms`（不做 deadline 预算）
        #   ⇒ 前端的等待预算只能是**纯客户端**常量。此前它们各自硬编码在 `api.js` 里
        #   （search 3000 / overview 5000），与端点契约**毫无联系** —— 契约改了那边不知道，
        #   改这边也没人知道。现在收敛为：契约声明 → 前端转录 → 此处逐值求差。
        #   ★ 与 X4a/X4b 同族（都是"两处各写一套会静默漂移"），对象是**客户端**预算；
        #     与 X16f 同法（把"偶然相等"变成"已声明契约"）。
        try:
            _core = read(os.path.join(POC, "js", "core.js"))
            _m = re.search(r"export\s+const\s+ENDPOINT_CLIENT_BUDGET_MS\s*=\s*"
                           r"Object\.freeze\(\{([\s\S]*?)\}\);", _core)
            if not _m:
                # 声明不存在 ⇒ 明确 SKIP，不得静默算过（铁律 #20：缺依赖不得假通过）
                rep.skip("X16g 端点客户端预算同源",
                         "core.js 未见 `ENDPOINT_CLIENT_BUDGET_MS` 冻结对象")
            else:
                _blk = _m.group(1)
                # 前端常量块逐项（`key: 数字`）。★ 键为**短名**（search/overview），
                #   而 `eps` 的键是**全路径**（/api/search）—— 上一版直接拿全路径
                #   去匹配短名块，必然取不到 ⇒ 报「前端 None」。此处按约定派生短名。
                _fe = {_g.group(1): int(_g.group(2))
                       for _g in re.finditer(r"([A-Za-z_$][\w$]*)\s*:\s*([0-9]+)", _blk)}
                # 端点路径 → 短名：从**事实源自身**按 `/api/<name>` 派生，
                #   不硬编码映射表（铁律 #88：枚举须派生自事实源）
                _want, _offname = {}, []
                for _k, _v in eps.items():
                    if not (isinstance(_v, dict) and "client_budget_ms" in _v):
                        continue
                    if not _k.startswith("/api/"):
                        _offname.append(_k)
                        continue
                    _want[_k[len("/api/"):]] = _v.get("client_budget_ms")
                _bad = []
                for _k, _v in _want.items():
                    _got = _fe.get(_k)
                    if _got != _v:
                        _bad.append(f"{_k}: 前端 {_got} ≠ 契约 {_v}")
                # 反向断言（铁律 #88 配套）：前端不得留契约未声明的残余预算键
                _extra = sorted(set(_fe) - set(_want))
                if _extra:
                    _bad.append("前端多出契约未声明的预算键：" + "、".join(_extra))
                rep.add(bool(_want) and not _offname and not _bad,
                        "X16g 端点客户端预算 == 契约 client_budget_ms（逐值 + 反向；D54 残余）",
                        ("；".join(_bad) if _bad
                         else (f"契约端点命名不符 `/api/<name>` 约定：{_offname}"
                               if _offname else ""))
                        or "、".join(f"{k}={v}" for k, v in sorted(_want.items())))
        except Exception as e:  # noqa: BLE001
            rep.skip("X16g 端点客户端预算同源", f"解析失败：{e}")

    # ---------------------------------------------------------------- X17 跨层契约
    # ★ 前端 F44 用**构造数据**测渲染：数据侧把字段改名时，它照样全过。
    #   这里补一次「前端读取的字段在真实图里真的存在」的比对 ——
    #   否则改名会让详情卡整块静默消失而无人察觉（D80 家族：判据隔着构造数据）。
    rep.section("[X17] 前端读取字段 ↔ 数据契约（防字段改名后静默失效）")
    _CONTRACT = [
        # (字段, 期望覆盖率, 说明) —— 「三态/学科」设计上是全量写，变量表允许有个别无解
        ("applicability_status", 1.00, "适用条件三态（G3）"),
        ("domain_tags", 1.00, "学科全标签（G2）"),
        ("domain", 1.00, "学科主标签"),
        ("symbol_table", 0.95, "变量表（G1）"),
    ]
    if not os.path.isfile(NORMALIZED):
        rep.skip("X17 前端读取字段 ↔ 数据契约一致",
                 "缺 06_PoC/etl/normalized.json（**不入库**，干净检出下必然 SKIP）")
    else:
        try:
            with open(NORMALIZED, encoding="utf-8") as f:
                _g = json.load(f)
            _fml = [n for n in _g.get("nodes", []) if "Formula" in (n.get("labels") or [])]
            _ev_src = read(os.path.join(JS_DIR, "evidence.js"))
            _bad, _ok = [], {}
            for _fld, _need, _desc in _CONTRACT:
                if _fld not in _ev_src:
                    _bad.append(f"前端**未读取** {_fld}（{_desc}）—— 渲染路径根本没接上")
                    continue
                _hit = sum(1 for n in _fml if (n.get("props") or {}).get(_fld) not in (None, "", [], {}))
                _rate = (_hit / len(_fml)) if _fml else 0.0
                _ok[_fld] = f"{_hit}/{len(_fml)}={_rate:.2%}"
                if _rate < _need:
                    _bad.append(f"{_fld} 覆盖率 {_rate:.2%} < {_need:.0%}（前端读它，数据却没生成）")
            rep.add(bool(_fml) and not _bad,
                    f"X17 前端读取的 {len(_CONTRACT)} 个字段在真实图里都存在（覆盖率达标）",
                    "；".join(_bad) if _bad
                    else "；".join(f"{k} {v}" for k, v in _ok.items()))
            # 可红性自证：喂一个**必然不存在**的字段，同一套判据必须能认出它
            _ghost = "__definitely_absent_field__"
            _ghost_bad = 0
            if _ghost not in _ev_src:
                _ghost_bad += 1                      # 「前端没读」这一支
            _gh = sum(1 for n in _fml if (n.get("props") or {}).get(_ghost) not in (None, "", [], {}))
            if not _fml or _gh / len(_fml) < 0.95:
                _ghost_bad += 1                      # 「数据没生成」这一支
            rep.add(_ghost_bad == 2,
                    "X17a 本段**可红**：喂一个不存在的字段，两条判据分支都必须报错（断言非常真）",
                    f"命中的分支数 {_ghost_bad}/2")
        except Exception as e:  # noqa: BLE001
            rep.skip("X17 前端读取字段 ↔ 数据契约一致", f"读取失败：{e}")

    # ---------------------------------------------------------------- X18 跨层：导航层级
    # ★ F13：前端按 `stack.length` 推层、后端按 `level` 名分派 —— 两侧的**顺序**必须同源。
    #   顺序不一致的症状极其隐蔽：点「物理」下钻，请求发的是 `level=tags&parent=物理`，
    #   后端照做，返回空 ⇒ 页面上是「物理里没有领域」。**不报错**，只是每次都空。
    rep.section("[X18] 导航层级顺序：前端 `js/nav.js` ↔ 后端 `queries.NAV_LEVELS`（逐值 + 顺序）")
    try:
        _navjs = read(os.path.join(POC, "js", "nav.js"))
        _qpy = read(os.path.join(ROOT, "03_知识层", "queries.py"))
        _m_fe = re.search(r"NAV_LEVELS\s*=\s*Object\.freeze\(\[([^\]]*)\]\)", _navjs)
        _m_be = re.search(r"^NAV_LEVELS\s*=\s*\(([^)]*)\)", _qpy, re.M)
        _fe = re.findall(r'"([a-z_]+)"', _m_fe.group(1)) if _m_fe else []
        _be = re.findall(r'"([a-z_]+)"', _m_be.group(1)) if _m_be else []
        _ok18 = bool(_fe) and bool(_be) and _fe == _be
        rep.add(_ok18,
                "X18a 前端 NAV_LEVELS == 后端 NAV_LEVELS（逐值且**顺序**相同）",
                f"前端 {_fe} / 后端 {_be}"
                + ("" if _ok18 else " ⇒ 下钻会发到后端不认的层，表现为「这一类里没有东西」"))

        # 可红性自证：把前端顺序反转，同一判据必须判不等（否则这条断言恒真）
        _rev = list(reversed(_fe))
        rep.add(bool(_fe) and _rev != _be,
                "X18a-NEG 【负对照】把前端层序反转 ⇒ 必须判不等（检测非恒真）",
                f"反转后 {_rev} vs 后端 {_be}")

        # 层名中文：每个 level 都必须有一句中文，否则界面上会出现裸的 `tags`
        _labels = set(re.findall(r"^\s*([a-z_]+):\s*\"", _navjs, re.M))
        _miss_lab = [lv for lv in _be if lv not in _labels]
        rep.add(bool(_be) and not _miss_lab,
                "X18b 每个 level 都有中文层名（缺一个，界面上就会裸出 `tags` 这种英文键）",
                "缺：" + "、".join(_miss_lab) if _miss_lab else f"{len(_be)} 层齐备")
    except Exception as e:  # noqa: BLE001
        rep.skip("X18 导航层级跨层一致", f"读取/解析失败：{e}")

    # ---------------------------------------------------------------- X19 跨层：公式五段
    # ★ F6：抽屉按 `FORMULA_SECTIONS` 的**顺序**渲染五段，后端按同一常量组织 `sections`。
    #   两侧各写一份 ⇒ 改名/调序时抽屉**静默漏掉一段**（不报错，只是不显示），
    #   与 X18（导航层序）同族：症状是「这一段好像从来没有过」。
    rep.section("[X19] 公式五段：前端 `js/formula.js` ↔ 后端 `queries.FORMULA_SECTIONS`")
    try:
        _fjs = read(os.path.join(POC, "js", "formula.js"))
        _m_ff = re.search(r"export const FORMULA_SECTIONS\s*=\s*Object\.freeze\(\[(.*?)\]\)", _fjs, re.S)
        _m_bf = re.search(r"^FORMULA_SECTIONS\s*=\s*\((.*?)\)", _qpy, re.S | re.M)
        _ff = re.findall(r'"([a-z_]+)"', _m_ff.group(1)) if _m_ff else []
        _bf = re.findall(r'"([a-z_]+)"', _m_bf.group(1)) if _m_bf else []
        _ok19 = bool(_ff) and bool(_bf) and _ff == _bf
        rep.add(_ok19,
                "X19a 前端 FORMULA_SECTIONS == 后端 FORMULA_SECTIONS（逐值且**顺序**相同）",
                f"前端 {_ff} / 后端 {_bf}"
                + ("" if _ok19 else " ⇒ 抽屉会静默漏掉一段，表现为「这一段好像从来没有过」"))

        # 可红性自证：把前端段序反转 ⇒ 同一判据必须判不等
        _rev2 = list(reversed(_ff))
        rep.add(bool(_ff) and _rev2 != _bf,
                "X19a-NEG 【负对照】把前端段序反转 ⇒ 必须判不等（检测非恒真）",
                f"反转后 {_rev2} vs 后端 {_bf}")
    except Exception as e:  # noqa: BLE001
        rep.skip("X19 公式五段跨层一致", f"读取/解析失败：{e}")
    # ---------------------------------------------------------------- N 段
    rep.section("[N] Node 纯逻辑断言（`node 06_PoC/tests/frontend_core_check.mjs`）")
    node = find_node()
    check_script = os.path.join(HERE, "frontend_core_check.mjs")
    if not node:
        rep.skip("N1 前端纯逻辑断言（五态/参数/合并/会话/模式）",
                 "环境无 node ⇒ **不计通过**（可用 STTP_NODE 指定解释器）")
    else:
        r = subprocess.run([node, check_script], cwd=POC, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        tail = [ln for ln in (r.stdout or "").splitlines() if "frontend_core_check:" in ln]
        summary = tail[-1].strip() if tail else "(无摘要行)"
        rep.add(r.returncode == 0, f"N1 前端纯逻辑断言全过（{os.path.basename(node)}）", summary)
        if r.returncode != 0:
            for ln in (r.stdout or "").splitlines():
                if "[FAIL]" in ln:
                    print("      " + ln.strip())

    # ---------------------------------------------------------------- H 段
    rep.section("[H] HTTP 层（MIME 正确性决定 ES Module 能否加载）")
    try:
        import viz_server as V  # type: ignore
        from http.server import ThreadingHTTPServer
        srv = ThreadingHTTPServer(("127.0.0.1", 0), V.VizHandler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            def probe(p):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}{p}", timeout=30) as r:
                        return r.status, (r.headers.get("Content-Type") or ""), r.read()
                except urllib.error.HTTPError as e:
                    return e.code, (e.headers.get("Content-Type") or ""), (e.read() or b"")

            st, ct, body = probe("/js/main.js")
            rep.add(st == 200 and "javascript" in ct, "H1 /js/main.js 返回 200 且 MIME 为 javascript",
                    f"{st} {ct}")
            st2, _, _ = probe("/js/../viz_server.py")
            rep.add(st2 == 403, "H2 目录穿越被拒（403）", str(st2))
            st3, ct3, b3 = probe("/")
            rep.add(st3 == 200 and "text/html" in ct3, "H3 / 返回页面（text/html）",
                    f"{st3} {ct3} {len(b3)} 字节")
            # 页面里引用的每个 js 都能 200
            bad_ref = []
            for m2 in MODULES:
                s, c2, _ = probe(f"/js/{m2}.js")
                if s != 200 or "javascript" not in c2:
                    bad_ref.append(f"{m2}({s})")
            rep.add(not bad_ref, f"H4 页面引用的 {len(MODULES)} 个模块均可 HTTP 取到",
                    "、".join(bad_ref) if bad_ref else f"{len(MODULES)}/{len(MODULES)}")
            st5, _, _ = probe("/js/does-not-exist.js")
            rep.add(st5 == 404, "H5 不存在的模块 ⇒ 404（不是静默 200）", str(st5))

            # ---- H6：**前端消费的字段** vs 后端**真实响应**（防"读了不存在的字段"）----
            import urllib.parse as _up

            def probe_json(p):
                s, _c, b = probe(p)
                try:
                    return s, json.loads(b.decode("utf-8"))
                except Exception:  # noqa: BLE001
                    return s, None

            s6, env = probe_json("/api/subgraph?mode=ego&seeds="
                                 + _up.quote("PQ:mass") + "&hops=1&max_nodes=40&max_edges=30&trust=all")
            if s6 != 200 or not isinstance(env, dict):
                rep.skip("H6 前端消费字段 ↔ 真实响应", f"子图端点不可用（HTTP {s6}）")
            else:
                need_top = ["status", "reason", "error_code", "snapshot_id", "query_hash",
                            "hidden_by_trust", "nodes", "edges", "expandable", "stats"]
                miss = [k for k in need_top if k not in env]
                rep.add(not miss, "H6a 统一信封顶层字段齐备（前端状态行读这些）",
                        "缺: " + ",".join(miss) if miss else f"{len(need_top)}/{len(need_top)}")

                # ★ H6a2/H6a3（2026-10-14 P1 回归）：`/api/stats` **必须**也带合法信封。
                #   现场：它是 legacy 端点、响应体无 `status` ⇒ 前端 `normalizeStatus(undefined)`
                #   判为 "error" ⇒ `detectMode` 认定后端不可用 ⇒ 退到 `bundle` ⇒
                #   `GET /graph_data.json`（48.7 MB 全图）⇒ 浏览器卡死。
                #   H6a 只覆盖 `/api/subgraph`，所以这个缺陷此前**无人防守**。
                st_st, st_env = probe_json("/api/stats")
                _five = ("ok", "no_result", "truncated", "timeout", "error")
                rep.add(st_st == 200 and isinstance(st_env, dict)
                        and st_env.get("status") in _five,
                        "H6a2 /api/stats 带合法 status（探活判据；缺它 ⇒ 误判 offline ⇒ 拉全图卡死）",
                        f"HTTP {st_st} · status={st_env.get('status') if isinstance(st_env, dict) else None}")
                rep.add(isinstance(st_env, dict) and bool(st_env.get("snapshot_id"))
                        and st_env.get("snapshot_id") == env.get("snapshot_id"),
                        "H6a3 /api/stats 的 snapshot_id 与 /api/subgraph 一致（同源同快照）",
                        f"stats={st_env.get('snapshot_id') if isinstance(st_env, dict) else None}"
                        f" · subgraph={env.get('snapshot_id')}")

                need_stats = ["n_nodes", "n_edges", "truncated_by", "pre_truncation",
                              "pending_frontier", "pending_frontier_note", "hidden_by_trust",
                              "elapsed_ms", "expansions", "session"]
                ms2 = [k for k in need_stats if k not in (env.get("stats") or {})]
                rep.add(not ms2, "H6b stats 字段齐备（截断/会话/下界提示读这些）",
                        "缺: " + ",".join(ms2) if ms2 else f"{len(need_stats)}/{len(need_stats)}")
                sess = (env.get("stats") or {}).get("session") or {}
                ms2b = [k for k in ("tracked", "n_nodes", "n_edges", "limit_nodes", "limit_edges")
                        if k not in sess]
                rep.add(not ms2b, "H6c stats.session 字段齐备（会话角标读这些）",
                        "缺: " + ",".join(ms2b) if ms2b else "5/5")

                n0 = (env.get("nodes") or [{}])[0]
                ms3 = [k for k in ("id", "label", "type", "subject", "why_shown") if k not in n0]
                rep.add(not ms3, "H6d 节点字段齐备（渲染 + why_shown 固定行读这些）",
                        "缺: " + ",".join(ms3) if ms3 else "5/5")

                e0 = (env.get("edges") or [{}])[0]
                ms4 = [k for k in ("id", "source", "target", "type", "kind", "confidence",
                                   "explicit_or_inferred", "verification_level", "verifier",
                                   "evidence", "why_shown", "stale") if k not in e0]
                rep.add(not ms4, "H6e 边字段齐备（视觉编码 + **证据卡片**读这些）",
                        "缺: " + ",".join(ms4) if ms4 else "12/12")
                ev0 = (e0.get("evidence") or [{}])[0]
                ms5 = [k for k in ("src", "src_ver", "src_hash", "impl", "impl_ver")
                       if k not in ev0]
                rep.add(not ms5, "H6f 证据字段齐备（铁律 #59：src 与 impl 必须分列可读）",
                        "缺: " + ",".join(ms5) if ms5 else "5/5")

                ex0 = (env.get("expandable") or [{}])[0]
                ms6 = [k for k in ("node_id", "token") if k not in ex0]
                rep.add(not ms6, "H6g expandable 字段齐备（展开用 node_id + token）",
                        "缺: " + ",".join(ms6) if ms6 else "2/2")

            # ---- H7：**跨层端到端** —— 前端生成的查询串 ⇒ 真实后端 -----------------
            #   ★ 为什么必须做（2026-10-14 P0 现场）：`buildParams` **漏掉 `q`** 时，
            #     前端照常发请求、后端返回 400「缺少参数 q」，而 X/N 段的形状断言
            #     （含 F11「同输入两次序列化相同」）**全绿** —— 没有任何断言看过
            #     "真正发出去的 URL 里到底有什么"。这正是 #83（形状断言 ≠ 行为断言）
            #     在**参数序列化**上的又一次现身。
            #   做法：让 `Api.search` 真跑一遍（捕获型 fetchImpl 取 URL），把该 URL
            #     原样交给**真实 HTTP 服务**；再**构造性**把旧行为（无 q）发一次，
            #     证明这条断言不是空转（#75 负对照须可构造）。
            if not node:
                rep.skip("H7 跨层端到端（前端查询串 ⇒ 真实后端）",
                         "环境无 node ⇒ **不计通过**（可用 STTP_NODE 指定）")
            else:
                api_uri = pathlib.Path(os.path.join(POC, "js", "api.js")).as_uri()
                js = (
                    "import { Api } from " + json.dumps(api_uri) + ";\n"
                    "let seen = null;\n"
                    "const api = new Api({ base: '', fetchImpl: (u) => { seen = u;\n"
                    "  return Promise.resolve({ ok: true, status: 200,"
                    " headers: { get: () => null },\n"
                    "    json: () => Promise.resolve({ status: 'ok', hits: [], count: 0,\n"
                    "      total_matches: 0, snapshot_id: 'sha256:000000000000',\n"
                    "      query_hash: '0000000000000000' }) }); } });\n"
                    "await api.search('F=ma', { limit: 3 });\n"
                    "console.log(seen);\n"
                )
                rr = subprocess.run([node, "--input-type=module", "-e", js], cwd=POC,
                                    capture_output=True, text=True, encoding="utf-8",
                                    errors="replace")
                lines = [x.strip() for x in (rr.stdout or "").splitlines() if x.strip()]
                got = lines[-1] if lines else ""
                if not got.startswith("/api/search"):
                    rep.add(False, "H7a 前端 Api.search 的查询串 ⇒ 真实后端 200（含 q=）",
                            f"未能取到 URL（RC={rr.returncode}）：{got!r} / {(rr.stderr or '')[:120]}")
                else:
                    s_be, be = probe_json(got)
                    has_q = bool(re.search(r"[?&]q=", got))
                    rep.add(s_be == 200 and has_q,
                            "H7a 前端 Api.search 的查询串 ⇒ 真实后端 200（含 q=，不再是 400）",
                            f"HTTP {s_be} · {got}")
                    hits = (be or {}).get("hits") or []
                    rep.add(bool(hits) and hits[0].get("id") == "MX:phy:newton2",
                            "H7b 该查询真的命中（F=ma ⇒ MX:phy:newton2）——「能通」升级为「能查到」",
                            f"hits={len(hits)} 首条={hits[0].get('id') if hits else None}")
                    s_bad, bad = probe_json("/api/search?limit=3&mode=ego")
                    rep.add(s_bad == 400 and (bad or {}).get("error_code") == "BAD_REQUEST"
                            and "q" in ((bad or {}).get("reason") or ""),
                            "H7c 负对照：旧行为（无 q 的查询串）确被后端 400 拒绝 ⇒ H7a 非空转",
                            f"HTTP {s_bad} · {((bad or {}).get('reason'))}")
                    # H7d：最短长度约束**确实存在于后端**（前端阈值不是凭空写的）
                    s_min, mn = probe_json("/api/search?q=F&limit=3")
                    rep.add(s_min == 400 and "至少" in ((mn or {}).get("reason") or ""),
                            "H7d 单字符查询确被后端拒绝（400）⇒ 前端 MIN_QUERY_LEN 有真实依据（非装饰常量）",
                            f"HTTP {s_min} · {((mn or {}).get('reason'))}")
        finally:
            srv.shutdown()
    except Exception as e:  # noqa: BLE001
        rep.skip("H1–H5 HTTP 层", f"起服务失败：{e}")

    # ---------------------------------------------------------------- H8 段
    # ★ 为什么必须**独立子进程**：同进程里后端一旦被 H6 摸过就已是**热态**，
    #   「冷启动误判离线」（P0-2）这个缺陷会**测不出来**。测量环境必须包含被测条件
    #   —— 这与「仪器自指」（#82）同族：在热态里断言冷态行为 = 假绿。
    rep.section("[H8] 冷启动与参数生效（独立子进程 ⇒ 真·冷态；P0-2/P0-4 的端到端证据）")
    vs_py = os.path.join(POC, "viz_server.py")
    if not os.path.isfile(vs_py):
        rep.skip("H8 冷启动端到端", "未找到 viz_server.py")
    else:
        import socket
        import tempfile
        import time as _time

        sk = socket.socket()
        sk.bind(("127.0.0.1", 0))
        hport = sk.getsockname()[1]
        sk.close()
        # ★ 代理绕过：本机代理会把 127.0.0.1 也当外部流量（实测直连需绕开）
        _opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        _logf = tempfile.NamedTemporaryFile(prefix="sttp_viz_", suffix=".log", delete=False)
        env8 = dict(os.environ)
        env8["PYTHONUNBUFFERED"] = "1"   # 否则日志块缓冲 8 KB ⇒ 诊断拿不到实时证据
        proc = None
        try:
            proc = subprocess.Popen([sys.executable or "python", vs_py,
                                     "--host", "127.0.0.1", "--port", str(hport)],
                                    cwd=POC, env=env8,
                                    stdout=_logf, stderr=subprocess.STDOUT)

            def _hz(timeout=5.0):
                with _opener.open(f"http://127.0.0.1:{hport}/api/healthz", timeout=timeout) as r:
                    return r.status, json.loads(r.read())

            def _hz_raw(timeout=5.0):
                with _opener.open(f"http://127.0.0.1:{hport}/api/healthz", timeout=timeout) as r:
                    return r.status, r.read()

            def _get(path, timeout=90.0):
                with _opener.open(f"http://127.0.0.1:{hport}{path}", timeout=timeout) as r:
                    return r.status, json.loads(r.read())

            # ---- H8a：**先等端口 listen**（TCP 可连），再量 healthz ----
            #   ★ 第一版把「spawn → 首个 healthz」当成探活延迟来断言，实测 3292 ms 判定失败。
            #     拆开看：其中 ~2.7 s 是 `main()` 在 bind **之前**同步加载 67.7 MB 图
            #     （进程还没监听，任何 HTTP 都无从谈起）。这是**真问题**（页面首次可访问
            #     被无谓推迟、`warming` 三态成为死代码），但**不是**探活延迟。
            #     ⇒ ① 修 `main()`（先 bind 再预热，见 F1）；② 断言拆成两段各自可测：
            #        H8a = 「listen → healthz」必须毫秒级；H8b = 「spawn → listen」不得再含图加载。
            t0 = _time.perf_counter()
            t_listen = None
            while _time.perf_counter() - t0 < 60.0:
                _sk = socket.socket()
                _sk.settimeout(0.2)
                okc = _sk.connect_ex(("127.0.0.1", hport)) == 0
                _sk.close()
                if okc:
                    t_listen = _time.perf_counter() - t0
                    break
                _time.sleep(0.02)

            hz_first_ms = None
            if t_listen is None:
                rep.add(False, "H8a 端口 listen 后 /api/healthz 毫秒级应答（探活不再误判离线）",
                        f"60 s 内端口未可用（日志：{open(_logf.name, encoding='utf-8', errors='replace').read()[-300:]!r}）")
            else:
                t1 = _time.perf_counter()
                first = None
                while _time.perf_counter() - t1 < 30.0:
                    try:
                        st, body = _hz()
                        first = (_time.perf_counter() - t1, st, body)
                        break
                    except Exception:  # noqa: BLE001
                        _time.sleep(0.02)
                if first is None:
                    rep.add(False, "H8a 端口 listen 后 /api/healthz 毫秒级应答（探活不再误判离线）",
                            "listen 后 30 s 内 healthz 仍无响应")
                else:
                    hz_first_ms, st0, b0 = first
                    rep.add(st0 == 200 and hz_first_ms < 0.5,
                            "H8a 端口 listen 后 /api/healthz 毫秒级应答（探活不再误判离线）",
                            f"listen→首个 healthz {hz_first_ms*1000:.0f} ms · HTTP {st0}"
                            + "（对照：同刻 /api/stats 冷态 4.9 s、前端旧预算 2 s ⇒ 必然 abort）")

            # ---- H8b：`spawn → listen` 已**不含**图加载（F1 回归护栏）----
            #   旧实现（bind 前同步 get_backend）= 2774 ms；新实现（bind 后异步预热）应 < 1 s。
            #   阈值取 2000 ms 留足余量（本机实测 ~400 ms），仍能拦住"把 get_backend 放回 bind 前"。
            if t_listen is not None:
                rep.add(t_listen < 2.0,
                        "H8b `spawn → listen` 不含图加载（先 bind 再预热，页面不再被 67.7 MB 阻塞）",
                        f"{t_listen*1000:.0f} ms < 2000 ms"
                        + ("" if t_listen < 2.0 else " ⇒ 疑似把 get_backend() 放回 bind 之前"))

            # ---- H8c：冷启动期**如实**报告状态（warming 不得编造 snapshot_id）----
            saw_warming = False
            if first is not None:
                st0, b0 = first[1], first[2]
                bk = b0.get("backend")
                saw_warming = (bk == "warming")
                ok_b = (b0.get("status") == "ok" and bk in ("warming", "ready", "failed")
                        and isinstance(b0.get("graph_ready"), bool))
                # warming 期间 snapshot_id **必须**为 null（未就绪就不知道快照）
                honest = (bk != "warming") or (b0.get("snapshot_id") is None)
                rep.add(ok_b and honest,
                        "H8c 冷启动期如实报告 backend/graph_ready（warming 不得编造 snapshot_id）",
                        f"backend={bk} · graph_ready={b0.get('graph_ready')} · "
                        f"snapshot_id={b0.get('snapshot_id')!r}")

                # ---- H8d：`warming` 期**页面即可用**（先 bind 的收益；不是"等图装完才开门"）----
                try:
                    _sk2 = _opener.open(f"http://127.0.0.1:{hport}/", timeout=10)
                    raw_h = _sk2.read()
                    ct_h = _sk2.headers.get("Content-Type", "")
                    _sk2.close()
                    page_ok = (len(raw_h) > 2000 and "html" in ct_h)
                except Exception as e:  # noqa: BLE001
                    page_ok, raw_h = False, b""
                    rep.add(False, "H8d `warming` 期页面即可 200（先 bind 再预热的收益）", str(e)[:120])
                else:
                    rep.add(page_ok,
                            "H8d `warming` 期页面即可 200（先 bind 再预热的收益）",
                            f"{len(raw_h)} 字节 · {ct_h} · 当时 backend={bk}")

                # ---- H8c2：`warming` 状态**确实可达**（否则本轮加的预热机制又是死代码）----
                #   不能硬性要求"必然看到 warming"（机器快时可能瞬间 ready ⇒ 假红）。
                #   故断言：**要么**看到了 warming，**要么** listen 后 200 ms 内就已 ready
                #   （后者说明预热可忽略，本机制无存在必要，也不构成缺陷）。
                if first is not None:
                    rep.add(saw_warming or (hz_first_ms is not None and hz_first_ms < 0.2),
                            "H8c2 `warming` 状态可达（预热机制非死代码；或预热快到无需它）",
                            f"首个 healthz backend={b0.get('backend')} · "
                            f"listen→healthz {hz_first_ms*1000:.0f} ms")

                # ---- H8e：healthz 是**轻量**端点（不触碰图谱：无 nodes/edges 且体积极小）----
                st_l, raw_l = _hz_raw()
                light = (st_l == 200 and len(raw_l) < 2048
                         and b'"nodes"' not in raw_l and b'"edges"' not in raw_l)
                rep.add(light, "H8e /api/healthz 不触碰图谱（无 nodes/edges，响应 < 2 KB）",
                        f"{len(raw_l)} 字节 · HTTP {st_l}")

            # ---- H8f：两个**并发冷** `/api/stats` ⇒ 单飞（不重复 4.3 s 冷算）----
            #   为什么需要（2026-10-15 实测发现）：三份派生缓存原先都是「锁内查、**锁外算**」
            #   ⇒ 并发调用各算一遍。未加单飞时，后台预热与一个立刻到达的 `/api/stats`
            #   会同时跑 `fidelity_counts_global()`（4.27 s），该请求由 4.9 s **恶化到 8.5 s**。
            #   断言用**可观测计数**（`healthz.cache_builds`），不靠测时间 ——
            #   测时间在快机器假绿、慢机器假红；计数是 O(1) 的因果本身。
            cold = {}

            def _one(i):
                _tt = _time.perf_counter()
                try:
                    with _opener.open(f"http://127.0.0.1:{hport}/api/stats", timeout=180) as r:
                        cold[i] = ((_time.perf_counter() - _tt) * 1000, r.status,
                                   json.loads(r.read()))
                except Exception as e:  # noqa: BLE001
                    cold[i] = ((_time.perf_counter() - _tt) * 1000, None, {"err": str(e)[:90]})

            _ths = [threading.Thread(target=_one, args=(i,)) for i in range(2)]
            _tc = _time.perf_counter()
            for _th in _ths:
                _th.start()
            for _th in _ths:
                _th.join()
            cold_wall = (_time.perf_counter() - _tc) * 1000
            cold_lat = [cold[i][0] for i in sorted(cold)]
            same_payload = (cold.get(0, (0, None, None))[2] == cold.get(1, (0, None, None))[2])
            rep.add(len(cold) == 2 and all(v[1] == 200 for v in cold.values()) and same_payload,
                    "H8f 两个并发冷 `/api/stats` 均成功且**载荷逐字节一致**",
                    f"各自 {cold_lat[0]:.0f}/{cold_lat[1]:.0f} ms · 墙钟 {cold_wall:.0f} ms"
                    + ("" if same_payload else " ⇒ 两份载荷不一致"))

            # ---- 等后端就绪（首查已 kick_warmup），再验参数真正生效 ----
            warm_ok, waited = False, _time.perf_counter()
            snap_ready, cb_ready = None, {}
            t_warm0 = _time.perf_counter()
            while _time.perf_counter() - t_warm0 < 90.0:
                try:
                    st, bw = _hz(timeout=10)
                    if bw.get("backend") == "ready":
                        warm_ok, snap_ready = True, bw.get("snapshot_id")
                        cb_ready = bw.get("cache_builds") or {}
                        break
                    if bw.get("backend") == "failed":
                        break
                except Exception:  # noqa: BLE001
                    pass
                _time.sleep(0.2)
            warm_ms = (_time.perf_counter() - t_warm0) * 1000
            rep.add(warm_ok, "H8g 后台预热最终到 ready（healthz 触发的预热线程真的完成）",
                    f"ready · snapshot_id={snap_ready} · 等待 {warm_ms:.0f} ms"
                    if warm_ok else "90 s 内未 ready")

            # ---- H8g2 ★ `ready` 必须**等价于**「整条读路径已热」，不得只因"图装好了" ----
            #   第一版 `_healthz` 在 `backend_ready()`（只看图）为真时就改写 `backend="ready"`，
            #   于是后台还在算 `graph_stats()`（≈4.8 s）时就已经宣称就绪 ⇒ 前端照旧要等。
            #   本断言把「ready ⇒ 派生缓存已建」钉住（用可观测计数，不靠测时间）。
            rep.add(warm_ok and all((cb_ready.get(k) or 0) > 0
                                    for k in ("stale", "fidelity", "estatus")),
                    "H8g2 `backend=ready` ⇒ 派生缓存已建（就绪＝读路径已热，非「图装好就算」）",
                    f"ready 时 cache_builds={cb_ready}")

            # ---- H8h：单飞可观测 —— 预热 + 2 个并发冷请求 ⇒ 每份缓存**只算一次** ----
            cb = (_hz()[1] or {}).get("cache_builds") or {}
            rep.add(bool(cb) and all(cb.get(k) == 1 for k in ("stale", "fidelity", "estatus")),
                    "H8h 单飞：预热 + 并发请求共享同一份冷算（`cache_builds` 各 == 1）",
                    f"cache_builds={cb}（应有 3 个潜在构建者：预热线程 + 2 个并发请求）")

            # ---- H8i：预热把 `/api/stats` 的**冷算**也建好了（F2 的收益，可量化）----
            #   对照：本机冷态首次 `/api/stats` ≈ 4.9 s（其中 `fidelity_counts_global()` 4.27 s）。
            #   阈值 2000 ms：既约束住"必须预热 stats"，又不至于因机器差异假红。
            t_s = _time.perf_counter()
            ss, stt = _get("/api/stats", timeout=60)
            stats_ms = (_time.perf_counter() - t_s) * 1000
            rep.add(ss == 200 and stats_ms < 2000,
                    "H8i 预热后 `/api/stats` 远离冷算（对照冷态 ≈ 4.9 s ⇒ 收益可量化）",
                    f"{stats_ms:.0f} ms · HTTP {ss} · 忠实度分列="
                    f"{'有' if (stt.get('evidence_fidelity') or stt.get('evidence_status')) else '无'}")

            # ---- H8j：`hops` 真的改变结果（`put("hops")` 修复的端到端证据）----
            q1 = ("/api/subgraph?mode=ego&seeds=CE:pq:gibbs_energy&hops=1&max_hops=1"
                  "&trust=all&max_nodes=200&max_edges=400&deadline_ms=20000")
            q3 = ("/api/subgraph?mode=ego&seeds=CE:pq:gibbs_energy&hops=3&max_hops=3"
                  "&trust=all&max_nodes=200&max_edges=400&deadline_ms=20000")
            s1, d1 = _get(q1)
            s3, d3 = _get(q3)
            n1 = {x.get("id") for x in (d1.get("nodes") or [])}
            n3 = {x.get("id") for x in (d3.get("nodes") or [])}
            rep.add(s1 == 200 and s3 == 200 and n1 != n3 and len(n3) > len(n1),
                    "H8j `hops=1` 与 `hops=3` 结果**不同**（跳数真的被后端采用，不再是静默丢弃）",
                    f"1 跳 {len(n1)} 节点 / 3 跳 {len(n3)} 节点 · 新增 {len(n3 - n1)}")

            # ---- H8k：`subjects` 生效、`subject`（单数）在本端点**不生效**（负对照）----
            base = ("/api/subgraph?mode=ego&seeds=CE:pq:gibbs_energy&hops=2&max_hops=2"
                    "&trust=all&max_nodes=200&max_edges=400&deadline_ms=20000")
            _, dp = _get(base + "&subjects=%E5%8C%96%E5%AD%A6")
            _, ds = _get(base + "&subject=%E5%8C%96%E5%AD%A6")
            np_, ns_ = len(dp.get("nodes") or []), len(ds.get("nodes") or [])
            rep.add(np_ > 0 and np_ < ns_,
                    "H8k `/api/subgraph` 认 `subjects`（复数）、不认 `subject`（单数）⇒ 学科筛选真的生效",
                    f"subjects ⇒ {np_} 节点 · subject ⇒ {ns_} 节点（等同未过滤）")

            # ---- H8l：`paths` 真的回来（统一信封里的 `paths` 非空）----
            sp, dpth = _get("/api/subgraph?mode=path&seeds=CE:pq:gibbs_energy,PB:pq:entropy"
                            "&max_hops=5&max_paths=5&trust=all&deadline_ms=20000")
            lst = dpth.get("paths") or []
            rep.add(sp == 200 and len(lst) > 0 and isinstance(lst[0], dict),
                    "H8l `mode=path` 的 `paths` 真的随统一信封回传（非空、元素为对象）",
                    f"HTTP {sp} · {len(lst)} 条路径")

            # ---- H8m：P0-4 负对照 —— 把 `src`/`dst` 发到 /api/subgraph **必然 no_result** ----
            sn, dn = _get("/api/subgraph?mode=path&src=CE:pq:gibbs_energy&dst=PB:pq:entropy"
                          "&max_hops=5&max_paths=5&trust=all&deadline_ms=20000")
            rep.add(sn == 200 and dn.get("status") == "no_result" and not (dn.get("paths") or []),
                    "H8m 【负对照】`src`/`dst` 发到 /api/subgraph ⇒ no_result（坐实 P0-4 不是玄学）",
                    f"HTTP {sn} · status={dn.get('status')} · {(dn.get('reason') or '')[:60]}")

            # ============================================================ H9 段
            # ★ D70：客户端断连（外部评审 2026-10-17 · RC1-12，**本轮复现并修复**）
            #   复现到的现场：客户端 RST 后，服务端在 `handle_one_request` 的
            #   `self.rfile.readline(65537)` 抛 `ConnectionResetError` ⇒ 漏到 `socketserver`
            #   ⇒ `handle_error()` 打印「Exception occurred during processing of request」
            #   + 完整 traceback + 40 个短横线 ⇒ **一次关闭标签页 = 三层栈**，真异常被淹掉。
            #   ★ 为什么必须**端到端**测、不能只读 `do_GET` 的 `except`：这条路上
            #     **请求行都还没读到**，`do_GET` 根本没被调用 —— 只看代码会得出
            #     「已经兜住了」的错误结论（正是本轮踩到的判断陷阱）。
            #   ★ 两个方向都要守：**不许打栈**（可读性）且**不许静默**（铁律 #67：
            #     异常吞成空 ⇒ 「日志干净」与「日志没在写」不可区分）。
            rep.section("[H9] 客户端断连（D70：不打三层栈，也不静默吞掉）")
            import viz_server as V  # noqa: F811 —— 同模块缓存；本段直接调生产方法

            _log0 = 0
            try:
                _log0 = os.path.getsize(_logf.name)
            except OSError:
                pass

            def _log_since(off):
                try:
                    with open(_logf.name, "rb") as _f:
                        _f.seek(off)
                        return _f.read().decode("utf-8", "replace")
                except OSError:
                    return ""

            # ---- H9a：**确定性**触发「读请求行时 RST」----
            #   只发到 `\r`（不构成完整请求行）⇒ 服务端**必定**阻塞在 `readline` 里，
            #   此刻 RST ⇒ `recv` 返回 ECONNRESET。比「发完整请求再抢时序」确定得多
            #   （后者会因机器快慢时红时绿，属铁律 #20 的反面）。
            _rst_ok, _rst_err = False, ""
            for _attempt in range(3):
                try:
                    _rs = socket.socket()
                    _rs.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                   struct.pack("ii", 1, 0))
                    _rs.connect(("127.0.0.1", hport))
                    _rs.sendall(b"GET /api/healthz HTTP/1.1\r")
                    _time.sleep(0.05)
                    _rs.close()                 # SO_LINGER(1,0) ⇒ 发 RST 而非 FIN
                    _rst_ok = True
                    break
                except OSError as _e:
                    _rst_err = str(_e)[:80]
            _time.sleep(1.0)
            _new = _log_since(_log0)
            _n_tb = _new.count("Traceback (most recent call last)")
            _n_hdr = _new.count("Exception occurred during processing of request")
            rep.add(_rst_ok and _n_tb == 0 and _n_hdr == 0,
                    "H9a 读请求行时 RST ⇒ 服务端**不打** traceback / 不打印 "
                    "「Exception occurred during processing of request」",
                    f"RST {('成功' if _rst_ok else '失败:' + _rst_err)} · 新增日志 "
                    f"{len(_new)} 字符 · traceback {_n_tb} 次 · 三层栈头 {_n_hdr} 次")

            # ---- H9b：断连必须**留一行可检索的记录**（静默吞掉同样不合格）----
            rep.add("客户端断连" in _new,
                    "H9b 断连**留一行可检索日志**（静默吞掉同样不合格 —— 铁律 #67）",
                    next((ln.strip()[:120] for ln in _new.splitlines()
                          if "客户端断连" in ln), "（未找到 ⇒ 可能被静默吞掉）"))

            # ---- H9c：断连未损害服务（子进程仍活、后续请求正常）----
            _alive = False
            try:
                _st_h, _b_h = _hz(timeout=10)
                _alive = (_st_h == 200 and (_b_h or {}).get("status") == "ok")
            except Exception:  # noqa: BLE001 —— 本断言就是要把「服务没了」抓成 FAIL
                _alive = False
            rep.add(_alive and proc.poll() is None,
                    "H9c 断连后服务仍可用（healthz 200 且子进程存活）",
                    f"healthz={'200' if _alive else '失败'} · 子进程 poll={proc.poll()}")

            # ---- H9d：**写响应**时断连 ⇒ `_send` 返回 False、不抛、计入统计 ----
            #   ★ 为什么这一段走「真 socketpair + 生产 `_send`」而不是端到端：
            #     小响应（healthz 几百字节）一次 `sendall` 就能全进内核发送缓冲 ⇒
            #     断连**写成功**，端到端会**假绿**（本轮实测确认）；而要确定性触发
            #     就必须用大响应，真实大响应路由 `/graph_data.json` 在干净检出里
            #     可能不存在（会触发即时生成并**写盘**，弄脏工作树，铁律 #97）。
            #     故：除「谁来调用它」之外全部是生产路径 —— 判据强度不降。
            def _dead_sock():
                _a, _b = socket.socketpair()
                _b.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                              struct.pack("ii", 1, 0))
                _b.close()                     # RST
                _time.sleep(0.05)
                return _a

            def _mk_handler(_sock):
                _h = V.VizHandler.__new__(V.VizHandler)   # 跳过 socket setup，只测 `_send`
                _h.wfile = _sock.makefile("wb", 0)        # 与 `wbufsize=0` 同口径
                _h.command = "GET"
                _h.path = "/api/stats"
                _h.request_version = "HTTP/1.1"
                _h.protocol_version = "HTTP/1.1"
                _h.client_address = ("127.0.0.1", 4242)
                _h.close_connection = False
                _h.requestline = "GET /api/stats HTTP/1.1"
                return _h

            _BIG = b"x" * (20 * 1000 * 1000)      # 20 MB：远超任何内核发送缓冲 ⇒ 必失败
            _w0 = V.DISCONNECT_STATS.get("write", 0)
            _h1 = _mk_handler(_dead_sock())
            _ret, _err = None, ""
            try:
                _ret = _h1._send(200, _BIG, "application/octet-stream")
            except BaseException as _e:  # noqa: BLE001 —— 抛了就是 FAIL，要拿到类型
                _err = f"{type(_e).__name__}: {_e}"
            _w1 = V.DISCONNECT_STATS.get("write", 0)
            rep.add(_ret is False and not _err and _w1 == _w0 + 1,
                    "H9d 写响应时断连 ⇒ `_send` 返回 False、不抛、并计入 `write` 断连统计",
                    f"返回={_ret!r} · 异常={_err or '（无）'} · write 计数 {_w0}→{_w1}")

            # ---- H9e：【可红性】同一构造下**裸 sendall** 必抛 ⇒ H9d 非真空 ----
            #   没有这条，H9d 在「socket 其实没断」时也会因为某种其它原因返回 False，
            #   而没人能发现（铁律 #100：注入已知缺陷必须变红）。
            _naked = ""
            try:
                _dead_sock().sendall(_BIG)
            except BaseException as _e:  # noqa: BLE001 —— 抓的就是"确实会抛"
                _naked = type(_e).__name__
            rep.add(_naked in ("BrokenPipeError", "ConnectionResetError",
                               "ConnectionAbortedError"),
                    "H9e 【可红性】同一构造下**裸 sendall** 必抛 ConnectionError "
                    "⇒ H9d 的 False 是**捕获**的结果，不是真空",
                    f"裸写抛出：{_naked or '（未抛出 ⇒ H9d 可能真空）'}")

            # ---- H9f：断连后不得继续读下一个请求（否则会空转/重复记日志）----
            rep.add(getattr(_h1, "close_connection", False) is True,
                    "H9f 断连后 `close_connection=True`（不再在同一连接上读下一个请求）",
                    f"close_connection={getattr(_h1, 'close_connection', None)!r}")

            # ---- H9g：【负对照】**非断连**异常必须照旧打完整栈 ----
            #   防止「一刀切静音」把真 bug 的可诊断性一起修掉（铁律 #67 的反面）。
            _stub = V.VizServer.__new__(V.VizServer)      # 不 bind，只借 `handle_error`
            _ebuf = io.StringIO()
            try:
                raise ValueError("H9g-sentinel")
            except ValueError:
                with contextlib.redirect_stderr(_ebuf):
                    _stub.handle_error(None, ("127.0.0.1", 1))
            _etxt = _ebuf.getvalue()
            _has_tb = "Traceback (most recent call last)" in _etxt
            rep.add(_has_tb and "H9g-sentinel" in _etxt,
                    "H9g 【负对照】非断连异常仍走 `handle_error` 打完整栈"
                    "（断连静音**不外溢**到真异常）",
                    f"stderr {len(_etxt)} 字符 · 含 traceback={_has_tb} · "
                    f"含哨兵={'H9g-sentinel' in _etxt}")
        except Exception as e:  # noqa: BLE001
            rep.skip("H8 冷启动端到端", f"子进程起服务失败：{e}")
        finally:
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=10)
                except Exception:  # noqa: BLE001
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
            try:
                _logf.close()
                os.unlink(_logf.name)
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------------- V 段
    rep.section("[V] 验收映射（V8 / V9 / V1 的**前端侧**证据）")
    rep.add("st-ok" in html and "st-truncated" in html and "st-timeout" in html
            and "st-error" in html and "st-no-result" in html,
            "V8a 五态在页面 CSS 上各自成类（不只靠颜色）", "")
    rep.add(all(k in js_src["panels"] for k in ("st-icon", "st-label", "statusIconSvg")),
            "V8b 五态渲染含内联 SVG 图标 + 文字标签（三重编码）—— 颜色只是三通道之一",
            "由 js/panels.js 的 renderStatusLine 生成")
    rep.add("api.bundle()" in js_src["main"] and "ENDPOINTS.bundleGraph" not in js_src["main"],
            "V1a main.js 只经 api.bundle() 读整图（不直接拼 graph_data.json 字面量）", "")
    rep.add('det.mode === "bundle"' in js_src["main"],
            "V1b 整图加载被 `mode === \"bundle\"` 分支包裹（live 模式走不到）", "")
    rep.add("bundle" in js_src["main"],
            "V9a 页面保留 mode=bundle 分支（离线样例链路）", "")
    rep.add(True,
            "V9b 发布样例链路由 `bash sttp.sh release`（28 条）/`release-full` 覆盖 —— "
            "本脚本**不替代**", "见回归")

    # V11 ★ 路径 `no_result` 的文案**不得**把「被可信度档过滤掉了」说成「不存在」。
    #   实测（2026-10-15，`MX:chem:co2 → MX:chem:combustion`，max_hops=5）：
    #     `trust=verified`（路径栏默认）⇒ **no_result**；`trust=all` ⇒ 找到 **3** 条路径（随后 timeout）。
    #   ⇒ "有没有路径"这个结论**对 trust 档敏感**；只说"确实没有路径"会把「被隐藏」
    #     报告成「不存在」—— 正是本项目最反对的那类结论。
    #   处方：`no_result` 分支须报出 `hidden_by_trust` 并提示可切档（静态断言，因 `main.js`
    #   依赖真实 DOM/画布，mjs 不加载它；行为证据见报告 §1.5 与 F 段）。
    _main_src = js_src.get("main", "")
    _v11 = ("no_result" in _main_src and "hidden_by_trust" in _main_src
            and "可信度" in _main_src)
    rep.add(_v11,
            "V11 路径 `no_result` 文案报出被隐藏边数并提示切可信度档（不得把「被过滤」说成「不存在」）",
            "已覆盖" if _v11 else "缺：no_result 分支未提及 hidden_by_trust/可信度档")

    # V12 ★ 路径结论文案的**单一来源**（Phase 51 / D50）。
    #   起因（真浏览器实测）：任务卡那条链路只显示「任务卡路径查询：无结果」，
    #   而底部路径栏会报出被隐藏边数与下一步动作 ⇒ **同一个结论，两种解释**。
    #   两处各写一套 = 把"这个结论对可信档/跳数敏感"这条认识论责任复制两份，
    #   迟早只修一份（D48 是同族教训：发送侧改了、校验侧没改）。
    #   处方：文案只能有一处字面量（`低可信度边` 恰好出现 1 次），
    #         且 `runPath` 与 `runTask` 都必须调用 `pathOutcomeText()`。
    _n_lit = _main_src.count("低可信度边")
    _n_calls = _main_src.count("pathOutcomeText(")
    _v12 = (_main_src.count("function pathOutcomeText(") == 1 and _n_lit == 1
            and _n_calls >= 3)      # 1 处定义调用 + 2 处使用
    rep.add(_v12,
            "V12 路径结论文案单一来源（两处调用同一 helper；`no_result` 文案字面量只许有 1 份）",
            "pathOutcomeText 定义 1 处 / 引用 %d 处 / 「低可信度边」字面量 %d 处"
            % (_n_calls, _n_lit))

    # V13 ★ 路径栏**不得自组请求参数**（Phase 56 / B1；外部评审 2026-10-17 §5）。
    #   实况：`runPath()` 只发 mode/seeds/max_paths/max_hops/deadline_ms，其中
    #     · 不读左侧「可信度」⇒ 后端 `viz_server.py` 用默认 trust="verified"；
    #     · `max_hops` 写死 `HARD.hops`（=5）⇒ 不读左侧跳数。
    #   而它紧邻的 `pathOutcomeText()` 明写「可把可信度切到 all 再试」「可增大跳数」
    #   ⇒ **文案指导用户做一件代码做不到的事**（与 Phase 55 D64 同族）。
    #   V12 守的是\"文案只有一份\"，恰好**帮不到**这里 —— 文案单份、且单份地骗人。
    #   故本断言改守**调用形状**：runPath 必须用 `taskRequest(`，
    #   且体内不得出现内联对象字面量形式的 `.subgraph({…})`（那就是自组参数的形状）。
    _rp_start = _main_src.find("async function runPath(")
    _rp_next = _main_src.find("\nasync function ", _rp_start + 1) if _rp_start >= 0 else -1
    _rp = _main_src[_rp_start:_rp_next if _rp_next > 0 else len(_main_src)] if _rp_start >= 0 else ""
    # ★ 必须匹配**调用形状** `= taskRequest(`，不能只找字面量 `taskRequest(`：
    #   本函数体里就有一句注释写着「统一走 `taskRequest()`」——
    #   第一版判据被这句**注释**满足了，注入"回到自组参数"的缺陷后仍然 PASS（R12 当场抓出）。
    #   这就是"名字出现过 ≠ 真的调用了"（#83/#100）。
    _rp_call = re.search(r"=\s*taskRequest\(", _rp) is not None
    _v13 = (bool(_rp) and _rp_call
            and re.search(r"\.subgraph\(\s*\{", _rp) is None
            and "trust" in _rp)
    rep.add(_v13,
            "V13 路径栏复用 `taskRequest()` 构造参数，不自组 subgraph 参数（B1：文案说的切档/加跳数必须真能生效）",
            "runPath 体 %d 字符｜调用形状 `= taskRequest(`=%s｜内联 .subgraph({…})=%s｜含 trust=%s"
            % (len(_rp), "有" if _rp_call else "无",
               "有（错）" if re.search(r"\.subgraph\(\s*\{", _rp) else "无",
               "是" if "trust" in _rp else "否"))

    # V14 ★ 路径结论须报出**本次实际用的**参数（Phase 56 / B1 的配套）。
    #   本函数的注释早就写着"这个结论对可信档与跳数都敏感"，却从不报出这两个值
    #   ⇒ "没路径"到底是"真没有"还是"档/跳数不够"，用户只能猜。
    #   且路径栏改走界面参数后，默认跳数由写死的 5 变成面板的 2 ——
    #   **不报出实际值，这个变化就是静默的**（会被读成"图退化了"）。
    _v14 = ("function pathOutcomeText(env, paths, ctx)" in _main_src
            and "本次参数：可信档" in _main_src
            and _main_src.count("pathOutcomeText(env, paths, {") >= 1)
    rep.add(_v14,
            "V14 路径结论报出本次实际参数（可信档/跳数），不让人靠猜判断 no_result 的成因",
            "签名带 ctx=%s｜文案含「本次参数：可信档」=%s"
            % ("是" if "ctx)" in _main_src else "否",
               "是" if "本次参数：可信档" in _main_src else "否"))

    # ---------------------------------------------------------------- G 段
    # D25（步骤 B 干净检出实测发现并修复）：部分命令的断言以**不入库**的权威图
    # （`06_PoC/etl/normalized.json` / `physicsbabel_circuits.parquet`）为前提，
    # 在裸 clone 里原先抛**裸 `FileNotFoundError`（RC=1）** ⇒ 评审者会误读成「代码坏了」。
    # 已统一改为「明确原因 + 不适用清单 + RC=2」（与 `subgraph`/`release` 既有口径一致）。
    # 本段是**防回退**断言（静态；真正的行为证据是裸 clone 实测，见报告 §9）。
    rep.section("[G] 干净检出可用性守卫（D25 防回退；铁律 #53/#63）")
    sh_src = read(os.path.join(ROOT, "sttp.sh"))
    # ★ 2026-10-20（Phase 62）补全：`gradepersist` / `symbolbackfill` / `domainbackfill`
    #   三个**早就挂了守卫**却不在名单里 —— 断言漏检 ⇒ 它们将来丢了守卫也不会有人发现。
    #   ★ 更强的一致性要求：名单必须**恰好等于** sttp.sh 里实际挂守卫的命令集合，
    #     否则「新增命令忘了挂守卫」与「挂了守卫忘了进名单」两种漏法都查不出来。
    _NEED_GUARD = ["stats", "t7", "noise", "scopeaudit", "gatecheck",
                   "eval", "gnneval", "dimaudit", "lineage",
                   "gradepersist", "symbolbackfill", "domainbackfill", "evdeps",
                   "applicabackfill", "papermentionbackfill", "docnums",
                   "chembackfill", "dataset", "elementrebuild", "mentionalign", "reverify"]
    #   （`evdeps` 是 G2b 加上后**立刻抓出来**的：它早挂了守卫，只是没人把它记进名单）
    #   （`papermentionbackfill` 同理，Phase 68 新增命令挂了守卫却漏登记 ——
    #     G2b 第二天就把它报出来了：这正说明"挂了守卫"和"断言看得见"是两件事）
    #   （`chembackfill` 是 Phase 69 新增时**主动补进来的** —— 上一次的教训是不必等 G2b 来报）
    #   （`dataset` 同理，Phase 70 F5 新增：它读权威快照建数据集，缺图必须 RC=2 而非裸 traceback）
    #   （`elementrebuild` 同理，Phase 71 新增：它读写权威快照，缺图必须 RC=2 而非裸 traceback）
    #   （`mentionalign` 同理，Phase 72 新增：同上，且它按重算对齐档位，缺图无法判定）

    def _guard_probe(text):
        has_def = "require_authority()" in text
        has_reason = ("不可用" in text) and ("exit 2" in text)
        miss = [c for c in _NEED_GUARD if ("require_authority %s" % c) not in text]
        # 反向：实际挂了守卫却**不在名单里**的 —— 这类漏检最安静（守卫在，断言不看它）
        actual = set(re.findall(r"^\s*require_authority (\w+)", text, re.M))
        extra = sorted(actual - set(_NEED_GUARD))
        return has_def, has_reason, miss, extra

    _g1, _g2, _gmiss, _gextra = _guard_probe(sh_src)
    rep.add(_g1, "G1 sttp.sh 定义 require_authority 前置守卫（缺权威图 ⇒ 明确原因而非裸 traceback）", "")
    rep.add(not _gmiss, "G2 %d 个「需权威图」命令均已挂守卫（漏一个 ⇒ 该命令在裸 clone 退回裸 traceback）"
            % len(_NEED_GUARD),
            ("缺: " + ",".join(_gmiss)) if _gmiss else "%d/%d" % (len(_NEED_GUARD), len(_NEED_GUARD)))
    rep.add(not _gextra, "G2b 名单 == sttp.sh 实际挂守卫的集合（**双向**："
            "「新增命令漏挂」与「挂了没进名单」两种漏法都查得出来）",
            ("实际有但名单无: " + ",".join(_gextra)) if _gextra else "双向一致")
    rep.add(_g2, "G3 守卫以**明确原因 + RC=2** 退出（不冒充成功、不冒充代码缺陷）", "")
    # 可红性：把守卫整体抹掉后，同一探针必须报「无定义 + 有缺失」
    _mut = sh_src.replace("require_authority() {", "REMOVED_GUARD() {", 1).replace("require_authority ", "removed_guard ")
    _m1, _m2, _mmiss, _mextra = _guard_probe(_mut)
    rep.add((not _m1) and bool(_mmiss) and len(_mmiss) == len(_NEED_GUARD),
            "G4 本段**可红**：抹掉守卫后 G1/G2 必须变红（断言非常真）", "")

    print("-" * 78)
    rep.section("[本静态仪器未覆盖 · 已由别的仪器接管或如实登记]")
    # ★ 2026-10-15（Phase 51）口径订正：
    #   本节原先写「本项目零第三方依赖、无 Playwright/jsdom ⇒ 真实浏览器渲染未实测」。
    #   **这句话有两处不准确**：
    #     ① 零第三方依赖约束的是**产品**（`06_PoC/js/` 无构建链），**不约束测试仪器**；
    #        `06_PoC/tests/a4_offline_render.py` 一直在用 Playwright，本环境 Playwright
    #        与 Chromium 均可用。
    #     ② 「未实测」只是**当时没有仪器**，不是"不能测"。
    #   现在：交互链路由 `bash sttp.sh taskflow`（真浏览器 B1–B5）覆盖，本段只列**仍然**
    #   没人覆盖的部分 —— 不许用"环境不允许"当挡箭牌（铁律 #34 负结果优先）。
    rep.skip("D14 布局 / 像素级断言（元素位置、视觉重叠、响应式断点）",
             "交互链路已由 `bash sttp.sh taskflow` 真浏览器覆盖；"
             "但**像素/布局**仍无断言 —— 需要截图比对基线，本轮未做")
    rep.skip("V8c 五态在**浏览器计算样式**下的实际颜色",
             "已由 X2 保证 CSS↔JS 同源；「浏览器里看起来是否真的可区分」未做像素级验证")

    rep.dump()

    if json_out:
        payload = {
            "schema": "sttp-phase43-frontend-selfcheck/v1",
            "generated_by": "06_PoC/tests/phase43_frontend_selfcheck.py",
            "n_pass": rep.n_pass, "n_fail": rep.n_fail, "n_skip": rep.n_skip,
            "rows": [{"kind": k, "name": n2, "detail": d} for k, n2, d in rep.rows],
        }
        with io.open(json_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"  （留痕已写入 {os.path.relpath(json_out, ROOT)}）")

    return 0 if rep.n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
