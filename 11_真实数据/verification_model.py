#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""verification_model.py —— 边级「可信性分层」模型（Phase 28 / 第 18 轮）

背景（铁律 #19）
----------------
`verified` 作为**单一布尔**会语义通胀：把四类语义完全不同的东西压成一个 `True` ——
  · 构造性事实（元素符号、分子式解析计数）
  · 确定性规则校验（量纲齐次、原子/电荷守恒、周期表位置）
  · 多源一致（PubChem inchikey 三方交叉）
  · **模型预测（GNN / LLM 链接预测）**   ← 最严重：把「预测」冒充「验证」
Phase 27 后的实测分布（9656/48735）：27545 条边 `verified=True`，其中
`kind ∈ {gnn_typed_verified, gnn_typed_inferred, llm_inferred*}` 的有 **3400+ 条**，
`has_symbol|real|PhysicsBabel` 有 **24105 条**（塔自动义，与物理门禁无关）。

分层受控词表（由弱到强）
------------------------
    unverified       无任何验证依据
    model_inferred   模型（GNN / LLM）推断，未独立复核
    source_asserted  外部源 / 仓库内人工策划**直接断言**，未做独立复核
    by_construction  由权威表 / 概念定义**构造性产生**（无判断空间）
    rule_checked     通过**确定性规则 / 算法校验**（独立实现、可复算）
    cross_source     **≥2 独立源一致**
    human_reviewed   人工复核确认

口径
----
    verified         = level ∈ {by_construction, rule_checked, cross_source, human_reviewed}
    verified_strict  = level ∈ {rule_checked, cross_source, human_reviewed}   ← 北极星口径

铁律 #14（自检输入不得与被检对象同源）
--------------------------------------
`composed_of` 的 count 用**本模块内独立实现的分子式解析器**复算，**不调用**
`rhea_ingest.parse_formula` / `pubchem_mol_ingest.parse_formula`（那是产出方，
同源自检会一起静默通过）。

用法
----
    python verification_model.py --audit          # 只读审计：分层分布 + 独立复算 + 纠错清单
    python verification_model.py --audit --json p # 附带落盘审计结果
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import dimension_table as dm          # noqa: E402
import element_reference as er        # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")

# ------------------------------------------------------------------- 属性形态守卫
# 2026-10-10 发现：权威图里 `dim_exponents`（5000 个 Formula 节点）与 `composition`
# （14 个分子节点）存的是 **Python repr 字符串**（`"{'mass': '1', ...}"`，单引号 →
# 不是合法 JSON），而非 dict。溯源：早期 Phase 11/12 的「viz 投影 → 回写 raw」往返
# 把投影层的字符串化形态冻进了权威图（本地 ingest 产出的是 dict，见
# `physicsbabel_raw.json`）。危害：任何期望 dict 的消费者会**静默拿到 str**。
# 处置：① 本轮 delta 从 pristine raw 恢复为 dict；② 读取侧容错（literal_eval）。
REPR_KEYS = ("dim_exponents", "composition")


def as_dict(v) -> dict:
    """把可能是 dict / JSON 串 / Python-repr 串的属性值稳妥还原为 dict（只读容错）。"""
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip()[:1] in "{[":
        for loader in (json.loads, ast.literal_eval):     # literal_eval 安全：不执行代码
            try:
                got = loader(v)
                if isinstance(got, dict):
                    return got
            except Exception:
                continue
    return {}

# --------------------------------------------------------------------------- 词表
LEVELS = [
    "unverified",
    "model_inferred",
    "source_asserted",
    "by_construction",
    "rule_checked",
    "cross_source",
    "human_reviewed",
]
RANK = {lv: i for i, lv in enumerate(LEVELS)}
VERIFIED_MIN = RANK["by_construction"]     # verified
STRICT_MIN = RANK["rule_checked"]          # verified_strict

# 声明性说明：每个等级**实际**覆盖了什么 / 不覆盖什么（供前端与报告引用）
LEVEL_DOC = {
    "unverified": "无验证依据（未分类 / 来源不明）",
    "model_inferred": "模型推断（GNN 链接预测 / LLM 推断），**不构成验证**",
    "source_asserted": "单一来源或仓库内策划直接断言；**未做独立复核**",
    "by_construction": "权威表 / 概念定义构造性产生；结论真，但**无判断空间**",
    "rule_checked": "确定性规则校验通过（独立实现、可复算）",
    "cross_source": "≥2 独立来源一致",
    "human_reviewed": "人工复核确认",
}

# 模型产物 kind（把「预测」误标为「已验证」的元凶）
MODEL_KINDS = {
    "gnn_typed_verified",
    "gnn_typed_inferred",
    "llm_inferred",
    "llm_inferred_gnn",
    "llm_review",
    "llm_review_gnn",
}


def is_verified(level: str) -> bool:
    return RANK.get(level, -1) >= VERIFIED_MIN


def is_strict(level: str) -> bool:
    return RANK.get(level, -1) >= STRICT_MIN


