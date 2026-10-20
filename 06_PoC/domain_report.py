#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
"""domain_report.py —— G2 学科分类的**只读**报告 + 对账（Phase 62）

规划：`00_项目管理/下一阶段规划_数据扩展与前端优化_20261019.md` §3.1 F2
    「`domain` 二级学科规则分类：5,090 个全部有细分；人工抽检 50 个，错分 ≤ 5」

它做什么
--------
1. 报**覆盖率**、主标签分布、跨域（多标签）规模、判定来源分布；
2. 跑对账断言 **Y1–Y6**，把「分得对不对」从一句承诺变成可回归的事实；
3. `--selfcheck` 用**合成节点**验证每条判据**能被踩红**（D72 家族：
   恒真判据比没有判据更坏 —— 它让人以为有保护，其实没有）。

它不做什么
----------
* **不改树**（产物只去 `--json`，默认 `.runlog/`）；
* **不联网**、不重新解析 parquet —— 只读**已落盘**的图；
* **不复用** `11_真实数据/phase62_domain_delta.py` 的任何代码。
  映射表在本文件里**重写一遍**是**故意的**：报告若复用生成侧的函数，
  就变成「用同一段代码验自己」（铁律 #87 的同族），两边口径漂移就看不出来了。

判据（沿用 Y 系列，与 G/R/V/F/T/A/S 族不撞号）
----------------------------------------------
  Y1 覆盖率：全部 Formula 的 `domain` 非空且落在受控词表内
  Y2 主标签归属：`domain` ∈ `domain_tags`（主标签必须是全标签之一）
  Y3 与参与量一致：`domain_tags` 换算回领域组，须等于由 `dim_exponents` 重算的领域组
  Y4 归属清晰：人工侧（`curated`）**不许**带派生领域组 `domain_groups`；
     规则侧必须同时带 `domain_source` 与 `domain_rule_version` —— 不允许「写了一半」
  Y5 非真空：必须同时存在跨域多标签与人工保留两类，
     否则 Y2/Y3 的通过可能只是那些分支根本没走到
  Y6 反循环 + 学科未改坏：① 判级模型源码**不得**读取 `props.domain`
     （Phase 61 的教训：派生字段一旦被判级读取，就会「用边证明边」）；
     ② `subject_of()` 学科大类须仍落在 物理/化学/数学，
     不得出现「跨学科」（03_知识层/queries.py 用它做学科过滤）

退出码：`0` 全过 ／ `1` 有 FAIL ／ `2` 缺权威图 ／ `3` 仪器自证失败

用法
----
    bash sttp.sh domains [--sample 20] [--json .runlog/domain_report.json]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
VM = os.path.join(ROOT, "11_真实数据", "verification_model.py")

RULE_VERSION = "d1"

# ---- 映射表：与生成侧**同源但独立抄写**（见文件头「不复用」说明）----
_Q = {}
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
        _Q[_k] = _grp

GROUP2DOMAIN = {"mech": "physics.mechanics", "em": "physics.em",
                "thermo": "physics.thermo", "chem": "chem.general",
                "photo": "physics.optics"}
DOMAIN2GROUP = {v: k for k, v in GROUP2DOMAIN.items()}

CONTROLLED = set(GROUP2DOMAIN.values()) | {
    "physics.relativity", "physics.qm", "physics.statmech", "physics.gravitation",
    "chem.acidbase", "chem.electrochem", "chem.energy", "chem.equilibrium",
    "chem.kinetics", "chem.periodic", "chem.solubility", "chem.stoich", "chem.thermo",
    "math.DG", "math.algebra", "math.analysis", "math.calculus", "math.geometry",
    "math.numbertheory",
}


class Rep:
    def __init__(self):
        self.rows = []

    def add(self, ok, name, detail=""):
        self.rows.append((bool(ok), name, detail))
        return bool(ok)

    def dump(self, title="domain_report"):
        bad = 0
        for ok, name, detail in self.rows:
            print("  [%s] %-16s %s" % ("PASS" if ok else "FAIL", name, detail))
            bad += 0 if ok else 1
        return bad


def subject_of(domain):
    """复刻 06_PoC/graph_export.py 的前缀规则（独立抄写，不 import）"""
    d = (domain or "").strip().lower()
    if d.startswith("chem") or d.startswith("ek") or d.startswith("bio"):
        return "化学"
    if d.startswith("phys") or d.startswith("phy") or d.startswith("pb"):
        return "物理"
    if d.startswith("math") or d.startswith("mx") or d.startswith("mg"):
        return "数学"
    return "跨学科" if d else "?"


def groups_from_dims(dim):
    return {_Q[k] for k in (dim or {}) if _Q.get(k) and _Q[k] != "const"}


def audit(formula_nodes, vm_text=""):
    """逐条核 Y1–Y4/Y6，返回 (stats, problems)"""
    st = collections.Counter()
    problems = collections.defaultdict(list)
    dist = collections.Counter()
    src_dist = collections.Counter()
    n_multi = 0
    for n in formula_nodes:
        p = n.get("props") or {}
        st["total"] += 1
        dom = p.get("domain")
        tags = p.get("domain_tags") or []
        src = p.get("domain_source")
        rv = p.get("domain_rule_version")
        groups = p.get("domain_groups")

        # Y1
        if not dom:
            problems["Y1"].append(n["id"])
        elif dom not in CONTROLLED:
            problems["Y1"].append(n["id"])
        else:
            st["ok"] += 1
            dist[dom] += 1

        # Y2 主标签 ∈ 全标签
        if dom and tags and dom not in tags:
            problems["Y2"].append(n["id"])
        if len(tags) > 1:
            n_multi += 1

        # Y3 全标签 ⇔ 参与量领域组
        dim = p.get("dim_exponents")
        if tags and dim:
            g_dim = groups_from_dims(dim)
            g_tag = {DOMAIN2GROUP[t] for t in tags if t in DOMAIN2GROUP}
            if g_tag != g_dim:
                problems["Y3"].append(n["id"])

        # Y4 归属清晰。★ 人工侧的身份标记是「**无** domain_rule_version」——
        #   与生成侧同口径：人工值**不写**版本，留着这个空位下次才能继续认出它。
        if rv is None:
            st["curated"] += 1
            if src != "curated":
                problems["Y4"].append(n["id"])     # 无版本却自称规则侧
            if groups is not None:
                problems["Y4"].append(n["id"])     # 人工侧混进了派生值
        else:
            if rv != RULE_VERSION:
                problems["Y4"].append(n["id"])     # 版本不对
            if src not in ("pb_declared", "rule_dims", "rule_name"):
                problems["Y4"].append(n["id"])     # 写了一半
            if not groups and src != "rule_name":
                problems["Y4"].append(n["id"])
        src_dist[src or "<空>"] += 1

        # Y6② 学科大类不得被改坏
        if dom and subject_of(dom) not in ("物理", "化学", "数学"):
            problems["Y6"].append(n["id"])

    # Y6① 反循环：判级模型不得读取 props.domain
    if vm_text and ("props" in vm_text) and ("domain" in vm_text):
        for line in vm_text.splitlines():
            s = line.strip()
            if s.startswith("#") or "domain" not in s:
                continue
            if "props" in s and "get" in s:
                problems["Y6"].append("verification_model.py 读取了 props.domain ⇒ 循环证明风险")

    return ({"total": st["total"], "ok": st["ok"], "curated": st["curated"],
             "multi": n_multi, "dist": dist, "src": src_dist}, problems)


def _syn(nid, domain=None, tags=None, source=None, rule=RULE_VERSION,
         groups=None, dims=None):
    p = {"id": nid}
    if domain is not None:
        p["domain"] = domain
    if tags is not None:
        p["domain_tags"] = tags
    if source is not None:
        p["domain_source"] = source
    if rule is not None:
        p["domain_rule_version"] = rule
    if groups is not None:
        p["domain_groups"] = groups
    if dims is not None:
        p["dim_exponents"] = dims
    return {"id": nid, "labels": ["Formula", "Entity"], "props": p}


def selfcheck():
    """★ 断言名**以判据 ID 开头**（`Y3:bad …`）：red_probe.starts_with_id() 按前缀匹配。"""
    print("【仪器自证】合成坏节点，逐条确认判据**能被踩红**")
    rep = Rep()
    cases = [
        ("Y1:bad 词表外的值", [_syn("Y1:bad", "xyzzy.unknown", ["xyzzy.unknown"],
                                    "pb_declared", groups=["mech"], dims={"mass": "1"})]),
        ("Y2:bad 主标签不在全标签里", [_syn("Y2:bad", "physics.em", ["physics.mechanics"],
                                            "pb_declared", groups=["mech"], dims={"mass": "1"})]),
        ("Y3:bad 全标签与参与量不符", [_syn("Y3:bad", "physics.em", ["physics.em"],
                                            "pb_declared", groups=["em"], dims={"mass": "1"})]),
        ("Y4:bad 人工侧混进派生领域组", [_syn("Y4:bad", "math.DG", ["math.DG"],
                                              "curated", rule=None, groups=["mech"])]),
        ("Y6:bad 判级模型读了 props.domain",
         [_syn("Y6:bad", "physics.em", ["physics.em"], "pb_declared",
               groups=["em"], dims={"charge": "1"})]),
    ]
    bad = 0
    for name, nodes in cases:
        vm = ('x = (n.get("props") or {}).get("domain")\n'
              if name.startswith("Y6") else "")
        _, probs = audit(nodes, vm_text=vm)
        key = name.split(":")[0]
        got = key in probs
        rep.add(got, name, "踩红成功" if got else "❌ 判据恒真，等于没有判据")
        bad += 0 if got else 1
    # 阴性对照：好节点**不得**被判红
    good = [_syn("OK:good", "physics.em", ["physics.mechanics", "physics.em"],
                 "pb_declared", groups=["mech", "em"], dims={"mass": "1", "charge": "1"})]
    _, probs = audit(good)
    clean = not probs
    rep.add(clean, "阴性对照", "好节点不触发任何判据" if clean else "❌ 误报：%s" % dict(probs))
    bad += 0 if clean else 1
    return 1 if rep.dump() else 0


def main():
    ap = argparse.ArgumentParser(description="Phase 62 G2 学科分类只读报告")
    ap.add_argument("--json", default=".runlog/domain_report.json")
    ap.add_argument("--sample", type=int, default=10)
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()

    if a.selfcheck:
        return selfcheck()

    if not os.path.isfile(NORM):
        print("[不可用] 缺少权威图 06_PoC/etl/normalized.json（被 .gitignore 刻意排除）")
        return 2
    with open(NORM, encoding="utf-8") as f:
        g = json.load(f)
    formulas = [n for n in g["nodes"] if "Formula" in (n.get("labels") or [])]

    vm_text = ""
    if os.path.isfile(VM):
        with open(VM, encoding="utf-8") as f:
            vm_text = f.read()

    st, probs = audit(formulas, vm_text=vm_text)
    total = st["total"]
    cover = 100.0 * st["ok"] / max(1, total)

    print("=" * 84)
    print("Phase 62 —— G2 学科分类 · 只读报告")
    print("=" * 84)
    print("  Formula 节点 %d；`domain` 在受控词表内 %d = %.2f%%"
          % (total, st["ok"], cover))
    print("  判定来源：%s" % dict(st["src"].most_common()))
    print("  跨域（多标签）%d 条 = %.1f%% —— 这是量纲合成式的本性，不是缺陷"
          % (st["multi"], 100.0 * st["multi"] / max(1, total)))
    print("\n  主标签分布（前 12）：")
    for k, v in st["dist"].most_common(12):
        print("     %-22s %5d" % (k, v))

    print("\n【对账】")
    rep = Rep()
    rep.add(not probs.get("Y1"), "Y1 覆盖率", "%d/%d = %.2f%%；词表外或空 %d 条（须 0）"
            % (st["ok"], total, cover, len(probs.get("Y1", []))))
    rep.add(not probs.get("Y2"), "Y2 主标签归属",
            "`domain` 不在 `domain_tags` 里的 %d 条（须 0）" % len(probs.get("Y2", [])))
    rep.add(not probs.get("Y3"), "Y3 与参与量一致",
            "全标签换算的领域组 != dim_exponents 重算的：%d 条（须 0）" % len(probs.get("Y3", [])))
    rep.add(not probs.get("Y4"), "Y4 归属清晰",
            "人工侧混派生值 / 规则侧写一半 / 无来源：%d 条（须 0）" % len(probs.get("Y4", [])))
    ok5 = st["multi"] > 0 and st["curated"] > 0
    rep.add(ok5, "Y5 非真空",
            "跨域多标签 %d（>0）且人工保留 %d（>0）—— 两者都 >0 才证明 Y2/Y3 的分支真走到了"
            % (st["multi"], st["curated"]))
    rep.add(not probs.get("Y6"), "Y6 反循环+学科未改坏",
            "判级模型读 props.domain 或学科落到「跨学科」：%d 条（须 0）" % len(probs.get("Y6", [])))
    bad = rep.dump()

    for k in sorted(probs):
        if probs[k]:
            print("     %s 前 5：%s" % (k, probs[k][:5]))

    if a.sample:
        print("\n【抽样 %d 条】" % a.sample)
        for n in formulas[:a.sample]:
            p = n.get("props") or {}
            print("  %-16s %-28s -> %-20s %s"
                  % (n["id"], str(p.get("latex") or p.get("name") or "")[:28],
                     p.get("domain"), "+".join(t for t in (p.get("domain_tags") or [])
                                               if t != p.get("domain")) or "-"))

    if a.json:
        os.makedirs(os.path.dirname(a.json) or ".", exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"coverage_pct": round(cover, 2), "n_total": total,
                       "n_ok": st["ok"], "n_multi": st["multi"],
                       "n_curated": st["curated"],
                       "primary_distribution": dict(st["dist"].most_common()),
                       "source_distribution": dict(st["src"].most_common()),
                       "problems": {k: v[:20] for k, v in probs.items()}},
                      f, ensure_ascii=False, indent=1)
        print("\n-> %s" % a.json)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
