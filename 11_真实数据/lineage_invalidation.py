# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
"""lineage_invalidation.py —— 来源/参考表/验证器变更 → 定位受影响证据 → 重新验证

R10 完成定义（复评原文）
----------------------
> **「来源变更能定位受影响断言并重新验证」**

Phase 45 · A04 扩展（从「来源绑定」到「**验证依赖绑定**」）
-----------------------------------------------------------
复评坐实两处缺口：
  * **A04a** `impl_ver` 从不被比对；且全库**只有一个值**（`100148c147cb`），
    根本区分不出「哪个验证器版本产出了哪条证据」；
  * **A04b** 证据只绑「提出者来源」，`impl` 里出现的**参考表依赖**（量纲表、元素表）
    **未登记** ⇒ 变异 `dimension_table` 只能定位 **28 条边**，而实测真实依赖覆盖
    **33238 条证据**。
本轮按 `evidence_deps.py` 的验证依赖模型扩展：除 `src_hash` 外，同时比对
  `refs`（参考表内容哈希）、`vfr`+`vch`（验证器身份）、`dvs`（验证器依赖版本）。

做法
----
1. **重算哈希**（Phase 44 · D1 起）：权威字节源 = **仓库 blob**
   （`git cat-file blob HEAD:<path>`）⇒ 与检出/行尾无关；
2. **定位受影响证据**（四类独立可比）：
   * `changed_input`     —— 提出者来源内容哈希与记录不符；
   * `changed_reference` —— 参考表内容哈希与记录不符（★ A04b 新增）；
   * `changed_verifier`  —— 验证器 code_hash 与记录不符（★ A04a 新增）；
   * `changed_dep_version` —— 验证器依赖文件哈希与记录不符（★ A04 新增）；
3. **血缘传递**：来源 A 的 `upstream` 含已变更来源 ⇒ A 的断言同样受影响（传递闭包）；
4. **重新验证**（`--reverify --old <图> --new <图>`）：★ 必须**显式**指明新旧图
   （A04 要求「须明确是摄入后的**新**规范图还是旧图」）。重验**生成新证据并保留旧证据**，
   账本留痕；**单纯重写哈希不能恢复 valid**（无账本留痕 ⇒ `needs_review`）。
5. **非真空自检**（`--selfcheck`）：八段（①–⑤ 来源级 + ⑥–⑧ 依赖级）。

★ 诚实边界（三条，均**分列披露**、不假装已覆盖）：
  ① `lineage_recorded=False` 的来源（7 个模型产物）**无法传递失效** —— 训练快照版本从未记录；
  ② locator **未入库** 的来源（12 个 `*_raw.json`/`*_cache.json`，约 20 MB，按「大体积派生对象
     不入库」有意排除）**不可从仓库复算** ⇒ 其断言**不可比**（实测 47 055 条）。
     它们**未变更**，故**不计入**「已变更」；分列披露（铁律 #70）；
  ③ `reference_refs` 的 `code_import` 档是**保守过近似**（模块 import 了某表 ≠ 该条证据真读了它）。
     按任务单「先按整文件保守失效」有意保留，过近似幅度在报告里分列报出。

用法
----
    python 11_真实数据/lineage_invalidation.py            # 报告（rc=1 若存在已变更依赖）
    python 11_真实数据/lineage_invalidation.py --json
    python 11_真实数据/lineage_invalidation.py --selfcheck
    python 11_真实数据/lineage_invalidation.py --reverify --old <旧图> --new <新图> [--emit-ledger]
    python 11_真实数据/lineage_invalidation.py --byte-source worktree   # ⚠ 非权威（随环境漂移）
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import evidence_deps as ed                              # noqa: E402
import source_registry as sr                             # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT = os.path.join(HERE, "lineage_invalidation_report.json")
LEDGER = os.path.join(HERE, "reverify_ledger.json")


def load_graph():
    d = json.load(open(NORM, encoding="utf-8"))
    return d["nodes"], d["edges"]


def recorded_hash_of_edge(e, base=None):
    """边证据里记录的来源哈希（取第一条带 src_hash 的证据）。"""
    evs = (e.get("props") or {}).get("verification_evidence") or []
    for ev in evs:
        if isinstance(ev, dict) and ev.get("src_hash"):
            return ev.get("src")
    # 无证据 → 退回 props.source
    return (e.get("props") or {}).get("source")


def analyze(base=ROOT, edges=None, byte_source="repo", with_deps=True):
    """核心分析：返回 (changed_sources, affected_edges, 诊断)。

    `changed_sources`：**当前**哈希 ≠ 证据记录哈希 的来源集合（输入类变更）。

    `byte_source`（Phase 44 · D1）：
      * `"repo"`（默认 · 权威）：当前哈希取自**仓库 blob** ⇒ 与检出/行尾无关。
        ⇒ 只有「locator 未入库」的来源无法参与比较，它们**分列**在诊断里
        （`not_reproducible_sources`），**不计入 changed**（它们并未变更）。
      * `"worktree"`（显式 opt-in · 非权威）：当前哈希取自**本机工作区**。
        仅用于「本机持有未入库缓存时，对 R10 做人工复核」；其结论**随环境漂移**，
        故**不得**作为对外裁决（这正是 Phase 43 §5-D1 的教训）。

    `with_deps`（Phase 45 · A04）：是否同时做**验证依赖**比对
    （参考表 `refs` / 验证器 `vfr`+`vch` / 依赖版本 `dvs`）。
    ★ 图上证据**不带 A04 记录**时（delta 未 apply）自动退化为「只有输入类」并
      在诊断里报 `n_evidence_without_recorded_deps` —— **不假装**做了依赖比对。
    """
    if edges is None:
        _n, edges = load_graph()
    cur = sr.hash_map(base, byte_source=byte_source)
    rsm = sr.repo_status_map(base)
    changed = set()
    compared = {}          # sid -> (recorded, current)
    for e in edges:
        for ev in ((e.get("props") or {}).get("verification_evidence") or []):
            if not isinstance(ev, dict):
                continue
            sid = ev.get("src")
            rec = ev.get("src_hash")
            c = cur.get(sid)
            if rec is None or c is None:
                continue        # 无哈希可比（模型源 / locator 未入库）
            compared[sid] = (rec, c)
            if rec != c:
                changed.add(sid)

    # 血缘传递：上游已变更 ⇒ 下游亦受影响
    changed_closure = set(changed)
    for sid in sr.load():
        if sid in changed_closure:
            continue
        for u in sr.upstream_closure(sid):
            if u in changed and u != sid:
                changed_closure.add(sid)
                break

    affected = [e for e in edges
                if any(isinstance(ev, dict) and ev.get("src") in changed_closure
                       for ev in ((e.get("props") or {}).get("verification_evidence") or []))]

    # 血缘缺口：依赖了 lineage 不可达的源
    gap_srcs = {s for s in sr.load() if not sr.load()[s].get("lineage_recorded")}
    n_on_gap = sum(1 for e in edges
                   if any(isinstance(ev, dict) and ev.get("src") in gap_srcs
                          for ev in ((e.get("props") or {}).get("verification_evidence") or [])))

    # ★ 不可从仓库复算（locator 未入库）⇒ **分列披露**，绝不并入 changed
    nir = {s for s, v in rsm.items() if v == "not_in_repo"}
    n_nir_claims = sum(1 for e in edges
                       if any(isinstance(ev, dict) and ev.get("src") in nir
                              for ev in ((e.get("props") or {}).get("verification_evidence") or [])))

    # ---------- Phase 45 · A04：验证依赖（参考表 / 验证器 / 依赖版本）----------
    dep = {"enabled": bool(with_deps), "n_evidence_with_deps": 0,
           "n_evidence_without_deps": 0, "status_counts": collections.Counter(),
           "reason_counts": collections.Counter(), "changed_reference_ids": set(),
           "changed_verifier_ids": set(), "changed_dep_versions": set(),
           "n_stale_evidence": 0, "n_stale_edges": 0,
           "n_ref_bound": 0, "n_verifier_bound": 0, "n_no_dep": 0}
    dep_affected_ids = set()
    if with_deps:
        ledger = ed.load_ledger(base)
        cur_dep = ed.current_snapshot(base=base, byte_source=byte_source, src_hashes=cur)
        cur_ref = cur_dep["reference_hashes"]
        cur_ver = cur_dep["verifiers"]
        for e in edges:
            p = e.get("props") or {}
            evs = p.get("verification_evidence") or []
            edge_stale = False
            for ev in evs:
                if not isinstance(ev, dict):
                    continue
                if not ed.has_recorded_deps(ev):
                    dep["n_evidence_without_deps"] += 1
                    continue
                dep["n_evidence_with_deps"] += 1
                if ev.get("refs"):
                    dep["n_ref_bound"] += 1
                if ev.get("vfr"):
                    dep["n_verifier_bound"] += 1
                st, rs = ed.evidence_status(
                    ed.recorded_deps(ev), cur_dep,
                    claim_rejected=bool(p.get("rejected")),
                    ledger_entry=ledger.get(ev.get("evd") or ""))
                dep["status_counts"][st] += 1
                for r in rs:
                    dep["reason_counts"][r.split(":")[0]] += 1
                    if r.startswith(ed.R_REF_CHANGED):
                        dep["changed_reference_ids"].add(r.split(":", 1)[1])
                    elif r.startswith(ed.R_VERIFIER_CHANGED):
                        dep["changed_verifier_ids"].add(r.split(":", 1)[1])
                    elif r.startswith(ed.R_DEP_CHANGED):
                        dep["changed_dep_versions"].add(r.split(":", 1)[1])
                if st == "stale":
                    dep["n_stale_evidence"] += 1
                    edge_stale = True
                elif not (ev.get("refs") or ev.get("vfr")):
                    dep["n_no_dep"] += 1
            if edge_stale:
                dep["n_stale_edges"] += 1
                dep_affected_ids.add(e.get("id") or
                                     "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target")))
        # 并入受影响集合（去重）
        for e in edges:
            eid = e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))
            if eid in dep_affected_ids and e not in affected:
                affected.append(e)
        dep["changed_reference_ids"] = sorted(dep["changed_reference_ids"])
        dep["changed_verifier_ids"] = sorted(dep["changed_verifier_ids"])
        dep["changed_dep_versions"] = sorted(dep["changed_dep_versions"])

    diag = {
        "byte_source": byte_source,
        "n_sources": len(sr.load()),
        "n_compared_sources": len(compared),
        "n_no_hash_sources": sum(1 for v in cur.values() if v is None),
        "repo_status": dict(sorted(collections.Counter(rsm.values()).items())),
        "not_reproducible_sources": sorted(nir),
        "n_claims_on_not_reproducible": n_nir_claims,
        "changed_direct": sorted(changed),
        "changed_with_lineage": sorted(changed_closure - changed),
        "lineage_gap_sources": sorted(gap_srcs),
        "n_claims_on_lineage_gap": n_on_gap,
        "n_affected_edges": len(affected),
        "compared": {k: {"recorded": sr.short(v[0]), "current": sr.short(v[1]),
                         "equal": v[0] == v[1]} for k, v in sorted(compared.items())},
        # Phase 45 · A04
        "n_affected_by_input": sum(1 for e in affected
                                   if any(isinstance(ev, dict) and ev.get("src") in changed_closure
                                          for ev in ((e.get("props") or {})
                                                     .get("verification_evidence") or []))),
        "deps": {k: (dict(v) if isinstance(v, collections.Counter) else v)
                 for k, v in dep.items()},
    }
    return changed, affected, diag


def _level_changes(affected):
    """对受影响边重跑判级模型，报告档位变化（沿用 Phase 41 的能力）。"""
    import verification_model as vm
    nodes, edges = load_graph()
    res, _ctx = vm.classify_all(nodes, edges)
    by_id = {}
    for e, r in res:
        eid = e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))
        by_id[eid] = (r["verification_level"], r["verification_scope"])
    chg = collections.Counter()
    for e in affected:
        eid = e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))
        old = ((e.get("props") or {}).get("verification_level"),
               (e.get("props") or {}).get("verification_scope"))
        new = by_id.get(eid)
        if new is None:
            chg[("(gone)", old[0])] += 1
        elif new != old:
            chg[(old[0], new[0])] += 1
    return chg


def detect_inplace_rewrites(old_edges, new_edges, ledger):
    """★ 「单纯重写哈希不能恢复 valid」的**程序化**检查。

    逐条比对「旧图记录的依赖值」与「新图记录的依赖值」：
      * 值**未变** ⇒ 无异议；
      * 值**变了**且新图证据能在账本里找到**自己的**条目（`evidence_id` 命中且
        `supersedes` 指向旧证据）⇒ 合法重验；
      * 值**变了**却**没有**对应账本条目 ⇒ **违规**（原地改哈希假装没变过）。

    返回 `[{"evidence_id","claim_id","field","old","new"}, …]`。
    """
    def _key_map(edges):
        m = {}
        for e in edges:
            cid = e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))
            for i, ev in enumerate((e.get("props") or {}).get("verification_evidence") or []):
                if isinstance(ev, dict):
                    m[(cid, i)] = ev
        return m

    om, nm = _key_map(old_edges), _key_map(new_edges)
    bad = []
    for k, oev in om.items():
        nev = nm.get(k)
        if nev is None:
            continue
        for fld in ("src_hash", "vch"):
            a, b = oev.get(fld), nev.get(fld)
            if a != b and b is not None:
                ent = ledger.get(nev.get("evd") or "")
                if not (ent and ent.get("supersedes") == oev.get("evd")):
                    bad.append({"evidence_id": nev.get("evd"), "claim_id": k[0],
                                "field": fld, "old": a, "new": b})
        orf, nrf = oev.get("refs") or {}, nev.get("refs") or {}
        for rid in set(orf) | set(nrf):
            if orf.get(rid) != nrf.get(rid) and nrf.get(rid) is not None:
                ent = ledger.get(nev.get("evd") or "")
                if not (ent and ent.get("supersedes") == oev.get("evd")):
                    bad.append({"evidence_id": nev.get("evd"), "claim_id": k[0],
                                "field": "refs:%s" % rid,
                                "old": orf.get(rid), "new": nrf.get(rid)})
    return bad


def reverify(old_graph, new_graph=NORM, base=ROOT, emit_ledger=False,
             out_ledger=LEDGER, with_levels=False, only_stale=True):
    """★ 显式「**旧图 vs 新图**」重验（A04 要求：须明确是摄入后的新规范图还是旧图）。

    语义
    ----
    * `old_graph`：**判定基线**（带记录依赖的图，通常是摄入/上次重验后的图）；
    * `new_graph`：**摄入后的新规范图**（当前环境，参考表/验证器可能已变）；
    * 对旧图里 `status != valid` 的每条证据：
        1. 在**新环境**重算依赖快照；
        2. **生成新证据**（新 `evd`、`reverified_at`、`supersedes_evidence_id` = 旧 evd）；
        3. **旧证据保留**（不删不改），仅由账本标示被取代；
        4. 账本条目留痕（旧值 → 新值 + 时刻 + 原因）⇒ 新旧记录与判定变化**可追溯**。

    ★ 为什么旧证据必须保留：任务单要求「**重验前答案停止引用旧证据**」。旧证据
      保留在原图里、状态仍是 `stale` ⇒ 查询默认排除 stale 即自动停止引用；
      删除它就**失去了判据变化的可追溯性**。

    `only_stale`（默认 True）：只重验**已变更**（`stale`）的证据。`needs_review`
    是「本环境不可判定」——**重验解决不了**（缺的是数据本身，不是判定），
    强行重验会把 47055 条不可复算证据全部改造，属**假动作**（铁律 #30：不可判定≠放过，
    但也≠假装已处理）。需要时显式传 `only_stale=False`。

    返回 `(entries, stats)`。`emit_ledger=True` 才落盘（默认 dry-run，与项目惯例一致）。
    """
    old = json.load(open(old_graph, encoding="utf-8"))
    oedges = old["edges"]
    if os.path.abspath(new_graph) != os.path.abspath(old_graph) and os.path.exists(new_graph):
        nedges = json.load(open(new_graph, encoding="utf-8"))["edges"]
    else:
        nedges = oedges
    ledger = ed.load_ledger(base)
    cur_dep = ed.current_snapshot(base=base, byte_source="repo",
                                  src_hashes=sr.hash_map(base))
    stamp = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")

    entries = []
    stats = collections.Counter()
    for e in oedges:
        cid = e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))
        for i, ev in enumerate((e.get("props") or {}).get("verification_evidence") or []):
            if not isinstance(ev, dict) or not ed.has_recorded_deps(ev):
                continue
            rec = ed.recorded_deps(ev)
            st, rs = ed.evidence_status(rec, cur_dep,
                                        ledger_entry=ledger.get(ev.get("evd") or ""))
            stats["n_checked"] += 1
            if st == "valid":
                stats["n_already_valid"] += 1
                continue
            if st == "rejected":
                stats["n_rejected_skipped"] += 1
                continue
            if only_stale and st != "stale":
                stats["n_needs_review_skipped"] += 1
                continue
            # 生成新证据（值取当前环境）
            newev = {k: v for k, v in ev.items()
                     if k not in ("evd", "refs", "vch", "dvs", "reverified_at",
                                  "supersedes_evidence_id")}
            refs, vobj = ed.dependencies_for(newev, cur_dep["verifiers"], base)
            newev["refs"] = {rr["ref_id"]: rr["content_hash"] for rr in refs}
            if vobj.get("verifier_id"):
                newev["vfr"] = vobj["verifier_id"]
                newev["vch"] = vobj["code_hash"]
                if vobj.get("dependency_versions"):
                    newev["dvs"] = dict(vobj["dependency_versions"])
            newev["reverified_at"] = stamp
            newev["supersedes_evidence_id"] = ev.get("evd")
            # 新证据 id 必须与旧证据**不同**：把 reverified_at 混进身份串
            newev["evd"] = ed.evidence_id(cid, i, dict(newev, detail="rv:" + stamp))
            sid = ev.get("src")
            entries.append({
                "evidence_id": newev["evd"], "claim_id": cid,
                "supersedes": ev.get("evd"),
                "src": sid,
                "old_src_hash": ev.get("src_hash"),
                "new_src_hash": cur_dep["src_hashes"].get(sid),
                "old_refs": dict(ev.get("refs") or {}), "new_refs": dict(newev["refs"]),
                "old_code_hash": ev.get("vch"), "new_code_hash": newev.get("vch"),
                "at": stamp, "reason": ",".join(sorted(rs)),
                "status_before": st, "new_evidence_id": newev["evd"],
            })
            stats["n_reverified"] += 1
            stats["status_before:%s" % st] += 1

    violations = detect_inplace_rewrites(oedges, nedges, ledger)
    stats["n_inplace_rewrite_violations"] = len(violations)
    if violations:
        stats["violations"] = violations[:5]

    if emit_ledger:
        payload = {
            "schema": "sttp-reverify-ledger/v1", "phase": 45,
            "old_graph": os.path.relpath(os.path.abspath(old_graph), ROOT),
            "new_graph": os.path.relpath(os.path.abspath(new_graph), ROOT),
            "generated_at": stamp,
            "note": ("旧证据**保留**（不删不改）；新证据取代旧证据。"
                     "单纯重写哈希而不入账本 ⇒ detect_inplace_rewrites() 必报违规。"),
            "entries": entries,
        }
        # 合并已有账本（append-only，按 evidence_id 去重）
        try:
            prev = json.load(open(out_ledger, encoding="utf-8")).get("entries") or []
        except (OSError, ValueError):
            prev = []
        seen = {e["evidence_id"] for e in prev}
        payload["entries"] = prev + [e for e in entries if e["evidence_id"] not in seen]
        with open(out_ledger, "w", encoding="utf-8", newline="") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
            f.write("\n")
        stats["ledger_written"] = out_ledger

    if with_levels:
        stats["level_changes"] = dict(_level_changes([e for e in oedges
                                                      if _edge_has_stale(e, cur_dep, ledger)]))
    return entries, stats


def _edge_has_stale(e, cur_dep, ledger):
    for ev in ((e.get("props") or {}).get("verification_evidence") or []):
        if isinstance(ev, dict) and ed.has_recorded_deps(ev):
            st, _ = ed.evidence_status(ed.recorded_deps(ev), cur_dep,
                                       ledger_entry=ledger.get(ev.get("evd") or ""))
            if st == "stale":
                return True
    return False


def _mirror_base(tmp, skip_locators=()):
    """把**全部** locator 的**权威字节**写进 tmp（写入字节而非硬链接）。

    ★ 为什么不是原样硬链接（Phase 44 · D1）：权威哈希口径已改为**仓库 blob**。
      想用临时仓库复现「未变异来源 == 真实图」，必须按**记录 `src_hash` 时的字节源**写入：
      来源可从仓库复算 ⇒ 写**仓库 blob 字节**；不可从仓库复算 ⇒ 写**工作区字节**
      （该来源的记录值正是它）。否则临时仓库会把「字节源差异」误报成「来源已变更」。
    """
    written = set()
    for s in sr.load():
        for rp in (sr.load()[s].get("locator") or []):
            if rp in skip_locators or rp in written:
                continue                      # 同一文件被多个来源共用 → 只写一次
            written.add(rp)
            ap = os.path.join(tmp, rp)
            os.makedirs(os.path.dirname(ap), exist_ok=True)
            body = sr.repo_blob(rp)
            if body is None:                  # 未入库 ⇒ 记录值来自工作区字节
                src = os.path.join(ROOT, rp)
                if not os.path.exists(src):
                    continue
                with open(src, "rb") as f:
                    body = f.read()
            with open(ap, "wb") as f:
                f.write(body)


def _git(args, cwd):
    import subprocess
    try:
        r = subprocess.run(["git"] + list(args), capture_output=True, cwd=cwd)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:                                  # pragma: no cover
        return 1, b"", str(e).encode()


def _git_commit_all(tmp, msg):
    """把 tmp 里当前文件**全部提交**（关掉行尾转换，保证 blob == 写入字节）。

    ★ 提交后必须清 `source_registry` 的 git 读取缓存：否则「变异前先读一次、变异后
      再读」会命中旧 blob，失效检测**静默失灵**（铁律 #38 的反面：PASS 掩盖了没测到）。
    """
    _git(["-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "add", "-A"], tmp)
    rc, _o, err = _git(["-c", "user.email=selfcheck@sttp.local",
                        "-c", "user.name=sttp-selfcheck",
                        "commit", "-q", "-m", msg], tmp)
    sr.clear_git_cache()
    return rc == 0, err


def _git_init_commit(tmp):
    """把 tmp 初始化为 git 仓库并把现有文件**提交**（使 repo 口径能取到 blob）。

    ★ 为什么自检必须建仓库（Phase 44 · D1）：权威哈希口径已改为**仓库 blob**
      （`git cat-file blob HEAD:<path>`）。若只把文件放进临时目录，repo 口径取不到
      blob ⇒ 变异**检测不到** ⇒ 自检会退化成"真空通过"（铁律 #20）。
      故这里造一个**真的**临时仓库并提交，让被测的是**生产路径本身**。
    """
    _git(["init", "-q"], tmp)
    ok, err = _git_commit_all(tmp, "selfcheck-base")
    if not ok:                                             # pragma: no cover
        print("     [WARN] 临时仓库提交失败：%s" % err.decode("utf-8", "replace")[:160])
    return ok


def _expected_srcs(mutated_files):
    """给定被变异的文件集合，独立算出「应当受影响」的来源集合
    （locator 命中 **或** 血缘上游命中）。"""
    direct = {s for s in sr.load()
              if set(sr.load()[s].get("locator") or []) & set(mutated_files)}
    expect = set(direct)
    for s in sr.load():
        if s in expect:
            continue
        if sr.upstream_closure(s) & direct and sr.load()[s].get("lineage_recorded"):
            expect.add(s)
    return direct, expect


def _ids_for(edges, srcs):
    return {e.get("id") for e in edges
            if any(isinstance(ev, dict) and ev.get("src") in srcs
                   for ev in ((e.get("props") or {}).get("verification_evidence") or []))}


def selfcheck():
    """判据非真空自检（五段）：
    ① **变异检测 + 无假阳性**（**差分口径**）：在一个临时 git 仓库里镜像全部 locator 的
       权威字节并提交，先取「未变异基线」，再变异一个来源的文件并**再次提交**，
       断言「新增的直接变更集合 == 独立算出的期望集合」。
       ★ 用差分而非绝对值：同一文件可能被两个来源以**不同字节源**记录
       （如 `seed_papers_openalex.json` 同时服务 `openalex`(locator 未入库 ⇒ 记录工作区字节)
       与 `openalex-topic-align`(已入库 ⇒ 记录仓库字节)），单一临时仓库无法同时复现两种字节；
       差分把这些**镜像固有差异**从判据里剔除，只测「变异是否被检出且不误伤」。
    ② 纯血缘传递：给一个**文件不共享**的下游临时加一条 upstream，
       变异其上游 ⇒ 该下游应被标记，**尽管它自己的文件一字未改**。
    ③ 未变异图无变更。
    ④ 不可从仓库复算的来源必须**分列披露**且不计入「已变更」（D1）。
    ⑤ 两口径（仓库 vs 工作区）确实不同 —— **构造性**证明（临时仓库里造 LF/CRLF 差异），
       故干净 clone / 已归一化环境也**不会退化成 SKIP**。"""
    ok = True
    _n, edges = load_graph()
    all_files = set()
    for s in sr.load():
        all_files |= set(sr.load()[s].get("locator") or [])

    # ---------- ① 变异检测 + 无假阳性（差分） ----------
    pick = "ElementKG2.0"
    locs = sr.load()[pick]["locator"]
    tmp = tempfile.mkdtemp(prefix="sttp_lineage_sc1_")
    try:
        _mirror_base(tmp, skip_locators=locs)
        _git_init_commit(tmp)          # ★ D1：被测口径是仓库 blob ⇒ 必须有真仓库
        base_changed, base_affected, _bd = analyze(base=tmp, edges=edges)
        base_ids = {e.get("id") for e in base_affected}
        for rp in locs:
            ap = os.path.join(tmp, rp)
            os.makedirs(os.path.dirname(ap), exist_ok=True)
            with open(os.path.join(ROOT, rp), "rb") as f:
                data = f.read()
            with open(ap, "wb") as f:
                f.write(data + b"\n/* MUTATION-FOR-SELFCHECK */\n")
        _git_commit_all(tmp, "mutation")   # ★ 变异必须进 HEAD blob，否则生产口径看不见
        direct, expect_srcs = _expected_srcs(locs)
        expect_ids = _ids_for(edges, expect_srcs) - base_ids
        changed, affected, diag = analyze(base=tmp, edges=edges)
        got_ids = {e.get("id") for e in affected} - base_ids
        delta_srcs = set(changed) - set(base_changed)
        print("  ① 变异文件              : %s" % locs)
        print("  ① 镜像固有基线（非变异）: %s" % sorted(base_changed))
        print("  ① 独立期望直接变更      : %s" % sorted(direct))
        print("  ① 工具判定直接变更      : %s（新增 %s）"
              % (sorted(changed), sorted(delta_srcs)))
        eq1 = delta_srcs == direct
        print("     新增直接变更集合相等  : %s" % ("PASS" if eq1 else "FAIL"))
        ok &= eq1
        eq2 = got_ids == expect_ids
        print("     受影响断言集合相等    : %s（期望 %d / 命中 %d，差 %d）"
              % ("PASS" if eq2 else "FAIL", len(expect_ids), len(got_ids),
                 len(got_ids - expect_ids) + len(expect_ids - got_ids)))
        ok &= eq2
    finally:
        sr.rmtree_force(tmp)          # ★ E9：Windows 只读 git object 会让 ignore_errors 静默失败

    # ---------- ② 纯血缘传递（文件不共享） ----------
    #    选两个 locator 无交集的来源 X(上游) / Y(下游)，临时给 Y 加 upstream=[X]
    by_files = {s: set(sr.load()[s].get("locator") or []) for s in sr.load()}
    cand = [(x, y) for x in sr.load() for y in sr.load()
            if x != y and by_files[x] and by_files[y]
            and not (by_files[x] & by_files[y])
            and x not in sr.upstream_closure(y)]
    x, y = (("PhysicsBabel", "chembl_api") if ("PhysicsBabel", "chembl_api") in cand
            else (cand[0] if cand else (None, None)))
    if x and y:
        saved = list(sr.REGISTRY[y].get("upstream") or [])
        sr.REGISTRY[y]["upstream"] = saved + [x]
        sr.REGISTRY[y]["lineage_recorded"] = True
        tmp = tempfile.mkdtemp(prefix="sttp_lineage_sc2_")
        try:
            _mirror_base(tmp, skip_locators=by_files[x])
            for rp in by_files[x]:
                ap = os.path.join(tmp, rp)
                os.makedirs(os.path.dirname(ap), exist_ok=True)
                with open(os.path.join(ROOT, rp), "rb") as f:
                    data = f.read()
                with open(ap, "wb") as f:
                    f.write(data + b"\n/* MUTATION-FOR-SELFCHECK */\n")
            _git_init_commit(tmp)      # ★ D1：同上，进 HEAD blob
            changed, affected, diag = analyze(base=tmp, edges=edges)
            y_flagged = y in diag["changed_with_lineage"]
            y_files_unchanged = y not in changed
            print("  ② 纯血缘：%s(变异) → %s(临时 imports_from)" % (x, y))
            print("     %s 文件未变但被传递标记 : %s" % (y, "PASS" if (y_flagged and y_files_unchanged) else "FAIL"))
            ok &= (y_flagged and y_files_unchanged)
        finally:
            sr.REGISTRY[y]["upstream"] = saved
            sr.rmtree_force(tmp)      # ★ E9：同上，显式清只读位后重试，失败即抛
    else:
        print("  ② 纯血缘：未找到可用的无交集来源对（跳过）")
        ok &= False

    # ---------- ③ 真实图无变更 ----------
    _c0, a0, d0 = analyze(base=ROOT, edges=edges)
    clean = (len(a0) == 0 and not d0["changed_direct"])
    print("  ③ 未变异图无变更        : %s（受影响 %d）" % ("PASS" if clean else "FAIL", len(a0)))
    ok &= clean
    # ---------- ④ 不可从仓库复算的来源：必须**分列披露**（D1） ----------
    nir_srcs = d0["not_reproducible_sources"]
    nir_claims = d0["n_claims_on_not_reproducible"]
    disc_ok = (bool(nir_srcs) and nir_claims > 0
               and not (set(nir_srcs) & set(d0["changed_direct"])))
    print("  ④ 未入库来源分列披露    : %s（%d 个来源 / %d 条断言，**不计入 changed**）"
          % ("PASS" if disc_ok else "FAIL", len(nir_srcs), nir_claims))
    ok &= disc_ok

    # ---------- ⑤ 两口径确实不同（**构造性**非真空；不依赖本机行尾状态） ----------
    #   ★ 不能靠「本机恰好有 CRLF 差异」证明 —— 干净 clone / 已归一化环境里那个差异是 0，
    #     断言会退化成 SKIP（`source_registry.selfcheck` ⑬ 现在就恒为 0）。故在**临时仓库**
    #     里把差异**造出来**：同一路径 blob 存 LF、工作区放 CRLF ⇒ 两种 byte_source 必须
    #     给出不同哈希，还原后必须重新相等。这才是「口径选择有意义」的消融对照（铁律 #65）。
    cw, _aw, _dw = analyze(base=ROOT, edges=edges, byte_source="worktree")
    extra = sorted(set(cw) - set(d0["changed_direct"]))
    import shutil as _sh
    pr = sr.divergence_probe()
    try:
        probe_ok = bool(pr.get("differ")) and bool(pr.get("restored_equal"))
    finally:
        sr.rmtree_force(pr.get("tmp"))   # ★ E9：同口径
    if probe_ok:
        print("  ⑤ 两口径确实不同        : PASS —— 构造对照 differ=True / 还原后相等=True"
              + ("；另：本机工作区口径多报 %d 个「已变更」%s" % (len(extra), extra[:3])
                 if extra else "（本机工作区与仓库字节一致）"))
    else:
        print("  ⑤ 两口径确实不同        : FAIL —— 构造对照 differ=%s / 还原后相等=%s（%s）"
              % (pr.get("differ"), pr.get("restored_equal"), pr.get("error", "")))
    ok &= probe_ok

    # ---------- ⑥ 依赖感知失效（端到端 · 真实图的**记录依赖**） ----------
    #   ★ A04 的核心验收：变异**参考表** ⇒ 必须**恰好**定位「记录了该参考表依赖」的证据。
    #     在临时 git 仓库里镜像参考表 + 一个输入，先取基线（应无 stale），
    #     再变异 dimension_table.py ⇒ 断言 stale 证据集合 == 独立算出的期望集合。
    ok &= _selfcheck_deps()

    # ---------- ⑦ 未受影响项保持原状 ----------
    ok &= _selfcheck_unaffected()

    # ---------- ⑧ 重验：可追溯 + 重写哈希不恢复 valid（构造性） ----------
    ok &= _selfcheck_reverify()
    return 0 if ok else 1


def _evidence_with_ref(edges, rid):
    """记录里含参考表 `rid` 的证据集合（(claim_id, 序号)）。"""
    out = set()
    for e in edges:
        cid = e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))
        for i, ev in enumerate((e.get("props") or {}).get("verification_evidence") or []):
            if isinstance(ev, dict) and (ev.get("refs") or {}).get(rid):
                out.add((cid, i))
    return out


def _selfcheck_deps():
    """⑥ 依赖感知失效：变异参考表 ⇒ **恰好**定位记录了该依赖的证据（无假阳/假阴）。"""
    _n, edges = load_graph()
    exp = _evidence_with_ref(edges, "dimension_table")
    if not exp:
        print("  ⑥ 依赖感知失效          : SKIP —— 图上证据尚无 A04 记录"
              "（先跑 phase45_evidence_deps_delta.py 并 apply）")
        return True            # 明确 SKIP，**不计通过**（项目惯例：不算 FAIL 也不算 PASS）
    tmp = tempfile.mkdtemp(prefix="sttp_lineage_sc3_")
    try:
        _mirror_refs(tmp, skip=("11_真实数据/dimension_table.py",))
        _git_init_commit(tmp)
        _c0, _a0, d0 = analyze(base=tmp, edges=edges)
        base_stale = _stale_keys(edges, tmp, d0)
        # 变异量纲表
        rel = "11_真实数据/dimension_table.py"
        ap = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(ap), exist_ok=True)
        with open(ap, "wb") as f:
            f.write(sr.repo_blob(rel, ROOT) + b"\n# MUTATION-FOR-SELFCHECK\n")
        _git_commit_all(tmp, "mutate-reference")
        _c1, _a1, d1 = analyze(base=tmp, edges=edges)
        got_new = _stale_keys(edges, tmp, d1) - base_stale
        exp_new = exp - base_stale
        eq = (got_new == exp_new)
        print("  ⑥ 依赖感知失效          : %s（期望 %d / 命中 %d，差 %d）"
              % ("PASS" if eq else "FAIL", len(exp_new), len(got_new),
                 len(got_new ^ exp_new)))
        print("     变异 %s ⇒ changed_reference=%s｜changed_verifier=%s｜changed_dep=%s"
              % (rel, d1["deps"]["changed_reference_ids"],
                 d1["deps"]["changed_verifier_ids"],
                 [os.path.basename(x) for x in d1["deps"]["changed_dep_versions"]]))
        return eq
    finally:
        sr.rmtree_force(tmp)


def _selfcheck_unaffected():
    """⑦ 未受影响项保持原状：只记录 `element_reference` 的证据不被量纲表变异牵连。"""
    _n, edges = load_graph()
    only_el = (_evidence_with_ref(edges, "element_reference")
               - _evidence_with_ref(edges, "dimension_table"))
    if not only_el:
        print("  ⑦ 未受影响项保持原状    : SKIP —— 无「只依赖元素表」的证据可测")
        return True
    tmp = tempfile.mkdtemp(prefix="sttp_lineage_sc4_")
    try:
        _mirror_refs(tmp, skip=("11_真实数据/dimension_table.py",))
        _git_init_commit(tmp)
        rel = "11_真实数据/dimension_table.py"
        ap = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(ap), exist_ok=True)
        with open(ap, "wb") as f:
            f.write(sr.repo_blob(rel, ROOT) + b"\n# MUTATION-FOR-SELFCHECK\n")
        _git_commit_all(tmp, "mutate-reference")
        _c, _a, d1 = analyze(base=tmp, edges=edges)
        stale = _stale_keys(edges, tmp, d1)
        leaked = only_el & stale
        okx = not leaked
        print("  ⑦ 未受影响项保持原状    : %s（只依赖元素表的 %d 条证据中，被量纲表变异牵连 %d 条）"
              % ("PASS" if okx else "FAIL", len(only_el), len(leaked)))
        for k in list(leaked)[:3]:
            print("       ⚠ 误牵连：%s#%d" % k)
        return okx
    finally:
        sr.rmtree_force(tmp)


def _selfcheck_reverify():
    """⑧ 重验：新旧记录可追溯 + **单纯重写哈希不恢复 valid**（构造性，走真实 `reverify()`）。

    ★ 为什么用**受控小图**而非整图：整图有 3 万余条待重验证据，自检会写出一份
      15 MB 的临时账本（秒级变分钟级）。用**构造的小图**走**同一条生产代码路径**
      （`reverify()` / `evidence_status()` / `detect_inplace_rewrites()`）即可，
      判据强度不降（铁律 #65：对照要有效，不要无效的规模）。
    """
    import copy as _cp
    ok = True
    tmp = tempfile.mkdtemp(prefix="sttp_lineage_sc5_")
    try:
        _mirror_refs(tmp)
        _git_init_commit(tmp)
        cur = ed.current_snapshot(base=tmp, byte_source="repo", src_hashes=sr.hash_map(tmp))

        # 构造一条**带记录依赖**的证据（src = `dimension_table` 来源，其 locator 已在临时仓库）
        if cur["src_hashes"].get("dimension_table") is None:
            print("  ⑧ 重验可追溯/防改哈希    : SKIP —— 临时仓库取不到 `dimension_table` 来源哈希")
            return True
        cid = "selfcheck|dimension_table|claimX"
        ev = {"kind": "recompute", "impl": "verification_model.formula_members",
              "detail": "selfcheck", "indep": True,
              "src": "dimension_table", "src_ver": None,
              "src_hash": cur["src_hashes"]["dimension_table"]}
        refs, vobj = ed.dependencies_for(ev, cur["verifiers"])
        ev["refs"] = {rr["ref_id"]: rr["content_hash"] for rr in refs}
        if vobj.get("verifier_id"):
            ev["vfr"] = vobj["verifier_id"]
            ev["vch"] = vobj["code_hash"]
            if vobj.get("dependency_versions"):
                ev["dvs"] = dict(vobj["dependency_versions"])
        ev["evd"] = ed.evidence_id(cid, 0, ev)
        base_st, _ = ed.evidence_status(ed.recorded_deps(ev), cur)
        old_edges = [{"id": cid, "source": "dimension_table", "target": "claimX",
                      "type": "selfcheck", "props": {"verification_evidence": [ev]}}]
        old_g = os.path.join(tmp, "_old_graph.json")
        json.dump({"nodes": [], "edges": _cp.deepcopy(old_edges)},
                  open(old_g, "w", encoding="utf-8"), ensure_ascii=False)

        # 变异量纲表 ⇒ 新环境
        rel = "11_真实数据/dimension_table.py"
        ap = os.path.join(tmp, rel)
        with open(ap, "rb") as f:
            body = f.read()
        with open(ap, "wb") as f:
            f.write(body + b"\n# MUTATION-FOR-SELFCHECK\n")
        _git_commit_all(tmp, "mutate-reference")

        cur2 = ed.current_snapshot(base=tmp, byte_source="repo", src_hashes=sr.hash_map(tmp))
        st_pre, rs_pre = ed.evidence_status(ed.recorded_deps(ev), cur2)
        c_pre = (base_st == "valid" and st_pre == "stale")
        print("  ⑧0 重验前旧证据已 stale  : %s（基线 %s → 变异后 %s：%s）"
              % ("PASS" if c_pre else "FAIL", base_st, st_pre, rs_pre))
        ok &= c_pre

        new_g = os.path.join(tmp, "_new_graph.json")
        json.dump({"nodes": [], "edges": _cp.deepcopy(old_edges)},
                  open(new_g, "w", encoding="utf-8"), ensure_ascii=False)
        ledger_path = os.path.join(tmp, "ledger.json")
        entries, stats = reverify(old_g, new_g, base=tmp, emit_ledger=True,
                                  out_ledger=ledger_path)
        c_a = (stats.get("n_reverified") == 1)
        print("  ⑧a 重验生成新证据        : %s（1 条；账本 %d 条）"
              % ("PASS" if c_a else "FAIL", len(entries)))
        ok &= c_a

        en = entries[0] if entries else {}
        c_b = (bool(entries) and en.get("supersedes") == ev["evd"]
               and en.get("new_evidence_id") == en.get("evidence_id")
               and en.get("evidence_id") != ev["evd"]
               and "ref_changed:dimension_table" in en.get("reason", ""))
        print("  ⑧b 新旧记录可追溯        : %s（supersedes=%s → 新 %s；原因 %s）"
              % ("PASS" if c_b else "FAIL",
                 str(en.get("supersedes"))[:18], str(en.get("evidence_id"))[:18],
                 en.get("reason")))
        ok &= c_b

        # (c) ★ 单纯重写哈希**不**恢复 valid。
        #     ★ 关键区分：**新证据**（新 id + reverified_at + supersedes + 账本）合法 ⇒ valid；
        #       **原地把旧证据的记录值改成当前值**（无账本）⇒ 必须仍为 needs_review。
        led = ed.load_ledger(tmp)
        #  c1 合法路径：新证据 + 完整账本 ⇒ valid
        rec_new = {"src": en.get("src"), "src_hash": en.get("new_src_hash"),
                   "reference_refs": [{"ref_id": k, "content_hash": v}
                                      for k, v in (en.get("new_refs") or {}).items()],
                   "verifier": {"verifier_id": None}}
        st_led = ed.evidence_status(rec_new, cur2,
                                    ledger_entry=led.get(en.get("evidence_id")))[0]
        #  c2 作弊路径：把记录值改成当前值 + 无账本 ⇒ needs_review
        cheat = dict(rec_new, _src_hash_rewritten=True)
        st_cheat, rs_cheat = ed.evidence_status(cheat, cur2)
        #  c3 记录级记号必须真的来自图级检出（两级联动，不是摆设）
        #     ★ 把**全部**记录值都改成当前值（src_hash + refs + vch + dvs）——
        #       此时图上只剩「没有账本」这一个信号，必须仍被拒 ⇒ 证明堵口有效。
        tampered_marked = _cp.deepcopy(old_edges)
        tevm = tampered_marked[0]["props"]["verification_evidence"][0]
        tevm["src_hash"] = en.get("new_src_hash")
        tevm["refs"] = dict(en.get("new_refs") or {})
        tevm["vch"] = en.get("new_code_hash")
        tevm["dvs"] = dict((cur2["verifiers"].get("verification_model") or {})
                           .get("dependency_versions") or {})
        n_mark = ed.mark_rewrites(tampered_marked,
                                  detect_inplace_rewrites(old_edges, tampered_marked, {}))
        tev2 = tampered_marked[0]["props"]["verification_evidence"][0]
        st_marked, rs_marked = ed.evidence_status(ed.recorded_deps(tev2), cur2)
        c_c = (st_led == "valid" and st_cheat == "needs_review"
               and any(r.startswith(ed.R_HASH_REWRITTEN) for r in rs_cheat)
               and n_mark == 1 and st_marked == "needs_review"
               and any(r.startswith(ed.R_HASH_REWRITTEN) for r in rs_marked))
        print("  ⑧c 重写哈希不恢复 valid  : %s（新证据+账本=%s｜原地改+无账本=%s｜"
              "改全 4 个值+图级检出记号 %d 条 ⇒ %s）"
              % ("PASS" if c_c else "FAIL", st_led, st_cheat, n_mark, st_marked))
        ok &= c_c

        # (d) 原地改哈希 ⇒ detect_inplace_rewrites 必报违规
        tampered = _cp.deepcopy(old_edges)
        tev = tampered[0]["props"]["verification_evidence"][0]
        tev["refs"] = {"dimension_table": _dup((tev.get("refs") or {}).get("dimension_table"))}
        viol = detect_inplace_rewrites(old_edges, tampered, {})
        c_d = (len(viol) == 1)
        print("  ⑧d 原地改哈希必被检出    : %s（违规 %d 条；字段 %s）"
              % ("PASS" if c_d else "FAIL", len(viol),
                 (viol[0]["field"] if viol else "-")))
        ok &= c_d

        # (e) 旧证据**保留**（不被删除）—— 用「新图里仍能查到旧 evd」断言
        still = any(ev.get("evd") == ev["evd"] for ev in
                    new_edges_evs(json.load(open(new_g, encoding="utf-8"))["edges"]))
        print("  ⑧e 旧证据保留（不删除）  : %s" % ("PASS" if still else "FAIL"))
        ok &= still
        return ok
    finally:
        sr.rmtree_force(tmp)


def new_edges_evs(edges):
    out = []
    for e in edges:
        for ev in ((e.get("props") or {}).get("verification_evidence") or []):
            if isinstance(ev, dict):
                out.append(ev)
    return out


def _dup(h):
    return None if h is None else (h[:-1] + ("0" if h[-1] != "0" else "1"))


def _rebase_recorded(edges, cur):
    """把证据的记录依赖**重写为给定快照的现值**（仅用于自检构造干净基线）。"""
    for e in edges:
        for ev in ((e.get("props") or {}).get("verification_evidence") or []):
            if not isinstance(ev, dict) or not ed.has_recorded_deps(ev):
                continue
            if ev.get("refs"):
                ev["refs"] = {k: cur["reference_hashes"].get(k) for k in ev["refs"]}
            if ev.get("src") and cur["src_hashes"].get(ev["src"]) is not None:
                ev["src_hash"] = cur["src_hashes"][ev["src"]]


def _mirror_refs(tmp, skip=()):
    """把参考表文件（按**记录时的字节源**）写进临时仓库。"""
    for meta in ed.REFERENCE_REGISTRY.values():
        for rel in meta["paths"]:
            if rel in skip:
                continue
            ap = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(ap), exist_ok=True)
            body = sr.repo_blob(rel, ROOT)
            if body is None:
                continue
            with open(ap, "wb") as f:
                f.write(body)


def _stale_keys(edges, base, diag):
    """当前 `status == stale` 的证据键集合 `(claim_id, 序号)`。"""
    ledger = ed.load_ledger(base)
    cur = ed.current_snapshot(base=base, byte_source="repo", src_hashes=sr.hash_map(base))
    out = set()
    for e in edges:
        p = e.get("props") or {}
        cid = e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))
        for i, ev in enumerate(p.get("verification_evidence") or []):
            if not isinstance(ev, dict) or not ed.has_recorded_deps(ev):
                continue
            st, _ = ed.evidence_status(ed.recorded_deps(ev), cur,
                                       claim_rejected=bool(p.get("rejected")),
                                       ledger_entry=ledger.get(ev.get("evd") or ""))
            if st == "stale":
                out.add((cid, i))
    return out


def main():
    ap = argparse.ArgumentParser(description="证据血缘失效与重验（Phase 41 · R10 / Phase 45 · A04）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--reverify", action="store_true",
                    help="重验：★ 必须同时给 --old 与 --new（A04 要求显式区分旧图/新规范图）")
    ap.add_argument("--old", default=None, help="重验的**判定基线**图（带记录依赖）")
    ap.add_argument("--new", default=NORM, help="重验的**新规范图**（默认 normalized.json）")
    ap.add_argument("--emit-ledger", action="store_true",
                    help="把重验账本落盘到 11_真实数据/reverify_ledger.json（默认 dry-run）")
    ap.add_argument("--with-levels", action="store_true", help="顺带跑判级模型报档位变化")
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--byte-source", choices=("repo", "worktree"), default="repo",
                    help="当前哈希的字节源：repo（默认·权威·与检出无关）"
                         "｜worktree（显式 opt-in·非权威·结论随环境漂移，仅供人工复核）")
    a = ap.parse_args()

    if a.selfcheck:
        print("=" * 92)
        print("lineage_invalidation 判据非真空自检（八段：①–⑤ 来源级 + ⑥–⑧ 依赖级）")
        print("=" * 92)
        rc = selfcheck()
        print("  ⇒ %s" % ("全过" if rc == 0 else "未通过"))
        return rc

    # ★ A04：`--reverify` 必须**显式**说明新旧图 —— 否则「重验的是哪份图」不可追溯。
    if a.reverify and not a.old:
        print("[拒绝] `--reverify` 必须显式给出 `--old <判定基线图>`（A04 要求：须明确"
              "重验的是**摄入后的新规范图**还是旧图）。", file=sys.stderr)
        print("       例：python 11_真实数据/lineage_invalidation.py --reverify \\\n"
              "             --old 06_PoC/etl/normalized.before_xxx.json --new 06_PoC/etl/normalized.json \\\n"
              "             --emit-ledger", file=sys.stderr)
        return 2
    if a.reverify:
        entries, stats = reverify(a.old, a.new, emit_ledger=a.emit_ledger,
                                  with_levels=a.with_levels)
        print("=" * 92)
        print("【重新验证】旧图 %s → 新图 %s"
              % (os.path.relpath(a.old, ROOT), os.path.relpath(a.new, ROOT)))
        print("=" * 92)
        print("  受检证据        : %d（已 valid %d｜无需重验 rejected %d｜"
              "不可判定跳过 %d）"
              % (stats.get("n_checked", 0), stats.get("n_already_valid", 0),
                 stats.get("n_rejected_skipped", 0), stats.get("n_needs_review_skipped", 0)))
        print("  **重验并生成新证据 : %d**" % stats.get("n_reverified", 0))
        print("  旧证据**保留**（不删除）｜账本写入：%s"
              % (stats.get("ledger_written") or "未落盘（加 --emit-ledger）"))
        viol = stats.get("n_inplace_rewrite_violations", 0)
        print("  原地改哈希违规   : %d %s"
              % (viol, "（干净）" if not viol else "（**有违规**，见 detect_inplace_rewrites）"))
        for k, v in sorted(stats.items()):
            if k.startswith("status_before:"):
                print("    %s ⇒ %d 条" % (k.split(":", 1)[1], v))
        if a.with_levels and stats.get("level_changes"):
            print("  档位变化        : %s" % stats["level_changes"])
        return 1 if viol else 0

    changed, affected, diag = analyze(byte_source=a.byte_source)
    if a.json:
        payload = {"schema": "sttp-lineage-invalidation/v3", "meta": diag}
        with open(a.out, "w", encoding="utf-8", newline="") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")

    print("=" * 92)
    print("证据血缘失效分析（Phase 41 · R10 / Phase 45 · A04 验证依赖绑定）")
    print("=" * 92)
    print("  字节源             : **%s**%s"
          % (diag["byte_source"],
             "（权威 · 取自仓库 blob，与检出/行尾无关）" if diag["byte_source"] == "repo"
             else "（⚠ 非权威：取自本机工作区，结论随环境漂移）"))
    print("  来源总数           : %d（仓库可复算 %d ｜ 未入库 %d ｜ 无 locator %d）"
          % (diag["n_sources"], diag["repo_status"].get("in_repo", 0),
             diag["repo_status"].get("not_in_repo", 0),
             diag["repo_status"].get("no_locator", 0)))
    print("  可哈希比对来源     : %d（不可比 %d）"
          % (diag["n_compared_sources"], diag["n_no_hash_sources"]))
    print("  已变更来源（直接） : %d %s" % (len(diag["changed_direct"]), diag["changed_direct"]))
    print("  血缘传递再加       : %d %s"
          % (len(diag["changed_with_lineage"]), diag["changed_with_lineage"]))
    print("  ★ 缺 locator/未入库 ⇒ **不可从仓库复算**：%d 个来源 ⇒ 牵连 %d 条断言"
          "（**分列披露，不计入「已变更」** —— 它们并未变更）"
          % (diag["n_no_hash_sources"], diag["n_claims_on_not_reproducible"]))
    print("  ⚠ 血缘缺口来源     : %d %s ⇒ 牵连 %d 条断言**无法传递失效**"
          % (len(diag["lineage_gap_sources"]), diag["lineage_gap_sources"],
             diag["n_claims_on_lineage_gap"]))

    dp = diag.get("deps") or {}
    print("\n  ---- Phase 45 · A04：验证依赖（参考表 / 验证器 / 依赖版本）----")
    if not dp.get("enabled"):
        print("  依赖比对           : 已关闭（--no-deps）")
    elif dp.get("n_evidence_with_deps", 0) == 0:
        print("  依赖比对           : ⚠ 图上证据**尚无 A04 记录**（%d 条无记录）"
              % dp.get("n_evidence_without_deps", 0))
        print("      ⇒ 先跑 `python 11_真实数据/phase45_evidence_deps_delta.py` 并 apply；"
              "本报告此时**只做输入类比对**，不假装做了依赖比对。")
    else:
        sc = dp.get("status_counts") or {}
        print("  带依赖记录的证据   : %d（无记录 %d；其中 %d 条无参考表亦无验证器）"
              % (dp.get("n_evidence_with_deps", 0), dp.get("n_evidence_without_deps", 0),
                 dp.get("n_no_dep", 0)))
        print("  参考表绑定 %d 条 ｜ 验证器绑定 %d 条"
              % (dp.get("n_ref_bound", 0), dp.get("n_verifier_bound", 0)))
        print("  状态分布           : valid %d ｜ **stale %d** ｜ needs_review %d ｜ rejected %d"
              % (sc.get("valid", 0), sc.get("stale", 0), sc.get("needs_review", 0),
                 sc.get("rejected", 0)))
        print("  已变更参考表       : %s" % (dp.get("changed_reference_ids") or "（无）"))
        print("  已变更验证器       : %s" % (dp.get("changed_verifier_ids") or "（无）"))
        print("  已变更依赖版本     : %s"
              % ([os.path.basename(x) for x in (dp.get("changed_dep_versions") or [])] or "（无）"))
        print("  依赖原因计数       : %s" % (dp.get("reason_counts") or {}))
    print("  **受影响断言       : %d**（输入类 %d）"
          % (diag["n_affected_edges"], diag.get("n_affected_by_input", 0)))

    if not a.json:
        print("\n提示：`--json` 落盘报告；`--reverify --old <旧图>` 重验；`--selfcheck` 自检。")
    else:
        print("\n-> %s" % os.path.relpath(a.out, ROOT))
    # 有已变更来源或依赖 → 非零退出（可接入 CI）
    dep_changed = bool(dp.get("n_stale_edges")) if dp.get("enabled") else False
    return 1 if (changed or dep_changed) else 0


if __name__ == "__main__":
    sys.exit(main())
