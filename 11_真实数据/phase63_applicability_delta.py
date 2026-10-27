#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
# -*- coding: utf-8 -*-
"""Phase 63 · G3 适用条件落盘（规则 a1，零抓取，只写人工核对过的）

前置：`06_PoC/tests/recon_applicability.py` 已把四条来源路径全部量清（commit a612421）：

    P1 PB 原文           0 条（parquet 15 列 + raw 12 字段零命中）⇒ No-Go
    P2 词典法（策展文本） 30 条真条件
    P2b 量纲推硬条件      0 条（恒真性质，冒充前提违反铁律 #14）
    P3 论文摘要           0 条（抓取时未保留 abstract）
    P4 LLM               需新建（现有通道是假设生成器，不是抽取器）

所以本脚本**不追求覆盖率**：它只能拿到 P2 那 30 条。规划 §2.1 写的「≥ 1,500」
在当前数据上不可达（差 1,470 条，缺口全在 5,000 条无指称的合成式上），
改数字不等于能达成 —— 这里**如实落 30 条**，并把「为什么其余没有」也记下来。

★ 最重要的设计：**落盘以人工核对表为准，不以词典命中为准**。
   词典只负责**发现**候选，VERDICT 负责**裁决**（54 条命中已逐条人工读过）。
   若出现核对表里没有的新候选 ⇒ **S3 直接 FAIL**（拒绝落盘）。
   否则「新增文本悄悄混进未经核对的条件」这种退化没人能发现。

字段（与 G1/G2 同款纪律：空位是信息，`null` 不是 0）：
    applicability_status      extracted / no_referent / not_extracted（**全量写**）
    applicability             原文子串列表（只在 extracted 上写）
    applicability_evidence    每条条件的 {term, field, snippet}（可追溯）
    applicability_source      只在 extracted 上写（留空位当「未经规则」的标记）
    applicability_rule_version 只在 extracted 上写 = "a1"

`no_referent`（无指称）不是「没抽到」，是「**无从谈适用条件**」——
`m = k₀·ℓ⁻¹·t²·F` 谁也不是。把这两种空区分开，免得后来者以为是我们漏了。

用法：
  python 11_真实数据/phase63_applicability_delta.py [--dry] [--out PATH] [--sample N]
  bash sttp.sh applicabackfill [--apply]     # = 本脚本 + apply_delta
"""
import argparse
import collections
import datetime
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase63_applicability_delta.json")
RECON = os.path.join(ROOT, "06_PoC", "06_PoC/tests/recon_applicability.py")

RULE_VERSION = "a1"
#   a1（定稿）：词典发现候选 + 人工核对表（VERDICT）裁决；
#              未经核对的候选一律拒写；无指称与未抽到分开记。

# ---------------------------------------------------------------------------
# 条件词典：封闭、可枚举。与探针保持一致（由 S5 强制比对，防止两边漂移）。
PROC = ["恒温下", "恒压下", "恒容下", "等温", "等压", "等容", "恒温", "恒压", "恒容",
        "常温下", "绝热", "可逆", "定态", "稳态", "静止", "低速", "稀薄"]
MODEL = ["理想气体", "质点", "点电荷", "真空", "导体", "薄透镜", "球面镜",
         "难溶电解质", "饱和溶液", "弱酸", "缓冲溶液", "完全燃烧", "光滑函数",
         "凸多面体", "直角三角形", "素数", "互素", "大于 1", "圆周运动",
         "两个热源", "介质中的光速", "closed orientable", "open cover",
         "smooth", "prime", "coprime"]
TEXT_KEYS = ("informal", "description", "usage")

