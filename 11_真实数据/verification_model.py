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
import math
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


# ------------------------------------------------------- Claim/Evidence 一等对象（Phase 32）
# 背景（第 21 轮遗留⑤）：`verification_level/scope/verifier` 已 100% 覆盖，但那是**标签**
#   —— 「谁验的 / 怎么验的」的**名字**，不是**证据数据**。实测 `evidence` 仅 1.18%（575 条，
#   且 400 条是字符串、175 条是 list，形态不统一）；`created_at` 仅 1.9%。
# 本轮把「断言 + 证据链」做成**一等对象**，逐条可溯源：
#     claim                —— 断言的可读陈述（人可读，机器以 (source,type,target) 为准）
#     verification_evidence —— 证据链（list of {kind, impl, detail, indep}）
#     evidence_at          —— 证据生成时刻
#
# ★ 关键设计：`indep`（是否**独立于提出者**）是北极星「证据可追溯」的判据 ——
#     A 段确定性复算 / 第二独立源 → `True`；来源自述 / 模型预测 / 构造律 → `False`。
#     不变量：level ≥ rule_checked ⟺ ≥1 条 `indep=True` 的证据（见 frozen_gate `evidence_traceable`）。
#
# ★ 铁律 #36（同源）：本表**由 scope 唯一决定** kind/indep，而 scope 由 `classify` 唯一给出，
#    ⇒ 证据的「档位语义」与判级模型**构造上不可能分歧**。
EVIDENCE_KINDS = [
    "recompute",         # 与提出者无关的确定性复算（A 段）
    "cross_source",      # ≥2 独立来源一致
    "source_assertion",  # 单一来源 / 策划直接断言（未独立复核）
    "model_prediction",  # GNN / LLM 推断
    "construction",      # 权威表 / 定义构造性产生
    "rebuttal",          # 判否：记录推翻该断言的判据
]

# scope -> (kind, indep)。**未列出的 scope** 按 level 兜底（见 _fallback_kind）。
SCOPE_KIND = {
    # ---- A 段：确定性复算（与提出者无关）→ recompute / indep=True ----
    "dimensional_strict_equal": ("recompute", True),
    "dimensional_mismatch": ("rebuttal", False),
    "period_authority_equal": ("recompute", True),
    "period_mismatch": ("rebuttal", False),
    "family_authority_equal": ("recompute", True),
    "family_mismatch": ("rebuttal", False),
    "formula_count_independent_recheck": ("recompute", True),
    "formula_count_node_recheck": ("recompute", True),
    "formula_count_mismatch": ("rebuttal", False),
    "formula_participation_membership": ("recompute", True),
    "formula_participation_not_found": ("rebuttal", False),
    "molar_mass_independent_recompute": ("recompute", True),
    "molar_mass_mismatch": ("rebuttal", False),
    "rationale_target_mismatch": ("rebuttal", False),
    "unit_dimension_recompute": ("recompute", True),
    "unit_dimension_mismatch": ("rebuttal", False),
    "webbook_unit_dimension_recompute": ("recompute", True),
    "webbook_unit_dimension_mismatch": ("rebuttal", False),
    "symbol_expr_recompute": ("recompute", True),
    "semantic_target_absent": ("rebuttal", False),
    "log_rule_usage": ("recompute", True),
    "exp_base_e_usage": ("recompute", True),
    "pi_constant_usage": ("recompute", True),
    "trig_identity_usage": ("recompute", True),
    # ---- Phase 33：常量定义式数值复算 + 反应侧别跨源校验 ----
    "codata_definition_recompute": ("recompute", True),
    "codata_definition_mismatch": ("rebuttal", False),
    # ---- 跨源一致 → cross_source / indep=True ----
    "formula_count_cross_source": ("cross_source", True),
    "molar_mass_cross_source": ("cross_source", True),
    "webbook_multi_reference_agreement": ("cross_source", True),
    "inchikey_exact_match": ("cross_source", True),
    "inchikey_cross_source": ("cross_source", True),
    "multi_source_alignment": ("cross_source", True),
    "equation_species_cross_source": ("cross_source", True),
    "equation_species_mismatch": ("rebuttal", False),
    # ---- by_construction：构造性产生（无判断空间）→ construction / indep=False ----
    "symbol_scan_from_text": ("construction", False),
    "formula_only": ("construction", False),
    "schema_axiom": ("construction", False),
    # ---- 模型产物 → model_prediction / indep=False ----
    "model_link_prediction": ("model_prediction", False),
    "latex_normalized_only": ("model_prediction", False),
    # ---- 来源自述 → source_assertion / indep=False ----
    "source_assertion": ("source_assertion", False),
    "manual_curation": ("source_assertion", False),
    "equation_sidedness": ("source_assertion", False),
    "published_descriptor_value": ("source_assertion", False),
    "bibliographic_metadata": ("source_assertion", False),
    "codata_derivation": ("source_assertion", False),
    "codata_unit": ("source_assertion", False),
    "name_exact_match": ("source_assertion", False),
    "composition_source": ("source_assertion", False),
    # ---- 未分类 ----
    "unclassified": ("rebuttal", False),
}


def _fallback_kind(level: str) -> "tuple[str, bool]":
    """未登记 scope 的兜底（按 level 定 kind）—— 保证证据**永不缺 kind**。"""
    if level == "human_reviewed":
        return ("recompute", True)
    if level == "cross_source":
        return ("cross_source", True)
    if level == "rule_checked":
        return ("recompute", True)
    if level == "by_construction":
        return ("construction", False)
    if level == "model_inferred":
        return ("model_prediction", False)
    if level == "source_asserted":
        return ("source_assertion", False)
    return ("rebuttal", False)


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


