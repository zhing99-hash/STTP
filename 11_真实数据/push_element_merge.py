# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""把 A5「元素节点规范化去重」的增量推送到 Aura。

为什么需要单独一个脚本（而不是复用 robust_aura_loader / push_element_fix）：
本次操作的主体是**删除节点**（20 个元素别名节点）并**改挂它们的边**。
`robust_aura_loader.py` 只写边；`push_element_fix.py` 只能 upsert 节点 + 写/删边，
没有 DETACH DELETE 节点的能力。

步骤（幂等，可重复执行；顺序不可颠倒）：
    1. upsert 15 个规范元素节点的**合并后属性**
       （合并了别名的 group / period / ek_* 等字段，并写入 same_as_aliases）
    2. DETACH DELETE 20 个别名节点
       （S→E 的旧边随之消失，不会悬挂）
    3. MERGE 端点已重定向到规范节点的边
       （必须在删除之后做，才能把被连带删掉的边补回来）

用法（需网络）：
    source env.sh
    python 11_真实数据/push_element_merge.py --dry-run   # 只打印计划，不连库
    python 11_真实数据/push_element_merge.py             # 实际推送

    # 该脚本已通用化为「delta 推送器」：任何含 nodes / delete_nodes / edges /
    # delete_edges 四段的 delta 都可推送（A7 的 phase16_periodic_delta /
    # phase16_trends_delta 亦复用之）：
    python 11_真实数据/push_element_merge.py --delta 06_PoC/etl/neo4j/phase16_periodic_delta.json
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
DELTA = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase15_elementmerge_delta.json")

# DB 边界属性消毒（Neo4j 不接受 dict / list[dict]；详见 06_PoC/neo4j_props.py 的说明）
sys.path.insert(0, os.path.join(ROOT, "06_PoC"))
import neo4j_props                                        # noqa: E402

URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PW = os.environ.get("NEO4J_PASSWORD")
DB = os.environ.get("NEO4J_DATABASE", "neo4j")

NODE_CYPHER = """
UNWIND $rows AS row
MERGE (n:Entity {id: row.id})
SET n += row.props
WITH n, row
CALL apoc.create.addLabels(n, row.labels) YIELD node
RETURN count(node) AS c
"""

# 标签**替换**变体（--set-labels）：apoc.create.addLabels 只增不减，用于「标签规范化」
# 会把旧标签留在节点上（本地是 PhysicalQuantity，云端变成 [Physical_quantity, PhysicalQuantity]）。
# setLabels 是整体替换，正合「收敛到受控词表」的语义；用 WHERE row.labels IS NOT NULL 守卫，
# 避免把不带 labels 的 delta 行的标签清空。
NODE_CYPHER_SET_LABELS = """
UNWIND $rows AS row
MERGE (n:Entity {id: row.id})
SET n += row.props
WITH n, row WHERE row.labels IS NOT NULL
CALL apoc.create.setLabels(n, row.labels) YIELD node
RETURN count(node) AS c
"""

DEL_NODE_CYPHER = """
UNWIND $rows AS row
MATCH (n:Entity {id: row.id})
DETACH DELETE n
RETURN count(*) AS c
"""

EDGE_CYPHER = """
UNWIND $rows AS row
MATCH (a:Entity {id: row.source}), (b:Entity {id: row.target})
CALL apoc.merge.relationship(a, row.type, {kind: row.kind}, row.props, b) YIELD rel
SET rel += row.props
RETURN count(rel) AS c
"""
# ⚠ 2026-10-10 踩坑（铁律 #13「语句跑完 ≠ 事情做成」的第二次现形）：
# `apoc.merge.relationship(a, type, identProps, props, b)` 的 `props` 是 **onCreateProps**
# —— **只在「新建」时生效**；对**已存在**的边（MERGE 命中）**什么都不写**。
# 于是「只更新边属性」的 delta 会**静默零效果**：日志照样报「边 MERGE 完成 48734/48734」、
# 计数不变、`reconcile_aura_edges` 也 0 差异（它只比 (source,type,kind) 三元组，不看属性）。
# 第 18 轮实测：Phase 28 的 48734 条边属性**一条都没落库**，Aura 上 `verification_level`
# 属性根本不存在，而脚本 exit=0。故补 `SET rel += row.props`（与 APOC 版本无关、create/match
# 都生效），再加下方 [5/5] **属性落地抽检**做仪器守卫。

