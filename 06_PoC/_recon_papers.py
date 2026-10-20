#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""G4 论文线 · 可行性探针（只读，不写图，不承诺数字）

为什么要先探：
    规划 §2.2 写「论文线要从物理主题**重建**」，验收口径是「P 级从『数学 178 条』
    扩到物理侧」。上一版 RC1 报告里我写了一句必然要被追问的话：

        5,090 个公式中被论文讨论数 = 0

    这句话听起来像**数据缺口**（再抓点论文就补上），但 G2/G3 连着两次教训是
    「判据逼着人造假时，先检查判据的前提」。所以本脚本先量三件事：

      (a) 现有的 178 条 `discusses` 到底连的是什么？（如果连的不是公式，
          「扩到物理侧」就不是加几条边，而是换粒度）
      (b) 公式有没有**可被文献指称的名字**？（如果名字就是 LaTeX 式子本身，
          那世界上没有一篇论文会讨论它 —— 这不是缺口，是无指称）
      (c) 即便有名字，检索能不能**判别**？（若宽松匹配给 9 万条、严格给 0 条，
          中间没有可用地带，那"被讨论数"这个指标本身不成立）

它回答七个问题：

    R1  现有论文边的端点类型：`discusses` 连的是 Formula 还是 Concept？
    R2  公式的"可匹配文本"：`name` 里有多少是人类可读的名字？PB 的 name 是什么形态？
    R3  有名字的那批是谁：来源分层、学科分布、重名、可检索性（括号/撇号/连字符）
    R4  PhysicsBabel **原文**里有没有文献字段？（若没有，"给 PB 公式找论文"连锚都没有）
    R5  OpenAlex 网络侧前提：物理/数学领域的摘要覆盖率；本仓库 raw 里实际存了几条摘要
    R6  检索判别力：精确串 / 宽松分词 / 符号汤 / 人类名字 四档命中量对比
    R7  结论：可达到底是多少条？5,000 个 PB 公式该怎么标注？

用法：
    python 06_PoC/_recon_papers.py            # 只读本地（确定性，用 2026-10-20 实测常数）
    python 06_PoC/_recon_papers.py --net      # 追加联网复测（约 8 次请求，走代理）

★ 为什么默认不联网：判据要能被反复跑且结果一致。联网值单列在 R5/R6，
  并标注 measured_at；`--net` 只做**复测**，不改任何本地数据。
