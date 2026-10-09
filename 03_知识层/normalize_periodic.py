# -*- coding: utf-8 -*-
"""元素周期表位置修复 + same_family 边校正（STTP · A7 阶段 1）。

真值源：`11_真实数据/element_reference.py`（IUPAC 周期表）。

解决的问题（均经全量核验）：
  1. `O` 的 `atomic_number` 为 16（应为 8）——ElementKG `HASATOMIC` 语义混淆的残留，
     A1 只修了符号解析，属性值本身没修；
  2. `period` 缺 6 个（Sc / Ts / B / Mg / Sn / Au）；
  3. `group` 仅覆盖 3/118（A5 合并时由 curated_seed 别名并集补出的 C / H / O）；
  4. `same_family` 682 条里 **308 条语义错误**：ElementKG2.0 把「3 族(Sc/Y/La/Ac) +
     镧系(Ce–Lu) + 锕系(Th–Lr)」并成一个 32 元"族"，与 IUPAC 18 族口径不符。

附带产出：为 118 个元素补齐 `period` / `group` / `block` / `series` 四个属性，
使 `same_period` / `same_family` 从「外部数据源给的边」升级为「由权威属性派生的关系」，
为后续周期性趋势推理（A7 阶段 2）提供可符号校验的排序轴。

用法：
    python 03_知识层/normalize_periodic.py            # dry-run（默认）
    python 03_知识层/normalize_periodic.py --apply    # 落盘 + 出 Aura delta
"""

import argparse
import itertools
import json
import os
import sys
from collections import Counter, defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))

from element_reference import BY_SYMBOL, by_atomic_number  # noqa: E402

NORMALIZED = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
DELTA_OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase16_periodic_delta.json")

SOURCE_TAG = "element_reference_iupac"


# --------------------------------------------------------------------- 工具
def is_element(node):
    labels = node.get("labels") or []
    return "Element" in labels or node.get("type") == "Element"


def family_key(group, series):
    """族归属键：有族号用族号，f 区用 series。La / Ac 有族号 3，归 3 族。"""
    return group if group is not None else ("series:" + series if series else None)


# ------------------------------------------------------------- 属性修复逻辑
def plan_node_fixes(nodes):
    """返回 (修改后的节点列表, 变更明细, 每个元素的权威族键)。"""
    changes = []
    famkey = {}
    for n in nodes:
        if not is_element(n):
            continue
        props = dict(n.get("props") or {})
        sym = props.get("symbol")
        exp = BY_SYMBOL.get(sym)
        if not exp:
            changes.append({"id": n["id"], "symbol": sym, "issue": "符号未在权威表中"})
            continue

        node_changes = []

        # (1) atomic_number：修错误值（保留原始值做 provenance）
        cur_z = props.get("atomic_number")
        if cur_z is not None and int(cur_z) != exp["atomic_number"]:
            if "ek_atomic_raw" not in props:
                props["ek_atomic_raw"] = cur_z
            node_changes.append(("atomic_number", cur_z, exp["atomic_number"]))
            props["atomic_number"] = exp["atomic_number"]
        elif cur_z is None:
            node_changes.append(("atomic_number", None, exp["atomic_number"]))
            props["atomic_number"] = exp["atomic_number"]
        else:
            props["atomic_number"] = int(cur_z)

        # (2) period / block：无条件对齐权威值
        for field, want in (("period", exp["period"]), ("block", exp["block"])):
            got = props.get(field)
            if got != want:
                node_changes.append((field, got, want))
            props[field] = want

        # (3) group：有族号则写，f 区（None）删除该键，避免语义混淆
        if exp["group"] is not None:
            got = props.get("group")
            if got != exp["group"]:
                node_changes.append(("group", got, exp["group"]))
            props["group"] = exp["group"]
        elif "group" in props:
            node_changes.append(("group", props.pop("group"), None))

        # (4) series：仅 f 区写
        if exp["series"]:
            if props.get("series") != exp["series"]:
                node_changes.append(("series", props.get("series"), exp["series"]))
            props["series"] = exp["series"]
        elif "series" in props:
            props.pop("series")

        props["periodic_source"] = SOURCE_TAG
        n["props"] = props
        famkey[n["id"]] = family_key(exp["group"], exp["series"])
        if node_changes:
            changes.append({"id": n["id"], "symbol": sym, "fields": node_changes})
    return changes, famkey


# --------------------------------------------------------- same_family 校正
def truth_family_pairs(famkey):
    """按权威族键生成真值 same_family 对（无序）。"""
    by = defaultdict(list)
    for nid, k in famkey.items():
        if k is not None:
            by[k].append(nid)
    pairs = set()
    for k, ids in by.items():
        for a, b in itertools.combinations(sorted(ids), 2):
            pairs.add(frozenset((a, b)))
    return pairs


