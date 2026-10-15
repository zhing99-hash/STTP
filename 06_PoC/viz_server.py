# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""
公式知识图谱 · Phase 4 轻量可视化后端（viz_server.py）
==================================================================
只用 Python 标准库 ``http.server``（**不引入 Flask**），把可视化前端
（graph_view.html）对接到真实查询层（``03_知识层/queries.py`` 的 NetworkX
后端），形成「查询 → 渲染」交互闭环。

端点
----
    GET /                        → graph_view.html（壳；★ Phase 43 起默认「查询面板 + 局部子图」）
    GET /vendor/<file>           → 本地化的前端依赖（Cytoscape / fcose / MathJax；A4）
    GET /js/<file>.js            → ★ Phase 43 前端 ES Module（零构建；必须 text/javascript）
    GET /graph_data.json         → 导出的图谱数据（缺失时即时调用 graph_export）
                                   ★ 仅供 `mode=bundle`（离线样例/小图）使用；live 模式不请求它
    GET /api/neighbors?node=<id> → queries.get_neighbors（返回 {node, neighbors, edges}）
    GET /api/stats               → 图谱统计
    ★ Phase 43（复评 A06 合并）—— 局部图谱查询：
    GET /api/search?q=…          → 文本搜索（前 N 命中 + 分数）
    GET /api/subgraph?…          → ★ 局部子图（ego / path / filter 三模式，五态 + 预算）
    GET /api/path?src=&dst=      → 有界可解释路径（改造：不再无界物化、不吞异常）
    GET /api/overview            → 概览元图（替代"全图浏览"，铁律 #66）

统一信封（所有 `/api/*` 查询端点）
----------------------------------
    {status, reason, error_code, snapshot_id, query, query_hash,
     hidden_by_trust, nodes?, edges?, expandable?, stats}
    status ∈ {ok, no_result, truncated, timeout, error}   ← 铁律 #67
    4xx 只用于**参数非法**；业务态的"空/截断/超时"一律 HTTP 200（便于前端统一处理）。

两层证据判据（Phase 44 字节忠实度 / Phase 45 验证依赖状态）
----------------------------------------------------------
    edge.fidelity        ∈ {verified, changed, unverifiable_no_source,
                            unverifiable_not_in_repo}          ← **边级字节**
    edge.evidence_status ∈ {valid, stale, needs_review, rejected} | null  ← **证据级依赖**
    ★ 二者**并列披露，不得互相替代**：前者只答「来源那个字节文件变了吗」，
      后者还答「它所依赖的参考表/验证器/命题本身变了吗」。
    ★ `exclude_stale` 的**唯一**判据是 `queries.edge_is_stale()`：它把两层**合流**
      （字节 ∈ STALE_FIDELITIES ∪ 状态 ∈ {stale, rejected}）。
      `needs_review`（本环境不可判定 = 缺数据本身）**不**被排除 —— 否则
      `exclude_stale` 会退化成「几乎清空全图」（铁律 #70），但**始终披露**。
    ★ 缺记录 ≠ 已验证：图上无 A04 记录时 `evidence_status=null` +
      `evidence_status_source="absent"`，**不得**写成 `"valid"`。

数据源（**页面与查询必须同版本**）
----------------------------------
    GRAPH_DATA_FILE   → 页面 `/graph_data.json` 读的前端投影（schema=formula-graph-view/v1）
    GRAPH_SOURCE_FILE → 查询后端 `/api/*` 读的规范图（nodes[].props / edges[].props）
    ★ Phase 37（R06）：两者应指向**同一份切片**的两种投影；发布样例由
      `bash sttp.sh sample` 同时设定，并由 `verify_release.py` 断言 id 集合逐元素相等。
    ★ Phase 43：所有响应带 `snapshot_id`（查询源 sha256[:12]）⇒ 图变了 id 就变，
      前端缓存与"复现同一次查询"自动失效。

健壮性
------
* import 守卫：networkx / queries 缺失时给出友好提示而非崩溃；
  queries 模块路径通过 ``sys.path`` 注入 ``03_知识层``。
* 未知路径返回 404；API 响应 ``application/json``，页面 ``text/html``。
* 全部响应带 CORS 头，便于跨源打开 / file:// 调试。
* 绑定 ``127.0.0.1:<port>``（默认 8765），启动打印访问 URL；
  ``Ctrl+C``（KeyboardInterrupt）优雅退出。

用法
----
    python viz_server.py                 # 默认 127.0.0.1:8765
    python viz_server.py --port 8080
    set VIZ_PORT=9000 && python viz_server.py

author: 可视化与前端专家 | 2026-09-28
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# Windows 控制台 UTF-8 修复
# 约定（A5 轮教训）：**原地 reconfigure()**，绝不替换 sys.stdout 对象。
# `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)` 会让旧 wrapper 进入 GC，
# 其 __del__ 连带关闭同一个底层 fd，此后进程内所有 print 抛
# `ValueError: I/O operation on closed file` —— 长驻服务会静默变成"端口在监听但响应为空"。
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    del _stream

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))          # STTP/06_PoC
ROOT = os.path.dirname(HERE)                                # STTP
KNOWLEDGE_DIR = os.path.join(ROOT, "03_知识层")             # queries.py 所在
ETL_DIR = os.path.join(HERE, "etl")
WITH_INFERRED = os.path.join(ETL_DIR, "with_inferred.json")
NORMALIZED = os.path.join(ETL_DIR, "normalized.json")
GRAPH_DATA = os.environ.get("GRAPH_DATA_FILE", os.path.join(HERE, "graph_data.json"))
HTML_FILE = os.path.join(HERE, "graph_view.html")
VENDOR_DIR = os.path.join(HERE, "vendor")                   # A4：本地化的前端依赖（Cytoscape / MathJax）
JS_DIR = os.path.join(HERE, "js")                           # ★ Phase 43：前端 ES Module（零构建）
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("PORT", "8765"))

# /vendor/* 允许的扩展名 -> MIME（白名单，避免任意文件外泄）
VENDOR_MIME = {
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
}

# 把 03_知识层 注入 sys.path，使 `import queries` 可用
if KNOWLEDGE_DIR not in sys.path:
    sys.path.insert(0, KNOWLEDGE_DIR)

# ---------------------------------------------------------------------------
# 懒加载：查询层后端（NetworkX）
# ---------------------------------------------------------------------------
_QUERIES = None          # 模块对象 / False（导入失败）
_BACKEND = None          # 后端实例 / False（初始化失败）
# ★ Phase 49：进程是 ThreadingHTTPServer（每请求一线程）⇒ 后端初始化必须**只发生一次**，
#   否则「前台请求 + 后台预热」会并发各加载一遍 67.7 MB 权威图。
_BACKEND_LOCK = threading.Lock()
_WARM_LOCK = threading.Lock()
_WARM = {"started": False, "done": False, "error": None, "t0": None, "ms": None}


def _load_queries():
    """import 守卫：加载 03_知识层/queries.py。"""
    global _QUERIES
    if _QUERIES is None:
        try:
            import queries  # type: ignore
            _QUERIES = queries
            print(f"[OK] 已加载查询层：{os.path.join(KNOWLEDGE_DIR, 'queries.py')}")
        except Exception as e:  # noqa: BLE001
            _QUERIES = False
            print(f"[WARN] 无法加载 queries.py（{type(e).__name__}: {e}），"
                  f"API 将返回降级提示。")
    return _QUERIES


def _graph_path():
    """查询层数据源（**必须与页面同版本**）。

    Phase 37（GPT6 复评 R06）：原先本函数只认 `with_inferred.json` / `normalized.json`，
    而页面数据源是 `GRAPH_DATA_FILE` —— 两者可被各自指向**不同版本**，正是复评指出的
    「页面和查询可能不属于同一数据版本」缺口。现允许用 `GRAPH_SOURCE_FILE` **显式钉死**：
    发布样例把页面指到 `sample/sample_graph.json`、把查询指到
    `sample/sample_normalized.json`（同一份切片的两种投影，id 集合逐元素相等）。

    优先级：GRAPH_SOURCE_FILE > with_inferred.json > normalized.json
    """
    explicit = os.environ.get("GRAPH_SOURCE_FILE")
    if explicit:
        p = explicit if os.path.isabs(explicit) else os.path.join(ROOT, explicit)
        if os.path.exists(p):
            return p
    return WITH_INFERRED if os.path.exists(WITH_INFERRED) else NORMALIZED


