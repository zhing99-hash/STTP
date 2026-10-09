"""
A4 · CDN 依赖本地化（vendoring）

把 graph_view.html 依赖的三个前端库下载到 06_PoC/vendor/，使可视化在**完全断网**时也能正常渲染。

依赖清单：
  cytoscape        —— 图渲染内核
  cytoscape-fcose  —— 力导布局（可选，缺失时降级为内置布局）
  mathjax (tex-svg)—— LaTeX 渲染（选 SVG 输出而非 CHTML：SVG 输出把字形路径内嵌，无需额外字体文件，
                      单文件即自包含；CHTML 需另拉 30+ 个 woff 字体文件）

用法：
  python 06_PoC/vendor/fetch_vendor.py            # 缺失才下载
  python 06_PoC/vendor/fetch_vendor.py --force    # 强制重下
  python 06_PoC/vendor/fetch_vendor.py --verify   # 只校验现有文件
"""
import argparse
import hashlib
import os
import ssl
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (compatible; STTP-vendor-fetch/1.0)"

# (本地文件名, CDN URL, 说明, 体积下限字节)
# ⚠️ 加载顺序即数组顺序：layout-base -> cose-base -> cytoscape-fcose
#    （fcose 的 UMD 工厂链：factory(root.layoutBase) -> root.coseBase -> root.cytoscapeFcose）
VENDOR = [
    (
        "cytoscape.min.js",
        "https://cdn.jsdelivr.net/npm/cytoscape@3.30.2/dist/cytoscape.min.js",
        "cytoscape 3.30.2 (图渲染内核)",
        300_000,
    ),
    (
        "layout-base.js",
        "https://cdn.jsdelivr.net/npm/layout-base@2.0.1/layout-base.js",
        "layout-base 2.0.1 (依赖链最底层；注册 window.layoutBase)",
        30_000,
    ),
    (
        "cose-base.js",
        "https://cdn.jsdelivr.net/npm/cose-base@2.2.0/cose-base.js",
        "cose-base 2.2.0 (注册 window.coseBase)",
        100_000,
    ),
    (
        "cytoscape-fcose.js",
        "https://cdn.jsdelivr.net/npm/cytoscape-fcose@2.2.0/cytoscape-fcose.js",
        "cytoscape-fcose 2.2.0 (力导布局；必须在 cose-base 之后加载)",
        50_000,
    ),
    (
        "tex-svg.js",
        "https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-svg.js",
        "MathJax 3.2.2 tex-svg (LaTeX -> SVG，自包含)",
        800_000,
    ),
]

FALLBACK_HOSTS = [
    "https://cdn.jsdelivr.net",
    "https://fastly.jsdelivr.net",
    "https://unpkg.com",
]


def _opener(use_proxy: bool):
    ctx = ssl.create_default_context()
    if use_proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler(), urllib.request.HTTPSHandler(context=ctx))
    # 直连：显式禁用代理
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))


def _swap_host(url: str, host: str) -> str:
    """把 URL 的 host 换成备用 CDN（unpkg 的路径规则不同，单独处理）。"""
    tail = url.split("//", 1)[1].split("/", 1)[1]
    if host.endswith("unpkg.com"):
        # unpkg: /npm/pkg@ver/path  ->  /pkg@ver/path
        if tail.startswith("npm/"):
            tail = tail[len("npm/"):]
    return host + "/" + tail


def download(url: str, timeout: int = 60):
    """先直连，失败再走系统代理；每个 host 都试一遍。最后一路带回显。"""
    last = None
    for use_proxy in (False, True):
        op = _opener(use_proxy)
        for host in FALLBACK_HOSTS:
            u = _swap_host(url, host)
            try:
                req = urllib.request.Request(u, headers={"User-Agent": UA})
                with op.open(req, timeout=timeout) as r:
                    data = r.read()
                if data:
                    return data, u
                last = RuntimeError(f"empty body from {u}")
            except Exception as e:  # noqa: BLE001
                last = e
    raise RuntimeError(f"全部下载通道失败: {last}")


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def verify_one(fname: str, min_bytes: int, quiet: bool = False) -> bool:
    p = os.path.join(HERE, fname)
    if not os.path.exists(p):
        if not quiet:
            print(f"  [缺] {fname}")
        return False
    sz = os.path.getsize(p)
    ok = sz >= min_bytes
    if not quiet:
        print(f"  [{'OK' if ok else '小'}] {fname:24s} {sz:>9,} B  sha256={sha256(open(p,'rb').read())}")
    return ok


def main():
    ap = argparse.ArgumentParser(description="STTP 前端依赖本地化")
    ap.add_argument("--force", action="store_true", help="强制重新下载")
    ap.add_argument("--verify", action="store_true", help="只校验，不下载")
    args = ap.parse_args()

    os.makedirs(HERE, exist_ok=True)

    if args.verify:
        print("== 校验 vendor 目录 ==")
        allok = all(verify_one(f, m) for f, _u, _d, m in VENDOR)
        print("结果:", "全部就绪" if allok else "存在缺失/异常")
        return 0 if allok else 1

    print("== A4 前端依赖本地化 ->", HERE, "==")
    ok_cnt = 0
    for fname, url, desc, min_bytes in VENDOR:
        dst = os.path.join(HERE, fname)
        if os.path.exists(dst) and not args.force:
            sz = os.path.getsize(dst)
            if sz >= min_bytes:
                print(f"  [跳过] {fname:24s} {sz:>9,} B  ({desc})")
                ok_cnt += 1
                continue
        try:
            data, used = download(url)
        except Exception as e:  # noqa: BLE001
            print(f"  [失败] {fname:24s} {e}")
            continue
        with open(dst, "wb") as f:
            f.write(data)
        flag = "OK" if len(data) >= min_bytes else "偏小(需复核)"
        print(f"  [{flag}] {fname:24s} {len(data):>9,} B  sha256={sha256(data)}  <- {used}")
        if len(data) >= min_bytes:
            ok_cnt += 1

    print(f"\n完成：{ok_cnt}/{len(VENDOR)} 项就绪")
    return 0 if ok_cnt == len(VENDOR) else 1


if __name__ == "__main__":
    raise SystemExit(main())
