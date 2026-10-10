# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""元素节点规范化去重（A5）：把四套并存的元素命名空间统一到 EK:el:<Symbol>。

背景
----
同一元素在图谱里最多有 3 个节点，来自 4 个命名空间：

    EK:el:<Symbol>    ElementKG2.0，118 个符号全覆盖，属性最富（23~26 字段）
    EK2:el:<Symbol>   ElementKG2.0 的**第二次重复导入**（13 个符号，12 字段）
    EL:<小写符号>      curated_seed 骨架（H / C / O，13 字段）
    IC:el:<英文名>     curated_seed 骨架（Cl / Na / Fe，12 字段）
    BC:el:<英文名>     curated_seed 骨架（N，12 字段）

15 个符号有 2~3 个节点，共 20 个冗余节点。带来的实际危害：

  * `composed_of`（分子组成）分散指向不同命名空间 —— C/H/O 指向 `EL:*`、
    N 指向 `BC:*`、其余多指向 `EK:el:*`，查询与推理必须先做 same_as 跳转；
  * GNN 训练时同一元素有多个嵌入，信号被稀释；
  * A2 的 GNN 甚至在**不同元素符号**之间猜出 same_as
    （`IC:el:chlorine→EL:o`、`BC:el:nitrogen→EL:h` 等 13 条），
    「同一实体」关系被用错（门控只校验物理/化学性质，不给 same_as 兜底）。

策略：**规范化合并（canonicalization）**
--------------------------------------
  1. 规范节点 = `EK:el:<Symbol>`（118 个符号全覆盖，且 id 严格等于 EK:el:+symbol）
  2. 别名属性「规范节点优先、别名补缺」并集入规范节点，并留 provenance
  3. 所有边端点重定向到规范节点
  4. 丢弃重定向产生的自环；丢弃跨符号 same_as（GNN 错边）
  5. 按 (source, type, target) 去重并合并属性
  6. 删除别名节点
  7. 产出 Aura delta（供联网后 `push_element_merge.py` 推送）

用法
----
    python 03_知识层/normalize_elements.py --dry-run   # 只体检，不落盘
    python 03_知识层/normalize_elements.py --apply     # 备份后落盘 + 重生成 viz 快照