def get_backend():
    """获取 NetworkX 后端（失败返回 None，不抛异常）。

    ★ 双检锁：首次初始化在**锁内**完成。否则 ThreadingHTTPServer 下
      「/api/stats 前台上报 + /api/healthz 触发后台预热」会并发跑两遍
      `q.get_backend("nx", …)` —— 两遍 67.7 MB 解析、两份内存图。
      ★ 锁**只包住初始化**，不包住调用方对后端的读（铁律 #79：持锁时不得调用
      可能再取同一把锁的函数）。
    """
    global _BACKEND
    if _BACKEND is None:
        with _BACKEND_LOCK:
            if _BACKEND is None:                      # 双检：可能已被另一线程建好
                q = _load_queries()
                if not q:
                    _BACKEND = False
                    return None
                path = _graph_path()
                try:
                    # 走 queries.py 的官方工厂：强制 networkx 后端，指向 with_inferred
                    _BACKEND = q.get_backend("nx", input_path=path)
                    # ★ 预热索引（一次性的 48712 边建索引 ~0.7 s）：
                    #   `deadline_ms` 只应度量**查询本身**，不应把冷启动建索引算进用户预算。
                    _BACKEND.node_count
                    print(f"[OK] NetworkX 后端就绪：{_BACKEND.node_count} 节点 / "
                          f"{_BACKEND.edge_count} 边（源：{os.path.basename(path)}）")
                except BaseException as e:  # noqa: BLE001  # 含 SystemExit（queries 缺源会 sys.exit）
                    _BACKEND = False
                    print(f"[WARN] NetworkX 后端初始化失败（{type(e).__name__}: {e}），"
                          f"API 将降级；/graph_data.json 不受影响。")
    return _BACKEND or None


# ---------------------------------------------------------------------------
# Phase 49 · 后台预热 + 轻量探活（修外部评审 P0-2：冷启动被误判离线）
# ---------------------------------------------------------------------------
# 实测：权威图 67.7 MB ⇒ 冷进程首次 `/api/stats` = **4.91 s**，而前端探活预算 **2 s**
# ⇒ 浏览器 abort ⇒ `detectMode` 判「后端不可用」⇒ 退 bundle ⇒ 首次打开就像坏了。
# ★ 2026-10-15 把 4.91 s **拆开**（单函数计时，见 `_warm_worker` 文档串）：
#     4.91 s 的主体**不是**「图没装进来」，而是 `fidelity_counts_global()` 冷算（4.27 s）
#     —— 即便图早已加载，首次 `/api/stats` 仍要 ~4.9 s。所以「只预热图」是不够的。
# 处方：① `/api/healthz` **不触碰图谱数据**，冷启动期间也毫秒级回答（实测 191 ms）；
#       ② 首次被探活时**异步预热**，把「加载权威图」**与「stats 冷算缓存」**一并建好，
#          与「用户读题、输入、确认实体」重叠；
#       ③ `main()` **先 bind 再预热**（见 main 内 F1 注释）：端口立刻可用、页面秒开。
# （锁与状态容器在文件头部声明，见 `_BACKEND_LOCK` / `_WARM_LOCK` / `_WARM`。）


def _warm_worker():
    """后台预热：把 67.7 MB 权威图的加载与索引建好，避免它落在用户第一次查询上。

    ★ Phase 49 修（F2，2026-10-15 实测）：**必须连 `/api/stats` 的冷算一起预热**。
      实测分段（本机 Python 3.11）：
        `get_backend()`                    = 2319 ms（加载 67.7 MB 权威图）
        `fidelity_counts_global()`         = **4270 ms** ← 单项最大
        `evidence_status_counts_global()`  = 567 ms
        `a04_registry_summary()`           = 111 ms
        `graph_stats()` 自身迭代（不可缓存）= ~308 ms
      ⇒ `/api/stats` **首次 ≈ 4.9 s**、其后 ≈ 0.31 s。
      这就是 P0-2 的**真正根因**：前端拿 `/api/stats` 当探活、预算 2 s ⇒ 必然 abort。
      `/api/healthz` 解决「探活不准」；本处预热解决「首次 stats 仍然慢」——两者缺一不可。
      预热后 `ready` 才真正代表「整条读路径已热」，而不只是「图装进来了」。
    """
    t0 = time.time()
    try:
        b = get_backend()
        if b is None:
            raise RuntimeError("查询层不可用（queries.py 或权威图缺失）")
        _ = b.node_count          # ★ 与 /api/stats 同口径地触发索引预热
        graph_stats()             # ★ F2：把忠实度/A04 分列计数（一次性 ~4.8 s）也建好
        _WARM["ms"] = int((time.time() - t0) * 1000)
        _WARM["done"] = True
        print(f"[OK] 预热完成：{b.node_count} 节点 / {b.edge_count} 边"
              f"（{_WARM['ms']} ms，后台线程；含 `/api/stats` 冷算缓存）")
    except BaseException as e:  # noqa: BLE001
        _WARM["error"] = f"{type(e).__name__}: {e}"
        print(f"[WARN] 预热失败：{_WARM['error']}")


def kick_warmup():
    """幂等启动预热线程（daemon：进程退出不阻塞）。"""
    with _WARM_LOCK:
        if _WARM["started"]:
            return False
        _WARM["started"] = True
        _WARM["t0"] = time.time()
    threading.Thread(target=_warm_worker, name="sttp-warmup", daemon=True).start()
    return True


def warm_state():
    """预热状态：`ready` / `warming` / `failed`（**三态分列**，不把 warming 混进 failed）。"""
    if _WARM["error"] is not None:
        return "failed"
    if _WARM["done"]:
        return "ready"
    return "warming"


def backend_ready() -> bool:
    return _BACKEND is not None and _BACKEND is not False


# ---------------------------------------------------------------------------
# Phase 43 · 局部图谱查询：投影口径 / stale 登记 / 会话预算 / expand_token
# ---------------------------------------------------------------------------
def _gx():
    """投影口径模块（`06_PoC/graph_export.py`）——全图与子图**共用同一份口径**。"""
    try:
        import graph_export  # 同目录
        return graph_export
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] graph_export 不可用（{type(e).__name__}: {e}）；子图端点将降级。")
        return None


_SOURCE_REGISTRY = None       # 模块对象 / False
_EVIDENCE_DEPS = None         # 模块对象 / False（A04 · Phase 45）
_STALE_CACHE = {"key": None, "val": None}
# ★ 必须用 **RLock**（可重入）：`stale_registry()` 会在持锁期间调用
#   `evidence_status_registry()`，而后者也要拿同一把锁。用 `threading.Lock()` 会
#   **自死锁**（单线程也一样：CPU≈0、无子进程、永久挂起 —— Phase 45 实测踩到，登记为 E11）。
#   已同时把嵌套调用移出锁外（双保险），但保留 RLock 以防将来再引入同类嵌套。
_STALE_LOCK = threading.RLock()
_FIDELITY_CACHE = {"key": None, "val": None}
_EVIDENCE_STATUS_CACHE = {"key": None, "val": None}

# ★ Phase 49 · 单飞（single-flight）构建锁（2026-10-15 实测新增）。
#   上面三个 `*_CACHE` 的既有实现都是「锁内查缓存、**锁外**算、锁内写回」——
#   后果：**并发调用会各算一遍**（同一次冷算 4.3 s 被算两遍，CPU 互相抢占）。
#   实测证据：`main()` 的后台预热线程在跑 `graph_stats()` 的同时，一个立刻到达的
#   `/api/stats` 请求也在跑同一份 `fidelity_counts_global()` ⇒ 该请求由 4.9 s
#   **恶化到 8.5 s**。ThreadingHTTPServer 每请求一线程，两个标签页即可触发。
#   处方：给每份「贵且幂等」的派生缓存各配一把**构建锁**，把「双检 + 计算 + 回写」
#   整体串行化 —— 与 `get_backend()` 的双检锁同一模式。
#   锁序恒为 BUILD → CACHE（无反向嵌套）⇒ 不会成环（铁律 #79）。
_STALE_BUILD_LOCK = threading.Lock()
_FIDELITY_BUILD_LOCK = threading.Lock()
_ESTATUS_BUILD_LOCK = threading.Lock()
# ★ 为什么把「实际算了几次」也暴露出来：单飞的正确性若只靠**测时间**来断言，
#   就会在快机器上假绿、在慢机器上假红。计数器让 O(1) 的因果可被直接观测：
#   两个并发冷请求 ⇒ `fidelity` 计数必须是 **1**（不是 2）。见 H8l。
#   （与「北极星须仪器」「断言须可构造」同一取向：把性质变成可读的数字。）
_BUILD_STATS = {"stale": 0, "fidelity": 0, "estatus": 0}


def build_stats() -> dict:
    """快照当前构建计数（healthz 暴露；只读、O(1)、不触发任何计算）。"""
    with _STALE_LOCK:
        return dict(_BUILD_STATS)
#: 环境开关：A04 状态映射（默认开）。设 `STTP_EVIDENCE_STATUS=0` 可关闭
#  —— 供「A04 未落图 / 想复现 Phase 44 行为」的对照实验，而**不是**为了静默退化。
_EVIDENCE_STATUS_ENABLED = os.environ.get("STTP_EVIDENCE_STATUS", "1").strip().lower() \
    not in ("0", "false", "no", "off")


def _load_source_registry():
    """import 守卫：加载 `11_真实数据/source_registry.py`（stale 判定的登记表）。"""
    global _SOURCE_REGISTRY
    if _SOURCE_REGISTRY is None:
        d = os.path.join(ROOT, "11_真实数据")
        try:
            if d not in sys.path:
                sys.path.insert(0, d)
            import source_registry  # type: ignore
            _SOURCE_REGISTRY = source_registry
        except Exception as e:  # noqa: BLE001
            _SOURCE_REGISTRY = False
            print(f"[WARN] 无法加载 source_registry（{type(e).__name__}: {e}）；"
                  f"exclude_stale 将不可用。")
    return _SOURCE_REGISTRY


