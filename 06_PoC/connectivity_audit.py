# -*- coding: utf-8 -*-
"""跨学科连通性审计 —— STTP 的「北极星仪器」。

为什么需要
----------
STTP 的北极星是**跨学科连通性**（数学 ↔ 物理 ↔ 化学），而此前没有任何工具
能一次性回答四个关键问题：

  1. 图谱是「一张网」还是「若干孤岛」？          → 连通分量 / 孤立节点
  2. 有多少边真的跨学科？跨在哪些学科之间？      → 跨域边矩阵
  3. 各学科占比是否失衡（物理独大风险）？        → 学科分布
  4. 学科标注本身可信吗？                        → 命名空间 × 学科 一致性

第 4 点尤其重要：**跨域统计建立在学科标注之上**。若标注错了，
「跨域边数」测的就是噪声。本工具因此把「标注一致性」也纳入输出。

用法
----
    python 06_PoC/connectivity_audit.py                      # 人类可读报告
    python 06_PoC/connectivity_audit.py --json out.json      # 同时落盘机器可读结果
    python 06_PoC/connectivity_audit.py --strict             # 存在孤立/悬空/自环时退非零
    python 06_PoC/connectivity_audit.py --input <other>.json # 指定输入（默认 normalized.json）

退出码：0 正常；2 仅在 --strict 且检出缺陷时。
"""

import argparse
import collections
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import graph_export  # noqa: E402

DEFAULT_IN = os.path.join(HERE, "etl", "normalized.json")
# 真孤岛冻结清单（Phase 30 新增）：把「撤回假边后暴露的真孤岛」显式冻结，
# 使 `--strict` 能对**孤岛集合的变动**报警 —— 而不是像以前那样只看孤立/悬空/自环。
# 背景：`--strict` 此前**不检查连通分量数**，导致「分量 1」这个北极星口径曾一度
# 只能靠人肉阅读，孤岛回归不会被任何门禁拦住（典型的「仪器缺一环」）。
KNOWN_ISLANDS = os.path.join(HERE, "known_islands.json")

# 枢纽命名空间：**有意跨学科**，其「混标」不是缺陷，不该报警（否则 --strict 变成狼来了）。
#   MX: Phase5 枢纽层，显式汇聚 chem(chemistry) / phy(physics) / math.DG / cross 四类 domain；
#   PQ: Phase5 数值层 + Phase8 元素性质层（chem.element_property）共用同一前缀。
# 真正需要报警的是**切片命名空间被误标**（例如 2026-10-09 修掉的 BC:rx 生物化学落「跨学科」）。
HUB_NAMESPACES = {"MX", "PQ"}


# ---------------------------------------------------------------------------
# 并查集（用于连通分量；图规模 ~7k 节点 / 40k 边，线性可承受）
# ---------------------------------------------------------------------------
class DSU:
    def __init__(self, keys):
        self.p = {k: k for k in keys}

    def find(self, x):
        p = self.p
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def audit(g, subjects=None):
    """对一张 {nodes, edges} 原始图做审计，返回结构化结果 dict。"""
    nodes = g.get("nodes") or []
    edges = g.get("edges") or []
    by_id = {n["id"]: n for n in nodes}

    def ptype(n):
        return graph_export.pick_type(n.get("labels"))

    # 学科：允许外部覆盖（用于「修档前/后」对比）
    if subjects is None:
        subjects = {n["id"]: graph_export.subject_of((n.get("props") or {}).get("domain"), n["id"])
                    for n in nodes}

    # ---- 度 ----
    deg = collections.Counter()
    for e in edges:
        deg[e["source"]] += 1
        deg[e["target"]] += 1

    # ---- 连通分量 ----
    dsu = DSU([n["id"] for n in nodes])
    for e in edges:
        if e["source"] in dsu.p and e["target"] in dsu.p:
            dsu.union(e["source"], e["target"])
    comp = collections.defaultdict(list)
    for n in nodes:
        comp[dsu.find(n["id"])].append(n["id"])
    comps = sorted(comp.values(), key=len, reverse=True)
    isolates = [n["id"] for n in nodes if deg[n["id"]] == 0]

    # ---- 跨域边 ----
    same = cross = 0
    pair = collections.Counter()
    cross_type = collections.Counter()
    for e in edges:
        s, t = e["source"], e["target"]
        if s not in subjects or t not in subjects:
            continue
        if subjects[s] == subjects[t]:
            same += 1
        else:
            cross += 1
            pair[tuple(sorted((subjects[s], subjects[t])))] += 1
            cross_type[e["type"]] += 1
    total_edges = same + cross

    # ---- 命名空间 × 学科 ----
    ns_subj = collections.defaultdict(collections.Counter)
    for n in nodes:
        ns_subj[n["id"].split(":")[0]][subjects[n["id"]]] += 1

    # ---- 结构缺陷 ----
    nid_set = set(by_id)
    dangling = [e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))
                for e in edges if e.get("source") not in nid_set or e.get("target") not in nid_set]
    selfloop = [e.get("id") for e in edges if e.get("source") == e.get("target")]

    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "subjects": dict(collections.Counter(subjects.values()).most_common()),
        "types": dict(collections.Counter(ptype(n) for n in nodes).most_common()),
        "components": {
            "count": len(comps),
            "largest": [len(c) for c in comps[:5]],
            "isolated_count": len(isolates),
            "isolated_sample": isolates[:25],
            "small": [
                {"size": len(c), "subjects": dict(collections.Counter(subjects[x] for x in c)),
                 "types": dict(collections.Counter(ptype(by_id[x]) for x in c)),
                 "sample": c[:4], "nodes": sorted(c)}
                for c in comps if len(c) < 100
            ],
        },
        "cross_domain": {
            "edges_total": total_edges,
            "same": same,
            "cross": cross,
            "cross_ratio": round(100.0 * cross / total_edges, 3) if total_edges else 0.0,
            "by_pair": {" ↔ ".join(k): v for k, v in pair.most_common()},
            "by_type": dict(cross_type.most_common()),
        },
        "namespace_subject": {k: dict(v.most_common()) for k, v in sorted(ns_subj.items())},
        "defects": {
            "dangling_edges": len(dangling),
            "self_loop_edges": len(selfloop),
            "isolated_nodes": len(isolates),
            "dangling_sample": dangling[:5],
            "selfloop_sample": selfloop[:5],
        },
    }


