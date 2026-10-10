#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""semantic_noise_audit.py —— 语义边「确定性反驳」独立审计器（Phase 30 / 第 20 轮）

动机
----
第 19 轮把北极星短板从「跨域桥」修好后，第 20 轮侦察发现：图上存在一批
**可被确定性反驳**的语义边 —— 不是「未验证」，而是**与自身载荷自相矛盾**：

  ① `define s| <数学公式> -> 气体常数`   （Euler 恒等式"定义"气体常数…）
  ② `has_symbol| <数学对象> -> 物理符号`（协变导数"含符号 T(温度)"＝**同名碰撞**）
  ③ `has_unit | <物理量> -> 单位`        （面积"以 J/K 为单位"…）

判据（全部**只读边自身 + 端点的表达式字段**，绝不读已存 `verification_level`）
--------------------------------------------------------------------------
  R1 `defines`     目标量的 symbol 必须出现在**源公式的表达式**里（`latex`/`formula`）
  R2 `has_symbol`  目标符号的 symbol 必须出现在**源对象的表达式**里
  R3 `has_unit`    目标单位解析出的量纲 == 源物理量的真量纲（`dimension_table`）
  R4 `has_unit`    同一物理量的多个 `has_unit` 目标**量纲必须唯一**（互斥→至多一个对）

Phase 31（第 21 轮「复算维度全覆盖」）新增/扩展
------------------------------------------------
  R1/R2 **扩展到 `derived_from`**：三类**模型产物语义边**（`defines`/`has_symbol`/
       `derived_from`）的目标标识若在源表达式中**缺席**，即为可被确定性反驳 —— 与判级模型
       `A11`、门禁 `model_semantic_target_present` **同谓词、三处独立实现**（铁律 #14）。
      ⚠ 仍**只对模型产物边**执行：策划数据的符号命名有习惯差异（`Q` vs `q`、`u` vs `d_o`），
      第 21 轮实测 22 条 `has_symbol` 缺席**全是记号变体** → 一律不用缺席撤策划边（铁律 #31）。
  R7 `dimensionally_consistent`：两端的真量纲**均可查**时必须严格相等。该边型在本轮之前
      **从未**被真量纲复算过（`A1` 存在却被命名缺口卡成「不可判定」，静默退化）→ 关掉
      缺口后一次抓出 8 条量纲互斥的假边。此处为**独立第二实现**。

Phase 32（第 22 轮「Claim/Evidence 对象化」）新增
--------------------------------------------------
  R9 证据链良构 + 独立抽验（R9a 良构；R9b 对自称 `symbol_in_source` 的证据用本脚本
     `has_symbol` 独立重算）。

Phase 33（第 23 轮「T9-i 证据独立攻坚」）新增 —— **对新增的两类独立证据做独立重算**
------------------------------------------------------------------------------
  R10 `equation_species_cross_source`：用**自带**方程切分/物种归一再算侧别；
      与判级模型 `reaction_side_cross_check` **同判据、独立实现**。
  R11 `codata_definition_recompute`：用**自带**定义式表再算常量派生。
  R13 T1–T8 达标边的**非独立理由**清算（Phase 35 · 自带任务族表与理由码表）；
  R14 全图**分母外**边的理由清算（Phase 35 · 自带学科映射与类型码表）。
      ⚠ 二者都带**双向正对照**（正确→True / 错误→False），确保审计器**真的能报错**（铁律 #43）。

三态（铁律 spirit：不可判定 ≠ 假）
----------------------------------
  源无表达式 / 端点无 symbol / 量纲查不到 → **不可判定**（`None`），**不撤**。

独立性（铁律 #14）
------------------
本脚本**不 import** `verification_model`（那是最终执行反驳的模块）；单位→量纲表
是**本脚本自带**的 SI 定义表，与 `dimension_table`（消元反解产物）互为独立实现。
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import dimension_table as dm          # noqa: E402   （真值表，非被检对象）

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")

