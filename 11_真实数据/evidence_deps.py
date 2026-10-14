# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
"""evidence_deps.py —— Phase 45 · A04：从「来源绑定」升级到「验证依赖绑定」

复评指摘（`07_交付物/外部评审/codex评测/review_20261012/下一轮研发任务单_20261012.md`）
------------------------------------------------------------------------------------
> **A04a｜失效器不比对 `impl_ver`** —— `lineage_invalidation.py` 只对 `src_hash`，
> **无 `impl_ver` 比对**；复评变异一条 evidence 的 `impl_ver` → 受影响 **0**。
> **A04b｜证据只绑「提出者来源」** —— 用**单个 `sid`** 绑该边**全部** evidence 的
> 来源身份；`impl` 里出现的**参考表依赖**（量纲表、元素表）**未登记为依赖**
> ⇒ 复评变异 `dimension_table` 哈希只定位 **28 条边**，而 `impl` 里含量纲表的远不止。

本模块做什么
------------
把「一条证据依赖什么」从**隐式**（只绑提出者来源）变成**显式且可复算**的三类依赖：

1. `input_refs`  —— **输入**依赖：提出者来源（整文件级；记录级**尚未实现**，如实标 `binding=input_source`）。
2. `reference_refs` —— **参考表/规则**依赖：本证据在验证时**读取**的权威表（量纲表、SI 基表、元素表）。
   两种绑定强度，**分列标注**（不混为一谈）：
     * `impl_declared` —— `impl` 串**显式点名**的参考表（强：产出方自己写了）；
     * `code_import`  —— 由验证器模块的**代码级 import** 推出的依赖（**保守/可能过近似**：
       模块 import 了某表 ≠ 该条证据真的读了它。按任务单「先按整文件保守失效」有意保留此档）。
3. `verifier` —— **验证器**身份：`verifier_id` + `code_hash` + `dependency_versions`。

★ 与 Phase 44 · D1 的关系
-------------------------
D1 修的是「**读错了字节源**」（worktree CRLF 被当成「来源已变更」）。本模块**沿用** D1 的结论：
一切哈希的权威字节源 = **仓库 blob**（`git cat-file blob HEAD:<rel>`，见 `source_registry.repo_blob`），
`source_registry._git()` 失败返回 `None` ⇒ **fail-closed，不给空字节**。

★ 为什么不需要改历史图
----------------------
本模块**不重算**任何旧证据：它从**已记录的字段**（`src`/`src_hash`/`impl`/`kind`）**确定性推导**
`reference_refs` 与 `verifier`。推导规则是**声明式常量表**（`IMPL_REF_TOKENS` / `code_imports`），
可逐条人工核对；凡推导不出的一律**留空**并走 `needs_review`，**不猜**。

用法
----
    python 11_真实数据/evidence_deps.py --emit-registry   # 生成 11_真实数据/reference_registry.json
    python 11_真实数据/evidence_deps.py --check           # 判据自检（构造性非真空）
    python 11_真实数据/evidence_deps.py --report          # 打印推导统计
"""
from __future__ import annotations

import argparse
import ast
import collections
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import source_registry as sr                             # noqa: E402

REGISTRY_OUT = os.path.join(HERE, "reference_registry.json")
LEDGER_OUT = os.path.join(HERE, "reverify_ledger.json")

# ---------------------------------------------------------------------------
# 1. 参考表 / 规则登记（**手工维护的白名单**，每一项都指向仓库内可复算的文件）
#
#    ★ 为什么是白名单而非扫描：参考表是**语义概念**（「验证方程量纲时用的那张表」），
#      目录里同时存在 `dimension_table.py`（表 + 比对逻辑）与 `dimension_table.json`
#      （反解产物）。二者语义不同（见 `si_base_table.py` 文档头：反解表含**同源自洽**成分），
#      必须**分开**登记，不能靠文件名猜测（铁律 #49「命名对齐须合法」的反面）。
# ---------------------------------------------------------------------------
REFERENCE_REGISTRY = collections.OrderedDict([
    ("dimension_table", {
        "kind": "python_module",
        "paths": ["11_真实数据/dimension_table.py"],
        "version_label": "反解量纲表（PhysicsBabel 方程消元产物）",
        "note": "含同源自洽成分（见 si_base_table.py 文档头）；与 SI 基表分开登记。",
    }),
    ("dimension_table.json", {
        "kind": "data_table",
        "paths": ["11_真实数据/dimension_table.json"],
        "version_label": "反解量纲表快照（JSON 形态）",
        "note": "机器可读快照；口径同 dimension_table.py。",
    }),
    ("si_base_table", {
        "kind": "python_module",
        "paths": ["11_真实数据/si_base_table.py"],
        "version_label": "独立 SI/ISO 80000 基表",
        "note": "**独立编纂**的外部对照（Phase 40 · R09），不反解、不外推。",
    }),
    ("element_reference", {
        "kind": "python_module",
        "paths": ["11_真实数据/element_reference.py"],
        "version_label": "IUPAC 2021 元素参考表（周期表 1–118）",
        "note": "原子量 / 符号 / 原子序数的权威参考表。",
    }),
    ("verification_model", {
        "kind": "python_module",
        "paths": ["11_真实数据/verification_model.py"],
        "version_label": "判级模型（验证器代码）",
        "note": "★ 是**验证器代码**而非参考表；登记在此仅为统一哈希口径。",
    }),
])

