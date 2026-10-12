# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""量纲字段语义迁移（Phase 40 · R09）。

复评 R09：「**指数、量纲向量、量名语义分离**；真实量纲用独立字段，避免新旧语义混杂。」

迁移前实测到的**两处同名异义**（同一 JSON 字段 `dimension` 承载两种语义）
-----------------------------------------------------------------------
1. `PB:*` 节点：`dimension = exp.get(k)` —— 是该量**在某方程里的指数**（标量，如 `-1`），
   与量纲毫无关系（`PB:pq:area` 写成 `-1`，而面积真量纲是 `L²`）。
2. 其余节点（种子切片）：`dimension` 是**量纲串**（如 `M*L^2*T^-2`），语义正确，
   但**部分串混用单位符号**（`temperature="K"`、`gas_const="...mol^-1*K^-1"`，
   而量纲符号应作 `Th`/`N`）—— 是「量纲 vs 单位」的第二处混杂。

迁移规则（确定、可复算、留痕）
------------------------------
* `PB:*`：`dimension` → **`exponent_in_term`**（指数语义）；
  另按独立 SI 基表补 **`dimension_vector`**（量纲语义，查得到才写）。
* 其余：`dimension` → **`dimension_vector`**：
    - 声明串可解析且与独立 SI 基表一致 → 原样保留（干净）
    - 声明串不可解析（含单位符号）或与基表不一致 → **取基表权威值**，
      并把原声明记入 `dimension_vector_declared`（铁律 #22：改正须留痕）
    - 基表未收录 → 原样保留，标注为「未收录」
* 幂等：已迁移的节点不再改动。

用法
----
    python migrate_dimension_field.py --dry-run     # 预演（默认）
    python migrate_dimension_field.py --apply       # 落地
    python migrate_dimension_field.py --audit       # 只出审计报告
