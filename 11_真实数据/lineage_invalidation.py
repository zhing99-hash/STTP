# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
"""lineage_invalidation.py —— 来源变更 → 定位受影响断言 → 重新验证（Phase 41 · R10）

R10 完成定义（复评原文）
----------------------
> **「来源变更能定位受影响断言并重新验证」**

做法
----
1. **重算哈希**：对 `source_registry` 中每个来源重算**当前内容哈希**；
2. **定位受影响断言**：与证据里记录的 `src_hash`（生成时刻的不可变哈希）比对，
   凡不等 ⇒ 该来源**已变更** ⇒ 所有 `evidence[*].src == 该来源` 的边均为**待重验**；
3. **血缘传递**：若来源 A 的 `upstream` 含已变更来源（`imports_from`），则 A 的断言
   同样受影响（传递闭包）；
4. **重新验证**（`--reverify`）：对受影响边在新图上重跑 `verification_model.classify_all`，
   报告档位（level/scope）变化；
5. **非真空自检**（`--selfcheck`）：在临时目录里**变异**某个来源的原始文件，
   断言「恰好」该来源（及其下游）的断言被标记，且**无假阳性**。

★ 诚实边界：`lineage_recorded=False` 的来源（6 个模型产物）**无法传递失效** ——
  其训练快照版本从未记录。本器对此**如实计数**，不假装已覆盖。

用法
----
    python 11_真实数据/lineage_invalidation.py            # 报告（rc=1 若存在已变更来源）
    python 11_真实数据/lineage_invalidation.py --json
    python 11_真实数据/lineage_invalidation.py --reverify
    python 11_真实数据/lineage_invalidation.py --selfcheck
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import source_registry as sr                             # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT = os.path.join(HERE, "lineage_invalidation_report.json")


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


def analyze(base=ROOT, edges=None):
    """核心分析：返回 (changed_sources, affected_edges, 诊断)。
    `changed_sources`：当前哈希 ≠ 证据记录哈希 的来源集合。"""
    if edges is None:
        _n, edges = load_graph()
    cur = sr.hash_map(base)
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
                continue        # 无哈希可比（模型/无 locator 源）
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

    diag = {
        "n_sources": len(sr.load()),
        "n_compared_sources": len(compared),
        "n_no_hash_sources": sum(1 for v in cur.values() if v is None),
        "changed_direct": sorted(changed),
        "changed_with_lineage": sorted(changed_closure - changed),
        "lineage_gap_sources": sorted(gap_srcs),
        "n_claims_on_lineage_gap": n_on_gap,
        "n_affected_edges": len(affected),
        "compared": {k: {"recorded": sr.short(v[0]), "current": sr.short(v[1]),
                         "equal": v[0] == v[1]} for k, v in sorted(compared.items())},
    }
    return changed, affected, diag


def reverify(affected):
    """对受影响边重跑判级模型，报告档位变化。返回统计 dict。"""
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


def _mirror_base(tmp, skip_locators=()):
    """把**全部** locator 文件镜像进 tmp（硬链接优先，失败退回复制），
    使未变异来源的哈希与真实图一致 —— 否则临时目录缺文件会伪造「全变更」。"""
    for s in sr.load():
        for rp in (sr.load()[s].get("locator") or []):
            if rp in skip_locators:
                continue
            ap = os.path.join(tmp, rp)
            os.makedirs(os.path.dirname(ap), exist_ok=True)
            if os.path.exists(ap):        # 同一文件被多个来源共用 → 只镜像一次
                continue
            src = os.path.join(ROOT, rp)
            if not os.path.exists(src):
                continue
            try:
                os.link(src, ap)          # 硬链接：零拷贝，NTFS 无需管理员
            except OSError:
                try:
                    shutil.copy2(src, ap)
                except shutil.SameFileError:
                    pass


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
    """判据非真空自检（两段）：
    ① 哈希检测 + 无假阳性：镜像全部 locator，只变异一个来源的文件，
       断言「命中集合 == 独立算出的期望集合」。
    ② 纯血缘传递：给一个**文件不共享**的下游临时加一条 upstream，
       变异其上游 ⇒ 该下游应被标记，**尽管它自己的文件一字未改**。"""
    ok = True
    _n, edges = load_graph()
    all_files = set()
    for s in sr.load():
        all_files |= set(sr.load()[s].get("locator") or [])

    # ---------- ① 哈希检测 + 无假阳性 ----------
    pick = "ElementKG2.0"
    locs = sr.load()[pick]["locator"]
    tmp = tempfile.mkdtemp(prefix="sttp_lineage_sc1_")
    try:
        _mirror_base(tmp, skip_locators=locs)
        for rp in locs:
            ap = os.path.join(tmp, rp)
            os.makedirs(os.path.dirname(ap), exist_ok=True)
            with open(os.path.join(ROOT, rp), "rb") as f:
                data = f.read()
            with open(ap, "wb") as f:
                f.write(data + b"\n/* MUTATION-FOR-SELFCHECK */\n")
        direct, expect_srcs = _expected_srcs(locs)
        expect_ids = _ids_for(edges, expect_srcs)
        changed, affected, diag = analyze(base=tmp, edges=edges)
        got_ids = {e.get("id") for e in affected}
        print("  ① 变异文件              : %s" % locs)
        print("  ① 独立期望直接变更      : %s" % sorted(direct))
        print("  ① 工具判定直接变更      : %s" % sorted(changed))
        eq1 = set(changed) == direct
        print("     直接变更集合相等      : %s" % ("PASS" if eq1 else "FAIL"))
        ok &= eq1
        eq2 = got_ids == expect_ids
        print("     受影响断言集合相等    : %s（期望 %d / 命中 %d，差 %d）"
              % ("PASS" if eq2 else "FAIL", len(expect_ids), len(got_ids),
                 len(got_ids - expect_ids) + len(expect_ids - got_ids)))
        ok &= eq2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

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
            changed, affected, diag = analyze(base=tmp, edges=edges)
            y_flagged = y in diag["changed_with_lineage"]
            y_files_unchanged = y not in changed
            print("  ② 纯血缘：%s(变异) → %s(临时 imports_from)" % (x, y))
            print("     %s 文件未变但被传递标记 : %s" % (y, "PASS" if (y_flagged and y_files_unchanged) else "FAIL"))
            ok &= (y_flagged and y_files_unchanged)
        finally:
            sr.REGISTRY[y]["upstream"] = saved
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        print("  ② 纯血缘：未找到可用的无交集来源对（跳过）")
        ok &= False

    # ---------- ③ 真实图无变更 ----------
    _c0, a0, d0 = analyze(base=ROOT, edges=edges)
    clean = (len(a0) == 0 and not d0["changed_direct"])
    print("  ③ 未变异图无变更        : %s（受影响 %d）" % ("PASS" if clean else "FAIL", len(a0)))
    ok &= clean
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="证据血缘失效与重验（Phase 41 · R10）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--reverify", action="store_true")
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    if a.selfcheck:
        print("=" * 92)
        print("lineage_invalidation 判据非真空自检（变异一个来源 → 应恰好标记其断言）")
        print("=" * 92)
        rc = selfcheck()
        print("  ⇒ %s" % ("全过" if rc == 0 else "未通过"))
        return rc

    changed, affected, diag = analyze()
    if a.json:
        payload = {"schema": "sttp-lineage-invalidation/v1", "meta": diag}
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")

    print("=" * 92)
    print("证据血缘失效分析（Phase 41 · R10）")
    print("=" * 92)
    print("  来源总数           : %d" % diag["n_sources"])
    print("  可哈希比对来源     : %d（无 locator/哈希 %d）"
          % (diag["n_compared_sources"], diag["n_no_hash_sources"]))
    print("  已变更来源（直接） : %d %s" % (len(diag["changed_direct"]), diag["changed_direct"]))
    print("  血缘传递再加       : %d %s"
          % (len(diag["changed_with_lineage"]), diag["changed_with_lineage"]))
    print("  **受影响断言       : %d**" % diag["n_affected_edges"])
    print("  ⚠ 血缘缺口来源     : %d %s ⇒ 牵连 %d 条断言**无法传递失效**"
          % (len(diag["lineage_gap_sources"]), diag["lineage_gap_sources"],
             diag["n_claims_on_lineage_gap"]))

    if a.reverify:
        print("\n【重新验证】对受影响边重跑判级模型")
        if not affected:
            print("  受影响断言 0 —— 无需重验（当前所有来源哈希与记录一致）")
        else:
            chg = reverify(affected)
            if not chg:
                print("  重验 %d 条：档位**无变化**" % len(affected))
            for k, v in chg.most_common():
                print("   %s -> %s : %d" % (k[0], k[1], v))

    if not a.json:
        print("\n提示：`--json` 落盘报告；`--reverify` 重验；`--selfcheck` 非真空自检。")
    elif a.json:
        print("\n-> %s" % os.path.relpath(a.out, ROOT))
    # 有已变更来源 → 非零退出（可接入 CI）
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
