# -*- coding: utf-8 -*-
"""Phase 8-B2 · PubChem 权威分子校验 + CID 跨源对齐。

背景（重要修正）
----------------
初始判断「分子层 754 个的摩尔质量全缺」**是错的** —— 键名是 `molecular_weight`
（不是 `molar_mass`），实际 **727/754 有值**，且 ElementKG2.0 的 713 个分子已带
`canonical_smiles` / `inchikey` / `exact_mass`（均为真实数据）。

故本模块的正确形态不是「补缺」而是「**独立校验 + 权威对齐**」（与 CODATA 常量
核校同一思路）：
  1. 用图谱自带的 `inchikey`（化学界唯一分子指纹，27 字符）向 PubChem 精确反查；
  2. **逐项比对** `molecular_weight` / `formula`，检出不一致（真实缺陷）；
  3. 回填权威 `pubchem_cid`，并对图谱中已存在的 `PC:mol:<cid>` 节点建 `same_as` 边。

关键技术点
----------
* PubChem PUG REST 批量返回的 **CID 顺序与请求顺序不一致**（实测请求 8 个返回 9 条且
  乱序）→ 不可按顺序对齐；必须在请求中索要 `InChIKey` 属性，用返回值中的
  InChIKey 精确匹配。
* 同一 inchikey 可能对应**多个 CID**（如 NBIIXXVUZAFLBC-UHFFFAOYSA-M → 19972274 / 1003）
  → 取**最小 CID** 作为确定性规则。
* 只查有 inchikey 的分子（713 个）—— 名称查询有歧义（曾见 OpenAlex concepts 的
  `Prime (order theory)` 类消歧错误），宁缺毋滥。

用法
----
    python 11_真实数据/pubchem_ingest.py --fetch     # 批量反查 -> pubchem_validate_raw.json
    python 11_真实数据/pubchem_ingest.py --check     # 与图谱逐项比对（只读）
    python 11_真实数据/pubchem_ingest.py --build     # 产出 delta（pubchem_cid + same_as）
"""

import argparse
import json
import os
import re
import socket
import ssl
import sys
import time
import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "06_PoC"))
sys.path.insert(0, HERE)

import graph_export          # noqa: E402
from element_reference import by_symbol  # noqa: E402  权威原子量（用于独立复算）

RAW = os.path.join(HERE, "pubchem_validate_raw.json")
NORMALIZED = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
SOURCE_TAG = "pubchem_pug_rest"
HOST = "pubchem.ncbi.nlm.nih.gov"
BATCH = 100          # PubChem 单次请求上限
PROPS = "MolecularFormula,MolecularWeight,InChIKey,CanonicalSMILES,Title"


# ----------------------------------------------------------------------------
def http_get(path, timeout=45, limit=4000000, retries=3):
    last = None
    for i in range(retries):
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((HOST, 443), timeout=timeout) as s:
                with ctx.wrap_socket(s, server_hostname=HOST) as ss:
                    ss.sendall((
                        "GET %s HTTP/1.1\r\nHost: %s\r\n"
                        "User-Agent: STTP/1.0 (academic knowledge-graph; contact: local)\r\n"
                        "Accept: application/json\r\nConnection: close\r\n\r\n" % (path, HOST)
                    ).encode())
                    buf = b""
                    while len(buf) < limit:
                        try:
                            c = ss.recv(65536)
                        except socket.timeout:
                            break
                        if not c:
                            break
                        buf += c
            head, _, body = buf.partition(b"\r\n\r\n")
            if "chunked" in head.decode("latin-1", "replace").lower():
                out, i = b"", 0
                while i < len(body):
                    j = body.find(b"\r\n", i)
                    if j < 0:
                        break
                    try:
                        n = int(body[i:j].split(b";")[0], 16)
                    except ValueError:
                        break
                    if n == 0:
                        break
                    out += body[j + 2: j + 2 + n]
                    i = j + 2 + n + 2
                body = out
            status = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
            if "200" not in status:
                raise RuntimeError("HTTP %s" % status)
            return body
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("拉取失败：%s" % last)


def load_norm():
    d = json.load(open(NORMALIZED, encoding="utf-8"))
    return d["nodes"] if isinstance(d, dict) else d


