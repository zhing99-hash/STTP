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
import source_registry as sr                              # noqa: E402

CUR_DEP = None        # S2 用：权威图的「当前依赖快照」（main() 里现算，不复用旧报告）

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
        print("  phase81_selfcheck（S1 证明器 + S2 读侧）: PASS %d / FAIL %d" % (npass, nfail))
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
    global CUR_DEP
    CUR_DEP = ed.current_snapshot(base=ROOT, byte_source="repo",
                                  src_hashes=sr.hash_map(ROOT))

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
    # ★ S3 起「不等」有两种解释：①口径错（必须红）；②内容未变但字节变了，
    #   已有 `content_equivalent` 证明把 from→to 桥住（合法，算「已桥接」）。
    #   判据因此改成「**没有任何一项是既不等又无证明**」；且桥接必须是活绑定
    #   （族 + 对象 + from + to 四要素全对上）—— 表里随便塞一张证明不算。
    live = ed.content_attestations(ROOT)
    n_ok, n_bad, n_skip, n_bridge, sample = 0, 0, 0, 0, ""
    for kind, key, h in ([("src", k, v) for k, v in rec_src.items()]
                         + [("ref", k, v) for k, v in sorted(rec_ref)]
                         + [("dep", k, v) for k, v in sorted(rec_dep)]):
        got_h = ra.composite_hash_now(kind, key)
        if got_h is None:
            n_skip += 1
        elif got_h == h:
            n_ok += 1
        elif ed._content_equivalent_covered(kind, key, h, got_h, live):
            n_bridge += 1
        else:
            n_bad += 1
            sample = sample or "%s:%s：图 %s ≠ 现算 %s（且无证明桥接）" % (
                kind, key, str(h)[:12], str(got_h)[:12])
    rep.add(n_bad == 0 and n_ok + n_bridge > 0,
            "S99g 复合哈希口径：不等必须有证明桥接，否则算不符"
            "（一致 %d / 已桥接 %d / 不符 %d / 不可复算 %d）"
            % (n_ok, n_bridge, n_bad, n_skip),
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
            # ★ 原 S99i4「历史版本对硬说成 to_is_head ⇒ 判失败」**已于 2026-10-28 退役**：
            #   S3 把注释加回后，`79c1648` 那一版的字节**恰好就是当前内容** ⇒
            #   「冒充当前」其实是**真话**，判据放行是对的。前提蒸发了的判据不能留着充数。
            #   同类守卫（且是受控夹具、不依赖仓库当前状态）由 S99v / S99w 承担：
            #   对象未变而 HEAD 前进 ⇒ 成立；对象再变 ⇒ 失效。
            d4 = json.load(open(p, encoding="utf-8"))
            d4["attestations"][0]["to_is_head"] = True      # 冒充「to 就是当前」
            p4 = os.path.join(tmp, "att4.json")
            json.dump(d4, open(p4, "w", encoding="utf-8"), ensure_ascii=False)
            rows4 = ra.check_attestations(path=p4, base=ROOT)
            _ = rows4        # 保留重算调用（不落判据），见上一条说明
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # =====================================================================
    # S2：读侧接通 —— 绑定匹配成立才抵，四项缺一即不生效
    # =====================================================================
    def fx(family, obj, rec_h, cur_h):
        """构造一条「该族对象从 rec_h 变到 cur_h」的最小证据 + 当前快照。"""
        rec = {"src": None, "src_hash": None, "reference_refs": [], "verifier": {}}
        cur = {"src_hashes": {}, "reference_hashes": {}, "verifiers": {}}
        # ★ ref/dep 夹具也要给一个**自洽的 src**：否则会凭空多出 `unverifiable_source`
        #   ⇒ 被判 needs_review 而非 valid，把「证明生效没生效」的信号盖掉（夹具缺陷）。
        rec["src"], rec["src_hash"] = "SRCFIX", "s" * 16
        cur["src_hashes"]["SRCFIX"] = "s" * 16
        if family == "src":
            rec["src"], rec["src_hash"] = obj, rec_h
            cur["src_hashes"][obj] = cur_h
        elif family == "ref":
            rec["reference_refs"] = [{"ref_id": obj, "content_hash": rec_h}]
            cur["reference_hashes"][obj] = cur_h
        else:
            rec["verifier"] = {"verifier_id": "V", "code_hash": "x",
                               "dependency_versions": {obj: rec_h}}
            cur["verifiers"] = {"V": {"code_hash": "x", "dependency_versions": {obj: cur_h}}}
        return rec, cur

    def att(family, obj, frm, to, **kw):
        d = {"id": "rva:t", "kind": "content_equivalent", "object_kind": family,
             "object_id": obj, "composite": {"from": frm, "to": to}, "to_is_head": True}
        d.update(kw)
        return {"rva:t": d}

    A, B = "a" * 16, "b" * 16
    # ---- S99k 正例：三族各自的 changed 都能被抵（非真空：hit 必须 > 0） ----
    for fam, obj, rid in (("src", "Phase13.Gate.B5", ed.R_SRC_CHANGED),
                          ("ref", "element_reference", ed.R_REF_CHANGED),
                          ("dep", TARGET, ed.R_DEP_CHANGED)):
        rec, cur = fx(fam, obj, A, B)
        st0, rs0 = ed.evidence_status(rec, cur)
        st1, rs1 = ed.evidence_status(rec, cur, attestations=att(fam, obj, A, B))
        rep.add(st0 == "stale" and st1 == "valid"
                and any(r.startswith("reverified:content_equivalent") for r in rs1),
                "S99k 正例 %s:%s —— 无证明 stale ⇒ 有证明 valid 且记 reverified" % (fam, obj),
                "无证明 %s%s | 有证明 %s%s" % (st0, rs0, st1, rs1))

    # ---- NEG-S99l 串族：ref 的证明不得抵 src_changed（同 object_id 也不行） ----
    rec, cur = fx("src", "element_reference", A, B)
    st, rs = ed.evidence_status(rec, cur, attestations=att("ref", "element_reference", A, B))
    rep.add(st == "stale", "NEG-S99l 串族：ref 证明抵不掉 src_changed（同名对象也不行）",
            "%s %s" % (st, rs))

    # ---- NEG-S99m 越权对象：证明写的是别的对象 ----
    rec, cur = fx("src", "Phase13.Gate.B5", A, B)
    st, rs = ed.evidence_status(rec, cur, attestations=att("src", "Phase13.Gate.B2", A, B))
    rep.add(st == "stale", "NEG-S99m 越权：证明的 object_id 不是本条证据的来源 ⇒ 不抵",
            "%s %s" % (st, rs))

    # ---- NEG-S99n/o 版本对不对齐 ----
    rec, cur = fx("src", "Phase13.Gate.B5", A, B)
    st, rs = ed.evidence_status(rec, cur, attestations=att("src", "Phase13.Gate.B5", "z" * 16, B))
    rep.add(st == "stale", "NEG-S99n from 不符：证明的 from ≠ 证据记录值 ⇒ 不抵", "%s %s" % (st, rs))
    st, rs = ed.evidence_status(rec, cur, attestations=att("src", "Phase13.Gate.B5", A, "c" * 16))
    rep.add(st == "stale", "NEG-S99o to 过期：证明的 to ≠ 当前哈希 ⇒ 不抵", "%s %s" % (st, rs))

    # ---- NEG-S99p 历史版本对：to_is_head=False 的证明不能抵「当前」的原因 ----
    st, rs = ed.evidence_status(rec, cur,
                                attestations=att("src", "Phase13.Gate.B5", A, B, to_is_head=False))
    rep.add(st == "stale", "NEG-S99p 历史版本对（to_is_head=False）⇒ 不抵当前原因", "%s %s" % (st, rs))

    # ---- NEG-S99q 串通道：Phase 73 的 code_equivalent 不得抵 src_changed ----
    ce = {"rva:c": {"id": "rva:c", "kind": "code_equivalent", "verifier": "V",
                    "from_code_hash": A, "to_code_hash": B}}
    st, rs = ed.evidence_status(rec, cur, attestations=ce)
    rep.add(st == "stale", "NEG-S99q 串通道：code_equivalent（Phase 73）不得抵 src_changed",
            "%s %s" % (st, rs))

    # ---- S99r 部分覆盖：只抵被证明覆盖的那一条，另一条照旧 ⇒ 仍 stale ----
    rec, cur = fx("src", "Phase13.Gate.B5", A, B)
    rec["reference_refs"] = [{"ref_id": "element_reference", "content_hash": A}]
    cur["reference_hashes"]["element_reference"] = B
    st, rs = ed.evidence_status(rec, cur, attestations=att("src", "Phase13.Gate.B5", A, B))
    rep.add(st == "stale" and any(r.startswith(ed.R_REF_CHANGED) for r in rs)
            and not any(r.startswith(ed.R_SRC_CHANGED) for r in rs),
            "S99r 部分覆盖：src 被抵、ref 未覆盖 ⇒ 仍 stale（不是一刀切放行）", "%s %s" % (st, rs))

    # ---- S99t 接线口按 kind 过滤：不得把 code_equivalent 顺带带上 ----
    all_atts = ed.load_attestations(ROOT)
    only = ed.content_attestations(ROOT)
    rep.add(all((v.get("kind") == "content_equivalent") for v in only.values()),
            "S99t 接线口：content_attestations() 只含 content_equivalent（全表 %d / 过滤后 %d）"
            % (len(all_atts), len(only)),
            "全表 kinds=%s" % sorted({str(v.get("kind")) for v in all_atts.values()}))

    # ---- S99u 接线**有据**：全图逐条对比，凡状态变化必须由证明解释得了 ----
    #   ★ v1 写的是「当前没有该证明 ⇒ 翻转必须为 0」—— 那是 S2 当时的快照，
    #     S3 一加证明它就必然失效。改成**长期成立**的形式：
    #     ① 变严（stale→valid/needs_review）必须带着 `reverified:content_equivalent`；
    #     ② 变松之外的方向（valid→stale 之类）**一条都不许有**（证明只能减不能加）；
    #     ③ 每条被翻转的证据，原先的变更原因必须**全部**被覆盖（不许「顺带洗白」）。
    flip, unexplained, worse, leftover, sample = 0, 0, 0, 0, ""
    for _e in (json.load(open(os.path.join(ROOT, "06_PoC/etl/normalized.json"),
                              encoding="utf-8"))["edges"]):
        _p = _e.get("props") or {}
        for _ev in (_p.get("verification_evidence") or []):
            if not isinstance(_ev, dict) or not ed.has_recorded_deps(_ev):
                continue
            _r = ed.recorded_deps(_ev)
            _a, _ra = ed.evidence_status(_r, CUR_DEP)
            _b, _rb = ed.evidence_status(_r, CUR_DEP, attestations=only)
            if _a == _b:
                continue
            flip += 1
            if _a == "valid":
                worse += 1
                continue
            if not any(x.startswith("reverified:content_equivalent") for x in _rb):
                unexplained += 1
                sample = sample or "%s → %s：%s" % (_a, _b, _rb)
                continue
            # 原先的「变更」原因必须一条不剩（不可判定原因可以留下 ⇒ needs_review 合法）
            left = [x for x in _rb if x.split(":")[0] in ed.CHANGE_REASONS]
            if left:
                leftover += 1
                sample = sample or "残留变更原因 %s" % left
    rep.add(unexplained == 0 and worse == 0 and leftover == 0,
            "S99u 接线有据：全图翻转 %d 条，必须①都带 reverified ②无变严③无残留变更原因"
            "（无据 %d / 变严 %d / 残留 %d）" % (flip, unexplained, worse, leftover), sample)

    # ---- S99v / S99w：新鲜度必须按**内容**判，不能按「HEAD 还停在 to_rev」 ----
    #   ★ 这是 S3 真用例逼出来的死锁：证明文件本身要提交进仓库 ⇒ HEAD 必然前移一格；
    #     若按 rev 判，每张证明**在记录它的那一笔提交里就立刻过期** ⇒ 通道永远打不开。
    #   用临时仓库造四段历史：A 基线 → B 只加注释（出证）→ C 无关提交 → D 改 AST。
    t2 = tempfile.mkdtemp(prefix="sttp_s99v_")
    try:
        rel = TARGET
        base_src = ra.blob_at("HEAD", rel)
        commented = (base_src or "") + "\n# S99v 夹具：只加一行注释，不进 AST\n"
        mutated = (base_src or "") + "\nS99V_MUTATION = 1     # 这一行进 AST\n"

        def _put(body, name=None):
            fp = os.path.join(t2, *(name or rel).split("/"))
            d = os.path.dirname(fp)
            if d and not os.path.isdir(d):
                os.makedirs(d)
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(body)

        def _g(*a):
            r = subprocess.run(["git"] + list(a), cwd=t2, capture_output=True)
            # ★ `source_registry._git` 有进程内缓存（键 = base + 参数）。生产里 HEAD
            #   一次运行内不动，缓存合法；但本夹具在一次进程里连续推进 HEAD
            #   ⇒ 不清缓存会读到旧 blob，S99w 会假绿（实测踩到）。
            sr.clear_git_cache()
            return r

        _g("init", "-q")
        _g("config", "user.email", "selfcheck@local")
        _g("config", "user.name", "selfcheck")
        if not base_src:
            rep.add(False, "S99v 夹具：取不到该文件当前 blob", "blob_at 返回 None")
        else:
            _put(base_src)
            _g("add", "-A")
            _g("commit", "-qm", "A base")
            rev_a = _g("rev-parse", "HEAD").stdout.decode().strip()
            _put(commented)
            _g("add", "-A")
            _g("commit", "-qm", "B comment only")
            att, why = ra.build_content_attestation("dep", rel, from_rev=rev_a, base=t2)
            if att is None:
                rep.add(False, "S99v 夹具：临时仓库里出不了证", why or "")
            else:
                # C：无关提交（HEAD 前进，但对象内容没变）
                _put("unrelated\n", "__s99v_unrelated.txt")
                _g("add", "-A")
                _g("commit", "-qm", "C unrelated")
                pj = os.path.join(t2, "_att.json")
                with open(pj, "w", encoding="utf-8") as f:
                    json.dump({"attestations": [att]}, f, ensure_ascii=False)
                rows = ra.check_attestations(path=pj, base=t2)
                ok_v = bool(rows) and rows[0][1] is True
                rep.add(ok_v, "S99v 新鲜度按内容判：HEAD 前进但对象未变 ⇒ 证明仍成立（不死锁）",
                        (rows[0][2] if rows else "无结果"))
                # D：对象真变（AST 变）⇒ 必须失效
                _put(mutated)
                _g("add", "-A")
                _g("commit", "-qm", "D ast change")
                rows2 = ra.check_attestations(path=pj, base=t2)
                rep.add(bool(rows2) and rows2[0][1] is False,
                        "S99w 对象再变（AST 变）⇒ 证明必须失效（不是永久通行）",
                        (rows2[0][2] if rows2 else "无结果"))
    finally:
        shutil.rmtree(t2, ignore_errors=True)

    return rep.render("Phase 81：S1 证明器 + S2 读侧接通（绑定四要素缺一不生效）")


if __name__ == "__main__":
    sys.exit(main())
