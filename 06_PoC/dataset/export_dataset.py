# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""STTP 数据集构建器（Phase 70 · F5）—— JSONL + manifest + sha256。

F5 的验收原文：「目录可被第三方 `wget` 后**离线校验通过**」。
本文件只负责**产出**那个目录；能不能被校验通过，由目录里的
`verify_dataset.py`（会随包复制过去）**独立**判定 —— 构建器**不给自己发合格证**。

★ 三条「不造第二套口径」的硬约束（否则 #105 明天就会来收账）
------------------------------------------------------------
1. **许可过滤**：直接调用 `06_PoC/release/make_sample.py::license_partition`，
   不在这里重新实现一遍「哪些来源可再分发」。三态表只有 `source_registry` 一份。
2. **投影格式**：直接调用 `06_PoC/graph_export.py::project_node / project_edge`，
   与可视化 `formula-graph-view/v1` 共用同一个投影函数。
3. **来源清单**：`pipeline_outputs`-式的「唯一事实源」思路 —— 产出文件名在
   `FILES` 一处声明，写盘/清单/校验三处全部从它派生（并用 S4 反向断言「声明集 == 实际写出集」）。

为什么先写临时目录再提升（fail-closed）
--------------------------------------
`dist.tmp` 里构建 → 跑 `verify_dataset.py` → **0 FAIL 才** `os.replace` 成 `dist/`。
直接写 `dist/` 的话，一次失败的构建会留下一份「看起来像数据集、其实不完整」的目录，
而下游（wget 测试、发布资产）只看目录在不在 —— 那正是「静默产出坏东西」。

判据（S1–S7）
-------------
| ID | 断言 |
|---|---|
| S1 | `manifest.source.sha256` == 构建后**重新现算**的上游哈希（不是转手抄的变量） |
| S2 | 许可后置审计 0 违规（`audit_redistribution` **独立重算**，不听分区函数自述） |
| S3 | 分区计数自洽：导出量 == 分区输出量，且 `excluded == total - kept` |
| S4 | 清单声明的文件集 == 目录里实际写出的文件集（含反向：多写的也算红） |
| S5 | **确定性**：同输入二次全量重建，逐字节相同（无时间戳/无随机/排序固定） |
| S6 | 产物上 `verify_dataset.py` 0 FAIL（不通过 ⇒ 不提升，RC=3） |
| S7 | 每条边的两端都在导出节点集内（分区后不得产生悬空边） |

用法
----
    python 06_PoC/dataset/export_dataset.py                 # 构建到 06_PoC/dataset/dist/
    python 06_PoC/dataset/export_dataset.py --e2e           # 构建后追加 wget 下载 + 离线校验
    python 06_PoC/dataset/export_dataset.py --selfcheck     # 许可判据可红性自检（合成图，不建包）
    bash sttp.sh dataset [--e2e]

退出码：`0` 成功 ｜ `1` 判据红 ｜ `2` 缺前置（上游快照不存在）｜ `3` 产物未通过离线校验。
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))          # 06_PoC/dataset
POC = os.path.dirname(HERE)                                # 06_PoC
ROOT = os.path.dirname(POC)                                # STTP
sys.path.insert(0, POC)
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
sys.path.insert(0, os.path.join(POC, "release"))

import graph_export as gx                                  # noqa: E402  ← 投影唯一口径
import source_registry as sr                               # noqa: E402  ← 三态唯一口径
import make_sample as ms                                   # noqa: E402  ← 许可分区唯一口径

DEFAULT_SRC = os.path.join(POC, "etl", "normalized.json")
DIST = os.path.join(HERE, "dist")
VERIFIER = os.path.join(HERE, "verify_dataset.py")
DIST_README = os.path.join(HERE, "README_dist.md")

MANIFEST_SCHEMA = "sttp-dataset-manifest/v1"
RECORD_SCHEMA = "sttp-dataset/v1"
DATASET_VERSION = "dataset_v1.0.0"

# 产物文件集（**一处声明**：写盘 / 清单 / 校验全部从它派生）
DATA_FILES = (("nodes.jsonl", "nodes"), ("edges.jsonl", "edges"))
DOC_FILES = ("README.md", "ATTRIBUTION.md", "LICENSE", "NOTICE", "DATA_LICENSES.md")
CODE_FILES = ("verify_dataset.py",)
FILES = tuple(p for p, _ in DATA_FILES) + CODE_FILES + DOC_FILES
SUMS = "SHA256SUMS"
MANIFEST = "manifest.json"
UNREGISTERED = "<unregistered>"


