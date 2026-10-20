#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""verify_release.py —— 发布校验器（fail-closed）（Phase 37 · GPT6 复评 R06/R07）

复评的 R06 完成定义是「**第三人从文档独立运行，页面和查询属于同一数据版本**」，
R07 要求「主图缺失、必要依赖缺失、关键 SKIP 或对账失败返回非零」。

本脚本把这些变成**机器可判的断言**，任一条不成立即非零退出：

  A 产物完整性      manifest 里每个样例产物的 sha256 / 字节数 / 计数必须对得上
  A4-A6 许可合规    manifest 声明的 profile 必须是 public_demo；**独立重算**样例内每条边/
                    每个节点的来源血缘闭包（`source_registry.redistribution_closure`），
                    `redistributable` 之外一律违规（0 违规才 PASS）。
                    ★ 复评 A03：原先打包器**不看许可**（`make_sample` 无白名单）。
  B 双投影同版本    sample_normalized.json 与 sample_graph.json 的节点/边 **id 集合逐元素相等**
  C 图卫生          无悬空端点 / 无重复 id / 无自环（样例内部）
  D 查询契约        sample_queries.json 每条查询在样例上执行通过
  E 查询后端就绪    真正用 03_知识层/queries.py 的 NetworkX 后端加载样例，计数与页面一致
  F 反例回归        frozen_gate 在样例上跑**自包含子集**（--release）：PASS 全过 / FAIL 0 / SKIP 0
  G 依赖           必要依赖缺件 → 非零；版本漂移 → 仅报告（不阻塞）
  H 完整快照        --full 时校验（需本地存在不入库的完整快照）

用法
----
    python 06_PoC/release/verify_release.py                # 校验离线样例发布包
    python 06_PoC/release/verify_release.py --full         # 追加完整快照校验
    python 06_PoC/release/verify_release.py --json         # 机器可读结果
退出码：0 全过 ／ 1 断言失败 ／ 2 必要依赖缺件 ／ 3 manifest 缺失或不可读
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
POC = os.path.dirname(HERE)
ROOT = os.path.dirname(POC)
sys.path.insert(0, POC)
sys.path.insert(0, os.path.join(ROOT, "03_知识层"))
sys.path.insert(0, os.path.join(ROOT, "04_验证闭环"))
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))

import graph_export as gx                                                        # noqa: E402
import source_registry as sr                                                     # noqa: E402
import interp_note as IN                                                          # noqa: E402

MANIFEST = os.path.join(HERE, "manifest.json")
SAMPLE_DIR = os.path.join(HERE, "sample")

LEVEL_RANK = {"unverified": 0, "model_inferred": 1, "source_asserted": 2,
              "by_construction": 3, "rule_checked": 4, "cross_source": 5, "human_reviewed": 6}

REQUIRED_MODULES = ("networkx", "sympy", "pint", "rdkit")
REQUIRED_LOCAL = ("verification_model", "verification_loop", "dimension_table",
                  "graph_export", "source_registry")
SAMPLE_KINDS = ("dim_table,gate_chem,gate_math,pq_quantity_gate,attrib_check,level_check,"
                "dim_field_semantics,dim_consistency,src_registered,lineage_acyclic")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Report:
    def __init__(self):
        self.checks = []
        self.hard_fail = 0
        self.warns = 0

    def add(self, ok, name, detail="", warn_only=False):
        status = "PASS" if ok else ("WARN" if warn_only else "FAIL")
        if not ok and not warn_only:
            self.hard_fail += 1
        if not ok and warn_only:
            self.warns += 1
        self.checks.append({"name": name, "status": status, "detail": detail})
        mark = {"PASS": "✅", "WARN": "⚠", "FAIL": "❌"}[status]
        print("%s %-34s %s" % (mark, name, detail))

    def payload(self):
        return {"fail": self.hard_fail, "warn": self.warns, "checks": self.checks}


# ---------------------------------------------------------------------------
def check_manifest(rep, manifest):
    arts = (manifest.get("artifacts") or {}).get("sample") or {}
    ok = bool(arts)
    rep.add(ok, "A0 manifest 含样例产物", "样例条目 %d 个" % len(arts))
    return arts


_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?$")