# ============================================================================ #
# Phase 30（第 20 轮）：语义边的**确定性反驳 / 复算**
# ============================================================================ #
# 单位符号 → SI 量纲向量。**本模块自带**的 SI 定义表（另在 `semantic_noise_audit.py`
# 有一份独立实现，互为对照，铁律 #14）。判据只依赖单位符号本身这一**外部事实**。
_UNIT_SI = {
    "kg": {"M": 1.0},
    "m": {"L": 1.0},
    "s": {"T": 1.0},
    "j": {"M": 1.0, "L": 2.0, "T": -2.0},
    "m/s": {"L": 1.0, "T": -1.0},
    "m/s^2": {"L": 1.0, "T": -2.0},
    "n": {"M": 1.0, "L": 1.0, "T": -2.0},
    "w": {"M": 1.0, "L": 2.0, "T": -3.0},
    "pa": {"M": 1.0, "L": -1.0, "T": -2.0},
    "k": {"Th": 1.0},
    "m^3": {"L": 3.0},
    "mol": {"N": 1.0},
    "j/k": {"M": 1.0, "L": 2.0, "T": -2.0, "Th": -1.0},
    "j/(mol*k)": {"M": 1.0, "L": 2.0, "T": -2.0, "N": -1.0, "Th": -1.0},
    "c": {"I": 1.0, "T": 1.0},
    "v": {"M": 1.0, "L": 2.0, "T": -3.0, "I": -1.0},
    "a": {"I": 1.0},
    "ω": {"M": 1.0, "L": 2.0, "T": -3.0, "I": -2.0},
    "ohm": {"M": 1.0, "L": 2.0, "T": -3.0, "I": -2.0},
    "f": {"M": -1.0, "L": -2.0, "T": 4.0, "I": 2.0},
    "t": {"M": 1.0, "T": -2.0, "I": -1.0},
    "h": {"M": 1.0, "L": 2.0, "T": -2.0, "I": -2.0},
    "wb": {"M": 1.0, "L": 2.0, "T": -2.0, "I": -1.0},
    "hz": {"T": -1.0},
    "ev": {"M": 1.0, "L": 2.0, "T": -2.0},
    "m^2": {"L": 2.0},
    # ⚠ 本项目把「角度 A」当基本量（见 dimension_table.DIM），故 radian = {A:1}；
    #   若照 SI 把 rad 当无量纲，会把合法的 `角度 --has_unit--> radian` 误撤。
    "rad": {"A": 1.0},
    "mol/l": {"N": 1.0, "L": -3.0},
}


def unit_dim_of_symbol(sym):
    """单位符号串 → 量纲向量；不可识别返回 None（**不猜**）。"""
    if not sym:
        return None
    s = str(sym).strip().lower().replace(" ", "").replace("·", "*")
    if s in _UNIT_SI:
        return dict(_UNIT_SI[s])
    s2 = s.replace("(", "").replace(")", "")
    return dict(_UNIT_SI[s2]) if s2 in _UNIT_SI else None


# ---------------------------------- 化学反应侧别「跨源」校验（Phase 33 · A12）
# 命题（第 23 轮）：T4「反应物/生成物归属」长期停在 `source_asserted`（Rhea/ChEBI 自述）。
#   朴素解法「重解析方程」是**同源自证**——适配器正是由 `equation` 串推出 `n_left` 再切
#   `chebi-id` 列（`rhea_ingest.py` 第 355~378 行），重解一遍只是复述（铁律 #14）。
#   真·独立路径：**Rhea 的 `Equation` 列（物种文本） × ChEBI 本体的 `label`/`formula`**
#   —— 两个**不同数据库**。用后者校验前者给出的侧别：参与物只应出现在**归属侧**。
# ★ 铁律 #14：判据只读**方程串**与**参与物属性**，绝不读已存 `verification_level`。
_RXN_PLUS = re.compile(r" \+ ")           # ⚠ 必须要求两侧空白：否则 `NAD(+)` 的电荷号会被误切
_RXN_PAREN = re.compile(r"\([^)]*\)")

# ---- ChEBI 本体「同义词」缓存（Phase 34 · A14）----------------------------------
# 经 ChEBI **OLS4** 拉取（`11_真实数据/chebi_synonym_fetch.py`），与 Rhea 是**不同数据库**。
# 赋予 A12 一个更宽的 ChEBI 名称集：Rhea 方程写 `pentanoate`，而 ChEBI 主名是 `valerate`
# —— 二者**同物异名**。仍属**跨源**（Rhea 方程串 × ChEBI 本体），与提出者无关（铁律 #26/#45）。
# ⚠ 缓存缺失 → 同义词通道自动关闭（退化为 Phase 33 的 label/formula 口径，**不降级任何已判边**）。
_SYN_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "chebi_synonym_cache.json")
try:
    _CHEBI_SYN = json.load(open(_SYN_CACHE_PATH, encoding="utf-8")) \
        if os.path.exists(_SYN_CACHE_PATH) else {}
except Exception:                                             # noqa: BLE001
    _CHEBI_SYN = {}
# 归一后长度下限：挡掉**单字母同义词**（如 `L-histidine residue` 的同义词 `H` → canon `h`
# 会误命中方程里的 `H(+)` —— 实测这是唯一的假阳来源；铁律 #31：收紧判据须配正对照）。
_SYN_MIN_LEN = 3


def chebi_alias_names(part_props) -> list:
    """参与物的 ChEBI **同义词集**（归一后）；缺失/未成功则返回空表。"""
    cid = (part_props or {}).get("chebi_id")
    rec = _CHEBI_SYN.get(cid) if cid else None
    if not (rec and rec.get("ok")):
        return []
    lab = _canon_species((part_props or {}).get("name") or "")
    out = []
    for s in rec.get("synonyms") or []:
        c = _canon_species(s)
        if c and len(c) >= _SYN_MIN_LEN and c != lab and c not in out:
            out.append(c)
    return out


def _canon_species(x) -> str:
    """物种串归一：小写、去括注 `(in)/(out)/(+)/(n)`、去空格与正负号。"""
    s = str(x).lower()
    s = _RXN_PAREN.sub("", s)
    return s.replace(" ", "").replace("-", "").replace("+", "")


def split_equation(eq):
    """把 `A + B = C + D` 切成 (左物种[], 右物种[])；无 ` = ` 返回 (None, None)。"""
    if not eq or " = " not in eq:
        return None, None
    lhs, rhs = str(eq).split(" = ", 1)
    f = lambda s: [x.strip() for x in _RXN_PLUS.split(s) if x.strip()]
    return f(lhs), f(rhs)


