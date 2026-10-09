"""
A4 · 断网渲染端到端测试（Playwright headless）

目的：证明 graph_view.html 在 **CDN 完全不可达** 时仍能完整渲染。

做法：拦截浏览器所有请求，凡 host 不是 127.0.0.1 / localhost 的一律 abort（模拟断网），
然后加载页面，断言：
  1. 页面里没有任何"非本地"的脚本真正加载成功（离线前提成立）；
  2. Cytoscape 内核、fcose 依赖链（layout-base / cose-base / cytoscape-fcose）、MathJax 全部就位；
  3. 图实例真的渲染出了节点/边；
  4. MathJax 能把 LaTeX 渲染成 SVG（tex-svg 自包含，无需字体文件）。

用法：
    python 06_PoC/tests/a4_offline_render.py
    python 06_PoC/tests/a4_offline_render.py --url http://127.0.0.1:8765/ --headed

退出码：0 全通过；1 有断言失败。
"""
import argparse
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765/")
    ap.add_argument("--timeout", type=int, default=180, help="首屏加载超时（秒）")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument(
        "--scenario",
        choices=["offline", "cdn-fallback"],
        default="offline",
        help="offline=本地 vendor 生效（默认）；cdn-fallback=额外拦掉 vendor，验证 CDN 兜底路径确实被触发",
    )
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

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
        page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout * 1000)

        if args.scenario == "offline":
            # 等图实例真正建起来（loadData 会拉 20MB graph_data.json，给足时间）
            try:
                page.wait_for_function(
                    "() => typeof cy !== 'undefined' && cy && cy.nodes().length > 0",
                    timeout=args.timeout * 1000,
                )
            except Exception as e:  # noqa: BLE001
                print(f"  [WARN] 等待图实例超时：{type(e).__name__}")
        else:
            page.wait_for_timeout(4000)

        results["cytoscape"] = page.evaluate("typeof window.cytoscape")
        results["layoutBase"] = page.evaluate("typeof window.layoutBase")
        results["coseBase"] = page.evaluate("typeof window.coseBase")
        results["cytoscapeFcose"] = page.evaluate("typeof window.cytoscapeFcose")
        results["MathJax"] = page.evaluate("typeof window.MathJax")
        results["nodes"] = page.evaluate("typeof cy !== 'undefined' && cy ? cy.nodes().length : -1")
        results["edges"] = page.evaluate("typeof cy !== 'undefined' && cy ? cy.edges().length : -1")
        results["fcose_registered"] = page.evaluate(
            "() => { try { cy.layout({ name:'fcose', quality:'draft' }); return true; }"
            " catch(e){ return String(e).slice(0,90); } }"
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
        checks = [
            ("CDN 兜底确实被触发（外部请求 > 0）", len(external_blocked) > 0),
            ("本地与 CDN 同时失效时优雅降级（无 cytoscape 但不崩）",
             results["cytoscape"] == "undefined" and results["dom_alive"]),
        ]
        print("\n-- 断言 --")
        ok = True
        for name, passed in checks:
            print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
            ok = ok and passed
        print("\n结论:", "反向对照通过 ✅（证明前一场景的 PASS 不是空过）" if ok else "反向对照失败 ❌")
        return 0 if ok else 1

    print("\n-- 依赖就位情况 --")
    for k in ("cytoscape", "layoutBase", "coseBase", "cytoscapeFcose", "MathJax"):
        v = results[k]
        # UMD 导出的全局可能是 object 也可能是 function（cytoscape/layoutBase/cytoscapeFcose 都是 function）
        print(f"  window.{k:16s} = {v:12s} {'OK' if v in ('object', 'function') else '缺失'}")

    print("\n-- 渲染结果 --")
    print(f"  节点 = {results['nodes']}   边 = {results['edges']}")
    print(f"  fcose 可用 -> {results['fcose_registered']}")
    print(f"  MathJax 渲染 -> {results['mathjax_render']}")

    print("\n-- 网络隔离 --")
    print(f"  本地放行请求 {len(allowed)} 条")
    print(f"  已阻断外部请求 {len(external_blocked)} 条")
    for u in external_blocked[:8]:
        print(f"    x {u[:96]}")
    print(f"  仍存在的无条件外部引用: {external_refs if external_refs else '无（全部走本地或兜底）'}")

    present = lambda k: results[k] in ("object", "function")
    checks = [
        ("Cytoscape 内核就位", present("cytoscape")),
        ("layout-base 就位", present("layoutBase")),
        ("cose-base 就位", present("coseBase")),
        ("cytoscape-fcose 就位", present("cytoscapeFcose")),
        ("MathJax 就位", present("MathJax")),
        ("fcose 布局可实例化", results["fcose_registered"] is True),
        ("MathJax 渲出 SVG", results["mathjax_render"] == "svg-ok"),
        ("图渲染出节点", isinstance(results["nodes"], int) and results["nodes"] > 0),
        ("图渲染出边", isinstance(results["edges"], int) and results["edges"] > 0),
        ("无外部请求被发起", len(external_blocked) == 0),
        ("无无条件外部引用", not external_refs),
        ("无未捕获 JS 异常", not console_errors),
    ]
    print("\n-- 断言 --")
    ok = True
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    if console_errors:
        for e in console_errors[:5]:
            print(f"    ! {e}")

    print("\n结论:", "断网渲染通过 ✅" if ok else "存在失败项 ❌")
    with open("06_PoC/etl/a4_offline_render.json", "w", encoding="utf-8") as f:
        json.dump({"scenario": args.scenario, "results": results,
                   "blocked": external_blocked, "external_refs": external_refs}, f,
                  ensure_ascii=False, indent=2)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
