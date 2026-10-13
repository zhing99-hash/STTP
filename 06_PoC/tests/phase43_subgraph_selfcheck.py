# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""
Phase 43 · 局部图谱查询 **非真空自检**（V1–V16）
==================================================================

目的
----
把 `00_项目管理/前端局部图谱浏览方案_20261013.md` §9 的验收表变成**可执行的仪器**，
并对每条断言同时给出「正例通过」与「注入反例」两条证据（铁律 #14 / #43）。

做法
----
1. 在**进程内**起一个真 HTTP 服务（`viz_server.VizHandler`，端口 0 自动分配）
   —— 断言的对象是**响应体**，不是内部函数返回值。
2. 用**零依赖**的 JSON-Schema 子集校验器跑 `contracts/*.schema.json`
   （铁律 #64：政策必须由程序执行）。
3. 对 V2–V5 / V10–V16：先断言正例，再**注入反例**断言其必然变红。
4. V16（stale 判据非真空）按铁律 #61 做**负对照**：改动一个被登记来源文件 →
   相关边判 stale → **逐字节还原**并复核（`finally` 保证必还原）。

覆盖说明（**如实披露，不假装覆盖**）
------------------------------------
* V8（五态 UI 颜色）/ V9（`mode=bundle` 发布样例链路）/ V1 的前端侧（首屏无全图 fetch）
  属**步骤 B（前端）**，本脚本只做后端侧代理断言，并在报告里标为"未覆盖"。
* Neo4j 后端在本环境不可达 ⇒ 相关契约测试记 **SKIP**（**不计入通过数**；铁律 #20/#37）。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))        # 06_PoC/tests
POC = os.path.dirname(HERE)                              # 06_PoC
ROOT = os.path.dirname(POC)                              # STTP
KNOWLEDGE = os.path.join(ROOT, "03_知识层")
CONTRACTS = os.path.join(ROOT, "contracts")
SECRET = "phase43-selfcheck-secret"


# ---------------------------------------------------------------------------
# 零依赖 JSON-Schema 子集校验器
# ---------------------------------------------------------------------------
def _is_type(v, t: str) -> bool:
    if t == "object":
        return isinstance(v, dict)
    if t == "array":
        return isinstance(v, list)
    if t == "string":
        return isinstance(v, str)
    if t == "boolean":
        return isinstance(v, bool)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "null":
        return v is None
    return False


def _deref(schema, root):
    if isinstance(schema, dict) and "$ref" in schema:
        node = root
        for part in schema["$ref"][2:].split("/"):
            node = node[part]
        return node
    return schema


def validate(inst, schema, root, path="$", errs=None):
    """支持：type(含联合) / required / properties / enum / pattern / minLength /
    minimum / maximum / items / $ref。够用即止（不引第三方库）。"""
    if errs is None:
        errs = []
    schema = _deref(schema, root)
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_is_type(inst, x) for x in types):
            errs.append("%s: 类型应为 %s，实际 %s" % (path, types, type(inst).__name__))
            return errs
    if "enum" in schema and inst not in schema["enum"]:
        errs.append("%s: 取值 %r 不在 enum" % (path, inst))
        return errs
    if isinstance(inst, str):
        if "pattern" in schema and not re.search(schema["pattern"], inst):
            errs.append("%s: %r 不匹配 pattern %s" % (path, inst[:40], schema["pattern"]))
        if "minLength" in schema and len(inst) < schema["minLength"]:
            errs.append("%s: 长度 %d < %d" % (path, len(inst), schema["minLength"]))
    if isinstance(inst, (int, float)) and not isinstance(inst, bool):
        if "minimum" in schema and inst < schema["minimum"]:
            errs.append("%s: %r < minimum %r" % (path, inst, schema["minimum"]))
        if "maximum" in schema and inst > schema["maximum"]:
            errs.append("%s: %r > maximum %r" % (path, inst, schema["maximum"]))
    if isinstance(inst, dict):
        for req in schema.get("required", []) or []:
            if req not in inst:
                errs.append("%s: 缺必填字段 %s" % (path, req))
        for k, sub in (schema.get("properties") or {}).items():
            if k in inst:
                validate(inst[k], sub, root, "%s.%s" % (path, k), errs)
    if isinstance(inst, list) and "items" in schema:
        for i, x in enumerate(inst):
            validate(x, schema["items"], root, "%s[%d]" % (path, i), errs)
    return errs


def load_schema(name: str) -> dict:
    with open(os.path.join(CONTRACTS, name), "r", encoding="utf-8") as f:
        return json.load(f)


