# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
"""phase73_reverify_delta.py —— Phase 73：把「验证器等价性证明」落到证据上

背景（D109）
------------
`vch` 是验证器的**整文件**摘要。Phase 72 给 `verification_model.py` 加了 105 行
（只服务 `mentioned_in` 的新分支 + 新函数 + 两个新常量），**没删没改任何既有语句**，
但 32,609 条证据的 `vch` 全部对不上 ⇒ A04 判 `stale`（=「已变更」）⇒
`subgraph` V18h/V16c 红、全图 66.7% 的边在 `exclude_stale` 打开时会被清空。

这是**第二次**踩同一个坑（Phase 61 踩过并 revert，提交信息里就写着「会让 32,609 条
证据全部 stale」）。所以本轮不只修数据，还要修「这类失效凭什么可以不重跑就恢复」。

做什么
------
对每条**只因验证器版本变化**而 stale 的证据，**只加一个键**：

    rva  <attestation id>   —— 指回「验证器版本对 + 排除范围」的等价性证明

**`vch` 一个字都不改**（第一版改过，被 E6 当场抓出来）：改完之后「记录值 == 当前值」，
证据就凭哈希相等算 valid，`rva` 形同虚设 —— 那正是
`hash_rewritten_without_reverify` 要堵的假象。`vch` 要忠实地留在「产出这条证据时的
验证器版本」，由 `rva` 在**读取时**把「旧版本 → 当前版本」这段差异撑住；
证明一旦缺失或被改坏，证据立刻回落 `stale`（E6 就是这条的负对照）。

**其余一律不动**：`evd` / `src_hash` / `refs` / `dvs` / `impl` / `impl_ver` / `vch`。
不增删边、不碰节点。
`dvs` 刻意保持记录侧原样：新加的 `phase68_paper_fetch` 只被新分支用到，
记进 `dvs` 会让这 3.2 万条将来对**不相干**的改动继续过敏（这正是本轮要治的病）。

凭什么可以「不重跑」就恢复（举证责任在这里）
--------------------------------------------
`reverify_attest.py` 对着 git 历史里的**真 blob** 做结构证明：顶层定义一个不少、
保留函数的语句序列是「插入-only」、每处插入的控制流守卫**正向点名**被排除的边类型。
所以「被排除类型之外的边，验证器行为不变」是可机械复算的命题，不是印象。
本轮 delta 再做两道**范围**核对：被改的证据所在这条边的 `type` 必须在排除范围之外，
被排除类型的证据**一条都不许改**（E3）。

用法
----
    python 11_真实数据/phase73_reverify_delta.py [--dry] [--selfcheck] [--out <path>]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import evidence_deps as ed                              # noqa: E402
import source_registry as sr                            # noqa: E402
import reverify_attest as ra                            # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase73_reverify_delta.json")

# 只有这几条属于「已变更」；仅当**唯一**的已变更是 verifier_changed 时才处置
_OTHER_CHANGE = (ed.R_SRC_CHANGED, ed.R_REF_CHANGED, ed.R_DEP_CHANGED)

# 这些字段必须逐条保持原样（E4 逐字比对）
_UNTOUCHED = ("kind", "impl", "impl_ver", "indep", "detail", "src", "src_ver",
              "src_hash", "refs", "dvs", "evd", "vfr", "vch")


def eid_of(e):
    return e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))


def _pick_attestation(ev, cur, attests):
    """按「verifier + from(+记录侧) → 当前」挑 attestation；挑不到 ⇒ None（fail-closed）。"""
    vid = ev.get("vfr")
    if not vid:
        return None
    cur_ch = ((cur.get("verifiers") or {}).get(vid) or {}).get("code_hash")
    for a in (attests or {}).values():
        if (a.get("kind") == "code_equivalent" and a.get("verifier") == vid
                and a.get("from_code_hash") == ev.get("vch")
                and a.get("to_code_hash") == cur_ch):
            return a
    return None


def build(nodes, edges, cur, attests):
    """返回 `(delta, stats, rows)`；`rows` 供判据复核。"""
    out_edges, st = [], collections.Counter()
    rows = []
    for e in edges:
        props = e.get("props") or {}
        evs = props.get("verification_evidence")
        if not isinstance(evs, list):
            continue
        cid, etype = eid_of(e), e.get("type")
        new_evs, touched = list(evs), False
        for i, ev in enumerate(evs):
            if not isinstance(ev, dict) or not ed.has_recorded_deps(ev):
                continue
            rec = ed.recorded_deps(ev)
            status, reasons = ed.evidence_status(rec, cur)
            if status == "valid":
                st["skip_valid"] += 1
                continue
            has_vc = any(r.split(":")[0] == ed.R_VERIFIER_CHANGED for r in reasons)
            other = [r for r in reasons if r.split(":")[0] in _OTHER_CHANGE]
            if not has_vc:
                st["skip_no_verifier_change"] += 1
                continue
            if other:
                st["skip_other_change"] += 1
                continue
            att = _pick_attestation(ev, cur, attests)
            if att is None:
                st["skip_no_attestation"] += 1
                continue
            if ev.get("rva") == att["id"]:
                # ★ 幂等（E7）：已挂同一条证明 ⇒ 不再产出 delta。
                #   不能靠「合并态 status 会变 valid」来省这一步 —— 上面判的是**未带证明**
                #   的当前态（那是"要不要处置"的依据），与"是否已经处置过"是两件事。
                st["skip_already"] += 1
                continue
            if etype in (att.get("exclude_types") or []):
                # 证明**不管**这类边（新分支正是为它加的）⇒ 不许借用
                st["skip_excluded_type"] += 1
                continue
            ev2 = dict(ev)
            # ★ 绝不改 `vch`。第一版把 `vch` 改成当前值，E6 立刻抓出来：
            #   改完之后「记录值 == 当前值」⇒ 证据仅凭哈希相等就算 valid，`rva` **形同虚设**
            #   —— 那正是 `evidence_deps` 里 `hash_rewritten_without_reverify` 要堵的假象。
            #   正确做法：`vch` 忠实地留在「产出这条证据时的验证器版本」，由 `rva`
            #   在**读取时**把「旧版本 → 当前版本」这段差异撑住。证明没了 ⇒ 立刻回落 stale。
            ev2["rva"] = att["id"]
            new_evs[i] = ev2
            touched = True
            st["n_reverified"] += 1
            st["before:%s" % status] += 1
            rows.append({"edge": cid, "type": etype, "idx": i,
                         "evd": ev.get("evd"), "vch": ev.get("vch"),
                         "rva": att["id"]})
        if touched:
            out_edges.append({"id": cid, "source": e["source"], "target": e["target"],
                              "type": etype,
                              "kind": props.get("kind") or e.get("kind") or "",
                              "props": {"verification_evidence": new_evs}})
    delta = {"meta": {"phase": 73, "tag": "reverify", "base_graph": "06_PoC/etl/normalized.json",
                      "generator": "phase73_reverify_delta.py",
                      "changed_keys": ["verification_evidence"],
                      "slim": True,
                      "slim_note": "只交付 verification_evidence；apply_delta 对 edge props 浅合并"},
             "nodes": [], "delete_nodes": [], "edges": out_edges, "delete_edges": []}
    return delta, st, rows


def simulate_merge(nodes, edges, delta):
    """内存里复刻 `apply_delta` 的边语义（浅合并 props），供 E5/E6 判「合并后的最终态」。"""
    import copy
    ns, es = copy.deepcopy(nodes), copy.deepcopy(edges)
    by_id = {eid_of(e): e for e in es}
    for d in delta["edges"]:
        old = by_id.get(d["id"])
        if old is None:
            es.append(d)
            by_id[d["id"]] = d
            continue
        merged = dict(old.get("props") or {})
        merged.update(d.get("props") or {})
        old["props"] = merged
    return ns, es


def a04_distribution(edges, cur, attests=None):
    dist = collections.Counter()
    for e in edges:
        for ev in ((e.get("props") or {}).get("verification_evidence") or []):
            if isinstance(ev, dict) and ed.has_recorded_deps(ev):
                dist[ed.evidence_status(ed.recorded_deps(ev), cur, attestations=attests)[0]] += 1
    return dist


# ---------------------------------------------------------------------------
# 判据
# ---------------------------------------------------------------------------
def judge(nodes, edges, delta, st, rows, cur, attests, rep):
    # E1 基线唯一：记录侧只有一个旧 vch、当前只有一个新 vch，且都被证明覆盖
    froms = {ev.get("vch") for e in edges
             for ev in ((e.get("props") or {}).get("verification_evidence") or [])
             if isinstance(ev, dict) and ev.get("vfr") == "verification_model"}
    tos = {a.get("to_code_hash") for a in (attests or {}).values()}
    cur_ch = ((cur.get("verifiers") or {}).get("verification_model") or {}).get("code_hash")
    rep.append(("E1 基线唯一：记录侧 vch 单值且 == 证明的 from、当前 == 证明的 to",
                len(froms) == 1 and tos == {cur_ch}
                and next(iter(froms), None) in {a.get("from_code_hash") for a in attests.values()},
                "from=%s｜to=%s｜当前=%s" % (sorted(froms), sorted(tos), cur_ch)))

    # E2 证明仍成立（拿 git 历史真 blob 重算，不是读文件里的结论）
    chk = ra.check_attestations()
    rep.append(("E2 等价性证明**重算**通过（不信表里的结论）",
                bool(chk) and all(ok is True for _, ok, _ in chk),
                "；".join("%s:%s" % (i, d) for i, ok, d in chk)[:220]))

    # E3 范围：改的边类型必须在排除之外；被排除类型的证据一条没改
    excl = set().union(*[(a.get("exclude_types") or set()) for a in (attests or {}).values()]) \
        if attests else set()
    bad_type = [r for r in rows if r["type"] in excl]
    on_excl = [r for r in rows if r["type"] in excl]
    rep.append(("E3 范围正确：被改的证据都不在排除类型上（%s 上一条未改）" % sorted(excl),
                not bad_type,
                "改动 %d 条，落在排除类型上的 %d 条" % (len(rows), len(on_excl))))

    # E4 非破坏：逐条比对未动字段 + 规模不变 + 节点零改动
    old_by_id = {eid_of(e): e for e in edges}
    diff = []
    for d in delta["edges"]:
        old = old_by_id.get(d["id"]) or {}
        oe = (old.get("props") or {}).get("verification_evidence") or []
        ne = d["props"]["verification_evidence"]
        if len(oe) != len(ne):
            diff.append((d["id"], "len", len(oe), len(ne)))
            continue
        for a, b in zip(oe, ne):
            for k in _UNTOUCHED:
                if a.get(k) != b.get(k):
                    diff.append((d["id"], k))
            extra = set(b) - set(a) - {"rva"}
            if extra:
                diff.append((d["id"], "extra:%s" % sorted(extra)))
    rep.append(("E4 非破坏：**只加 rva 一个键**（vch/evd/src_hash/refs/dvs/impl 逐字不变）",
                not diff, "差异 %d 处%s" % (len(diff), "" if not diff else "：%s" % diff[:3])))
    rep.append(("E4b 规模不变、节点零改动",
                not delta["nodes"] and not delta["delete_edges"] and not delta["delete_nodes"],
                "nodes=%d delete_edges=%d" % (len(delta["nodes"]), len(delta["delete_edges"]))))

    # E5 效果：合并态重算 ⇒ 「已变更」为 0
    m_nodes, m_edges = simulate_merge(nodes, edges, delta)
    after = a04_distribution(m_edges, cur, attests)
    rep.append(("E5 合并态：A04 **已变更(stale) 归零**（其余分档如实保留）",
                after.get("stale", 0) == 0,
                "合并后 %s" % dict(sorted(after.items()))))

    # E6 反作弊：把证明的 to 改坏 ⇒ 合并态必须回到 stale（证明真的在管，不是摆设）
    forged = {k: dict(v, to_code_hash="dead" + str(i))
              for i, (k, v) in enumerate((attests or {}).items())}
    forged_after = a04_distribution(m_edges, cur, forged)
    rep.append(("E6 反作弊：证明的 to 被改坏 ⇒ 同一条证据回落 stale（缺证明不放过）",
                forged_after.get("stale", 0) == st.get("n_reverified", 0) > 0,
                "伪造后 stale=%d（=本轮改动 %d 条）"
                % (forged_after.get("stale", 0), st.get("n_reverified", 0))))

    # E7 幂等：合并态再 build ⇒ 0 变更
    d2, st2, _ = build(m_nodes, m_edges, cur, attests)
    rep.append(("E7 幂等：合并态下再跑一遍 ⇒ 0 变更", not d2["edges"],
                "二次变更 %d 条" % len(d2["edges"])))
    return m_nodes, m_edges


# ---------------------------------------------------------------------------
# 自检（构造性 + 负对照）
# ---------------------------------------------------------------------------
def selfcheck():
    rows = []

    def add(cond, name, detail=""):
        rows.append((None if cond is None else bool(cond), name, detail))

    ev = {"kind": "recompute", "impl": "verification_model.formula_members",
          "detail": "x", "indep": True, "src": "S1", "src_hash": "h1",
          "refs": {"dimension_table": "r1"}, "vfr": "verification_model",
          "vch": "OLD", "dvs": {"d": "1"}, "evd": "evd:aaaa"}
    cur = {"src_hashes": {"S1": "h1"}, "reference_hashes": {"dimension_table": "r1"},
           "verifiers": {"verification_model": {"code_hash": "NEW", "dependency_versions": {"d": "1"}}}}
    att = {"id": "rva:t", "kind": "code_equivalent", "verifier": "verification_model",
           "from_code_hash": "OLD", "to_code_hash": "NEW", "exclude_types": ["mentioned_in"]}

    def mk(etype, evv):
        return [{"id": "e1", "source": "s", "target": "t", "type": etype,
                 "props": {"verification_evidence": [evv]}}]

    st0 = ed.evidence_status(ed.recorded_deps(ev), cur)[0]
    add(st0 == "stale", "S1 前提：版本不同 ⇒ 未处置时确为 stale", st0)

    d, st, _ = build([], mk("has_symbol", ev), cur, {att["id"]: att})
    add(len(d["edges"]) == 1 and st["n_reverified"] == 1, "S1b 非排除类型 ⇒ 被处置",
        "改动 %d 条" % st.get("n_reverified", 0))

    d2, st2, _ = build([], mk("mentioned_in", ev), cur, {att["id"]: att})
    add(not d2["edges"] and st2.get("skip_excluded_type") == 1,
        "S2 负对照：证据在**被排除**类型上 ⇒ 拒绝借用证明", dict(st2))

    srcbad = dict(ev, src_hash="OTHER")
    d3, st3, _ = build([], mk("has_symbol", srcbad), cur, {att["id"]: att})
    add(not d3["edges"] and st3.get("skip_other_change") == 1,
        "S3 负对照：同时存在 src_changed ⇒ 不处置（证明只抵 verifier_changed）", dict(st3))

    d4, st4, _ = build([], mk("has_symbol", ev), cur, {})
    add(not d4["edges"] and st4.get("skip_no_attestation") == 1,
        "S4 负对照：没有证明 ⇒ 不处置（fail-closed）", dict(st4))

    old_evd = ed.evidence_id("e1", 0, ev)
    new_evd = ed.evidence_id("e1", 0, dict(ev, vch="NEW", rva="rva:t"))
    add(old_evd == new_evd, "S5 evidence_id 不含 vch ⇒ 改 vch 不改 evd（身份稳定）",
        "%s == %s" % (old_evd, new_evd))

    print("=" * 92)
    print("phase73_reverify_delta 自检（S1–S5；S2/S3/S4 三个负对照）")
    print("=" * 92)
    bad = 0
    for ok, name, detail in rows:
        print("  %s %s%s" % ("✅" if ok else "❌", name, "" if not detail else "｜%s" % (detail,)))
        bad += 0 if ok else 1
    print("  ⇒ %d/%d 通过" % (len(rows) - bad, len(rows)))
    return 0 if bad == 0 else 1


def main():
    ap = argparse.ArgumentParser(description="Phase 73 验证器等价性证明落图")
    ap.add_argument("--base", default=NORM)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()
    if a.selfcheck:
        return selfcheck()

    g = json.load(open(os.path.abspath(a.base), encoding="utf-8"))
    nodes, edges = g["nodes"], g["edges"]
    attests = ed.load_attestations(ROOT)
    cur = ed.current_snapshot(base=ROOT, byte_source="repo", src_hashes=sr.hash_map(ROOT))

    print("=" * 92)
    print("Phase 73 —— 验证器等价性证明落图（把「整文件哈希变了」与「判据语义变了」分开）")
    print("  基线 %d 节点 / %d 边｜证明 %d 条：%s"
          % (len(nodes), len(edges), len(attests), sorted(attests)))
    print("=" * 92)

    before = a04_distribution(edges, cur)
    print("  处置前 A04：%s" % dict(sorted(before.items())))

    delta, st, rows = build(nodes, edges, cur, attests)
    print("  本轮：%s" % dict(sorted(st.items())))
    print("  delta：%d 条边" % len(delta["edges"]))

    rep = []
    m_nodes, m_edges = judge(nodes, edges, delta, st, rows, cur, attests, rep)
    bad = 0
    print("\n【判据 E1–E7】")
    for name, ok, detail in rep:
        print("  %s %s%s" % ("✅" if ok else "❌", name, "" if not detail else "｜%s" % (detail,)))
        bad += 0 if ok else 1
    print("  ⇒ %d/%d" % (len(rep) - bad, len(rep)))
    if bad:
        print("[拒绝] 判据未过 ⇒ 不写 delta", file=sys.stderr)
        return 2

    if a.dry:
        print("[DRY] 未落盘")
        return 0
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        json.dump(delta, f, ensure_ascii=False, separators=(",", ":"))
    print("-> %s (%.2f MB)" % (os.path.relpath(a.out, ROOT), os.path.getsize(a.out) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
