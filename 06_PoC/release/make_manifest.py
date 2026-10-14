#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""make_manifest.py —— 发布清单生成器（Phase 37 · GPT6 复评 R06）

复评原文（R06 完成定义）：**第三人从文档独立运行，页面和查询属于同一数据版本**，
并要求完整快照「附**源版本、下载许可、校验和、构建命令、依赖版本与预期统计**」。

本脚本把这些**一次性钉死**为 `06_PoC/release/manifest.json`：

    code          仓库版本（git HEAD / 是否脏）+ 许可证
    build         构建命令 + 管线 + Python 版本
    dependencies  从 requirements.lock 读的**目标**版本 + 本机实测版本 + 漂移清单
    artifacts     每个产物的 sha256 / 字节数 / 节点边数 / 是否入库
    expected_stats 预期统计（可复算的口径，供 verify_release 现算比对）
    licenses      代码许可 + 数据许可登记文件

★ 铁律 #44：`expected_stats` 里凡是能从**入库产物**复算的，一律标 `recomputable: sample`；
  只有完整快照（不入库）的图级统计标 `recomputable: full_snapshot`，并注明获取方式。
★ 不写时间戳进样例文件；manifest 自己可以有 `created`。

用法
----
    python 06_PoC/release/make_manifest.py                 # 生成/刷新 manifest.json
    python 06_PoC/release/make_manifest.py --no-stats      # 跳过仪器现算（快速）
    python 06_PoC/release/make_manifest.py --pack          # 额外打包完整快照到 dist/
