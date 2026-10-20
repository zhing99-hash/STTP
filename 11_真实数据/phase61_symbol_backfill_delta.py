#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
"""phase61_symbol_backfill_delta.py —— G1 · 符号回填（零抓取）

本轮命题
--------
规划 `下一阶段规划_数据扩展与前端优化_20261019.md` F1：`symbols`（变量表）覆盖率
0.16% → ≥90%。**数据早就在库里**：`has_symbol` 已有 24,341 条边，只是从没反向聚合回节点。

但「聚合回来」不等于「抄回来」——侦察（`06_PoC/_recon_symbols.py`）把三件事量出来了：

  R1  `has_symbol` 指向的多是**物理量节点**而非 Symbol 节点（23,983 / 24,341）。
      ⇒ 直接取 `target.name` 会得到「mass」「length」这种**量名**，写进 `symbols` 就是
        **把量名冒充字形**（节点字段语义是字形，见 `etl_pipeline` 的 has_symbol 生成侧）。
  R2  PhysicsBabel 的 `symbols` 列与 `keys` 列**不逐位对齐**（0 / 4000 按位首字母全对）。
      ⇒ 「第 i 个 keys 配第 i 个符号」是错的，且错得很安静。
  R3  以「共现签名」代替逐位对齐：某量名与某字形出现在**完全相同的方程集合**里，
      则二者互为指称。56 个量名 100% 唯一解，且人工正对照全对
      （mass→m、length→ℓ、time→t、force→F、energy→E、velocity→v、acceleration→a）。

落盘字段（节点 props）
----------------------
  symbols                 **源自己声明的**字形列表，按在公式里的出现次序排列。
                          ★ 只收「源声明」（PhysicsBabel 的 `symbols` 列 / 人工变量表）；
                            **沿边反推**出来的字形（目标 Symbol 节点自带 `latex`）**不进这里** ——
                            判级模型 A10/A4 的 `symbol_in_source()` 会读 `props.symbols`，
                            把推出来的值写进去就等于**用边证明边**（实测会假升 2 条 has_symbol，
                            门禁 `P1-main-level-matches-recompute` 当场抓住）。
  symbol_table            [{sym, quantity, quantity_id, src[, sym_curated|sym_candidate|reason]}]
                          **变量表**：全部端点逐条列出（含未确认字形），这里才是「给用户看」的字段
  symbols_rule_version    规则版本 `s1`
  symbols_at_commit       落盘时完整 HEAD sha
  symbols_unresolved      仅当 `symbols` 为空时出现，写明原因
                          （`no_has_symbol_edge` / `targets_carry_no_glyph` / `glyphs_only_from_edges`）

★ 非破坏性：只加字段；已有 `symbols` 原样保留（若与推导不一致则**拒绝产出**，不静默覆盖）。
★ 幂等/次序无关：推导对输入边序**不敏感**（脚本内用逆序重算一遍自证 S6）。
★ 零抓取：只读本地 `normalized.json` + `physicsbabel_circuits.parquet`。

用法
----
  python 11_真实数据/phase61_symbol_backfill_delta.py [--dry] [--out PATH] [--sample N]
  bash sttp.sh symbolbackfill [--apply]     # = 本脚本 + apply_delta
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
PARQUET = os.path.join(HERE, "physicsbabel_circuits.parquet")
OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase61_symbol_backfill_delta.json")

RULE_VERSION = "s2"
# 规则变更史（`symbols_rule_version` 就是干这个的：数据里记着「按哪一版规则算的」）
#   s1（2026-10-20 起草，未出本机）：所有字形（源声明 + 沿边反推）都写进 `symbols`。
#       ✗ 实测被门禁打回：`P1-main-level-matches-recompute` 抓到 2 条 has_symbol 假升
#         （`OP:fo:mirror→OP:sy:u`、`NT:fo:euclid_algorithm→NT:sy:n`）——
#         判级模型 A10 的 `symbol_in_source()` 会读 `props.symbols`，于是**用边证明边**。
#   s2（2026-10-20 定稿）：`symbols` 只收**源自己声明**的字形（PhysicsBabel `symbols` 列 /
#       人工变量表）；沿边反推的字形只进 `symbol_table`。变量表照样全覆盖，循环证明不存在。
FORMULA_LABELS = ("Formula", "Definition", "Lemma", "Theorem", "Law")


def head_full():
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:                                   # noqa: BLE001
        return None


# ---------------------------------------------------------------- PB 字形表
def build_pb_map(nodes, edges):
    """PhysicsBabel 量名 → 字形（共现签名法）。

    返回 (name2glyph, eq_tokens, target2key, diag)。**不逐位对齐**：`symbols` 列是方程内的
    字形集合，与 `keys` 同长但不同序（R2）。用「出现在哪些方程里」的签名做匹配：
    两个对象的签名完全相同 ⇒ 它们指同一批方程 ⇒ 互为指称。

    `target2key` 解决另一半问题：图上的边指向**节点**（`PQ:c` 名字叫 "Speed of light"），
    而 parquet 的键是 `light_speed` —— 名字对不上，只能靠「这些节点各自出现在哪些方程里」
    再对一次签名。`PB:pq:<key>` 的 id 自带键名，正好当**正对照**：推断出的键必须与 id 后缀一致。
    """
    diag = {"rows_read": 0, "rows_used": 0, "len_mismatch": 0,
            "dup_equation": 0, "ambiguous_names": [], "unique_names": 0, "names": 0,
            "targets": 0, "target_ok": 0, "target_bad": []}
    eq_index = {}
    for n in nodes:
        if n["id"].startswith("PB:fo:"):
            eq = (n.get("props") or {}).get("equation")
            if eq:
                eq_index[eq] = n["id"]
    try:
        import pandas as pd
    except Exception as exc:                            # noqa: BLE001
        print("[WARN] pandas 不可用（%s）⇒ PhysicsBabel 字形不可解" % exc)
        return {}, {}, diag
    if not os.path.isfile(PARQUET):
        print("[WARN] 缺少 %s ⇒ PhysicsBabel 字形不可解" % os.path.relpath(PARQUET, ROOT))
        return {}, {}, diag

    df = pd.read_parquet(PARQUET, columns=["keys", "symbols", "equation", "plausible"])
    diag["rows_read"] = int(len(df))
    df = df[df["plausible"].astype(bool)]
    # 与摄取侧同一口径：2 ≤ len(keys) ≤ 6（逗号数 1..5）
    df = df[df["keys"].astype(str).str.count(",").between(1, 5)]
    df = df[df["equation"].astype(str).isin(set(eq_index))]
    diag["rows_used"] = int(len(df))

    name_sig = collections.defaultdict(set)
    glyph_sig = collections.defaultdict(set)
    eq_tokens = {}
    for eq, keys_s, syms_s in zip(df["equation"].astype(str),
                                  df["keys"].astype(str),
                                  df["symbols"].astype(str)):
        keys = [k.strip() for k in keys_s.split(",") if k.strip()]
        toks = [t.strip() for t in syms_s.split("·") if t.strip()]
        if len(keys) != len(toks):
            diag["len_mismatch"] += 1
            continue
        if eq in eq_tokens:
            if set(eq_tokens[eq]) != set(toks):
                diag["dup_equation"] += 1
                eq_tokens[eq] = None            # 同式不同字形 ⇒ 不可判定，弃用
            continue
        eq_tokens[eq] = toks
        for k in keys:
            name_sig[k].add(eq)
        for t in toks:
            glyph_sig[t].add(eq)

    name2glyph = {}
    diag["names"] = len(name_sig)
    for name, sig in name_sig.items():
        cand = sorted(g for g, gs in glyph_sig.items() if gs == sig)
        if len(cand) == 1:
            name2glyph[name] = cand[0]
            diag["unique_names"] += 1
        else:
            name2glyph[name] = None
            diag["ambiguous_names"].append((name, cand))

    # ---- 节点 → PhysicsBabel 键（同样用签名，PB:pq:<key> 的 id 当正对照）----
    tgt_sig = collections.defaultdict(set)
    eq_of_node = {n["id"]: str((n.get("props") or {}).get("equation") or "")
                  for n in nodes if n["id"].startswith("PB:fo:")}
    for e in edges:
        if e.get("type") != "has_symbol" or not str(e["source"]).startswith("PB:fo:"):
            continue
        eq = eq_of_node.get(e["source"], "")
        if eq in eq_tokens and eq_tokens[eq]:
            tgt_sig[e["target"]].add(eq)
    target2key = {}
    for tid, sig in tgt_sig.items():
        cand = sorted(k for k, s in name_sig.items() if s == sig)
        if len(cand) != 1:
            continue
        target2key[tid] = cand[0]
        diag["targets"] += 1
        if tid.startswith("PB:pq:"):
            if tid[len("PB:pq:"):] == cand[0]:
                diag["target_ok"] += 1
            else:
                diag["target_bad"].append((tid, cand[0]))
    return name2glyph, eq_tokens, target2key, diag


# ---------------------------------------------------------------- 单节点推导
def _order_by_appearance(glyphs, text):
    """按字形在公式文本里的**首次出现位置**排序；找不到的排在后面（不丢）。"""
    def key(g):
        if g and text:
            i = text.find(g)
            if i >= 0:
                return (0, i, len(g), g)
        return (1, 0, len(g or ""), g or "")
    return sorted(glyphs, key=key)


def derive_node(node, out_targets, by_id, name2glyph, eq_tokens, target2key):
    """返回 (symbols, symbol_table, unresolved_reason)。纯函数：只看入参，不看顺序之外的状态。"""
    props = node.get("props") or {}
    text = str(props.get("latex") or props.get("equation") or "")
    # 判定「字形是否属于本公式」的可查文本：公式本体 + 简述 + 名称。
    # ★ 只查 latex 会误杀合法符号（`M` 的 `n` 只出现在 "n-dimensional manifold" 里）；
    #   放到整段文本上查，才是「这个记号是否真的属于这条记录」的正确问法。
    hay = " ".join(str(props.get(k) or "") for k in ("latex", "equation", "informal", "name"))
    is_pb = node["id"].startswith("PB:fo:") and text in eq_tokens

    table, glyphs, unresolved = [], [], []
    ordered = []      # 只装**源声明**的字形（进 `symbols`）；`glyphs` 装全部字形（供统计）
    for tid in out_targets:
        t = by_id.get(tid)
        if not t:
            continue
        tp = t.get("props") or {}
        qname = tp.get("name")
        curated = tp.get("symbol")
        if "Symbol" in (t.get("labels") or []):
            curated = curated or tp.get("latex") or qname
        sym, src = None, None
        if is_pb:
            key = target2key.get(tid)
            if key and name2glyph.get(key):
                sym, src = name2glyph[key], "pb_symbols_column"
        if not sym and curated:
            sym, src = curated, "edge_target"
        ent = {"sym": sym, "quantity": qname, "quantity_id": tid, "src": src or "none"}
        if sym and curated and sym != curated:
            # ★ 差异必须**记录**而不是被挑选掉：同一量在不同语境下写法不同（c vs c₀）
            ent["sym_curated"] = curated
        if sym and hay and sym not in hay:
            # ★ 硬规矩：写进 `symbols` 的字形**必须在公式原文里出现**。
            #   策展节点说法与原文不符时（charge 策展写作 Q，原文写 q），
            #   **不猜、不挑**，只记录候选并留空 —— 空位是信息，错字形是污染。
            ent["sym_candidate"] = sym
            ent["sym"] = None
            ent["reason"] = "glyph_not_in_formula"
            sym = None
        if sym:
            glyphs.append(sym)
            # ★ 只有**源自己声明的字形**才进 `symbols`（PB 的 `symbols` 列 / 人工变量表）。
            #   从边反推出来的（目标 Symbol 节点自带 `latex`）**只进 `symbol_table`**：
            #   判级模型 A10/A4 的 `symbol_in_source()` 会读 `props.symbols`，
            #   若把「由他推出来的字形」写进去，就变成**用边证明边** —— 实测会让 2 条
            #   has_symbol 由 source_asserted 假升到 rule_checked（OP:fo:mirror / NT:fo:euclid_algorithm），
            #   门禁 `P1-main-level-matches-recompute` 直接抓到。
            declared = (src == "pb_symbols_column")
            if declared:
                ordered.append(sym)
        else:
            unresolved.append(tid)
        table.append(ent)

    # 去重保序（同一字形被多个量共用时只列一次；表里仍逐条保留）
    seen, uniq = set(), []
    for g in _order_by_appearance(ordered, text):
        if g not in seen:
            seen.add(g)
            uniq.append(g)
    reason = None
    if not uniq:
        if not out_targets:
            reason = "no_has_symbol_edge"
        elif not glyphs:
            reason = "targets_carry_no_glyph"
        else:
            # 字形有，但**没有一个来自源的声明**（都是沿边反推的）⇒ 不写 `symbols`，见上
            reason = "glyphs_only_from_edges"
    return uniq, table, reason, unresolved


# ---------------------------------------------------------------- 主流程
def derive_all(nodes, edges, name2glyph, eq_tokens, target2key, reverse=False):
    by_id = {n["id"]: n for n in nodes}
    out = collections.defaultdict(list)
    es = list(reversed(edges)) if reverse else edges
    for e in es:
        if e.get("type") == "has_symbol":
            out[e["source"]].append(e["target"])
    for k in out:
        out[k].sort()                      # 次序无关：端点排序后再推导
    res = {}
    for n in nodes:
        if not any(lb in FORMULA_LABELS for lb in (n.get("labels") or [])):
            continue
        res[n["id"]] = derive_node(n, out.get(n["id"], []), by_id,
                                   name2glyph, eq_tokens, target2key)
    return res, out


def main():
    ap = argparse.ArgumentParser(description="Phase 61 G1 符号回填 delta")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--sample", type=int, default=0, help="打印 N 条抽样供人工核对")
    a = ap.parse_args()

    if not os.path.isfile(NORM):
        print("[不可用] 缺少权威图 06_PoC/etl/normalized.json（被 .gitignore 刻意排除）")
        print("  恢复：bash sttp.sh export，或从 Release 资产下载同名快照。")
        return 2

    with open(NORM, encoding="utf-8") as f:
        g = json.load(f)
    nodes, edges = g["nodes"], g["edges"]
    print("=" * 92)
    print("Phase 61 delta —— G1 符号回填（`symbols` / `symbol_table`）")
    print("  输入 %d 节点 / %d 边  规则 %s" % (len(nodes), len(edges), RULE_VERSION))
    print("=" * 92)

    name2glyph, eq_tokens, target2key, diag = build_pb_map(nodes, edges)
    print("\n【PhysicsBabel 字形表】")
    print("  parquet 行 %d → 过滤后与图内方程相交 %d；长度不符丢弃 %d；同式异形丢弃 %d"
          % (diag["rows_read"], diag["rows_used"], diag["len_mismatch"], diag["dup_equation"]))
    print("  量名 %d：唯一解 %d ／ 多解 %d"
          % (diag["names"], diag["unique_names"], len(diag["ambiguous_names"])))
    for nm, cand in diag["ambiguous_names"][:5]:
        print("    多解 %-14s → %s" % (nm, cand))
    print("  节点→键：可定 %d 个（其中 PB:pq:<键> 正对照 %d 通过 / %d 不符）"
          % (diag["targets"], diag["target_ok"], len(diag["target_bad"])))
    for tid, key in diag["target_bad"][:5]:
        print("     [正对照不符] %s 推断为 %s" % (tid, key))
    ctrl = "  ".join("%s→%s" % (k, name2glyph.get(k) or "?")
                     for k in ("mass", "length", "time", "force", "energy",
                               "velocity", "acceleration"))
    print("  正对照（人工已知）：%s" % ctrl)

    res, out = derive_all(nodes, edges, name2glyph, eq_tokens, target2key)
    res_rev, _ = derive_all(nodes, edges, name2glyph, eq_tokens, target2key, reverse=True)

    # ---------------- 组装 delta ----------------
    commit = head_full()
    now = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    d_nodes = []
    prov = collections.Counter()
    n_formula = 0
    n_cover = 0
    n_table_cover = 0        # 变量表非空的公式（真正的「可判断性」指标）
    n_glyph = 0
    n_entry = 0
    curated_diff = []
    human_kept = []          # 落盘前就有人写 symbols 的（数学侧 8 条）——**一律不覆盖**
    human_conflict = []
    idem_conflict = []       # 同一版规则重跑算出不同值（须 0 —— 否则是非确定性）
    upgrades = {}            # 旧规则版本 → 覆盖条数（规则升级要可见）
    rejected = []            # 字形不在公式原文 ⇒ 不写，只留候选（计数受 S3 约束）
    rejected_pb = []         # PB 侧出现拒写 = 对齐算法坏了（硬断言）
    for n in nodes:
        r = res.get(n["id"])
        if r is None:
            continue
        n_formula += 1
        syms, table, reason, unresolved = r
        props = n.get("props") or {}
        keep = props.get("symbols")
        if syms:
            n_cover += 1
            n_glyph += len(syms)
        if table:
            n_table_cover += 1
        if keep and not props.get("symbols_rule_version"):
            # ★ 人工值优先：不写、不覆盖，只对账（差异单列，交给人判断谁对）
            human_kept.append(n["id"])
            if sorted(keep) != sorted(syms):
                human_conflict.append((n["id"], keep, syms))
            continue
        if keep and props.get("symbols_rule_version") and props["symbols_rule_version"] != RULE_VERSION:
            # 旧规则落过盘 ⇒ 本次按新规则**重算覆盖**（这是升级，不是漂移）
            upgrades[props["symbols_rule_version"]] = upgrades.get(props["symbols_rule_version"], 0) + 1
        elif keep and props.get("symbols_rule_version") and list(keep) != list(syms):
            # 同一版规则重跑却算出了不同的值 ⇒ 非确定性，必须停下
            idem_conflict.append((n["id"], keep, syms))
        n_entry += len(table)
        for ent in table:
            prov[ent["src"]] += 1
            if ent.get("sym_curated"):
                curated_diff.append((n["id"], ent["sym"], ent["sym_curated"]))
            if ent.get("reason") == "glyph_not_in_formula":
                rejected.append((n["id"], ent["sym_candidate"], ent["quantity_id"]))
                if n["id"].startswith("PB:fo:"):
                    rejected_pb.append((n["id"], ent["sym_candidate"], ent["quantity_id"]))
        d_nodes.append({
            "id": n["id"],
            "props": dict(
                {"symbols": syms, "symbol_table": table,
                 "symbols_rule_version": RULE_VERSION, "symbols_at_commit": commit},
                **({"symbols_unresolved": reason} if reason else {}))})

    # ---------------- 自检 ----------------
    print("\n【回填自检】")
    cover = 100.0 * (n_cover + len(human_kept)) / max(1, n_formula)
    cover_tbl = 100.0 * n_table_cover / max(1, n_formula)
    mism = sum(1 for k in res if res[k][0] != res_rev[k][0])
    pb_bad = []
    for n in nodes:
        if not n["id"].startswith("PB:fo:"):
            continue
        text = str((n.get("props") or {}).get("equation") or "")
        toks = eq_tokens.get(text)
        if not toks or n["id"] not in res:
            continue
        if set(res[n["id"]][0]) != set(toks):
            pb_bad.append((n["id"], sorted(res[n["id"]][0]), sorted(toks)))
    rows = [
        ("S1 覆盖率", cover >= 90.0 and cover_tbl >= 90.0,
         "`symbols`（源声明）覆盖 %d（推导 %d + 人工保留 %d）/ %d = %.2f%%；"
         "`symbol_table`（变量表）覆盖 %d / %d = %.2f%%（门槛均 ≥90%%）"
         % (n_cover + len(human_kept), n_cover, len(human_kept), n_formula, cover,
            n_table_cover, n_formula, cover_tbl)),
        ("S2 与源声明一致", not pb_bad,
         "PhysicsBabel 公式推导字形集 != parquet `symbols` 列的：%d 条（须 0）" % len(pb_bad)),
        ("S3 拒写受控", not rejected_pb and len(rejected) <= 0.01 * max(1, n_entry),
         "因原文查无而拒写 %d 条（须 ≤1%% —— 再多就是上游写法乱了，该停下来查而不是继续写）；"
         "其中 PhysicsBabel 侧 %d 条（须 0：源自己声明的字形不可能查无）"
         % (len(rejected), len(rejected_pb))),
        ("S4 人工值未覆盖", not idem_conflict,
         "人工保留 %d 条（不覆盖）；同版规则重跑却算出不同值的 %d 条（须 0）；"
         "旧规则重算覆盖：%s" % (len(human_kept), len(idem_conflict), upgrades or "无")),
        ("S5 次序无关", mism == 0,
         "边序逆序重算后 symbols 不一致的节点：%d（须 0）" % mism),
        ("S6 非真空", n_entry > 0 and n_cover > 0 and len(curated_diff) > 0,
         "变量表条目 %d（>0）；「同一量两种写法」条目 %d（>0 才证明这条路径真的在走）"
         % (n_entry, len(curated_diff))),
    ]
    bad = 0
    for name, ok, detail in rows:
        print("  [%s] %-14s %s" % ("PASS" if ok else "FAIL", name, detail))
        bad += 0 if ok else 1

    print("\n  字形来源：%s" % dict(prov.most_common()))
    print("  `symbols` 为空但变量表非空的公式：%d（字形只来自边 ⇒ 按规矩不写进 `symbols`）"
          % (n_formula - n_cover - len(human_kept)))
    if curated_diff:
        print("  同一量的两种写法（%d 条，前 5）：" % len(curated_diff))
        for nid, x, y in curated_diff[:5]:
            print("     %-12s 源声明 %-8s 策展 %-8s" % (nid, x, y))
    if human_conflict:
        print("  人工变量表与推导不符（%d 条 —— **不覆盖**，登记为待复核）：" % len(human_conflict))
        for nid, keep, got in human_conflict:
            print("     %-28s 人工 %s" % (nid, keep))
            print("     %-28s 推导 %s" % ("", got))
    if rejected:
        print("  原文查无字形 ⇒ 只留候选不写（%d 条，前 5）：" % len(rejected))
        for nid, cand, qid in rejected[:5]:
            print("     %-22s 候选 %-10s ← %s" % (nid, cand, qid))

    if a.sample:
        print("\n【抽样 %d 条 · 人工核对用】" % a.sample)
        picked = [n for n in nodes if n["id"] in res and res[n["id"]][0]][:a.sample]
        for n in picked:
            syms, table, _, _ = res[n["id"]]
            print("  %-14s %s" % (n["id"], str((n.get("props") or {}).get("latex"))[:64]))
            print("      symbols = %s" % syms)
            for ent in table:
                print("        %-8s ← %-14s %s%s"
                      % (ent["sym"], ent["quantity"], ent["quantity_id"],
                         ("   (策展写法 %s)" % ent["sym_curated"]) if ent.get("sym_curated") else ""))

    if bad:
        print("\n[FATAL] 自检未过 ⇒ **拒绝产出 delta**（宁可不落盘，也不把错的固化进快照）")
        return 1

    payload = {
        "meta": {
            "phase": 61, "tag": "symbol-backfill",
            "generator": "phase61_symbol_backfill_delta.py",
            "theme": "G1 符号回填：has_symbol 边 → 节点 symbols / symbol_table（零抓取）",
            "base_graph": os.path.relpath(NORM, ROOT),
            "symbol_rule": RULE_VERSION,
            "at_commit": commit, "stamp": now,
            "n_formula_nodes": n_formula, "n_covered": n_cover,
            "coverage_pct": round(cover, 2),
            "n_table_covered": n_table_cover,
            "coverage_table_pct": round(cover_tbl, 2),
            "n_glyphs": n_glyph, "n_table_entries": n_entry,
            "glyph_sources": dict(prov.most_common()),
        },
        "nodes": d_nodes,
        "delete_nodes": [],
        "edges": [],
        "delete_edges": [],
    }
    print("\n【delta 汇总】nodes=%d（只加字段，边 0 条，删除 0）" % len(d_nodes))
    if a.dry:
        print("[DRY] 未落盘")
        return 0
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print("-> %s (%.1f MB)" % (os.path.relpath(a.out, ROOT), os.path.getsize(a.out) / 1e6))
    print("   落盘：python 06_PoC/apply_delta.py --delta %s --phase 61 --tag symbolbackfill --apply"
          % os.path.relpath(a.out, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
