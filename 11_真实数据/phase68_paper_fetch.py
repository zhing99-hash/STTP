#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""Phase 68 · G4 论文线：**mentioned_in 候选检索**（联网；产物是候选池，不是结论）

这一步在做什么，以及为什么不直接叫「抽取」
------------------------------------------
G4 探针（Phase 67 `_recon_papers.py`）判死的结论是：

    `discusses` 178 条端点全是 Paper→MathConcept，**没有一条连到 Formula**；
    PB 合成的 5,000 个公式 `name == latex`，**没有可被文献指称的对象**；
    只有 **80 条 curated**（其中物理 38）有人类给它起的名字。

所以本脚本只做这 80 条。它的产物是**候选池**：每条公式若干篇"标题或摘要里出现了这个名字"的论文，
外加一句定位句。候选 ≠ 事实 —— 判定与落盘在 `phase68_paper_mention_delta.py`。

★ 为什么不用检索返回的 `meta.count` 当"被讨论数"（V6）:
    `Nernst equation` 检索给 7,373 篇，其中绝大多数只是**教学附录里提一次名字**。
    提及 ≠ 讨论。任何把 count 写进图的行为都是在制造一个新的撒谎字段（通律 7）。
    这里保留 count 只是为了让你看清量级，标注为 `search_total`，**不进图**。

★ 为什么严格到只剩"连续串"这一档（不为召回放宽）:
    探针 R6 实测过四档：精确串 0 ↔ 宽松分词 3（且是假阳性：相机设计/激光烧蚀）
    ↔ 换写法 92,302 ↔ 人类具名 15,595。**中间没有判别带**。
    G3 的教训也是同一条：靠宽松匹配凑覆盖率，代价是把「Frankl-Wilson 定理」
    当成「威尔逊定理」塞进图，而且没人看得出 —— 失败的形态是"多了一条证据"，
    不是报错。⇒ 判据只有一档：**规范化后公式名的完整词序列必须连续出现**。

代价要先讲清：本法漏掉改写式提及（"first law of thermodynamics" 写成
"thermodynamics first law"）。漏是可检索的欠账，错是不可见的污染 —— 选漏不选错。

用法
----
    python 11_真实数据/phase68_paper_fetch.py --fetch            # 联网抓（≈80 次请求，2—4 分钟）
    python 11_真实数据/phase68_paper_fetch.py --fetch --limit 5   # 先跑 5 条试水
    python 11_真实数据/phase68_paper_fetch.py --selfcheck        # 离线自检（不需要网络）
    python 11_真实数据/phase68_paper_fetch.py --show CE:fo:nernst # 打印已有候选
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

from fetch_common import (                                     # noqa: E402
    get_json, invert_abstract, STATS, FetchError, BudgetExhausted,
)

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT = os.path.join(HERE, "phase68_paper_mentions.json")

BASE = "https://api.openalex.org/works"
MAILTO = "sttp-etl@example.org"
PER_FORMULA = 25            # 每条公式取多少候选（按被引降序）
SNIPPET_MAX = 320           # 定位句截断长度
HUMAN_NAME_RE = re.compile(r"[A-Za-z]{4,}")

# 公式一级学科 → OpenAlex field（用于**降噪**，不是判定；2026-10-21 实测 id）
FIELD_OF = {"physics": "fields/31", "math": "fields/26", "chem": "fields/16"}
FIELD_NAME = {"fields/31": "Physics and Astronomy", "fields/26": "Mathematics",
              "fields/16": "Chemistry"}

# OpenAlex 返回的学科名（用于发现映射错了）
SELECT = ",".join(["id", "doi", "title", "display_name", "publication_year", "type",
                   "cited_by_count", "primary_topic", "authorships", "ids",
                   "abstract_inverted_index", "language"])


# ------------------------------------------------------------ 名字规范化
_PAREN_RE = re.compile(r"\([^)]*\)")
_TOKEN_RE = re.compile(r"[\w]+(?:-[\w]+)*", re.UNICODE)


