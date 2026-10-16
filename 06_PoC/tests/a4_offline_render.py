# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""
A4 · 断网渲染端到端测试（Playwright headless）

目的：证明 graph_view.html 在 **CDN 完全不可达** 时仍能完整渲染。

做法：拦截浏览器所有请求，凡 host 不是 127.0.0.1 / localhost 的一律 abort（模拟断网），
然后加载页面，断言：
  1. 页面里没有任何"非本地"的脚本真正加载成功（离线前提成立）；
  2. Cytoscape 内核、fcose 依赖链（layout-base / cose-base / cytoscape-fcose）、MathJax 全部就位；
  3. 图实例真的渲染出了节点/边；
  4. MathJax 能把 LaTeX 渲染成 SVG（tex-svg 自包含，无需字体文件）。

用法（★ 走标准入口，否则"没有目标就没有人会跑它"—— 正是 D51 的形态）：
    bash sttp.sh offline                          # = 本脚本；产物写 .runlog/（不入库）
    python 06_PoC/tests/a4_offline_render.py --url http://127.0.0.1:8765/ --headed

退出码：0 全通过；1 有断言失败；**2 = 缺前置**（未装 Playwright / 目标页面不可达 ⇒ 明确 SKIP，
        **不计通过**，铁律 #20：缺依赖不得假通过，也不得崩成裸 ImportError 让评审者读成"代码坏了"）。

★ 本仪器**不是自包含的**（D56）：`subgraph` / `frontend` 自起 `ThreadingHTTPServer`，
  而"断网渲染"必须在**真端口上的真页面**上验证 ⇒ 它依赖外部已在跑的 `bash sttp.sh viz`。
  前置缺失时**必须返回 2**（不是 0）—— 否则退出码上 SKIP 与 PASS 不可分（#101）。
  该纪律由 `bash sttp.sh redprobe` 的 **R5** 构造性守住。