# 「门禁名」与「实测范围」的合法配对表 —— 用于识别**虚假归因**
# （例：`has_symbol` 挂着 `R-PHY`，却从未跑过量纲门禁 → 归因与范围不符）
GATE_SCOPE_OK = {
    # ⚠ 白名单**只放**量纲门禁自己的范围。**切勿**把 `formula_participation_*` 加进来：
    #   Phase 27 已坐实 `has_symbol` 的验证与量纲门禁无关，把它列进来等于给虚假归因开后门
    #   （冻结反例 `P0-6-attrib-has-symbol-phy-bogus` 会在下一次运行立刻 FAIL）。
    "R-PHY": {"dimensional_strict_equal", "dimensional_mismatch"},
    "R-CHEM": {"formula_count_independent_recheck", "formula_count_cross_source",
               "formula_count_mismatch", "period_authority_equal", "family_authority_equal",
               "equation_sidedness", "source_assertion", "published_descriptor_value",
               "inchikey_cross_source", "inchikey_exact_match", "name_exact_match",
               "manual_curation", "composition_source"},
    "R-MATH": {"latex_normalized_only", "model_link_prediction", "bibliographic_metadata",
               "source_assertion", "manual_curation", "symbol_scan_from_text"},
    "R-BIO": {"equation_sidedness", "source_assertion", "model_link_prediction",
              "manual_curation"},
}


def _gate_matches_scope(gate, scope) -> bool:
    if not gate:
        return True
    return scope in GATE_SCOPE_OK.get(gate, set())


# ----------------------------------------------------------------- 独立分子式解析（铁律 #14）
_TOK = re.compile(r"([A-Z][a-z]?|\(|\)|\[|\]|R\d*|[XAZ]|\d+|·|\.)")


def parse_formula_independent(f: str) -> dict:
    """**独立实现**的分子式解析器（不复用产出方代码）。

    支持：元素、下标、圆括号/方括号嵌套乘子、水合物 `·`/`.`、占位符 R/X/A/Z（按伪元素计）。
    电荷后缀（`+`/`-`/`^n`）忽略（只比元素计数）。返回 {元素: 计数}；无法解析则抛 ValueError。
    """
    if not f:
        raise ValueError("empty")
    s = str(f).strip()
    # 去掉电荷 / 同位素尾部
    s = re.sub(r"(\^\d*[+\-]+\d*|[+\-]\d*)$", "", s)
    if not s:
        raise ValueError("only-charge")

    def parse_seq(i, stop):
        acc = collections.defaultdict(float)
        while i < len(s):
            ch = s[i]
            if ch in stop:
                return acc, i
            if ch in "([":
                inner, i = parse_seq(i + 1, ")]" if ch == "(" else "])")
                i += 1  # 跳过闭合括号
                m = re.match(r"\d+", s[i:])
                mult = float(m.group()) if m else 1.0
                i += len(m.group()) if m else 0
                for k, v in inner.items():
                    acc[k] += v * mult
                continue
            if ch in ")]":
                return acc, i
            if ch in "·.":
                i += 1
                continue
            m = _TOK.match(s, i)
            if not m:
                raise ValueError("bad token at %d: %r" % (i, s[i:i + 4]))
            tok = m.group()
            if re.fullmatch(r"\d+", tok):        # 裸数字（如 hydrate 前缀）忽略
                i += len(tok)
                continue
            nxt = re.match(r"\d+", s[i + len(tok):])
            cnt = float(nxt.group()) if nxt else 1.0
            acc[tok] += cnt
            i += len(tok) + (len(nxt.group()) if nxt else 0)
        return acc, i

    acc, _ = parse_seq(0, "")
    if not acc:
        raise ValueError("no-atoms")
    # 归一为 int（能量化则量化）
    out = {}
    for k, v in acc.items():
        out[k] = int(v) if abs(v - round(v)) < 1e-9 else v
    return out


# --------------------------------------------------------------------------- 上下文
def sym_of_el_id(nid: str):
    """`EK:el:Sc` / `EL:el:Sc` -> 'Sc'；非元素节点返回 None。"""
    if not nid or ":el:" not in nid:
        return None
    return nid.split(":el:", 1)[1]


PLACEHOLDERS = {"R", "X", "A", "Z"}

# PhysicsBabel 适配器把「参与量名」写进了 rationale；据此可**独立**核对该量是否
# 真的出现在公式的 `dim_exponents` 键中（比按目标节点名比对更稳，免受别名/大小写影响）。
_RATIONALE_KEY = re.compile(r"方程中\s*(.+?)\s*为参与量")


def rationale_key(rationale):
    if not rationale:
        return None
    m = _RATIONALE_KEY.search(str(rationale))
    return m.group(1).strip() if m else None


# Phase13 Gate.B1 把「所依据的分子式」写进了 rationale：`化学式 CO2 含 C×1（组成解析）`
_FORMULA_IN_RATIONALE = re.compile(r"化学式\s*([^\s含]+)\s*含")

# Phase9 LLM 精炼把「该量属于哪一类物理量」写进了 rationale：
# `分子摩尔质量/分子量属质量类物理量（组成校验）`
# → 可**确定性地**核对：声称的类 与 目标物理量的实际量纲 是否自洽。
# 实测该理由被原样粘贴到 8 个不同目标上（质量 + 能量 + 动能 + 内能 …），
# 即「一条理由 / 多个互斥目标」——**自相矛盾**，属真实缺陷而非「未验证」。
_CLASS_IN_RATIONALE = re.compile(r"属(.+?)类物理量")

# 类名 → 该类的量纲（用 SI 基本量表示）；用于与 dimension_table 的真量纲比对
CLASS_DIM = {
    "质量": {"M": 1.0},
    "能量": {"L": 2.0, "M": 1.0, "T": -2.0},
    "力": {"L": 1.0, "M": 1.0, "T": -2.0},
    "压强": {"L": -1.0, "M": 1.0, "T": -2.0},
    "电荷": {"I": 1.0, "T": 1.0},
    "温度": {"Th": 1.0},
    "时间": {"T": 1.0},
    "长度": {"L": 1.0},
}


