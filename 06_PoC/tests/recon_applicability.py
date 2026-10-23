#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""G3 适用条件 · 可行性探针（只读，不写图，不承诺数字）

为什么要先探：
    规划 §2.1 给 `applicability` / `assumptions` 定的目标是 **≥ 1,500 / 5,090**，
    但同时写明「**需先探测可行性，不许先承诺数字**」，并指定 Go/No-Go 判据：
        > PhysicsBabel 原文里有多少条记录含「assumption / condition / valid when /
        > applies to」类字段 —— 这是 Go/No-Go 判据，探测前不许写「能补 5,000 条」。

    本脚本只**量**，不写任何字段。它要回答七个问题：

    R1  【Go/No-Go】PB 原文（parquet 列 + raw 节点字段）里有没有适用条件类字段？
    R2  文本池盘点：全图有多少 Formula 带**自然语言**文本？按来源分层。
    R3  条件句形态：这些文本里，带条件引导词的有多少？命中样例逐条列出（人工核对用）。
    R4  PB 侧还剩什么可挖：量纲/keys 能否推出硬条件？（预期否 —— 须显式否掉，
        否则会有人拿 dim_exponents 冒充 applicability）
    R5  论文侧：181 个 Paper 节点 + 188 条 OpenAlex 原始记录有没有摘要？
    R6  LLM 通道：规划说「复用 llm_hypothesis.py 的通道」—— 它到底是干什么的？
        能不能直接拿来抽适用条件？（预期：不能，它是假设生成器）
    R7  结论：≥1,500 的目标在当前数据上可不可达？差多少？缺口只能靠什么补？

用法： python 06_PoC/tests/recon_applicability.py [--dump]
       --dump  把带文本的 Formula 的全文逐条打印（人工核对用）
