# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""F5 验收的**端到端**执行：真的把目录 wget 下来，再在**断网**进程里校验。

为什么不能只跑一遍 `verify_dataset.py` 就算过
---------------------------------------------
「目录可被第三方 wget 后离线校验通过」这句话里有三个可验证的动作，缺一个都只是自述：

1. **wget 得到**：目录必须是**纯静态 + 无鉴权 + 逐文件可取**。若某个文件只在仓库里有、
   或者需要带 token、或者要先跑一段脚本才生成 —— 第三人拿到 URL 也复现不了。
2. **下载后仍是同一份**：传输可能截断/改写。所以拿下载副本的 sha256 与**源目录**比，
   而不是跟清单里登记的比（清单可能是错的，源目录是这次真的发出去的东西）。
3. **离线仍能校验**：只把「我没看到联网」当作离线是不够的。本脚本用 `sitecustomize`
   在子进程里**把 socket.connect 打断**，再跑校验器 —— 它若能跑完，就证明真的不靠网络。

外加一条**负对照**（E4）：在下载副本上改一个字节，校验器必须变红。
否则 E3 的绿可能只是「校验器什么都没查」。

下载客户端
----------
优先 `wget`，没有则 `curl`，再没有则用标准库 `urllib`（三者都是**纯 HTTP GET**，
语义等价）。用了哪个会如实打印在 E1 上 —— 本机常装的是 curl，但 README 里给第三方的
命令仍然是 `wget -r`（wget 是通用的，不该因为构建机没装就假装用了它）。

用法
----
    python 06_PoC/dataset/wget_offline_e2e.py [--dir 06_PoC/dataset/dist] [--keep]
退出码：`0` 全过 ｜ `1` 有判据红 ｜ `2` 缺前置（目录不存在 / 未构建）。
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")