def _load_evidence_deps():
    """import 守卫：加载 `11_真实数据/evidence_deps.py`（A04 验证依赖模型）。

    不可用时的行为是**显式降级**：`stale_registry()` 不带 `status` ⇒
    `queries.edge_is_stale()` 回落到四分类口径（Phase 44 行为），
    且 `/api/stats` 的 `a04.available=false` 会说明原因 —— **不静默假装没有这一层**。
    """
    global _EVIDENCE_DEPS
    if _EVIDENCE_DEPS is None:
        d = os.path.join(ROOT, "11_真实数据")
        try:
            if d not in sys.path:
                sys.path.insert(0, d)
            import evidence_deps  # type: ignore
            _EVIDENCE_DEPS = evidence_deps
        except Exception as e:  # noqa: BLE001
            _EVIDENCE_DEPS = False
            print(f"[WARN] 无法加载 evidence_deps（{type(e).__name__}: {e}）；"
                  f"A04 验证依赖状态将不可用（回落到四分类口径）。")
    return _EVIDENCE_DEPS


def evidence_status_registry(edges, src_hashes=None):
    """现算**全图** A04 状态映射 `{edge_id: {status, reasons, source, n_evidence,
    n_with_deps, evidence:[{evidence_id, status, reasons, verifier, refs:[{ref_id,
    binding, content_hash}], src_hash}]}}`。

    ★ 为什么在服务端一次性算好（而不是每边现算）：A04 的 `evidence_status()`
      需要**当前**参考表/验证器快照，而它**与边无关**（全图共用一份）。逐边现算会
      重复读 5 张参考表 × 49572 条证据 ⇒ 纯浪费；且逐边现算极易出现"两条边用不同快照"
      的静默不一致。这里**先算一次 current，再对全部证据用同一份 current**（铁律 #40）。
    ★ 缓存键 = HEAD sha：参考表/验证器身份与证据记录都是"那一次提交的字节"的函数，
      HEAD 不变 ⇒ 结果不变（与 `stale_registry` 同键，两处口径一致）。
    ★ 无 A04 记录 ⇒ 返回 **{}**（不是 None）—— 与「模块不可用」区分开。
    """
    ed = _load_evidence_deps()
    q = _load_queries()
    if not ed or not q:
        return None
    base = ROOT
    try:
        key = _head_sha(base)
        with _STALE_LOCK:
            if _EVIDENCE_STATUS_CACHE["key"] == key and _EVIDENCE_STATUS_CACHE["val"] is not None:
                return _EVIDENCE_STATUS_CACHE["val"]
        ledger = ed.load_ledger(base)
        cur = ed.current_snapshot(base=base, byte_source="repo", src_hashes=src_hashes)
        out = {}
        for e in edges:
            props = e.get("props") or {}
            evs = props.get("verification_evidence") or []
            if not evs:
                continue
            recs = []
            worst, worst_rank, reasons_all = None, -1, []
            for idx, ev in enumerate(evs):
                if not isinstance(ev, dict):
                    continue
                if not ed.has_recorded_deps(ev):
                    continue          # 未带 A04 记录的证据**跳过**，不编造状态
                rc = ed.recorded_deps(ev)
                eid = ev.get("evd") or ed.evidence_id(q.edge_key(e), idx, ev)
                declared = set(ed.impl_refs(ev.get("impl")) or [])
                st, rs = ed.evidence_status(rc, cur, claim_rejected=bool(props.get("rejected")),
                                            ledger_entry=ledger.get(eid))
                rank = q.EVIDENCE_STATUS_RANK.get(st, -1)
                if rank > worst_rank:
                    worst, worst_rank = st, rank
                for r in rs:
                    if r not in reasons_all:
                        reasons_all.append(r)
                recs.append({
                    "evidence_id": eid,
                    "index": idx,
                    "status": st,
                    "reasons": rs,
                    "src": ev.get("src"),
                    "src_hash": ev.get("src_hash"),
                    "verifier": ev.get("vfr"),
                    "verifier_code_hash": ev.get("vch"),
                    "refs": [{"ref_id": k, "content_hash": v,
                              "binding": "impl_declared" if k in declared else "code_import"}
                             for k, v in (ev.get("refs") or {}).items()],
                })
            if not recs:
                continue
            out[q.edge_key(e)] = {
                "status": worst, "reasons": reasons_all, "source": "recorded",
                "n_evidence": len(evs), "n_with_deps": len(recs), "evidence": recs,
            }
        with _STALE_LOCK:
            _EVIDENCE_STATUS_CACHE["key"] = key
            _EVIDENCE_STATUS_CACHE["val"] = out
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] A04 状态映射不可用（{type(e).__name__}: {e}）")
        return None


_A04_SUMMARY_CACHE = {"key": None, "val": None}


def a04_registry_summary() -> dict:
    """A04 参考表登记摘要（`/api/stats` 用；不返回全量以控体积）。按 HEAD 缓存。"""
    ed = _load_evidence_deps()
    if not ed:
        return {"available": False, "reason": "evidence_deps 模块不可用"}
    try:
        key = _head_sha(ROOT)
        with _STALE_LOCK:
            if _A04_SUMMARY_CACHE["key"] == key and _A04_SUMMARY_CACHE["val"]:
                return _A04_SUMMARY_CACHE["val"]
        out = _a04_registry_summary_uncached(ed)
        with _STALE_LOCK:
            _A04_SUMMARY_CACHE["key"] = key
            _A04_SUMMARY_CACHE["val"] = out
        return out
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": "%s: %s" % (type(e).__name__, e)}


