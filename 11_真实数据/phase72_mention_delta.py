#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""Phase 72 · 论文提及边的**档位登记**与**证据对象补齐**

起因（D108，`07_交付物/Phase71_元素层重建_20261022.md` §八）
----------------------------------------------------------
本轮第一次完整跑 `bash sttp.sh gatecheck`，`[3/3]` 的**正对照**（未注入的原图）就过不去：

    (q) evidence-traceable      34,870 条 level>=rule_checked 里 **101 条无证据链**
    (r) evidence-wellformed     证据对象覆盖 **48,712 / 48,813**
    (t) level-scope-reproducible **103 条**落库档位 ≠ 重算

那 101 条全是 `mentioned_in`（Paper→Formula，Phase 68 论文线）：
`rule=m1`、`matched_name`/`where`/`snippet`/`snippet` 都在，**就是没有 `verification_evidence`**；
而 `classify` 里**根本没有 m1 这一档** ⇒ 重算落到 B8「无来源无判据 ⇒ unverified」
⇒ 落库（rule_checked）≠ 重算（unverified）。

★ 处置的原则（用户选项 A：**补登记 + 补证据对象，一次做对**）
----------------------------------------------------------
不是把落库改小去迁就重算，而是**把 m1 登记进判级模型** —— 与 D106 同形：
**错的经常不是数据，是登记口径。** 具体三件事：

    ① `verification_model` 新增 A11 分支：`mentioned_in`/`openalex_name_mention`
       用 m1 重算（`name_mention_in_text`，与建边用的是同一份实现，不复制）。
    ② 重算的输入分两档，**刻意用两个 scope 区分强弱**：
         · `name_mention_title_recompute`  —— 输入是 **Paper 节点的 title**（与边的记录无关）
         · `name_mention_snippet_recompute`—— 输入是 **边自带的 snippet**（摘要未落库到节点，
                                              只能把规则重跑一遍 ⇒ 只证明"可复现"）
    ③ 本脚本把落库对齐重算，并给这 101 条补上 `verification_evidence`
       （由 `vm.build_evidence` **从重算结果构造**，铁律 #36：证据与档位同源，
       不是照着边的旧标签另写一份）。

★ 为什么 `mentioned_in` 只到 `rule_checked` 而不是更高
----------------------------------------------------
m1 只证明「这个名字连续出现在标题/摘要里」，**不**证明这篇论文真的在讨论该公式
（Phase 68 的人工裁决表里 reject 的那批就是字面命中但语境不对）。
故 claim 的措辞也写死为「文本中出现」，不写「讨论了该公式」。

判据（一律看**合并后的最终态**，因此可安全重跑 —— #108）
--------------------------------------------------
    E1  待改集合非空（清空即真空 ⇒ 红）
    E2  每条待改边都**有重算依据**（重算不可判定 ⇒ 拒绝落盘，不猜）
    E3  合并态：重算档位 == 落库档位（0 差异）—— 即 (t) 的最终态
    E4  合并态：全图证据对象覆盖 == 100% —— 即 (r) 的最终态
    E5  合并态：level>=rule_checked 的边**全部**有 indep=True 且 impl==verifier 的证据 —— 即 (q)
    E6  不增不删边、不改任何节点（规模与节点 props 逐字不变）
    E7  幂等：在合并态上再跑一遍 build ⇒ 0 变更（#115：判据不得依赖"还没跑过"）

自检（含负对照）
--------------
    S1  `name_mention_in_text` 三态：命中 / 不命中 / 不可判定
    S2  负对照：snippet 里**没有**该名字 ⇒ classify 必须判 unverified/mismatch
    S3  正对照：名字出现在 **Paper 节点 title** ⇒ 必须判 title 档（不是 snippet 档）
    S4  登记口径 == 事实：图上的 mentioned_in 全部是 openalex_name_mention 且全部被重算覆盖
    S5  `build_evidence(重算结果)` 的 impl == verifier 且 detail 非空（(q) 的前置）
    S6  幂等：合并态下再 build ⇒ 0 变更

用法
----
    python 11_真实数据/phase72_mention_delta.py [--dry] [--out PATH]
    python 11_真实数据/phase72_mention_delta.py --selfcheck
    bash sttp.sh mentionalign [--apply]