# ---------------------------------------------------------------- 工具
def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def write_text(path: str, body: str) -> None:
    """★ `newline="\\n"` 是**确定性**的一部分：Windows 默认 CRLF 会让同一份数据
    在两台机器上产出不同 sha256（本项目已因 CRLF 吃过一次亏，见 #105 旁的记录）。"""
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)


def write_jsonl(path: str, rows) -> None:
    write_text(path, "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for r in rows))


def write_json(path: str, obj) -> None:
    write_text(path, json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def license_of(sid) -> dict:
    """记录的 `license` 字段 = 该来源在其血缘闭包下的三态（`source_registry` 唯一口径）。"""
    sid = sid if isinstance(sid, str) and sid else UNREGISTERED
    return {"source": sid, "redistribution": sr.redistribution_closure(sid)}


def count_by(rows, key: str) -> dict:
    c = collections.Counter(r.get(key) if isinstance(r.get(key), str) else "<none>" for r in rows)
    return dict(sorted(c.items()))


# ---------------------------------------------------------------- 投影
def project_records(nodes, edges):
    """规范图 → 数据集记录。投影复用 `graph_export`（不另写一套字段映射）。"""
    nrec = []
    for n in nodes:
        r = gx.project_node(n)
        r["license"] = license_of((n.get("props") or {}).get("source"))
        nrec.append(r)
    erec = []
    for e in edges:
        r = gx.project_edge(e)
        r["attrs"] = dict(e.get("props") or {})       # 原始属性全量透传（无损回放）
        r["license"] = license_of((e.get("props") or {}).get("source"))
        erec.append(r)
    nrec.sort(key=lambda r: str(r.get("id")))
    erec.sort(key=lambda r: str(r.get("id")))
    return nrec, erec


def source_table(nrec, erec):
    """来源表：每个来源的许可文本 / 版本 / 三态 / 记录数。ATTRIBUTION 与 V5 都用它。"""
    cnt = collections.defaultdict(lambda: {"nodes": 0, "edges": 0})
    for r in nrec:
        cnt[(r.get("license") or {}).get("source")]["nodes"] += 1
    for r in erec:
        cnt[(r.get("license") or {}).get("source")]["edges"] += 1
    out = []
    for sid in sorted(cnt, key=lambda s: str(s)):
        e = sr.get(sid) or {}
        sid_ok = sid if isinstance(sid, str) and sid else UNREGISTERED
        lic = e.get("license") or ("未登记来源（forbidden，不得分发）"
                                   if sid_ok == UNREGISTERED else "（登记表未给出许可文本）")
        out.append({
            "id": sid_ok,
            "license": lic,
            "version": e.get("version") or "-",
            "kind": e.get("kind") or "unregistered",
            "upstream": list(e.get("upstream") or []),
            "redistribution": sr.redistribution_closure(sid_ok),
            "nodes": cnt[sid]["nodes"], "edges": cnt[sid]["edges"],
        })
    return out


def attribution_md(srcs, profile) -> str:
    head = [
        "# 来源与署名（ATTRIBUTION）",
        "",
        "本数据集由 STTP 汇总，**按来源分别声明**许可。使用/再分发时请保留本文件，",
        "并遵循下表每行的许可要求（CC BY 类来源须署名 + 注明版本 + 许可链接 + 修改说明）。",
        "",
        "数据集许可 profile：`%s`" % profile,
        "",
        "| 来源 | 许可 | 版本 | 再分发状态 | 节点数 | 边数 | 上游 |",
        "|---|---|---|---|---:|---:|---|",
    ]
    rows = ["| %s | %s | %s | %s | %d | %d | %s |" % (
        s["id"], s["license"], s["version"], s["redistribution"],
        s["nodes"], s["edges"], "、".join(s["upstream"]) or "—") for s in srcs]
    tail = [
        "",
        "`再分发状态` 取自 STTP 的 `11_真实数据/source_registry.py`（三态表）。",
        "`redistributable` 表示可随本包再分发；`internal_only` / `forbidden` **不得对外分发**。",
        "",
        "图结构与派生关系（schema、边类型、学科分类）为 STTP 原创，按 Apache-2.0 提供（见 LICENSE）。",
        "",
    ]
    return "\n".join(head + rows + tail)


# ---------------------------------------------------------------- 构建
def load_source(path: str):
    with io.open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    return d.get("nodes") or [], d.get("edges") or []


def build_into(src_path: str, profile: str, out_dir: str) -> dict:
    """把数据集写到 `out_dir`（**不做**校验，也不提升）。返回构建统计。"""
    nodes, edges = load_source(src_path)
    pnodes, pedges, lic = ms.license_partition(nodes, edges, profile)
    nrec, erec = project_records(pnodes, pedges)
    srcs = source_table(nrec, erec)

    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)

    write_jsonl(os.path.join(out_dir, "nodes.jsonl"), nrec)
    write_jsonl(os.path.join(out_dir, "edges.jsonl"), erec)

    # 文档 / 代码（随包，保证第三方自包含）
    shutil.copyfile(DIST_README, os.path.join(out_dir, "README.md"))
    for name, src in (("LICENSE", os.path.join(ROOT, "LICENSE")),
                      ("NOTICE", os.path.join(ROOT, "NOTICE")),
                      ("DATA_LICENSES.md", os.path.join(ROOT, "DATA_LICENSES.md")),
                      ("verify_dataset.py", VERIFIER)):
        shutil.copyfile(src, os.path.join(out_dir, name))
    write_text(os.path.join(out_dir, "ATTRIBUTION.md"), attribution_md(srcs, profile))

    files = []
    for p, role in DATA_FILES:
        fp = os.path.join(out_dir, p)
        files.append({"path": p, "role": role, "bytes": os.path.getsize(fp),
                      "sha256": sha256_file(fp),
                      "lines": len(nrec) if role == "nodes" else len(erec)})
    for p in CODE_FILES + DOC_FILES:
        fp = os.path.join(out_dir, p)
        files.append({"path": p, "role": "doc", "bytes": os.path.getsize(fp),
                      "sha256": sha256_file(fp)})

    src_sha = sha256_file(src_path)
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "dataset": {
            "name": "STTP Formula Graph Dataset",
            "version": DATASET_VERSION,
            "version_status": "格式版本；与图快照 ID 的绑定（规划 F9）尚未做",
            "record_schema": RECORD_SCHEMA,
            "license_profile": profile,
            "license_policy": lic.get("license_policy"),
            "determinism": "同输入 ⇒ 同字节：无时间戳、无随机数、记录按 id 排序、行尾固定 LF",
        },
        "source": {
            "path": os.path.relpath(src_path, ROOT).replace(os.sep, "/"),
            "sha256": src_sha,
            "bytes": os.path.getsize(src_path),
            "nodes": len(nodes), "edges": len(edges),
            "tracked_in_git": False,
            "obtain": "原始快照不入库：本地 `bash sttp.sh export` 重建，或从 Release 资产下载同名快照",
        },
        "generator": {
            "path": "06_PoC/dataset/export_dataset.py",
            "sha256": sha256_file(os.path.abspath(__file__)),
            "python": "%d.%d.%d" % sys.version_info[:3],
            "command": "bash sttp.sh dataset",
        },
        "files": files,
        "counts": {
            "nodes": len(nrec), "edges": len(erec),
            "node_types": count_by(nrec, "type"),
            "edge_types": count_by(erec, "type"),
            "subjects": count_by(nrec, "subject"),
            "license_excluded_edges": lic.get("license_excluded_edges"),
            "license_excluded_nodes": lic.get("license_excluded_nodes"),
            "license_excluded_by_source": lic.get("license_excluded_by_source"),
        },
        "license": {
            "profile": profile,
            "policy": lic.get("license_policy"),
            "sources": srcs,
        },
        "verify": {
            "command": "python3 verify_dataset.py",
            "assertions": ["V0", "V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9"],
            "sums_command": "sha256sum -c SHA256SUMS",
        },
        "boundary": [
            "上游快照字节不在包内（约 82 MB，按仓库惯例不入库）⇒ V8 只能核对登记值，不能重算",
            "来源三态（redistributable/internal_only/forbidden）的判定规则在仓库 source_registry.py；"
            "包内只有判定结果，无法独立重算「某上游该不该可再分发」",
            "sha256 覆盖字节而非语义：语义由 counts（V6）、引用闭合（V4）、上游溯源（V8）共同约束",
        ],
    }
    write_json(os.path.join(out_dir, MANIFEST), manifest)
    names = [f["path"] for f in files] + [MANIFEST]
    write_text(os.path.join(out_dir, SUMS), "".join(
        "%s  %s\n" % (sha256_file(os.path.join(out_dir, n)), n) for n in names))
    return {"manifest": manifest, "nodes": nrec, "edges": erec, "lic": lic,
            "pnodes": pnodes, "pedges": pedges,
            "src_sha": src_sha, "src_nodes": len(nodes), "src_edges": len(edges)}


