# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
# -*- coding: utf-8 -*-
"""从 phase41 快照抽取 M2 原型用的真实子集（一次性工具，不入仓库）。

抽取策略：
  种子 = FO:emc2 / FO:fma
  1 跳全收（defines / has_symbol / same_as / derived_from）
  枢纽抽样：PQ:mass 的 12 条 has_symbol 入边（真实总数 5,000，标注 total）
  检索池：核心节点 + 全库 "mass" 命中 + 度数最高的 60 个公式 + 全部 PhysicalQuantity
证据等级按 GRADE_RULE=e1 现算（与《证据分级规范》一致）。
"""
import json
import sys
import collections
import re

sys.path.insert(0, "11_真实数据")
import source_registry as SR  # noqa: E402

SRC = "06_PoC/graph_data_phase41.json"
OUT = "C:/tmp/proto_data.json"

d = json.load(open(SRC, encoding="utf-8"))
N, E = d["nodes"], d["edges"]
idx = {n["id"]: n for n in N}

known = set(SR.ids())
hashes = SR.hash_map(".")
rstat = SR.repo_status_map(".")


def fidelity(e):
    evs = e.get("evidence") or []
    if not evs:
        return "unverifiable_no_source"
    mis = nir = nl = False
    for x in evs:
        if not isinstance(x, dict):
            return "unverifiable_no_source"
        s = x.get("src")
        if s not in known:
            return "unverifiable_no_source"
        cur = hashes.get(s)
        if cur is None:
            if rstat.get(s) == "not_in_repo":
                nir = True
            else:
                nl = True
            continue
        if x.get("src_hash") != cur:
            mis = True
    if mis:
        return "changed"
    if nir:
        return "unverifiable_not_in_repo"
    if nl:
        return "unverifiable_no_source"
    return "verified"


LV = {"by_construction": 4, "rule_checked": 4, "cross_source": 5,
      "source_asserted": 3, "model_inferred": 1}
FD = {"verified": 5, "unverifiable_not_in_repo": 2, "unverifiable_no_source": 1, "changed": 1}


def grade_of(e):
    lv = e.get("verification_level")
    f = fidelity(e)
    V, F = LV.get(lv, 1), FD.get(f, 1)
    evs = [x for x in (e.get("evidence") or []) if isinstance(x, dict)]
    srcs = {x.get("src") for x in evs}
    indep = sum(1 for x in evs if x.get("indep"))
    g = min(V, F)
    if g >= 3 and (len(srcs) >= 2 or indep >= 2):
        g = min(5, g + 1)
    return g, lv, f


deg = collections.Counter()
for e in E:
    deg[e["source"]] += 1
    deg[e["target"]] += 1

out_edges = collections.defaultdict(list)
in_edges = collections.defaultdict(list)
for e in E:
    out_edges[e["source"]].append(e)
    in_edges[e["target"]].append(e)

SEEDS = ["FO:emc2", "FO:fma"]
keep_n = set(SEEDS)
keep_e = []
seen_e = set()


def ekey(e):
    return (e["source"], e["target"], e["type"])


def add_edge(e, sample=False, total=None):
    k = ekey(e)
    if k in seen_e:
        return
    seen_e.add(k)
    g, lv, f = grade_of(e)
    keep_e.append({
        "s": e["source"], "t": e["target"], "ty": e["type"],
        "g": g, "lv": lv, "fd": f,
        "ei": e.get("explicit_or_inferred"),
        **({"sample": True, "total": total} if sample else {}),
    })


# ---- 1 跳 ----
for s in SEEDS:
    for e in out_edges[s] + in_edges[s]:
        keep_n.add(e["source"])
        keep_n.add(e["target"])
        add_edge(e)

# ---- 枢纽抽样：PQ:mass 的 has_symbol 入边（真实 5000 条） ----
mass_syms = [e for e in in_edges["PQ:mass"] if e["type"] == "has_symbol"]
total_mass_sym = len(mass_syms)
for e in mass_syms[:12]:
    keep_n.add(e["source"])
    add_edge(e, sample=True, total=total_mass_sym)

