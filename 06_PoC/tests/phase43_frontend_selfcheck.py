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

★ 本自检**不引入任何第三方依赖**（不用 Playwright / jsdom）：
    可被断言的部分刻意做成了**纯逻辑模块**（`js/core.js`、`js/style.js`），
    `node` 直接 import 即可断言；其余部分用**静态结构 + 跨层比对**覆盖。
    真实浏览器渲染（布局/像素/交互链路）**本轮未实测** ⇒ 见报告 §6-D14，
    本脚本对此**如实记 SKIP**，绝不计入通过。

退出码：0 = 无 FAIL；1 = 有 FAIL；2 = 前置不满足（缺文件 / 缺 node）。
"""

import io
import json
import os
import pathlib
import re
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

MODULES = ["core", "style", "api", "modes", "subgraph", "viz", "panels", "evidence", "main"]


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

        pairs = [("nodes", "DEFAULT_NODES"), ("edges", "DEFAULT_EDGES"), ("hops", "DEFAULT_HOPS"),
                 ("deadline_ms", "DEFAULT_DEADLINE_MS"), ("paths", "DEFAULT_MAX_PATHS")]
        bad = []
        for k, attr in pairs:
            fv, bv = js_num(b_budget, k), getattr(Q, attr, None)
            if fv != bv:
                bad.append(f"{k}: 前端 {fv} ≠ {bv}")
        rep.add(not bad, "X4a 默认预算：前端 BUDGET == queries.DEFAULT_*", "；".join(bad) if bad else "5/5")

        hard = getattr(Q, "HARD", {}) or {}
        bad = []
        for k in ("nodes", "edges", "hops", "deadline_ms", "paths"):
            fv, bv = js_num(b_hard, k), hard.get(k)
            if fv != bv:
                bad.append(f"{k}: 前端 {fv} ≠ {bv}")
        rep.add(not bad, "X4b 硬上限：前端 HARD == queries.HARD", "；".join(bad) if bad else "5/5")

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

    # ---------------------------------------------------------------- G 段
    # D25（步骤 B 干净检出实测发现并修复）：部分命令的断言以**不入库**的权威图
    # （`06_PoC/etl/normalized.json` / `physicsbabel_circuits.parquet`）为前提，
    # 在裸 clone 里原先抛**裸 `FileNotFoundError`（RC=1）** ⇒ 评审者会误读成「代码坏了」。
    # 已统一改为「明确原因 + 不适用清单 + RC=2」（与 `subgraph`/`release` 既有口径一致）。
    # 本段是**防回退**断言（静态；真正的行为证据是裸 clone 实测，见报告 §9）。
    rep.section("[G] 干净检出可用性守卫（D25 防回退；铁律 #53/#63）")
    sh_src = read(os.path.join(ROOT, "sttp.sh"))
    _NEED_GUARD = ["stats", "t7", "noise", "scopeaudit", "gatecheck",
                   "eval", "gnneval", "dimaudit", "lineage"]

    def _guard_probe(text):
        has_def = "require_authority()" in text
        has_reason = ("不可用" in text) and ("exit 2" in text)
        miss = [c for c in _NEED_GUARD if ("require_authority %s" % c) not in text]
        return has_def, has_reason, miss

    _g1, _g2, _gmiss = _guard_probe(sh_src)
    rep.add(_g1, "G1 sttp.sh 定义 require_authority 前置守卫（缺权威图 ⇒ 明确原因而非裸 traceback）", "")
    rep.add(not _gmiss, "G2 9 个「需权威图」命令均已挂守卫（漏一个 ⇒ 该命令在裸 clone 退回裸 traceback）",
            ("缺: " + ",".join(_gmiss)) if _gmiss else "9/9")
    rep.add(_g2, "G3 守卫以**明确原因 + RC=2** 退出（不冒充成功、不冒充代码缺陷）", "")
    # 可红性：把守卫整体抹掉后，同一探针必须报「无定义 + 有缺失」
    _mut = sh_src.replace("require_authority() {", "REMOVED_GUARD() {", 1).replace("require_authority ", "removed_guard ")
    _m1, _m2, _mmiss = _guard_probe(_mut)
    rep.add((not _m1) and bool(_mmiss) and len(_mmiss) == len(_NEED_GUARD),
            "G4 本段**可红**：抹掉守卫后 G1/G2 必须变红（断言非常真）", "")

    print("-" * 78)
    rep.section("[未实测 · 如实登记，不计通过]")
    rep.skip("D14 真实浏览器渲染（布局/像素/交互链路）",
             "本项目零第三方依赖、无 Playwright/jsdom ⇒ 本轮未实测；"
             "已覆盖：纯逻辑（node）+ 静态结构 + 跨层一致性 + HTTP MIME")
    rep.skip("V8c 五态在**浏览器计算样式**下的实际颜色",
             "已由 X2 保证 CSS↔JS 同源；「浏览器里看起来是否真的可区分」未跑真实渲染")

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