def _fold_char(s: str) -> str:
    """字符级折叠：dash 拆词、撇号归一、**变音符剥离**。

    为什么要剥离变音符：`Schrödinger` 在图里的名字带变音符，而大量文献摘要
    （尤其是老论文、ASCII 排版）写作 `Schrodinger`。两边同时折叠 ⇒ 匹配得上。
    这一步若只在一边做，等于把一批真命中判成假阴性 —— 而且**看不出来**，
    因为失败形态是"没命中"，不是"报错"。
    """
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    for ch in "–—−":
        s = s.replace(ch, "-")
    s = s.replace("’", "'")
    # ★★ 连字符按**词内**处理（D93）。第一版把 '-' 换成空格，立刻把
    #    "Frankl-Wilson theorem" 拆成 [frankl, wilson, theorem]，
    #    于是 "Wilson's theorem" 在别人的复合姓氏上命中。正确做法是
    #    保留成一个 token、折叠时删掉连接符（maxwell-boltzmann → maxwellboltzmann）。
    #    代价：文献把 "Maxwell-Boltzmann" 写成空格版时会漏 —— 选漏不选错。
    # ★ 所有格必须**整体删掉**，而不是只删撇号：
    #   "Schrödinger's equation" 若只删撇号会切成 [schrodinger, s, equation]，
    #   名字就永远连不上 ⇒ 一批真命中变假阴性。失败形态是"没搜到"，不是报错，
    #   这是最难被发现的那一类退化。
    s = re.sub(r"'s(?![A-Za-z])", " ", s)
    return s


def canon_tokens(s: str):
    """→ [(canon, start, end), ...]；start/end 指向**原文**位置（用于取定位句）。

    ★ 必须保留原文偏移量：定位句是给人看的证据，丢了位置等于让人没法复核。
    """
    s = _PAREN_RE.sub(" ", _fold_char(s or ""))
    out = []
    for m in _TOKEN_RE.finditer(s):
        t = m.group(0).lower()
        t = t.replace("'", "").replace("-", "")
        if not t:
            continue
        out.append((t, m.start(), m.end()))
    return out


def name_tokens(name: str):
    return [t for t, _s, _e in canon_tokens(name)]


def find_span(toks, ntoks):
    """首处**连续**匹配的下标；无匹配 -1。长度必须恰好等于名字长度。

    只认连续、长度恰好等于名字长度。G3 的 applicability 词典就是因为允许宽匹配，
    准确率掉到 79.6%；这里从一开始不给这个口子。
    """
    if not ntoks or len(ntoks) > len(toks):
        return -1
    n = len(ntoks)
    for i in range(len(toks) - n + 1):
        if all(toks[i + k][0] == ntoks[k] for k in range(n)):
            return i
    return -1


