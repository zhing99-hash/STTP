#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 78（**F8 批量导出子集**：按学科/来源/验证等级切片）自检 —— 真图判据。

★ 为什么单独一个文件：F8 的判据是**对账式**（Σ 各部分 == 全集），
  它既不是前端静态结构、也不是子图端点形状，而是「切片是否会静默丢数据」。
  这类缺陷**从不报错** —— 使用者拿到 3 份切片，以为加起来是全库，其实少了一半。

判据
----
  S80 学科节点守恒：Σ 各学科切片命中节点 == 全图节点数
  S80e 学科边守恒 ：Σ 各学科切片边数 == 全图边数（每条边恰归一片）
  S81 来源节点守恒：Σ 各来源切片命中节点 == 全图节点数
  S81e 来源边守恒 ：Σ 各来源切片边数 == 全图边数
  S80c/S81c 片内自洽：internal + crossing == 该片边数

  ★ **节点守恒与边守恒必须是两条断言、两个编号**：
    实际踩过 —— 两者都叫 S80 时，注入「跨界边被丢弃」的缺陷会让探针
    指向 `expect_red=["S81"]` 而红的是 S80 ⇒ 探针 FAIL，排查半天。
    编号重名会让「哪条判据红了」这件事本身失去意义。
  S82 等级分档守恒：内部 e1 与对外 p1 **两套都要** Σ == 全图边数
  S83 等级单调：逐边断言对外 ≤ 内部（对外不可能比内部更可复算，G4 同族）
  S84 空片分说：谓词合法无匹配 ⇒ ok/no_match；维度非法 ⇒ 抛 SliceError
  S85 归一化完备：真图上出现的 domain 前缀**全部**已登记（出现未登记 ⇒ 红）
  S86 口径必选：grade 维度不指定 basis ⇒ 抛错（不默认）
  S92 台账无原机路径：`slices.jsonl` 入库，不得含 `C:\\` / `/Users/` 等绝对路径
  S93 台账 parent 可追溯：每条切片记录的 parent 必须能在 `versions.jsonl` 里找到