def parse_formula(f):
    """分子式 -> {元素符号: 原子数}。支持电荷后缀（Al+3 / H2O4P- / Cl-）。

    实测本批 713 条仅两种形态：657 纯元素式 + 56 带电荷；无括号 / 无水合物，
    故无需括号展开逻辑（若后续出现，应显式失败而非静默算错）。
    """
    s = re.sub(r"([+-]\d*)$", "", str(f or "").strip())
    if not s or re.search(r"[()\[\]·.]", s):
        return None                      # 复杂式：宁可跳过，不静默算错
    out = {}
    for sym, num in re.findall(r"([A-Z][a-z]?)(\d*)", s):
        if not sym:
            continue
        out[sym] = out.get(sym, 0) + (int(num) if num else 1)
    # 往返校验：还原出的串应与输入一致（防止正则静默吞字符）
    back = "".join("%s%s" % (k, ("%d" % v) if v > 1 else "") for k, v in out.items())
    if back != s:
        return None
    return out


def formula_mass(comp):
    """用权威原子量独立复算式量；任一元素缺权威原子量则返回 (None, 缺的元素)。"""
    tot, missing = 0.0, []
    for sym, cnt in comp.items():
        ref = by_symbol(sym)
        if not ref:
            missing.append(sym)
            continue
        tot += float(ref["atomic_weight"]) * cnt
    return (None, missing) if missing else (tot, [])


def graph_molecules():
    """返回 [(node, inchikey)]，仅含带 inchikey 的 Molecule 节点。"""
    out = []
    for n in load_norm():
        if graph_export.pick_type(n.get("labels") or []) != "Molecule":
            continue
        ik = (n.get("props") or {}).get("inchikey")
        if ik:
            out.append((n, str(ik)))
    return out


