# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""断言**名字集合**比对（铁律 #87）。

为什么需要它
------------
"加了 N 条断言"这种说法在本项目里被证明**不可靠**：
一次提交可能是"加 2 条、删 1 条"，总数净 +1 —— 只看总数就会把一次删除漏掉。
故核对一律按**名字集合**（每个断言的 ID token）做差集，并显式分列
`新增 / 删除 / 改名`；**只要出现删除就必须逐条给出理由**（断言过期应重写而非删除，铁律 #74）。

用法
----
    python 06_PoC/tests/assert_name_diff.py <旧文件> <新文件> [<旧> <新> ...]
    python 06_PoC/tests/assert_name_diff.py --pair a.py:b.py --json out.json

与仓库里"取旧版本"的正确姿势（避免工作区 CRLF 干扰，见铁律 #69/#72）配合：

    git cat-file blob HEAD:06_PoC/tests/xxx.py > /tmp/old_xxx.py
    python 06_PoC/tests/assert_name_diff.py /tmp/old_xxx.py 06_PoC/tests/xxx.py

支持两种断言注册写法（本仓库现有两种）：
  * Python：`rep.add(cond, NAME, detail)` / `rep.skip(NAME, detail)`（`ast` 解析，能处理多行与 f-string）
  * ESM   ：`check(NAME, cond, detail)`

退出码：`0` = 无删除；`1` = **有删除**（须人工说明）；`2` = 用法错误。
"""
import argparse
import ast
import io
import json
import re
import sys

# 断言名形如 `S3 必需 DOM id 齐备` / `B4k-客户端等待预算…` / `F40a`
_ID_RE = re.compile(r"^\s*([A-Za-z]{1,4}\d+[a-z]?)")
_JS_CHECK_RE = re.compile(r'\bcheck\(\s*(["\'`])((?:\\\1|(?!\1).)*)\1')


def norm(name):
    """取断言名里的 ID token；取不到则退化为前 24 个字符。"""
    m = _ID_RE.match(name or "")
    return m.group(1) if m else (name or "").strip()[:24]


def from_python(path):
    """`ast` 解析：取 `rep.add` 的第 2 个实参、`rep.skip`/`rep.check` 的第 1 个实参。"""
    src = io.open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        attr = getattr(node.func, "attr", None)
        if attr not in ("add", "skip", "check", "ok"):
            continue
        if attr == "add":
            name = node.args[1] if len(node.args) >= 2 else None
        else:
            name = node.args[0] if len(node.args) >= 1 else None
        if isinstance(name, ast.Constant) and isinstance(name.value, str):
            out.append(name.value)
        elif isinstance(name, ast.JoinedStr) and name.values:
            head = name.values[0]
            if isinstance(head, ast.Constant):
                out.append(head.value)
    return out


def from_js(path):
    src = io.open(path, encoding="utf-8").read()
    return [m.group(2) for m in _JS_CHECK_RE.finditer(src)]


def collect(path):
    raw = from_js(path) if path.endswith((".mjs", ".js")) else from_python(path)
    return raw, [norm(n) for n in raw]


def diff_pair(old_p, new_p):
    old_raw, old = collect(old_p)
    new_raw, new = collect(new_p)
    so, sn = set(old), set(new)
    return {
        "new_file": new_p,
        "old_entries": len(old_raw), "old_ids": len(so),
        "new_entries": len(new_raw), "new_ids": len(sn),
        "added": sorted(sn - so),
        "removed": sorted(so - sn),
    }


def main():
    ap = argparse.ArgumentParser(description="断言名字集合比对（铁律 #87）")
    ap.add_argument("paths", nargs="*", help="成对的 <旧> <新> 文件路径")
    ap.add_argument("--pair", action="append", default=[],
                    help="`<旧>:<新>` 形式（可重复）")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()

    pairs = [p.split(":", 1) for p in args.pair if ":" in p]
    rest = list(args.paths)
    while len(rest) >= 2:
        pairs.append([rest.pop(0), rest.pop(0)])
    if rest or not pairs:
        ap.print_usage()
        print("★ 用法：assert_name_diff.py <旧文件> <新文件> [--pair 旧:新] [--json out.json]")
        return 2

    reports, any_removed = [], False
    for old_p, new_p in pairs:
        r = diff_pair(old_p, new_p)
        reports.append(r)
        any_removed = any_removed or bool(r["removed"])
        print("文件: %s" % r["new_file"])
        print("  条目 %d→%d ｜ 唯一 ID %d→%d"
              % (r["old_entries"], r["new_entries"], r["old_ids"], r["new_ids"]))
        print("  新增 ID（%d）: %s" % (len(r["added"]), ", ".join(r["added"]) or "（无）"))
        print("  删除 ID（%d）: %s" % (len(r["removed"]), ", ".join(r["removed"]) or "（无）"))
        if r["removed"]:
            print("  ★ 有删除 —— 按铁律 #87/#74 必须**逐条**说明理由"
                  "（断言过期应重写，不应直接删）")
    if args.json_out:
        io.open(args.json_out, "w", encoding="utf-8", newline="\n").write(
            json.dumps(reports, ensure_ascii=False, indent=2))
        print("已写出: %s" % args.json_out)
    return 1 if any_removed else 0


if __name__ == "__main__":
    sys.exit(main())
