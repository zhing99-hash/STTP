#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""Phase 71 · 元素层重建与元素边来源订正

侦察结论（`07_交付物/Phase71_元素层许可侦察_20261022.md`）
------------------------------------------------------
F5 对外包 `composed_of` = 0、1,397 个分子没有组成关系。表层原因是「Element 节点来自
许可待核对的 ElementKG」，深层原因是**两件事叠在一起**：

    V1  图里 118 个 Element 节点来自 **ElementKG 1.0**（`elementkg_raw.json`），
        而 1.0 在 OpenKG 上**没有授权字段**，OWL 文件 4.07 MB 内
        `license|licence|creativecommons|cc-by|rights|dc:rights` **0 命中**。
        只有 2.0 标了 CC BY-SA（且不带版本号）⇒ **核 2.0 的许可救不了它**。
    V2  Element 节点其实是**两个源混合**：8 个真值字段（原子序数/周期/族/块/series/
        原子量/名/符号）来自 `element_reference`（Apache-2.0，节点自带
        `periodic_source: element_reference_iupac`），27 个物性字段（熔点/沸点/密度/
        电负性/电子构型…）才是 ElementKG 的。前端 `06_PoC/js/*.js` 与
        `graph_view.html` 对这 15 个物性字段名 **0 命中** ⇒ 剥离不影响 UI。
    V3  1,729 条元素边（`same_period` 1,355 对 / `same_family` 374 对）的 `verifier`
        是 `element_reference.py`、`explicit_or_inferred` 是 `inferred`，
        准入判据就写在 `elementkg_ingest.py:136-144`：`period_of()==period_of()` /
        `family_key()==family_key()` —— **ElementKG 的 hasPeriod*/hasFamily* 只提供候选对，
        能不能成立是我们的权威表判的**。它们被登记成 `source=ElementKG2.0`，
        于是被许可过滤器连坐剔除（D106）。

★★ 本脚本的硬线：**重新算，不是改标签**。
   `same_period` / `same_family` 一律用 `element_reference` 分组**重新生成**一份集合，
   然后断言「生成集 == 图上现有集」（E2/E3，双向差集都必须为空）。
   断言不过就**拒绝执行** —— 绝不允许「算出来不一样但还是把 source 改掉」。
   这样 `source` 被改成 `element_reference` 时，依据是「我们真的独立算了一遍且结果一致」，
   而不是「把别人的东西改个名据为己有」。

★★ 第二个硬线：**不删数据，只把不可外发的挪走**。
   27 个 ElementKG 物性字段剥离后写入 `11_真实数据/element_props_elementkg.json`
   （`.gitignore` 排除、标注 `internal_only`），供将来核下 1.0 条款后回填。
   剥离的是**分发资格**，不是**数据本身**。

判据（看**合并后的最终态**，因此可安全重跑）
------------------------------------------
    E1  118/118 个 Element 节点可由 element_reference 精确重建
    E2  same_period 重算集 == 现有集（缺口 0 / 多出 0）
    E3  same_family 重算集 == 现有集（缺口 0 / 多出 0）
    E4  edge id 与现有逐字一致（方向不动，整图 diff 才不会爆）
    E5  剥离后 Element props 不含任何 ElementKG 独有字段
    E6  幂等：重跑时本应无变更（合并后最终态达标）
    E7  负对照：把 er 的 period 改错一位 ⇒ E2 必须变红

用法：
    python 11_真实数据/phase71_element_delta.py [--dry] [--out PATH]
    python 11_真实数据/phase71_element_delta.py --selfcheck
    bash sttp.sh elementrebuild [--apply]