# ----------------------------------------------------------------------------
def cmd_fetch(_a):
    mols = graph_molecules()
    print("图谱中有 inchikey 的分子：%d 个" % len(mols))
    iks = [ik for _, ik in mols]
    uniq = sorted(set(iks))
    print("去重后 inchikey：%d 个（%d 批 × %d）" % (len(uniq), (len(uniq) + BATCH - 1) // BATCH, BATCH))

    recs = {}
    multi = {}
    for bi in range(0, len(uniq), BATCH):
        chunk = uniq[bi:bi + BATCH]
        path = "/rest/pug/compound/inchikey/%s/property/%s/JSON" % (",".join(chunk), PROPS)
        t0 = time.time()
        body = http_get(path)
        d = json.loads(body.decode("utf-8", "replace"))
        props = d.get("PropertyTable", {}).get("Properties", [])
        got = 0
        for x in props:
            ik = x.get("InChIKey")
            if not ik:
                continue
            cid = x.get("CID")
            if ik in recs:
                # 同 inchikey 多 CID → 取最小（确定性）
                multi.setdefault(ik, [recs[ik]["cid"]]).append(cid)
                if cid is not None and (recs[ik]["cid"] is None or cid < recs[ik]["cid"]):
                    recs[ik]["cid"] = cid
            else:
                recs[ik] = {
                    "inchikey": ik, "cid": cid,
                    "formula": x.get("MolecularFormula"),
                    "mw": float(x["MolecularWeight"]) if x.get("MolecularWeight") else None,
                    "smiles": x.get("ConnectivitySMILES") or x.get("CanonicalSMILES"),
                    "title": x.get("Title"),
                }
                got += 1
        miss = [k for k in chunk if k not in recs]
        print("  批 %2d/%2d 请求 %3d 返回 %3d 新增 %3d 耗时 %.1fs%s" % (
            bi // BATCH + 1, (len(uniq) + BATCH - 1) // BATCH, len(chunk), len(props), got,
            time.time() - t0, ("  未命中 %d" % len(miss)) if miss else ""))
        time.sleep(0.4)

    json.dump({
        "meta": {"source": SOURCE_TAG, "host": HOST, "props": PROPS,
                 "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
                 "queried": len(uniq), "matched": len(recs),
                 "multi_cid_inchikeys": len(multi)},
        "records": sorted(recs.values(), key=lambda r: r["inchikey"]),
        "multi_cid": multi,
    }, open(RAW, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("  -> %s（命中 %d / %d；同 inchikey 多 CID %d 例）" % (
        os.path.relpath(RAW, ROOT), len(recs), len(uniq), len(multi)))


def _rel(a, b):
    if a is None or b is None:
        return None
    if a == b:
        return 0.0
    den = max(abs(a), abs(b))
    return abs(a - b) / den if den else None


def _norm_formula(f):
    return str(f or "").replace(" ", "").strip()


def cmd_check(_a):
    if not os.path.exists(RAW):
        print("[ERR] 缺少 raw，先跑 --fetch"); return 1
    raw = json.load(open(RAW, encoding="utf-8"))
    by_ik = {r["inchikey"]: r for r in raw["records"]}
    mols = graph_molecules()

    print("=" * 100)
    print("图谱分子 vs PubChem 逐项核校（共 %d 个有 inchikey 的分子）" % len(mols))
    print("=" * 100)
    mw_ok = mw_diff = mw_miss = 0
    fm_ok = fm_diff = fm_miss = 0
    rows = []
    for n, ik in mols:
        p = n.get("props") or {}
        r = by_ik.get(ik)
        if not r:
            mw_miss += 1; fm_miss += 1
            continue
        gw = p.get("molecular_weight")
        gw = float(gw) if gw not in (None, "") else None
        rd = _rel(gw, r["mw"])
        if rd is None:
            mw_miss += 1
        elif rd == 0:
            mw_ok += 1
        elif rd < 1e-4:
            mw_ok += 1
        else:
            mw_diff += 1
            rows.append((n["id"], "MW", gw, r["mw"], rd))
        gf = _norm_formula(p.get("formula"))
        rf = _norm_formula(r["formula"])
        if not rf:
            fm_miss += 1
        elif gf == rf:
            fm_ok += 1
        else:
            fm_diff += 1
            rows.append((n["id"], "Formula", gf, rf, None))
    print("  分子量：一致 %d / 不一致 %d / 缺 %d" % (mw_ok, mw_diff, mw_miss))
    print("  分子式：一致 %d / 不一致 %d / 缺 %d" % (fm_ok, fm_diff, fm_miss))
    if rows:
        print()
        print("  不一致明细（前 40）：")
        for r in rows[:40]:
            print("    %-28s %-8s 图谱=%-14s PubChem=%-14s %s" % (
                r[0], r[1], str(r[2])[:14], str(r[3])[:14],
                ("rel=%.2e" % r[4]) if r[4] is not None else ""))
        if len(rows) > 40:
            print("    … 另有 %d 条" % (len(rows) - 40))

    # ---- 独立质量守恒校验：用权威原子量（IUPAC）复算分子式 ----
    # 三方交叉：ElementKG 的 molecular_weight / PubChem 的 MolecularWeight / 本模块复算。
    print()
    print("  独立质量守恒校验（权威原子量复算分子式 vs PubChem）：")
    ok = bad = skip = 0
    bad_rows = []
    rels = []
    for r in raw["records"]:
        comp = parse_formula(r["formula"])
        if comp is None:
            skip += 1
            continue
        m, missing = formula_mass(comp)
        if m is None:
            skip += 1
            continue
        rd = _rel(m, r["mw"])
        rels.append(rd)
        if rd is not None and rd < 2e-3:
            ok += 1
        else:
            bad += 1
            bad_rows.append((r["inchikey"], r["formula"], r["mw"], round(m, 3), rd))
    print("    复算一致 %d / 超阈 %d / 跳过 %d" % (ok, bad, skip))
    if rels:
        rels_sorted = sorted(x for x in rels if x is not None)
        print("    相对误差 中位 %.2e / 最大 %.2e" % (
            rels_sorted[len(rels_sorted) // 2], rels_sorted[-1]))
    for b in bad_rows[:20]:
        print("      %-28s %-14s PubChem=%-10s 复算=%-10s rel=%.2e" % (
            b[0], str(b[1])[:14], str(b[2])[:10], str(b[3])[:10], b[4] if b[4] is not None else -1))

    # ---- composed_of 的 count 独立校验（PubChem 分子式 vs 图谱已有 count）----
    # 图谱已有 2664 条 composed_of（多为 Phase13 GNN 门控验证产物，count 全覆盖）。
    # 本节用 PubChem 分子式**独立复算**原子数，检出 count 不一致。
    print()
    print("  composed_of 的 count 独立校验（PubChem 分子式 vs 图谱）：")
    nd = json.load(open(NORMALIZED, encoding="utf-8"))
    nedges = nd["edges"] if isinstance(nd, dict) else []
    gcount = {}
    for e in nedges:
        if e.get("type") == "composed_of":
            gcount[(e.get("source"), e.get("target"))] = (e.get("props") or {}).get("count")
    ik_to_nid = {}
    for n, ik in mols:
        ik_to_nid[ik] = n["id"]
    n_ok = n_bad = n_new = n_miss = 0
    mismatch = []
    for r in raw["records"]:
        nid = ik_to_nid.get(r["inchikey"])
        if not nid:
            continue
        comp = parse_formula(r["formula"])
        if comp is None:
            continue
        for sym, cnt in comp.items():
            key = (nid, "EK:el:%s" % sym)
            if key not in gcount:
                n_new += 1
            elif gcount[key] in (None, ""):
                n_miss += 1
            elif int(gcount[key]) == cnt:
                n_ok += 1
            else:
                n_bad += 1
                mismatch.append((nid, sym, gcount[key], cnt))
    print("    一致 %d / 不一致 %d / 图谱缺该边 %d / 图谱边缺 count %d" % (n_ok, n_bad, n_new, n_miss))
    for m in mismatch[:20]:
        print("      %-28s %-4s 图谱=%s  PubChem=%s" % (m[0], m[1], m[2], m[3]))


def cmd_build(_a):
    if not os.path.exists(RAW):
        print("[ERR] 缺少 raw，先跑 --fetch"); return 1
    raw = json.load(open(RAW, encoding="utf-8"))
    by_ik = {r["inchikey"]: r for r in raw["records"]}
    nodes = load_norm()
    byid = {n["id"]: n for n in nodes}
    now = datetime.datetime.now().isoformat(timespec="seconds")

    upd = []
    for n in nodes:
        if graph_export.pick_type(n.get("labels") or []) != "Molecule":
            continue
        ik = (n.get("props") or {}).get("inchikey")
        if not ik:
            continue
        r = by_ik.get(str(ik))
        if not r:
            continue
        props = dict(n.get("props") or {})
        props.update({
            "pubchem_cid": r["cid"],
            "pubchem_molecular_weight": r["mw"],
            "pubchem_formula": r["formula"],
            "pubchem_verified": True,
            "pubchem_source": SOURCE_TAG,
            "verified_at": now,
        })
        upd.append({"id": n["id"], "labels": n.get("labels"), "props": props})

    # 与图谱中已存在的 PC:mol:<cid> 节点建 same_as（不新增节点）
    edges = []
    for u in upd:
        cid = u["props"].get("pubchem_cid")
        if cid is None:
            continue
        tgt = "PC:mol:%d" % cid
        if tgt in byid and tgt != u["id"]:
            edges.append({
                "id": "same_as|%s|%s" % (u["id"], tgt),
                "source": u["id"], "target": tgt, "type": "same_as",
                "kind": "cross_source_alignment",
                "props": {"explicit_or_inferred": "inferred", "alignment_source": "pubchem",
                          "method": "inchikey_exact", "confidence": 0.99,
                          "kind": "cross_source_alignment"},
            })

    # composed_of 边：保守模式
    #   图谱已有 2664 条 composed_of（多为 Phase13 GNN 门控产物，带 verification_gate/
    #   rationale/kind=gnn_typed_verified 等元数据）。**绝不整体覆盖**，只做两件事：
    #     (a) count 与 PubChem 分子式不符 → 修正 count 并留痕（保留原有全部元数据）；
    #     (b) 图谱尚缺的边 → 新增。
    #   实测检出 19 条 count 错误，根因：Phase13 解析分子式未剥离电荷后缀（`CHO2-` /
    #   `Cr2O7-2` / `BH4-`），致**末尾元素**原子数退化为 1。
    g_edges = json.load(open(NORMALIZED, encoding="utf-8"))
    g_edges = g_edges["edges"] if isinstance(g_edges, dict) else []
    gco = {}
    for e in g_edges:
        if e.get("type") == "composed_of":
            gco[(e.get("source"), e.get("target"))] = e

    ik_to_nid = {}
    for n in nodes:
        if graph_export.pick_type(n.get("labels") or []) == "Molecule":
            ik = (n.get("props") or {}).get("inchikey")
            if ik:
                ik_to_nid[str(ik)] = n["id"]

    skip_formula = skip_elem = 0
    n_add = n_fix = n_keep = 0
    fixed = []
    for r in raw["records"]:
        nid = ik_to_nid.get(r["inchikey"])
        if not nid:
            continue
        comp = parse_formula(r["formula"])
        if comp is None:
            skip_formula += 1
            continue
        for sym, cnt in comp.items():
            tgt = "EK:el:%s" % sym
            if tgt not in byid:
                skip_elem += 1
                continue
            old = gco.get((nid, tgt))
            if old is None:
                edges.append({
                    "id": "composed_of|%s|%s" % (nid, tgt),
                    "source": nid, "target": tgt, "type": "composed_of",
                    "kind": "elemental_composition",
                    "props": {"count": cnt, "element_symbol": sym,
                              "explicit_or_inferred": "explicit", "source": SOURCE_TAG,
                              "confidence": 0.99, "derived_from": "pubchem_formula",
                              "kind": "elemental_composition"},
                })
                n_add += 1
                continue
            oc = (old.get("props") or {}).get("count")
            if oc in (None, ""):
                props = dict(old.get("props") or {})
                props.update({"count": cnt, "count_source": SOURCE_TAG})
                edges.append({"id": old["id"], "source": nid, "target": tgt,
                              "type": "composed_of", "kind": old.get("kind"), "props": props})
                n_fix += 1
                continue
            if int(oc) != cnt:
                props = dict(old.get("props") or {})     # 保留原元数据
                props.update({
                    "count": cnt, "count_corrected_from": oc,
                    "count_corrected_by": SOURCE_TAG, "count_corrected_at": now,
                    "correction_reason": "Phase13 解析分子式未剥离电荷后缀，致末尾元素原子数退化为 1",
                    "pubchem_formula": r["formula"], "pubchem_cid": r["cid"],
                })
                edges.append({"id": old["id"], "source": nid, "target": tgt,
                              "type": "composed_of", "kind": old.get("kind"), "props": props})
                n_fix += 1
                fixed.append((nid, sym, oc, cnt, r["formula"]))
            else:
                n_keep += 1

    delta = {
        "meta": {"phase": "Phase8c-pubchem", "source": "11_真实数据/pubchem_ingest.py",
                 "reason": "PubChem 权威分子校验（inchikey 精确匹配）+ CID 跨源对齐 + count 缺陷修正",
                 "generated_at": now},
        "nodes": upd, "delete_nodes": [], "edges": edges, "delete_edges": [],
    }
    out = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase19_pubchem_delta.json")
    json.dump(delta, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("  delta -> %s" % os.path.relpath(out, ROOT))
    print("    分子节点补 pubchem_cid %d 个" % len(upd))
    print("    same_as 跨源对齐边 %d 条" % (len(edges) - n_add - n_fix))
    print("    composed_of：新增 %d / 修正 count %d / 已一致未动 %d（跳过复杂式 %d / 缺元素 %d）"
          % (n_add, n_fix, n_keep, skip_formula, skip_elem))
    for f in fixed:
        print("      ✎ %-28s %-4s %s -> %s   (%s)" % (f[0], f[1], f[2], f[3], f[4]))


def main():
    ap = argparse.ArgumentParser(description="PubChem 权威分子校验 + 对齐")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fetch", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--build", action="store_true")
    a = ap.parse_args()
    if a.fetch:
        cmd_fetch(a)
    elif a.check:
        cmd_check(a)
    elif a.build:
        cmd_build(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