def check_release_meta(rep, manifest):
    """A0b–A0d 版本元数据**三处独立来源**交叉一致（Phase 66 · RC1）。

    ★ 为什么这条断言必须存在：`release.phase` 曾**硬编码 37**，连续漂移 28 个开发阶段
      无人发现 —— 因为本脚本**从来不读它**。没有判据的字段不是"低优先级"，
      是**必然会说谎的字段**（通律③：判据范围宽一格就等于恒真）。
    ★ 为什么是"三处"而不是"两处"：只比 `manifest` 与 `VERSION` 会留下"一起改"的缝；
      再加**产物文件名**（`graph_data_phaseN.json`）这一处**由构建产生、不随改字而变**的证据，
      才能把「清单里写的阶段」与「数据实际由哪个阶段导出」钉在一起。
    ★ 判据一律用**绝对量**（逐字相等 / 整数相等），不用增量：
      增量在二次运行会假警报（通律②）。
    """
    rel = manifest.get("release") or {}
    vpath = os.path.join(ROOT, "VERSION")
    if not os.path.exists(vpath):
        # ★ 不是 SKIP：VERSION 是发布必备元数据，缺了就是不能发布（fail-closed）
        rep.add(False, "A0b 版本号与 VERSION 一致",
                "缺少 %s（发布必备；缺失不得降级为 WARN）" % os.path.relpath(vpath, ROOT))
        return
    raw = open(vpath, encoding="utf-8").read().strip()
    m = _VERSION_RE.match(raw)
    if not m:
        rep.add(False, "A0b 版本号与 VERSION 一致",
                "VERSION 内容 `%s` 不符合 `MAJOR.MINOR.PATCH[-PRE]`" % raw)
        return
    got = rel.get("version")
    rep.add(got == raw, "A0b 版本号与 VERSION 一致",
            "manifest %s ／ VERSION %s" % (got, raw))

    minor = int(m.group(2))
    rep.add(rel.get("phase") == minor, "A0c 开发阶段 == VERSION 的 MINOR",
            "manifest.phase=%s ／ VERSION.%s ⇒ %d"
            % (rel.get("phase"), raw, minor))

    snap = ((manifest.get("artifacts") or {}).get("full") or {}).get("snapshot") or {}
    spath = snap.get("path") or ""
    fm = re.search(r"phase(\d+)\.json$", os.path.basename(spath))
    if not spath:
        # 未登记快照 ⇒ **本段测不到**（干净 clone 上属正常）：不写 PASS 冒充测过
        #   （铁律 #20：SKIP ≠ PASS）。快照是否存在由 H1/H1b 负责，不在这里重复判定。
        return
    if not fm:
        rep.add(False, "A0d 快照阶段与清单一致",
                "快照文件名 `%s` 不含 phaseN，无法交叉验证" % os.path.basename(spath))
        return
    real = int(fm.group(1))
    rep.add(rel.get("snapshot_phase") == real, "A0d 快照阶段与清单一致",
            "manifest.snapshot_phase=%s ／ 文件名 phase%d"
            % (rel.get("snapshot_phase"), real))


def check_artifacts(rep, arts):
    for fn, info in sorted(arts.items()):
        p = os.path.join(SAMPLE_DIR, fn)
        if not os.path.exists(p):
            rep.add(False, "A1 产物存在 %s" % fn, "缺失")
            continue
        rep.add(True, "A1 产物存在 %s" % fn, "%.1f KB" % (os.path.getsize(p) / 1024.0))
        got = sha256_file(p)
        rep.add(got == info.get("sha256"), "A2 sha256 %s" % fn,
                "%s%s" % (got[:16], "" if got == info.get("sha256") else " ≠ manifest " + str(info.get("sha256"))[:16]))


def load_sample():
    p = os.path.join(SAMPLE_DIR, "sample_normalized.json")
    with open(p, encoding="utf-8") as f:
        norm = json.load(f)
    p2 = os.path.join(SAMPLE_DIR, "sample_graph.json")
    with open(p2, encoding="utf-8") as f:
        view = json.load(f)
    return norm, view


