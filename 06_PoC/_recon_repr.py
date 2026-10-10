# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""侦察：权威图 normalized.json 里，哪些属性的值是「Python 字面量串」而非结构化对象。
只读，不改任何数据。"""
import json, ast, collections, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")

g = json.load(open(NORM, encoding="utf-8"))
nodes = g.get("nodes") or []
edges = g.get("edges") or []

def looks_literal(v):
    """值是否为字符串形态的 dict/list/tuple/set。"""
    if not isinstance(v, str):
        return False
    s = v.strip()
    if not s or s[0] not in "[{(":
        return False
    try:
        got = ast.literal_eval(s)
    except Exception:
        return False
    return isinstance(got, (dict, list, tuple, set)) and len(s) > 2

def scan(items, label):
    by_key = collections.Counter()
    samples = {}
    json_ok = collections.Counter()   # 字符串但 json.loads 能过（合法 JSON 串）
    for it in items:
        props = it.get("props") or it.get("properties") or {}
        if not isinstance(props, dict):
            continue
        for k, v in props.items():
            if looks_literal(v):
                by_key[k] += 1
                if k not in samples:
                    samples[k] = str(v)[:120]
                try:
                    json.loads(v)
                    json_ok[k] += 1
                except Exception:
                    pass
    print("---- %s ----" % label)
    if not by_key:
        print("  （无）")
    for k, c in by_key.most_common():
        kind = "合法JSON串" if json_ok.get(k, 0) == c else ("混合" if json_ok.get(k) else "Python-repr串(非法JSON)")
        print("  %-28s %6d 条  [%s]" % (k, c, kind))
        print("       e.g. %s" % samples[k])
    return by_key

nk = scan(nodes, "节点属性")
ek = scan(edges, "边属性")

print()
print("节点总数 %d / 边总数 %d" % (len(nodes), len(edges)))
print("涉及 key 并集：节点 %s / 边 %s" % (sorted(nk), sorted(ek)))

# 额外：结构层面有多少 props 值类型不是 (str/int/float/bool/None)
print()
print("== 再看：值类型分布（全局属性值）==")
tc = collections.Counter()
for it in nodes + edges:
    props = it.get("props") or {}
    if isinstance(props, dict):
        for v in props.values():
            tc[type(v).__name__] += 1
print("  ", dict(tc.most_common()))