# ---------------------------------------------------------------- 判据
class Rep:
    def __init__(self):
        self.rows = []

    def add(self, ok, name, note=""):
        self.rows.append((ok, name, note))
        tag = "[PASS]" if ok is True else ("[SKIP]" if ok is None else "[FAIL]")
        print("%s %s  %s" % (tag, name, note))

    @property
    def n_fail(self):
        return sum(1 for ok, _, _ in self.rows if ok is False)

    @property
    def n_skip(self):
        return sum(1 for ok, _, _ in self.rows if ok is None)


def run_verifier(dataset_dir: str) -> tuple[int, str]:
    r = subprocess.run([sys.executable, os.path.join(dataset_dir, "verify_dataset.py"),
                        dataset_dir], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def judge(rep: Rep, src_path: str, out_dir: str, st: dict, total_nodes: int, total_edges: int):
    m = st["manifest"]
    nrec, erec, lic = st["nodes"], st["edges"], st["lic"]

    # S1 源哈希：构建后**重新现算**，与清单登记的比
    again = sha256_file(src_path)
    rep.add(m["source"]["sha256"] == again and len(again) == 64,
            "S1 清单登记的源快照 sha256 == 构建后现算值",
            "%s（%d 字节 / 源 %d 节点 %d 边）" % (again[:16], m["source"]["bytes"],
                                                 st["src_nodes"], st["src_edges"]))

    # S2 许可后置审计（独立重算）
    # ★ 必须喂**分区后的规范图**（`props.source` 在这里），不是投影后的记录
    #   （投影把原始属性收进了 `attrs`）—— 2026-10-22 首次构建就踩了：喂错输入时
    #   审计把 16,251 条 `props` 缺失读成"来源 <none>"⇒ S2 红。判据没错，是我的接线错。
    viol = ms.audit_redistribution(st["pnodes"], st["pedges"])
    profile = m["dataset"]["license_profile"]
    if profile == "public_demo":
        rep.add(not viol, "S2 public_demo 下独立重算的再分发违规 == 0",
                "0 违规（`audit_redistribution` 重算，非分区函数自述）" if not viol
                else "★ %d 处违规（前 3：%s）" % (len(viol), viol[:3]))
    else:
        rep.add(True, "S2 internal_research 不过滤（如实记录违规数，不冒充对外可用）",
                "违规 %d 处 —— 本 profile 仅供内部复现，**不得对外分发**" % len(viol))

    # S3 分区计数自洽
    exp_n = len(nrec)
    exp_e = len(erec)
    ok3 = (lic["license_excluded_edges"] == total_edges - exp_e
           and lic["license_excluded_nodes"] == total_nodes - exp_n
           and exp_e > 0 and exp_n > 0)
    rep.add(ok3, "S3 分区计数自洽（excluded == 总 - 保留）",
            "保留 %d 节点 / %d 边；剔除 %d 边 / %d 节点"
            % (exp_n, exp_e, lic["license_excluded_edges"], lic["license_excluded_nodes"]))

    # S4 声明集 == 实际写出集（反向）
    declared = {f["path"] for f in m["files"]} | {MANIFEST, SUMS}
    actual = {n for n in os.listdir(out_dir) if os.path.isfile(os.path.join(out_dir, n))}
    rep.add(declared == actual, "S4 声明集 == 实际写出集（双向；多写一个也算红）",
            "齐备 %d 个文件" % len(actual) if declared == actual
            else "★ 缺 %s；多 %s" % (sorted(declared - actual), sorted(actual - declared)))

    # S7 边端点闭合
    ids = {r.get("id") for r in nrec}
    bad = [e for e in erec if e.get("source") not in ids or e.get("target") not in ids]
    rep.add(not bad, "S7 每条边的两端都在导出节点集内（分区后无悬空）",
            "%d 条边全部闭合" % len(erec) if not bad
            else "★ %d 条悬空（前 3：%s）" % (len(bad), [x.get("id") for x in bad[:3]]))
    return m


def determinism(rep: Rep, src_path: str, profile: str, ref_dir: str):
    """S5：同输入二次**全量**重建（重新读盘、重新分区、重新序列化），与 `ref_dir` 逐字节比。"""
    import tempfile
    tmp2 = tempfile.mkdtemp(prefix="sttp-ds-det-")
    try:
        build_into(src_path, profile, os.path.join(tmp2, "d"))
        diffs = []
        for p in FILES + (MANIFEST, SUMS):
            if sha256_file(os.path.join(ref_dir, p)) != sha256_file(os.path.join(tmp2, "d", p)):
                diffs.append(p)
        rep.add(not diffs, "S5 确定性：二次全量重建逐字节相同（无时间戳/无随机/排序固定）",
                "%d 个文件 sha256 全部相同" % len(FILES + (MANIFEST, SUMS)) if not diffs
                else "★ 不一致：%s" % diffs)
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)


