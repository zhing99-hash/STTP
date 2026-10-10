# -*- coding: utf-8 -*-
"""Aura 边对账：使云端严格等于本地（本地 normalized.json 为准）。

## 为什么需要
推送只能「加」，历史上被替换掉的旧方案留下的边**不会被自动清理**。
2026-10-09 实测：云端比本地多 155 个唯一三元组（Phase 11 期 `kind=reaction_role`
「反应 → 每个分子命名空间别名」的遗留）+ 39 条同三元组不同 `kind` 的重复边。
（注意：Aura 边的真实主键是 `(source, type, kind)`，所以同三元组不同 kind 会各存一条。）

## 判定
以本地为基准，三类差异：
  1. **云端独有三元组** → 删
  2. **三元组相同但 kind 与本地不一致** → 删（保留与本地 kind 一致的那条）
  3. **本地独有三元组** → 补（补边时复用 `neo4j_props` 做属性消毒）
节点侧：默认只报告不删（节点删除风险高，且实测已一致）。

## 用法（需网络）
    python 06_PoC/reconcile_aura_edges.py --dry-run     # 只打印差异与计划
    python 06_PoC/reconcile_aura_edges.py               # 实际清理
    python 06_PoC/reconcile_aura_edges.py --report-only # 只报告差异，不清理
"""
import argparse
import json
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import neo4j_props                                          # noqa: E402

LOCAL = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")

DEL_EDGE = """
UNWIND $rows AS row
MATCH (a:Entity {id: row.source})-[r]->(b:Entity {id: row.target})
WHERE type(r) = row.type AND coalesce(r.kind,'') = coalesce(row.kind,'')
DELETE r
RETURN count(r) AS c
"""

MERGE_EDGE = """
UNWIND $rows AS row
MATCH (a:Entity {id: row.source}), (b:Entity {id: row.target})
CALL apoc.merge.relationship(a, row.type, {kind: row.kind}, row.props, b) YIELD rel
SET rel += row.props
RETURN count(rel) AS c
"""