# ★ 本校验器**只实现**这些关键字。schema 里一旦出现别的关键字（anyOf/const/minItems…），
#   校验会**静默跳过** ⇒ 契约形同虚设（铁律 #14 的变体）。因此下面这把「支持集」本身要被断言。
SUPPORTED_KEYWORDS = {
    "$schema", "$id", "$defs", "$ref", "title", "description",
    "type", "enum", "pattern", "minLength", "minimum", "maximum",
    "required", "properties", "items",
}


def collect_keywords(node, out=None):
    """递归收集 schema 里出现的所有 JSON-Schema 关键字（跳过被引用的属性名）。"""
    if out is None:
        out = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("properties", "$defs"):
                for sub in (v or {}).values():
                    collect_keywords(sub, out)
            elif k == "items":
                collect_keywords(v, out)
            else:
                out.add(k)
    elif isinstance(node, list):
        for x in node:
            collect_keywords(x, out)
    return out


# ---------------------------------------------------------------------------
# 报告器
# ---------------------------------------------------------------------------
class Rep:
    def __init__(self):
        self.rows = []          # (状态 'PASS'|'FAIL'|'SKIP', 名称, 明细)
        self.notes = []

    def add(self, ok, name, detail=""):
        self.rows.append(("PASS" if ok else "FAIL", name, detail))
        print("  [%s] %-52s %s" % ("PASS" if ok else "FAIL", name, detail))
        return bool(ok)

    def skip(self, name, note):
        self.rows.append(("SKIP", name, note))
        print("  [SKIP] %-52s %s" % (name, note))

    def note(self, text):
        self.notes.append(text)
        print("  [NOTE] " + text)

    def counts(self):
        c = {"PASS": 0, "FAIL": 0, "SKIP": 0}
        for s, _, _ in self.rows:
            c[s] += 1
        return c


# ---------------------------------------------------------------------------
# HTTP / 工具
# ---------------------------------------------------------------------------
def http_get(port: int, path: str, timeout: int = 180):
    url = "http://127.0.0.1:%d%s" % (port, path)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except Exception:  # noqa: BLE001
            return e.code, {"_raw": body}