"""
import argparse
import copy
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "06_PoC"))

import verification_model as vm  # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase72_mention_delta.json")

RULE_VERSION = "mention-m1-r1"
# ★ 固定时间戳：delta 必须**逐字节可重放**（`export_dataset` 的 S5 二次重建会比对 sha256）。
EVIDENCE_AT = "2026-10-22T14:30:00+08:00"
MENTION_KIND = "openalex_name_mention"
MENTION_TYPE = "mentioned_in"


def _now():
    return EVIDENCE_AT


# --------------------------------------------------------------------------- 重算
def recompute(nodes, edges):
    """用 `verification_model` 逐边重算（**唯一口径**）。返回 (rows, ctx)。

    ★ 刻意**不自己实现判级**：落库与重算必须同源，否则又是一次「同一份证据两个结论」（#102）。
    """
    res, ctx = vm.classify_all(nodes, edges)
    return res, ctx


def build(nodes, edges):
    """产出 delta。返回 (delta, st, rows)。"""
    rows, ctx = recompute(nodes, edges)
    node_by_id = ctx["node_by_id"]

    todo_level = []      # 落库档位 ≠ 重算档位
    todo_evidence = []   # 有重算依据、但没有证据对象
    undecidable = []     # 重算不可判定（None）⇒ 不猜、不入 delta
    for e, r in rows:
        p = e.get("props") or {}
        eid = e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))
        if r is None:
            undecidable.append(eid)
            continue
        if p.get("verification_level") != r["verification_level"]:
            todo_level.append((e, r))
        if p.get("verification_evidence") is None:
            todo_evidence.append((e, r))

    seen = {}
    for e, r in todo_level + todo_evidence:
        eid = e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))
        seen[eid] = (e, r)

    delta_edges = []
    for eid, (e, r) in sorted(seen.items()):
        p = e.get("props") or {}
        props = dict(p)
        props["verification_level"] = r["verification_level"]
        props["verification_scope"] = r["verification_scope"]
        props["verifier"] = r["verifier"]
        props["verified"] = bool(vm.is_verified(r["verification_level"]))
        props["claim"] = vm.claim_of(e, node_by_id)
        props["verification_evidence"] = vm.build_evidence(r)
        props["evidence_at"] = _now()
        if r.get("note"):
            props["verification_note"] = r["note"]
        props["mention_rule_version"] = RULE_VERSION
        delta_edges.append({
            "id": eid,
            "kind": p.get("kind") or e.get("kind"),
            "type": e.get("type"),
            "source": e.get("source"),
            "target": e.get("target"),
            "props": props,
        })

    st = {
        "edges_total": len(edges),
        "todo_level": len(todo_level),
        "todo_evidence": len(todo_evidence),
        "delta_edges": len(delta_edges),
        "undecidable": len(undecidable),
        "mention_edges": len([1 for e in edges if e.get("type") == MENTION_TYPE]),
        "mention_recomputed": len(ctx.get("mention_ok") or {}),
        "by_scope": {},
    }
    for _eid, (_e, _r) in seen.items():
        st["by_scope"][_r["verification_scope"]] = st["by_scope"].get(_r["verification_scope"], 0) + 1

    delta = {
        "meta": {
            "phase": 72,
            "rule_version": RULE_VERSION,
            "note": "Phase 72：把论文提及规则 m1 登记进判级模型，并补齐 101 条边的证据对象（D108）",
            "generated_at": _now(),
            "verifier": vm.MENTION_VERIFIER,
        },
        "nodes": [],
        "edges": delta_edges,
    }
    return delta, st, rows


# ------------------------------------------------------------------- 合并态模拟
def simulate_merge(nodes, edges, delta):
    """复刻 `apply_delta` 的边语义（按 id MERGE + props 整体替换），不落盘。

    ★ 判据必须看**合并后的最终态**（#108）：只看「本轮产出」会导致首次运行必红、二次必绿。
    """
    m_nodes = copy.deepcopy(nodes)
    m_edges = copy.deepcopy(edges)
    e_by_id = {e.get("id"): e for e in m_edges}
    for de in delta.get("edges") or []:
        eid = de.get("id") or "%s|%s|%s" % (de.get("type"), de.get("source"), de.get("target"))
        if eid in e_by_id:
            e_by_id[eid]["props"] = dict(de.get("props") or {})
    return m_nodes, m_edges


def judge(nodes, edges, delta):
    """在**合并后的最终态**上打 E1–E7。返回 (rep, info)。"""
    rep = []
    m_nodes, m_edges = simulate_merge(nodes, edges, delta)

    # E1 非真空
    rep.append(("E1 待改集合非空（清空即真空 ⇒ 判据失效）",
                len(delta["edges"]) > 0, "%d 条待改" % len(delta["edges"])))

    # E2 每条都有重算依据
    bad = [e["id"] for e in delta["edges"]
           if not (e.get("props") or {}).get("verifier")]
    rep.append(("E2 每条待改边都有重算依据（无依据 ⇒ 不落盘、不猜）",
                not bad, "缺依据 %d 条 %s" % (len(bad), bad[:3]) if bad else "全部有依据"))

    # E3 合并态：落库 == 重算
    rows, _ctx = recompute(m_nodes, m_edges)
    mism = [(e.get("id"), (e.get("props") or {}).get("verification_level"),
             r["verification_level"]) for e, r in rows
            if (e.get("props") or {}).get("verification_level") != r["verification_level"]]
    rep.append(("E3 合并态：落库档位 == 重算档位（(t) 的最终态）",
                not mism, "差异 %d 条 %s" % (len(mism), mism[:3]) if mism else "0 差异"))

    # E4 合并态：证据对象覆盖 100%
    n_with = sum(1 for e in m_edges
                 if (e.get("props") or {}).get("verification_evidence") is not None)
    rep.append(("E4 合并态：全图证据对象覆盖 == 100%（(r) 的最终态）",
                n_with == len(m_edges), "%d / %d 条" % (n_with, len(m_edges))))

    # E5 合并态：strict 边全部有独立证据且 impl == verifier
    bad5 = []
    n_strict = 0
    for e in m_edges:
        p = e.get("props") or {}
        if not vm.is_strict(p.get("verification_level")):
            continue
        n_strict += 1
        evs = p.get("verification_evidence")
        good = [it for it in (evs or [])
                if isinstance(it, dict) and it.get("indep")
                and it.get("detail") and it.get("impl") == p.get("verifier")]
        if not good:
            bad5.append((e.get("id"), p.get("verifier")))
    rep.append(("E5 合并态：level>=rule_checked 全部有独立证据（(q) 的最终态）",
                not bad5, "strict %d 条，缺独立证据 %d 条 %s" % (n_strict, len(bad5), bad5[:3])
                if bad5 else "strict %d 条，均有独立证据" % n_strict))

    # E6 不增不删边、不改节点
    same_e = len(m_edges) == len(edges) and len(m_nodes) == len(nodes)
    same_n = all((m.get("props") or {}) == (o.get("props") or {})
                 for m, o in zip(m_nodes, nodes))
    rep.append(("E6 不增不删边、不改任何节点",
                same_e and same_n,
                "边 %d→%d；节点 props 逐字未变=%s" % (len(edges), len(m_edges), same_n)))

    # E7 幂等：合并态上再 build ⇒ 0 变更
    d2, _st2, _rows2 = build(m_nodes, m_edges)
    rep.append(("E7 幂等：合并态下再跑一遍 ⇒ 0 变更（#115：不看「还没跑过」）",
                len(d2["edges"]) == 0, "二次变更 %d 条" % len(d2["edges"])))

    # E8 ★判级模型**登记**了 m1（D108 的本体）
    #   ★ 为什么 E3 不够：E3 判的是「落库 == 重算」的**最终态**，而本 delta 的作用
    #     恰恰就是"把落库改成重算" ⇒ 只要重算被改坏成 `unverified`，delta 会**跟着把
    #     落库也降成 unverified**，E3 依然绿（两边都错，但一致）。
    #     也就是说 E3 只能证明"自洽"，**证明不了 m1 还在模型里**。
    #   ⇒ 这条直接查重算结果的 scope：m1 一旦被撤掉，这些边会落到 B8 兜底
    #     （scope=`unclassified`），于是**在这里**变红。
    rows_f, _ctx_f = recompute(nodes, edges)
    unreg = []
    n_mention = 0
    for e, r in rows_f:
        if e.get("type") != MENTION_TYPE:
            continue
        if ((e.get("props") or {}).get("kind") or e.get("kind") or "") != MENTION_KIND:
            continue
        n_mention += 1
        sc = (r or {}).get("verification_scope") or ""
        if not sc.startswith("name_mention_"):
            unreg.append((e.get("id"), sc))
    rep.append(("E8 判级模型**登记**了 m1（重算 scope 必须是 name_mention_*，"
                "落到 unclassified 即 m1 被撤）",
                n_mention > 0 and not unreg,
                "提及边 %d 条；未登记 %d 条 %s" % (n_mention, len(unreg), unreg[:2])
                if unreg else "提及边 %d 条，全部由 m1 判级" % n_mention))

    info = {"strict": n_strict, "with_evidence": n_with, "edges": len(m_edges)}
    return rep, info


# --------------------------------------------------------------------------- 自检
def selfcheck():
    rep = []

    # S1 三态
    ok_hit, why_hit = vm.name_mention_in_text("Newton's second law",
                                              "…we apply Newton's second law to the system…")
    ok_miss, why_miss = vm.name_mention_in_text("Kinetic energy",
                                                "the electron will return to the core")
    ok_na, why_na = vm.name_mention_in_text("", "anything")
    rep.append(("S1 name_mention_in_text 三态（命中/不命中/不可判定）",
                ok_hit is True and ok_miss is False and ok_na is None,
                "命中=%s；不命中=%s；空名=%s" % (ok_hit, ok_miss, ok_na)))

    # S2 负对照：snippet 里没有该名字 ⇒ 必须判否（证明 A11 不是恒真）
    nodes = [
        {"id": "PA:t", "labels": ["Paper"], "props": {"title": "Completely unrelated title"}},
        {"id": "FO:t", "labels": ["Formula"], "props": {"name": "Kinetic energy"}},
    ]
    edges = [{"id": "mentioned_in|PA:t|FO:t", "type": "mentioned_in",
              "source": "PA:t", "target": "FO:t",
              "props": {"kind": MENTION_KIND, "where": "abstract",
                        "matched_name": "Kinetic energy", "snippet": "no such phrase here",
                        "rule": "m1"}}]
    rows, _ = vm.classify_all(nodes, edges)
    r = rows[0][1]
    rep.append(("S2 负对照：snippet 里没有该名字 ⇒ 必须判 unverified/mismatch",
                r["verification_level"] == "unverified"
                and r["verification_scope"] == "name_mention_mismatch",
                "%s / %s" % (r["verification_level"], r["verification_scope"])))

    # S3 正对照：名字出现在 Paper 节点 title ⇒ 必须判 title 档（而非 snippet 档）
    nodes2 = [
        {"id": "PA:t2", "labels": ["Paper"],
         "props": {"title": "A note on Newton's second law"}},
        {"id": "FO:t2", "labels": ["Formula"], "props": {"name": "Newton's second law"}},
    ]
    edges2 = [{"id": "mentioned_in|PA:t2|FO:t2", "type": "mentioned_in",
               "source": "PA:t2", "target": "FO:t2",
               "props": {"kind": MENTION_KIND, "where": "title",
                         "matched_name": "Newton's second law", "snippet": "irrelevant",
                         "rule": "m1"}}]
    rows2, _ = vm.classify_all(nodes2, edges2)
    r2 = rows2[0][1]
    rep.append(("S3 正对照：命中 Paper 节点 title ⇒ 判 name_mention_title_recompute",
                r2["verification_level"] == "rule_checked"
                and r2["verification_scope"] == "name_mention_title_recompute",
                "%s / %s" % (r2["verification_level"], r2["verification_scope"])))

    # S4 登记口径 == 事实
    try:
        with open(NORM, encoding="utf-8") as fh:
            g = json.load(fh)
        men = [e for e in g["edges"] if e.get("type") == MENTION_TYPE]
        kinds = {(e.get("props") or {}).get("kind") for e in men}
        rows3, ctx3 = vm.classify_all(g["nodes"], g["edges"])
        cov = len(ctx3.get("mention_ok") or {})
        rep.append(("S4 图上的 mentioned_in 全部是 %s 且全部被重算覆盖" % MENTION_KIND,
                    bool(men) and kinds == {MENTION_KIND} and cov == len(men),
                    "%d 条；kind 集合 %s；重算覆盖 %d" % (len(men), sorted(kinds), cov)))
    except OSError as e:
        rep.append(("S4 图上的 mentioned_in 全部是 %s 且全部被重算覆盖" % MENTION_KIND,
                    None, "权威图不可用（RC=2 口径）：%s" % e))

    # S5 build_evidence 的 impl/detail 满足 (q)
    r_ok = {"verification_level": "rule_checked",
            "verification_scope": "name_mention_title_recompute",
            "verifier": vm.MENTION_VERIFIER, "detail": "命中片段"}
    ev = vm.build_evidence(r_ok)[0]
    rep.append(("S5 build_evidence：impl == verifier 且 detail 非空（(q) 的前置）",
                ev["impl"] == vm.MENTION_VERIFIER and bool(ev["detail"]) and ev["indep"] is True,
                "kind=%s indep=%s impl=%s" % (ev["kind"], ev["indep"], ev["impl"])))

    # S6 幂等（在真实图的合并态上）
    try:
        delta, st, _rows = build(g["nodes"], g["edges"])
        r6, _info = judge(g["nodes"], g["edges"], delta)
        ok6 = all(ok for _n, ok, _d in r6)
        rep.append(("S6 幂等 + 六条判据在真实图上全过（合并态）",
                    ok6, "待改 %d 条；判据 %s" % (st["delta_edges"],
                                              "全过" if ok6 else "有红")))
    except NameError:
        rep.append(("S6 幂等 + 六条判据在真实图上全过（合并态）",
                    None, "权威图不可用，跳过（非通过而非失败）"))

    print("\n【phase72_mention_delta 自检】")
    width = max(len(t) for t, _, _ in rep)
    bad = 0
    for title, ok, detail in rep:
        if ok is None:
            print("  [SKIP] %s  —— %s" % (title.ljust(width), detail))
            continue
        if not ok:
            bad += 1
        print("  [%s] %s  —— %s" % ("PASS" if ok else "FAIL", title.ljust(width), detail))
    print("-" * 78)
    print("  合计 %d 条，失败 %d" % (len(rep), bad))
    return 1 if bad else 0


# --------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="Phase 72：论文提及档位登记 + 证据对象补齐")
    ap.add_argument("--dry", action="store_true", help="只判不改")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--selfcheck", action="store_true", help="自检 S1–S6（含 2 个负对照）")
    a = ap.parse_args(argv)

    if a.selfcheck:
        return selfcheck()

    if not os.path.exists(NORM):
        print("[RC=2] 权威图不可用：%s" % NORM)
        return 2

    with open(NORM, encoding="utf-8") as fh:
        g = json.load(fh)
    nodes, edges = g["nodes"], g["edges"]

    delta, st, _rows = build(nodes, edges)
    rep, _info = judge(nodes, edges, delta)

    print("=" * 84)
    print("  Phase 72 · 论文提及档位登记 + 证据对象补齐（D108）")
    print("=" * 84)
    print("  图规模        : %d 节点 / %d 边" % (len(nodes), len(edges)))
    print("  mentioned_in  : %d 条（重算覆盖 %d）"
          % (st["mention_edges"], st["mention_recomputed"]))
    print("  待改（档位）  : %d 条 ｜ 待改（缺证据）: %d 条 ｜ 去重后: %d 条"
          % (st["todo_level"], st["todo_evidence"], st["delta_edges"]))
    print("  不可判定      : %d 条（不猜，不入 delta）" % st["undecidable"])
    for k in sorted(st["by_scope"]):
        print("    %-34s %d 条" % (k, st["by_scope"][k]))
    print()

    n_fail = 0
    for name, ok, note in rep:
        if not ok:
            n_fail += 1
        print("[%s] %s  %s" % ("PASS" if ok else "FAIL", name, note))
    print("-" * 84)

    if n_fail:
        print("  ★ %d 条判据红 ⇒ 不写 delta" % n_fail)
        return 1
    if a.dry:
        print("  --dry：未写 delta")
        return 0

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(delta, fh, ensure_ascii=False, sort_keys=True, indent=1)
        fh.write("\n")
    print("  delta 写出: %s（%d 条边）"
          % (os.path.relpath(a.out, ROOT).replace(os.sep, "/"), len(delta["edges"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
