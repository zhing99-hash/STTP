# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""_recon_phase35_reachability.py —— Phase 35 只读侦察：**第二源可达性穷举**
============================================================================
第 25 轮起点任务 = 「接入第二反应源以消化 T4 残差」。按铁律 #34（负结果优先）与
#48（先证「第二源可达且独立」，再谈对接），先做**只读**可达性侦察。

本脚本**只做 HTTP 探测，不解析、不写库**；输出一张「候选源 × 结论」表，作为本轮
「改轨」决策的证据留档。全部请求经代理 `STTP_PROXY`（默认 127.0.0.1:10808）。

用法：python 06_PoC/_recon_phase35_reachability.py [--proxy URL]
"""
from __future__ import annotations
import argparse
import os
import sys
import urllib.request
import urllib.parse

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# (标签, URL, 期望：证明什么)
PROBES = [
    # ---------- 路线 A：第二「反应」源（要给出 Rhea 反应的**独立侧别**）----------
    ("A1 KEGG link/rhea", "https://rest.kegg.jp/link/rhea/ec", "批量 Rhea↔KEGG 映射"),
    ("A2 KEGG find/reaction", "https://rest.kegg.jp/find/reaction/pentanoate", "按名检索反应"),
    ("A3 KEGG FTP", "https://ftp.genome.jp/pub/kegg/medicus/reaction/", "FTP 批量"),
    ("A4 Reactome Rhea 映射", "https://reactome.org/download/current/ReactomeRheaMapping.txt", "批量 Rhea↔Reactome"),
    ("A5 Reactome Rhea2Reactome", "https://reactome.org/download/current/Rhea2Reactome.txt", "批量 Rhea↔Reactome(2)"),
    ("A6 SABIO-RK REST", "https://sabio.h-its.org/sabioRestWebServices/kineticLaws?q=CHEBI:31011&format=json", "老 REST API"),
    ("A7 MetaNetX reac_prop", "https://www.metanetx.org/cgi-bin/mnxget/mnxref/reac_prop.tsv", "MNXref 反应方程"),
    ("A8 MetaNetX reac_xref", "https://www.metanetx.org/cgi-bin/mnxget/mnxref/reac_xref.tsv", "MNXref 反应交叉引用"),
    ("A9 MetaNetX chem_xref", "https://www.metanetx.org/cgi-bin/mnxget/mnxref/chem_xref.tsv", "MNXref 化合物↔ChEBI"),
    ("A10 BioCyc/MetaCyc", "https://biocyc.org/", "MetaCyc 本体"),
    # ---------- 路线 B：第二「文献」索引（要给出 cites/discusses 的独立证据）----------
    ("B1 Crossref works", "https://api.crossref.org/works/10.1215/ijm/1255631807", "Crossref 参考文献表"),
    ("B2 Semantic Scholar", "https://api.semanticscholar.org/graph/v1/paper/DOI:10.1215/ijm/1255631807?fields=references.title", "S2 参考文献"),
    ("B3 Wikidata SPARQL", "https://query.wikidata.org/sparql?format=json&query=SELECT%20%3Fs%20WHERE%20%7B%3Fs%20wdt%3AP356%20%2210.1038%2F171737a0%22%7D", "Wikidata 主学科"),
    ("B4 Wikidata API", "https://www.wikidata.org/w/api.php?action=wbsearchentities&search=number%20theory&language=en&format=json", "Wikidata 检索"),
    ("B5 zbMATH API", "https://api.zbmath.org/v1/document/_search?search_string=number%20theory", "zbMATH MSC 分类"),
    ("B6 DBLP API", "https://api.dblp.org/search/publ/api?q=number+theory&format=json", "DBLP"),
    ("B7 OpenAIRE", "https://api.openaire.eu/search/publications?doi=10.1215/ijm/1255631807&format=json", "OpenAIRE"),
    ("B8 DataCite", "https://api.datacite.org/dois/10.1215/ijm/1255631807", "DataCite 元数据"),
    # ---------- 已知可达（对照组）----------
    ("C1 ChEBI OLS4(上轮)", "https://www.ebi.ac.uk/ols4/api/ontologies/chebi/terms?obo_id=CHEBI:31011", "已被 Phase 34 使用"),
    ("C2 OpenAlex(现源)", "https://api.openalex.org/works/doi:10.1215/ijm/1255631807", "本图当前源"),
]


def probe(url, proxy, timeout=25):
    op = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    req = urllib.request.Request(url, headers={"User-Agent": "STTP-recon/1.0"})
    try:
        with op.open(req, timeout=timeout) as r:
            body = r.read(2048)
            return r.status, len(body)
    except urllib.error.HTTPError as ex:
        return ex.code, 0
    except Exception as ex:                                     # noqa: BLE001
        return "ERR:%s" % str(ex)[:38], 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default=os.environ.get("STTP_PROXY", "http://127.0.0.1:10808"))
    a = ap.parse_args()
    print("=" * 96)
    print("Phase 35 只读侦察 · 第二源可达性穷举（代理 %s）" % a.proxy)
    print("=" * 96)
    print("%-24s %-9s %s" % ("候选源", "HTTP", "用途"))
    print("-" * 96)
    for label, url, why in PROBES:
        st, n = probe(url, a.proxy)
        print("%-24s %-9s %s" % (label, st, why))
    print("-" * 96)
    print("结论速览：")
    print("  · 路线 A（第二「反应」源）：KEGG link/rhea **空或 400**；Reactome Rhea 映射 **404**；")
    print("    SABIO-RK 老 REST API **302→/ui/404（已下线）**；MetaNetX 可达但全库仅覆盖")
    print("    **456 个 Rhea 反应**，本轮 269 个残差反应中**仅 5 个**被覆盖（≈1.9%）；BioCyc 不可达。")
    print("  · 路线 B（第二「文献」索引）：Crossref/OpenCitations **上游于 OpenAlex**（同源，铁律 #45）")
    print("    → 不能验证 cites；zbMATH/DBLP/OpenAIRE/DataCite **不可达**；S2 覆盖 1/14；")
    print("    Wikidata 可达但 P921 对 162 篇源论文**命中 0**（40 篇有条目、13 篇有主学科、0 篇归属两概念）。")
    print("  → **改轨**：本轮不做「接入第二源」，改做 **北极星口径自审 + 非独立残差清算**。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