def check_counts(rep, arts, norm, view):
    exp = arts.get("sample_normalized.json") or {}
    rep.add(len(norm["nodes"]) == exp.get("nodes"), "A3 节点计数",
            "%d（manifest %s）" % (len(norm["nodes"]), exp.get("nodes")))
    rep.add(len(norm["edges"]) == exp.get("edges"), "A3 边计数",
            "%d（manifest %s）" % (len(norm["edges"]), exp.get("edges")))
    vexp = (arts.get("sample_graph.json") or {})
    rep.add(len(view["nodes"]) == vexp.get("nodes") and len(view["edges"]) == vexp.get("edges"),
            "A3 投影计数", "节点 %d / 边 %d" % (len(view["nodes"]), len(view["edges"])))


def check_same_version(rep, norm, view):
    n1 = {n["id"] for n in norm["nodes"]}
    n2 = {n["id"] for n in view["nodes"]}
    e1 = {e["id"] for e in norm["edges"]}
    e2 = {e["id"] for e in view["edges"]}
    rep.add(n1 == n2, "B1 页面↔查询 节点 id 集合相等",
            "正常 %d / 投影 %d / 差 %d" % (len(n1), len(n2), len(n1 ^ n2)))
    rep.add(e1 == e2, "B1 页面↔查询 边 id 集合相等",
            "正常 %d / 投影 %d / 差 %d" % (len(e1), len(e2), len(e1 ^ e2)))


def check_hygiene(rep, norm, view):
    nid = [n["id"] for n in norm["nodes"]]
    eid = [e["id"] for e in norm["edges"]]
    known = set(nid)
    dup_n = [k for k, v in collections.Counter(nid).items() if v > 1]
    dup_e = [k for k, v in collections.Counter(eid).items() if v > 1]
    rep.add(not dup_n, "C1 无重复节点 id", "重复 %d" % len(dup_n))
    rep.add(not dup_e, "C1 无重复边 id", "重复 %d" % len(dup_e))
    dang = [e["id"] for e in norm["edges"] if e["source"] not in known or e["target"] not in known]
    rep.add(not dang, "C2 无悬空端点", "悬空 %d" % len(dang))
    loop = [e["id"] for e in norm["edges"] if e["source"] == e["target"]]
    rep.add(not loop, "C2 无自环", "自环 %d" % len(loop))
    # 投影侧
    vknown = {n["id"] for n in view["nodes"]}
    vdang = [e["id"] for e in view["edges"] if e["source"] not in vknown or e["target"] not in vknown]
    rep.add(not vdang, "C2 投影无悬空端点", "悬空 %d" % len(vdang))


def check_license(rep, manifest, norm):
    """A4-A6 许可合规（Phase 42 · A03：许可进打包）。

    ★ 铁律 #64：许可政策必须由程序执行 —— 打包时不过滤、校验时不复查，等于没有政策。
    ★ 铁律 #14 / #45：**独立重算**，不听构建器自述 —— 直接从样例产物重算来源血缘闭包。
    ★ fail-closed：缺 `props.source` 或未登记来源 ⇒ 视为不可再分发（不猜测 —— 铁律 #60）。
    """
    lic = manifest.get("license") or {}
    prof = lic.get("profile")
    rep.add(prof == "public_demo", "A4 许可 profile",
            "manifest profile=%s（发布样例必须 public_demo）" % prof)

    nodes = norm.get("nodes") or []
    edges = norm.get("edges") or []
    nsrc = {n["id"]: (n.get("props") or {}).get("source") for n in nodes}
    bad = []
    for e in edges:
        s = (e.get("props") or {}).get("source")
        if not (s and sr.is_redistributable(s)):
            bad.append(("edge", e.get("id"), s or "<none>"))
        for nid in (e["source"], e["target"]):
            t = nsrc.get(nid)
            if not (t and sr.is_redistributable(t)):
                bad.append(("node", nid, t or "<none>"))
    rep.add(not bad, "A5 许可合规（独立重算）",
            "样例 %d 节点 / %d 边 ｜ 违规 %d%s"
            % (len(nodes), len(edges), len(bad),
               "（来源闭包全 = redistributable）" if not bad else "：%s" % bad[:3]))

    ex_e, ex_n = lic.get("excluded_edges"), lic.get("excluded_nodes")
    rep.add(isinstance(ex_e, int) and isinstance(ex_n, int) and ex_e > 0,
            "A6 许可剔除已登记且非真空",
            "剔除 %s 边 / %s 节点（0 边 ⇒ 白名单形同虚设）" % (ex_e, ex_n))