# `sitecustomize` 会在解释器启动时被自动导入 ⇒ 用它当"断网开关"，不需要改被测脚本一行。
GUARD = '''# -*- coding: utf-8 -*-
"""由 wget_offline_e2e.py 注入：本进程不得访问网络。"""
import socket


class OfflineGuard(RuntimeError):
    pass


def _deny(*a, **k):
    raise OfflineGuard("OFFLINE_GUARD：本进程已断网，出现网络访问即判失败")


socket.socket.connect = _deny
socket.socket.connect_ex = _deny
socket.socket.sendto = _deny
socket.create_connection = _deny
'''


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def which(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return n, p
    return None, None


def download(client: str, url: str, dst: str) -> None:
    if client == "wget":
        r = subprocess.run(["wget", "-q", "-O", dst, url], capture_output=True)
        if r.returncode != 0:
            raise RuntimeError("wget 失败（RC=%d）：%s" % (r.returncode, r.stderr[:200]))
    elif client == "curl":
        r = subprocess.run(["curl", "-fsS", "--max-time", "60", "-o", dst, url],
                           capture_output=True)
        if r.returncode != 0:
            raise RuntimeError("curl 失败（RC=%d）：%s" % (r.returncode, r.stderr[:200]))
    else:
        with urllib.request.urlopen(url, timeout=60) as resp, open(dst, "wb") as f:
            f.write(resp.read())


def run_verifier(dataset_dir: str, guard_dir: str):
    """在**断网**子进程里跑随包校验器（cwd 指向下载目录，PYTHONPATH 只加 guard）。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = guard_dir
    env.pop("PYTHONHOME", None)
    r = subprocess.run([sys.executable, os.path.join(dataset_dir, "verify_dataset.py"), dataset_dir],
                       cwd=dataset_dir, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="F5 端到端：wget 下载 + 断网校验（Phase 70）")
    ap.add_argument("--dir", default=DIST, help="要发布的数据集目录（默认 06_PoC/dataset/dist）")
    ap.add_argument("--keep", action="store_true", help="保留下载副本（便于人工复核）")
    a = ap.parse_args(argv)

    src = os.path.abspath(a.dir)
    if not os.path.isdir(src):
        print("[前置缺失] 数据集目录不存在：%s（RC=2；先跑 `bash sttp.sh dataset`）" % src)
        return 2
    mpath = os.path.join(src, "manifest.json")
    if not os.path.isfile(mpath):
        print("[前置缺失] 缺 manifest.json（RC=2）")
        return 2
    with io.open(mpath, encoding="utf-8") as f:
        manifest = json.load(f)
    names = [f["path"] for f in manifest["files"]] + ["manifest.json", "SHA256SUMS"]

    client, path = which("wget", "curl")
    if client is None:
        client = "urllib"
    print("=" * 78)
    print("  F5 端到端：按清单逐个 HTTP GET 下载 → 断网校验（Phase 70）")
    print("=" * 78)
    print("  源目录  : %s" % src)
    print("  文件数  : %d（%s）" % (len(names), "、".join(names[:4]) + " …"))
    print("  客户端  : %s%s" % (client, ("（%s）" % path) if path else "（标准库，纯 GET）"))

    # ---- 起静态服务器（纯 http.server：无鉴权、无 JS、无路由）----
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):          # 访问日志会淹没判据行，默认静音
            pass

    handler = functools.partial(Quiet, directory=src)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    print("  服务    : http://127.0.0.1:%d/ （目录索引 + 逐文件）" % port)

    work = tempfile.mkdtemp(prefix="sttp-ds-e2e-")
    dest = os.path.join(work, "dataset")
    os.makedirs(dest)
    guard = os.path.join(work, "guard")
    os.makedirs(guard)
    with io.open(os.path.join(guard, "sitecustomize.py"), "w", encoding="utf-8") as f:
        f.write(GUARD)

    rows = []

    def add(ok, name, note=""):
        rows.append(ok)
        print("[%s] %s  %s" % ("PASS" if ok else "FAIL", name, note))

    try:
        print("-" * 78)
        # ---- E1 下载（逐个文件，纯 GET）----
        err = ""
        for n in names:
            try:
                download(client, "http://127.0.0.1:%d/%s" % (port, n),
                         os.path.join(dest, n))
            except Exception as e:                                     # noqa: BLE001
                err = "%s：%s" % (n, e)
                break
        add(not err, "E1 清单里 %d 个文件均可被纯 HTTP GET 逐个取到（客户端 %s）" % (len(names), client),
            err or "全部下载成功（无鉴权、无脚本生成、无动态路由）")

        # ---- E5 目录里没有清单外的文件漏下来 ----
        got = {n for n in os.listdir(dest) if os.path.isfile(os.path.join(dest, n))}
        add(got == set(names), "E5 下载目录文件集 == 清单文件集（不多不少）",
            "齐备 %d 个" % len(got) if got == set(names)
            else "★ 多 %s；缺 %s" % (sorted(got - set(names)), sorted(set(names) - got)))

        # ---- E2 下载副本逐字节 == 源目录 ----
        diff = [n for n in names
                if not os.path.isfile(os.path.join(dest, n))
                or sha256_file(os.path.join(dest, n)) != sha256_file(os.path.join(src, n))]
        add(not diff, "E2 下载副本逐字节等于源目录（%d 个文件逐个现算 sha256，不引用清单）" % len(names),
            "全部相同" if not diff else "★ 不同：%s" % diff[:5])

        # ---- E3 断网校验 ----
        rc, out = run_verifier(dest, guard)
        fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
        guard_hit = "OFFLINE_GUARD" in out
        add(rc == 0 and not fails and not guard_hit,
            "E3 断网（socket.connect 被 sitecustomize 打断）下随包校验器 RC=0",
            "RC=0；%d 条判据全过；未触发任何网络访问"
            % len([ln for ln in out.splitlines() if ln.startswith("[PASS]")])
            if rc == 0 and not fails else "★ RC=%d；%s" % (rc, (fails or out.splitlines()[-3:])[:3]))

        # ---- E4 负对照：改一字节必须变红 ----
        target = os.path.join(dest, "nodes.jsonl")
        raw = open(target, "rb").read()
        idx = raw.find(b'"type"')
        open(target, "wb").write(raw[:idx] + b'"TYPE"' + raw[idx + 6:] if idx >= 0 else raw + b" ")
        rc2, out2 = run_verifier(dest, guard)
        fails2 = [ln for ln in out2.splitlines() if ln.startswith("[FAIL]")]
        add(rc2 != 0 and any("V2" in ln for ln in fails2),
            "E4 负对照：在**下载副本**上改一个字节 ⇒ 校验器必须变红（证明 E3 的绿不是空跑）",
            "RC=%d；红的是 %s" % (rc2, [ln.split()[1] for ln in fails2][:4])
            if rc2 != 0 else "★ 改了字节仍 RC=0 —— 校验器是死的")
    finally:
        httpd.shutdown()
        httpd.server_close()
        if a.keep:
            print("  （下载副本保留在 %s）" % dest)
        else:
            shutil.rmtree(work, ignore_errors=True)

    print("-" * 78)
    n_fail = sum(1 for r in rows if not r)
    print("  ⇒ %s（PASS %d / FAIL %d）" % ("端到端通过" if not n_fail else "★ 端到端未通过",
                                            len(rows) - n_fail, n_fail))
    return 0 if not n_fail else 1


if __name__ == "__main__":
    sys.exit(main())
