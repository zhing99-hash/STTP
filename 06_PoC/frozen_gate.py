# -*- coding: utf-8 -*-
"""冻结反例集门禁（Frozen Counterexample Gate）· Phase 27 可信性修复轮 / Phase 28 分层可信性
==========================================================================
与 `06_PoC/connectivity_audit.py` **并列**的回归门禁：把已坐实的错误模式冻结为
反例集（`frozen_counterexamples.json`），断言它们**不得**再次被判「已证实 / 应存在」。

设计要点（对应项目铁律）
------------------------
* 铁律 #14 —— 自检输入若与被检对象同源会一起静默通过：本门禁**独立**于校验器实现，
  且区分「结果层扫描」（主图）与「校验器层动态调用」（gate_*），互为交叉验证。
* 铁律 #19 —— `verified` 不可作单一布尔：反例集对 gate 一律比对 **verdict + scope**。
* 每个 P0 均含**正对照**（应当成立者必须成立），防止门禁「过严」把正确数据一并淹没。

用例 kind（`run_case` 分支）
----------------------------
* `dim_table`     —— 量纲真值表判等 / 不等
* `gate_chem`     —— 化学方程式守恒门禁（原子 + 电荷）
* `gate_math`     —— 数学等价门禁（变量域）
* `graph_scan`    —— 主图结果层扫描：forbidden / ABSENT / ZERO(量纲) / level_consistent
* `level_check`   —— **Phase 28**：合成小图走 `verification_model.classify_all`，
                    断言结果层 `verification_level` / `verification_scope`
* `attrib_check`  —— **Phase 28**：门禁归因配对表（虚假归因：gate 与 scope 不符）

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
try:
    import verification_model as vm                      # Phase 28 分层模型
except Exception as e:                                   # noqa: BLE001
    vm = None
    print("[WARN] verification_model 不可用：%s" % e)


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

    if kind == "level_check":
        # Phase 28：把「可信性分层」本身冻结为回归门禁。
        # 用例自带 nodes / edges（合成小图），走与主图**完全相同**的 classify_all 路径，
        # 断言结果层的 level / scope —— 防止分层模型的语义随重构悄悄漂移（铁律 #19）。
        if vm is None:
            return "SKIP", "verification_model 不可用"
        inp = case["input"]
        nodes = inp.get("nodes") or []
        edges = inp.get("edges") or []
        if not edges:
            return "SKIP", "无待判边"
        res, _ctx = vm.classify_all(nodes, edges)
        exp = case["expect"]
        idx = exp.get("edge", 0)
        if idx >= len(res):
            return "FAIL", "边索引越界 %d/%d" % (idx, len(res))
        _e, r = res[idx]
        lvl, scope = r["verification_level"], r["verification_scope"]
        fails = []
        if "level" in exp and lvl != exp["level"]:
            fails.append("level=%s≠%s" % (lvl, exp["level"]))
        if "not_level" in exp and lvl == exp["not_level"]:
            fails.append("level=%s 命中禁项" % lvl)
        if "min_level" in exp and vm.RANK.get(lvl, -1) < vm.RANK.get(exp["min_level"], 99):
            fails.append("level=%s 低于下限 %s" % (lvl, exp["min_level"]))
        if "max_level" in exp and vm.RANK.get(lvl, 99) > vm.RANK.get(exp["max_level"], -1):
            fails.append("level=%s 高于上限 %s" % (lvl, exp["max_level"]))
        if "scope" in exp and scope != exp["scope"]:
            fails.append("scope=%s≠%s" % (scope, exp["scope"]))
        for ns in (exp.get("not_scope") or []):
            if scope == ns:
                fails.append("scope=%s 命中禁项" % scope)
        detail = "level=%s scope=%s" % (lvl, scope)
        if fails:
            return "FAIL", detail + "  ← " + "; ".join(fails)
        return "PASS", detail

    if kind == "attrib_check":
        # 门禁归因配对表：把「虚假归因」冻结为回归门禁（Phase 27 P0-4 的延伸）。
        # 例：`has_symbol` 挂着 `R-PHY`，却从未跑过量纲门禁 → 归因与范围不符。
        if vm is None:
            return "SKIP", "verification_model 不可用"
        gate = case["input"]["gate"]
        scope = case["input"]["scope"]
        matched = vm._gate_matches_scope(gate, scope)
        want = (case["expect"] == "MATCH")
        return ("PASS" if matched == want else "FAIL"), \
            "gate=%s scope=%s matched=%s（期望 %s）" % (gate, scope, matched, case["expect"])

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
                    if "prop" in f:
                        val = (e.get("props") or {}).get(f["prop"])
                        if "equals" in f and val == f["equals"]:
                            hits.append((e["source"], e["target"]))
                        if "in_values" in f and val in f["in_values"]:
                            hits.append((e["source"], e["target"]))
            return ("PASS" if not hits else "FAIL"), "命中 %d：%s" % (len(hits), hits[:3])

        # (b2) 门禁归因自洽：任何**已存**的 `verification_gate` 必须与其**派生**
        #      `verification_scope` 配对合法（`_gate_matches_scope`）。这是 Phase 27
        #      「虚假归因」的落库级版本 —— 审计只改了派生字段，若不撤回归因则库里
        #      仍留着一句「某个从未跑过的门禁验过它」的假话（铁律 #15 / #13）。
        if scan.get("gate_attrib"):
            if vm is None:
                return "SKIP", "verification_model 不可用"
            bad = []
            n_gated = 0
            for e in graph["edges"]:
                p = e.get("props") or {}
                g = p.get("verification_gate")
                if not g or g == "NONE":
                    continue
                n_gated += 1
                if not vm._gate_matches_scope(g, p.get("verification_scope")):
                    bad.append((e["type"], g, p.get("verification_scope")))
            return ("PASS" if not bad else "FAIL"), \
                "已归因 %d 条，归因-范围不符 %d 条：%s" % (n_gated, len(bad), bad[:3])

        # (d) 落库一致性：`verified` 布尔必须与 `verification_level` 自洽（铁律 #19/#13）
        #     凡存了分层字段的边，其 verified 必须 == is_verified(level)。
        #     任何「布尔与分层打架」都说明有下游绕过分层直接写布尔 → 静默降级风险。
        if scan.get("level_consistent"):
            if vm is None:
                return "SKIP", "verification_model 不可用"
            bad = []
            n_tagged = 0
            for e in graph["edges"]:
                p = e.get("props") or {}
                lvl = p.get("verification_level")
                if lvl is None:
                    continue                       # 未分层边不在本断言范围
                n_tagged += 1
                want = vm.is_verified(lvl)
                if p.get("verified") != want:
                    bad.append((e["type"], e["source"], e["target"], lvl, p.get("verified")))
            return ("PASS" if not bad else "FAIL"), \
                "已分层 %d 条，布尔-分层不符 %d 条：%s" % (n_tagged, len(bad), bad[:3])

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