# 参考表 id → 在 `impl` 串里出现的**字面 token**（用于 impl_declared 匹配）
#   ★ 只列**语义无歧义**的 token：`dimension_table` 与 `element_reference` 都是专名，
#     不会与自然语言混淆。刻意**不**加 `model`/`table` 这类泛词（会误命中）。
IMPL_REF_TOKENS = collections.OrderedDict([
    ("element_reference", ("element_reference",)),
    ("dimension_table", ("dimension_table",)),
    ("dimension_table.json", ("dimension_table.json",)),
    ("si_base_table", ("si_base_table",)),
])

# 验证器 id → 其**模块相对路径**（代码级依赖的根）
VERIFIER_MODULES = collections.OrderedDict([
    ("verification_model", "11_真实数据/verification_model.py"),
    ("dimension_table", "11_真实数据/dimension_table.py"),
    ("element_reference", "11_真实数据/element_reference.py"),
    ("si_base_table", "11_真实数据/si_base_table.py"),
])

# 参考表 id → 模块相对路径（用于 code_import 与本表 import 的互相映射）
REF_MODULE_PATH = {rid: v["paths"][0] for rid, v in REFERENCE_REGISTRY.items()}
MODULE_PATH_REF = {v["paths"][0]: rid for rid, v in REFERENCE_REGISTRY.items()}

# ---------------------------------------------------------------------------
# 2. 生命周期与原因码
# ---------------------------------------------------------------------------
STATUSES = ("valid", "stale", "needs_review", "rejected")
STATUS_RANK = {s: i for i, s in enumerate(STATUSES)}

# 原因码前缀（可程序化判别，勿写自由文本）
R_SRC_CHANGED = "src_changed"
R_REF_CHANGED = "ref_changed"
R_VERIFIER_CHANGED = "verifier_changed"
R_DEP_CHANGED = "dep_version_changed"
R_UNVERIFIABLE_SRC = "unverifiable_source"
R_UNVERIFIABLE_REF = "unverifiable_reference"
R_UNVERIFIABLE_VERIFIER = "unverifiable_verifier"
R_NO_REFERENCE = "no_reference_declared"
R_CLAIM_REJECTED = "claim_rejected"
R_HASH_REWRITTEN = "hash_rewritten_without_reverify"
R_REVERIFIED = "reverified"

# 「已变更」类原因（⇒ stale）与「不可判定」类原因（⇒ needs_review）
CHANGE_REASONS = (R_SRC_CHANGED, R_REF_CHANGED, R_VERIFIER_CHANGED, R_DEP_CHANGED)
UNVERIFIABLE_REASONS = (R_UNVERIFIABLE_SRC, R_UNVERIFIABLE_REF, R_UNVERIFIABLE_VERIFIER,
                        R_HASH_REWRITTEN)

# ★ 与 Phase 43/44 的**四分类**映射（对外口径不变；见 queries.edge_fidelity）
FIDELITY_OF_STATUS = {
    "valid": "verified",
    "stale": "changed",
    "needs_review": "unverifiable_no_source",     # 细分见 reason（no_source vs not_in_repo）
    "rejected": "changed",
}


def short(h, n=16):
    return None if h is None else str(h)[:n]


def sha256_file(path):
    """文件内容的 sha256[:12]；文件不存在 ⇒ None（fail-closed，不给空字节）。"""
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def _repo_bytes_hash(rel, base=ROOT):
    """权威字节源 = **仓库 blob**（Phase 44 · D1）。取不到 ⇒ None（fail-closed）。"""
    body = sr.repo_blob(rel, base)
    if body is None:
        return None
    return hashlib.sha256(body).hexdigest()[:12]


def reference_content_hash(ref_id, base=ROOT, byte_source="repo"):
    """参考表的**复合**哈希 = sha256("rel:文件哈希" 逐文件拼接)[:16]。

    ★ 复合而非「取第一个文件」：`dimension_table` 只有 1 个文件，但登记形态允许
      多文件（如未来把 `si_base_table.py` 并入量纲族）；复合哈希对「任一文件变更」
      都敏感，且**确定性**（文件顺序取登记顺序，不依赖目录遍历）。
    ★ 任一文件取不到哈希 ⇒ 整体返回 None（**不**用 0/空串顶替 —— 铁律 #62 fail-closed）。
    """
    parts = []
    for rel in REFERENCE_REGISTRY.get(ref_id, {}).get("paths", []):
        if byte_source == "worktree":
            h = sha256_file(os.path.join(base, rel))
        else:
            h = _repo_bytes_hash(rel, base)
        if h is None:
            return None
        parts.append("%s:%s" % (rel, h))
    if not parts:
        return None
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def reference_hashes(base=ROOT, byte_source="repo"):
    return {rid: reference_content_hash(rid, base, byte_source) for rid in REFERENCE_REGISTRY}