DEL_EDGE_CYPHER = """
UNWIND $rows AS row
MATCH (a:Entity {id: row.source})-[r]->(b:Entity {id: row.target})
WHERE type(r) = row.type
  AND (row.kind IS NULL OR coalesce(r.kind,'') = coalesce(row.kind,''))
DELETE r
RETURN count(*) AS c
"""

# 边删除后的**残留回查**（按 source/target/type，不带 kind，因为带 kind 的已被删）。
# ⚠ 2026-10-10 踩坑：旧版 DEL_EDGE_CYPHER 强制 `kind` 相等，而多数 delta 的 delete_edges
# 不带 kind（→ NULL）→ `coalesce(r.kind,'')='real' ≠ ''` → **一条都没删**；但 run_batched 的
# `done += len(seg)` 是**无条件累加**，于是日志报「边 DELETE 完成 1160/1160」——铁律 #13
# 「语句跑完 ≠ 事情做成」。故补此回查并按残留数判失败。
LEFT_EDGE_CYPHER = """
UNWIND $rows AS row
MATCH (a:Entity {id: row.source})-[r]->(b:Entity {id: row.target})
WHERE type(r) = row.type
RETURN count(r) AS c
"""

# 边**属性**落地抽检（铁律 #13 守卫）：把 delta 边的属性与 DB 上实际属性**逐键比对**。
# 只抽样（默认 300 条等距），足够捕获「onCreateProps 只在新建时生效」这类**系统性**缺陷。
FETCH_EDGE_PROPS_CYPHER = """
UNWIND $rows AS row
MATCH (a:Entity {id: row.source})-[r]->(b:Entity {id: row.target})
WHERE type(r) = row.type
  AND coalesce(r.kind,'') = coalesce(row.kind,'')
RETURN row.source AS s, row.target AS t, row.type AS ty, properties(r) AS p
"""


def verify_edge_props(driver, edges, sample=300):
    """抽样比对边属性是否真的落库。返回 (已检条数, 不符条数, 前 3 样例)。"""
    n = len(edges)
    if not n:
        return 0, 0, []
    if n <= sample:
        rows = edges
    else:
        step = n / float(sample)
        rows = [edges[int(i * step)] for i in range(sample)]
    checked, bad, samples = 0, 0, []
    for i in range(0, len(rows), 50):
        seg = rows[i:i + 50]
        with driver.session(database=DB) as s:
            got = s.run(FETCH_EDGE_PROPS_CYPHER, rows=seg).data()
        idx = {(g["s"], g["ty"], g["t"]): (g["p"] or {}) for g in got}
        for e in seg:
            key = (e["source"], e["type"], e["target"])
            if key not in idx:                       # 端点/类型匹配不上 → 由 DELETE 回查兜底
                continue
            checked += 1
            dbp = idx[key]
            for k, v in (e.get("props") or {}).items():
                if dbp.get(k) != v:
                    bad += 1
                    if len(samples) < 3:
                        samples.append((key[0], key[1], k, v, dbp.get(k)))
                    break
    return checked, bad, samples