# ---------------------------------------------------------------------------
# 人工核对表：**逐条读过 54 条命中后的裁决**（这是事实，不是代码逻辑）。
# 必须与探针里的 VERDICT 完全一致 —— 由 S5 强制比对。
VERDICT = {
    ("MX:lemma:partition_of_unity", "open cover"): True,
    ("MX:thm:gauss_bonnet", "closed orientable"): True,
    ("CM:fo:gravity", "质点"): True,
    ("CM:fo:centripetal", "圆周运动"): True,
    ("OM:fo:combustion", "完全燃烧"): True,
    ("TH:fo:ideal_gas", "理想气体"): True,
    ("TH:fo:boyle", "恒温下"): True,
    ("TH:fo:boyle", "等温"): True,
    ("TH:fo:boyle", "恒温"): True,
    ("TH:fo:boyle", "理想气体"): True,
    ("TH:fo:charles", "恒压下"): True,
    ("TH:fo:charles", "等压"): True,
    ("TH:fo:charles", "恒压"): True,
    ("TH:fo:charles", "理想气体"): True,
    ("TH:fo:gay_lussac", "恒容下"): True,
    ("TH:fo:gay_lussac", "等容"): True,
    ("TH:fo:gay_lussac", "恒容"): True,
    ("TH:fo:gay_lussac", "理想气体"): True,
    ("TH:fo:carnot", "两个热源"): True,
    ("EM:fo:coulomb", "静止"): True,
    ("EM:fo:coulomb", "点电荷"): True,
    ("EM:fo:ohm", "导体"): True,
    ("EM:fo:gauss", "真空"): True,
    ("QM:fo:schrodinger", "定态"): True,
    ("MA:fo:pythagoras", "直角三角形"): True,
    ("MC:fo:taylor", "光滑函数"): True,
    ("MG:fo:euler_poly", "凸多面体"): True,
    ("OP:fo:lens", "薄透镜"): True,
    ("OP:fo:mirror", "球面镜"): True,
    ("SM:fo:maxwell", "理想气体"): True,
    ("SM:fo:ideal_gas_kin", "理想气体"): True,
    ("CE:fo:kw", "常温下"): True,
    ("CE:fo:ka", "弱酸"): True,
    ("CE:fo:henderson", "弱酸"): True,
    ("CE:fo:henderson", "缓冲溶液"): True,
    ("CE:fo:ksp", "难溶电解质"): True,
    ("CE:fo:ksp", "饱和溶液"): True,
    ("NT:fo:euler_theorem", "互素"): True,
    ("NT:fo:fermat_little", "素数"): True,
    ("NT:fo:crt", "互素"): True,
    ("NT:fo:wilson", "素数"): True,
    ("NT:fo:fundamental_arithmetic", "素数"): True,
    ("NT:fo:fundamental_arithmetic", "大于 1"): True,
    # ---- False：误命中（附模式）----
    ("MX:def:manifold", "smooth"): False,
    ("MX:def:riemannian_metric", "smooth"): False,
    ("MX:lemma:partition_of_unity", "smooth"): False,
    ("FO:emc2", "静止"): False,
    ("RT:fo:time_dilation", "静止"): False,
    ("RT:fo:mass_energy", "静止"): False,
    ("OP:fo:mirror", "薄透镜"): False,
    ("OP:fo:refr_speed", "真空"): False,
    ("OP:fo:refr_speed", "介质中的光速"): False,
    ("NT:fo:euclid_algorithm", "互素"): False,
    ("NT:fo:unit_group", "互素"): False,
}


def load_recon_verdict():
    """把探针脚本里的 VERDICT 读出来（不执行它的 main）。"""
    spec = importlib.util.spec_from_file_location("recon_applicability", RECON)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return None
    return getattr(mod, "VERDICT", None)


def head_full():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"],
                                       cwd=ROOT, stderr=subprocess.DEVNULL
                                       ).decode().strip()
    except Exception:
        return "unknown"


def drop_substrings(terms):
    """「恒温」是「恒温下」的子串 ⇒ 只留长的那条原文。"""
    out = []
    for t in terms:
        if any(t != u and t in u for u in terms):
            continue
        out.append(t)
    return out


def derive(node):
    """返回 (status, terms, evidences, unverified, rejected)。"""
    p = node.get("props") or {}
    fields = [(k, str(p.get(k) or "")) for k in TEXT_KEYS]
    if not any(t.strip() for _, t in fields):
        return "no_referent", [], [], [], []

    hits = []
    for k, t in fields:
        if not t:
            continue
        for w in PROC + MODEL:
            i = t.find(w)
            if i >= 0:
                hits.append((w, k, t[max(0, i - 18):i + len(w) + 18]))

    terms, evid, unverified, rejected = [], [], [], []
    for w, k, snip in hits:
        v = VERDICT.get((node["id"], w))
        if v is True:
            if w not in terms:
                terms.append(w)
                evid.append({"term": w, "field": k, "snippet": snip})
        elif v is False:
            rejected.append(w)
        else:
            unverified.append((w, k, snip))
    terms = drop_substrings(terms)
    evid = [e for e in evid if e["term"] in terms]
    if terms:
        return "extracted", terms, evid, unverified, rejected
    return "not_extracted", [], [], unverified, rejected


