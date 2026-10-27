# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""STTP 数据集版本化（Phase 77 · F9）—— 版本号由内容**派生**，不再是手写常量。

背景
----
F5（Phase 70/71）落地的是**格式版本**：`DATASET_VERSION = "dataset_v1.1.0"`，
一个**手写的常量**。换一张图重新构建，它一个字都不变。于是

    「手上这个包，是从哪张图导出来的？」

这个问题在包内**无法回答**：`manifest.source.sha256` 记了上游哈希，但它与版本号
之间没有任何**被程序咬住**的关系。两份内容不同、版本相同的包，在引用、缓存、
复现三处都会静默互串（与 D42「快照 id 只登记不校验」同族）。

规划原文 F9：「`dataset_v1.0`，与图快照 ID 绑定」。本模块把它落地成**派生**
而不是**约定**：`dataset_id` 的每一段都由本次构建的输入算出来，改一处就变一处；
手写死的版本号不会被静默放过（S8 / S11 咬住）。

三个概念，先分开再各自给判据（与 D109 同形）
------------------------------------------
| 名字 | 是什么 | 谁决定 | 变了意味着什么 |
|---|---|---|---|
| `format_version` | 记录**格式**契约（`dataset_v1.1.0`） | **人** | 解析器可能要改 |
| `schema_id` | 记录的**字段集**指纹（只看键，不看值） | 派生 | **破坏性**变更 |
| `content_id` | 包**内容**指纹（上游哈希 + 计数 + 每文件哈希） | 派生 | 只是数据刷新 |
| `snapshot_id` | 图快照身份 `sha256:<12>`（与 `Backend.snapshot_id` 同口径） | 派生 | 换图了 |
| `dataset_id` | `{format_version}+g{snapshot12}.c{content8}` | 派生 | 上面任一变了 |

★ 关键设计决定：**破坏性由人声明，机器只证明「已声明」。**
  兼容性不是算出来的 —— 机器不知道「新加一个可选字段算不算破坏」，人知道。
  所以人负责升 MAJOR / MINOR / PATCH，机器只咬住
  「破坏性变更必须已在版本号上体现」（`breaking ⇒ declared`，S11）。
  反过来做（机器自动升版本）会把「数据刷新」与「格式破坏」压成同一件事，
  那正是版本号失去意义的常见方式（#104「口径必须先定」同族）。
  ★ 这条也是**故意不自动化**的那类自动化：自动化的部分（指纹）必须确定性，
    不自动化的部分（语义严重性）必须留下人的签字位。

★ 第二个设计决定：**指纹不含时间、不含本机路径、不含主机名。**
  含了就失去「同一份输入 ⇒ 同一个 id」，也就回答不了「这两份包是不是同一份」。

破坏性 ↔ 版本段的对应（写死在这里，因为它是**契约**，不是实现细节）
--------------------------------------------------------------
| `changed` 里含 | required_bump | 含义 |
|---|---|---|
| `record_fields` | **major** | 字段集变了 ⇒ 旧解析器会炸 |
| `license_profile` / `license_policy` | **minor** | 包里有什么变了 ⇒ 语义/授权变了 |
| `source_graph` / `files` | **patch** | 仅仅数据刷新 |
| （空） | none | 与上一版逐字节相同 |

判据位置
--------
* **S8–S12**：`06_PoC/dataset/export_dataset.py`（真图构建时跑）
* **E6–E8**：本文件 `--selfcheck`（合成图，秒级，**不依赖权威图**）
* **R49–R51**：`06_PoC/tests/red_probe.py`（注入缺陷 ⇒ 上述断言必须真会红）

★ E6–E8 由**本文件**提供、`export_dataset.py --selfcheck` 直接调用 ——
  不另写一份实现（D102「两份实现口径漂移」是本项目踩过两次的坑）。

用法
----
    python 06_PoC/dataset/versioning.py --selfcheck
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))          # 06_PoC/dataset
POC = os.path.dirname(HERE)                                # 06_PoC
ROOT = os.path.dirname(POC)                                # STTP