# --------------------------------------------------------------- 单位 → 量纲（自带）
# 仅收录图上实际出现的 SI 单位符号；每条给出**定义式**，可逐条复核。
# 不可识别 → None（宁缺勿滥，不得猜）。
UNIT_SI = {
    "kg":  {"M": 1.0},
    "m":   {"L": 1.0},
    "s":   {"T": 1.0},
    "j":   {"M": 1.0, "L": 2.0, "T": -2.0},
    "m/s": {"L": 1.0, "T": -1.0},
    "m/s^2": {"L": 1.0, "T": -2.0},
    "n":   {"M": 1.0, "L": 1.0, "T": -2.0},
    "w":   {"M": 1.0, "L": 2.0, "T": -3.0},
    "pa":  {"M": 1.0, "L": -1.0, "T": -2.0},
    "k":   {"Th": 1.0},
    "m^3": {"L": 3.0},
    "mol": {"N": 1.0},
    "j/k": {"M": 1.0, "L": 2.0, "T": -2.0, "Th": -1.0},
    "j/(mol*k)": {"M": 1.0, "L": 2.0, "T": -2.0, "N": -1.0, "Th": -1.0},
    "c":   {"I": 1.0, "T": 1.0},
    "v":   {"M": 1.0, "L": 2.0, "T": -3.0, "I": -1.0},
    "a":   {"I": 1.0},
    "ω":   {"M": 1.0, "L": 2.0, "T": -3.0, "I": -2.0},
    "ohm": {"M": 1.0, "L": 2.0, "T": -3.0, "I": -2.0},
    "f":   {"M": -1.0, "L": -2.0, "T": 4.0, "I": 2.0},
    "t":   {"M": 1.0, "T": -2.0, "I": -1.0},
    "h":   {"M": 1.0, "L": 2.0, "T": -2.0, "I": -2.0},
    "wb":  {"M": 1.0, "L": 2.0, "T": -2.0, "I": -1.0},
    "hz":  {"T": -1.0},
    "ev":  {"M": 1.0, "L": 2.0, "T": -2.0},
    "m^2": {"L": 2.0},
    "rad": {"A": 1.0},        # ⚠ 本项目把「角度 A」当基本量（DIM 含 "A"）→ radian = {A:1}
    "mol/l": {"N": 1.0, "L": -3.0},
}


def unit_dim(sym):
    """单位符号 → 量纲向量；不可识别返回 None。容忍 `J/(mol*K)`/`J/mol*K`/Ω 等写法。"""
    if not sym:
        return None
    s = str(sym).strip().lower().replace(" ", "").replace("·", "*")
    if s in UNIT_SI:
        return dict(UNIT_SI[s])
    s = s.replace("(", "").replace(")", "")           # J/(mol*K) -> j/mol*k
    if s in UNIT_SI:
        return dict(UNIT_SI[s])
    return None


def _norm(d):
    return {k: float(v) for k, v in (d or {}).items() if abs(float(v)) > 1e-9}


# ------------------------------------------------------------------ 表达式字段
# ⚠ 判据**只读表达式**（latex / formula），**不读散文**（informal/statement）——
#   第 20 轮实测：若把散文计入，`Rate of doing work.` 里的 "R"、"failure of" 里的 "f"、
#   `The covariant…` 里的 "T" 会让 **9 条真·错误边**被误判为「符号存在」而逃过反驳。
#   表达式中不含该符号 ⟹ 该公式**确实**与该量无关 → 反驳是可靠的（sound）。
EXPR_KEYS = ("latex", "formula")
TEXT_KEYS = EXPR_KEYS


def props_of(n):
    return (n or {}).get("props") or {}


def expr_text(node, keys=EXPR_KEYS):
    p = props_of(node)
    return " ".join(str(p.get(k)) for k in keys if p.get(k))


def sym_of(node):
    """目标实体的「符号」：`symbol` → `latex` → 从名称 `X (desc)` 中取 `X`。"""
    p = props_of(node)
    for k in ("symbol", "latex"):
        if p.get(k):
            return str(p[k]).strip()
    nm = str(p.get("name") or "").strip()
    m = re.match(r"^([^\s(]+)", nm)
    return m.group(1) if m else None


# ⚠ 关键：LaTeX 命令必须**归一为命令名**而非删除 —— `\nu`/`\tau`/`\Phi`/`\hbar`
#   若被整段删掉，会让「符号确实存在」的合法边被误判为"缺失"→ **错撤**。
#   故 `\nu` -> `nu`、`\nabla` -> `nabla`、`\mathcal{E}` -> `mathcalE`（保留语义）。
_LATEX_CMD = re.compile(r"\\([A-Za-z]+)")
_STRIP = re.compile(r"[\\{}$\s]+")
# ⚠ 包装命令**先剥壳取内容**（Phase 32 修复）：`\mathrm{pH}` 应归一为 `pH`（≠ `mathrmpH`），
#   否则与 `verification_model._norm_latex` 的约定不一致 → **假阳性**（实测 2 条：
#   `CE:fo:ph --has_symbol--> CE:sy:pH`，源 latex 明文含 `pH`，符号却是 `\mathrm{pH}`）。
#   铁律 #31「包装命令须剥壳」在**第二个实现**上的复现 —— 两实现必须共用同一归一化约定
#   （独立性在于**判据逻辑**，不在于文本归一化）。
_WRAP = re.compile(r"\\(?:mathrm|text|operatorname|mbox|textrm|mathbf|mathit"
                   r"|mathcal|mathsf|mathtt|mathbb)\s*\{([^{}]*)\}")


def _norm_text(s):
    t = str(s)
    prev = None
    while prev != t:                       # 迭代剥壳（支持 \text{\mathrm{x}} 嵌套）
        prev = t
        t = _WRAP.sub(r"\1", t)
    return _STRIP.sub("", _LATEX_CMD.sub(r"\1", t))


