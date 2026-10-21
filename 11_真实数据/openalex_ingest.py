# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""openalex_ingest.py —— Phase 8 真实 ETL：从 OpenAlex 拉取数学文献真实子图。

背景
----
Phase 8 目标是「真实 ETL 规模化」（路线图 §3）。原计划的 arXiv 官方 API
（``export.arxiv.org/api/query``）经探测被沙箱出口白名单**按路径拦截**（TLS 可握手但
响应 0 字节超时）；而 **OpenAlex（api.openalex.org）可达**，它是 CC0 授权的开放学术图谱，
覆盖 arXiv 预印本与正式出版物，含 works / 引用 / 主题概念，字段完整、可批量拉取。

因此本模块以 OpenAlex 为真实数据源，拉取目标子领域的高被引文献，
构建 ``Paper`` 节点 + ``cites`` 真实引用边 + ``discusses`` 主题桥接边。

数据源可达性（2026-10-09 实测）
--------------------------------
  可达：api.openalex.org ✓  api.crossref.org ✓  api.datacite.org ✓  arxiv.org/abs,/list ✓
  被拦：export.arxiv.org/api/query ✗（0 B 超时）  wikidata.org ✗  huggingface.co ✗

用法
----
    python 11_真实数据/openalex_ingest.py --fetch            # 拉取 raw（默认写入 11_真实数据/）
    python 11_真实数据/openalex_ingest.py --fetch --limit 200
    python 11_真实数据/openalex_ingest.py --build            # raw -> seed 格式（供 merge_seed_delta）
    python 11_真实数据/openalex_ingest.py --selfcheck        # 离线自检（含 to_raw 不丢摘要）

Phase 68 改动（D92-①：摘要从未被抓取）
-------------------------------------
本模块原先把 `abstract_inverted_index` **既没进 SELECT 也没进 to_raw** ⇒ 抓出来的
188 条 raw 摘要覆盖率为 0（G4 探针 R5 实测），而摘要正是「公式在文献里有没有被提到」
唯一的定位依据。现在：

  * SELECT / fields 增列 `abstract_inverted_index`
  * `to_raw()` 增 `abstract`（还原文本）与 `abstract_len`（词数，用于覆盖率统计）
  * HTTP 层改为复用 `fetch_common`（规划 F17），本模块不再自带第三份 `_get_json`

★ 只改生成器，**不重跑历史抓取**：`openalex_math_raw.json` 是登记过的数据源产物，
  它的字节进过来源核对；为补摘要而重写它会让既有证据判定为漂移。**修了生成器，不等于历史产物会变** ——
  这一点必须写进文档，否则下一个人会以为 raw 里应该有摘要，然后得出「本地 0 条 ⇒ 网络也没有」的错误结论。
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.parse
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, HERE)
# ★ 复用抓取公共层（F17）：限速/重试/UA/绕过代理只此一份实现。
#   本模块原先自带一份 `_get_json`，与 chebi_synonym_fetch、_recon_papers 构成三份
#   互不相同的 HTTP 代码；差别只在注释里（"代理会 502，必须直连"），抄丢一次就踩一次。
from fetch_common import (                                    # noqa: E402
    get_json, invert_abstract, abstract_len, POLITE_MAILTO, STATS, FetchError,
)

ROOT = os.path.abspath(os.path.join(HERE, ".."))
RAW = os.path.join(HERE, "openalex_math_raw.json")
SEED = os.path.join(HERE, "seed_papers_openalex.json")

MAILTO = "sttp-etl@example.org"
BASE = "https://api.openalex.org/works"

# 目标子领域：Algebra and Number Theory（承接 Phase 7 数论切片 NT）
SUBFIELD_ID = "subfields/2602"
SUBFIELD_NAME = "Algebra and Number Theory"

SELECT = ",".join([
    "id", "doi", "title", "display_name", "publication_year", "type", "cited_by_count",
    "authorships", "primary_topic", "topics", "concepts", "referenced_works",
    "ids", "language", "open_access", "locations", "biblio",
    "abstract_inverted_index",          # ★ D92-①：漏这一列 ⇒ 摘要覆盖率永远为 0
])

