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
    GET /                        → graph_view.html
    GET /vendor/<file>           → 本地化的前端依赖（Cytoscape / fcose / MathJax；A4）
    GET /graph_data.json         → 导出的图谱数据（缺失时即时调用 graph_export）
    GET /api/neighbors?node=<id> → queries.get_neighbors（返回 {node, neighbors, edges}）
    GET /api/path?src=<id>&dst=<id> → queries.paths_between（返回 {paths}）
    GET /api/stats               → 图谱统计

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

import json
import os
import sys
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
    """查询层数据源：优先 with_inferred.json（与 graph_data.json 一致）。"""
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
            print(f"[OK] NetworkX 后端就绪：{_BACKEND.node_count} 节点 / "
                  f"{_BACKEND.edge_count} 边（源：{os.path.basename(path)}）")
        except BaseException as e:  # noqa: BLE001  # 含 SystemExit（queries 缺源会 sys.exit）
            _BACKEND = False
            print(f"[WARN] NetworkX 后端初始化失败（{type(e).__name__}: {e}），"
                  f"API 将降级；/graph_data.json 不受影响。")
    return _BACKEND or None


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

            if route == "/graph_data.json":
                return self._json(200, load_graph_data())

            if route == "/api/stats":
                return self._json(200, graph_stats())

            if route == "/api/neighbors":
                return self._api_neighbors(qs)

            if route == "/api/path":
                return self._api_path(qs)

            # 未知路径
            return self._json(404, {"error": "not found", "path": self.path})
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

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

    # ---- /api/path ----
    def _api_path(self, qs):
        src = (qs.get("src") or [""])[0].strip()
        dst = (qs.get("dst") or [""])[0].strip()
        if not src or not dst:
            return self._json(400, {"error": "missing query params: src / dst"})
        b = get_backend()
        if b is None:
            return self._json(503, {"error": "查询层不可用（NetworkX 后端未就绪）",
                                    "src": src, "dst": dst, "paths": []})
        paths = b.paths_between(src, dst)
        return self._json(200, {
            "src": src, "dst": dst,
            "count": len(paths),
            "paths": paths,
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
    print(f"  端点     : /  ·  /graph_data.json  ·  /api/neighbors?node=<id>")
    print(f"             /api/path?src=<id>&dst=<id>  ·  /api/stats")
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
