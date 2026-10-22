# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""STTP 数据集**离线校验器**（Phase 70 · F5）—— 只依赖 Python 标准库。

为什么单独存在一个文件
----------------------
F5 的验收是「目录可被第三方 `wget` 后**离线校验通过**」。这句话里有两个词必须当真：

* **第三方** ⇒ 本文件会被**复制进数据集目录**，拿到目录的人只需要 `python3`，
  不 clone 仓库、不装依赖、不联网。
* **离线校验** ⇒ **不得** import 仓库里的任何模块（`source_registry` / `graph_export`
  一律不碰）。它只能相信目录里的字节 + 清单里登记的哈希。

这两个约束合起来意味着：本文件的判据**不能**复用构建侧的任何函数。
这不是重复劳动，而是刻意（铁律 #14：自检输入须独立）——若校验器调用构建器算哈希，
那就变成「构建器自己跟自己一致」，一个算错的哈希会两边一起错、且永不被发现。

判据（V1–V9）
-------------
| ID | 断言 | 红意味着什么 |
|---|---|---|
| V0 | `manifest.json` 可解析且 `schema` 匹配 | 目录不是本格式，或传输截断 |
| V1 | 声明集 == 实际集（缺/多都算） | 有文件没下全，或夹带了未登记内容 |
| V2 | 逐文件 sha256 + 字节数 == 清单登记 | **字节被改过**（下载损坏 / 被人动过） |
| V3 | JSONL 每行可解析、必填键齐备、行数 == 登记 | 格式退化 / 半行截断 |
| V4 | 边端点都在节点集内；节点 id、边 id 各自唯一 | 引用悬空（图不可用） |
| V5 | 每条记录 `license.redistribution` 与来源表一致 | **许可泄漏**（对外包混进不可再分发内容） |
| V6 | 清单里的计数 == 实测计数 | 清单描述的不是这份数据 |
| V7 | `SHA256SUMS` 自洽（`sha256sum -c` 等价）且文件集一致 | 不用 python 的人拿到的是错的校验清单 |
| V8 | 上游快照登记项形态完整 | 不知道这份数据从哪来 |
| V9 | 每个来源都在 `ATTRIBUTION.md` 有行（CC BY 须署名） | 署名义务没落地 |

**V8 的能力边界（必须说清楚，不许冒充能算）**：上游快照 81 MB 按惯例不入数据集目录，
所以本判据只能核对**登记值是否完整**，不能重算上游哈希。这条边界写进清单的 `boundary`。

`--selftest`
------------
在临时目录里**独立**造一份 3 节点 / 2 边的合成数据集（**不复用构建器代码**），
然后逐项注入已知缺陷，断言目标判据**真的会红**；外加一条负对照（不改 ⇒ 必须全绿）。
「PASS 只说明当前没红，不说明有能力红」（#38）—— 本自检就是补后半句。

用法
----
    python verify_dataset.py                  # 校验本文件所在目录
    python verify_dataset.py path/to/dataset
    python verify_dataset.py --selftest       # 判据可红性自检（不需要真实数据集）

退出码：`0` 全过 ｜ `1` 有判据红 ｜ `2` 缺前置（目录不存在 / manifest 缺失）。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile

MANIFEST = "manifest.json"
SUMS = "SHA256SUMS"
SELF = "verify_dataset.py"
MANIFEST_SCHEMA = "sttp-dataset-manifest/v1"
RECORD_SCHEMA = "sttp-dataset/v1"

NODE_KEYS = ("id", "type", "labels", "subject", "license")
EDGE_KEYS = ("id", "source", "target", "type", "kind", "license")
ATTRIBUTION = "ATTRIBUTION.md"
DOC_FILES = ("README.md", ATTRIBUTION, "LICENSE", "NOTICE", "DATA_LICENSES.md")


