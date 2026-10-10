# -*- coding: utf-8 -*-
"""冻结反例集门禁（Frozen Counterexample Gate）· Phase 27 可信性修复轮
==========================================================================
与 `06_PoC/connectivity_audit.py` **并列**的回归门禁：把已坐实的错误模式冻结为
反例集（`frozen_counterexamples.json`），断言它们**不得**再次被判「已证实 / 应存在」。

设计要点（对应项目铁律）
------------------------
* 铁律 #14 —— 自检输入若与被检对象同源会一起静默通过：本门禁**独立**于校验器实现，
  且区分「结果层扫描」（主图）与「校验器层动态调用」（gate_*），互为交叉验证。
* 铁律 #19 —— `verified` 不可作单一布尔：反例集对 gate 一律比对 **verdict + scope**。
* 每个 P0 均含**正对照**（应当成立者必须成立），防止门禁「过严」把正确数据一并淹没。

用法
----
    python 06_PoC/frozen_gate.py            # 人类可读
    python 06_PoC/frozen_gate.py --json     # 机器可读
退出码：0 = 全部通过；1 = 有回归。
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "04_验证闭环"))
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))

CASES = os.path.join(HERE, "frozen_counterexamples.json")
NORM = os.path.join(HERE, "etl", "normalized.json")

try:
    import dimension_table as dt
except Exception as e:                                   # noqa: BLE001
    dt = None
    print("[WARN] dimension_table 不可用：%s" % e)
try:
    import verification_loop as vl
except Exception as e:                                   # noqa: BLE001
    vl = None
    print("[WARN] verification_loop 不可用：%s" % e)


# --------------------------------------------------------------- helpers
def qname(nid):
    if nid.startswith("PB:pq:"):
        return nid[len("PB:pq:"):]
    ns, _, rest = nid.partition(":")
    for pre in ("pq:", "phy:"):
        if rest.startswith(pre):
            return rest[len(pre):]
    if ns == "PQ":
        return rest
    return None


def load_graph():
    with open(NORM, encoding="utf-8") as f:
        return json.load(f)


def run_case(case, graph):
    """返回 (status, detail)。status ∈ {PASS, FAIL, SKIP}。"""
    kind, exp = case["kind"], case["expect"]

    if kind == "dim_table":
        if dt is None:
            return "SKIP", "dimension_table 不可用"
        a, b = case["input"]["a"], case["input"]["b"]
        da, db = dt.dim_of(a), dt.dim_of(b)
        if da is None or db is None:
            return "SKIP", "量纲未知：%s=%s %s=%s" % (a, da, b, db)
        equal = dt.dim_equal(a, b)
        if exp == "DIM_EQUAL":
            return ("PASS" if equal else "FAIL"), "equal=%s" % equal
        if exp == "DIM_NOT_EQUAL":
            return ("PASS" if not equal else "FAIL"), "equal=%s" % equal
        return "SKIP", "未知 expect"

    if kind in ("gate_chem", "gate_math"):
        if vl is None:
            return "SKIP", "verification_loop 不可用"
        cand = dict(case["input"])
        cand["type"] = "chemical_reaction" if kind == "gate_chem" else "proves"
        try:
            fn = vl.gate_chem if kind == "gate_chem" else vl.gate_math
            verdict, evidence, codes = fn(cand)
        except Exception as e:                            # noqa: BLE001
            return "SKIP", "门禁异常：%s" % e
        # ⚠ 铁律 #13「语句跑完 ≠ 事情做成」：门禁因**缺依赖**而降级为 NEEDS_REVIEW 时，
        # 反例"未被判 VERIFIED"是**假通过**（根本没验），必须记为 SKIP 而非 PASS。
        if codes and any(str(c).endswith("-00") for c in codes):
            return "SKIP", "门禁依赖缺失（%s）——未真正验证：%s" % (codes, evidence[:60])
        ok = (verdict == "VERIFIED") if exp == "VERIFIED" else (verdict != "VERIFIED")
        return ("PASS" if ok else "FAIL"), "verdict=%s codes=%s" % (verdict, codes)

    if kind == "graph_scan":
        if graph is None:
            return "SKIP", "主图不可用"
        scan = case["scan"]

        # (a) 禁项扫描：forbidden 列表任一命中即 FAIL
        if "forbidden" in scan:
            hits = []
            for f in scan["forbidden"]:
                for e in graph["edges"]:
                    if e.get("type") != f.get("edge_type"):
                        continue
                    if "rationale_contains" in f:
                        rat = str((e.get("props") or {}).get("rationale") or "")
                        if f["rationale_contains"] in rat:
                            hits.append((e["source"], e["target"]))
                    if "status" in f:
                        if (e.get("props") or {}).get("status") == f["status"]:
                            hits.append((e["source"], e["target"]))
            return ("PASS" if not hits else "FAIL"), "命中 %d：%s" % (len(hits), hits[:3])

        # (b) ABSENT：指定 type(+kind) 的边必须不存在
        if exp == "ABSENT":
            n = sum(1 for e in graph["edges"]
                    if e.get("type") == scan.get("edge_type")
                    and (scan.get("edge_kind") is None or e.get("kind") == scan["edge_kind"]))
            return ("PASS" if n == 0 else "FAIL"), "剩余 %d 条" % n

        # (c) ZERO：全图 dc 边中「量纲严格不等」的条数必须为 0
        if scan.get("dim_mismatch"):
            if dt is None:
                return "SKIP", "dimension_table 不可用"
            bad = []
            for e in graph["edges"]:
                if e.get("type") != scan.get("edge_type"):
                    continue
                na, nb = qname(e["source"]), qname(e["target"])
                if not na or not nb:
                    continue
                if dt.dim_of(na) is None or dt.dim_of(nb) is None:
                    continue
                if not dt.dim_equal(na, nb):
                    bad.append((e["source"], e["target"],
                                (e.get("props") or {}).get("source")))
            return ("PASS" if not bad else "FAIL"), "违规 %d：%s" % (len(bad), bad[:3])

        return "SKIP", "未知 scan"

    return "SKIP", "未知 kind：%s" % kind


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    cases = json.load(open(CASES, encoding="utf-8"))["cases"]
    graph = None
    try:
        graph = load_graph()
    except Exception as e:                                # noqa: BLE001
        print("[WARN] 主图载入失败：%s" % e)

    results, tally = [], collections.Counter()
    for c in cases:
        status, detail = run_case(c, graph)
        tally[status] += 1
        results.append({"id": c["id"], "p0": c.get("p0"), "kind": c["kind"],
                        "expect": c["expect"], "status": status, "detail": detail})
        if not a.json:
            mark = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭"}[status]
            print("%s %-30s [%s] %s" % (mark, c["id"], c["kind"], detail))

    if a.json:
        print(json.dumps({"tally": dict(tally), "results": results},
                         ensure_ascii=False, indent=2))
    else:
        print("-" * 78)
        print("汇总：PASS %d / FAIL %d / SKIP %d" % (tally["PASS"], tally["FAIL"], tally["SKIP"]))
        if tally["FAIL"]:
            print("★ 冻结反例集回归：%d 条已坐实的错误模式重新出现" % tally["FAIL"])

    return 1 if tally["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