def plan_edge_fixes(edges, famkey, sym_of):
    truth = truth_family_pairs(famkey)
    bad_family = []
    have = set()
    for e in edges:
        if e["type"] != "same_family":
            continue
        key = frozenset((e["source"], e["target"]))
        have.add(key)
        if key not in truth:
            bad_family.append(e)
    missing = truth - have
    # same_period 只做一致性核验，不改动
    period_mismatch = []
    z_of = {}
    for nid in famkey:
        s = sym_of.get(nid)
        r = BY_SYMBOL.get(s)
        if r:
            z_of[nid] = r["period"]
    for e in edges:
        if e["type"] != "same_period":
            continue
        ps, pt = z_of.get(e["source"]), z_of.get(e["target"])
        if ps is not None and pt is not None and ps != pt:
            period_mismatch.append(e)
    return bad_family, missing, period_mismatch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="落盘 + 产出 Aura delta")
    ap.add_argument("--input", default=NORMALIZED)
    args = ap.parse_args()

    data = json.load(open(args.input, encoding="utf-8"))
    nodes, edges = data["nodes"], data["edges"]
    sym_of = {n["id"]: (n.get("props") or {}).get("symbol")
              for n in nodes if is_element(n)}

    print("=" * 70)
    print("A7 · 元素周期表位置修复 + same_family 校正   [%s]" % ("APPLY" if args.apply else "DRY-RUN"))
    print("=" * 70)
    print("输入: %s" % os.path.relpath(args.input, ROOT))
    print("      %d 节点 / %d 边\n" % (len(nodes), len(edges)))

    changes, famkey = plan_node_fixes(nodes)
    bad_family, missing, period_mismatch = plan_edge_fixes(edges, famkey, sym_of)

    # ---------------------------------------------------------- 变更报表
    print("[1] 元素节点属性修复 —— 涉及 %d 个节点" % len(changes))
    field_cnt = Counter()
    for ch in changes:
        for f, _, _ in ch.get("fields", []):
            field_cnt[f] += 1
    print("    按字段: %s" % dict(field_cnt))
    hot = [ch for ch in changes if any(f in ("atomic_number",) for f, _, _ in ch.get("fields", []))]
    print("    atomic_number 修正 %d 条:" % len(hot))
    for ch in hot:
        for f, got, want in ch["fields"]:
            print("      %-16s %s: %s → %s" % (ch["id"], f, got, want))
    print()

    print("[2] 属性覆盖度（修复后）")
    for field in ("period", "group", "block", "series"):
        cov = sum(1 for n in nodes if is_element(n) and (n.get("props") or {}).get(field) is not None)
        print("    %-8s %3d/118" % (field, cov))
    print("    注：group 无值的是 f 区 28 个中的 14 个「纯镧系/锕系」成员（Ce–Lu / Th–Lr），")
    print("        由 series + block='f' 表达；La / Ac 仍计入 3 族。")
    print()

    print("[3] same_family 边校正")
    print("    现有 %d 条 | 真值 %d 条 | 错误 %d 条 | 缺失 %d 条"
          % (sum(1 for e in edges if e["type"] == "same_family"),
             len(truth_family_pairs(famkey)), len(bad_family), len(missing)))
    conf = Counter()
    for e in bad_family:
        ks = famkey.get(e["source"])
        kt = famkey.get(e["target"])
        conf[(str(ks), str(kt))] += 1
    print("    错误边类型分布（前 8）:")
    for k, v in conf.most_common(8):
        print("      %s → %s : %d" % (k[0], k[1], v))
    if missing:
        print("    ⚠ 缺失 %d 条（需新增）" % len(missing))
    else:
        print("    ✅ 无缺失（现有边是真值的超集，纯删除即可）")
    print()

    print("[4] same_period 边核验（只报不改）")
    print("    与权威周期矛盾: %d 条 %s" % (len(period_mismatch), "✅" if not period_mismatch else "⚠"))
    print()

    if not args.apply:
        print("DRY-RUN 结束，未落盘。加 --apply 执行。")
        return

    # ---------------------------------------------------------- 落盘
    backup = os.path.join(os.path.dirname(args.input),
                          os.path.basename(args.input).replace(".json", ".before_periodic.json"))
    if not os.path.exists(backup):
        json.dump(data, open(backup, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("备份 → %s" % os.path.relpath(backup, ROOT))

    # 删除错误 same_family 边
    bad_ids = {id(e) for e in bad_family}
    kept = [e for e in edges if id(e) not in bad_ids]
    data["edges"] = kept

    json.dump(data, open(args.input, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("已写回 %s（%d 节点 / %d 边，删除 %d 条错误 same_family）"
          % (os.path.relpath(args.input, ROOT), len(nodes), len(kept), len(bad_family)))

    # ---------------------------------------------------------- Aura delta
    touched = {ch["id"] for ch in changes if ch.get("fields")}
    delta_nodes = []
    for n in nodes:
        if n["id"] in touched:
            delta_nodes.append({"id": n["id"], "labels": n.get("labels") or ["Element", "Entity"],
                                "props": n.get("props") or {}})
    delta = {
        "meta": {
            "phase": "A7-periodic-table",
            "source": "03_知识层/normalize_periodic.py",
            "reason": "元素周期表位置（period/group/block/series）对齐 IUPAC 权威表；"
                      "修正 O 的 atomic_number；删除 308 条语义错误的 same_family 边",
            "truth_source": "11_真实数据/element_reference.py",
            "node_count": len(delta_nodes),
            "delete_edge_count": len(bad_family),
        },
        "nodes": delta_nodes,
        "delete_nodes": [],
        "edges": [],
        "delete_edges": [{"source": e["source"], "target": e["target"],
                          "type": e["type"], "kind": e.get("kind")} for e in bad_family],
    }
    os.makedirs(os.path.dirname(DELTA_OUT), exist_ok=True)
    json.dump(delta, open(DELTA_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("Aura delta → %s" % os.path.relpath(DELTA_OUT, ROOT))
    print("   节点 %d · 删除边 %d" % (len(delta_nodes), len(bad_family)))


if __name__ == "__main__":
    main()