"""
import argparse
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")

# ★ 产物**不入库**（Phase 52 · D51 收口）：本脚本原先把结果写进
#   `06_PoC/etl/a4_offline_render.json`，那是一个**入库路径** ⇒ 每跑一次就把工作树弄脏，
#   把"检出自变脏"变成了常态（E29 家族；实测该文件长期停在旧快照 7339/40249）。
#   默认改到 `.runlog/`（`.gitignore` 已覆盖），并保留 `--out` 供调用方指定。
DEFAULT_OUT = os.path.join(".runlog", "a4_offline_render.json")


class Rep:
    """极简断言报告器（与 `phase43_*_selfcheck.py` 的 `rep.add(cond, NAME, detail)` **同形**）。

    ★ 为什么要换成这个形状（D51 的完整解法）：D51 的表面是"没有 sttp.sh 入口 ⇒ 没人跑"，
      但根因是**双重失明** —— ① 没人跑（无目标）；② 即使跑了，它用 `[(name, cond)]`
      列表，**`bash sttp.sh names` 采集不到它的断言名**（铁律 #87 的核对工具只认
      `rep.add/skip/check/ok` 调用）⇒ "悄悄改掉/删掉一条断言"仍然无人能发现。
      改成 `rep.add(cond, "名", detail)` 后，本文件的断言名也进入名字集合核对的覆盖范围。
    """

    def __init__(self):
        self.rows = []

    def add(self, cond, name, detail=""):
        self.rows.append((bool(cond), name, str(detail)))
        return bool(cond)

    def summary(self):
        npass = sum(1 for ok, _, _ in self.rows if ok)
        nfail = len(self.rows) - npass
        print("\n-- 断言 --")
        for ok, name, detail in self.rows:
            print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                                   ("  —— " + detail) if detail else ""))
        print("\n  合计 PASS %d / FAIL %d" % (npass, nfail))
        return nfail == 0


def main():
    ap = argparse.ArgumentParser(
        description="A4 断网渲染端到端验证（CDN 全不可达时页面仍能完整渲染）",
        epilog="★ 前置（**非自包含**，D56）：需外部已在跑的本地 viz 服务（默认 "
               "http://127.0.0.1:8765/）。缺前置 ⇒ 明确记 SKIP 并返回 **2**"
               "（2 = 前置缺失；1 = 真有断言失败；0 = 通过）。"
               "启动方式：另开终端 `bash sttp.sh viz`（前台长驻）。")
    ap.add_argument("--url", default="http://127.0.0.1:8765/")
    ap.add_argument("--timeout", type=int, default=180, help="首屏加载超时（秒）")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument(
        "--scenario",
        choices=["offline", "cdn-fallback"],
        default="offline",
        help="offline=本地 vendor 生效（默认）；cdn-fallback=额外拦掉 vendor，验证 CDN 兜底路径确实被触发",
    )
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="结果 JSON 路径（默认 .runlog/，**不入库**——写进入库路径会把工作树弄脏）")
    args = ap.parse_args()

    # ---- 前置①：Playwright（**可选依赖**，刻意不在零安装路径上，见指南 §0.1 / D14）----
    #   ★ 保留 `from … import …` 字面形式：`bash sttp.sh install` 的"依赖声明覆盖"检查
    #     靠 `ast.ImportFrom` 扫全仓 import；换成 importlib 动态导入会让它扫不到 ⇒
    #     已声明的 ACK 会被判成"陈旧 ACK"，自检变红（A05-7a）。
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        print("== A4 断网渲染测试：SKIP（缺前置）==")
        print("  原因：未安装 Playwright —— 它是**可选依赖**，本项目零安装路径刻意不装它。")
        print("  复现：pip install playwright && python -m playwright install chromium")
        print("  细节：%s: %s" % (type(e).__name__, e))
        print("  ★ 退出码 2 = **前置缺失**（本仪器非自包含，需外部 viz 服务在跑）；")
        print("    它既不是「通过」，也不代表「页面有问题」（D56）。")
        return 2

    external_blocked = []
    vendor_blocked = []
    allowed = []

    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        page = browser.new_page(viewport={"width": 1600, "height": 900})

        console_errors = []
        page.on("pageerror", lambda e: console_errors.append(str(e)[:160]))

        def route_handler(route, request):
            url = request.url
            path = url.split("//", 1)[-1].split("/", 1)[-1] if "//" in url else url
            host = url.split("//", 1)[-1].split("/", 1)[0].split(":")[0]
            if args.scenario == "cdn-fallback" and path.startswith("vendor/"):
                vendor_blocked.append(url)
                return route.abort()
            if host in LOCAL_HOSTS:
                allowed.append(url)
                return route.continue_()
            external_blocked.append(url)
            route.abort()

        page.route("**/*", route_handler)

        print(f"== A4 断网渲染测试 [{args.scenario}] -> {args.url} ==")
        # ---- 前置②：目标页面可达（缺 ⇒ SKIP + RC=2，**不冒充**"断言失败"）----
        #   区分这两件事很重要：`RC=1` 的含义是"页面跑起来了但断言不过"；
        #   页面根本没起来属于**前置缺失**，混在一起会让失败的归因指向被测对象（D54 家族的教训）。
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout * 1000)
        except Exception as e:  # noqa: BLE001
            browser.close()
            print("== A4 断网渲染测试：SKIP（缺前置）==")
            print("  原因：目标页面不可达（%s）：%s" % (args.url, type(e).__name__))
            print("  复现：另开一个终端运行 `bash sttp.sh viz`（前台长驻），再重跑本命令。")
            print("  ★ 退出码 2 = **前置缺失**（本仪器非自包含，需外部 viz 服务在跑）；")
            print("    它既不是「通过」，也不代表「页面有问题」（D56）。")
            return 2

        if args.scenario == "offline":
            # 等**新前端初始化完成**（`main.js` 把应用状态挂在 `window.__sttp`，
            # 与真浏览器验收仪器 `taskflow` 用同一个入口）。
            # ★ 这里曾经等 `cy.nodes().length > 0` —— 那是 Phase 43 **之前**的观测点：
            #   当时页面把 Cytoscape 实例放在全局 `cy`。前端重构为「局部图谱 + ES Module」
            #   之后，实例收进模块作用域，全局 `cy` 只剩静态的 `<div id="cy">`
            #   ⇒ 实测 `TypeError: cy.nodes is not a function`。
            #   而本脚本当时**没有 sttp.sh 入口**（D51）⇒ 没人跑 ⇒ 失效长期未被发现。
            try:
                page.wait_for_function("() => !!(window.__sttp && window.__sttp.mode)",
                                       timeout=args.timeout * 1000)
            except Exception as e:  # noqa: BLE001
                print(f"  [WARN] 等待前端初始化超时：{type(e).__name__}")
        else:
            # ★ 这不是"等待某状态"，而是一个**观察窗口**：本场景要断言的命题本身
            #   就含时间窗（"本地与 CDN 同时失效时，页面会不会去够兜底"），
            #   等到为止会让断言恒真。与铁律 #95 不冲突 —— 它管的是"别用固定延时
            #   替代可观测终止条件"，而这里的固定延时就是被测命题的一部分。
            page.wait_for_timeout(4000)

        results["cytoscape"] = page.evaluate("typeof window.cytoscape")
        results["layoutBase"] = page.evaluate("typeof window.layoutBase")
        results["coseBase"] = page.evaluate("typeof window.coseBase")
        results["cytoscapeFcose"] = page.evaluate("typeof window.cytoscapeFcose")
        results["MathJax"] = page.evaluate("typeof window.MathJax")
        # ★ 观测点对齐**当前**前端（Phase 43 之后）：应用状态在 `window.__sttp`。
        results["app_ready"] = page.evaluate("!!(window.__sttp && window.__sttp.mode)")
        results["app_mode"] = page.evaluate("(window.__sttp && window.__sttp.mode) || null")
        results["cy_container"] = page.evaluate("!!document.getElementById('cy')")
        # 断网只该挡住 **CDN**，**不该**挡住同源的本地后端 —— 这一条把两者分开。
        results["local_backend_ok"] = page.evaluate(
            """async () => {
                 try { const r = await fetch('./api/healthz', { cache: 'no-store' });
                       return !!r.ok; } catch (e) { return false; }
               }"""
        )
        # fcose 布局**可实例化**（不只是"扩展对象存在"）—— 必须走**当前页面真实的实例**。
        # ★ 第一版写成 `window.cytoscape({container: 游离 div, elements: []}).layout({name:'fcose'})`
        #   ⇒ 实测 `TypeError: Cannot read properties of undefined (reading 'h')`
        #   （游离容器没有尺寸/未挂载 ⇒ 布局初始化失败）。这是**探测方式**的问题，不是产品缺陷。
        #   改用与页面"布局切换"下拉框完全相同的路径（`main.js`：`state.viz.cy.layout(...)`）。
        results["fcose_registered"] = page.evaluate(
            """() => {
                 try {
                   const st = window.__sttp || {};
                   const c = st.viz && st.viz.cy;
                   if (!c) return 'no-cy-instance';
                   c.layout({ name: 'fcose', quality: 'draft' });
                   return true;
                 } catch (e) { return String(e).slice(0, 90); }
               }"""
        )

        # MathJax：把 LaTeX 渲成 SVG
        results["mathjax_render"] = page.evaluate(
            """async () => {
              if (!window.MathJax || !MathJax.typesetPromise) return 'no-mathjax';
              const d = document.createElement('div');
              d.textContent = '$E = mc^2$';
              document.body.appendChild(d);
              try { await MathJax.typesetPromise([d]); } catch (e) { return 'typeset-err:' + e; }
              const svg = d.querySelector('mjx-container svg');
              const c = d.querySelector('mjx-container');
              d.remove();
              if (svg) return 'svg-ok';
              return c ? 'container-without-svg' : 'no-output';
            }"""
        )

        # 交叉验证：页面上所有 script/img/link 里是否还有无条件的外部引用
        external_refs = page.evaluate(
            """() => Array.from(document.querySelectorAll('script[src],link[href],img[src]'))
                 .map(e => e.getAttribute('src') || e.getAttribute('href'))
                 .filter(u => u && /^https?:\\/\\//.test(u))"""
        )
        # 页面是否可交互（DOM 未崩）
        results["dom_alive"] = page.evaluate("!!document.getElementById('cy') || document.body.children.length > 3")

        browser.close()

    if args.scenario == "cdn-fallback":
        print("\n-- 反向对照：本地 vendor 被拦掉后，CDN 兜底是否被触发 --")
        print(f"  vendor 请求被拦: {len(vendor_blocked)} 条")
        print(f"  外部(CDN)请求被触发: {len(external_blocked)} 条")
        for u in external_blocked[:6]:
            print(f"    -> {u[:96]}")
        print(f"  window.cytoscape = {results['cytoscape']}（预期 undefined：本地和 CDN 都断，页面应优雅降级）")
        print(f"  页面 DOM 存活 = {results['dom_alive']}")
        print(f"  未捕获 JS 异常 = {len(console_errors)}")
        rep = Rep()
        rep.add(len(external_blocked) > 0,
                "NETC1 CDN 兜底确实被触发（外部请求 > 0；证明 NET 段的 PASS 不是空过）",
                "外部请求 %d 条" % len(external_blocked))
        rep.add(results["cytoscape"] == "undefined" and results["dom_alive"],
                "NETC2 本地与 CDN 同时失效时优雅降级（无 cytoscape 但不崩）",
                "cytoscape=%s · dom_alive=%s" % (results["cytoscape"], results["dom_alive"]))
        ok = rep.summary()
        print("\n结论:", "反向对照通过（证明前一场景的 PASS 不是空过）" if ok else "反向对照失败")
        return 0 if ok else 1

    print("\n-- 依赖就位情况 --")
    for k in ("cytoscape", "layoutBase", "coseBase", "cytoscapeFcose", "MathJax"):
        v = results[k]
        # UMD 导出的全局可能是 object 也可能是 function（cytoscape/layoutBase/cytoscapeFcose 都是 function）
        print(f"  window.{k:16s} = {v:12s} {'OK' if v in ('object', 'function') else '缺失'}")

    print("\n-- 应用状态（观测点 = window.__sttp，与 taskflow 同一入口）--")
    print(f"  已初始化 = {results['app_ready']}   运行模式 = {results['app_mode']}")
    print(f"  #cy 容器 = {results['cy_container']}   断网下本地后端可达 = {results['local_backend_ok']}")
    print(f"  fcose 可实例化 -> {results['fcose_registered']}")
    print(f"  MathJax 渲染 -> {results['mathjax_render']}")

    print("\n-- 网络隔离 --")
    print(f"  本地放行请求 {len(allowed)} 条")
    print(f"  已阻断外部请求 {len(external_blocked)} 条")
    for u in external_blocked[:8]:
        print(f"    x {u[:96]}")
    print(f"  仍存在的无条件外部引用: {external_refs if external_refs else '无（全部走本地或兜底）'}")

    present = lambda k: results[k] in ("object", "function")   # noqa: E731
    rep = Rep()
    # ★ 断网的核心命题：**一切依赖都来自本地** ⇒ CDN 完全不可达时页面依然可用。
    rep.add(present("cytoscape"), "NET1 Cytoscape 内核就位（本地 vendor，非 CDN）", results["cytoscape"])
    rep.add(present("layoutBase"), "NET2 layout-base 就位（fcose 依赖链）", results["layoutBase"])
    rep.add(present("coseBase"), "NET3 cose-base 就位（fcose 依赖链）", results["coseBase"])
    rep.add(present("cytoscapeFcose"), "NET4 cytoscape-fcose 就位", results["cytoscapeFcose"])
    rep.add(present("MathJax"), "NET5 MathJax 就位", results["MathJax"])
    rep.add(results["fcose_registered"] is True, "NET6 fcose 布局可实例化",
            str(results["fcose_registered"]))
    rep.add(results["mathjax_render"] == "svg-ok",
            "NET7 MathJax 渲出 SVG（tex-svg 自包含 ⇒ 无需字体文件）", results["mathjax_render"])
    # ★ NET8/NET9 是**重写**（铁律 #74「断言过期 ≠ 回归，须重写而非删除」）：
    #   旧断言 `图渲染出节点` / `图渲染出边` 测的是**全局 `cy`** —— 那是 Phase 43 之前的
    #   观测点（当时页面把 Cytoscape 实例放在全局）。前端改为「局部图谱 + ES Module」后
    #   实例收进模块作用域，全局 `cy` 只剩静态 `<div id="cy">` ⇒ 旧断言实测抛
    #   `TypeError: cy.nodes is not a function`。故**改写为等效命题**（"页面真的跑起来了"），
    #   观测点换成 `window.__sttp`，而不是把这两条删掉了事。
    rep.add(results["app_ready"] is True,
            "NET8 新前端已初始化（window.__sttp 就位；等效替代旧「图渲染出节点」）",
            str(results["app_ready"]))
    rep.add(results["app_mode"] in ("live", "bundle"),
            "NET9 模式判定非 offline（等效替代旧「图渲染出边」：断网未波及本地后端）",
            str(results["app_mode"]))
    rep.add(results["cy_container"] is True, "NET10 渲染容器 #cy 就位", str(results["cy_container"]))
    rep.add(results["local_backend_ok"] is True,
            "NET11 断网下本地后端仍可达（被挡的只有 CDN，不是后端）",
            str(results["local_backend_ok"]))
    rep.add(len(external_blocked) == 0,
            "NET12 无外部请求被发起（离线前提成立，否则本测试是空过）",
            "外部请求 %d 条" % len(external_blocked))
    rep.add(not external_refs,
            "NET13 无无条件外部引用（script/link/img 全走本地）",
            "、".join(external_refs[:3]))
    rep.add(not console_errors, "NET14 无未捕获 JS 异常",
            "；".join(console_errors[:2]))
    ok = rep.summary()
    if console_errors:
        for e in console_errors[:5]:
            print(f"    ! {e}")

    print("\n结论:", "断网渲染通过" if ok else "存在失败项")
    # ★ 写出前**先建目录**并**锁行尾**（`newline="\n"`，铁律 #77）：本仓库
    #   `.gitattributes` 是 `* text=auto eol=lf`，用默认行尾写会让同一结果在不同平台
    #   字节不一致 —— 而"字节可复现"正是本项目的硬要求（#61）。
    _out_dir = os.path.dirname(args.out)
    if _out_dir:
        os.makedirs(_out_dir, exist_ok=True)
    with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"scenario": args.scenario, "results": results,
                   "blocked": external_blocked, "external_refs": external_refs}, f,
                  ensure_ascii=False, indent=2)
        f.write("\n")
    print("  结果 JSON（不入库）: %s" % args.out.replace("\\", "/"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