def q(**kw) -> str:
    from urllib.parse import urlencode
    return "?" + urlencode({k: str(v) for k, v in kw.items() if v is not None})


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snap_via_subprocess(path: str) -> str:
    code = ("import sys;sys.path.insert(0,%r);import queries;"
            "b=queries.get_backend('nx', input_path=%r);"
            "print(b.snapshot_id())" % (KNOWLEDGE, path))
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                         capture_output=True, text=True)
    if out.returncode != 0:
        tail = (out.stderr.strip().splitlines() or ["<no stderr>"])[-1]
        return "<ERR:%s>" % tail
    return out.stdout.strip().splitlines()[-1]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Phase 43 局部图谱查询自检（V1–V16）")
    ap.add_argument("--json", default=None, help="把报告写成 JSON（审计留痕）")
    ap.add_argument("--keep-going", action="store_true")
    args = ap.parse_args(argv)

    os.environ["STTP_EXPAND_SECRET"] = SECRET
    if POC not in sys.path:
        sys.path.insert(0, POC)
    if KNOWLEDGE not in sys.path:
        sys.path.insert(0, KNOWLEDGE)
    import viz_server as V            # noqa: E402
    import queries as Q               # noqa: E402
    import graph_export as GX         # noqa: E402

    rep = Rep()
    t_all = time.time()
    print("=" * 78)
    print("  Phase 43 · 局部图谱查询自检（V1–V16）")
    print("=" * 78)

    srv = V.ThreadingHTTPServer(("127.0.0.1", 0), V.VizHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("  HTTP 服务: 127.0.0.1:%d（真响应体断言）" % port)
    source = V._graph_path()
    print("  查询源   : %s" % os.path.relpath(source, ROOT))
    print("-" * 78)

    # ---------- 0. 契约与跨模块一致性（前置） ----------
    print("\n[0] 契约 / 口径一致性")
    r = http_get(port, "/api/subgraph" + q(seeds="MX:def:manifold", mode="ego", hops=1))[1]
    errs = validate(r, load_schema("subgraph.schema.json"),
                    load_schema("subgraph.schema.json"))
    rep.add(not errs, "C1 subgraph 响应通过 subgraph.schema.json", str(errs[:2]))
    r = http_get(port, "/api/search" + q(q="manifold", limit=3))[1]
    sch = load_schema("search.schema.json")
    rep.add(not validate(r, sch, sch), "C2 search 响应通过 search.schema.json")
    r = http_get(port, "/api/overview")[1]
    sch = load_schema("overview.schema.json")
    rep.add(not validate(r, sch, sch), "C3 overview 响应通过 overview.schema.json")
    r = http_get(port, "/api/subgraph" + q(seeds="X", mode="bogus"))[1]
    sch = load_schema("error.schema.json")
    rep.add(not validate(r, sch, sch), "C4 错误信封通过 error.schema.json")
    # 反例：抹掉 why_shown ⇒ 校验器必须报错（证明校验器不是空壳）
    bad = json.loads(json.dumps(r))
    bad["error_code"] = "NOT_A_CODE"
    rep.add(bool(validate(bad, sch, sch)), "C5 注入非法 error_code ⇒ 校验器变红",
            "（注入反例，必须报错）")

    # ★ C5b 契约不是空壳：schema 里不得出现本校验器**未实现**的关键字（否则被静默跳过）
    used = set()
    for name in ("subgraph.schema.json", "search.schema.json",
                 "overview.schema.json", "error.schema.json"):
        used |= collect_keywords(load_schema(name))
    unsupported = sorted(used - SUPPORTED_KEYWORDS)
    rep.add(not unsupported,
            "C5b 四份 schema 只用「已实现关键字」（无静默跳过）",
            "未实现=%s" % unsupported if unsupported else "%d 个关键字全部受支持" % len(used))
    if unsupported:
        rep.note("★ %s 出现在 schema 中但校验器不实现 ⇒ 这些约束**根本没被检查**，"
                 "须补实现或改写 schema。" % unsupported)

    # 跨模块等级表一致性（防 3 处表漂移）
    gx_rank = dict(GX.RANK_ORDER)
    q_rank = dict(Q._RANK)
    rep.add(gx_rank == q_rank, "C6 queries._RANK == graph_export.RANK_ORDER",
            "一致" if gx_rank == q_rank else "★漂移")
    try:
        sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
        import verification_model as VM   # noqa: E402
        vm_rank = {lv: i for i, lv in enumerate(VM.LEVELS)}
        rep.add(vm_rank == q_rank, "C7 queries._RANK == verification_model.RANK",
                "一致" if vm_rank == q_rank else "★漂移 %s" % (vm_rank,))
        rep.add(VM.VERIFIED_MIN == Q.VERIFIED_MIN and VM.STRICT_MIN == Q.STRICT_MIN,
                "C8 VERIFIED_MIN/STRICT_MIN 两处一致")
    except Exception as e:  # noqa: BLE001
        rep.add(False, "C7 verification_model 可导入并一致", "★%s: %s" % (type(e).__name__, e))

    # ---------- V1 首屏不加载全图（后端侧代理） ----------
    print("\n[V1] 首屏不加载全图（后端侧代理：子图端点不得读全图投影）")
    calls = {"n": 0}
    orig_load = V.load_graph_data

    def _spy():
        calls["n"] += 1
        return orig_load()

    V.load_graph_data = _spy
    try:
        c, r = http_get(port, "/api/subgraph" + q(seeds="MX:def:manifold", mode="ego"))
        ok = (c == 200 and calls["n"] == 0
              and r["stats"]["n_nodes"] <= 500)
        rep.add(ok, "V1a /api/subgraph 不读 graph_data.json",
                "load_graph_data 调用 %d 次；返回 %d 节点"
                % (calls["n"], r["stats"]["n_nodes"]))
        c2, r2 = http_get(port, "/api/overview")
        rep.add(c2 == 200 and calls["n"] == 0, "V1b /api/overview 亦不读全图",
                "调用 %d 次" % calls["n"])
        calls["n"] = 0
        http_get(port, "/graph_data.json")
        rep.add(calls["n"] == 1, "V1 反例：/graph_data.json 确实读全图（证明探针有效）",
                "调用 %d 次" % calls["n"])
    finally:
        V.load_graph_data = orig_load

    # ---------- V2 超预算 ⇒ truncated ----------
    print("\n[V2] 超预算 ⇒ status=truncated（且 n_nodes ≤ max_nodes）")
    c, r = http_get(port, "/api/subgraph" + q(mode="filter", types="has_quantity",
                                              max_nodes=50, max_edges=100))
    ok = (c == 200 and r["status"] == "truncated" and bool(r["reason"])
          and r["stats"]["n_nodes"] <= 50 and r["stats"]["truncated_by"] == "nodes"
          and r["stats"]["pre_truncation"]["n_edges"] > r["stats"]["n_edges"])
    rep.add(ok, "V2a 正例：max_nodes=50 ⇒ truncated/nodes，reason 非空",
            "%s / pre=%s / got=%s" % (r["status"], r["stats"].get("pre_truncation"),
                                      r["stats"]["n_edges"]))
    c, r = http_get(port, "/api/subgraph" + q(mode="filter", types="has_quantity",
                                              max_nodes=5000, max_edges=20000))
    rep.add(r["status"] == "ok" and r["stats"]["truncated_by"] is None,
            "V2 反例：预算放大 ⇒ status=ok（证明 truncated 不是恒真）",
            "%s n_nodes=%s" % (r["status"], r["stats"]["n_nodes"]))

    # ---------- V3 不存在 seed ⇒ no_result ----------
    print("\n[V3] 不存在的 seed ⇒ no_result（≠ ok）")
    c, r = http_get(port, "/api/subgraph" + q(seeds="DOES:NOT:EXIST", mode="ego"))
    rep.add(c == 200 and r["status"] == "no_result" and r["nodes"] == [],
            "V3a 正例：seeds=DOES:NOT:EXIST ⇒ no_result", r["status"])
    c, r = http_get(port, "/api/subgraph" + q(seeds="MX:def:manifold", mode="ego"))
    rep.add(r["status"] == "ok", "V3 反例：存在的 seed ⇒ ok（证明 no_result 不是恒真）",
            r["status"])

    # ---------- V4 超时 ⇒ timeout ----------
    print("\n[V4] 超 deadline ⇒ status=timeout（elapsed_ms ≥ deadline_ms）")
    c, r = http_get(port, "/api/subgraph" + q(mode="filter", types="has_symbol",
                                              deadline_ms=1))
    rep.add(c == 200 and r["status"] == "timeout"
            and r["stats"]["elapsed_ms"] >= 1 and r["error_code"] == "GRAPH_QUERY_TIMEOUT",
            "V4a 正例：deadline_ms=1 ⇒ timeout/GRAPH_QUERY_TIMEOUT",
            "%s elapsed=%sms" % (r["status"], r["stats"]["elapsed_ms"]))
    c, r = http_get(port, "/api/subgraph" + q(mode="filter", types="has_quantity",
                                              max_nodes=100, max_edges=200,
                                              deadline_ms=30000))
    rep.add(r["status"] in ("ok", "truncated"),
            "V4 反例：预算充足 ⇒ 不超时（证明 timeout 不是恒真）", r["status"])

    # ---------- V5 snapshot_id 绑定查询源 ----------
    print("\n[V5] snapshot_id == 查询源 sha256[:12]；换图 ⇒ 变")
    want = "sha256:" + sha256_file(source)[:12]
    got = http_get(port, "/api/subgraph" + q(seeds="MX:def:manifold"))[1]["snapshot_id"]
    rep.add(got == want, "V5a snapshot_id == sha256(查询源)[:12]", got)
    other = os.path.join(POC, "release", "sample", "sample_normalized.json")
    s_other = snap_via_subprocess(other)
    want_other = "sha256:" + sha256_file(other)[:12]
    rep.add(s_other == want_other and s_other != got,
            "V5b 换一份图 ⇒ snapshot_id 变（且等于该图哈希）",
            "%s vs %s" % (got, s_other))

    # ---------- V6 证据来源身份齐全 ----------
    print("\n[V6] 子图边证据含 src/src_ver/src_hash/impl_ver")
    c, r = http_get(port, "/api/subgraph" + q(seeds="MX:def:manifold", mode="ego", hops=2))
    need = ("src", "src_ver", "src_hash", "impl_ver")
    n_ev, bad = 0, []
    for e in r["edges"]:
        for ev in (e.get("evidence") or []):
            n_ev += 1
            miss = [k for k in need if not ev.get(k)]
            if miss:
                bad.append((e["id"], miss))
    rep.add(n_ev > 0 and not bad, "V6a 全部证据对象四项齐备",
            "%d 条证据 / %d 处缺失" % (n_ev, len(bad)))
    with_ev = sum(1 for e in r["edges"] if e.get("evidence"))
    rep.note("V6 覆盖率（如实披露）：%d/%d 条边带证据" % (with_ev, len(r["edges"])))
    # 反例：抹掉一条证据的 src ⇒ schema 必须报错
    if n_ev:
        import copy
        inj = copy.deepcopy(r)
        for e in inj["edges"]:
            if e.get("evidence"):
                del e["evidence"][0]["src"]
                break
        sch = load_schema("subgraph.schema.json")
        rep.add(bool(validate(inj, sch, sch)), "V6 反例：抹掉 evidence[].src ⇒ 校验器变红",
                "（必须报错）")

    # ---------- V7 exclude_stale ----------
    print("\n[V7] exclude_stale=true 时 stale 边不出现")
    c, a = http_get(port, "/api/subgraph" + q(seeds="MX:def:manifold", mode="ego",
                                              hops=3, max_nodes=500,
                                              exclude_stale="false"))
    c, b2 = http_get(port, "/api/subgraph" + q(seeds="MX:def:manifold", mode="ego",
                                               hops=3, max_nodes=500,
                                               exclude_stale="true"))
    n_stale = sum(1 for e in a["edges"] if e.get("stale"))
    rep.add(n_stale > 0, "V7a 基线（exclude_stale=false）确实含 stale 边",
            "%d / %d 条" % (n_stale, len(a["edges"])))
    rep.add(all(not e.get("stale") for e in b2["edges"]) and b2["stats"]["n_edges"] < a["stats"]["n_edges"],
            "V7b exclude_stale=true ⇒ 无 stale 边且边数下降",
            "%d → %d" % (a["stats"]["n_edges"], b2["stats"]["n_edges"]))

    # ---------- V8 / V9 属步骤 B ----------
    print("\n[V8][V9] 前端侧验收（步骤 B / 回归）")
    rep.skip("V8 五态在 UI 上颜色可区分", "属步骤 B（前端 js/*.js）；本轮未覆盖")
    rep.skip("V9 mode=bundle 发布样例链路", "由回归 `bash sttp.sh release` 覆盖（本轮脚本不替代）")

    # ---------- V10 确定性 ----------
    print("\n[V10] 同参数两次 ⇒ query_hash 相同、nodes/edges 逐元素相同")
    p = q(mode="filter", types="has_quantity", max_nodes=60, max_edges=120)
    _, r1 = http_get(port, "/api/subgraph" + p)
    _, r2 = http_get(port, "/api/subgraph" + p)
    same = (r1["query_hash"] == r2["query_hash"]
            and json.dumps(r1["nodes"], sort_keys=True) == json.dumps(r2["nodes"], sort_keys=True)
            and json.dumps(r1["edges"], sort_keys=True) == json.dumps(r2["edges"], sort_keys=True))
    rep.add(same, "V10a 两次响应逐元素相同", "hash=%s" % r1["query_hash"])
    _, r3 = http_get(port, "/api/subgraph" + q(mode="filter", types="has_symbol",
                                               max_nodes=60, max_edges=120))
    rep.add(r1["query_hash"] != r3["query_hash"],
            "V10 反例：换参数 ⇒ query_hash 变（证明 hash 不是常量）",
            "%s vs %s" % (r1["query_hash"], r3["query_hash"]))

    # ---------- V11 type / kind 不混用 ----------
    print("\n[V11] types（语义关系）与 kinds（来源管线）是两个维度")
    c, t = http_get(port, "/api/subgraph" + q(mode="filter", types="has_quantity",
                                              max_nodes=50, max_edges=80))
    c, k = http_get(port, "/api/subgraph" + q(mode="filter", kinds="has_quantity",
                                              max_nodes=50, max_edges=80))
    rep.add(t["status"] in ("ok", "truncated") and t["stats"]["n_edges"] > 0,
            "V11a types=has_quantity 有结果",
            "%s n_edges=%d" % (t["status"], t["stats"]["n_edges"]))
    rep.add(k["status"] == "no_result" and k["stats"]["pre_truncation"]["n_edges"] == 0,
            "V11b kinds=has_quantity ⇒ 0 条（证明未混用）", k["status"])

    # ---------- V12 会话累计硬门 ----------
    print("\n[V12] 会话累计硬门（达上限即拒绝继续展开）")
    sid = "selfcheck-%d" % int(time.time())
    hit = None
    for i in range(40):
        c, rr = http_get(port, "/api/subgraph"
                         + q(session_id=sid, mode="ego", seeds="PQ:mass", hops=1,
                             max_nodes=20, max_edges=20))
        if rr["status"] == "error":
            hit = (i, rr)
            break
    s_ok = hit is not None and hit[1]["error_code"] == "SUBGRAPH_LIMIT_REACHED"
    rep.add(s_ok, "V12a 反复展开 ⇒ SUBGRAPH_LIMIT_REACHED",
            ("第 %d 次拒绝；session=%s" % (hit[0], hit[1]["stats"]["session"])) if hit
            else "★未触发（40 次内未耗尽）")
    if hit:
        sess = hit[1]["stats"]["session"]
        rep.add(sess["n_nodes"] <= 120 and sess["n_edges"] <= 200 and sess["exhausted"],
                "V12b 会话计数不超限且标记 exhausted", str(sess))
        c, rr2 = http_get(port, "/api/subgraph"
                          + q(session_id="fresh-%s" % sid, mode="ego",
                              seeds="MX:def:manifold", hops=1, max_nodes=20))
        rep.add(rr2["status"] == "ok", "V12 反例：新会话可正常展开（证明硬门按会话记账）",
                rr2["status"])

    # ---------- V13 expand_token 不可绕过 ----------
    print("\n[V13] expand_token 服务端签发、不可伪造")
    c, eg = http_get(port, "/api/subgraph" + q(seeds="MX:def:manifold", mode="ego", hops=1))
    tok = (eg.get("expandable") or [{}])[0].get("token")
    if tok:
        c, rr = http_get(port, "/api/subgraph" + q(mode="expand", expand_token=tok))
        rep.add(c == 200 and rr["status"] in ("ok", "truncated"),
                "V13a 正例：合法 token ⇒ 可展开", rr["status"])
        body = tok.rsplit(".", 1)[0]
        try:
            raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
            pay = json.loads(raw.decode("utf-8"))
            pay["hops"] = 99
            raw2 = json.dumps(pay, sort_keys=True, separators=(",", ":")).encode("utf-8")
            forged = base64.urlsafe_b64encode(raw2).decode().rstrip("=") + "." + tok.rsplit(".", 1)[1]
            c, rr = http_get(port, "/api/subgraph" + q(mode="expand", expand_token=forged))
            rep.add(c == 400 and rr["error_code"] == "BAD_REQUEST",
                    "V13b 反例：篡改 token 预算字段 ⇒ 400 BAD_REQUEST",
                    "%s/%s" % (c, rr.get("error_code")))
        except Exception as e:  # noqa: BLE001
            rep.add(False, "V13b 篡改 token", "★异常 %s" % e)
        c, rr = http_get(port, "/api/subgraph" + q(mode="expand", expand_token="aaa.bbb"))
        rep.add(c == 400 and rr["error_code"] == "BAD_REQUEST",
                "V13c 反例：任意字符串 token ⇒ 400", "%s/%s" % (c, rr.get("error_code")))
    else:
        rep.add(False, "V13 需要 expandable[].token", "★该种子无可展开邻居")

    # ---------- V14 可信范围两档并显 ----------
    print("\n[V14] trust 两档并显，隐藏量可加总回全量")
    _, v1 = http_get(port, "/api/subgraph" + q(mode="filter", trust="verified", max_edges=1))
    _, v2 = http_get(port, "/api/subgraph" + q(mode="filter", trust="verified_strict", max_edges=1))
    p1, p2 = v1["stats"]["pre_truncation"], v2["stats"]["pre_truncation"]
    h1, h2 = v1["stats"]["hidden_by_trust"], v2["stats"]["hidden_by_trust"]
    rep.add(h1 > 0 and h2 > h1, "V14a hidden_by_trust 随档位变化（非零、非恒等）",
            "verified=%d / strict=%d" % (h1, h2))
    rep.add(p1["n_edges"] - p2["n_edges"] == h2 - h1,
            "V14b visible(v)−visible(s) == hidden(s)−hidden(v)",
            "%d == %d" % (p1["n_edges"] - p2["n_edges"], h2 - h1))
    rep.add(p1["n_edges"] + h1 == p2["n_edges"] + h2,
            "V14c visible+hidden 两档相等（可加总回全量）",
            "%d == %d" % (p1["n_edges"] + h1, p2["n_edges"] + h2))
    rep.note("V14 实测：verified 可见 %d / 隐藏 %d；verified_strict 可见 %d / 隐藏 %d（差 %d 条=by_construction）"
             % (p1["n_edges"], h1, p2["n_edges"], h2, p1["n_edges"] - p2["n_edges"]))

    # ---------- V15 why_shown 覆盖 ----------
    print("\n[V15] 每个节点/边都有 why_shown 且 ∈ 枚举")
    allowed = set(Q.WHY_SHOWN_VALUES)
    miss = [n["id"] for n in eg["nodes"] if n.get("why_shown") not in allowed]
    miss += [e["id"] for e in eg["edges"] if e.get("why_shown") not in allowed]
    rep.add(not miss, "V15a 覆盖完整且取值合法", "缺失=%d" % len(miss))
    _, eg3 = http_get(port, "/api/subgraph" + q(seeds="MX:def:manifold", mode="ego", hops=3))
    kinds_seen = {n["why_shown"] for n in eg3["nodes"]} | {e["why_shown"] for e in eg3["edges"]}
    rep.add(len(kinds_seen) >= 2, "V15b why_shown 取值分布非单一", "%s" % sorted(kinds_seen))
    import copy
    inj = copy.deepcopy(eg3)
    if inj["nodes"]:
        inj["nodes"][0].pop("why_shown", None)
    sch = load_schema("subgraph.schema.json")
    rep.add(bool(validate(inj, sch, sch)), "V15 反例：抹掉 why_shown ⇒ 校验器变红")

    # ---------- V16 stale 判据非真空（负对照，铁律 #61） ----------
    print("\n[V16] stale 判据非真空：改动被登记来源 ⇒ 相关边判 stale")
    target = os.path.join(POC, "sample_mathxiv.json")
    orig_bytes = open(target, "rb").read() if os.path.exists(target) else None
    orig_sha = hashlib.sha256(orig_bytes).hexdigest() if orig_bytes else None
    probe = q(seeds="MX:def:manifold", mode="ego", hops=3, max_nodes=500)
    try:
        base = http_get(port, "/api/subgraph" + probe)[1]
        base_stale = sum(1 for e in base["edges"] if e.get("stale"))
        key_before = V._STALE_CACHE["key"]
        rep.add(orig_bytes is not None, "V16a 找到负对照目标文件（被登记 locator）",
                os.path.relpath(target, ROOT))
        with open(target, "ab") as f:
            f.write(b"\n/* phase43 selfcheck negative control */\n")
        after = http_get(port, "/api/subgraph" + probe)[1]
        after_stale = sum(1 for e in after["edges"] if e.get("stale"))
        key_after = V._STALE_CACHE["key"]
        rep.add(after_stale > base_stale,
                "V16b 改动登记文件 ⇒ stale 边增加（判据非真空）",
                "%d → %d" % (base_stale, after_stale))
        rep.add(key_before != key_after,
                "V16c 缓存键随内容身份变化（不是按时间猜）", "键已更新")
    finally:
        if orig_bytes is not None:
            with open(target, "wb") as f:
                f.write(orig_bytes)
    if orig_sha is not None:
        rep.add(sha256_file(target) == orig_sha, "V16d 负对照后逐字节还原（铁律 #61）",
                sha256_file(target)[:16])
        restored = http_get(port, "/api/subgraph" + probe)[1]
        rep.add(sum(1 for e in restored["edges"] if e.get("stale")) == base_stale,
                "V16e 还原后 stale 计数回到基线", "%d" % base_stale)

    # ---------- V16 附带：字节忠实性探针（铁律 #61 推广） ----------
    print("\n[V16f] 字节忠实性探针：worktree 字节 vs 仓库 blob")
    fid = http_get(port, "/api/subgraph" + probe)[1]["stats"].get("stale_registry") or {}
    # 独立重算（不复用服务端缓存）：
    ind = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files", "-s", "-z", "--"],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    tracked = {}
    for rec in ind.stdout.split("\0"):
        if not rec:
            continue
        meta, _, p = rec.partition("\t")
        f = meta.split()
        if p and len(f) >= 2:
            tracked[p.strip()] = f[1]
    rels = []
    sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
    import source_registry as SR        # noqa: E402
    for sid in SR.ids():
        for rel in (SR.get(sid) or {}).get("locator") or []:
            if rel not in rels:
                rels.append(rel)
    n_unf = 0
    for rel in rels:
        if rel not in tracked:
            continue
        h = subprocess.run(["git", "hash-object", "--no-filters", "--", rel],
                           cwd=ROOT, capture_output=True, text=True)
        if h.stdout.strip() != tracked[rel]:
            n_unf += 1
    rep.add("n_unfaithful" in fid and fid.get("n_unfaithful") == n_unf,
            "V16f 响应里的字节忠实性计数 == 独立重算",
            "响应 %s / 独立 %d" % (fid.get("n_unfaithful"), n_unf))
    if n_unf:
        rep.note("★ 已知缺陷（本轮发现并登记）：%d/%d 个登记 locator 的 worktree 字节 ≠ 仓库 blob "
                 "⇒ 证据里的 src_hash 在干净检出下不可复现、stale 判据会假阳。"
                 "根因与修法见 `07_交付物/Phase43_*.md`；**exclude_stale 默认仍为 false**。"
                 % (n_unf, fid.get("checked")))
    else:
        rep.note("字节忠实性：全部登记 locator 与仓库 blob 一致")

    # ---------- V17 会话记账语义（无 id ⇒ 不记账；有 id ⇒ 累加 + reset 清空 + 表有上限） ----------
    print("\n[V17] 会话记账语义：匿名不记账 / reset 清空 / 会话表有上限")
    sts = []
    for _ in range(3):
        _, an = http_get(port, "/api/subgraph"
                         + q(mode="ego", seeds="PQ:mass", hops=1, max_nodes=20, max_edges=20))
        sts.append(an)
    rep.add(all(x["status"] in ("ok", "truncated") for x in sts)
            and all(x["stats"]["session"].get("tracked") is False for x in sts),
            "V17a 无 session_id ⇒ tracked=False 且连续 3 次都不被硬门拒绝",
            "status=%s tracked=%s" % ([x["status"] for x in sts],
                                      [x["stats"]["session"].get("tracked") for x in sts]))
    rep.add(all(x["stats"]["session"]["n_nodes"] == 0 for x in sts),
            "V17b 匿名调用不改写会话表（计数恒为 0）",
            "n_nodes=%s" % [x["stats"]["session"]["n_nodes"] for x in sts])

    sid2 = "selfcheck-acc-%d" % int(time.time())
    _, a1 = http_get(port, "/api/subgraph"
                     + q(session_id=sid2, mode="ego", seeds="PQ:mass", hops=1,
                         max_nodes=20, max_edges=20))
    _, a2 = http_get(port, "/api/subgraph"
                     + q(session_id=sid2, mode="ego", seeds="PQ:mass", hops=1,
                         max_nodes=20, max_edges=20))
    s1, s2 = a1["stats"], a2["stats"]
    rep.add(s2["session"]["tracked"] and s2["expansions"] > s1["expansions"]
            and s2["session"]["n_nodes"] >= s1["session"]["n_nodes"],
            "V17c 有 session_id ⇒ tracked=True 且展开次数递增",
            "exp %s→%s / n %s→%s" % (s1["expansions"], s2["expansions"],
                                     s1["session"]["n_nodes"], s2["session"]["n_nodes"]))
    _, a3 = http_get(port, "/api/subgraph"
                     + q(session_id=sid2, mode="ego", seeds="PQ:mass", hops=1,
                         max_nodes=20, max_edges=20, reset="1"))
    rep.add(a3["stats"]["session"]["n_nodes"] <= s2["session"]["n_nodes"]
            and a3["stats"]["expansions"] == 1,
            "V17d reset=1 ⇒ 该会话计数先清零再计本次",
            "n %s→%s / exp=%s" % (s2["session"]["n_nodes"],
                                  a3["stats"]["session"]["n_nodes"], a3["stats"]["expansions"]))

    old_max = V.SESSION_MAX
    try:
        V.SESSION_MAX = 3
        for i in range(6):
            http_get(port, "/api/subgraph"
                     + q(session_id="cap-%d-%d" % (int(time.time()), i), mode="ego",
                         seeds="PQ:mass", hops=1, max_nodes=10, max_edges=10))
        n_live = len(V._SESSIONS)
        rep.add(n_live <= 3, "V17e 会话表超上限 ⇒ 按最久未写淘汰（内存不无界增长）",
                "V.SESSION_MAX=3 ⇒ 实存 %d" % n_live)
    finally:
        V.SESSION_MAX = old_max

    # ---------- Neo4j 契约（本环境不可达 ⇒ SKIP，不计通过） ----------
    print("\n[Neo4j] 双后端同契约（本环境不可达 ⇒ SKIP）")
    try:
        nb = Q.get_backend("neo4j", uri="bolt://127.0.0.1:7687", user="neo4j",
                           password="neo4j")
        if nb.name == "neo4j":
            rep.skip("N1 Neo4j 子图契约一致", "本环境无 Neo4j 实例")
        else:
            rep.skip("N1 Neo4j 子图契约一致", "回退到 NetworkX（未连上 Neo4j）")
    except Exception as e:  # noqa: BLE001
        rep.skip("N1 Neo4j 子图契约一致", "不可达：%s" % type(e).__name__)

    srv.shutdown()
    c = rep.counts()
    print("-" * 78)
    print("  合计 PASS %d / FAIL %d / SKIP %d   （耗时 %.1f s）"
          % (c["PASS"], c["FAIL"], c["SKIP"], time.time() - t_all))
    print("=" * 78)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"schema": "sttp-phase43-selfcheck/v1", "counts": c,
                       "query_source": os.path.relpath(source, ROOT),
                       "rows": [{"status": s, "name": n, "detail": d} for s, n, d in rep.rows],
                       "notes": rep.notes,
                       "elapsed_s": round(time.time() - t_all, 2)},
                      f, ensure_ascii=False, indent=1)
        print("  报告已写：%s" % args.json)
    return 1 if c["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