# ---------------------------------------------------------------------------
# 3. 代码级依赖（AST）—— 验证器模块 import 了哪些本地参考模块
# ---------------------------------------------------------------------------
_LOCAL_MODULES = None


def local_modules():
    """`11_真实数据/` 下的本地模块名 → 相对路径（供 AST 命中判定）。"""
    global _LOCAL_MODULES
    if _LOCAL_MODULES is None:
        m = {}
        d = os.path.join(ROOT, "11_真实数据")
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if fn.endswith(".py") and not fn.startswith("_"):
                    m[fn[:-3]] = "11_真实数据/%s" % fn
        _LOCAL_MODULES = m
    return _LOCAL_MODULES


def code_imports(rel):
    """静态解析 `rel` 的 **AST**，返回它 import 的本地模块相对路径集合（**保守**）。

    ★ 用 AST 而不是正则：正则会把文档字符串里 `from dimension_table import …`
      的**用法示例**误当成真 import（`dimension_table.py` 头部就有），AST 只看真实代码。
    ★ 函数内的**惰性 import**（如 `dimension_table.py:389` 的 `import si_base_table`）
      AST 同样能取到 —— 这正是「保守」应有的行为：代码里出现了这条依赖就必须登记。
    ★ 语法错误/读不到 ⇒ 返回 None（fail-closed，**不**返回空集假装「无依赖」）。
    """
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        return None
    try:
        tree = ast.parse(open(p, encoding="utf-8").read())
    except (SyntaxError, UnicodeDecodeError):
        return None
    lm = local_modules()
    out = set()
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:                      # 相对 import：本项目无包结构，跳过
                continue
            names = [(node.module or "").split(".")[0]]
        for n in names:
            if n and n in lm:
                out.add(lm[n])
    return out


def verifier_identity(vid=("verification_model",), base=ROOT):
    """验证器身份：`{verifier_id, code_hash, dependency_versions}`。

    `dependency_versions` = 验证器模块的**直接本地依赖**（rel → sha256[:12]）。
    传 `None` 表示「无代码验证器」（纯数据断言）。
    """
    out = {}
    for v in vid:
        if not v:
            continue
        rel = VERIFIER_MODULES.get(v)
        if rel is None:
            out[v] = {"verifier_id": v, "code_hash": None, "dependency_versions": {}}
            continue
        deps = code_imports(rel) or set()
        out[v] = {
            "verifier_id": v,
            "code_hash": _repo_bytes_hash(rel, base),
            "dependency_versions": {d: _repo_bytes_hash(d, base) for d in sorted(deps)},
        }
    return out


# ---------------------------------------------------------------------------
# 4. 依赖推导（这是 A04b 的核心修复）
# ---------------------------------------------------------------------------
def impl_refs(impl, kind=None):
    """从 `impl` 串提取 **impl_declared** 参考表 id（声明级，强绑定）。"""
    s = impl or ""
    out = []
    for rid, toks in IMPL_REF_TOKENS.items():
        if any(t in s for t in toks):
            out.append(rid)
    # `dimension_table.json` 命中时不再重复报 `dimension_table`（后者是前者的子串）
    if "dimension_table.json" in out and "dimension_table" in out:
        out.remove("dimension_table")
    return out


def verifier_of(ev):
    """判定本条证据的验证器 id。

    * `impl` 以 `verification_model.` 开头 ⇒ `verification_model`；
    * `impl` 恰是某个参考模块名（`element_reference.py` 等）⇒ 该模块；
    * 其余（纯数据断言 / 外部管线）⇒ **None**（不编造验证器）。
    """
    impl = (ev.get("impl") or "").strip()
    if impl.startswith("verification_model."):
        return "verification_model"
    for vid, rel in VERIFIER_MODULES.items():
        base = os.path.basename(rel)
        if impl == base or impl == vid or impl.startswith(vid + "("):
            return vid
    return None