"""
import argparse
import collections
import datetime
import itertools
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import element_reference as er  # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase71_element_delta.json")
PROPS_ARCHIVE = os.path.join(HERE, "element_props_elementkg.json")

RULE_VERSION = "elem-r1"

# 真值 8 字段：element_reference 能给的（这是 Element 节点的**全部**合法内容）
ER_TRUTH_FIELDS = ("atomic_number", "atomic_weight", "period", "group",
                   "block", "series", "symbol", "name")
# 元数据 6 个：不是科学内容，是图谱自用的路由/登记/版本信息
META_FIELDS = ("id", "source", "domain", "ntype", "periodic_source",
               "element_source_version")
KEEP_FIELDS = set(ER_TRUTH_FIELDS) | set(META_FIELDS)

NEW_SOURCE = "element_reference"

# 两条元素边：语义都是「同一权威键 ⇒ 相连」，键全部来自 element_reference
DERIVED_EDGES = ("same_period", "same_family")


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def is_element(n):
    return "Element" in (n.get("labels") or [])


def family_key(sym):
    """IUPAC 族归属键：有族号用族号，f 区用 series。

    必须与 `elementkg_ingest.family_key` **逐字同口径** —— 侦察时我先用 `series`
    单一口径重算，只命中 184/374，差点得出「families 是 ElementKG 独有分类」的错误结论。
    口径不对，算出来的差距是假的（#113，同一个坑第三次）。
    """
    r = er.by_symbol(sym) if sym else None
    if not r:
        return None
    if r["group"] is not None:
        return ("g", r["group"])
    if r["series"]:
        return ("series", r["series"])
    return None


def key_of(reltype, props):
    """把一条元素边映射到它赖以成立的权威键。"""
    if reltype == "same_period":
        return er.period_of_z((props or {}).get("atomic_number"))
    if reltype == "same_family":
        return family_key((props or {}).get("symbol"))
    raise ValueError(reltype)


def derived_pairs(elements, reltype):
    """用 element_reference 重新生成元素边的**无序对**集合。

    ★ 完全不看 ElementKG 的 hasPeriod*/hasFamily* —— 只看权威表。
      生成结果与图上现有集合一致，才说明「这条边本来就是我们自己算的」。
    """
    groups = collections.defaultdict(list)
    for n in elements:
        p = n.get("props") or {}
        k = key_of(reltype, p)
        if k is not None:
            groups[k].append(n["id"])
    out = set()
    for _k, ids in groups.items():
        for a, b in itertools.combinations(sorted(ids), 2):
            out.add(frozenset((a, b)))
    return out


def rebuild_element(p):
    """返回 (新 props, 被剥离的字段 dict)。新 props 只含 er 真值 + 元数据。"""
    z = (p or {}).get("atomic_number")
    r = er.by_atomic_number(z) if z is not None else None
    if not r:
        return None, {}
    new = {}
    for f in ER_TRUTH_FIELDS:
        if r.get(f) is not None:
            new[f] = r[f]
    new["id"] = (p or {}).get("id")
    new["source"] = NEW_SOURCE
    new["domain"] = (p or {}).get("domain") or "chem.element"
    new["ntype"] = (p or {}).get("ntype") or "element"
    new["periodic_source"] = "element_reference_iupac"
    new["element_source_version"] = RULE_VERSION
    dropped = {k: v for k, v in (p or {}).items() if k not in KEEP_FIELDS}
    return new, dropped


# --------------------------------------------------------------------------
# 构建
# --------------------------------------------------------------------------

def build(nodes, edges):
    elements = [n for n in nodes if is_element(n)]
    st = collections.Counter()
    d_nodes, d_edges = [], []
    archive = {}
    unreconstructable = []

    # ---------- A · Element 节点重建 ----------
    for n in elements:
        p = n.get("props") or {}
        cur_src = p.get("source")
        cur_has_foreign = any(k not in KEEP_FIELDS for k in p)
        if cur_src == NEW_SOURCE and not cur_has_foreign:
            st["elem_already"] += 1
            continue
        new, dropped = rebuild_element(dict(p, id=n["id"]))
        if new is None:
            unreconstructable.append(n["id"])
            st["elem_no_er"] += 1
            continue
        d_nodes.append({
            "id": n["id"],
            "props": new,
            # ★ apply_delta 的 upsert 是 merged.update()，只能加改不能删 —— 必须显式 drop
            "props_drop": sorted(dropped),
        })
        if dropped:
            archive[n["id"]] = dropped
        st["elem_rebuilt"] += 1
        st["fields_dropped"] += len(dropped)

    # ---------- B · 元素边：重新生成 → 断言等于现有 → 才改来源 ----------
    el_after = []
    by_id = {n["id"]: n for n in nodes}
    for n in elements:
        p = dict(n.get("props") or {})
        new, _ = rebuild_element(dict(p, id=n["id"]))
        el_after.append({"id": n["id"], "labels": n.get("labels") or [],
                         "props": new if new else p})

    edge_judge = {}
    for reltype in DERIVED_EDGES:
        have_edges = [e for e in edges if e.get("type") == reltype]
        have_pairs = {frozenset((e["source"], e["target"])) for e in have_edges}
        gen_pairs = derived_pairs(el_after, reltype)
        missing = gen_pairs - have_pairs
        extra = have_pairs - gen_pairs
        edge_judge[reltype] = {
            "have_edges": len(have_edges),
            "have_pairs": len(have_pairs),
            "gen_pairs": len(gen_pairs),
            "missing": missing,
            "extra": extra,
        }
        # ★ 断言不过 ⇒ 拒绝改来源（宁可这轮什么都不做，也不能把来源改错）
        if missing or extra:
            st["edge_assert_failed_%s" % reltype] = len(missing) + len(extra)
            continue
        for e in have_edges:
            if (e.get("props") or {}).get("source") == NEW_SOURCE:
                st["edge_already"] += 1
                continue
            newp = dict(e.get("props") or {})
            newp["source"] = NEW_SOURCE
            newp["derivation"] = "recomputed_from_element_reference:%s" % reltype
            newp["derivation_version"] = RULE_VERSION
            newp.setdefault("verified", True)
            d_edges.append({
                "id": e["id"], "source": e["source"], "target": e["target"],
                "type": e["type"], "kind": e.get("kind") or newp.get("kind") or "",
                "props": newp,
            })
            st["edge_reowned"] += 1

    delta = {
        "meta": {
            "phase": 71,
            "rule_version": RULE_VERSION,
            "generated_at": _now(),
            "note": ("Element 节点改由 element_reference 重建（剥离 ElementKG 物性 %d 字段）；"
                     "same_period/same_family 用权威表重新生成并断言与现有集一致后，"
                     "来源订正为 element_reference" % len(KEEP_FIELDS)),
        },
        "nodes": d_nodes,
        "edges": d_edges,
    }
    return delta, st, edge_judge, archive, unreconstructable, el_after


# --------------------------------------------------------------------------
# 判据
# --------------------------------------------------------------------------

def simulate_merge(nodes, edges, delta):
    """在内存里按 `apply_delta` 的语义把 delta 合进去（不落盘、不碰原对象）。

    ★ 为什么要自己模拟一遍：E5/E6 的判定对象必须是**合并后的最终态**。
      直接看「本轮产出」（`st["elem_rebuilt"]`）会把「有没有改过」当成「改对了没有」——
      首次运行必红、二次运行必绿，判据等于什么也没测出来（#108）。
      这里刻意**复刻** apply_delta 的两条语义（节点 merged.update + props_drop；
      边按 id MERGE + props 合并），所以它和真正落盘的结果一致。
    """
    import copy
    m_nodes = copy.deepcopy(nodes)
    m_edges = copy.deepcopy(edges)
    by_id = {n["id"]: n for n in m_nodes}

    for dn in delta.get("nodes") or []:
        nid = dn.get("id")
        if nid not in by_id:
            continue
        old = by_id[nid]
        merged = dict(old.get("props") or {})
        merged.update(dn.get("props") or {})
        for k in (dn.get("props_drop") or []):
            merged.pop(k, None)
        old["props"] = merged
        if dn.get("labels"):
            old["labels"] = dn["labels"]

    e_by_id = {e.get("id"): e for e in m_edges}
    for de in delta.get("edges") or []:
        eid = de.get("id") or "%s|%s|%s" % (de.get("type"), de.get("source"), de.get("target"))
        if eid in e_by_id:
            tgt = e_by_id[eid]
            p = dict(tgt.get("props") or {})
            p.update(de.get("props") or {})
            tgt["props"] = p
            if de.get("kind"):
                tgt["kind"] = de["kind"]
    return m_nodes, m_edges

def judge(nodes, edges, st, edge_judge, el_after, unreconstructable, archive, merged=None):
    rep = []

    def add(ok, name, note):
        rep.append((name, ok, note))

    els = [n for n in nodes if is_element(n)]

    # E1 118/118 可由 er 重建
    ok_n = sum(1 for n in els if er.by_atomic_number((n.get("props") or {}).get("atomic_number")))
    add(ok_n == len(els) and len(els) > 0,
        "E1 每个 Element 节点都能由 element_reference 精确重建",
        "%d / %d 命中%s" % (ok_n, len(els),
                          "" if not unreconstructable else "；无法重建：%s" % unreconstructable[:3]))

    # E2/E3 重算集 == 现有集
    for reltype in DERIVED_EDGES:
        j = edge_judge.get(reltype) or {}
        ok = not j.get("missing") and not j.get("extra") and j.get("have_pairs", 0) > 0
        add(ok, "E%s %s 重算集 == 图上现有集（缺口/多出都为 0）"
            % ({"same_period": "2", "same_family": "3"}[reltype], reltype),
            "%d 条 = %d 对；生成 %d 对；缺口 %d；多出 %d"
            % (j.get("have_edges", 0), j.get("have_pairs", 0), j.get("gen_pairs", 0),
               len(j.get("missing") or ()), len(j.get("extra") or ())))

    # E4 edge id 与现有逐字一致（方向不动）
    idset = {e["id"] for e in edges}
    bad = [e["id"] for e in (st.get("_d_edges") or []) if e["id"] not in idset]
    add(not bad,
        "E4 改来源不动边 id/方向（整图 diff 可控）",
        "全部沿用现有 id" if not bad else "★ 出现新 id：%s" % bad[:3])

    # E5/E6 一律看**合并后的最终态**（#108：判据不能看「本轮产出」，
    #   否则首次运行必红、二次运行必绿 —— 那测的是「有没有改过」，不是「改对了没有」）
    m_nodes, m_edges = merged if merged else (nodes, edges)

    # E5 合并后不含 ElementKG 独有字段
    foreign = collections.Counter()
    for n in m_nodes:
        if not is_element(n):
            continue
        for k in (n.get("props") or {}):
            if k not in KEEP_FIELDS:
                foreign[k] += 1
    add(not foreign, "E5 合并后 Element props 只剩 er 真值字段 + 元数据",
        "白名单 %d 个字段，%d 个元素全部合规" % (len(KEEP_FIELDS), len([n for n in m_nodes if is_element(n)]))
        if not foreign else "★ 仍含 ElementKG 字段：%s" % foreign.most_common(5))

    # E6 幂等：把合并结果再跑一遍 build，必须 0 变更
    if merged:
        _d2, st2, ej2, _a2, _u2, _e2 = build(m_nodes, m_edges)
        leftover = st2["elem_rebuilt"] + st2["edge_reowned"]
        add(leftover == 0, "E6 幂等：合并后再跑一遍 build ⇒ 0 变更",
            "0 变更（节点 %d / 边 %d）" % (st2["elem_rebuilt"], st2["edge_reowned"]))
    else:
        add(False, "E6 幂等：合并后再跑一遍 build ⇒ 0 变更", "未提供合并结果，无法判定")

    return rep


def selfcheck():
    """离线自检（不读图、不写盘）。S3/S4 含负对照 —— 证明「绿」不是恒真。"""
    rep = []

    # S1 er 能给出全部 118 元素
    n_ok = len([z for z in range(1, 119) if er.by_atomic_number(z)])
    rep.append(("S1 element_reference 覆盖 1–118 号元素（%d/118）" % n_ok,
                n_ok == 118, "%d / 118" % n_ok))

    # S2 family_key 口径：必须复刻 IUPAC 18 族校正 ——
    #    Sc/Y/La/Ac 占 3 族位置（group=3），Ce–Lu 不占族号只由 series 表达。
    #    这正是 ElementKG hasFamily 的错误所在：它把「3 族 + 镧系 + 锕系」并成一个 32 元族。
    k_sc, k_la, k_ce = family_key("Sc"), family_key("La"), family_key("Ce")
    rep.append(("S2 family_key 按 IUPAC 18 族（La 占 3 族、Ce 归镧系，两者不同族）",
                k_sc == ("g", 3) and k_la == ("g", 3)
                and k_ce == ("series", "lanthanide") and k_sc != k_ce,
                "Sc=%s La=%s Ce=%s" % (k_sc, k_la, k_ce)))

    # S3 负对照：同一周期的两个元素必须在生成集里；跨周期必须不在
    el = [{"id": "E:Na", "props": {"atomic_number": 11, "symbol": "Na"}},
          {"id": "E:Mg", "props": {"atomic_number": 12, "symbol": "Mg"}},
          {"id": "E:K", "props": {"atomic_number": 19, "symbol": "K"}}]
    pairs = derived_pairs(el, "same_period")
    has = frozenset(("E:Na", "E:Mg")) in pairs
    not_has = frozenset(("E:Na", "E:K")) in pairs
    rep.append(("S3 same_period 生成正确（同周期相连、跨周期不相连）",
                has and not not_has, "Na-Mg=%s Na-K=%s" % (has, not_has)))

    # S4 负对照：把周期表改错 ⇒ 生成集必须随之改变（判据可红）
    orig = er.period_of_z
    try:
        er.period_of_z = lambda z: 1 if z in (11, 19) else orig(z)
        pairs2 = derived_pairs(el, "same_period")
        changed = frozenset(("E:Na", "E:K")) in pairs2
    finally:
        er.period_of_z = orig
    rep.append(("S4 负对照：周期判据被改坏 ⇒ 生成集必须变（否则 E2 恒真）",
                changed, "改坏后 Na-K 被错误连上 = %s" % changed))

    # S5 重建后的 Element props 不含 ElementKG 物性
    new, dropped = rebuild_element({
        "atomic_number": 26, "symbol": "Fe", "melting_point": 1811.0,
        "density": 7874.0, "electronegativity": 1.83, "source": "ElementKG2.0",
        "id": "EK:el:Fe", "domain": "chem.element", "ntype": "element"})
    ok = (new is not None and "melting_point" not in new and "density" not in new
          and new.get("symbol") == "Fe" and new.get("source") == NEW_SOURCE)
    rep.append(("S5 重建只留真值字段（物性进 archive 不进 props）",
                ok, "props=%s dropped=%d" % (sorted(new or {}), len(dropped))))

    # S6 负对照：重建不得凭空造原子量
    new2, _ = rebuild_element({"atomic_number": 999, "symbol": "Xx"})
    rep.append(("S6 负对照：表外原子序数 ⇒ 拒绝重建（不编造）",
                new2 is None, "atomic_number=999 ⇒ %s" % ("拒绝" if new2 is None else "★ 编出来了")))

    print("=" * 84)
    print("Phase 71 元素层重建 · 离线自检")
    print("=" * 84)
    n_fail = 0
    for name, ok, note in rep:
        if not ok:
            n_fail += 1
        print("[%s] %s  %s" % ("PASS" if ok else "FAIL", name, note))
    print("-" * 84)
    print("合计 %d 条，通过 %d，失败 %d" % (len(rep), len(rep) - n_fail, n_fail))
    return 0 if n_fail == 0 else 1


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Phase 71 元素层重建")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()

    if a.selfcheck:
        return selfcheck()

    g = json.load(open(NORM, encoding="utf-8"))
    nodes, edges = g["nodes"], g["edges"]

    delta, st, edge_judge, archive, unrecon, el_after = build(nodes, edges)
    st["_d_edges"] = delta["edges"]

    print("=" * 84)
    print("Phase 71 · 元素层重建（rule=%s）" % RULE_VERSION)
    print("  图谱当前 %d 节点 / %d 边" % (len(nodes), len(edges)))
    print("=" * 84)

    # ★ 断言失败的边型，其 delta 必须被丢弃（build 里已跳过；这里再兜一次）
    for reltype in DERIVED_EDGES:
        j = edge_judge[reltype]
        if j["missing"] or j["extra"]:
            before = len(delta["edges"])
            delta["edges"] = [e for e in delta["edges"] if e.get("type") != reltype]
            print("  ★ %s 重算与现有不一致（缺 %d / 多 %d）⇒ 拒绝改这一种边的来源"
                  "（丢弃 %d 条 delta）"
                  % (reltype, len(j["missing"]), len(j["extra"]), before - len(delta["edges"])))

    rep = judge(nodes, edges, st, edge_judge, el_after, unrecon, archive,
                merged=simulate_merge(nodes, edges, delta))

    print()
    print("-- 变更 --")
    print("  Element 节点重建 : %d 个（已达标 %d，无法重建 %d）"
          % (st["elem_rebuilt"], st["elem_already"], st["elem_no_er"]))
    print("  剥离字段总数     : %d（归档到 %s）"
          % (st["fields_dropped"], os.path.relpath(PROPS_ARCHIVE, ROOT).replace(os.sep, "/")))
    print("  元素边来源订正   : %d 条（已达标 %d）"
          % (st["edge_reowned"], st["edge_already"]))
    print()

    print("-- 判据 --")
    n_fail = 0
    for name, ok, note in rep:
        if not ok:
            n_fail += 1
        print("[%s] %s  %s" % ("PASS" if ok else "FAIL", name, note))

    # 边重算明细
    print()
    print("-- 元素边重算明细 --")
    for reltype in DERIVED_EDGES:
        j = edge_judge[reltype]
        print("  %-12s 现有 %d 条 / %d 对 ｜ 权威表重算 %d 对 ｜ 缺口 %d ｜ 多出 %d"
              % (reltype, j["have_edges"], j["have_pairs"], j["gen_pairs"],
                 len(j["missing"]), len(j["extra"])))

    # 归档（只读产物的落盘：这是 ElementKG 数据的内部副本，不入库）
    if archive and not a.dry:
        payload = {
            "meta": {
                "note": "ElementKG 物性字段的内部归档 —— 许可未明示，**不得进入任何对外包**；"
                        "仅为将来核下条款后回填而保留",
                "redistribution": "internal_only",
                "source": "ElementKG 1.0 / 2.0",
                "phase": 71, "rule_version": RULE_VERSION, "generated_at": _now(),
            },
            "elements": archive,
        }
        with open(PROPS_ARCHIVE, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, sort_keys=True, indent=1)
            f.write("\n")
        print()
        print("  归档写入: %s（%d 个元素）"
              % (os.path.relpath(PROPS_ARCHIVE, ROOT).replace(os.sep, "/"), len(archive)))

    print("-" * 84)
    if n_fail:
        print("  ★ %d 条判据红 ⇒ 不写 delta（先查清楚再跑）" % n_fail)
        return 1

    if a.dry:
        print("  --dry：未写 delta 文件")
        return 0

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(delta, f, ensure_ascii=False, sort_keys=True, indent=1)
        f.write("\n")
    print("  delta 写出: %s（%d 节点 / %d 边）"
          % (os.path.relpath(a.out, ROOT).replace(os.sep, "/"),
             len(delta["nodes"]), len(delta["edges"])))
    print("=" * 84)
    return 0


if __name__ == "__main__":
    sys.exit(main())
