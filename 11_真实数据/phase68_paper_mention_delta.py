#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""Phase 68 · G4 论文线落盘：`mentioned_in` 三态（规则 m1）

端口号式的自我说明：为什么这一锤落在 80 条而不是 5,090 条
--------------------------------------------------------
Phase 67 的探针（`_recon_papers.py`）把 G4 的性质判死了：

    V1  现有 178 条 `discusses` 没有一条连到 Formula（全是 Paper→MathConcept）
    V2  PhysicsBabel 的 5,000 个公式 `name == latex`（100%）⇒ **没有指称对象**
    V3  「物理侧可达量级」是几十条，不是几千条（物理 curated 38 条）
    V4  PB 原文没有任何文献字段 ⇒ 连锚都没有
    V5  OpenAlex 摘要覆盖率 86%，但本仓库 raw 里 0 条（to_raw 丢的，本 Phase 已修）
    V6  命中数不能当「被讨论数」（9 万 vs 0，中间没有判别带）

⇒ 所以在写之前先把话说清楚：**这里落的是「提及」（mentioned_in），不是「讨论」。**
   边名必须说出事实。声称"某个工作者讨论了　m = k₀·F·S·i⁻²·ρ_e⁻¹"是假的，
   声称"某篇文献的文字里提到了牛顿第二定律这个名字"是可核验的真句子。

三态（与 G3 同构）
-----------------
    extracted      80 条 curated 具名公式中，检索到带**定位句**的文献 ⇒ 建 `mentioned_in`
    not_extracted  有名字、本轮没拿到可信提及（0 候选 / 名字歧义被裁决否掉）
    no_referent    **5,010 条**：从未被任何人命名的合成式 + 8 条名字是种类名的
                   （mathxiv 的 `definition` / `lemma`）⇒ 不存在"被提及"这回事

★ 为什么 8 条 mathxiv 也算 no_referent：它们的 name 是**类型名**不是公式名，
   「提到了 definition」不构成对某个公式的提及 ⇒ 与合成式同类，都是无指称。
  （探针 R7 记的 5,002 = 5,090 − 88 human-name；这里把那 8 条并进来 = 5,010，
   差额的原因写在这里，不是改口径。）

★★ 双层设计（照抄 G3 成功的结构：规则发现 + 人工裁决）
   规则层：名字连续串出现在标题/摘要 + 学科优先（缺则回退）+ 单词名直接否
   裁决层：`VERDICT` 表逐条给 80 条公式的 accept/accept1/reject
   表与候选必须**同时更新**：缺任何一条 ⇒ S3 FAIL ⇒ 拒绝产出 delta。
   「补几条候选提高覆盖」这件事永远正当，只有这条守卫拦得住它把噪声混进图。

用法：
  python 11_真实数据/phase68_paper_mention_delta.py [--dry] [--out PATH] [--sample N]
  python 11_真实数据/phase68_paper_mention_delta.py --draft     # 打印待裁决清单
  bash sttp.sh papermentionbackfill [--apply]   # = 本脚本 + apply_delta
