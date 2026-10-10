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

        # (i) 单位—量纲一致（Phase 30）：全图 `has_unit` 边必须满足 dim(量) == dim(单位)。
        #     第 20 轮实测 29 条违规（面积→J/K、气体常数→Pa、力→kg…），其中 **5 条是
        #     仓库内策划数据的真实错误**（`PQ:force → UN:kg`）。不可判定者不计入。
        if scan.get("unit_dim_consistent"):
            if vm is None:
                return "SKIP", "verification_model 不可用"
            n_by_id = {n["id"]: n for n in (graph.get("nodes") or [])}
            bad, n_checked, n_na = [], 0, 0
            for e in graph["edges"]:
                if e.get("type") != "has_unit":
                    continue
                un, qn = n_by_id.get(e["target"]), n_by_id.get(e["source"])
                if not un or not qn:
                    continue
                up = un.get("props") or {}
                ud = vm.unit_dim_of_symbol(up.get("symbol") or up.get("name"))
                qn_name = (qn.get("props") or {}).get("name") or e["source"].split(":")[-1]
                qd = vm.dm.dim_of(qn_name)
                if ud is None or qd is None:
                    n_na += 1
                    continue
                n_checked += 1
                if vm._drop(ud) != vm._drop(qd):
                    bad.append((e["source"], e["target"], qn_name, vm._drop(qd), vm._drop(ud)))
            return ("PASS" if not bad else "FAIL"), \
                "可判定 %d 条 / 不可判定 %d 条，量纲不符 %d 条：%s" % \
                (n_checked, n_na, len(bad), bad[:3])

        # (j) 单位唯一（Phase 30）：同一物理量的多个 `has_unit` 目标**量纲必须唯一**
        #     —— 一个量不能既以 Pa 又以 J/(mol·K) 为单位。第 20 轮实测 `气体常数`
        #     挂了 **12 条**互斥单位边（10 种量纲），是 GNN 塌缩的典型指纹。
        if scan.get("unit_unique_per_quantity"):
            if vm is None:
                return "SKIP", "verification_model 不可用"
            n_by_id = {n["id"]: n for n in (graph.get("nodes") or [])}
            agg = collections.defaultdict(list)
            for e in graph["edges"]:
                if e.get("type") != "has_unit":
                    continue
                un = n_by_id.get(e["target"])
                if not un:
                    continue
                ud = vm.unit_dim_of_symbol((un.get("props") or {}).get("symbol"))
                if ud is not None:
                    agg[e["source"]].append(tuple(sorted(vm._drop(ud).items())))
            bad = [(s, len(v), len(set(v))) for s, v in agg.items() if len(set(v)) > 1]
            return ("PASS" if not bad else "FAIL"), \
                "多单位量的 %d 个，量纲互斥 %d 个：%s" % (len(agg), len(bad), bad[:3])

        # (k) 语义边目标必须**出现于源表达式**（Phase 30）：模型产物的 `defines` /
        #     `has_symbol` 边，其目标符号必须真的出现在源公式里。判据**只读表达式**
        #     （不读散文）—— 实测若把散文计入，`Rate of doing work.` 里的 "R" 会让
        #     9 条真·错误边逃过反驳。策划数据不在此断言范围（符号命名带人为习惯）。
        if scan.get("semantic_target_present"):
            if vm is None:
                return "SKIP", "verification_model 不可用"
            n_by_id = {n["id"]: n for n in (graph.get("nodes") or [])}
            bad, n_checked = [], 0
            for e in graph["edges"]:
                if e.get("type") not in ("defines", "has_symbol"):
                    continue
                p = e.get("props") or {}
                k = p.get("kind") or e.get("kind") or ""
                s = p.get("source") or ""
                if not ((k in vm.MODEL_KINDS) or ("GNN" in s) or ("LLM" in s)
                        or ("gnn" in k) or ("llm" in k)):
                    continue
                sn, tn = n_by_id.get(e["source"]), n_by_id.get(e["target"])
                if not sn or not tn:
                    continue
                ok = vm.symbol_in_source(sn, vm.target_symbol(tn))
                if ok is None:
                    continue
                n_checked += 1
                if ok is False:
                    bad.append((e["source"], e["target"], vm.target_symbol(tn)))
            return ("PASS" if not bad else "FAIL"), \
                "可判定 %d 条，符号缺失 %d 条：%s" % (n_checked, len(bad), bad[:3])

        # (l) 数学桥必须有**算子证据**（Phase 30）：`kind == math_operator_bridge` 的边
        #     必须 ① 达到 `rule_checked`；② 源表达式确实含目标数学对象所辖算子。
        #     这是「数学桥」第一次**有仪器**—— 防止将来有人凭名字相似建数学桥。
        if scan.get("math_bridge_evidenced"):
            if vm is None:
                return "SKIP", "verification_model 不可用"
            cfg = scan["math_bridge_evidenced"]
            minlv = cfg.get("min_level", "rule_checked")
            n_by_id = {n["id"]: n for n in (graph.get("nodes") or [])}
            sel, bad = [], []
            for e in graph["edges"]:
                p = e.get("props") or {}
                if (p.get("kind") or e.get("kind")) != "math_operator_bridge":
                    continue
                sel.append(e)
                lvl = p.get("verification_level")
                if vm.RANK.get(lvl, -1) < vm.RANK.get(minlv, 99):
                    bad.append((e["source"], e["target"], "level=%s" % lvl))
                    continue
                sn = n_by_id.get(e["source"])
                hits = vm.math_op_of(vm.expr_of_node(sn))
                if not any(str(e["target"]) in vm.MATH_OP_RULES[h][1] for h in hits):
                    bad.append((e["source"], e["target"], "无算子证据"))
            ok = (not bad) and len(sel) >= cfg.get("min_count", 1)
            return ("PASS" if ok else "FAIL"), \
                "数学桥 %d 条（需≥%d），无证据/低于 %s 的 %d 条：%s" % \
                (len(sel), cfg.get("min_count", 1), minlv, len(bad), bad[:3])

        # (m) 真孤岛冻结（Phase 30）：连通分量必须**逐节点**等于 `known_islands.json`
        #     的并集，且分量总数 ≤ `meta.max_components`。
        #     为什么必须断言：`connectivity_audit --strict` 在 Phase 30 之前**只看
        #     孤立/悬空/自环**，**不看连通分量数** → 「分量 1」这个北极星口径长期只能
        #     靠人肉阅读。第 20 轮撤回 81 条假边后，26 个节点（此前**唯一**靠假边挂在
        #     主图上）暴露为 4 个真孤岛，而当时**没有任何仪器会报警**。
        #     本断言把「孤岛集合」冻结下来：新增孤岛 → FAIL；真要消除孤岛须补**有证据**
        #     的边并同步更新清单（铁律 #16：改口径必须留对照；#20：查不到依赖不得记 PASS）。
        if scan.get("island_freeze"):
            ki_path = os.path.join(HERE, "known_islands.json")
            if not os.path.exists(ki_path):
                return "SKIP", "known_islands.json 不存在（不得记 PASS）"
            try:
                ki = json.load(open(ki_path, encoding="utf-8"))
            except Exception as e:                        # noqa: BLE001
                return "SKIP", "known_islands.json 解析失败：%s" % e
            ids = [n["id"] for n in (graph.get("nodes") or [])]
            par = {i: i for i in ids}

            def _find(x):
                while par[x] != x:
                    par[x] = par[par[x]]
                    x = par[x]
                return x

            for e in graph["edges"]:
                s, t = e.get("source"), e.get("target")
                if s in par and t in par:
                    rs, rt = _find(s), _find(t)
                    if rs != rt:
                        par[rs] = rt
            buckets = collections.defaultdict(set)
            for i in ids:
                buckets[_find(i)].add(i)
            comps = sorted(buckets.values(), key=len, reverse=True)
            max_comp = int(scan["island_freeze"].get("max_components",
                                                     (ki.get("meta") or {}).get("max_components", 1 << 30)))
            known = set()
            for isl in (ki.get("islands") or []):
                known |= set(isl["nodes"])
            actual = set()
            for c in comps:
                if len(c) < 100:
                    actual |= c
            fails = []
            if len(comps) > max_comp:
                fails.append("分量 %d > 上限 %d" % (len(comps), max_comp))
            extra = sorted(actual - known)
            if extra:
                fails.append("新增孤岛节点 %d 个：%s" % (len(extra), extra[:5]))
            missing = sorted(known - actual)
            if missing:
                fails.append("冻结孤岛已消失 %d 个（若补了真边须更新清单）：%s" % (len(missing), missing[:5]))
            return ("PASS" if not fails else "FAIL"), \
                "分量 %d 个（上限 %d）；冻结岛 %d 个 / %d 节点；实际孤岛 %d 个 / %d 节点%s" % (
                    len(comps), max_comp, len(ki.get("islands") or []), len(known),
                    sum(1 for c in comps if len(c) < 100), len(actual),
                    "" if not fails else "  ← " + "; ".join(fails))

        # (n) 符号复算必须有**真证据**（Phase 31）：凡 `verification_scope ==
        #     symbol_expr_recompute` 的边，其目标符号**必须真的**出现在源的结构化表达式中。
        #     为什么必须断言：`symbol_expr_recompute` 是**升级**用的 scope（source_asserted →
        #     rule_checked）。若判据被改坏成「无条件命中」，会**批量虚高**（铁律 #23 的镜像：
        #     不只看「落库==重算」，还要看「重算本身有没有证据」）。同时断言**数量下限**，
        #     防止将来某次改动把证据链悄悄清空（那会静默退化为「零条」，无仪器则无人知）。
        if "symbol_expr_evidenced" in scan:
            if vm is None:
                return "SKIP", "verification_model 不可用"
            n_by_id = {n["id"]: n for n in (graph.get("nodes") or [])}
            bad, n_seen = [], 0
            for e in graph["edges"]:
                if (e.get("props") or {}).get("verification_scope") != "symbol_expr_recompute":
                    continue
                n_seen += 1
                sn, tn = n_by_id.get(e["source"]), n_by_id.get(e["target"])
                if not sn or not tn:
                    bad.append((e["source"], e["target"], "端点缺失"))
                    continue
                if vm.symbol_in_source(sn, vm.target_symbol(tn)) is not True:
                    bad.append((e["source"], vm.target_symbol(tn), e.get("type")))
            lo = int(scan["symbol_expr_evidenced"].get("min_count", 0))
            fails = []
            if bad:
                fails.append("无证据 %d 条：%s" % (len(bad), bad[:3]))
            if n_seen < lo:
                fails.append("条数 %d < 下限 %d（证据链疑似被清空）" % (n_seen, lo))
            return ("PASS" if not fails else "FAIL"), \
                "symbol_expr_recompute %d 条（需≥%d），无证据 %d 条%s" % (
                    n_seen, lo, len(bad), "" if not fails else "  ← " + "; ".join(fails))

        # (o) **模型产物语义边**目标必须可验证（Phase 31）：`defines`/`has_symbol`/
        #     `derived_from` 的**模型产物**边，其目标标识**不得**在源的结构化表达式中缺席。
        #     这是 Phase 30 `semantic_target_present` 的同族扩展 —— Phase 30 只把 R1/R2 写在
        #     **delta 生成器**里，判级模型没有；本轮 `target_symbol` 改进后新暴露出 3 条
        #     「目标缺席」的模型边（`MX:sym:n ← MX:math:binomial` 等）**门禁先红、模型无规则可撤**。
        #     现由 `verification_model` A11 与门禁**共用同一谓词**，构造上不可能分歧。
        # ⚠ 判据必须用 `in scan` 而非 `scan.get(...)`：本断言的配置项是**空字典**（无需参数），
        #   而空字典为假值 → 曾使本行被跳过、直接落到 `未知 scan`（SKIP＝假通过，铁律 #20）。
        if "model_semantic_target_present" in scan:
            if vm is None:
                return "SKIP", "verification_model 不可用"
            n_by_id = {n["id"]: n for n in (graph.get("nodes") or [])}
            bad, n_dec = [], 0
            for e in graph["edges"]:
                if e.get("type") not in ("defines", "has_symbol", "derived_from"):
                    continue
                p = e.get("props") or {}
                k = p.get("kind") or e.get("kind") or ""
                s = p.get("source") or ""
                if not ((k in vm.MODEL_KINDS) or ("GNN" in s) or ("LLM" in s)
                        or ("gnn" in k) or ("llm" in k)):
                    continue
                sn, tn = n_by_id.get(e["source"]), n_by_id.get(e["target"])
                if not sn or not tn:
                    continue
                ok = vm.symbol_in_source(sn, vm.target_symbol(tn))
                if ok is None:
                    continue
                n_dec += 1
                if ok is False:
                    bad.append((e.get("type"), e["source"], vm.target_symbol(tn)))
            return ("PASS" if not bad else "FAIL"), \
                "可判定 %d 条，目标缺席 %d 条：%s" % (n_dec, len(bad), bad[:3])

        # (p) 量纲一致边必须**真的一致**（Phase 31）：`dimensionally_consistent` 的任一条，
        #     只要两端真量纲**均可查**，就必须严格相等。这是 T3（全图最短板任务族）的仪器。
        #     为什么现在才有：该边型此前**从未**被真量纲复算过（A1 存在但被命名缺口卡成
        #     「不可判定」）—— 关闭缺口后一次就抓出 8 条量纲互斥的假边（铁律 #33）。
        # ⚠ 同上：`in scan` 而非 `scan.get(...)`（空字典是假值 → 会静默退化为 SKIP）。
        if "dim_consistent_recompute" in scan:
            if vm is None or dt is None:
                return "SKIP", "verification_model / dimension_table 不可用"
            n_by_id = {n["id"]: n for n in (graph.get("nodes") or [])}
            bad, n_dec = [], 0
            for e in graph["edges"]:
                if e.get("type") != "dimensionally_consistent":
                    continue
                na = vm.qname_of_node(n_by_id.get(e["source"]))
                nb = vm.qname_of_node(n_by_id.get(e["target"]))
                da, db = dt.dim_of(na), dt.dim_of(nb)
                if da is None or db is None:
                    continue
                n_dec += 1
                if not dt.dim_equal(na, nb):
                    bad.append((na, nb, (e.get("props") or {}).get("verification_scope")))
            return ("PASS" if not bad else "FAIL"), \
                "可判定 %d 条，量纲互斥 %d 条：%s" % (n_dec, len(bad), bad[:3])

        # (q) **证据可追溯**（Phase 32）：凡 `level >= rule_checked` 的边，必须携带
        #     ≥1 条**独立证据**（`verification_evidence[].indep == True`），且该条证据的
        #     `impl` 必须与边的 `verifier` **一致**、`detail` **非空**。
        #     为什么必须断言：`indep` 是北极星「跨域结论正确且**证据可追溯**」的直接判据 ——
        #     没有它，「rule_checked」只是一个**标签**；有了它，才可沿证据链追到具体判据。
        #     同时断言**覆盖率下限**：防止将来某次改动把证据链悄悄清空（静默退化为「零条」）。
        # ⚠ `in scan` 而非 `scan.get(...)`（空字典是假值 → 会静默退化为 SKIP，铁律 #20/#37）。
        if "evidence_traceable" in scan:
            if vm is None:
                return "SKIP", "verification_model 不可用"
            bad, n_strict, n_cov = [], 0, 0
            for e in graph["edges"]:
                p = e.get("props") or {}
                lv = p.get("verification_level")
                if not vm.is_strict(lv):
                    continue
                n_strict += 1
                evs = p.get("verification_evidence")
                if not isinstance(evs, list) or not evs:
                    bad.append((e["source"], e.get("type"), e["target"], "无证据链"))
                    continue
                n_cov += 1
                good = [it for it in evs if isinstance(it, dict) and it.get("indep")
                        and it.get("detail") and it.get("impl") == p.get("verifier")]
                if not good:
                    bad.append((e["source"], e.get("type"), e["target"], "无独立证据/impl 不符"))
            lo = float(scan["evidence_traceable"].get("min_ratio", 1.0))
            fails = []
            if bad:
                fails.append("缺独立证据 %d 条：%s" % (len(bad), bad[:3]))
            if n_strict and (n_cov / float(n_strict)) < lo:
                fails.append("覆盖率 %.1f%% < 下限 %.1f%%" % (100.0 * n_cov / n_strict, 100.0 * lo))
            return ("PASS" if not fails else "FAIL"), \
                "level>=rule_checked %d 条；带证据链 %d 条（%.1f%%）；缺独立证据 %d 条%s" % (
                    n_strict, n_cov, (100.0 * n_cov / n_strict if n_strict else 0.0), len(bad),
                    "" if not fails else "  ← " + "; ".join(fails))

        # (r) **证据对象良构**（Phase 32）：全图每条边的 `verification_evidence`（若存在）必须
        #     ① 非空；② 每条 `kind ∈ EVIDENCE_KINDS`；③ `detail` 非空；④ `indep` 与
        #     `kind` **语义配对合法**（`recompute`/`cross_source` 必为独立；其余必为非独立）。
        #     并断言**全图证据覆盖率**下限（本轮由 1.18% → 100%，防止回退）。
        if "evidence_wellformed" in scan:
            if vm is None:
                return "SKIP", "verification_model 不可用"
            indep_kinds = {"recompute", "cross_source"}
            bad, n_with, tot = [], 0, 0
            for e in graph["edges"]:
                tot += 1
                evs = (e.get("props") or {}).get("verification_evidence")
                if evs is None:
                    continue
                n_with += 1
                if not isinstance(evs, list) or not evs:
                    bad.append((e["source"], e.get("type"), e["target"], "空/非列表"))
                    continue
                for it in evs:
                    if not isinstance(it, dict):
                        bad.append((e["source"], e.get("type"), e["target"], "非对象条目"))
                        break
                    if it.get("kind") not in vm.EVIDENCE_KINDS:
                        bad.append((e["source"], e.get("type"), e["target"],
                                    "非法 kind=%s" % it.get("kind")))
                        break
                    if not it.get("detail"):
                        bad.append((e["source"], e.get("type"), e["target"], "detail 空"))
                        break
                    if bool(it.get("indep")) != (it.get("kind") in indep_kinds):
                        bad.append((e["source"], e.get("type"), e["target"],
                                    "indep 与 kind 不配对（%s/%s）"
                                    % (it.get("kind"), it.get("indep"))))
                        break
            lo = float(scan["evidence_wellformed"].get("min_ratio", 1.0))
            fails = []
            if bad:
                fails.append("不良构 %d 条：%s" % (len(bad), bad[:3]))
            if tot and (n_with / float(tot)) < lo:
                fails.append("覆盖率 %.1f%% < 下限 %.1f%%" % (100.0 * n_with / tot, 100.0 * lo))
            return ("PASS" if not fails else "FAIL"), \
                "证据对象覆盖 %d / %d（%.1f%%）；不良构 %d 条%s" % (
                    n_with, tot, (100.0 * n_with / tot if tot else 0.0), len(bad),
                    "" if not fails else "  ← " + "; ".join(fails))

        # (s) **repr 串零残留**（Phase 32）：全图任何 props 值不得是「Python repr 化的
        #     容器」—— 即形如 `[...]` / `{...}`、**不是合法 JSON**、却能被 `ast.literal_eval`
        #     还原为 list/dict 的字符串。这正是「属性形态 → 静默降级」的指纹
        #     （`str({'a':1})` 产出单引号 repr，下游 `json.loads` 静默失败）。
        #     为什么现在加：本轮引入 `verification_evidence`（**嵌套对象列表**）后，
        #     任何一处 `str()` 强转都会立刻制造 repr 残留 —— 必须有守卫把它钉死。
        if "no_repr_residue" in scan:
            import json as _json
            import ast as _ast
            bad, n_scanned = [], 0

            def _is_repr(s):
                if not isinstance(s, str):
                    return False
                t = s.strip()
                if len(t) < 3 or t[0] not in "[{" or t[-1] not in "]}":
                    return False
                if (t[0], t[-1]) not in (("[", "]"), ("{", "}")):
                    return False
                try:
                    _json.loads(t)
                    return False            # 合法 JSON → 正常
                except Exception:
                    pass
                try:
                    got = _ast.literal_eval(t)
                    return isinstance(got, (list, dict))
                except Exception:
                    return False

            for e in graph["edges"]:
                for k, v in (e.get("props") or {}).items():
                    n_scanned += 1
                    if _is_repr(v):
                        bad.append(("edge", e.get("type"), k, str(v)[:40]))
            for n in graph.get("nodes") or []:
                for k, v in (n.get("props") or {}).items():
                    n_scanned += 1
                    if _is_repr(v):
                        bad.append(("node", n.get("id"), k, str(v)[:40]))
            return ("PASS" if not bad else "FAIL"), \
                "扫描 %d 个属性值，repr 残留 %d 条：%s" % (n_scanned, len(bad), bad[:3])

        # (t) **判级可重算重现**（Phase 33 · 铁律 #25）：全图每条边**落库**的
        #     `verification_level` / `verification_scope` / `verifier` 必须与
        #     `verification_model.classify_all` **重算**的结果**逐条一致**。
        #     为什么必须断言：这是「落库档位 = 模型产物」的唯一全局守卫。此前每一轮只靠
        #     delta 生成器**一次性自检**，一旦有人手改存档、或判级模型漂移，就会产生
        #     「落库 ≠ 重算」的**静默不一致**（第 19 轮实测 157 条，铁律 #23）。
        #     本轮把 T4/T6 的 2791 条升档落库后，此断言证明**全部 48712 条**都能被重算重现。
        #     ⚠ `in scan` 而非 `scan.get(...)`（空字典是假值 → 会静默退化为 SKIP，铁律 #20/#37）。
        if "level_scope_reproducible" in scan:
            if vm is None:
                return "SKIP", "verification_model 不可用"
            nodes = graph.get("nodes") or []
            edges = graph.get("edges") or []
            res, _ctx = vm.classify_all(nodes, edges)
            bad, n_seen = [], 0
            for e, r in res:
                n_seen += 1
                p = e.get("props") or {}
                for k in ("verification_level", "verification_scope", "verifier"):
                    if p.get(k) != r.get(k):
                        bad.append((e.get("type"), e.get("source"), e.get("target"),
                                    "%s: 落库 %r ≠ 重算 %r" % (k, p.get(k), r.get(k))))
                        break
            return ("PASS" if not bad else "FAIL"), \
                "重算覆盖 %d 条，落库≠重算 %d 条：%s" % (n_seen, len(bad), bad[:3])

        # (u) **T4 残差必须清算到位**（Phase 34 · 铁律 #30/#34）：凡 `reactant_of`/`product_of`
        #     且**非** cross_source/mismatch 的边，其「不可独立复算的理由」必须落在受控枚举内
        #     —— **零未归类**。这是「负结果也是交付物」的机器化落地：不可判定 ≠ 可以放过。
        #     判据由 `verification_model.residual_reason` **单一提供**（铁律 #36，同源）；
        #     独立审计器 R12 用**自带实现**重算同一口径（铁律 #43）。
        #     ⚠ `in scan` 而非 `scan.get(...)`（空字典是假值 → 静默退化为 SKIP，铁律 #20/#37）。
        if "residual_accounted" in scan:
            if vm is None:
                return "SKIP", "verification_model 不可用"
            n_by_id = {n["id"]: n for n in (graph.get("nodes") or [])}
            bad, n_seen = [], 0
            reasons = collections.Counter()
            for e in (graph.get("edges") or []):
                if e.get("type") not in ("reactant_of", "product_of"):
                    continue
                sc = (e.get("props") or {}).get("verification_scope")
                if sc in ("equation_species_cross_source", "equation_species_mismatch"):
                    continue
                n_seen += 1
                rs = vm.residual_reason(e, n_by_id)
                reasons[rs] += 1
                if rs not in vm.RESIDUAL_REASONS:
                    bad.append((e.get("id"), rs))
            return ("PASS" if not bad else "FAIL"), \
                "T4 残差 %d 条，未归类 %d 条：%s  %s" % (
                    n_seen, len(bad), bad[:3], dict(reasons))

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
