#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""外部数据库在线交叉核对 —— 证据分级里的 **D 级**（独立于双方）。

★ 为什么单列一台、且不并入 phase80
----------------------------------
`phase80` 必须保持**离线可复现**（今天能跑、明年也一样能跑）。联网核对的结果
会随时间漂移（数据库本身在改），两者混在一台仪器里，会让「结构性绿」
与「今天恰好绿」无法区分。因此：

  phase80        = 离线：我方 ETL 有没有篡改、化学上是否自洽
  xcheck（本脚本）= 在线：外部权威库同不同意我这个结构

★ 它回答什么问题
----------------
"图里这条反应式的分子，PubChem 认不认？ChEBI 有没有这个条目？"
RDKit 只能说「语法合法且结构自洽」，**说不出它是不是真实存在的化合物**。
由第三方库落槌，才构成 codex《四项要求》第四问要求的独立证明。

★ 必须同时披露的**共同上游**
----------------------------
ElementKG10M 源自 USPTO 专利反应抽取，**不与 PubChem 同源**；
但 ChEBI 条目本身是 ChEBI 一手数据，我方 `chebi_cache.json` 只是它的缓存 ⇒
ChEBI 这条核对是「对上游本体的回访」，独立性弱于 PubChem 那条。两者分开计数。

用法： bash sttp.sh xcheck --sample 40 --seed 20261027
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
NORM_GRAPH = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")

UA = {"User-Agent": "STTP-external-crosscheck/1.0 (+research prototype)"}
PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/%s/property/InChIKey/JSON"
OLS_CHEBI = ("https://www.ebi.ac.uk/ols4/api/ontologies/chebi/terms"
             "?iri=http%%3A%%2F%%2Fpurl.obolibrary.org%%2Fobo%%2FCHEBI_%s")


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


# ------------------------------------------------------------- 取样构造

def collect_molecules(sample_n, seed):
    """从 EK2:rxn 里取样若干反应式，抽出其中的**独立分子 SMILES**。

    为什么先 quantify 再去网：请求数受 PubChem 限流约束（≤5 req/s），
    取样口径必须是**可复现的**（固定 seed），否则两次跑出的命中率无法比较。
    """
    try:
        from rdkit import RDLogger
        from rdkit.Chem import AllChem as Chem
        RDLogger.DisableLog("rdApp.*")
    except Exception as exc:  # pragma: no cover
        print("[FATAL] 需要 RDKit：%s" % exc)
        return None, None, None

    with open(NORM_GRAPH, encoding="utf-8") as fh:
        nodes = json.load(fh)["nodes"]

    rxns = [n for n in nodes
            if n.get("id", "").startswith("EK2:rxn:")
            and (n.get("props") or {}).get("mapped_smiles")]
    rng = random.Random(seed)
    picked = rxns if sample_n <= 0 else rng.sample(rxns, min(sample_n, len(rxns)))

    mols, per_rxn = {}, []
    bad = 0
    for n in picked:
        smi = n["props"]["mapped_smiles"]
        try:
            rxn = Chem.ReactionFromSmiles(smi)
        except Exception:
            rxn = None
        if rxn is None:
            bad += 1
            continue
        here = {"id": n["id"], "reactants": [], "products": []}
        for role, group in (("reactants", rxn.GetReactants()), ("products", rxn.GetProducts())):
            for mol in group:
                try:
                    Chem.SanitizeMol(mol)
                    for a in mol.GetAtoms():
                        a.SetAtomMapNum(0)      # 剥掉映射编号，比较**结构本身**
                    cano = Chem.MolToSmiles(mol)
                except Exception:
                    continue
                if not cano:
                    continue
                here[role].append(cano)
                if cano not in mols:
                    mols[cano] = None
        per_rxn.append(here)

    # 过滤掉不参与比较的东西：单原子碎片（K+、O 骨架等试剂常见）
    candidates = [m for m in mols if len(m) > 3]
    return candidates, per_rxn, dict(total_rxns=len(rxns), sampled=len(picked), bad=bad)


# --------------------------------------------------------- PubChem 结构核对

def check_pubchem(smiles_list, pause=0.25):
    from rdkit.Chem import AllChem as Chem
    from rdkit.Chem.inchi import MolToInchiKey

    stat = {"queried": 0, "found": 0, "agree": 0, "mismatch": 0, "notfound": 0, "error": 0}
    details = {"mismatch": [], "notfound": []}
    for smi in smiles_list:
        stat["queried"] += 1
        try:
            mine = MolToInchiKey(Chem.MolFromSmiles(smi))
            _s, body = _get(PUBCHEM % urllib.parse.quote(smi), timeout=25)
            ext = json.loads(body)["PropertyTable"]["Properties"][0]["InChIKey"]
            stat["found"] += 1
            if ext == mine:
                stat["agree"] += 1
            else:
                stat["mismatch"] += 1
                if len(details["mismatch"]) < 5:
                    # ★ 必须打**完整** SMILES：截断会让读者照着错的字符去重测
                    #   （本轮吃过一次：漏掉结尾两个字符 ⇒ 得出不同的 InChIKey，
                    #   差点误判 RDKit 计算不确定）。不可省略，一个字符都不能省。
                    details["mismatch"].append({"smiles": smi, "ours": mine, "pubchem": ext})
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                stat["notfound"] += 1
                if len(details["notfound"]) < 5:
                    details["notfound"].append({"smiles": smi, "http": 404})
            else:
                stat["error"] += 1
        except Exception:
            stat["error"] += 1
        time.sleep(pause)
    return stat, details


# ------------------------------------------------------------ ChEBI 存在核对

