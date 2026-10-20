#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
"""phase62_domain_delta.py —— G2 · 学科分类（纯规则，零抓取）

本轮命题
--------
规划 `下一阶段规划_数据扩展与前端优化_20261019.md` F2：`domain` 二级学科，
「5,090 个全部有细分；人工抽检 50 个，错分 ≤ 5」。

侦察（`06_PoC/_recon_domain.py`）先把这件事的**性质**量清楚了，其中三条直接改变了做法：

  R1  `pb_domains` 是**源声明**：链路 parquet.domains → ingest（行 95 直拷，无加工）
      → normalized，与上游逐一比对 5000/5000 一致 ⇒ 权威输入，优先于任何规则推断。
  R2  六个标签里 `mechanics` 是**背景词**（100% 出现，因为 mass 出现在所有公式里），
      `constants` 是**修饰语**（含物理常数，不是学科）。
      ⇒ 主标签必须从**特征词**（em / thermo / chemistry / photometry）里选，
        否则 5,000 条会被背景词吞掉，等于没分。
  R3  我手写的「量 → 领域」映射表与 PB 的 domains 口径**双向集合相等 5000/5000**
      （源 ⊆ 量证据 且 量证据 ⊆ 源）。
      ⇒ 映射表口径与上游对齐，**可以拿它去补源没说到的地方**，且两条路径互为校验。

★ 最重要的一条认知：**这 5,000 个公式是量纲齐次合成式**（`m = k₀ · X^a·Y^b·Z^c`），
  它们天生跨域 —— `em,mechanics` 这类双标签是**正确答案**，不是缺陷。
  所以**不强行消歧成单一学科**：`domain` 存主标签（供浏览分组），
  `domain_tags` 存完整标签（不丢信息）。若硬塞单选，3,211 条立刻变错分，
  F2 的「错分 ≤ 5」根本不可能满足。

落盘字段（Formula 节点 props）
------------------------------
  domain                 主二级学科（**单值**，受控词表内）—— 供学科浏览/分组/导出切片
  domain_tags            完整二级学科标签（数组，受控词表内，按词表次序确定性排序）
  domain_source          判定来源：`pb_declared` / `curated` / `rule_name`
  domain_rule_version    规则版本 `d1`
  domain_groups          参与量导出的领域组（mech/em/thermo/chem/photo），**不含 const**；
                         这是可确定性重算的派生证据，写下来是为了将来做化学二级细分时有据可依

★ 绝不覆盖人工值：落盘前 `domain` 已有细分的 89 个（curated_seed / mathxiv）**只补字段不改值**。
★ 非破坏性：不改判级、不删边；同一 commit 上重跑幂等。
★ 零抓取：只读本地 `normalized.json`。

用法
----
  python 11_真实数据/phase62_domain_delta.py [--dry] [--out PATH] [--sample N]
  bash sttp.sh domainbackfill [--apply]      # = 本脚本 + apply_delta
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
NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase62_domain_delta.json")

RULE_VERSION = "d1"
#   d1（定稿）：源声明（pb_domains）与参与量（dim_exponents + 映射表）双算互校验；
#              主标签取特征词；跨域不消歧，`domain_tags` 保留全标签。

# ---------------------------------------------------------------------------
# 「量 → 领域组」映射表（人工知识，规则的本体）
#   R3 已用 PB 上游口径双向验证：集合相等 5000/5000。
#   const 组是**物理常数**，不单独成科 —— 「公式里含 c₀」不等于「这公式是相对论」，
#   把它当学科就是把噪声读成信号（与 Phase 61「原文查无就不写」同一条纪律）。
# ---------------------------------------------------------------------------
Q_GROUP = {}
for _grp, _keys in (
    ("const", ["light_speed", "planck", "gas_constant", "boltzmann", "elem_charge",
               "avogadro", "grav_const"]),
    ("mech", ["mass", "length", "time", "velocity", "acceleration", "force", "momentum",
              "ang_momentum", "action", "energy", "power", "pressure", "torque",
              "inertia", "gravity", "omega", "frequency", "area", "volume", "density",
              "viscosity", "kin_viscosity", "flowrate", "stiffness", "surf_tension",
              "angle"]),
    ("em", ["charge", "current", "voltage", "resistance", "resistivity", "conductivity",
            "capacitance", "inductance", "mag_flux", "bfield", "efield", "permittivity",
            "permeability"]),
    ("thermo", ["temperature", "entropy", "heat_capacity", "specific_heat",
                "thermal_cond", "thermal_diff", "expansion"]),
    ("chem", ["amount", "concentration", "molar_mass", "molar_energy", "cat_activity"]),
    ("photo", ["lum_intensity", "luminance", "illuminance"]),
):
    for _k in _keys:
        Q_GROUP[_k] = _grp

# pb 标签 → 领域组
TAG2GROUP = {"mechanics": "mech", "em": "em", "thermo": "thermo",
             "chemistry": "chem", "photometry": "photo", "constants": "const"}

# 领域组 → 二级学科
GROUP2DOMAIN = {"mech": "physics.mechanics", "em": "physics.em",
                "thermo": "physics.thermo", "chem": "chem.general",
                "photo": "physics.optics"}

# 受控词表：**全部取自库里已有的人工标注**，不新造学科
#   （唯一新增 chem.general —— 源只给到 chemistry 一级，二级无证据 ⇒ 不臆造细分，
#    这是「空位是信息」，不能假装分到了 chem.kinetics）
CONTROLLED = [
    "physics.mechanics", "physics.em", "physics.thermo", "physics.optics",
    "physics.relativity", "physics.qm", "physics.statmech", "physics.gravitation",
    "chem.general", "chem.acidbase", "chem.electrochem", "chem.energy",
    "chem.equilibrium", "chem.kinetics", "chem.periodic", "chem.solubility",
    "chem.stoich", "chem.thermo",
    "math.DG", "math.algebra", "math.analysis", "math.calculus", "math.geometry",
    "math.numbertheory",
]
_ORDER = {d: i for i, d in enumerate(CONTROLLED)}

# 主标签选取次序：**特征词优先**（R2），mech 只在无特征词时才兜底
FEATURE_ORDER = ["em", "thermo", "chem", "photo"]

# 名称关键词兜底：仅用于「既无 pb_domains 也无 dim_exponents」的节点。
#   当前全库只命中 1 个（pb:newton_second，种子节点，只有 label）。
#   ★ 新增此类种子节点必须同步扩充本表，否则判不出来 ⇒ S1 会红（fail-loud，不静默放过）。
NAME_RULES = [("newton", "physics.mechanics")]


def head_full():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"],
                                       cwd=ROOT, stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "unknown"


def groups_from_dims(dim):
    """参与量 → 领域组集合（剔除 const；const 不构成学科）"""
    out = set()
    for k in (dim or {}):
        g = Q_GROUP.get(k)
        if g is None or g == "const":
            continue
        out.add(g)
    return out


def groups_from_tags(pb):
    """源声明标签 → 领域组集合（剔除 const）"""
    if not pb:
        return set()
    out = set()
    for t in str(pb).split(","):
        g = TAG2GROUP.get(t.strip())
        if g and g != "const":
            out.add(g)
    return out


def pick_primary(groups):
    """主标签：特征词优先（固定次序 ⇒ 确定性），无特征词则 mech 兜底"""
    for g in FEATURE_ORDER:
        if g in groups:
            return GROUP2DOMAIN[g]
    return GROUP2DOMAIN["mech"]


def derive(node):
    """返回 (domain, tags, groups, source, reason)

    domain 为 None 表示判不出来（会触发 S1 失败）。

    ★ 人工值的识别**必须独立于本规则**：用「有没有 `domain_rule_version`」这个
      **外部标记**，而不是「domain 是否非 phys」。
      为什么：第一版写成 `if cur and cur != "phys"` ⇒ 首次落盘后规则写的值
      也满足该条件，二次运行时 5,090 条全被误认成人工值，`domain_tags` 被抹成
      单值，4,289 条跨域标签当场丢失（R22 探针抓住：S3 因此恒真）。
      与 Phase 61 的 `symbols_rule_version` 同款纪律。
    """
    p = node.get("props") or {}
    cur = p.get("domain")
    managed = p.get("domain_rule_version") is not None
    # ① 人工已有细分且**从未被本规则管过** ⇒ 不覆盖
    if (not managed) and cur and cur != "phys":
        return cur, [cur], None, "curated", None

    g_tag = groups_from_tags(p.get("pb_domains"))
    g_dim = groups_from_dims(p.get("dim_exponents"))
    groups = g_tag | g_dim

    if groups:
        dom = pick_primary(groups)
        tags = sorted((GROUP2DOMAIN[g] for g in groups), key=lambda d: _ORDER.get(d, 999))
        src = "pb_declared" if g_tag else "rule_dims"
        return dom, tags, sorted(groups), src, None

    # ② 只有名称可用（种子节点）
    text = " ".join(str(p.get(k) or "") for k in ("label", "name", "latex", "equation")).lower()
    for kw, dom in NAME_RULES:
        if kw in text:
            return dom, [dom], [], "rule_name", None
    return None, [], [], "none", "no_evidence"


def derive_all(nodes, reverse=False):
    seq = list(reversed(nodes)) if reverse else nodes
    return {n["id"]: derive(n) for n in seq
            if "Formula" in (n.get("labels") or [])}


def subject_of(domain):
    """复刻 06_PoC/graph_export.py 的 subject_of 前缀规则（**不 import**：对账要独立）"""
    d = (domain or "").strip().lower()
    if d.startswith("chem") or d.startswith("ek") or d.startswith("bio"):
        return "化学"
    if d.startswith("phys") or d.startswith("phy") or d.startswith("pb"):
        return "物理"
    if d.startswith("math") or d.startswith("mx") or d.startswith("mg"):
        return "数学"
    return "跨学科" if d else "?"


def main():
    ap = argparse.ArgumentParser(description="Phase 62 G2 学科分类 delta")
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
    nodes = [n for n in g["nodes"] if "Formula" in (n.get("labels") or [])]
    print("=" * 92)
    print("Phase 62 delta —— G2 学科分类（`domain` / `domain_tags`，纯规则零抓取）")
    print("  Formula 节点 %d   规则 %s" % (len(nodes), RULE_VERSION))
    print("=" * 92)

    res = derive_all(nodes)
    res_rev = derive_all(nodes, reverse=True)

    # ---------------- 组装 delta ----------------
    commit = head_full()
    now = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    d_nodes = []
    n_total = len(nodes)
    n_ok = 0                 # domain 判出来的
    n_changed = 0            # domain 值实际上被改动的（"phys" → 二级）
    n_curated = 0            # 人工值保留（derive 判为 curated 的）
    n_human = 0              # 独立判定的「人工值」数（无 rule_version 且已有细分）
    n_managed = 0            # 已被本规则（任何版本）管过的
    n_idem_bad = 0           # 同版本重算值不同 ⇒ 非确定性
    idem_drift = []
    dist = collections.Counter()
    tag_dist = collections.Counter()
    src_dist = collections.Counter()
    n_multi = 0              # 跨域（标签 > 1）
    curated_changed = []     # 人工值被改（须 0）
    dual_bad = []            # 源声明路径 != 参与量路径（须 0）
    outside = []             # 产出值不在受控词表（须 0）
    subj_bad = []            # 学科归属被改坏（须 0）
    unknown_keys = collections.Counter()
    unclassified = []
    idem_conflict = []

    for n in nodes:
        p = n.get("props") or {}
        dom, tags, groups, src, reason = res[n["id"]]
        res_rev_dom = res_rev[n["id"]][0]
        if res_rev_dom != dom:
            idem_conflict.append(n["id"])

        # ★ 人工身份**独立判定**（不看 derive 返回的 src —— 那正是被 R22 短路的东西）
        old = p.get("domain")
        is_human = (p.get("domain_rule_version") is None) and old and old != "phys"
        if is_human:
            n_human += 1
        if p.get("domain_rule_version") is not None:
            n_managed += 1      # ★ 只数「真的被规则管过」的，不是「非人工」
        # 同版本重算却算出不同的值 ⇒ 非确定性，必须停下
        if (p.get("domain_rule_version") == RULE_VERSION and old != dom):
            n_idem_bad += 1
            idem_drift.append((n["id"], old, dom))

        g_tag = groups_from_tags(p.get("pb_domains"))
        g_dim = groups_from_dims(p.get("dim_exponents"))
        if g_tag and g_dim and g_tag != g_dim:
            dual_bad.append((n["id"], sorted(g_tag), sorted(g_dim)))
        for k in (p.get("dim_exponents") or {}):
            if k not in Q_GROUP:
                unknown_keys[k] += 1

        if dom is None:
            unclassified.append(n["id"])
            continue
        n_ok += 1
        if dom not in CONTROLLED:
            outside.append((n["id"], dom))
        dist[dom] += 1
        src_dist[src] += 1
        if len(tags) > 1:
            n_multi += 1
        tag_dist[tuple(tags)] += 1

        before_subj = subject_of(old) if old else "?"
        after_subj = subject_of(dom)
        if old != dom:
            n_changed += 1
            # ★ 学科大类绝不能被改坏（"phys"→"physics.em" 都应是「物理」）
            if old and old != "phys" and before_subj != after_subj:
                subj_bad.append((n["id"], old, dom, before_subj, after_subj))
        # ★ 人工值被覆盖 ⇒ 立即记（不依赖 src，见上面 is_human 的说明）
        if is_human and old != dom:
            curated_changed.append((n["id"], old, dom))
        if src == "curated":
            n_curated += 1

        newp = {"domain": dom, "domain_tags": tags, "domain_source": src}
        # ★ 人工侧**不写** rule_version：留着「无版本」这个标记，下次才能继续认出它是人工值
        if not is_human:
            newp["domain_rule_version"] = RULE_VERSION
        if groups is not None:
            newp["domain_groups"] = groups
        if reason:
            newp["domain_reason"] = reason
        d_nodes.append({"id": n["id"], "props": newp})

    # ---------------- 自检 ----------------
    print("\n【分类自检】")
    cover = 100.0 * n_ok / max(1, n_total)
    rows = [
        ("S1 覆盖率", n_ok == n_total and not outside and not unclassified,
         "`domain` 判出 %d / %d = %.2f%%（须 100%%）；落在受控词表外的 %d 条（须 0）；"
         "判不出来 %d 条（须 0 —— 有就会触发本条失败，fail-loud）"
         % (n_ok, n_total, cover, len(outside), len(unclassified))),
        ("S2 双路径一致", not dual_bad,
         "源声明(pb_domains) 与 参与量(dim_exponents) 导出的领域组不等：%d 条（须 0 —— "
         "R3  reconnaissance 已验证两者应集合相等，不等说明映射表或上游变了）" % len(dual_bad)),
        ("S3 人工值未覆盖", not curated_changed and n_human > 0,
         "人工已有细分 %d 条（**独立判定**：无 rule_version 且已有细分），被改动 %d 条（须 0）；"
         "★ 判据不依赖被测对象自报身份，否则 R22 式的短路会让它恒真" % (n_human, len(curated_changed))),
        ("S4 学科未被改坏", not subj_bad,
         "改后 subject_of 与改前不一致的：%d 条（须 0 —— "
         "03_知识层/queries.py 用 subject_of 做学科过滤，改坏就污染前端学科切换）" % len(subj_bad)),
        ("S5 次序无关", not idem_conflict,
         "节点逆序重算后 domain 不一致：%d 条（须 0）" % len(idem_conflict)),
        ("S6 非真空", n_multi > 0 and n_human > 0 and src_dist.get("pb_declared", 0) > 0,
         "跨域多标签 %d 条（>0 才证明「不强行消歧」这条真的在走）；人工侧 %d 条（>0）；"
         "源声明侧 %d 条（>0）。★ 都用**绝对量**：用增量会在二次运行时假警报"
         % (n_multi, n_human, src_dist.get("pb_declared", 0))),
        ("S7 映射表全覆盖", not unknown_keys,
         "映射表未覆盖的量纲键：%d 种（须 0）%s"
         % (len(unknown_keys), dict(unknown_keys.most_common(5)) if unknown_keys else "")),
        ("S8 幂等（重跑 0 改动）", not idem_drift and (n_managed == 0 or n_changed == 0),
         "同规则版本重算值不同的 %d 条（须 0）；已被规则管过 %d 条、本次改动 %d 条"
         "（★ 管过之后再跑必须 0 改动 —— 第一版在这里把 4,289 条跨域标签抹成了单值）"
         % (len(idem_drift), n_managed, n_changed)),
    ]
    bad = 0
    for name, ok, detail in rows:
        print("  [%s] %-14s %s" % ("PASS" if ok else "FAIL", name, detail))
        bad += 0 if ok else 1

    print("\n  主标签分布：")
    for k, v in dist.most_common():
        print("     %-22s %5d" % (k, v))
    print("  判定来源：%s" % dict(src_dist.most_common()))
    print("  标签组合（前 6）：")
    for k, v in tag_dist.most_common(6):
        print("     %-46s %d" % (" + ".join(k), v))
    if idem_drift:
        print("  ⚠ 同版本重算值不同（前 5）：")
        for nid, o, d in idem_drift[:5]:
            print("     %-14s 落盘 %-20s 重算 %s" % (nid, o, d))
    if dual_bad:
        print("  ⚠ 双路径不一致（前 5）：")
        for nid, x, y in dual_bad[:5]:
            print("     %-14s 源 %s  量 %s" % (nid, x, y))
    if unclassified:
        print("  ⚠ 判不出来（前 5）：%s" % unclassified[:5])

    if a.sample:
        # F2 判据要求「人工抽检 50 个，错分 ≤ 5」。
        # 抽必须**分层 + 固定 seed**：取前 N 个会全落在同一类（第一批节点都是 mathxiv），
        # 那种抽样看不出错误率，等于没抽。
        import random
        rng = random.Random(20261020)
        by_dom = collections.defaultdict(list)
        for n in nodes:
            d = res[n["id"]][0]
            if d:
                by_dom[d].append(n)
        picked = []
        doms = sorted(by_dom, key=lambda d: -len(by_dom[d]))
        # 最大余数法：保证总数**恰好** = --sample（配额取整会多抽，抽检条数必须对得上判据）
        want = min(a.sample, n_total)
        exact = {d: want * len(by_dom[d]) / n_total for d in doms}
        quota = {d: int(exact[d]) for d in doms}
        for d in sorted(doms, key=lambda d: -(exact[d] - quota[d]))[:want - sum(quota.values())]:
            quota[d] += 1
        for d in doms:
            picked += rng.sample(by_dom[d], min(quota[d], len(by_dom[d])))
        print("\n【分层随机抽样 %d 条 · 人工核对用（seed=20261020，可复现）】" % len(picked))
        for n in picked:
            dom, tags, groups, src, _ = res[n["id"]]
            p = n.get("props") or {}
            print("  %-14s %-30s -> %-20s %s"
                  % (n["id"], str(p.get("latex") or p.get("name") or "")[:30], dom,
                     "+".join(t for t in tags if t != dom) or "-"))

    if bad:
        print("\n[FATAL] 自检未过 ⇒ **拒绝产出 delta**（宁可不落盘，也不把错的固化进快照）")
        return 1

    payload = {
        "meta": {
            "phase": 62, "tag": "domain-classify",
            "generator": "phase62_domain_delta.py",
            "theme": "G2 学科分类：pb_domains 源声明 + 参与量双路径互校验（纯规则，零抓取）",
            "base_graph": os.path.relpath(NORM, ROOT),
            "domain_rule": RULE_VERSION,
            "at_commit": commit, "stamp": now,
            "n_formula_nodes": n_total, "n_classified": n_ok,
            "coverage_pct": round(cover, 2),
            "n_changed": n_changed, "n_curated_kept": n_curated,
            "n_cross_domain": n_multi,
            "primary_distribution": dict(dist.most_common()),
            "source_distribution": dict(src_dist.most_common()),
            "controlled_vocab": CONTROLLED,
        },
        "nodes": d_nodes,
        "delete_nodes": [], "edges": [], "delete_edges": [],
    }
    print("\n【delta 汇总】nodes=%d（只改/加 props，边 0 条，删除 0）" % len(d_nodes))
    if a.dry:
        print("[DRY] 未落盘")
        return 0
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print("-> %s (%.1f MB)" % (os.path.relpath(a.out, ROOT), os.path.getsize(a.out) / 1e6))
    print("   落盘：python 06_PoC/apply_delta.py --delta %s --phase 62 --tag domainbackfill --apply"
          % os.path.relpath(a.out, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