_create = datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- HTTP
def _get_json(url: str, timeout: int = 30):
    """薄转发到 `fetch_common.get_json`（保留旧名：外部文档引用过它）。

    行为差异有两处，都是刻意的：
      * 失败会抛 `FetchError`（带可重试/不可重试标注），不再是裸 URLError 冒出去
      * 自动限速默认 0.35s ⇒ 调用方**不必再自己 sleep**（下面两处 time.sleep 已删）。
        留着调用方的 sleep 不会坏，但会让"限速"这件事出现在两个地方，
        改一个忘一个是必然的。
    """
    return get_json(url, timeout=timeout)


def fetch_works(subfield_id: str, limit: int, per_page: int = 100):
    """按 subfield 拉取 article 类 works（按被引降序），cursor 分页。"""
    got, cursor, pages = [], "*", 0
    while len(got) < limit:
        q = urllib.parse.urlencode({
            "filter": "primary_topic.subfield.id:%s,type:article" % subfield_id,
            "sort": "cited_by_count:desc",
            "per_page": min(per_page, limit - len(got)),
            "cursor": cursor,
            "select": SELECT,            # ★ 不再另写一份 fields（原先两处需手工同步）
            "mailto": MAILTO,
        })
        d = _get_json("%s?%s" % (BASE, q))
        res = d.get("results", [])
        got += res
        cursor = d.get("meta", {}).get("next_cursor")
        pages += 1
        print("  第 %d 页：+%d（累计 %d / 目标 %d）" % (pages, len(res), len(got), limit))
        if not res or not cursor:
            break
    return got


def fetch_works_by_ids(wids, per_page: int = 50):
    """按 OpenAlex work id 批量取（用于把高频被引论文也纳入集合）。"""
    wids = [w for w in wids if w]
    out = []
    for i in range(0, len(wids), per_page):
        chunk = wids[i:i + per_page]
        q = urllib.parse.urlencode({
            "filter": "openalex_id:%s" % "|".join(chunk),
            "per_page": per_page,
            "select": SELECT,
            "mailto": MAILTO,
        })
        d = _get_json("%s?%s" % (BASE, q))
        out += d.get("results", [])
    return out


# ---------------------------------------------------------------- 归一
def oa_id(w: dict) -> str:
    """https://openalex.org/W123 -> W123"""
    return (w.get("id") or "").rsplit("/", 1)[-1]


def norm_title(s: str) -> str:
    return " ".join((s or "").lower().split())


def to_raw(w: dict) -> dict:
    """OpenAlex work -> 归一 raw 记录（保留可审计字段）。"""
    ids = w.get("ids") or {}
    tp = w.get("primary_topic") or {}
    sub = tp.get("subfield") or {}
    fld = tp.get("field") or {}
    authors = [a.get("author", {}).get("display_name") for a in (w.get("authorships") or [])]
    authors = [a for a in authors if a]
    topics = [t.get("display_name") for t in (w.get("topics") or []) if t.get("display_name")]
    concepts = [c.get("display_name") for c in (w.get("concepts") or []) if c.get("display_name")]
    locs = w.get("locations") or []
    venue = ""
    for L in locs:
        src = (L or {}).get("source") or {}
        if src.get("display_name"):
            venue = src["display_name"]
            break
    refs = [r.rsplit("/", 1)[-1] for r in (w.get("referenced_works") or [])]
    aix = w.get("abstract_inverted_index")
    return {
        "wid": oa_id(w),
        "doi": ids.get("doi") or w.get("doi"),
        "arxiv": ids.get("arxiv"),
        "title": w.get("display_name") or w.get("title") or "",
        "year": w.get("publication_year"),
        "pub_type": w.get("type"),
        "cited_by": w.get("cited_by_count") or 0,
        "authors": authors,
        "venue": venue,
        "language": w.get("language"),
        "primary_topic": tp.get("display_name"),
        "topics": topics,
        "concepts": concepts,
        "subfield": sub.get("display_name"),
        "subfield_id": (sub.get("id") or "").rsplit("/", 1)[-1],
        "field": fld.get("display_name"),
        "is_oa": (w.get("open_access") or {}).get("is_oa"),
        "oa_status": (w.get("open_access") or {}).get("oa_status"),
        "referenced_works": refs,
        # ★ 摘要（D92-①）。存**还原后的文本**而不存倒排表：
        #   倒排表只是另一种表示，同时存两份就等于允许"文本自己错而且没人查"；
        #   文本才是后面做定位句、做人工核对时真正看的那一层。还原正确性由
        #   fetch_common --selfcheck 的 F1/F5/F6 守着。
        "abstract": invert_abstract(aix),
        "abstract_len": abstract_len(aix),
    }