# ---------------------------------------------------------------- 基础工具
def sha256_file(path: str, bufsize: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def read_text(path: str) -> str:
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def load_jsonl(path: str):
    """返回 (rows, errors)。空行也算错误 —— 静默跳过空行会让「行数」失去意义。"""
    rows, errs = [], []
    with io.open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            s = line.strip()
            if not s:
                errs.append("第 %d 行为空" % i)
                continue
            try:
                obj = json.loads(s)
            except ValueError as e:
                errs.append("第 %d 行 JSON 解析失败：%s" % (i, e))
                continue
            if not isinstance(obj, dict):
                errs.append("第 %d 行不是对象（%s）" % (i, type(obj).__name__))
                continue
            rows.append(obj)
    return rows, errs


def count_by(rows, key):
    out = {}
    for r in rows:
        k = r.get(key)
        if not isinstance(k, str):
            k = "<none>"
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


# ---------------------------------------------------------------- 判据
def verify(dataset_dir: str):
    """返回 [(id, ok|None, detail)]；`None` = 因前置缺失无法判定（不得记为 PASS）。"""
    rep = []
    mpath = os.path.join(dataset_dir, MANIFEST)
    if not os.path.isfile(mpath):
        return [("V0", False, "缺 %s —— 目录不是数据集或传输未完成" % MANIFEST)]
    try:
        with io.open(mpath, "r", encoding="utf-8") as f:
            m = json.load(f)
    except ValueError as e:
        return [("V0", False, "%s 不是合法 JSON（很可能是下载被截断）：%s" % (MANIFEST, e))]
    rep.append(("V0", m.get("schema") == MANIFEST_SCHEMA,
                "schema=%r（期望 %r）" % (m.get("schema"), MANIFEST_SCHEMA)))

    files = [f for f in (m.get("files") or []) if isinstance(f, dict) and f.get("path")]
    declared = {f["path"]: f for f in files}
    try:
        actual = {n for n in os.listdir(dataset_dir)
                  if os.path.isfile(os.path.join(dataset_dir, n))}
    except OSError as e:
        return rep + [("V1", False, "目录不可读：%s" % e)]

    # ---- V1 声明集 == 实际集 ----
    expected = set(declared) | {MANIFEST, SUMS}
    missing, extra = sorted(expected - actual), sorted(actual - expected)
    if not missing and not extra:
        note = "齐备（声明 %d + 清单 2 = %d，实际 %d）" % (len(declared), len(expected), len(actual))
    else:
        note = "；".join((["缺 " + ",".join(missing)] if missing else [])
                        + (["多 " + ",".join(extra)] if extra else []))
    rep.append(("V1", not missing and not extra, note))

    # ---- V2 逐文件 sha256 / 字节数 ----
    bad = []
    for p, f in sorted(declared.items()):
        fp = os.path.join(dataset_dir, p)
        if not os.path.isfile(fp):
            bad.append("%s 不存在" % p)
            continue
        real_sha, real_n = sha256_file(fp), os.path.getsize(fp)
        if f.get("sha256") != real_sha:
            bad.append("%s sha256 不符（清单 %s / 实测 %s）"
                       % (p, str(f.get("sha256"))[:12], real_sha[:12]))
        if f.get("bytes") is not None and f["bytes"] != real_n:
            bad.append("%s 字节数不符（清单 %s / 实测 %d）" % (p, f["bytes"], real_n))
    rep.append(("V2", not bad, "%d 个文件逐个现算 sha256；%s"
                % (len(declared), "全部相符" if not bad else "★ " + "；".join(bad[:5]))))

    # ---- V3 JSONL 结构 + 行数 ----
    roles = {f.get("role"): p for p, f in declared.items()}
    npath, epath = roles.get("nodes"), roles.get("edges")
    nodes, edges, perr, eerr = [], [], [], []
    v3 = []
    if not npath or not epath:
        v3.append("清单缺 role=nodes / role=edges 的条目")
    else:
        nodes, perr = load_jsonl(os.path.join(dataset_dir, npath))
        edges, eerr = load_jsonl(os.path.join(dataset_dir, epath))
        for name, rows, errs, keys, path in (("节点", nodes, perr, NODE_KEYS, npath),
                                             ("边", edges, eerr, EDGE_KEYS, epath)):
            if errs:
                v3.append("%s 解析错误 %d 条（前 3：%s）" % (name, len(errs), errs[:3]))
            miss = {}
            for r in rows:
                for k in keys:
                    if k not in r:
                        miss[k] = miss.get(k, 0) + 1
            if miss:
                v3.append("%s 缺必填键 %s" % (name, miss))
            rec = declared[path].get("lines")
            if rec is not None and rec != len(rows):
                v3.append("%s 行数 %d ≠ 清单登记 %s" % (name, len(rows), rec))
    rep.append(("V3", not v3, "%d 行节点 / %d 行边可解析且键齐备；%s"
                % (len(nodes), len(edges), "全部合规" if not v3 else "★ " + "；".join(v3[:4]))))

    counts = m.get("counts") or {}

    # ---- V4 引用完整性 / 唯一性 ----
    v4 = []
    nid = [r.get("id") for r in nodes]
    eid = [r.get("id") for r in edges]
    dup_n = len(nid) - len(set(nid))
    dup_e = len(eid) - len(set(eid))
    if dup_n:
        v4.append("节点 id 重复 %d 个" % dup_n)
    if dup_e:
        v4.append("边 id 重复 %d 个" % dup_e)
    idset = set(nid)
    dangling = [e for e in edges
                if e.get("source") not in idset or e.get("target") not in idset]
    if dangling:
        v4.append("悬空边 %d 条（前 3：%s）" % (
            len(dangling), [(x.get("id"), x.get("source"), x.get("target")) for x in dangling[:3]]))
    if not nodes or not edges:
        # ★ 空数据上「0 条悬空边」是**恒真**的 —— 不许把真空当通过（#38 的反面）
        rep.append(("V4", None, "节点/边为空，无法判定引用闭合"))
    else:
        rep.append(("V4", not v4, "%d 节点 / %d 边；%s"
                    % (len(nodes), len(edges),
                       "引用闭合、id 唯一" if not v4 else "★ " + "；".join(v4))))

    # ---- V5 许可一致性 ----
    lic = m.get("license") or {}
    profile = (m.get("dataset") or {}).get("license_profile") or lic.get("license_profile")
    srcs = {s.get("id"): s for s in (lic.get("sources") or []) if isinstance(s, dict)}
    v5, unknown, leak = [], {}, {}
    for kind, rows in (("节点", nodes), ("边", edges)):
        for r in rows:
            l = r.get("license") or {}
            sid, st = l.get("source"), l.get("redistribution")
            s = srcs.get(sid)
            if s is None:
                unknown[sid or "<none>"] = unknown.get(sid or "<none>", 0) + 1
                continue
            if s.get("redistribution") != st:
                v5.append("%s %s 的记录态 %r ≠ 来源表 %r" % (kind, r.get("id"), st, s.get("redistribution")))
            if st != "redistributable":
                leak[kind] = leak.get(kind, 0) + 1
    if unknown:
        v5.append("记录引用了来源表里没有的来源 %s" % unknown)
    if profile == "public_demo" and leak:
        v5.append("public_demo 下仍有不可再分发记录 %s" % leak)
    rep.append(("V5", not v5, "profile=%s；来源表 %d 项；%s"
                % (profile, len(srcs),
                   "每条记录均可再分发" if not v5 else "★ " + "；".join(v5[:4]))))

    # ---- V6 计数一致 ----
    v6 = []
    for key, real_n in (("nodes", len(nodes)), ("edges", len(edges))):
        if counts.get(key) is None:
            v6.append("清单缺 counts.%s" % key)
        elif counts[key] != real_n:
            v6.append("counts.%s=%s ≠ 实测 %d" % (key, counts[key], real_n))
    for key, rows, field in (("node_types", nodes, "type"), ("edge_types", edges, "type"),
                             ("subjects", nodes, "subject")):
        rec = counts.get(key)
        if rec is None:
            v6.append("清单缺 counts.%s" % key)
            continue
        real = count_by(rows, field)
        if rec != real:
            diff = sorted(set(rec) | set(real))
            v6.append("counts.%s 不符（差异 %s）" % (
                key, [(d, rec.get(d), real.get(d)) for d in diff if rec.get(d) != real.get(d)][:3]))
    rep.append(("V6", not v6, "节点类型 %d / 边类型 %d / 学科 %d 与清单逐项相等"
                % (len(counts.get("node_types") or {}), len(counts.get("edge_types") or {}),
                   len(counts.get("subjects") or {})) if not v6 else "★ " + "；".join(v6[:3])))

    # ---- V7 SHA256SUMS 自洽 ----
    spath = os.path.join(dataset_dir, SUMS)
    v7 = []
    if not os.path.isfile(spath):
        v7.append("缺 %s" % SUMS)
    else:
        listed = {}
        for i, line in enumerate(read_text(spath).splitlines(), 1):
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) != 2 or len(parts[0]) != 64:
                v7.append("第 %d 行格式不合 `sha256sum -c` 约定：%r" % (i, line[:60]))
                continue
            listed[parts[1].lstrip("*")] = parts[0]
        want = actual - {SUMS}
        if set(listed) != want:
            v7.append("文件集不符（未登记 %s；登记了不存在 %s）"
                      % (sorted(want - set(listed)), sorted(set(listed) - want)))
        for p, h in sorted(listed.items()):
            fp = os.path.join(dataset_dir, p)
            if os.path.isfile(fp) and sha256_file(fp) != h:
                v7.append("%s 的 SHA256SUMS 哈希不符" % p)
    rep.append(("V7", not v7, "%s 可被 `sha256sum -c` 直接校验；%s"
                % (SUMS, "自洽" if not v7 else "★ " + "；".join(v7[:4]))))

    # ---- V8 上游登记（形态核对；字节不在包内） ----
    src = m.get("source") or {}
    v8 = []
    if not isinstance(src.get("sha256"), str) or len(src.get("sha256") or "") != 64:
        v8.append("source.sha256 缺失或不是 64 位十六进制")
    for k in ("bytes", "nodes", "edges"):
        if not isinstance(src.get(k), int) or src.get(k) <= 0:
            v8.append("source.%s 缺失或非正整数" % k)
    if not src.get("path"):
        v8.append("source.path 缺失")
    rep.append(("V8", not v8, "登记 %s（%s 节点 / %s 边，sha256=%s…）；"
                "★ 上游字节不在包内，本项只能核对登记、不能重算" % (
                    src.get("path") or "?", src.get("nodes"), src.get("edges"),
                    str(src.get("sha256"))[:12]) if not v8 else "★ " + "；".join(v8)))

    # ---- V9 署名落地 ----
    v9 = []
    apath = os.path.join(dataset_dir, ATTRIBUTION)
    if not os.path.isfile(apath):
        v9.append("缺 %s" % ATTRIBUTION)
    else:
        table = {}
        for line in read_text(apath).splitlines():
            if line.startswith("|") and line.count("|") >= 3:
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if cells and cells[0] and not set(cells[0]) <= set("-: "):
                    table[cells[0]] = cells[1:]
        for sid, s in sorted(srcs.items()):
            if sid not in table:
                v9.append("%s 未在 ATTRIBUTION 表内" % sid)
                continue
            licv = str(s.get("license") or "")
            if not licv or licv not in " | ".join(table[sid]):
                v9.append("%s 的许可文本未落到署名行（%r）" % (sid, licv))
    for d in DOC_FILES:
        fp = os.path.join(dataset_dir, d)
        if not os.path.isfile(fp) or os.path.getsize(fp) == 0:
            v9.append("%s 缺失或为空" % d)
    rep.append(("V9", not v9, "%d 个来源均已在 ATTRIBUTION 登记许可文本；随包文档齐备"
                % len(srcs) if not v9 else "★ " + "；".join(v9[:4])))
    return rep