def subjects_from(g, overrides=None):
    """学科映射，overrides 可覆盖单个节点的 subject（用于模拟纠偏后的效果）。"""
    out = {}
    for n in g.get("nodes") or []:
        nid = n["id"]
        if overrides and nid in overrides:
            out[nid] = overrides[nid]
        else:
            out[nid] = graph_export.subject_of((n.get("props") or {}).get("domain"), nid)
    return out


def check_islands(r, allow_path=None):
    """把「小分量集合」与冻结清单（`known_islands.json`）逐节点比对。

    返回 dict；同时把结果挂到 `r["island_freeze"]` 供渲染 / `--strict`。

    判定（三条中任一不满足 → verdict=REGRESSION）：
      ① 分量总数 ≤ `meta.max_components`
      ② 小分量节点集合**逐节点**等于冻结清单的并集（既不许新增、也不许缺项）
      ③ 清单声明的 island_count / island_nodes_total 与实际一致
    清单文件缺失 → verdict=UNKNOWN（`--strict` 下**不放过**，按回归处理，铁律 #20）。
    """
    cp = r["components"]
    actual_islands = [sorted(s["nodes"]) for s in cp["small"]]
    actual_nodes = set().union(*actual_islands) if actual_islands else set()
    out = {
        "known_islands": 0, "known_nodes": 0, "max_components": None,
        "actual_components": cp["count"], "actual_islands": len(actual_islands),
        "actual_nodes": len(actual_nodes),
        "verdict": "UNKNOWN", "notes": [],
    }
    path = allow_path or KNOWN_ISLANDS
    if not os.path.exists(path):
        out["notes"].append("冻结清单缺失：%s（无法判定孤岛是否为已知）" % os.path.relpath(path, ROOT))
        r["island_freeze"] = out
        return out
    try:
        with open(path, encoding="utf-8") as f:
            kf = json.load(f)
    except Exception as e:                                   # noqa: BLE001
        out["notes"].append("冻结清单解析失败：%s" % e)
        r["island_freeze"] = out
        return out

    meta = kf.get("meta") or {}
    known = [sorted(i["nodes"]) for i in (kf.get("islands") or [])]
    known_nodes = set().union(*known) if known else set()
    out.update(known_islands=len(known), known_nodes=len(known_nodes),
               max_components=meta.get("max_components"))

    if out["max_components"] is not None and cp["count"] > out["max_components"]:
        out["notes"].append("连通分量 %d 个 > 上限 %d 个" % (cp["count"], out["max_components"]))
    extra = sorted(actual_nodes - known_nodes)
    if extra:
        out["notes"].append("**新增孤岛节点** %d 个（不在冻结清单内）：%s"
                            % (len(extra), extra[:5]))
    missing = sorted(known_nodes - actual_nodes)
    if missing:
        out["notes"].append("冻结孤岛已消失 %d 个（若是补了真边则应更新清单）：%s"
                            % (len(missing), missing[:5]))
    if len(actual_islands) != len(known):
        out["notes"].append("孤岛个数 %d ≠ 清单 %d" % (len(actual_islands), len(known)))
    out["verdict"] = "FROZEN-OK" if not out["notes"] else "REGRESSION"
    r["island_freeze"] = out
    return out