# ---------------------------------------------------------------- 主流程
def cmd_fetch(args):
    print("=" * 72)
    print("OpenAlex 拉取 · subfield=%s (%s)" % (SUBFIELD_NAME, SUBFIELD_ID))
    print("=" * 72)
    works = fetch_works(SUBFIELD_ID, args.limit, args.per_page)
    seeds = [to_raw(w) for w in works]
    print("  初次拉取 %d 篇" % len(seeds))

    # 统计 references 频次，把高频被引（领域基石）也纳入集合，形成更闭合的引用网
    seed_ids = {s["wid"] for s in seeds}
    freq = {}
    for s in seeds:
        for r in s["referenced_works"]:
            if r not in seed_ids:
                freq[r] = freq.get(r, 0) + 1
    top_ref = [w for w, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:args.expand]]
    if top_ref:
        print("  高频被引待补 %d 篇（出现次数 top）" % len(top_ref))
        extra = [to_raw(w) for w in fetch_works_by_ids(top_ref)]
        have = seed_ids
        for e in extra:
            if e["wid"] not in have:
                seeds.append(e)
                have.add(e["wid"])
        print("  补入后合计 %d 篇" % len(seeds))

    payload = {
        "meta": {
            "source": "openalex",
            "endpoint": BASE,
            "subfield_id": SUBFIELD_ID,
            "subfield": SUBFIELD_NAME,
            "license": "CC0",
            "fetched_at": _create,
            "count": len(seeds),
            "note": "arXiv 官方 API 被出口白名单按路径拦截，改用可达的 OpenAlex 开放学术图谱",
        },
        "works": seeds,
    }
    json.dump(payload, open(RAW, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n[OK] raw -> %s  (%.1f KB)" % (os.path.relpath(RAW, ROOT),
                                           os.path.getsize(RAW) / 1024))
    # 概览
    yrs = [s["year"] for s in seeds if s["year"]]
    tot_refs = sum(len(s["referenced_works"]) for s in seeds)
    print("  年份 %d–%d ｜ 引用记录 %d 条 ｜ 有摘要主题 %d 篇"
          % (min(yrs), max(yrs), tot_refs, sum(1 for s in seeds if s["topics"])))
    return 0


# ---------------------------------------------------------------- 桥接映射
# 论文主题（OpenAlex primary_topic，新体系，质量高于 concepts）-> 图中概念节点
# 说明：**不使用 OpenAlex 的 concepts 字段做匹配** —— 它存在明显消歧错误
# （"Prime (order theory)"、"Ring (chemistry)"、"Calculus (dental)"、"Dentistry"），
# 用它匹配 NT:mc:prime 会把序理论论文错误挂到数论素数上（外部字段语义必须验证）。
TOPIC_TO_CONCEPT = {
    "Analytic Number Theory Research":        "NT:mc:number_theory",
    "Algebraic Geometry and Number Theory":   "NT:mc:number_theory",
    "Advanced Mathematical Identities":       "NT:mc:number_theory",
    "Rings, Modules, and Algebras":           "MA:mc:abstract_algebra",
    "Advanced Topics in Algebra":             "MA:mc:abstract_algebra",
    "Commutative Algebra and Its Applications": "MA:mc:abstract_algebra",
}

# 新增领域概念节点（MathConcept，schema 已声明；真实存在的数学分支，非臆造）
NEW_CONCEPTS = [
    {"id": "NT:mc:number_theory",   "name": "Number theory",   "definition": "整数与整数性质的研究（整除、素数、同余、丢番图方程等）"},
    {"id": "MA:mc:abstract_algebra", "name": "Abstract algebra", "definition": "群、环、域、模等代数结构的研究"},
]

