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

        # (e) 跨域桥自洽：`has_quantity`（分子→物理量）边承载的**确定性语义载荷**
        #     必须与目标物理量的**真量纲**一致。载荷两来源（同一族缺陷）：
        #       a) 显式类声明「属质量类物理量」── 上游把同一条理由粘贴到 能量/动能/内能
        #          等互斥目标上（Phase 29 实测 42 条）；
        #       b) **数值断言**「摩尔质量 N g/mol」── 只能锚定质量类（Phase 29 实测另 20 条）。
        #     ⚠ 比对必须用 `vm.dim_matches_class`（含「每摩尔」形式）：
        #       否则 `molar_mass = M·N⁻¹ ≠ M` 会把 **700 条合法桥**全部误判为违规
        #       （第 19 轮首次实现即踩中，一次假 FAIL 762 条）。
        if scan.get("hq_class_consistent"):
            if vm is None or dt is None:
                return "SKIP", "verification_model / dimension_table 不可用"
            nm = {n["id"]: ((n.get("props") or {}).get("name") or "").lower()
                  for n in (graph.get("nodes") or [])}
            bad, n_checked = [], 0
            for e in graph["edges"]:
                if e.get("type") != "has_quantity":
                    continue
                rat = (e.get("props") or {}).get("rationale")
                label, want = vm.class_dim_in_rationale(rat)
                if label is None and vm._MASS_IN_RATIONALE.search(str(rat or "")):
                    label, want = "摩尔质量(数值断言)", vm.CLASS_DIM["质量"]
                if label is None:
                    continue
                got = dt.dim_of(nm.get(e["target"], ""))
                m = vm.dim_matches_class(got, want)
                if m is None:
                    continue
                n_checked += 1
                if m is False:
                    bad.append((e["source"], e["target"], label))
            return ("PASS" if not bad else "FAIL"), \
                "已标注类 %d 条，自相矛盾 %d 条：%s" % (n_checked, len(bad), bad[:3])

        # (g) 落库档位必须等于**重算**档位（铁律 #23）。
        #     比「布尔 == is_verified(level)」更强：后者只保证自洽，
        #     前者要求落库的 level 本身能被分类器**逐边重现** ——
        #     否则「复算」退化为「复述」，任何口径漂移都会被静默固化。
        #     第 19 轮实测：正是这条抓出 157 条 WebBook 桥「落库 rule_checked / 重算 source_asserted」。
        if scan.get("level_matches_recompute"):
            if vm is None:
                return "SKIP", "verification_model 不可用"
            _res, _ctx = vm.classify_all(graph.get("nodes") or [], graph["edges"])
            mism = []
            for e, r in _res:
                cur = (e.get("props") or {}).get("verification_level")
                if cur != r["verification_level"]:
                    mism.append((e.get("id") or "%s|%s|%s"
                                 % (e.get("type"), e.get("source"), e.get("target")),
                                 cur, r["verification_level"]))
            return ("PASS" if not mism else "FAIL"), \
                "已重算 %d 条，落库≠复算 %d 条：%s" % (len(_res), len(mism), mism[:3])

        # (h) 桥的取值必须**可追溯到具名来源**：`kind == webbook_thermochemistry` 的边
        #     必须声明 `n_references >= min_references`。第 19 轮实测：16 条边在多条文献
        #     **分歧超出容差**时，旧生成器仍取了「全体中位数」→ 值**既不来自任何单一文献、
        #     也不满足"多源一致"**，而 `n_references=0`（静默编造）。本断言冻结该模式。
        if scan.get("bridge_source_traceable"):
            cfg = scan["bridge_source_traceable"]
            k, mn = cfg.get("kind", "webbook_thermochemistry"), cfg.get("min_references", 1)
            bad, n_sel = [], 0
            for e in graph["edges"]:
                p = e.get("props") or {}
                if (p.get("kind") or e.get("kind")) != k:
                    continue
                n_sel += 1
                refs = p.get("n_references")
                if refs is None or int(refs) < mn:
                    bad.append((e.get("source"), e.get("target"), refs, p.get("value")))
            return ("PASS" if not bad else "FAIL"), \
                "%s：共 %d 条（需 n_references≥%d），不可追溯 %d 条 %s" % \
                (k, n_sel, mn, len(bad), bad[:3])

        # (f) 跨域桥证据下限（**含正对照**）：目标为 `target` 的边必须**全部** ≥ `min_level`，
        #     且总数 ≥ `min_count` —— 防止「一条都没有」把断言真空通过。
        if scan.get("bridge_evidence"):
            if vm is None:
                return "SKIP", "verification_model 不可用"
            cfg = scan["bridge_evidence"]
            tgt, minlv = cfg["target"], cfg["min_level"]
            sel = [e for e in graph["edges"]
                   if e.get("type") == cfg.get("edge_type", "has_quantity")
                   and e.get("target") == tgt]
            bad = [e["source"] for e in sel
                   if vm.RANK.get((e.get("props") or {}).get("verification_level"), -1)
                   < vm.RANK.get(minlv, 99)]
            ok = (not bad) and len(sel) >= cfg.get("min_count", 1)
            return ("PASS" if ok else "FAIL"), \
                "%s：共 %d 条（需≥%d），低于 %s 的 %d 条 %s" % \
                (tgt, len(sel), cfg.get("min_count", 1), minlv, len(bad), bad[:3])

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