def _a04_registry_summary_uncached(ed) -> dict:
    try:
        reg = ed.load_registry(ROOT)
        refs = (reg or {}).get("references") or {}
        return {
            "available": True,
            "schema": (reg or {}).get("schema"),
            "byte_source": (reg or {}).get("byte_source"),
            "n_references": len(refs),
            "references": {rid: {"kind": v.get("kind"), "version": v.get("version_label"),
                                 "content_hash": v.get("content_hash")}
                           for rid, v in refs.items()},
            "verifiers": {vid: {"code_hash": v.get("code_hash"),
                                "n_dependency_versions": len(v.get("dependency_versions") or {})}
                          for vid, v in ((reg or {}).get("verifiers") or {}).items()},
            "status_enabled": _EVIDENCE_STATUS_ENABLED,
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": "%s: %s" % (type(e).__name__, e)}


def _head_sha(base):
    """当前 HEAD 的 sha（拿不到则为 None）。

    ★ 为什么缓存键要看它（Phase 44 · D1）：权威哈希口径已改为**仓库字节**
      （`git cat-file blob HEAD:<path>`）⇒ **只有 HEAD 变化才会改变当前哈希**。
      过去按 locator 的 `mtime/size` 做键，隐含「哈希依赖工作区」的旧假设；
      HEAD 变更才是真依赖（且自检里「提交后重新读」必须能看见新 blob）。
    """
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=base,
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def stale_registry():
    """登记快照 `{known, hashes, repo_status}`，用于 `exclude_stale` 与忠实度分列。

    ★ 缓存键 = `(HEAD sha, 每源的 lineage_recorded)`。HEAD 变（提交/切换）⇒ 键变 ⇒ 重算。
      不再把工作区 `mtime/size` 计入键：字节源既已是仓库 blob，工作区改动**本来就不该**
      影响裁决（这正是 D1 修复要的性质）。
    ★ `hashes` 只对**可从仓库复算**的来源有值；未入库 / 无 locator ⇒ None，
      由 `queries.edge_fidelity` 分列（`unverifiable_not_in_repo` / `unverifiable_no_source`）。
    """
    sr = _load_source_registry()
    q = _load_queries()
    if not sr or not q:
        return None
    base = getattr(sr, "ROOT", ROOT)
    key = (_head_sha(base),
           tuple((sid, bool((sr.get(sid) or {}).get("lineage_recorded"))) for sid in sr.ids()))
    with _STALE_LOCK:
        if _STALE_CACHE["key"] == key and _STALE_CACHE["val"] is not None:
            return _STALE_CACHE["val"]
    # ★ Phase 49：整体放进 `_STALE_BUILD_LOCK`（单飞）—— 见文件头单飞锁说明。
    #   注意 `_STALE_LOCK` 自身仍是 **RLock**（E11 的历史原因保留），但计算在构建锁内、
    #   缓存锁外，行为与原先一致，只是**并发不再各算一遍**。
    with _STALE_BUILD_LOCK:
        with _STALE_LOCK:                      # 双检：等锁期间可能已被别人建好
            if _STALE_CACHE["key"] == key and _STALE_CACHE["val"] is not None:
                return _STALE_CACHE["val"]
        # ★ 计算在 `_STALE_LOCK` **锁外**做：`evidence_status_registry()` 自己也要拿
        #   `_STALE_LOCK`，在持锁时调它会自死锁（Phase 45 · E11，实测）。
        with _STALE_LOCK:
            _BUILD_STATS["stale"] += 1
        hm = sr.hash_map(base=base)
        rsm = sr.repo_status_map(base=base)
        # ★ Phase 45 · A04：把**证据级验证依赖状态**注入同一份登记快照，
        #   使 `queries.edge_is_stale()` 成为「字节忠实度 ∪ 依赖状态」的**唯一**排除口径。
        #   映射不可用（模块缺失 / 关闭开关 / 图上无 A04 记录）⇒ 传 `{}` ⇒
        #   行为与 Phase 44 逐位一致（**回落**，而非改判）。
        smap = None
        if _EVIDENCE_STATUS_ENABLED:
            b = get_backend()
            if b is not None:
                smap = evidence_status_registry(b.all_edges(), src_hashes=hm)
        val = q.build_stale_registry(sr.ids(), hm, rsm, status_by_edge=(smap or {}))
        with _STALE_LOCK:
            _STALE_CACHE["key"], _STALE_CACHE["val"] = key, val
            return _STALE_CACHE["val"]


_FIDELITY_COUNTS_CACHE = {"key": None, "val": None}


def fidelity_counts_global():
    """**全图**证据字节忠实度四分类计数（`queries.edge_fidelity`，按 HEAD 缓存）。

    为什么随响应暴露（而不是只写文档）：`unverifiable_not_in_repo` 是**已知边界**
    （12 个来源的 `*_raw.json`/`*_cache.json` 约 20 MB，按「大体积派生对象不入库」有意排除，
    实测牵连 47 055 / 48 712 条边）。它**不计入 `stale`**（那些来源并未变更），
    但**必须**让人一眼看到 —— 否则「stale≈0」会被误读成「全部证据都可字节复算」。
    """
    q = _load_queries()
    b = get_backend()
    if not q or not b:
        return None
    base = ROOT
    try:
        key = _head_sha(base)
        with _FIDELITY_BUILD_LOCK:             # ★ 单飞：并发只算一遍（见文件头说明）
            with _STALE_LOCK:
                if _FIDELITY_COUNTS_CACHE["key"] == key and _FIDELITY_COUNTS_CACHE["val"]:
                    return _FIDELITY_COUNTS_CACHE["val"]
            reg = stale_registry()
            if reg is None:
                return None
            with _STALE_LOCK:
                _BUILD_STATS["fidelity"] += 1
            out = q.fidelity_counts(b.all_edges(), reg)
            out["n_edges_total"] = b.edge_count
            with _STALE_LOCK:
                _FIDELITY_COUNTS_CACHE["key"] = key
                _FIDELITY_COUNTS_CACHE["val"] = out
            return out
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 忠实度分列计数不可用（{type(e).__name__}: {e}）")
        return None


_EVIDENCE_STATUS_COUNTS_CACHE = {"key": None, "val": None}


def evidence_status_counts_global():
    """**全图** A04 状态分列计数（`queries.evidence_status_counts`，按 HEAD 缓存）。

    与 `fidelity_counts_global()` 并列但**粒度不同**（见 `queries` 模块头的两层说明）。
    ★ `needs_review` 必须**始终可见**：它是「本环境不可判定」（缺数据本身，非变更），
      不计入 `exclude_stale`；若不在响应里报出，就会被读成「全都是 valid」。
    ★ `no_record` 单列：图上还没有 A04 记录的边（缺记录 ≠ 已验证）。
    """
    q = _load_queries()
    b = get_backend()
    if not q or not b:
        return None
    base = ROOT
    try:
        key = _head_sha(base)
        with _ESTATUS_BUILD_LOCK:               # ★ 单飞：并发只算一遍（见文件头说明）
            with _STALE_LOCK:
                if (_EVIDENCE_STATUS_COUNTS_CACHE["key"] == key
                        and _EVIDENCE_STATUS_COUNTS_CACHE["val"]):
                    return _EVIDENCE_STATUS_COUNTS_CACHE["val"]
            reg = stale_registry()
            if reg is None:
                return None
            with _STALE_LOCK:
                _BUILD_STATS["estatus"] += 1
            out = q.evidence_status_counts(b.all_edges(), reg)
            if out is None:
                return None
            with _STALE_LOCK:
                _EVIDENCE_STATUS_COUNTS_CACHE["key"] = key
                _EVIDENCE_STATUS_COUNTS_CACHE["val"] = out
            return out
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] A04 状态计数不可用（{type(e).__name__}: {e}）")
        return None


def locator_byte_fidelity() -> dict:
    """登记 locator 的 **worktree 字节 vs 仓库 blob**（铁律 #61：被哈希的字节必须可复现）。

    ★ Phase 44 · D1 之后本探针的**定位变了**：权威哈希口径已改为**仓库 blob**
      （`git cat-file blob HEAD:<path>`），裁决**不再**依赖工作区字节 ⇒ 本探针不再
      「决定 stale 是否假阳」，而是**可观测性**：告诉你「本机工作区与仓库是否一致」，
      从而判断显式 opt-in 的 `--byte-source worktree` 复核路径是否可信。
      返回里以 `affects_verdict: false` + `canonical_byte_source` 明确这一点。
    实现：`git ls-files -s`（索引 blob 哈希）+ `git hash-object --no-filters`（worktree 原始哈希）。
    结果按 locator 的 `(mtime_ns,size)` 键缓存；git 不可用 ⇒ `faithful=null`（**不假装通过**）。
    """
    sr = _load_source_registry()
    if not sr:
        return {"faithful": None, "note": "source_registry 不可用"}
    base = getattr(sr, "ROOT", ROOT)
    rels, key = [], []
    for sid in sr.ids():
        for rel in (sr.get(sid) or {}).get("locator") or []:
            if rel in rels:
                continue
            rels.append(rel)
            try:
                st = os.stat(os.path.join(base, rel))
                key.append((rel, st.st_mtime_ns, st.st_size))
            except OSError:
                key.append((rel, None, None))
    key = tuple(key)
    with _STALE_LOCK:
        if _FIDELITY_CACHE["key"] == key:
            return _FIDELITY_CACHE["val"]
    out = {"faithful": None, "checked": 0, "n_unfaithful": 0, "unfaithful": [],
           "n_untracked": 0, "note": ""}
    try:
        # ★ `-z` + `core.quotepath=false`：非 ASCII 路径默认会被转义成 `"\347\234..."`，
        #   直接用 `-s` 解析会把整目录（10_种子数据 / 11_真实数据）判成"未入库"（曾踩到）。
        idx = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files", "-s", "-z", "--"],
                             cwd=base, capture_output=True, text=True, timeout=60,
                             encoding="utf-8", errors="replace")
        if idx.returncode != 0:
            out["note"] = "git ls-files 失败：%s" % idx.stderr.strip()[:120]
        else:
            tracked = {}
            for rec in idx.stdout.split("\0"):
                if not rec:
                    continue
                meta, _, path = rec.partition("\t")
                fields = meta.split()
                if path and len(fields) >= 2:
                    tracked[path.strip()] = fields[1]
            for rel in rels:
                if rel not in tracked:
                    out["n_untracked"] += 1     # 未入库的 locator（.gitignore 的原始数据）⇒ 无法从仓库校验
                    continue
                out["checked"] += 1
                h = subprocess.run(["git", "hash-object", "--no-filters", "--", rel],
                                   cwd=base, capture_output=True, text=True, timeout=120)
                if h.returncode != 0 or h.stdout.strip() != tracked[rel]:
                    out["unfaithful"].append(rel)
    except Exception as e:  # noqa: BLE001
        out["note"] = "无法校验（%s: %s）" % (type(e).__name__, e)
    if out["note"] == "":
        out["n_unfaithful"] = len(out["unfaithful"])
        out["faithful"] = (out["n_unfaithful"] == 0)
        # ★ Phase 44 · D1：**裁决不再依赖 worktree 字节**（权威口径 = 仓库 blob）。
        #   本探针因此从「缺陷证据」转为**可观测性**：它回答「本机工作区是否与仓库一致」，
        #   只影响显式 opt-in 的 `--byte-source worktree` 复核路径。
        out["canonical_byte_source"] = "git cat-file blob HEAD:<path>"
        out["affects_verdict"] = False
        out["note"] = (
            "worktree 字节 vs 仓库 blob：%d/%d 个已入库 locator 不一致%s；"
            "%d 个 locator 未入库（不可从仓库复算）。★ 权威口径已改为**仓库字节** ⇒ "
            "本探针**不影响** stale/fidelity 裁决，仅用于判断 opt-in 的 worktree 复核是否可靠。"
            % (out["n_unfaithful"], out["checked"],
               ("（%s）" % ", ".join(out["unfaithful"][:3])) if out["unfaithful"] else "",
               out["n_untracked"]))
    with _STALE_LOCK:
        _FIDELITY_CACHE["key"], _FIDELITY_CACHE["val"] = key, out
    return out


