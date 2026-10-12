# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""物理量量纲真值表（Phase 27 · 可信性修复轮 · P0-1）
=========================================================
背景
----
`physicsbabel_ingest.py` 旧版把「方程维度自洽（整式齐次）」**误写**为
「方程内前两个参与量**量纲一致**」，并标 `verified=True` —— 这是**错误的命题**：
  方程 `m = k₀·ℓ⁻¹·t²·F` 齐次 ≠ `mass` 与 `length` 量纲相同。
该错误在主图沉淀 **1130 条** `dimensionally_consistent` 边（占总 1200 条的 94%）。

修复思路（本模块）
------------------
PhysicsBabel 的 `exponents` 是**方程级齐次系数**：对每个 plausible 方程，
    ∏ Qᵢ^expᵢ  =  无量纲
把它视为关于「各物理量量纲向量」的**线性方程组**（指数为系数），
以 SI 基本量（mass/length/time/current/temperature/amount/luminous/angle）的量纲
为已知边界条件，用**消元法**反解出每个物理量的真实量纲向量。

这是「量纲分析」的标准逆问题，**无外部依赖、可复现、物理可验证**：
实测解出的 force=MLT⁻² / energy=ML²T⁻² / voltage=ML²T⁻³I⁻¹ / planck=ML²T⁻¹ /
permittivity=M⁻¹L⁻³T⁴I² 全部与教科书一致。

产物
----
  11_真实数据/dimension_table.json
    {"<量名>": {"M":1,"L":2,"T":-2,...}}  （已解出）
  未解出量（缺基本量覆盖，如光学量缺 J 基）**不写入** —— 「宁缺勿滥」，
  下游凡查不到量纲者一律**不建 `dimensionally_consistent` 边**。

用法
----
  from dimension_table import DIM, dim_of, dim_equal
  dim_of("force")            -> {"M":1,"L":1,"T":-2}
  dim_equal("energy","torque") -> True   （都是 ML²T⁻²）