def class_dim_in_rationale(rationale):
    """取出 rationale 声称的「物理量类」及其应有量纲；无声明返回 (None, None)。"""
    if not rationale:
        return None, None
    m = _CLASS_IN_RATIONALE.search(str(rationale))
    if not m:
        return None, None
    label = m.group(1).strip()
    for name, dim in CLASS_DIM.items():
        if name in label:
            return label, dim
    return label, None


def _drop(d):
    return {k: float(v) for k, v in (d or {}).items() if abs(float(v)) > 1e-9}


# 单位串 → SI 基本量向量（Phase 29）。
#
# ⚠ 这是**本模块自己的**独立解析实现，**不 import** 产出方（`webbook_ingest.UNIT_DIM`）——
#   铁律 #14：自检输入若与被检对象同源就会**一起静默通过**。
#   判据仅基于单位串本身（`kJ/mol` 是能量/物质的量，与"谁写的"无关）。
#   复合单位用 `/` 与 `*` 显式拆解，可逐项复算。
_UNIT_BASE = {
    "j": {"M": 1.0, "L": 2.0, "T": -2.0},      # J = kg·m²/s²
    "kj": {"M": 1.0, "L": 2.0, "T": -2.0, "W": 3.0},   # kJ = 10³ J（量纲同 J，W 为量级占位）
    "mol": {"N": 1.0},
    "k": {"Th": 1.0},
    "kg": {"M": 1.0},
}


def unit_dimension(unit):
    """把单位串（`kJ/mol`、`J/mol*K`…）解析为量纲向量；不可解析返回 None。

    只做**乘除**与**整数幂**（`mol^2`、`s^-1`）；分子/分母各段按 `*` 拆开，
    指数取 `^n`。无法识别的单位符号一律返回 None（**不猜**）。
    """
    if not unit:
        return None
    s = str(unit).strip().lower().replace(" ", "").replace("·", "*")
    s = s.replace("(mol*k)", "mol*k").replace("(mol·k)", "mol*k")
    if "/" in s:
        num, _, den = s.partition("/")
    else:
        num, den = s, ""
    out = {}

    def _acc(seg, sign):
        if not seg:
            return True
        for tok in seg.split("*"):
            if not tok:
                continue
            exp = 1.0
            if "^" in tok:
                base, _, e = tok.partition("^")
                try:
                    exp = float(e)
                except ValueError:
                    return False
                tok = base
            d = _UNIT_BASE.get(tok)
            if d is None:
                return False
            for k, v in d.items():
                if k == "W":                       # 量级占位，不参与量纲
                    continue
                out[k] = out.get(k, 0.0) + sign * exp * v
        return True

    if not _acc(num, 1.0) or not _acc(den, -1.0):
        return None
    return {k: v for k, v in out.items() if abs(v) > 1e-9}


def dim_matches_class(got, want):
    """目标量的真量纲 `got` 是否属于 rationale 声称的类 `want`。

    **含「每摩尔」形式**：摩尔量（per amount）是同类量的表示法 ——
    `molar_mass = mass / amount`、`molar_energy = energy / amount`。
    只做 `got == want` 会把 700 条合法的「分子 → 摩尔质量」桥误判为自相矛盾
    （第 19 轮实测：漏掉这一层的首次实现一次删掉 762 条，其中 700 条是**好边**）。
    返回 True / False / None（不可判定）。
    """
    g, w = _drop(got), _drop(want)
    if not g or not w:
        return None
    if g == w:
        return True
    mol = dict(w)
    mol["N"] = mol.get("N", 0.0) - 1.0
    return g == _drop(mol)


# 分子摩尔质量（Phase9.B5 / Phase13.Gate.B5 写入）：`摩尔质量 44.009 g/mol（化学式解析+原子量）`
_MASS_IN_RATIONALE = re.compile(r"摩尔质量\s*([0-9.]+)\s*g/mol")

# 下标 / 上标字符（`CO₂`、`SO₄²⁻`）→ ASCII，供「名称即化学式」的节点使用
_SUBSUP = str.maketrans("₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹", "01234567890123456789")
_FORMULA_TOKEN = re.compile(r"[A-Za-z0-9\(\)\[\]\.\+\-]{1,40}")


def formula_from_name(name):
    """无 `formula` / `pubchem_formula` 时，**节点名本身可能就是化学式**（`CO₂` / `CH₄`）。

    第 19 轮实测：`MX` 域的 `MX:chem:co2`(name=`CO₂`) / `MX:chem:methane`(name=`CH₄`)
    有 `has_quantity → molar_mass` 边且值**完全正确**（44.009 / 16.043），却因为节点没有
    `formula` 字段而无法独立复算，被降级为 `model_inferred`（铁律 #17 的同类问题：
    「字段缺失」≠「不可用」）。此处做**保守**回退：只有把下标归一后能被独立解析器
    接受的串才认（`Water`、`n-Butanol` 等自然语言名一律不认）。
    """
    if not name:
        return None
    s = str(name).translate(_SUBSUP).strip()
    if not _FORMULA_TOKEN.fullmatch(s) or not re.search(r"[A-Z]", s):
        return None
    try:
        comp = parse_formula_independent(s)
    except Exception:                                    # noqa: BLE001
        return None
    return s if comp else None


def src_mass_formula(props):
    """摩尔质量复算所用的分子式来源（**有序**回退，不猜）：PubChem → formula → 名称。"""
    p = props or {}
    return (p.get("pubchem_formula") or p.get("formula")
            or formula_from_name(p.get("name")))


def formula_in_rationale(rationale):
    """从 rationale 中取出所述分子式串（无则 None）。"""
    if not rationale:
        return None
    m = _FORMULA_IN_RATIONALE.search(str(rationale))
    return m.group(1) if m else None


