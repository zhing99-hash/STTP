#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_recon_phase33_rulepreview.py —— 第 23 轮只读侦察 6：把 T4/T6 两条待实现规则**在真实图上预演**。

目的：
  · T4 —— 用 ChEBI label/formula 跨源校验 Rhea 方程侧别，确认 correct/both/wrong 分布，
           并区分 label 命中 / 仅 formula 命中 / 同侧重复（歧义）。
  · T6 —— 对 constant_derivation(18) 做定义式数值复算（用图上常量值），对 constant_unit(6)
           做「常量量纲 vs 单位量纲」可判定性测试。
**只读，不改任何数据。**

★ 关键（铁律 #14）：T4 的判据只读**方程串**与**参与物 label/formula**，绝不读已存判级；
   T6 的判据只读**常量节点 value** 与**定义式/单位串**，绝不读已存判级。
"""
from __future__ import annotations
import collections, json, math, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import verification_model as vm   # noqa: E402
import dimension_table as dm      # noqa: E402

NORM = os.path.join(HERE, "etl", "normalized.json")


def split_eq(eq):
    if " = " not in eq:
        return None, None
    lhs, rhs = eq.split(" = ", 1)
    f = lambda s: [x.strip() for x in s.split(" + ") if x.strip()]
    return f(lhs), f(rhs)


def canon(x):
    s = str(x).lower()
    s = re.sub(r"\([^)]*\)", "", s)      # 去 (in)/(out)/(+)/(n)
    return s.replace(" ", "").replace("-", "").replace("+", "")


# ---- T6 定义式（key = 边 props.definition；value = (输入节点id[], 求值函数)） ----
CODATA_DEFS = {
    "R = N_A·k_B": (["CO:pq:avogadro", "SM:pq:boltz_const"],
                    lambda v: v[0] * v[1]),
    "F = N_A·e": (["CO:pq:avogadro", "CO:pq:elementary_charge"],
                  lambda v: v[0] * v[1]),
    "m_u = M_u/N_A": (["CO:pq:avogadro"],
                      lambda v: 1e-3 / v[0]),                # M_u = 1 g/mol（SI 定义）
    "α = e²/(4πε₀ħc)": (["CO:pq:elementary_charge", "EM:pq:permittivity",
                         "QM:pq:reduced_planck", "RT:pq:light_speed"],
                        lambda v: v[0] ** 2 / (4 * math.pi * v[1] * v[2] * v[3])),
    "σ = 2π⁵k⁴/(15h³c²)": (["SM:pq:boltz_const", "QM:pq:planck_const", "RT:pq:light_speed"],
                           lambda v: 2 * math.pi ** 5 * v[0] ** 4 / (15 * v[1] ** 3 * v[2] ** 2)),
    "Z_0 = μ₀c": (["EM:pq:permeability", "RT:pq:light_speed"],
                  lambda v: v[0] * v[1]),
    "R_∞ = α²m_e c/(2h)": (["CO:pq:fine_structure", "CO:pq:electron_mass",
                            "RT:pq:light_speed", "QM:pq:planck_const"],
                           lambda v: v[0] ** 2 * v[1] * v[2] / (2 * v[3])),
}


def main():
    d = json.load(open(NORM, encoding="utf-8"))
    nodes, edges = d["nodes"], d["edges"]
    N = {n["id"]: n for n in nodes}
    print("=" * 100)
    print("规则预演 —— 输入 %d 节点 / %d 边" % (len(nodes), len(edges)))
    print("=" * 100)

    # =================== T4：参与物 label/formula × 方程侧别 ===================
    t4 = [e for e in edges if e.get("type") in ("reactant_of", "product_of")]
    rxn_cache = {}
    st = collections.Counter()
    by_stored = collections.Counter()
    up_label, up_formula = [], []
    wrong, both = [], []
    for e in t4:
        rid = e["target"]
        r = rxn_cache.setdefault(rid, (N.get(rid) or {}).get("props") or {})
        eq = r.get("equation")
        lhs, rhs = split_eq(eq) if eq else (None, None)
        if lhs is None:
            st["no_equation"] += 1
            continue
        pp = (N.get(e["source"]) or {}).get("props") or {}
        lab = canon(pp.get("name") or "")
        fml = canon(pp.get("formula") or "")
        L = [canon(x) for x in lhs]
        R = [canon(x) for x in rhs]
        cs, os_ = (L, R) if e["type"] == "reactant_of" else (R, L)
        lh, lw = bool(lab) and lab in cs, bool(lab) and lab in os_
        fh, fw = bool(fml) and fml in cs, bool(fml) and fml in os_
        if lh and lw:
            st["label_both"] += 1; both.append(e["id"]); continue
        if lw and not lh:
            st["label_wrong"] += 1; wrong.append(e["id"]); continue
        if lh:                                   # label 仅正确侧
            st["up_label"] += 1
            up_label.append((e["id"], "label=%s" % pp.get("name")))
            continue
        if fh and fw:
            st["formula_both"] += 1; both.append(e["id"]); continue
        if fw:
            st["formula_wrong"] += 1; wrong.append(e["id"]); continue
        if fh:                                   # 仅 formula 命中
            st["up_formula"] += 1
            st["formula_dup_side"] += (1 if cs.count(fml) > 1 else 0)
            up_formula.append((e["id"], "formula=%s" % pp.get("formula")))
            continue
        st["nomatch"] += 1
    print("\n【T4 参与物侧别跨源校验】总 %d" % len(t4))
    for k in ("up_label", "up_formula", "formula_dup_side", "label_both", "formula_both",
              "label_wrong", "formula_wrong", "nomatch", "no_equation"):
        print("   %-18s %6d" % (k, st[k]))
    print("   → 可升档 = %d（label %d + formula %d）" % (st["up_label"] + st["up_formula"],
                                                      st["up_label"], st["up_formula"]))
    if wrong:
        print("   ⚠ 反例样本：", wrong[:5])
        for wid in wrong[:5]:
            e = next(x for x in t4 if x["id"] == wid)
            pp = (N.get(e["source"]) or {}).get("props") or {}
            r = (N.get(e["target"]) or {}).get("props") or {}
            print("      %s label=%s formula=%s eq=%s"
                  % (wid, pp.get("name"), pp.get("formula"), str(r.get("equation"))[:70]))

    # =================== T6a：constant_derivation 数值复算 ===================
    print("\n【T6a constant_derivation 定义式复算】")
    for e in edges:
        if e.get("type") != "derived_from" or (e.get("props") or {}).get("kind") != "constant_derivation":
            continue
        dfn = (e["props"] or {}).get("definition")
        spec = CODATA_DEFS.get(dfn)
        # ★ 被派生的常量是边的 **source**（edge = derived_from|被派生|输入）；
        #   初版误比 target（输入常量）的值 → 全 FAIL，属脚本 bug（非数据缺陷）。
        src_id = e["source"]
        tval = ((N.get(src_id) or {}).get("props") or {}).get("value")
        if spec is None:
            print("   %-56s 定义式未登记：%s" % (e["id"], dfn)); continue
        ins, fn = spec
        vals = [((N.get(i) or {}).get("props") or {}).get("value") for i in ins]
        if any(v is None for v in vals):
            print("   %-56s 输入缺值 %s" % (e["id"], ins)); continue
        try:
            calc = fn([float(v) for v in vals])
            rel = abs(calc - float(tval)) / max(abs(float(tval)), 1e-30)
            print("   %-56s 复算=%.10g 记录=%.10g rel=%.2e %s"
                  % (e["id"], calc, tval, rel, "OK" if rel <= 1e-6 else "FAIL"))
        except Exception as ex:
            print("   %-56s 求值异常 %s" % (e["id"], ex))

    # =================== T6b：constant_unit 量纲可判定性 ===================
    print("\n【T6b constant_unit 量纲复算】（量名/单位 → 真量纲）")
    cu = [e for e in edges if e.get("type") == "has_unit"
          and (e.get("props") or {}).get("kind") == "constant_unit"]
    n_ok = n_undec = n_bad = 0
    for e in cu:
        sp = (N.get(e["source"]) or {}).get("props") or {}
        tp = (N.get(e["target"]) or {}).get("props") or {}
        qname = sp.get("codata_quantity") or sp.get("name") or e["source"].split(":")[-1]
        usym = (e["props"] or {}).get("unit") or tp.get("symbol") or tp.get("name")
        qd = dm.dim_of(qname)
        ud = vm.unit_dim_of_symbol(usym)
        if qd is None or ud is None:
            n_undec += 1
            print("   %-52s 量=%s dim=%s | 单位=%s dim=%s → 不可判定"
                  % (e["id"], qname, qd, usym, ud))
        else:
            ok = vm._drop(qd) == vm._drop(ud)
            n_ok += ok; n_bad += (not ok)
            print("   %-52s 量=%s=%s | 单位=%s=%s → %s"
                  % (e["id"], qname, vm._drop(qd), usym, vm._drop(ud), "OK" if ok else "FAIL"))
    print("   → 可判定且一致 %d / 不一致 %d / 不可判定 %d（共 %d）" % (n_ok, n_bad, n_undec, len(cu)))

    # =================== 汇总预期 ===================
    est_up = st["up_label"] + st["up_formula"] + n_ok
    print("\n【本轮预期升档】T4 %d + T6a %d + T6b %d = %d"
          % (st["up_label"] + st["up_formula"],
             sum(1 for e in edges if e.get("type") == "derived_from"
                 and (e.get("props") or {}).get("kind") == "constant_derivation"),
             n_ok, est_up))


if __name__ == "__main__":
    main()
