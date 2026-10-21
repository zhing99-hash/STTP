#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""第一方源码许可证头检查（D92）

为什么会有这个文件
------------------
发布清单（`06_PoC/release/make_manifest.py`）里写着一句：

    "code": "Apache-2.0（根 LICENSE + NOTICE + 全部第一方源码 SPDX 头）"

而这句话此前**没有判据在核对它**。后果是：

  * Phase 66：手抄一遍 06_PoC 下的 .py，发现缺 5 个 ⇒ 补 69/69；
  * Phase 68：顺手多看了一个目录（11_真实数据），又缺 2 个。

两次都是**人肉核对**，两次都不完整。这不是"粗心"，是结构问题 ——
> 通律 7：没有判据的字段不是"低优先级"，是**必然会说谎**的字段。
> 这句话写在 Phase 66 的 D88 上，四天后在同一个仓库里又触发一次。

所以这里把它变成一条可反复跑、可被注入验证的检查。

★ 判据要能被注入变红（`--selfcheck` 的 S4 / `redprobe` 的 R29）：
  一个"只是遍历然后打印 OK"的检查，永远 PASS，等于把断言换成了一条注释。

用法
----
    python 06_PoC/licence_check.py                     # 扫仓库（默认排除第三方目录）
    python 06_PoC/licence_check.py DIR [DIR ...]       # 扫指定目录（供负对照/探针用）
    python 06_PoC/licence_check.py --json OUT
    python 06_PoC/licence_check.py --selfcheck         # 含负对照（不需要网络）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

SPDX_LINE = "SPDX-License-Identifier: Apache-2.0"
COPY_PREFIX = "Copyright"

# 排除：第三方/产物/缓存。规则是**路径形状**，不看是否是 .py
EXCLUDE_PARTS = {".git", "__pycache__", "node_modules", ".pytest_cache",
                 "site-packages", ".venv", "venv", ".runlog"}
EXCLUDE_DIRS = {"08_部署包", "公共数据集", "12_评测", "外部评审", }
# ★ 为什么把「外部评审」排除在外：那里的脚本是**评审方**（codex）提供的，
#   不是本项目第一方源码。给它盖上 `SPDX-License-Identifier: Apache-2.0`
#   等于替别人声明许可证 —— 为了让 S2 变绿而这样做，是**制造一份假的许可声明**，
#   比缺一个头坏得多。同理 `vendor/**.js` 也不是我们的代码（但它们不是 .py，不进扫描）。
#   ⇒ 第一方的边界必须先定义清楚，检查才有意义；含糊的"A 盘所有 .py"会逼人造假。


def _py_files(root: str):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in EXCLUDE_PARTS and d not in EXCLUDE_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def _head_lines(path: str, n: int = 8) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join(f.readline() for _ in range(n))
    except OSError:
        return ""


def scan(roots) -> dict:
    files = []
    for r in roots:
        files += _py_files(r)
    no_spdx = [p for p in files if SPDX_LINE not in _head_lines(p)]
    no_cr = [p for p in files if COPY_PREFIX not in _head_lines(p)]
    return {"scanned": len(files), "files": files,
            "no_spdx": no_spdx, "no_copyright": no_cr}


# ---------------------------------------------------------------- 报告
def check(roots, *, allow_empty=False):
    r = scan(roots)
    rs = []

    def add(ok, name, detail=""):
        print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                               ("  — " + detail) if detail else ""))
        rs.append(bool(ok))

    print("=" * 74)
    print("第一方源码许可证头检查")
    print("=" * 74)
    print("  扫描目录：%s" % ", ".join(roots))
    # S1 必须真扫到东西：空扫描会让下面三条全部"无文件失败" ⇒ 假绿
    add(r["scanned"] > 0 or allow_empty, "S1 扫描到第一方源码文件（非空）",
        "%d 个 .py" % r["scanned"])
    add(not r["no_spdx"], "S2 每个 .py 都有 SPDX 头",
        ("缺 %d 个：" % len(r["no_spdx"])) + "、".join(
            os.path.relpath(p, ROOT) for p in r["no_spdx"][:6]) if r["no_spdx"] else
        "%d/%d" % (r["scanned"], r["scanned"]))
    add(not r["no_copyright"], "S3 每个 .py 都有 Copyright 行",
        ("缺 %d 个" % len(r["no_copyright"])) if r["no_copyright"] else "%d/%d"
        % (r["scanned"], r["scanned"]))
    # S4 负对照：**注入一个没有 SPDX 头的文件，检查必须红**
    with tempfile.TemporaryDirectory(prefix="sttp_lic_") as td:
        bad = os.path.join(td, "no_header.py")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("# -*- coding: utf-8 -*-\nprint(1)\n")
        good = os.path.join(td, "with_header.py")
        with open(good, "w", encoding="utf-8") as f:
            f.write("# -*- coding: utf-8 -*-\n# %s\n# Copyright 2026 zhing\nprint(1)\n" % SPDX_LINE)
        neg = scan([td])
        add(neg["scanned"] == 2 and neg["no_spdx"] == [bad],
            "S4 负对照：无头文件**必须被抓出来**（判据可红）",
            "抓到 %d/%d" % (len(neg["no_spdx"]), 1))
    bad = sum(1 for x in rs if not x)
    print("-" * 74)
    print("  合计 %d 条，通过 %d，失败 %d ｜ 扫描 %d 个 .py"
          % (len(rs), len(rs) - bad, bad, r["scanned"]))
    return {"ok": bad == 0, "counts": {"scanned": r["scanned"],
                                       "no_spdx": len(r["no_spdx"]),
                                       "no_copyright": len(r["no_copyright"])},
            "no_spdx": r["no_spdx"]}, (1 if bad else 0)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    json_out = None
    if "--json" in sys.argv:
        i = sys.argv.index("--json")
        json_out = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
    roots = [os.path.abspath(a) for a in args] or [ROOT]
    res, rc = check(roots)
    if json_out:
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print("  → %s" % json_out)
    return rc


if __name__ == "__main__":
    sys.exit(main())