def reaction_side_cross_check(part_node, rxn_node, is_reactant: bool):
    """参与物（ChEBI）在反应方程（Rhea）中的侧别是否与边一致。

    判据强度分层（**优先保证 Phase 33 口径零降级**：label → formula → synonym）：
      ok=True  —— 主名 `label`（强，Phase 33）/ 式 `formula`（中，Phase 33）/
                  **同义词 `synonym`（弱，Phase 34 · ChEBI 本体）** 只出现在**归属侧**；
      ok=False —— **主名 / 式**只出现在**相反侧**（确定性矛盾）；
      ok=None  —— 不可判定（无方程 / 两侧都出现 / 同侧重复 / 泛称无法匹配）→ **不升档**。
    ⚠ 同义词**只用于升档、不用于反驳**（宁缺勿滥；铁律 #31）。
    ⚠ 同义词通道在 `label`/`formula` 均未命中时才启用 ⇒ **绝不改变 Phase 33 已升的 2773 条**。
    """
    rp = (rxn_node or {}).get("props") or {}
    lhs, rhs = split_equation(rp.get("equation"))
    if lhs is None:
        return None, "", "反应节点无方程串"
    pp = (part_node or {}).get("props") or {}
    lab = _canon_species(pp.get("name") or "")
    fml = _canon_species(pp.get("formula") or "")
    L = [_canon_species(x) for x in lhs]
    R = [_canon_species(x) for x in rhs]
    cs, os_ = (L, R) if is_reactant else (R, L)
    side = "反应物（左）" if is_reactant else "生成物（右）"
    lh, lw = bool(lab) and lab in cs, bool(lab) and lab in os_
    if lh and lw:
        return None, "", "label「%s」两侧都出现（同物异名/催化剂，歧义）" % pp.get("name")
    if lw and not lh:
        return False, "", "label「%s」只出现在相反侧（与「%s」矛盾）" % (pp.get("name"), side)
    if lh:
        return True, "label", "label「%s」仅在%s出现；相反侧无" % (pp.get("name"), side)
    fh, fw = bool(fml) and fml in cs, bool(fml) and fml in os_
    if fh and fw:
        return None, "", "formula「%s」两侧都出现（歧义）" % pp.get("formula")
    if fw:
        return False, "", "formula「%s」只出现在相反侧（与「%s」矛盾）" % (pp.get("formula"), side)
    if fh:
        if cs.count(fml) > 1:      # 同侧同式多物种 → 可能指代他物（宁缺勿滥）
            return None, "", "formula「%s」在归属侧重复出现（可能指代他物，歧义）" % pp.get("formula")
        return True, "formula", "formula「%s」仅在%s出现（label 未命中）" % (pp.get("formula"), side)
    # ---- Phase 34 · A14：ChEBI 同义词通道（label/formula 均未命中时启用）----
    syns = chebi_alias_names(pp)
    if syns:
        sh = [s for s in syns if s in cs]
        sw = [s for s in syns if s in os_]
        if sh and not sw:
            if cs.count(sh[0]) > 1:    # 同侧重复 → 可能指代他物（歧义）
                return None, "", "同义词「%s」在归属侧重复出现（可能指代他物，歧义）" % sh[0]
            return True, "synonym", "ChEBI 同义词「%s」仅在%s出现；相反侧无" % (sh[0], side)
        if sh and sw:
            return None, "", "同义词「%s」两侧都出现（歧义）" % sh[0]
        if sw:
            # 保守：同义词只在相反侧**不据此反驳**（同义词集较宽，易假阳；铁律 #31）
            return None, "", "同义词「%s」只出现在相反侧（保守：不据此反驳）" % sw[0]
    return None, "", "label/formula/ChEBI 同义词 均未在方程中出现（泛称，无法匹配）"