def promote(tmp_dir: str, out_dir: str) -> None:
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.replace(tmp_dir, out_dir)


# ---------------------------------------------------------------- 许可自检（合成图）
def selfcheck() -> int:
    """E1–E5：用**合成图**证明许可过滤与后置审计都不是恒真。不看真实快照，秒级可跑。"""
    nodes = [
        {"id": "N1", "labels": ["Formula", "Entity"], "props": {"source": "curated_seed", "latex": "x"}},
        {"id": "N2", "labels": ["Symbol", "Entity"], "props": {"source": "curated_seed"}},
        {"id": "N3", "labels": ["Entity", "Molecule"], "props": {"source": "PhysicsBabel"}},   # internal_only
        {"id": "N4", "labels": ["Formula", "Entity"], "props": {}},                            # 缺 source
    ]
    edges = [
        {"id": "E1", "source": "N1", "target": "N2", "type": "has_symbol", "kind": "has_symbol",
         "props": {"source": "curated_seed"}},
        {"id": "E2", "source": "N1", "target": "N3", "type": "has_symbol", "kind": "has_symbol",
         "props": {"source": "ElementKG2.0"}},
        {"id": "E3", "source": "N1", "target": "N4", "type": "has_symbol", "kind": "has_symbol",
         "props": {"source": "curated_seed"}},
    ]
    pub_n, pub_e, pub_i = ms.license_partition(nodes, edges, "public_demo")
    int_n, int_e, int_i = ms.license_partition(nodes, edges, "internal_research")

    rep = Rep()
    print("=" * 78)
    print("  export_dataset.py --selfcheck：合成图上证明许可过滤/审计可红（E1–E5）")
    print("=" * 78)
    rep.add(len(pub_e) == 1 and pub_i["license_excluded_edges"] == 2,
            "E1 public_demo 只留血缘闭包可再分发的 1 条边（另有 2 条被剔除且计数如实）",
            "保留 %s；剔除 %d 边 / %d 节点"
            % ([e["id"] for e in pub_e], pub_i["license_excluded_edges"],
               pub_i["license_excluded_nodes"]))
    rep.add(not ms.audit_redistribution(pub_n, pub_e),
            "E2 后置审计在 public_demo 产物上 0 违规（与 E1 相互独立：一个筛、一个查）",
            "0 违规")
    viol_int = ms.audit_redistribution(int_n, int_e)
    rep.add(len(int_e) == 3 and len(viol_int) > 0,
            "E3 internal_research 不过滤 ⇒ 审计**真的**报出违规（证明 E2 的 0 不是恒真）",
            "保留 3 边；审计报 %d 处违规" % len(viol_int))
    kept_ids = {n["id"] for n in pub_n}
    rep.add("N4" not in kept_ids and "N3" not in kept_ids,
            "E4 缺 props.source ⇒ 视为不可再分发（fail-closed，不猜测）",
            "N4（缺来源）与 N3（internal_only）均未进入对外包")
    all_pub = [{"id": "P1", "labels": ["Formula"], "props": {"source": "curated_seed"}},
               {"id": "P2", "labels": ["Symbol"], "props": {"source": "curated_seed"}}]
    all_pub_e = [{"id": "PE1", "source": "P1", "target": "P2", "type": "has_symbol",
                  "kind": "has_symbol", "props": {"source": "curated_seed"}}]
    n2, e2, i2 = ms.license_partition(all_pub, all_pub_e, "public_demo")
    rep.add(len(e2) == 1 and i2["license_excluded_edges"] == 0,
            "E5 负对照：全可再分发的图 ⇒ 一条都不剔（证明「剔除」不是恒真）",
            "保留 1/1 边，剔除 0")
    print("-" * 78)
    print("  ⇒ %s（PASS %d / FAIL %d）"
          % ("全过" if not rep.n_fail else "未通过", len(rep.rows) - rep.n_fail, rep.n_fail))
    return 0 if not rep.n_fail else 1


