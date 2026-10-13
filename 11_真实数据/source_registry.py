# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
"""source_registry.py —— 来源登记表（Phase 41 · GPT6 复评 R10：证据血缘/版本/失效）

复评原文（`07_交付物/外部评审/STTP_复评与Apache2.0许可意见_20261011.md`）
----------------------------------------------------------------------
> 「证据『独立』至少要区分三件事：**计算实现独立、数据来源独立、原始实验/文献独立**。
>   …… 建议**记录来源之间的 `derived_from`、`imports_from`、共同文献 ID 和版本**，
>   而不是只保留一个布尔值。」
> 「（证据对象）至少补齐：`claim_id`、断言条件、`source_id + source_version + locator`、
>   **原始内容哈希**、`verifier_version`、**输入哈希**、实际检查项目及结果、**证据血缘**。」
> R10 完成定义：**「来源变更能定位受影响断言并重新验证」**。

本模块提供**唯一权威**的来源登记表，供三处消费：
1. `phase41_lineage_delta.py` —— 给每条边的证据绑定来源、版本与内容哈希；
2. `lineage_invalidation.py` —— 重算哈希、定位受影响断言、传递血缘；
3. `frozen_gate.py` —— 断言「每条断言的来源都已被登记」。

★ 铁律 #57（Phase 40）的反面应用：本表**不从图内反解**，而是逐条手工登记
  `locator`（可核对的本地文件）+ `license`（引自 `DATA_LICENSES.md`）+ `upstream`（血缘）。
  凡「无法确定」的字段一律显式记为 `lineage_recorded=False`，**不猜测**。

字段
----
`kind`     database | curated | derived | model | constructed
`version`  可核对的人类标签（含数据集名/年份）；**权威身份以 content_hash 为准**
`locator`  产生该来源数据的本地文件（相对仓库根）
`upstream` **imports_from 血缘**：本来源直接依赖的上游来源 id（空 = 无上游）
`lineage_recorded`  upstream 是否已确证（False ⇒ 血缘缺口，失效传递不可用）
`license`  引自 `DATA_LICENSES.md` 的条款结论
`redistribution` **机器可读**再分发三态（A03）：`redistributable / internal_only / forbidden`；
   血缘闭包取最严者（`redistribution_closure`）—— 派生对象不得比上游更宽松
`note`     说明

用法
----
    python 11_真实数据/source_registry.py            # 打印登记表 + 计算 content_hash，落盘 JSON
    python 11_真实数据/source_registry.py --check    # 自检：无环 / 上游都存在 / 哈希可算
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

OUT = os.path.join(HERE, "source_registry.json")

SEED_FILES = [
    "10_种子数据/seed_biochem.json",
    "10_种子数据/seed_chem_equilibrium.json",
    "10_种子数据/seed_classical_mechanics.json",
    "10_种子数据/seed_electromagnetism.json",
    "10_种子数据/seed_energy_combustion.json",
    "10_种子数据/seed_inorganic.json",
    "10_种子数据/seed_math_algebra.json",
    "10_种子数据/seed_math_calculus.json",
    "10_种子数据/seed_math_geometry.json",
    "10_种子数据/seed_math_numbertheory.json",
    "10_种子数据/seed_optics.json",
    "10_种子数据/seed_organic_chem.json",
    "10_种子数据/seed_quantum.json",
    "10_种子数据/seed_relativity.json",
    "10_种子数据/seed_stat_mech.json",
    "10_种子数据/seed_thermo.json",
]

# ---------------------------------------------------------------------------
# 唯一权威登记表。**逐条手工登记**；不确定的字段置 `lineage_recorded=False`。
# ---------------------------------------------------------------------------
REGISTRY = {
    # ================= 外部数据库（database） =================
    "PhysicsBabel": dict(
        kind="database", version="PhysicsBabel（本地 raw）", retrieved="2026-10-09",
        locator=["11_真实数据/physicsbabel_raw.json"],
        upstream=[], lineage_recorded=True,
        license="待核对（DATA_LICENSES.md 二）；不入发布包",
        note="方程/量/符号主源；dim_exponents 为方程内指数（Phase 40 起改指 exponent_in_term）"),
    "ElementKG2.0": dict(
        kind="database", version="ElementKG 2.0（本地 raw）", retrieved="2026-10-08",
        locator=["11_真实数据/elementkg10m_raw.json", "11_真实数据/elementkg_raw.json"],
        upstream=[], lineage_recorded=True,
        license="待核对（DATA_LICENSES.md 二）；不入发布包",
        note="元素关系源；自述型关系（same_source_elementkg）"),
    "ChEBI": dict(
        kind="database", version="ChEBI（OLS4 API 缓存）", retrieved="2026-10-09",
        locator=["11_真实数据/chebi_cache.json", "11_真实数据/chebi_synonym_cache.json"],
        upstream=[], lineage_recorded=True,
        license="CC BY 4.0（EMBL-EBI）",
        note="本体主名/同义词/分子式；与 Rhea 分别登记"),
    "Rhea": dict(
        kind="database", version="Rhea（EBI 反应库，本地 raw）", retrieved="2026-10-09",
        locator=["11_真实数据/rhea_raw.json"],
        upstream=[], lineage_recorded=True,
        license="CC BY 4.0（rhea-db.org）",
        note="反应方程源"),
    "chembl_api": dict(
        kind="database", version="ChEMBL（API）", retrieved="2026-10-09",
        locator=["11_真实数据/chembl_raw.json"],
        upstream=[], lineage_recorded=True,
        license="CC BY-SA 3.0 Unported",
        note="药学维度（max_phase/ATC/类药性）"),
    "NIST Chemistry WebBook": dict(
        kind="database", version="NIST Chemistry WebBook（本地 raw+cache）", retrieved="2026-10-10",
        locator=["11_真实数据/webbook_raw.json", "11_真实数据/webbook_cache.json"],
        upstream=[], lineage_recorded=True,
        license="NIST 数据通常可自由使用（附声明）",
        note="热化学桥（std_entropy 等锚定量）"),
    "nist_codata_2022": dict(
        kind="database", version="CODATA 2022（allascii.txt 派生 raw）", retrieved="2026-10-09",
        locator=["11_真实数据/codata2022_raw.json"],
        upstream=[], lineage_recorded=True,
        license="NIST 数据通常可自由使用（附声明）",
        note="基本物理常数数值与单位"),
    "openalex": dict(
        kind="database", version="OpenAlex v0.2（本地 raw）", retrieved="2026-10-09",
        locator=["11_真实数据/openalex_math_raw.json", "11_真实数据/seed_papers_openalex.json"],
        upstream=[], lineage_recorded=True,
        license="元数据 CC0",
        note="文献层（cites/discusses）"),
    "PubChem": dict(
        kind="database", version="PubChem（本地 raw）", retrieved="2026-10-08",
        locator=["11_真实数据/pubchem_mol_raw.json", "11_真实数据/pubchem_validate_raw.json"],
        upstream=[], lineage_recorded=True,
        license="逐字段核对前不外发快照（DATA_LICENSES.md 二）",
        note="分子层（分子式/分子量核验）"),
    "mathxiv": dict(
        kind="database", version="MathXiv/ArxiTeX 样例（项目内加工语料）", retrieved="2026-09-30",
        locator=["06_PoC/sample_mathxiv.json"],
        upstream=[], lineage_recorded=True,
        license="待核对（含 arXiv 派生内容）；不外发原始文本",
        note="数学文献元数据（derived_from 的 source_text_only）"),
    "wikidata": dict(
        kind="database", version="Wikidata（MediaWiki API 离线对齐）", retrieved=None,
        locator=[],
        upstream=[], lineage_recorded=True,
        license="CC0",
        note="实体节点（WD:Q*，节点 props.source='wikidata'）；无入库 raw（API 只读查询）"),

    # ================= 项目自策划（curated） =================
    "curated_seed": dict(
        kind="curated", version="STTP 自策划种子（16 个 seed_*.json）", retrieved=None,
        locator=SEED_FILES,
        upstream=[], lineage_recorded=True,
        license="Apache-2.0（STTP 原创）",
        note="专题种子（热力学/化学平衡/数学/力学/电磁/光学…）"),
    "curated_seed/energy_combustion": dict(
        kind="curated", version="STTP 自策划种子（能燃切片）", retrieved=None,
        locator=["10_种子数据/seed_energy_combustion.json"],
        upstream=[], lineage_recorded=True,
        license="Apache-2.0（STTP 原创）",
        note="燃烧能专题切片（历史独立命名）"),

    # ================= 派生（derived；有确定上游） =================
    "rhea_chebi": dict(
        kind="derived", version="Rhea 方程 × ChEBI 词典匹配", retrieved=None,
        locator=["11_真实数据/rhea_raw.json", "11_真实数据/chebi_cache.json"],
        upstream=["Rhea", "ChEBI"], lineage_recorded=True,
        license="CC BY 4.0（Rhea）/ CC BY 4.0（ChEBI）",
        note="★ 共享词典：一致性命中 ≠ 独立证据（铁律 #51 / 复评 R03）"),
    "openalex-topic-align": dict(
        kind="derived", version="OpenAlex 主题对齐", retrieved=None,
        locator=["11_真实数据/seed_papers_openalex.json"],
        upstream=["openalex"], lineage_recorded=True,
        license="CC0",
        note="文献↔主题词对齐"),
    "Phase13.Gate.B1": dict(
        kind="derived", version="Phase13 门禁 B1（分子式计数独立复算）", retrieved=None,
        locator=["11_真实数据/physicsbabel_raw.json"],
        upstream=["PhysicsBabel"], lineage_recorded=True,
        license="—（项目内派生）",
        note="composed_of 计数复算（parse_formula_independent）"),
    "Phase13.Gate.B2": dict(
        kind="derived", version="Phase13 门禁 B2（周期权威核对）", retrieved=None,
        locator=["11_真实数据/element_reference.py"],
        upstream=["element_reference"], lineage_recorded=True,
        license="—（项目内派生）",
        note="same_period 权威相等"),
    "Phase13.Gate.B3": dict(
        kind="derived", version="Phase13 门禁 B3（量纲严格相等）", retrieved=None,
        locator=["11_真实数据/dimension_table.py"],
        upstream=["dimension_table"], lineage_recorded=True,
        license="—（项目内派生）",
        note="dimensionally_consistent 复算"),
    "Phase13.Gate.B4": dict(
        kind="derived", version="Phase13 门禁 B4（反应侧别模型判定）", retrieved=None,
        locator=["11_真实数据/physicsbabel_raw.json"],
        upstream=["PhysicsBabel"], lineage_recorded=True,
        license="—（项目内派生）",
        note="reactant_of/product_of 模型链接预测（未独立复核）"),
    "Phase13.Gate.B5": dict(
        kind="derived", version="Phase13 门禁 B5（摩尔质量独立复算）", retrieved=None,
        locator=["11_真实数据/element_reference.py"],
        upstream=["PhysicsBabel", "element_reference"], lineage_recorded=True,
        license="—（项目内派生）",
        note="has_quantity 摩尔质量复算"),

    # ================= 构造（constructed；项目内真值表/规则） =================
    "dimension_table": dict(
        kind="constructed", version="STTP 量纲真值表（+ Phase 40 独立 SI 基表）", retrieved=None,
        locator=["11_真实数据/dimension_table.py", "11_真实数据/si_base_table.py"],
        upstream=[], lineage_recorded=True,
        license="Apache-2.0（STTP 原创）",
        note="量纲真值源；Phase 40 已引入独立 SI/ISO 80000 外对照（铁律 #57）"),
    "element_reference": dict(
        kind="constructed", version="STTP 元素参考表（118 元素）", retrieved=None,
        locator=["11_真实数据/element_reference.py"],
        upstream=[], lineage_recorded=True,
        license="Apache-2.0（STTP 原创，数据源 IUPAC）",
        note="元素真值源（周期/族/块/family 与原子量）"),
    "schema-axiom": dict(
        kind="constructed", version="图 schema 公理（part_of 传递律）", retrieved=None,
        locator=[],
        upstream=[], lineage_recorded=True,
        license="Apache-2.0（STTP 原创）",
        note="由 schema 公理直接给出的结构边"),
    "cross_source_alignment": dict(
        kind="constructed", version="跨源对齐（Wikidata 实体对齐）", retrieved=None,
        locator=["06_PoC/sample_mathxiv.json"],
        upstream=["curated_seed", "wikidata"], lineage_recorded=True,
        license="CC0（Wikidata）+ Apache-2.0（STTP 原创）",
        note="数学定义↔Wikidata 实体的 same_as 对齐（multi_source_alignment）"),

    # ================= 模型（model；血缘缺口 = R10 要暴露的问题） =================
    "Phase13.GNN.typed": dict(
        kind="model", version="Phase13 带类型 GNN（typed 推理）", retrieved=None,
        locator=[], upstream=[], lineage_recorded=False,
        license="—（项目内模型产物）",
        note="⚠ 血缘缺口：训练时所用快照版本未记录 ⇒ 无法传递失效（R10 指出的问题本身）"),
    "Phase9.GNN": dict(
        kind="model", version="Phase9 GNN（早期无类型）", retrieved=None,
        locator=[], upstream=[], lineage_recorded=False,
        license="—（项目内模型产物）",
        note="⚠ 血缘缺口：训练快照版本未记录"),
    "Phase9.GNN+LLM": dict(
        kind="model", version="Phase9 GNN + LLM 复核", retrieved=None,
        locator=[], upstream=[], lineage_recorded=False,
        license="—（项目内模型产物）",
        note="⚠ 血缘缺口"),
    "LLM-review": dict(
        kind="model", version="LLM 复核（反应侧别）", retrieved=None,
        locator=[], upstream=[], lineage_recorded=False,
        license="—（项目内模型产物）",
        note="⚠ 血缘缺口"),
    "llm_inferred_gnn": dict(
        kind="model", version="LLM/GNN 推断（模型链接预测）", retrieved=None,
        locator=[], upstream=[], lineage_recorded=False,
        license="—（项目内模型产物）",
        note="⚠ 血缘缺口"),
    "heuristic": dict(
        kind="model", version="启发式规则产出（早期）", retrieved=None,
        locator=[], upstream=[], lineage_recorded=False,
        license="—（项目内模型产物）",
        note="⚠ 血缘缺口"),

    # ================= 哨兵 =================
    "unregistered": dict(
        kind="unregistered", version="(未登记)", retrieved=None,
        locator=[], upstream=[], lineage_recorded=False,
        license="—",
        note="哨兵：任何断言若落到此处 ⇒ 门禁 `evidence_source_bound` FAIL"),
}

# ---------------------------------------------------------------------------
# 缺源回填表（`props.source` 为空时的**确定性**归因；不猜测）
#    键 = (边类型, verifier)；值 = 来源 id。
#    依据：verifier 字段本身即「谁做的」，据此定归因；无法归因者 → unregistered。
#    （Phase 41 侦察坐实：R04a 正名产生的 11 条 uses_operator 桥丢失了 props.source。）
# ---------------------------------------------------------------------------
INFER_RULES = {
    ("same_as", "curated_seed"): "curated_seed",
    ("same_as", "cross_source_alignment"): "cross_source_alignment",
    ("uses_operator", "verification_model.math_operator_presence"): "curated_seed",
    ("has_symbol", "verification_model.symbol_in_source"): "curated_seed",
    ("derived_from", "llm_inferred_gnn"): "llm_inferred_gnn",
    ("has_unit", "dimension_table(量纲) × 单位符号解析"): "curated_seed",
}

VALID_KINDS = ("database", "curated", "derived", "model", "constructed", "unregistered")

# ---------------------------------------------------------------------------
# 再分发三态（Phase 42 · A03：把 `DATA_LICENSES.md` §三.4 的「政策」变成**可执行判据**）
#
#     redistributable  可再分发（CC BY / CC0 / NIST 可自由使用 / STTP 自有 Apache-2.0）
#     internal_only    仅内部（条款待核对、须专项核对、或模型产物而训练数据血缘缺口）
#     forbidden        禁止（未登记来源的哨兵）
#
# ★ 铁律 #64：「政策」必须由程序执行 —— 写在文档里的许可边界若无程序判据即等于没有。
# ★ 铁律 #60：模型产物的**训练快照血缘未记录** ⇒ 不得默认按「可再分发」纳入，保守记为 internal_only。
# ★ fail-closed：本表**必须覆盖 REGISTRY 全部来源**（自检 ⑧ 强制）；未列出的 id ⇒ forbidden，不猜测。
# ---------------------------------------------------------------------------
REDISTRIBUTABLE = "redistributable"
INTERNAL_ONLY = "internal_only"
FORBIDDEN = "forbidden"
REDIST_STATES = (REDISTRIBUTABLE, INTERNAL_ONLY, FORBIDDEN)
# 严格度：越大越严（血缘闭包取 max）
_REDIST_RANK = {REDISTRIBUTABLE: 0, INTERNAL_ONLY: 1, FORBIDDEN: 2}

REDISTRIBUTION = {
    # ---- 外部数据库 ----
    "Rhea": REDISTRIBUTABLE,                       # CC BY 4.0（须署名）
    "ChEBI": REDISTRIBUTABLE,                      # CC BY 4.0（须署名）
    "NIST Chemistry WebBook": REDISTRIBUTABLE,     # NIST 可自由使用（附声明）
    "nist_codata_2022": REDISTRIBUTABLE,           # 同上
    "openalex": REDISTRIBUTABLE,                   # 元数据 CC0
    "wikidata": REDISTRIBUTABLE,                   # CC0
    "chembl_api": INTERNAL_ONLY,                   # CC BY-SA 3.0：对外分发须专项核对（DATA_LICENSES 一）
    "PhysicsBabel": INTERNAL_ONLY,                 # 待核对；不入发布包（DATA_LICENSES 二）
    "ElementKG2.0": INTERNAL_ONLY,                 # 待核对；不入发布包
    "PubChem": INTERNAL_ONLY,                      # 逐字段核对前不外发快照
    "mathxiv": INTERNAL_ONLY,                      # 含 arXiv 派生内容；不外发原始文本
    # ---- 项目自策划 ----
    "curated_seed": REDISTRIBUTABLE,               # Apache-2.0（STTP 原创）
    "curated_seed/energy_combustion": REDISTRIBUTABLE,
    # ---- 构造（真值表/规则）----
    "dimension_table": REDISTRIBUTABLE,            # Apache-2.0（数据源 SI/ISO 80000）
    "element_reference": REDISTRIBUTABLE,          # Apache-2.0（数据源 IUPAC）
    "schema-axiom": REDISTRIBUTABLE,
    "cross_source_alignment": REDISTRIBUTABLE,     # 上游 curated_seed + wikidata
    # ---- 派生（值必须等于血缘闭包，自检 ⑨ 强制）----
    "rhea_chebi": REDISTRIBUTABLE,                 # 上游 Rhea + ChEBI
    "openalex-topic-align": REDISTRIBUTABLE,       # 上游 openalex
    "Phase13.Gate.B1": INTERNAL_ONLY,              # 上游 PhysicsBabel
    "Phase13.Gate.B2": REDISTRIBUTABLE,            # 上游 element_reference
    "Phase13.Gate.B3": REDISTRIBUTABLE,            # 上游 dimension_table
    "Phase13.Gate.B4": INTERNAL_ONLY,              # 上游 PhysicsBabel
    "Phase13.Gate.B5": INTERNAL_ONLY,              # 上游 PhysicsBabel + element_reference
    # ---- 模型（训练快照血缘未记录 ⇒ 保守 internal_only）----
    "Phase13.GNN.typed": INTERNAL_ONLY,
    "Phase9.GNN": INTERNAL_ONLY,
    "Phase9.GNN+LLM": INTERNAL_ONLY,
    "LLM-review": INTERNAL_ONLY,
    "llm_inferred_gnn": INTERNAL_ONLY,
    "heuristic": INTERNAL_ONLY,
    # ---- 哨兵 ----
    "unregistered": FORBIDDEN,
}

# 许可文本中一旦出现这些词，就**不得**标为 redistributable（自检 ⑩，防文档与代码脱钩）
_NON_REDIST_KEYWORDS = ("待核对", "不外发", "不打包", "仅侦察", "订阅", "受限")


# ---------------------------------------------------------------- 基本访问
def load() -> dict:
    return REGISTRY


def ids() -> list:
    return sorted(REGISTRY)


def get(sid: str) -> dict:
    return REGISTRY.get(sid)


def is_registered(sid) -> bool:
    return sid in REGISTRY and sid != "unregistered"


def version_of(sid) -> str:
    e = REGISTRY.get(sid) or {}
    return e.get("version") or "-"


def kind_of(sid) -> str:
    e = REGISTRY.get(sid) or {}
    return e.get("kind") or "unregistered"


# ---------------------------------------------------------------- 再分发（A03）
def redistribution_of(sid) -> str:
    """本来源**自身**的再分发立场。未登记/未列入 ⇒ forbidden（fail-closed）。"""
    return REDISTRIBUTION.get(sid, FORBIDDEN)


def redistribution_closure(sid) -> str:
    """**血缘闭包**下的再分发立场 = 上游链上最严者。
    派生对象不得比其上游更宽松（否则即是把受限内容「洗白」）。"""
    chain = upstream_closure(sid)
    return max((redistribution_of(s) for s in chain),
               key=lambda st: _REDIST_RANK.get(st, 99), default=FORBIDDEN)


def is_redistributable(sid) -> bool:
    return redistribution_closure(sid) == REDISTRIBUTABLE


def check_redistribution_coverage() -> list:
    """⑧ 三态表必须覆盖 REGISTRY 全部来源（否则存在「默认放行/默认禁止」的暗角）。"""
    missing = sorted(set(REGISTRY) - set(REDISTRIBUTION))
    extra = sorted(set(REDISTRIBUTION) - set(REGISTRY))
    bad = [k for k, v in REDISTRIBUTION.items() if v not in REDIST_STATES]
    return [("missing", m) for m in missing] + [("extra", e) for e in extra] + \
           [("bad_state", b) for b in bad]


def check_redistribution_lineage() -> list:
    """⑨ 派生来源的三态**必须等于**其血缘闭包（不得声明得比上游宽松）。"""
    bad = []
    for sid in REGISTRY:
        declared, closure = redistribution_of(sid), redistribution_closure(sid)
        if _REDIST_RANK.get(declared, 99) < _REDIST_RANK.get(closure, 99):
            bad.append((sid, declared, closure))
    return bad


def check_redistribution_vs_license() -> list:
    """⑩ 许可文本里写明「待核对/不外发/不打包/仅侦察/订阅/受限」的来源，
    不得被标为 redistributable（防 `DATA_LICENSES.md` 与代码脱钩 —— 铁律 #61）。"""
    bad = []
    for sid, e in REGISTRY.items():
        lic = str(e.get("license") or "")
        if any(k in lic for k in _NON_REDIST_KEYWORDS) and redistribution_of(sid) == REDISTRIBUTABLE:
            bad.append((sid, lic))
    return bad


