# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""独立 SI/ISO 80000 权威量纲基表（Phase 40 · R09）。

为什么需要（GPT6 复评 R09）
---------------------------
`dimension_table.json` 是 **PhysicsBabel 方程消元反解**的产物：它的量纲向量是从
**同一批方程**里解出来的。用这张表再去校验同一批方程，包含**同源自洽**成分
（铁律 #40「同源口径才算数」/#51「不同数据库≠独立证据」的同型问题）。

本模块提供一份**独立编纂**的权威基表：逐条按 SI Brochure（BIPM, 9th ed., 2019）
与 ISO 80000 各分部给出量纲，**不反解、不外推、不猜测**。它的用途不是替换反解表，
而是作为**外部对照**：

    agree                 两表量纲一致（反解表可信度得到独立支持）
    convention_difference 仅差在「角度」维度约定（见下），不是错误
    conflict              非约定性的量纲分歧 —— 必须逐条裁断
    only_in_reverse       SI 基表未收录（覆盖缺口，如实披露）

角度维度约定（本表最重要的「约定」披露）
----------------------------------------
ISO 80000-3 与 SI 均规定**平面角为无量纲导出量**（rad = 1）。STTP 的 `DIM` 向量里
却有第 8 个符号 `A`（angle）。实测反解表中**只有 `angle` 一个量带 `A`**，而一切由
角度派生的量（`omega`、`ang_momentum`）都按「角度无量纲」给出 —— 即：
`A` 基是**孤例 / 悬空约定**，不构成一致自洽的基。

本表把这一差异**显式记录**为 `convention` 字段（`ISO80000-dimensionless`），
并在 `compare()` 中把「仅差 A」归为 `convention_difference`，**不判为错误**
（铁律 #30「不可判定≠可放过」的对偶：约定差异 ≠ 事实错误）。