# ---------------------------------- CODATA 常量定义式「数值复算」（Phase 33 · A13）
# 命题：`derived_from (kind=constant_derivation)` 断言「常量 A 由定义式给出（输入 B）」
#   —— 定义式与常量值均为**外部事实**（SI 定义 / CODATA），与「谁提的边」无关。
#   把被派生常量的**记录值**与「读输入常量值代入定义式」的独立复算比对（铁律 #26）。
# ★ 铁律 #14：只读节点 `value` 与 `definition` 串，绝不读已存判级。
# `M_u`（摩尔质量常数）= 1 g/mol = 1e-3 kg/mol（SI 定义值，非图上节点，故内联）。
_CODATA_DEFS = {
    "R = N_A·k_B": (["CO:pq:avogadro", "SM:pq:boltz_const"],
                    lambda v: v[0] * v[1]),
    "F = N_A·e": (["CO:pq:avogadro", "CO:pq:elementary_charge"],
                  lambda v: v[0] * v[1]),
    "m_u = M_u/N_A": (["CO:pq:avogadro"],
                      lambda v: 1e-3 / v[0]),
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
_CODATA_REL_TOL = 1e-6


def codata_derivation_recheck(node_by_id, src_id, defn):
    """返回 `(ok, why)`：定义式未登记 / 输入缺值 → `(None, ...)`（不判定，不猜）。"""
    spec = _CODATA_DEFS.get(defn)
    if spec is None:
        return None, "定义式未登记：%s" % defn
    ins, fn = spec
    vals = []
    for i in ins:
        v = ((node_by_id.get(i) or {}).get("props") or {}).get("value")
        if v is None:
            return None, "输入常量缺值：%s" % i
        vals.append(float(v))
    rec = ((node_by_id.get(src_id) or {}).get("props") or {}).get("value")
    if rec is None:
        return None, "被派生常量缺值"
    try:
        calc = fn(vals)
    except Exception as ex:                                   # noqa: BLE001
        return None, "定义式求值异常(%s)" % ex
    rel = abs(calc - float(rec)) / max(abs(float(rec)), 1e-30)
    ok = rel <= _CODATA_REL_TOL
    return ok, "%s ⇒ 复算 %.10g vs 记录 %.10g（rel %.1e）" % (defn, calc, float(rec), rel)


# ---- T4 残差「不可独立复算理由」分类（Phase 34 · A14 的配套清算）---------------
# 凡 `reactant_of`/`product_of` 且**非** cross_source/mismatch 的边，必须能归入一个
# **显式理由** —— 既是「负结果也是交付物」（铁律 #34），也落地「不可判定 ≠ 可以放过」（铁律 #30）。
# ★ 门禁不变量 `residual_accounted` 调用**本函数**（单一口径，铁律 #36）；
#   独立审计器 R12 用**自带实现**重算同一口径（铁律 #43：审计器必须允许它报错）。
RESIDUAL_REASONS = (
    "same_source_elementkg",            # ElementKG2.0：无方程串，SMILES 亦同源 ⇒ 无独立源
    "same_source_curated",              # curated_seed：方程与图节点皆项目策划 ⇒ 同源
    "generic_class",                    # Rhea 泛称类参与物（无具体对应物）
    "polymer_residue",                  # Rhea 聚合物 / 残基占位
    "placeholder_complex",              # Rhea 方程含 `[占位复合物]` ⇒ 不可判定
    "naming_variant_no_second_source",  # 命名/质子化变体，且 ChEBI 同义词集亦未覆盖
)


def residual_reason(edge, node_by_id) -> str:
    """T4 残差边「不可独立复算」理由码；非 T4 边返回 `""`；未归类返回 `"UNCLASSIFIED"`。"""
    if edge.get("type") not in ("reactant_of", "product_of"):
        return ""
    pp = (node_by_id.get(edge.get("source")) or {}).get("props") or {}
    rp = (node_by_id.get(edge.get("target")) or {}).get("props") or {}
    rsrc = rp.get("source") or "-"
    if rsrc == "ElementKG2.0":
        return "same_source_elementkg"
    if str(rsrc).startswith("curated_seed"):
        return "same_source_curated"
    if rsrc == "Rhea":
        if pp.get("is_generic"):
            return "generic_class"
        if pp.get("is_polymer"):
            return "polymer_residue"
        if rp.get("equation") and "[" in str(rp.get("equation")):
            return "placeholder_complex"
        return "naming_variant_no_second_source"
    return "UNCLASSIFIED"


# ---- 数学算子存在性（数学桥的确定性证据）----
# 每条：scope 名 -> (判据正则, 允许的目标数学对象集合)。
# 「公式 F 使用了数学工具 M」当且仅当 **F 的表达式中出现了 M 所辖的算子/常量** ——
# 与「谁提的」无关，可复算。
MATH_OP_RULES = {
    # ⚠ 只收「算子与目标**一一对应**」的规则。第 20 轮主动**删除**了两条不够严的规则：
    #   · `\sum` → Taylor/二项式：**泛指求和 ≠ 级数展开**（`Z=\sum_i e^{-E_i/kT}` 是配分函数，
    #     与 Taylor 无关）—— 会把合法公式错链到错误数学对象。
    #   · `\nabla` → `MX:math:expand`：语义错位（微分算子 ≠ 展开）。
    #   宁缺勿滥：宁可少建桥，也不建**语义错**的桥。
    "log_rule_usage": (r"\\log|\\ln|\\lg", {"MA:fo:log_product"}),
    "exp_base_e_usage": (r"e\^\{|\\exp", {"MA:sy:e", "MA:pq:euler_e"}),
    "pi_constant_usage": (r"\\pi|π", {"MA:sy:pi"}),
    "trig_identity_usage": (r"\\sin|\\cos|\\tan", {"MX:math:trig_unit"}),
    "differential_rule_usage": (r"\\frac\{d\}|d/dx",
                                {"MX:math:derivative_power", "MX:math:power_rule"}),
}
_MATH_OP_RE = {k: re.compile(v[0]) for k, v in MATH_OP_RULES.items()}


def math_op_of(text):
    """返回表达式文本命中的数学算子 scope 列表（可能多个）。"""
    if not text:
        return []
    return [k for k, rx in _MATH_OP_RE.items() if rx.search(str(text))]


def expr_of_node(node) -> str:
    """节点的「表达式」文本（latex / formula）—— 数学桥与符号复算的判据来源。"""
    p = (node or {}).get("props") or {}
    parts = [str(p[k]) for k in ("latex", "formula") if p.get(k)]
    if not parts:
        # 铁律 #29 的同类回退：**字段缺失 ≠ 不可用** —— 无公式字段时，名称本身可能就是
        #   化学式（`MX:chem:co2` 的 name=`CO₂`）。`formula_from_name` 是**保守**回退
        #   （下标归一后须能被独立解析器接受才认，自然语言名一律不认）。
        fn = formula_from_name(p.get("name"))
        if fn:
            parts.append(fn)
    return " ".join(parts)


def declared_symbols(node):
    """节点自带的 `symbols` 声明（部分 MathML 节点会显式列出成员符号）。"""
    d = ((node or {}).get("props") or {}).get("symbols")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except Exception:                                # noqa: BLE001
            d = [d]
    return [str(x) for x in (d or [])]


def target_symbol(node):
    """目标实体的符号：`symbol` → `latex` → 名称首段（`T (temperature)` → `T`）。

    Phase 31 增补：正则取不出时（名称以 `(` 起头，如 `MX:math:binomial` 的 name=`(a+b)²`）
    **回退为整名** —— 这类节点的 `name` 本身就是其表达式，弃之会令复算**静默退化为不可判定**
    （CO₂ 的一条假桥正是这样逃掉的）。
    """
    p = (node or {}).get("props") or {}
    for k in ("symbol", "latex"):
        if p.get(k):
            return str(p[k]).strip()
    nm = str(p.get("name") or "").strip()
    m = re.match(r"^([^\s(]+)", nm)
    if m:
        return m.group(1)
    return nm or None


_LATEX_CMD = re.compile(r"\\([A-Za-z]+)")
_STRIP = re.compile(r"[\\{}$\s]+")
# Phase 31：`\mathrm{pH}` / `\text{...}` 等**包装类命令**要先剥壳取内容，否则
#   `\mathrm{pH}` → `mathrmpH` 会与源式里的 `pH` **对不上**（实测 `CE:fo:ph` 等 3 条假 MISS）。
_WRAP_CMD = re.compile(r"\\(?:mathrm|text|operatorname|mbox|textrm)\s*\{([^{}]*)\}")


def _norm_latex(s):
    s = _WRAP_CMD.sub(r"\1", str(s))
    return _STRIP.sub("", _LATEX_CMD.sub(r"\1", s))


def symbol_in_source(src_node, sym):
    """目标符号是否出现于源对象的**表达式/声明**中（宽松；不可判定返回 None）。

    ⚠ 宽松判据只会「多判为出现」（不撤），绝不「少判为出现」（错撤）——
      因此由 False 得出的**反驳是可靠的**（sound），代价是漏掉同名碰撞（如 `V=IR` 的 R）。
    """
    if not sym:
        return None
    text = expr_of_node(src_node)
    if not text:
        return None
    s = str(sym).strip()
    cands = [s]
    m = re.match(r"^(.+?)_\{?(.+?)\}?$", s)
    if m:
        cands.append(m.group(1))
    if any(c in declared_symbols(src_node) for c in cands):
        return True
    t = _norm_latex(text)
    return any(c and _norm_latex(c) in t for c in cands)


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

    def R(level, scope, verifier, note=None, detail=None):
        """构造判级结果。

        `note`   —— 判否/存疑时的**理由**（历史字段，保持向后兼容）。
        `detail` —— **证据明细**（Phase 32）：无论判过还是判否，都记录**被判定的那个事实**
                    （如「独立解析 CH4 中 C=1 vs 记录 1」）。它是 Claim/Evidence 一等对象的
                    载荷；`build_evidence` 直接取用，**不重新推导**（铁律 #36：同源）。
        """
        out = {"verification_level": level, "verification_scope": scope,
               "verifier": verifier}
        if note:
            out["note"] = note
        if detail:
            out["detail"] = detail
        return out

    # ================= A. 确定性复算（与提出者无关） =================
    # A1 量—量量纲一致
    if t == "dimensionally_consistent":
        if kind == "dimension_table":
            return R("rule_checked", "dimensional_strict_equal", "dimension_table.py",
                     detail="量纲真值表直接给出（构造性）")
        ok, why = ctx["dim_ok"].get(eid, (None, ""))
        if ok is True:
            return R("rule_checked", "dimensional_strict_equal",
                     "verification_model.dimension_table",
                     detail="独立复算量纲一致：%s" % why)
        if ok is False:
            return R("unverified", "dimensional_mismatch",
                     "verification_model.dimension_table",
                     "独立复算量纲不一致：%s" % why, detail=why)

    # A2 周期表位置（元素—元素）
    if t in ("same_period", "same_family"):
        key = "period_ok" if t == "same_period" else "family_ok"
        ok, why = ctx[key].get(eid, (None, ""))
        if ok is True:
            return R("rule_checked", "%s_authority_equal" % t.split("_")[1],
                     "element_reference.py",
                     detail="权威表比对一致：%s" % why)
        if ok is False:
            return R("unverified", "%s_mismatch" % t.split("_")[1],
                     "element_reference.py", why, detail=why)

    # A3 分子组成：独立解析分子式复算 count
    #   关键区分：**式串的来源**决定可信上限（避免把「模型自述的分子式」洗成已验证）
    #   · 节点带外部权威式（PubChem）且计数一致 → cross_source
    #   · 边带 `from_formula`（源侧适配器写入，如 ChEBI/PubChem/策划种子）且计数一致 → rule_checked
    #   · 仅有模型侧 rationale 中的式串 → 内部一致但**无外部锚**，仍记 model_inferred
    if t == "composed_of":
        aok, auth, auth_field = ctx["composed_auth_ok"].get(eid, (None, None, None))
        ok, why, via = ctx["composed_ok"].get(eid, (None, "", None))
        # 第二源必须**真的独立**：节点权威式为 `pubchem_formula`（PubChem），
        # 且边自身没有取自同一 PubChem 的 `from_formula`
        if aok is True and auth_field == "pubchem_formula" \
                and not (p.get("from_formula") and p.get("source") == "PubChem"):
            return R("cross_source", "formula_count_cross_source",
                     "PubChem(pubchem_formula) × 图内组成边",
                     detail="%s；并与节点权威式 pubchem_formula=%s 复核一致" % (why, auth))
        if ok is True:
            # Phase 31：证据起点不同 → 单列 scope（不作 cross_source，也不冒充「边自带式」）
            if via == "node_formula":
                return R("rule_checked", "formula_count_node_recheck",
                         "verification_model.parse_formula_independent(源节点 formula)",
                         detail=why)
            return R("rule_checked", "formula_count_independent_recheck",
                     "verification_model.parse_formula_independent",
                     detail=why)
        if ok is False:
            return R("unverified", "formula_count_mismatch",
                     "verification_model.parse_formula_independent", why, detail=why)
        if kind == "pubchem_composition":
            return R("cross_source", "inchikey_cross_source", "PubChem/curated",
                     detail="PubChem index 命中（inchikey 精确匹配）")

    # A4 公式—参与量（PhysicsBabel has_symbol）
    if t == "has_symbol" and src == "PhysicsBabel" and kind == "real":
        pbkey = rationale_key(p.get("rationale"))
        cand = [pbkey] if pbkey else [p.get("_tname") or "", p.get("_qname") or ""]
        for cc in cand:
            if cc and (e.get("source"), dm.canon(cc).lower()) in ctx["formula_members"]:
                return R("rule_checked", "formula_participation_membership",
                         "verification_model.formula_members",
                         detail="%s ∈ dim_exponents(%s)（独立读公式的齐次系数表）"
                                % (cc, p.get("rationale") or e.get("source")))
        return R("unverified", "formula_participation_not_found",
                 "verification_model.formula_members",
                 "该量不在公式 exponents 中且与记录键不符（陈旧错挂，待清理）",
                 detail="候选键 %s 均未出现在源公式 dim_exponents 中" % (cand,))

    # A5 跨域桥 · 分子 → 摩尔质量物理量（Phase9.B5 / Phase13.Gate.B5）
    #    证据 = 「元素原子量 × 独立解析的化学式计数」复算 == 记录值（与提出者无关）
    if t == "has_quantity" and ctx["mass_ok"].get(eid) is not None:
        ok, cross, why = ctx["mass_ok"][eid]
        if ok is True and cross:
            return R("cross_source", "molar_mass_cross_source",
                     "element_reference.py × PubChem(pubchem_molecular_weight)", why, detail=why)
        if ok is True:
            return R("rule_checked", "molar_mass_independent_recompute",
                     "verification_model.parse_formula_independent × element_reference.py",
                     why, detail=why)
        if ok is False:
            return R("unverified", "molar_mass_mismatch",
                     "verification_model.parse_formula_independent × element_reference.py",
                     why, detail=why)

    # A6 跨域桥 · rationale 声称的「类」与目标物理量真量纲自洽性
    #    **一条理由被粘贴到多个互斥目标**（质量 + 动能 + 内能…）→ 自相矛盾 → 不成立
    if t == "has_quantity" and ctx["hq_class_ok"].get(eid) is not None:
        ok, label, why = ctx["hq_class_ok"][eid]
        if ok is False:
            return R("unverified", "rationale_target_mismatch",
                     "verification_model.dimension_table",
                     "理由声称「%s类」，与目标物理量真量纲不符：%s" % (label, why),
                     detail="理由声称「%s类」vs 目标真量纲 %s" % (label, why))
        # ⚠ ok is True 时**不新增升档**（本轮只做对象化，不改判级口径）；
        #   该情形继续落 B 段按来源定级 —— 其证据仍由 build_evidence 以「来源自述」记录。

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
                     % (p.get("unit"), _drop(want), qn, _drop(got)),
                     detail="单位 `%s`→%s vs 目标 %s→%s"
                            % (p.get("unit"), _drop(want), qn, _drop(got)))
        else:
            nref = int(p.get("n_references") or 0)
            if nref >= 2:
                return R("cross_source", "webbook_multi_reference_agreement",
                         "NIST WebBook(≥2 独立文献) × dimension_table",
                         "单位量纲独立复算通过；%d 条独立文献吻合" % nref,
                         detail="单位 `%s`→%s == 目标 %s→%s；%d 条独立文献吻合"
                                % (p.get("unit"), _drop(want), qn, _drop(got), nref))
            return R("rule_checked", "webbook_unit_dimension_recompute",
                     "NIST WebBook × dimension_table(单位量纲)",
                     "单位 `%s` 经量纲表独立复算一致" % p.get("unit"),
                     detail="单位 `%s`→%s == 目标 %s→%s"
                            % (p.get("unit"), _drop(want), qn, _drop(got)))

    # A8 单位—量纲（Phase 30）：`has_unit` 的确定性复算
    #     「量 Q 有单位 U」⟺ dim(Q) == dim(U)。两者都是外部事实，可复算。
    if t == "has_unit":
        ok, why = ctx["unit_ok"].get(eid, (None, ""))
        if ok is True:
            return R("rule_checked", "unit_dimension_recompute",
                     "dimension_table(量纲) × 单位符号解析", why,
                     detail="量纲复算一致：%s" % why)
        if ok is False:
            return R("unverified", "unit_dimension_mismatch",
                     "dimension_table(量纲) × 单位符号解析",
                     "单位量纲与物理量真量纲不符：%s" % why,
                     detail="量纲不符：%s" % why)

    # A9 数学桥（Phase 30）：源公式**含**目标数学对象所辖的算子 → 确定性证据
    #     例：`pH=-\\log_{10}[H^+]` 含对数算子 → 与对数律 `MA:fo:log_product` 的桥成立。
    if t in ("derived_from", "has_symbol") and ctx["math_ok"].get(eid, (None,))[0] is True:
        _ok, _scope, _why = ctx["math_ok"][eid]
        return R("rule_checked", _scope, "verification_model.math_operator_presence", _why,
                 detail=_why)

    # A10 公式—符号（Phase 31）：**目标符号字面出现于源的结构化表达式** → 确定性复算。
    #     把 A4（只覆盖 PhysicsBabel 公式）推广到**任意来源**——由 A4 扩为通用规则后，
    #     人工策划的 `Mass-energy equivalence --has_symbol--> E`（latex 含 `E=mc^2`）等
    #     不再停在 `source_asserted`（「被信任」），而是 `rule_checked`（「被验证」）。
    #     ⚠ **只升不撤**：`symbol_in_source` 判否（False）**不足以反驳** —— 实测 22 条判否
    #       全部是**记号变体**（`Q` vs latex 里的 `q_1`、`ε` vs `\\mathcal{E}`、`u` vs `d_o`、
    #       `[\\,]` 括号记号），**并非错误**。用它撤边会误伤（铁律 #31：收紧判据须配正对照）。
    if t == "has_symbol" and ctx["sym_expr_ok"].get(eid, (None,))[0] is True:
        return R("rule_checked", "symbol_expr_recompute",
                 "verification_model.symbol_in_source",
                 "目标符号 %s 出现于源公式的结构化表达式（latex/formula/symbols）"
                 % ctx["sym_expr_ok"][eid][1],
                 detail="%s ∈ 源结构化表达式（latex/formula/symbols）"
                        % ctx["sym_expr_ok"][eid][1])

    # A11 模型产物语义边的「目标缺席」反驳（Phase 31）：**仅模型产物** —— 目标标识在源的
    #     **结构化表达式**里毫无支撑 → 撤。适用于 `defines`/`has_symbol`/`derived_from`。
    #     闸门：`ctx["derived_support_ok"]` **只为**模型产物建档 ⇒ 85 条合法 `derived_from`
    #       与全部人工策划 `has_symbol`/`defines` **查不到条目 ⇒ 不可能被误撤**。
    #     判据只读 latex/formula/symbols（**不读散文**，铁律 #31）。
    #     与门禁 `semantic_target_present` **共用同一条谓词**（同一函数 `symbol_in_source`），
    #       故「模型判级」与「门禁验收」在构造上不可能分歧。
    #     注：判「有支撑」**不升档** —— 同式共现 ≠ 派生关系（宁缺勿滥）。
    if t in ("defines", "has_symbol", "derived_from") \
            and ctx["derived_support_ok"].get(eid, (None,))[0] is False:
        return R("unverified", "semantic_target_absent",
                 "verification_model.symbol_in_source",
                 "模型断言的目标在源的结构化表达式中无支撑（%s）"
                 % ctx["derived_support_ok"][eid][1],
                 detail="目标标识 %s 不在源结构化表达式中（模型产物，可确定性反驳）"
                        % ctx["derived_support_ok"][eid][1])

    # A12 化学反应侧别**跨源**校验（Phase 33）：Rhea 方程 × ChEBI label/formula。
    #     判据只读方程串与参与物属性（铁律 #14），两源独立（Rhea vs ChEBI 本体）。
    #     ⚠ 三态：**只出现在归属侧**才升档；歧义（两侧都出现）/同侧重复/泛称无法匹配 → 落 B 段
    #       按来源定级；只在相反侧 → 确定性矛盾（实测 0 条）。
    if t in ("reactant_of", "product_of") and ctx["rxn_side_ok"].get(eid) is not None:
        ok, strength, why = ctx["rxn_side_ok"][eid]
        if ok is True:
            return R("cross_source", "equation_species_cross_source",
                     "verification_model.reaction_side_cross_check(Rhea 方程 × ChEBI %s)" % strength,
                     detail="%s（跨源：Rhea 方程串 × ChEBI 本体 %s）" % (why, strength))
        if ok is False:
            return R("unverified", "equation_species_mismatch",
                     "verification_model.reaction_side_cross_check(Rhea 方程 × ChEBI)",
                     why, detail=why)

    # A13 CODATA 常量定义式**数值复算**（Phase 33）：读输入常量值代入定义式，与被派生常量记录值比对。
    #     定义式与常量值均为外部事实（SI 定义 / CODATA），与「谁提的边」无关（铁律 #26）。
    if t == "derived_from" and ctx["codata_def_ok"].get(eid) is not None:
        ok, why = ctx["codata_def_ok"][eid]
        if ok is True:
            return R("rule_checked", "codata_definition_recompute",
                     "verification_model.codata_derivation_recheck(CODATA 定义式)",
                     detail="定义式独立复算一致：%s" % why)
        if ok is False:
            return R("unverified", "codata_definition_mismatch",
                     "verification_model.codata_derivation_recheck(CODATA 定义式)",
                     why, detail=why)

    # ================= B. 无独立复算可用：按提出者 / 来源定级 =================
    # B1 模型产物（GNN 链接预测 / LLM 推断）—— **不构成验证**
    if kind in MODEL_KINDS or ("GNN" in src) or ("LLM" in src) \
            or ("gnn" in kind) or ("llm" in kind):
        return R("model_inferred", "model_link_prediction", src or kind,
                 "GNN/LLM 预测，非验证",
                 detail="模型预测产物（kind=%s / source=%s）；**不构成验证**" % (kind or "-", src or "-"))

    # B2 仓库内人工策划
    if src.startswith("curated_seed"):
        return R("source_asserted", "manual_curation", "curated_seed",
                 detail="仓库内策划种子（curated_seed）直接断言；未做独立复核")

    # B3 化学反应方向
    if t in ("reactant_of", "product_of"):
        if kind in ("rhea_reactant", "rhea_product"):
            return R("source_asserted", "equation_sidedness", "Rhea/ChEBI",
                     detail="Rhea/ChEBI 反应方程的方向（kind=%s）" % kind)
        if kind == "real_reaction":
            return R("source_asserted", "source_assertion", src or "ElementKG2.0",
                     detail="来源 %s 直接断言反应方向" % (src or "ElementKG2.0"))
        return R("source_asserted", "source_assertion", src or "unknown",
                 detail="来源 %s 直接断言反应方向（kind=%s）" % (src or "unknown", kind or "-"))

    # B4 物理量 / 单位
    if t == "has_quantity":
        if src == "chembl_api":
            return R("source_asserted", "published_descriptor_value", "ChEMBL API",
                     detail="ChEMBL 已发表描述符值（kind=%s）" % (kind or "-"))
        return R("source_asserted", "source_assertion", src or "unknown",
                 detail="来源 %s 直接断言「分子—物理量」关联" % (src or "unknown"))
    if t == "has_unit":
        if kind == "constant_unit":
            return R("source_asserted", "codata_unit", "NIST CODATA",
                     detail="NIST CODATA 常数单位（kind=constant_unit）")
        return R("source_asserted", "source_assertion", src or "unknown",
                 detail="来源 %s 直接断言「量—单位」关联" % (src or "unknown"))

    # B5 同义 / 等价边
    if t == "has_symbol":
        # 非 PhysicsBabel 的 has_symbol（符号扫描类）：由文本扫描确定性产生
        return R("by_construction", "symbol_scan_from_text", src or "etl_pipeline.py",
                 detail="由文本符号扫描确定性产生（源=%s）" % (src or "etl_pipeline.py"))
    if t == "same_as":
        if kind == "pubchem_bridge":
            return R("cross_source", "inchikey_exact_match", "PubChem",
                     detail="PubChem InChIKey 精确匹配（kind=pubchem_bridge）")
        if kind == "cross_source_alignment":
            return R("cross_source", "multi_source_alignment", "cross_source_alignment",
                     detail="跨源对齐（kind=cross_source_alignment）")
        if kind == "chebi_name_bridge":
            return R("source_asserted", "name_exact_match", "ChEBI label",
                     detail="ChEBI 标签名精确匹配（kind=chebi_name_bridge）")
        return R("source_asserted", "manual_curation", src or "curated_seed",
                 detail="来源 %s 断言同义（kind=%s）" % (src or "curated_seed", kind or "-"))
    if t == "same_formula_as":
        # Phase 27 已把边类型收窄为「分子式相同」——声明范围内为真
        return R("by_construction", "formula_only",
                 "elementkg10m_ingest.py(skeleton)",
                 detail="构造性：边类型收窄为「分子式相同」（kind=skeleton）")
    if t == "same_latex_normalized":
        return R("model_inferred", "latex_normalized_only", "gnn_infer.py(norm_latex)",
                 detail="仅 LaTeX 归一化后同形（GNM 归一），**不构成验证**")

    # B6 常量派生 / 文献 / schema
    if t == "derived_from" and kind == "constant_derivation":
        return R("source_asserted", "codata_derivation", "NIST CODATA",
                 detail="NIST CODATA 常数派生（kind=constant_derivation）")
    if t in ("cites", "discusses", "defines") and src in ("openalex", "mathxiv",
                                                         "openalex-topic-align"):
        return R("source_asserted", "bibliographic_metadata", src,
                 detail="文献元数据（源=%s）" % src)
    if t == "derived_from" and src == "mathxiv":
        return R("source_asserted", "bibliographic_metadata", "mathxiv",
                 detail="文献元数据（源=mathxiv）")
    if t == "part_of" and src == "schema-axiom":
        return R("by_construction", "schema_axiom", "schema-axiom",
                 detail="schema 公理（源=schema-axiom）")

    # B7 其它外部源断言
    if src in ("ElementKG2.0", "ElementKG", "ChEBI", "PubChem", "rhea_chebi",
               "nist_codata_2022", "chembl_api"):
        return R("source_asserted", "source_assertion", src,
                 detail="外部源 %s 直接断言（未做独立复核）" % src)

    # B8 连来源都没有 → 不猜
    return R("unverified", "unclassified", src or "unknown",
             detail="无来源、无判据 → 不猜（unclassified）")