# ---------------------------------------------------------------- 内容哈希
def hash_files(rel_paths, base=ROOT) -> str:
    """对一组文件做**确定性** sha256：按 relpath 排序，逐文件喂入
    `relpath + NUL + 内容字节`。缺文件跳过（并计入结果元数据由调用方判断）。"""
    h = hashlib.sha256()
    for rp in sorted(rel_paths):
        ap = os.path.join(base, rp)
        h.update(rp.encode("utf-8"))
        h.update(b"\x00")
        if os.path.exists(ap):
            with open(ap, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
        else:
            h.update(b"<MISSING>")
        h.update(b"\x00")
    return h.hexdigest()


def content_hash_of(sid: str, base=ROOT):
    """来源的**当前**内容哈希；无 locator（如 wikidata/model）返回 None。"""
    e = REGISTRY.get(sid) or {}
    loc = e.get("locator") or []
    if not loc:
        return None
    return hash_files(loc, base)


def hash_map(base=ROOT) -> dict:
    return {sid: content_hash_of(sid, base) for sid in REGISTRY}


def short(x, n=12):
    return (x or "-")[:n]


# ---------------------------------------------------------------- 血缘
def upstream_of(sid) -> list:
    e = REGISTRY.get(sid) or {}
    return list(e.get("upstream") or [])


def upstream_closure(sid) -> set:
    """传递闭包（含自身）：imports_from 的血缘传递。"""
    seen, stack = set(), [sid]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for u in upstream_of(cur):
            if u not in seen:
                stack.append(u)
    return seen


def check_acyclic() -> list:
    """返回检测到的环（list of path）；空 = 无环。"""
    cycles = []
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {s: WHITE for s in REGISTRY}

    def dfs(u, path):
        color[u] = GRAY
        for v in upstream_of(u):
            if v not in REGISTRY:
                continue
            if color[v] == GRAY:
                cycles.append(path + [u, v])
            elif color[v] == WHITE:
                dfs(v, path + [u])
        color[u] = BLACK

    for s in REGISTRY:
        if color[s] == WHITE:
            dfs(s, [])
    return cycles


def check_upstreams_exist() -> list:
    """上游 id 未登记 → 返回 [(sid, missing_upstream)]。"""
    return [(s, u) for s in REGISTRY for u in upstream_of(s) if u not in REGISTRY]


# ------------------------------------------------- 行尾稳定性（铁律 #53 / Phase 41 补）
# 本登记表把**源码内容**也当作证据输入：`content_hash` / 证据的 `src_hash` 都是
# 「文件原始字节的 sha256」。若被登记的文本文件在检出时被 core.autocrlf 改成 CRLF，
# 则干净 clone 上「重算哈希 == 存档哈希」不成立 → 失效机制会把**未改动的来源误判为已变更**。
# 故：凡被登记为 locator 的文件，其扩展名必须在 `.gitattributes` 中**关掉行尾转换**。
# 这两个检查把「隐含假设」变成「门禁可红」（铁律 #16 / #60）。
BYTE_STABLE_GLOBS = ("*.json", "*.lock", "11_真实数据/*.py")


def _matches_any(rel: str, globs) -> bool:
    import fnmatch
    r = rel.replace("\\", "/")
    return any(fnmatch.fnmatch(r, g) for g in globs)


def check_locator_stability() -> list:
    """返回「未锁行尾」的 locator 列表（应为空）。"""
    bad = []
    for sid in sorted(REGISTRY):
        for p in (REGISTRY[sid].get("locator") or []):
            if not _matches_any(p, BYTE_STABLE_GLOBS):
                bad.append((sid, p))
    return bad


def check_gitattributes_declares(globs=BYTE_STABLE_GLOBS) -> list:
    """`.gitattributes` 必须真的声明了这些锁行尾规则；返回缺失的 glob。"""
    ap = os.path.join(ROOT, ".gitattributes")
    if not os.path.exists(ap):
        return list(globs)
    with open(ap, encoding="utf-8") as f:
        txt = f.read()
    return [g for g in globs if g not in txt]


# ---------------------------------------------------------------- 断言的来源归因
def proposer_source(edge) -> tuple:
    """返回 (source_id, inferred: bool)。

    1. `props.source` 非空 → 直接用（inferred=False）
    2. 否则查 `INFER_RULES[(type, verifier)]`（inferred=True）
    3. 否则 `("unregistered", False)` ⇒ 门禁会红（**不静默通过**）
    """
    p = edge.get("props") or {}
    s = p.get("source")
    if s:
        return (s, False)
    rule = INFER_RULES.get((edge.get("type"), p.get("verifier")))
    if rule:
        return (rule, True)
    return ("unregistered", False)


# ---------------------------------------------------------------- 落盘 / 自检
def build_payload(base=ROOT) -> dict:
    hm = hash_map(base)
    out = {}
    for sid in sorted(REGISTRY):
        e = dict(REGISTRY[sid])
        e["content_hash"] = hm.get(sid)
        e["has_locator"] = bool(e.get("locator"))
        # Phase 42 · A03：机器可读的再分发三态（自身 + 血缘闭包）
        e["redistribution"] = redistribution_of(sid)
        e["redistribution_closure"] = redistribution_closure(sid)
        out[sid] = e
    n_no_hash = sum(1 for v in out.values() if v["content_hash"] is None)
    n_gap = sum(1 for v in out.values() if not v.get("lineage_recorded"))
    by_redist = collections.Counter(v["redistribution_closure"] for v in out.values())
    return {
        "schema": "sttp-source-registry/v2",
        "meta": {
            "phase": 42, "theme": "R10 血缘/版本/失效 + A03 再分发三态（政策可执行）",
            "n_sources": len(out),
            "n_without_content_hash": n_no_hash,
            "n_lineage_gap": n_gap,
            "redistribution_closure": dict(sorted(by_redist.items())),
            "redistributable_sources": sorted(s for s in out
                                              if out[s]["redistribution_closure"] == REDISTRIBUTABLE),
            "hash_algo": "sha256(relpath NUL content NUL ...) over sorted locators",
        },
        "sources": out,
    }


def dump(path=OUT, base=ROOT) -> dict:
    payload = build_payload(base)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    return payload


def selfcheck() -> int:
    ok = True
    # ① 无环
    cyc = check_acyclic()
    print("  ① 血缘无环          : %s" % ("PASS" if not cyc else "FAIL %s" % cyc))
    ok &= not cyc
    # ② 上游都存在
    miss = check_upstreams_exist()
    print("  ② 上游 id 均已登记   : %s" % ("PASS" if not miss else "FAIL %s" % miss))
    ok &= not miss
    # ③ kind 合法
    bad = [(s, e.get("kind")) for s, e in REGISTRY.items() if e.get("kind") not in VALID_KINDS]
    print("  ③ kind ∈ 受控词表    : %s" % ("PASS" if not bad else "FAIL %s" % bad))
    ok &= not bad
    # ④ 有 locator 的源必须能算出哈希
    nohash = [s for s in REGISTRY if REGISTRY[s].get("locator") and content_hash_of(s) is None]
    print("  ④ 有 locator ⇒ 有哈希 : %s" % ("PASS" if not nohash else "FAIL %s" % nohash))
    ok &= not nohash
    # ⑤ 血缘缺口必须被显式标记（model 类无 upstream ⇒ lineage_recorded 必须为 False）
    bad2 = [s for s, e in REGISTRY.items()
            if e.get("kind") == "model" and not e.get("upstream") and e.get("lineage_recorded")]
    print("  ⑤ 模型源无上游⇒标记缺口: %s" % ("PASS" if not bad2 else "FAIL %s" % bad2))
    ok &= not bad2
    # ⑥ locator 必须落在「已锁行尾」的扩展名内（否则 clone 上哈希漂移）
    unst = check_locator_stability()
    print("  ⑥ locator 行尾已锁   : %s" % ("PASS" if not unst else "FAIL %s" % unst[:5]))
    ok &= not unst
    # ⑦ .gitattributes 必须真的声明这些锁（防止「检查项与配置脱钩」）
    missg = check_gitattributes_declares()
    print("  ⑦ .gitattributes 已声明: %s" % ("PASS" if not missg else "FAIL 缺 %s" % missg))
    ok &= not missg
    # ⑧ 再分发三态必须覆盖全部来源（A03：政策可执行 + fail-closed）
    cov = check_redistribution_coverage()
    print("  ⑧ 再分发三态全覆盖   : %s" % ("PASS" if not cov else "FAIL %s" % cov[:5]))
    ok &= not cov
    # ⑨ 派生来源的三态不得比其血缘闭包更宽松（A03：派生递归）
    lin = check_redistribution_lineage()
    print("  ⑨ 派生 ≤ 上游严格度  : %s" % ("PASS" if not lin else "FAIL %s" % lin[:5]))
    ok &= not lin
    # ⑩ 许可文本与三态不得脱钩（写「待核对/不外发」却标可再分发）
    dsc = check_redistribution_vs_license()
    print("  ⑩ 许可文本 ↔ 三态   : %s" % ("PASS" if not dsc else "FAIL %s" % dsc[:5]))
    ok &= not dsc
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="STTP 来源登记表（Phase 41 · R10）")
    ap.add_argument("--check", action="store_true", help="只做自检，不落盘")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    print("=" * 92)
    print("来源登记表 source_registry —— Phase 41（R10：证据血缘/版本/失效）")
    print("=" * 92)
    hm = hash_map()
    print("%-32s %-12s %-8s %-16s %s" % ("source_id", "kind", "lineage", "redistribution", "content_hash"))
    for sid in sorted(REGISTRY):
        e = REGISTRY[sid]
        print("%-32s %-12s %-8s %-16s %s" % (
            sid, e.get("kind"), "ok" if e.get("lineage_recorded") else "GAP",
            redistribution_closure(sid),
            short(hm.get(sid)) if hm.get(sid) else "-"))
    n_gap = sum(1 for s in REGISTRY if not REGISTRY[s].get("lineage_recorded"))
    n_nohash = sum(1 for s in REGISTRY if hm.get(s) is None)
    n_red = sum(1 for s in REGISTRY if is_redistributable(s))
    print("-" * 104)
    print("共 %d 个来源 ｜ 血缘缺口 %d ｜ 无内容哈希 %d（无 locator）｜ **可再分发（血缘闭包）%d**"
          % (len(REGISTRY), n_gap, n_nohash, n_red))
    print("【自检】")
    rc = selfcheck()
    print("  ⇒ %s" % ("全过" if rc == 0 else "未通过"))
    if not a.check:
        payload = dump(a.out)
        print("-> %s（%d 源）" % (os.path.relpath(a.out, ROOT), payload["meta"]["n_sources"]))
    return rc


if __name__ == "__main__":
    sys.exit(main())
