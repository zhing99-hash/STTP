#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_recon_phase34_synpreview.py —— 第 24 轮只读侦察 3：ChEBI 同义词对齐预演。
对 Rhea 残差的去重参与物抽样，经代理从 ChEBI OLS4 取 synonyms，
用「label ∪ synonyms」重跑侧别匹配（复用 A12 判据），统计可升级数 + 反例数（wrong）。
只读；小样本（默认 50）用于估算产出率。绝不写图。
"""
from __future__ import annotations
import collections, json, os, re, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
NORM = os.path.join(HERE, "etl", "normalized.json")
PROXY = "http://127.0.0.1:10808"

_RXN_PLUS = re.compile(r" \+ ")
_RXN_PAREN = re.compile(r"\([^)]*\)")


def canon(x):
    s = str(x).lower()
    s = _RXN_PAREN.sub("", s)
    return s.replace(" ", "").replace("-", "").replace("+", "")


def split_eq(eq):
    if not eq or " = " not in eq:
        return None, None
    lhs, rhs = str(eq).split(" = ", 1)
    f = lambda s: [x.strip() for x in s.split(" + ") if x.strip()]
    return f(lhs), f(rhs)


_opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))


def fetch_syn(chebi_id):
    url = ("https://www.ebi.ac.uk/ols4/api/ontologies/chebi/terms?obo_id=" + chebi_id)
    try:
        with _opener.open(url, timeout=20) as r:
            d = json.loads(r.read().decode("utf-8"))
        terms = d.get("_embedded", {}).get("terms", [])
        if not terms:
            return None
        t = terms[0]
        return {"label": t.get("label"), "synonyms": t.get("synonyms") or []}
    except Exception as ex:                                   # noqa: BLE001
        return {"err": str(ex)[:60]}


def main():
    d = json.load(open(NORM, encoding="utf-8"))
    N = {n["id"]: n for n in d["nodes"]}
    t4 = [e for e in d["edges"] if e["type"] in ("reactant_of", "product_of")]
    resid = [e for e in t4
             if (e.get("props") or {}).get("verification_scope") != "equation_species_cross_source"]
    rh = [e for e in resid if ((N.get(e["target"]) or {}).get("props") or {}).get("source") == "Rhea"]

    # 上限：参与物为「具体实体」的边
    def specific(pid):
        p = (N.get(pid) or {}).get("props") or {}
        return not p.get("is_generic") and not p.get("is_polymer")
    ceil = sum(1 for e in rh if specific(e["source"]))
    print("Rhea 残差边 = %d；参与物为具体实体的边（粗上限）= %d" % (len(rh), ceil))

    parts = sorted({e["source"] for e in rh if specific(e["source"])})
    print("具体实体参与物 = %d（去重）" % len(parts))

    # 抽样
    samp = parts[:50]
    t0 = time.time()
    syn_cache = {}
    nerr = 0
    for i, pid in enumerate(samp):
        cid = ((N.get(pid) or {}).get("props") or {}).get("chebi_id")
        r = fetch_syn(cid)
        if r is None or "err" in r:
            nerr += 1
            syn_cache[pid] = []
            continue
        syn_cache[pid] = (r.get("synonyms") or [])
    dt = time.time() - t0
    print("取样 %d，耗时 %.1fs（均 %.2fs/条），失败 %d" % (len(samp), dt, dt / max(len(samp), 1), nerr))

    # 用 label ∪ synonyms 重跑匹配
    st = collections.Counter()
    for e in rh:
        pid = e["source"]
        if pid not in syn_cache:
            continue
        p = (N.get(pid) or {}).get("props") or {}
        rp = (N.get(e["target"]) or {}).get("props") or {}
        lhs, rhs = split_eq(rp.get("equation"))
        if lhs is None:
            st["no_eq"] += 1
            continue
        names = set()
        if p.get("name"):
            names.add(canon(p["name"]))
        for s in syn_cache[pid]:
            if s:
                names.add(canon(s))
        names.discard("")
        L = [canon(x) for x in lhs]
        R = [canon(x) for x in rhs]
        cs, os_ = (L, R) if e["type"] == "reactant_of" else (R, L)
        hit_c = any(n in cs for n in names)
        hit_o = any(n in os_ for n in names)
        if hit_c and hit_o:
            st["both_ambiguity"] += 1
        elif hit_o:
            st["WRONG"] += 1
        elif hit_c:
            st["upgrade"] += 1
        else:
            st["still_nomatch"] += 1
    n = sum(st.values())
    print("\n=== 抽样 %d 条边，用 label∪synonyms 匹配 ===" % n)
    for k, v in st.most_common():
        print("   %-18s %5d  (%.1f%%)" % (k, v, 100.0 * v / max(n, 1)))
    # 样例
    print("\n=== upgrade 样例（前 6）===")
    shown = 0
    for e in rh:
        pid = e["source"]
        if pid not in syn_cache:
            continue
        p = (N.get(pid) or {}).get("props") or {}
        rp = (N.get(e["target"]) or {}).get("props") or {}
        lhs, rhs = split_eq(rp.get("equation"))
        if lhs is None:
            continue
        names = {canon(p["name"])} | {canon(s) for s in syn_cache[pid] if s}
        names.discard("")
        L = [canon(x) for x in lhs]
        R = [canon(x) for x in rhs]
        cs, os_ = (L, R) if e["type"] == "reactant_of" else (R, L)
        if any(n in cs for n in names) and not any(n in os_ for n in names):
            print("   label=%r syn=%r" % (p.get("name"), [s for s in syn_cache[pid]][:3]))
            print("      eq=%r  type=%s" % (rp.get("equation"), e["type"]))
            shown += 1
            if shown >= 6:
                break

    # 外推
    if n:
        rate = st["upgrade"] / n
        print("\n推测：抽样 upgrade 率 %.1f%% → 全量（具体实体累计边）约 %.0f 条可升级"
              % (100.0 * rate, rate * ceil))


if __name__ == "__main__":
    main()