# ★ Phase 55（D58）：`interp_note()` / `dep_detail()` 的**实现已上移到
#   `06_PoC/interp_note.py`**（唯一实现，#88/#92）。Phase 53 把实现留在本文件、
#   把 `frozen_gate` 登记为「同族未改」⇒ 链上出现**两份**同类实现，而没有任何东西
#   会提醒"第 6 个入口又漏了"。现在全链只有一份实现 + 一份入口声明（`ENTRY_POINTS`），
#   由 `bash sttp.sh interpcheck` 静态核对。本文件只**消费**，不另写实现。
interp_note = IN.note                    # 保留本地名 ⇒ 调用点与断言名一字不改（铁律 #74）
dep_detail = IN.dep_detail


def check_deps(rep, release):
    missing_mods, missing_local = [], []
    for m in REQUIRED_MODULES:
        try:
            importlib.import_module(m)
        except Exception:                                                        # noqa: BLE001
            missing_mods.append(m)
    for m in REQUIRED_LOCAL:
        try:
            importlib.import_module(m)
        except Exception:                                                        # noqa: BLE001
            missing_local.append(m)
    missing = missing_mods + missing_local
    rep.add(not missing, "G1 必要依赖就绪", dep_detail(missing))
    if missing and release:
        rep.add(False, "G2 release 模式要求依赖齐全",
                "缺 %s · %s" % (missing, interp_note()))
    # ★ D57 的回归保护：缺依赖诊断**必须自述解释器**。
    #   注意本断言用**合成缺失表**触发"缺件分支"—— 绿树下该分支永不执行，
    #   所以它是这条纪律**唯一常驻**的守卫（G1 的 detail 只在真缺件时才被读到）。
    #   其**可红性**（把解释器从 detail 里删掉 ⇒ 必须变红）由
    #   `bash sttp.sh redprobe` 的 R4 探针构造性证明（铁律 #100）。
    rep.add(sys.executable in dep_detail(["__probe_missing__"]),
            "G3 缺依赖诊断自述解释器（D57）：合成缺件分支的 detail 须含 sys.executable",
            interp_note())
    # ★ Phase 55（D58 收口）：同一条纪律的另一半 —— `skip_detail()`（"某一步**没有真正
    #   执行**，原因是缺依赖/前置"）也必须自述解释器。它服务的是 `frozen_gate` 的
    #   「门禁依赖缺失」SKIP：Phase 53 只修 G1/G2，那一处当时被登记为「同族未改」（D58）。
    #   ★ 这里**刻意不抽成 `guard_interp(rep, NAME)` 这种包装**（第一版就是那么写的，已被
    #     本轮的碰撞/采集检测抓出来）：采集器认的是**调用形状** `rep.add(cond, 字面量名, …)`,
    #     包一层之后 G4 在 `bash sttp.sh names` 里**完全隐形**（铁律 #98 的第二次踩坑）。
    _ok4, _detail4 = IN.guard()
    rep.add(_ok4, "G4 门禁缺依赖 SKIP 自述解释器（D58）：合成缺件分支的 skip_detail 须含 sys.executable",
            _detail4)
    return missing