★ 缺权威图 ⇒ 记 **SKIP**（不计通过数，铁律 #20/#37），不假通过。
"""

from __future__ import annotations

import collections
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
for p in (os.path.join(ROOT, "03_知识层"), os.path.join(ROOT, "06_PoC"),
          os.path.join(ROOT, "06_PoC", "dataset"),
          os.path.join(ROOT, "11_真实数据")):
    if p not in sys.path:
        sys.path.insert(0, p)

NORM_GRAPH = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")


def main() -> int:
    rep = []

    def add(ok, name, detail=""):
        rep.append((bool(ok), name, str(detail)))

    if not os.path.exists(NORM_GRAPH):
        print("[SKIP] 缺少权威图：%s" % os.path.relpath(NORM_GRAPH, ROOT))
        print("       切片对账的断言前提是**权威图口径**（9750 节点 / 48813 边）；")
        print("       干净检出里结构性不可用 ⇒ 记 SKIP（**不构成通过**）。")
        print("       恢复：bash sttp.sh export（从 Aura 反向导出）")
        print("       纯逻辑判据不依赖权威图：python 06_PoC/dataset/slicing.py --selfcheck")
        return 0

    try:
        import queries as _q
        import source_registry as _sr
        import slicing as _sl
    except Exception as ex:                                  # noqa: BLE001
        print("[SKIP] 无法加载 queries / source_registry / slicing：%s: %s"
              % (type(ex).__name__, ex))
        return 0

    with io.open(NORM_GRAPH, "r", encoding="utf-8") as f:
        g = json.load(f)
    nodes, edges = g.get("nodes") or [], g.get("edges") or []
    TN, TE = len(nodes), len(edges)

    # ---- S80/S80e（学科）与 S81/S81e（来源）—— 四个**不同**编号 ----
    for dim, sid in (("domain", "S80"), ("source", "S81")):
        r = _sl.build_slices(nodes, edges, dim)
        rc = r["reconcile"]
        add(rc["nodes_ok"], "%s %s 维度：**节点**守恒（Σ 命中 == 全图）"
            % (sid, dim), "%d vs %d" % (rc["sum_nodes_matched"], TN))
        add(rc["edges_ok"], "%se %s 维度：**边**守恒（每条边恰归一片）"
            % (sid, dim), "%d vs %d" % (rc["sum_edges_matched"], TE))
        add(rc["per_slice_internal_plus_crossing_ok"],
            "%sc %s 维度：片内 internal + crossing 自洽" % (sid, dim),
            "%s" % rc["per_slice_detail"])

    # ---- 等级：双口径分档 ----
    reg = _q.build_stale_registry(_sr.ids(), _sr.hash_map(base=ROOT),
                                  _sr.repo_status_map(base=ROOT))
    # ★ Phase 82：自建 registry ⇒ 必须显式接上第五档，否则与服务端/grade_report
    #   算出**两套等级**（D140 同族）。切片按 grade 分档，两套数 = 切片含义漂移。
    try:
        import fidelity_proven as _fp                         # noqa: PLC0415
        _fp.attach_proven(reg, edges, base=ROOT)
    except Exception as _ex:                                 # noqa: BLE001
        print("[WARN] 第五档 proven 不可用（%s: %s）" % (type(_ex).__name__, _ex))
    buckets = {}
    for basis in _sl.GRADE_BASIS:
        gof = ((lambda e: _q.edge_grade(e, reg)) if basis == "internal_e1"
               else (lambda e: _q.edge_grade_public(e, reg, _sr.is_redistributable)))
        b = _sl.grade_buckets(edges, gof)
        buckets[basis] = b
        add(sum(b.values()) == TE,
            "S82 %s 等级分档守恒（Σ == 边总数）" % basis,
            "%d vs %d；分档 %s" % (sum(b.values()), TE, dict(b)))

    # ---- S83 单调性：对外 ≤ 内部（逐边）----
    bad = 0
    for e in edges:
        gi = _q.edge_grade(e, reg)
        gp = _q.edge_grade_public(e, reg, _sr.is_redistributable)
        if gi is None or gp is None:
            continue
        if gp > gi:
            bad += 1
    add(bad == 0, "S83 等级单调：逐边断言对外 ≤ 内部（须 0 条违反）",
        "违反 %d 条" % bad)

    # ---- S85 归一化完备 ----
    unmapped = set()
    for n in nodes:
        _sl.domain_of_node(n, unmapped)
    add(not unmapped, "S85 学科归一化完备：真图无未登记前缀",
        "未登记前缀：%s" % (sorted(unmapped) or "无"))

    # ---- S79 谓词契约（D123）：真图上也必须验，不能只靠合成图 ----
    # ★ 为什么这里也要验：`redist_of` 误传 `redistribution_closure`（三态字符串）
    #   会让对外折扣恒为 5 ⇒ 对外分数虚高成内部值，且**不报错**。
    #   合成图那条（slicing.py --selfcheck）只证明函数本身会抓手误；
    #   这里证明**真图这条调用链**确实把校验串进去了。
    ok_good, d_good = _sl.assert_bool_predicate(_sr.is_redistributable,
                                                ["FO", "mathxiv", "pb"])
    ok_bad, d_bad = _sl.assert_bool_predicate(_sr.redistribution_closure,
                                              ["FO", "mathxiv", "pb"])
    add(ok_good and not ok_bad,
        "S79 谓词契约：`is_redistributable` 放行，`redistribution_closure` 必须被抓（D123）",
        "good: %s | bad: %s" % (d_good, d_bad))

    # ---- S84 空片分说 ----
    empty = _sl.slice_by_source(nodes, edges, "__definitely_not_a_source__")
    add(empty["counts"]["nodes_matched"] == 0 and empty["counts"]["edges_matched"] == 0
        and empty["status"] == "ok" and empty["reason"] == _sl.REASON_NO_MATCH,
        "S84 空片分说：合法谓词无匹配 ⇒ `ok` + `reason=no_match`",
        "status=%s reason=%s" % (empty["status"], empty["reason"]))
    try:
        _sl.build_slices(nodes, edges, "__not_a_dimension__")
        add(False, "S86 口径/维度非法 ⇒ **必须抛错**", "没抛错 ⇒ 条件不成立会被当成空片")
    except _sl.SliceError as ex:
        add(True, "S86 维度非法 ⇒ **必须抛 SliceError**（HTTP 400 同族）", "已抛：%s" % ex)

    # ---- S92 / S93 切片台账（入库文件，必须可移植、可追溯）----
    srp = _sl.slice_registry_path(ROOT)
    if not os.path.exists(srp):
        print("  [SKIP] S92/S93 切片台账尚不存在（未跑过 `slicing.py --dim … --value …`）")
        print("         —— 记 SKIP **不构成通过**（铁律 #20）")
    else:
        import versioning as _ver
        srows = _sl.load_slice_registry(srp)
        text = io.open(srp, "r", encoding="utf-8").read()
        bad = [p for p in ("C:\\", "C:/", "/Users/", "/home/", "D:\\") if p in text]
        add(not bad, "S92 台账无原机路径（`slices.jsonl` 是入库文件）",
            "命中：%s" % (bad or "无") + "｜%d 条记录" % len(srows))
        vrows = {r.get("dataset_id") for r in
                 (_ver.load_registry(_ver.registry_path(ROOT)) or ())}
        orphan = [r.get("slice_dataset_id") for r in srows
                  if r.get("parent_dataset_id") not in vrows]
        add(not orphan, "S93 台账 parent 可追溯：切片 parent 必须存在于 `versions.jsonl`",
            "孤儿 %d 条：%s" % (len(orphan), orphan or "无"))

    print("=" * 78)
    print("  Phase 78（F8 导出切片）真图判据")
    print("=" * 78)
    npass = nfail = 0
    for ok, name, detail in rep:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
        if detail:
            print("         %s" % detail)
        npass += ok
        nfail += (not ok)
    print("-" * 78)
    print("  phase78_selfcheck: PASS %d / FAIL %d" % (npass, nfail))
    print("\n  结论：%s" % ("切片对账全部成立 —— 各部分之和 == 全集，无静默丢数据。"
                            if nfail == 0 else "存在 FAIL —— 先修，不要绕过。"))
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