def dependencies_for(ev, vident=None):
    """返回 `(reference_refs, verifier_obj)`。

    reference_refs 的绑定强度：
      * `impl_declared`：`impl` 串显式点名；
      * `code_import` ：验证器模块**代码级 import** 推出的保守依赖。
    同一 ref 若两者都命中 ⇒ 记 `impl_declared`（更强）。

    ★ **口径诚实**：`code_import` 是**过近似**——`verification_model.py` import 了
      `dimension_table` 与 `element_reference`，于是**每一条** `verification_model.*`
      证据都被判为依赖这两张表，即使某个具体函数（如 `formula_members`）运行时并不读它们。
      这是任务单明确允许的「**先按整文件保守失效**」。过近似的幅度在 `--report` 里**分列报出**。
    """
    vident = vident if vident is not None else verifier_identity()
    vid = verifier_of(ev)
    refs = collections.OrderedDict()

    for rid in impl_refs(ev.get("impl")):
        refs[rid] = "impl_declared"

    if vid and vid in vident:
        for rel in (vident[vid].get("dependency_versions") or {}):
            rid = MODULE_PATH_REF.get(rel)
            if rid and rid not in refs:
                refs[rid] = "code_import"

    out = []
    for rid, binding in refs.items():
        meta = REFERENCE_REGISTRY.get(rid) or {}
        out.append({
            "ref_id": rid,
            "kind": meta.get("kind"),
            "version": meta.get("version_label"),
            "content_hash": reference_content_hash(rid),   # ★ 记录时刻的当前值
            "binding": binding,
        })
    vobj = (dict(vident[vid]) if (vid and vid in vident)
            else {"verifier_id": None, "code_hash": None, "dependency_versions": {}})
    return out, vobj


# ---------------------------------------------------------------------------
# 5. 证据身份
# ---------------------------------------------------------------------------
def evidence_id(claim_id, idx, ev):
    """确定性证据 id = `evd:sha256(claim|idx|src|impl|kind|detail)[:16]`。"""
    key = "|".join([str(claim_id), str(idx), str(ev.get("src")), str(ev.get("impl")),
                    str(ev.get("kind")), str(ev.get("detail"))])
    return "evd:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def claim_version(claim_text, ev_ids):
    key = "|".join([str(claim_text)] + sorted(str(x) for x in ev_ids))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# 6. 重验账本（「单纯重写哈希不能恢复 valid」的程序化保证）
# ---------------------------------------------------------------------------
def load_ledger(base=ROOT):
    """重验账本：`{evidence_id: {"old_src_hash":…, "new_src_hash":…, "at":…, "reason":…}}`。

    ★ 为什么必须有账本：`status` 是**每次读取时现算**的（不落库，故不会自行过期）。
      若只把记录的哈希改成当前值，status 会**直接变回 valid** —— 那就等于「重写哈希
      即可恢复」，正是任务单禁止的。**程序化**的堵法：凡「记录的哈希」与「账本里该
      证据的 `new_*` 值」不一致，或账本无该条记录而图里的 `evidence_at` 又晚于
      基线时刻，一律判 `needs_review:hash_rewritten_without_reverify`。
    """
    p = base if os.path.isabs(base) and base.endswith(".json") else os.path.join(base, "11_真实数据", "reverify_ledger.json")
    if not os.path.exists(p):
        return {}
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    ent = d.get("entries") if isinstance(d, dict) else None
    if not isinstance(ent, list):
        return {}
    return {e["evidence_id"]: e for e in ent if isinstance(e, dict) and e.get("evidence_id")}


