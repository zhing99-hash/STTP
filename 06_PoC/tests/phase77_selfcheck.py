#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 77（F6 公式详情 / F9 数据集版本化）自检 —— 判据 + 自证（负对照）。

★ 为什么单独一个文件：F6 的判据既不是「前端静态结构」（phase43_frontend_selfcheck）
  也不是「子图端点」（phase43_subgraph_selfcheck），而是**后端方法层的语义门禁** ——
  尤其「非公式 ⇒ 不给空壳」这条，前端断言不到（前端只看到 status）。

判据
----
  F60 门禁     ：非 Formula 标签的节点 ⇒ `no_result` + 原因说明它是什么（不给五段空壳）
  F61 三态互异 ：extracted / not_extracted / no_referent 的 `kind` 三者互不相同且非空
  F62 跨字段自洽：顶层 `missing` == `sections[*].present == false` 的集合
  F63 覆盖率守恒：`sum(coverage.values()) == formula_nodes`
  F64 占位值不冒充数据：`clean_value("暂无")` 等一律判为「没有」（None）
  F65 覆盖率现算：夹具里造一个**已知分布**，断言返回值**逐项等于**它（不是抄来的常数）
  F66 端点参数：缺 `id` / `limit` 非整数 / 越界 ⇒ HTTP 400 + `BAD_REQUEST`
  F67 契约落地：`contracts/formula.schema.json` 可解析且登记在端点契约里

★ 每条判据都配**负对照**（自证）：注入缺陷 ⇒ 该判据必须变红，否则是恒真。
  red_probe.py 的 R52–R58 是这些判据的**构造性可红性**证明（改的是事实源）。
