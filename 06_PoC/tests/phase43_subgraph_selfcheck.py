# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""
Phase 43 · 局部图谱查询 **非真空自检**（V1–V20）
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
        props = schema.get("properties") or {}
        for k, sub in props.items():
            if k in inst:
                validate(inst[k], sub, root, "%s.%s" % (path, k), errs)
        # ★ Phase 45 补齐 `additionalProperties`（此前 C5b 报「未实现」⇒ 该约束**根本没被检查**，
        #   正是 D12 登记的那类「静默跳过」。A04 契约用它约束 `evidence[].refs`
        #   （`{ref_id: 哈希}` 的**动态键**）与 `ref_bindings`，无法用 `properties` 表达。
        ap = schema.get("additionalProperties")
        if ap is not None and ap is not True:
            for k, v in inst.items():
                if k in props:
                    continue
                if ap is False:
                    errs.append("%s: 不允许额外属性 %s" % (path, k))
                elif isinstance(ap, dict):
                    validate(v, ap, root, "%s.%s" % (path, k), errs)
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
    "required", "properties", "items", "additionalProperties",
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
            elif k == "additionalProperties":
                out.add(k)
                if isinstance(v, dict):        # 值里的关键字也要被检查（防「约束里再藏未实现关键字」）
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
    ap = argparse.ArgumentParser(description="Phase 43 局部图谱查询自检（V1–V20）")
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
    print("  Phase 43 · 局部图谱查询自检（V1–V20）")
    print("=" * 78)

    # ★ 前置：本自检需要**权威图**（不入库）。缺源时给**明确原因 + RC=2**，
    #   而不是让后续断言以 KeyError/一串 FAIL 的形式崩掉（干净检出必读此提示）。
    source = V._graph_path()
    if not os.path.exists(source):
        print("-" * 78)
        print("  ⛔ 找不到查询源：%s" % source)
        print("     该文件被 .gitignore 排除（**不入库**）⇒ 干净检出下本自检**不可能**运行。")
        print("     三种可用做法：")
        print("       ① 在已有权威图的副本上运行（本项目主工作区默认如此）；")
        print("       ② 指定替代源：GRAPH_SOURCE_FILE=<path> bash sttp.sh subgraph"
              "（★ 但权威图口径的断言，如「48712 边」「hidden_by_trust 两档恒等」，")
        print("          在小样例上**必然 FAIL** —— 本自检不是为样例设计的）；")
        print("       ③ 干净检出只验**下游链路**："
              "python 06_PoC/release/verify_release.py --release")
        print("          （只校验**已入库**的离线样例；实测裸 clone 下 PASS 28 / FAIL 0。）")
        print("          ★ 不要用 `bash sttp.sh release`：它会先**重建**样例、需要权威图 ⇒")
        print("            在裸 clone 上必定失败（那是**正确**的 fail-closed，不是可用路径）。")
        print("          ★ `bash sttp.sh frontend` 可在裸 clone 跑（56/0/4，缺图处记 SKIP 不假通过）。")
        print("-" * 78)
        return 2
    srv = V.ThreadingHTTPServer(("127.0.0.1", 0), V.VizHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("  HTTP 服务: 127.0.0.1:%d（真响应体断言）" % port)
    print("  查询源   : %s" % os.path.relpath(source, ROOT))
    _b = V.get_backend()
    n_full = _b.node_count if _b is not None else 0
    print("  后端规模 : %d 节点" % n_full)
    print("-" * 78)
    if n_full < 1000:
        print("  ⛔ 查询层后端不可用，或当前查询源只有 %d 个节点（疑似入库样例）——"
              "本自检的多数断言以**权威图口径**（9657/48712）为前提。" % n_full)
        print("     请改用权威图运行，或改跑 `bash sttp.sh release`。")
        return 2

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

    # ★ C5b2 反例：`additionalProperties` 必须**真的生效**
    #   为什么单独证一次：把关键字加进 `SUPPORTED_KEYWORDS` 只是「声明」，
    #   若不真正执行，C5b 会**变绿而约束仍为空**（铁律 #38 的典型形态）。
    #   A04 契约用它约束 `evidence[].refs` 的**动态键**（`{ref_id: 哈希}`），
    #   无法改用 `properties` 表达 ⇒ 它必须真被检查。
    ap_sch = {"type": "object", "properties": {"a": {"type": "string"}},
              "additionalProperties": {"type": "integer"}}
    rep.add(bool(validate({"a": "x", "b": "not-int"}, ap_sch, ap_sch)),
            "C5b2 additionalProperties 生效：动态键值类型不符 ⇒ 校验器变红",
            "（注入反例，必须报错）")
    rep.add(not validate({"a": "x", "b": 3}, ap_sch, ap_sch),
            "C5b3 additionalProperties 正例：动态键值合法 ⇒ 通过（断言非恒真）")

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

    # V5c/V5d ★ Phase 52（D42）：让「复现校验」这条契约条款**真的被验证**。
    #   背景：后端早就实现「请求的 snapshot_id ≠ 当前查询源 ⇒ SNAPSHOT_MISMATCH」，
    #   但**前端从不发送**该参数 ⇒ 这条分支在任何真实链路里都走不到，
    #   等于契约里写了一条没人用的条款（页面数据与查询源不是同一份时会静默按新图作答）。
    #   本轮前端开始发送它（`core.buildParams` + `Api.subgraph`），故这里补上**两侧**：
    #     ① 不符 ⇒ 必须**拒绝**（否则"校验"只是装饰）；
    #     ② 相符 ⇒ 必须**放行**（否则"拒绝"可能只是恒拒，负对照缺失 —— 铁律 #75）。
    c, r_bad = http_get(port, "/api/subgraph" + q(seeds="MX:def:manifold",
                                                  snapshot_id="sha256:000000000000"))
    rep.add(r_bad.get("status") == "error" and r_bad.get("error_code") == "SNAPSHOT_MISMATCH",
            "V5c 请求带**不符**的 snapshot_id ⇒ SNAPSHOT_MISMATCH（校验真会拒）",
            "%s / %s" % (r_bad.get("status"), r_bad.get("error_code")))
    c, r_ok = http_get(port, "/api/subgraph" + q(seeds="MX:def:manifold", snapshot_id=got))
    rep.add(r_ok.get("status") in ("ok", "truncated") and r_ok.get("error_code") is None,
            "V5d 请求带**相符**的 snapshot_id ⇒ 正常放行（证明 V5c 不是恒拒）",
            "%s" % r_ok.get("status"))

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
    # ★ 2026-10-15 修正（Phase 49）：原实现**隐式依赖 `max_hops` 的默认值 = 3**
    #   —— 它只传 `hops=3`，指望子图够大、足以包含 stale 边。Phase 49 把
    #   `queries.DEFAULT_HOPS` 由 3 收敛为 2（它是 `max_hops` 的默认**夹紧上限**）
    #   ⇒ `hops=3` 被夹紧到 2、子图由 25 边降到 9 边、其中 stale 边为 0 ⇒ V7a 变红。
    #   处置：**把前置条件写显式**（`max_hops=3`），而不是把库默认值改回去 ——
    #   本断言要的是「`exclude_stale` 真的移除 stale 边」，与某个默认值无关；
    #   测试应当**构造**自己需要的前置条件，而不是依赖"当前恰好如此"的默认
    #   （同族：#75 负对照须可构造、#74 断言过期须重写而非删除）。
    #   另：该夹紧**不是静默的**（见新增 V7c），所以这不是 #85 家族缺陷。
    print("\n[V7] exclude_stale=true 时 stale 边不出现")
    c, a = http_get(port, "/api/subgraph" + q(seeds="MX:def:manifold", mode="ego",
                                              hops=3, max_hops=3, max_nodes=500,
                                              exclude_stale="false"))
    c, b2 = http_get(port, "/api/subgraph" + q(seeds="MX:def:manifold", mode="ego",
                                               hops=3, max_hops=3, max_nodes=500,
                                               exclude_stale="true"))
    n_stale = sum(1 for e in a["edges"] if e.get("stale"))
    rep.add(n_stale > 0, "V7a 基线（exclude_stale=false）确实含 stale 边",
            "%d / %d 条" % (n_stale, len(a["edges"])))
    rep.add(all(not e.get("stale") for e in b2["edges"]) and b2["stats"]["n_edges"] < a["stats"]["n_edges"],
            "V7b exclude_stale=true ⇒ 无 stale 边且边数下降",
            "%d → %d" % (a["stats"]["n_edges"], b2["stats"]["n_edges"]))
    # V7c ★ 新增：`hops` 超出 `max_hops` 默认值时**必须披露**（不得静默丢弃 —— #85 家族）
    #   对照（2026-10-15 实测）：`hops=3` 且不传 `max_hops` ⇒ `status=truncated`、
    #   `reason="请求跳数被夹紧至 max_hops=2"`、`stats.truncated_by="hops"`、
    #   `stats.hops=2` / `stats.requested_hops=3`。即"请求 3 跳、实得 2 跳"这件事
    #   在响应里是**可读的**，不是悄悄少给一跳。
    _, c3 = http_get(port, "/api/subgraph" + q(seeds="MX:def:manifold", mode="ego",
                                               hops=3, max_nodes=500))
    s3 = c3.get("stats") or {}
    rep.add(s3.get("truncated_by") == "hops" and s3.get("requested_hops") == 3
            and s3.get("hops") == 2 and "夹紧" in (c3.get("reason") or ""),
            "V7c 请求跳数被 `max_hops` 夹紧时**必须披露**（truncated_by=hops + requested/actual）",
            "truncated_by=%s · requested=%s → 实走=%s · reason=%s"
            % (s3.get("truncated_by"), s3.get("requested_hops"), s3.get("hops"),
               (c3.get("reason") or "")[:40]))

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

    # ---------- V16 stale 判据：D1 后「裁定与检出无关」+ 判据仍非真空（铁律 #61） ----------
    #   ★ D1 根因修复**翻转了本段的断言方向**：权威字节源已是仓库 blob ⇒ **工作区**
    #     变异**不应**再影响裁决（这正是修复要的性质）。旧形式断言「变异 ⇒ stale 增加」，
    #     测的是**旧缺陷**，在修好之后必然变红 —— 那不是回归，是断言过期。
    #   故拆成两件独立的事：
    #     ① D1 性质（HTTP 层，走生产路径）：worktree 变异 ⇒ 服务端裁决**不变**；
    #     ② 非真空：把某个已登记来源的**当前哈希**改成不同值（等价于"该来源文件真的变了"）
    #        ⇒ 相关边必须由 verified 翻成 changed（证明判据本身能动，不是恒绿）。
    print("\n[V16] stale 判据：D1 后裁定与检出/行尾无关 + 判据仍非真空")
    target = os.path.join(POC, "sample_mathxiv.json")
    orig_bytes = open(target, "rb").read() if os.path.exists(target) else None
    orig_sha = hashlib.sha256(orig_bytes).hexdigest() if orig_bytes else None
    probe = q(seeds="MX:def:manifold", mode="ego", hops=3, max_nodes=500)
    rep.add(orig_bytes is not None, "V16a 找到负对照目标文件（被登记 locator）",
            os.path.relpath(target, ROOT))
    if orig_bytes is not None:
        sr = V._load_source_registry()
        rsm = sr.repo_status_map(ROOT)
        all_e = V.get_backend().all_edges()
        s_before = sum(1 for e in http_get(port, "/api/subgraph" + probe)[1]["edges"]
                       if e.get("stale"))
        try:
            with open(target, "ab") as f:
                f.write(b"\n/* phase43 selfcheck negative control */\n")
            s_after = sum(1 for e in http_get(port, "/api/subgraph" + probe)[1]["edges"]
                          if e.get("stale"))
            rep.add(s_after == s_before,
                    "V16b ★ D1：worktree 变异 ⇒ 服务端 stale **不变**（裁定与检出/行尾无关）",
                    "%d → %d" % (s_before, s_after))
        finally:
            if orig_bytes is not None:
                with open(target, "wb") as f:
                    f.write(orig_bytes)
        rep.add(sha256_file(target) == orig_sha, "V16e 负对照后逐字节还原（铁律 #61）",
                sha256_file(target)[:16])
        s_restored = sum(1 for e in http_get(port, "/api/subgraph" + probe)[1]["edges"]
                         if e.get("stale"))
        rep.add(s_restored == s_before, "V16e2 还原后服务端 stale 计数回到基线",
                "%d" % s_restored)

        # ② 非真空：翻转某个"被最多边引用"的已登记来源的当前哈希
        hm = sr.hash_map(ROOT)
        refs = {}
        for e in all_e:
            for ev in ((e.get("props") or {}).get("verification_evidence") or []):
                if isinstance(ev, dict) and hm.get(ev.get("src")):
                    refs[ev["src"]] = refs.get(ev["src"], 0) + 1
        sid_t = max(refs, key=refs.get) if refs else None
        # ★ Phase 73（D109）：**两侧必须同一命题**。
        #   旧写法 `reg0 = V.stale_registry()` 取的是服务端 registry（含 A04 证据层），
        #   而 `reg1` 是 `Q.build_stale_registry()` 本地构造（**不含** A04）⇒
        #   「含 A04 的 stale 总数」 vs 「不含 A04 的 stale 总数」比大小，两侧根本不是一个问题：
        #     · A04 判出 0 条时两边恒等 ⇒ 判据恒真，什么都不守；
        #     · A04 一旦判出 stale（本图 3.2 万条）⇒ 左大右小 ⇒ **假红**。
        #   这与 D72（V18h 旧版）是同一个模子，Phase 57 修 V18h 时漏修了同函数的 V16c。
        #   修法：两侧都用同一条构造路径，**只让哈希图这一个变量不同**。
        reg0 = Q.build_stale_registry(sr.ids(), hm, rsm)
        n0 = sum(1 for e in all_e if Q.edge_is_stale(e, reg0))
        if sid_t:
            hm2 = dict(hm)
            hm2[sid_t] = "0" * 64
            reg1 = Q.build_stale_registry(sr.ids(), hm2, rsm)
            n1 = sum(1 for e in all_e if Q.edge_is_stale(e, reg1))
            n_ch = sum(1 for e in all_e if Q.edge_fidelity(e, reg1) == "changed")
            rep.add(n1 > n0 and n_ch > 0,
                    "V16c 判据非真空：翻转已登记来源的当前哈希 ⇒ 相关边翻成 changed",
                    "stale %d → %d（changed=%d，来源 %s，引用 %d 条）"
                    % (n0, n1, n_ch, sid_t, refs.get(sid_t, 0)))
        else:
            rep.add(False, "V16c 判据非真空：翻转已登记来源的当前哈希 ⇒ 相关边翻成 changed",
                    "找不到有当前哈希的来源（hashes 全为 None？）")

        # 缓存键：HEAD 变 ⇒ 键必变；不得按 mtime/size 猜
        with V._STALE_LOCK:
            V._STALE_CACHE["key"] = None
        V.stale_registry()
        k0 = V._STALE_CACHE["key"]
        real_head = V._head_sha(ROOT)
        real_head_fn = V._head_sha                # 捕获原函数对象（monkeypatch 后要还回）
        try:
            V._head_sha = (lambda *a, **k: "f" * 40)
            V.stale_registry()
            k1 = V._STALE_CACHE["key"]
        finally:
            V._head_sha = real_head_fn
        V.stale_registry()
        rep.add(k0 != k1 and isinstance(k0, tuple) and k0[0] == real_head,
                "V16d 缓存键随 HEAD 变化（HEAD 变 ⇒ 键变 ⇒ 重算；不含 mtime/size）",
                "HEAD=%s｜伪 HEAD 下键已变=%s" % (str(real_head)[:12], k0 != k1))

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
        rep.note("★ D1 已修根因（本轮）：%d/%d 个**已入库** locator 的 worktree 字节 ≠ 仓库 blob "
                 "（本机 `core.autocrlf=true` 把 LF 检出成 CRLF）。**这不影响裁决** —— "
                 "权威字节源已是 `git cat-file blob HEAD:<path>`（见 V16b/V18d/V18e），"
                 "此计数只作**诊断披露**（`affects_verdict: false`）。"
                 "另有 13 个 locator 未入库（`*_raw.json`/`*_cache.json`，按「大体积派生对象不入库」"
                 "有意排除）⇒ 占 47055 条边的 `unverifiable_not_in_repo`。"
                 "完整论证见 `07_交付物/Phase44_D1_*.md`；**exclude_stale 默认仍为 false**。"
                 % (n_unf, fid.get("checked")))
    else:
        rep.note("字节忠实性：全部已入库 locator 的工作区字节与仓库 blob 一致（无需归一化差异披露）")

    # V16g ★ 构造性非真空：本机若已归一化（上面 n_unfaithful=0），「两口径差异」就无从观测
    #   ⇒ 在**临时仓库**里把差异造出来，证明「口径选择」不是同义反复（干净 clone 亦能跑）。
    pr = SR.divergence_probe()
    try:
        probe_ok = bool(pr.get("differ")) and bool(pr.get("restored_equal"))
    finally:
        import shutil as _sh
        SR.rmtree_force(pr.get("tmp"))   # ★ E9：Windows 只读 git object ⇒ ignore_errors 会静默失败
    rep.add(probe_ok,
            "V16g 两口径可构造差异（blob=LF / 工作区=CRLF ⇒ 哈希必不同；还原后必相等）",
            "differ=%s / restored_equal=%s %s"
            % (pr.get("differ"), pr.get("restored_equal"), pr.get("error", "")))

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

    # ---------- V18 证据字节忠实度四分类（Phase 44 · D1 根因修复，铁律 #41/#61/#67） ----------
    #   本段守三件事：
    #     ① **可加总**：四分类之和 == 边总数（分列而不合并 ⇒ 不丢不可判定项）；
    #     ② **口径不变**：`stale` 仍 == `changed + unverifiable_no_source`，
    #        且 `unverifiable_not_in_repo` **不计入** stale（那些来源并未变更）；
    #     ③ **响应自洽**：每条边的 `fidelity` 与 `stale` 布尔必须互相推出（两处口径防漂移）。
    #   数值不写死 —— 全部从响应读 + 独立重算对比，图变时不会假红。
    print("\n[V18] 证据字节忠实度四分类（D1：仓库字节为准 ⇒ changed 应为 0）")
    if "SR" not in dir():                       # V16f 已导入；此处防御性重导（幂等）
        sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
        import source_registry as SR        # noqa: E402, F811
    _, fid_r = http_get(port, "/api/subgraph"
                        + q(seeds="MX:def:manifold", mode="ego", hops=3, max_nodes=800))
    gstats = fid_r["stats"]
    gcounts = gstats.get("evidence_fidelity_global") or {}
    loccounts = gstats.get("evidence_fidelity") or {}

    # V18a 四分类齐备 + 求和 == 边总数（分列可加总）
    classes = list(Q.FIDELITY_CLASSES)
    have_all = all(k in gcounts for k in classes) and gcounts.get("n_edges_total")
    nsum = sum(gcounts.get(k, 0) for k in classes) if have_all else None
    rep.add(bool(have_all) and nsum == gcounts.get("n_edges_total"),
            "V18a 全图四分类齐备且求和 == 边总数（分列可加总）",
            " ".join("%s=%s" % (k, gcounts.get(k)) for k in classes)
            + "  Σ=%s / 总=%s" % (nsum, gcounts.get("n_edges_total")) if have_all
            else "字段缺失：%s" % gcounts)

    # V18b 本地（本次可见子图）同样可加总，且 ≤ 全图
    lsum = sum(loccounts.get(k, 0) for k in classes) if loccounts else None
    n_edges_seen = len(fid_r["edges"])
    rep.add(lsum == n_edges_seen,
            "V18b 本次子图四分类求和 == 本次返回边数",
            "Σ=%s / 边=%d" % (lsum, n_edges_seen))
    rep.add(all(loccounts.get(k, 0) <= gcounts.get(k, 0) for k in classes) if loccounts else False,
            "V18c 本次子图各分类 ≤ 全图同类（无越界）",
            " ".join("%s %s≤%s" % (k, loccounts.get(k), gcounts.get(k)) for k in classes)
            if loccounts else "缺 evidence_fidelity")

    # V18d ★ D1 修复的核心断言：以仓库字节为准 ⇒ 全图 `changed` 必须为 0
    rep.add(gcounts.get("changed") == 0,
            "V18d 全图 changed == 0（D1 根因已修：裁决与检出/行尾无关）",
            "changed=%s（若 >0 ⇒ 有已登记来源的真实字节与仓库 blob 不符，须查来源而非行尾）"
            % gcounts.get("changed"))

    # V18e 字节源必须是仓库（不是 worktree）——否则 D1 会回归
    #   ★ 这两个字段是**图级事实**（不随子图变），发布在 `/api/stats`；
    #     子图响应只带 `evidence_fidelity_global`（也是图级，但为读数方便一并给了）。
    _, stats_r = http_get(port, "/api/stats")
    fsrc = stats_r.get("evidence_byte_source") or ""
    rep.add(fsrc.startswith("repo"),
            "V18e 证据字节源 == 仓库 blob（不是 worktree，D1 防回退）",
            fsrc or "缺 evidence_byte_source")

    # V18f 【Phase 45 重写，铁律 #74】`stale` 口径 = **两层并集**（不是仅字节层）
    #   为什么必须重写而不是删除：Phase 45 起 `edge_is_stale()` 把「字节忠实度」与
    #   「A04 依赖状态」合流到**一处**。旧断言 `stale == changed + unverifiable_no_source`
    #   在 A04 判出 stale/rejected 时会**假红**；但它守的性质（字节层不丢项）仍然重要
    #   ⇒ 拆成两条：① 字节层那部分**恰好**等于旧口径；② 总数 ⊇ 字节层且 ⊆ 两层并集。
    reg = V.stale_registry()
    b_all = V.get_backend().all_edges()
    ind_counts = Q.fidelity_counts(b_all, reg)          # 独立重算（不复用服务端缓存）
    ind_fid_stale = sum(1 for e in b_all
                        if Q.edge_fidelity(e, reg) in Q.STALE_FIDELITIES)
    base_stale = ind_counts["changed"] + ind_counts["unverifiable_no_source"]
    ind_a04_stale = sum(1 for e in b_all
                        if (Q.edge_evidence_status(e, reg) or {}).get("status")
                        in Q.STALE_EVIDENCE_STATUSES)
    ind_stale = sum(1 for e in b_all if Q.edge_is_stale(e, reg))
    rep.add(ind_fid_stale == base_stale,
            "V18f 字节层 stale 计数 == changed + unverifiable_no_source（旧口径原样保留）",
            "字节层 stale=%d == %d + %d" % (ind_fid_stale, ind_counts["changed"],
                                            ind_counts["unverifiable_no_source"]))
    rep.add(base_stale <= ind_stale <= base_stale + ind_a04_stale,
            "V18f2 【重写】全图 stale = 字节层 ∪ A04 层（两层并集，含于区间内）",
            "stale=%d ∈ [%d, %d]（A04 层贡献 %d）"
            % (ind_stale, base_stale, base_stale + ind_a04_stale, ind_a04_stale))

    # V18g 独立重算 == 响应（服务端不是另算一套）
    diffs = [k for k in classes if ind_counts.get(k) != gcounts.get(k)]
    rep.add(not diffs,
            "V18g 独立重算四分类 == 响应 evidence_fidelity_global",
            "全部一致" if not diffs else "差：%s" % {k: (ind_counts.get(k), gcounts.get(k))
                                                    for k in diffs})

    # V18h ★ `unverifiable_not_in_repo` 明确**不**计入 stale（已知边界，不是缺陷信号）
    #   ★ D72（Phase 57）旧判据 `ind_stale == changed + no_source` 有**两个**毛病：
    #     ① `ind_stale` 走 `edge_is_stale()`（含 A04 层），右边是纯字节层 ⇒ 口径混用；
    #        当 A04 层贡献为 0 时两边恒等 ⇒ 判据**恒真**，什么都不守；
    #        A04 一旦判出 stale/rejected 立刻假红（与 V18f2 已修的旧断言同族）。
    #     ② 它想守的性质（not_in_repo 不算 stale）用**总数比总数**验证，测不到具体边。
    #   ⇒ 改为**构造性**：拿真实的 not_in_repo 边逐条验证 `edge_is_stale()` 为 False。
    #     `bool(nir)` 保证判据非真空 —— 样本为空时**红**，不是空过。
    notinrepo = gcounts.get("unverifiable_not_in_repo") or 0
    nir = [e for e in b_all if Q.edge_fidelity(e, reg) == "unverifiable_not_in_repo"]
    nir_stale = sum(1 for e in nir if Q.edge_is_stale(e, reg))
    rep.add(bool(nir) and nir_stale == 0,
            "V18h [构造性] unverifiable_not_in_repo 的边不被判 stale（未入库 ≠ 变更）",
            "样本 %d 条中判 stale %d 条｜not_in_repo=%d（占 %.1f%%）"
            % (len(nir), nir_stale, notinrepo,
               100.0 * notinrepo / max(1, gcounts.get("n_edges_total") or 1)))

    # V18i 来源侧三分类：in_repo + not_in_repo + no_locator == 来源总数
    rsrc = stats_r.get("source_repo_status") if isinstance(stats_r.get("source_repo_status"), dict) else {}
    n_src = sum(rsrc.values()) if rsrc else 0
    rep.add(bool(rsrc) and n_src > 0 and set(rsrc) == set(SR.REPO_STATUS),
            "V18i 来源 repo_status 三分类齐备（in_repo/not_in_repo/no_locator）",
            " ".join("%s=%s" % (k, rsrc.get(k)) for k in SR.REPO_STATUS) if rsrc
            else "缺 source_repo_status")
    # not_in_repo 的来源数 == 独立重算
    ind_rsm = SR.repo_status_map(ROOT)
    ind_rs = {k: sum(1 for v in ind_rsm.values() if v == k) for k in SR.REPO_STATUS}
    rep.add(rsrc == ind_rs, "V18i2 来源分列 == 独立重算 repo_status_map",
            "响应 %s / 独立 %s" % (rsrc, ind_rs))

    # V18i3 `/api/subgraph` 与 `/api/stats` 的图级读数必须一致（防两处各算一套）
    rep.add(stats_r.get("evidence_fidelity") == gcounts,
            "V18i3 /api/stats 与 /api/subgraph 的 evidence_fidelity_global 逐值一致",
            "stats=%s" % stats_r.get("evidence_fidelity") if stats_r.get("evidence_fidelity") == gcounts
            else "subgraph=%s ≠ stats=%s" % (gcounts, stats_r.get("evidence_fidelity")))

    # V18j 响应内每条边：fidelity ∈ 四分类或 null，且 stale ⇔ fidelity ∈ STALE_FIDELITIES
    bad_val = []
    for e in fid_r["edges"]:
        fv = e.get("fidelity")
        if fv is not None and fv not in Q.FIDELITY_CLASSES:
            bad_val.append((e["id"], fv))
    rep.add(not bad_val, "V18j 每条边 fidelity ∈ 四分类 ∪ {null}（无越界取值）",
            "越界=%s" % bad_val[:3] if bad_val else "%d 条合法" % len(fid_r["edges"]))

    # V18o ★ 证据等级（Phase 58）：每条边 grade ∈ {1..5} ∪ {null}；
    #   本环境登记表可用 ⇒ **不得出现 null**（null = 未判定，只在登记表不可用时合法）。
    bad_g = []
    for e in fid_r["edges"]:
        g = e.get("grade")
        if not (isinstance(g, int) and 1 <= g <= 5):
            bad_g.append((e.get("id"), g))
    rep.add(not bad_g and len(fid_r["edges"]) > 0,
            "V18o 每条边 grade ∈ {1..5}（登记表可用 ⇒ 无 null）",
            "越界=%s" % bad_g[:3] if bad_g else "%d 条合法" % len(fid_r["edges"]))

    # V18p ★ 等级统计两处口径各自闭合（V18i3 同族，防「统计与边各算一套」）：
    #   ① /api/stats 的 evidence_grade（全图）== 独立重算 `Q.grade_counts(b_all, reg)`；
    #   ② subgraph 响应 stats.evidence_grade（可见子图）== 响应内逐边重数；
    #   ③ 规则版本随行（前端要把它钉在 UI 上，缺了就是口径失踪）。
    gc_all = Q.grade_counts(b_all, reg)
    stats_g = stats_r.get("evidence_grade")
    stats_g6 = ({k: stats_g.get(k) for k in ("1", "2", "3", "4", "5", "null")}
                if isinstance(stats_g, dict) else None)
    sub_stats_g = ((fid_r.get("stats") or {}).get("evidence_grade")
                   if isinstance(fid_r.get("stats"), dict) else None)
    gc_sub = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "null": 0}
    for e in fid_r["edges"]:
        g = e.get("grade")
        gc_sub["null" if g is None else str(g)] += 1
    ok_p = (isinstance(stats_g, dict)
            and stats_g6 == gc_all and sub_stats_g == gc_sub
            and stats_g.get("n_edges_total") == len(b_all)
            and stats_g.get("rule") == Q.GRADE_RULE_VERSION)
    rep.add(ok_p,
            "V18p 等级分列两处口径各自闭合（全图重算 == /api/stats；子图重数 == 响应 stats）",
            "全图=%s stats=%s｜子图=%s resp=%s（rule=%s）"
            % (gc_all, stats_g6, gc_sub, sub_stats_g, stats_g.get("rule") if isinstance(stats_g, dict) else None))
    # V18q ★ Phase 60 **双口径分列**（铁律 #90）：`/api/stats` 必须同时给出
    #   `evidence_grade`（内部 e1）与 `evidence_grade_public`（对外 p1），且
    #   ① 键不同（**不合并**、不平均、不择优）；
    #   ② 各自带自己的规则版本（两个版本号**不同** ⇒ 共用一个就是"把两套写成一套"）；
    #   ③ 逐档单调：对外 ≥k 分的边数 **不得多于** 内部（对外不可能更可复算）。
    #   ★ 缺 `source_registry` ⇒ 记 SKIP（不可判定，不得假通过，铁律 #20）。
    try:
        sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
        import source_registry as SR2      # noqa: E402, F811
        gp_all = Q.grade_public_counts(b_all, reg, redist_of=SR2.is_redistributable)
    except Exception as ex:                # noqa: BLE001
        gp_all = None
        rep.note("V18q：source_registry 不可用（%s: %s）⇒ 对外口径记 SKIP" % (type(ex).__name__, ex))
    stats_gp = stats_r.get("evidence_grade_public")
    if gp_all is None:
        rep.add(None, "V18q 对外口径（p1）与内部口径（e1）在 /api/stats 上**分列**且不合并",
                "source_registry 不可用 ⇒ 不可判定（SKIP）")
    else:
        stats_gp6 = ({k: stats_gp.get(k) for k in ("1", "2", "3", "4", "5", "null")}
                     if isinstance(stats_gp, dict) else None)
        mono = all(sum(int(gp_all.get(str(g), 0)) for g in range(k, 6))
                   <= sum(int(gc_all.get(str(g), 0)) for g in range(k, 6))
                   for k in (3, 4, 5))
        rules = (stats_r.get("evidence_grade_rule"), stats_r.get("evidence_grade_public_rule"))
        ok_q = (isinstance(stats_gp, dict) and stats_gp6 == gp_all and mono
                and rules[0] == Q.GRADE_RULE_VERSION
                and rules[1] == Q.GRADE_PUBLIC_RULE_VERSION
                and rules[0] != rules[1])
        rep.add(ok_q,
                "V18q 对外口径（p1）与内部口径（e1）在 /api/stats 上**分列**且不合并",
                "对外=%s stats=%s｜单调(≥3/4/5)=%s｜规则=%s / %s"
                % (gp_all, stats_gp6, mono, rules[0], rules[1]))
    # V18k 【Phase 45 重写，铁律 #74】旧断言「stale ⇔ fidelity ∈ STALE_FIDELITIES」
    #   在 A04 接入后**不再成立**（依赖层可单独把一条边判 stale）。
    #   改守**真性质**：`stale ⇔ 字节层命中 ∪ 依赖层命中` —— 两处口径仍不漂移。
    bad_pair2 = []
    for e in fid_r["edges"]:
        fv = e.get("fidelity")
        sv = e.get("evidence_status")
        expect = ((fv in Q.STALE_FIDELITIES) if fv else False) \
            or (sv in Q.STALE_EVIDENCE_STATUSES)
        if bool(e.get("stale")) != expect:
            bad_pair2.append((e["id"], fv, sv, e.get("stale")))
    rep.add(not bad_pair2,
            "V18k 【重写】边 stale ⇔ (fidelity ∈ STALE_FIDELITIES) ∪ (evidence_status ∈ "
            "{stale,rejected}) —— 两层合流于一处，不漂移",
            "矛盾=%s" % bad_pair2[:3] if bad_pair2 else "一致（%d 条）" % len(fid_r["edges"]))

    # V18l 契约：带 fidelity 字段的响应仍通过 schema；反例（非法枚举）必须变红
    sch_sg = load_schema("subgraph.schema.json")
    rep.add(not validate(fid_r, sch_sg, sch_sg),
            "V18l 含 fidelity/fidelity_reasons 的响应通过 subgraph.schema.json")
    import copy as _copy
    inj_f = _copy.deepcopy(fid_r)
    if inj_f["edges"]:
        inj_f["edges"][0]["fidelity"] = "totally_bogus"
    rep.add(bool(validate(inj_f, sch_sg, sch_sg)),
            "V18m 反例：注入非法 fidelity ⇒ schema 必须变红（枚举非常真）")

    # V18n 负对照：四分类**恒等式**不是真空 —— 人为篡改 n_edges_total 必须破坏求和
    tamper = _copy.deepcopy(gcounts)
    tamper["n_edges_total"] = (tamper.get("n_edges_total") or 0) + 1
    tamper_sum = sum(tamper.get(k, 0) for k in classes)
    rep.add(tamper_sum != tamper["n_edges_total"],
            "V18n 负对照：篡改边总数 ⇒ 求和恒等式必然不成立（断言非真空）",
            "Σ=%d ≠ 篡改后总数=%d" % (tamper_sum, tamper["n_edges_total"]))

    # ---------- V19 A04 验证依赖状态（Phase 45：五态 + 两层合流 + 缺记录不假装） ----------
    #   本段守四件事：
    #     ① **跨模块枚举一致**：`queries` 与 `evidence_deps` 的状态/映射逐值相同（防漂移）；
    #     ② **可加总**：五态 + no_record 之和 == 边总数（含 `no_record` ⇒ 不把缺记录吞进 valid）；
    #     ③ **合流于一处**：`stale ⇔ 字节层 ∪ 依赖层`（V18k 已守），且 `needs_review` **不**被排除；
    #     ④ **构造性非真空**：人为把一条「字节层 verified」的边标成 A04 stale ⇒ 必须立即被排除
    #        （否则 A04 只是挂在响应里好看，并没有真的参与裁决）。
    print("\n[V19] A04 验证依赖状态（五态 / 两层合流 / 缺记录不假装 / 构造性非真空）")
    try:
        if "ED" not in dir():               # noqa: F821  （V18 已插 path）
            sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
            import evidence_deps as ED      # noqa: E402, F811
        ed_ok, ed_err = True, ""
    except Exception as e:  # noqa: BLE001
        ED, ed_ok, ed_err = None, False, "%s: %s" % (type(e).__name__, e)

    if not ed_ok:
        rep.skip("V19a–V19e A04 状态", "evidence_deps 不可导入：%s" % ed_err)
    else:
        # V19a 跨模块枚举与映射逐值一致（两处各写一份 = 必然漂移）
        rep.add(tuple(Q.EVIDENCE_STATUSES) == tuple(ED.STATUSES),
                "V19a queries.EVIDENCE_STATUSES == evidence_deps.STATUSES（跨模块防漂移）",
                "queries=%s / evidence_deps=%s" % (Q.EVIDENCE_STATUSES, ED.STATUSES))
        rep.add(dict(Q.FIDELITY_OF_EVIDENCE_STATUS) == dict(ED.FIDELITY_OF_STATUS),
                "V19a2 状态→四分类映射逐值一致（needs_review→unverifiable_no_source 等）",
                "%s" % Q.FIDELITY_OF_EVIDENCE_STATUS)
        rep.add(tuple(Q.STALE_EVIDENCE_STATUSES) == ("stale", "rejected"),
                "V19a3 计入排除的 A04 状态 == {stale, rejected}（needs_review 不排除）",
                "%s" % (Q.STALE_EVIDENCE_STATUSES,))

        # V19b 全图分列可加总（五键 + no_record）
        sc_glob = stats_r.get("evidence_status")
        keys5 = list(Q.EVIDENCE_STATUSES) + ["no_record"]
        have5 = bool(sc_glob) and all(k in sc_glob for k in keys5) and sc_glob.get("n_edges_total")
        ssum = sum(sc_glob.get(k, 0) for k in keys5) if have5 else None
        rep.add(bool(have5) and ssum == sc_glob.get("n_edges_total"),
                "V19b 全图 A04 分列齐备（五态 + no_record）且求和 == 边总数",
                " ".join("%s=%s" % (k, sc_glob.get(k)) for k in keys5)
                + "  Σ=%s / 总=%s" % (ssum, sc_glob.get("n_edges_total")) if have5
                else "字段缺失：%s" % sc_glob)

        # V19c 本次可见子图同样可加总（子图口径 == 响应边数）
        sc_loc = (fid_r["stats"].get("evidence_status"))
        lsum2 = sum(sc_loc.get(k, 0) for k in keys5) if sc_loc else None
        rep.add(lsum2 == len(fid_r["edges"]),
                "V19c 本次子图 A04 分列求和 == 本次返回边数",
                "Σ=%s / 边=%d" % (lsum2, len(fid_r["edges"])))

        # V19d 独立重算 == 响应（服务端不是另算一套）
        ind_sc = Q.evidence_status_counts(b_all, reg)
        diffs2 = [k for k in keys5 + ["n_excluded_by_status"]
                  if (ind_sc or {}).get(k) != (sc_glob or {}).get(k)]
        rep.add(not diffs2 and ind_sc is not None,
                "V19d 独立重算 A04 分列 == 响应 evidence_status_global",
                "全部一致" if not diffs2 else "差：%s" % {k: ((ind_sc or {}).get(k),
                                                              (sc_glob or {}).get(k))
                                                         for k in diffs2})

        # V19e 取值合法 + `source` 与 `null` 严格对应（缺记录 **不得**写成 valid）
        bad_sv, bad_src = [], []
        for e in fid_r["edges"]:
            sv = e.get("evidence_status")
            src_flag = e.get("evidence_status_source")
            if sv is not None and sv not in Q.EVIDENCE_STATUSES:
                bad_sv.append((e["id"], sv))
            if src_flag not in ("recorded", "absent"):
                bad_src.append((e["id"], "source=%r" % src_flag))
            elif (sv is None) != (src_flag == "absent"):
                bad_src.append((e["id"], "status=%r 与 source=%r 不自洽" % (sv, src_flag)))
        rep.add(not bad_sv, "V19e 每条边 evidence_status ∈ 五态 ∪ {null}（无越界取值）",
                "越界=%s" % bad_sv[:3] if bad_sv else "%d 条合法" % len(fid_r["edges"]))
        rep.add(not bad_src,
                "V19e2 缺记录 ⇒ status=null 且 source=absent（**不得**渲染成 valid）",
                "矛盾=%s" % bad_src[:3] if bad_src else "自洽（%d 条）" % len(fid_r["edges"]))

        # V19f `needs_review` **不**参与排除（铁律 #70：不可判定 ≠ 已变更）
        bad_nr = [e["id"] for e in fid_r["edges"]
                  if e.get("evidence_status") == "needs_review"
                  and (e.get("fidelity") not in Q.STALE_FIDELITIES)
                  and e.get("stale")]
        rep.add(not bad_nr,
                "V19f needs_review **不**导致 stale（缺数据本身 ≠ 已变更；否则排除开关自毁）",
                "误判=%s" % bad_nr[:3] if bad_nr else "无（%d 条 needs_review）"
                % sum(1 for e in fid_r["edges"] if e.get("evidence_status") == "needs_review"))

        # V19g stale/rejected **确实**参与排除（端到端，非截断子图）
        _, gb = http_get(port, "/api/subgraph"
                         + q(seeds="MX:def:manifold", mode="ego", hops=2,
                             max_nodes=4000, max_edges=8000, trust="all"))
        _, ge = http_get(port, "/api/subgraph"
                         + q(seeds="MX:def:manifold", mode="ego", hops=2,
                             max_nodes=4000, max_edges=8000, trust="all",
                             exclude_stale="1"))
        if gb["stats"].get("truncated_by"):
            rep.skip("V19g exclude_stale 端到端集合差",
                     "子图被截断（%s）⇒ 集合差不可精确判定" % gb["stats"]["truncated_by"])
        else:
            ids_b = {e["id"] for e in gb["edges"]}
            ids_e = {e["id"] for e in ge["edges"]}
            want = {e["id"] for e in gb["edges"] if e.get("stale")}
            cond = (not any(e.get("stale") for e in ge["edges"])
                    and ids_e <= ids_b and want <= (ids_b - ids_e)
                    and len(ids_e) < len(ids_b))
            rep.add(bool(cond) and bool(want),
                    "V19g exclude_stale：结果内无 stale、结果 ⊆ 基线、且所有 stale 边都被移出",
                    "基线 %d 边（含 stale %d）→ 排除后 %d 边"
                    % (len(ids_b), len(want), len(ids_e)))

        # V19h ★ 构造性非真空（铁律 #75）：人为把一条**字节层 verified**的边标 A04 stale
        fake = {"known": reg.get("known"), "hashes": reg.get("hashes"),
                "repo_status": reg.get("repo_status"),
                "status": dict(reg.get("status") or {})}
        target = next((e for e in b_all
                       if Q.edge_fidelity(e, reg) == "verified" and not Q.edge_is_stale(e, reg)),
                      None)
        if target is None:
            rep.skip("V19h 构造性 A04 排除", "找不到「字节层 verified 且当前不 stale」的边")
        else:
            tkey = Q.edge_key(target)
            fake["status"][tkey] = {"status": "stale",
                                    "reasons": ["ref_changed:dimension_table"],
                                    "source": "recorded"}
            rep.add((not Q.edge_is_stale(target, reg)) and Q.edge_is_stale(target, fake)
                    and bool(Q.stale_reasons(target, fake)),
                    "V19h 构造性：字节层 verified 的边被标 A04 stale ⇒ 立即被排除且给出理由",
                    "key=%s（标记前 stale=False，标记后 True）" % tkey)
            # V19i 同一条边标成 `needs_review` ⇒ **不得**被排除（反向对照）
            fake["status"][tkey] = {"status": "needs_review",
                                    "reasons": ["unverifiable_reference:dimension_table"],
                                    "source": "recorded"}
            rep.add(not Q.edge_is_stale(target, fake),
                    "V19i 反向对照：同一条边标成 needs_review ⇒ **不**被排除",
                    "key=%s result_stale=%s" % (tkey, Q.edge_is_stale(target, fake)))

        # V19j 负对照：篡改分列计数 ⇒ 求和恒等式必然破裂（断言非真空）
        tamper2 = dict(sc_glob or {})
        tamper2["valid"] = (tamper2.get("valid") or 0) + 1
        tamper2_sum = sum(tamper2.get(k, 0) for k in keys5)
        rep.add(tamper2 and tamper2_sum != tamper2.get("n_edges_total"),
                "V19j 负对照：篡改 valid 计数 ⇒ 五态求和必然不等于边总数（非真空）",
                "Σ=%s ≠ 总=%s" % (tamper2_sum, tamper2.get("n_edges_total")))

    # V19k 契约：含 A04 字段的响应通过 subgraph.schema.json；反例必须变红
    sch_sg2 = load_schema("subgraph.schema.json")
    rep.add(not validate(fid_r, sch_sg2, sch_sg2),
            "V19k 含 evidence_status 的响应通过 subgraph.schema.json")
    inj_s = _copy.deepcopy(fid_r)
    if inj_s["edges"]:
        inj_s["edges"][0]["evidence_status"] = "totally_bogus"
    rep.add(bool(validate(inj_s, sch_sg2, sch_sg2)),
            "V19l 反例：注入非法 evidence_status ⇒ schema 必须变红（枚举非常真）")
    # V19m `/api/stats` 与 `/api/subgraph` 的图级 A04 读数一致（防两处各算一套）
    rep.add(stats_r.get("evidence_status") == sc_glob,
            "V19m /api/stats 与 /api/subgraph 的 evidence_status_global 逐值一致",
            "stats=%s" % stats_r.get("evidence_status") if stats_r.get("evidence_status") == sc_glob
            else "subgraph=%s ≠ stats=%s" % (sc_glob, stats_r.get("evidence_status")))
    # V19n A04 登记摘要可用（参考表 + 验证器身份），且字节源为仓库
    a04 = stats_r.get("a04") or {}
    rep.add(bool(a04.get("available")) and (a04.get("n_references") or 0) > 0
            and (a04.get("byte_source") == "repo"),
            "V19n /api/stats 披露 A04 参考表登记（可用 + 字节源=repo）",
            "available=%s n_refs=%s byte_source=%s"
            % (a04.get("available"), a04.get("n_references"), a04.get("byte_source")))

    # ---------- V20 hidden_by_trust 必须是「不同边数」（Phase 50 · D45） ----------
    print("\n[V20] hidden_by_trust ＝ 被可信档位**排除的不同边数**（不是遍历判定次数）")
    # 背景（实测）：`mode=path` 是"路径 BFS"，同一个被拒边会在**每条部分路径**上被重复
    #   检查（`MX:chem:co2→MX:chem:combustion` 实测单条边最多 601 次、总判定 ~1.9e6 次）。
    #   旧实现用计数器 ⇒ 把 11,660 条边报成 62,640 条，**超过全图边总数 48,712**，
    #   且随 deadline 波动（同一查询两次不同值）。⇒ 改为 set 去重（#12：计数≠内容）。
    bk = V.get_backend()
    n_edges_total = len(bk._index()["edges"])
    _orig_fp = bk._find_paths
    _probe = {}

    def _spy_find_paths(a, b_, mp, mh, dl, edge_ok=None, t0=None, counter=None):
        """截获真实 `edge_ok`：既数"判定次数"，也数"被拒的不同边"。"""
        seen, calls = set(), [0]
        real = edge_ok

        def _w(e):
            calls[0] += 1
            ok = real(e)
            if not ok and not bk._trust_ok(e, "verified"):
                seen.add(e["id"])
            return ok

        out = _orig_fp(a, b_, mp, mh, dl,
                       edge_ok=_w if edge_ok is not None else None, t0=t0, counter=counter)
        _probe["last"] = {"calls": calls[0], "distinct": seen}
        return out

    bk._find_paths = _spy_find_paths
    try:
        r20 = bk.subgraph(seeds=["MX:chem:co2", "MX:chem:combustion"], mode="path",
                          trust="verified", max_paths=5, max_hops=5, deadline_ms=10000)
    finally:
        bk._find_paths = _orig_fp          # ★ 必须还原（共享后端实例，服务端也用它）
    got20 = r20["stats"]["hidden_by_trust"]
    dist20 = len(_probe["last"]["distinct"])
    calls20 = _probe["last"]["calls"]
    rep.add(got20 == dist20,
            "V20a hidden_by_trust == 同一次遍历里被拒边的**不同**边数",
            "报告=%d / 独立去重=%d" % (got20, dist20))
    rep.add(0 < got20 <= n_edges_total,
            "V20b 0 < hidden_by_trust ≤ 全图边数（旧实现的 62,640 > 48,712 会在此变红）",
            "%d ≤ %d" % (got20, n_edges_total))
    rep.add(calls20 > dist20,
            "V20c 负对照：探针有效（判定次数 ≫ 不同边数 ⇒ 计数器必然虚高）",
            "判定 %d 次 / 不同 %d 条 = %.1fx" % (calls20, dist20, calls20 / max(1, dist20)))
    _, eg20 = http_get(port, "/api/subgraph" + q(seeds="TH:fo:ideal_gas", mode="ego",
                                                trust="verified"))
    h20 = eg20["stats"]["hidden_by_trust"]
    rep.add(0 < h20 <= n_edges_total,
            "V20d ego 模式同样 ≤ 全图边数（两种模式共用同一套去重口径）",
            "ideal_gas hidden=%d ≤ %d" % (h20, n_edges_total))
    _, hp20 = http_get(port, "/api/subgraph"
                       + q(mode="path", trust="verified", max_paths=5, max_hops=5,
                           deadline_ms=10000, seeds="MX:chem:co2,MX:chem:combustion"))
    hh20 = hp20["stats"]["hidden_by_trust"]
    rep.add(0 < hh20 <= n_edges_total,
            "V20e HTTP 信封透传且仍 ≤ 全图边数（跨层口径一致，前端文案据此可成立）",
            "http=%d ≤ %d" % (hh20, n_edges_total))

    # ---------- V21 空结果诊断（F12 / D69） ----------
    #  ★ 用**合成图**而不是真图：真图里当前**没有**度为 0 的节点（现算 0 个），
    #    最关键的「孤立 ⇒ 不给放宽按钮」那条分支在真图上根本走不到。
    #    判据看不见的分支等于没判据（#118 的读法：上一次没跑的那台仪器）。
    print("\n[V21] 空结果诊断：单条件反事实 + 放宽的边界（合成图，4 节点 3 边）")
    import shutil
    import tempfile
    _fx = {
        "nodes": [
            {"id": "N:iso", "labels": ["Definition"], "props": {"domain": "d1"}},
            {"id": "N:a", "labels": ["Definition"], "props": {"domain": "d1"}},
            {"id": "N:b", "labels": ["Quantity"], "props": {"domain": "d2"}},
            {"id": "N:c", "labels": ["Definition"], "props": {"domain": "d1"}},
        ],
        "edges": [
            {"id": "E:low", "source": "N:a", "target": "N:b", "type": "has_symbol",
             "kind": "has_symbol", "props": {"verification_level": "model_inferred"}},
            {"id": "E:high", "source": "N:a", "target": "N:c", "type": "has_symbol",
             "kind": "has_symbol", "props": {"verification_level": "by_construction"}},
            {"id": "E:mid", "source": "N:b", "target": "N:c", "type": "has_quantity",
             "kind": "has_quantity", "props": {"verification_level": "by_construction"}},
        ],
    }
    _tmp = tempfile.mkdtemp(prefix="sttp-f12-")
    _fxp = os.path.join(_tmp, "fixture.json")
    with open(_fxp, "w", encoding="utf-8") as _f:
        json.dump(_fx, _f, ensure_ascii=False)
    _fb = Q.NXBackend(_fxp)

    def _nt(labels):
        return (labels or ["Unknown"])[0]

    def _so(domain, nid):
        return domain or ""

    def _eg(**kw):
        kw.setdefault("node_type_of", _nt)
        kw.setdefault("subject_of_fn", _so)
        return _fb.subgraph(**kw)

    def _dg(r):
        return r.get("stats", {}).get("empty_diagnosis") or {}

    def _cn(d):
        return {x["key"]: x["n"] for x in (d.get("counts") or [])}

    r = _eg(seeds=["N:iso"], mode="ego", hops=1, trust="all")
    d0 = _dg(r)
    rep.add(r["status"] == "ok" and not r["edges"] and d0.get("why") == "isolated"
            and d0.get("action") is None and d0.get("degree") == 0,
            "V21a 孤立节点 ⇒ why=isolated 且**无放宽动作**（放宽必然无效，给了就是假动作）",
            "%s why=%s action=%s degree=%s"
            % (r["status"], d0.get("why"), d0.get("action"), d0.get("degree")))
    r = _eg(seeds=["N:a"], mode="ego", hops=1, trust="all")
    rep.add(len(r["edges"]) > 0 and "empty_diagnosis" not in r["stats"],
            "V21a' 反例：有边的种子 ⇒ 不产生诊断（证明 isolated 不是恒真）",
            "edges=%d" % len(r["edges"]))

    r = _eg(seeds=["N:a"], mode="ego", hops=1, trust="verified", kinds="__none__")
    d1 = _dg(r)
    a1 = d1.get("action") or {}
    c1 = _cn(d1)
    rep.add(d1.get("why") == "filtered" and a1.get("key") == "kinds" and c1.get("kinds") == 1,
            "V21b 两个条件同时生效时，只选出**有效**的那一项（kinds=1 条 / trust=0 条）",
            "action=%s gain=%s counts=%s" % (a1.get("key"), a1.get("gain"), c1))
    rep.add(a1.get("key") in c1 and c1.get(a1.get("key"), 0) > 0,
            "V21b' 一致性：建议的动作必须是**计数为正**的那一项（不得建议放宽一个无效项）",
            "%s → %s" % (a1.get("key"), c1.get(a1.get("key"))))

    r = _eg(seeds=["N:a"], mode="ego", hops=1, trust="all", kinds="__none__")
    d2 = _dg(r)
    a2 = d2.get("action") or {}
    rep.add(a2.get("gain") == 2 and _cn(d2).get("kinds") == 2,
            "V21c 同一条边、换可信档后反事实计数**跟着变**（1 → 2）⇒ 是真复算，不是常量",
            "gain=%s kinds=%s" % (a2.get("gain"), _cn(d2).get("kinds")))

    r = _eg(seeds=["N:a"], mode="ego", hops=1, trust="verified")
    rep.add(len(r["edges"]) == a1.get("gain"),
            "V21d **承诺 == 交付**：按诊断建议放宽后重跑，实得边数 == 承诺的 gain",
            "承诺 %s 条 / 实得 %d 条" % (a1.get("gain"), len(r["edges"])))

    r = _eg(seeds=["N:a"], mode="ego", hops=0, trust="all")
    d3 = _dg(r)
    rep.add(d3.get("why") == "hops_zero" and (d3.get("action") or {}).get("key") == "hops",
            "V21e 跳数=0 ⇒ 单列为 hops_zero（放大可信档救不回来），动作＝增跳数",
            "%s → %s" % (d3.get("why"), (d3.get("action") or {}).get("key")))

    r = _eg(seeds=["DOES:NOT:EXIST"], mode="ego")
    d4 = _dg(r)
    rep.add(r["status"] == "no_result" and d4.get("why") == "missing_seed"
            and d4.get("action") is None,
            "V21f 种子不存在 ⇒ **不给任何放宽动作**（放宽救不了一个不存在的名字）",
            "%s why=%s" % (r["status"], d4.get("why")))

    r = _eg(mode="filter", kinds="has_symbol", subjects="d2")
    d5 = _dg(r)
    a5 = d5.get("action") or {}
    rep.add(r["status"] == "no_result" and a5.get("key") == "subjects" and a5.get("gain") == 1,
            "V21g filter 模式：学科限定把两个端点拆开 ⇒ 建议放宽学科，而不是瞎猜边级条件",
            "action=%s gain=%s" % (a5.get("key"), a5.get("gain")))
    rep.add(d5.get("scanned") == len(_fx["edges"]) and d5.get("scan_complete") is True,
            "V21h 诊断必须自报扫描范围（`scanned`）：**0 条 ≠ 没扫过**（D110b）",
            "scanned=%s complete=%s" % (d5.get("scanned"), d5.get("scan_complete")))

    r = _eg(seeds=["N:a", "N:iso"], mode="path", hops=1, max_hops=2, trust="all")
    d6 = _dg(r)
    rep.add(d6.get("counterfactual") is False
            and (d6.get("action") or {}).get("key") == "hops"
            and d6.get("counts") == [],
            "V21i path 模式**不做**反事实计数（连通性不由单条件决定）⇒ counterfactual=false",
            "action=%s counts=%s" % ((d6.get("action") or {}).get("key"), d6.get("counts")))

    # 诊断只能出现在「真空」时：有结果还必须给诊断 ⇒ 每次查询都在渲染无用的解释块
    r = _eg(seeds=["N:a"], mode="ego", hops=1, kinds="has_symbol", trust="all")
    rep.add(len(r["edges"]) == 2 and "empty_diagnosis" not in r["stats"],
            "V21j 非空结果 ⇒ 不带诊断（诊断只在真空时出现，不做常驻噪音）",
            "edges=%d" % len(r["edges"]))
    shutil.rmtree(_tmp, ignore_errors=True)

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
