# -*- coding: utf-8 -*-
"""Phase 9 侦察：依赖可用性 + 真实图规模 + 跨域候选边池规模。"""
import sys, json, os
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FULL = os.path.join(ROOT, "06_PoC", "graph_data_full.json")

print("== 依赖可用性 ==")
deps = {}
for m in ["torch", "networkx", "numpy", "pint", "sympy", "rdkit", "sklearn", "rapidfuzz"]:
    try:
        mod = __import__(m)
        deps[m] = getattr(mod, "__version__", "?")
    except Exception as e:
        deps[m] = "FAIL: %s" % type(e).__name__
for k, v in deps.items():
    print("  %-12s %s" % (k, v))

print("\n== 载入真实图 ==")
with open(FULL, encoding="utf-8") as f:
    g = json.load(f)
nodes, edges = g["nodes"], g["edges"]
print("节点 %d / 边 %d" % (len(nodes), len(edges)))

import collections
print("节点类型:", dict(collections.Counter(n.get("type") for n in nodes)))

import networkx as nx
G = nx.Graph()
id2subj = {}
for n in nodes:
    G.add_node(n["id"])
    id2subj[n["id"]] = n.get("subject") or "跨学科"
for e in edges:
    G.add_edge(e["source"], e["target"])
print("nx: %d 节点 / %d 边" % (G.number_of_nodes(), G.number_of_edges()))

by_subj = collections.defaultdict(list)
for nid, s in id2subj.items():
    by_subj[s].append(nid)
sizes = {s: len(v) for s, v in by_subj.items()}
print("学科规模:", sizes)

subs = list(sizes.keys())
total_cross = 0
for i in range(len(subs)):
    for j in range(i + 1, len(subs)):
        total_cross += sizes[subs[i]] * sizes[subs[j]]
existing_cross = sum(1 for e in edges if id2subj.get(e["source"]) != id2subj.get(e["target"]))
candidate_pool = total_cross - existing_cross
print("跨域节点对总数: %d" % total_cross)
print("已有跨域边: %d" % existing_cross)
print("跨域候选边池(未连): %d" % candidate_pool)

# 现有边按 kind 分布（看推断/显式比例）
print("边 kind 分布:", dict(collections.Counter(e.get("kind") for e in edges)))
print("边 explicit_or_inferred:", dict(collections.Counter(e.get("explicit_or_inferred") for e in edges)))