def declared_symbols(node):
    """节点自带的 `symbols` 声明（部分 MathML 节点会显式列出成员符号）。"""
    p = props_of(node)
    d = p.get("symbols")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except Exception:                                  # noqa: BLE001
            d = [d]
    return [str(x) for x in (d or [])]


def has_symbol(node, sym):
    """符号是否属于源对象（**宽松**判据：宁可判为出现，也绝不误撤）。

    判据顺序：① 节点 `symbols` 声明中**逐字**出现；② 表达式中**大小写敏感**子串出现。
    三者皆不满足才返回 False（可反驳）。源无表达式 → None（不可判定）。
    """
    if not sym:
        return None
    text = expr_text(node, TEXT_KEYS)
    if not text:
        return None
    s = str(sym).strip()
    cands = [s]
    m = re.match(r"^(.+?)_\{?(.+?)\}?$", s)                # k_B / x_i -> 也试 k
    if m:
        cands.append(m.group(1))
    decl = declared_symbols(node)
    if any(c in decl for c in cands):
        return True
    t = _norm_text(text)
    return any(c and _norm_text(c) in t for c in cands)


# ===================== R10 / R11（Phase 33 · 自带实现，不 import 判级模型） =====================
# R10 化学反应侧别**跨源**校验：用本脚本**自带**的方程切分与物种归一，独立重算
#     「参与物是否只出现在归属侧」。与 `verification_model.reaction_side_cross_check`
#     **同判据、独立实现** —— 若某条边自称 `equation_species_cross_source` 却在此重算为否，
#     即为**候选反驳**（铁律 #43：审计器必须允许它报错）。
# ⚠ 分隔符必须要求**两侧空白**（` \+ `）：否则 `NAD(+)`/`Fe(2+)` 的电荷号会被误切
#   （这正是 Phase 33 判级模型初版的一个 bug，此处独立实现必须避开同一坑）。
_RXN_EQ_SPLIT = re.compile(r" = ")
_RXN_TERM_SPLIT = re.compile(r" \+ ")
_RXN_PAREN_AUDIT = re.compile(r"\([^)]*\)")

# ---- ChEBI 同义词缓存（Phase 34 · R10 扩展）----------------------------------
# 审计器**自带**读取同一份外部数据（`11_真实数据/chebi_synonym_cache.json`，来自 ChEBI OLS4），
# 但用**自己的**归一 + 匹配实现 ⇒ 与判级模型「同判据、独立实现」（铁律 #43）。
# ⚠ 长度护栏**独立取值 2**（判级模型用 3）：两者若都判「只在归属侧」⇒ 交叉确认更强。
_AUDIT_SYN_PATH = os.path.join(ROOT, "11_真实数据", "chebi_synonym_cache.json")
try:
    _AUDIT_SYN = json.load(open(_AUDIT_SYN_PATH, encoding="utf-8")) \
        if os.path.exists(_AUDIT_SYN_PATH) else {}
except Exception:                                             # noqa: BLE001
    _AUDIT_SYN = {}
_AUDIT_SYN_MIN = 2


def _audit_alias_names(pp) -> list:
    """自带实现：参与物的 ChEBI 同义词集（归一后）。"""
    cid = (pp or {}).get("chebi_id")
    rec = _AUDIT_SYN.get(cid) if cid else None
    if not (rec and rec.get("ok")):
        return []
    lab = _audit_canon((pp or {}).get("name") or "")
    out = []
    for s in rec.get("synonyms") or []:
        c = _audit_canon(s)
        if c and len(c) >= _AUDIT_SYN_MIN and c != lab and c not in out:
            out.append(c)
    return out


def _audit_canon(x) -> str:
    s = str(x).lower()
    s = _RXN_PAREN_AUDIT.sub("", s)
    return s.replace(" ", "").replace("-", "").replace("+", "")


def rxn_side_check(part_node, rxn_node, is_reactant):
    """自带实现：True=只在归属侧 / False=只在相反侧 / None=不可判定。

    Phase 34 扩展：label/formula 均未命中时，启用 **ChEBI 同义词**通道（只升不驳）。
    """
    eq = props_of(rxn_node).get("equation")
    if not eq or " = " not in str(eq):
        return None
    lhs, rhs = str(eq).split(" = ", 1)
    L = [_audit_canon(x) for x in _RXN_TERM_SPLIT.split(lhs) if x.strip()]
    R = [_audit_canon(x) for x in _RXN_TERM_SPLIT.split(rhs) if x.strip()]
    cs, os_ = (L, R) if is_reactant else (R, L)
    pp = props_of(part_node)
    lab = _audit_canon(pp.get("name") or "")
    fml = _audit_canon(pp.get("formula") or "")
    lh, lw = bool(lab) and lab in cs, bool(lab) and lab in os_
    if lh and lw:
        return None
    if lw and not lh:
        return False
    if lh:
        return True
    fh, fw = bool(fml) and fml in cs, bool(fml) and fml in os_
    if fh and fw:
        return None
    if fw:
        return False
    if fh:
        if cs.count(fml) > 1:
            return None
        return True
    # Phase 34：同义词通道（只升不驳）
    syms = _audit_alias_names(pp)
    if syms:
        sh = [s for s in syms if s in cs]
        sw = [s for s in syms if s in os_]
        if sh and not sw:
            if cs.count(sh[0]) > 1:
                return None
            return True
        if sh and sw:
            return None
        if sw:
            return None
    return None