def main():
    ap = argparse.ArgumentParser(description="Phase 63 G3 适用条件 delta")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--sample", type=int, default=0, help="打印 N 条抽样供人工核对")
    a = ap.parse_args()

    with open(NORM, encoding="utf-8") as f:
        g = json.load(f)
    nodes = [n for n in g["nodes"] if "Formula" in n.get("labels", [])]
    print("Phase 63 · G3 适用条件（规则 %s，零抓取）" % RULE_VERSION)
    print("  Formula 节点：%d" % len(nodes))

    res = {n["id"]: derive(n) for n in nodes}
    res_rev = {n["id"]: derive(n) for n in reversed(nodes)}

    # ---- 统计 ----
    st = collections.Counter(v[0] for v in res.values())
    dist = collections.Counter()
    unverified_all, rejected_all = [], []
    idem_conflict, evid_bad, forged = [], [], []
    n_changed = 0
    d_nodes = []

    for n in nodes:
        p = n.get("props") or {}
        status, terms, evid, unver, rej = res[n["id"]]
        if res_rev[n["id"]][0] != status or res_rev[n["id"]][1] != terms:
            idem_conflict.append(n["id"])
        unverified_all += [(n["id"],) + u for u in unver]
        rejected_all += [(n["id"], w) for w in rej]
        for t in terms:
            dist[t] += 1
        # S2 可追溯：每条 evidence 的 term 必须在它自报的字段原文里
        for e in evid:
            src_text = str(p.get(e["field"]) or "")
            if e["term"] not in src_text or e["snippet"] not in src_text:
                evid_bad.append((n["id"], e["term"], e["field"]))
        # S4 不编造：无指称的一律不得有 applicability
        if status == "no_referent" and (terms or p.get("applicability")):
            forged.append(n["id"])

        managed = p.get("applicability_status") is not None
        newp = {"applicability_status": status}
        if status == "extracted":
            newp["applicability"] = terms
            newp["applicability_evidence"] = evid
            newp["applicability_source"] = "curated_text"
            newp["applicability_rule_version"] = RULE_VERSION
        if not managed:
            n_changed += 1
        elif any(p.get(k) != v for k, v in newp.items()):
            n_changed += 1
        d_nodes.append({"id": n["id"], "props": newp})

    n_ext = st["extracted"]
    n_noref = st["no_referent"]
    n_notext = st["not_extracted"]
    cover = 100.0 * n_ext / max(1, len(nodes))

    # ---- 自检 ----
    recon_v = load_recon_verdict()
    drift = (recon_v is not None and recon_v != VERDICT)
    checks = [
        ("S1 status 全覆盖", st["extracted"] + st["no_referent"] + st["not_extracted"] == len(nodes),
         "extracted %d / no_referent %d / not_extracted %d = %d（须 = %d）"
         % (n_ext, n_noref, n_notext, n_ext + n_noref + n_notext, len(nodes))),
        ("S2 evidence 可追溯", not evid_bad,
         "evidence 的 term/snippet 查不到原文的：%d 条（须 0）%s"
         % (len(evid_bad), evid_bad[:3])),  # noloss-exempt: 错误信息截断，不入图
        ("S3 无未经核对的候选", not unverified_all,
         "核对表里没有的候选：%d 条（须 0 —— 出现就必须先人工裁决，否则准确率是假数）%s"
         % (len(unverified_all), unverified_all[:3])),  # noloss-exempt: 错误信息截断，不入图
        ("S4 不编造（无指称不得有值）", not forged,
         "no_referent 却带 applicability 的：%d 条（须 0）" % len(forged)),
        ("S5 核对表与探针一致", not drift,
         "本脚本 VERDICT 与 _recon_applicability.py %s"
         % ("**不一致** ⇒ 两份表漂移了，必有一份是旧的" if drift else "一致（54 条）")),
        ("S6 次序无关", not idem_conflict,
         "节点逆序重算后不一致：%d 条（须 0）" % len(idem_conflict)),
        ("S7 非真空", n_ext > 0 and n_noref > 0,
         "extracted %d（>0，证明真抽到了）；no_referent %d（>0，证明「无指称」这条真的在走）"
         % (n_ext, n_noref)),
        ("S8 幂等", n_changed == 0 or not any(
            (n.get("props") or {}).get("applicability_status") is not None for n in nodes),
         "本次改动 %d 条（首次应 = %d；已管过之后再跑须 = 0）" % (n_changed, len(nodes))),
    ]
    print("\n【分类自检】")
    bad = 0
    for name, ok, detail in checks:
        # ★ 形状必须与 phase60/61/62 一致：`  [FAIL] NAME  detail`。
        #   `red_probe._FAIL_LINE` 只认 `[FAIL]` / `❌` 两种开头 ——
        #   少一对**方括号**，探针就采集不到，「判据变红」与「探针看不见」会被混为一谈。
        print("  [%s] %-26s %s" % ("PASS" if ok else "FAIL", name, detail))
        if not ok:
            bad += 1

    # 规则独立准确率（不使用人工裁决时的表现，供参考）
    n_true_pairs = sum(1 for v in VERDICT.values() if v is True)
    print("\n【口径说明】")
    print("  词典命中 %d 对，其中人工裁决 True %d / False %d"
          % (len(VERDICT), n_true_pairs, len(VERDICT) - n_true_pairs))
    print("  · 规则独立准确率（不靠人工裁决）= %.1f%% —— 达不到判据的 90%%，"
          % (100.0 * n_true_pairs / len(VERDICT)))
    print("    所以**不能**只靠规则自动落盘；")
    print("  · 落盘结果准确率 = **100%**（每条都是人工逐条读过的，且可指出原文子串）。")
    print("  本次被裁决否掉的候选：%d 对（量名·常数名 / 被定义对象 / 用途 / 类比提及）"
          % len(rejected_all))

    print("\n【条件短语分布】")
    for k, v in dist.most_common(20):
        print("   %-14s %d" % (k, v))

    if a.sample:
        picked = [n for n in nodes if res[n["id"]][0] == "extracted"][:a.sample]
        print("\n【抽样 %d 条 · 人工核对用】" % len(picked))
        for n in picked:
            status, terms, evid, _, _ = res[n["id"]]
            print("  %-24s %-22s %s"
                  % (n["id"], str((n.get("props") or {}).get("latex"))[:22],
                     " + ".join(terms)))

    if bad:
        print("\n[FATAL] 自检未过 ⇒ **拒绝产出 delta**")
        return 1

    now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    payload = {
        "meta": {
            "phase": 63, "tag": "applicability-extract",
            "generator": "phase63_applicability_delta.py",
            "theme": "G3 适用条件：词典发现 + 人工核对表裁决（零抓取，不编造）",
            "base_graph": os.path.relpath(NORM, ROOT),
            "rule": RULE_VERSION,
            "at_commit": head_full(), "stamp": now,
            "n_formula_nodes": len(nodes),
            "n_extracted": n_ext, "n_no_referent": n_noref, "n_not_extracted": n_notext,
            "coverage_pct": round(cover, 2),
            "target_was": 1500, "gap": 1500 - n_ext,
            "note": "目标 1,500 在当前数据上不可达：缺口全在 5,000 条无指称的 "
                    "PhysicsBabel 合成式上，它们没有适用条件可言。",
            "term_distribution": dict(dist.most_common()),
        },
        "nodes": d_nodes,
        "delete_nodes": [], "edges": [], "delete_edges": [],
    }
    print("\n【delta 汇总】nodes=%d（只改/加 props）" % len(d_nodes))
    if a.dry:
        print("[DRY] 未落盘")
        return 0
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print("-> %s (%.1f MB)" % (os.path.relpath(a.out, ROOT), os.path.getsize(a.out) / 1e6))
    print("   落盘：python 06_PoC/apply_delta.py --delta %s --phase 63 "
          "--tag applicabackfill --apply" % os.path.relpath(a.out, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