# ---- SY:m 的入边（9 条全收，真实） ----
for e in in_edges["SY:m"][:9]:
    keep_n.add(e["source"])
    add_edge(e)

# ---- 2 跳：已收公式的 defines 目标（补出物理量层） ----
for nid in list(keep_n):
    if idx.get(nid, {}).get("type") == "Formula":
        for e in out_edges[nid]:
            if e["type"] in ("defines", "has_symbol", "same_as", "derived_from"):
                keep_n.add(e["target"])
                add_edge(e)

# ---- 低档边：子集节点的 dimensionally_consistent / related_to（真实多为 E1） ----
for nid in list(keep_n):
    for e in out_edges[nid]:
        if e["type"] in ("dimensionally_consistent", "related_to") and len(keep_e) < 150:
            keep_n.add(e["target"])
            add_edge(e)

# ---- 每节点至多 8 条边，防止星形爆炸 ----
cnt = collections.Counter()
bounded = []
for e in keep_e:
    if cnt[e["s"]] < 8 or e["g"] >= 3:
        cnt[e["s"]] += 1
        bounded.append(e)
keep_e = bounded

nodes_out = []
for nid in sorted(keep_n):
    n = idx.get(nid)
    if not n:
        continue
    a = n.get("attrs") or {}
    nodes_out.append({
        "id": nid,
        "l": str(n.get("label") or a.get("name") or nid)[:90],
        "t": n.get("type"),
        "s": n.get("subject"),
        "d": n.get("domain"),
        "lx": (a.get("latex") or "")[:120],
        "de": (a.get("description") or "")[:200],
        "us": (a.get("usage") or "")[:200],
        "ap": a.get("applicability"),
        "as": a.get("assumptions"),
        "dg": deg.get(nid, 0),
        "nt": a.get("ntype"),
    })


def brief(n):
    a = n.get("attrs") or {}
    return {"i": n["id"], "l": str(n.get("label") or a.get("name") or n["id"])[:70],
            "t": n.get("type"), "s": n.get("subject"), "d": deg.get(n["id"], 0)}


# ---- 检索池 ----
pool = {}
for nid in keep_n:
    if nid in idx:
        pool[nid] = brief(idx[nid])
for n in N:
    blob = (str(n.get("label")) + " " + str((n.get("attrs") or {}).get("name"))).lower()
    if "mass" in blob:
        pool[n["id"]] = brief(n)
for n in sorted(N, key=lambda x: -deg.get(x["id"], 0)):
    if n.get("type") == "Formula" and len(pool) < 190:
        pool[n["id"]] = brief(n)
for n in N:
    if n.get("type") == "PhysicalQuantity":
        pool[n["id"]] = brief(n)

data = {
    "meta": {
        "snapshot": "graph_data_phase41.json",
        "head": "e0b38a3",
        "at": "2026-10-19",
        "nodes_total": len(N),
        "edges_total": len(E),
        "grade_rule": "e1",
        "subset_nodes": len(nodes_out),
        "subset_edges": len(keep_e),
        "pool_size": len(pool),
    },
    "nodes": nodes_out,
    "edges": keep_e,
    "pool": sorted(pool.values(), key=lambda x: -x["d"]),
    "hub": {
        "PQ:mass": {"in": len(in_edges["PQ:mass"]), "out": len(out_edges["PQ:mass"]),
                    "has_symbol": total_mass_sym},
        "total_has_symbol": sum(1 for e in E if e["type"] == "has_symbol"),
        "total_edges": len(E),
        "cross_source_total": sum(1 for e in E if e.get("verification_level") == "cross_source"),
        "cross_source_molecule": sum(1 for e in E if e.get("verification_level") == "cross_source" and idx.get(e["source"], {}).get("type") == "Molecule"),
        "derived_from_total": sum(1 for e in E if e["type"] == "derived_from"),
    },
}

json.dump(data, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print("nodes=%d edges=%d pool=%d" % (len(nodes_out), len(keep_e), len(pool)))
print("bytes=%d" % len(open(OUT, "rb").read()))
print("等级分布:", dict(collections.Counter(e["g"] for e in keep_e)))
print("类型分布:", dict(collections.Counter(n["t"] for n in nodes_out).most_common()))