"""
import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import dimension_table as dt            # noqa: E402
import si_base_table as si              # noqa: E402

SEED_DIR = os.path.join(ROOT, "10_种子数据")
TARGETS = [
    (os.path.join(HERE, "physicsbabel_raw.json"), "props"),
    (os.path.join(ROOT, "06_PoC", "etl", "normalized.json"), "props"),
    (os.path.join(ROOT, "06_PoC", "graph_data_phase36.json"), "attrs"),
]
SEED_GLOB_DIR = SEED_DIR


def vec_to_str(v):
    """向量 dict → 规范量纲串（M*L^2*T^-2）；空 → '1'。"""
    parts = []
    for d in dt.DIM:
        e = v.get(d, 0)
        if abs(e) > 1e-9:
            ei = int(round(e))
            parts.append(d if ei == 1 else "%s^%d" % (d, ei))
    return "*".join(parts) if parts else "1"


# 声明串里的**符号归一**（确定性，非物理猜测）：实测种子串只用到这两个单位符号
#   K（开尔文）→ Th（热力学温度量纲）、mol（摩尔）→ N（物质的量量纲）。
# `dimensionless` 为无量纲的书写形式 → 空向量。
SYMBOL_FIX = {"K": "Th", "mol": "N"}
DIMENSIONLESS_TOKENS = {"dimensionless", "1", "", "0"}


def parse_declared(declared):
    """把（可能混用单位符号的）声明串解析为向量；不可解析返回 None。"""
    s = str(declared).strip()
    if s.lower() in DIMENSIONLESS_TOKENS:
        return {}
    out = []
    for tok in s.replace("·", "*").replace(" ", "*").split("*"):
        tok = tok.strip()
        if not tok:
            continue
        if "^" in tok:
            base, _, e = tok.partition("^")
        else:
            base, e = tok, ""
        base = SYMBOL_FIX.get(base, base)
        out.append(base + ("^" + e if e else ""))
    return dt.parse_dim_string("*".join(out))


def authoritative_dim(name):
    """按量名取独立 SI 基表向量；未收录返回 None。"""
    c = si.canon(name)
    if c is None:
        return None
    return dict(si.load()[c]["dim"])


def _norm(v):
    return tuple(round(float(v.get(k, 0)), 9) for k in dt.DIM)


def migrate_node(n, container_key, stats, changes):
    p = n.get(container_key)
    if not isinstance(p, dict) or "dimension" not in p:
        return False
    nid = n.get("id", "?")
    declared = p.pop("dimension")
    name = p.get("name") or nid.split(":")[-1]

    if nid.startswith("PB:"):
        p["exponent_in_term"] = declared
        a = authoritative_dim(name)
        if a is not None:
            p["dimension_vector"] = vec_to_str(a)
        stats["pb_to_exponent"] += 1
        return True

    # 非 PB：量纲语义
    a = authoritative_dim(name)
    parsed = parse_declared(declared)
    if a is not None and parsed is not None and _norm(parsed) == _norm(a):
        p["dimension_vector"] = vec_to_str(a)
        stats["seed_agree"] += 1
    elif a is not None:
        p["dimension_vector"] = vec_to_str(a)
        p["dimension_vector_declared"] = declared
        reason = "unit_symbol" if parsed is None else "mismatch"
        stats["seed_authoritative_" + reason] += 1
        changes.append({"id": nid, "name": name, "declared": declared,
                        "authoritative": vec_to_str(a), "reason": reason})
    elif parsed is not None:
        canon_s = vec_to_str(parsed)
        p["dimension_vector"] = canon_s
        if canon_s != str(declared).strip():
            p["dimension_vector_declared"] = declared
            raw = dt.parse_dim_string(declared)          # 不做符号归一
            reason = ("canonical_order" if raw is not None and _norm(raw) == _norm(parsed)
                      else "unit_symbol")
            stats["seed_" + reason] += 1
            changes.append({"id": nid, "name": name, "declared": declared,
                            "authoritative": canon_s, "reason": reason})
        else:
            stats["seed_kept"] += 1
    else:
        p["dimension_vector"] = declared
        p["dimension_vector_declared"] = declared
        stats["seed_unparsable_unknown"] += 1
        changes.append({"id": nid, "name": name, "declared": declared,
                        "authoritative": None, "reason": "unparsable_unknown"})
    return True


def process_file(path, container_key, stats, changes, apply):
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    nodes = doc.get("nodes") or []
    n_changed = 0
    for n in nodes:
        if migrate_node(n, container_key, stats, changes):
            n_changed += 1
    if n_changed and apply:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
    return n_changed


def audit(apply=False):
    """执行迁移（apply=False 为预演），返回统计与变更清单。"""
    stats = collections.Counter()
    changes = []
    per_file = {}
    targets = list(TARGETS)
    import glob
    for sp in sorted(glob.glob(os.path.join(SEED_GLOB_DIR, "seed_*.json"))):
        targets.append((sp, "props"))
    for path, ck in targets:
        c = process_file(path, ck, stats, changes, apply)
        if c:
            per_file[os.path.relpath(path, ROOT).replace("\\", "/")] = c
    return stats, per_file, changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="落地（默认为预演/dry-run）")
    ap.add_argument("--audit", action="store_true", help="只出审计（不写文件）")
    ap.add_argument("--json", help="把审计/变更写 JSON")
    a = ap.parse_args()

    apply = a.apply and not a.audit
    stats, per_file, changes = audit(apply=apply)
    print("模式:", "APPLY（已写盘）" if apply else "DRY-RUN（预演，未写盘）")
    print("受影响文件:")
    for k, v in per_file.items():
        print("   %-46s %d 节点" % (k, v))
    print("节点处置:")
    for k in ["pb_to_exponent", "seed_agree", "seed_kept",
              "seed_unit_symbol", "seed_canonical_order",
              "seed_authoritative_mismatch", "seed_authoritative_unit_symbol",
              "seed_unparsable_unknown"]:
        if stats[k]:
            print("   %-34s %d" % (k, stats[k]))
    print("需留痕的改动（取基表权威值 / 未收录且不可解析）:", len(changes))
    for c in changes[:20]:
        print("   %-22s 声明 %-26s → 权威 %s  [%s]"
              % (c["id"], c["declared"], c["authoritative"], c["reason"]))
    if len(changes) > 20:
        print("   ... 其余 %d 条见 JSON" % (len(changes) - 20))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"mode": "apply" if apply else "dry-run",
                       "per_file": per_file, "stats": dict(stats),
                       "changes": changes}, f, ensure_ascii=False, indent=2)
        print("→ %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