def check_chebi(nodes, sample_n, seed, pause=0.2):
    cands = [n for n in nodes
             if n.get("id", "").startswith("CH:cpd:")
             and str((n.get("props") or {}).get("chebi_id") or "").upper().startswith("CHEBI:")]
    rng = random.Random(seed)
    picked = rng.sample(cands, min(sample_n, len(cands))) if sample_n else cands
    stat = {"queried": 0, "exists": 0, "missing": 0, "error": 0}
    missing_ids = []
    for n in picked:
        cid = str(n["props"]["chebi_id"]).split(":")[-1]
        stat["queried"] += 1
        try:
            _s, body = _get(OLS_CHEBI % cid, timeout=25)
            terms = json.loads(body).get("_embedded", {}).get("terms", [])
            if terms:
                stat["exists"] += 1
            else:
                stat["missing"] += 1
                if len(missing_ids) < 5:
                    missing_ids.append(n["id"])
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                stat["missing"] += 1
                if len(missing_ids) < 5:
                    missing_ids.append(n["id"])
            else:
                stat["error"] += 1
        except Exception:
            stat["error"] += 1
        time.sleep(pause)
    return stat, {"missing": missing_ids}


# ------------------------------------------------------------------ main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="外部在线交叉核对（D 级独立证据）")
    ap.add_argument("--sample", type=int, default=40, help="取样反应式条数（默认 40）")
    ap.add_argument("--chebi-sample", type=int, default=25, help="ChEBI 取样条数（0=跳过）")
    ap.add_argument("--seed", type=int, default=20261027, help="随机种子，保证可复现")
    ap.add_argument("--out", default="", help="结果 JSON 落盘路径")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    import rdkit
    print("=" * 78)
    print("  外部数据库在线交叉核对（D 级证据）")
    print("=" * 78)
    print("工具：RDKit %s / Python %d.%d.%d" % (rdkit.__version__, *sys.version_info[:3]))
    print("口径：seed=%d  sample=%d  chebi=%d" % (args.seed, args.sample, args.chebi_sample))

    if not os.path.exists(NORM_GRAPH):
        print("[FATAL] 缺少权威图：%s" % NORM_GRAPH)
        return 2

    with open(NORM_GRAPH, encoding="utf-8") as fh:
        nodes = json.load(fh)["nodes"]

    result = {"seed": args.seed, "sample": args.sample,
              "rdkit": rdkit.__version__, "python": "%d.%d.%d" % sys.version_info[:3]}

    # ---- PubChem ----
    cands, per_rxn, meta = collect_molecules(args.sample, args.seed)
    if cands is None:
        return 2
    print()
    print("反应式 %d 条，取样 %d 条（解析失败 %d）⇒ 独立分子SMILES %d 个"
          % (meta["total_rxns"], meta["sampled"], meta["bad"], len(cands)))
    print("-" * 78)

    net_err = 0
    try:
        pstat, pdet = check_pubchem(cands)
    except Exception as exc:
        print("[SKIP] PubChem 不可达（%s）⇒ 不算通过，也不算失败" % exc)
        pstat, pdet, net_err = None, {}, 1

    if pstat:
        print("【PubChem 结构身份核对】判定标准：InChIKey 逐字符相符")
        print("  请求 %d · 命中 %d · 相符 %d · 不符 %d · 库中没有 %d · 网络错 %d"
              % (pstat["queried"], pstat["found"], pstat["agree"],
                 pstat["mismatch"], pstat["notfound"], pstat["error"]))
        if pstat["found"]:
            print("  ⇒ 已确认集合中，相符率 %.1f%%"
                  % (100.0 * pstat["agree"] / pstat["found"]))
        for d in pdet.get("mismatch", []):
            print("     不符样例 SM", d["smiles"][:50], "我方", d["ours"], " PubChem", d["pubchem"])
        for d in pdet.get("notfound", []):
            print("     未收录样例", d["smiles"][:60])
        result["pubchem"] = {"stat": pstat, "detail": pdet}

    # ---- ChEBI ----
    if args.chebi_sample:
        print("-" * 78)
        try:
            cstat, cdet = check_chebi(nodes, args.chebi_sample, args.seed)
            print("【ChEBI 条目存在核对】判定标准：OLS4 能取到该 CHEBI id 的 term")
            print("  请求 %d · 存在 %d · 缺失 %d · 网络错 %d"
                  % (cstat["queried"], cstat["exists"], cstat["missing"], cstat["error"]))
            if cstat["queried"]:
                print("  ⇒ 存在率 %.1f%%" % (100.0 * cstat["exists"] / cstat["queried"]))
            if cdet.get("missing"):
                print("     缺失样例：%s" % "、".join(cdet["missing"]))
            result["chebi"] = {"stat": cstat, "detail": cdet}
        except Exception as exc:
            print("[SKIP] ChEBI 不可达（%s）" % exc)
            net_err += 1

    print("-" * 78)
    print("★ 独立性声明（必须跟着结果一起读）")
    print("  · PubChem 线与 ElementKG10M 不同源（后者源自 USPTO 专利抽取）⇒ 独立性强")
    print("  · ChEBI 线是对**我方缓存的上游本体**回访 ⇒ 独立性较弱，仅证『未脱落』")
    print("  · 两者都**只证明「结构存在于外部库且身份一致」**，")
    print("    不证明反应式方程、计量系数、条件的正确性 —— 那是下一层的问题")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=1)
        print()
        print("结果已落盘：%s" % args.out)

    # 网络整体不可达 ⇒ RC=2（与既有「外部前置缺失」口径一致，不得报 0）
    return 2 if (net_err >= 2) else 0


if __name__ == "__main__":
    raise SystemExit(main())
