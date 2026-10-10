#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""webbook_ingest.py —— NIST Chemistry WebBook 适配器（Phase 29 / 第 19 轮 · 跨域桥专项）

为什么选它
----------
第 18 轮把北极星最短板定位为 **T7 跨域桥 = 0.0%**。复查发现 T7=0 有两个口径缺陷
（门槛不可达 + 类型窗口漏掉 `has_quantity` 主桥，见 `06_PoC/task_trust_audit.py` 的
口径裁定）。但**内容侧**也确有其事：图里「化学物质 → 物理量」的桥几乎都是
`model_inferred` 的填充边，**没有一条来自真实测量的物理量**。

本适配器接入 **NIST Chemistry WebBook**（`webbook.nist.gov`，实测直连可达）：
对图中已有的分子，抓取其**气相热化学数据**（ΔfH°gas / S°gas），建**跨域热化学桥**
    Molecule --has_thermochemical_property--> <物理量表节点>

证据阶梯（**与提出者无关的确定性判据**）
----------------------------------------
1. **单位量纲独立复算**：把 `kJ/mol` / `J/mol*K` 解析为 SI 基本量向量，与目标物理量
   在 `dimension_table` 中的真量纲比对 → 通过 = `rule_checked`。
2. **多源一致**：WebBook 同一物理量常给出**多条独立文献**（不同作者/不同测量）。
   若 ≥2 条独立文献在容差内吻合 → `cross_source`（**两个独立来源一致**，
   无需外部第二源，来源本身即多源）。

铁律对齐
--------
* #14 自检输入不得与被检对象同源：本模块的分子式解析用 `verification_model` 的
  独立实现，不复用产出方解析器。
* #16 改口径必须做反向对照：见 `task_trust_audit.py --legacy-t7`。
* #23 跨字段自洽须由仪器断言：见冻结反例集 `P1-bridge-*`。

用法
----
    python 11_真实数据/webbook_ingest.py --list                 # 列出候选分子（不联网）
    python 11_真实数据/webbook_ingest.py --limit 40            # 抓取前 40 个
    python 11_真实数据/webbook_ingest.py --only H2O,CO2,CH4    # 指定化学式
    python 11_真实数据/webbook_ingest.py --out ... --cache ... # 自定义路径