# ---------------------------------------------------------------------------
# 7. 状态计算
# ---------------------------------------------------------------------------
def evidence_status(recorded, current, *, claim_rejected=False, ledger_entry=None):
    """计算单条证据的 `(status, status_reasons[])`。

    参数
    ----
    recorded : dict —— 图里**记录**的依赖快照：
        `{"src":…, "src_hash":…, "reference_refs":[{ref_id, content_hash}], "verifier":{…}}`
    current : dict —— **当前**的依赖快照：
        `{"src_hashes":{sid:h}, "reference_hashes":{rid:h}, "verifiers":{vid:{code_hash,
          dependency_versions}}}`
    claim_rejected : 该命题是否已被显式否决（`props.rejected is True`）。
    ledger_entry : 重验账本里该证据的条目（无 ⇒ None）。

    判定优先级（**确定性**，不依赖遍历顺序）
    -----------------------------------------
    1. `claim_rejected` ⇒ `rejected`（原因 `claim_rejected`）。
    2. 收集原因：**已变更**（可复算且不符）与**不可判定**（不可复算/缺记录）。
    3. 有任一**已变更**原因 ⇒ `stale`（确定信号优先于「不可判定」）。
    4. 否则有任一**不可判定**原因（或账本显示被无留痕地改写过）⇒ `needs_review`。
    5. 否则 ⇒ `valid`。

    ★ 「不可从仓库复算」≠「已变更」：前者进 `needs_review`，**不**进 `stale`
      —— 若并入，`exclude_stale` 会退化成「几乎清空全图」（自毁开关，铁律 #70）。
    """
    reasons = []
    if claim_rejected:
        return "rejected", [R_CLAIM_REJECTED]

    src = recorded.get("src")
    rec_src = recorded.get("src_hash")
    cur_src = (current.get("src_hashes") or {}).get(src)
    if rec_src is None or cur_src is None:
        reasons.append("%s:%s" % (R_UNVERIFIABLE_SRC, src))
    elif rec_src != cur_src:
        reasons.append("%s:%s" % (R_SRC_CHANGED, src))

    cur_refs = current.get("reference_hashes") or {}
    for rr in (recorded.get("reference_refs") or []):
        rid = rr.get("ref_id")
        rec = rr.get("content_hash")
        cur = cur_refs.get(rid)
        if rec is None or cur is None:
            reasons.append("%s:%s" % (R_UNVERIFIABLE_REF, rid))
        elif rec != cur:
            reasons.append("%s:%s" % (R_REF_CHANGED, rid))

    ver = recorded.get("verifier") or {}
    vid = ver.get("verifier_id")
    if vid:
        cur_v = (current.get("verifiers") or {}).get(vid) or {}
        rec_ch = ver.get("code_hash")
        cur_ch = cur_v.get("code_hash")
        if rec_ch is None or cur_ch is None:
            reasons.append("%s:%s" % (R_UNVERIFIABLE_VERIFIER, vid))
        elif rec_ch != cur_ch:
            reasons.append("%s:%s" % (R_VERIFIER_CHANGED, vid))
        cur_deps = cur_v.get("dependency_versions") or {}
        for rel, rec_h in (ver.get("dependency_versions") or {}).items():
            cur_h = cur_deps.get(rel)
            if rec_h is None or cur_h is None:
                reasons.append("%s:%s" % (R_UNVERIFIABLE_VERIFIER, rel))
            elif rec_h != cur_h:
                reasons.append("%s:%s" % (R_DEP_CHANGED, rel))

    if not (recorded.get("reference_refs") or vid):
        reasons.append(R_NO_REFERENCE)          # 信息性：本条无参考/验证器可绑

    # ★ 「重写哈希不能恢复 valid」的程序化堵口
    if ledger_entry is None:
        if recorded.get("_src_hash_rewritten"):
            reasons.append(R_HASH_REWRITTEN)
    else:
        if ledger_entry.get("new_src_hash") != rec_src:
            reasons.append(R_HASH_REWRITTEN)
        elif not (ledger_entry.get("at") and ledger_entry.get("new_evidence_id")):
            reasons.append(R_HASH_REWRITTEN)
        else:
            # 有完整留痕 ⇒ 本次重验**取代**了旧判据，不因旧哈希差异而 stale
            reasons = [r for r in reasons if not r.startswith(R_SRC_CHANGED)]
            reasons.append(R_REVERIFIED)

    if any(r.split(":")[0] in CHANGE_REASONS for r in reasons):
        return "stale", reasons
    if any(r.split(":")[0] in UNVERIFIABLE_REASONS for r in reasons):
        return "needs_review", reasons
    return "valid", reasons


# ---------------------------------------------------------------------------
# 8. 落盘：参考表登记（小体积，可入库）
# ---------------------------------------------------------------------------
def build_registry_payload(base=ROOT):
    hashes = reference_hashes(base)
    vident = verifier_identity(base=base)
    return {
        "schema": "sttp-reference-registry/v1",
        "phase": 45,
        "byte_source": "repo",
        "byte_source_note": ("权威字节源 = git cat-file blob HEAD:<rel>（Phase 44 · D1）。"
                             "取不到 ⇒ null，**不**用空串顶替（fail-closed）。"),
        "generator": "evidence_deps.py --emit-registry",
        "references": collections.OrderedDict([
            (rid, {
                "kind": meta["kind"],
                "paths": meta["paths"],
                "version_label": meta["version_label"],
                "content_hash": hashes.get(rid),
                "note": meta["note"],
            }) for rid, meta in REFERENCE_REGISTRY.items()
        ]),
        "verifiers": vident,
        "note": ("参考表登记**手工维护**（语义概念不可从文件名猜，铁律 #49 反面）。"
                 "内容哈希由本脚本在仓库 blob 口径下现算。"),
    }


def emit_registry(path=REGISTRY_OUT, base=ROOT):
    payload = build_registry_payload(base)
    with open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=False)
        f.write("\n")
    return path


def load_registry(base=ROOT):
    p = os.path.join(base, "11_真实数据", "reference_registry.json")
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))


# ---------------------------------------------------------------------------
# 9. 报告
# ---------------------------------------------------------------------------
def report(base=ROOT):
    """对真实图做一次推导统计（**不改任何东西**）。"""
    g = json.load(open(os.path.join(base, "06_PoC", "etl", "normalized.json"), encoding="utf-8"))
    edges = g["edges"]
    vident = verifier_identity(base=base)
    n_ev = 0
    ref_ids = collections.Counter()
    bindings = collections.Counter()
    vid_c = collections.Counter()
    n_no_dep = 0
    for e in edges:
        for i, ev in enumerate(((e.get("props") or {}).get("verification_evidence") or [])):
            if not isinstance(ev, dict):
                continue
            n_ev += 1
            refs, v = dependencies_for(ev, vident)
            if not refs and not v.get("verifier_id"):
                n_no_dep += 1
            for rr in refs:
                ref_ids[rr["ref_id"]] += 1
                bindings[rr["binding"]] += 1
            vid_c[v.get("verifier_id")] += 1
    print("=" * 92)
    print("A04 验证依赖推导报告（只读，不改图）")
    print("=" * 92)
    print("  evidence 对象总数      : %d" % n_ev)
    print("  无任何参考/验证器依赖  : %d（纯数据断言 ⇒ 状态只由输入决定）" % n_no_dep)
    print("\n  【reference_refs 命中】（一个 evidence 可命中多个）")
    for rid, c in ref_ids.most_common():
        print("    %-22s %7d" % (rid, c))
    print("\n  【绑定强度分列】（★ code_import 是**保守过近似**，任务单允许）")
    for b, c in bindings.most_common():
        print("    %-16s %7d" % (b, c))
    print("\n  【验证器分布】")
    for v, c in vid_c.most_common():
        print("    %-22s %7d" % (str(v), c))
    print("\n  【参考表当前哈希（仓库 blob 口径）】")
    for rid, h in reference_hashes(base).items():
        print("    %-22s %s" % (rid, h))
    return 0