# ---------------------------------------------------------------- 合成夹具（--selftest）
def _sums_text(dataset_dir: str, names) -> str:
    return "".join("%s  %s\n" % (sha256_file(os.path.join(dataset_dir, n)), n)
                   for n in sorted(names))


def _write_jsonl(path: str, rows) -> None:
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")) + "\n")


def _write_fixture(d: str) -> dict:
    """**独立**造一份最小数据集（刻意不复用 `export_dataset.py` 的任何函数）。

    独立不是因为懒得 import，而是：若夹具与构建器共用同一套写盘/算哈希代码，
    `--selftest` 就只能证明「同一个人两次算得一样」，证明不了**格式约定本身可判**。
    """
    os.makedirs(d, exist_ok=True)
    nodes = [
        {"id": "A", "type": "Formula", "labels": ["Formula", "Entity"], "subject": "物理",
         "license": {"source": "curated_seed", "redistribution": "redistributable"}},
        {"id": "B", "type": "Symbol", "labels": ["Symbol", "Entity"], "subject": "物理",
         "license": {"source": "curated_seed", "redistribution": "redistributable"}},
        {"id": "C", "type": "Molecule", "labels": ["Entity", "Molecule"], "subject": "化学",
         "license": {"source": "ChEBI", "redistribution": "redistributable"}},
    ]
    edges = [
        {"id": "e1", "source": "A", "target": "B", "type": "has_symbol", "kind": "has_symbol",
         "license": {"source": "curated_seed", "redistribution": "redistributable"}},
        {"id": "e2", "source": "A", "target": "C", "type": "defines", "kind": "defines",
         "license": {"source": "ChEBI", "redistribution": "redistributable"}},
    ]
    _write_jsonl(os.path.join(d, "nodes.jsonl"), nodes)
    _write_jsonl(os.path.join(d, "edges.jsonl"), edges)

    docs = {
        "README.md": "# 合成夹具（--selftest 专用）\n",
        "LICENSE": "Apache License\nVersion 2.0\n",
        "NOTICE": "STTP synthetic fixture\n",
        "DATA_LICENSES.md": "Rhea CC BY 4.0\nChEBI CC BY 4.0\n",
    }
    for n, body in docs.items():
        with io.open(os.path.join(d, n), "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
    sources = [
        {"id": "ChEBI", "license": "CC BY 4.0（EMBL-EBI）", "redistribution": "redistributable"},
        {"id": "curated_seed", "license": "Apache-2.0（STTP 原创）", "redistribution": "redistributable"},
    ]
    with io.open(os.path.join(d, ATTRIBUTION), "w", encoding="utf-8", newline="\n") as f:
        f.write("| 来源 | 许可 | 再分发 |\n|---|---|---|\n")
        for s in sources:
            f.write("| %s | %s | %s |\n" % (s["id"], s["license"], s["redistribution"]))
    shutil.copyfile(os.path.abspath(__file__), os.path.join(d, SELF))

    files = []
    for p, role in (("nodes.jsonl", "nodes"), ("edges.jsonl", "edges")):
        files.append({"path": p, "role": role, "bytes": os.path.getsize(os.path.join(d, p)),
                      "sha256": sha256_file(os.path.join(d, p)), "lines": 3 if role == "nodes" else 2})
    for p in (SELF,) + DOC_FILES:
        fp = os.path.join(d, p)
        files.append({"path": p, "role": "doc", "bytes": os.path.getsize(fp),
                      "sha256": sha256_file(fp)})
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "dataset": {"name": "fixture", "version": "dataset_v0", "record_schema": RECORD_SCHEMA,
                    "license_profile": "public_demo"},
        "source": {"path": "fixture/none.json", "sha256": "0" * 64, "bytes": 1,
                   "nodes": 3, "edges": 2},
        "files": files,
        "counts": {"nodes": 3, "edges": 2,
                   "node_types": count_by(nodes, "type"), "edge_types": count_by(edges, "type"),
                   "subjects": count_by(nodes, "subject")},
        "license": {"sources": sources},
        "boundary": ["夹具：上游字节不存在"],
    }
    with io.open(os.path.join(d, MANIFEST), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write("\n")
    names = [f["path"] for f in files] + [MANIFEST]
    with io.open(os.path.join(d, SUMS), "w", encoding="utf-8", newline="\n") as f:
        f.write(_sums_text(d, names))
    return manifest


def _refresh_sums(d: str) -> None:
    """把 SHA256SUMS 按当前字节重算 —— 用于「改了清单但不改校验和」的定位实验，
    这样目标判据的红就不会被 V7 的连带红掩盖掉（判据要**各自**可红）。"""
    names = [n for n in os.listdir(d) if os.path.isfile(os.path.join(d, n)) and n != SUMS]
    with io.open(os.path.join(d, SUMS), "w", encoding="utf-8", newline="\n") as f:
        f.write(_sums_text(d, names))


def _mut_set_manifest(d: str, fn) -> None:
    p = os.path.join(d, MANIFEST)
    m = json.loads(read_text(p))
    fn(m)
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(m, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write("\n")


def selftest() -> int:
    """逐项注入已知缺陷，断言目标判据**真的会红**（+ 负对照）。"""
    cases = []

    def add(name, mutate, want):
        cases.append((name, mutate, want))

    add("VS1 负对照：不作任何改动 ⇒ 判据必须全绿（证明「变红」不是恒真）",
        None, None)

    def m_byte(d):
        p = os.path.join(d, "nodes.jsonl")
        b = open(p, "rb").read()
        open(p, "wb").write(b.replace(b'"subject":"\xe7\x89\xa9\xe7\x90\x86"',
                                      b'"subject":"\xe7\x89\xa9\xe7\x90\x86" ', 1))
    add("VS2 数据字节被改一位（JSON 仍可解析、语义不变）⇒ V2 必须变红（哈希才是那条线）",
        m_byte, ["V2"])

    def m_drop_line(d):
        p = os.path.join(d, "nodes.jsonl")
        rows = open(p, encoding="utf-8").readlines()
        open(p, "w", encoding="utf-8", newline="\n").write("".join(rows[:-1]))
    add("VS3 删掉最后一行节点 ⇒ V3 必须变红（行数是格式契约）", m_drop_line, ["V3"])

    def m_dangling(d):
        with io.open(os.path.join(d, "edges.jsonl"), "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"id": "e9", "source": "A", "target": "ZZZ", "type": "has_symbol",
                                "kind": "has_symbol",
                                "license": {"source": "curated_seed",
                                            "redistribution": "redistributable"}},
                               ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    add("VS4 加一条指向不存在节点的边 ⇒ V4 必须变红（悬空引用）", m_dangling, ["V4"])

    def m_license(d):
        rows = [json.loads(l) for l in open(os.path.join(d, "nodes.jsonl"), encoding="utf-8")]
        rows[2]["license"]["redistribution"] = "internal_only"
        _write_jsonl(os.path.join(d, "nodes.jsonl"), rows)
    add("VS5 对外包里混进一条 internal_only ⇒ V5 必须变红（许可泄漏是红线）", m_license, ["V5"])

    def m_counts(d):
        _mut_set_manifest(d, lambda m: m["counts"].__setitem__("nodes", 4))
        _refresh_sums(d)
    add("VS6 清单把节点数写成 4（真值 3，校验和已同步）⇒ V6 必须变红（清单描述的不是这份数据）",
        m_counts, ["V6"])

    def m_sums(d):
        p = os.path.join(d, SUMS)
        lines = read_text(p).splitlines()
        h, n = lines[0].split()
        lines[0] = "%s  %s" % ("0" * 64, n)
        open(p, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    add("VS7 SHA256SUMS 里篡改一个哈希 ⇒ V7 必须变红（不装 python 的人只能靠它）", m_sums, ["V7"])

    add("VS8 少下载一个文件 ⇒ V1 必须变红（缺件不得当成完整）",
        lambda d: os.remove(os.path.join(d, ATTRIBUTION)), ["V1"])

    def m_extra(d):
        open(os.path.join(d, "notes.txt"), "w", encoding="utf-8").write("夹带\n")
    add("VS9 目录里多一个未登记文件 ⇒ V1 必须变红（未登记内容不得随包发出去）",
        m_extra, ["V1"])

    print("=" * 78)
    print("  verify_dataset.py --selftest：合成夹具逐项注入缺陷，判据必须真的变红")
    print("=" * 78)
    n_pass, n_fail = 0, 0
    for name, mutate, want in cases:
        tmp = tempfile.mkdtemp(prefix="sttp-ds-st-")
        try:
            _write_fixture(tmp)
            if mutate is not None:
                mutate(tmp)
            rows = verify(tmp)
            reds = [rid for rid, ok, _ in rows if ok is False]
            unknown = [rid for rid, ok, _ in rows if ok is None]
            if want is None:
                ok = not reds and not unknown
                note = "全绿（%d 条判据）" % len(rows) if ok else "★ 意外变红：%s" % (reds or unknown)
            else:
                ok = all(w in reds for w in want)
                note = ("目标 %s 已变红（连带 %s）"
                        % (want, [r for r in reds if r not in want] or "无")
                        if ok else "★ 目标 %s 没变红（红的是 %s）" % (want, reds))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, "\n        " + note))
        n_pass += 1 if ok else 0
        n_fail += 0 if ok else 1
    print("-" * 78)
    print("  ⇒ %s（PASS %d / FAIL %d）" % ("全过" if not n_fail else "未通过", n_pass, n_fail))
    return 0 if not n_fail else 1


# ---------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="STTP 数据集离线校验器（Phase 70 · F5）")
    ap.add_argument("dataset_dir", nargs="?", default=None,
                    help="数据集目录（默认：本文件所在目录）")
    ap.add_argument("--selftest", action="store_true", help="判据可红性自检（不需要真实数据集）")
    ap.add_argument("--quiet", action="store_true", help="只打印失败项与结论")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()

    d = a.dataset_dir or os.path.dirname(os.path.abspath(__file__))
    if not os.path.isdir(d):
        print("[前置缺失] 目录不存在：%s（RC=2：不冒充校验通过）" % d)
        # ★ 铁律 #92：非通过出口必须自述解释器。**本文件刻意内联打印而不 import
        #   `06_PoC/interp_note.py`** —— 它会被复制进对外数据集、由第三方在**仓库之外**
        #   以 `python3 verify_dataset.py` 运行（Phase 70 F5），一旦依赖仓库内模块就失去
        #   「下载即可自证」的性质（铁律 #53）。此处的偏离已登记在 `interp_selfreport_check.EXEMPT`。
        print("        原因：数据集目录缺件 · 解释器 %s" % sys.executable)
        return 2
    rows = verify(d)
    if not rows:
        print("[前置缺失] 无法判定（RC=2）")
        print("        原因：产物里没有可判定的记录 · 解释器 %s" % sys.executable)
        return 2
    w = max(len(r[0]) for r in rows) + 1
    for rid, ok, detail in rows:
        if a.quiet and ok is True:
            continue
        tag = "[PASS]" if ok else ("[SKIP]" if ok is None else "[FAIL]")
        print("%s %-*s %s" % (tag, w, rid, detail))
    n_pass = sum(1 for r in rows if r[1] is True)
    n_fail = sum(1 for r in rows if r[1] is False)
    print("-" * 78)
    print("  %s（PASS %d / FAIL %d / 共 %d）｜目录 %s"
          % ("离线校验通过" if not n_fail else "★ 校验未通过", n_pass, n_fail, len(rows), d))
    return 0 if not n_fail else 1


if __name__ == "__main__":
    sys.exit(main())