"""
import json
import os
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "dimension_table.json")

# SI 基本量向量。key 顺序固定以保证可复现。
DIM = ["M", "L", "T", "I", "Th", "N", "J", "A"]
BASE_VEC = {
    "mass": {"M": 1}, "length": {"L": 1}, "time": {"T": 1}, "current": {"I": 1},
    "temperature": {"Th": 1}, "amount": {"N": 1}, "luminous_intensity": {"J": 1},
    "angle": {"A": 1},
}


def _solve_from_parquet(parquet_path: str):
    """从 PhysicsBabel parquet 反解量纲表。返回 {name: {dim: exp}}。"""
    import pandas as pd

    df = pd.read_parquet(parquet_path)
    pl = df[df["plausible"] == True]  # noqa: E712
    eqs = []
    for _, r in pl.iterrows():
        keys = [k.strip() for k in str(r["keys"]).split(",") if k.strip()]
        if len(keys) < 2 or len(keys) > 6:
            continue
        try:
            exp = json.loads(str(r["exponents"]))
        except Exception:
            continue
        if set(exp) != set(keys):        # 指数表的键必须与参与量严格一致，否则弃用
            continue
        eqs.append((keys, {k: float(v) for k, v in exp.items()}))

    known = {k: dict(v) for k, v in BASE_VEC.items()}
    for _ in range(50):
        changed = False
        for keys, exp in eqs:
            unknown = [k for k in keys if k not in known]
            if len(unknown) != 1:        # 只处理「恰好一个未知量」的方程（可精确解出）
                continue
            u = unknown[0]
            cu = exp.get(u, 0.0)
            if abs(cu) < 1e-9:
                continue
            vec = collections.defaultdict(float)
            ok = True
            for k in keys:
                if k == u:
                    continue
                v = known.get(k)
                if v is None:
                    ok = False
                    break
                for d, p in v.items():
                    vec[d] += -exp[k] / cu * p
            if not ok:
                continue
            known[u] = {d: round(p, 6) for d, p in vec.items() if abs(p) > 1e-9}
            changed = True
        if not changed:
            break
    # 只保留「非基本量」的自解结果（基本量由 BASE_VEC 权威给定）
    return {k: v for k, v in known.items()}, len(eqs)


def build(parquet_path: str, out_path: str = OUT) -> dict:
    table, n_eq = _solve_from_parquet(parquet_path)
    payload = {
        "meta": {
            "source": "PhysicsBabel exponents 消元反解",
            "method": "linear elimination over equation-level homogeneity exponents",
            "base_dims": DIM,
            "n_equations_used": n_eq,
            "n_quantities": len(table),
            "note": "仅收录可被唯一解出的量；查不到者下游一律不建 dimensionally_consistent 边",
        },
        "dimensions": table,
    }
    json.dump(payload, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("[OK] %s: %d 个量" % (os.path.basename(out_path), len(table)))
    return table


# ---------------------------------------------------------------- 运行时接口
_TABLE = None

# 量名别名 -> 真值表规范名。用于把**其它命名空间**的物理量（种子切片 CM/TH/QM/CE 的
# `CM:pq:work`、Phase9 的 `MX:phy:kinetic_energy` 等）对齐到同一量纲。
# 只收录语义**确定**的别名；不确定者一律不映射（→ 审计判为 unknown，宁缺勿滥）。
ALIAS = {
    "ke": "energy", "kinetic_energy": "energy",
    "work": "energy", "work_function": "energy",
    "heat": "energy", "internal_energy": "energy",
    "gibbs_energy": "energy", "activation_energy": "energy",
    "enthalpy": "energy", "free_energy": "energy", "potential_energy": "energy",
    "angular_momentum": "ang_momentum",
    "impulse": "momentum",
    "speed": "velocity",
    "gravity": "acceleration",     # 重力加速度
    "torque": "torque",

    # ---- Phase 31：关闭「展示名 → 规范名」的**系统性命名缺口** ----
    # 背景：图上物理量多用**展示名**（`Electric charge` / `Speed of light` / `Focal length`），
    #   真值表用 snake_case；不映射则 `dim_of` 返回 None → A1（量—量量纲）/A8（单位—量纲）
    #   复算**静默退化为「不可判定」**（铁律 #23），使本可确定性判定的边停在 `model_inferred`。
    #   实测：168 个物理量节点中 **84 个（50%）** 查不到量纲，是复算落地不上去的**唯一瓶颈**。
    # 收录原则（宁缺勿滥，铁律 #28）：**只收语义确定无疑的「同义命名 / 规范译名」**，
    #   不含任何需要物理推理才能确定的映射；每个映射都在 `06_PoC/frozen_counterexamples.json`
    #   有正对照（dimension_table.ALIAS[...] == 该量真量纲）。
    # 电磁（EM）
    "electric_charge": "charge",
    "electric_current": "current",
    "electric_field": "efield",
    "magnetic_field": "bfield",
    "vacuum_permittivity": "permittivity",
    "vacuum_permeability": "permeability",
    "electromotive_force": "voltage",          # 电动势与电压同量纲
    "magnetic_flux": "mag_flux",
    # 量子 / 相对论（QM / RT）
    "planck_constant": "planck",
    "reduced_planck_constant": "planck",       # ħ = h/2π，与 h 同量纲
    "wavelength": "length",
    "proper_time": "time",
    "speed_of_light": "light_speed",
    # 统计（SM）
    "boltzmann_constant": "boltzmann",
    "statistical_entropy": "entropy",
    # 力学 / 热力学（CM / TH / MG / OP）
    "gravitational_constant": "grav_const",
    "amount_of_substance": "amount",
    "radius": "length",
    "focal_length": "length",
    "object_distance": "length",
    "image_distance": "length",
    # 化学热力学（CE）
    "gibbs_free_energy": "energy",
    # 光度（OP）—— J 基已入库；`luminance`/`illuminance` 需派生式，本轮**不建**（宁缺勿滥）
    "lum_intensity": "luminous_intensity",
}


# ------------------------------------------------------------------ 量纲代数派生
# Phase 29 追加：**由已解出的量做线性组合**得到的新量（只允许乘/除/幂，不做任何猜测）。
#
# 为什么需要：`dimension_table.json` 是 PhysicsBabel 方程**消元反解**的产物，
# 只能覆盖「在方程里独立出现」的量。像 `molar_entropy = entropy / amount`
# 这种**以「每摩尔」形式出现**的复合量不在其中 —— 而 NIST WebBook 的热化学桥
# 恰恰锚定在 `std_entropy`（单位 `J/mol*K`）上。若不补，
# 桥的量纲复算会**静默退化为「不可判定」**（`dim_of` 返回 None），
# 从而把有证据的桥降级为 `source_asserted`（铁律 #23 的静默降级）。
#
# 每条派生都写明**来源式**，可逐条复核；解析失败/依赖缺失一律**跳过**（宁缺勿滥）。
DERIVED = {
    # 摩尔熵 = 熵 / 物质的量                → L²MTh⁻¹T⁻²N⁻¹
    "molar_entropy": (("entropy", 1.0), ("amount", -1.0)),
    # 摩尔热容 = 热容 / 物质的量            → L²MT⁻²Th⁻¹N⁻¹
    "molar_heat_capacity": (("heat_capacity", 1.0), ("amount", -1.0)),
}


def _apply_derived(tbl: dict) -> None:
    """把 DERIVED 里的派生量并入（in-place）；能算则算，算不出就**不写**。"""
    for name, terms in DERIVED.items():
        if name in tbl:
            continue
        vec = collections.defaultdict(float)
        ok = True
        for q, k in terms:
            base = tbl.get(q)
            if base is None:
                ok = False
                break
            for d, p in base.items():
                vec[d] += float(k) * float(p)
        if not ok:
            continue
        tbl[name] = {d: round(p, 6) for d, p in vec.items() if abs(p) > 1e-9}


def load(path: str = OUT) -> dict:
    global _TABLE
    if _TABLE is None:
        with open(path, encoding="utf-8") as f:
            _TABLE = json.load(f)["dimensions"]
        _apply_derived(_TABLE)
    return _TABLE


def _display_key(name: str) -> str:
    """把「展示名」（如 `Kinetic energy` / `Gibbs free energy`）规整为 snake_case 键。

    图上的量名多为展示名（首字母大写、含空格），而真值表 / ALIAS 用 snake_case。
    不规整会**静默查不到**（`dim_of` 返回 None），使本可复算的边退化为不可判定。
    """
    s = str(name).strip().lower()
    out = []
    for ch in s:
        out.append(ch if (ch.isalnum() or ch == "_") else "_")
    s = "".join(out)
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")


def canon(name: str) -> str:
    """把量名归一为真值表规范名（先查 ALIAS，再试 snake_case 展示名）。"""
    s = str(name)
    if s in ALIAS:
        return ALIAS[s]
    k = _display_key(s)
    if k in ALIAS:
        return ALIAS[k]
    tbl = load()
    if k in tbl:
        return k
    return s


def dim_of(name: str):
    """返回量纲向量 dict；未知量返回 None。"""
    return load().get(canon(name))


def dim_equal(a: str, b: str) -> bool:
    """两量量纲是否**严格相等**；任一未知返回 False（宁缺勿滥）。"""
    da, db = dim_of(a), dim_of(b)
    if not da or not db:
        return False
    return _norm(da) == _norm(db)


def _norm(d: dict) -> tuple:
    return tuple((k, d.get(k, 0)) for k in DIM)


def is_homogeneous(keys, exponents):
    """校验某方程是否量纲齐次：Σ exp_i·dim(Q_i) == 0。

    三态（铁律 #19 spirit：不可判定 ≠ 假）：
        True  —— 全部量已知且线性组合为 0（齐次）
        False —— 全部量已知但线性组合非 0（**真·不齐次**，是硬缺陷）
        None  —— 存在未知量纲的量 → **无法判定**（不得当作 False 使用）
    """
    acc = collections.defaultdict(float)
    for k in keys:
        d = dim_of(k)
        if d is None:
            return None
        e = float(exponents.get(k, 0))
        for dim, p in d.items():
            acc[dim] += e * p
    return all(abs(v) < 1e-9 for v in acc.values())


def same_dimension_pairs(names):
    """返回给定量名集合中「量纲严格相等」的无序对列表（宁缺勿滥：未知量不参与）。"""
    out = []
    ns = sorted(set(names))
    for i in range(len(ns)):
        for j in range(i + 1, len(ns)):
            if dim_equal(ns[i], ns[j]):
                out.append((ns[i], ns[j]))
    return out


# ---------------------------------------------------------------- 量纲串解析（R09）
# `dimension_vector` 字段在图上以**字符串**形式存储（历史格式）：`M*L^2*T^-2`；
# 无量纲记作 `1`。本函数把它解析回向量，用于与独立 SI 基表逐条对照。
# 注意：符号集与 DIM 一致（M L T I Th N J A），**不退化为「单位串」解析**
#   —— `12_评测/eval_runner.py` 里另有一套「单位串」解析器（`m` 在那里是米），
#   两套语法不得混用（Phase 38 US-07 的教训）。
_DIM_TOKEN = None


def parse_dim_string(s):
    """把量纲串（`M*L^2*T^-2` / `1`）解析为向量 dict；无法解析返回 None。"""
    global _DIM_TOKEN
    if _DIM_TOKEN is None:
        _DIM_TOKEN = __import__("re").compile(r"^([A-Za-z]+)(?:\^(-?\d+))?$")
    if s is None:
        return None
    t = str(s).strip()
    if t in ("", "1", "0"):
        return {}
    acc = collections.defaultdict(float)
    for tok in t.replace("·", "*").replace(" ", "*").split("*"):
        tok = tok.strip()
        if not tok:
            continue
        m = _DIM_TOKEN.match(tok)
        if not m:
            return None
        sym = m.group(1)
        if sym not in DIM:
            return None
        acc[sym] += float(m.group(2) or 1)
    return {k: acc[k] for k in DIM if abs(acc.get(k, 0.0)) > 1e-9}


# 声明串里的**符号归一**（确定性，非物理猜测；R09）：
#   实测种子串只把两个**单位符号**混进了量纲串 —— K（开尔文）→ Th、mol（摩尔）→ N；
#   `dimensionless` 是无量纲的书写形式。
SYMBOL_FIX = {"K": "Th", "mol": "N"}
_DIMENSIONLESS_TOKENS = {"dimensionless", "1", "", "0"}


def vec_to_str(v):
    """向量 dict → 规范量纲串（`M*L^2*T^-2`）；空 → `1`。"""
    parts = []
    for d in DIM:
        e = v.get(d, 0)
        if abs(e) > 1e-9:
            ei = int(round(e))
            parts.append(d if ei == 1 else "%s^%d" % (d, ei))
    return "*".join(parts) if parts else "1"


def normalize_dim_string(s):
    """把（可能混用单位符号的）量纲声明串规范化为规范形；不可解析则原样返回。

    例：`K` → `Th`；`M*L^2*T^-2*K^-1` → `M*L^2*T^-2*Th^-1`；`N*L^-3` → `L^-3*N`；
        `dimensionless` → `1`。
    """
    t = str(s).strip()
    if t.lower() in _DIMENSIONLESS_TOKENS:
        return "1"
    fixed = []
    for tok in t.replace("·", "*").replace(" ", "*").split("*"):
        tok = tok.strip()
        if not tok:
            continue
        base, _, e = tok.partition("^")
        base = SYMBOL_FIX.get(base, base)
        fixed.append(base + ("^" + e if e else ""))
    v = parse_dim_string("*".join(fixed))
    return vec_to_str(v) if v is not None else t


if __name__ == "__main__":
    import sys as _sys
    if "--compare" in _sys.argv:
        # 反解表 vs 独立 SI 基表对照（R09）：作为「反解表可信度」的外部证据
        _sys.path.insert(0, HERE)
        import si_base_table as _si
        _cmp = _si.compare(load())
        print("反解表条目 %d / 可对照 %d" % (_cmp["n_reverse"], _cmp["n_checked"]))
        print("  agree                 : %d" % len(_cmp["agree"]))
        print("  convention_difference : %d  %s" % (
            len(_cmp["convention_difference"]),
            [x["name"] for x in _cmp["convention_difference"]]))
        print("  conflict              : %d  %s" % (
            len(_cmp["conflict"]), [x["name"] for x in _cmp["conflict"]]))
        print("  only_in_reverse       : %d  %s" % (
            len(_cmp["only_in_reverse"]), [x["name"] for x in _cmp["only_in_reverse"]]))
        print("  only_in_si            : %d  %s" % (
            len(_cmp["only_in_si"]), [x["name"] for x in _cmp["only_in_si"]]))
        print("  agree_rate(checked)   : %s" % _cmp["agree_rate_of_checked"])
        if "--json" in _sys.argv:
            import json as _json
            print(_json.dumps(_cmp, ensure_ascii=False, indent=2))
        _sys.exit(1 if _cmp["conflict"] else 0)
    build(os.path.join(HERE, "physicsbabel_circuits.parquet"))