def elem_family(sym: str):
    """元素「族」的权威口径：主族用族号，f 区用 block+series。"""
    r = er.by_symbol(sym)
    if not r:
        return None
    if r.get("group"):
        return ("g", r["group"])
    return ("f", r.get("block"), r.get("series"))


def qname_of_node(node: dict) -> str:
    """取节点上的量名（用于量纲查表）。"""
    p = (node or {}).get("props") or {}
    return p.get("name") or (node or {}).get("id", "").split(":")[-1]


# --------------------------------------------------------------------------- 分类
def classify(e: dict, ctx: dict):
    """返回 {verification_level, verification_scope, verifier[, note]}；不可判定返回 None。

    分级原则（**证据优先于提出者**）：
      A 段先做「与提出者无关」的确定性复算 —— 只要复算可判且通过，即 `rule_checked`；
        复算判否 → `unverified` 并附 note（真实缺陷，交给审计清单）。
        复算**不可判定** → 落到 B 段，按提出者/来源定级（**不猜、不放水**）。
      B 段按来源定级：模型产物 → `model_inferred`；外部源/仓库内策划 → `source_asserted`。

    ⚠ 注意：A 段的 `composed_of` 复算验的是「**记录计数 == 独立解析记录分子式**」这一
    **内部一致性**；它**不**验证分子式本身是否正确（那需要外部源 → `source_asserted`/`cross_source`）。
    因此 scope 名为 `formula_count_independent_recheck` 而非 `composition_verified`。
    """
    p = e.get("props") or {}
    t = e.get("type")
    kind = p.get("kind") or e.get("kind") or ""
    src = p.get("source") or ""
    eid = e.get("id") or "%s|%s|%s" % (t, e.get("source"), e.get("target"))

    def R(level, scope, verifier, note=None):
        out = {"verification_level": level, "verification_scope": scope,
               "verifier": verifier}
        if note:
            out["note"] = note
        return out

    # ================= A. 确定性复算（与提出者无关） =================
    # A1 量—量量纲一致
    if t == "dimensionally_consistent":
        if kind == "dimension_table":
            return R("rule_checked", "dimensional_strict_equal", "dimension_table.py")
        ok, why = ctx["dim_ok"].get(eid, (None, ""))
        if ok is True:
            return R("rule_checked", "dimensional_strict_equal",
                     "verification_model.dimension_table")
        if ok is False:
            return R("unverified", "dimensional_mismatch",
                     "verification_model.dimension_table",
                     "独立复算量纲不一致：%s" % why)

    # A2 周期表位置（元素—元素）
    if t in ("same_period", "same_family"):
        key = "period_ok" if t == "same_period" else "family_ok"
        ok, why = ctx[key].get(eid, (None, ""))
        if ok is True:
            return R("rule_checked", "%s_authority_equal" % t.split("_")[1],
                     "element_reference.py")
        if ok is False:
            return R("unverified", "%s_mismatch" % t.split("_")[1],
                     "element_reference.py", why)

    # A3 分子组成：独立解析分子式复算 count
    #   关键区分：**式串的来源**决定可信上限（避免把「模型自述的分子式」洗成已验证）
    #   · 节点带外部权威式（PubChem）且计数一致 → cross_source
    #   · 边带 `from_formula`（源侧适配器写入，如 ChEBI/PubChem/策划种子）且计数一致 → rule_checked
    #   · 仅有模型侧 rationale 中的式串 → 内部一致但**无外部锚**，仍记 model_inferred
    if t == "composed_of":
        aok, auth, auth_field = ctx["composed_auth_ok"].get(eid, (None, None, None))
        ok, why = ctx["composed_ok"].get(eid, (None, ""))
        # 第二源必须**真的独立**：节点权威式为 `pubchem_formula`（PubChem），
        # 且边自身没有取自同一 PubChem 的 `from_formula`
        if aok is True and auth_field == "pubchem_formula" \
                and not (p.get("from_formula") and p.get("source") == "PubChem"):
            return R("cross_source", "formula_count_cross_source",
                     "PubChem(pubchem_formula) × 图内组成边")
        if ok is True:
            return R("rule_checked", "formula_count_independent_recheck",
                     "verification_model.parse_formula_independent")
        if ok is False:
            return R("unverified", "formula_count_mismatch",
                     "verification_model.parse_formula_independent", why)
        if kind == "pubchem_composition":
            return R("cross_source", "inchikey_cross_source", "PubChem/curated")

    # A4 公式—参与量（PhysicsBabel has_symbol）
    if t == "has_symbol" and src == "PhysicsBabel" and kind == "real":
        pbkey = rationale_key(p.get("rationale"))
        cand = [pbkey] if pbkey else [p.get("_tname") or "", p.get("_qname") or ""]
        for cc in cand:
            if cc and (e.get("source"), dm.canon(cc).lower()) in ctx["formula_members"]:
                return R("rule_checked", "formula_participation_membership",
                         "verification_model.formula_members")
        return R("unverified", "formula_participation_not_found",
                 "verification_model.formula_members",
                 "该量不在公式 exponents 中且与记录键不符（陈旧错挂，待清理）")

    # A5 跨域桥 · 分子 → 摩尔质量物理量（Phase9.B5 / Phase13.Gate.B5）
    #    证据 = 「元素原子量 × 独立解析的化学式计数」复算 == 记录值（与提出者无关）
    if t == "has_quantity" and ctx["mass_ok"].get(eid) is not None:
        ok, cross, why = ctx["mass_ok"][eid]
        if ok is True and cross:
            return R("cross_source", "molar_mass_cross_source",
                     "element_reference.py × PubChem(pubchem_molecular_weight)", why)
        if ok is True:
            return R("rule_checked", "molar_mass_independent_recompute",
                     "verification_model.parse_formula_independent × element_reference.py", why)
        if ok is False:
            return R("unverified", "molar_mass_mismatch",
                     "verification_model.parse_formula_independent × element_reference.py", why)

    # A6 跨域桥 · rationale 声称的「类」与目标物理量真量纲自洽性
    #    **一条理由被粘贴到多个互斥目标**（质量 + 动能 + 内能…）→ 自相矛盾 → 不成立
    if t == "has_quantity" and ctx["hq_class_ok"].get(eid) is not None:
        ok, label, why = ctx["hq_class_ok"][eid]
        if ok is False:
            return R("unverified", "rationale_target_mismatch",
                     "verification_model.dimension_table",
                     "理由声称「%s类」，与目标物理量真量纲不符：%s" % (label, why))

    # A7 跨域桥 · NIST WebBook 热化学（Phase 29 接入）
    #    证据是**外源的确定性判据**，不是「谁提的」：
    #      ① 单位（`kJ/mol` / `J/mol*K`）经量纲表**独立复算**，必须与目标物理量真量纲一致；
    #      ② 页面内 **≥2 条独立文献**在容差内吻合 → 第二级证据。
    #    ⚠ 判据**只读边自身的 `unit` / `n_references`**，绝不读已存的 `verification_level`
    #      —— 否则「复算」变成复述，铁律 #14 退化。
    #    ⚠ 本段是**必需**的：缺它则这些边落库为 `rule_checked` 而重算为 `source_asserted`，
    #      产生「落库档位 ≠ 复算档位」的静默不一致（第 19 轮实测 157 条，铁律 #23）。
    if t == "has_quantity" and kind == "webbook_thermochemistry":
        tnode = (ctx.get("node_by_id") or {}).get(e.get("target")) or {}
        qn = (tnode.get("props") or {}).get("name") or str(e.get("target") or "").split(":")[-1]
        got = dm.dim_of(qn)                     # 目标物理量的**真量纲**（真值表）
        want = unit_dimension(p.get("unit"))    # 边自带单位的量纲（本模块独立解析）
        if got is None or want is None:
            pass            # 不可判定 → 落到 B 段按来源定级，不猜
        elif _drop(want) != _drop(got):
            return R("unverified", "webbook_unit_dimension_mismatch",
                     "NIST WebBook × dimension_table(单位量纲)",
                     "单位 `%s` 解析为 %s，但目标量 %s 真量纲为 %s"
                     % (p.get("unit"), _drop(want), qn, _drop(got)))
        else:
            nref = int(p.get("n_references") or 0)
            if nref >= 2:
                return R("cross_source", "webbook_multi_reference_agreement",
                         "NIST WebBook(≥2 独立文献) × dimension_table",
                         "单位量纲独立复算通过；%d 条独立文献吻合" % nref)
            return R("rule_checked", "webbook_unit_dimension_recompute",
                     "NIST WebBook × dimension_table(单位量纲)",
                     "单位 `%s` 经量纲表独立复算一致" % p.get("unit"))

    # ================= B. 无独立复算可用：按提出者 / 来源定级 =================
    # B1 模型产物（GNN 链接预测 / LLM 推断）—— **不构成验证**
    if kind in MODEL_KINDS or ("GNN" in src) or ("LLM" in src) \
            or ("gnn" in kind) or ("llm" in kind):
        return R("model_inferred", "model_link_prediction", src or kind,
                 "GNN/LLM 预测，非验证")

    # B2 仓库内人工策划
    if src.startswith("curated_seed"):
        if t in ("reactant_of", "product_of"):
            return R("source_asserted", "manual_curation", "curated_seed")
        if t == "has_unit":
            return R("source_asserted", "manual_curation", "curated_seed")
        return R("source_asserted", "manual_curation", "curated_seed")

    # B3 化学反应方向
    if t in ("reactant_of", "product_of"):
        if kind in ("rhea_reactant", "rhea_product"):
            return R("source_asserted", "equation_sidedness", "Rhea/ChEBI")
        if kind == "real_reaction":
            return R("source_asserted", "source_assertion", src or "ElementKG2.0")
        return R("source_asserted", "source_assertion", src or "unknown")

    # B4 物理量 / 单位
    if t == "has_quantity":
        if src == "chembl_api":
            return R("source_asserted", "published_descriptor_value", "ChEMBL API")
        return R("source_asserted", "source_assertion", src or "unknown")
    if t == "has_unit":
        if kind == "constant_unit":
            return R("source_asserted", "codata_unit", "NIST CODATA")
        return R("source_asserted", "source_assertion", src or "unknown")

    # B5 同义 / 等价边
    if t == "has_symbol":
        # 非 PhysicsBabel 的 has_symbol（符号扫描类）：由文本扫描确定性产生
        return R("by_construction", "symbol_scan_from_text", src or "etl_pipeline.py")
    if t == "same_as":
        if kind == "pubchem_bridge":
            return R("cross_source", "inchikey_exact_match", "PubChem")
        if kind == "cross_source_alignment":
            return R("cross_source", "multi_source_alignment", "cross_source_alignment")
        if kind == "chebi_name_bridge":
            return R("source_asserted", "name_exact_match", "ChEBI label")
        return R("source_asserted", "manual_curation", src or "curated_seed")
    if t == "same_formula_as":
        # Phase 27 已把边类型收窄为「分子式相同」——声明范围内为真
        return R("by_construction", "formula_only",
                 "elementkg10m_ingest.py(skeleton)")
    if t == "same_latex_normalized":
        return R("model_inferred", "latex_normalized_only", "gnn_infer.py(norm_latex)")

    # B6 常量派生 / 文献 / schema
    if t == "derived_from" and kind == "constant_derivation":
        return R("source_asserted", "codata_derivation", "NIST CODATA")
    if t in ("cites", "discusses", "defines") and src in ("openalex", "mathxiv",
                                                         "openalex-topic-align"):
        return R("source_asserted", "bibliographic_metadata", src)
    if t == "derived_from" and src == "mathxiv":
        return R("source_asserted", "bibliographic_metadata", "mathxiv")
    if t == "part_of" and src == "schema-axiom":
        return R("by_construction", "schema_axiom", "schema-axiom")

    # B7 其它外部源断言
    if src in ("ElementKG2.0", "ElementKG", "ChEBI", "PubChem", "rhea_chebi",
               "nist_codata_2022", "chembl_api"):
        return R("source_asserted", "source_assertion", src)

    # B8 连来源都没有 → 不猜
    return R("unverified", "unclassified", src or "unknown")