def render(r, title="跨学科连通性审计"):
    L = []
    bar = "=" * 84
    L.append(bar)
    L.append(title)
    L.append(bar)
    L.append("  规模            : %d 节点 / %d 边" % (r["nodes"], r["edges"]))
    L.append("  学科分布        : %s" % "  ".join("%s=%d" % (k, v) for k, v in r["subjects"].items()))
    phys = r["subjects"].get("物理", 0)
    if r["nodes"]:
        L.append("  物理占比        : %.1f%% %s" % (100.0 * phys / r["nodes"],
                  "⚠ 偏离均衡（核心风险）" if phys / r["nodes"] > 0.5 else "OK"))
    cd = r["cross_domain"]
    L.append("  跨域边          : %d / %d  = %.2f%%  %s" % (
        cd["cross"], cd["edges_total"], cd["cross_ratio"],
        "⚠ 偏低" if cd["cross_ratio"] < 5 else "OK"))
    if cd["by_pair"]:
        L.append("      学科对      : %s" % "  ".join("%s=%d" % (k, v) for k, v in list(cd["by_pair"].items())[:8]))
    if cd["by_type"]:
        L.append("      边类型      : %s" % "  ".join("%s=%d" % (k, v) for k, v in list(cd["by_type"].items())[:8]))
    cp = r["components"]
    L.append("-" * 84)
    L.append("  连通分量        : %d 个；最大 5 个 = %s" % (cp["count"], cp["largest"]))
    L.append("  孤立节点(deg=0) : %d %s" % (cp["isolated_count"],
              "" if cp["isolated_count"] == 0 else "← 死节点，不可达/不参与推理"))
    if cp["small"]:
        L.append("  小分量(<100)    : %d 个，共 %d 节点" % (
            len(cp["small"]), sum(s["size"] for s in cp["small"])))
        for s in cp["small"][:6]:
            L.append("      size=%-4d %s  样例=%s" % (
                s["size"], dict(list(s["types"].items())[:3]), s["sample"][:2]))
        # Phase 30：把「是否在冻结清单内」直接打在报告里 —— 孤岛要么被冻结，
        # 要么就是回归；不存在「没被注意到」的第三种状态。
        _kf = r.get("island_freeze") or {}
        if _kf:
            L.append("      冻结核对    : %s（清单 %d 个岛 / %d 节点，最大分量上限 %s）"
                     % (_kf["verdict"], _kf["known_islands"], _kf["known_nodes"],
                        _kf["max_components"]))
            for msg in _kf["notes"]:
                L.append("      ⚠ %s" % msg)
    d = r["defects"]
    L.append("-" * 84)
    L.append("  悬空边 / 自环边 : %d / %d" % (d["dangling_edges"], d["self_loop_edges"]))
    L.append("  命名空间→学科   :")
    for ns, sub in r["namespace_subject"].items():
        tags = "  ".join("%s=%d" % (k, v) for k, v in sub.items())
        if len(sub) > 1 and ns not in HUB_NAMESPACES:
            mixed = "  ⚠混标"
        elif len(sub) > 1:
            mixed = "  (枢纽层，设计内)"
        else:
            mixed = ""
        L.append("      %-6s %s%s" % (ns, tags, mixed))
    L.append(bar)
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="跨学科连通性审计（STTP 北极星仪器）")
    ap.add_argument("--input", default=DEFAULT_IN, help="原始图 JSON（默认 etl/normalized.json）")
    ap.add_argument("--json", dest="json_out", default=None, help="把结构化结果写到该路径")
    ap.add_argument("--strict", action="store_true", help="检出孤立/悬空/自环/**未冻结孤岛**时退非零")
    ap.add_argument("--islands", dest="islands", default=KNOWN_ISLANDS,
                    help="真孤岛冻结清单（默认 06_PoC/known_islands.json）")
    a = ap.parse_args()

    with open(a.input, encoding="utf-8") as f:
        g = json.load(f)

    r = audit(g)
    kf = check_islands(r, a.islands)
    print(render(r, "跨学科连通性审计 · %s" % os.path.relpath(a.input, ROOT)))

    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
        print("  [json] -> %s" % os.path.relpath(a.json_out, ROOT))

    bad = r["defects"]["dangling_edges"] + r["defects"]["self_loop_edges"] + r["defects"]["isolated_nodes"]
    if a.strict and kf["verdict"] != "FROZEN-OK":
        bad += max(1, len(kf["notes"]))
        print("[STRICT] 孤岛冻结核对未通过（%s → 视为回归）" % kf["verdict"])
    if a.strict and bad:
        print("[STRICT] 检出 %d 项结构缺陷 → 退出码 2" % bad)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