def run_batched(driver, cypher, rows, label, batch=200, max_retry=6):
    """返回 (成功行数, 被放弃的行数)。

    ⚠ 「重试 6 次后放弃该批」是**静默降级**的高发点：脚本仍会 exit=0，日志里只有
    几行 [retry] 容易被淹没。故这里显式把放弃的行数返回，由 main 决定退出码。
    """
    total, done, abandoned = len(rows), 0, 0
    for i in range(0, total, batch):
        seg = rows[i:i + batch]
        for attempt in range(max_retry):
            try:
                with driver.session(database=DB) as s:
                    tx = s.begin_transaction()
                    tx.run(cypher, rows=seg)
                    tx.commit()
                done += len(seg)
                break
            except Exception as e:
                wait = 2 + attempt * 2
                print(f"  [retry {attempt+1}/{max_retry}] {label} 批 {i}-{i+len(seg)} 失败: {e} (等 {wait}s)")
                time.sleep(wait)
        else:
            abandoned += len(seg)
            print(f"  !! {label} 批 {i}-{i+len(seg)} 重试 {max_retry} 次仍失败，放弃 {len(seg)} 条")
        if (i // batch) % 10 == 0:
            print(f"  进度 {label} {done}/{total}")
    return done, abandoned


def norm_del_ids(del_nodes):
    """把 delete_nodes 归一为**纯 id 字符串列表**。

    ⚠ 2026-10-09 实测踩坑：本推送器原先假设 delete_nodes 是字符串列表（phase15 就是），
    但新写的 `06_PoC/sync_seed_delta.py` 产出的是 ``[{"id": ...}]``（与 `apply_delta.py`
    的宽容口径一致）。于是 ``[{"id": i} for i in del_nodes]`` 变成
    ``{"id": {"id": "EK2:rxn:..."}}`` —— ``MATCH (n {id: row.id})`` 拿 map 去比字符串，
    **一条都没删，且不报错**；连下面「别名残留」自检也用同一份错 id，于是自检也一起通过。
    这就是铁律 #10 说的「静默降级」，故此处双管齐下：先归一格式，再断言实删数。
    """
    out = []
    for x in (del_nodes or []):
        v = x if isinstance(x, str) else (x or {}).get("id")
        if v:
            out.append(v)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不连接数据库")
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--delta", default=DELTA,
                    help="delta 文件路径（默认 phase15 元素合并）")
    ap.add_argument("--set-labels", action="store_true",
                    help="用 apoc.create.setLabels **整体替换** labels（默认 addLabels 只增不减）。"
                         "仅用于「标签规范化」类 delta，避免旧标签残留。")
    a = ap.parse_args()

    with open(a.delta, encoding="utf-8") as f:
        d = json.load(f)
    nodes = d.get("nodes", [])
    raw_del = d.get("delete_nodes", [])
    del_nodes = norm_del_ids(raw_del)
    if raw_del and any(not isinstance(x, str) for x in raw_del):
        print("[WARN] delete_nodes 里含非字符串条目（%s…），已归一为纯 id；"
              "建议让 delta 生成方统一输出字符串。" % (raw_del[0],))
    edges = d.get("edges", [])
    dels = d.get("delete_edges", [])

    # ⚠ 关键：在推送到 Neo4j 之前消毒属性。
    # Neo4j 只接受 primitive / 同质 primitive 数组；delta 里可能夹带 dict（`alias_sources`）
    # 或 list[dict]（`gnn_type_probs`），本地 NetworkX 不校验 → 推到 Aura 会**整批**失败。
    # 2026-10-09 实测：因缺这一步，2 个边批（600 边）+ 节点批（118 元素的 period/group）被静默放弃。
    nodes = neo4j_props.sanitize_rows(nodes, where="node")
    edges = neo4j_props.sanitize_rows(edges, where="edge")

    print(f"[in] {os.path.relpath(a.delta, ROOT)}")
    print(neo4j_props.report())
    print(f"     upsert 节点 {len(nodes)} / 待删节点 {len(del_nodes)}"
          f" / 新增·改挂边 {len(edges)} / 待删边 {len(dels)}")
    print(f"     phase: {d.get('meta', {}).get('phase')}")
    print(f"     reason: {d.get('meta', {}).get('reason')}")

    if a.dry_run:
        print("\n[dry-run] 将执行：")
        print(f"  标签模式: {'setLabels（整体替换）' if a.set_labels else 'addLabels（只增）'}")
        if nodes:
            print(f"  1) MERGE+SET 节点 {len(nodes)} 个")
            for n in nodes[:5]:
                props = n.get("props") or {}
                aliases = props.get("same_as_aliases") or []
                extra = f"（合并别名 {aliases}）" if aliases else ""
                print(f"       {n['id']:18} {len(props)} 字段{extra}")
            if len(nodes) > 5:
                print(f"       … 共 {len(nodes)}")
        if del_nodes:
            print(f"  2) DETACH DELETE 节点 {len(del_nodes)} 个：{del_nodes}")
        if edges:
            print(f"  3) MERGE 边 {len(edges)} 条")
            for e in edges[:5]:
                print(f"       {e['type']:22} {e['source']} -> {e['target']}")
            if len(edges) > 5:
                print(f"       … 共 {len(edges)}")
        if dels:
            print(f"  4) DELETE 陈错边 {len(dels)} 条")
            for e in dels[:5]:
                print(f"       {str(e.get('type')):22} {e.get('source')} -> {e.get('target')}")
            if len(dels) > 5:
                print(f"       … 共 {len(dels)}")
        print("\n[dry-run] 未连接数据库，未做任何改动。")
        return 0

    if not PW:
        print("[ERR] 未设置 NEO4J_PASSWORD，请先 source env.sh", file=sys.stderr)
        return 2

    from neo4j import GraphDatabase
    print(f"[conn] {URI} db={DB}")
    driver = GraphDatabase.driver(URI, auth=(USER, PW), database=DB)
    try:
        with driver.session(database=DB) as s:
            n0 = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            e0 = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        print(f"[before] Aura: {n0} 节点 / {e0} 边")

        an, bn = (run_batched(driver, NODE_CYPHER_SET_LABELS if a.set_labels else NODE_CYPHER,
                              nodes, "节点 upsert", a.batch) if nodes else (0, 0))
        print(f"[1/4] 节点 upsert 完成 {an}/{len(nodes)}")
        adn, bdn = (run_batched(driver, DEL_NODE_CYPHER, [{"id": i} for i in del_nodes],
                                "删除节点", 50) if del_nodes else (0, 0))
        print(f"[2/4] 节点 DETACH DELETE 完成 {adn}/{len(del_nodes)}")
        ae, be = run_batched(driver, EDGE_CYPHER, edges, "MERGE 边", a.batch) if edges else (0, 0)
        print(f"[3/4] 边 MERGE 完成 {ae}/{len(edges)}")
        if dels:
            add, bd = run_batched(driver, DEL_EDGE_CYPHER, dels, "删除陈错边", a.batch)
            print(f"[4/4] 边 DELETE 完成 {add}/{len(dels)}")
        else:
            bd = 0

        # [5/5] 边属性落地抽检 —— 铁律 #13：`MERGE 跑完 48734/48734` 不等于属性真的写进去了
        ck, badp = 0, 0
        if edges:
            ck, badp, bad_samples = verify_edge_props(driver, edges, 300)
            print(f"[5/5] 边属性抽检：已检 {ck} 条，不符 {badp} 条")
            for s_ in bad_samples:
                print(f"      ✗ {s_[0]}--{s_[1]}-->... 键 {s_[2]}：delta={s_[3]!r} db={s_[4]!r}")

        with driver.session(database=DB) as s:
            n1 = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            e1 = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            left = s.run("MATCH (n) WHERE n.id IN $ids RETURN count(n) AS c",
                         ids=del_nodes).single()["c"]
            elem = s.run("MATCH (n:Element) RETURN count(n) AS c").single()["c"]
            left_edges = (s.run(LEFT_EDGE_CYPHER, rows=dels).single()["c"]
                          if dels else 0)
        print(f"[after ] Aura: {n1} 节点 / {e1} 边  (Δ{n1-n0:+d} / Δ{e1-e0:+d})")
        print(f"[check ] 待删节点残留 {left}/{len(del_nodes)}（应为 0） · "
              f"**待删边残留 {left_edges}/{len(dels)}（应为 0）** · Element 节点 {elem}（应为 118）")
        if left_edges:
            print("\n" + "!" * 72)
            print(f"!! 待删边仍有 {left_edges} 条残留 → **云端与本地已分叉**！")
            print("!! 多为 delta 的 delete_edges 缺 kind 且语句未做通配所致（已修语句）；")
            print("!! 请重跑本步（MERGE 幂等，可安全重复）。")
            print("!" * 72)
            return 3

        if badp:
            print("\n" + "!" * 72)
            print(f"!! 边属性抽检不符 {badp}/{ck} → **属性未真正落库**（云端与本地已分叉）！")
            print("!! 常见原因：apoc.merge.relationship 的 props 只是 onCreateProps，")
            print("!! 对**已存在**边不写任何属性 —— 必须补 `SET rel += row.props`。")
            print("!" * 72)
            return 3

        lost = bn + bdn + be + bd
        if lost:
            print("\n" + "!" * 72)
            print(f"!! 本步有 {lost} 条记录因批次反复失败被放弃 → **云端与本地已分叉**！")
            print("!! 看上方 [retry] 行的异常类型；修好后重跑本步即可（MERGE 幂等，可安全重复）。")
            print("!" * 72)
            return 3
        # ⚠ 实删数断言：删除语句「跑完」≠「删掉了」。上面的 adn 只是**送出的行数**，
        # 端点匹配不上时 Cypher 不报错、也不会删任何东西（静默降级）。必须回查。
        if del_nodes and left:
            print("\n" + "!" * 72)
            print(f"!! 要求删除 {len(del_nodes)} 个节点，仍有 {left} 个残留 → 云端与本地**已分叉**！")
            print("!! 常见原因：id 格式不符（dict vs 字符串）导致 MATCH 落空，或节点缺少 :Entity 标签。")
            print("!! 残留样例：" + str(del_nodes[:5]))
            print("!" * 72)
            return 3
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
