#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""Phase 69 · 化学侧与 symbols 派生（侦察先行，见 07_交付物/Phase69_...侦察_20261022.md）

这一锤为什么落在这些数上（先把「达不达得到」说清楚）
----------------------------------------------------
Phase 69 的**侦察**先量了三件事，结论决定了本脚本只做能做的一半：

    V1  F4「SMILES ≥ 90%」其实**已达标**：canonical_smiles 2,086/2,133 = 97.8%。
        缺的是**口径** —— `smiles`（22 条）与 `canonical_smiles`（2,086 条）并存，
        门槛没规定算哪个 ⇒ 本脚本定 `canonical_smiles` 为权威，并把 22 条规范化补齐。
    V2  F4「Reaction 名称 ≥ 95%」**不可达**：源头 ElementKG2.0 的 800 条反应
        `name` 字段值全是 None（不是 ETL 漏抽）；其中 610 条连反应方向 `>>` 都没有；
        图边里同时有 reactant_of + product_of 的**只有 59 条**。
        ⇒ 诚实上限 59.1%。本脚本**只补这 59 条**，其余标 `unnamed_no_evidence`。
    V3  82 条空 `symbols` 里 **78 条 symbol_table 有完整符号** ⇒
        不是「推不出」，是「推出来了没写回」。修复不碰 Phase 61 规则本身。

★★ 本脚本的硬线：**不编造**。
   - 符号必须来自该公式自己的 `symbol_table`，一个都不许多；
   - 反应名必须来自该反应自己的 reactant/product **边的分子名**，拼不出就标无名；
   - SMILES 必须能被 RDKit 解析；解析失败的不补，标 `no_smiles`。
   这三条各自有判据（S2/S3/S5），且各自有红探针（R34/R35）守着。

symbols 的排序规则（V4，侦察时量出来的，不是猜的）
------------------------------------------------
`symbols` **不是** `symbol_table` 的顺序 —— 实测 4,999/5,000 条顺序不同。
真实规则是：**按符号在 `latex` 里首次出现的位置排序**。
（去重后 5,000 条符合此规则；8 条不符合的全部是 `MX:def:/lemma:/thm:` 人工值，
  它们不走规则，本脚本**不碰**它们，并在自检 S1 里显式列出。）

用法：
  python 11_真实数据/phase69_chem_delta.py [--dry] [--sample N]
  python 11_真实数据/phase69_chem_delta.py --selfcheck     # 含负对照
  bash sttp.sh chembackfill [--apply]                      # = 本脚本 + apply_delta
