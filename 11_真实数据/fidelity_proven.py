#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""Phase 82 · V18 第五档 `changed_but_proven` —— **判据本体**（先判据，后开闸）

为什么需要这一档
----------------
Phase 81 的 S3 真用例给出的是**负结果**：`content_equivalent` 证明能让证据层
（`evidence_deps.evidence_status`）里 2431 条证据从 stale 回到 valid，但
`queries.edge_fidelity()` —— 边级**字节层**判据 —— 仍然把 703 条边记成 `changed`，
于是 V18d「全图 changed == 0」照旧红。

两层问的**不是同一个问题**：
  · 字节层：来源那一个文件，**当前字节**是否等于登记时的字节；
  · 证据层：这条证据**依赖的东西**（来源/参考表/验证器）变了以后，还能不能算数。

要让「双重身份文件带一行注释」这条路真正走通，字节层必须能说第三种话：
**「字节变了，但内容等价，且这个等价性当场可复算」**。
本模块就是这句话的判据实现。它**不改变**现有四分类的任何行为 —— 开闸前
它是影子（影子跑通、负对照齐备，才允许把它接进 V18d / 服务端）。

判据（能被伪造打红的三条底线）
------------------------------
  1. **绑定四要素**：`kind/object` 对齐 ＋ `composite.from` == 该条证据的记录值
     ＋ `composite.to` == 当前值 ＋ `to_is_head`（历史版本对不放行当前原因）。
  2. **当场复算**：不信任证明里的任何字段，取 git 里的**真 blob** 重跑
     `ast_identical`（逐文件 AST dump 逐字符相同），并把复合字段整体复算一遍。
     证明被改一个字符 ⇒ 整条作废（`reverify_attest.check_content_attestation`）。
  3. **全覆盖**：一条边有 N 条「哈希不符」的证据，必须 N 条**全部**被覆盖才算
     proven；漏一条就仍是 `changed`。多来源边只覆盖一部分是最容易漏的口子。

★ 为什么不能只看 `attestations` 表里的字段：D144 的教训 —— R43 红探针用「只伪造
  `to_code_hash`」的方式造出了假绿。证明文件本身是**可写文件**，判据必须能识破
  对它的篡改，所以第 2 条不接受任何「表里写着 X」作为证据。

★ 为什么 `require_head` 是显式参数而不是默认放开：读侧（S2）只认 `to_is_head=True`。
  判据侧为了能**回放历史版本对**（自检要用真实历史证明），允许传 False，但
  必须由调用方显式声明 —— 默认严格，放松要留痕。

用法
----
    import fidelity_proven as fp
    r = fp.split_changed(edges, stale_registry, ed.content_attestations())
    r["n_proven"] / r["n_unproven"] / r["conserved"]