schema: sttp-si-base-table/v1
"""
import collections
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "si_base_table.json")

SCHEMA = "sttp-si-base-table/v1"

# ---------------------------------------------------------------------------- #
# 权威基表（手工编纂；每条记 name / dim / si_unit / source / convention / scope）
#
# dim 用 STTP 的 8 符号向量 {M,L,T,I,Th,N,J,A}；缺省即 0。
# source 为**可核对**的权威出处（非本项目产物）。
# convention：量纲约定。`ISO80000-dimensionless` 专指角度按无量纲处理。
# scope：适用范围/口径提示（何时不可套用）。
# ---------------------------------------------------------------------------- #
SRC_SI = "SI Brochure 9th ed. (BIPM, 2019) Table 1/2"
SRC_3 = "ISO 80000-3:2019 (Space and time)"
SRC_4 = "ISO 80000-4:2019 (Mechanics)"
SRC_5 = "ISO 80000-5:2019 (Thermodynamics)"
SRC_6 = "ISO 80000-6:2022 (Electromagnetism)"
SRC_7 = "ISO 80000-7:2019 (Light and radiation)"
SRC_9 = "ISO 80000-9:2019 (Physical chemistry / molecular physics)"
SRC_12 = "ISO 80000-12:2019 (Condensed matter physics)"

Q = {
    # ---- SI 基本量（7 + 角度）----
    "mass":               ({"M": 1}, "kg", SRC_SI, "SI", "SI 基本量"),
    "length":             ({"L": 1}, "m", SRC_SI, "SI", "SI 基本量"),
    "time":               ({"T": 1}, "s", SRC_SI, "SI", "SI 基本量"),
    "current":            ({"I": 1}, "A", SRC_SI, "SI", "SI 基本量"),
    "temperature":        ({"Th": 1}, "K", SRC_SI, "SI", "SI 基本量（热力学温度）"),
    "amount":             ({"N": 1}, "mol", SRC_SI, "SI", "SI 基本量（物质的量）"),
    "luminous_intensity": ({"J": 1}, "cd", SRC_SI, "SI", "SI 基本量"),
    "angle":              ({}, "rad", SRC_3, "ISO80000-dimensionless",
                           "平面角：ISO 80000-3 规定 rad = 1（无量纲）；STTP 反解表另立 A 基"),

    # ---- 光度与辐射（ISO 80000-7）----
    # 注意：sr（球面度）无量纲 ⇒ 光通量与光强度、照度与亮度**两两同量纲**，
    #   是「量纲相同但物理意义不同」的教科书例子（与能量/力矩同型）。
    "luminous_flux": ({"J": 1}, "lm", SRC_7, "SI", "光通量 lm=cd*sr（sr 无量纲）；与光强度同量纲"),
    "illuminance":   ({"J": 1, "L": -2}, "lx", SRC_7, "SI", "照度 lx=lm/m^2；与亮度同量纲、意义不同"),
    "luminance":     ({"J": 1, "L": -2}, "cd/m^2", SRC_7, "SI", "亮度 cd/m^2；与照度同量纲、意义不同"),

    # ---- 空间与时间（ISO 80000-3）----
    "area":         ({"L": 2}, "m^2", SRC_3, "SI", "面积"),
    "volume":       ({"L": 3}, "m^3", SRC_3, "SI", "体积"),
    "velocity":     ({"L": 1, "T": -1}, "m/s", SRC_3, "SI", "速度（含 speed）"),
    "acceleration": ({"L": 1, "T": -2}, "m/s^2", SRC_3, "SI", "加速度（含重力加速度）"),
    "frequency":    ({"T": -1}, "Hz", SRC_3, "SI", "频率（周期现象，非角频率）"),
    "omega":        ({"T": -1}, "rad/s", SRC_3, "ISO80000-dimensionless",
                     "角频率/角速度：rad 无量纲故为 T^-1；若采 angle 为基则应含 A"),
    "flowrate":     ({"L": 3, "T": -1}, "m^3/s", SRC_3, "SI", "体积流量"),
    "kin_viscosity": ({"L": 2, "T": -1}, "m^2/s", SRC_3, "SI", "运动黏度"),

    # ---- 力学（ISO 80000-4）----
    "mass_density": ({"M": 1, "L": -3}, "kg/m^3", SRC_4, "SI", "密度（kg/m^3）"),
    "momentum":     ({"M": 1, "L": 1, "T": -1}, "kg*m/s", SRC_4, "SI", "动量；冲量同量纲"),
    "ang_momentum": ({"M": 1, "L": 2, "T": -1}, "kg*m^2/s", SRC_4, "ISO80000-dimensionless",
                     "角动量：rad 无量纲故为 ML^2T^-1；若采 angle 为基则应含 A"),
    "force":        ({"M": 1, "L": 1, "T": -2}, "N", SRC_4, "SI", "力"),
    "torque":       ({"M": 1, "L": 2, "T": -2}, "N*m", SRC_4, "SI", "力矩（与能量同量纲，物理意义不同）"),
    "pressure":     ({"M": 1, "L": -1, "T": -2}, "Pa", SRC_4, "SI", "压强/应力"),
    "stiffness":    ({"M": 1, "T": -2}, "N/m", SRC_4, "SI", "刚度（力/长度）"),
    "surf_tension": ({"M": 1, "T": -2}, "N/m", SRC_4, "SI", "表面张力（同为 N/m，与刚度同量纲不同物理意义）"),
    "energy":       ({"M": 1, "L": 2, "T": -2}, "J", SRC_4, "SI", "能量/功/热量（同量纲）"),
    "power":        ({"M": 1, "L": 2, "T": -3}, "W", SRC_4, "SI", "功率"),
    "action":       ({"M": 1, "L": 2, "T": -1}, "J*s", SRC_4, "SI", "作用量（与角动量、普朗克常量同量纲）"),
    "inertia":      ({"M": 1, "L": 2}, "kg*m^2", SRC_4, "SI", "转动惯量"),
    "viscosity":    ({"M": 1, "L": -1, "T": -1}, "Pa*s", SRC_4, "SI", "动力黏度"),

    # ---- 热力学（ISO 80000-5）----
    "entropy":      ({"M": 1, "L": 2, "T": -2, "Th": -1}, "J/K", SRC_5, "SI", "熵/热容（同量纲）"),
    "heat_capacity": ({"M": 1, "L": 2, "T": -2, "Th": -1}, "J/K", SRC_5, "SI", "热容（与熵同量纲）"),
    "specific_heat": ({"L": 2, "T": -2, "Th": -1}, "J/(kg*K)", SRC_5, "SI", "比热容/比熵"),
    "boltzmann":    ({"M": 1, "L": 2, "T": -2, "Th": -1}, "J/K", SRC_5, "SI", "玻尔兹曼常量（与熵同量纲）"),
    "thermal_cond": ({"M": 1, "L": 1, "T": -3, "Th": -1}, "W/(m*K)", SRC_5, "SI", "热导率"),
    "thermal_diff": ({"L": 2, "T": -1}, "m^2/s", SRC_5, "SI", "热扩散率（与运动黏度同量纲）"),
    "expansion":    ({"Th": -1}, "1/K", SRC_5, "SI", "体胀系数"),

    # ---- 电磁（ISO 80000-6）----
    "charge":       ({"I": 1, "T": 1}, "C", SRC_6, "SI", "电荷（与元电荷同量纲）"),
    "elem_charge":  ({"I": 1, "T": 1}, "C", SRC_6, "SI", "元电荷"),
    "voltage":      ({"M": 1, "L": 2, "T": -3, "I": -1}, "V", SRC_6, "SI", "电压/电动势"),
    "resistance":   ({"M": 1, "L": 2, "T": -3, "I": -2}, "Ohm", SRC_6, "SI", "电阻"),
    "resistivity":  ({"M": 1, "L": 3, "T": -3, "I": -2}, "Ohm*m", SRC_6, "SI", "电阻率"),
    "conductivity": ({"M": -1, "L": -3, "T": 3, "I": 2}, "S/m", SRC_6, "SI", "电导率（电阻率之倒数）"),
    "capacitance":  ({"M": -1, "L": -2, "T": 4, "I": 2}, "F", SRC_6, "SI", "电容"),
    "inductance":   ({"M": 1, "L": 2, "T": -2, "I": -2}, "H", SRC_6, "SI", "电感"),
    "efield":       ({"M": 1, "L": 1, "T": -3, "I": -1}, "V/m", SRC_6, "SI", "电场强度"),
    "bfield":       ({"M": 1, "T": -2, "I": -1}, "T", SRC_6, "SI", "磁感应强度（特斯拉）"),
    "mag_flux":     ({"M": 1, "L": 2, "T": -2, "I": -1}, "Wb", SRC_6, "SI", "磁通量"),
    "permittivity": ({"M": -1, "L": -3, "T": 4, "I": 2}, "F/m", SRC_6, "SI", "电容率（与电容同量纲，比 F/m 需再除 L）"),
    "permeability": ({"M": 1, "L": 1, "T": -2, "I": -2}, "H/m", SRC_6, "SI", "磁导率"),

    # ---- 物理化学（ISO 80000-9）----
    "amount_conc":  ({"N": 1, "L": -3}, "mol/m^3", SRC_9, "SI", "物质的量浓度（含 concentration）"),
    "molar_mass":   ({"M": 1, "N": -1}, "kg/mol", SRC_9, "SI", "摩尔质量"),
    "molar_energy": ({"M": 1, "L": 2, "T": -2, "N": -1}, "J/mol", SRC_9, "SI", "摩尔能（含标准吉布斯能、摩尔焓）"),
    "molar_entropy": ({"M": 1, "L": 2, "T": -2, "Th": -1, "N": -1}, "J/(mol*K)", SRC_9, "SI",
                      "摩尔熵（= 熵/物质的量；NIST WebBook std_entropy 锚定量）"),
    "molar_heat_capacity": ({"M": 1, "L": 2, "T": -2, "Th": -1, "N": -1}, "J/(mol*K)", SRC_9, "SI",
                            "摩尔热容（与摩尔熵同量纲）"),
    "gas_constant": ({"M": 1, "L": 2, "T": -2, "Th": -1, "N": -1}, "J/(mol*K)", SRC_9, "SI",
                     "摩尔气体常量（与摩尔熵、摩尔热容同量纲）"),
    "cat_activity": ({"N": 1, "T": -1}, "kat", SRC_9, "SI", "催化活性（katal）"),

    # ---- 固体/凝聚态（ISO 80000-12）----
    "avogadro":     ({"N": -1}, "1/mol", "CODATA 2018 / ISO 80000-9", "SI", "阿伏伽德罗常量（1/mol）"),
    "light_speed":  ({"L": 1, "T": -1}, "m/s", "CODATA 2018", "SI", "真空光速"),
    "grav_const":   ({"L": 3, "M": -1, "T": -2}, "m^3/(kg*s^2)", "CODATA 2018", "SI", "万有引力常量"),
    "planck":       ({"M": 1, "L": 2, "T": -1}, "J*s", "CODATA 2018", "SI", "普朗克常量"),
}

# 别名对齐（与 dimension_table.ALIAS 语义一致的「同义命名/规范译名」）
ALIAS = {
    "speed": "velocity",
    "gravity": "acceleration",
    "ke": "energy", "work": "energy", "heat": "energy",
    "internal_energy": "energy", "kinetic_energy": "energy",
    "potential_energy": "energy", "gibbs_energy": "energy",
    "gibbs_free_energy": "energy", "enthalpy": "energy",
    "free_energy": "energy", "activation_energy": "energy",
    "impulse": "momentum",
    "density": "mass_density",
    "concentration": "amount_conc",
    "e_mass_density": "mass_density",
    "wavelength": "length", "radius": "length", "focal_length": "length",
    "object_distance": "length", "image_distance": "length",
    "proper_time": "time",
    "gravitational_constant": "grav_const",
    "speed_of_light": "light_speed",
    "planck_constant": "planck",
    "reduced_planck_constant": "planck",
    "boltzmann_constant": "boltzmann",
    "statistical_entropy": "entropy",
    "electric_charge": "charge", "electric_current": "current",
    "electric_field": "efield", "magnetic_field": "bfield",
    "vacuum_permittivity": "permittivity", "vacuum_permeability": "permeability",
    "electromotive_force": "voltage", "magnetic_flux": "mag_flux",
    "amount_of_substance": "amount", "lum_intensity": "luminous_intensity",
    "angular_momentum": "ang_momentum",
    "torque": "torque",
}

DIM = ["M", "L", "T", "I", "Th", "N", "J", "A"]
_TABLE = None


def _norm_vec(d):
    return tuple(round(float(d.get(k, 0.0)), 9) for k in DIM)


def _display_key(name):
    """展示名 → snake_case 键（`Gibbs free energy` → `gibbs_free_energy`）。

    与 `dimension_table._display_key` 同一规整口径；不规整会**静默查不到**。
    """
    s = str(name).strip().lower()
    out = [ch if (ch.isalnum() or ch == "_") else "_" for ch in s]
    s = "".join(out)
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")


def canon(name):
    """归一为基表规范名（先 ALIAS，再展示名 snake_case，再同名直取）。"""
    s = str(name)
    if s in ALIAS:
        s = ALIAS[s]
    if s in Q:
        return s
    k = _display_key(s)
    if k in ALIAS:
        k = ALIAS[k]
    return k if k in Q else None


def load():
    """返回 {name: {"dim":..., "si_unit":..., "source":..., "convention":..., "scope":...}}。"""
    global _TABLE
    if _TABLE is None:
        _TABLE = {k: {"dim": dict(v[0]), "si_unit": v[1], "source": v[2],
                      "convention": v[3], "scope": v[4]} for k, v in Q.items()}
    return _TABLE


def dim_of(name):
    """返回量纲向量 dict；未知/未收录返回 None（宁缺勿滥）。"""
    c = canon(name)
    if c is None:
        return None
    return dict(Q[c][0])


def normalize(d):
    return _norm_vec(d)


def dump(path=OUT):
    payload = {
        "schema": SCHEMA,
        "meta": {
            "name": "独立 SI/ISO 80000 权威量纲基表",
            "why": "为 PhysicsBabel 反解表提供**独立外部对照**，打断同源自洽（复评 R09）",
            "sources": [SRC_SI, SRC_3, SRC_4, SRC_5, SRC_6, SRC_9, SRC_12,
                        "CODATA 2018 recommended values"],
            "base_dims": DIM,
            "n_quantities": len(Q),
            "angle_convention": {
                "si_iso80000": "平面角无量纲（rad = 1）",
                "sttp_reverse_table": "另立 A 为第 8 基，但实测仅 angle 一个量带 A（孤例）",
                "handling": "compare() 将「仅差 A」归为 convention_difference，不判为错误",
            },
            "note": "手工编纂，逐条可核；不反解、不外推、不猜测。",
        },
        "convention": {k: v["convention"] for k, v in load().items()},
        "source": {k: v["source"] for k, v in load().items()},
        "scope": {k: v["scope"] for k, v in load().items()},
        "quantities": {k: v["dim"] for k, v in load().items()},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
    return payload


# ---------------------------------------------------------------------------- #
# 对照
# ---------------------------------------------------------------------------- #
def compare(reverse_table):
    """把反解表（{name: {dim:exp}}）与独立基表逐条对照。

    返回 dict，分四类：
      agree                 量纲严格相等
      convention_difference 仅差「角度 A」分量（约定差异，非错误）
      conflict              非约定性的量纲分歧（必须裁断）
      only_in_reverse       基表未收录（覆盖缺口，如实披露）
    """
    base = load()
    agree, conv, conflict, only_rev = [], [], [], []
    checked = 0
    rev_canon = set()
    for name, rdim in sorted(reverse_table.items()):
        c = canon(name)
        if c is None:
            only_rev.append({"name": name, "reverse_dim": dict(rdim)})
            continue
        rev_canon.add(c)
        checked += 1
        rd = _norm_vec(rdim)
        bd = _norm_vec(base[c]["dim"])
        if rd == bd:
            agree.append(name)
            continue
        # 只看 A（角度）分量是否解释全部差异
        diff = tuple(r - b for r, b in zip(rd, bd))
        a_only = diff[:len(DIM) - 1] == tuple(0.0 for _ in DIM[:-1]) and diff[-1] != 0.0
        if a_only:
            conv.append({"name": name, "reverse_dim": dict(rdim),
                         "si_dim": dict(base[c]["dim"]),
                         "note": "仅角度维度约定差异（ISO 80000 视 rad 为无量纲）"})
        else:
            conflict.append({"name": name, "reverse_dim": dict(rdim),
                             "si_dim": dict(base[c]["dim"]),
                             "source": base[c]["source"]})
    # SI 基表独有的量（反解表覆盖缺口 —— 如实披露，勿当成错）
    only_si = [{"name": k, "dim": dict(base[k]["dim"]), "source": base[k]["source"]}
               for k in sorted(base) if k not in rev_canon]
    return {
        "n_reverse": len(reverse_table),
        "n_checked": checked,
        "agree": agree,
        "convention_difference": conv,
        "conflict": conflict,
        "only_in_reverse": only_rev,
        "only_in_si": only_si,
        "agree_rate_of_checked": round(len(agree) / checked, 4) if checked else None,
    }


if __name__ == "__main__":
    p = dump()
    print("[OK] %s: %d 个量（独立基表）" % (os.path.basename(OUT), p["meta"]["n_quantities"]))