"""
import argparse
import collections
import datetime
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase69_chem_delta.json")

RULE_VERSION = "chem-s1"

# 数学侧人工值前缀：它们的 symbols/symbol_table 是人工填的，不遵循 latex 排序规则，
# 本脚本一律不碰（侦察量出的 8 条例外 + 12 条无变量表的都在这一族）。
MANUAL_PREFIX = "MX:"

# 反应名三态（与 G3/G4 同构：硬线是「有没有依据」，不是「有没有值」）
ST_NAMED = "named_from_source"      # 源头就给了名字（Rhea 939 + curated 11）
ST_DERIVED = "derived_from_edges"   # 从 reactant/product 边的分子名拼出（59 条）
ST_UNNAMED = "unnamed_no_evidence"  # 拼不出也不编（699 条）


def _head_full():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT).decode("ascii", "ignore").strip()
    except Exception:
        return ""


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------
# A1 · symbols 派生
# --------------------------------------------------------------------------

def unresolved_syms(symbol_table):
    """`sym` 为 null 的条目数 —— 规则识别出量但**字形在公式里找不到**，
    于是故意不指定符号（Phase 61 的诚实设计，`reason: glyph_not_in_formula`）。

    ★ 这些**不是**待补的缺口，是「已知未能确定」。把它们算进符号集合
      就等于把 `null` 变成 `'None'` 字符串 —— 那是编造（C1 判据第一版就
      差点这么做：它把 str(None) 也算进了集合，逼得人只能去改数据）。
    """
    n = 0
    for e in (symbol_table or []):
        if isinstance(e, dict) and e.get("sym") is None:
            n += 1
    return n


def derive_symbols(symbol_table, latex):
    """按「latex 中首次出现位置」排序派生符号列表。

    - 符号**只**来自 `symbol_table`（不凭空加，判据 S2 守）；
    - 排序按 `latex` 中首次出现位置（V4，与已有 5,000 条同规则，判据 S1 守）；
    - 不在 latex 里出现的符号排在末尾，保持 `symbol_table` 原顺序；
    - `sym` 为 null 的条目**跳过**（不编造，见 `unresolved_syms`）。
    """
    syms = []
    for e in (symbol_table or []):
        s = e.get("sym") if isinstance(e, dict) else e
        if s is not None and str(s) not in syms:
            syms.append(str(s))
    if not latex:
        return syms

    def key(s):
        p = latex.find(s)
        return (0, p) if p >= 0 else (1, 0)

    # 稳定排序：出现在 latex 里的按位置，其余保持表内顺序（sort 稳定 ⇒ 末尾仍按原序）
    ordered = sorted(syms, key=key)
    return ordered


# --------------------------------------------------------------------------
# A2 · SMILES 规范化（RDKit，可缺失 ⇒ fail-safe 不补）
# --------------------------------------------------------------------------

def _canonicalize(smiles):
    """返回 (ok, canonical_or_None)。RDKit 不可用或解析失败 ⇒ (False, None)。"""
    try:
        from rdkit import Chem, RDLogger  # noqa: F401
        RDLogger.DisableLog("rdApp.*")
    except Exception:
        return (False, None)
    try:
        m = Chem.MolFromSmiles(smiles)
        if m is None:
            return (True, None)      # 有库但解析不了 ⇒ 明确失败，不是没库
        return (True, Chem.MolToSmiles(m))
    except Exception:
        return (True, None)


# --------------------------------------------------------------------------
# B · 反应名：只从「自己的边」拼
# --------------------------------------------------------------------------

def build_reaction_names(nodes, edges):
    """返回 {rid: {"name":..., "status":..., "n_reactant":..., "n_product":...}}

    ★ 硬线：名字里的每个分子名都必须来自该反应自己的 `reactant_of` / `product_of` 边。
      只有 reagent_of（试剂）或两种边都不全的 ⇒ 一律 `unnamed_no_evidence`。
    """
    by_id = {n["id"]: n for n in nodes}
    per = collections.defaultdict(lambda: collections.defaultdict(list))
    for e in edges:
        s = e.get("source") or e.get("from")
        t = e.get("target") or e.get("to")
        ty = e.get("type")
        if ty in ("reactant_of", "product_of"):
            per[s][ty].append(t)

    out = {}
    for n in nodes:
        if "Reaction" not in (n.get("labels") or []):
            continue
        rid = n["id"]
        p = n.get("props") or {}
        src_name = p.get("name")
        if src_name:
            out[rid] = {"name": src_name, "status": ST_NAMED,
                        "n_reactant": 0, "n_product": 0}
            continue

        rs, ps = per[rid].get("reactant_of", []), per[rid].get("product_of", [])
        rn = [((by_id.get(t) or {}).get("props") or {}).get("name") for t in rs]
        pn = [((by_id.get(t) or {}).get("props") or {}).get("name") for t in ps]
        # ★ 任一侧缺、或任一分子的名字本身缺失 ⇒ 拼不完整 ⇒ 不编
        if not rs or not ps or any(x is None for x in rn + pn):
            out[rid] = {"name": None, "status": ST_UNNAMED,
                        "n_reactant": len(rs), "n_product": len(ps)}
            continue
        name = " + ".join(rn) + " → " + " + ".join(pn)
        out[rid] = {"name": name, "status": ST_DERIVED,
                    "n_reactant": len(rs), "n_product": len(ps)}
    return out


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------

def selfcheck():
    """离线自检（不联网、不改图）。S2/S3/S5/S7 含负对照 —— 证明「绿」不是恒真。"""
    rep = []

    # ---- 正例：排序规则能重现历史数据（非人工值部分）----
    g = json.load(open(NORM, encoding="utf-8"))
    forms = [n for n in g["nodes"] if "Formula" in (n.get("labels") or [])]
    auto = [n for n in forms
            if not n["id"].startswith(MANUAL_PREFIX)
            and (n.get("props") or {}).get("symbols")
            and (n.get("props") or {}).get("latex")]
    same = sum(1 for n in auto
               if derive_symbols((n.get("props") or {}).get("symbol_table"),
                                 (n.get("props") or {}).get("latex"))
               == list((n.get("props") or {}).get("symbols")))
    rep.append(("S1 排序规则能重现历史（非人工值 %d 条）" % len(auto),
                len(auto) > 0 and same == len(auto),
                "重现 %d / %d" % (same, len(auto))))

    # ---- S2 负对照：不得凭空加符号 ----
    st = [{"sym": "a"}, {"sym": "b"}]
    got = derive_symbols(st, "b = a")
    rep.append(("S2 符号只来自 symbol_table（不多不少）",
                got == ["b", "a"] and len(got) == len(st),
                "derive=%s" % got))
    got_bad = derive_symbols(st + [{"sym": "zzz"}], "b = a")
    rep.append(("S2b 负对照：多一个表外符号 ⇒ 必须被检出",
                len(got_bad) == 3 and got_bad != got,
                "表内 2 个却产出 3 个 ⇒ 可被判据发现"))

    # ---- S3 负对照：顺序必须按 latex ----
    rep.append(("S3 按 latex 出现顺序（b 在 a 前）",
                derive_symbols([{"sym": "a"}, {"sym": "b"}], "b = a") == ["b", "a"],
                "latex='b = a' ⇒ ['b','a']"))

    # ---- S4：sym=null（规则未确定字形）必须跳过，且要能被数出来 ----
    tbl_n = [{"sym": None, "sym_candidate": "Q", "reason": "glyph_not_in_formula"},
             {"sym": "k"}]
    rep.append(("S4 sym=null 的条目跳过（不得写成 'None'）",
                derive_symbols(tbl_n, "k = 1") == ["k"] and unresolved_syms(tbl_n) == 1,
                "derive=%s unresolved=%d" % (derive_symbols(tbl_n, "k = 1"),
                                             unresolved_syms(tbl_n))))

    # ---- S5 负对照：反应名不得编造 ----
    nodes = [
        {"id": "R1", "labels": ["Reaction"], "props": {}},
        {"id": "R2", "labels": ["Reaction"], "props": {}},
        {"id": "M1", "labels": ["Molecule"], "props": {"name": "water"}},
        {"id": "M2", "labels": ["Molecule"], "props": {"name": "salt"}},
    ]
    edges = [
        {"source": "R1", "target": "M1", "type": "reactant_of"},
        {"source": "R1", "target": "M2", "type": "product_of"},
        {"source": "R2", "target": "M1", "type": "reagent_of"},   # 只有试剂 ⇒ 不可拼
    ]
    got = build_reaction_names(nodes, edges)
    rep.append(("S5 有 reactant+product 才拼名，只有试剂的不拼",
                got["R1"]["status"] == ST_DERIVED
                and got["R1"]["name"] == "water → salt"
                and got["R2"]["status"] == ST_UNNAMED
                and got["R2"]["name"] is None,
                "R1=%s/%s  R2=%s/%s" % (got["R1"]["status"], got["R1"]["name"],
                                        got["R2"]["status"], got["R2"]["name"])))

    # ---- S6：源头有名字的必须保留，不得被覆盖 ----
    nodes2 = [{"id": "R3", "labels": ["Reaction"], "props": {"name": "Rhea 原名"}}]
    got2 = build_reaction_names(nodes2, [])
    rep.append(("S6 源头名字优先，不被派生覆盖",
                got2["R3"]["status"] == ST_NAMED and got2["R3"]["name"] == "Rhea 原名",
                "%s" % got2["R3"]["name"]))

    # ---- S7：分子名为空 ⇒ 不得拼 ----
    nodes3 = [
        {"id": "R4", "labels": ["Reaction"], "props": {}},
        {"id": "M3", "labels": ["Molecule"], "props": {}},   # 无名分子
    ]
    edges3 = [{"source": "R4", "target": "M3", "type": "reactant_of"},
              {"source": "R4", "target": "M3", "type": "product_of"}]
    got3 = build_reaction_names(nodes3, edges3)
    rep.append(("S7 分子本身无名 ⇒ 不拼（不得拿 id 当名字）",
                got3["R4"]["status"] == ST_UNNAMED and got3["R4"]["name"] is None,
                "%s" % got3["R4"]["status"]))

    bad = [r for r in rep if not r[1]]
    for name, ok, note in rep:
        # ★ 必须是 `[PASS]`/`[FAIL]` —— red_probe 只认这个形状的行。
        #   第一版打印裸 `FAIL S1`：判据确实红了，但探针看不见，于是 R34/R35 报「没变红」。
        #   判据的输出格式**本身就是接口**，不对齐等于对探针隐形。
        print("[%s] %s  %s" % ("PASS" if ok else "FAIL", name, note))
    print("\n自检：PASS %d / FAIL %d" % (len(rep) - len(bad), len(bad)))
    return 1 if bad else 0


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Phase 69 化学侧与 symbols 派生")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()

    if a.selfcheck:
        return selfcheck()

    g = json.load(open(NORM, encoding="utf-8"))
    nodes, edges = g["nodes"], g["edges"]
    forms = [n for n in nodes if "Formula" in (n.get("labels") or [])]
    mols = [n for n in nodes if "Molecule" in (n.get("labels") or [])]
    rxns = [n for n in nodes if "Reaction" in (n.get("labels") or [])]

    d_nodes = []
    st = collections.Counter()
    dropped = collections.Counter()

    # ---------- A1 · symbols 派生 ----------
    for n in forms:
        p = n.get("props") or {}
        if p.get("symbols"):
            continue
        if n["id"].startswith(MANUAL_PREFIX):
            st["sym_skip_manual"] += 1
            continue
        tbl = p.get("symbol_table")
        if not tbl:
            st["sym_no_table"] += 1
            continue
        new = derive_symbols(tbl, p.get("latex"))
        if not new:
            st["sym_empty_derive"] += 1
            continue
        newp = dict(p)
        newp["symbols"] = new
        newp["symbols_derive_version"] = RULE_VERSION
        # ★ 不完整 ≠ 完整：表里若有 sym=null（规则未能确定字形），必须显式记下来，
        #   否则补了 3 个符号就看着像「这个公式的符号全齐了」。
        n_unres = unresolved_syms(tbl)
        if n_unres:
            newp["symbols_incomplete"] = True
            newp["symbols_unresolved"] = n_unres
            st["sym_incomplete"] += 1
        d_nodes.append({"id": n["id"], "props": newp})
        st["sym_derived"] += 1

    # ---------- A2 · SMILES 口径：canonical_smiles 为权威 ----------
    rdkit_ok = rdkit_missing = 0
    for n in mols:
        p = n.get("props") or {}
        if p.get("canonical_smiles"):
            continue
        raw = p.get("smiles")
        if not raw:
            st["smiles_no_source"] += 1
            continue
        have_lib, can = _canonicalize(raw)
        if not have_lib:
            rdkit_missing += 1
            dropped["smiles_rdkit_missing"] += 1
            continue
        if not can:
            rdkit_ok += 1
            dropped["smiles_parse_fail"] += 1
            continue
        newp = dict(p)
        newp["canonical_smiles"] = can
        newp["canonical_smiles_source"] = "rdkit_from_smiles"
        d_nodes.append({"id": n["id"], "props": newp})
        st["smiles_canonicalized"] += 1

    # ---------- B · 反应名三态 ----------
    rnames = build_reaction_names(nodes, edges)
    for n in rxns:
        info = rnames.get(n["id"])
        if not info:
            continue
        st["rxn_" + info["status"]] += 1
        p = n.get("props") or {}
        if info["status"] == ST_NAMED:
            # 源头有名：名字本身不动，但**状态也要标** —— 三态必须覆盖全部反应，
            # 否则「没标状态」会被读成「没查过」，那正是 D101 记过的坑。
            newp = dict(p)
            newp["reaction_name_status"] = ST_NAMED
            newp["reaction_name_rule"] = RULE_VERSION
            d_nodes.append({"id": n["id"], "props": newp})
            continue
        newp = dict(p)
        newp["reaction_name_status"] = info["status"]
        if info["name"]:
            newp["reaction_name"] = info["name"]
        newp["reaction_name_rule"] = RULE_VERSION
        d_nodes.append({"id": n["id"], "props": newp})

    # ---------- 判据（S2/S3/S5/S6/S7 已在 selfcheck 里；这里查落盘一致性）----------
    checks = []
    derived_ids = {d["id"] for d in d_nodes}
    # C1：补出来的 symbols 必须**不多不少**等于 symbol_table 的符号集合
    src_by_id = {n["id"]: (n.get("props") or {}) for n in nodes}
    setbad = []
    for d in d_nodes:
        p = d["props"]
        if "symbols" not in p:
            continue
        tbl = src_by_id[d["id"]].get("symbol_table") or []
        # ★ sym=null 的条目**不算**（规则明确未指定字形）—— 把它们算进来就会
        #   逼着人把 null 写成 'None' 去凑齐集合，那是编造（D101 同族：判据逼歪分类）。
        want = {str(e.get("sym")) for e in tbl
                if isinstance(e, dict) and e.get("sym") is not None}
        if set(p["symbols"]) != want or len(p["symbols"]) != len(want):
            setbad.append(d["id"])
    checks.append(("C1 补的 symbols 集合 == symbol_table 里已确定字形的符号（不多不少）",
                   not setbad, "越界 %d 条 %s" % (len(setbad), setbad[:3])))

    # C1b：被标「不完整」的必须真的有 sym=null 的条目，且数量对得上（不得乱标）
    incbad = [d["id"] for d in d_nodes
              if d["props"].get("symbols_incomplete")
              and d["props"].get("symbols_unresolved")
              != unresolved_syms(src_by_id[d["id"]].get("symbol_table") or [])]
    checks.append(("C1b 标「不完整」的必须有真实未确定条目且数量一致",
                   not incbad, "越界 %d 条 %s" % (len(incbad), incbad[:3])))

    # C2：补出来的反应名，其每个分子名都必须能在图里找到对应 Molecule
    molnames = {((n.get("props") or {}).get("name")) for n in mols}
    namebad = []
    for d in d_nodes:
        nm = d["props"].get("reaction_name")
        if not nm:
            continue
        for part in nm.replace("→", "+").split("+"):
            part = part.strip()
            if part and part not in molnames:
                namebad.append((d["id"], part))
    checks.append(("C2 反应名里的分子名必须在 Molecule 表里存在（不得拼造）",
                   not namebad, "越界 %d 条 %s" % (len(namebad), namebad[:3])))

    # C3：不编造 —— 状态为 unnamed 的一律不得带 reaction_name
    forge = [d["id"] for d in d_nodes
             if d["props"].get("reaction_name_status") == ST_UNNAMED
             and d["props"].get("reaction_name")]
    checks.append(("C3 判为「无依据」的反应不得带名字（不编造）",
                   not forge, "越界 %d 条 %s" % (len(forge), forge[:3])))

    # ★ 以下两条看的是**合并后的最终状态**，不是「本轮新增了多少」。
    #   第一版写的是「本轮 sym_derived > 0」：重跑时已补过的不再产出 ⇒ 判据误报红，
    #   而数据其实是对的。delta 脚本必须幂等 —— 同一份输入跑两次，结论应当一致。
    final_sym = dict((n["id"], (n.get("props") or {}).get("symbols"))
                     for n in forms)
    final_stat = dict((n["id"], (n.get("props") or {}).get("reaction_name_status"))
                      for n in rxns)
    for d in d_nodes:
        if "symbols" in d["props"]:
            final_sym[d["id"]] = d["props"]["symbols"]
        if "reaction_name_status" in d["props"]:
            final_stat[d["id"]] = d["props"]["reaction_name_status"]

    n_sym = sum(1 for v in final_sym.values() if v)
    cnt = collections.Counter(final_stat.values())
    checks.append(("C4 非真空（最终态：三件事都真的有值）",
                   n_sym > 0 and cnt[ST_NAMED] > 0
                   and cnt[ST_DERIVED] > 0 and cnt[ST_UNNAMED] > 0,
                   "最终有 symbols %d ｜ named %d ｜ derived %d ｜ unnamed %d"
                   % (n_sym, cnt[ST_NAMED], cnt[ST_DERIVED], cnt[ST_UNNAMED])))

    # C5：三态必须覆盖**全部**反应 —— 漏标会被读成「没查过」（D101 同族）
    ok3 = (ST_NAMED, ST_DERIVED, ST_UNNAMED)
    checks.append(("C5 反应名三态覆盖全部 Reaction（不得漏标）",
                   all(v in ok3 for v in final_stat.values()),
                   "已覆盖 %d / %d"
                   % (sum(1 for v in final_stat.values() if v in ok3), len(rxns))))

    print("\n【统计】%s" % json.dumps(dict(st), ensure_ascii=False))
    print("【丢弃】%s ｜ RDKit 缺失 %d ｜ 解析失败 %d"
          % (json.dumps(dict(dropped), ensure_ascii=False), rdkit_missing, rdkit_ok))

    bad = [c for c in checks if not c[1]]
    for name, ok, note in checks:
        # ★ 必须是 `[PASS]`/`[FAIL]` —— red_probe 只认这个形状的行。
        #   第一版打印裸 `FAIL S1`：判据确实红了，但探针看不见，于是 R34/R35 报「没变红」。
        #   判据的输出格式**本身就是接口**，不对齐等于对探针隐形。
        print("[%s] %s  %s" % ("PASS" if ok else "FAIL", name, note))
    print("判据：PASS %d / FAIL %d" % (len(checks) - len(bad), len(bad)))

    if a.sample:
        print("\n【样例】")
        for d in d_nodes[:a.sample]:
            print("  ", d["id"], json.dumps(d["props"], ensure_ascii=False)[:200])

    if bad:
        print("\n[FATAL] 自检未过 ⇒ **拒绝产出 delta**")
        return 1

    payload = {
        "meta": {
            "phase": 69, "tag": "chem-symbols",
            "generator": "phase69_chem_delta.py",
            "theme": "F4 化学侧定口径 + symbols 派生写回 + 反应名三态（侦察先行）",
            "base_graph": os.path.relpath(NORM, ROOT),
            "rule": RULE_VERSION,
            "at_commit": _head_full(), "stamp": _now(),
            "n_formula": len(forms), "n_molecule": len(mols), "n_reaction": len(rxns),
            "stats": dict(st),
            "dropped": dict(dropped),
            "note": "symbols 派生自**本公式自己的** symbol_table，按 latex 首次出现位置排序"
                    "（与已有 5,000 条同规则）；MX: 数学侧人工值不碰。"
                    "反应名三态：%s（源头原名）/ %s（从自己的 reactant+product 边拼出）/"
                    "%s（拼不出，不编造）。F4 的「Reaction ≥95%%」经侦察判为不可达，"
                    "本脚本**只补有依据的 59 条**，不凑数。"
                    % (ST_NAMED, ST_DERIVED, ST_UNNAMED),
        },
        "nodes": d_nodes,
        "delete_nodes": [],
        "edges": [],
        "delete_edges": [],
    }
    print("\n【delta 汇总】nodes=%d" % len(d_nodes))
    if a.dry:
        print("[DRY] 未落盘")
        return 0
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print("-> %s" % os.path.relpath(a.out, ROOT))
    print("   落盘：python 06_PoC/apply_delta.py --delta %s --phase 69 "
          "--tag chembackfill --apply" % os.path.relpath(a.out, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