# --------------------------------------------------------------------------- 独立复算
# ------------------------------------------- Claim/Evidence 一等对象（Phase 32）
# 断言的可读陈述模板（人可读；机器以 (source,type,target) 为准）
_CLAIM_TEMPLATES = {
    "has_symbol": "「{s}」的符号集合包含「{t}」",
    "composed_of": "「{s}」含 {n} 个「{t}」",
    "dimensionally_consistent": "「{s}」与「{t}」量纲一致",
    "has_unit": "物理量「{s}」的单位是「{t}」",
    "has_quantity": "「{s}」与物理量「{t}」相关",
    "derived_from": "「{s}」派生自「{t}」",
    "defines": "「{s}」定义「{t}」",
    "same_as": "「{s}」与「{t}」同义/等价",
    "same_formula_as": "「{s}」与「{t}」分子式相同",
    "same_latex_normalized": "「{s}」与「{t}」LaTeX 归一化后同形",
    "part_of": "「{s}」属于「{t}」",
    "same_period": "元素「{s}」与「{t}」同周期",
    "same_family": "元素「{s}」与「{t}」同族",
    "reactant_of": "「{s}」是「{t}」的反应物",
    "product_of": "「{s}」是「{t}」的反应产物",
    "cites": "「{s}」引用「{t}」",
    "discusses": "「{s}」讨论「{t}」",
}