def run_queries(rep, norm, view):
    qp = os.path.join(SAMPLE_DIR, "sample_queries.json")
    qs = json.load(open(qp, encoding="utf-8"))["queries"]
    node_by_id = {n["id"]: n for n in norm["nodes"]}
    subj = {nid: gx.subject_of((n.get("props") or {}).get("domain"), str(nid))
            for nid, n in node_by_id.items()}

    # 查询后端（真正走 03_知识层/queries.py）
    backend = None
    try:
        import queries                                                           # type: ignore
        backend = queries.get_backend("nx", input_path=os.path.join(SAMPLE_DIR, "sample_normalized.json"))
    except BaseException as e:                                                   # noqa: BLE001
        print("  [WARN] 查询后端初始化失败：%s: %s" % (type(e).__name__, e))

    rep.add(backend is not None and backend.node_count == len(norm["nodes"])
            and backend.edge_count == len(norm["edges"]),
            "E1 查询后端就绪且计数一致",
            ("节点 %d / 边 %d" % (backend.node_count, backend.edge_count)) if backend
            else "后端不可用")

    all_ok = True
    for q in qs:
        kind, exp, ok, detail = q["kind"], q.get("expect") or {}, True, ""
        try:
            if kind == "stats":
                ok = len(norm["nodes"]) == exp["nodes"] and len(norm["edges"]) == exp["edges"]
                detail = "%d/%d" % (len(norm["nodes"]), len(norm["edges"]))
            elif kind == "cross_domain_count_min":
                n = sum(1 for e in norm["edges"]
                        if subj.get(e["source"]) and subj.get(e["target"])
                        and subj[e["source"]] != subj[e["target"]])
                ok = n >= exp["min"] and (exp.get("exact") is None or n == exp["exact"])
                detail = "跨学科边 %d（期望 ≥%d%s）" % (n, exp["min"],
                                                       "" if exp.get("exact") is None else " 且 ==%d" % exp["exact"])
            elif kind == "neighbors_min":
                if backend is None:
                    ok, detail = False, "后端不可用"
                else:
                    nb = backend.get_neighbors(q["node"])
                    ok = len(nb) >= exp["min_degree"]
                    detail = "%s 邻居 %d（期望 ≥%d）" % (q["node"], len(nb), exp["min_degree"])
            elif kind == "path_exists":
                if backend is None:
                    ok, detail = False, "后端不可用"
                else:
                    paths = backend.paths_between(q["src"], q["dst"])
                    ok = len(paths) >= exp["min_paths"]
                    detail = "%s→%s 路径 %d（期望 ≥%d）" % (q["src"], q["dst"], len(paths), exp["min_paths"])
            elif kind == "edge_evidence":
                e = next((x for x in norm["edges"] if x["id"] == q["edge_id"]), None)
                if e is None:
                    ok, detail = False, "找不到边 %s" % q["edge_id"]
                else:
                    lv = (e.get("props") or {}).get("verification_level")
                    ev = (e.get("props") or {}).get("verification_evidence") or []
                    ok = LEVEL_RANK.get(lv, -1) >= exp["min_level_rank"] and \
                        (not exp.get("require_evidence") or len(ev) > 0)
                    detail = "level=%s 证据 %d 条" % (lv, len(ev))
            else:
                ok, detail = False, "未知 kind"
        except Exception as e:                                                   # noqa: BLE001
            ok, detail = False, "%s: %s" % (type(e).__name__, e)
        rep.add(ok, "D1 查询 %s" % q["id"], detail)
        all_ok = all_ok and ok
    return all_ok


def run_frozen_gate(rep, release):
    cmd = [sys.executable, os.path.join(POC, "frozen_gate.py"),
           "--graph", os.path.join(SAMPLE_DIR, "sample_normalized.json"),
           "--kinds", SAMPLE_KINDS, "--json"]
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900)
        data = json.loads(r.stdout)
        t = data.get("tally") or {}
        ok = (t.get("FAIL", 0) == 0) and (t.get("SKIP", 0) == 0) and (t.get("PASS", 0) > 0)
        rep.add(ok, "F1 样例反例回归（自包含子集）",
                "PASS %d / FAIL %d / SKIP %d"
                % (t.get("PASS", 0), t.get("FAIL", 0), t.get("SKIP", 0)))
        return ok
    except Exception as e:                                                       # noqa: BLE001
        rep.add(False, "F1 样例反例回归（自包含子集）", "执行失败：%s: %s" % (type(e).__name__, e))
        return False