# R11 CODATA 常量定义式数值复算：**自带**定义式表（独立于判级模型）。
_AUDIT_CODATA_DEFS = {
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


def codata_check(node_by_id, src_id, defn):
    """自带实现：True=复算一致 / False=不一致 / None=不可判定。"""
    spec = _AUDIT_CODATA_DEFS.get(defn)
    if spec is None:
        return None
    ins, fn = spec
    vals = []
    for i in ins:
        v = props_of(node_by_id.get(i)).get("value")
        if v is None:
            return None
        vals.append(float(v))
    rec = props_of(node_by_id.get(src_id)).get("value")
    if rec is None:
        return None
    try:
        calc = fn(vals)
    except Exception:                                      # noqa: BLE001
        return None
    return abs(calc - float(rec)) / max(abs(float(rec)), 1e-30) <= 1e-6


# ------------------------------------------------------------------------- 主逻辑
MODEL_KINDS = {"gnn_typed_verified", "gnn_typed_inferred", "llm_inferred",
               "llm_inferred_gnn", "llm_review", "llm_review_gnn"}


def run(path=NORM):
    d = json.load(open(path, encoding="utf-8"))
    N = {n["id"]: n for n in d["nodes"]}
    E = d["edges"]

    def kind_of(e):
        return (e.get("props") or {}).get("kind") or e.get("kind") or "-"

    def src_of(e):
        return (e.get("props") or {}).get("source") or ""

    def is_model(e):
        k, s = kind_of(e), src_of(e)
        return (k in MODEL_KINDS) or ("GNN" in s) or ("LLM" in s) \
            or ("gnn" in k) or ("llm" in k)

    findings = collections.defaultdict(list)
    undecidable = collections.Counter()

    # ---- R1 / R2 / R6：目标 symbol 必须出现在源对象（声明 + 表达式）里 ----
    #   ⚠ 只对**模型产物**边执行反驳：策划数据里的符号命名（`Q` vs `q`、`eps` vs
    #     `\mathcal{E}`）带人为习惯，误撤代价高；模型边的"符号缺失"则是纯噪声。
    #   ⚠ Phase 31：类型集从 (`defines`,`has_symbol`) 扩到含 `derived_from` ——
    #     与判级模型 A11 / 门禁同谓词（三处独立实现互为交叉验证，铁律 #14）。
    SEMANTIC_TYPES = ("defines", "has_symbol", "derived_from")
    for e in E:
        if e["type"] not in SEMANTIC_TYPES:
            continue
        if not is_model(e):
            continue
        tnode = N.get(e["target"])
        snode = N.get(e["source"])
        if not tnode or not snode:
            continue
        s = sym_of(tnode)
        ok = has_symbol(snode, s)
        tag = "R2_symbol_target_absent"
        if e["type"] == "defines":
            tag = "R1_defines_target_symbol_absent"
        elif e["type"] == "derived_from":
            tag = "R6_derived_from_target_absent"
        if s is None or ok is None:
            undecidable[e["type"]] += 1
        elif ok is False:
            findings[tag].append(
                (e["id"], e["target"], s, kind_of(e), src_of(e)))

    # ---- R3 / R4：has_unit 量纲一致 + 唯一性（**全量**：量纲是客观事实，无命名歧义）----
    q_units = collections.defaultdict(list)      # src -> [(eid, unit_id, dim)]
    for e in E:
        if e["type"] != "has_unit":
            continue
        un, qn = N.get(e["target"]), N.get(e["source"])
        if not un or not qn:
            continue
        ud = unit_dim(unit_str(un))
        qname = props_of(qn).get("name") or e["source"].split(":")[-1]
        qd = dm.dim_of(qname)
        if ud is None or qd is None:
            undecidable["R3"] += 1
            q_units[e["source"]].append((e["id"], e["target"], None))
            continue
        if _norm(ud) != _norm(qd):
            findings["R3_unit_dim_mismatch"].append(
                (e["id"], e["source"], qname, e["target"], unit_str(un), kind_of(e),
                 _norm(qd), _norm(ud)))
        q_units[e["source"]].append((e["id"], e["target"], _norm(ud)))

    for src, lst in q_units.items():
        dims = {tuple(sorted(u[2].items())) for u in lst if u[2] is not None}
        if len(dims) > 1:
            findings["R4_unit_multi_conflict"].append(
                (src, props_of(N.get(src)).get("name"), len(lst), len(dims)))

    # ---- R7：`dimensionally_consistent` 两端真量纲必须严格相等（Phase 31 新增）----
    #     此前该边型**从未**被真量纲复算（铁律 #33：新维度先问「这里有没有从未被检验过的
    #     断言」）。此处独立于 `verification_model` 自行解析节点→量名→量纲。
    for e in E:
        if e["type"] != "dimensionally_consistent":
            continue
        na = _qname(N.get(e["source"]), e["source"])
        nb = _qname(N.get(e["target"]), e["target"])
        da, db = dm.dim_of(na), dm.dim_of(nb)
        if da is None or db is None:
            undecidable["R7"] += 1
            continue
        if _norm(da) != _norm(db):
            findings["R7_dim_consistent_mismatch"].append(
                (e["id"], na, nb, kind_of(e), src_of(e)))

    # ---- R9：证据链良构 + 独立抽验（Phase 32 新增）----
    #     独立于 `verification_model`：**自带** indep-kind 枚举与符号复算，不 import 判级模型。
    #       R9a 良构：level>=rule_checked 的边必须带 ≥1 条 {indep=真, detail 非空, impl==verifier}
    #       R9b 抽验：证据 `impl` 自称 `symbol_in_source` 的，**用本脚本的 has_symbol 重算**
    #                目标符号是否真在源表达式里（若否 → 候选反驳，说明证据是伪造/失效的）
    STRICT = {"rule_checked", "cross_source", "human_reviewed"}
    INDEP_KINDS = {"recompute", "cross_source"}          # 自带枚举（不 import）
    n_strict = n_ok_ev = 0
    n_recheck = 0
    for e in E:
        p = props_of(e)
        lv = p.get("verification_level")
        if lv not in STRICT:
            continue
        n_strict += 1
        evs = p.get("verification_evidence")
        if not isinstance(evs, list):
            findings["R9a_evidence_missing"].append((e["id"], lv, "无证据链"))
            continue
        good = [it for it in evs if isinstance(it, dict) and it.get("indep")
                and it.get("detail") and it.get("impl") == p.get("verifier")]
        if not good:
            findings["R9a_evidence_missing"].append((e["id"], lv, "无独立证据/impl 不符"))
            continue
        n_ok_ev += 1
        # R9b：对「自称符号复算」的证据**独立重算**
        for it in good:
            if "symbol_in_source" not in str(it.get("impl")):
                continue
            tn, sn = N.get(e["target"]), N.get(e["source"])
            ts = sym_of(tn)
            if not sn or not ts:
                continue
            n_recheck += 1
            got = has_symbol(sn, ts)
            if got is False:
                findings["R9b_symbol_evidence_stale"].append(
                    (e["id"], ts, "证据自称符号复算但独立重算为缺席"))

    # ---- R10 / R11：对 Phase 33 新增的两类**独立证据**做**独立重算**（自带实现）----
    #      R10：凡自称 `equation_species_cross_source` 的边，本脚本独立重算侧别 → 若不为「只在归属侧」= 候选反驳
    #      R11：凡自称 `codata_definition_recompute` 的边，本脚本独立复算定义式 → 若不一致 = 候选反驳
    n_r10 = n_r11 = 0
    for e in E:
        p = props_of(e)
        sc = p.get("verification_scope")
        if sc == "equation_species_cross_source":
            n_r10 += 1
            got = rxn_side_check(N.get(e["source"]), N.get(e["target"]),
                                 e.get("type") == "reactant_of")
            if got is not True:
                findings["R10_reaction_side_recheck_failed"].append(
                    (e["id"], e.get("type"), "独立重算=%s" % got, src_of(e)))
        elif sc == "codata_definition_recompute":
            n_r11 += 1
            got = codata_check(N, e["source"], p.get("definition"))
            if got is not True:
                findings["R11_codata_recheck_failed"].append(
                    (e["id"], "codata_definition", "独立重算=%s" % got, src_of(e)))

    # ---- R12：T4 残差清算（Phase 34 · 独立实现）----
    #     凡 `reactant_of`/`product_of` 且**非** cross_source/mismatch 的边，必须能归入
    #     一个「**不可独立复算的显式理由**」类别，**零未归类**（铁律 #30：不可判定 ≠ 可以放过）。
    #     类别枚举（本脚本自带，与判级侧 `verification_model.residual_reason` 同**口径**、独立实现）。
    n_r12 = 0
    r12_reason = collections.Counter()
    for e in E:
        if e.get("type") not in ("reactant_of", "product_of"):
            continue
        sc = props_of(e).get("verification_scope")
        if sc in ("equation_species_cross_source", "equation_species_mismatch"):
            continue
        n_r12 += 1
        rp = props_of(N.get(e["target"]))
        pp = props_of(N.get(e["source"]))
        rsrc = rp.get("source") or "-"
        if rsrc == "ElementKG2.0":
            reason = "same_source_elementkg"
        elif str(rsrc).startswith("curated_seed"):
            reason = "same_source_curated"
        elif rsrc == "Rhea":
            if pp.get("is_generic"):
                reason = "generic_class"
            elif pp.get("is_polymer"):
                reason = "polymer_residue"
            elif rp.get("equation") and "[" in str(rp.get("equation")):
                reason = "placeholder_complex"
            else:
                reason = "naming_variant_no_second_source"
        else:
            reason = "UNCLASSIFIED"
        r12_reason[reason] += 1
        if reason == "UNCLASSIFIED":
            findings["R12_unclassified_residual"].append((e["id"], rsrc, sc))

    # ---- R13 / R14：两项**横向清算**（Phase 35 · 本脚本自带实现，不 import 判级模型）----
    #   R13 `independence_accounted`：T1–T8 **达标边**中凡证据不独立者，其理由须落在受控枚举。
    #   R14 `scope_accounted`       ：全图凡**不在任务族**的边，其「为何不在分母」须落在受控枚举。
    #   ★ 口径必须与判级侧**同口径、独立实现**（铁律 #36 + #43）：本脚本自带任务族表、
    #     自带学科映射、自带理由码表 —— 若两侧口径分叉，这里会立刻报「候选反驳」。
    _A_TASK = {                                    # 边类型 → (族, 门槛)
        "same_period": ("T1", 4), "same_family": ("T1", 4),
        "composed_of": ("T2", 4), "dimensionally_consistent": ("T3", 4),
        "reactant_of": ("T4", 2), "product_of": ("T4", 2),
        "has_symbol": ("T5", 4), "derived_from": ("T6", 2), "has_unit": ("T6", 2),
        "cites": ("T8", 2), "discusses": ("T8", 2),
    }
    _A_RANK = {"unverified": 0, "model_inferred": 1, "source_asserted": 2,
               "by_construction": 3, "rule_checked": 4, "cross_source": 5,
               "human_reviewed": 6}
    _A_SCOPE_TYPE = {                              # 不在分母的理由（自带副本）
        "has_quantity": "descriptor_assertion_not_in_task_set",
        "has_functionalgroup": "ekg_relation_not_in_task_set",
        "reagent_of": "ekg_relation_not_in_task_set",
        "has_element": "ekg_relation_not_in_task_set",
        "defines": "symbol_binding", "related_to": "gnn_link_prediction",
        "proves": "gnn_link_prediction", "same_as": "identity_not_in_task_set",
        "part_of": "structure_not_in_task_set",
        "same_formula_as": "structure_not_in_task_set",
        "chemical_reaction": "structure_not_in_task_set",
    }
    _A_INDEP = {"same_source_elementkg", "same_source_curated", "generic_class",
                "polymer_residue", "placeholder_complex", "naming_variant_no_second_source",
                "no_independent_citation_index", "no_second_subject_index",
                "source_text_only", "unit_name_not_in_reference"}
    _A_PH = __import__("re").compile(r"^(FG|reaction|molecule|mol|rxn|rx)[_\-]?\d+$", __import__("re").I)

    def _a_disc(nid):                              # 自带学科映射（与 graph_export 独立实现）
        # ⚠ 口径=**数据规格**：`domain` 优先；为空时按 id 命名空间兜底（表须与
        #   `graph_export._NS_SUBJECT` **同步**）。★ 第 25 轮实录：本函数最初**漏了兜底**，
        #   R13 当场误报 47 条候选反驳 —— 这是铁律 #43「审计器必须允许它报错」的**首次真捕获**：
        #   实现差异会被误读为「数据反驳」，故口径规格必须**显式且同源**。
        d = (props_of(N.get(nid)).get("domain") or "").strip().lower()
        if d.startswith(("chem", "ek", "bio")):
            return "化学"
        if d.startswith(("phys", "phy", "pb")):
            return "物理"
        if d.startswith(("math", "mx", "mg")):
            return "数学"
        if d:
            return "跨学科"
        pfx = str(nid or "").split(":")[0]
        return _A_NS.get(pfx) or _A_NS.get(pfx.upper(), "数学")

    _A_NS = {
        "PQ": "物理", "PB": "物理", "CM": "物理", "QM": "物理", "TH": "物理",
        "EM": "物理", "OP": "物理", "SM": "物理", "RT": "物理", "UN": "物理",
        "SY": "物理", "CO": "物理", "FO": "物理",
        "EL": "化学", "EK": "化学", "EK2": "化学", "IC": "化学", "BC": "化学",
        "RX": "化学", "OM": "化学", "MO": "化学", "PC": "化学", "CE": "化学",
        "RH": "化学", "CH": "化学",
        "MX": "数学", "MG": "数学", "MA": "数学", "MC": "数学", "NT": "数学",
        "PA": "数学",
        "WD": "跨学科", "wd": "跨学科",
    }

    def _a_ph(nid):
        nm = props_of(N.get(nid)).get("name")
        s = str(nm).strip() if nm is not None else ""
        return str(nid).startswith("EK2:") and (not s or bool(_A_PH.match(s)))

    def _a_indep(e):                               # 非独立理由（自带实现）
        p = props_of(e)
        evs = p.get("verification_evidence")
        evs = evs if isinstance(evs, list) else []
        if any(x.get("indep") for x in evs):
            return ""
        t = e["type"]
        if t in ("reactant_of", "product_of"):
            rp, pp = props_of(N.get(e["target"])), props_of(N.get(e["source"]))
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
        if t == "cites":
            return "no_independent_citation_index"
        if t == "discusses":
            return "no_second_subject_index"
        if t == "derived_from":
            return ("same_source_curated"
                    if str(props_of(N.get(e["source"])).get("source") or "").startswith("curated_seed")
                    else "source_text_only")
        if t == "has_unit":
            return ("same_source_curated"
                    if str(props_of(N.get(e["source"])).get("source") or "").startswith("curated_seed")
                    else "unit_name_not_in_reference")
        return "UNCLASSIFIED"

    def _a_scope(e):                               # 「不在分母」理由（自带实现）
        p = props_of(e)
        r = _A_SCOPE_TYPE.get(e["type"])
        if r is None and p.get("verification_scope") == "model_link_prediction":
            r = "gnn_link_prediction"
        return r or "UNCLASSIFIED"

    def _a_in_task(e):
        return bool(_A_TASK.get(e["type"])) or (_a_disc(e["source"]) != _a_disc(e["target"]))

    n_r13 = n_r14 = n_ph_edge = 0
    r13_reason, r14_reason = collections.Counter(), collections.Counter()
    for e in E:
        p = props_of(e)
        if not _a_in_task(e):
            n_r14 += 1
            n_ph_edge += 1 if (_a_ph(e["source"]) or _a_ph(e["target"])) else 0
            r = _a_scope(e)
            r14_reason[r] += 1
            if r == "UNCLASSIFIED":
                findings["R14_unclassified_scope"].append((e.get("type"), e.get("id"),
                                                           "UNCLASSIFIED"))
            continue
        # ⚠ 达标判定 = 「任一族门槛满足」；T7（跨域）门槛 = rule_checked(4)。
        #   第 25 轮实录：本行最初写成 `if fam and lv < fam[1]: continue`，对**仅靠 T7 达标**
        #   的边**漏检门槛** → 47 条 model_inferred 跨域边被错当达标边 → 误报 47 条候选反驳。
        #   两处bug合起来说明：**审计器的第一价值是「逼口径显式化」**（铁律 #43）。
        lv = _A_RANK.get(p.get("verification_level"), -1)
        fam = _A_TASK.get(e["type"])
        cross = _a_disc(e["source"]) != _a_disc(e["target"])
        passed = (fam is not None and lv >= fam[1]) or (cross and lv >= 4)
        if not passed:
            continue
        n_r13 += 1
        r = _a_indep(e)
        if not r:
            continue
        r13_reason[r] += 1
        if r == "UNCLASSIFIED":
            findings["R13_unclassified_independent"].append((e.get("type"), e.get("id"), r))

    # 正对照（铁律 #31/#43：新判据必须能**双向**报错 —— 不是「永远不报」的摆设）
    _pc_scope = _a_scope({"type": "bogus_relation", "props": {}})
    _pc_indep = _a_indep({"type": "bogus_type", "props": {"verification_evidence": []}})
    _pc_ctrl = {"unknown-scope-type": _pc_scope, "unknown-indep-type": _pc_indep}
    pc_r13_ok = (_pc_ctrl["unknown-scope-type"] == "UNCLASSIFIED"
                 and _pc_ctrl["unknown-indep-type"] == "UNCLASSIFIED")

    # ---- 汇报 ----
    print("=" * 92)
    print("语义边确定性反驳审计 —— %s" % os.path.relpath(path, ROOT))
    print("  规模 %d 节点 / %d 边" % (len(N), len(E)))
    print("=" * 92)
    for k in sorted(findings):
        rows = findings[k]
        def _k(r):
            if k.startswith(("R13", "R14")):
                return r[-1]                      # 理由码
            return r[3] if k.startswith(("R1", "R2")) else (r[5] if len(r) > 5 else "-")
        kc = collections.Counter(_k(r) for r in rows)
        print("\n【%s】%d 条   按 kind：%s" % (k, len(rows), dict(kc)))
        for r in rows[:14]:
            print("   -", r)
    print("\n【不可判定（不撤）】", dict(undecidable))
    print("\n【R9 证据链（Phase 32 · 独立实现）】")
    print("   level>=rule_checked %d 条；带独立证据链 %d 条（%.1f%%）；独立重算抽验 %d 条"
          % (n_strict, n_ok_ev, 100.0 * n_ok_ev / n_strict if n_strict else 0.0, n_recheck))
    # 正对照（铁律 #31：收紧/修改判据必须配正对照）—— 冻结本轮修掉的「包装命令未剥壳」缺陷
    _pc = [("\\mathrm{pH}", "pH"), ("\\text{mass}", "mass"), ("\\operatorname{log}", "log"),
           ("x_{i}", "x_i")]
    _pc_bad = [(a, b, _norm_text(a)) for a, b in _pc if _norm_text(a) != b]
    print("   正对照 · 包装命令剥壳：%s"
          % ("✅ 全部符合预期" if not _pc_bad else "❌ 不符 %s" % _pc_bad))

    print("\n【R10/R11/R12 独立证据重算与残差清算（Phase 33/34 · 独立实现）】")
    print("   R10 自称跨源侧别校验 %d 条，独立重算不为「只在归属侧」%d 条"
          % (n_r10, len(findings.get("R10_reaction_side_recheck_failed", []))))
    print("   R11 自称 CODATA 定义式复算 %d 条，独立重算不一致 %d 条"
          % (n_r11, len(findings.get("R11_codata_recheck_failed", []))))
    print("   R12 T4 残差清算 %d 条，未归类 %d 条   按理由：%s"
          % (n_r12, len(findings.get("R12_unclassified_residual", [])), dict(r12_reason)))
    print("   R13 T1–T8 达标边 %d 条，非独立理由未归类 %d 条   按理由：%s"
          % (n_r13, len(findings.get("R13_unclassified_independent", [])), dict(r13_reason)))
    print("   R14 分母外边 %d 条（占位实体边 %d），理由未归类 %d 条   按理由：%s"
          % (n_r14, n_ph_edge, len(findings.get("R14_unclassified_scope", [])), dict(r14_reason)))
    print("   正对照 · R13/R14 清算表可报错（未知类型/未知来源 → %s）：%s"
          % (_pc_ctrl, "✅ 符合预期" if pc_r13_ok else "❌ 不符"))
    # 正对照（铁律 #31：新判据必须配正对照，且要能**双向**报错）
    _pc_rxn = {
        "reactant-ok": rxn_side_check({"props": {"name": "H2O"}},
                                      {"props": {"equation": "H2O + CO2 = H2CO3"}}, True),
        "product-wrong": rxn_side_check({"props": {"name": "H2O"}},
                                        {"props": {"equation": "H2O + CO2 = H2CO3"}}, False),
    }
    # Phase 34 同义词通道正对照：'valerate'（ChEBI:31011）的同义词含 'pentanoate'
    _pc_syn = rxn_side_check(
        {"props": {"name": "valerate", "formula": "C5H9O2", "chebi_id": "CHEBI:31011"}},
        {"props": {"equation": "pentanamide + H2O = pentanoate + NH4(+)"}}, False)
    _pc_syn_rebut = rxn_side_check(
        {"props": {"name": "valerate", "formula": "C5H9O2", "chebi_id": "CHEBI:31011"}},
        {"props": {"equation": "pentanamide + H2O = pentanoate + NH4(+)"}}, True)
    _pc_cod = {
        "def-ok": codata_check(N, "TH:pq:gas_const", "R = N_A·k_B"),
        "def-wrong": codata_check(N, "CO:pq:elementary_charge", "R = N_A·k_B"),
    }
    pc_ok = (_pc_rxn == {"reactant-ok": True, "product-wrong": False}
             and _pc_cod == {"def-ok": True, "def-wrong": False}
             and _pc_syn is True and _pc_syn_rebut is None)
    print("   正对照 · R10 侧别（%s）/ R11 定义式（%s）：%s"
          % (_pc_rxn, _pc_cod, "✅ 双向符合预期" if pc_ok else "❌ 不符"))
    print("   正对照 · 同义词通道（命中归侧=%s / 仅相反侧不驳=%s）：%s"
          % (_pc_syn, _pc_syn_rebut,
             "✅ 符合预期" if (_pc_syn is True and _pc_syn_rebut is None) else "❌ 不符"))

    tot = sum(len(v) for v in findings.values())
    print("\n合计候选反驳 %d 条" % tot)
    return findings, undecidable


def unit_str(un):
    p = props_of(un)
    return p.get("symbol") or p.get("name")


def _qname(node, nid):
    """节点 → 物理量规范名（**本脚本自带**解析，不 import verification_model）。"""
    p = props_of(node)
    if p.get("name"):
        return str(p["name"]).strip()
    n = str(nid)
    for pre in ("PQ:", "PB:pq:", "PB:phy:"):
        if n.startswith(pre):
            return n[len(pre):]
    ns, _, rest = n.partition(":")
    for pre in ("pq:", "phy:"):
        if rest.startswith(pre):
            return rest[len(pre):]
    return rest if ns in ("PQ",) else None


def main():
    ap = argparse.ArgumentParser(description="语义边确定性反驳审计器（只读）")
    ap.add_argument("--norm", default=NORM)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    f, und = run(a.norm)
    if a.json:
        json.dump({k: [list(map(str, r)) for r in v] for k, v in f.items()},
                  open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("->", a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
