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
    """获取 NetworkX 后端（失败返回 None，不抛异常）。"""
    global _BACKEND
    if _BACKEND is None:
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
_STALE_CACHE = {"key": None, "val": None}
_STALE_LOCK = threading.Lock()
_FIDELITY_CACHE = {"key": None, "val": None}


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


def stale_registry():
    """登记快照 `{known, hashes}`，用于 `exclude_stale`。

    ★ 缓存键 = 全部 locator 文件的 `(relpath, mtime_ns, size)` + 每源的 `lineage_recorded`。
    任一被登记文件被改动 ⇒ 键变 ⇒ **当次请求即可判定 stale**（V16 负对照要求）。
    无 locator / 未登记 ⇒ fail-closed 视为 stale（见 queries.edge_is_stale 的说明）。
    """
    sr = _load_source_registry()
    q = _load_queries()
    if not sr or not q:
        return None
    base = getattr(sr, "ROOT", ROOT)
    key = []
    for sid in sr.ids():
        ent = sr.get(sid) or {}
        key.append((sid, bool(ent.get("lineage_recorded"))))
        for rel in (ent.get("locator") or []):
            try:
                st = os.stat(os.path.join(base, rel))
                key.append((rel, st.st_mtime_ns, st.st_size))
            except OSError:
                key.append((rel, None, None))
    key = tuple(key)
    with _STALE_LOCK:
        if _STALE_CACHE["key"] != key:
            _STALE_CACHE["key"] = key
            _STALE_CACHE["val"] = q.build_stale_registry(sr.ids(), sr.hash_map(base=base))
        return _STALE_CACHE["val"]


def locator_byte_fidelity() -> dict:
    """登记 locator 的 **worktree 字节 vs 仓库索引 blob**（铁律 #61 推广：被哈希的字节必须与仓库一致）。

    ★ 为什么必须做：stale 判据靠 `evidence[].src_hash` 与 `source_registry` **当前内容哈希**
    比对；而"当前内容哈希"是对 **worktree 文件字节**算的。若 worktree 的字节与仓库里的字节
    不一致（哪怕只是 CRLF/LF），那么：
      * 本机自洽，但**干净检出（clone）后全部对不上** ⇒ 假 stale；
      * 证据哈希因此**平台相关**，违反铁律 #61。
    本函数把"字节是否忠实"变成**响应里可见的字段**，而不是靠人记得。
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
        if out["n_unfaithful"]:
            out["note"] = ("★ worktree 字节 ≠ 仓库 blob（%d/%d 个已入库登记 locator）⇒ "
                           "证据里的 src_hash 在干净检出下不可复现，stale 判据会假阳；"
                           "另有 %d 个 locator 未入库（不可从仓库校验）"
                           % (out["n_unfaithful"], out["checked"], out["n_untracked"]))
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
                return self._json(200, graph_stats())

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
                "expandable": [], "stats": {}}
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
        params = {
            "mode": mode, "seeds": seeds, "query": query_txt,
            "hops": self._p(qs, "hops"), "node_types": self._p(qs, "node_types"),
            "types": self._p(qs, "types"), "kinds": self._p(qs, "kinds"),
            "trust": self._p(qs, "trust", "verified"),
            "min_confidence": self._p(qs, "min_confidence", "0"),
            "exclude_model_inferred": self._p(qs, "exclude_model_inferred", "false"),
            "exclude_stale": self._p(qs, "exclude_stale", "false"),
            "subjects": self._p(qs, "subjects"),
            "max_nodes": self._p(qs, "max_nodes", "500"),
            "max_edges": self._p(qs, "max_edges", "2000"),
            "max_hops": self._p(qs, "max_hops", "3"),
            "deadline_ms": self._p(qs, "deadline_ms", "3000"),
            "max_paths": self._p(qs, "max_paths", "20"),
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
                p["stale"] = bool(stale) and q.edge_is_stale(e, stale)
                if p["stale"]:
                    p["stale_reasons"] = q.stale_reasons(e, stale)
                edges_out.append(p)

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
        return self._json(200, self._env(
            status=r["status"], reason=r["reason"], error_code=r["error_code"],
            query=params, query_hash=qh, seed=seed_info,
            hidden_by_trust=r["stats"].get("hidden_by_trust", 0),
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
    # 预热查询层（顺便打印规模），失败也不致命
    get_backend()
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