def batched(session, cypher, rows, label, batch, max_retry=6):
    done, lost = 0, 0
    for i in range(0, len(rows), batch):
        seg = rows[i:i + batch]
        for attempt in range(max_retry):
            try:
                session.run(cypher, rows=seg).consume()
                done += len(seg)
                break
            except Exception as e:                            # noqa: BLE001
                wait = 2 + attempt * 2
                print("  [retry %d/%d] %s 批 %d-%d 失败: %s (等 %ds)"
                      % (attempt + 1, max_retry, label, i, i + len(seg), str(e)[:120], wait))
                time.sleep(wait)
        else:
            lost += len(seg)
            print("  !! %s 批 %d-%d 重试 %d 次仍失败，放弃 %d 条"
                  % (label, i, i + len(seg), max_retry, len(seg)))
    print("  [%s] 完成 %d/%d（放弃 %d）" % (label, done, len(rows), lost))
    return lost


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印差异与计划，不连库改动")
    ap.add_argument("--report-only", action="store_true", help="连库比对，但不清理")
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--local", default=LOCAL)
    a = ap.parse_args()

    # ---------- 本地 ----------
    d = json.load(open(a.local, encoding="utf-8"))
    l_keys = {}                                  # (s,type,t) -> kind
    for e in d["edges"]:
        l_keys[(e["source"], e["type"], e["target"])] = str(e.get("kind") or "")
    L_nid = {n["id"] for n in d["nodes"]}
    print("[local] %s → %d 节点 / %d 唯一三元组" % (os.path.relpath(a.local, ROOT),
                                                    len(L_nid), len(l_keys)))

    if a.dry_run:
        print("[dry-run] 未连库。")
        return 0

    env = {}
    for line in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    if not env.get("NEO4J_PASSWORD"):
        print("[ERR] 未设置 NEO4J_PASSWORD", file=sys.stderr)
        return 2

    from neo4j import GraphDatabase
    drv = GraphDatabase.driver(env["NEO4J_URI"],
                               auth=(env["NEO4J_USER"], env["NEO4J_PASSWORD"]),
                               connection_timeout=30)
    try:
        with drv.session(database=env.get("NEO4J_DATABASE", "neo4j")) as s:
            C_nid = {r[0] for r in s.run("MATCH (n:Entity) RETURN n.id")}
            c_edges = [(r[0], r[1], r[2], r[3] if r[3] is not None else "")
                       for r in s.run("MATCH (a:Entity)-[r]->(b:Entity) "
                                      "RETURN a.id, type(r), b.id, r.kind")]
            before = s.run("MATCH ()-[r]->() RETURN count(r)").single()[0]

        print("[cloud] %d 节点 / %d 边（原始）" % (len(C_nid), before))
        print("\n=== 差异 ===")
        print("  节点：仅本地 %d / 仅云端 %d" % (len(L_nid - C_nid), len(C_nid - L_nid)))
        for x in sorted(C_nid - L_nid)[:10]:
            print("       +C %s" % x)

        c_map = {}
        for src, typ, tgt, knd in c_edges:
            c_map.setdefault((src, typ, tgt), []).append(knd)

        extra_triples = sorted(set(c_map) - set(l_keys))
        kind_variants = []
        for key, kinds in c_map.items():
            lk = l_keys.get(key)
            if lk is None:
                continue
            if len(kinds) > 1:
                for k in kinds:
                    if k != lk:
                        kind_variants.append((key[0], key[1], key[2], k))
            elif kinds[0] != lk:
                kind_variants.append((key[0], key[1], key[2], kinds[0]))
        missing = sorted(set(l_keys) - set(c_map))

        # ⚠ 安全兜底：若某三元组在云端**只有**与本地不同的 kind，删完它会整体消失。
        # 这类必须在删除后按本地 kind 补回，否则「清理」会变成「丢边」。
        del_kinds = {}
        for (s_, t_, g_, k_) in kind_variants:
            del_kinds.setdefault((s_, t_, g_), set()).add(k_)
        reinsert = []
        for key, lk in l_keys.items():
            kinds = c_map.get(key)
            if not kinds:
                continue
            remaining = [k for k in kinds if k not in del_kinds.get(key, set())]
            if lk not in remaining:
                reinsert.append(key)
        reinsert = sorted(set(reinsert) - set(missing))

        print("  边：")
        print("      云端独有三元组        %d" % len(extra_triples))
        print("      同三元组 kind 不一致  %d" % len(kind_variants))
        print("      本地独有三元组（待补）%d" % len(missing))
        print("      删后需按本地 kind 补回 %d" % len(reinsert))
        from collections import Counter
        if extra_triples:
            print("      [云端独有] 按类型:", dict(Counter(t for _, t, _ in extra_triples).most_common(8)))
            print("      [云端独有] 按 kind:",
                  dict(Counter(k for key in extra_triples for k in c_map[key]).most_common(8)))
        if kind_variants:
            print("      [kind 不一致] 按类型:",
                  dict(Counter(v[1] for v in kind_variants).most_common(8)))
            for v in kind_variants[:5]:
                print("        %-14s %-32s -> %-28s cloud_kind=%r" % (v[1], v[0], v[2], v[3]))

        if a.report_only:
            print("\n[report-only] 未做任何改动。")
            return 0

        to_del = [{"source": s_, "type": t_, "target": g_, "kind": k_}
                  for (s_, t_, g_) in extra_triples for k_ in c_map[(s_, t_, g_)]]
        to_del += [{"source": s_, "type": t_, "target": g_, "kind": k_}
                   for (s_, t_, g_, k_) in kind_variants]
        to_add = []
        l_props = {(e["source"], e["type"], e["target"]): e.get("props") or {}
                   for e in d["edges"]}
        for key in list(missing) + list(reinsert):
            to_add.append({"source": key[0], "type": key[1], "target": key[2],
                           "kind": l_keys[key], "props": neo4j_props.sanitize_props(
                               l_props.get(key, {}), where="edge")})

        print("\n=== 执行计划 ===  删除 %d 条 / 补 %d 条" % (len(to_del), len(to_add)))
        print(neo4j_props.report())
        lost = 0
        with drv.session(database=env.get("NEO4J_DATABASE", "neo4j")) as s:
            if to_del:
                lost += batched(s, DEL_EDGE, to_del, "删除云端独有/kind 变体", a.batch)
            if to_add:
                lost += batched(s, MERGE_EDGE, to_add, "补本地独有边", a.batch)
            after = s.run("MATCH ()-[r]->() RETURN count(r)").single()[0]
            uni = s.run("MATCH (a:Entity)-[r]->(b:Entity) "
                        "RETURN count(DISTINCT a.id + '|' + type(r) + '|' + b.id)").single()[0]
            n_after = s.run("MATCH (n) RETURN count(n)").single()[0]

        print("\n[after ] 云端 %d 节点 / %d 边（原始） / %d 唯一三元组" % (n_after, after, uni))
        print("         本地       %d 节点 / %d 唯一三元组" % (len(L_nid), len(l_keys)))
        ok = (after == len(l_keys) and uni == len(l_keys))
        print("         对账结果：%s" % ("✅ 严格一致" if ok else "❌ 仍不一致"))
        if lost:
            print("         !! 有 %d 条被放弃 → 云端与本地已分叉" % lost)
            return 3
        return 0
    finally:
        drv.close()


if __name__ == "__main__":
    sys.exit(main())