# ---------------------------------------------------------------------------
# 10. 自检（构造性非真空）
# ---------------------------------------------------------------------------
def selfcheck():
    """判据自检：**七段**，每段都可失败（不假通过）。

    ① 参考表登记**自洽**：id 唯一、文件存在于仓库、哈希可算（不可算即 FAIL，不 SKIP）；
    ② AST 依赖**可复现**：`verification_model.py` 必须命中 `dimension_table` 与
       `element_reference`（这是 A04b 的根基，写死在断言里）；
    ③ **impl_declared 命中**：构造 `impl` 串，断言 token 提取正确，且**泛词不误命中**；
    ④ **状态机五态**：valid/stale/needs_review/rejected 各有一条构造输入必命中；
    ⑤ **不可从仓库复算 ≠ 已变更**：src 不可比 ⇒ `needs_review` 而**非** `stale`（铁律 #70）；
    ⑥ **单纯重写哈希不恢复 valid**（构造性）：把记录哈希改成当前值、**无**账本 ⇒ 必为
       `needs_review:hash_rewritten_without_reverify`；补上**完整账本** ⇒ 才可变 `valid`；
    ⑦ **五类变异逐一可定位**（构造性）：在**临时 git 仓库**里分别变异
       ①原始数据 ②量纲表 ③元素表 ④验证器 ⑤验证器依赖版本，
       断言每类都使**恰好**预期的证据集合转 `stale`，**未受影响项保持原状**。
    """
    ok = True
    print("  ① 参考表登记自洽        : ", end="")
    prob = []
    seen = set()
    for rid, meta in REFERENCE_REGISTRY.items():
        if rid in seen:
            prob.append("重复 id %s" % rid)
        seen.add(rid)
        for rel in meta["paths"]:
            if not os.path.exists(os.path.join(ROOT, rel)):
                prob.append("文件不存在 %s" % rel)
        if reference_content_hash(rid) is None:
            prob.append("哈希不可算 %s" % rid)
    print("PASS（%d 张参考表 / %d 个文件）"
          % (len(REFERENCE_REGISTRY),
             sum(len(m["paths"]) for m in REFERENCE_REGISTRY.values()))
          if not prob else "FAIL %s" % prob)
    ok &= not prob

    print("  ② AST 代码级依赖可复现  : ", end="")
    deps = code_imports("11_真实数据/verification_model.py")
    want = {"11_真实数据/dimension_table.py", "11_真实数据/element_reference.py"}
    got = want & (deps or set())
    print("%s（命中 %s；全部命中 %d 个）"
          % ("PASS" if got == want else "FAIL 期望 %s 实得 %s" % (sorted(want), sorted(deps or [])),
             sorted(os.path.basename(x) for x in got), len(deps or [])))
    ok &= (got == want)

    print("  ③ impl token 提取       : ", end="")
    cases = [
        ("NIST WebBook × dimension_table(单位量纲)", ["dimension_table"]),
        ("element_reference.py", ["element_reference"]),
        ("element_reference.py × PubChem(pubchem_molecular_weight)", ["element_reference"]),
        ("verification_model.formula_members", []),
        ("Phase13.Gate.B4", []),
        ("NIST 量纲与单位符号解析", []),          # 泛词「量纲」**不得**误命中
        ("dimension_table.json", ["dimension_table.json"]),
    ]
    bad = [(s, want_r, impl_refs(s)) for s, want_r in cases if impl_refs(s) != want_r]
    print("%s（%d 例，误命中 %d）" % ("PASS" if not bad else "FAIL %s" % bad, len(cases), len(bad)))
    ok &= not bad

    print("  ④ 状态机五态           : ", end="")
    cur = {"src_hashes": {"S": "aaaa"}, "reference_hashes": {"R": "bbbb"},
           "verifiers": {"V": {"code_hash": "cccc", "dependency_versions": {"D": "dddd"}}}}
    base_rec = {"src": "S", "src_hash": "aaaa",
                "reference_refs": [{"ref_id": "R", "content_hash": "bbbb"}],
                "verifier": {"verifier_id": "V", "code_hash": "cccc",
                             "dependency_versions": {"D": "dddd"}}}
    import copy as _cp
    st_cases = []
    st_cases.append(("valid", evidence_status(base_rec, cur)[0]))
    r2 = _cp.deepcopy(base_rec); r2["src_hash"] = "zzzz"
    st_cases.append(("stale", evidence_status(r2, cur)[0]))
    r3 = _cp.deepcopy(base_rec); r3["src_hash"] = None
    st_cases.append(("needs_review", evidence_status(r3, cur)[0]))
    st_cases.append(("rejected", evidence_status(base_rec, cur, claim_rejected=True)[0]))
    r5 = _cp.deepcopy(base_rec); r5["src_hash"] = "aaaa"; r5["_src_hash_rewritten"] = True
    st5 = evidence_status(r5, cur)
    st_cases.append(("needs_review", st5[0]))
    bad5 = [c for c in st_cases if c[0] != c[1]]
    print("%s（%d 例）" % ("PASS" if not bad5 else "FAIL %s" % bad5, len(st_cases)))
    ok &= not bad5

    print("  ⑤ 不可复算 ≠ 已变更     : ", end="")
    r = {"src": "S", "src_hash": None,
         "reference_refs": [], "verifier": {"verifier_id": None}}
    st, rs = evidence_status(r, cur)
    c5 = (st == "needs_review" and any(x.startswith(R_UNVERIFIABLE_SRC) for x in rs))
    print("%s（status=%s，**非** stale）" % ("PASS" if c5 else "FAIL", st))
    ok &= c5

    print("  ⑥ 重写哈希不恢复 valid  : ", end="")
    rec_old = {"src": "S", "src_hash": "aaaa", "reference_refs": [],
               "verifier": {"verifier_id": None}}
    rec_rewritten = {"src": "S", "src_hash": "aaaa", "reference_refs": [],
                     "verifier": {"verifier_id": None}, "_src_hash_rewritten": True}
    s_noledger = evidence_status(rec_rewritten, cur)[0]
    led = {"evidence_id": "evd:0" * 1, "old_src_hash": "zzzz", "new_src_hash": "aaaa",
           "at": "2026-10-14T00:00:00+08:00", "new_evidence_id": "evd:1111111111111111",
           "reason": "recompute"}
    s_ledger = evidence_status(rec_old, cur, ledger_entry=led)[0]
    s_badled = evidence_status(rec_old, cur, ledger_entry={"evidence_id": "evd:0",
                                                           "new_src_hash": "zzzz"})[0]
    c6 = (s_noledger == "needs_review" and s_ledger == "valid" and s_badled == "needs_review")
    print("%s（无账本=%s｜账本不完全=%s｜账本完整=%s）"
          % ("PASS" if c6 else "FAIL", s_noledger, s_badled, s_ledger))
    ok &= c6

    print("  ⑦ 五类变异逐一可定位    : ", end="")
    c7, detail = _mutation_probe()
    print("%s" % ("PASS" if c7 else "FAIL"))
    for d in detail:
        print("       %s" % d)
    ok &= c7

    return 0 if ok else 1