# 领域概念 -> 既有具体概念（数论包含素数/同余/模算术/欧拉函数/最大公约数）
PART_OF = [
    ("NT:mc:prime",              "NT:mc:number_theory"),
    ("NT:mc:congruence",         "NT:mc:number_theory"),
    ("NT:mc:modular_arithmetic", "NT:mc:number_theory"),
    ("NT:mc:totient",            "NT:mc:number_theory"),
    ("NT:mc:gcd",                "NT:mc:number_theory"),
]


def _clean(props: dict) -> dict:
    """去掉 None / 空值（Neo4j 不接受 null 属性）；空 list 也剔除。"""
    out = {}
    for k, v in props.items():
        if v is None:
            continue
        if isinstance(v, (list, str)) and len(v) == 0:
            continue
        out[k] = v
    return out


def _edge(s, t, etype, kind, conf, eoi, source, evidence=None):
    p = _clean({"confidence": conf, "source": source, "explicit_or_inferred": eoi,
                "created_at": _create, "kind": kind, "evidence": evidence})
    return {"source": s, "target": t, "type": etype, "kind": kind, "props": p}


def cmd_build(args):
    if not os.path.exists(RAW):
        print("缺少 raw，先跑 --fetch"); return 1
    d = json.load(open(RAW, encoding="utf-8"))
    works = [w for w in d["works"] if w.get("field") == "Mathematics"]
    print("载入 raw %d 篇（保留 Mathematics %d 篇）" % (len(d["works"]), len(works)))

    nodes, edges = [], []
    wids, pid_of = set(), {}
    for w in works:
        pid = "PA:oa:" + w["wid"]
        pid_of[w["wid"]] = pid
        wids.add(w["wid"])
        props = {
            "id": pid,
            "name": w["title"],
            "title": w["title"],
            "doi": w.get("doi"),
            "arxiv": w.get("arxiv"),
            "openalex_id": w["wid"],
            "year": w.get("year"),
            "pub_type": w.get("pub_type"),
            "cited_by": w.get("cited_by"),
            "authors": w.get("authors") or [],
            "author_count": len(w.get("authors") or []),
            "venue": w.get("venue"),
            "primary_topic": w.get("primary_topic"),
            "subfield": w.get("subfield"),
            "field": w.get("field"),
            "oa_status": w.get("oa_status"),
            "language": w.get("language"),
            "domain": "math",
            "ntype": "paper",
            "source": "openalex",
            "explicit_or_inferred": "explicit",
            "confidence": 0.95,
            "created_at": _create,
            "version": "v0.2",
        }
        nodes.append({"id": pid, "labels": ["Paper"], "props": _clean(props)})

    # 新增领域概念节点
    for c in NEW_CONCEPTS:
        nodes.append({"id": c["id"], "labels": ["MathConcept"], "props": _clean({
            "id": c["id"], "name": c["name"], "definition": c["definition"],
            "domain": "math", "ntype": "mathconcept", "source": "openalex-topic-align",
            "explicit_or_inferred": "inferred", "confidence": 0.9,
            "created_at": _create, "version": "v0.2",
        })})

    # cites 边（集合内真实引用）
    cites = set()
    for w in works:
        for r in w["referenced_works"]:
            if r in wids and r != w["wid"]:
                cites.add((w["wid"], r))
    for a, b in sorted(cites):
        edges.append(_edge(pid_of[a], pid_of[b], "cites", "explicit_citation",
                           1.0, "explicit", "openalex",
                           evidence="OpenAlex referenced_works"))

    # discusses 边（论文 -> 领域概念，按 primary_topic 对齐）
    disc = 0
    for w in works:
        cid = TOPIC_TO_CONCEPT.get(w.get("primary_topic"))
        if cid:
            edges.append(_edge(pid_of[w["wid"]], cid, "discusses", "topic_align",
                               0.7, "inferred", "openalex-topic-align",
                               evidence="primary_topic=%s" % w.get("primary_topic")))
            disc += 1

    # part_of 边（领域概念 -> 具体概念）
    for child, parent in PART_OF:
        edges.append(_edge(child, parent, "part_of", "part_of",
                           0.9, "inferred", "schema-axiom",
                           evidence="领域包含关系"))

    seed = {
        "meta": {
            "phase": "Phase8-openalex",
            "source": "openalex",
            "subfield": d["meta"]["subfield"],
            "generated_at": _create,
            "node_count": len(nodes), "edge_count": len(edges),
            "note": "Phase 8 真实 ETL：OpenAlex 数学文献子图（Paper 节点 + 引用网 + 主题桥）",
        },
        "nodes": nodes, "edges": edges,
    }
    json.dump(seed, open(SEED, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n[OK] seed -> %s" % os.path.relpath(SEED, ROOT))
    print("  Paper 节点 %d ｜ 新概念 %d ｜ cites 边 %d ｜ discusses 边 %d ｜ part_of 边 %d"
          % (len(works), len(NEW_CONCEPTS), len(cites), disc, len(PART_OF)))
    print("  合计 节点 %d / 边 %d" % (len(nodes), len(edges)))
    return 0


# ---------------------------------------------------------------- 自检
_SYNTH_WORK = {
    "id": "https://openalex.org/W0000000000",
    "display_name": "A synthetic work for self-check",
    "publication_year": 2026,
    "type": "article",
    "abstract_inverted_index": {
        "We": [0], "verify": [1], "Snell's": [2], "law": [3], "here": [4], ".": [5],
    },
    "ids": {"doi": None},
    "authorships": [{"author": {"display_name": "Self Check"}}],
    "open_access": {"is_oa": True, "oa_status": "gold"},
}


def _sc(ok: bool, name: str, detail: str = "") -> bool:
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  — " + detail) if detail else ""))
    return ok


def cmd_selfcheck(args):
    """离线自检：**不联网**，只验证"生成器不再丢摘要"这一类可离线判定的契约。

    ★ 为什么连 SELECT 常量都要单独判：`fetch_works` 原先自带一份 `fields` 串，
      与 `SELECT` 手工同步 ⇒ 将来谁再加一列，最可能的结果就是 SELECT 加了、
      另一个串没加，抓出来照样没摘要。常量本身也要有人核对。
    """
    print("=" * 74)
    print("openalex_ingest 离线自检（摘要保留 + 常量单一来源）")
    print("=" * 74)
    raw = to_raw(_SYNTH_WORK)
    rs = [
        _sc(raw.get("abstract") == "We verify Snell's law here .",
            "O1 to_raw 保留摘要文本", repr(raw.get("abstract"))),
        _sc(raw.get("abstract_len") == 6, "O2 摘要词数落盘",
            "= %s" % raw.get("abstract_len")),
        _sc("abstract_inverted_index" in SELECT, "O3 SELECT 含摘要列（浏览器实际请求的那一份）"),
        _sc(to_raw({}).get("abstract") == "", "O4 无摘要时不写脏值（空串而非 None）"),
    ]
    # 常量单一来源：两处请求都引用同一个 SELECT，源码里不得再有一份硬编码字段串。
    # ★★ 锚点用**正则提取调用形态**，不要写成包含字段内容或 select 常量的字面串 ——
    #    第一版 O5 数 SELECT 的字段内容，第二版数 select 键后面跟的那个常量名，
    #    两次都被**断言自己的源码/注释**算进去了（自指第三次，同 D77/D87 一族）。
    #    ⇒ 注释里也不要再现拼出那个调用形态。
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    selvals = re.findall(r'"select":\s*([A-Za-z_][A-Za-z_0-9]*)', src)
    rs.append(_sc(selvals == ["SELECT", "SELECT"],
                  "O5 两个请求都用同一个 SELECT 常量（无第二份硬编码字段串）",
                  "调用点取值 %s" % selvals))
    bad = sum(1 for x in rs if not x)
    print("-" * 74)
    print("  合计 %d 条，通过 %d，失败 %d" % (len(rs), len(rs) - bad, bad))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="拉取 raw")
    ap.add_argument("--build", action="store_true", help="raw -> seed")
    ap.add_argument("--limit", type=int, default=150, help="初次拉取篇数")
    ap.add_argument("--expand", type=int, default=40, help="补充高频被引篇数")
    ap.add_argument("--per-page", type=int, default=100)
    ap.add_argument("--selfcheck", action="store_true", help="离线自检（不联网）")
    args = ap.parse_args()
    if args.selfcheck:
        return cmd_selfcheck(args)
    if args.fetch:
        return cmd_fetch(args)
    if args.build:
        return cmd_build(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
