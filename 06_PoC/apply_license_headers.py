# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
"""
apply_license_headers.py —— 为一/三方源码批量添加 Apache-2.0 SPDX 头注释（幂等）。

背景
----
项目许可证由 MIT 迁移到 **Apache License 2.0**。根目录 `LICENSE` 已替换为
Apache-2.0 全文、`NOTICE` 已创建；本脚本负责 **源文件级** 的 SPDX 标识头。

规则
----
- 目标：**第一方源码**（`.py` / `.sh` / `.ps1` / `.js` / `.cypher` / `.html`）。
- 跳过：`06_PoC/vendor/`（第三方库，保留其自带许可证，**不得改动**）、
  任何已含 `SPDX-License-Identifier` 的文件（**幂等**）。
- Python：头注释插在 shebang / `# -*- coding -*-` 之后（PEP 263：编码声明必须在第 1、2 行）。
- Shell / PowerShell：插在 shebang 之后。
- 其余（js / cypher / html）：插在文件首行（HTML 在 `<!DOCTYPE>` 之后）。

用法
----
    python 06_PoC/apply_license_headers.py --dry-run    # 只预演，不写盘
    python 06_PoC/apply_license_headers.py             # 实际写入
    python 06_PoC/apply_license_headers.py --revert     # 移除本脚本添加的头（按签名精确回滚）
"""
import argparse
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

START = "SPDX-License-Identifier: Apache-2.0"
SIG = "Copyright 2026 zhing"
BODY = [
    START,
    SIG,
    "",
    "Licensed under the Apache License, Version 2.0 (the \"License\");",
    "you may not use this file except in compliance with the License.",
    "You may obtain a copy of the License at",
    "    http://www.apache.org/licenses/LICENSE-2.0",
]

TRACKED_EXTS = (".py", ".sh", ".ps1", ".js", ".cypher", ".html")
SKIP_DIR_MARKERS = ("06_PoC/vendor/", "06_PoC/vendor\\")


def prefix_for(path: str):
    """返回 (行前缀, 行后缀) 二元组，例如 (\"# \", \"\") 或 (\"<!-- \", \" -->\")。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".py", ".sh", ".ps1", ".cypher"):
        return "# ", ""
    if ext == ".js":
        return "// ", ""
    if ext == ".html":
        return "<!-- ", " -->"
    raise ValueError(ext)


def render(path: str):
    pre, suf = prefix_for(path)
    return [pre + ln + suf if ln else (pre.rstrip() if suf == "" else pre + suf) for ln in BODY]


def tracked_files():
    out = subprocess.run(
        ["git", "-C", ROOT, "-c", "core.quotepath=false", "ls-files", "-z", "--"]
        + ["*" + e for e in TRACKED_EXTS],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    files = [f for f in out.stdout.split("\0") if f.strip()]
    keep = []
    for f in files:
        norm = f.replace("\\", "/")
        if any(m in norm or m.replace("/", "\\") in f for m in SKIP_DIR_MARKERS):
            continue
        keep.append(norm)
    return sorted(set(keep))


def insertion_index(lines, path):
    """计算头注释应插入的行号。"""
    ext = os.path.splitext(path)[1].lower()
    idx = 0
    if ext in (".py", ".sh", ".ps1"):
        if idx < len(lines) and lines[idx].startswith("#!"):
            idx += 1
        if ext == ".py" and idx < 2 and idx < len(lines) and re.match(r"^#.*coding[:=]", lines[idx]):
            idx += 1
    elif ext == ".html":
        for i, ln in enumerate(lines[:3]):
            if ln.strip().lower().startswith("<!doctype"):
                idx = i + 1
                break
    return idx


def has_header(text: str) -> bool:
    return START in text


def apply_one(path, dry, revert=False):
    full = os.path.join(ROOT, path)
    with open(full, "r", encoding="utf-8") as fh:
        text = fh.read()
    lines = text.splitlines()

    if revert:
        idx = insertion_index(lines, path)
        block = render(path)
        if lines[idx:idx + len(block)] == block:
            new_lines = lines[:idx] + lines[idx + len(block):]
            # 去掉紧随的一个空行（若原为注释块与正文之间的分隔）
            if idx < len(new_lines) and new_lines[idx].strip() == "":
                new_lines = new_lines[:idx] + new_lines[idx + 1:]
            new = "\n".join(new_lines) + ("\n" if text.endswith("\n") else "")
            if not dry:
                with open(full, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(new)
            return "reverted"
        return "skip(no-sig)"

    if has_header(text):
        return "skip(has-header)"

    idx = insertion_index(lines, path)
    block = render(path)
    new_lines = lines[:idx] + block + [""] + lines[idx:]
    new = "\n".join(new_lines) + ("\n" if text.endswith("\n") else "")
    if not dry:
        with open(full, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new)
    return "inserted@%d" % idx


def main():
    ap = argparse.ArgumentParser(description="Apache-2.0 SPDX 头注释批处理")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()

    files = tracked_files()
    stats = {"inserted": 0, "skip": 0, "reverted": 0, "skip(no-sig)": 0}
    for p in files:
        r = apply_one(p, args.dry_run, revert=args.revert)
        if r.startswith("inserted"):
            key = "inserted"
        elif r.startswith("reverted"):
            key = "reverted"
        else:
            key = "skip"
        stats[key] = stats.get(key, 0) + 1
        if args.dry_run or args.revert:
            print("  %-14s %s" % (r, p))

    print("\n== 汇总 ==")
    print("  目标文件 %d ｜ 新增 %d ｜ 跳过 %d ｜ 回滚 %d"
          % (len(files), stats.get("inserted", 0), stats.get("skip", 0), stats.get("reverted", 0)))
    if args.dry_run:
        print("  （dry-run：未写盘）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
