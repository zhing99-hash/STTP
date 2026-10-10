#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""chebi_synonym_fetch.py —— Phase 34 数据获取：ChEBI 本体「同义词」缓存。

背景（第 24 轮）：T4 残差的根因是**命名差异**（Rhea 方程写 `pentanoate`，ChEBI 主名是 `valerate`）。
`chebi_cache.json` 只有 label/description/xrefs，**无同义词**。本脚本经代理从 **ChEBI OLS4**
（`https://www.ebi.ac.uk/ols4/api/ontologies/chebi/terms?obo_id=<id>`，与 Rhea 是**不同数据库**）
拉取残差参与物的同义词集合，存为 `chebi_synonym_cache.json`。

- 线程化（默认 8 并发）+ 重试；失败项记入 `_failed`。
- 幂等：已缓存的 chebi_id 直接跳过。
- 只读图，只写缓存文件。

用法：python 11_真实数据/chebi_synonym_fetch.py [--workers 8] [--limit N]
"""
from __future__ import annotations
import argparse
import concurrent.futures as cf
import json
import os
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT = os.path.join(HERE, "chebi_synonym_cache.json")
PROXY = os.environ.get("STTP_PROXY", "http://127.0.0.1:10808")
OLS = "https://www.ebi.ac.uk/ols4/api/ontologies/chebi/terms?obo_id="

_opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))


def collect_targets():
    d = json.load(open(NORM, encoding="utf-8"))
    N = {n["id"]: n for n in d["nodes"]}
    t4 = [e for e in d["edges"] if e["type"] in ("reactant_of", "product_of")]
    resid = [e for e in t4
             if (e.get("props") or {}).get("verification_scope") != "equation_species_cross_source"]
    rh = [e for e in resid if ((N.get(e["target"]) or {}).get("props") or {}).get("source") == "Rhea"]
    cids = {}
    for e in rh:
        p = (N.get(e["source"]) or {}).get("props") or {}
        cid = p.get("chebi_id")
        if cid:
            cids[cid] = {"name": p.get("name"), "formula": p.get("formula"),
                         "charge": p.get("charge"), "is_generic": p.get("is_generic"),
                         "is_polymer": p.get("is_polymer")}
    return cids


def fetch_one(item, retries=3):
    cid, meta = item
    url = OLS + urllib.parse.quote(cid)
    last = ""
    for _ in range(retries):
        try:
            with _opener.open(url, timeout=25) as r:
                d = json.loads(r.read().decode("utf-8"))
            terms = d.get("_embedded", {}).get("terms", [])
            if not terms:
                return cid, {"ok": False, "reason": "no_term", **meta}
            t = terms[0]
            return cid, {"ok": True, "label": t.get("label"),
                         "synonyms": t.get("synonyms") or [], **meta}
        except Exception as ex:                               # noqa: BLE001
            last = str(ex)[:80]
    return cid, {"ok": False, "reason": "err:" + last, **meta}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    targets = collect_targets()
    if args.limit:
        targets = dict(list(targets.items())[:args.limit])
    cache = {}
    if os.path.exists(OUT):
        cache = json.load(open(OUT, encoding="utf-8"))
    todo = [(cid, m) for cid, m in targets.items() if cid not in cache or not cache[cid].get("ok")]
    print("目标 chebi_id = %d；已缓存 = %d；待抓 = %d；并发 = %d"
          % (len(targets), len(cache), len(todo), args.workers))
    if not todo:
        print("无待抓项，退出。")
        return
    done = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for cid, res in ex.map(fetch_one, todo):
            cache[cid] = res
            done += 1
            if done % 50 == 0 or done == len(todo):
                nok = sum(1 for v in cache.values() if v.get("ok"))
                print("  进度 %d/%d（缓存内 ok=%d）" % (done, len(todo), nok))
    json.dump(cache, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    nok = sum(1 for v in cache.values() if v.get("ok"))
    nsy = sum(len(v.get("synonyms") or []) for v in cache.values() if v.get("ok"))
    print("写入 %s：条目 %d（ok=%d），同义词总数 %d" % (OUT, len(cache), nok, nsy))


if __name__ == "__main__":
    main()