"""
import argparse
import collections
import datetime
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from phase68_paper_fetch import canon_tokens, name_tokens, find_span  # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
MENT = os.path.join(HERE, "phase68_paper_mentions.json")
OUT = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase68_paper_mention_delta.json")

RULE_VERSION = "m1"
#   m1（定稿）：连续串匹配 + 学科优先回退 + 单词名否决 + 人工裁决表（80 条逐条读过）

FIELD_MAP = {"physics": "31", "math": "26", "chem": "16"}   # 2026-10-21 实测 OpenAlex field id
SOURCE = "openalex-mention"
VERIFICATION_LEVEL = "rule_checked"
SOURCE_LEVEL = "source_asserted"

# ---------------------------------------------------------------------------
# 人工裁决表（**事实**，不是代码逻辑）
# 每条公式我都读过了它的 top-3 候选标题/片段，裁决依据写在 note 里。
# decision: accept（取前 3） / accept1（只取第 1 条，其余噪声明显） / reject（不入图）
# ---------------------------------------------------------------------------
VERDICT = {
    # ---- accept：top1 标题本身就写着这个公式名，或三条候选都在同一语境 ----
    "BC:fo:atp_hydrolysis_eq": ("accept", "ATP 水解在分子马达/Hsp70 综述的自然语境里被提及（跨回退）"),
    "CE:fo:arrhenius": ("accept", "top1 标题即《The development of the Arrhenius equation》"),
    "CE:fo:henderson": ("accept", "top1 为 Henderson-Hasselbalch 专论；次条为界面 pH 探针"),
    "CE:fo:le_chatelier": ("accept", "三条均为化学语境（固相合成/激酶热力学/黑膜）"),
    "CE:fo:nernst": ("accept", "燃料电池建模 + 神经电生理，均在 Nernst 方程语境（跨回退）"),
    "EM:fo:coulomb": ("accept", "top1 标题为库仑定律的实验检验"),
    "EM:fo:faraday": ("accept", "top1 标题为法拉第定律向自旋力的推广"),
    "EM:fo:gauss": ("accept", "电磁学教科书 + 黑洞规范系综，均为高斯定律语境"),
    "EM:fo:lorentz": ("accept", "超导磁通钉扎/超导电磁方程，均为洛伦兹力语境"),
    "FO:ke": ("accept", "等离子体与 DFT 论文均在动能语境提及"),
    "MA:fo:euler_identity": ("accept", "素数分布与分拆函数奇偶性均在欧拉恒等式语境"),
    "MA:fo:pythagoras": ("accept", "数学史教科书条目"),
    "MC:fo:chain_rule": ("accept", "随机微分方程数值模拟语境"),
    "MC:fo:ftc": ("accept", "实分析与保测映射，均为微积分基本定理语境"),
    "MC:fo:taylor": ("accept", "方差估计/复变函数教科书语境"),
    "MG:fo:euler_poly": ("accept", "三条标题全部就是欧拉多面体公式（最强的一组）"),
    "NT:fo:crt": ("accept", "数论教科书与中国剩余定理条目"),
    "NT:fo:euler_theorem": ("accept", "数论与微积分教科书条目"),
    "NT:fo:fermat_little": ("accept", "三条标题全部是费马小定理的新证明"),
    "NT:fo:fundamental_arithmetic": ("accept", "数论教科书与组合数学条目"),
    "NT:fo:wilson": ("accept", "top1 数论教科书；transversal covers / 保度量函数提及 Wilson 定理"),
    "OP:fo:lens": ("accept", "关联成像与鬼成像，均在薄透镜公式语境"),
    "OP:fo:snell": ("accept", "负折射偏移与哨声波导，均在斯涅尔定律语境"),
    "QM:fo:de_broglie": ("accept", "超轻标量暗物质/激光冷却，均指德布罗意波长"),
    "QM:fo:photoelectric": ("accept", "单光子源与剂量计算，均在光电效应语境"),
    "QM:fo:schrodinger": ("accept", "三条标题全部是薛定谔方程的求解与推导"),
    "QM:fo:uncertainty": ("accept", "量子力学相位/纳米振子，均为不确定性原理语境"),
    "RT:fo:lorentz_factor": ("accept", "GW170817 与 X 射线双星喷流，均指洛伦兹因子"),
    "SM:fo:ideal_gas_kin": ("accept", "GADGET-2 与玻色凝聚，均提及理想气体状态方程"),
    "SM:fo:maxwell": ("accept", "相对论统计力学与 H II 区 κ 分布，均指麦克斯韦-玻尔兹曼分布"),
    "SM:fo:partition": ("accept", "Jaynes 1957 信息论统计力学 + Onsager 二维模型，均为配分函数"),
    "TH:fo:boyle": ("accept", " Boyle 定律与引力不稳定、黑洞热力学的类比"),
    "TH:fo:carnot": ("accept", "有限时间热力学三条，均为卡诺效率语境"),
    "TH:fo:charles": ("accept", "top1《Charles's law revisited》；另两条为气体定律教学研究"),
    "TH:fo:first_law": ("accept", "宇宙学视界热力学，均引用热力学第一定律"),
    # ---- accept1：只有第 1 条站得住，其余明显不可信 ----
    "CE:fo:ka": ("accept1", "仅 1 条候选且为跨领域（核糖体结构），证据弱但提及成立"),
    "CE:fo:mass_action": ("accept1", "top1 1920 年标题即 LAW OF MASS ACTION；另两条不可信"),
    "CM:fo:centripetal": ("accept1", "完全电离等离子体中的检验粒子，提及向心力"),
    "CM:fo:gravity": ("accept1", "修正引力/Friedmann 方程，提及牛顿引力定律"),
    "FO:fma": ("accept1", "TeVeS 宇宙学论文提及牛顿第二定律"),
    "MA:fo:binomial": ("accept1", "离散与组合数学教科书条目"),
    "MG:fo:law_cosines": ("accept1", "猫步态生物力学论文，提及余弦定理"),
    "OM:fo:combustion": ("accept1", "标题即《Chemical kinetic modeling of hydrocarbon combustion》"),
    "TH:fo:gay_lussac": ("accept1", "唯一候选是 2026 年预印本（c=0），提及成立但质量低"),
    "TH:fo:ideal_gas": ("accept1", "大气折射率论文提及理想气体状态方程"),
    # ---- reject ----
    #   单词型名字：任何摘要都会出现，无从区分
    "CM:fo:impulse": ("reject", "单词型名字（连字符按词内处理）⇒ 候选无法与背景词区分"),
    "CM:fo:momentum": ("reject", "单词型名字（统计学功效/动量混在同一批候选里）"),
    "CM:fo:power": ("reject", "单词型名字（候选几乎全是「统计功效」文献）"),
    "CM:fo:work": ("reject", "单词型名字（劳务/功？无法分辨）"),
    #   同名不同义：**只有拿到 incidencia 才会发现，规则不可能自己知道**
    "CE:fo:poh": ("reject", "唯一候选里的 pOH 是「术后高淀粉酶血症」缩写，与 pOH 定义无关"),
    "IC:fo:periodic": ("reject", "唯一候选是生态学论文，periodic law 指周期性现象而非元素周期律"),
    #   0 候选（检索命中 >0，但无一满足「名字连续出现在标题或摘要」）
    "BC:fo:photosynthesis_eq": ("reject", "0 条候选"),
    "CE:fo:gibbs_standard": ("reject", "0 条候选"),
    "CE:fo:gibbs_reaction": ("reject", "0 条候选"),
    "CE:fo:ksp": ("reject", "0 条候选"),
    "CE:fo:kw": ("reject", "0 条候选"),
    "CE:fo:ph": ("reject", "0 条候选"),
    "CE:fo:reaction_quotient": ("reject", "0 条候选"),
    "EM:fo:biot_savart": ("reject", "0 条候选"),
    "FO:emc2": ("reject", "0 条候选"),
    "IC:fo:ph": ("reject", "0 条候选"),
    "MA:fo:log_product": ("reject", "0 条候选"),
    "MA:fo:quadratic": ("reject", "0 条候选"),
    "MC:fo:limit_def": ("reject", "0 条候选"),
    "MC:fo:power_rule": ("reject", "0 条候选"),
    "MG:fo:circle_area": ("reject", "0 条候选"),
    "MG:fo:law_sines": ("reject", "0 条候选"),
    "MG:fo:sphere_vol": ("reject", "0 条候选"),
    "NT:fo:basel": ("reject", "0 条候选"),
    "NT:fo:congruence": ("reject", "0 条候选"),
    "NT:fo:euclid_algorithm": ("reject", "0 条候选"),
    "NT:fo:unit_group": ("reject", "0 条候选"),
    "OP:fo:mirror": ("reject", "0 条候选"),
    "OP:fo:refr_speed": ("reject", "0 条候选"),
    "QM:fo:planck": ("reject", "0 条候选"),
    "RT:fo:length_contraction": ("reject", "0 条候选"),
    "RT:fo:mass_energy": ("reject", "0 条候选"),
    "RT:fo:rel_momentum": ("reject", "0 条候选"),
    "RT:fo:time_dilation": ("reject", "0 条候选"),
    "SM:fo:boltzmann": ("reject", "0 条候选"),
}


# ---------------------------------------------------------------------------
def _head_full():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"],
                                       cwd=ROOT, stderr=subprocess.DEVNULL
                                       ).decode().strip()
    except Exception:
        return "unknown"


def _ftail(url):
    return (url or "").rsplit("/", 1)[-1]


def pick(row):
    """规则层：学科优先（缺则回退）+ 排序，返回 (选中的候选列表, 是否同学科)。"""
    want = FIELD_MAP.get((row.get("domain") or "").split(".")[0], "?")
    cands = row.get("candidates") or []
    infield = [c for c in cands if _ftail(c.get("field_id")) == want]
    pool = infield or cands
    pool = sorted(pool, key=lambda c: (0 if c.get("where") == "title" else 1,
                                       -(c.get("cited_by") or 0)))
    return pool, bool(infield)


def main():
    ap = argparse.ArgumentParser(description="Phase 68 G4 mentioned_in delta")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--draft", action="store_true",
                    help="打印待裁决清单（VERDICT 表里还没有的条目）")
    ap.add_argument("--pool", default=MENT,
                    help="候选池路径（默认正式池；红探针用不了联网产物，改指 degraded 夹具）")
    ap.add_argument("--allow-partial", action="store_true",
                    help="候选池含 error 行时仍继续（默认拒绝产出；error 行记 not_extracted）")
    a = ap.parse_args()

    with open(NORM, encoding="utf-8") as f:
        g = json.load(f)
    with open(a.pool, encoding="utf-8") as f:
        ment = json.load(f)
    rows = {r["fid"]: r for r in ment.get("formulas") or []}

    # ★★ 输入完整性闸门（D96）。候选池里只要有 error 行，就意味着「这一条**没被
    #    检索过**」，而不是「检索过但没有提及」。这两种状态在图上差着一个数量级的
    #    可信度 —— 混为一谈就是把一次配额故障画成 \"99% 公式没有文献提及\"。
    #    默认拒绝产出（RC=2）；要放行必须显式 --allow-partial，并把每一行都标成
    #    not_extracted（可被后续完整运行覆盖）。
    bad_rows = sorted(fid for fid, r in rows.items() if r.get("error"))
    n_err = len(bad_rows)
    integ = (ment.get("meta") or {}).get("integrity") or {}
    trust = (ment.get("meta") or {}).get("trust")
    print("Phase 68 · G4 mentioned_in（规则 %s）" % RULE_VERSION)
    print("  候选池：%s ｜ integrity=%s" % (os.path.basename(a.pool), integ or "无"))
    if n_err:
        print("  ⚠ 候选池里 %d/%d 条未能真正检索（= 未知，不是无）：" % (n_err, len(rows)))
        for fid in bad_rows[:6]:
            print("      %-26s %s" % (fid, str(rows[fid]["error"])[:64]))
        if n_err > 6:
            print("      … 其余 %d 条同类" % (n_err - 6))
        if trust == "DEGRADED":
            print("  ⚠ meta.trust=DEGRADED：该文件已被标记为不可信的运行产物")
        if not a.allow_partial:
            print("\n[REFUSE] 候选池不完整 ⇒ **拒绝产出 delta**（这不是报错，是拒绝说不知道的事）。")
            print("         要放行请加 --allow-partial，届时上述 %d 条会记为 not_extracted。" % n_err)
            print("         要修好请重跑：bash sttp.sh paperfetch --resume")
            return 2
    nodes = g["nodes"]
    forms = [n for n in nodes if "Formula" in (n.get("labels") or [])]
    exist_ids = {n["id"] for n in nodes}

    print("Phase 68 · G4 mentioned_in（规则 %s）" % RULE_VERSION)
    print("  Formula 节点 %d ｜ 候选池覆盖 %d 条 curated" % (len(forms), len(rows)))

    if a.draft:
        missing = sorted(set(rows) - set(VERDICT))
        print("\n【待裁决】%d 条未进 VERDICT 表：" % len(missing))
        for fid in missing:
            row = rows[fid]
            pool, inf = pick(row)
            top = ["%s|%s|%s" % (c["wid"], c["where"], (c["title"] or "")[:52]) for c in pool[:3]]  # noloss-exempt: 候选列表显示，不入图
            print("  %-28s %-38s n=%d %s%s" % (fid, row["name"][:38], len(pool),
                                               "" if inf else "[跨] ", " ; ".join(top) or "（无候选）"))
        extra = sorted(set(VERDICT) - set(rows))
        if extra:
            print("  ⚠ 表里有但候选池没有（数据变了？）：%s" % extra)
        return 0

    # ---------------------------------------------------------------- 落盘
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    d_nodes, d_edges = [], []
    new_papers, dup_ignore = [], []
    st = collections.Counter()
    unverified, accept_but_empty, ref_bad, forged, snippet_bad = [], [], [], [], []
    pretend = []
    curated_no_ref = set()   # D101：记 no_referent 的 **curated** 条（查过、裁决为无）
    fake_unsearched = []     # D101：记 not_extracted 但池里**没有 error**（把已确认无说成未知）
    cross_cnt = 0

    for n in forms:
        fid = n["id"]
        row = rows.get(fid)
        p = n.get("props") or {}
        if row is None:
            st["no_referent"] += 1
            newp = {"papers_status": "no_referent"}
            d_nodes.append({"id": fid, "props": newp})
            continue

        # ★ 未真正检索过的一条 ⇒ not_extracted，绝不能落进 no_referent。
        #   no_referent 是「查过了，它在文献里没有指称对象」这一**事实陈述**；
        #   quota/网络失败导致没查 ⇒ 是「不知道」。把两者混在一起，一次配额故障
        #   就会被固化成上万个"确认无人提及"，而它根本没被验证过。
        if row.get("error"):
            st["not_extracted"] += 1
            st["unsearched"] += 1
            newp = {"papers_status": "not_extracted", "papers": []}
            if p.get("papers_status") == "extracted":
                # 之前已经有真证据，现在一次抓取失败不能把它降级
                newp = {"papers_status": "extracted", "papers": p.get("papers") or []}
                st["not_extracted"] -= 1
                st["extracted"] += 1
            d_nodes.append({"id": fid, "props": newp})
            # ★ 二次确认：落给这一行的**状态**确实不是 no_referent。
            #   只比计数不够 —— 完全可以在保住计数的同时把状态写成"已确认无"
            #   （数字照旧、语义已歪），那正是最省事的那种退化方式。
            if newp.get("papers_status") == "no_referent":
                pretend.append(fid)
            continue

        dec = VERDICT.get(fid)
        if dec is None:
            unverified.append(fid)
            continue
        decision, _note = dec
        ntoks = row.get("name_tokens") or name_tokens(row["name"])
        pool, infield = pick(row)
        # 规则层否决：单词型名字（不论表里怎么写）
        if len(ntoks) < 2:
            pool = []
        take = {"accept": 3, "accept1": 1, "reject": 0}.get(decision, 0)
        chosen = [c for c in pool[:take]]
        # S5 裁决与候选一致：accept 却没有可用候选 ⇒ 数据变了，表该重读
        if decision.startswith("accept") and not chosen:
            accept_but_empty.append(fid)

        if chosen:
            st["extracted"] += 1
            refs = []
            for c in chosen:
                pid = "PA:oa:" + c["wid"]
                if pid not in exist_ids:
                    exist_ids.add(pid)
                    new_papers.append(pid)
                    authors = c.get("authors") or []
                    d_nodes.append({
                        "id": pid, "labels": ["Paper", "Entity"],
                        "props": {
                            "id": pid, "name": c.get("title") or pid,
                            "title": c.get("title") or "",
                            "openalex_id": c["wid"], "doi": c.get("doi"),
                            "year": c.get("year"), "pub_type": "article",
                            "cited_by": c.get("cited_by") or 0,
                            "author_count": len(authors), "authors": authors,
                            "field": c.get("field"), "topic": c.get("topic"),
                            "venue": None, "language": c.get("language"),
                            "source": SOURCE, "version": "68.1",
                            "ntype": "Paper", "domain": (row.get("domain") or "").split(".")[0],
                            "primary_topic": c.get("topic"),
                            "explicit_or_inferred": "explicit", "confidence": 0.7,
                            "created_at": now,
                        },
                    })
                else:
                    dup_ignore.append(pid)
                # S2 可追溯：**回到 snippet 里重跑一次匹配**，防定位句指向别处
                s_toks = canon_tokens(c.get("snippet") or "")
                if find_span(s_toks, ntoks) < 0 and find_span(
                        canon_tokens(c.get("title") or ""), ntoks) < 0:
                    snippet_bad.append((fid, c["wid"]))
                if not (c.get("snippet") or "").strip():
                    snippet_bad.append((fid, c["wid"], "空"))
                d_edges.append({
                    "source": pid, "target": fid, "type": "mentioned_in",
                    "kind": "openalex_name_mention",
                    "props": {
                        "confidence": 0.7, "source": SOURCE,
                        "explicit_or_inferred": "explicit", "created_at": now,
                        "kind": "openalex_name_mention",
                        "evidence": "title_or_abstract 中连续出现「%s」" % row["name"],
                        "where": c.get("where"),
                        "snippet": c.get("snippet"),
                        "matched_name": row["name"],
                        "field_matched": infield,
                        "rule": RULE_VERSION,
                        "verification_level": VERIFICATION_LEVEL,
                        "verification_scope": "openalex abstract/title string match",
                        "source_verification_level": SOURCE_LEVEL,
                        "cited_by": c.get("cited_by") or 0,
                        "doi": c.get("doi"),
                    },
                })
                if not infield:
                    cross_cnt += 1
                refs.append({"openalex_id": c["wid"], "openalex_node": pid,
                             "title": (c.get("title") or "")[:180], "year": c.get("year"),
                             "where": c.get("where"), "snippet": c.get("snippet"),
                             "cited_by": c.get("cited_by") or 0, "doi": c.get("doi"),
                             "field": c.get("field"), "field_matched": infield})
            newp = {"papers_status": "extracted", "papers": refs,
                    "papers_count": len(refs), "papers_source": SOURCE,
                    "papers_rule_version": RULE_VERSION,
                    "papers_search_total": row.get("search_total")}
        else:
            # ★ D101：走到这里的是「**检索过了**（没有 error），但裁决后没有可采信的候选」。
            #   两种子情形：① 搜索命中为 0 候选（名字在 title/abstract 里压根没连续出现）；
            #   ② 有候选但人工判定它不是指这个公式（如 Periodic law 命中生态学的 periodic）。
            #   对 mentioned_in（提及）这条边而言，两者都是**「查过了，确认没有指称」**
            #   ⇒ no_referent。此前一律记成 not_extracted，等于把「已确认无」降级成
            #   「未知」：80 条里 35 条明明查过，报告却要说它们没被检索。
            #   D96 防的是「未知 ⇒ 已确认无」这个方向；这里歪的是它的镜像。
            #   区分依据只有一条硬线：**有没有 error**（有没有真正检索过）。
            st["no_referent"] += 1
            st["no_referent_curated"] += 1
            curated_no_ref.add(fid)
            newp = {"papers_status": "no_referent", "papers": [],
                    "papers_count": 0,
                    "papers_reason": (VERDICT.get(fid) or ("", ""))[1],
                    "papers_reject_kind": ("no_candidate" if not pool else "verdict_reject"),
                    "papers_search_total": row.get("search_total")}
        # ★ 双向：只有「本轮没检索过（有 error）」的 curated 落进 no_referent 才算编造；
        #   检索过且裁决为无的落进 no_referent 是**正确的**，不该红。
        if newp.get("papers_status") == "no_referent" and fid in set(bad_rows):
            ref_bad.append(fid)
        # ★ S5c：反方向的同一条硬线 —— 记成「未检索」的，必须**真的**没检索过。
        #   只比**计数**不够：完全可以在保住 extracted/not_extracted 数字不变的前提下，
        #   把状态字符串从 no_referent 换成 not_extracted（数字照旧、语义已歪），
        #   那正是最省事的退化方式（D96 里 pretend 那一条防的就是同类手法）。
        if newp.get("papers_status") == "not_extracted" and not row.get("error"):
            fake_unsearched.append(fid)
        d_nodes.append({"id": fid, "props": newp})

    total = st["extracted"] + st["no_referent"] + st["not_extracted"]
    # D101：curated 的三态是 extracted / no_referent(查过、裁决为无) / not_extracted(没查)。
    #   三者之和才等于 curated 条数；此前只算 extracted+not_extracted，是在旧分类下才成立的等式。
    checked = st["extracted"] + st["not_extracted"] + st["no_referent_curated"]
    print("  extracted %d ｜ not_extracted %d ｜ no_referent %d（合计 %d / %d）"
          % (st["extracted"], st["not_extracted"], st["no_referent"], total, len(forms)))
    if st["unsearched"]:
        print("  ⚠ 其中 %d 条**本轮没被检索过**（抓取失败/配额）：状态是 not_extracted（未知），"
              "不是 no_referent（已确认无）—— 别把它读成结论。" % st["unsearched"])
    print("  入图 evidenced papers %d（其中复用已有 Paper 节点 %d）｜ 边 %d ｜ 跨学科引用 %d"
          % (len(new_papers), len(dup_ignore), len(d_edges), cross_cnt))

    # ---------------------------------------------------------------- 自检
    managed = sum(1 for n in forms if (n.get("props") or {}).get("papers_status") is not None)
    checks = [
        ("S1 三态全覆盖", total == len(forms),
         "%d / %d" % (total, len(forms))),
        ("S2 每条 summarizing 证据可追溯", not snippet_bad,
         "snippet 里复现不出公式名的：%d 条（须 0）%s" % (len(snippet_bad), snippet_bad[:3])),  # noloss-exempt: 错误信息截断，不入图
        ("S3 候选全部已裁决", not unverified,
         "表里没有的：%d 条（须 0 —— 出现就必须先人工读，否则准确率是假数）%s"
         % (len(unverified), unverified[:5])),  # noloss-exempt: 错误信息截断，不入图
        ("S4 裁决与候选一致", not accept_but_empty,
         "判 accept 却拿不到候选的：%d 条（须 0 ⇒ 候选池变了，表要重读）%s"
         % (len(accept_but_empty), accept_but_empty[:3])),  # noloss-exempt: 错误信息截断，不入图
        # ★ D101 改写：旧版是「curated 一律不许记 no_referent」—— 那是在把
        #   「查过确认无」也一并禁掉，逼着它们落到 not_extracted（未知）才不红。
        #   正确的硬线是：**没检索过的**不许记成已确认无（那才是编造）。
        ("S5 不编造（**未检索过**的不许记成已确认无）", not ref_bad,
         "no_referent 里混进本轮有 error 的 curated：%d 条（须 0）／ 已裁决为无的 "
         "curated %d 条（应 >0，证明这条分支在走）"
         % (len(ref_bad), len(curated_no_ref))),
        # ★ D96：这条断言守的是**故障退化的正确方向**。配额失败必须落到
        #   not_extracted；落到 no_referent 就会把一次基础设施故障写成一条永久结论。
        ("S5b 抓取失败 ⇒ not_extracted（不许落到 no_referent）",
         len(bad_rows) == st["unsearched"] and not pretend,
         "池里 error %d 条 ／ 记为未检索 %d 条（必须相等）／状态被写成 no_referent 的 %d 条（须 0）" % (len(bad_rows), st["unsearched"], len(pretend))),
        ("S5c 检索过的不许记成未检索（D101 镜像）", not fake_unsearched,
         "记 not_extracted 但池里没有 error 的 curated：%d 条（须 0 —— "
         "出现即「把已确认无降级成未知」）%s" % (len(fake_unsearched), fake_unsearched[:3])),  # noloss-exempt: 错误信息截断，不入图
        ("S6 非真空", st["extracted"] > 0 and st["no_referent"] > 0 and checked == len(rows),
         "extracted %d（>0 证明抽到了）；no_referent %d（>0 证明这条分支在走）；"
         "已裁决 %d = curated %d" % (st["extracted"], st["no_referent"], checked, len(rows))),
        ("S7 边端点都在图里", all(e["source"] in exist_ids and e["target"] in exist_ids
                                 for e in d_edges),
         "%d 条边" % len(d_edges)),
        ("S8 幂等", managed == 0,
         "已管过的 %d 条（首次须 0；再跑须全部一致、0 改动）" % managed),
    ]
    print("\n【分类自检】")
    bad = 0
    for name, ok, detail in checks:
        print("  [%s] %-24s %s" % ("PASS" if ok else "FAIL", name, detail))
        if not ok:
            bad += 1

    if a.sample:
        shown = 0
        for n in forms:
            row = rows.get(n["id"])
            if not row:
                continue
            pool, inf = pick(row)
            if not pool:
                continue
            c = pool[0]
            print("  %-26s %-34s %s｜%s｜%s" % (n["id"], row["name"][:34], c["wid"],
                                               c["where"], (c["snippet"] or "")[:90]))
            shown += 1
            if shown >= a.sample:
                break

    if bad:
        print("\n[FATAL] 自检未过 ⇒ **拒绝产出 delta**")
        return 1

    payload = {
        "meta": {
            "phase": 68, "tag": "paper-mention",
            "generator": "phase68_paper_mention_delta.py",
            "theme": "G4 论文线：80 条具名公式的 mentioned_in（提及，不是讨论）三态",
            "base_graph": os.path.relpath(NORM, ROOT),
            "evidence_file": os.path.relpath(MENT, ROOT),
            "rule": RULE_VERSION,
            "at_commit": _head_full(), "stamp": now,
            "n_formula_nodes": len(forms),
            "n_curated": len(rows),
            "n_extracted": st["extracted"], "n_not_extracted": st["not_extracted"],
            "n_no_referent": st["no_referent"],
            "n_new_paper_nodes": len(new_papers), "n_edges": len(d_edges),
            "note": "no_referent %d 条 = 5,010 条「无从谈被提及」（5,002 条无人类名的 PB "
                    "合成式 + 8 条 mathxiv 名字为类型名 definition/lemma 的节点）"
                    " + %d 条 curated 中**检索过但裁决为无**（0 候选或候选非指该式）"
                    " ⇒ 是「查过了，确认无」，不是「没查」。"
                    % (st["no_referent"], st["no_referent_curated"]),
            "n_no_referent_curated": st["no_referent_curated"],
        },
        "nodes": d_nodes,
        "delete_nodes": [],
        "edges": d_edges,
        "delete_edges": [],
    }
    print("\n【delta 汇总】nodes=%d（新增 Paper %d + Formula 属性）｜edges=%d"
          % (len(d_nodes), len(new_papers), len(d_edges)))
    if a.dry:
        print("[DRY] 未落盘")
        return 0
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print("-> %s" % os.path.relpath(a.out, ROOT))
    print("   落盘：python 06_PoC/apply_delta.py --delta %s --phase 68 "
          "--tag papermentionbackfill --apply" % os.path.relpath(a.out, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
