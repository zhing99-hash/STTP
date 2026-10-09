# -*- coding: utf-8 -*-
"""
Phase 26 — Rhea + ChEBI 化学·反应层真实数据接入（**控物理失衡**）
=================================================================
背景：2026-10-09 连通性审计发现北极星结构性风险 —— 物理占比 71.5%、跨域边 90% 依赖
单一 ``has_quantity``、跨学科实质只在「化学↔物理」。老板裁定：**优先补化学/数学侧的源**。

为什么选 Rhea + ChEBI
---------------------
- Rhea（EBI 反应库）给出**化学反应**及其**命名参与物**（ChEBI 标识），方程以 ``=`` 明确
  分隔反应物 / 产物 —— **方向语义天然确定**，可复用既有 ``reactant_of`` / ``product_of``，
  **零 schema 改动**。
- 每条反应带 **EC 号**（酶分类），是不同于 ``has_quantity`` 的**新化学维度**（作为属性落地，
  不新建边类型，避免为凑指标而造 schema）。
- ChEBI 参与物带 **分子式 / 摩尔质量 / 电荷 / SMILES / 交叉引用**：
  * 分子式 → ``composed_of`` 连到 Phase 8 真实元素层（``EK:el:*``）；
  * PubChem 交叉引用 → ``same_as`` 桥到既有 ``PC:mol:<CID>``（真实跨源对齐）。

设计原则（对齐项目铁律）
-----------------------
1. **零 schema 改动**：复用 ``Reaction`` / ``Molecule`` / ``reactant_of`` / ``product_of`` /
   ``composed_of`` / ``same_as``；EC 号只作节点属性。
2. **不制造孤立节点**：只有当反应/化合物**确实产生边**（参与物、组成、对齐）时才建节点。
3. **学科标注显式化**：新节点一律写 ``domain=chem.*``（``subject_of`` 里显式 domain 优先），
   并在 ``graph_export._NS_SUBJECT`` 补 ``RH``/``CH`` 兜底键，防止像上一轮那样被误标。
4. **元素只认真值源**：``composed_of`` 的元素符号须通过 ``element_reference.is_element`` 校验。
5. **属性可消毒**：所有 props 为 primitive 或同质 primitive 数组（Neo4j 边界要求）。

可达性：``probe_sources.py`` 实测 www.rhea-db.org / www.ebi.ac.uk 双通道 ✅（KEGG/zbMATH 被拦）。

用法
----
    python 11_真实数据/rhea_ingest.py --fetch [--limit 1000] [--workers 8]
    python 11_真实数据/rhea_ingest.py --check
    python 11_真实数据/rhea_ingest.py --build
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import element_reference as er  # noqa: E402  元素符号唯一真值源


def is_element(sym: str) -> bool:
    """元素符号校验（走 element_reference 唯一真值源，本地不另立白名单）。"""
    try:
        return er.by_symbol(sym) is not None
    except Exception:      # noqa: BLE001
        return False

RAW = os.path.join(HERE, "rhea_raw.json")
CACHE = os.path.join(HERE, "chebi_cache.json")
NEO4J_DIR = os.path.join(ROOT, "06_PoC", "etl", "neo4j")
DELTA = os.path.join(NEO4J_DIR, "phase26_rhea_delta.json")
NORMALIZED = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")

SOURCE_TAG = "rhea_chebi"

RHEA_API = ("https://www.rhea-db.org/rhea?query=%s"
            "&columns=rhea-id,equation,chebi-id,ec&format=tsv&limit=%d")
OLS_TERM = "https://www.ebi.ac.uk/ols4/api/ontologies/chebi/terms?obo_id=CHEBI:%s"

# 核心代谢物锚点（**跨源对齐的关键**）：Rhea 广谱切片按 id 升序，前若干条多为冷门反应，
# 不会命中既有分子层。额外按这些化合物检索，确保水/ATP/NAD+/乙醇/葡萄糖等**必然入图**，
# 从而让 Rhea 化学子图通过 ChEBI 节点与既有 PC:mol:*（及其 has_quantity→物理量）连通。
CORE_CHEBI = [
    "CHEBI:15377",   # water
    "CHEBI:15379",   # dioxygen
    "CHEBI:16526",   # carbon dioxide
    "CHEBI:15422",   # ATP
    "CHEBI:16761",   # ADP
    "CHEBI:456216",  # ADP(3-)
    "CHEBI:57540",   # NAD(+)
    "CHEBI:57945",   # NADH
    "CHEBI:58349",   # NADP(+)
    "CHEBI:57783",   # NADPH
    "CHEBI:16236",   # ethanol
    "CHEBI:15343",   # acetaldehyde
    "CHEBI:30089",   # acetate
    "CHEBI:15361",   # pyruvate
    "CHEBI:17234",   # glucose
    "CHEBI:15428",   # glycine
    "CHEBI:29985",   # L-glutamate
    "CHEBI:16240",   # hydrogen peroxide
    "CHEBI:16134",   # ammonia
    "CHEBI:16199",   # urea
]

# ChEBI 标签 → 既有节点（**人工策划**，宁缺毋滥；只覆盖 14 个已真实化分子）
BRIDGE_BY_NAME = {
    "water": "PC:mol:962", "carbon dioxide": "PC:mol:280", "methane": "PC:mol:297",
    "dioxygen": "PC:mol:977", "ethane": "PC:mol:6324", "ethene": "PC:mol:6325",
    "benzene": "PC:mol:241", "ethanol": "PC:mol:702", "sodium chloride": "PC:mol:5234",
    "hydrogen chloride": "PC:mol:313", "dichlorine": "PC:mol:24526",
    "ATP": "PC:mol:5957", "glycine": "PC:mol:750", "D-glucose": "PC:mol:5793",
}

UA = {"User-Agent": "STTP-rhea-ingest/1.0 (research; contact: local)"}

ELEM_RE = re.compile(r"([A-Z][a-z]?)(\d*)")
# ChEBI 中大量「通式化合物」（R 基团 / 聚合物 / 泛指）——不作为具体分子建节点
GENERIC_MARK = re.compile(r"[R*]|\bn\b|polymer", re.I)


# ────────────────────────── 网络 ──────────────────────────
def _get(url: str, timeout: int = 40) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    last = None
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:      # noqa: BLE001
            last = e
            time.sleep(1.2 * (i + 1))
    raise last  # type: ignore[misc]


# ────────────────────────── 解析 ──────────────────────────
def split_equation(equation: str):
    """'a primary alcohol + NAD(+) = an aldehyde + NADH + H(+)'
    → (['a primary alcohol', 'NAD(+)'], ['an aldehyde', 'NADH', 'H(+)'])

    ⚠ 只按 ``' = '``（带空格）切分：电荷写法 ``NAD(+)`` / ``H(+)`` 里的 ``+`` 两侧无空格，
    不会被误当分隔符。
    """
    if " = " not in equation:
        return None, None
    lhs, rhs = equation.split(" = ", 1)

    def names(side):
        return [s.strip() for s in side.split(" + ") if s.strip()]

    return names(lhs), names(rhs)


def fetch_reactions(query: str, limit: int):
    q = query.replace(":", "%3A")
    raw = _get(RHEA_API % (q, limit)).decode("utf-8", "replace")
    lines = [l for l in raw.splitlines() if l.strip()]
    if not lines:
        return []
    header = lines[0].split("\t")
    out = []
    for ln in lines[1:]:
        cols = ln.split("\t")
        cols += [""] * (len(header) - len(cols))
        rec = dict(zip(header, cols))
        rid = (rec.get("Reaction identifier") or "").strip()
        eq = (rec.get("Equation") or "").strip()
        chebis = [c.strip() for c in (rec.get("ChEBI identifier") or "").split(";") if c.strip()]
        ecs = [e.strip() for e in (rec.get("EC number") or "").split(";") if e.strip()]
        if not rid or not chebis:
            continue
        lhs, rhs = split_equation(eq)
        if lhs:
            # ⚠ Rhea 的 ``chebi-id`` 列是**按首次出现顺序去重**的：同一 ChEBI 实体跨区室
            # （``sulfate(out)``/``sulfate(in)``）或同一聚合物不同聚合度（``tRNA(n)``/``tRNA(n+1)``）
            # 只列一次。因此切分点应取「**去重后的左侧物种数**」，而非原始左侧项数。
            # （实测 939 条上两种口径完全一致，此处取更严谨的一种。）
            n_left_raw = len(lhs)
            n_left = len({re.sub(r"\((?:in|out)\)$", "", x) for x in lhs})
        else:
            n_left_raw = n_left = None
        out.append({"rhea_id": rid, "equation": eq, "chebi": chebis,
                    "ec": ecs, "n_left": n_left, "n_left_raw": n_left_raw,
                    "names_left": lhs, "names_right": rhs})
    return out


def fetch_chebi(num: str) -> dict:
    """单个 ChEBI 术语 → {label, formula, mass, charge, smiles, inchi_key, xrefs}"""
    d = json.loads(_get(OLS_TERM % num, timeout=35).decode("utf-8", "replace"))
    terms = (d.get("_embedded") or {}).get("terms") or []
    if not terms:
        return {"ok": False}
    t = terms[0]
    a = t.get("annotation") or {}

    def first(k):
        v = a.get(k)
        return v[0] if isinstance(v, list) and v else v

    xrefs = []
    for x in (t.get("obo_xref") or []):
        db, xid = x.get("database"), x.get("id")
        if db and xid:
            xrefs.append("%s:%s" % (db, xid))
    return {
        "ok": True,
        "label": t.get("label"),
        "is_obsolete": bool(t.get("is_obsolete")),
        "description": (t.get("description") or [None])[0],
        "formula": first("generalized_empirical_formula"),
        "mass": first("mass"),
        "monoisotopic_mass": first("monoisotopic_mass"),
        "charge": first("charge"),
        "smiles": first("smiles_string"),
        "inchi_key": first("inchi_key_string"),
        "xrefs": xrefs,
    }


def cmd_fetch(a):
    t0 = time.time()
    print("=" * 78)
    print("Rhea 拉取：广谱 query=* limit=%d ＋ 核心代谢物锚点 %d 个" % (a.limit, len(CORE_CHEBI)))
    print("=" * 78)
    pool = {}
    for r in fetch_reactions("*", a.limit):
        pool[r["rhea_id"]] = r
    n_broad = len(pool)
    print("[Rhea] 广谱切片 %d 条" % n_broad)
    for q in CORE_CHEBI:
        try:
            for r in fetch_reactions(q, a.core_limit):
                pool.setdefault(r["rhea_id"], r)
        except Exception as e:      # noqa: BLE001
            print("[WARN] %s 拉取失败：%s" % (q, str(e)[:90]))
    reacs = list(pool.values())
    print("[Rhea] 核心代谢物补充后 %d 条（新增 %d）" % (len(reacs), len(reacs) - n_broad))

    # 方程切分自检：左/右物种数应与方程 ``=`` 两侧一致（若不一致仅记录，不中断）
    mismatch = [r for r in reacs if r["n_left"] is None]
    if mismatch:
        print("[WARN] %d 条反应方程无法切分（无 ' = '）" % len(mismatch))

    cache = {}
    if os.path.exists(CACHE):
        try:
            cache = json.load(open(CACHE, encoding="utf-8"))
        except Exception:       # noqa: BLE001
            cache = {}
    need = sorted({c for r in reacs for c in r["chebi"]} - set(cache))
    print("[ChEBI] 唯一参与物 %d，缓存命中 %d，待拉 %d"
          % (len({c for r in reacs for c in r["chebi"]}), len(cache), len(need)))

    ok = fail = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        fut = {ex.submit(fetch_chebi, c.split(":")[-1]): c for c in need}
        for i, f in enumerate(as_completed(fut), 1):
            cid = fut[f]
            try:
                cache[cid] = f.result()
                ok += 1
            except Exception as e:      # noqa: BLE001
                cache[cid] = {"ok": False, "error": str(e)[:120]}
                fail += 1
            if i % 200 == 0:
                print("   ... %d/%d（ok %d / fail %d）%.0fs"
                      % (i, len(need), ok, fail, time.time() - t0))

    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    raw = {"meta": {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "limit": a.limit, "core_limit": a.core_limit,
                    "core_chebi": CORE_CHEBI,
                    "reactions": len(reacs),
                    "compounds": len(cache), "source": SOURCE_TAG},
           "reactions": reacs, "compounds": cache}
    json.dump(raw, open(RAW, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n[OUT] %s" % RAW)
    print("      反应 %d · 化合物 %d（新拉 %d ok / %d fail）· 用时 %.0fs"
          % (len(reacs), len(cache), ok, fail, time.time() - t0))


def cmd_check(a):
    raw = json.load(open(RAW, encoding="utf-8"))
    reacs, cps = raw["reactions"], raw["compounds"]
    print("=" * 78)
    print("自检（%d 反应 / %d 化合物）" % (len(reacs), len(cps)))
    print("=" * 78)
    # 1) 方程切分一致性
    bad = 0
    for r in reacs:
        if r["n_left"] is not None and r["names_left"] is not None:
            if len(r["names_left"]) + len(r["names_right"]) != len(r["chebi"]):
                bad += 1
    print("  方程物种数 vs ChEBI 数不一致：%d / %d" % (bad, len(reacs)))
    # 2) 化合物字段覆盖
    have_f = sum(1 for c in cps.values() if c.get("ok") and c.get("formula"))
    have_x = sum(1 for c in cps.values() if c.get("ok") and c.get("xrefs"))
    okc = sum(1 for c in cps.values() if c.get("ok"))
    print("  化合物 ok %d / 有分子式 %d / 有交叉引用 %d" % (okc, have_f, have_x))
    # 3) 名称桥（人工策划）能否命中
    ids = {n["id"] for n in json.load(open(NORMALIZED, encoding="utf-8"))["nodes"]}
    hits = []
    for cid, c in cps.items():
        if not (c.get("ok") and c.get("label")):
            continue
        tgt = BRIDGE_BY_NAME.get(c["label"])
        if tgt and tgt in ids:
            hits.append("%s(%s)->%s" % (c["label"], cid, tgt))
    print("  名称桥命中既有分子：%d %s" % (len(hits), hits[:6]))
    # 4) 元素符号合法性
    bad_el = set()
    for c in cps.values():
        if not (c.get("ok") and c.get("formula")):
            continue
        for sym, _ in ELEM_RE.findall(str(c["formula"])):
            if not is_element(sym):
                bad_el.add(sym)
    print("  非法元素符号：%s" % (sorted(bad_el) or "无"))


# ────────────────────────── 构建 delta ──────────────────────────
def parse_formula(formula: str):
    s = str(formula or "").strip()
    s = re.sub(r"\^\d*[+\-\u2212]+\d*$", "", s)
    s = re.sub(r"[+\-\u2212]\d*$", "", s)
    comp = {}
    for sym, num in ELEM_RE.findall(s):
        n = int(num) if num else 1
        comp[sym] = comp.get(sym, 0) + n
    return comp


def cmd_build(a):
    raw = json.load(open(RAW, encoding="utf-8"))
    reacs, cps = raw["reactions"], raw["compounds"]
    graph = json.load(open(NORMALIZED, encoding="utf-8"))
    existing = {n["id"] for n in graph["nodes"]}

    nodes, edges = [], []
    used_cpd = set()

    def add_edge(src, tgt, etype, kind, **props):
        props.setdefault("confidence", 0.95)
        props.setdefault("explicit_or_inferred", "explicit")
        props.setdefault("source", SOURCE_TAG)
        props.setdefault("kind", kind)
        edges.append({"id": "%s|%s|%s" % (etype, src, tgt),
                      "source": src, "target": tgt, "type": etype,
                      "kind": kind, "props": props})

    n_rxn = n_generic = n_partial = 0
    for r in reacs:
        rid = "RH:rxn:" + r["rhea_id"].split(":")[-1]
        chebis = r["chebi"]
        n_left = r["n_left"]
        if n_left is None:
            continue
        n_left = min(n_left, len(chebis))       # 防御：方程与 chebi 数不一致时截断
        reactants, products = chebis[:n_left], chebis[n_left:]
        if not reactants and not products:
            continue
        nodes.append({
            "id": rid, "labels": ["Entity", "Reaction"],
            "props": {
                "domain": "chem.reaction", "ntype": "reaction",
                "name": r["equation"], "equation": r["equation"],
                "rhea_id": r["rhea_id"],
                "ec_numbers": r["ec"] or None,
                "n_reactants": len(reactants), "n_products": len(products),
                "source": "Rhea",
            },
        })
        n_rxn += 1
        for c in reactants:
            add_edge("CH:cpd:" + c.split(":")[-1], rid, "reactant_of", "rhea_reactant")
            used_cpd.add(c)
        for c in products:
            add_edge("CH:cpd:" + c.split(":")[-1], rid, "product_of", "rhea_product")
            used_cpd.add(c)

    # 化合物节点（只为「确实参与反应」的建，避免孤立）
    n_cpd = n_comp_edges = 0
    for cid in sorted(used_cpd):
        c = cps.get(cid) or {}
        if not c.get("ok"):
            continue
        num = cid.split(":")[-1]
        nid = "CH:cpd:" + num
        formula = c.get("formula") or ""
        label = str(c.get("label") or "")
        # ⚠ 两类"非完整分子式"要区别对待：
        #   is_polymer   —— ``(C11H20O12P)n``：计数是**重复单元**，整分子计数未知 → 不可建 composed_of
        #   placeholder  —— 含 R/X/A/* 占位符（``C3H6O6PR2``）：R 以外的 C/H/O/P 计数**精确**
        #                   → 可建"部分结构"composed_of（标 partial_structure，降置信度）。
        # 早期版本把两类一律跳过，导致这类反应的化合物**无一锚定元素层 → 形成孤岛**
        # （2026-10-09 实测 2 个分量 / 8 节点，已修）。
        is_polymer = bool(re.search(r"\)\s*n", formula, re.I)) or "polymer" in formula.lower()
        has_placeholder = bool(re.search(r"[RXA*]", formula))
        generic = has_placeholder or bool(re.match(r"^(a|an|any|some)\b", label, re.I))
        props = {
            "domain": "chem.compound", "ntype": "molecule",
            "name": label or cid, "chebi_id": cid,
            "formula": formula or None, "charge": c.get("charge"),
            "is_generic": generic, "is_polymer": is_polymer, "source": "ChEBI",
        }
        if c.get("mass") is not None:
            try:
                props["molecular_weight"] = float(c["mass"])
            except (TypeError, ValueError):
                pass
        if c.get("smiles"):
            props["canonical_smiles"] = c["smiles"]
        if c.get("inchi_key"):
            props["inchi_key"] = c["inchi_key"]
        if c.get("xrefs"):
            props["xrefs"] = sorted(c["xrefs"])[:20]
        nodes.append({"id": nid, "labels": ["Entity", "Molecule"], "props": props})
        n_cpd += 1

        # 分子式 → 元素层（聚合物跳过；含占位符者标 partial_structure 仍建）
        if formula and not is_polymer:
            for sym, cnt in parse_formula(formula).items():
                if not is_element(sym):
                    continue
                ek = "EK:el:" + sym
                edges.append({
                    "id": "composed_of|%s|%s" % (nid, ek), "source": nid, "target": ek,
                    "type": "composed_of", "kind": "chebi_composition",
                    "props": {"count": cnt,
                              "confidence": 0.98 if not generic else 0.7,
                              "explicit_or_inferred": "explicit",
                              "partial_structure": generic,
                              "source": "ChEBI", "from_formula": formula,
                              "kind": "chebi_composition"},
                })
                n_comp_edges += 1
                if generic:
                    n_partial += 1

        # 人工策划名称桥 → 既有真实分子（ChEBI 的 obo_xref **不含** PubChem CID，
        # 实测确认，故不走 xref；改用标签精确匹配的策划映射，精度优先）
        tgt = BRIDGE_BY_NAME.get(props["name"])
        if tgt and tgt in existing:
            add_edge(nid, tgt, "same_as", "chebi_name_bridge",
                     alignment="manual_curation", bridge_label=props["name"])
        if generic:
            n_generic += 1

    # ── 防孤岛守卫（2026-10-09）────────────────────────────────────────────
    # 只保留「**至少一个参与物能锚定主图**」的反应。锚定 = 有 ``composed_of`` 连到元素层
    # （``EK:el:*`` 已在图中），或有人工名称桥。否则该反应 + 其化合物会成为**新的连通
    # 分量**——审计首次应用本 delta 时实测到 2 个分量 / 8 节点，即由此产生。
    anchored = {e["source"] for e in edges if e["type"] in ("composed_of", "same_as")}
    parts = collections.defaultdict(set)
    for e in edges:
        if e["type"] in ("reactant_of", "product_of"):
            parts[e["target"]].add(e["source"])
    alive_rxn = {rid for rid, members in parts.items() if members & anchored}
    kept_cpd = set()
    for e in edges:
        if e["type"] in ("reactant_of", "product_of") and e["target"] in alive_rxn:
            kept_cpd.add(e["source"])
    n_drop_rxn = len(parts) - len(alive_rxn)
    if n_drop_rxn:
        print("  [guard] 丢弃「无锚定参与物」反应 %d 条（防孤岛）" % n_drop_rxn)

    kept_new = alive_rxn | kept_cpd
    allowed = kept_new | existing
    nodes = [n for n in nodes if n["id"] in kept_new]
    edges = [e for e in edges if e["source"] in allowed and e["target"] in allowed]

    # 边 id 去重（type|src|tgt；同一化合物可能同时出现在方程两侧，但 type 已区分）
    uniq, seen = [], set()
    for e in edges:
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        uniq.append(e)
    dup = len(edges) - len(uniq)
    edges = uniq

    # 节点 id 去重
    nseen, nuniq = set(), []
    for n in nodes:
        if n["id"] in nseen:
            continue
        nseen.add(n["id"])
        nuniq.append(n)
    nodes = nuniq

    # 悬空边自检
    node_ids = {n["id"] for n in nodes} | existing
    dangling = [e for e in edges if e["source"] not in node_ids or e["target"] not in node_ids]

    delta = {
        "meta": {
            "phase": "phase26", "name": "phase26_rhea",
            "source": SOURCE_TAG,
            "description": "Rhea+ChEBI 化学·反应层：真实反应（含 EC 号）+ ChEBI 参与物"
                           "（分子式/质量/交叉引用）→ 复用 reactant_of/product_of/composed_of/same_as",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "generated_by": "11_真实数据/rhea_ingest.py --build",
        },
        "nodes": nodes, "delete_nodes": [], "edges": edges, "delete_edges": [],
    }
    os.makedirs(NEO4J_DIR, exist_ok=True)
    json.dump(delta, open(DELTA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    tcnt = collections.Counter(e["type"] for e in edges)
    n_rxn_f = sum(1 for n in nodes if "Reaction" in n["labels"])
    n_cpd_f = sum(1 for n in nodes if "Molecule" in n["labels"])
    n_gen_f = sum(1 for n in nodes if "Molecule" in n["labels"] and n["props"].get("is_generic"))
    n_poly_f = sum(1 for n in nodes if "Molecule" in n["labels"] and n["props"].get("is_polymer"))
    n_part_f = sum(1 for n in nodes if "Molecule" in n["labels"] and n["props"].get("is_generic")
                   and not n["props"].get("is_polymer"))
    print("=" * 78)
    print("delta -> %s" % DELTA)
    print("  节点 %d（反应 %d / 化合物 %d；化合物中 通式 %d、聚合物 %d）"
          % (len(nodes), n_rxn_f, n_cpd_f, n_gen_f, n_poly_f))
    print("  边   %d %s（去重 %d）" % (len(edges), dict(tcnt), dup))
    print("  组成边 %d（其中部分结构 %d）· 名称桥 %d"
          % (tcnt.get("composed_of", 0), n_part_f, tcnt.get("same_as", 0)))
    print("  自检：悬空边 %d（须为 0）· 边 id 唯一 %s · 节点 id 唯一 %s"
          % (len(dangling), len({e['id'] for e in edges}) == len(edges),
             len({n['id'] for n in nodes}) == len(nodes)))
    if dangling:
        print("  ⚠ 悬空边示例：%s" % [d["id"] for d in dangling[:5]])


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fetch", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--build", action="store_true")
    ap.add_argument("--limit", type=int, default=800, help="Rhea 广谱反应条数上限")
    ap.add_argument("--core-limit", type=int, default=30, help="每个核心代谢物的检索上限")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    if a.fetch:
        cmd_fetch(a)
    elif a.check:
        cmd_check(a)
    else:
        cmd_build(a)


if __name__ == "__main__":
    main()