# ---- 契约常量（改名即破坏性变更，须升 MAJOR）----
FINGERPRINT_SCHEMA = "sttp-dataset-fingerprint/v1"
REGISTRY_SCHEMA = "sttp-dataset-version-registry/v1"

SNAPSHOT_PREFIX = "sha256:"
SNAPSHOT_HEX = 12          # ★ 与 03_知识层/queries.Backend.snapshot_id 同口径（铁律 #53）
CONTENT_HEX = 8

REGISTRY_NAME = "versions.jsonl"

_HEX_RE = re.compile(r"^[0-9a-f]+$")
_V_RE = re.compile(r"^dataset_v(\d+)\.(\d+)\.(\d+)$")

# bump 严重度：用于「已声明」的判定（允许超额声明，不允许少声明 / 回退）
_BUMP_SEVERITY = {"none": 0, "patch": 1, "minor": 2, "major": 3}
_ADVISORY = ("license_profile", "license_policy")
_CONTENTAL = ("source_graph", "files")

NO_BASELINE = "无上一版记录 ⇒ 不做兼容判定（SKIP 不构成通过）"


class VersioningError(ValueError):
    """版本化输入不合法。**一律抛错**，不猜一个值继续走（fail-closed）。"""


# ---------------------------------------------------------------- 指纹原语
def _canon(obj) -> str:
    """规范序列化：键排序、非 ASCII 不转义、无多余空白。

    ★ 与 `export_dataset.write_json` 同一套参数 —— 同一份数据在任何机器上
      必须得到同一个字节串（#105 的教训）。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(text: str, n: int) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


# ---------------------------------------------------------------- 身份
def snapshot_id(source_sha256) -> str:
    """图快照身份 `sha256:<hex[:12]>`。

    ★ 与 `03_知识层/queries.py::Backend.snapshot_id` **同一个口径**（前缀 + 12 位）：
      前端 `/api/overview` 返回的 `snapshot_id` 与本函数的结果可以直接比字符串，
      于是「数据集是从当前页面读的那张图导出的吗」变成一个**可断言的句子**
      （X19；不再是"相信构建时没换图"）。
    """
    if not isinstance(source_sha256, str):
        raise VersioningError("图快照 sha256 必须是字符串，收到 %r" % (source_sha256,))
    s = source_sha256.strip().lower()
    if len(s) < SNAPSHOT_HEX or not _HEX_RE.match(s):
        raise VersioningError("图快照 sha256 必须是十六进制串，收到 %r" % (source_sha256,))
    return SNAPSHOT_PREFIX + s[:SNAPSHOT_HEX]


def schema_id(node_keys, edge_keys) -> str:
    """记录的**字段集**指纹 —— 只看键，不看值。

    ★ 为什么用字段集而不是 `record_schema` 字符串：`record_schema` 是人写的常量，
      字段真的增删了它可以**一个字都不改**（那正是 F9 要堵的缝）。字段集是从
      实际写出的记录里**现算**的，改了就一定变（S10 / E8 咬住）。
    """
    return _digest(_canon({
        "schema": FINGERPRINT_SCHEMA,
        "kind": "record_fields",
        "nodes": sorted({str(k) for k in (node_keys or ())}),
        "edges": sorted({str(k) for k in (edge_keys or ())}),
    }), SNAPSHOT_HEX)


def files_fingerprint(files) -> str:
    """产物文件指纹（path + sha256 + bytes）。"""
    rows = [{"path": str(f.get("path")), "sha256": str(f.get("sha256")),
             "bytes": int(f.get("bytes") or 0)} for f in (files or ())]
    rows.sort(key=lambda r: r["path"])
    return _digest(_canon({"schema": FINGERPRINT_SCHEMA, "kind": "files", "files": rows}),
                   CONTENT_HEX)


def content_id(*, record_schema, manifest_schema, license_profile, license_policy,
               source_sha256, source_bytes, counts, files) -> str:
    """包**内容**指纹：上游哈希 + 格式 schema + policy + 计数 + 每文件哈希。

    ★ `files` 含随包校验器自身的哈希 —— 校验器也是包的一部分，改了它就是改了包；
      但这会让「仅改校验器」也产生新版本。这是**故意**的：包是一个整体，
      它的验证能力变了，包就变了（与 D106「来源标签按骨架登记」同族）。
    """
    payload = {
        "schema": FINGERPRINT_SCHEMA,
        "kind": "content",
        "record_schema": str(record_schema),
        "manifest_schema": str(manifest_schema),
        "license_profile": license_profile,
        "license_policy": license_policy,
        "source": {"sha256": str(source_sha256), "bytes": int(source_bytes)},
        "counts": counts,
        "files": [{"path": str(f.get("path")), "sha256": str(f.get("sha256")),
                   "bytes": int(f.get("bytes") or 0)}
                  for f in sorted(files or (), key=lambda x: str(x.get("path")))],
    }
    return _digest(_canon(payload), CONTENT_HEX)


def _split_snapshot(snap) -> str:
    s = str(snap or "")
    if not s.startswith(SNAPSHOT_PREFIX):
        raise VersioningError("snapshot_id 必须以 %r 开头，收到 %r" % (SNAPSHOT_PREFIX, s))
    body = s[len(SNAPSHOT_PREFIX):]
    if len(body) != SNAPSHOT_HEX or not _HEX_RE.match(body):
        raise VersioningError("snapshot_id 需要 %d 位十六进制，收到 %r" % (SNAPSHOT_HEX, s))
    return body


def dataset_id(format_version, snap, content) -> str:
    """`{format_version}+g<snapshot12>.c<content8>`。

    ★ 用 semver 的 build metadata（`+` 之后）承载内容身份 ——
      这样**格式兼容的判定仍然只看 `MAJOR.MINOR.PATCH`**，
      而「是不是同一个包」看整串。两个问题不混在一处比较。
    """
    body = _split_snapshot(snap)
    c = str(content or "")
    if len(c) != CONTENT_HEX or not _HEX_RE.match(c):
        raise VersioningError("content_id 必须是 %d 位十六进制，收到 %r" % (CONTENT_HEX, c))
    parse_format_version(format_version)          # fail-closed：版本号形态不对 ⇒ 抛错
    return "%s+g%s.c%s" % (format_version, body, c)


def parse_format_version(v) -> tuple:
    if not isinstance(v, str):
        raise VersioningError("format_version 必须是字符串，收到 %r" % (v,))
    m = _V_RE.match(v.strip())
    if not m:
        raise VersioningError("format_version 形如 dataset_v<主>.<次>.<补丁>，收到 %r" % (v,))
    return tuple(int(x) for x in m.groups())


def parse_dataset_id(did) -> dict:
    """回读 `dataset_id` ⇒ `{format_version, snapshot_id, content_id}`。

    ★ 任一处不合预期都**抛错**，绝不返回一个"大概是这个"的字典 ——
      解析失败被降级成 `None` 正是 D64 那类「静默放过」最常见的入口。
    """
    if not isinstance(did, str) or "+" not in did:
        raise VersioningError("dataset_id 形如 dataset_v1.1.0+g<12hex>.c<8hex>，收到 %r" % (did,))
    v, _, meta = did.partition("+")
    parse_format_version(v)
    parts = meta.split(".")
    if len(parts) != 2:
        raise VersioningError("dataset_id 元数据段应为 g<12hex>.c<8hex>，收到 %r" % (meta,))
    g, c = parts
    if not g.startswith("g") or not c.startswith("c"):
        raise VersioningError("dataset_id 元数据段前缀应为 g / c，收到 %r" % (meta,))
    return {"format_version": v,
            "snapshot_id": SNAPSHOT_PREFIX + _split_snapshot(SNAPSHOT_PREFIX + g[1:]),
            "content_id": c[1:]}


def suggest_version(v, required) -> str:
    """给出「满足 `required` 的最低版本号」 —— 让判据红的时候**可直接照抄**。

    ★ 判据只说「不行」是不够的：使用者还得自己翻代码去猜该改成多少。
      这里是**人签字**的位置，机器只负责把候选值摆在面前（#104 同族）。
    """
    a, b, c = parse_format_version(v)
    if required == "major":
        return "dataset_v%d.0.0" % (a + 1)
    if required == "minor":
        return "dataset_v%d.%d.0" % (a, b + 1)
    if required == "patch":
        return "dataset_v%d.%d.%d" % (a, b, c + 1)
    return v


# ---------------------------------------------------------------- 兼容判定
def actual_bump(prev_v, cur_v) -> str:
    """相邻两版 `format_version` 的实际升幅：`major/minor/patch/none/downgrade`。

    ★ `downgrade` 是**真实且需要被看见**的一种：版本号回退会让"新版"缓存失效，
      把它当成 `none` 放过，等于允许一次静默的版本穿越。
    """
    a, b = parse_format_version(prev_v), parse_format_version(cur_v)
    if a == b:
        return "none"
    if b[0] != a[0]:
        return "major" if b[0] > a[0] else "downgrade"
    if b[1] != a[1]:
        return "minor" if b[1] > a[1] else "downgrade"
    return "patch" if b[2] > a[2] else "downgrade"


def classify_change(prev, cur) -> dict:
    """比较相邻两版，给出 `breaking` / `advisory` / `required_bump` / `declared`。

    ★ `prev` 为 `None` ⇒ `breaking=None`（**无基线**）。调用方必须记 SKIP ——
      「没比较过」不等于「兼容」，这是本项目反复出现的一类谎言（铁律 #20）。
    """
    if not prev:
        return {"breaking": None, "advisory": None, "changed": [],
                "required_bump": None, "actual_bump": None, "declared": None,
                "reasons": [NO_BASELINE]}

    changed = []
    if prev.get("schema_id") != cur.get("schema_id"):
        changed.append("record_fields")
    for k in _ADVISORY:
        if prev.get(k) != cur.get(k):
            changed.append(k)
    if prev.get("source_sha256") != cur.get("source_sha256"):
        changed.append("source_graph")
    if prev.get("files_id") != cur.get("files_id"):
        changed.append("files")

    breaking = "record_fields" in changed
    advisory = any(k in changed for k in _ADVISORY)
    required = ("major" if breaking else
                "minor" if advisory else
                "patch" if any(k in changed for k in _CONTENTAL) else "none")
    act = actual_bump(prev.get("format_version"), cur.get("format_version"))
    declared = (act != "downgrade"
                and _BUMP_SEVERITY.get(act, -1) >= _BUMP_SEVERITY[required])

    reasons = []
    if breaking:
        reasons.append("记录字段集变了（schema_id %s → %s）"
                       % (str(prev.get("schema_id"))[:8], str(cur.get("schema_id"))[:8]))
    if advisory:
        reasons.append("许可口径变了（%s）" % "、".join(k for k in _ADVISORY if k in changed))
    if "source_graph" in changed:
        reasons.append("上游图快照换了（%s… → %s…）"
                       % (str(prev.get("source_sha256"))[:12],
                          str(cur.get("source_sha256"))[:12]))
    if act == "downgrade":
        reasons.append("★ 版本号回退（%s → %s）—— 允许升，不允许退"
                       % (prev.get("format_version"), cur.get("format_version")))
    elif not declared:
        reasons.append("★ 变更未声明：应升 %s，实为 %s（%s → %s）⇒ 改 DATASET_VERSION 为 %s"
                       % (required, act, prev.get("format_version"),
                          cur.get("format_version"),
                          suggest_version(prev.get("format_version"), required)))
    if not reasons:
        reasons.append("与上一版逐字节相同（无变更）")
    return {"breaking": breaking, "advisory": advisory, "changed": changed,
            "required_bump": required, "actual_bump": act, "declared": declared,
            "reasons": reasons}


# ---------------------------------------------------------------- 版本台账
def registry_path(root: str = ROOT) -> str:
    """`06_PoC/dataset/versions.jsonl` —— 追加式台账（**入库**，因为版本是对外承诺）。

    ★ 为什么**只在内容真的变了时才追加**：同图重复构建不该 append（否则每次跑
      `bash sttp.sh dataset` 都把工作树弄脏 —— E29 家族）。于是「台账条数不变」
      本身就是一条判据（S12 幂等）。
    """
    return os.path.join(root, "06_PoC", "dataset", REGISTRY_NAME)


def load_registry(path: str) -> list:
    """读台账。**坏行直接抛错**，不静默跳过（跳过一行 = 少一条历史，看不出来）。"""
    if not os.path.exists(path):
        return []
    rows = []
    with io.open(path, "r", encoding="utf-8") as f:
        for i, ln in enumerate(f, 1):
            s = ln.strip()
            if not s:
                continue
            try:
                rows.append(json.loads(s))
            except ValueError as e:
                raise VersioningError("版本台账第 %d 行不是合法 JSON：%s" % (i, e))
    return rows


def append_registry(path: str, entry: dict) -> None:
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(_canon(entry) + "\n")


def next_seq(entries) -> int:
    if not entries:
        return 1
    return max(int(e.get("seq") or 0) for e in entries) + 1


# ---------------------------------------------------------------- 自检（合成图）
class _Rep:
    def __init__(self):
        self.rows = []

    def add(self, ok, name, note=""):
        self.rows.append((ok, name, note))
        tag = "[PASS]" if ok is True else ("[SKIP]" if ok is None else "[FAIL]")
        print("%s %s  %s" % (tag, name, note))

    @property
    def n_fail(self):
        return sum(1 for ok, _, _ in self.rows if ok is False)


def _entry(fmt, snap, sid, cid, fid, prof, pol, sha, nbyte, counts):
    return {"format_version": fmt, "snapshot_id": snap, "schema_id": sid,
            "content_id": cid, "files_id": fid, "license_profile": prof,
            "license_policy": pol, "source_sha256": sha, "source_bytes": nbyte,
            "counts": counts}


def selfcheck() -> int:
    """E6–E8：**合成**输入上证明版本化的三条性质都不是恒真。秒级、不依赖权威图。

    | ID | 断言 | 破坏它会怎样（为什么要这条） |
    |---|---|---|
    | E6 | 同输入 ⇒ 同 `dataset_id` | 版本号每次构建都变 ⇒ 无法引用、缓存全失效 |
    | E7 | 换图 ⇒ `dataset_id` 必变 | **就是 F9 想堵的那个缝**：两张图导出同一个版本号 |
    | E8 | 记录字段集变 ⇒ 判 `breaking`；不升 MAJOR ⇒ 判「未声明」 | 旧解析器静默炸在使用者机器上 |
    """
    rep = _Rep()
    print("=" * 78)
    print("  versioning.py --selfcheck：合成输入上证明版本化可红（E6–E8）")
    print("=" * 78)

    files = [{"path": "nodes.jsonl", "sha256": "aa" * 32, "bytes": 1234},
             {"path": "edges.jsonl", "sha256": "bb" * 32, "bytes": 5678}]
    counts = {"nodes": 7, "edges": 9}
    kw = dict(record_schema="sttp-dataset/v1", manifest_schema="sttp-dataset-manifest/v1",
              license_profile="public_demo", license_policy="redistributable_only",
              source_sha256="cc" * 32, source_bytes=98765, counts=counts, files=files)

    # ---- E6 确定性：同输入 ⇒ 同 id ----
    c1 = content_id(**kw)
    c2 = content_id(files=list(reversed(files)), **{k: v for k, v in kw.items() if k != "files"})
    snap1 = snapshot_id(kw["source_sha256"])
    sid1 = schema_id(["id", "type", "license"], ["id", "type"])
    did1 = dataset_id("dataset_v1.1.0", snap1, c1)
    did2 = dataset_id("dataset_v1.1.0", snap1, c2)
    rep.add(did1 == did2,
            "E6 同输入 ⇒ 同 dataset_id（文件顺序不同也算同输入；含时间戳即红）",
            "%s" % did1)

    # ---- E7 绑定：换图 ⇒ id 必变 ----
    kw_b = dict(kw)
    kw_b["source_sha256"] = "dd" * 32
    c_b = content_id(**kw_b)
    snap_b = snapshot_id(kw_b["source_sha256"])
    did_b = dataset_id("dataset_v1.1.0", snap_b, c_b)
    p_b = parse_dataset_id(did_b)
    # ★ E7 盯的是 **content_id**（c 段）：只断言 `dataset_id` 整体变化是不够的 ——
    #   g 段本身就由上游哈希派生，整体断言会被 g 段"带着红"，看不出 c 段有没有跟上。
    rep.add(c_b != c1 and did_b != did1 and p_b["snapshot_id"] == snap_b and snap_b != snap1,
            "E7 换上游图 ⇒ content_id 与 dataset_id 必变（版本号手写 ⇒ 这个缝不会自己消失）",
            "content %s → %s ｜ id %s → %s" % (c1, c_b, did1, did_b))

    # ---- E8 破坏性判定：字段集变 ⇒ breaking；不升 MAJOR ⇒ 未声明 ----
    sid2 = schema_id(["id", "type", "license", "attrs"], ["id", "type"])
    a = _entry("dataset_v1.1.0", snap1, sid1, c1, files_fingerprint(files),
               "public_demo", "redistributable_only", kw["source_sha256"], 98765, counts)
    b = _entry("dataset_v1.1.0", snap1, sid2, c_b, files_fingerprint(files),
               "public_demo", "redistributable_only", kw["source_sha256"], 98765, counts)
    r_undeclared = classify_change(a, b)
    ok8a = (r_undeclared["breaking"] is True and r_undeclared["required_bump"] == "major"
            and r_undeclared["declared"] is False)
    rep.add(ok8a, "E8a 记录字段集变了却没升 MAJOR ⇒ 判「破坏性未声明」（S11 会红）",
            "；".join(r_undeclared["reasons"]))

    b_ok = _entry("dataset_v2.0.0", snap1, sid2, c_b, files_fingerprint(files),
                  "public_demo", "redistributable_only", kw["source_sha256"], 98765, counts)
    r_declared = classify_change(a, b_ok)
    # ★ E8b 只盯 `declared`（不重复断言 breaking —— 那是 E8a 的活）⇒
    #   注入「breaking 恒 False」时只有 E8a 红，探针的隔离性才成立。
    ok8b = (r_declared["declared"] is True)
    rep.add(ok8b, "E8b 负对照：升了 MAJOR ⇒ declared=True（证明 E8a 的红不是恒真）",
            "；".join(r_declared["reasons"]))

    # ---- E8c 无基线 ⇒ breaking 为 None（SKIP，不假通过）----
    r_none = classify_change(None, b_ok)
    ok8c = (r_none["breaking"] is None and r_none["declared"] is None
            and NO_BASELINE in r_none["reasons"])
    rep.add(ok8c, "E8c 负对照：无上一版 ⇒ breaking=None（SKIP 不构成通过）",
            "；".join(r_none["reasons"]))

    print("-" * 78)
    print("  ⇒ %s（PASS %d / FAIL %d）"
          % ("全过" if not rep.n_fail else "未通过",
             sum(1 for ok, _, _ in rep.rows if ok is True), rep.n_fail))
    return 0 if not rep.n_fail else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="STTP 数据集版本化（Phase 77 · F9）")
    ap.add_argument("--selfcheck", action="store_true", help="合成输入自检（E6–E8）")
    a = ap.parse_args(argv)
    if a.selfcheck:
        return selfcheck()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
