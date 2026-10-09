# -*- coding: utf-8 -*-
"""Phase 8-B3 · ChEMBL 化学·药物层真实化。

背景
----
图谱的分子层（754 个 `Molecule`，其中 713 个带 `inchikey`）目前只有化学身份
（分子式 / 分子量 / PubChem CID），**完全没有「药学」维度**：
哪些分子是**已上市药物**、处于研发哪一阶段、属于哪一类药理分类 —— 全缺。

ChEMBL（EMBL-EBI 维护的开放药物发现数据库，CC BY-SA 3.0）正好补这一块。

数据源可达性（2026-10-09 三轮实测，见 `probe_sources.py`）
----------------------------------------------------------
  `www.ebi.ac.uk` 是**同一主机、按路径区别对待**的白名单：
    * `/europepmc/webservices/rest/*`   直连 3/3 ✅
    * `/chembl/api/data/*`              **直连 0/3 ⛔，走代理 2/3 🟡（抖动）**
  → 因此本模块的 HTTP 层必须是**直连优先 → 回退代理 → 多轮重试**。

API 要点（实测）
----------------
  * 按结构反查：`molecule.json?molecule_structures__standard_inchi_key=<INK>&limit=1`
    （`__iexact` 亦可；简单等值即可）
  * `only=` 字段裁剪有效（2455 B → 1915 B）
  * `indication_class` 即使对阿司匹林也是 `None` —— **ChEMBL 该字段基本为空，弃用**
  * 有价值的跨学科字段：
      `max_phase`（0–4，4=已批准上市）、`first_approval`（首次批准年份）、
      `molecule_type`、`natural_product`（天然产物）、`oral`/`parenteral`（给药途径）、
      `orphan`（孤儿药）、`first_in_class`（首创药）、`black_box_warning`、
      `availability_type`、`atc_classifications`（WHO ATC 五级码列表）

用法
----
    python 11_真实数据/chembl_ingest.py --fetch           # 拉取（支持断点续传）
    python 11_真实数据/chembl_ingest.py --fetch --limit 50
    python 11_真实数据/chembl_ingest.py --check           # 覆盖率 / 阶段分布 / 交叉校验（只读）
    python 11_真实数据/chembl_ingest.py --build           # 产出 delta
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(HERE, "chembl_raw.json")
NORMALIZED = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
NEO4J_DIR = os.path.join(ROOT, "06_PoC", "etl", "neo4j")
DELTA = os.path.join(NEO4J_DIR, "phase22_chembl_delta.json")

SOURCE_TAG = "chembl_api"
HOST = "www.ebi.ac.uk"
BASE = "https://www.ebi.ac.uk/chembl/api/data/molecule.json"

# ── 类药性 / 理化描述符 → 物理量节点（复用量化层，**零 schema 改动**）──────────────
# A7 已建立「元素性质物理量化」范式：`PQ:el:<prop>` 节点（PhysicalQuantity）
# + `Element -[has_quantity]-> PQ:el:<prop>` 边。此处对分子层做同构扩展：
# `PQ:chem:<prop>` + `Molecule -[has_quantity]-> PQ:chem:<prop>`。
# 单位取自 ChEMBL 描述符定义的固有量纲（非推断），故 unit_inferred=False。
CHEM_DESCRIPTORS = [
    # key,             中文名,           英文名,            符号,      单位,     类别
    ("alogp",          "脂水分配系数",    "XLogP3",          "logP",     "无量纲",  "druglikeness"),
    ("psa",            "拓扑极性表面积",  "TPSA",            "TPSA",     "Å²",     "druglikeness"),
    ("hbd",            "氢键供体数",      "HBD",             "HBD",      "个",     "druglikeness"),
    ("hba",            "氢键受体数",      "HBA",             "HBA",      "个",     "druglikeness"),
    ("rtb",            "可旋转键数",      "RotatableBonds",  "nRotB",    "个",     "druglikeness"),
    ("aromatic_rings", "芳香环数",        "AromaticRings",   "nArRings", "个",     "constitution"),
    ("heavy_atoms",    "重原子数",        "HeavyAtoms",      "nHeavy",   "个",     "constitution"),
    ("qed_weighted",   "类药性定量估计",  "QED",             "QED",      "无量纲",  "druglikeness"),
]
PQ_PREFIX = "PQ:chem:"

# 只取需要字段：显著减小 payload（实测 2455 B -> 1915 B）
ONLY = ",".join([
    "molecule_chembl_id", "max_phase", "molecule_type", "first_approval",
    "natural_product", "oral", "parenteral", "orphan", "first_in_class",
    "black_box_warning", "availability_type", "atc_classifications",
    "molecule_properties", "molecule_structures", "molecule_synonyms",
])

_lock = threading.Lock()

# `www.ebi.ac.uk/chembl/*` 直连已知被拦（0/3）。若每个请求都先等直连超时，
# 会白白浪费 ~2.5s/次。故首轮探测后**自动永久跳过直连**（线程安全地置 False）。
_DIRECT_OK = True
_direct_fail = 0


# ---------------------------------------------------------------------------
# HTTP：直连优先 -> 回退代理（www.ebi.ac.uk/chembl 直连被拦）-> 多轮重试
# ---------------------------------------------------------------------------
def _proxy():
    for k in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"):
        v = os.environ.get(k)
        if v:
            return v.strip()
    return None


def http_json(url, timeout=25, direct_tries=1, proxy_tries=6):
    """返回 (dict, err)。直连失败后走代理重试；直连一旦确认不通便不再尝试。"""
    global _DIRECT_OK, _direct_fail
    proxy = _proxy()
    attempts = []
    if direct_tries and _DIRECT_OK:
        attempts.append(("direct", None))
    if proxy:
        attempts += [("proxy", proxy)] * max(1, proxy_tries)
    last = None
    for tag, px in attempts:
        ph = (urllib.request.ProxyHandler({"http": px, "https": px}) if px
              else urllib.request.ProxyHandler({}))
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context()), ph)
        opener.addheaders = [("User-Agent", "STTP/1.0 (academic knowledge-graph; local)"),
                             ("Accept", "application/json")]
        try:
            with opener.open(url, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8")), None
        except Exception as e:                      # noqa: BLE001
            last = "%s: %s" % (tag, e)
            if tag == "direct":
                with _lock:
                    _direct_fail += 1
                    if _direct_fail >= 3:
                        if _DIRECT_OK:
                            print("  [通道] 直连连续失败 %d 次，后续仅走代理" % _direct_fail)
                        _DIRECT_OK = False
            time.sleep(0.6)
    return None, last


# ---------------------------------------------------------------------------
# 图谱侧：取出带 inchikey 的分子
# ---------------------------------------------------------------------------
def load_molecules():
    d = json.load(open(NORMALIZED, encoding="utf-8"))
    nodes = d["nodes"] if isinstance(d, dict) else d
    out = []
    for n in nodes:
        p = n.get("props") or {}
        ink = p.get("inchikey")
        if ink:
            out.append({
                "id": n["id"],
                "inchikey": ink,
                "label": n.get("label") or p.get("name"),
                "formula": p.get("formula"),
                "mw": p.get("molecular_weight"),
                "pubchem_cid": p.get("pubchem_cid"),
            })
    out.sort(key=lambda x: x["id"])
    return out


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------
def _fetch_one(m, tries=2):
    q = urllib.parse.urlencode({
        "molecule_structures__standard_inchi_key": m["inchikey"],
        "only": ONLY,
        "limit": 1,
    })
    url = "%s?%s" % (BASE, q)
    for _ in range(tries):
        d, err = http_json(url)
        if d is not None:
            ms = d.get("molecules") or []
            if not ms:
                return m["inchikey"], {"found": False}
            x = ms[0]
            st = x.get("molecule_structures") or {}
            pr = x.get("molecule_properties") or {}
            syn = x.get("molecule_synonyms") or []
            return m["inchikey"], {
                "found": True,
                "chembl_id": x.get("molecule_chembl_id"),
                "max_phase": x.get("max_phase"),
                "molecule_type": x.get("molecule_type"),
                "first_approval": x.get("first_approval"),
                "natural_product": x.get("natural_product"),
                "oral": x.get("oral"),
                "parenteral": x.get("parenteral"),
                "orphan": x.get("orphan"),
                "first_in_class": x.get("first_in_class"),
                "black_box_warning": x.get("black_box_warning"),
                "availability_type": x.get("availability_type"),
                "atc_codes": x.get("atc_classifications") or [],
                "full_mwt": pr.get("full_mwt"),
                "alogp": pr.get("alogp"),
                "hba": pr.get("hba"),
                "hbd": pr.get("hbd"),
                "psa": pr.get("psa"),
                "rtb": pr.get("rtb"),
                "qed_weighted": pr.get("qed_weighted"),
                "aromatic_rings": pr.get("aromatic_rings"),
                "heavy_atoms": pr.get("heavy_atoms"),
                "molecular_formula": pr.get("full_molformula"),
                "standard_inchi_key": st.get("standard_inchi_key"),
                "canonical_smiles": st.get("canonical_smiles"),
                "synonyms": [s.get("molecule_synonym") for s in syn[:6]
                             if s.get("molecule_synonym")],
            }
    return m["inchikey"], {"found": False, "error": "fetch_failed"}


def cmd_fetch(a):
    mols = load_molecules()
    if a.limit:
        mols = mols[:a.limit]
    old = {}
    if os.path.exists(RAW) and not a.fresh:
        try:
            old = json.load(open(RAW, encoding="utf-8")).get("records") or {}
            print("断点续传：已有 %d 条记录" % len(old))
        except Exception:
            old = {}

    todo = [m for m in mols if m["inchikey"] not in old]
    print("图谱带 inchikey 分子 %d 个；待拉取 %d 个（并发 %d）" % (len(mols), len(todo), a.workers))

    done = dict(old)
    n_ok = n_miss = n_err = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(_fetch_one, m): m for m in todo}
        for i, f in enumerate(as_completed(futs), 1):
            ink, rec = f.result()
            with _lock:
                done[ink] = rec
                if rec.get("found"):
                    n_ok += 1
                elif rec.get("error"):
                    n_err += 1
                else:
                    n_miss += 1
            if i % 25 == 0 or i == len(todo):
                el = time.time() - t0
                print("  %4d/%4d  命中 %d · 未收录 %d · 失败 %d  (%.0fs, %.1f/s)"
                      % (i, len(todo), n_ok, n_miss, n_err, el, i / max(el, 0.1)))
                # 增量落盘，防中断丢失
                _save_raw(done, mols)
    _save_raw(done, mols)
    print("完成：命中 %d / 未收录 %d / 失败 %d / 合计 %d" % (n_ok, n_miss, n_err, len(done)))
    print("-> %s" % RAW)


def _save_raw(done, mols):
    payload = {
        "source": SOURCE_TAG,
        "source_url": BASE,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "total_molecules_in_graph": len(mols),
        "records": done,
    }
    tmp = RAW + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, RAW)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------
def cmd_check(a):
    if not os.path.exists(RAW):
        print("缺少 %s，先跑 --fetch" % RAW); return
    raw = json.load(open(RAW, encoding="utf-8"))
    recs = raw["records"]
    mols = {m["inchikey"]: m for m in load_molecules()}

    found = {k: v for k, v in recs.items() if v.get("found")}
    print("=" * 76)
    print("ChEMBL 覆盖率")
    print("=" * 76)
    print("  图谱待查分子   %d" % len(mols))
    print("  已发起查询     %d" % len(recs))
    print("  ChEMBL 命中    %d  (%.1f%%)" % (len(found), 100.0 * len(found) / max(len(recs), 1)))

    # max_phase 分布
    from collections import Counter
    def phase(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None
    ph = Counter()
    for v in found.values():
        p = phase(v.get("max_phase"))
        ph[p if p is not None else "无"] += 1
    print("\n  max_phase 分布（4=已批准上市）:")
    for k in [4, 3, 2, 1, 0, "无"]:
        if ph.get(k):
            label = {4: "已批准上市", 3: "III 期临床", 2: "II 期临床",
                     1: "I 期临床", 0: "临床前", "无": "未标注"}.get(k, str(k))
            print("    %-10s %4d" % ("%s %s" % (k, label), ph[k]))

    approved = [k for k, v in found.items() if phase(v.get("max_phase")) == 4]
    print("\n  已批准药物（max_phase=4）: %d 个" % len(approved))
    if approved:
        print("  抽样 12 个已批准药物：")
        for k in sorted(approved)[:12]:
            v = found[k]
            m = mols.get(k) or {}
            print("    %-30s %-14s %s%s" % (str(m.get("label"))[:30],
                                            v.get("chembl_id"),
                                            ("首批 %s 年" % v["first_approval"]) if v.get("first_approval") else "",
                                            ("  ATC %s" % ",".join(v["atc_codes"][:3])) if v.get("atc_codes") else ""))

    # ATC 覆盖
    with_atc = [v for v in found.values() if v.get("atc_codes")]
    codes = set()
    for v in with_atc:
        codes.update(v["atc_codes"])
    print("\n  ATC 分类：%d 个分子有 ATC 码，去重后 %d 个五级码" % (len(with_atc), len(codes)))
    if codes:
        lv = Counter(c[0] for c in codes)
        print("    一级（解剖主类）分布: %s" % dict(sorted(lv.items())))

    # 给药途径 / 天然产物 / 药物类型
    # ⚠ ChEMBL 的「是/否」是三态（1 是 / 0 否 / -1 未知）。这里**必须**只数 True，
    # 直接用 `if v.get(field)` 会把 `-1`（未知）也数进去（曾误报「孤儿药 262 个」）。
    for field, label in (("oral", "口服"), ("parenteral", "注射"),
                         ("natural_product", "天然产物"), ("orphan", "孤儿药"),
                         ("first_in_class", "首创药"), ("black_box_warning", "黑框警告")):
        c = sum(1 for v in found.values() if v.get(field) in (True, 1))
        unk = sum(1 for v in found.values() if v.get(field) in (-1, -1.0))
        if c or unk:
            print("  %-8s %d 个%s" % (label, c, ("（未知 %d）" % unk) if unk else ""))
    mt = Counter(v.get("molecule_type") for v in found.values() if v.get("molecule_type"))
    print("  分子类型: %s" % dict(mt.most_common(6)))

    # 分子量交叉校验（ChEMBL full_mwt vs 图谱 molecular_weight）
    bad, ok, skip = [], 0, 0
    for k, v in found.items():
        mw_g = (mols.get(k) or {}).get("mw")
        mw_c = v.get("full_mwt")
        try:
            a1, a2 = float(mw_g), float(mw_c)
        except (TypeError, ValueError):
            skip += 1
            continue
        if a1 <= 0:
            skip += 1
            continue
        rel = abs(a1 - a2) / a1
        if rel < 5e-3:
            ok += 1
        else:
            bad.append((k, a1, a2, rel))
    print("\n  分子量交叉校验（图谱 vs ChEMBL full_mwt）: 一致 %d · 偏差 %d · 无法比 %d"
          % (ok, len(bad), skip))
    for k, a1, a2, rel in sorted(bad, key=lambda x: -x[3])[:8]:
        print("    ⚠ %-30s 图谱=%.3f ChEMBL=%.3f (rel=%.2e)"
              % (str((mols.get(k) or {}).get('label'))[:30], a1, a2, rel))


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
def cmd_build(a):
    if not os.path.exists(RAW):
        print("缺少 %s，先跑 --fetch" % RAW); return
    raw = json.load(open(RAW, encoding="utf-8"))
    recs = raw["records"]
    by_ink = {m["inchikey"]: m for m in load_molecules()}
    now = datetime.now().isoformat(timespec="seconds")

    nodes, edges = [], []
    pq_used = set()
    n_enrich = 0
    for ink, v in sorted(recs.items()):
        if not v.get("found"):
            continue
        m = by_ink.get(ink)
        if not m:
            continue
        props = {
            "chembl_id": v.get("chembl_id"),
            "chembl_source": SOURCE_TAG,
            "chembl_fetched_at": now,
        }
        if v.get("max_phase") is not None:
            try:
                mp = int(float(v["max_phase"]))
            except (TypeError, ValueError):
                mp = None
            # ChEMBL 的 `max_phase=-1` 表示「不适用/无研发记录」，非「临床前(0)」。
            # 仅写 0..4 的真实阶段；-1 与 null 一样视为「未知」，不写属性以免误读。
            if mp is not None and 0 <= mp <= 4:
                props["max_phase"] = mp
        for k in ("molecule_type", "first_approval"):
            if v.get(k) is not None:
                props[k] = v[k]
        # ⚠ ChEMBL 的「是/否」字段是**三态**：`1`=是、`0`=否、**`-1`=未知**。
        # 直接用 `bool(x)` 会把 `-1` 当成 `True`，把「未知」错标成「是」。
        # 因此仅在 0/1 时写布尔值，-1 一律**不写**（保持「未知」语义）。
        for k in ("natural_product", "orphan", "first_in_class", "black_box_warning"):
            raw = v.get(k)
            if raw in (0, 1, 0.0, 1.0):
                props[k] = bool(int(raw))
        # `oral` / `parenteral` 是真布尔（ChEMBL BooleanField）
        for k in ("oral", "parenteral"):
            if isinstance(v.get(k), bool):
                props[k] = v[k]
        # `availability_type` 是枚举（-1 未知 / 0..3 具体类别）→ 存原值，不做布尔化
        if v.get("availability_type") is not None:
            try:
                props["chembl_availability_type"] = int(float(v["availability_type"]))
            except (TypeError, ValueError):
                pass
        if v.get("atc_codes"):
            props["atc_codes"] = list(v["atc_codes"])
        for k, dst in (("alogp", "chembl_alogp"), ("psa", "chembl_psa"),
                       ("hbd", "chembl_hbd"), ("hba", "chembl_hba"),
                       ("qed_weighted", "chembl_qed"), ("full_mwt", "chembl_full_mwt")):
            if v.get(k) is not None:
                props[dst] = v[k]
        if v.get("synonyms"):
            props["chembl_synonyms"] = list(v["synonyms"])

        nodes.append({"id": m["id"], "props": props})
        n_enrich += 1

        # ── 类药性 / 理化描述符 → has_quantity 边（可复用量化层）──────────────
        for key, cn, en, sym, unit, cat in CHEM_DESCRIPTORS:
            raw = v.get(key)
            if raw is None:
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            edges.append({
                "id": "has_quantity|%s|%s%s" % (m["id"], PQ_PREFIX, key),
                "source": m["id"],
                "target": PQ_PREFIX + key,
                "type": "has_quantity",
                "kind": "chembl_descriptor",
                "props": {
                    "value": val,
                    "unit": unit,
                    "explicit_or_inferred": "explicit",
                    "confidence": 0.95,
                    "source": SOURCE_TAG,
                    "descriptor_source": "ChEMBL molecule_properties",
                    "molecule_chembl_id": v.get("chembl_id"),
                },
            })
            pq_used.add(key)

    # 只为「确实有值」的描述符建物理量节点，避免产生孤立节点
    for key, cn, en, sym, unit, cat in CHEM_DESCRIPTORS:
        if key not in pq_used:
            continue
        nodes.append({
            "id": PQ_PREFIX + key,
            "labels": ["Entity", "PhysicalQuantity"],
            "props": {
                "name": cn, "en_name": en, "symbol": sym,
                "domain": "chem.molecule_property",
                "ntype": "physical_quantity",
                "unit": unit,
                "unit_inferred": False,
                "unit_basis": "ChEMBL 描述符定义（固有量纲，非推断）",
                "category": cat,
                "source": SOURCE_TAG,
            },
        })

    delta = {
        "meta": {
            "name": "phase21_chembl",
            "source": SOURCE_TAG,
            "description": "ChEMBL 化学·药物层：分子属性真实化（max_phase/first_approval/ATC/给药途径）",
            "built_at": now,
            "generated_by": "11_真实数据/chembl_ingest.py --build",
        },
        "nodes": nodes,
        "delete_nodes": [],
        "edges": edges,
        "delete_edges": [],
    }
    os.makedirs(NEO4J_DIR, exist_ok=True)
    with open(DELTA, "w", encoding="utf-8") as f:
        json.dump(delta, f, ensure_ascii=False, indent=1)
    print("delta -> %s" % DELTA)
    print("  节点更新 %d · 新增 0 · 删除 0 · 边 新增 %d" % (n_enrich, len(edges)))


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fetch", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--build", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--fresh", action="store_true", help="忽略已有 raw，全部重拉")
    a = ap.parse_args()
    if a.fetch: cmd_fetch(a)
    elif a.check: cmd_check(a)
    elif a.build: cmd_build(a)


if __name__ == "__main__":
    main()
