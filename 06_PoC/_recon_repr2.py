# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""侦察 2：更宽的「字符串化」检测 —— 含 dict/list 片段但首字符不是 [{ 的，
以及源头已知的 str(list) 写法（如 pb_domains）。只读。"""
import json, ast, collections, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
g = json.load(open(NORM, encoding="utf-8"))
nodes = g.get("nodes") or []
edges = g.get("edges") or []

# 1) 首字符不是 [{，但内部明显像 python 容器
pat_dict = re.compile(r"\{\s*'[^']+'\s*:")
pat_list = re.compile(r"\[\s*'[^']*'\s*(,|\])")
susp = collections.Counter(); samp = {}
for it in nodes + edges:
    for k, v in (it.get("props") or {}).items():
        if isinstance(v, str):
            if pat_dict.search(v) or pat_list.search(v):
                susp[k] += 1
                samp.setdefault(k, v[:150])
print("== 疑似字符串化（内部含 python 容器片段）==")
for k, c in susp.most_common(20):
    print("  %-28s %6d  e.g. %s" % (k, c, samp[k]))
if not susp:
    print("  （无）")

# 2) 指定几个「已知有源头 str() 写法」的 key 直接点名
for key in ("pb_domains", "domains", "dim_exponents", "composition", "authors",
            "citations", "tags", "aliases", "synonyms", "origins"):
    cnt = collections.Counter()
    sample = None
    for it in nodes + edges:
        p = it.get("props") or {}
        if key in p:
            cnt[type(p[key]).__name__] += 1
            if sample is None:
                sample = p[key]
    if cnt:
        print("  %-18s 类型分布 %-40s e.g. %s" % (key, dict(cnt), str(sample)[:80]))