# --------------------------------------------------------------------------- 独立复算
def build_ctx(nodes, edges):
    node_by_id = {n["id"]: n for n in nodes}
    ctx = {
        "node_by_id": node_by_id,
        "formula_members": set(),
        "composed_ok": {},
        "composed_auth_ok": {},
        "period_ok": {},
        "family_ok": {},
        "dim_ok": {},
        "mass_ok": {},          # eid -> (ok, cross, why)：分子摩尔质量独立复算
        "hq_class_ok": {},      # eid -> (ok, class_label, why)：rationale 声称类 vs 目标量纲
        "bugs": collections.defaultdict(list),
    }

    # (a) PhysicsBabel 公式成员集合（独立实现：直接读 Formula.dim_exponents）
    for n in nodes:
        if "Formula" not in (n.get("labels") or []):
            continue
        for k in as_dict((n.get("props") or {}).get("dim_exponents")):
            ctx["formula_members"].add((n["id"], dm.canon(k).lower()))

    for e in edges:
        t = e.get("type")
        p = e.get("props") or {}
        eid = e.get("id") or "%s|%s|%s" % (t, e.get("source"), e.get("target"))
        src, tgt = e["source"], e["target"]

        # (b) same_period / same_family 与权威表比对
        if t in ("same_period", "same_family"):
            a, b = sym_of_el_id(src), sym_of_el_id(tgt)
            if not a or not b:
                ctx["period_ok" if t == "same_period" else "family_ok"][eid] = \
                    (None, "非元素端点")
                continue
            if t == "same_period":
                pa = (er.by_symbol(a) or {}).get("period")
                pb = (er.by_symbol(b) or {}).get("period")
                ok = (pa is not None and pa == pb)
                ctx["period_ok"][eid] = (ok, "period %s=%s vs %s=%s" % (a, pa, b, pb))
            else:
                fa, fb = elem_family(a), elem_family(b)
                ok = (fa is not None and fa == fb)
                ctx["family_ok"][eid] = (ok, "family %s=%s vs %s=%s" % (a, fa, b, fb))

        # (c) composed_of：独立解析分子式复算 count
        #     分子式来源优先级：`from_formula` 属性 → rationale 中的「化学式 XXX 含 Y×n」
        if t == "composed_of":
            sym = sym_of_el_id(tgt)
            f = p.get("from_formula")
            if not f:
                m = _FORMULA_IN_RATIONALE.search(str(p.get("rationale") or ""))
                f = m.group(1) if m else None
            cnt = p.get("count")
            # 节点上的**权威分子式**：`pubchem_formula`（PubChem 交叉源）优先，其次 `formula`
            nprops = node_by_id.get(src, {}).get("props") or {}
            if nprops.get("pubchem_formula"):
                auth, auth_field = nprops["pubchem_formula"], "pubchem_formula"
            elif nprops.get("formula"):
                auth, auth_field = nprops["formula"], "formula"
            else:
                auth, auth_field = None, None
            if not sym or cnt is None:
                ctx["composed_ok"][eid] = (None, "无计数可复算")
                continue
            if f is None:
                ctx["composed_ok"][eid] = (None, "无分子式串可复算")
            else:
                try:
                    comp = parse_formula_independent(f)
                    got = comp.get(sym)
                    ok = (got is not None and float(got) == float(cnt))
                    ctx["composed_ok"][eid] = (
                        ok, "%s: 独立解析 %s=%s vs 记录 %s (from %s)" % (sym, sym, got, cnt, f))
                    if not ok:
                        ctx["bugs"]["composed_of_count_mismatch"].append(
                            (eid, f, sym, cnt, got))
                except Exception as ex:
                    ctx["composed_ok"][eid] = (None, "分子式不可独立解析(%s)" % ex)
            # 第二道：与节点权威分子式复核（可判定 → 说明是「式串陈旧」而非「计数错」）
            if auth:
                try:
                    ca = parse_formula_independent(auth)
                    ga = ca.get(sym)
                    aok = (ga is not None and float(ga) == float(cnt))
                    ctx["composed_auth_ok"][eid] = (aok, auth, auth_field)
                    if aok and ctx["composed_ok"].get(eid, (None,))[0] is False:
                        ctx["bugs"]["composed_of_stale_formula"].append(
                            (src, auth, f, sym, cnt))
                except Exception:
                    ctx["composed_auth_ok"][eid] = (None, auth, auth_field)
            else:
                ctx["composed_auth_ok"][eid] = (None, None, None)

        # (d) dimensionally_consistent（非 dimension_table 的）独立复算
        if t == "dimensionally_consistent" and p.get("kind") != "dimension_table":
            na = qname_of_node(node_by_id.get(src))
            nb = qname_of_node(node_by_id.get(tgt))
            da, db = dm.dim_of(na), dm.dim_of(nb)
            if da is None or db is None:
                ctx["dim_ok"][eid] = (None, "量纲未知(%s/%s)" % (na, nb))
            else:
                ok = dm.dim_equal(na, nb)
                ctx["dim_ok"][eid] = (ok, "%s vs %s" % (na, nb))
                if not ok:
                    ctx["bugs"]["dim_mismatch"].append((eid, na, nb))
        # (e) has_quantity：分子 → 物理量（**跨域桥**）的两道确定性复核
        #     e1) 目标为摩尔质量 → 用元素原子量 × 独立解析的化学式计数**复算**摩尔质量
        #     e2) rationale 声称的「类」与目标物理量的真量纲是否自洽（识破「一条理由多目标」）
        if t == "has_quantity":
            tn = node_by_id.get(tgt) or {}
            tprops = tn.get("props") or {}
            qname = tprops.get("name") or t.split(":")[-1]
            canon_q = dm.canon(qname)

            # e1 摩尔质量复算
            if canon_q == "molar_mass":
                sp = (node_by_id.get(src, {}).get("props") or {})
                f = src_mass_formula(sp)
                rec = p.get("value")
                if rec is None:
                    m = _MASS_IN_RATIONALE.search(str(p.get("rationale") or ""))
                    rec = float(m.group(1)) if m else None
                if not f or rec is None:
                    ctx["mass_ok"][eid] = (None, False, "无分子式/记录值可复算")
                else:
                    try:
                        comp = parse_formula_independent(f)
                        tot, miss = 0.0, []
                        for sym, cnt in comp.items():
                            w = er.weight_of(sym)
                            if w is None:
                                miss.append(sym)
                                break
                            tot += float(w) * float(cnt)
                        if miss:
                            ctx["mass_ok"][eid] = (None, False, "缺原子量 %s" % miss)
                        else:
                            rel = abs(tot - float(rec)) / max(abs(float(rec)), 1e-9)
                            ok = rel <= 0.005
                            pmm = sp.get("pubchem_molecular_weight")
                            cross = bool(ok and pmm is not None
                                         and abs(float(pmm) - float(tot)) / max(abs(tot), 1e-9) <= 0.01)
                            why = "%s: 复算 %.4f vs 记录 %s（%+.2f%%）；PubChem %s" % (
                                f, tot, rec, 100.0 * (tot - float(rec)) / max(abs(float(rec)), 1e-9), pmm)
                            ctx["mass_ok"][eid] = (ok, cross, why)
                            if not ok:
                                ctx["bugs"]["molar_mass_mismatch"].append(
                                    (src, f, rec, round(tot, 4)))
                    except Exception as ex:
                        ctx["mass_ok"][eid] = (None, False, "分子式不可独立解析(%s)" % ex)

            # e2 rationale 承载的**确定性语义载荷** vs 目标真量纲
            #    载荷有两个来源（同一族缺陷：一条理由被原样粘到互斥目标上）：
            #      a) 显式类声明「属X类物理量」（Phase9 LLM 精炼模板）
            #      b) **数值断言**「摩尔质量 N g/mol」（Phase9.B5 模板）—— 只能锚定质量类
            #    ⚠ 第 19 轮补 b)：只认 a) 会漏掉 20 条同类缺陷（rationale 写摩尔质量数值、
            #       目标却是 energy/ke/internal_energy），因为它们没写「属…类」四个字。
            label, want = class_dim_in_rationale(p.get("rationale"))
            if label is None and _MASS_IN_RATIONALE.search(str(p.get("rationale") or "")):
                label, want = "摩尔质量(数值断言)", CLASS_DIM["质量"]
            if label is not None:
                got = dm.dim_of(qname)
                ok = dim_matches_class(got, want)
                if ok is None:
                    ctx["hq_class_ok"][eid] = (None, label, "类/量纲不可判定")
                else:
                    ctx["hq_class_ok"][eid] = (ok, label, "声称 %s 类 vs 目标 %s=%s" % (label, canon_q, got))
                    if not ok:
                        ctx["bugs"]["has_quantity_class_mismatch"].append(
                            (src, tgt, label, canon_q))
    return ctx