"""
import os
import sys
import json
import argparse
import collections

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "06_PoC"))
import graph_export  # noqa: E402

RAW = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
RAW_BAK = os.path.join(ROOT, "06_PoC", "etl", "normalized.before_elemmerge.json")
OUT_VIZ = os.path.join(ROOT, "06_PoC", "graph_data_phase15.json")
OUT_DELTA = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase15_elementmerge_delta.json")

CANON_PREFIX = "EK:el:"

# 属性并集时**不允许**被别名覆盖的键（身份类字段）
IDENTITY_KEYS = {"id", "symbol", "name", "atomic_number", "atomic_weight", "atomic_mass"}

# 边 kind 的「可信度」排序，去重时取最高的那条作为主边
KIND_RANK = {
    "explicit": 100, "curated_seed": 95, "molecule_element": 90,
    "pubchem_composition": 90, "real": 85, "ek_bridge": 80,
    "elementkg_bridge": 80, "ek_same_period": 75, "ek_same_family": 75,
    "gnn_typed_verified": 70, "gnn_typed_inferred": 30,
}


def is_element(node: dict) -> bool:
    labels = node.get("labels") or []
    return "Element" in labels or node.get("type") == "Element"


def conf_of(edge: dict) -> float:
    p = edge.get("props") or {}
    for k in ("confidence", "conf", "score"):
        v = p.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return 0.0


def rank_of(edge: dict) -> tuple:
    """主边选取：kind 可信度 > verified > confidence。"""
    kind = edge.get("kind") or "unknown"
    verified = bool((edge.get("props") or {}).get("verified"))
    return (KIND_RANK.get(kind, 50), 1 if verified else 0, conf_of(edge))


def collect_symbols(nodes):
    """返回 (symbol -> [node, ...], 无符号元素节点列表)。"""
    bysym = collections.defaultdict(list)
    nosym = []
    for n in nodes:
        if not is_element(n):
            continue
        props = n.get("props") or {}
        sym = props.get("symbol")
        sym = str(sym).strip() if sym is not None else ""
        if not sym:
            nosym.append(n)
            continue
        bysym[sym].append(n)
    return bysym, nosym


def merge_props(canon_node, aliases, report):
    """别名属性并集入规范节点。返回 (新 props, 新增键数, 冲突键数)。"""
    merged = dict(canon_node.get("props") or {})
    added, conflicts, alias_sources = [], [], {}
    # 字段多的别名优先补缺（信息量更大者先入）
    for a in sorted(aliases, key=lambda x: -len(x.get("props") or {})):
        ap = a.get("props") or {}
        alias_sources[a["id"]] = ap.get("source", "unknown")
        for k, v in ap.items():
            if k in ("id", "same_as_aliases", "alias_sources", "merged_node_count"):
                continue
            if k not in merged or merged[k] in (None, "", [], {}):
                merged[k] = v
                if k not in added:
                    added.append(k)
            elif merged[k] != v and k not in IDENTITY_KEYS:
                conflicts.append(k)
                report["prop_conflicts"].setdefault(k, []).append(
                    {"canon": merged[k], "alias": v, "alias_id": a["id"]})

    merged["id"] = canon_node["id"]
    merged["same_as_aliases"] = sorted(a["id"] for a in aliases)
    merged["alias_sources"] = alias_sources
    merged["merged_node_count"] = 1 + len(aliases)
    return merged, sorted(set(added)), sorted(set(conflicts))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="落盘（默认 dry-run）")
    ap.add_argument("--dry-run", action="store_true", help="只体检不落盘（默认行为）")
    ap.add_argument("--input", default=RAW)
    a = ap.parse_args()

    data = json.load(open(a.input, encoding="utf-8"))
    nodes, edges = data["nodes"], data["edges"]
    report = collections.defaultdict(collections.Counter)
    report["prop_conflicts"] = {}

    print("=" * 72)
    print("元素节点规范化去重 (A5)")
    print("=" * 72)
    print("[1] 载入 %s" % os.path.relpath(a.input, ROOT))
    print("    原始：%d 节点 / %d 边" % (len(nodes), len(edges)))

    # ---------------- 1. 选规范节点，构建 alias -> canon ----------------
    bysym, nosym = collect_symbols(nodes)
    canon_of, alias2canon, canon_nodes = {}, {}, {}
    for sym, ns in bysym.items():
        want = CANON_PREFIX + sym
        cand = [n for n in ns if n["id"] == want]
        if not cand:
            # 找不到规范节点（理论不会发生）：保留原样并告警
            report["no_canonical"].update(sym)
            continue
        canon_nodes[sym] = cand[0]
        canon_of[sym] = want
        for n in ns:
            if n["id"] != want:
                alias2canon[n["id"]] = want
    alias_ids = set(alias2canon)
    print("    元素节点 %d 个，唯一符号 %d 个，待合并别名 %d 个"
          % (sum(len(v) for v in bysym.values()), len(bysym), len(alias_ids)))
    if nosym:
        print("    !! 无 symbol 的元素节点 %d 个（保留不动）：%s"
              % (len(nosym), [n["id"] for n in nosym][:5]))
    if report["no_canonical"]:
        print("    !! 无规范节点的符号：%s" % list(report["no_canonical"]))

    # ---------------- 2. 合属性 ----------------
    node_by_id = {n["id"]: n for n in nodes}
    gained = {}
    for sym, canon in canon_nodes.items():
        aliases = [n for n in bysym[sym] if n["id"] != canon["id"]]
        if not aliases:
            continue
        merged, added, conflicts = merge_props(canon, aliases, report)
        canon["props"] = merged
        gained[canon["id"]] = (added, conflicts)
    print("[2] 属性并集：%d 个规范节点吸收别名属性" % len(gained))
    for cid, (added, conflicts) in sorted(gained.items()):
        if added:
            print("      %-14s +%s%s" % (cid, ",".join(added),
                  ("  冲突:%s" % ",".join(conflicts)) if conflicts else ""))

    # ---------------- 3. 重定向边端点 ----------------
    def red(x):
        return alias2canon.get(x, x)

    redirected = 0
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s in alias_ids or t in alias_ids:
            e["source"], e["target"] = red(s), red(t)
            report["redirected_types"].update([e.get("type") or "?"])
            redirected += 1
    print("[3] 边端点重定向：%d 条" % redirected)
    print("      受影响类型: %s" % dict(report["redirected_types"].most_common()))

    # ---------------- 4. 丢弃自环 / 跨符号 same_as ----------------
    kept, selfloops, bad_sameas = [], collections.Counter(), []
    for e in edges:
        s, t, ty = e.get("source"), e.get("target"), e.get("type")
        if s == t:
            selfloops[ty or "?"] += 1
            continue
        if ty == "same_as" and s in node_by_id and t in node_by_id \
                and is_element(node_by_id[s]) and is_element(node_by_id[t]):
            ss = (node_by_id[s].get("props") or {}).get("symbol")
            st_ = (node_by_id[t].get("props") or {}).get("symbol")
            if ss != st_:
                bad_sameas.append((s, ss, t, st_, e.get("kind")))
                continue
        kept.append(e)
    print("[4] 丢弃自环 %d 条 %s" % (sum(selfloops.values()), dict(selfloops)))
    print("      丢弃跨符号 same_as %d 条（GNN 错边）" % len(bad_sameas))
    for x in bad_sameas:
        print("        %s(%s) --same_as--> %s(%s)  kind=%s" % x)

    # ---------------- 5. 按 (source,type,target) 去重 + 合属性 ----------------
    groups = collections.OrderedDict()
    for e in kept:
        groups.setdefault((e.get("source"), e.get("type"), e.get("target")), []).append(e)
    out_edges, merged_groups = [], 0
    for (s, ty, t), group in groups.items():
        if len(group) == 1:
            out_edges.append(group[0])
            continue
        merged_groups += 1
        report["merged_types"].update([ty or "?"])
        primary = max(group, key=rank_of)
        props = dict(primary.get("props") or {})
        props["merged_edge_ids"] = [g.get("id") for g in group]
        props["merged_kinds"] = sorted({g.get("kind") or "unknown" for g in group})
        # composed_of 若化学计量数不一致，取最大值并记录
        if ty == "composed_of":
            counts = [((g.get("props") or {}).get("count")) for g in group]
            counts = [c for c in counts if isinstance(c, (int, float))]
            if counts and len(set(counts)) > 1:
                report["count_conflicts"].update(["%s->%s" % (s, t)])
                props["count"] = max(counts)
                props["count_conflict"] = sorted(set(counts))
        primary["props"] = props
        out_edges.append(primary)
    print("[5] 三元组去重：%d 组重合，边 %d -> %d" % (merged_groups, len(kept), len(out_edges)))
    if merged_groups:
        print("      重合类型: %s" % dict(report["merged_types"].most_common()))
    if report["count_conflicts"]:
        print("      !! count 冲突 %d 组：%s" % (len(report["count_conflicts"]),
              list(report["count_conflicts"])[:5]))

    # ---------------- 6. 重写涉及别名的边 id ----------------
    rewritten = 0
    for e in out_edges:
        eid = e.get("id") or ""
        if e["source"] in alias_ids or e["target"] in alias_ids or \
                any(al in eid for al in alias_ids):
            e["id"] = "%s|%s|%s" % (e.get("type"), e["source"], e["target"])
            rewritten += 1
    fixed = graph_export.uniquify_edge_ids(out_edges)
    print("[6] 边 id 重写 %d 条；兜底去重改名 %d 条" % (rewritten, fixed))

    # ---------------- 7. 移除别名节点 ----------------
    out_nodes = [n for n in nodes if n["id"] not in alias_ids]
    print("[7] 移除别名节点 %d 个" % len(alias_ids))
    print("    结果：%d 节点 / %d 边（节点 %+d，边 %+d）"
          % (len(out_nodes), len(out_edges),
             len(out_nodes) - len(nodes), len(out_edges) - len(edges)))

    # ---------------- 8. 产出 Aura delta ----------------
    canon_payload = []
    for sym, canon in canon_nodes.items():
        if canon["id"] in gained:
            canon_payload.append({"id": canon["id"],
                                  "labels": canon.get("labels") or ["Entity", "Element"],
                                  "props": canon["props"]})
    # Aura 侧 DETACH DELETE 别名节点会连带删掉挂在别名上的边，
    # 故把「端点含规范元素节点」的边全部带上重推（MERGE 幂等，重复无害）。
    absorbed = {c["id"] for c in canon_payload}
    push_edges = [e for e in out_edges
                  if e["source"] in absorbed or e["target"] in absorbed]
    print("      待重推边（端点含吸收别名的规范节点）：%d 条" % len(push_edges))
    delta = {
        "meta": {
            "phase": "A5-element-merge",
            "source": "03_知识层/normalize_elements.py",
            "reason": "四套元素命名空间（EK/EK2/EL/IC/BC）统一到 EK:el:<Symbol>",
            "canonical_rule": "EK:el:<Symbol>",
            "aliases_merged": sorted(alias_ids),
            "note": "先 upsert 规范节点属性，再 DETACH DELETE 别名节点，最后 MERGE 重定向边",
            "counts": {"nodes": len(out_nodes), "edges": len(out_edges),
                       "aliases_removed": len(alias_ids),
                       "selfloops_dropped": sum(selfloops.values()),
                       "bad_same_as_dropped": len(bad_sameas),
                       "edge_groups_merged": merged_groups},
        },
        "nodes": canon_payload,
        "delete_nodes": sorted(alias_ids),
        "edges": [{"id": e["id"], "source": e["source"], "target": e["target"],
                   "type": e["type"], "kind": e.get("kind") or "explicit",
                   "props": e.get("props") or {}} for e in push_edges],
        "delete_edges": [{"source": x[0], "target": x[2], "type": "same_as",
                          "kind": x[4]} for x in bad_sameas],
    }
    print("[8] Aura delta：节点 %d · 删除节点 %d · 边 %d · 删除边 %d"
          % (len(delta["nodes"]), len(delta["delete_nodes"]),
             len(delta["edges"]), len(delta["delete_edges"])))

    if not a.apply:
        print("\n[DRY-RUN] 未写任何文件。加 --apply 落盘。")
        return

    # ---------------- 9. 落盘 ----------------
    if not os.path.exists(RAW_BAK):
        import shutil
        shutil.copy2(a.input, RAW_BAK)
        print("\n[9] 已备份 -> %s" % os.path.relpath(RAW_BAK, ROOT))
    data["nodes"], data["edges"] = out_nodes, out_edges
    json.dump(data, open(a.input, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("    已写回 %s" % os.path.relpath(a.input, ROOT))

    viz = graph_export.build_graph_data(a.input)
    json.dump(viz, open(OUT_VIZ, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("    已生成 viz 快照 %s：%d 节点 / %d 边"
          % (os.path.relpath(OUT_VIZ, ROOT), len(viz["nodes"]), len(viz["edges"])))
    meta = viz.get("meta") or {}
    print("    悬空端点: %s" % (len(meta.get("dangling_endpoints") or [])))
    print("    重复边 id: %s" % meta.get("edge_id_collisions_fixed", 0))

    os.makedirs(os.path.dirname(OUT_DELTA), exist_ok=True)
    json.dump(delta, open(OUT_DELTA, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("    已写出 Aura delta %s" % os.path.relpath(OUT_DELTA, ROOT))


if __name__ == "__main__":
    main()
