# -*- coding: utf-8 -*-
"""
align_to_existing_graph.py
==========================
Phase 5.A — 对现有 36 节点图谱跑 Wikidata 适配器, 产出 same_as 候选边.

输入 : 06_PoC/etl/neo4j/neo4j_ready.json (36 节点 / 52 边, 已 Aura 部署, 只读)
输出 : 09_科研扩展/5A_wikidata/same_as_edges.json
缓存 : 09_科研扩展/5A_wikidata/data/wikidata_cache.json

每条边: source(图节点 id) / target(WD:<qid>) / confidence / explicit_or_inferred /
        alignment_source / domain / qid / label / method

注意: 只读 neo4j_ready.json, 不修改任何 00-08 文件.
"""

from __future__ import annotations
import os
import json
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))  # 01tuopu
POCH_DATA = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "neo4j_ready.json")
CACHE_PATH = os.path.join(HERE, "data", "wikidata_cache.json")
OUT_PATH = os.path.join(HERE, "same_as_edges.json")

sys.path.insert(0, HERE)
from wikidata_adapter import (WikidataAdapter, DISAMBIG_QID, ID_PREFIX_DOMAIN)

# node_id -> 候选搜索标签 (优先最具体/最易解析者)
LABEL_MAP = {
    "MX:thm:gauss_bonnet":          ["Gauss-Bonnet theorem"],
    "MX:thm:riemann_curvature":     ["Riemann curvature tensor"],
    "MX:def:manifold":              ["differentiable manifold", "manifold"],
    "MX:def:tangent_space":         ["tangent space"],
    "MX:def:riemannian_metric":     ["Riemannian metric"],
    "MX:def:levi_civita":           ["Levi-Civita connection"],
    "MX:lemma:covariant_derivative":["covariant derivative"],
    "MX:lemma:partition_of_unity":  ["partition of unity"],
    "MX:math:binomial":             ["binomial theorem"],
    "MX:math:pythagorean_identity": ["Pythagorean trigonometric identity"],
    "MX:math:power_rule":           ["power rule"],
    "MX:sym:pi":                    ["pi"],
    "MX:sym:chi_m":                 ["Euler characteristic"],
    "MX:sym:nabla":                 ["del (nabla)"],
    "MX:phy:newton2":               ["Newton's second law of motion"],
    "MX:phy:kinetic_energy":        ["kinetic energy"],
    "MX:phy:energy":                ["energy"],
    "MX:chem:co2":                  ["carbon dioxide"],
    "MX:chem:methane":              ["methane"],
    "MX:chem:combustion":           ["combustion"],
}


def expected_domain(node_id: str) -> str:
    for prefix, dom in ID_PREFIX_DOMAIN.items():
        if node_id.startswith(prefix):
            return dom
    return "unknown"


def main():
    print("=" * 64)
    print("Phase 5.A — align existing 36-node graph to Wikidata")
    print("=" * 64)
    if not os.path.exists(POCH_DATA):
        print(f"[FAIL] PoC data not found: {POCH_DATA}")
        print("[END] exit=1")
        return 1

    with open(POCH_DATA, "r", encoding="utf-8") as f:
        graph = json.load(f)
    nodes = {n["id"]: n for n in graph["nodes"]}
    print(f"[OK] loaded {len(nodes)} nodes from neo4j_ready.json")

    adapter = WikidataAdapter(cache_path=CACHE_PATH, timeout=15.0,
                              retries=1, min_interval=3.0, backoff=4.0)

    edges = []
    wd_nodes = {}
    resolved = 0
    for nid, labels in LABEL_MAP.items():
        if nid not in nodes:
            print(f"[WARN] {nid} not in graph; skip")
            continue
        exp_dom = expected_domain(nid)
        qid = None
        chosen_label = None
        chosen_method = None
        for lab in labels:
            cands = adapter.search_by_label(lab)
            if cands:
                # 优先 domain 一致
                pick = None
                for c in cands:
                    rel = adapter.get_relations(c["qid"])  # SPARQL
                    if rel["domain"] == exp_dom:
                        pick = c; break
                if pick is None:
                    pick = cands[0]
                qid = pick["qid"]
                chosen_label = pick["label"]
                chosen_method = pick["method"]
                # 记录 WD 节点信息
                rel = adapter.get_relations(qid)  # SPARQL (可能命中缓存)
                dom = rel["domain"]
                if qid == DISAMBIG_QID or dom == "disambig":
                    print(f"[FAIL] {nid} -> {qid} is disambiguation page; skip")
                    qid = None
                    break
                wd_nodes[qid] = {"id": f"WD:{qid}",
                                 "labels": ["Entity", "Wikidata"],
                                 "props": {"qid": qid, "label": chosen_label,
                                           "domain": dom,
                                           "source": "wikidata"}}
                # 置信度: 精确标签命中 + domain 一致 -> 高
                conf = 0.60
                if pick.get("exact") or chosen_method == "sparql":
                    conf = 0.85
                if dom == exp_dom:
                    conf = min(0.97, conf + 0.10)
                elif dom == "unknown":
                    conf = 0.70
                edges.append({
                    "id": f"same_as:{nid}->{qid}",
                    "source": nid,
                    "target": f"WD:{qid}",
                    "kind": "SAME_AS",
                    "confidence": round(conf, 2),
                    "explicit_or_inferred": "inferred",
                    "alignment_source": "wikidata",
                    "domain": dom,
                    "qid": qid,
                    "label": chosen_label,
                    "method": chosen_method,
                })
                resolved += 1
                print(f"[OK] {nid} -> WD:{qid} ({chosen_label}) "
                      f"domain={dom} conf={edges[-1]['confidence']}")
                break
        if qid is None:
            print(f"[FAIL] {nid} -> no Wikidata candidate resolved")

    # 写出
    out = {
        "generated_by": "align_to_existing_graph.py",
        "source_graph": "06_PoC/etl/neo4j/neo4j_ready.json",
        "schema": "edge: source(node_id) -> target(WD:<qid>), kind=SAME_AS",
        "counts": {"same_as_edges": len(edges),
                   "wd_nodes": len(wd_nodes),
                   "sparql_success": adapter.stats["sparql_success"],
                   "sparql_fail": adapter.stats["sparql_fail"],
                   "wbsearch_success": adapter.stats["wbsearch_success"],
                   "wbsearch_fail": adapter.stats["wbsearch_fail"],
                   "cache_hit": adapter.stats["cache_hit"]},
        "edges": edges,
        "wd_nodes": list(wd_nodes.values()),
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("-" * 64)
    print(f"[OK] wrote {len(edges)} same_as edges -> {os.path.relpath(OUT_PATH, ROOT)}")
    print(f"     SPARQL success={adapter.stats['sparql_success']} "
          f"fail={adapter.stats['sparql_fail']} | "
          f"wbsearch success={adapter.stats['wbsearch_success']} "
          f"fail={adapter.stats['wbsearch_fail']} | "
          f"cache_hit={adapter.stats['cache_hit']}")
    if len(edges) >= 5:
        print(f"[OK] produced >=5 same_as edges ({len(edges)})")
        print("[END] exit=0")
        return 0
    else:
        print(f"[FAIL] only {len(edges)} same_as edges (<5)")
        print("[END] exit=1")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