# ---- expand_token（服务端签发；前端不得自拼无限查询，方案 §5.2.1）----
EXPAND_SECRET = os.environ.get("STTP_EXPAND_SECRET") or secrets.token_hex(16)
SESSION_NODES = int(os.environ.get("STTP_SESSION_NODES", "120"))
SESSION_EDGES = int(os.environ.get("STTP_SESSION_EDGES", "200"))
# 会话表上限：超过即按「最久未写」淘汰，避免匿名会话把内存撑爆（无上限＝内存无界增长）
SESSION_MAX = int(os.environ.get("STTP_SESSION_MAX", "256"))
_SESSIONS: dict = {}
_SESS_LOCK = threading.Lock()


def _sign_token(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    mac = hmac.new(EXPAND_SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()[:16]
    return body + "." + mac


def _verify_token(token: str):
    """→ (payload, None) 或 (None, 拒绝原因)。HMAC 不匹配一律拒绝。"""
    if not isinstance(token, str) or "." not in token:
        return None, "token 格式非法"
    body, mac = token.rsplit(".", 1)
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except Exception:  # noqa: BLE001
        return None, "token 载荷解码失败"
    want = hmac.new(EXPAND_SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(want, mac):
        return None, "token 签名不符（伪造或被篡改）"
    try:
        return json.loads(raw.decode("utf-8")), None
    except Exception:  # noqa: BLE001
        return None, "token 载荷不是合法 JSON"


_EMPTY_SESSION = {"n_nodes": 0, "n_edges": 0, "expansions": 0}


def _session_get(session_id) -> dict:
    """session_id 为 None/空 ⇒ 返回全新零值会话。**注意：此时不得写回**（见 _session_set）。"""
    with _SESS_LOCK:
        return dict(_SESSIONS.get(session_id) or _EMPTY_SESSION)


def _session_set(session_id, n_nodes: int, n_edges: int, expansions: int) -> dict:
    """写会话。session_id 为 None/空 ⇒ **不落库、不判硬门**（匿名调用互不干扰）。

    有 id 时按写入顺序维护 LRU：超上限淘汰最久未写者（不静默无限增长）。
    """
    if not session_id:
        return {"n_nodes": n_nodes, "n_edges": n_edges, "expansions": expansions}
    with _SESS_LOCK:
        _SESSIONS.pop(session_id, None)          # 先删后插 ⇒ 刷新到「最新」端
        _SESSIONS[session_id] = {"n_nodes": n_nodes, "n_edges": n_edges,
                                 "expansions": expansions}
        while len(_SESSIONS) > SESSION_MAX:
            _SESSIONS.pop(next(iter(_SESSIONS)), None)   # 淘汰最久未写
        return dict(_SESSIONS[session_id])


def _qhash(params: dict) -> str:
    """规范化参数（键排序 + 默认值补全）→ `sha256[:16]`，用于缓存键与复现。"""
    norm = {k: params[k] for k in sorted(params)
            if k not in ("session_id", "reset", "expand_token")}
    raw = json.dumps(norm, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _repo_status_counts():
    """来源的 `repo_status` 三分类计数（`in_repo` / `not_in_repo` / `no_locator`）。

    `not_in_repo` = 有 locator 但未入库（大体积拉取缓存，有意排除）⇒ **不可从仓库复算**。
    它是**已知边界**，随 `/api/stats` 与 `/api/subgraph` 一并暴露。
    """
    sr = _load_source_registry()
    if not sr:
        return None
    try:
        m = sr.repo_status_map(getattr(sr, "ROOT", ROOT))
        return {k: sum(1 for v in m.values() if v == k) for k in sr.REPO_STATUS}
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] repo_status 统计不可用（{type(e).__name__}: {e}）")
        return None


def graph_stats() -> dict:
    """图谱统计：优先用查询层后端，其次读 graph_data.json。"""
    b = get_backend()
    if b is not None:
        try:
            from collections import Counter
            import graph_export  # 同目录：类型解析口径必须与权威转换器一致
            ntypes = Counter()
            etypes = Counter()
            verified = 0
            eoi = Counter()
            for _, d in b.G.nodes(data=True):
                # 不能直接用 labels[0]：图里 1658 个节点的 labels 是 ["Entity", "Reaction"]，
                # 首标签恒为 Entity，会把 Reaction/Molecule/Symbol 全统计成 Entity，
                # 与 graph_data_phaseNN.json 的类型分布对不上。改用权威 pick_type。
                ntypes[graph_export.pick_type(d.get("labels"))] += 1
            for _, _, _, d in b.G.edges(keys=True, data=True):
                etypes[d.get("type", "")] += 1
                eoi[d.get("explicit_or_inferred", "")] += 1
                if d.get("verified"):
                    verified += 1
            return {
                "backend": b.name,
                "source": os.path.basename(_graph_path()),
                "nodes": b.node_count,
                "edges": b.edge_count,
                "node_types": dict(ntypes),
                "edge_types": dict(etypes),
                "explicit_or_inferred": dict(eoi),
                "verified_edges": verified,
                # ★ Phase 44 · D1：证据字节忠实度**分列**（全图）—— 必须始终可见，
                #   否则「stale 很小」会被误读成「证据都可字节复算」。
                "evidence_fidelity": fidelity_counts_global(),
                # ★ Phase 45 · A04：**验证依赖状态**分列（全图）—— 与字节忠实度**并列**。
                #   `needs_review` 不计入 stale（不可判定 ≠ 已变更），但必须始终可见；
                #   `no_record` = 图上尚无 A04 记录的边。
                "evidence_status": evidence_status_counts_global(),
                "a04": a04_registry_summary(),
                "source_repo_status": _repo_status_counts(),
                "evidence_byte_source": "repo（git cat-file blob HEAD:<path>）",
            }
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] 统计失败：{e}")
    # 兜底：读 graph_data.json 的 meta
    data = load_graph_data()
    return {
        "backend": "graph_data.json (fallback)",
        "source": data.get("meta", {}).get("source_file", ""),
        "nodes": data.get("meta", {}).get("node_count", 0),
        "edges": data.get("meta", {}).get("edge_count", 0),
        "node_types": data.get("meta", {}).get("node_types", {}),
        "edge_types": data.get("meta", {}).get("edge_types", {}),
        "explicit_or_inferred": data.get("meta", {}).get("explicit_or_inferred", {}),
        "verified_edges": data.get("meta", {}).get("verified_edges", 0),
    }


# ---------------------------------------------------------------------------
# graph_data.json 加载 / 即时生成
# ---------------------------------------------------------------------------
def load_graph_data() -> dict:
    """读取 graph_data.json；不存在则调用 graph_export 即时生成。"""
    if os.path.exists(GRAPH_DATA):
        try:
            with open(GRAPH_DATA, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("nodes") and data.get("edges"):
                return data
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] graph_data.json 读取失败（{e}），尝试重新生成。")

    # 即时生成
    try:
        import graph_export  # 同目录
        src = WITH_INFERRED if os.path.exists(WITH_INFERRED) else NORMALIZED
        data = graph_export.build_graph_data(src)
        with open(GRAPH_DATA, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[OK] 已生成 {GRAPH_DATA}")
        return data
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] 无法生成图谱数据：{e}")
        return {"nodes": [], "edges": [], "meta": {"error": str(e)}}


# ---------------------------------------------------------------------------
# HTTP 处理器
# ---------------------------------------------------------------------------
class VizHandler(BaseHTTPRequestHandler):
    server_version = "FormulaGraphViz/1.0"

    # ---- 工具：统一响应（带 CORS） ----
    def _send(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")           # CORS
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _html(self, code: int, path: str):
        try:
            with open(path, "r", encoding="utf-8") as f:
                body = f.read().encode("utf-8")
            self._send(code, body, "text/html; charset=utf-8")
        except FileNotFoundError:
            self._json(404, {"error": f"not found: {os.path.basename(path)}"})

    def _static(self, rel: str, base_dir: str):
        """安全地发送 base_dir 下的静态文件（A4：/vendor/* 用）。

        防目录穿越：解析后的绝对路径必须落在 base_dir 内；扩展名走白名单。
        """
        base = os.path.realpath(base_dir)
        target = os.path.realpath(os.path.join(base, rel.lstrip("/")))
        if target != base and not target.startswith(base + os.sep):
            return self._json(403, {"error": "forbidden path"})
        ext = os.path.splitext(target)[1].lower()
        ctype = VENDOR_MIME.get(ext)
        if ctype is None:
            return self._json(403, {"error": f"extension not allowed: {ext or '(none)'}"})
        try:
            with open(target, "rb") as f:
                body = f.read()
        except FileNotFoundError:
            return self._json(404, {"error": f"not found: {rel}"})
        self._send(200, body, ctype)

    # ---- 日志：静音默认打印，保留简洁访问日志 ----
    def log_message(self, fmt, *args):  # noqa: A003
        print(f"  [HTTP] {self.address_string()} {fmt % args}")

    # ---- OPTIONS（CORS 预检） ----
    def do_OPTIONS(self):  # noqa: N802
        self._send(204, b"", "text/plain")

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    # ---- GET 路由 ----
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        try:
            if route in ("/", "/index.html", "/graph_view.html"):
                return self._html(200, HTML_FILE)

            # A4：本地化的前端依赖（Cytoscape / fcose / MathJax），支持完全离线渲染
            if route == "/vendor" or route.startswith("/vendor/"):
                return self._static(parsed.path[len("/vendor"):], VENDOR_DIR)

            # ★ Phase 43：前端 ES Module（零构建，浏览器原生 import）
            # 必须走 text/javascript（见 VENDOR_MIME）——MIME 不对浏览器会拒绝加载 module。
            if route == "/js" or route.startswith("/js/"):
                return self._static(parsed.path[len("/js"):], JS_DIR)

            if route == "/graph_data.json":
                return self._json(200, load_graph_data())

            if route == "/api/stats":
                # ★ 统一信封补齐（2026-10-14 实测的 P1 根因）：
                #   `/api/stats` 是 legacy 端点，历史响应体**不带 `status`**。
                #   前端 `normalizeStatus(undefined)` ⇒ 判为 "error" ⇒ `detectMode` 认定
                #   「后端不可用」⇒ 退到 `bundle` ⇒ `GET /graph_data.json`（48.7 MB 全图）
                #   ⇒ 解析 + 渲染 9657 节点/48712 边 ⇒ **浏览器卡死**（正是铁律 #66 要消灭的行为）。
                #   这里补齐 `status`/`snapshot_id`，使其与其余 `/api/*` 同口径。
                _st = graph_stats()
                _st["status"] = "ok"
                _st["snapshot_id"] = self._snapshot_id()
                return self._json(200, _st)

            # ★ Phase 49：轻量探活（毫秒级，**不触碰图谱数据**）—— 修 P0-2
            if route == "/api/healthz":
                return self._healthz()

            if route == "/api/neighbors":
                return self._api_neighbors(qs)

            # ★ Phase 43：局部图谱查询（复评 A06 合并）
            if route == "/api/search":
                return self._guard(self._api_search, qs)
            if route == "/api/subgraph":
                return self._guard(self._api_subgraph, qs)
            if route == "/api/overview":
                return self._guard(self._api_overview, qs)
            if route == "/api/path":
                return self._guard(self._api_path, qs)

            # 未知路径
            return self._json(404, {"error": "not found", "path": self.path})
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    # ---- 统一异常/参数错误处理（铁律 #67：异常绝不吞成空）----
    def _guard(self, fn, qs):
        try:
            return fn(qs)
        except ValueError as e:
            # 参数非法 ⇒ HTTP 400 + BAD_REQUEST（唯一的 4xx 业务场景）
            return self._json(400, {"status": "error", "reason": str(e),
                                    "error_code": "BAD_REQUEST",
                                    "snapshot_id": self._snapshot_id()})
        except Exception as e:  # noqa: BLE001
            return self._json(200, {"status": "error",
                                    "reason": "%s: %s" % (type(e).__name__, e),
                                    "error_code": "TOOL_EXECUTION_ERROR",
                                    "snapshot_id": self._snapshot_id()})

    # ---- Phase 49：/api/healthz（轻量探活；**绝不触发图谱加载**）----
    def _healthz(self):
        """毫秒级探活：只读进程内状态，**不触碰图谱数据**。

        ★ 为什么必须存在：`/api/stats` 冷启动要 4.91 s（其中忠实度四分类冷算 4.27 s，
          **不只是**加载 67.7 MB 权威图），前端探活预算只有 2 s ⇒ 首次打开必被误判离线（P0-2）。
        ★ 三态分列（不把「预热中」混进「失败」，铁律 #84）：
          `backend=ready` 已就绪 / `warming` 进程在跑、图谱在建 / `failed` 初始化真失败。
        ★ `snapshot_id` 在未就绪时**为 null**（拿它要加载图谱 ⇒ 与"轻量"自相矛盾）。
        ★ 首次被探活即**异步预热**，让加载与用户输入重叠。
        ★ 实测：本端点 191 ms（listen→首个 healthz），且**不含**图谱数组 ⇒ 与图谱规模无关。
        ★ 2026-10-15 修正（F4，自测发现）：`backend` 必须反映**整条预热**是否完成，
          **不能**在"图已装好"时就报 `ready`。第一版写成
          `ready = backend_ready(); if ready: body["backend"] = "ready"` ——
          而 `backend_ready()` 只看**图**；于是后台预热还在算 `graph_stats()`（约 4.8 s）时，
          healthz 已经宣称 `ready`，前端据此发 `/api/stats` ⇒ **仍然要等那 4.8 s**
          （F2 的收益被这句覆盖悄悄抵消，`warm_ms` 也为 null）。
          ⇒ 现在：`backend = warm_state()`（`ready` 当且仅当 `_WARM["done"]`），
            `graph_ready` 单独反映图是否已装好。两者**分列**，各自含义明确。
        ★ 并且**无条件**调用 `kick_warmup()`（幂等）：否则「图先被普通请求装好、
          healthz 才被首次探到」这条路径下预热永不启动 ⇒ `_WARM["done"]` 永远为假 ⇒
          `backend` 永远停在 `warming`（前端永远等不到就绪）。
        """
        kick_warmup()                     # 幂等：确保预热一定在跑
        ready = backend_ready()           # 仅表示"图已装好"
        done = bool(_WARM["done"])        # 整条预热（图 + `/api/stats` 冷算）是否完成
        src = _graph_path()
        body = {
            "status": "ok",                       # 进程可达（与"图谱是否就绪"分列）
            # ★ 三态：`warm_state()` 的 ready 当且仅当 `_WARM["done"]`。
            #   不得因为"图已装好"就改写成 ready —— 见本函数文档串的 F4 说明。
            "backend": warm_state(),
            "graph_ready": ready,                 # 图已装好（预热的第一阶段）
            "pid": os.getpid(),
            "source": os.path.basename(src),
            "source_present": os.path.exists(src),
            "snapshot_id": None,
            "warm_ms": _WARM.get("ms"),
            "warm_error": _WARM.get("error"),
            # ★ 派生缓存的**实际构建次数**（O(1) 只读）：单飞是否真的单飞、可被直接观测
            #   （否则只能靠测时间推断 ⇒ 快机器假绿 / 慢机器假红）。见 H8h。
            "cache_builds": build_stats(),
            "endpoints": ["/api/healthz", "/api/stats", "/api/search",
                          "/api/subgraph", "/api/overview", "/api/path"],
        }
        if done:
            # 仅预热完成后才取快照（此时 `snapshot_id()` 是纯内存计算）
            try:
                body["snapshot_id"] = self._snapshot_id()
            except Exception:  # noqa: BLE001
                body["snapshot_id"] = None
        return self._json(200, body)

    def _snapshot_id(self) -> str:
        b = get_backend()
        if b is None:
            return "sha256:" + hashlib.sha256(b"<no-backend>").hexdigest()[:12]
        try:
            return b.snapshot_id()
        except Exception:  # noqa: BLE001
            return "sha256:" + hashlib.sha256(b"<snapshot-error>").hexdigest()[:12]

    @staticmethod
    def _p(qs, name, default=None):
        v = qs.get(name)
        return v[0] if v else default

    # ---- /api/search ----
    def _api_search(self, qs):
        q = (self._p(qs, "q") or "").strip()
        if not q:
            raise ValueError("缺少参数 q")
        b = get_backend()
        if b is None:
            return self._json(503, {"status": "error", "reason": "查询层不可用",
                                    "error_code": "TOOL_EXECUTION_ERROR"})
        gx = _gx()
        params = {"q": q, "types": self._p(qs, "types"), "subject": self._p(qs, "subject"),
                  "limit": self._p(qs, "limit", "20")}
        r = b.search(q, node_types=self._p(qs, "types"), subjects=self._p(qs, "subject"),
                     limit=int(params["limit"] or 20),
                     node_type_of=(gx.pick_type if gx else None),
                     subject_of_fn=(gx.subject_of if gx else None))
        r.update({"snapshot_id": self._snapshot_id(), "query_hash": _qhash(params)})
        return self._json(200, r)

    # ---- /api/overview ----
    def _api_overview(self, qs):
        b = get_backend()
        if b is None:
            return self._json(503, {"status": "error", "reason": "查询层不可用",
                                    "error_code": "TOOL_EXECUTION_ERROR"})
        gx = _gx()
        params = {"mode": self._p(qs, "mode", "aggregate"), "k": self._p(qs, "k", "300")}
        r = b.overview(mode=params["mode"], k=int(params["k"] or 300),
                       node_type_of=(gx.pick_type if gx else None),
                       subject_of_fn=(gx.subject_of if gx else None))
        r.update({"snapshot_id": self._snapshot_id(), "query_hash": _qhash(params)})
        return self._json(200, r)

    # ---- /api/subgraph（核心）----
    def _env(self, **kw):
        base = {"status": "error", "reason": "", "error_code": None,
                "snapshot_id": self._snapshot_id(), "query": {}, "query_hash": "",
                "seed": None, "hidden_by_trust": 0, "nodes": [], "edges": [],
                # ★ Phase 49：`paths` **恒存在**（非 path 模式为空数组）⇒
                #   前端可以无条件读 `env.paths`，不必按模式分支（少一处静默错位）。
                "paths": [], "expandable": [], "stats": {}}
        base.update(kw)
        return base

    def _api_subgraph(self, qs):
        b = get_backend()
        if b is None:
            return self._json(503, self._env(status="error", reason="查询层不可用",
                                             error_code="TOOL_EXECUTION_ERROR"))
        gx = _gx()
        q = _load_queries()

        mode = (self._p(qs, "mode", "ego") or "ego").lower()
        seeds = self._p(qs, "seeds")
        query_txt = self._p(qs, "query")
        session_id = self._p(qs, "session_id")
        snapshot_req = self._p(qs, "snapshot_id")
        cur_snap = self._snapshot_id()
        # ★ Phase 49：默认预算**直接引用 queries 常量**，不再各自硬编码
        #   （原先这里写死 "500"/"2000"/"3"/"3000"/"20"，与 `queries.DEFAULT_*` 各写一套；
        #    Phase 49 把默认值收敛到 40/70/2/10000，三处不同步就会让"省略参数"的请求
        #    拿到与文档不一致的图。X4a/X4c 断言三处同源。）
        params = {
            "mode": mode, "seeds": seeds, "query": query_txt,
            "hops": self._p(qs, "hops"), "node_types": self._p(qs, "node_types"),
            "types": self._p(qs, "types"), "kinds": self._p(qs, "kinds"),
            "trust": self._p(qs, "trust", "verified"),
            "min_confidence": self._p(qs, "min_confidence", "0"),
            "exclude_model_inferred": self._p(qs, "exclude_model_inferred", "false"),
            "exclude_stale": self._p(qs, "exclude_stale", "false"),
            "subjects": self._p(qs, "subjects"),
            "max_nodes": self._p(qs, "max_nodes", str(q.DEFAULT_NODES)),
            "max_edges": self._p(qs, "max_edges", str(q.DEFAULT_EDGES)),
            "max_hops": self._p(qs, "max_hops", str(q.DEFAULT_HOPS)),
            "deadline_ms": self._p(qs, "deadline_ms", str(q.DEFAULT_DEADLINE_MS)),
            "max_paths": self._p(qs, "max_paths", str(q.DEFAULT_MAX_PATHS)),
        }
        qh = _qhash(params)

        # 快照不符 ⇒ 明确拒绝（前端缓存/复现语义）
        if snapshot_req and snapshot_req != cur_snap:
            return self._json(200, self._env(
                status="error", error_code="SNAPSHOT_MISMATCH",
                reason="请求的 snapshot_id=%s 与当前查询源 %s 不符（图已更新）"
                       % (snapshot_req, cur_snap),
                query=params, query_hash=qh))

        # 会话级展开：必须携带服务端签发的 token（§5.2.1）
        asked_hops = params["hops"]
        if mode == "expand":
            tok = self._p(qs, "expand_token")
            payload, err = _verify_token(tok)
            if err:
                return self._json(400, self._env(status="error", error_code="BAD_REQUEST",
                                                 reason="expand_token 校验失败：%s" % err,
                                                 query=params, query_hash=qh))
            if payload.get("snap") not in (None, cur_snap):
                return self._json(200, self._env(
                    status="error", error_code="SNAPSHOT_MISMATCH",
                    reason="token 绑定的快照 %s 与当前 %s 不符"
                           % (payload.get("snap"), cur_snap),
                    query=params, query_hash=qh))
            seeds = payload.get("seed") or seeds
            asked_hops = payload.get("hops") or 1
            mode = "ego"

        # 一步直达：query= 先搜首个命中作 seed
        seed_info = None
        if query_txt and not seeds:
            sr = b.search(query_txt, limit=1,
                          node_type_of=(gx.pick_type if gx else None),
                          subject_of_fn=(gx.subject_of if gx else None))
            if not sr["hits"]:
                return self._json(200, self._env(
                    status="no_result", reason="搜索「%s」没有任何命中" % query_txt,
                    query=params, query_hash=qh))
            seeds = [sr["hits"][0]["id"]]

        stale = stale_registry()
        if self._as_bool(params["exclude_stale"]) and stale is None:
            return self._json(200, self._env(
                status="error", error_code="EVIDENCE_STALE",
                reason="exclude_stale=true 但登记表不可用 ⇒ 无法判定，fail-closed 拒绝",
                query=params, query_hash=qh))

        r = b.subgraph(
            seeds=seeds, mode=mode, hops=(int(asked_hops) if asked_hops not in (None, "")
                                          else None),
            node_types=params["node_types"], subjects=params["subjects"],
            types=params["types"], kinds=params["kinds"], trust=params["trust"],
            min_confidence=float(params["min_confidence"] or 0),
            exclude_model_inferred=self._as_bool(params["exclude_model_inferred"]),
            exclude_stale=self._as_bool(params["exclude_stale"]), stale_sources=stale,
            max_nodes=int(params["max_nodes"] or 500),
            max_edges=int(params["max_edges"] or 2000),
            max_hops=int(params["max_hops"] or 3),
            deadline_ms=int(params["deadline_ms"] or 3000),
            max_paths=int(params["max_paths"] or 20),
            node_type_of=(gx.pick_type if gx else None),
            subject_of_fn=(gx.subject_of if gx else None),
        )

        # 会话累计记账（模式语义见报告「会话记账语义」）：
        #   ① 无 session_id ⇒ track=False ⇒ **不记账、不判硬门**（匿名调用彼此独立，
        #      否则所有匿名请求共享同一计数、第二次展开即被拒 —— 这正是基准脚本踩到的坑）；
        #   ② mode=ego 为增量（累加）；filter/path 视为"新视图" ⇒ 先重置再计数；
        #   ③ reset=1 显式清空该会话（前端"重置"按钮）。
        track = bool(session_id)
        if track and self._as_bool(self._p(qs, "reset")):
            _session_set(session_id, 0, 0, 0)
        session = _session_get(session_id) if track else dict(_EMPTY_SESSION)
        incremental = (mode == "ego")
        if track and not incremental:
            session = _session_set(session_id, 0, 0, 0)
        n_nodes_next = session["n_nodes"] + r["stats"].get("n_nodes", 0)
        n_edges_next = session["n_edges"] + r["stats"].get("n_edges", 0)
        if track and incremental and (n_nodes_next > SESSION_NODES or n_edges_next > SESSION_EDGES):
            return self._json(200, self._env(
                status="error", error_code="SUBGRAPH_LIMIT_REACHED",
                reason="本次会话累计预算已用尽（%d/%d 节点，%d/%d 边）⇒ 拒绝继续展开；"
                       "请返回上一层或重置" % (session["n_nodes"], SESSION_NODES,
                                        session["n_edges"], SESSION_EDGES),
                query=params, query_hash=qh,
                stats={"session": {"tracked": True,
                                   "n_nodes": session["n_nodes"], "n_edges": session["n_edges"],
                                   "limit_nodes": SESSION_NODES, "limit_edges": SESSION_EDGES,
                                   "exhausted": True}}))
        if track and incremental:
            session = _session_set(session_id, n_nodes_next, n_edges_next,
                                  session["expansions"] + 1)

        # 投影（复用 graph_export 唯一口径）+ why_shown + 证据 staleness
        nodes_out, edges_out = [], []
        if gx:
            for n in r["nodes"]:
                p = gx.project_node(n)
                p["why_shown"] = r["why_nodes"].get(n["id"], "user_specified")
                nodes_out.append(p)
            for e in r["edges"]:
                p = gx.project_edge(e)
                p["why_shown"] = r["why_edges"].get(e["id"], "user_specified")
                # ★ 证据字节忠实度**四分类**（Phase 44 · D1）：与 `stale` 并存但**语义更细** ——
                #   `stale=True` 只说「不可确认新鲜」，`fidelity` 说清**为什么**
                #   （已变更 / 无 locator / locator 未入库）。前端据此避免把
                #   「不可从仓库复算」渲染成「已变更」。
                #   ★ 登记表不可用时 `fidelity` 为 **null**（不可判定），**不得**写成 "verified"。
                p["fidelity"] = q.edge_fidelity(e, stale) if stale else None
                p["stale"] = bool(stale) and q.edge_is_stale(e, stale)
                reasons = q.fidelity_reasons(e, stale) if stale else []
                if reasons:
                    p["fidelity_reasons"] = reasons
                if p["stale"]:
                    p["stale_reasons"] = q.stale_reasons(e, stale)
                # ★ A04 · 验证依赖状态（Phase 45）：**与 fidelity 并列的第二层**。
                #   `evidence_status=None` + `evidence_status_source="absent"` 表示
                #   「该图上没有这条边的 A04 记录」——**不得**渲染成 valid（缺记录 ≠ 已验证）。
                rec = q.edge_evidence_status(e, stale)
                p["evidence_status"] = (rec or {}).get("status")
                p["evidence_status_source"] = "recorded" if rec else "absent"
                if rec:
                    if rec.get("reasons"):
                        p["evidence_status_reasons"] = list(rec["reasons"])
                    p["evidence_status_n"] = {"evidence": rec.get("n_evidence"),
                                              "with_deps": rec.get("n_with_deps")}
                    # 把**逐条**状态并进证据数组：前端证据卡直接读，不必再映射一遍
                    # （少一层映射就少一类「少搬一格」型静默错位）
                    by_id = {x["evidence_id"]: x for x in (rec.get("evidence") or [])}
                    for ev in (p.get("evidence") or []):
                        if not isinstance(ev, dict) or ev.get("evd") not in by_id:
                            continue
                        x = by_id[ev["evd"]]
                        ev["status"] = x["status"]
                        if x.get("reasons"):
                            ev["status_reasons"] = list(x["reasons"])
                        binds = {rr["ref_id"]: rr["binding"] for rr in (x.get("refs") or [])}
                        if binds:
                            ev["ref_bindings"] = binds
                edges_out.append(p)
            fidelity_local = (q.fidelity_counts(r["edges"], stale) if stale else None)
        else:
            fidelity_local = None

        expandable = []
        for x in r["expandable"]:
            expandable.append({
                "node_id": x["node_id"], "estimated_neighbors": x["estimated_neighbors"],
                "token": _sign_token({"v": 1, "snap": cur_snap, "seed": x["node_id"],
                                      "hops": 1, "filt": qh}),
            })

        if seeds:
            first = (seeds if isinstance(seeds, list) else str(seeds).split(","))[0]
            nn = next((n for n in nodes_out if n["id"] == first), None)
            seed_info = ({"id": nn["id"], "type": nn.get("type"), "subject": nn.get("subject")}
                         if nn else {"id": first, "type": None, "subject": None})

        stats = dict(r["stats"])
        stats["expansions"] = session["expansions"]
        # tracked=False ⇒ 本次未记账，会话预算不适用（前端须据此隐藏"会话累计"角标）
        stats["session"] = {"tracked": track,
                            "n_nodes": session["n_nodes"], "n_edges": session["n_edges"],
                            "limit_nodes": SESSION_NODES, "limit_edges": SESSION_EDGES,
                            "exhausted": False}
        # ★ stale 判据的**前提**必须随响应可见（铁律 #61 推广）：被哈希的字节是否与仓库一致
        stats["stale_registry"] = locator_byte_fidelity()
        # ★ 证据字节忠实度**分列**（Phase 44 · D1）：本次可见子图 + 全图，两者都给。
        #   `unverifiable_not_in_repo` 是已知边界（未入库的 raw/cache）⇒ 永远显式出现，
        #   不合并进 `stale`，避免「stale 很小」被误读成「证据都可字节复算」。
        stats["evidence_fidelity"] = fidelity_local
        stats["evidence_fidelity_global"] = fidelity_counts_global()
        # ★ A04（Phase 45）：本次可见子图 + 全图的**验证依赖状态**分列（与上面并列）。
        #   `stats["evidence_status"]` 由 `queries.subgraph()` 在 stats 内给出（本次可见边）；
        #   这里补上全图口径。两者均为 None ⇒ 该图上无 A04 记录（不假装 valid）。
        stats["evidence_status_global"] = evidence_status_counts_global()
        stats["a04"] = a04_registry_summary()
        return self._json(200, self._env(
            status=r["status"], reason=r["reason"], error_code=r["error_code"],
            query=params, query_hash=qh, seed=seed_info,
            hidden_by_trust=r["stats"].get("hidden_by_trust", 0),
            # ★ Phase 49：透传路径链（仅 mode=path 非空；其余恒为 []）。
            #   以前这里不透传 ⇒ 前端 `env.meta.paths` / `env.raw.paths` 都取不到
            #   ⇒ 子图能画、路径清单永远为空（"后端给了、UI 回吞"同族）。
            paths=r.get("paths") or [],
            nodes=nodes_out, edges=edges_out, expandable=expandable, stats=stats))

    @staticmethod
    def _as_bool(v) -> bool:
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    # ---- /api/path（改造：有界 + 五态；保留 legacy 键以免破坏旧页面）----
    def _api_path(self, qs):
        src = (self._p(qs, "src") or "").strip()
        dst = (self._p(qs, "dst") or "").strip()
        if not src or not dst:
            raise ValueError("缺少参数 src / dst")
        b = get_backend()
        if b is None:
            return self._json(503, {"status": "error", "reason": "查询层不可用",
                                    "error_code": "TOOL_EXECUTION_ERROR",
                                    "src": src, "dst": dst, "paths": []})
        params = {"src": src, "dst": dst,
                  "max_paths": self._p(qs, "max_paths", "20"),
                  "max_hops": self._p(qs, "max_hops", "5"),
                  "deadline_ms": self._p(qs, "deadline_ms", "3000")}
        r = b.paths_between_bounded(src, dst, max_paths=int(params["max_paths"] or 20),
                                    max_hops=int(params["max_hops"] or 5),
                                    deadline_ms=int(params["deadline_ms"] or 3000))
        r.update({"src": src, "dst": dst, "count": len(r["paths"]),   # legacy 键
                  "snapshot_id": self._snapshot_id(), "query_hash": _qhash(params)})
        return self._json(200, r)

    # ---- /api/neighbors ----
    def _api_neighbors(self, qs):
        node = (qs.get("node") or [""])[0].strip()
        if not node:
            return self._json(400, {"error": "missing query param: node"})
        b = get_backend()
        if b is None:
            return self._json(503, {"error": "查询层不可用（NetworkX 后端未就绪）",
                                    "node": node, "neighbors": [], "edges": []})
        neighbors = b.get_neighbors(node)
        # 归一化：每条邻居记录 → 有向边
        edges = []
        for nb in neighbors:
            nid, direction = nb.get("id"), nb.get("direction")
            if direction == "out":
                src, dst = node, nid
            else:
                src, dst = nid, node
            edges.append({
                "source": src, "target": dst,
                "type": nb.get("type", ""), "kind": nb.get("kind", ""),
                "confidence": nb.get("confidence"),
                "direction": direction,
            })
        return self._json(200, {
            "node": node,
            "degree": len(neighbors),
            "neighbors": neighbors,
            "edges": edges,
        })


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="公式知识图谱 · Phase 4 可视化后端（标准库 http.server）")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=int(os.environ.get("VIZ_PORT", DEFAULT_PORT)))
    args = ap.parse_args(argv)

    url = f"http://{args.host}:{args.port}/"
    print("=" * 68)
    print("  公式知识图谱 · Phase 4 可视化后端")
    print("=" * 68)
    print(f"  项目根   : {ROOT}")
    print(f"  查询层   : {KNOWLEDGE_DIR}")
    print(f"  数据源   : {os.path.basename(_graph_path())}")
    # ★ Phase 49 修（F1，2026-10-15 实测）：**先 bind 再预热**。
    #   原实现在这里同步调用 `get_backend()`（"预热查询层（顺便打印规模）"），
    #   而它位于 `ThreadingHTTPServer(...)` **之前** ⇒ 后果有二：
    #     ① 端口要等 67.7 MB 权威图加载完才开始 listen（实测 spawn→listen **2774 ms**，
    #        其中 import 仅 237 ms，其余全是图加载）⇒ 页面首次可访问被无谓推迟；
    #     ② `warming` 三态在产品路径（`bash sttp.sh viz`）**永不出现** ⇒ 本轮新加的
    #        「后台预热」在该路径下是**死代码**（无人防守 = 迟早腐坏，铁律 #74 家族）。
    #   改法：bind 之后 `kick_warmup()` 异步预热。于是：端口立刻可用 → 页面秒开 →
    #   `/api/healthz` 如实报 `warming` → 预热与「用户读题 / 输入 / 确认实体」重叠。
    kick_warmup()
    print("-" * 68)
    print(f"  访问地址 : {url}")
    print(f"  端点     : /  ·  /js/<module>.js  ·  /vendor/<lib>  ·  /graph_data.json")
    print(f"             /api/stats  ·  /api/neighbors?node=<id>")
    print(f"             ★ /api/search?q=<文本>            （局部图谱：搜索）")
    print(f"             ★ /api/subgraph?seeds=<id>&mode=ego&hops=1   （局部子图，五态+预算）")
    print(f"             ★ /api/path?src=<id>&dst=<id>&max_paths=20    （有界路径；legacy 扁平信封）")
    print(f"             ★ /api/overview                    （概览元图，替代全图）")
    print(f"  前端模式 : ?mode=live（默认，查询面板+子图）｜ ?mode=bundle（样例全量，小图）")
    print(f"  会话预算 : {SESSION_NODES} 节点 / {SESSION_EDGES} 边（超出即拒绝继续展开）")
    print(f"             无 session_id ⇒ 不记账、不限流；reset=1 清空该会话（前端「重置」）")
    print(f"             会话表上限 {SESSION_MAX} 个（超出按最久未写淘汰）")
    print("  按 Ctrl+C 停止")
    print("=" * 68)

    httpd = ThreadingHTTPServer((args.host, args.port), VizHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] 收到中断，正在关闭 …")
    finally:
        httpd.server_close()
        b = get_backend()
        if b is not None:
            try:
                b.close()
            except Exception:
                pass
        print("[INFO] 服务已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