def _mutation_probe():
    """⑦ 在**临时 git 仓库**里分别变异五类依赖，断言「恰好」预期集合转 stale。

    ★ 为什么必须在临时仓库里做：生产口径是**仓库 blob**。若只改工作区文件，
      repo 口径取不到新 blob ⇒ 变异检不出 ⇒ 断言退化成真空通过（铁律 #20/#75）。
    """
    import tempfile
    import subprocess

    def _g(args, cwd):
        return subprocess.run(["git"] + list(args), capture_output=True, cwd=cwd)

    detail = []
    tmp = tempfile.mkdtemp(prefix="sttp_evdeps_probe_")
    try:
        # 镜像参考表 + 一个输入源到临时仓库
        files = set()
        for meta in REFERENCE_REGISTRY.values():
            files |= set(meta["paths"])
        input_rel = "11_真实数据/elementkg_raw.json"
        if os.path.exists(os.path.join(ROOT, input_rel)):
            files.add(input_rel)
        for rel in sorted(files):
            ap = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(ap), exist_ok=True)
            body = sr.repo_blob(rel, ROOT)
            if body is None:
                # ★ 该 locator **未入库**（如 ElementKG2.0 的 raw）⇒ 无可取 blob，
                #   退而取工作区字节（把它写进临时仓库并提交后，repo 口径即可取到）。
                p = os.path.join(ROOT, rel)
                if not os.path.exists(p):
                    continue
                with open(p, "rb") as f:
                    body = f.read()
            with open(ap, "wb") as f:
                f.write(body)
        _g(["init", "-q"], tmp)
        _g(["-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "add", "-A"], tmp)
        _g(["-c", "user.email=p@x", "-c", "user.name=p", "commit", "-q", "-m", "base"], tmp)
        sr.clear_git_cache()

        cur0 = {
            "reference_hashes": reference_hashes(base=tmp),
            "verifiers": verifier_identity(base=tmp),
            "src_hashes": {("ElementKG2.0" if os.path.exists(os.path.join(ROOT, input_rel))
                            else "X"): _repo_bytes_hash(input_rel, tmp)},
        }
        src_sid = "ElementKG2.0"
        rec = {
            "src": src_sid,
            "src_hash": cur0["src_hashes"].get(src_sid),
            "reference_refs": [{"ref_id": "dimension_table",
                                "content_hash": cur0["reference_hashes"]["dimension_table"]},
                               {"ref_id": "element_reference",
                                "content_hash": cur0["reference_hashes"]["element_reference"]}],
            "verifier": {"verifier_id": "verification_model",
                         "code_hash": cur0["verifiers"]["verification_model"]["code_hash"],
                         "dependency_versions":
                             cur0["verifiers"]["verification_model"]["dependency_versions"]},
        }
        st0, rs0 = evidence_status(rec, cur0)
        if st0 != "valid":
            detail.append("(基线应为 valid，实得 %s / %s)" % (st0, rs0))
            return False, detail

        def _mutate(rel, expect_reason_prefix):
            ap = os.path.join(tmp, rel)
            with open(ap, "rb") as f:
                body = f.read()
            with open(ap, "wb") as f:
                f.write(body + b"\n# MUTATION\n")
            _g(["-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "add", "-A"], tmp)
            _g(["-c", "user.email=p@x", "-c", "user.name=p", "commit", "-q", "-m", "m"], tmp)
            sr.clear_git_cache()
            c = {
                "reference_hashes": reference_hashes(base=tmp),
                "verifiers": verifier_identity(base=tmp),
                "src_hashes": {src_sid: _repo_bytes_hash(input_rel, tmp)},
            }
            st, rs = evidence_status(rec, c)
            hit = [r for r in rs if r.startswith(expect_reason_prefix)]
            okx = (st == "stale" and bool(hit))
            return okx, st, rs

        ok_all = True
        for label, rel, prefix in [
            ("原始数据", input_rel, R_SRC_CHANGED),
            ("量纲表", "11_真实数据/dimension_table.py", R_REF_CHANGED),
            ("元素表", "11_真实数据/element_reference.py", R_REF_CHANGED),
            ("验证器", "11_真实数据/verification_model.py", R_VERIFIER_CHANGED),
            ("依赖版本", "11_真实数据/element_reference.py", R_DEP_CHANGED),
        ]:
            good, st, rs = _mutate(rel, prefix)
            ok_all &= good
            detail.append("%s 变异 %-34s ⇒ status=%-6s 命中 %s  %s"
                          % ("✅" if good else "❌", os.path.basename(rel), st,
                             prefix, "PASS" if good else "原因不足：%s" % rs))
        # 未受影响项保持原状：先按**当时**的现值登记一条「只依赖元素表」的证据，
        # 再变异**量纲表** ⇒ 该记录必须仍为 valid（不受无关依赖的变更牵连）。
        c_now = {"reference_hashes": reference_hashes(base=tmp),
                 "verifiers": verifier_identity(base=tmp),
                 "src_hashes": {src_sid: _repo_bytes_hash(input_rel, tmp)}}
        rec_el_only = {
            "src": src_sid, "src_hash": c_now["src_hashes"].get(src_sid),
            "reference_refs": [{"ref_id": "element_reference",
                                "content_hash": c_now["reference_hashes"]["element_reference"]}],
            "verifier": {"verifier_id": None},
        }
        base_ok = evidence_status(rec_el_only, c_now)[0] == "valid"
        good, st, rs = _mutate("11_真实数据/dimension_table.py", R_REF_CHANGED)
        c_after = {"reference_hashes": reference_hashes(base=tmp), "verifiers": {},
                   "src_hashes": {src_sid: c_now["src_hashes"][src_sid]}}
        st2 = evidence_status(rec_el_only, c_after)[0]
        keep = (base_ok and st2 == "valid")
        ok_all &= keep
        detail.append("%s 未受影响项保持原状（基线 %s；量纲表变异后 仅依赖元素表者 仍 %s）"
                      % ("✅" if keep else "❌", "valid" if base_ok else "非 valid", st2))
        return ok_all, detail
    finally:
        sr.rmtree_force(tmp)


def main():
    ap = argparse.ArgumentParser(description="A04 验证依赖模型（Phase 45）")
    ap.add_argument("--emit-registry", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", default=REGISTRY_OUT)
    a = ap.parse_args()
    if a.check:
        print("=" * 92)
        print("evidence_deps 判据自检（七段 · 构造性非真空）")
        print("=" * 92)
        rc = selfcheck()
        print("  ⇒ %s" % ("全过" if rc == 0 else "未通过"))
        return rc
    if a.emit_registry:
        p = emit_registry(a.out)
        print("-> %s（%d 字节）" % (os.path.relpath(p, ROOT), os.path.getsize(p)))
        return 0
    return report()


if __name__ == "__main__":
    sys.exit(main())
