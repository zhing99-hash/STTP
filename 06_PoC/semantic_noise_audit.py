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


def _norm_text(s):
    return _STRIP.sub("", _LATEX_CMD.sub(r"\1", str(s)))


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

    # ---- 汇报 ----
    print("=" * 92)
    print("语义边确定性反驳审计 —— %s" % os.path.relpath(path, ROOT))
    print("  规模 %d 节点 / %d 边" % (len(N), len(E)))
    print("=" * 92)
    for k in sorted(findings):
        rows = findings[k]
        def _k(r):
            return r[3] if k.startswith(("R1", "R2")) else (r[5] if len(r) > 5 else "-")
        kc = collections.Counter(_k(r) for r in rows)
        print("\n【%s】%d 条   按 kind：%s" % (k, len(rows), dict(kc)))
        for r in rows[:14]:
            print("   -", r)
    print("\n【不可判定（不撤）】", dict(undecidable))
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