def check_full(rep, manifest, release=False):
    """H 完整快照校验。

    ★ 复评 A01b：原先只核「前端展示快照」的 sha256，**不核规范图（权威图）**，
      且缺失一律记为 WARN ⇒ 在 `--release` 下形成 fail-open。
      本版：① 增 `H1b 规范图 sha256`；② `--release` 下所有缺失/不一致一律 FAIL（不再 WARN）。
    """
    arts = (manifest.get("artifacts") or {}).get("full") or {}

    # ---- H1 前端展示快照 ----
    snap = arts.get("snapshot")
    if not snap:
        rep.add(False, "H1 完整快照已登记", "manifest 无 full.snapshot（--no-stats 生成？）",
                warn_only=not release)
    else:
        p = os.path.join(ROOT, snap["path"])
        if not os.path.exists(p):
            rep.add(False, "H1 完整快照存在",
                    "本地缺失（不入库属正常）；获取方式：%s" % snap.get("obtain"),
                    warn_only=not release)
        else:
            got = sha256_file(p)
            rep.add(got == snap["sha256"], "H1 完整快照 sha256", got[:16])

    # ---- H1b 规范图（权威图）★ 复评 A01b 缺口 ----
    nrm = arts.get("normalized")
    if not nrm:
        rep.add(False, "H1b 规范图已登记", "manifest 无 full.normalized", warn_only=not release)
    else:
        p = os.path.join(ROOT, nrm["path"])
        if not os.path.exists(p):
            rep.add(False, "H1b 规范图存在",
                    "本地缺失（不入库属正常）：%s" % nrm["path"], warn_only=not release)
        else:
            got = sha256_file(p)
            rep.add(got == nrm["sha256"], "H1b 规范图 sha256", got[:16])

    # ---- H2 北极星现算 == manifest ----
    exp = ((manifest.get("expected_stats") or {}).get("full_snapshot")) or {}
    import tempfile
    tmp = tempfile.mkdtemp(prefix="sttp_verify_")
    # ★ E9：本脚本原先**从不清理**该临时目录（实测泄漏 24 个 `sttp_verify_*`）。
    #   `sr.rmtree_force` 先清只读位再删（Windows 的 git loose object 是只读的），失败即抛 ——
    #   与铁律 #62 fail-closed 一致，不再靠 `ignore_errors=True` 假装清理成功。
    try:
        ap = os.path.join(tmp, "astro.json")
        subprocess.run([sys.executable, os.path.join(POC, "task_trust_audit.py"),
                        "--graph", os.path.join(POC, "etl", "normalized.json"), "--json", ap],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=1800)
        if os.path.exists(ap):
            now = json.load(open(ap, encoding="utf-8"))
            old = exp.get("task_trust") or {}
            rep.add(now.get("north_star_pct") == old.get("north_star_pct"),
                    "H2 北极星现算 == manifest",
                    "现算 %s / manifest %s" % (now.get("north_star_pct"), old.get("north_star_pct")))
        else:
            rep.add(False, "H2 北极星现算", "仪器未产出结果（规范图缺失？）", warn_only=not release)
    finally:
        try:
            sr.rmtree_force(tmp)
        except OSError as _e:                                              # noqa: BLE001
            rep.add(False, "H2 临时目录清理", "清理失败（不静默）：%s" % _e)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="STTP 发布校验器（Phase 37 R06/R07）")
    ap.add_argument("--full", action="store_true", help="追加完整快照校验")
    ap.add_argument("--release", action="store_true", help="fail-closed：缺依赖/缺主图亦非零")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    print("=" * 78)
    print("  STTP 发布校验（Phase 37 · R06/R07 · fail-closed）")
    print("=" * 78)

    if not os.path.exists(MANIFEST):
        print("[ERROR] manifest 缺失：%s（先跑 make_manifest.py）" % MANIFEST)
        return 3
    try:
        manifest = json.load(open(MANIFEST, encoding="utf-8"))
    except Exception as e:                                                       # noqa: BLE001
        print("[ERROR] manifest 不可读：%s" % e)
        return 3

    rep = Report()
    arts = check_manifest(rep, manifest)
    check_release_meta(rep, manifest)      # ★ A0b–A0d：VERSION / manifest / 快照文件名 三处交叉
    check_artifacts(rep, arts)
    norm, view = load_sample()
    check_counts(rep, arts, norm, view)
    check_same_version(rep, norm, view)
    check_hygiene(rep, norm, view)
    check_license(rep, manifest, norm)
    missing = check_deps(rep, a.release)
    run_queries(rep, norm, view)
    run_frozen_gate(rep, a.release)
    if a.full:
        check_full(rep, manifest, release=a.release)

    print("-" * 78)
    verdict = "✅ 发布校验通过" if rep.hard_fail == 0 else "❌ 发布校验失败（%d 条断言不成立）" % rep.hard_fail
    print("  %s ｜ 断言 %d 条 ｜ FAIL %d ｜ WARN %d"
          % (verdict, len(rep.checks), rep.hard_fail, rep.warns))
    print("=" * 78)

    if a.json:
        print(json.dumps(rep.payload(), ensure_ascii=False, indent=2))

    if rep.hard_fail:
        return 1
    if missing and a.release:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