# --------------------------------------------------------------------------- 主流程
def load_norm(path=NORM):
    d = json.load(open(path, encoding="utf-8"))
    return d["nodes"], d["edges"]


def classify_all(nodes, edges):
    ctx = build_ctx(nodes, edges)
    node_by_id = ctx["node_by_id"]
    out = []
    for e in edges:
        e = dict(e)
        p = dict(e.get("props") or {})
        tn = node_by_id.get(e["target"])
        p["_qname"] = qname_of_node(tn)
        p["_tname"] = ((tn or {}).get("props") or {}).get("name") or ""
        e["props"] = p
        r = classify(e, ctx)
        out.append((e, r))
    return out, ctx


def audit(path=NORM):
    nodes, edges = load_norm(path)
    res, ctx = classify_all(nodes, edges)
    print("=" * 92)
    print("verification_model 审计 —— %s" % os.path.relpath(path, ROOT))
    print("  规模 %d 节点 / %d 边" % (len(nodes), len(edges)))
    print("=" * 92)

    lv = collections.Counter(r["verification_level"] for _, r in res)
    print("\n【分层分布】")
    tot = sum(lv.values())
    for k in LEVELS:
        n = lv.get(k, 0)
        print("  %-16s %6d  %5.1f%%   %s" % (k, n, 100.0 * n / tot, LEVEL_DOC[k]))

    n_ver = sum(1 for _, r in res if is_verified(r["verification_level"]))
    n_str = sum(1 for _, r in res if is_strict(r["verification_level"]))
    old_ver = sum(1 for e in edges if (e.get("props") or {}).get("verified") is True)
    print("\n【口径对照】")
    print("  旧口径 verified=True（现状）      : %d" % old_ver)
    print("  新 verified（含 by_construction） : %d" % n_ver)
    print("  新 verified_strict（独立复核）    : %d   ← 北极星口径" % n_str)

    # ---- 通胀剥离 / 归因纠正（本轮核心口径） ----
    demoted = [(e, r) for e, r in res
               if (e.get("props") or {}).get("verified") is True
               and not is_verified(r["verification_level"])]
    misattr = [(e, r) for e, r in res
               if (e.get("props") or {}).get("verified") is True
               and is_verified(r["verification_level"])
               and (e.get("props") or {}).get("verification_gate")
               not in (None, "NONE")
               and not _gate_matches_scope((e.get("props") or {}).get("verification_gate"),
                                           r["verification_scope"])]
    newly = [(e, r) for e, r in res
             if (e.get("props") or {}).get("verified") is None
             and is_verified(r["verification_level"])]
    print("\n【通胀剥离 / 归因纠正】")
    print("  ① 旧自称已验证、实为模型预测（降级 model_inferred）: %d" % len(demoted))
    print("  ② 旧自称已验证、但门禁归因与实测范围不符（已改正）  : %d" % len(misattr))
    print("  ③ 旧无任何标记、本轮首次取得证据（新增正标签）      : %d" % len(newly))
    if demoted:
        cd = collections.Counter((e["type"], (e["props"] or {}).get("kind") or "-")
                                 for e, _ in demoted)
        for k, n in cd.most_common(8):
            print("      降级 %-24s | %-22s %6d" % (k[0], k[1], n))
    if misattr:
        cm = collections.Counter(((e["props"] or {}).get("verification_gate"),
                                  r["verification_scope"]) for e, r in misattr)
        for k, n in cm.most_common(8):
            print("      纠正 %-8s -> %-38s %6d" % (k[0], k[1], n))

    print("\n【旧 verified=True 的去向（前 20 组）】")
    c = collections.Counter()
    for e, r in res:
        if (e.get("props") or {}).get("verified") is True:
            p = e["props"]
            c[(e["type"], p.get("kind") or "-",
               (p.get("source") or "-")[:18], r["verification_level"])] += 1
    for k, n in c.most_common(20):
        print("  %-24s | %-20s | %-18s -> %-15s %6d" % (k[0], k[1], k[2], k[3], n))

    print("\n【独立复算发现的不一致（真实缺陷）】")
    for name, items in ctx["bugs"].items():
        print("  %s : %d 条" % (name, len(items)))
        for it in items[:8]:
            print("     -", it)
    if not ctx["bugs"]:
        print("  （无）")

    # 未分类边
    unc = [(e, r) for e, r in res if r["verification_scope"] == "unclassified"]
    print("\n【未分类边】%d 条" % len(unc))
    cc = collections.Counter((e["type"], (e["props"].get("source") or "-")) for e, _ in unc)
    for k, n in cc.most_common(15):
        print("  %-24s | %-24s %6d" % (k[0], k[1], n))

    print("\n【复算覆盖率】")
    for key in ("composed_ok", "period_ok", "family_ok", "dim_ok"):
        d = ctx[key]
        yes = sum(1 for v in d.values() if v[0] is True)
        no = sum(1 for v in d.values() if v[0] is False)
        na = sum(1 for v in d.values() if v[0] is None)
        print("  %-14s 通过 %5d / 不一致 %4d / 不可判定 %5d" % (key, yes, no, na))
    d = ctx["mass_ok"]
    yes = sum(1 for v in d.values() if v[0] is True)
    no = sum(1 for v in d.values() if v[0] is False)
    na = sum(1 for v in d.values() if v[0] is None)
    cr = sum(1 for v in d.values() if v[0] is True and v[1])
    print("  %-14s 通过 %5d / 不一致 %4d / 不可判定 %5d   （其中与 PubChem 亦一致 → cross_source %d）"
          % ("mass_ok", yes, no, na, cr))
    d = ctx["hq_class_ok"]
    yes = sum(1 for v in d.values() if v[0] is True)
    no = sum(1 for v in d.values() if v[0] is False)
    na = sum(1 for v in d.values() if v[0] is None)
    print("  %-14s 自洽 %5d / 自相矛盾 %4d / 不可判定 %5d" % ("hq_class_ok", yes, no, na))
    print("=" * 92)
    return res, ctx


def main():
    ap = argparse.ArgumentParser(description="边级可信性分层模型 / 审计器")
    ap.add_argument("--audit", action="store_true", help="只读审计")
    ap.add_argument("--json", default=None, help="审计结果落盘路径")
    a = ap.parse_args()
    if not a.audit:
        ap.print_help()
        return 0
    res, ctx = audit()
    if a.json:
        payload = {
            "levels": LEVELS,
            "level_doc": LEVEL_DOC,
            "distribution": dict(collections.Counter(
                r["verification_level"] for _, r in res)),
            "bugs": {k: v for k, v in ctx["bugs"].items()},
        }
        json.dump(payload, open(a.json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print("审计结果 ->", a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