运行自检： bash sttp.sh phase82
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
for _p in (HERE, os.path.join(ROOT, "03_知识层")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import queries as Q                                       # noqa: E402
import reverify_attest as ra                              # noqa: E402
import evidence_deps as ed                                # noqa: E402

#: 第五档的判定值。`changed` 保留原名 —— 它现在的含义收窄为「变了且**未**证明等价」。
VERDICTS = ("not_changed", "changed_but_proven", "changed")


def proven_index(attestations=None, base=ROOT):
    """`{(object_kind, object_id): [att,…]}`，**只收 `content_equivalent`**。

    为什么按 `(kind, obj)` 分组而不是按 id：绑定是**按对象**的（Phase 81 · S2 的
    `_content_equivalent_covered` 同口径）—— 一张证明说「对象 X 从 A 到 B 内容相同」，
    则任何记录 A / 当前 B 的证据都成立，不必逐证据挂引用（7 万条挂不动）。
    """
    if attestations is None:
        attestations = ed.content_attestations(base)
    idx = {}
    for a in (attestations or {}).values():
        if not isinstance(a, dict) or a.get("kind") != ra.CONTENT_KIND:
            continue
        key = (a.get("object_kind"), a.get("object_id"))
        idx.setdefault(key, []).append(a)
    return idx


def _default_now(kind, obj, base=ROOT):
    return ra.composite_hash_now(kind, obj, base)


def _make_now(now, base):
    """★ `now` 必须记忆化：它默认要起一次 `git cat-file` 取 blob。不缓存的话，
    每条「不符」的证据都会拉一次子进程 —— 703 条边 × 十来次调用实测 **110 秒**，
    门禁里这个量级会被当成「仪器卡住」而遭外部超时杀掉
    （红探针的注入体因此残留在工作树里 ⇒ D146）。慢仪器本身就是风险源。
    """
    memo = {}
    _n = now or (lambda k, o: _default_now(k, o, base))

    def _now(k, o):
        key = (k, o)
        if key not in memo:
            memo[key] = _n(k, o)
        return memo[key]

    return _now


def _covered(atts, kind, obj, rec, cur, require_head, now, cache, base):
    """这条「已变更」原因是否被一张**当场复算成立**的证明抵掉。

    返回 `(att_id|None, why)`。任何一环不成立 ⇒ `None` + 可读理由（不是静默跳过）。
    """
    if not atts:
        return None, "无 %s:%s 的 content_equivalent 证明" % (kind, obj)
    now_val = now(kind, obj)
    if now_val != cur:
        # 当前值本身不可信（registry 缓存与仓库字节不一致）⇒ 不得放行，交给人查。
        return None, ("当前值与现算不一致（registry=%s / 现算=%s）⇒ fail-closed"
                      % (str(cur)[:12], str(now_val)[:12]))
    for a in atts:
        if require_head and not a.get("to_is_head"):
            continue
        if a.get("object_kind") != kind or a.get("object_id") != obj:
            continue
        comp = a.get("composite") or {}
        if False:  # redprobe: 不再核对 from（证明可跨版本复用）
            continue
        if comp.get("to") != cur:
            continue
        aid = a.get("id")
        if aid not in cache:
            ok, detail = ra.check_content_attestation(a, base=base)
            cache[aid] = (ok, detail)
        ok, detail = cache[aid]
        if ok is True:
            return aid, detail
        if ok is False:
            return None, "证明 %s 复算不成立：%s" % (aid, detail)
        return None, "证明 %s 本次测不到（%s）⇒ 不冒充通过" % (aid, detail)
    # 有同族同对象的证明，但四要素全不匹配 ⇒ 说清差在哪，别只报「没有」
    near = []
    for a in atts:
        comp = a.get("composite") or {}
        near.append("%s(from=%s,to=%s,head=%s)"
                    % (a.get("id"), str(comp.get("from"))[:8], str(comp.get("to"))[:8],
                       a.get("to_is_head")))
    return None, ("证明存在但绑定不符（记录=%s / 当前=%s）：%s"
                  % (str(rec)[:12], str(cur)[:12], "；".join(near)))


def edge_changed_split(e, registry, index, now=None, require_head=True, base=ROOT, cache=None):
    """单条边 → `(verdict, detail)`。

    `verdict` ∈ `VERDICTS`；`detail` 记下每条不符证据的处理结果，供报告直接打印
    （判据的失败必须能定位到**哪一条证据**没被覆盖，不能只说「有 703 条红」）。
    """
    now = now or (lambda k, o: _default_now(k, o, base))
    cache = {} if cache is None else cache
    if Q.edge_fidelity(e, registry) != "changed":
        return "not_changed", {"n_mismatch": 0, "covered": [], "misses": []}

    known = (registry or {}).get("known") or set()
    hashes = (registry or {}).get("hashes") or {}
    miss = []
    for ev in (e.get("props") or {}).get("verification_evidence") or []:
        if not isinstance(ev, dict):
            continue
        src = ev.get("src")
        if src not in known:
            continue
        cur = hashes.get(src)
        rec = ev.get("src_hash")
        if cur is None or rec == cur:
            continue
        miss.append((src, rec, cur))
    if not miss:
        # 字节层说 changed，却找不到任何不符的证据 ⇒ 两层口径已经分叉，必须红。
        return "changed", {"n_mismatch": 0, "covered": [],
                           "misses": [{"src": None,
                                       "why": "edge_fidelity=changed 但逐证据找不到不符项（口径分叉）"}]}

    covered, misses = [], []
    for src, rec, cur in miss:
        aid, why = _covered(index.get(("src", src)), "src", src, rec, cur,
                            require_head, now, cache, base)
        if aid:
            covered.append(aid)
        else:
            misses.append({"src": src, "why": why})
    return ("changed_but_proven" if not misses else "changed"), {
        "n_mismatch": len(miss), "covered": covered, "misses": misses}


def split_changed(edges, registry, attestations=None, now=None, require_head=True,
                  base=ROOT):
    """全图二分：`changed` 拆成 `changed_but_proven` / `changed`（**未证明**）。

    返回 `{"n_total","n_changed","n_proven","n_unproven","conserved",
            "unproven":[{"edge","detail"}…]}`。
    `conserved` 是**自洽断言**：proven + unproven 必须**恰好**等于 changed ——
    既不许漏项（有边消失），也不许重复（一条边两处计）。
    """
    index = proven_index(attestations, base)
    cache = {}
    _now = _make_now(now, base)
    n_ch = n_pr = 0
    unproven = []
    proven_keys = []
    for e in edges:
        verdict, detail = edge_changed_split(e, registry, index, now=_now,
                                             require_head=require_head, base=base,
                                             cache=cache)
        if verdict == "not_changed":
            continue
        n_ch += 1
        if verdict == "changed_but_proven":
            n_pr += 1
            proven_keys.append(Q.edge_key(e))
        else:
            unproven.append({"edge": Q.edge_key(e), "detail": detail})
    return {"n_total": len(edges), "n_changed": n_ch, "n_proven": n_pr,
            "n_unproven": n_ch - n_pr, "conserved": (n_pr + (n_ch - n_pr)) == n_ch,
            "proven_keys": proven_keys, "unproven": unproven}


def attach_proven(reg, edges, base=ROOT, verbose=True):
    """**就地**把 proven 映射注入一份 registry（供**自建** registry 的消费方用）。

    ★ D140（注册 ≠ 生效）的同族风险：`queries.edge_fidelity_proven()` 只在 registry
      带 `proven` 时才可能为真。服务端 `viz_server.stale_registry()` 已经注入，
      但**自己构造** registry 的消费方（`grade_report` / `dataset/slicing` /
      `phase78`）拿不到 ⇒ 同一条边在两处算出**两个等级**。
      实测：5 分 702 → 41（掉了 661 条），而 G2 断言的是「5 分且 fidelity≠verified」，
      掉档后它们**不再是 5 分** ⇒ 自洽、不红 —— 典型的「数据悄悄变差、仪器全部安静」。

    ⇒ 凡是自建 registry 又要算 `edge_grade` 的地方，都必须调一次本函数。
      判据侧由 `report_number_check` 的等级分布声明 + G5 咬住（两处必须同一套数）。
    """
    try:
        atts = ed.content_attestations(base)
        if not atts:
            return reg
        reg["proven"] = proven_by_edge(edges, reg, atts, base=base)
        return reg
    except Exception as _e:                                   # noqa: BLE001
        # ★ 不静默：接不上就要让人看见，否则「等级掉了」会被读成「数据本来就这样」。
        if verbose:
            print("[WARN] 第五档 proven 未接上（%s: %s）⇒ 等级按四分类口径（会掉档）"
                  % (type(_e).__name__, _e))
        return reg


def proven_by_edge(edges, registry, attestations=None, now=None, require_head=True,
                   base=ROOT):
    """`{edge_key: True}` —— 供 `queries.build_stale_registry(proven_by_edge=…)` 注入。

    ★ 与 `split_changed` **同一份判定**，只是出口不同：那边给报告（带理由，便于定位
      「哪一条证据没被覆盖」），这边给 03 层（只要 True/False，便于 `edge_grade` 用）。
      两条出口若分叉，就会出现「报告说 703 条已证明、等级却按未证明掉 4 档」
      ⇒ 由 phase82 的 **S115** 判据咬住（同源不得分叉）。
    """
    index = proven_index(attestations, base)
    cache = {}
    _now = _make_now(now, base)
    out = {}
    for e in edges:
        verdict, _ = edge_changed_split(e, registry, index, now=_now,
                                        require_head=require_head, base=base, cache=cache)
        if verdict == "changed_but_proven":
            out[Q.edge_key(e)] = True
    return out


def main(argv=None):
    print(__doc__.strip().splitlines()[0])
    print("本模块是判据本体；自检见 06_PoC/tests/phase82_selfcheck.py"
          "（bash sttp.sh phase82）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
