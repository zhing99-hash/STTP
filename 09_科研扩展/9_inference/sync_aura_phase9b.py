# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Phase 9 任务1 · 批量把重筛结果同步到 Aura（UNWIND 批处理，减少往返）。
- DROP 的 related_to 边：删除。
- UPGRADE 的 related_to 边：删除旧 related_to，写入 typed(reactant_of/product_of) 已验证桥。
- KEEP 的 related_to 边：更新 rationale / plausibility / status。
仅操作 kind=llm_inferred_gnn 的本阶段推断边；按 (start.id, end.id) 精确匹配。
"""
import json, os, sys
from neo4j import GraphDatabase

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ORIG = os.path.join(ROOT, "09_科研扩展", "9_inference", "phase9_raw.json")
REFINED = os.path.join(ROOT, "09_科研扩展", "9_inference", "phase9_refined_raw.json")

import os
URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PW = os.environ.get("NEO4J_PASSWORD", "formula_graph_2026")
DB = os.environ.get("NEO4J_DATABASE", "neo4j")


def derive():
    orig = json.load(open(ORIG, encoding="utf-8"))
    ref = json.load(open(REFINED, encoding="utf-8"))
    orig_rel = [(e["source"], e["target"]) for e in orig["edges"] if e["type"] == "related_to"]
    ref_by = {(e["source"], e["target"]): e for e in ref["edges"]}
    orig_set = set(orig_rel)
    drops, upgrades, keeps = [], [], []
    for e in ref["edges"]:
        if e["type"] == "related_to":
            keeps.append({"s": e["source"], "t": e["target"],
                          "rat": e["props"].get("rationale"),
                          "pl": e["props"].get("plausibility")})
        elif e["kind"] == "llm_inferred_gnn" and e["type"] in ("reactant_of", "product_of"):
            p = dict(e["props"]); p["id"] = f"gnn9v:{e['source']}->{e['target']}"
            upgrades.append({"s": e["source"], "t": e["target"], "ntype": e["type"], "props": p})
    for s, t in orig_set:
        if (s, t) not in ref_by:
            drops.append({"s": s, "t": t})
    return drops, upgrades, keeps


def main():
    drops, upgrades, keeps = derive()
    print(f"DROP={len(drops)} UPGRADE={len(upgrades)} KEEP={len(keeps)}", flush=True)
    driver = GraphDatabase.driver(URI, auth=(USER, PW), database=DB)
    with driver.session(database=DB) as sess:
        # 1) 删除 DROP + UPGRADE 的旧 related_to
        del_pairs = [{"s": s, "t": t} for s, t in drops] + \
                    [{"s": s, "t": t} for s, t, _, _ in upgrades]
        if del_pairs:
            n = sess.run(
                "UNWIND $pairs AS p MATCH (a {id:p.s})-[r:related_to]->(b {id:p.t}) DELETE r RETURN count(r) AS c",
                pairs=del_pairs).single()["c"]
            print(f"  已删除 related_to 边: {n}", flush=True)
        # 2) 写入 UPGRADE typed 边（按类型分组，MERGE 不支持参数化类型）
        for ntype in ("reactant_of", "product_of"):
            rows = [u for u in upgrades if u["ntype"] == ntype]
            if not rows:
                continue
            sess.run(
                "UNWIND $rows AS x MATCH (a {id:x.s}),(b {id:x.t}) "
                "MERGE (a)-[r:%s {id:x.props.id}]->(b) SET r=x.props" % ntype,
                rows=rows)
        print(f"  已写入升级桥(typed): {len(upgrades)}", flush=True)
        # 3) 更新 KEEP related_to 的 rationale/plausibility/status
        if keeps:
            sess.run(
                "UNWIND $rows AS x MATCH (a {id:x.s})-[r:related_to]->(b {id:x.t}) "
                "SET r.rationale=x.rat, r.plausibility=x.pl, r.status='NEEDS_REVIEW', r.verified=false",
                rows=keeps)
        print(f"  已更新保留假设 rationale: {len(keeps)}", flush=True)
        rec = sess.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        print(f"  Aura 当前总边数: {rec}", flush=True)
    driver.close()


if __name__ == "__main__":
    main()