def _node_disp(node) -> str:
    """节点的可读名（name → symbol → id 末段）。"""
    p = (node or {}).get("props") or {}
    return str(p.get("name") or p.get("symbol")
               or ((node or {}).get("id") or "").split(":")[-1])


def claim_of(e: dict, node_by_id: dict) -> str:
    """把边转成**可读断言**（Claim）。"""
    t = e.get("type")
    s = _node_disp(node_by_id.get(e.get("source")))
    o = _node_disp(node_by_id.get(e.get("target")))
    tmpl = _CLAIM_TEMPLATES.get(t)
    if tmpl:
        if "{n}" in tmpl:
            n = (e.get("props") or {}).get("count")
            return tmpl.format(s=s, t=o, n=(n if n is not None else "?"))
        return tmpl.format(s=s, t=o)
    return "「%s」--%s-->「%s」" % (s, t, o)


def build_evidence(r: dict) -> list:
    """由判级结果构造**证据链**（list of {kind, impl, detail, indep}）。

    ★ 与 `classify` **同源**（铁律 #36）：`kind`/`indep` 由 `scope` 唯一决定（`SCOPE_KIND`），
      `detail` 由 `classify` 在**判定现场**给出 —— 此处**不重新推导**，
      ⇒ 证据语义与档位**构造上不可能分歧**（这正是第 21 轮 A11 的教训）。

    证据链**永不缺 kind**：未登记 scope 由 `_fallback_kind(level)` 兜底。
    """
    lv = r.get("verification_level")
    sc = r.get("verification_scope")
    ver = r.get("verifier") or "-"
    kind, indep = SCOPE_KIND.get(sc) or _fallback_kind(lv)
    detail = r.get("detail") or r.get("note") or ""
    return [{"kind": kind, "impl": ver, "detail": detail, "indep": bool(indep)}]


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
        "unit_ok": {},          # eid -> (ok, why)：has_unit 单位量纲 vs 物理量真量纲
        "math_ok": {},          # eid -> (ok, scope, why)：数学桥「算子存在性」复算
        "sym_expr_ok": {},      # eid -> (ok, why)：has_symbol 目标符号是否在源结构化表达式（Phase 31）
        "derived_support_ok": {},  # eid -> (ok, why)：derived_from（**仅模型产物**）目标是否有结构化支撑
        "rxn_side_ok": {},      # eid -> (ok, strength, why)：反应侧别跨源校验（Phase 33）
        "codata_def_ok": {},    # eid -> (ok, why)：CODATA 常量定义式数值复算（Phase 33）
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
            via = "edge_from_formula"
            if not f:
                m = _FORMULA_IN_RATIONALE.search(str(p.get("rationale") or ""))
                f = m.group(1) if m else None
                via = "edge_rationale"
            cnt = p.get("count")
            # 节点上的**权威分子式**：`pubchem_formula`（PubChem 交叉源）优先，其次 `formula`
            nprops = node_by_id.get(src, {}).get("props") or {}
            if nprops.get("pubchem_formula"):
                auth, auth_field = nprops["pubchem_formula"], "pubchem_formula"
            elif nprops.get("formula"):
                auth, auth_field = nprops["formula"], "formula"
            else:
                auth, auth_field = None, None
            # Phase 31 兜底：边自身无式串，但**源节点带 `formula`**（如 `MO:ch4.formula=CH4`）
            #   —— 「CH4 含 4 个 H」这一断言仍可由节点声明式**确定性复算**，只是证据链起点是
            #   节点而非边；故单列 scope（`formula_count_node_recheck`）以示区别、不作 cross_source。
            if not f and nprops.get("formula"):
                f, via = nprops["formula"], "node_formula"
            if not sym or cnt is None:
                ctx["composed_ok"][eid] = (None, "无计数可复算", via)
                continue
            if f is None:
                ctx["composed_ok"][eid] = (None, "无分子式串可复算", via)
            else:
                try:
                    comp = parse_formula_independent(f)
                    got = comp.get(sym)
                    ok = (got is not None and float(got) == float(cnt))
                    ctx["composed_ok"][eid] = (
                        ok, "%s: 独立解析 %s=%s vs 记录 %s (from %s)" % (sym, sym, got, cnt, f), via)
                    if not ok:
                        ctx["bugs"]["composed_of_count_mismatch"].append(
                            (eid, f, sym, cnt, got))
                except Exception as ex:
                    ctx["composed_ok"][eid] = (None, "分子式不可独立解析(%s)" % ex, via)
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

        # (f) has_unit：**单位量纲 vs 物理量真量纲**（Phase 30 新增）
        #     判据只依赖「单位符号」与「量名」两个外部事实，与提出者无关。
        if t == "has_unit":
            un = node_by_id.get(tgt) or {}
            qn = node_by_id.get(src) or {}
            up = un.get("props") or {}
            usym = up.get("symbol") or up.get("name")
            ud = unit_dim_of_symbol(usym)
            qname = (qn.get("props") or {}).get("name") or str(src).split(":")[-1]
            qd = dm.dim_of(qname)
            if ud is None or qd is None:
                ctx["unit_ok"][eid] = (None, "单位/量纲不可判定(%s / %s)" % (usym, qname))
            else:
                ok = _drop(ud) == _drop(qd)
                ctx["unit_ok"][eid] = (
                    ok, "%s=%s vs %s=%s" % (qname, _drop(qd), usym, _drop(ud)))
                if not ok:
                    ctx["bugs"]["unit_dimension_mismatch"].append(
                        (eid, qname, usym, _drop(qd), _drop(ud)))

        # (g) 数学桥：源公式是否**真的**含目标数学对象所辖的算子（Phase 30 新增）
        #     三态：命中且目标被该规则覆盖 → True；命中但目标不在覆盖集 → None（不判否）；
        #           源无算子 → None（不判否）。
        if t in ("derived_from", "has_symbol"):
            tnode = node_by_id.get(tgt) or {}
            tid = str(tgt)
            snode = node_by_id.get(src) or {}
            hits = math_op_of(expr_of_node(snode))
            matched = [k for k in hits if tid in MATH_OP_RULES[k][1]]
            if matched:
                ctx["math_ok"][eid] = (True, matched[0],
                                       "源表达式含 %s 所辖算子 → 目标 %s" % (matched[0], tid))
            else:
                ctx["math_ok"][eid] = (None, hits[0] if hits else "",
                                       "算子命中但目标未被覆盖" if hits else "源表达式无数学算子")

        # (h) has_symbol（Phase 31）：目标符号是否出现于源的**结构化表达式**（latex/formula/symbols）
        #     意义：把 A4（只认 PhysicsBabel 公式）推广到**任意来源**——人工策划的
        #     「公式 X 用到符号 Y」同样是**可确定性复算**的（Y 字面在 X 的 latex 里）。
        #     ⚠ 三态：True 才升档；False **本轮一律不据此撤边**（见 classify 的 A10 注释）。
        if t == "has_symbol":
            snode = node_by_id.get(src) or {}
            sym = target_symbol(node_by_id.get(tgt) or {})
            ctx["sym_expr_ok"][eid] = (symbol_in_source(snode, sym), "sym=%s" % sym)

        # (i) **模型产物语义边**（defines / has_symbol / derived_from）：目标标识是否出现于
        #     源的**结构化表达式**（Phase 31）。闸门与门禁 `semantic_target_present` 共用同一条
        #     谓词（kind/source 判定「模型产物」），确保**判级模型与门禁自洽**。
        #     为何要推广到 has_symbol/defines：Phase 30 的 R1/R2 只写在 **delta 生成器**里，
        #     判级模型没有 —— 于是当 `target_symbol` 改进后新暴露出 3 条「目标缺席」的模型边
        #     （`MX:sym:n`←`MX:math:binomial` 等），门禁立刻 FAIL 而模型无规则可撤。
        if t in ("defines", "has_symbol", "derived_from"):
            k = p.get("kind") or e.get("kind") or ""
            s = p.get("source") or ""
            is_model = (k in MODEL_KINDS) or ("GNN" in s) or ("LLM" in s) \
                or ("gnn" in k) or ("llm" in k)
            if is_model:
                snode = node_by_id.get(src) or {}
                sym = target_symbol(node_by_id.get(tgt) or {})
                ctx["derived_support_ok"][eid] = (symbol_in_source(snode, sym), "sym=%s" % sym)

        # (j) 化学反应侧别**跨源**校验（Phase 33）：Rhea 方程串 × ChEBI 本体 label/formula。
        #     判据只读方程与参与物属性（铁律 #14）；两源**独立**（不同数据库）。
        if t in ("reactant_of", "product_of"):
            ctx["rxn_side_ok"][eid] = reaction_side_cross_check(
                node_by_id.get(src), node_by_id.get(tgt), t == "reactant_of")

        # (k) CODATA 常量定义式**数值复算**（Phase 33）：读输入常量值代入定义式，与被派生常量记录值比对。
        if t == "derived_from" and (p.get("kind") or e.get("kind")) == "constant_derivation":
            ctx["codata_def_ok"][eid] = codata_derivation_recheck(
                node_by_id, src, p.get("definition"))
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
    for key, lab in (("unit_ok", "unit_ok"), ("math_ok", "math_ok"),
                     ("sym_expr_ok", "sym_expr_ok"), ("derived_support_ok", "derived_ok")):
        d = ctx[key]
        yes = sum(1 for v in d.values() if v[0] is True)
        no = sum(1 for v in d.values() if v[0] is False)
        na = sum(1 for v in d.values() if v[0] is None)
        print("  %-14s 通过 %5d / 不一致 %4d / 不可判定 %5d" % (lab, yes, no, na))
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