# ---------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="STTP 数据集构建器（Phase 70 · F5）")
    ap.add_argument("--src", default=DEFAULT_SRC, help="规范图输入（默认 06_PoC/etl/normalized.json）")
    ap.add_argument("--out", default=DIST, help="输出目录（默认 06_PoC/dataset/dist）")
    ap.add_argument("--profile", default="public_demo", choices=ms.PROFILES,
                    help="许可 profile（默认 public_demo，对外可用）")
    ap.add_argument("--selfcheck", action="store_true", help="许可判据可红性自检（合成图，不建包）")
    ap.add_argument("--e2e", action="store_true", help="构建后追加 wget 下载 + 离线校验")
    ap.add_argument("--skip-determinism", action="store_true",
                    help="跳过二次全量重建（会记 SKIP，**不构成通过**）")
    a = ap.parse_args(argv)

    if a.selfcheck:
        return selfcheck()

    if not os.path.exists(a.src):
        print("[前置缺失] 找不到规范图：%s（RC=2）" % a.src)
        print("        （`06_PoC/etl/normalized.json` 被 .gitignore 排除；先跑 `bash sttp.sh export`）")
        return 2

    print("=" * 78)
    print("  STTP 数据集导出（F5）—— JSONL + manifest + sha256")
    print("=" * 78)
    print("  输入    : %s" % os.path.relpath(a.src, ROOT).replace(os.sep, "/"))
    print("  profile : %s" % a.profile)

    tmp = a.out.rstrip("/\\") + ".tmp"
    st = build_into(a.src, a.profile, tmp)
    m = st["manifest"]["counts"]
    print("  规模    : %d 节点 / %d 边（源 %d / %d；剔除 %d 边 / %d 节点）"
          % (m["nodes"], m["edges"], st["src_nodes"], st["src_edges"],
             m["license_excluded_edges"], m["license_excluded_nodes"]))
    sizes = sum(os.path.getsize(os.path.join(tmp, p)) for p in FILES + (MANIFEST, SUMS))
    print("  体积    : %.1f KB（%d 个文件）" % (sizes / 1024.0, len(FILES) + 2))
    print("-" * 78)

    rep = Rep()
    judge(rep, a.src, tmp, st, st["src_nodes"], st["src_edges"])

    if a.skip_determinism:
        rep.add(None, "S5 确定性：二次全量重建逐字节相同（本轮 --skip-determinism 跳过）",
                "未运行 —— SKIP 不构成通过")
    else:
        determinism(rep, a.src, a.profile, tmp)

    # S6 离线校验器在**临时产物**上跑（fail-closed：不过就不提升）
    rc, out = run_verifier(tmp)
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    rep.add(rc == 0 and not fails, "S6 随包 verify_dataset.py 在产物上 0 FAIL（不确定不提升）",
            "RC=0；%d 条判据全过" % len([ln for ln in out.splitlines() if ln.startswith("[PASS]")])
            if rc == 0 else "★ RC=%d；%s" % (rc, fails[:3]))

    if rep.n_fail:
        print("-" * 78)
        print("  ★ 有判据红 ⇒ **不提升**产物（%s 保留供排查）" % os.path.basename(tmp))
        return 1

    promote(tmp, a.out)

    print("-" * 78)
    print("  产物    : %s" % os.path.relpath(a.out, ROOT).replace(os.sep, "/"))
    print("  第三方  : python3 verify_dataset.py ｜ sha256sum -c SHA256SUMS")
    print("=" * 78)
    if rep.n_fail or rep.n_skip:
        return 1

    if a.e2e:
        e2e = os.path.join(HERE, "wget_offline_e2e.py")
        r = subprocess.run([sys.executable, e2e, "--dir", a.out])
        return r.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