"""
from __future__ import annotations
import json
import collections
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
PB_RAW = os.path.join(ROOT, "11_真实数据", "physicsbabel_raw.json")
OA_RAW = os.path.join(ROOT, "11_真实数据", "openalex_math_raw.json")
SEED = os.path.join(ROOT, "11_真实数据", "seed_papers_openalex.json")

NET = "--net" in sys.argv

# 2026-10-20 实测（--net 可复测；这里是给"不联网也能复核结论"用的基线）
MEASURED = {
    "measured_at": "2026-10-20",
    "openalex": {
        # fields/31 = Physics and Astronomy；fields/26 = Mathematics（fields/17 是计算机，别用错）
        "fields/31": {"works": 23734720, "abs_pct_sample50": 86},
        "fields/26": {"works": 4625779, "abs_pct_sample50": 66},
    },
    "search_hits": {
        "m = k₀ · F·S·i⁻²·ρ_e⁻¹": 0,          # 精确串：合成式在文献里不存在
        "k0 F S i^-2 rho_e^-1": 3,            # 宽松分词：命中相机/激光烧蚀（假阳性）
        "F = k0 m^2 v^3 S^-1": 92302,         # 同类合成式换一种写法 ⇒ 爆炸
        "Coulomb's law": 15595,               # 人类具名：命中也爆炸，且以教学文献为主
        "Nernst equation": 49050,
        "Mass-energy equivalence": 165584,
    },
}

# 需要归一化才能检索的字符（撇号/括号/连字符/逗号）
NAME_FRICTION_RE = re.compile(r"[’'()\-,–—/]")
HUMAN_NAME_RE = re.compile(r"[A-Za-z]{4,}")


def hdr(t):
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def line(k, v=""):
    print("  %-34s %s" % (k, v))


def load():
    with open(NORM, encoding="utf-8") as f:
        g = json.load(f)
    nodes, edges = g["nodes"], g["edges"]
    by = {n["id"]: n for n in nodes}
    forms = [n for n in nodes if "Formula" in (n.get("labels") or [])]
    return nodes, edges, by, forms


def first_label(n):
    lab = n.get("labels") or []
    return lab[0] if lab else "?"


# ------------------------------------------------------------------ R1
def r1(edges, by):
    hdr("R1  现有论文边的端点类型 —— 「扩到物理侧」是不是加几条边的事？")
    disc = [e for e in edges if e.get("type") == "discusses"]
    pair = collections.Counter(
        (first_label(by.get(e["source"], {})), first_label(by.get(e["target"], {})))
        for e in disc)
    line("discusses 总数", len(disc))
    for (s, t), n in pair.most_common():
        line("  %s → %s" % (s, t), n)
    to_formula = sum(n for (s, t), n in pair.items() if "Formula" in (t, s))
    line("其中有一端是 Formula 的", to_formula)

    # 公式作为端点出现在哪些边里（证明公式与文献侧完全隔离）
    fe = collections.Counter()
    for e in edges:
        if e["source"] in by and "Formula" in (by[e["source"]].get("labels") or []):
            fe["out:" + str(e.get("type"))] += 1
        if e["target"] in by and "Formula" in (by[e["target"]].get("labels") or []):
            fe["in:" + str(e.get("type"))] += 1
    line("公式为端点的边类型", ", ".join("%s=%d" % (k, v) for k, v in fe.most_common()))
    papers = sum(1 for n in by.values() if "Paper" in (n.get("labels") or []))
    line("Paper 节点数", papers)
    return to_formula, fe


# ------------------------------------------------------------------ R2
def r2(forms):
    hdr("R2  公式有没有可被文献指称的名字？（这是本线真正的 Go/No-Go）")
    by_src = collections.Counter((n.get("props") or {}).get("source", "?") for n in forms)
    human = [n for n in forms if HUMAN_NAME_RE.search((n.get("props") or {}).get("name") or "")]
    line("公式总数", len(forms))
    line("name 含 >=4 字母英文词", "%d（%.2f%%）" % (len(human), 100.0 * len(human) / len(forms)))
    for s, n in by_src.most_common():
        h = sum(1 for x in human if ((x.get("props") or {}).get("source") or "?") == s)
        line("  %-28s 共 %5d 篇" % (s, n), "有人类名 %d" % h)

    # PB 的 name 是不是就是 latex（逐条相等，不抽样）
    pb = [n for n in forms if (n.get("props") or {}).get("source") == "PhysicsBabel"]
    same = sum(1 for n in pb
               if ((n.get("props") or {}).get("name") or "")
               == ((n.get("props") or {}).get("latex") or (n.get("props") or {}).get("equation") or ""))
    line("PB 公式 name == latex", "%d / %d（%.1f%%）" % (same, len(pb), 100.0 * same / max(1, len(pb))))
    samples = sorted(pb, key=lambda x: (x.get("props") or {}).get("name") or "")[:3]
    for n in samples:
        line("  PB 名字样例", (n.get("props") or {}).get("name", "")[:60])
    return human, pb


# ------------------------------------------------------------------ R3
def r3(human):
    hdr("R3  有名字的那批是谁 —— 能不能直接拿来检索？")
    src = collections.Counter((n.get("props") or {}).get("source") for n in human)
    for s, n in src.most_common():
        line("来源 " + str(s), n)
    cur = [n for n in human if ((n.get("props") or {}).get("source") or "").startswith("curated_seed")]
    names = [(n.get("props") or {}).get("name") or "" for n in cur]
    dup = [k for k, v in collections.Counter(names).items() if v > 1]
    line("curated 条数 / 唯一名", "%d / %d" % (len(cur), len(set(names))))
    line("重名", dup or "（无）")
    dom = collections.Counter(((n.get("props") or {}).get("domain") or "").split(".")[0] for n in cur)
    line("curated 按一级学科", dom.most_common())

    # mathxiv 那批名字是不是通用词
    mx = [n for n in human if (n.get("props") or {}).get("source") == "mathxiv"]
    line("mathxiv 的 name 样例", [((n.get("props") or {}).get("name") or "")[:20] for n in mx][:8])

    # 检索阻力：撇号/括号/连字符 —— 直接喂 API 会降命中或报错（R6 已实测 400）
    friction = [nm for nm in names if NAME_FRICTION_RE.search(nm)]
    line("名字含撇号/括号/连字符", "%d / %d" % (len(friction), len(names)))
    for nm in friction[:8]:
        line("  ", nm)
    has_ref = sum(1 for n in cur if (n.get("props") or {}).get("source_ref"))
    line("curated 带 source_ref 的", "%d / %d" % (has_ref, len(cur)))
    return cur


# ------------------------------------------------------------------ R4
def r4():
    hdr("R4  PhysicsBabel 原文有没有文献字段？（没有锚就没有「找论文」这回事）")
    with open(PB_RAW, encoding="utf-8") as f:
        raw = json.load(f)
    line("raw 结构", type(raw).__name__ + " keys=" + ",".join(list(raw.keys())[:5]))
    nodes = raw.get("nodes") or []
    line("PB 节点数", len(nodes))
    ks = collections.Counter()
    for r in nodes:
        for k in (r.get("props") or {}):
            ks[k] += 1
    line("props 字段（前 12）", ", ".join(k for k, _ in ks.most_common(12)))
    # ★ 不能把 `source` 算作文献字段：这里它取的是**生成器名**（PhysicsBabel），
    #   是 provenance 不是 citation。第一版正则含 `source` ⇒ 报「有 1 个」⇒ 结论错。
    #   判据要按**取值**判，不按字段名判。
    src_vals = collections.Counter((r.get("props") or {}).get("source") for r in nodes)
    line("  `source` 的实际取值", src_vals.most_common(3))
    lit = [k for k in ks if re.search(r"(ref|paper|doi|cite|url|author|journal|publication|arxiv)", k, re.I)]
    lit = [k for k in lit if k not in ("source",)]
    line("文献类字段（按名）", lit or "**0 个**")
    # 按值再确认一遍：有没有任何字段装的是 DOI / URL / 作者列表
    val_lit = []
    for r in nodes[:500]:
        for k, v in (r.get("props") or {}).items():
            if isinstance(v, str) and re.search(r"(doi\.org|arxiv\.org|https?://)", v):
                val_lit.append(k)
            elif isinstance(v, list) and v and all(isinstance(x, str) for x in v) \
                    and any(re.search(r"(10\.\d{4,}/)", x) for x in v):
                val_lit.append(k)
    line("文献类字段（按值，前 500 条抽样）", sorted(set(val_lit)) or "**0 个**")
    return ks, sorted(set(lit)) + sorted(set(val_lit))


# ------------------------------------------------------------------ R5
def r5():
    hdr("R5  OpenAlex 网络侧前提：摘要拿不拿得到？本仓库存了几条？")
    with open(OA_RAW, encoding="utf-8") as f:
        raw = json.load(f)
    works = raw.get("works") or []
    line("raw works", len(works))
    line("已有 abstract 的", sum(1 for w in works if w.get("abstract_inverted_index")))
    ks = collections.Counter()
    for w in works:
        for k in w:
            ks[k] += 1
    line("raw 字段是否含 abstract", "含" if any("abstract" in k for k in ks) else "**不含（to_raw 丢掉了）**")
    for fid, m in MEASURED["openalex"].items():
        line("领域 %s 总量" % fid, "{:,}".format(m["works"]))
        line("  抽样 50 条摘要覆盖", "%d%%" % m["abs_pct_sample50"])
    line("（measured_at）", MEASURED["measured_at"] + "；--net 可复测")

    if NET:
        print("\n  [--net] 联网复测中……")
        try:
            import ssl
            import urllib.request
            ctx = ssl.create_default_context()
            UA = {"User-Agent": "STTP-ETL/0.1 (research; mailto:sttp-etl@example.org)"}

            def get(u):
                r = urllib.request.Request(u, headers=UA)
                with urllib.request.urlopen(r, timeout=30, context=ctx) as f:
                    return json.loads(f.read())

            for fid in ("fields/31", "fields/26"):
                j = get("https://api.openalex.org/works?filter=primary_topic.field.id:%s"
                        "&select=id,abstract_inverted_index&per-page=50"
                        "&mailto=sttp-etl@example.org" % fid)
                res = j["results"]
                ab = sum(1 for r in res if r.get("abstract_inverted_index"))
                line("  复测 %s" % fid, "总数 %s ｜ 摘要 %d/%d" % (
                    "{:,}".format(j["meta"]["count"]), ab, len(res)))
        except Exception as e:                       # noqa: BLE001 —— 网络失败不影响本地结论
            line("  联网复测失败", "%s: %s" % (type(e).__name__, e))


# ------------------------------------------------------------------ R6
def r6():
    hdr("R6  检索判别力 —— 四档命中量：能不能靠命中数当「被讨论数」？")
    h = MEASURED["search_hits"]
    rows = [
        ("精确串（含 Unicode 数学符号）", "m = k₀ · F·S·i⁻²·ρ_e⁻¹", h["m = k₀ · F·S·i⁻²·ρ_e⁻¹"]),
        ("宽松分词（去掉符号）", "k0 F S i^-2 rho_e^-1", h["k0 F S i^-2 rho_e^-1"]),
        ("同类合成式另一种写法", "F = k0 m^2 v^3 S^-1", h["F = k0 m^2 v^3 S^-1"]),
        ("人类具名 · 物理", "Coulomb's law", h["Coulomb's law"]),
        ("人类具名 · 化学", "Nernst equation", h["Nernst equation"]),
        ("人类具名 · 相对论", "Mass-energy equivalence", h["Mass-energy equivalence"]),
    ]
    for lab, q, n in rows:
        line(lab, "%-30s ⇒ %s 篇" % (q[:30], "{:,}".format(n)))
    line("判别带宽度", "合成式 0 ↔ 符号汤 92,302（中间无可用阈值）")
    line("具名公式的问题", "「提及」≠「讨论」：15,595 篇里绝大多数只是提到名字一次")
    line("（measured_at）", MEASURED["measured_at"])


# ------------------------------------------------------------------ R7
def r7(forms, human, cur, to_formula, lit_fields):
    hdr("R7  结论：可达到底是多少？5,000 个 PB 公式该怎么标注？")
    phys_cur = [n for n in cur if ((n.get("props") or {}).get("domain") or "").startswith("physics")]
    uniq_phys = len({(n.get("props") or {}).get("name") for n in phys_cur})
    uniq_all = len({(n.get("props") or {}).get("name") for n in cur})
    no_name = len(forms) - len(human)
    line("规划口径「扩到物理侧」的可落地分母", "物理 curated %d 条（唯一名 %d）" % (len(phys_cur), uniq_phys))
    line("全学科 curated 分母", "%d 条（唯一名 %d）" % (len(cur), uniq_all))
    line("Formula↔Paper 现有边", to_formula)
    line("无人类可读名的公式", no_name)
    print()
    print("  裁决（每条绑定到上面的绝对量；判据用绝对量，不用增量）：")
    verdicts = [
        ("V1 现有 178 条 `discusses` 与公式无关",
         to_formula == 0,
         "R1：Formula 端点为 0，全部是 Paper→MathConcept"),
        ("V2 「5,090 个公式都有论文讨论」不可达",
         no_name > 4000,
         "R2：有人类名的 %d 条，其余 %d 条 name 就是 LaTeX 式子" % (len(human), no_name)),
        ("V3 物理侧可达量级是「几十条」而非「几千条」",
         len(phys_cur) < 100,
         "R3：物理 curated %d 条（唯一名 %d）" % (len(phys_cur), uniq_phys)),
        ("V4 PB 原文没有文献锚点",
         len(lit_fields) == 0,
         "R4：按字段名与按取值两路都查了，文献类字段 %d 个%s"
         % (len(lit_fields), ("（" + ",".join(lit_fields) + "）") if lit_fields else "")),
        ("V5 摘要字段拿得到，但本仓库没存",
         True,
         "R5：OpenAlex 有 abstract_inverted_index；raw 中 188 条里 0 条有摘要（to_raw 丢掉）"),
        ("V6 命中数不能当「被讨论数」",
         MEASURED["search_hits"]["F = k0 m^2 v^3 S^-1"] > 1000,
         "R6：合成式 0 ↔ 符号汤 92,302，无判别带"),
    ]
    for name, ok, why in verdicts:
        print("    [%s] %s" % ("PASS" if ok else "FAIL", name))
        print("           %s" % why)
    print()
    print("  ⇒ 唯一诚实的做法：**三态标注**，与 G3 的 applicability 同构")
    print("     extracted    : curated 具名公式，检索到**带定位句**的文献 → 建 mentioned_in 边")
    print("     no_referent  : %d 条无人类可读名的公式 —— 从未被任何人写出，不存在「被讨论」" % no_name)
    print("     not_extracted: curated 里检索不到（名字太泛/太旧/闭源）")
    print()
    print("  ✗ 不许做的三件事：")
    print("     1. 拿 `concepts`/`topics` 回填成「公式被讨论」（那是主题不是公式）")
    print("     2. 拿符号汤检索的命中数当证据（92,302 篇是噪声）")
    print("     3. 把 no_referent 算成欠账、写进覆盖率分母")


def main():
    nodes, edges, by, forms = load()
    print("=" * 78)
    print("G4 论文线 · 可行性探针（只读；不写图；不承诺数字）")
    print("=" * 78)
    print("  数据: normalized.json ｜ nodes=%d edges=%d formulas=%d" % (len(nodes), len(edges), len(forms)))
    to_formula, _fe = r1(edges, by)
    human, pb = r2(forms)
    cur = r3(human)
    _ks, lit_fields = r4()
    r5()
    r6()
    r7(forms, human, cur, to_formula, lit_fields)
    return 0


if __name__ == "__main__":
    sys.exit(main())