def sentence_around(text: str, toks, i: int, n: int):
    """以匹配区间为中心取一句。先按句子标点找边界，退化时按字符窗兜底。"""
    start = toks[i][1]
    end = toks[i + n - 1][2]
    lo = max(0, start - SNIPPET_MAX // 2)
    # ★ D94：句子边界必须按**名字所在的位置**分左右寻找，不能在整段里乱找。
    #   第一版在一个 ±160 字符的窗口里取 `rfind(". ")`，那个句边界可能落在
    #   **名字之后** ⇒ 截出来的句子里根本没有公式名 ⇒ 51 条证据指向空处。
    #   而且这种错不会报错，只会让"定位句"变成一句不相干的话 —— 比没有更糟。
    lo = max(0, start - SNIPPET_MAX // 2)
    hi = min(len(text), end + SNIPPET_MAX // 2)
    left = text[lo:start]
    lidx = max(left.rfind(". "), left.rfind("; "), left.rfind("? "), left.rfind("! "))
    s = lo + (lidx + 2 if lidx >= 0 else 0)
    right = text[end:hi]
    ridx = right.find(". ")
    e = end + (ridx + 1) if ridx >= 0 else hi
    seg = text[s:e]
    if len(seg) > SNIPPET_MAX:
        # 太长就以名字起点为锚取窗（名字通常在几十字符内），保证名字完整落在窗里
        off = start - s
        a = max(0, off - SNIPPET_MAX // 3)
        seg = seg[a:a + SNIPPET_MAX] + "…"
    return " ".join(seg.split())


def search_name(name: str) -> str:
    """给 OpenAlex 的检索串：去括号内容、去 LaTex 残留、压缩空白。"""
    s = _PAREN_RE.sub(" ", name or "")
    s = " ".join(s.split())
    return s


# ------------------------------------------------------------ 图侧名单
def load_curated():
    """80 条 curated 具名公式（Phase 67 R3 认定的分母）。"""
    with open(NORM, encoding="utf-8") as f:
        g = json.load(f)
    forms = [n for n in g["nodes"] if "Formula" in (n.get("labels") or [])]
    cur = []
    for n in forms:
        p = n.get("props") or {}
        nm = p.get("name") or ""
        src = p.get("source") or ""
        # curated 的来源写法不统一（curated_seed / curated_seed_20261016 …）
        if not src.startswith("curated_seed"):
            continue
        if not HUMAN_NAME_RE.search(nm):
            continue
        cur.append({"fid": n["id"], "name": nm, "domain": p.get("domain") or "",
                    "latex": p.get("latex") or ""})
    cur.sort(key=lambda r: r["fid"])
    return forms, cur


def match_in_work(work, ntoks, stats=None):
    """在一篇 work 上找提及：先看标题再看摘要，返回 None 或 dict。

    ★★ 抽完必须**当场回到定位句里再匹配一次**（D94）。
       候选池保存的是 snippet，不是全文 —— 一旦句子的边界算错，日后没有任何办法恢复。
       这里验不过就**丢弃该候选**并计数：宁可少一条候选，也不留一条指向空的证据。
    """
    title = work.get("display_name") or work.get("title") or ""

    def ok(snip, why):
        if find_span(canon_tokens(snip), ntoks) >= 0:
            return True
        if stats is not None:
            stats["dropped_snippet"] += 1
        return False

    if find_span(canon_tokens(title), ntoks) >= 0:
        snip = " ".join(title.split())[:SNIPPET_MAX]
        return {"where": "title", "snippet": snip} if ok(snip, "title") else None
    abst = invert_abstract(work.get("abstract_inverted_index"))
    if not abst:
        return None
    atoks = canon_tokens(abst)
    i = find_span(atoks, ntoks)
    if i < 0:
        return None
    snip = sentence_around(abst, atoks, i, len(ntoks))
    if find_span(canon_tokens(snip), ntoks) < 0:
        # 句子太长被窗口切掉 ⇒ 退回以名字为中心的固定窗
        start, end = atoks[i][1], atoks[i + len(ntoks) - 1][2]
        snip = "…" + abst[max(0, start - 60):min(len(abst), end + 140)] + "…"
        if find_span(canon_tokens(snip), ntoks) < 0:
            if stats is not None:
                stats["dropped_snippet"] += 1
            return None
    return {"where": "abstract", "snippet": snip}


# ------------------------------------------------------------ 抓取
def search_once(name: str, per_page: int = PER_FORMULA):
    q = urllib.parse.urlencode({
        # ★ title_and_abstract.search：限定"名字出现在标题或摘要里"，
        #   而不是全文检索 —— 后者连参考文献里的一次引用都算，噪声不可控。
        "filter": "title_and_abstract.search:%s,type:article" % name,
        "sort": "cited_by_count:desc",
        "per-page": per_page,
        "select": SELECT,
        "mailto": MAILTO,
    })
    return get_json("%s?%s" % (BASE, q))


def _load_prev():
    """上一次成功产出的候选池（用于 --resume）。没有就返回 {}。"""
    path = getattr(_CTX, "out", OUT)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:                       # noqa: BLE001 —— 坏的旧文件当作没有
        return {}
    out = {}
    for r in (d.get("formulas") or []):
        # ★ 只沿用「跑成功」的条目；error 行必须重抓。
        #   特别注意 `--resume` 读的可能是**上一次 FAIL 的产物**吗？不会 —— D95
        #   保证失败的运行不写 OUT（写到 .partial.json），所以 OUT 永远只有成功态
        #   或「本次明确允许的部分态」两种可能。这里再兜一层是双保险。
        if r.get("candidates") is not None and not r.get("error"):
            out[r["fid"]] = r
    return out


class _Ctx:
    """一次性重写全局路径的测试挂载点（只为自检用，不进生产路径）。

    ★ 为什么非要这么个别扭的东西：D96 的「配额耗尽即停」是一条**结构性**要求，
      它的判据必须能离线跑 —— 不能等到下次真撞墙才知道它灵不灵（下次要等 22 小时）。
      于是自检要能把写盘目标指到临时目录。代价是多一个对象，收益是判据天天可跑。
    """
    out = OUT


_CTX = _Ctx()


def cmd_estimate(args):
    """只读估算：重跑要花多少次请求（**不联网**）。

    ★ 为什么要有这一步：今天的教训是「同一个出口 IP 每天只有约 100 次检索额度」。
      上手就跑 = 一天额度用掉 80 次；先估 = 知道这次会花多少、`--resume` 能省多少。
      这一层**一个请求都不发**，所以它自己永远不会消耗配额、也不会被限流挡住。
    """
    forms, cur = load_curated()
    prev = _load_prev() if args.resume else {}
    if args.limit:
        cur = cur[:args.limit]
    reuse = sum(1 for c in cur if c["fid"] in prev)
    need = len(cur) - reuse
    print("=" * 78)
    print("Phase 68 · 抓取前估算（**不联网、不消耗配额**）")
    print("=" * 78)
    print("  curated 具名公式     ：%d 条（本次范围）" % len(cur))
    print("  --resume 可沿用      ：%d 条（上次**成功**的，不用重新请求）" % reuse)
    print("  **本次将实际请求**   ：%d 次" % need)
    print("  OpenAlex 免费额度     ：约 100 次检索/天（按出口 IP；$0.001/次、上限 $0.1/天）")
    if need > 100:
        print("  ⚠ 超出单日额度上限 ⇒ 必须分批多天，或先做物理学子集。\n"
              "    建议：python 11_真实数据/phase68_paper_fetch.py --domains physics --estimate")
    else:
        print("  余量：新的一天还剩约 %d 次" % max(0, 100 - need))
    print()
    print("  确认后执行：python 11_真实数据/phase68_paper_fetch.py --fetch%s\n"
          % (" --resume" if args.resume else ""))
    return 0


def cmd_fetch(args):
    forms, cur = load_curated()
    prev = _load_prev() if args.resume else {}
    print("=" * 78)
    print("Phase 68 · mentioned_in 候选检索（联网）")
    print("=" * 78)
    print("  公式总数 %d ｜ curated 具名 %d 条 ｜ 本次处理 %s"
          % (len(forms), len(cur), ("前 %d 条" % args.limit) if args.limit else "全部"))
    if args.limit:
        cur = cur[:args.limit]

    out_rows, t0 = [], time.time()
    _drop = collections.Counter()
    reused = 0
    budget_hit = None
    for k, c in enumerate(cur, 1):
        # ★ --resume：上一次**成功且确定无候选**的条目直接沿用。
        #   注意只沿用「跑成功」的（error 为空的那类），失败的不沿用 ——
        #   否则一次限流就会把"抓失败了"固化成"这条公式没有候选"，无法区分。
        hit = prev.get(c["fid"])
        if hit is not None:
            out_rows.append(hit)
            reused += 1
            print("  [%2d/%d] %-24s %-30s 沿用上次 %2d 条候选"
                  % (k, len(cur), c["fid"], c["name"][:30], len(hit["candidates"])))
            continue
        nm, ntoks = c["name"], name_tokens(c["name"])
        row = {"fid": c["fid"], "name": nm, "domain": c["domain"],
               "search_name": search_name(nm), "name_tokens": ntoks,
               "field_of": FIELD_OF.get((c["domain"] or "").split(".")[0]),
               "search_total": None, "candidates": [], "error": None}
        try:
            j = search_once(row["search_name"])
            row["search_total"] = (j.get("meta") or {}).get("count")
            for w in j.get("results") or []:
                m = match_in_work(w, ntoks, _drop)
                if not m:
                    continue
                tp = w.get("primary_topic") or {}
                fld = tp.get("field") or {}
                row["candidates"].append({
                    "wid": (w.get("id") or "").rsplit("/", 1)[-1],
                    "doi": ((w.get("ids") or {}).get("doi") or w.get("doi")),
                    "title": w.get("display_name") or w.get("title") or "",
                    "year": w.get("publication_year"),
                    "cited_by": w.get("cited_by_count") or 0,
                    "language": w.get("language"),
                    "field_id": fld.get("id"),
                    "field": fld.get("display_name"),
                    "topic": tp.get("display_name"),
                    "where": m["where"], "snippet": m["snippet"],
                })
        except BudgetExhausted as e:
            # ★ D96：**立刻停**。配额是按出口 IP 按天结算的，第一条撞墙意味着
            #   后面 79 条一条也发不出去 —— 继续循环只会产出 79 条相同的失败。
            #   2026-10-21 的教训：没分类之前，同样的情况跑成了 65 条失败 + 两小时。
            row["error"] = str(e)
            out_rows.append(row)
            for rest in cur[k:]:
                out_rows.append({"fid": rest["fid"], "name": rest["name"],
                                 "domain": rest["domain"],
                                 "search_name": search_name(rest["name"]),
                                 "name_tokens": name_tokens(rest["name"]),
                                 "field_of": FIELD_OF.get((rest["domain"] or "").split(".")[0]),
                                 "search_total": None, "candidates": [],
                                 "error": "aborted: budget exhausted（本轮未真正发起请求）"})
            print("  [%2d/%d] %-24s %-30s 候选  0 ／ 配额耗尽"
                  % (k, len(cur), c["fid"], nm[:30]))
            budget_hit = str(e)
            break
        except FetchError as e:
            row["error"] = str(e)
        out_rows.append(row)
        n_hit = len(row["candidates"])
        print("  [%2d/%d] %-24s %-30s 候选 %2d ／ 检索命中 %s"
              % (k, len(cur), c["fid"], nm[:30], n_hit,
                 "{:,}".format(row["search_total"]) if row["search_total"] is not None else "ERR"))
        if row["error"]:
            print("        ⚠ %s" % row["error"])

    payload = {
        "meta": {
            "phase": "Phase68-paper-mention",
            "source": "openalex",
            "endpoint": BASE,
            "filter": "title_and_abstract.search + type:article，被引降序",
            "per_formula": PER_FORMULA,
            "rule": "mentioned_in_v1：公式名规范化后必须**连续**出现在标题或摘要",
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "n_formulas_total": len(forms),
            "n_curated": len(cur),
            "stats": dict(STATS),
            "dropped_snippet": dict(_drop),
            "license": "CC0",
            "note": "候选池，不是结论；判定与落盘由 phase68_paper_mention_delta 依 VERDICT 表执行",
        },
        "formulas": out_rows,
    }
    n_err = sum(1 for r in out_rows if r.get("error"))
    if budget_hit:
        payload["meta"]["budget_blocked"] = budget_hit
        payload["meta"]["budget_blocked_index"] = (
            [i for i, r in enumerate(out_rows) if not str(r["error"]).startswith("aborted")]
            or [-1])[-1]
    payload["meta"]["n_error"] = n_err
    payload["meta"]["n_reused"] = reused
    out_path = getattr(_CTX, "out", OUT)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # ★★ D95：**不允许用劣化的数据覆盖一份好的候选池**。
    #   2026-10-21 第二轮抓取撞上 HTTP 429 ⇒ 65/80 条失败 ⇒ 脚本照常落盘，
    #   把第一轮的 447 条候选直接替成了 59 条，而且是**静默**的：
    #   失败形态不是报错，是"数据变少了"。这一类必须堵在写盘这一步。
    if n_err:
        partial = out_path.replace(".json", ".partial.json")
        os.replace(tmp, partial)
        print("\n[FATAL] 本次有 %d/%d 条失败 ⇒ **不覆盖**已有候选池 %s"
              % (n_err, len(out_rows), os.path.relpath(out_path, ROOT)))
        print("        部分结果写在 %s（供排查）；上次的成功产物未被改动。"
              % os.path.relpath(partial, ROOT))
        return 1
    os.replace(tmp, out_path)

    hit = sum(1 for r in out_rows if r["candidates"])
    cands = sum(len(r["candidates"]) for r in out_rows)
    infield = sum(1 for r in out_rows for c in r["candidates"]
                  if c["field_id"] == r["field_of"])
    print("-" * 78)
    print("  有候选的公式 %d/%d ｜ 候选总数 %d ｜ 同学科候选 %d"
          % (hit, len(out_rows), cands, infield))
    print("  请求 %d 次、失败 %d、被限流 %d ｜ 耗时 %.0fs ｜ 定位句自检失败被丢 %d ｜ 沿用 %d"
          % (STATS["requests"], STATS["failures"], STATS["throttled"],
             time.time() - t0, _drop["dropped_snippet"], reused))
    print("  → %s" % os.path.relpath(OUT, ROOT))
    return 0


def cmd_show(args):
    if not os.path.exists(OUT):
        print("尚无候选文件：%s" % OUT)
        return 2
    with open(OUT, encoding="utf-8") as f:
        d = json.load(f)
    for row in (d.get("formulas") or []):
        if args.show not in (row["fid"], row["name"]):
            continue
        print("=" * 78)
        print("%s  %s  [%s]  search_total=%s"
              % (row["fid"], row["name"], row["domain"], row["search_total"]))
        for c in row["candidates"]:
            print("  - %s｜%s｜%s｜cites=%d｜%s" % (c["wid"], c["year"], c["field"], c["cited_by"], c["where"]))
            print("    %s" % (c["title"] or "")[:110])
            print("    「%s」" % c["snippet"][:220])
        return 0
    print("未找到公式：%s" % args.show)
    return 1


# ------------------------------------------------------------ 自检
# ★ 这些样例刻意包含会咬人的形状：变音符、所有格、连字符、括号限定名、
#   以及**同名不同概念**（这才是本线的头号假阳性）。
_CASES = [
    # (文本, 公式名, 是否应命中, 理由)
    ("The Nernst equation relates electrode potential to concentration.",
     "Nernst equation", True, "最基本的形态"),
    ("We review Schrödinger's equation for a particle in a box.",
     "Schrödinger equation", True, "所有格 's 应在两边同时折叠"),
    ("The Maxwell-Boltzmann distribution is derived here.",
     "Maxwell-Boltzmann distribution", True, "连字符拆词后仍对齐"),
    ("The Schrödinger equation (time-independent) is solved numerically.",
     "Schrödinger equation (time-independent)", True, "**括号限定名**要在检索名里剥掉"),
    ("Mass-energy equivalence is the well-known result.",
     "Mass-energy equivalence", True, "hyphen 拆词"),
    ("In economics, the Le Chatelier principle describes constrained optimization.",
     "Le Chatelier's principle", True, "★ 真串、假概念：matcher 只判提及，**由 VD 层拦**"),
    ("We derive an analog of the Frankl-Wilson theorem on independence numbers.",
     "Wilson's theorem", False, "★ 姓氏型名字的典型假阳性（Frankl-Wilson ≠ Wilson 定理）"),
    ("The Nerst equation was misspelled in this abstract.",
     "Nernst equation", False, "拼写差一个字母 ⇒ 不得命中"),
    ("Wilson's theorem states that (p-1)! ≡ -1 mod p.",
     "Wilson's theorem", True, "真正的威尔逊定理"),
    ("The partition function Z is the normalizing constant.",
     "Partition function", True, "通用名词，命中即算提及（概念歧义留 VD）"),
]


def _sc_estimate_readonly():
    """C9：估算必须**真的一个请求都不发**（运行时验证，不是源码注释）。

    ★ 为什么连这也值得一条断言：配额耗尽那天，唯一安全的动作就是「只做不联网的事」。
      估算要是偷偷发一次请求，既浪费本就紧张的额度，又会让"先估后跑"
      这个习惯失去意义 —— 而它的失败形态又是静默的（没人会去看 STATS）。
    """
    import argparse
    real_get = globals()["get_json"]
    calls = {"n": 0}

    def fake_get(url, *a, **k):
        calls["n"] += 1
        raise AssertionError("估算阶段不许发起任何请求")

    globals()["get_json"] = fake_get
    try:
        rc = cmd_estimate(argparse.Namespace(limit=0, resume=True))
    finally:
        globals()["get_json"] = real_get
    return [_sc(rc == 0 and calls["n"] == 0,
                "C9 估算不发任何请求（stub 掉 get_json 后仍为 0 次）",
                "rc=%s ／ 实际发起 %d 次" % (rc, calls["n"]))]


def _sc_abort_budget():
    """C5：配额耗尽 ⇒ 第 1 条就停，其余标记为未发起请求（不联网）。

    2026-10-21 实测：同样的情形，旧的逻辑把 80 条全部跑成失败（每条 3 次重试 ×
    30 秒退避 ≈ 两小时），并且把结果照常写盘。判据不能只是"代码里有这段"，
    必须是"跑起来是这个行为"。
    """
    import argparse
    import tempfile

    real_search, real_out = globals()["search_once"], getattr(_CTX, "out", OUT)
    calls = {"n": 0}

    def boom(name):
        calls["n"] += 1
        raise BudgetExhausted("OpenAlex 当日额度已耗尽（仿真）", retry_after=80202.0,
                              reset_at=80202)

    with tempfile.TemporaryDirectory() as td:
        _CTX.out = os.path.join(td, "mentions.json")
        globals()["search_once"] = boom
        try:
            rc = cmd_fetch(argparse.Namespace(limit=20, resume=False))
        finally:
            globals()["search_once"] = real_search
            partial = os.path.join(td, "mentions.partial.json")
            written = os.path.exists(partial)
            keep_root = os.path.exists(_CTX.out)
            _CTX.out = real_out

    return [
        _sc(calls["n"] == 1, "C5 「配额耗尽」在第 1 条就停，不再消耗请求",
            "实际发起 %d 次（旧逻辑会跑满 20 次）" % calls["n"]),
        _sc(rc == 1, "C6 失败的抓取以 RC=1 退出（调用方能据此中止流水线）", "rc=%s" % rc),
        _sc(written and not keep_root,
            "C7 失败只写 .partial.json，**不动**正式候选池（name it or lose it）",
            "partial=%s ／ 正式=%s（须 True/False）" % (written, keep_root)),
    ]


def _sc_protect_good_pool():
    """C8：一份已存在的**成功**候选池，不会被后续失败的运行覆盖掉。

    这是 2026-10-21 真实发生过的事故：第二轮把第一轮 447 条候选的成品静默替成
    59 条。守卫是事后加的，所以这里补一条**天天可跑**的证据。
    """
    import argparse
    import tempfile

    good = {"meta": {"n_error": 0}, "formulas": [
        {"fid": "X:1", "name": "Alpha law", "domain": "physics",
         "candidates": [{"wid": "W1", "title": "On Alpha law", "where": "title"}]},
    ]}
    real_search = globals()["search_once"]
    real_out = getattr(_CTX, "out", OUT)

    def boom(name):
        raise BudgetExhausted("OpenAlex 当日额度已耗尽（仿真）", retry_after=80202.0)

    with tempfile.TemporaryDirectory() as td:
        _CTX.out = os.path.join(td, "mentions.json")
        with open(_CTX.out, "w", encoding="utf-8") as f:
            json.dump(good, f)
        globals()["search_once"] = boom
        try:
            cmd_fetch(argparse.Namespace(limit=5, resume=False))
        finally:
            globals()["search_once"] = real_search
            after = json.load(open(_CTX.out, encoding="utf-8"))
            _CTX.out = real_out

    kept = [r["fid"] for r in after.get("formulas") or []]
    return [_sc(kept == ["X:1"],
                "C8 好数据没有被失败运行覆盖（旧候选完好）",
                "运行后候选池条目 = %s（须仍为 ['X:1']）" % kept)]


def _sc(ok: bool, name: str, detail: str = "") -> bool:
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  — " + detail) if detail else ""))
    return ok


def cmd_selfcheck(args):
    print("=" * 78)
    print("phase68_paper_fetch 离线自检（匹配契约；不联网）")
    print("=" * 78)
    rs = []
    # ★ 编号：动态部分用 H（hit case），纯函数契约用 C —— 两组**不许共用前缀**。
    #   共用一个前缀会让"按 ID 定位断言"这件事失去唯一性（D74 撞号、D87 锚点过期的同一族）。
    for text, nm, want, why in _CASES:
        toks, ntoks = canon_tokens(text), name_tokens(nm)
        i = find_span(toks, ntoks)
        got = i >= 0
        label = "H%-2d %-38s %s" % (len(rs) + 1, nm[:38], "should-hit" if want else "should-miss")
        rs.append(_sc(got == want, label, why if got != want else ""))
        if got and want:
            sn = sentence_around(text, toks, i, len(ntoks))
            # ★ D94 的守卫：**把名字塞回定位句里再匹配一次**。
            #   只查"非空"等于没查 —— 句子边界算歪时，snippet 照样是非空的一句废话。
            rs.append(_sc(bool(sn.strip()) and find_span(canon_tokens(sn), ntoks) >= 0,
                          "    定位句里能重新找到公式名（可溯源）",
                          sn[:66] if sn.strip() else "空串！"))
    # 纯函数额外契约
    # NFKD 把上标 ² 折成 2 ⇒ "mc²" 与文献里常见的 ASCII 写法 "mc2" 对齐
    rs.append(_sc(name_tokens("E = mc²") == ["e", "mc2"],
                  "C1 含符号/上标的名字也能分词（上标 NFKD 折叠）",
                  str(name_tokens("E = mc²"))))
    rs.append(_sc(search_name("Schrödinger equation (time-independent)") == "Schrödinger equation",
                  "C2 检索名剥离括号限定",
                  search_name("Schrödinger equation (time-independent)")))
    rs.append(_sc([t for t, _s, _e in canon_tokens("Newton’s law")] == ["newton", "law"],
                  "C3 弯撇号所有格整体折叠（不是把 's 留成一个词）",
                  str([t for t, _s, _e in canon_tokens("Newton’s law")])))
    # C4 数据完整性守卫（D95）：**失败的运行不许覆盖好的候选池**。
    #   这是一条源码级契约 —— 弱，但比没有强：至少谁删掉这段会立刻红。
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    # ★★ 锚点一律用**字符串拼接**构造（本会话第 4 次踩自指：O5 两次 + C4 一次）。
    #    断言/注释里一旦写出完整的目标串，它自己就被算进去了。
    _p_write = 'json.dump(' + 'payload'
    _p_move = 'os.replace(' + 'tmp, out_path)'
    _p_part = '.partial' + '.json'
    guarded = (src.count(_p_write) == 1 and src.count(_p_move) == 1 and _p_part in src)
    rs.append(_sc(guarded, "C4 劣化数据不得覆盖候选池（tmp→partial 写入路径完整）",
                  "写盘点 %d ／ 原子替换 %d ／ partial 分支 %s"
                  % (src.count(_p_write), src.count(_p_move), _p_part in src)))

    # ---- C5：真跑一遍「配额耗尽」路径（stub 掉网络），验证它**立刻停**。 ----
    # 这条不能用源码锚点糊弄：上一次我们用源码锚点证明了"魔法都在"，可真实
    # 行为仍然是跑满 80 条。要证明的是**运行时**行为，就得真的跑一次。
    rs.extend(_sc_abort_budget())
    rs.extend(_sc_protect_good_pool())
    rs.extend(_sc_estimate_readonly())
    bad = sum(1 for x in rs if not x)
    print("-" * 78)
    print("  合计 %d 条，通过 %d，失败 %d" % (len(rs), len(rs) - bad, bad))
    print("  注：①/⑤ 里命中但概念不同的情形（Le Chatelier 经济学）**刻意判为命中** ——")
    print("      matcher 的职责只是「这段文字提到了这个名字」，概念是否一致归 VERDICT 表。")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--resume", action="store_true",
                    help="沿用上次**成功**的条目，只补抓失败/缺失的")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--estimate", action="store_true",
                    help="只读估算本次会实际发起多少次请求（**不联网、不消耗配额**）")
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--show", default="")
    args = ap.parse_args()
    if args.selfcheck:
        return cmd_selfcheck(args)
    if args.show:
        return cmd_show(args)
    if args.estimate:
        return cmd_estimate(args)
    if args.fetch:
        return cmd_fetch(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
