# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
"""phase81_selfcheck.py —— Phase 81 · S1：内容等价证明器（`content_equivalent`）

本轮**只做证明器，不动读侧**（`evidence_status` / `_attestation_ok` 一个字不改）。
理由见立项文档：读侧一改就牵动 70.5% 的证据，而权威图不入库（D134，改坏无救）
⇒ 先把「证明器会拒真变更」钉死，再开读侧的闸（那是 S2 的事）。

判据
----
  S99a   正例（真实历史）：79c1648^ → 79c1648 只加了一行注释 ⇒ ast_identical **成立**
  S99a2  正例（内存变异）：当前文件追加一行注释 ⇒ 成立
  NEG-S99b 改一个常量（原子量/数字）⇒ **必须拒**
  NEG-S99c 改模块 docstring ⇒ 必须拒（字符串进 AST，不是"看起来没变"）
  NEG-S99d 删掉一个顶层定义 ⇒ 必须拒
  NEG-S99e 给函数参数加类型注解 ⇒ 必须拒（注解进 AST）
  S99f   身份清单：`element_reference.py` 的身份集合 == 已知 6 个（带指纹，登记一改就红）
  S99g   复合口径：本证明器算的 src/ref/dep 复合哈希 == 图上记录值（口径错则通道漏）
  S99h   fail-closed：不给 from_hash/from_rev ⇒ 拒绝出证；反查不到 rev ⇒ 拒绝出证
  S99i   闭环：生成的证明能被 `--check` 重算；to 过期 ⇒ 判失败
  NEG-S99j 越权：files 与 object 不匹配（拿别的文件冒充）⇒ `--check` 必须判失败

★ 为什么负对照用**真实文件**而不是构造样本：构造样本只能证明"我的比较逻辑会拒绝"，
  证明不了"真实文件里那类改动会被抓住"。改原子量就是拿 `element_reference.py`
  真实内容试的 —— 这才是这条通道真正要挡的东西。

运行： bash sttp.sh phase81
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
for p in (os.path.join(ROOT, "11_真实数据"), os.path.join(ROOT, "06_PoC")):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import reverify_attest as ra                              # noqa: E402
import evidence_deps as ed                                # noqa: E402

TARGET = "11_真实数据/element_reference.py"

# ★ S99f 的指纹：2026-10-28 现算。**不是我估的 4 个** —— 机器数出来是 6 个
#   （人肉漏了 `src:element_reference` 与 `verifier:element_reference`，正是 D138 的实证）。
KNOWN_IDENTITIES = {
    ("src", "Phase13.Gate.B2"),
    ("src", "Phase13.Gate.B5"),
    ("src", "element_reference"),
    ("ref", "element_reference"),
    ("verifier", "element_reference"),
    ("dep", TARGET),
}

# 历史上那次「加注释」的提交（79c1648），其父提交就是被登记的字节
COMMENT_REV = "79c1648"


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
class Report:
    def __init__(self):
        self.rows = []

    def add(self, ok, name, detail=""):
        self.rows.append((bool(ok), name, detail or ""))
        return bool(ok)

    def render(self, title):
        print("=" * 76)
        print("  " + title)
        print("=" * 76)
        for ok, name, detail in self.rows:
            print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                                   ("  —— " + detail) if detail else ""))
        npass = sum(1 for ok, _, _ in self.rows if ok)
        nfail = len(self.rows) - npass
        print()
        print("  phase81_selfcheck（S1 证明器）: PASS %d / FAIL %d" % (npass, nfail))
        return 0 if nfail == 0 else 1


# ---------------------------------------------------------------------------
# 变异器：在**真实文件**上做受控改动
# ---------------------------------------------------------------------------
def _git(args, cwd=ROOT):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True)


def real_src(rev="HEAD", rel=TARGET):
    b = ra.blob_at(rev, rel)
    if b is None:
        return None
    return b


def mutate(src, mode):
    """返回变异后的源码；做不出来 ⇒ None（**不静默返回原文**，否则负对照会假绿）。"""
    if mode == "comment":
        return src + "\n# probe: 注释不进 AST\n"
    if mode == "value":
        # ★ 必须走 **AST** 定位数值常量：正则会先命中注释里的 `Copyright 2026`
        #   ⇒ 改了 AST 没变 ⇒ 负对照假绿（D121 同族：先证明干扰生效，再评判据）。
        tree = ast.parse(src)
        offs = [0]
        for line in src.splitlines(True):
            offs.append(offs[-1] + len(line))
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) \
                    and not isinstance(n.value, bool):
                a = offs[n.lineno - 1] + n.col_offset
                b = offs[n.end_lineno - 1] + n.end_col_offset
                seg = src[a:b]
                if seg and seg[-1].isdigit():
                    new = seg[:-1] + ("0" if seg[-1] != "0" else "1")
                    return src[:a] + new + src[b:]
        return None
    if mode == "docstring":
        tree = ast.parse(src)
        d = ast.get_docstring(tree)
        if not d:
            return None
        return src.replace(d, d + " （改动）", 1)
    if mode == "delete":
        tree = ast.parse(src)
        fns = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if not fns:
            return None
        lines = src.splitlines()
        n = fns[-1]
        del lines[n.lineno - 1: (n.end_lineno or n.lineno)]
        return "\n".join(lines) + "\n"
    if mode == "annotate":
        tree = ast.parse(src)
        for n in tree.body:
            if isinstance(n, ast.FunctionDef) and n.args.args:
                a = n.args.args[0]
                col = a.col_offset + len(a.arg)
                off = sum(len(x) + 1 for x in src.splitlines()[:a.lineno - 1])
                return src[:off + col] + ": int" + src[off + col:]
        return None
    return None


def main():
    rep = Report()

    # ---- S99a：真实历史正例（加注释 ⇒ 成立） ----
    old = real_src(COMMENT_REV + "^")
    new = real_src(COMMENT_REV)
    if old is None or new is None:
        rep.add(False, "S99a 正例（真实历史 %s^ → %s）" % (COMMENT_REV, COMMENT_REV),
                "取不到历史 blob")
    else:
        pr, why = ra.ast_identical_proof(old, new)
        rep.add(pr is not None and pr.get("method") == "ast_identical",
                "S99a 正例：只加一行注释（真实提交 %s）⇒ 证明成立" % COMMENT_REV, why)

    # ---- S99a2：内存变异正例 ----
    cur = real_src()
    cm = mutate(cur, "comment") if cur else None
    pr2, why2 = (ra.ast_identical_proof(cur, cm) if cm else (None, "变异器没做出改动"))
    rep.add(pr2 is not None, "S99a2 正例：当前文件追加一行注释 ⇒ 证明成立", why2)

    # ---- 四个负对照（全在真实文件上做） ----
    for mode, name, tag in (
        ("value", "改一个数字常量（原子量那一类）", "NEG-S99b"),
        ("docstring", "改模块 docstring", "NEG-S99c"),
        ("delete", "删掉一个顶层函数", "NEG-S99d"),
        ("annotate", "给函数参数加类型注解", "NEG-S99e"),
    ):
        v = mutate(cur, mode) if cur else None
        if v is None:
            rep.add(False, "%s 负对照：%s ⇒ 必须拒" % (tag, name), "变异器失败（不可静默跳过）")
            continue
        p, w = ra.ast_identical_proof(cur, v)
        rep.add(p is None, "%s 负对照：%s ⇒ 必须拒" % (tag, name),
                ("已拒：" + w) if p is None else ("**没拒** ⇒ 通道会把真变更洗白"))

    # ---- S99f：身份清单（D138） ----
    ids = ra.identities_of(TARGET)
    got = {(k, o) for k, o, _ in ids}
    rep.add(got == KNOWN_IDENTITIES,
            "S99f 身份清单：%s 充当 %d 个身份（指纹 6 个）" % (TARGET, len(ids)),
            "缺 %s / 多 %s" % (sorted(KNOWN_IDENTITIES - got), sorted(got - KNOWN_IDENTITIES))
            if got != KNOWN_IDENTITIES else "（人肉会估成 4 个，机器数出 6 个）")

    # ---- S99g：复合哈希口径与图上记录值一致 ----
    g = json.load(open(os.path.join(ROOT, "06_PoC", "etl", "normalized.json"), encoding="utf-8"))
    rec_src, rec_ref, rec_dep = {}, set(), set()
    for e in g.get("edges") or []:
        for ev in ((e.get("props") or {}).get("verification_evidence") or []):
            if not isinstance(ev, dict):
                continue
            if ev.get("src") and ev.get("src_hash"):
                rec_src.setdefault(ev["src"], ev["src_hash"])
            for k, v in (ev.get("refs") or {}).items():
                rec_ref.add((k, v))
            for k, v in (ev.get("dvs") or {}).items():
                rec_dep.add((k, v))
    # ★ 「现算不出来」≠「不一致」：locator 未入库（如 elementkg_raw.json）时
    #   `composite_hash_now` 按 fail-closed 返回 None，那是**不可复算**，必须分列，
    #   不得当成不符（与 evidence_status 里「不可判定 ≠ 已变更」同一条规矩）。
    n_ok, n_bad, n_skip, sample = 0, 0, 0, ""
    for kind, key, h in ([("src", k, v) for k, v in rec_src.items()]
                         + [("ref", k, v) for k, v in sorted(rec_ref)]
                         + [("dep", k, v) for k, v in sorted(rec_dep)]):
        got_h = ra.composite_hash_now(kind, key)
        if got_h is None:
            n_skip += 1
        elif got_h == h:
            n_ok += 1
        else:
            n_bad += 1
            sample = sample or "%s:%s：图 %s ≠ 现算 %s" % (kind, key, str(h)[:12], str(got_h)[:12])
    rep.add(n_bad == 0 and n_ok > 0,
            "S99g 复合哈希口径：可复算的项必须逐一对上（一致 %d / 不符 %d / 不可复算 %d）"
            % (n_ok, n_bad, n_skip),
            sample or ("不可复算的 %d 项是 locator 未入库，属 fail-closed 的预期行为" % n_skip))

    # ---- S99h：fail-closed ----
    a1, w1 = ra.build_content_attestation("ref", "element_reference")
    rep.add(a1 is None and "必须显式" in (w1 or ""),
            "S99h fail-closed：不给 from_hash/from_rev ⇒ 拒绝出证（防 from==to 空证明）", w1 or "")
    a2, w2 = ra.build_content_attestation("ref", "element_reference", from_hash="0" * 16)
    rep.add(a2 is None, "S99h2 fail-closed：from 哈希反查不到任何提交 ⇒ 拒绝出证", w2 or "")

    # ---- S99i：生成 → 重算闭环（写在临时目录，不动权威表） ----
    tmp = tempfile.mkdtemp(prefix="sttp81_")
    try:
        p = os.path.join(tmp, "att.json")
        rc = ra.emit_content(path=p, object_kind="ref", obj_id="element_reference",
                             from_rev=COMMENT_REV + "^", to_rev=COMMENT_REV)
        rep.add(rc == 0, "S99i 生成：真实历史版本对（%s^ → %s）能出证" % (COMMENT_REV, COMMENT_REV),
                "rc=%s" % rc)
        if rc == 0:
            rows = ra.check_attestations(path=p, base=ROOT)
            rep.add(rows and rows[0][1] is True,
                    "S99i2 重算：刚生成的证明能被 --check 重算成立",
                    (rows[0][2] if rows else "没有条目"))
            # 越权：把 files 换成别的文件（冒充）
            d = json.load(open(p, encoding="utf-8"))
            bad = dict(d["attestations"][0])
            bad["files"] = [{"rel": "11_真实数据/dimension_table.py",
                             "from_hash": bad["files"][0]["from_hash"],
                             "to_hash": bad["files"][0]["to_hash"]}]
            bad["id"] = "rva:bad0000bad0"
            d["attestations"].append(bad)
            p2 = os.path.join(tmp, "att2.json")
            json.dump(d, open(p2, "w", encoding="utf-8"), ensure_ascii=False)
            rows2 = {i: ok for i, ok, _ in ra.check_attestations(path=p2, base=ROOT)}
            rep.add(rows2.get("rva:bad0000bad0") is False,
                    "NEG-S99j 越权：files 与 object 不匹配（拿别的文件冒充）⇒ 判失败",
                    str(rows2.get("rva:bad0000bad0")))
            # to 过期：篡改 composite.to ⇒ 判失败
            d2 = json.load(open(p, encoding="utf-8"))
            d2["attestations"][0]["composite"]["to"] = "f" * 16
            p3 = os.path.join(tmp, "att3.json")
            json.dump(d2, open(p3, "w", encoding="utf-8"), ensure_ascii=False)
            rows3 = ra.check_attestations(path=p3, base=ROOT)
            rep.add(rows3 and rows3[0][1] is False,
                    "S99i3 篡改：composite.to 被改 ⇒ 判失败（不是静默通过）",
                    (rows3[0][2] if rows3 else ""))
            # 历史版本对：证明**本身**要能重算（to 不是 HEAD 也照算），
            # 但声称 to_is_head 的证明一旦 HEAD 走了就必须失效
            d4 = json.load(open(p, encoding="utf-8"))
            d4["attestations"][0]["to_is_head"] = True      # 冒充「to 就是当前」
            p4 = os.path.join(tmp, "att4.json")
            json.dump(d4, open(p4, "w", encoding="utf-8"), ensure_ascii=False)
            rows4 = ra.check_attestations(path=p4, base=ROOT)
            rep.add(rows4 and rows4[0][1] is False,
                    "S99i4 腐烂：把历史版本对硬说成 to_is_head ⇒ 判失败（读侧只认当前）",
                    (rows4[0][2] if rows4 else ""))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return rep.render("Phase 81 · S1：内容等价证明器（ast_identical）—— 只证证明器，不动读侧")


if __name__ == "__main__":
    sys.exit(main())
