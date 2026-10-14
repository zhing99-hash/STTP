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
1. **重算哈希**（Phase 44 · D1 起）：对 `source_registry` 中每个来源重算**当前内容哈希**，
   权威字节源 = **仓库 blob**（`git cat-file blob HEAD:<path>`）⇒ **与检出/行尾无关**。
   `--byte-source worktree` 可显式切回「本机工作区字节」（**非权威**，仅供持有未入库缓存时人工复核）；
2. **定位受影响断言**：与证据里记录的 `src_hash`（生成时刻的不可变哈希）比对，
   凡不等 ⇒ 该来源**已变更** ⇒ 所有 `evidence[*].src == 该来源` 的边均为**待重验**；
3. **血缘传递**：若来源 A 的 `upstream` 含已变更来源（`imports_from`），则 A 的断言
   同样受影响（传递闭包）；
4. **重新验证**（`--reverify`）：对受影响边在新图上重跑 `verification_model.classify_all`，
   报告档位（level/scope）变化；
5. **非真空自检**（`--selfcheck`）：在临时 **git 仓库**里**变异并提交**某个来源的原始文件，
   断言「恰好」该来源（及其下游）的断言被标记，且**无假阳性**。

★ 诚实边界（两条，均**分列披露**、不假装已覆盖）：
  ① `lineage_recorded=False` 的来源（7 个模型产物）**无法传递失效** —— 训练快照版本从未记录；
  ② locator **未入库** 的来源（12 个 `*_raw.json`/`*_cache.json`，约 20 MB，按「大体积派生对象
     不入库」有意排除）**不可从仓库复算** ⇒ 其断言**不可比**（实测 47 055 条）。
     它们**未变更**，故**不计入**「已变更」；但对外必须与「已变更」**分列**并报，
     不得合并成单一数字（见 `queries.edge_fidelity` 的四分类）。

用法
----
    python 11_真实数据/lineage_invalidation.py            # 报告（rc=1 若存在已变更来源）
    python 11_真实数据/lineage_invalidation.py --json
    python 11_真实数据/lineage_invalidation.py --reverify
    python 11_真实数据/lineage_invalidation.py --selfcheck
    python 11_真实数据/lineage_invalidation.py --byte-source worktree   # ⚠ 非权威（随环境漂移）
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


def analyze(base=ROOT, edges=None, byte_source="repo"):
    """核心分析：返回 (changed_sources, affected_edges, 诊断)。

    `changed_sources`：**当前**哈希 ≠ 证据记录哈希 的来源集合。

    `byte_source`（Phase 44 · D1）：
      * `"repo"`（默认 · 权威）：当前哈希取自**仓库 blob** ⇒ 与检出/行尾无关。
        ⇒ 只有「locator 未入库」的来源无法参与比较，它们**分列**在诊断里
        （`not_reproducible_sources`），**不计入 changed**（它们并未变更）。
      * `"worktree"`（显式 opt-in · 非权威）：当前哈希取自**本机工作区**。
        仅用于「本机持有未入库缓存时，对 R10 做人工复核」；其结论**随环境漂移**，
        故**不得**作为对外裁决（这正是 Phase 43 §5-D1 的教训）。
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
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="证据血缘失效与重验（Phase 41 · R10）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--reverify", action="store_true")
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--byte-source", choices=("repo", "worktree"), default="repo",
                    help="当前哈希的字节源：repo（默认·权威·与检出无关）"
                         "｜worktree（显式 opt-in·非权威·结论随环境漂移，仅供人工复核）")
    a = ap.parse_args()

    if a.selfcheck:
        print("=" * 92)
        print("lineage_invalidation 判据非真空自检（变异一个来源 → 应恰好标记其断言）")
        print("=" * 92)
        rc = selfcheck()
        print("  ⇒ %s" % ("全过" if rc == 0 else "未通过"))
        return rc

    changed, affected, diag = analyze(byte_source=a.byte_source)
    if a.json:
        payload = {"schema": "sttp-lineage-invalidation/v2", "meta": diag}
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")

    print("=" * 92)
    print("证据血缘失效分析（Phase 41 · R10）")
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
    print("  **受影响断言       : %d**" % diag["n_affected_edges"])
    print("  ★ 缺 locator/未入库 ⇒ **不可从仓库复算**：%d 个来源 ⇒ 牵连 %d 条断言"
          "（**分列披露，不计入「已变更」** —— 它们并未变更）"
          % (diag["n_no_hash_sources"], diag["n_claims_on_not_reproducible"]))
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
