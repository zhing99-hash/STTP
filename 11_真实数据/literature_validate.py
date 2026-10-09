# -*- coding: utf-8 -*-
"""Phase 8-B4 · 文献层多源交叉校验（Crossref + DataCite）。

背景
----
Phase 8 首步用 **OpenAlex** 建了 181 篇数学文献子图（`Paper` 节点 + `cites`/`discusses` 边）。
但 OpenAlex 的字段是**单一来源**，而本项目已多次踩到「外部字段语义/取值不可想当然」
（ElementKG `HASATOMIC` 把族号当原子序数、PhysicsBabel `score` 是复杂度取反、
OpenAlex `concepts` 有消歧错误）—— 文献层的元数据同样需要**独立信源交叉校验**。

本模块用两个**权威且可达**的 DOI 注册机构 API 做校验：
  * **Crossref**（`api.crossref.org`）：绝大多数学术期刊 DOI 的注册机构
  * **DataCite**（`api.datacite.org`）：数据集 / 预印本 / 机构库 DOI 的注册机构
两者**直连均 3/3 可达**（见 `probe_sources.py`），无需走代理。

校验内容
--------
  * `title`（题名）
  * `year`（发表年）
  * `venue` / `container-title`（期刊/会议名）
  * `type`（文献类型）
  * **引用计数**：Crossref `is-referenced-by-count` vs OpenAlex `cited_by`
    （两者口径不同：Crossref 只统计 Crossref 系引用，OpenAlex 覆盖面更广；
     因此**不求数值相等**，而是校验「量级是否可比、是否有一方为零而另一方数千」这类硬矛盾）

用法
----
    python 11_真实数据/literature_validate.py --fetch          # 按 DOI 逐篇查询（支持续传）
    python 11_真实数据/literature_validate.py --fetch --limit 20
    python 11_真实数据/literature_validate.py --check          # 三方对比报告（只读）
    python 11_真实数据/literature_validate.py --build          # 产出 delta
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def unescape(s):
    """Crossref 的标题/刊名里含**双重转义的 HTML 实体**（如 `&amp;#233;` → `&#233;` → `é`）。
    实测样例：`M&amp;#233;moires de la Soci&amp;#233;t&amp;#233; math&amp;#233;matique`。
    必须**两次 unescape**，否则会被误判成「刊名不一致」（我方曾据此误报 3 条）。"""
    if s is None:
        return None
    return html.unescape(html.unescape(str(s)))

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(HERE, "literature_validate_raw.json")
NORMALIZED = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
NEO4J_DIR = os.path.join(ROOT, "06_PoC", "etl", "neo4j")
DELTA = os.path.join(NEO4J_DIR, "phase21_literature_delta.json")

SOURCE_TAG = "crossref+datacite"
CROSSREF = "https://api.crossref.org/works/"
DATACITE = "https://api.datacite.org/dois/"
UA = "STTP/1.0 (academic knowledge-graph; mailto:sttp-etl@example.org)"


def _proxy():
    for k in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"):
        v = os.environ.get(k)
        if v:
            return v.strip()
    return None


def http_json(url, timeout=25, tries=4, use_proxy=False):
    """Crossref / DataCite **直连可达**，默认不走代理；失败再回退代理。"""
    proxy = _proxy()
    plans = [(None, tries)]
    if proxy:
        plans.append((proxy, max(2, tries // 2)))
    last = None
    for px, n in plans:
        ph = (urllib.request.ProxyHandler({"http": px, "https": px}) if px
              else urllib.request.ProxyHandler({}))
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context()), ph)
        opener.addheaders = [("User-Agent", UA), ("Accept", "application/json")]
        for _ in range(n):
            try:
                with opener.open(url, timeout=timeout) as r:
                    return json.loads(r.read().decode("utf-8")), r.status, None
            except urllib.error.HTTPError as e:
                return None, e.code, "http_%s" % e.code
            except Exception as e:                  # noqa: BLE001
                last = "%s" % e
                time.sleep(0.5)
    return None, None, last


# ---------------------------------------------------------------------------
def bare_doi(s):
    if not s:
        return None
    s = str(s).strip()
    s = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", s, flags=re.I)
    return s or None


def load_papers():
    d = json.load(open(NORMALIZED, encoding="utf-8"))
    nodes = d["nodes"] if isinstance(d, dict) else d
    out = []
    for n in nodes:
        if not str(n.get("id", "")).startswith("PA:"):
            continue
        p = n.get("props") or {}
        out.append({
            "id": n["id"],
            "title": p.get("title") or n.get("label"),
            "doi": bare_doi(p.get("doi")),
            "raw_doi": p.get("doi"),
            "year": p.get("year"),
            "venue": p.get("venue"),
            "pub_type": p.get("pub_type"),
            "oa_cited_by": p.get("cited_by"),
            "openalex_id": p.get("openalex_id"),
        })
    out.sort(key=lambda x: x["id"])
    return out


# ---------------------------------------------------------------------------
def _fetch_one(p):
    doi = p.get("doi")
    if not doi:
        return p["id"], {"found": False, "reason": "no_doi"}
    enc = urllib.parse.quote(doi, safe="")
    # Crossref 优先
    d, st, err = http_json(CROSSREF + enc)
    if d is not None and isinstance(d, dict) and d.get("message"):
        m = d["message"]
        return p["id"], {
            "found": True, "via": "crossref", "doi": doi,
            "title": unescape((m.get("title") or [None])[0]),
            "year": _year_from(m.get("issued")),
            "container": unescape((m.get("container-title") or [None])[0]),
            "type": m.get("type"),
            "cited_by": m.get("is-referenced-by-count"),
            "reference_count": m.get("references-count"),
            "publisher": unescape(m.get("publisher")),
            "issn": (m.get("ISSN") or [None])[0],
            "license": ((m.get("license") or [{}])[0]).get("URL"),
            "author_count": len(m.get("author") or []),
            "is_preprint": bool(m.get("institution")),
            "raw_doi": m.get("DOI"),
            "source_api": "crossref",
        }
    # DataCite 兜底（数据集 / 预印本 / 机构库）
    d2, st2, err2 = http_json(DATACITE + enc)
    if d2 is not None and isinstance(d2, dict) and d2.get("data"):
        at = (d2["data"].get("attributes") or {})
        ti = at.get("titles") or []
        return p["id"], {
            "found": True, "via": "datacite", "doi": doi,
            "title": unescape(ti[0].get("title") if ti else None),
            "year": at.get("publicationYear"),
            "container": unescape((at.get("container") or {}).get("title")),
            "type": None,
            "resource_type": ((at.get("types") or {}).get("resourceTypeGeneral")),
            "cited_by": at.get("citationCount"),
            "reference_count": None,
            "publisher": unescape(at.get("publisher")),
            "author_count": len(at.get("creators") or []),
            "raw_doi": (at.get("doi")),
            "source_api": "datacite",
        }
    return p["id"], {"found": False, "reason": err or err2 or "not_found",
                     "crossref_status": st, "datacite_status": st2}


def _year_from(issued):
    try:
        return (issued.get("date-parts") or [[None]])[0][0]
    except Exception:                               # noqa: BLE001
        return None


def cmd_fetch(a):
    papers = load_papers()
    if a.limit:
        papers = papers[:a.limit]
    old = {}
    if os.path.exists(RAW) and not a.fresh:
        try:
            old = json.load(open(RAW, encoding="utf-8")).get("records") or {}
            print("断点续传：已有 %d 条" % len(old))
        except Exception:                           # noqa: BLE001
            old = {}
    todo = [p for p in papers if p["id"] not in old]
    print("Paper %d 篇（%d 篇有 DOI）；待查 %d 篇（并发 %d）"
          % (len(papers), sum(1 for p in papers if p["doi"]), len(todo), a.workers))

    done = dict(old)
    n_ok = n_bf = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(_fetch_one, p): p for p in todo}
        for i, f in enumerate(as_completed(futs), 1):
            pid, rec = f.result()
            done[pid] = rec
            if rec.get("found"):
                n_ok += 1
            else:
                n_bf += 1
            if i % 20 == 0 or i == len(todo):
                el = time.time() - t0
                print("  %3d/%3d  命中 %d · 未命中 %d  (%.0fs)" % (i, len(todo), n_ok, n_bf, el))
            if i % 20 == 0:
                _save(done, papers)
    _save(done, papers)
    print("完成：命中 %d / 未命中 %d / 合计 %d" % (n_ok, n_bf, len(done)))
    print("-> %s" % RAW)


def _save(done, papers):
    payload = {
        "source": SOURCE_TAG,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "total_papers": len(papers),
        "records": done,
    }
    tmp = RAW + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, RAW)


# ---------------------------------------------------------------------------
def _norm_title(s):
    if not s:
        return ""
    s = re.sub(r"\s+", " ", str(s)).strip().lower()
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff ]+", "", s)
    return s


def _similar(a, b):
    """轻量相似度：基于词集合的 Jaccard。"""
    wa, wb = set(_norm_title(a).split()), set(_norm_title(b).split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def cmd_check(a):
    if not os.path.exists(RAW):
        print("缺少 %s，先跑 --fetch" % RAW); return
    recs = json.load(open(RAW, encoding="utf-8"))["records"]
    papers = {p["id"]: p for p in load_papers()}

    hit = {k: v for k, v in recs.items() if v.get("found")}
    via_cr = [v for v in hit.values() if v.get("via") == "crossref"]
    via_dc = [v for v in hit.values() if v.get("via") == "datacite"]
    print("=" * 76)
    print("文献层多源校验 · 覆盖率")
    print("=" * 76)
    print("  Paper 总数        %d" % len(papers))
    print("  有 DOI            %d" % sum(1 for p in papers.values() if p["doi"]))
    print("  校验命中          %d  (Crossref %d · DataCite %d)"
          % (len(hit), len(via_cr), len(via_dc)))
    miss = {k: v for k, v in recs.items() if not v.get("found")}
    if miss:
        from collections import Counter
        print("  未命中 %d，原因: %s" % (len(miss), dict(Counter(v.get("reason") for v in miss.values()))))

    print("\n--- 题名一致性 ---")
    bad = []
    for k, v in hit.items():
        p = papers.get(k) or {}
        s = _similar(p.get("title"), v.get("title"))
        if s < 0.6:
            bad.append((k, p.get("title"), v.get("title"), s))
    print("  一致(≥0.6) %d · 疑似不一致 %d" % (len(hit) - len(bad), len(bad)))
    for k, t1, t2, s in bad[:10]:
        print("    ⚠ %.2f\n        图谱   %s\n        %-8s %s" % (s, str(t1)[:95], (hit[k].get("via") or ""), str(t2)[:95]))

    print("\n--- 年份一致性 ---")
    yb = []
    for k, v in hit.items():
        y1 = (papers.get(k) or {}).get("year")
        y2 = v.get("year")
        try:
            if int(y1) != int(y2):
                yb.append((k, y1, y2))
        except (TypeError, ValueError):
            pass
    print("  一致 %d · 不一致 %d" % (len(hit) - len(yb), len(yb)))
    for k, y1, y2 in yb[:10]:
        print("    ⚠ %s 图谱=%s %s=%s  %s" % (k, y1, hit[k].get("via"), y2,
                                              str((papers.get(k) or {}).get("title"))[:60]))

    print("\n--- 期刊/会议名一致性 ---")
    vb = []
    for k, v in hit.items():
        a1 = _norm_title((papers.get(k) or {}).get("venue"))
        a2 = _norm_title(v.get("container"))
        if a1 and a2 and _similar(a1, a2) < 0.5:
            vb.append((k, (papers.get(k) or {}).get("venue"), v.get("container")))
    print("  可比对 %d · 不一致 %d" % (sum(1 for k, v in hit.items()
                                       if _norm_title((papers.get(k) or {}).get("venue")) and _norm_title(v.get("container"))),
                                      len(vb)))
    for k, a1, a2 in vb[:8]:
        print("    ⚠ 图谱 %-38s vs %s %s" % (str(a1)[:38], hit[k].get("via"), str(a2)[:38]))

    print("\n--- 引用计数口径对比（OpenAlex cited_by vs Crossref is-referenced-by-count）---")
    both = [(k, (papers.get(k) or {}).get("oa_cited_by"), v.get("cited_by"))
            for k, v in hit.items() if v.get("via") == "crossref" and v.get("cited_by") is not None]
    print("  可比对 %d 篇" % len(both))
    print("  ⚠ 口径说明：Crossref 的 `is-referenced-by-count` **只统计 Crossref 体系内的引用**，")
    print("     而 OpenAlex 索引范围更广（含体系外老文献）。故「Crossref 显著小于 OpenAlex」属**预期**，")
    print("     本模块不据此改数，只作独立第二口径保留。真正可疑的是**反向**（Crossref > OpenAlex）。")
    cal, rev = [], []
    for k, oa, cr in both:
        try:
            oa, cr = int(oa), int(cr)
        except (TypeError, ValueError):
            continue
        if cr > 0 and oa > 0:
            if cr / oa > 1.5:
                rev.append((k, oa, cr))
            elif oa / cr > 8:
                cal.append((k, oa, cr))
        elif cr == 0 and oa > 0:
            cal.append((k, oa, cr))
    print("  口径差异（Crossref 低估，预期） %d 篇" % len(cal))
    print("  **反向异常（Crossref > OpenAlex×1.5，可疑）** %d 篇" % len(rev))
    for k, oa, cr in rev[:10]:
        print("    ⛔ %-16s OpenAlex=%-7s Crossref=%-7s  %s"
              % (k.replace("PA:oa:", ""), oa, cr,
                 str((papers.get(k) or {}).get("title"))[:52]))
    for k, oa, cr in cal[:5]:
        print("    · %-16s OpenAlex=%-7s Crossref=%-7s  %s"
              % (k.replace("PA:oa:", ""), oa, cr,
                 str((papers.get(k) or {}).get("title"))[:52]))
    if both:
        import statistics
        ratio = [c / o for _, o, c in both if o and c]
        if ratio:
            print("  Crossref/OpenAlex 比值 中位数 %.2f（<1 属正常，Crossref 口径更窄）"
                  % statistics.median(ratio))


# ---------------------------------------------------------------------------
def cmd_build(a):
    if not os.path.exists(RAW):
        print("缺少 %s，先跑 --fetch" % RAW); return
    recs = json.load(open(RAW, encoding="utf-8"))["records"]
    papers = {p["id"]: p for p in load_papers()}
    now = datetime.now().isoformat(timespec="seconds")
    nodes = []
    n_hit = n_flag = 0
    for pid, v in sorted(recs.items()):
        if pid not in papers:
            continue
        p = papers[pid]
        if not v.get("found"):
            nodes.append({"id": pid, "props": {
                "doi_crossref_checked": True, "doi_check_result": v.get("reason") or "not_found",
                "doi_checked_at": now}})
            continue
        props = {
            "doi_verified": True,
            "doi_verified_via": v.get("via"),
            "doi_verified_at": now,
            "doi_verified_source": SOURCE_TAG,
        }
        flags = []
        # —— 一致性判定（只记 flag，不静默改数）——
        s = _similar(p.get("title"), v.get("title"))
        if s < 0.6:
            flags.append("title_mismatch")
            props["crossref_title"] = v.get("title")
        try:
            if p.get("year") is not None and v.get("year") is not None \
                    and int(p["year"]) != int(v["year"]):
                flags.append("year_mismatch")
        except (TypeError, ValueError):
            pass
        if v.get("year") is not None:
            props["crossref_year"] = v["year"]
        a1 = _norm_title(p.get("venue"))
        a2 = _norm_title(v.get("container"))
        if a1 and a2 and _similar(a1, a2) < 0.5:
            flags.append("venue_variant")
        # —— 引用计数：Crossref 口径更窄，仅作独立第二口径保留 ——
        try:
            oa = int(p["oa_cited_by"]) if p.get("oa_cited_by") is not None else None
            cr = int(v["cited_by"]) if v.get("cited_by") is not None else None
        except (TypeError, ValueError):
            oa = cr = None
        if oa is not None and cr is not None:
            if cr > 0 and oa > 0 and cr / oa > 1.5:
                flags.append("citation_reverse_anomaly")     # 可疑：Crossref 本应是子集
            elif oa > 0 and (cr == 0 or oa / max(cr, 1) > 8):
                flags.append("citation_caliber_gap")        # 预期：口径差异

        if v.get("via") == "crossref":
            if cr is not None:
                props["crossref_cited_by"] = cr
            if v.get("reference_count") is not None:
                props["crossref_reference_count"] = int(v["reference_count"])
            if v.get("type"):
                props["crossref_type"] = v["type"]
            if v.get("publisher"):
                props["publisher"] = v["publisher"]
            if v.get("issn"):
                props["issn"] = v["issn"]
            if v.get("container"):
                props["crossref_container_title"] = v["container"]
            if v.get("author_count"):
                props["crossref_author_count"] = v["author_count"]
            if v.get("license"):
                props["license_url"] = v["license"]
        else:
            if cr is not None:
                props["datacite_cited_by"] = cr
            if v.get("publisher"):
                props["publisher"] = v["publisher"]
            if v.get("resource_type"):
                props["datacite_resource_type"] = v["resource_type"]
        if flags:
            props["doi_check_flags"] = flags
            n_flag += 1
        nodes.append({"id": pid, "props": props})
        n_hit += 1

    delta = {
        "meta": {
            "name": "phase22_literature_validate",
            "source": SOURCE_TAG,
            "description": "文献层多源交叉校验：Crossref / DataCite 独立核验 DOI 元数据（只记 flag，不改 OpenAlex 原值）",
            "built_at": now,
            "generated_by": "11_真实数据/literature_validate.py --build",
        },
        "nodes": nodes, "delete_nodes": [], "edges": [], "delete_edges": [],
    }
    os.makedirs(NEO4J_DIR, exist_ok=True)
    with open(DELTA, "w", encoding="utf-8") as f:
        json.dump(delta, f, ensure_ascii=False, indent=1)
    print("delta -> %s" % DELTA)
    print("  节点更新 %d（核验命中 %d · 带 flag %d）" % (len(nodes), n_hit, n_flag))


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fetch", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--build", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--fresh", action="store_true")
    a = ap.parse_args()
    if a.fetch: cmd_fetch(a)
    elif a.check: cmd_check(a)
    elif a.build: cmd_build(a)


if __name__ == "__main__":
    main()