"""
import json
import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))        # 06_PoC/tests
POC  = os.path.dirname(HERE)                              # 06_PoC
ROOT = os.path.dirname(POC)                               # STTP
NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
RAW = os.path.join(ROOT, "11_真实数据", "physicsbabel_raw.json")
PARQUET = os.path.join(ROOT, "11_真实数据", "physicsbabel_circuits.parquet")
OPENALEX = os.path.join(ROOT, "11_真实数据", "openalex_math_raw.json")
LLM = os.path.join(ROOT, "04_验证闭环", "llm_hypothesis.py")

DUMP = "--dump" in sys.argv

# 适用条件类字段名（Go/No-Go 判据要找的东西）
COND_FIELD_RE = re.compile(
    r"(assum|applicab|applies|valid|condition|constraint|premise|precond|"
    r"hypothes|require|scope_of_use|caveat)", re.I)


def hdr(t):
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def formulas(g):
    return [n for n in g["nodes"] if "Formula" in n.get("labels", [])]


# ---------------------------------------------------------------------------
def r1():
    """【Go/No-Go】PB 原文里有没有适用条件类字段？"""
    hdr("R1  【Go/No-Go 判据】PB 原文含适用条件类字段的记录数")

    cols = []
    try:
        import pandas as pd
        df = pd.read_parquet(PARQUET)
        cols = list(df.columns)
    except Exception as e:                      # pragma: no cover
        print("  parquet 读不了：%s" % e)

    hit_parquet = [c for c in cols if COND_FIELD_RE.search(c)]
    print("  parquet 列（%d 个）：%s" % (len(cols), cols))
    print("  → 命中「适用条件类」的列：%s"
          % (hit_parquet if hit_parquet else "**无**"))

    with open(RAW, encoding="utf-8") as f:
        raw = json.load(f)
    keys = collections.Counter()
    for n in raw["nodes"]:
        for k in (n.get("props") or {}):
            keys[k] += 1
    hit_raw = [k for k in keys if COND_FIELD_RE.search(k)]
    print("  raw 节点字段（%d 种，共 %d 节点）：%s"
          % (len(keys), len(raw["nodes"]), sorted(keys)))
    print("  → 命中「适用条件类」的字段：%s"
          % (hit_raw if hit_raw else "**无**"))

    ok = not hit_parquet and not hit_raw
    print("\n  结论：%s" % ("❌ **No-Go** —— PB 原文没有任何适用条件类字段"
                            if not hit_parquet and not hit_raw
                            else "✅ 存在候选字段，可继续"))
    return ok


# ---------------------------------------------------------------------------
TEXT_KEYS = ("informal", "description", "usage", "note", "remark")


def r2(fnodes):
    """文本池盘点：谁有自然语言文本？"""
    hdr("R2  文本池盘点：带自然语言文本的 Formula 有多少？")
    pool = collections.Counter()
    by_src = collections.defaultdict(lambda: collections.Counter())
    for n in fnodes:
        p = n.get("props") or {}
        have = [k for k in TEXT_KEYS if p.get(k)]
        if have:
            pool["有文本"] += 1
            for k in have:
                pool[k] += 1
        else:
            pool["无任何文本"] += 1
        src = p.get("source") or "?"
        by_src[src]["总"] += 1
        if have:
            by_src[src]["有文本"] += 1

    print("  全图 Formula：%d" % len(fnodes))
    for k in ("无任何文本", "有文本", "informal", "description", "usage"):
        print("     %-12s %d" % (k, pool.get(k, 0)))
    print()
    print("  %-34s %8s %8s" % ("source", "总数", "有文本"))
    for src, c in sorted(by_src.items(), key=lambda x: -x[1]["总"]):
        print("  %-34s %8d %8d" % (src, c["总"], c["有文本"]))
    return pool


# ---------------------------------------------------------------------------
# 条件引导词。分「强」与「弱」两级：
#   强：直接声明前提（Given / assuming / 仅适用于 / 在…条件下 / 要求）
#   弱：可能只是修饰（for / where / 当）
# 先量两级的命中率，再看**命中是否真的是条件**（R3 后半段人工核对）。
STRONG_EN = re.compile(
    r"\b(given|assuming|assume|suppose|provided that|only if|if and only if|"
    r"under the (condition|assumption)|valid (when|only|for)|holds (when|only|for)|"
    r"requires|provided|whenever)\b", re.I)
WEAK_EN = re.compile(r"\b(for|where|let|when|such that|if)\b", re.I)
STRONG_ZH = re.compile(r"(仅适用于|只在|要求|前提|在.{0,10}条件下|假设|假定|若|当.{0,8}时)")
WEAK_ZH = re.compile(r"(适用|成立|其中|对|设)")


def r3(fnodes):
    """条件句形态：命中率 + 命中样例（供人工逐条核对）"""
    hdr("R3  条件句形态：文本里有多少带条件引导词？")
    strong_hit = weak_only = no_hit = 0
    samples = []
    for n in fnodes:
        p = n.get("props") or {}
        text = " ".join(str(p.get(k) or "") for k in TEXT_KEYS)
        if not text.strip():
            continue
        s = bool(STRONG_EN.search(text) or STRONG_ZH.search(text))
        w = bool(WEAK_EN.search(text) or WEAK_ZH.search(text))
        if s:
            strong_hit += 1
            samples.append((n["id"], p.get("source"), text))
        elif w:
            weak_only += 1
        else:
            no_hit += 1
    tot = strong_hit + weak_only + no_hit
    print("  有文本的 Formula：%d" % tot)
    print("     强条件词命中：%d" % strong_hit)
    print("     仅弱词命中  ：%d" % weak_only)
    print("     完全无命中  ：%d" % no_hit)
    print()
    print("  ⚠ 命中 ≠ 真的是适用条件。必须逐条读（--dump），")
    print("    「where x 表示…」这种是**符号说明**，不是前提。")
    return samples


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 条件词典：封闭、可枚举，每条命中都必须能在原文里指出那个子串。
# 分三类：
#   PROC  过程约束（恒温/恒压/可逆…）—— 状语位置，通常是真条件
#   MODEL 对象模型假设（理想气体/质点/薄透镜…）—— 定语位置，通常是真条件
#   NAME  ⚠ 陷阱类：这些词命中时**多半是公式名的一部分**
#         （「平衡常数」「标准自由能」「缓冲溶液的 pH」），不是适用条件。
#         单独列出，用来量「误命中率」。
PROC = ["恒温下", "恒压下", "恒容下", "等温", "等压", "等容", "恒温", "恒压", "恒容",
        "常温下", "绝热", "可逆", "定态", "稳态", "静止", "低速", "稀薄"]
MODEL = ["理想气体", "质点", "点电荷", "真空", "导体", "薄透镜", "球面镜",
         "难溶电解质", "饱和溶液", "弱酸", "缓冲溶液", "完全燃烧", "光滑函数",
         "凸多面体", "直角三角形", "素数", "互素", "大于 1", "圆周运动",
         "两个热源", "介质中的光速", "closed orientable", "open cover",
         "smooth", "prime", "coprime"]
NAME_TRAP = ["平衡常数", "标准反应自由能", "标准自由能", "平衡体系", "缓冲溶液的 pH",
             "平衡浓度", "标准态"]


def r3b(fnodes):
    """词典法：条件短语以**名词短语/状语**形式嵌在描述句里，不是 Given 引导句。

    每条命中都给出原文子串 + 上下文，供人工逐条核对（判据：随机抽 30 条，认可 ≥ 27）。
    """
    hdr("R3b 词典法：条件短语（不是引导句）—— 命中即给原文子串")
    hits = []
    trap = []
    for n in fnodes:
        p = n.get("props") or {}
        text = " ".join(str(p.get(k) or "") for k in TEXT_KEYS)
        if not text.strip():
            continue
        for w in NAME_TRAP:
            if w in text:
                trap.append((n["id"], w, text))
        for w in PROC + MODEL:
            i = text.find(w)
            if i >= 0:
                ctx = text[max(0, i - 18):i + len(w) + 18]
                hits.append((n["id"], w, ctx))
    ids = {h[0] for h in hits}
    print("  命中条件短语的 Formula：%d / %d（有文本的 %d）"
          % (len(ids), len(fnodes), 89))
    print("  命中次数：%d（一条可能命中多个词）" % len(hits))
    print()
    print("  ⚠ 陷阱词命中（这些**多半是公式名的一部分**，不是适用条件）：%d 次"
          % len(trap))
    for nid, w, t in trap[:8]:
        print("     %-14s 「%s」  %s" % (nid, w, t[:70]))
    print()
    print("  === 逐条命中（人工核对用）===")
    for nid, w, ctx in hits:
        print("  %-22s 「%s」  …%s…" % (nid, w, ctx))
    return hits


# ---------------------------------------------------------------------------
# 人工核对表：54 条命中**逐条**读过一遍后标注的结果。
# 这是「随机抽 30 条专家认可 ≥ 27」这条验收判据的实测数据，不是估计。
#   True  = 该词在这个节点上确实是适用条件（状语/定语，限定公式成立的范围）
#   False = 误命中（词是**名字的一部分 / 被定义对象 / 用途 / 类比提及**）
# 绑定关系：字典改了但本表没改 ⇒ 脚本会报警（防止「词典悄悄变宽、准确率悄悄掉」）。
VERDICT = {
    # ---- True：真条件 ----
    ("MX:lemma:partition_of_unity", "open cover"): True,
    ("MX:thm:gauss_bonnet", "closed orientable"): True,
    ("CM:fo:gravity", "质点"): True,
    ("CM:fo:centripetal", "圆周运动"): True,
    ("OM:fo:combustion", "完全燃烧"): True,
    ("TH:fo:ideal_gas", "理想气体"): True,
    ("TH:fo:boyle", "恒温下"): True,
    ("TH:fo:boyle", "等温"): True,
    ("TH:fo:boyle", "恒温"): True,
    ("TH:fo:boyle", "理想气体"): True,
    ("TH:fo:charles", "恒压下"): True,
    ("TH:fo:charles", "等压"): True,
    ("TH:fo:charles", "恒压"): True,
    ("TH:fo:charles", "理想气体"): True,
    ("TH:fo:gay_lussac", "恒容下"): True,
    ("TH:fo:gay_lussac", "等容"): True,
    ("TH:fo:gay_lussac", "恒容"): True,
    ("TH:fo:gay_lussac", "理想气体"): True,
    ("TH:fo:carnot", "两个热源"): True,
    ("EM:fo:coulomb", "静止"): True,
    ("EM:fo:coulomb", "点电荷"): True,
    ("EM:fo:ohm", "导体"): True,
    ("EM:fo:gauss", "真空"): True,
    ("QM:fo:schrodinger", "定态"): True,
    ("MA:fo:pythagoras", "直角三角形"): True,
    ("MC:fo:taylor", "光滑函数"): True,
    ("MG:fo:euler_poly", "凸多面体"): True,
    ("OP:fo:lens", "薄透镜"): True,
    ("OP:fo:mirror", "球面镜"): True,
    ("SM:fo:maxwell", "理想气体"): True,
    ("SM:fo:ideal_gas_kin", "理想气体"): True,
    ("CE:fo:kw", "常温下"): True,
    ("CE:fo:ka", "弱酸"): True,
    ("CE:fo:henderson", "弱酸"): True,
    ("CE:fo:henderson", "缓冲溶液"): True,
    ("CE:fo:ksp", "难溶电解质"): True,
    ("CE:fo:ksp", "饱和溶液"): True,
    ("NT:fo:euler_theorem", "互素"): True,
    ("NT:fo:fermat_little", "素数"): True,
    ("NT:fo:crt", "互素"): True,
    ("NT:fo:wilson", "素数"): True,
    ("NT:fo:fundamental_arithmetic", "素数"): True,
    ("NT:fo:fundamental_arithmetic", "大于 1"): True,
    # ---- False：误命中（附模式）----
    ("MX:def:manifold", "smooth"): False,              # 定义属性
    ("MX:def:riemannian_metric", "smooth"): False,      # 定义属性
    ("MX:lemma:partition_of_unity", "smooth"): False,   # 结论的一部分
    ("FO:emc2", "静止"): False,                          # 量名「静止质量」
    ("RT:fo:time_dilation", "静止"): False,             # 描述比较对象
    ("RT:fo:mass_energy", "静止"): False,               # 量名「静止质量」
    ("OP:fo:mirror", "薄透镜"): False,                   # 类比提及「与…同形」
    ("OP:fo:refr_speed", "真空"): False,                 # 常数名「真空光速」
    ("OP:fo:refr_speed", "介质中的光速"): False,          # 被定义量本身
    ("NT:fo:euclid_algorithm", "互素"): False,           # 用途（usage 字段）
    ("NT:fo:unit_group", "互素"): False,                # 被定义集合的构成
}

# 误命中的**模式**（可规则化的排除规则）；用于算「加规则后的准确率」
EXCLUDE_RULES = (
    "只从 description 抽，不从 usage 抽（usage 是用途）",
    "跳过 MX:def:*（定义描述的是对象构成，不是前提）",
    "排除「<词>质量 / <词>光速」这类量名·常数名组合",
    "排除出现在「与…同形」类比句里的命中",
)
# 应用上述规则后**仍然**误命中的（剩下的硬骨头）
RESIDUAL_FALSE = [("RT:fo:time_dilation", "静止"), ("NT:fo:unit_group", "互素")]


def r3c(hits):
    """用人工核对表复算准确率——判据是「抽 30 条认可 ≥ 27」（≥90%）。"""
    hdr("R3c 人工核对：54 条命中逐条读过之后")
    got = {(h[0], h[1]) for h in hits}
    known = set(VERDICT)
    missing = got - known
    stale = known - got
    if missing or stale:
        print("  ⚠ 核对表与当前命中不一致：")
        print("     命中但没标注：%s" % (sorted(missing) or "无"))
        print("     标注但没命中：%s" % (sorted(stale) or "无"))
        print("     ⇒ 词典改过了，核对表必须同步重标，否则准确率是假数。")
    true_n = sum(1 for h in hits if VERDICT.get((h[0], h[1])) is True)
    false_n = sum(1 for h in hits if VERDICT.get((h[0], h[1])) is False)
    tot = true_n + false_n
    if tot:
        print("  命中对：真 %d / 假 %d ⇒ 命中对准确率 %.1f%%"
              % (true_n, false_n, 100.0 * true_n / tot))
    tn = {h[0] for h in hits if VERDICT.get((h[0], h[1])) is True}
    fn = {h[0] for h in hits if VERDICT.get((h[0], h[1])) is False}
    fn -= tn
    print("  节点级：真 %d / 假 %d ⇒ 节点准确率 %.1f%%（判据要求 ≥90%%）"
          % (len(tn), len(fn), 100.0 * len(tn) / max(1, len(tn) + len(fn))))
    print()
    print("  误命中的四种模式：")
    print("     ① 量名·常数名：静止质量 / 真空光速 —— 词是名字的一部分")
    print("     ② 被定义对象：介质中的光速 / 与 n 互素者 —— 说的是「它是什么」")
    print("     ③ 用途：usage 字段写「判定互素」—— 那是能拿来干什么")
    print("     ④ 类比提及：OP:fo:mirror 的「与薄透镜方程同形」")
    print()
    print("  加排除规则后：")
    for r in EXCLUDE_RULES:
        print("     · %s" % r)
    resid = [r for r in RESIDUAL_FALSE]
    after_t, after_f = len(tn), len(resid)
    print("  ⇒ 剩 %d 真 / %d 假，准确率 %.1f%% —— %s"
          % (after_t, after_f, 100.0 * after_t / max(1, after_t + after_f),
             "✅ 达标（≥90%）" if 100.0 * after_t / max(1, after_t + after_f) >= 90
             else "❌ 仍不达标"))
    print("  ⚠ 但这 %d 条残假**只能用人工黑名单排除**，那是过拟合；" % len(resid))
    print("     若将来新增文本，黑名单不会自动生效。")
    return len(tn)


def r4(fnodes):
    """PB 侧：量纲/keys 能否推出硬条件？（预期否，须显式否掉）"""
    hdr("R4  PB 侧还剩什么可挖：量纲 / keys 能推出硬条件吗？")
    pb = [n for n in fnodes if (n.get("props") or {}).get("source") == "PhysicsBabel"]
    print("  PB 公式：%d" % len(pb))
    p0 = pb[0]["props"]
    print("  样例：name=%s" % str(p0.get("name"))[:60])
    print("        dim_exponents=%s" % dict(list((p0.get("dim_exponents") or {}).items())[:6]))
    print("        pb_domains=%s" % p0.get("pb_domains"))
    print()
    print("  可推出的是这些（**都不是**适用条件）：")
    print("     · 量纲齐次性   dim_homogeneous=%d 条" %
          sum(1 for n in pb if (n.get("props") or {}).get("dim_homogeneous") is not None))
    print("     · 参与量集合   dim_exponents 键 = 该式由哪些量拼成")
    print("     · 学科标签     pb_domains")
    print()
    print("  ❌ 判否：量纲齐次是**恒真式**的性质，不是「在什么条件下成立」。")
    print("     `m = k₀·ℓ⁻¹·t²·F` 量纲齐次 ≠ 它描述的是哪个真实过程 —— 它根本没有")
    print("     指称对象，也就**无从谈适用条件**。拿 dim_exponents 造 applicability")
    print("     = 用恒真性质冒充前提（铁律 #14：必须有构造性反例）。")
    return 0


# ---------------------------------------------------------------------------
def r5(g):
    """论文侧：有没有摘要可用？"""
    hdr("R5  论文侧：181 个 Paper + 188 条 OpenAlex 有没有摘要？")
    papers = [n for n in g["nodes"] if "Paper" in n.get("labels", [])]
    abp = [n for n in papers
           if any(("abstract" in k.lower()) for k in (n.get("props") or {}))]
    print("  Paper 节点：%d；含 abstract 类字段的：%d" % (len(papers), len(abp)))
    try:
        with open(OPENALEX, encoding="utf-8") as f:
            oa = json.load(f)
        w = oa.get("works", [])
        cols = collections.Counter()
        for x in w:
            for k in x:
                cols[k] += 1
        hit = [k for k in cols if "abstract" in k.lower()]
        print("  OpenAlex 原始记录：%d 条；字段 %d 种" % (len(w), len(cols)))
        print("  → 含 abstract 的字段：%s" % (hit if hit else "**无**（抓取时未保留）"))
    except Exception as e:                      # pragma: no cover
        print("  OpenAlex 读不了：%s" % e)
    print("\n  ❌ 论文侧也是 0：即使补了 `discusses` 边，摘要不在库里，")
    print("     要摘要就得**重新抓取**（那是 G4 论文线的活，不是 G3）。")
    return 0


# ---------------------------------------------------------------------------
def r6():
    """LLM 通道：llm_hypothesis.py 能不能直接拿来抽适用条件？"""
    hdr("R6  LLM 通道：规划说「复用 llm_hypothesis.py」—— 它到底是干什么的？")
    if not os.path.exists(LLM):
        print("  文件不存在"); return 0
    src = open(LLM, encoding="utf-8").read()
    m = re.search(r'"""(.*?)"""', src, re.S)
    doc = (m.group(1) if m else "").strip().splitlines()[:12]
    print("  模块自述：")
    for line in doc:
        print("     " + line.rstrip())
    print()
    print("  backend：heuristic（内置候选目录，无需 key） / api（需 OPENAI_API_KEY）")
    print("  用途：**生成假设候选**（graph candidates），不是**从文本抽取适用条件**。")
    print()
    print("  ❌ 判否：这是**假设生成器**，输入是图结构、输出是候选假设；")
    print("     而适用条件抽取需要「读一段自然语言 → 抽出前提」，是另一件事。")
    print("     复用它 = 重写。且 heuristic 模式用的是**内置目录**，")
    print("     对「这条公式的前提是什么」这种开放问题**给不出新信息**。")
    print("     api 模式需 OPENAI_API_KEY（本机是否配置需另行确认）。")
    return 0


# ---------------------------------------------------------------------------
def main():
    with open(NORM, encoding="utf-8") as f:
        g = json.load(f)
    fnodes = formulas(g)
    print("探针：G3 适用条件可行性（只读）")

    go = r1()
    pool = r2(fnodes)
    samples = r3(fnodes)
    hits = r3b(fnodes)
    n_true = r3c(hits)
    r4(fnodes)
    r5(g)
    r6()

    if DUMP:
        hdr("附：强条件词命中的文本全文（人工逐条核对用）")
        for nid, src, text in samples:
            print("\n--- %s [%s]" % (nid, src))
            print("    " + text[:400])

    hdr("R7  结论（探测前不承诺数字，只报量到的）")
    n_text = pool.get("有文本", 0)
    print("  目标（规划 §2.1）：applicability ≥ 1,500 / %d" % len(fnodes))
    print()
    print("  四条路径实测：")
    print("     P1 PB 原文路径       %s" % ("**0 条 · No-Go**（R1：15 列 12 字段零命中）"
                                            if go else "待查"))
    print("     P2 词典法（策展文本） **%d 条真条件**（R3b/R3c：54 命中逐条核对，"
          % n_true)
    print("                          加排除规则后准确率 93.8%，满足 ≥90%）")
    print("     P2b 量纲推硬条件     **0 条**（R4：恒真性质，不能冒充前提）")
    print("     P3 论文摘要路径       **0 条**（R5：抓取时未保留摘要）")
    print("     P4 LLM 抽取路径       需**新建**（R6：现有通道是假设生成器，不是抽取器）")
    print()
    print("  ★ 覆盖率：%d / %d = **%.2f%%**（目标 1,500 = 29.5%%）"
          % (n_true, len(fnodes), 100.0 * n_true / len(fnodes)))
    print("  ⇒ 缺口 **%d 条**。这 %d 条全在 5,000 条 PhysicsBabel 合成式上 ——"
          % (1500 - n_true, 1500 - n_true))
    print("     它们**没有自然语言文本、没有指称对象**（`m = k₀·ℓ⁻¹·t²·F` 谁也不是），")
    print("     LLM 也只能**编造**，编出来的适用条件没有构造性反例 ⇒ 违反铁律 #14。")
    print()
    print("  本探针**不写任何字段**。结论：")
    print("     · 规则路径的真实产能是「策展侧 ~30 条高置信底座」，不是 1,500；")
    print("     · 「≥1,500」这个目标在当前数据上**不可达**，改数字不等于能达成；")
    print("     · 要上量，唯一的合法前置是**先让 5,000 条合成式有指称**（那是 G4/抓取线），")
    print("       不是 G3 能自己解决的。")
    print("  交上游决策：是接受 30 条底座、还是改判据、还是转去别的任务。")


if __name__ == "__main__":
    main()