"""
from __future__ import annotations

import argparse
import datetime as _dt
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
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))   # ★ E9：复用 source_registry.rmtree_force

import graph_export as gx                                                       # noqa: E402
import source_registry as sr                                                     # noqa: E402

MANIFEST = os.path.join(HERE, "manifest.json")
SAMPLE_DIR = os.path.join(HERE, "sample")
LOCK = os.path.join(ROOT, "requirements.lock")
DIST = os.path.join(ROOT, "dist")

# 需要登记为「发布产物」的文件
SAMPLE_FILES = ("sample_normalized.json", "sample_graph.json", "sample_queries.json")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                             text=True, encoding="utf-8", errors="replace")
        return (out.stdout or "").strip()
    except Exception:                                                           # noqa: BLE001
        return ""


def read_lock(path: str) -> dict:
    """解析 requirements.lock 的 `name==version` 行（含 python）。"""
    if not os.path.exists(path):
        return {}
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        k, _, v = line.partition("==")
        out[k.strip()] = v.strip()
    return out


def installed_versions(names) -> dict:
    import platform
    got = {"python": platform.python_version()}
    for m in names:
        if m == "python":
            continue
        try:
            mod = importlib.import_module(m)
            got[m] = str(getattr(mod, "__version__", "") or "")
        except Exception:                                                       # noqa: BLE001
            got[m] = None
    return got


def resolve_snapshot(explicit: str | None) -> str | None:
    """定位完整快照：显式 > .env 的 GRAPH_DATA_FILE > 最大 phaseN。"""
    if explicit:
        return explicit if os.path.exists(explicit) else None
    env = os.path.join(ROOT, ".env")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            m = re.match(r"\s*GRAPH_DATA_FILE\s*=\s*(\S+)", line)
            if m:
                p = os.path.join(ROOT, m.group(1).replace("/", os.sep))
                if os.path.exists(p):
                    return p
    cands = []
    for fn in os.listdir(POC):
        m = re.match(r"graph_data_phase(\d+)\.json$", fn)
        if m:
            cands.append((int(m.group(1)), os.path.join(POC, fn)))
    return max(cands)[1] if cands else None


def norm_stats(path: str) -> dict:
    """规范图的节点/边/学科统计。"""
    import collections
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    nodes, edges = d.get("nodes") or [], d.get("edges") or []
    subj = collections.Counter()
    for n in nodes:
        subj[gx.subject_of((n.get("props") or {}).get("domain"), str(n.get("id") or ""))] += 1
    return {"nodes": len(nodes), "edges": len(edges), "subjects": dict(sorted(subj.items()))}


def view_stats(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return {"nodes": len(d.get("nodes") or []), "edges": len(d.get("edges") or []),
            "schema": d.get("schema")}


def run_tool_file(base_cmd, out_path, timeout=900):
    """跑一个把结构化结果**写到文件**的仪器（task_trust_audit / connectivity_audit）。"""
    try:
        subprocess.run([*base_cmd, "--json", out_path], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if os.path.exists(out_path):
            with open(out_path, encoding="utf-8") as f:
                return json.load(f)
    except Exception:                                                           # noqa: BLE001
        pass
    return None


def run_tool_stdout(base_cmd, timeout=900):
    """跑一个把 JSON **打印到 stdout** 的仪器（frozen_gate --json）。"""
    try:
        r = subprocess.run(base_cmd, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return json.loads(r.stdout)
    except Exception:                                                           # noqa: BLE001
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="STTP 发布清单生成器（Phase 37 R06）")
    ap.add_argument("--snapshot", default=None, help="完整快照路径（默认取 .env GRAPH_DATA_FILE）")
    ap.add_argument("--no-stats", action="store_true", help="跳过仪器现算（快照统计记 null）")
    ap.add_argument("--pack", action="store_true", help="把完整快照打包到 dist/ 并记录校验和")
    ap.add_argument("--out", default=MANIFEST)
    a = ap.parse_args(argv)

    py = sys.executable
    created = _dt.datetime.now().astimezone().isoformat(timespec="seconds")

    # ---------------- artifacts ----------------
    artifacts = {"sample": {}, "full": {}}
    for fn in SAMPLE_FILES:
        p = os.path.join(SAMPLE_DIR, fn)
        if not os.path.exists(p):
            print("[ERROR] 缺少样例产物：%s（先跑 make_sample.py）" % p)
            return 2
        st = norm_stats(p) if fn == "sample_normalized.json" else (
            view_stats(p) if fn == "sample_graph.json" else
            {"queries": len(json.load(open(p, encoding="utf-8")).get("queries") or [])})
        artifacts["sample"][fn] = {
            "path": os.path.relpath(p, ROOT).replace("\\", "/"),
            "sha256": sha256_file(p), "bytes": os.path.getsize(p),
            "tracked_in_git": True, **st,
        }

    # ---------------- license profile（Phase 42 · A03）----------------
    #  从**样例产物自身**读出许可 profile 与剔除计数（读产物，不信构建器内存 —— 铁律 #14）。
    _sm = json.load(open(os.path.join(SAMPLE_DIR, "sample_normalized.json"), encoding="utf-8"))
    _met = _sm.get("meta") or {}
    license_block = {
        "profile": _met.get("license_profile"),
        "policy": _met.get("license_policy"),
        "excluded_edges": _met.get("license_excluded_edges"),
        "excluded_nodes": _met.get("license_excluded_nodes"),
        "excluded_by_source": _met.get("license_excluded_by_source"),
        "audit_violations": _met.get("license_audit_violations"),
        "redistributable_sources": _met.get("license_redistributable_sources"),
        "note": ("★ 铁律 #64：许可政策由程序执行 —— 发布样例必须 public_demo（默认）；"
                 "verify_release 会**独立重算**样例内每条边/每个节点的来源血缘闭包并断言 0 违规，"
                 "且断言剔除计数非真空（0 边 ⇒ 白名单形同虚设）。"),
    }

    snap = resolve_snapshot(a.snapshot)
    if snap:
        artifacts["full"]["snapshot"] = {
            "path": os.path.relpath(snap, ROOT).replace("\\", "/"),
            "sha256": sha256_file(snap), "bytes": os.path.getsize(snap),
            "tracked_in_git": False, **view_stats(snap),
            "obtain": "原始大数据不入库：本地 `bash sttp.sh export` 重建，或从 Release 资产下载同名快照",
        }
    norm = os.path.join(POC, "etl", "normalized.json")
    if os.path.exists(norm):
        artifacts["full"]["normalized"] = {
            "path": os.path.relpath(norm, ROOT).replace("\\", "/"),
            "sha256": sha256_file(norm), "bytes": os.path.getsize(norm),
            "tracked_in_git": False, **norm_stats(norm),
        }

    # ---------------- dependencies ----------------
    lock = read_lock(LOCK)
    inst = installed_versions(list(lock))
    drift = {}
    for k, v in lock.items():
        got = inst.get(k)
        if got != v:
            drift[k] = {"lock": v, "installed": got}
    deps = {
        "lock_file": os.path.relpath(LOCK, ROOT).replace("\\", "/"),
        "target": lock,
        "installed": inst,
        "drift": drift,
        "drift_note": ("漂移**不阻塞**运行（缺件才阻塞）；但化学/量纲结果建议在锁定版本上复核。"
                       "外部复评环境为 rdkit 2026.3.6 / pint 0.26.1 / networkx 3.7 / numpy 2.5.3。"),
    }

    # ---------------- expected stats ----------------
    exp = {
        "sample": {
            "recomputable": "sample（入库产物，verify_release 现算比对）",
            "nodes": artifacts["sample"]["sample_normalized.json"]["nodes"],
            "edges": artifacts["sample"]["sample_normalized.json"]["edges"],
            "subjects": artifacts["sample"]["sample_normalized.json"].get("subjects"),
        },
        "full_snapshot": None,
    }
    if snap and not a.no_stats:
        import tempfile
        tmp = tempfile.mkdtemp(prefix="sttp_manifest_")
        # ★ E9：本脚本原先**从不清理**该临时目录（实测泄漏 33 个 `sttp_manifest_*`）。
        #   用 `sr.rmtree_force` 先清只读位再删（Windows 的 git loose object 是只读的），
        #   删不掉即抛（fail-closed，铁律 #62），不再静默。
        try:
            ta = run_tool_file([py, os.path.join(POC, "task_trust_audit.py"),
                                "--graph", norm if os.path.exists(norm) else snap],
                               os.path.join(tmp, "astro.json"))
            fg = run_tool_stdout([py, os.path.join(POC, "frozen_gate.py"), "--json"])
            ca = run_tool_file([py, os.path.join(POC, "connectivity_audit.py"), "--strict"],
                               os.path.join(tmp, "conn.json"))
            exp["full_snapshot"] = {
                "recomputable": "full_snapshot（不入库；`bash sttp.sh release-full` 现算校验）",
                "nodes": artifacts["full"]["snapshot"]["nodes"],
                "edges": artifacts["full"]["snapshot"]["edges"],
                "task_trust": ta,
                "frozen_gate": ({"tally": fg.get("tally")} if isinstance(fg, dict) else None),
                "connectivity": (ca if isinstance(ca, dict) else None),
            }
        finally:
            sr.rmtree_force(tmp)          # ★ 失败即抛，由调用方决定是否致命

    # ---------------- pack ----------------
    packed = None
    if a.pack and snap:
        import tarfile
        os.makedirs(DIST, exist_ok=True)
        phase = re.search(r"phase(\d+)", os.path.basename(snap))
        name = "sttp_snapshot_phase%s.tar.gz" % (phase.group(1) if phase else "unknown")
        tarp = os.path.join(DIST, name)
        with tarfile.open(tarp, "w:gz") as tf:
            tf.add(snap, arcname=os.path.basename(snap))
        packed = {"path": os.path.relpath(tarp, ROOT).replace("\\", "/"),
                  "bytes": os.path.getsize(tarp), "sha256": sha256_file(tarp),
                  "note": "dist/ 不入库；上传为 Release 资产后即成为下载通道"}
        with open(tarp + ".sha256", "w", encoding="utf-8", newline="\n") as f:
            f.write("%s  %s\n" % (packed["sha256"], name))

    # ---------------- assemble ----------------
    # 脏检查**排除本清单自身**：否则「生成清单 → 工作区脏 → 再提交 → 又脏」形成循环，
    # 每次生成的 working_tree_dirty 都恒为真。清单不参与「被描述的工作区」判定。
    porcelain = [ln for ln in git("status", "--porcelain").splitlines()
                 if "06_PoC/release/manifest.json" not in ln]
    dirty = bool(porcelain)
    manifest = {
        "schema": "sttp-release-manifest/v1",
        "release": {
            "name": "STTP 研究快照",
            "phase": 37,
            "created": created,
            "positioning": "路线 A：研究原型（收敛版）—— 可复现研究 / 真实跨学科任务 / 方法比较",
            "code_license": "Apache-2.0",
            "data_license_doc": "DATA_LICENSES.md",
        },
        "code": {
            # ★ 权威锚点是 artifacts[*].sha256（与 git 提交无关）。
            #   git_head_at_build 只作**上下文**：本次发布提交是该提交的后继，
            #   清单本身即落在发布提交内 —— 故不把它写成"当前提交"（会自指）。
            "git_head_at_build": git("rev-parse", "HEAD"),
            "git_head_at_build_short": git("rev-parse", "--short", "HEAD"),
            "git_branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "working_tree_dirty_at_build": dirty,
            "authority_note": ("产物完整性以 artifacts[*].sha256 为权威；git_head_at_build 仅描述"
                               "清单生成时刻的仓库上下文（发布提交为其后继）。"),
            "dirty_sample": porcelain[:8],
        },
        "build": {
            "python": sys.version.split()[0],
            "commands": [
                "bash sttp.sh export                 # 从 Aura/本地重建完整快照（大数据，不入库）",
                "python 06_PoC/release/make_sample.py # 从规范图确定性抽取离线样例",
                "python 06_PoC/release/make_manifest.py --pack",
                "python 06_PoC/release/verify_release.py --release",
            ],
            "pipeline": [
                "11_真实数据/*_raw.json",
                "→ 06_PoC/etl/normalized.json（权威规范图）",
                "→ graph_export.build_graph_data()",
                "→ 06_PoC/graph_data_phase<N>.json（前端快照，.env GRAPH_DATA_FILE）",
                "→ 06_PoC/viz_server.py（页面 /graph_data.json + 查询 /api/*）",
            ],
        },
        "dependencies": deps,
        "artifacts": artifacts,
        "license": license_block,
        "expected_stats": exp,
        "counterexamples": {
            "path": "06_PoC/frozen_counterexamples.json",
            "cases": len(json.load(open(os.path.join(POC, "frozen_counterexamples.json"),
                                        encoding="utf-8")).get("cases") or []),
            "sample_runnable_kinds": ["dim_table", "gate_chem", "gate_math",
                                      "pq_quantity_gate", "attrib_check", "level_check",
                                      "dim_field_semantics", "dim_consistency",
                                      "src_registered", "lineage_acyclic"],
            "full_only_kinds": ["graph_scan"],
            "note": ("`graph_scan` 断言的是**全图不变量**（连通分量/分母外计数等），"
                     "在样例上无意义 → 样例模式只跑自包含子集（SKIP=0）。"),
        },
        "licenses": {
            "code": "Apache-2.0（根 LICENSE + NOTICE + 全部第一方源码 SPDX 头）",
            "vendor": "06_PoC/vendor/licenses/（cytoscape/cose-base/layout-base/cytoscape-fcose=MIT，MathJax=Apache-2.0）",
            "data": "DATA_LICENSES.md（逐源登记 + 打包纪律三态）",
            "raw_data_excluded": "公共数据集/ 与 *_raw.json 不入库；见 DATA_LICENSES.md 的对分发限制",
        },
        "packed_snapshot": packed,
    }

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")

    print("=" * 70)
    print("  发布清单（Phase 37 · R06）")
    print("=" * 70)
    print("  写入        : %s" % os.path.relpath(a.out, ROOT).replace("\\", "/"))
    print("  git HEAD    : %s%s" % (manifest["code"]["git_head_at_build_short"],
                                   "（生成时工作区另有未提交改动）" if dirty else ""))
    for fn, info in artifacts["sample"].items():
        print("  样例 %-24s %8.1f KB  sha256=%s" % (fn, info["bytes"] / 1024.0, info["sha256"][:16]))
    if snap:
        f = artifacts["full"]["snapshot"]
        print("  快照 %-24s %8.1f MB  sha256=%s" % (os.path.basename(snap),
                                                   f["bytes"] / 1048576.0, f["sha256"][:16]))
    print("  依赖漂移    : %s" % (", ".join("%s(%s≠%s)" % (k, v["installed"], v["lock"])
                                          for k, v in drift.items()) or "无"))
    if packed:
        print("  打包快照    : %s (%.1f MB)" % (packed["path"], packed["bytes"] / 1048576.0))
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