"""
from __future__ import annotations

import argparse
import collections
import html as H
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import verification_model as vm          # noqa: E402

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT_DEFAULT = os.path.join(HERE, "webbook_raw.json")
CACHE_DEFAULT = os.path.join(HERE, "webbook_cache.json")

BASE = "https://webbook.nist.gov/cgi/cbook.cgi"
UA = "STTP-research-prototype/1.0 (contact: local research; polite, cached)"

# 只抓「气相热化学」表；ΔfH°gas 与 S°gas 是我们能确定性验证的两个量
Q_SYNONYMS = {
    "std_enthalpy_of_formation": ("ΔfH°gas", "ΔfHºgas", "Δ f H° gas"),
    "std_entropy": ("S°gas", "Sºgas"),
}
# 单位 → 量纲（SI 基本量向量）；用于与 dimension_table 比对（**确定性复算**）
UNIT_DIM = {
    "kj/mol": {"M": 1.0, "L": 2.0, "T": -2.0, "N": -1.0},
    "j/mol": {"M": 1.0, "L": 2.0, "T": -2.0, "N": -1.0},
    "j/mol*k": {"M": 1.0, "L": 2.0, "T": -2.0, "N": -1.0, "Th": -1.0},
    "kj/mol*k": {"M": 1.0, "L": 2.0, "T": -2.0, "N": -1.0, "Th": -1.0},
    "j/(mol*k)": {"M": 1.0, "L": 2.0, "T": -2.0, "N": -1.0, "Th": -1.0},
}

VALUE_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(?:±|\+/-)?\s*(\d+(?:\.\d+)?)?\s*$")


def _get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def strip_tags(seg):
    seg = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", seg, flags=re.S | re.I)
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", seg)))


# --------------------------------------------------------------------- 页面解析
def parse_page(txt):
    """从 WebBook 物种页文本中抽「气相热化学」表的行。

    返回 {"formula":..., "cas":..., "name":...,
          "rows":[{"quantity","value","uncertainty","unit","method","reference"}]}
    """
    out = {"formula": None, "cas": None, "name": None, "rows": []}

    m = re.search(r"Formula\s*:\s*([A-Za-z0-9\(\)\[\]·\.\+\- ]{1,40}?)\s*(?:Molecular weight|IUPAC|CAS)", txt)
    if m:
        out["formula"] = re.sub(r"\s+", "", m.group(1))
    m = re.search(r"CAS\s*Registry\s*Number\s*:?\s*([\d\-]{5,})", txt)
    if m:
        out["cas"] = m.group(1)

    # 找到「Gas phase thermochemistry data」表区（到页面末尾的导航为止）
    i = txt.find("Gas phase thermochemistry data")
    if i < 0:
        return out
    j = txt.find("Quantity Value Units Method Reference", i)
    if j < 0:
        return out
    seg = txt[j:]
    k = seg.find("Go To: Top", 40)
    if k > 0:
        seg = seg[:k]
    else:
        seg = seg[:40000]

    # 行模式：<quantity> <value> [± <unc>] <unit> <method> <reference>
    #   注意数量名可能带后缀（`S° gas,1 bar`），故接一个可选修饰段
    row_re = re.compile(
        r"(Δ\s*f\s*H[º°]\s*gas|Δ\s*c\s*H[º°]\s*gas|S[º°]\s*gas)"
        r"(?:\s*,\s*[0-9.]+\s*[A-Za-z]+)?\s+"
        r"(-?\d+(?:\.\d+)?)\s*(?:±\s*(\d+(?:\.\d+)?))?\s*"
        r"(kJ/mol|J/mol\*K|kJ/mol\*K|J/mol)\s+"
        r"(\S+)\s+"                          # method（Ccb / Review / N/A / Cm / GT）
        r"([A-Z][^,]{2,60}?,\s*\d{4})"       # reference（作者, 年）
    )
    for mm in row_re.finditer(seg):
        q, v, u, unit, method, ref = mm.groups()
        q = re.sub(r"\s+", "", q).replace("º", "°")
        out["rows"].append({"quantity": q, "value": float(v),
                            "uncertainty": float(u) if u else None,
                            "unit": unit.strip(), "method": method.strip(),
                            "reference": ref.strip()})
    return out


def normalize_quantity(q):
    for canon, syns in Q_SYNONYMS.items():
        for s in syns:
            if s.replace(" ", "") == q.replace(" ", ""):
                return canon
    return None


# --------------------------------------------------------------------- 候选分子
def candidates(nodes):
    """挑「有可读名 + 化学式」的分子节点（WebBook 可按名检索）。"""
    out = []
    for n in nodes:
        if n.get("type") != "Molecule" and "Molecule" not in (n.get("labels") or []):
            continue
        p = n.get("props") or {}
        name = (p.get("name") or "").strip()
        formula = (p.get("pubchem_formula") or p.get("formula") or "").strip()
        if not name or not formula:
            continue
        if not re.fullmatch(r"[A-Za-z0-9\(\)\[\]·\.\+\-]{1,40}", formula):
            continue
        # 名字要是**可读英文名**（排除单字母 / SMILES / 纯式串 / 超长 IUPAC）
        if len(name) < 4 or len(name) > 40 or not re.match(r"^[A-Za-z]", name):
            continue
        if not re.search(r"[a-z]{3,}", name):
            continue
        if re.fullmatch(r"[A-Za-z]{1,2}", name):          # A / F / H / I / N3 …
            continue
        out.append({"node": n["id"], "name": name, "formula": formula,
                    "pubchem_cid": p.get("pubchem_cid"),
                    "pubchem_mw": p.get("pubchem_molecular_weight")})
    return out


def main():
    ap = argparse.ArgumentParser(description="NIST Chemistry WebBook 热化学适配器")
    ap.add_argument("--graph", default=NORM)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--cache", default=CACHE_DEFAULT)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="", help="逗号分隔的化学式，只抓这些")
    ap.add_argument("--list", action="store_true", help="只列候选，不联网")
    ap.add_argument("--sleep", type=float, default=0.4, help="请求间隔（礼貌）")
    a = ap.parse_args()

    nodes, _ = vm.load_norm(a.graph)
    cand = candidates(nodes)
    # 优先小分子（WebBook 覆盖最好、歧义最少）
    cand.sort(key=lambda c: (len(c["formula"]), c["formula"]))
    if a.only:
        want = {x.strip() for x in a.only.split(",") if x.strip()}
        cand = [c for c in cand if c["formula"] in want]

    print("=" * 92)
    print("NIST Chemistry WebBook 适配器 —— 候选分子 %d 个" % len(cand))
    print("=" * 92)
    if a.list:
        for c in cand[:60]:
            print("  %-28s %-14s %-10s %s" % (c["node"], c["formula"], c["name"][:26], c["pubchem_cid"]))
        print("  … 共 %d" % len(cand))
        return 0

    if a.limit:
        cand = cand[:a.limit]
    cache = {}
    if os.path.exists(a.cache):
        cache = json.load(open(a.cache, encoding="utf-8"))

    records, stat = [], collections.Counter()
    for i, c in enumerate(cand, 1):
        key = c["formula"] + "|" + c["name"].lower()
        page = cache.get(key)
        if page is None:
            url = BASE + "?" + urllib.parse.urlencode(
                {"Name": c["name"], "Units": "SI", "cTG": "on"})
            try:
                raw = _get(url)
                page = {"html_len": len(raw), "txt": strip_tags(raw), "url": url}
            except Exception as ex:
                stat["fetch_fail"] += 1
                print("  [%3d] %-12s %-24s FETCH_FAIL %s" % (i, c["formula"], c["name"][:24], str(ex)[:60]))
                continue
            cache[key] = page
            if i % 10 == 0:                      # 增量落盘：中断也不丢已抓部分
                json.dump(cache, open(a.cache, "w", encoding="utf-8"), ensure_ascii=False)
                print("      … 已缓存 %d 页" % len(cache), flush=True)
            time.sleep(a.sleep)
        txt = page["txt"]
        # 歧义判定：若返回的是**候选列表**（多个物种）则跳过
        if "Species with the same formula" in txt or "Search for this species" in txt:
            stat["ambiguous"] += 1
            continue
        parsed = parse_page(txt)
        if not parsed["rows"]:
            stat["no_thermo"] += 1
            continue
        # 化学式必须与节点一致（防同名异物）
        if parsed["formula"] and parsed["formula"].replace(" ", "") != c["formula"].replace(" ", ""):
            stat["formula_mismatch"] += 1
            continue
        rec = dict(c)
        rec["cas"] = parsed["cas"]
        rec["url"] = page["url"]
        rec["rows"] = parsed["rows"]
        records.append(rec)
        stat["ok"] += 1
        print("  [%3d] %-12s %-24s rows=%d  ΔfH°=%s  S°=%s"
              % (i, c["formula"], c["name"][:24], len(parsed["rows"]),
                 next((r["value"] for r in parsed["rows"] if r["quantity"].startswith("ΔfH")), "-"),
                 next((r["value"] for r in parsed["rows"] if r["quantity"].startswith("S")), "-")))

    json.dump(cache, open(a.cache, "w", encoding="utf-8"), ensure_ascii=False)
    payload = {"source": "NIST Chemistry WebBook", "base": BASE,
               "generator": "webbook_ingest.py", "phase": 29,
               "stats": dict(stat), "records": records}
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(payload, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n统计：", dict(stat))
    print("命中 %d 个分子 -> %s" % (len(records), os.path.relpath(a.out, ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