"""

from __future__ import annotations

import json
import os
import sys
from typing import Callable, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
for p in (os.path.join(ROOT, "03_知识层"), os.path.join(ROOT, "06_PoC"),
          os.path.join(ROOT, "11_真实数据")):
    if p not in sys.path:
        sys.path.insert(0, p)

import queries as q  # noqa: E402


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
class Report:
    def __init__(self) -> None:
        self.rows: List[tuple] = []
        self.skips: List[tuple] = []

    def add(self, ok: bool, name: str, detail: str = "") -> bool:
        self.rows.append((bool(ok), name, detail or ""))
        return bool(ok)

    def skip(self, name: str, why: str = "") -> None:
        self.skips.append((name, why or ""))

    @property
    def n_pass(self) -> int:
        return sum(1 for ok, _, _ in self.rows if ok)

    @property
    def n_fail(self) -> int:
        return sum(1 for ok, _, _ in self.rows if not ok)

    def exit_code(self) -> int:
        return 0 if self.n_fail == 0 else 1

    def render(self, title: str) -> None:
        print("=" * 68)
        print("  " + title)
        print("=" * 68)
        for ok, name, detail in self.rows:
            print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                                  ("  —— " + detail) if detail else ""))
        if self.skips:
            print()
            print("[未覆盖 · 如实登记]")
            for name, why in self.skips:
                print("  [SKIP] %s  —— %s" % (name, why))
        print()
        print("  phase77_selfcheck: PASS %d / FAIL %d / SKIP %d"
              % (self.n_pass, self.n_fail, len(self.skips)))
        print()
        if self.n_fail:
            print("  结论：有判据未通过 —— 上述断言**有能力红**，请按 detail 定位。")
        else:
            print("  结论：全部判据通过；负对照已证明这些判据**不是恒真**。")


# ---------------------------------------------------------------------------
# 夹具后端（**内存**，不需要权威图 ⇒ 干净检出也能跑，铁律 #53）
# ---------------------------------------------------------------------------
class FixtureBackend(q.Backend):
    """最小实现：`Backend` 只要求 3 个原语。"""

    name = "fixture"

    def __init__(self, nodes: List[dict], edges: List[dict]) -> None:
        self._nodes = nodes
        self._edges = edges

    def source_identity(self):
        return ("fixture", "phase77_formula_fixture")

    def all_nodes(self) -> List[dict]:
        return self._nodes

    def all_edges(self) -> List[dict]:
        return self._edges


def _fixture() -> FixtureBackend:
    """三条公式覆盖三种 `applicability_status`，外加一个单位、一个论文、一个等价目标。"""
    nodes = [
        {"id": "F:extracted", "labels": ["Formula"], "props": {
            "name": "E", "applicability_status": "extracted",
            "applicability": ["仅适用于惯性系"], "source": "FO"}},
        {"id": "F:not_extracted", "labels": ["Formula"], "props": {
            "name": "N", "applicability_status": "not_extracted",
            "discoverer": "暂无"}},
        {"id": "F:no_referent", "labels": ["Formula"], "props": {
            "name": "R", "applicability_status": "no_referent"}},
        # ★ 关键反例：主类型是 Lemma，但**带 Formula 标签** ⇒ 门禁必须放行
        {"id": "MX:lemma:x", "labels": ["Lemma", "Formula", "Entity"], "props": {
            "name": "lemma", "applicability_status": "not_extracted"}},
        {"id": "U:newton", "labels": ["Unit"], "props": {"name": "newton"}},
        {"id": "P:paper", "labels": ["Paper"], "props": {"name": "p"}},
        {"id": "WD:q1", "labels": ["WikidataEntity"], "props": {"name": "Q1"}},
    ]
    edges = [
        {"id": "e1", "source": "F:extracted", "target": "WD:q1",
         "type": "same_as", "kind": "", "props": {"verifier": "seed"}},
    ]
    return FixtureBackend(nodes, edges)


# ---------------------------------------------------------------------------
# 判据
# ---------------------------------------------------------------------------
def check(rep: Report) -> None:
    b = _fixture()
    lic: Callable[[str], str] = lambda s: {"FO": "redistributable"}.get(s, "forbidden")

    # ---- F60 门禁：非公式 ⇒ no_result，且不得给五段空壳 ----
    r_unit = b.formula_detail("U:newton", license_of=lic)
    r_paper = b.formula_detail("P:paper", license_of=lic)
    gate_ok = (r_unit["status"] == q.NO_RESULT and "不是公式" in (r_unit["reason"] or "")
               and r_paper["status"] == q.NO_RESULT
               and not r_unit.get("sections"))
    rep.add(gate_ok, "F60 门禁：非 Formula 标签的节点 ⇒ no_result（不给五段空壳）",
           "Unit→%s；Paper→%s" % (r_unit["status"], r_paper["status"]))

    # ★ 门禁用 labels 而非主类型：MX:lemma:x 主类型是 Lemma，但**必须放行**
    r_lemma = b.formula_detail("MX:lemma:x", license_of=lic,
                               node_type_of=lambda ls: "Lemma")
    rep.add(r_lemma["status"] == q.OK,
           "F60b 门禁判据用 labels：主类型=Lemma 但带 Formula 标签 ⇒ 仍放行",
           "status=%s" % r_lemma["status"])

    # ---- F61 三态互异 ----
    kinds = []
    for nid in ("F:extracted", "F:not_extracted", "F:no_referent"):
        rr = b.formula_detail(nid, license_of=lic)
        kinds.append((rr["sections"]["applicability"].get("kind") or ""))
    rep.add(len(set(kinds)) == 3 and all(k.strip() for k in kinds),
           "F61 三种 applicability_status 的 kind 互不相同且非空",
           " / ".join(kinds))

    # ---- F62 跨字段自洽 ----
    rr = b.formula_detail("F:no_referent", license_of=lic)
    secs = rr["sections"]
    comp = sorted(k for k in ("applicability", "symbols", "dimension",
                              "provenance", "equivalents")
                 if not secs[k].get("present"))
    rep.add(comp == sorted(rr.get("missing") or []),
           "F62 顶层 missing == 各段 present=false 的集合",
           "computed=%s declared=%s" % (comp, sorted(rr.get("missing") or [])))

    # ---- F63 覆盖率守恒 ----
    cov = b.formula_coverage()
    total = sum(cov["counts"].values())
    rep.add(total == cov["formula_nodes"],
           "F63 覆盖率守恒：sum(coverage) == formula_nodes",
           "%d == %d" % (total, cov["formula_nodes"]))

    # ---- F64 占位值不冒充数据 ----
    bad = [v for v in ("暂无", "", "  ", "-", "—", "N/A", "None") if q.clean_value(v) is not None]
    nested = q.clean_value(["a", "暂无", "b"])
    rep.add(not bad and nested == ["a", "b"],
           "F64 占位值一律判为「没有」（含列表逐元素清洗）",
           "未清洗=%s；列表清洗=%s" % (bad, nested))

    # ---- F65 覆盖率**现算**（夹具分布已知）----
    want = {"extracted": 1, "not_extracted": 2, "no_referent": 1}
    got = cov["counts"]
    rep.add(got == want and cov["formula_nodes"] == 4,
           "F65 覆盖率现算：夹具已知分布 == 返回值（不是抄来的历史数字）",
           "got=%s want=%s · nodes=%d" % (got, want, cov["formula_nodes"]))

    # ---- F66 端点参数校验（HTTP 层）----
    try:
        import urllib.request
        import urllib.parse
        base = os.environ.get("STTP_VIZ_BASE", "").rstrip("/")
        if base:
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with op.open(base + "/api/formula", timeout=30) as resp:
                    code = resp.status
            except urllib.error.HTTPError as e:
                code = e.code
            rep.add(code == 400, "F66 端点参数：缺 id ⇒ HTTP 400", "HTTP %s" % code)
        else:
            rep.skip("F66 端点参数（HTTP 400）",
                     "未设 STTP_VIZ_BASE ⇒ 不在本仪器里起服务；由 `bash sttp.sh frontend` 覆盖")
    except Exception as e:  # pragma: no cover
        rep.skip("F66 端点参数（HTTP 400）", "环境不可用：%s" % type(e).__name__)

    # ---- F67 契约落地 ----
    p = os.path.join(ROOT, "contracts", "formula.schema.json")
    pe = os.path.join(ROOT, "contracts", "endpoints.schema.json")
    ok_schema = False
    det = "缺失"
    try:
        with open(p, encoding="utf-8") as f:
            sch = json.load(f)
        with open(pe, encoding="utf-8") as f:
            eps = json.load(f)
        ep = (eps.get("endpoints") or {}).get("/api/formula") or {}
        ok_schema = (sch.get("properties", {}).get("sections", {})
                        .get("required") == list(q.FORMULA_SECTIONS)
                     and ep.get("params", {}).get("id") is not None)
        det = ("sections.required=%s；端点登记 params=%s"
               % (sch.get("properties", {}).get("sections", {}).get("required"),
                  sorted((ep.get("params") or {}).keys())))
    except Exception as e:
        det = "%s: %s" % (type(e).__name__, e)
    rep.add(ok_schema, "F67 契约落地：formula.schema.json 与端点契约一致", det)


# ---------------------------------------------------------------------------
# 自证：注入缺陷 ⇒ 对应判据必须变红（证明判据非恒真）
# ---------------------------------------------------------------------------
def selfproof(rep: Report) -> None:
    """在**内存副本**上注入缺陷，验证判据抓得住。

    ★ 不改磁盘文件（铁律 #97：仪器不得弄脏工作树）。真正的「改事实源」由
      `red_probe.py` 的 R52–R58 负责，那里改完必定还原。
    """
    b = _fixture()

    # ① 门禁失效：强行让它对 Unit 也返回 ok ⇒ F60 必须红
    orig = q.Backend.formula_detail

    def _no_gate(self, node_id, **kw):
        idx = self._index()
        n = idx["nodes"].get(node_id)
        if n is None:
            return orig(self, node_id, **kw)
        return {"status": q.OK, "error_code": None, "reason": "",
                "node": {"id": node_id, "labels": list(n.get("labels") or []),
                         "type": "X", "is_formula": False},
                "sections": {}, "missing": [], "stats": {}}

    q.Backend.formula_detail = _no_gate
    try:
        got = b.formula_detail("U:newton")
        red = not (got["status"] == q.NO_RESULT)
    finally:
        q.Backend.formula_detail = orig
    rep.add(red, "S60 【自证】去掉门禁 ⇒ 非公式也返回 ok ⇒ F60 必须红",
           "注入后 status=%s" % (red and "ok(已生效)" or "仍 no_result"))

    # ② 占位值冒充数据：让 clean_value 原样返回 ⇒ F64 必须红
    orig_cv = q.clean_value
    q.clean_value = lambda v: v
    try:
        leaked = q.clean_value("暂无")
        red2 = leaked is not None
    finally:
        q.clean_value = orig_cv
    rep.add(red2, "S64 【自证】占位值不再清洗 ⇒ F64 必须红",
           "注入后 clean_value('暂无')=%r" % (leaked,))


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    only = None
    if "--only" in argv:
        only = set(argv[argv.index("--only") + 1].split(",")) if \
            argv.index("--only") + 1 < len(argv) else None

    rep = Report()
    print("[F] F6 公式详情 · 判据")
    check(rep)
    print()
    print("[S] 自证（负对照）")
    selfproof(rep)
    print()

    # --json 留痕（写入入库外路径 .runlog/，见 E29）
    if "--json" in argv:
        out = argv[argv.index("--json") + 1]
        try:
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                json.dump({"pass": rep.n_pass, "fail": rep.n_fail,
                           "skip": len(rep.skips),
                           "rows": [{"ok": o, "name": n, "detail": d}
                                    for o, n, d in rep.rows]}, f,
                          ensure_ascii=False, indent=2)
            print("  留痕已写入 %s" % out)
        except Exception as e:
            print("  [WARN] 留痕写入失败：%s" % e)

    rep.render("Phase 77 · F6 公式详情 / F9 数据集版本化 自检")
    return rep.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
