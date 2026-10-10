# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Phase 12 完成器（后台常驻，自动收尾）：
  等 Aura 边总数连续 2 次不再增长（推送完成）后，
  1) 运行 export_aura.py 从 Aura 反向导出权威 graph_data_phase12.json；
  2) 重启 viz_server 指向该全量图。
独立于 phase12_finalize.py 的推送/复核同步，避免人工盯守。
"""
import os, time, subprocess, signal, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"

def aura_edges():
    from neo4j import GraphDatabase
    URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687"); U = os.environ.get("NEO4J_USER", "neo4j")
    P = os.environ.get("NEO4J_PASSWORD", "formula_graph_2026")
    d = GraphDatabase.driver(URI, auth=(U, P), database=os.environ.get("NEO4J_DATABASE", "neo4j"))
    with d.session(database=os.environ.get("NEO4J_DATABASE", "neo4j")) as s:
        c = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
    d.close()
    return c

def export_aura():
    p = os.path.join(HERE, "..", "09_科研扩展", "9_inference", "export_aura.py")
    p = os.path.normpath(p)
    print("[export] 运行 export_aura.py ...")
    subprocess.run([PY, p], check=True, timeout=600)
    out = os.path.join(HERE, "graph_data_phase12.json")
    print(f"[export] 完成 -> {out}")

def restart_viz():
    # 用 tasklist 找到运行中的 viz_server.py 进程并终止
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV"], capture_output=True, text=True, timeout=30).stdout
        for line in out.splitlines():
            if "python.exe" in line.lower() and "viz_server.py" in line.lower():
                # CSV: "python.exe","pid",...
                parts = line.strip().strip('"').split('","')
                if len(parts) >= 2:
                    try:
                        pid = int(parts[1])
                        if pid == os.getpid():
                            continue
                        print(f"[viz] 终止旧 viz PID {pid}")
                        subprocess.run(["taskkill", "/PID", str(pid), "/F"], timeout=30)
                    except Exception as e:
                        print(f"[viz] 终止失败 {e}")
    except Exception as e:
        print(f"[viz] tasklist 失败: {e}")
    time.sleep(3)
    env = dict(os.environ)
    env["GRAPH_DATA_FILE"] = os.path.join(HERE, "graph_data_phase12.json")
    subprocess.Popen([PY, os.path.join(HERE, "viz_server.py")], env=env)
    time.sleep(4)
    try:
        import urllib.request
        r = urllib.request.urlopen("http://127.0.0.1:8765/", timeout=10)
        print(f"[viz] 重启完成 HTTP {r.status}, len={len(r.read())}")
    except Exception as e:
        print(f"[viz] HTTP 探测失败: {e}")

def main():
    print("[waiter] 等待 Aura 推送完成（边数连续 2 次稳定）...")
    prev = None; stable = 0
    for _ in range(120):  # 最多 60 分钟
        try:
            c = aura_edges()
        except Exception as e:
            print(f"[waiter] 计数失败 {e}, 重试"); time.sleep(30); continue
        print(f"[waiter] 当前 Aura 边: {c}")
        if prev is not None and c == prev:
            stable += 1
            if stable >= 2:
                print(f"[waiter] 边数稳定于 {c}，推送完成")
                try:
                    export_aura()
                except Exception as e:
                    print(f"[export] 失败: {e}")
                try:
                    restart_viz()
                except Exception as e:
                    print(f"[viz] 重启失败: {e}")
                print("[DONE] Phase 12 完成器收尾完毕")
                return
        else:
            stable = 0
        prev = c
        time.sleep(30)
    print("[waiter] 超时仍未稳定，请人工检查")

if __name__ == "__main__":
    main()
