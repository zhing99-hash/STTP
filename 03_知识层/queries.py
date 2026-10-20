# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""
公式知识图谱 · 查询层（queries.py）
================================================================

外查询接口（Phase 43 起分两组）：

**A. 元素级（原始接口，保持兼容）**

    get_node(id)                      取单个节点
    get_neighbors(id)                 取邻居（方向/边类型/置信度）
    subgraph_by_confidence(threshold) 按置信度阈值取子图
    paths_between(a, b)               取两点间路径（**兼容层**：有界，见下）
    list_by_type(t)                   按标签/类型列节点
    filter_edges_by_kind(kind)        按 kind 过滤边

**B. 子图级（Phase 43 / 复评 A06 —— 前端局部图谱浏览的后端核心）**

    search(q, ...)                    文本搜索（前 N 命中 + 分数）
    subgraph(...)                     ★ 局部子图抽取（ego / path / filter）
    paths_between_bounded(a, b, ...)  ★ 有界可解释路径（`ok/no_result/truncated/timeout`）
    overview(...)                     概览元图（替代"全图浏览"）
    snapshot_id()                     查询源身份（sha256[:12]，绑缓存与复现）

设计要点（与项目铁律对齐）
--------------------------
* **五态**（铁律 #67）：`ok / no_result / truncated / timeout / error`。
  「空」与「失败」**必须分列**；异常**绝不**吞成空列表。
* **有界**（复评 A06）：原先 `paths_between` 是
  ``list(nx.all_simple_paths(...))`` —— 无界物化，且 ``except Exception: return []``
  把失败伪装成"没有路径"。现改为带 `max_paths` / `deadline_ms` 的确定性 BFS。
* **契约单源**：`NXBackend` 与 `Neo4jBackend` 只提供 3 个**原语**
  （`all_nodes` / `all_edges` / `source_identity`），其余全部逻辑在基类**共用一份实现**
  —— 两个后端不可能语义漂移（本次不实现第二份 Cypher 抽取逻辑）。
* **确定性**（铁律 #56）：同一参数两次请求，节点/边顺序与内容逐元素相同
  （所有遍历按 `edge id` / `node id` 稳定排序，无随机数、无时间戳）。
* **口径外置**：节点类型与学科解析依赖 `06_PoC/graph_export.pick_type / subject_of`
  这一**权威口径**。03 层不反向 import 06 层 ⇒ 由调用方以
  `node_type_of` / `subject_of_fn` 形参注入（单一口径，不另起一套，铁律 #3/#40）。
  同理，A04 的**验证依赖状态**由调用方用 `11_真实数据/evidence_deps.py` 现算后经
  `build_stale_registry(..., status_by_edge=…)` 注入（03 层不反向 import 11 层）。
* **两层判据不可互相替代**（Phase 45 · A04）：
  ① `edge_fidelity()` = **边级字节忠实度**（来源那一个字节文件变了吗）；
  ② `edge_evidence_status()` = **证据级验证依赖状态**（`valid/stale/needs_review/rejected`，
     还看参考表 / 验证器 / 依赖版本 / 命题是否被否决 / 哈希来路是否明）。
  二者的**排除口径合流到 `edge_is_stale()` 一处**；无 A04 记录时**完全回落**到 ①。

双后端
------
1. **Neo4j 驱动路径**：数据库可达时优先走 Cypher（语句见 ``queries.cypher``）。
2. **NetworkX 兜底**：无可用 DB 时，把 ``normalized.json`` 载入 ``nx.MultiDiGraph``
   并在内存中执行等价查询——即使没有运行中的 Neo4j 也能证明接口逻辑正确。

运行
----
    python queries.py                 # 自动选择后端并跑示例查询
    python queries.py --backend nx    # 强制 NetworkX 兜底
    python queries.py --backend neo4j --uri bolt://localhost:7687
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_INPUT = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")

# ----------------------------------------------------------------------------
# 五态（铁律 #67）—— 「空」与「失败」必须分列
# ----------------------------------------------------------------------------
OK = "ok"
NO_RESULT = "no_result"
TRUNCATED = "truncated"
TIMEOUT = "timeout"
ERROR = "error"
STATUSES = (OK, NO_RESULT, TRUNCATED, TIMEOUT, ERROR)

# ----------------------------------------------------------------------------
# 可信范围（两档；铁律 #19「verified 须分层」）
#   与 graph_export.RANK_ORDER / 11_真实数据/verification_model.py 的 7 档表**必须一致**；
#   自检脚本 `06_PoC/tests/phase43_subgraph_selfcheck.py` 有一条**跨模块一致性断言**防漂移。
# ----------------------------------------------------------------------------
_RANK = {
    "unverified": 0,
    "model_inferred": 1,
    "source_asserted": 2,
    "by_construction": 3,
    "rule_checked": 4,
    "cross_source": 5,
    "human_reviewed": 6,
}
VERIFIED_MIN = _RANK["by_construction"]     # verified（默认档）
STRICT_MIN = _RANK["rule_checked"]          # verified_strict

TRUST_ALL = "all"
TRUST_VERIFIED = "verified"
TRUST_STRICT = "verified_strict"
TRUST_LEVELS = (TRUST_ALL, TRUST_VERIFIED, TRUST_STRICT)

# ----------------------------------------------------------------------------
# why_shown（吸收 codex 设计稿 §5.5）：每个节点/边都必须回答"为什么它在图里"
# ----------------------------------------------------------------------------
WHY_USED = "used_in_step"          # 本次计算/判定直接使用
WHY_EVIDENCE = "evidence_path"     # 位于结论的证据路径上
WHY_USER = "user_specified"        # 用户明确指定（种子 / 搜索命中）
WHY_TASK = "task_anchor"           # 系统识别的任务锚点（本轮后端不使用，前端可留位）
WHY_CONTEXT = "verified_context"   # 经过验证的一跳上下文
WHY_EXPANSION = "expansion"        # 由某次展开并入
WHY_SHOWN_VALUES = (WHY_USED, WHY_EVIDENCE, WHY_USER, WHY_TASK, WHY_CONTEXT, WHY_EXPANSION)

# ----------------------------------------------------------------------------
# 预算（默认值 / 硬上限）
#   ★ 默认入口不得是「加载全量」（铁律 #66）；超出即 truncated，绝不静默截断。
#   ★ Phase 49 收敛（外部评审 §4/§6.1「默认筛选预算从 500 节点 / 2 000 边降到
#     40 / 70，超时提高到 5—10 秒」）：
#     ① `has_symbol` 占 24 341/48 712 条边 ⇒ 500 节点的 ego 子图必然是不可读的毛线团；
#     ② 权威图 67.7 MB，冷启动首查 4.91 s ⇒ 3 000 ms 预算**必然超时**；
#     ③ 实测「50 节点/100 边/10 s ⇒ 约 63 ms 返回」⇒ 40/70/10 s 留有余量。
#   ★ 前端 `js/core.js` 的 `BUDGET` 与之相等（跨层断言 **X4a**：nodes/edges/hops/deadline
#     四项**对称**；`paths` 例外 ⇒ **X4a-path** 断言"前端 5 ≤ 库 20"的**非对称**关系）。
#   ★ 注意 `DEFAULT_HOPS` 同时是 `subgraph()` 的 `max_hops` **默认夹紧上限**，故 3 → 2
#     会让"只传 `hops=3`、不传 `max_hops`"的请求实走 2 跳。这**不是静默丢弃**：
#     响应会给出 `status=truncated`、`truncated_by="hops"`、`hops=2`/`requested_hops=3`
#     与 reason「请求跳数被夹紧至 max_hops=2」⇒ 由后端自检 **V7c** 断言（2026-10-15 新增，
#     起因：V7a 隐式依赖该默认值 = 3 而变红，见该处注释）。
# ----------------------------------------------------------------------------
DEFAULT_NODES = 40
DEFAULT_EDGES = 70
DEFAULT_HOPS = 2
# ★ 文本搜索的最短查询长度（**单一真值源**）：前端 `core.js` 的 `MIN_QUERY_LEN`
#   必须与之相等（跨层断言 X5b）；前端据此**不发**明知会被 400 拒绝的请求
#   （否则"第一次按键"就制造一次 BAD_REQUEST，并把 400 显示成"没有匹配"）。
MIN_QUERY_LEN = 2
DEFAULT_DEADLINE_MS = 10000
# ★ 保持 20：这是**库级**上限（`paths_between()` 兼容层与 `/api/path` 的默认均取它，
#   发布验证 `verify_release.py` 也依赖它）。前端 `BUDGET.paths = 5` 是**UI 请求值**
#   （便于逐条解释路径），属"请求 ≤ 库默认"的**非对称**关系，由 X4a 显式断言，
#   不是两处各写一套默认。
DEFAULT_MAX_PATHS = 20
DEFAULT_PATH_HOPS = 5
DEFAULT_SESSION_NODES = 120
DEFAULT_SESSION_EDGES = 200
HARD = {"nodes": 5000, "edges": 20000, "hops": 5, "deadline_ms": 30000, "paths": 200}

# ----------------------------------------------------------------------------
# Cypher 语句（与 queries.cypher 保持一字不差）
# ----------------------------------------------------------------------------
CYPHER: Dict[str, str] = {
    "get_node": (
        "MATCH (n {id:$id}) "
        "RETURN n.id AS id, labels(n) AS labels, properties(n) AS props"
    ),
    "get_neighbors": (
        "MATCH (n {id:$id})-[r]-(m) "
        "RETURN m.id AS id, TYPE(r) AS type, properties(r) AS props, "
        "       CASE WHEN startNode(r).id = $id THEN 'out' ELSE 'in' END AS direction"
    ),
    "subgraph_by_confidence": (
        "MATCH (a)-[r]->(b) WHERE r.confidence >= $threshold "
        "RETURN a.id AS start, b.id AS end, TYPE(r) AS type, properties(r) AS props"
    ),
    "paths_between": (
        "MATCH p=(a {id:$a})-[:*1..5]->(b {id:$b}) "
        "RETURN [n IN nodes(p) | n.id] AS path LIMIT 20"
    ),
    "list_by_type": (
        "MATCH (n) WHERE $t IN labels(n) OR n.type = $t "
        "RETURN n.id AS id, labels(n) AS labels, properties(n) AS props"
    ),
    "filter_edges_by_kind": (
        "MATCH (a)-[r]->(b) WHERE r.kind = $kind "
        "RETURN a.id AS start, b.id AS end, TYPE(r) AS type, properties(r) AS props"
    ),
    # ---- Phase 43：子图抽取只依赖这两条**原语**语句（其余逻辑在基类共用）----
    "all_nodes": (
        "MATCH (n) RETURN n.id AS id, labels(n) AS labels, properties(n) AS props"
    ),
    "all_edges": (
        "MATCH (a)-[r]->(b) "
        "RETURN coalesce(r.id, a.id + '|' + type(r) + '|' + b.id) AS id, "
        "       a.id AS source, b.id AS target, type(r) AS type, "
        "       coalesce(r.kind, '') AS kind, properties(r) AS props"
    ),
}


# ----------------------------------------------------------------------------
# 模块级小工具
# ----------------------------------------------------------------------------
def _ms(t0: float) -> int:
    return int(round((time.monotonic() - t0) * 1000))


def _expired(t0: float, deadline_ms: int) -> bool:
    return (time.monotonic() - t0) * 1000.0 >= float(deadline_ms)


def _clamp(v, lo: int, hi: int):
    """返回 (值, 是否被夹紧)。"""
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError("预算参数必须是整数：%r" % (v,))
    c = n < lo or n > hi
    return max(lo, min(hi, n)), c


def _norm_set(v) -> Optional[set]:
    """逗号分隔字符串 / 列表 → set；空 → None（= 不过滤）。"""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.split(",")
    s = {str(x).strip() for x in (v or []) if str(x).strip()}
    return s or None


def _norm_seeds(seeds) -> List[str]:
    if seeds is None:
        return []
    if isinstance(seeds, str):
        seeds = seeds.split(",")
    out, seen = [], set()
    for s in seeds:
        s = str(s).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def edge_rank(props: dict) -> int:
    """边的可信档位（与 graph_export.derive_verified/strict 同一判定）。

    无 `verification_level` 时退回旧 `verified` 布尔（>= by_construction 视为 3）。
    """
    lv = (props or {}).get("verification_level")
    if lv is None:
        return _RANK["by_construction"] if (props or {}).get("verified") else 0
    return _RANK.get(lv, 0)


def build_stale_registry(known_sources, current_hashes, repo_status=None,
                         status_by_edge=None) -> dict:
    """构造 stale/fidelity 判定所需的登记快照
    （由调用方从 `11_真实数据/source_registry.py` 现算）。

    ★ 语义（方案 §5.2 + Phase 44 · D1）：一条边的**证据字节忠实度**是**四分类**
      （互斥且完备，见 `edge_fidelity`）：
      ① `verified`                  全部来源可从仓库复算，且 `src_hash` == 当前仓库哈希；
      ② `changed`                   可复算但哈希不符 ⇒ **真 stale**（来源已变更，需重验）；
      ③ `unverifiable_no_source`    无证据 / 来源未登记 / 来源**无 locator** ⇒ fail-closed；
      ④ `unverifiable_not_in_repo`  来源**有 locator 但未入库** ⇒ 不可从仓库复算。

    ★ ③ 与 ④ **都**是「不可确认为新鲜」，但**性质不同**：
      ③ 是「本就无外部数据文件」（模型/规则/领域约定），**计入 `stale`**；
      ④ 是「数据文件存在但按『大体积派生对象不入库』有意未随仓库分发」
      （`*_raw.json`/`*_cache.json`，约 20 MB，**可从上游原始数据复算**）——
      它是**已知边界**，**分列披露但不计入 `stale`**。
      理由：把它并进 `stale` 会让「已变更」这一**可操作**信号被 47 176 条（96.8%）淹没，
      使 `exclude_stale` 退化为「什么都别显示」；而它**并非变更**。
      ⇒ 它**不静默**：始终以独立计数出现在 `stats.evidence_fidelity` 与边属性 `fidelity` 上。

    ★ 第 4 个可选键 `status`（Phase 45 · A04）：`{edge_id: {"status":…, "reasons":[…],
      "source":"recorded"|"absent", "n_evidence":…, "evidence":[…]}}`，
      由调用方用 `11_真实数据/evidence_deps.py` 的 `evidence_status()` **现算后注入**
      （03 层不反向 import 11 层 ⇒ 与 `node_type_of` 同样的「形参注入」惯例）。
      **不注入时**（`{}`/`None`）本模块行为与 Phase 44 **逐位一致**——这是有意的：
      发布样例图、裸 clone 前的中间图都可能还没有 A04 记录，此时不得因缺记录而改判。
    """
    return {"known": set(known_sources or ()),
            "hashes": dict(current_hashes or {}),
            "repo_status": dict(repo_status or {}),
            "status": dict(status_by_edge or {})}


FIDELITY_CLASSES = ("verified", "changed", "unverifiable_no_source",
                    "unverifiable_not_in_repo")
#: 计入 `stale` 的忠实度类别（fail-closed 口径，与 Phase 43 保持一致）
STALE_FIDELITIES = ("changed", "unverifiable_no_source")

# ----------------------------------------------------------------------------
# A04 · 验证依赖状态（Phase 45）—— 与 `evidence_deps.STATUSES` **逐值一致**
#   ① 这是**证据级**判据（参考表 / 验证器 / 依赖版本 / 命题被否决 / 哈希来路不明），
#      与上面**边级**的「字节忠实度」四分类是**两层**，不可互相替代：
#        · 忠实度只回答「来源那一个字节文件变了吗」；
#        · 依赖状态还回答「它所依赖的**参考表/验证器**变了吗」。
#   ② 两层的**排除口径**必须合流到 `edge_is_stale()` 这**一个**函数（铁律 #40），
#      否则「前端显示不 stale、后端却按 stale 排除」这类静默错位必然出现。
# ----------------------------------------------------------------------------
EVIDENCE_STATUSES = ("valid", "stale", "needs_review", "rejected")
#: 严重度排序（多证据聚合取**最坏**）：值越大越严重
EVIDENCE_STATUS_RANK = {"valid": 0, "needs_review": 1, "stale": 2, "rejected": 3}
#: 计入 `stale`（即被 `exclude_stale` 排除）的 A04 状态。
#   ★ `rejected`（命题已被显式否决）**一并排除**：它比 stale 更该被藏起来。
#   ★ `needs_review` **不**排除 —— 它是「本环境不可判定」（缺数据本身，非变更），
#     若并入就退化成「几乎清空全图」（铁律 #70），只是**必须显式披露**。
STALE_EVIDENCE_STATUSES = ("stale", "rejected")
#: A04 状态 → 字节忠实度四分类（与 `evidence_deps.FIDELITY_OF_STATUS` 同口径；
#   自检 [V19] 有一条跨模块逐值一致性断言防漂移）
FIDELITY_OF_EVIDENCE_STATUS = {"valid": "verified", "stale": "changed",
                               "needs_review": "unverifiable_no_source",
                               "rejected": "changed"}


def edge_key(e) -> str:
    """边的稳定键（与 `evidence_deps.mark_rewrites()` 的 `cid` **同口径**，避免两处不一致）。"""
    props = e.get("props") or {}
    return (e.get("id") or props.get("id")
            or "%s|%s|%s" % (e.get("type") or props.get("type"), e.get("source"), e.get("target")))


def edge_evidence_status(e, stale_registry) -> Optional[dict]:
    """边的 **A04 验证依赖状态**（聚合到边）。

    返回 `{"status","reasons","source","n_evidence","n_with_deps",…}`；
    **无 A04 记录 ⇒ `None`**（★ 绝不写成 `"valid"` —— 那正是「被信任≠被验证」，
    铁律 #19/#30）。聚合规则：多证据取**最坏**（`EVIDENCE_STATUS_RANK`）。
    """
    if not stale_registry:
        return None
    m = stale_registry.get("status") or {}
    if not m:
        return None
    rec = m.get(edge_key(e))
    if not rec:
        return None
    return rec


def edge_evidence_statuses(e, stale_registry) -> List[dict]:
    """逐条证据的 A04 状态（证据卡片用）。无记录 ⇒ `[]`。"""
    rec = edge_evidence_status(e, stale_registry)
    if not rec:
        return []
    return list(rec.get("evidence") or [])


def edge_fidelity(e, stale_registry) -> str:
    """边的**证据字节忠实度**四分类（见 `build_stale_registry` 的说明）。

    ★ 这是**边级字节**层（只看 `src_hash` vs 当前仓库字节），**不含**参考表/验证器依赖。
      依赖层见 `edge_evidence_status()`，两者**并列披露、不得互相替代**。

    优先级（多来源时取**最强信号**）：
      无证据/畸形 → 未登记 → 哈希可算且不符 → 未入库 → 无当前哈希 → verified
    """
    if not stale_registry:
        return "verified"
    known = stale_registry.get("known") or set()
    hashes = stale_registry.get("hashes") or {}
    rstat = stale_registry.get("repo_status") or {}
    evs = (e.get("props") or {}).get("verification_evidence") or []
    if not evs:
        return "unverifiable_no_source"          # 无证据 ⇒ 无法判定（fail-closed）
    any_mismatch = False
    any_not_in_repo = False
    any_no_locator = False
    for ev in evs:
        if not isinstance(ev, dict):
            return "unverifiable_no_source"
        src = ev.get("src")
        if src not in known:
            return "unverifiable_no_source"
        cur = hashes.get(src)
        if cur is None:
            if rstat.get(src) == "not_in_repo":
                any_not_in_repo = True
            else:
                any_no_locator = True
            continue
        if ev.get("src_hash") != cur:
            any_mismatch = True
    if any_mismatch:
        return "changed"
    if any_not_in_repo:
        return "unverifiable_not_in_repo"
    if any_no_locator:
        return "unverifiable_no_source"
    return "verified"


def fidelity_counts(edges, stale_registry) -> dict:
    """四分类计数（**可直接加总回边总数** —— 分列而不合并，铁律 #41/#67 同一精神）。"""
    c = {k: 0 for k in FIDELITY_CLASSES}
    for e in edges:
        k = edge_fidelity(e, stale_registry)
        c[k] = c.get(k, 0) + 1
    return c


# ★ 证据等级 1–5（Phase 58 · `00_项目管理/证据分级规范与抓取优先级_20261019.md`）。
#   **派生**而非人工打分：grade = min(断言强度档, 字节忠实度档)，多源独立且 ≥3 时 +1（封顶 5）。
#   规则变更必须升 `GRADE_RULE_VERSION` —— 分数写进投影后即固化（D64 同族：不得随前端现算漂移）。
GRADE_RULE_VERSION = "e1"
GRADE_VL = {"cross_source": 5, "by_construction": 4, "rule_checked": 4,
            "source_asserted": 3, "model_inferred": 1}   # 缺 level ⇒ 按 1（fail-closed）
GRADE_FD = {"verified": 5, "unverifiable_not_in_repo": 2,
            "unverifiable_no_source": 1, "changed": 1}   # 未入库=2：「不可复算」≠「不可信」


def edge_grade(e, stale_registry) -> Optional[int]:
    """边的证据等级 1–5；登记表不可用 ⇒ **None**（不可判定，不得假装 5 分）。

    多源加成：证据来自 ≥2 个不同来源、或 ≥2 条标记 `indep` 的独立证据，且基础档 ≥3 ⇒ +1。
    """
    if not stale_registry:
        return None
    props = e.get("props") or {}
    v = GRADE_VL.get(props.get("verification_level"), 1)
    f = GRADE_FD.get(edge_fidelity(e, stale_registry), 1)
    g = min(v, f)
    evs = [x for x in (props.get("verification_evidence") or []) if isinstance(x, dict)]
    n_src = len({x.get("src") for x in evs if x.get("src")})
    n_indep = sum(1 for x in evs if x.get("indep"))
    if g >= 3 and (n_src >= 2 or n_indep >= 2):
        g = min(5, g + 1)
    return g


def grade_counts(edges, stale_registry) -> dict:
    """等级分布计数；`null` 单列（登记表不可用时不是 0 条，是**不可判定**）。"""
    c = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "null": 0}
    for e in edges:
        g = edge_grade(e, stale_registry)
        c["null" if g is None else str(g)] += 1
    return c


def evidence_status_counts(edges, stale_registry) -> Optional[dict]:
    """A04 状态分列计数（**边级聚合**）。

    返回 `{"valid":…, "stale":…, "needs_review":…, "rejected":…,
           "no_record":…, "n_edges_total":…, "n_excluded_by_status":…}`。
    ★ `no_record`（图上无 A04 记录）**单列**，不并入 `valid`：缺记录 ≠ 已验证。
    ★ 登记表/A04 不可用 ⇒ 返回 **None**（不可判定，不假装全是 valid）。
    """
    if not stale_registry or not (stale_registry.get("status") or {}):
        return None
    c = {k: 0 for k in EVIDENCE_STATUSES}
    c.update({"no_record": 0, "n_edges_total": 0, "n_excluded_by_status": 0})
    for e in edges:
        c["n_edges_total"] += 1
        rec = edge_evidence_status(e, stale_registry)
        if not rec:
            c["no_record"] += 1
            continue
        st = rec.get("status")
        c[st] = c.get(st, 0) + 1
        if st in STALE_EVIDENCE_STATUSES:
            c["n_excluded_by_status"] += 1
    return c


def edge_is_stale(e, stale_registry) -> bool:
    """`stale` = 「**不可确认**其证据新鲜」的 fail-closed 口径（铁律 #30）。
    由 `edge_fidelity` 单点定义，避免两处判据漂移。
    ★ `unverifiable_not_in_repo` **不在其中**（见 `build_stale_registry`）。

    ★ Phase 45 · A04 起，本函数是**唯一的排除口径单点**，把**两层**合流：
      ① 边级字节忠实度 ∈ `STALE_FIDELITIES`；
      ② 证据级 A04 状态 ∈ `STALE_EVIDENCE_STATUSES`（`stale` / `rejected`）。
      `needs_review` **不**排除（不可判定 ≠ 已变更，铁律 #70），但由
      `edge_evidence_status()` 单独披露 ⇒ 「不排除」不等于「不告知」。
      **无 A04 记录 ⇒ 完全回落到 ①**，故 A04 未落图的图（发布样例、裸 clone 中期图）
      行为与 Phase 44 逐位一致。
    """
    if edge_fidelity(e, stale_registry) in STALE_FIDELITIES:
        return True
    st = edge_evidence_status(e, stale_registry)
    return bool(st) and st.get("status") in STALE_EVIDENCE_STATUSES


def fidelity_reasons(e, stale_registry) -> List[str]:
    """逐条给出**非 verified** 的原因（证据卡片用；四条与四分类一一对应）。"""
    out = []
    if not stale_registry:
        return out
    known = stale_registry.get("known") or set()
    hashes = stale_registry.get("hashes") or {}
    rstat = stale_registry.get("repo_status") or {}
    evs = (e.get("props") or {}).get("verification_evidence") or []
    if not evs:
        return ["无证据（无法判定）"]
    for ev in evs:
        if not isinstance(ev, dict):
            out.append("证据对象畸形（无法判定）")
            continue
        src = ev.get("src")
        if src not in known:
            out.append("来源未登记：%s" % src)
        elif rstat.get(src) == "not_in_repo" and hashes.get(src) is None:
            out.append("locator 未入库（不可从仓库复算，需上游原始数据）：%s" % src)
        elif hashes.get(src) is None:
            out.append("来源无 locator（不可复算）：%s" % src)
        elif ev.get("src_hash") != hashes.get(src):
            out.append("内容哈希不符（来源已变更）：%s" % src)
    return out


def stale_reasons(e, stale_registry) -> List[str]:
    """**仅** stale 原因（= 两层排除口径的并集，与 `edge_is_stale` 严格等价）。

    保持 Phase 43 的对外契约：**`stale_reasons` 非空 ⇔ `edge_is_stale` 为真**
    （自检 [V19] 有反例断言守住这条等价 —— 曾因 A04 只改 `edge_is_stale`
    不改本函数而出现「被排除却说不出理由」的静默错位）。
    """
    if not edge_is_stale(e, stale_registry):
        return []
    out = list(fidelity_reasons(e, stale_registry))
    st = edge_evidence_status(e, stale_registry)
    if st and st.get("status") in STALE_EVIDENCE_STATUSES:
        for r in (st.get("reasons") or []):
            tag = "验证依赖：%s" % r
            if tag not in out:
                out.append(tag)
    return out


def _match_score(q: str, ql: str, nid: str, label: str, latex: str) -> int:
    nidl, labl, ltxl = (nid or "").lower(), (label or "").lower(), (latex or "").lower()
    if nidl == ql:
        return 100
    if labl == ql:
        return 90
    if nidl.startswith(ql):
        return 70
    if labl.startswith(ql):
        return 65
    if ql and ql in labl:
        return 55
    if ql and ql in ltxl:
        return 50
    if ql and ql in nidl:
        return 45
    return 0


# ----------------------------------------------------------------------------
# 后端基类（**全部查询逻辑都在这里**，两个后端共用 ⇒ 契约不可能漂移）
# ----------------------------------------------------------------------------

class Backend:
    """子类只需实现 3 个原语；其余由本类提供（单一实现，铁律 #40）。"""

    name = "base"

    # ---------------- 原语（子类实现） ----------------
    def all_nodes(self) -> List[dict]:
        """[{id, labels, props}]"""
        raise NotImplementedError

    def all_edges(self) -> List[dict]:
        """[{id, source, target, type, kind, props}]"""
        raise NotImplementedError

    def source_identity(self):
        """查询源身份 → `(kind, value)`。子类**应当覆盖**（文件后端用 ("file", abspath)）。"""
        return ("unknown", self.name)

    def close(self) -> None:  # pragma: no cover
        pass

    # ---------------- 索引（懒建一次） ----------------
    def _index(self) -> dict:
        if getattr(self, "_idx", None) is not None:
            return self._idx
        nodes: Dict[str, dict] = {}
        for n in self.all_nodes():
            nodes[n["id"]] = {"id": n["id"],
                              "labels": list(n.get("labels") or []),
                              "props": dict(n.get("props") or {})}
        edges: List[dict] = []
        adj: Dict[str, list] = {}
        for raw in self.all_edges():
            props = dict(raw.get("props") or {})
            e = {
                "id": raw.get("id") or "%s|%s|%s" % (raw.get("type") or props.get("type") or "",
                                                     raw.get("source"), raw.get("target")),
                "source": raw.get("source"),
                "target": raw.get("target"),
                "type": raw.get("type") or props.get("type") or "",
                "kind": raw.get("kind") or props.get("kind") or "",
                "props": props,
            }
            edges.append(e)
            adj.setdefault(e["source"], []).append((e, e["target"], "out"))
            adj.setdefault(e["target"], []).append((e, e["source"], "in"))
        # 确定性（铁律 #56）：边按 id 稳定排序 ⇒ 遍历顺序与请求参数无关
        edges.sort(key=lambda x: x["id"])
        for k in adj:
            adj[k].sort(key=lambda t: (t[0]["id"], t[2]))
        self._idx = {"nodes": nodes, "edges": edges, "adj": adj}
        return self._idx

    @property
    def node_count(self) -> int:
        return len(self._index()["nodes"])

    @property
    def edge_count(self) -> int:
        return len(self._index()["edges"])
    def source_path(self) -> Optional[str]:
        kind, val = self.source_identity()
        return val if kind == "file" else None

    def snapshot_id(self) -> str:
        """查询源身份 `sha256[:12]`。

        ★ 快照绑定的意义（铁律 #53 / Phase 37）：页面与查询必须同版本；
          图一旦变化 id 就变 ⇒ 前端缓存与"复现同一次查询"自动失效。
        实现：按 `(path, mtime_ns, size)` 缓存摘要（54.9 MB 全量哈希约 150 ms，
        不适合每请求现算；文件被替换 ⇒ mtime/size 变 ⇒ 摘要重算）。
        """
        kind, val = self.source_identity()
        if kind == "file" and val and os.path.exists(val):
            st = os.stat(val)
            key = (val, st.st_mtime_ns, st.st_size)
            if getattr(self, "_snap_key", None) != key:
                h = hashlib.sha256()
                with open(val, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                self._snap_key, self._snap_id = key, "sha256:" + h.hexdigest()[:12]
            return self._snap_id
        seed_txt = "%s|%s" % (kind, val)
        return "sha256:" + hashlib.sha256(seed_txt.encode("utf-8")).hexdigest()[:12]

    # ---------------- A. 元素级（通用实现；NX 后端曾各自实现，现统一） ----------------
    def get_node(self, node_id: str) -> Optional[dict]:
        n = self._index()["nodes"].get(node_id)
        if n is None:
            return None
        return {"id": node_id, "labels": list(n["labels"]), "props": dict(n["props"])}

    def get_neighbors(self, node_id: str) -> List[dict]:
        out: List[dict] = []
        for e, other, direction in self._index()["adj"].get(node_id, []):
            out.append({"id": other, "type": e["type"], "direction": direction,
                        "kind": e["kind"],
                        "confidence": (e.get("props") or {}).get("confidence")})
        return out

    def subgraph_by_confidence(self, threshold: float) -> Dict[str, list]:
        nodes, edges = set(), []
        for e in self._index()["edges"]:
            conf = (e.get("props") or {}).get("confidence")
            if conf is not None and float(conf) >= threshold:
                edges.append({"start": e["source"], "end": e["target"], "type": e["type"],
                              "kind": e["kind"], "confidence": conf})
                nodes.add(e["source"])
                nodes.add(e["target"])
        return {"nodes": sorted(nodes), "edges": edges}

    def list_by_type(self, t: str) -> List[dict]:
        res = []
        for nid, n in sorted(self._index()["nodes"].items()):
            if t in n["labels"] or n["props"].get("type") == t:
                res.append({"id": nid, "labels": list(n["labels"])})
        return res

    def filter_edges_by_kind(self, kind: str) -> List[dict]:
        res = []
        for e in self._index()["edges"]:
            if e["kind"] == kind:
                res.append({"start": e["source"], "end": e["target"], "type": e["type"],
                            "kind": kind,
                            "confidence": (e.get("props") or {}).get("confidence")})
        return res

    def paths_between(self, a: str, b: str, cutoff: int = 5) -> List[list]:
        """**兼容层**（Phase 43 收窄）：返回节点 id 路径列表，最多 `DEFAULT_MAX_PATHS` 条。

        ★ 已移除无界 `list(all_simple_paths(...))` 与 `except Exception: return []`
        （复评 A06）。需要五态/截断信息请直接用 `paths_between_bounded`。
        """
        r = self.paths_between_bounded(a, b, max_paths=DEFAULT_MAX_PATHS, max_hops=cutoff)
        return r["paths"]

    # ---------------- 过滤（子图共用） ----------------
    @staticmethod
    def _trust_ok(e: dict, trust: str) -> bool:
        if trust == TRUST_ALL:
            return True
        return edge_rank(e.get("props")) >= (STRICT_MIN if trust == TRUST_STRICT else VERIFIED_MIN)

    @staticmethod
    def _edge_ok(e: dict, f: dict) -> bool:
        """与可信档位无关的过滤（type/kind/confidence/model_inferred/stale）。

        ★ `exclude_stale` 走 `edge_is_stale()` —— 那是**唯一**的排除口径单点，
          已把「边级字节忠实度」与「证据级 A04 依赖状态」两层合流（Phase 45）。
          在此处另写一套判据就会产生「显示不 stale、却被静默排除」的错位。
        """
        p = e.get("props") or {}
        if f["types"] is not None and e["type"] not in f["types"]:
            return False
        if f["kinds"] is not None and e["kind"] not in f["kinds"]:
            return False
        if f["min_confidence"] > 0.0:
            c = p.get("confidence")
            if c is None or float(c) < f["min_confidence"]:
                return False
        if f["exclude_model_inferred"] and p.get("verification_level") == "model_inferred":
            return False
        if f["exclude_stale"] and edge_is_stale(e, f.get("stale_sources")):
            return False
        return True

    # ---------------- A/B. 搜索 ----------------
    def search(self, q: str, node_types=None, subjects=None, limit: int = 20,
               node_type_of: Optional[Callable] = None,
               subject_of_fn: Optional[Callable] = None) -> dict:
        t0 = time.monotonic()
        q = (q or "").strip()
        if not q or len(q) < MIN_QUERY_LEN:
            raise ValueError("参数 q 至少 %d 个字符" % MIN_QUERY_LEN)
        limit, _ = _clamp(limit, 1, 100)
        nt, sub = _norm_set(node_types), _norm_set(subjects)
        ql = q.lower()
        hits = []
        for nid, n in sorted(self._index()["nodes"].items()):
            props = n["props"]
            ntype = node_type_of(n["labels"]) if node_type_of else \
                (n["labels"][0] if n["labels"] else "Unknown")
            subj = subject_of_fn(props.get("domain"), nid) if subject_of_fn else \
                str(props.get("domain") or "")
            if nt and ntype not in nt:
                continue
            if sub and subj not in sub:
                continue
            label = str(props.get("name") or nid)
            latex = str(props.get("latex") or "")
            score = _match_score(q, ql, nid, label, latex)
            if score > 0:
                hits.append({"id": nid, "type": ntype, "subject": subj, "label": label,
                             "formula": latex, "score": score})
        hits.sort(key=lambda h: (-h["score"], h["id"]))
        total = len(hits)
        shown = hits[:limit]
        return {
            "status": OK if shown else NO_RESULT,
            "reason": "" if shown else "没有匹配「%s」的节点" % q,
            "query": {"q": q, "node_types": sorted(nt) if nt else None,
                      "subjects": sorted(sub) if sub else None, "limit": limit},
            "hits": shown, "count": len(shown), "total_matches": total,
            "truncated": total > len(shown), "elapsed_ms": _ms(t0),
        }

    # ---------------- B. 有界路径 ----------------
    def _find_paths(self, a: str, b: str, max_paths: int, max_hops: int, deadline_ms: int,
                    edge_ok: Optional[Callable] = None, t0: Optional[float] = None,
                    counter: Optional[dict] = None) -> dict:
        """确定性 **BFS**（先短后长）收集至多 `max_paths` 条简单路径。

        返回 `{paths, truncated, timed_out, pending, missing, elapsed_ms}`；
        每条 path = `{"nodes": [id...], "edges": [edge dict...]}`。

        ★ 本函数**不**返回 `hidden_by_trust`：它只是 BFS，`edge_ok` 会被**每条部分路径**
          重复调用（实测同一被拒边最多 601 次）⇒ 在 BFS 内计数就是把"判定次数"当成
          "不同边数"。去重计数由调用方（`subgraph`）持有集合完成（D45）。
        """
        t0 = t0 if t0 is not None else time.monotonic()
        idx = self._index()
        if a not in idx["nodes"] or b not in idx["nodes"]:
            missing = [x for x in (a, b) if x not in idx["nodes"]]
            return {"paths": [], "truncated": False, "timed_out": False, "pending": 0,
                    "missing": missing, "elapsed_ms": _ms(t0)}
        paths: List[dict] = []
        truncated = False
        timed_out = False
        # 队列元素：(当前节点, 节点路径, 边路径, 已访问集合)
        from collections import deque
        q = deque([(a, [a], [], {a})])
        pending = 0
        while q:
            if _expired(t0, deadline_ms):
                timed_out = True
                pending = len(q)
                break
            node, npath, epath, seen = q.popleft()
            if len(epath) >= max_hops:
                continue
            for e, other, _direction in idx["adj"].get(node, []):
                if other in seen:
                    continue
                if edge_ok is not None and not edge_ok(e):
                    continue
                npath2, epath2 = npath + [other], epath + [e]
                if other == b:
                    paths.append({"nodes": npath2, "edges": epath2})
                    if len(paths) >= max_paths:
                        truncated = True
                        pending = len(q)
                        break
                    continue
                q.append((other, npath2, epath2, seen | {other}))
            if truncated:
                break
        return {"paths": paths, "truncated": truncated, "timed_out": timed_out,
                "pending": pending, "missing": [], "elapsed_ms": _ms(t0)}

    def paths_between_bounded(self, a: str, b: str, max_paths: int = DEFAULT_MAX_PATHS,
                              max_hops: int = DEFAULT_PATH_HOPS,
                              deadline_ms: int = DEFAULT_DEADLINE_MS,
                              edge_ok: Optional[Callable] = None) -> dict:
        """有界可解释路径（复评 A06）。**不吞异常**：失败是 `error`，不是"无路径"。"""
        t0 = time.monotonic()
        max_paths, _ = _clamp(max_paths, 1, HARD["paths"])
        max_hops, _ = _clamp(max_hops, 1, HARD["hops"])
        deadline_ms, _ = _clamp(deadline_ms, 1, HARD["deadline_ms"])
        try:
            r = self._find_paths(a, b, max_paths, max_hops, deadline_ms,
                                 edge_ok=edge_ok, t0=t0)
        except Exception as ex:                                   # noqa: BLE001
            return {"status": ERROR, "reason": "%s: %s" % (type(ex).__name__, ex),
                    "error_code": "TOOL_EXECUTION_ERROR", "paths": [], "path_edges": [],
                    "truncated": False, "pending": 0,
                    "max_paths": max_paths, "max_hops": max_hops,
                    "elapsed_ms": _ms(t0)}
        if r["missing"]:
            return {"status": NO_RESULT, "reason": "节点不存在：%s" % ",".join(r["missing"]),
                    "error_code": None, "paths": [], "path_edges": [], "truncated": False,
                    "pending": 0, "max_paths": max_paths, "max_hops": max_hops,
                    "elapsed_ms": _ms(t0)}
        if r["timed_out"]:
            return {"status": TIMEOUT, "reason": "超过 deadline_ms=%d" % deadline_ms,
                    "error_code": "GRAPH_QUERY_TIMEOUT",
                    "paths": [p["nodes"] for p in r["paths"]],
                    "path_edges": [p["edges"] for p in r["paths"]],
                    "truncated": False, "pending": r["pending"],
                    "max_paths": max_paths, "max_hops": max_hops, "elapsed_ms": _ms(t0)}
        if not r["paths"]:
            return {"status": NO_RESULT,
                    "reason": "在 max_hops=%d 内无可达路径" % max_hops, "error_code": None,
                    "paths": [], "path_edges": [], "truncated": False, "pending": 0,
                    "max_paths": max_paths, "max_hops": max_hops, "elapsed_ms": _ms(t0)}
        return {"status": TRUNCATED if r["truncated"] else OK,
                "reason": ("已达 max_paths=%d，还有更多路径" % max_paths) if r["truncated"] else "",
                "error_code": None,
                "paths": [p["nodes"] for p in r["paths"]],
                "path_edges": [p["edges"] for p in r["paths"]],
                "truncated": r["truncated"], "pending": r["pending"],
                "max_paths": max_paths, "max_hops": max_hops, "elapsed_ms": _ms(t0)}

    # ---------------- B. 局部子图（核心） ----------------
    def subgraph(self, seeds=None, mode: str = "ego", hops: Optional[int] = None,
                 node_types=None, subjects=None, types=None, kinds=None,
                 trust: str = TRUST_VERIFIED, min_confidence: float = 0.0,
                 exclude_model_inferred: bool = False, exclude_stale: bool = False,
                 stale_sources: Optional[dict] = None,   # 实为 stale registry（由 build_stale_registry 构造）
                 max_nodes: int = DEFAULT_NODES, max_edges: int = DEFAULT_EDGES,
                 max_hops: int = DEFAULT_HOPS, deadline_ms: int = DEFAULT_DEADLINE_MS,
                 max_paths: int = DEFAULT_MAX_PATHS,
                 node_type_of: Optional[Callable] = None,
                 subject_of_fn: Optional[Callable] = None) -> dict:
        """局部子图抽取。返回**规范化** nodes/edges + `why_shown` 映射 + 五态。

        ★ 投影为前端格式（`graph_data`）由调用方做（复用 graph_export 的**唯一口径**）；
        本函数只回答"取哪一块"。

        模式：`ego`（种子邻域，默认 1 跳）/ `path`（两点间路径上的子图，默认 5 跳）/
        `filter`（全图按条件筛，**会全量扫描以获得"实有"计数**）。
        """
        t0 = time.monotonic()
        idx = self._index()
        nodes_by_id = idx["nodes"]
        mode = (mode or "ego").lower()
        if mode not in ("ego", "path", "filter"):
            raise ValueError("mode 必须 ∈ {ego, path, filter}，收到 %r" % (mode,))
        if trust not in TRUST_LEVELS:
            raise ValueError("trust 必须 ∈ %s，收到 %r" % (list(TRUST_LEVELS), trust))

        clamp = {}
        max_nodes, clamp["nodes"] = _clamp(max_nodes, 1, HARD["nodes"])
        max_edges, clamp["edges"] = _clamp(max_edges, 1, HARD["edges"])
        max_hops, clamp["hops"] = _clamp(max_hops, 0, HARD["hops"])
        deadline_ms, clamp["deadline_ms"] = _clamp(deadline_ms, 1, HARD["deadline_ms"])
        max_paths, clamp["paths"] = _clamp(max_paths, 1, HARD["paths"])
        # 未显式给 hops ⇒ ego 用 1 跳、path 用 DEFAULT_PATH_HOPS（再受 max_hops 约束）
        req_hops = hops
        explicit_hops = hops is not None
        if hops is None:
            hops = DEFAULT_PATH_HOPS if mode == "path" else 1
        hop_clamped = bool(explicit_hops and int(hops) > max_hops)
        hops = max(0, min(int(hops), max_hops))

        nt, sub = _norm_set(node_types), _norm_set(subjects)
        if (nt or sub) and not (node_type_of and subject_of_fn):
            raise ValueError("按 node_types/subjects 过滤需调用方提供 node_type_of / subject_of")
        f = {"types": _norm_set(types), "kinds": _norm_set(kinds),
             "min_confidence": max(0.0, float(min_confidence or 0.0)),
             "exclude_model_inferred": bool(exclude_model_inferred),
             "exclude_stale": bool(exclude_stale),
             "stale_sources": stale_sources or {}}

        def node_ok(nid: str) -> bool:
            n = nodes_by_id.get(nid)
            if n is None:
                return False
            if nt and (node_type_of(n["labels"]) if node_type_of else "Unknown") not in nt:
                return False
            if sub and (subject_of_fn(n["props"].get("domain"), nid) if subject_of_fn else "") not in sub:
                return False
            return True

        why_nodes: Dict[str, str] = {}
        why_edges: Dict[str, str] = {}
        # ★ Phase 49：仅 `mode=path` 非空。每项 = 路径上**按序**的节点 id 与边 id，
        #   与 legacy `/api/path` 的 `paths` 形状一致（都是 `{"nodes":[…], "edges":[…]}`）。
        path_chains: List[dict] = []
        # ★ Phase 50（D45）：`hidden_by_trust` ＝ **被可信档位排除的边的「不同」边数**，
        #   故必须用 **set** 去重，不能让遍历过程的判定次数充数。
        #   实测教训：`mode=path` 是"路径 BFS"，同一个被拒边会在**每条部分路径**上被
        #   重复检查（单条边最多 601 次）⇒ 用计数器会把 11,660 条边报成 62,640 条，
        #   甚至**超过全图边总数 48,712**，且随 deadline 波动（同一查询两次不同值）。
        #   ⇒ 对外计数必须等于"被排除对象集合"的基数（铁律 #12：计数≠内容）。
        hidden_edges: set = set()
        truncated_by = None
        pre_truncation = None
        pending = 0
        reasons: List[str] = []
        seed_list = _norm_seeds(seeds)
        missing_seeds = [s for s in seed_list if s not in nodes_by_id]
        present_seeds = [s for s in seed_list if s in nodes_by_id]

        def _mark_hidden(e) -> int:
            """记下**被可信档位排除**的边（按边 id 去重）；返回值恒 0 以适配 `edge_ok` 协议。"""
            hidden_edges.add(e["id"])
            return 0

        if mode == "path":
            if len(present_seeds) < 2:
                return self._envelope(NO_RESULT,
                                      "path 模式需要 2 个存在的种子（起点/终点），"
                                      "收到 %d 个存在 / %d 个缺失" % (len(present_seeds), len(missing_seeds)),
                                      {}, {},
                                      query={"mode": mode, "seeds": seed_list},
                                      stats={"missing_seeds": missing_seeds,
                                             "elapsed_ms": _ms(t0)})
            a, b = present_seeds[0], present_seeds[1]

            def path_edge_ok(e) -> bool:
                if not node_ok(e["source"]) or not node_ok(e["target"]):
                    return False
                if not self._edge_ok(e, f):
                    return False
                if not self._trust_ok(e, trust):
                    _mark_hidden(e)
                    return False
                return True

            pr = self._find_paths(a, b, max_paths, hops, deadline_ms,
                                  edge_ok=path_edge_ok, t0=t0)
            if pr["missing"]:
                return self._envelope(NO_RESULT, "节点不存在：%s" % ",".join(pr["missing"]),
                                      {}, {}, query={"mode": mode, "seeds": seed_list},
                                      stats={"missing_seeds": pr["missing"],
                                             "elapsed_ms": _ms(t0)})
            if pr["timed_out"]:
                truncated_by = "deadline"
                reasons.append("超过 deadline_ms=%d" % deadline_ms)
            if not pr["paths"]:
                return self._envelope(NO_RESULT,
                                      "在 %d 跳内没有满足过滤条件的路径（a=%s, b=%s）" % (hops, a, b),
                                      {}, {}, query={"mode": mode, "seeds": seed_list},
                                      stats={"missing_seeds": missing_seeds, "hops": hops,
                                             "hidden_by_trust": len(hidden_edges),
                                             "elapsed_ms": _ms(t0)})
            for p in pr["paths"]:
                for nid in p["nodes"]:
                    why_nodes[nid] = WHY_EVIDENCE
                for e in p["edges"]:
                    why_edges[e["id"]] = WHY_EVIDENCE
            # ★ Phase 49：把路径链原样带出（只取 id，不复制整条边字典 ⇒ 响应不被放大）。
            path_chains = [{"nodes": list(p["nodes"]),
                            "edges": [e["id"] for e in p["edges"]]}
                           for p in pr["paths"]]
            if pr["truncated"]:
                truncated_by = truncated_by or "paths"
                reasons.append("已达 max_paths=%d" % max_paths)
            pending = pr["pending"]

        elif mode == "filter":
            if not (f["types"] or f["kinds"] or f["min_confidence"] > 0.0
                    or f["exclude_model_inferred"] or f["exclude_stale"] or nt or sub
                    or trust != TRUST_ALL):
                raise ValueError("filter 模式至少需要一个过滤条件"
                                 "（types/kinds/min_confidence/node_types/subjects/trust/…）")
            matched_nodes, matched_edges = set(), []
            scan_timed_out = False
            i = -1
            for i, e in enumerate(idx["edges"]):
                if (i & 2047) == 0 and _expired(t0, deadline_ms):
                    scan_timed_out = True
                    break
                if not self._edge_ok(e, f):
                    continue
                if not self._trust_ok(e, trust):
                    hidden_edges.add(e["id"])
                    continue
                if not (node_ok(e["source"]) and node_ok(e["target"])):
                    continue
                matched_edges.append(e)
                matched_nodes.add(e["source"])
                matched_nodes.add(e["target"])
            if scan_timed_out:
                truncated_by = "deadline"
                reasons.append("全量扫描超过 deadline_ms=%d（计数不完整）" % deadline_ms)
            else:
                # 实有计数（全量扫描 ⇒ 可如实给出「实有 Y」，方案 §5.2）
                pre_truncation = {"n_nodes": len(matched_nodes), "n_edges": len(matched_edges)}
            if not matched_edges:
                if scan_timed_out:
                    return self._envelope(
                        TIMEOUT,
                        "全量扫描超过 deadline_ms=%d，未取到任何结果（冷启动索引构建也计入超时）"
                        % deadline_ms,
                        {}, {}, query={"mode": mode},
                        stats={"hidden_by_trust": len(hidden_edges), "truncated_by": "deadline",
                               "pre_truncation": None, "elapsed_ms": _ms(t0)})
                return self._envelope(NO_RESULT,
                                      "过滤后没有边（已扫描 %d 条，hidden_by_trust=%d）"
                                      % (i + 1, len(hidden_edges)),
                                      {}, {}, query={"mode": mode}, stats={
                                          "hidden_by_trust": len(hidden_edges),
                                          "pre_truncation": pre_truncation,
                                          "truncated_by": None,
                                          "elapsed_ms": _ms(t0)})
            edge_sel = matched_edges if scan_timed_out else matched_edges[:max_edges]
            edges_cut = len(matched_edges) > len(edge_sel)
            # 节点预算：按**子图内度数**降序取（确定性 tie-break=id）⇒ 保留的是"有边的"节点，
            # 避免按 id 截断后induced 边全被切掉（实测过：50 节点 → 0 边）
            deg = {}
            for e in edge_sel:
                deg[e["source"]] = deg.get(e["source"], 0) + 1
                deg[e["target"]] = deg.get(e["target"], 0) + 1
            node_ids = sorted(deg, key=lambda x: (-deg[x], x))
            if len(node_ids) > max_nodes:
                truncated_by = truncated_by or "nodes"
                keep = set(node_ids[:max_nodes])
                edge_sel = [e for e in edge_sel if e["source"] in keep and e["target"] in keep]
                for nid in keep:
                    why_nodes[nid] = WHY_USER
            else:
                for nid in node_ids:
                    why_nodes[nid] = WHY_USER
                if edges_cut:
                    truncated_by = truncated_by or "edges"
            for e in edge_sel:
                why_edges[e["id"]] = WHY_USER
            if truncated_by and truncated_by != "deadline":
                reasons.append("已达 %s 预算（max_nodes=%d / max_edges=%d）"
                               % ("节点" if truncated_by == "nodes" else "边",
                                  max_nodes, max_edges))

        else:  # ego
            if not present_seeds:
                return self._envelope(
                    NO_RESULT,
                    "种子节点不存在：%s" % ",".join(seed_list) if seed_list else "缺少参数 seeds",
                    {}, {}, query={"mode": mode, "seeds": seed_list},
                    stats={"missing_seeds": missing_seeds, "elapsed_ms": _ms(t0)})
            for s in present_seeds:
                why_nodes[s] = WHY_USER
            from collections import deque
            queue = deque((s, 0) for s in sorted(present_seeds))
            while queue:
                if _expired(t0, deadline_ms):
                    truncated_by = truncated_by or "deadline"
                    reasons.append("超过 deadline_ms=%d" % deadline_ms)
                    pending = len(queue)
                    break
                nid, d = queue.popleft()
                if d >= hops:
                    continue
                for e, other, _direction in idx["adj"].get(nid, []):
                    if e["id"] in why_edges:
                        continue
                    if not self._edge_ok(e, f):
                        continue
                    if not self._trust_ok(e, trust):
                        hidden_edges.add(e["id"])
                        continue
                    if not node_ok(other):
                        continue
                    if len(why_edges) >= max_edges:
                        truncated_by = truncated_by or "edges"
                        reasons.append("已达边预算 max_edges=%d" % max_edges)
                        pending = len(queue) + 1
                        queue.clear()
                        break
                    if other not in why_nodes and len(why_nodes) >= max_nodes:
                        truncated_by = truncated_by or "nodes"
                        reasons.append("已达节点预算 max_nodes=%d" % max_nodes)
                        pending = len(queue) + 1
                        queue.clear()
                        break
                    why_edges[e["id"]] = WHY_CONTEXT if d == 0 else WHY_EXPANSION
                    if other not in why_nodes:
                        why_nodes[other] = WHY_CONTEXT if d == 0 else WHY_EXPANSION
                        queue.append((other, d + 1))
            if truncated_by is None and hop_clamped and hops == max_hops:
                truncated_by = "hops"
                reasons.append("请求跳数被夹紧至 max_hops=%d" % max_hops)

        nodes_out = [nodes_by_id[nid] for nid in sorted(why_nodes) if nid in nodes_by_id]
        edges_out = sorted((e for e in idx["edges"] if e["id"] in why_edges),
                           key=lambda x: x["id"])

        status = TRUNCATED if truncated_by else OK
        if truncated_by == "deadline":
            status = TIMEOUT
        expandable = self._expandable(idx, why_nodes, f, trust, node_ok, cap=40,
                                      t0=t0, deadline_ms=deadline_ms)
        stats = {
            "n_nodes": len(nodes_out), "n_edges": len(edges_out),
            "truncated_by": truncated_by, "pre_truncation": pre_truncation,
            "pending_frontier": pending,
            "pending_frontier_note": "下界：中断时已知但未展开的节点数（ego 模式无法给出全量实有）",
            "hidden_by_trust": len(hidden_edges),
            "missing_seeds": missing_seeds,
            "hops": hops, "requested_hops": req_hops,
            "clamped": {k: v for k, v in clamp.items() if v},
            "elapsed_ms": _ms(t0),
        }
        # ★ A04 · 验证依赖状态（Phase 45）：本次可见子图内的**分列**计数。
        #   与 `evidence_fidelity` 并列但**粒度不同**：前者是**边级字节**忠实度，
        #   本项是**证据级依赖**状态（参考表 / 验证器 / 依赖版本 / 命题被否决 / 哈希来路不明）。
        #   无 A04 记录 ⇒ 该键**不出现**（不可判定不假装全 valid）。
        st_counts = evidence_status_counts(edges_out, f.get("stale_sources"))
        if st_counts is not None:
            stats["evidence_status"] = st_counts
            stats["evidence_status_note"] = (
                "A04 验证依赖状态（边级聚合，多证据取最坏）。"
                "needs_review = 本环境**不可判定**（缺数据本身，≠ 已变更）⇒ 不计入 exclude_stale；"
                "no_record = 该图上无 A04 记录（缺记录 ≠ 已验证）。")
        return self._envelope(status, "；".join(reasons), why_nodes, why_edges,
                              nodes=nodes_out, edges=edges_out, expandable=expandable,
                              # ★ Phase 49：`mode=path` 时把**路径链**一并给出。
                              #   以前只给 nodes/edges 的并集，前端拿不到「有哪几条路径」⇒
                              #   子图画得出来、路径清单却是空的（"后端给了、UI 回吞"同族）。
                              paths=path_chains,
                              query={"mode": mode, "seeds": seed_list, "trust": trust,
                                     "types": sorted(f["types"]) if f["types"] else None,
                                     "kinds": sorted(f["kinds"]) if f["kinds"] else None,
                                     "min_confidence": f["min_confidence"],
                                     "exclude_model_inferred": f["exclude_model_inferred"],
                                     "exclude_stale": f["exclude_stale"],
                                     "max_nodes": max_nodes, "max_edges": max_edges,
                                     "max_hops": max_hops, "deadline_ms": deadline_ms},
                              stats=stats)

    @staticmethod
    def _expandable(idx, why_nodes, f, trust, node_ok, cap=40,
                    t0: Optional[float] = None, deadline_ms: Optional[int] = None) -> List[dict]:
        """可展开节点：过滤后**尚未并入**的邻居数（服务端据此签发 token，§5.2.1）。"""
        out = []
        for nid in sorted(why_nodes):
            if t0 is not None and deadline_ms is not None and _expired(t0, deadline_ms):
                break
            if not node_ok(nid):
                continue
            n_new = 0
            for e, other, _direction in idx["adj"].get(nid, []):
                if other in why_nodes:
                    continue
                if not node_ok(other):
                    continue
                if not Backend._edge_ok(e, f) or not Backend._trust_ok(e, trust):
                    continue
                n_new += 1
            if n_new:
                out.append({"node_id": nid, "estimated_neighbors": n_new})
        out.sort(key=lambda x: (-x["estimated_neighbors"], x["node_id"]))
        return out[:cap]

    @staticmethod
    def _envelope(status, reason, why_nodes, why_edges, nodes=None, edges=None,
                  expandable=None, query=None, stats=None, error_code=None,
                  paths=None) -> dict:
        """统一信封。`error_code` 与五态**共存**：五态管"结果类别"，error_code 管"原因"。

        `paths`：仅 `mode=path` 非空（Phase 49）；其余模式恒为 `[]` ⇒
        前端可无条件读 `env.paths.length`，不需要按模式分支（少一个静默错位点）。
        """
        if error_code is None:
            error_code = {TRUNCATED: "SUBGRAPH_LIMIT_REACHED",
                          TIMEOUT: "GRAPH_QUERY_TIMEOUT"}.get(status)
        return {
            "status": status,
            "reason": reason or "",
            "error_code": error_code,
            "nodes": nodes or [],
            "edges": edges or [],
            "paths": paths or [],
            "why_nodes": why_nodes or {},
            "why_edges": why_edges or {},
            "expandable": expandable or [],
            "query": query or {},
            "stats": stats or {},
        }

    # ---------------- B. 概览（替代"全图浏览"） ----------------
    def overview(self, mode: str = "aggregate", k: int = 300,
                 node_type_of: Optional[Callable] = None,
                 subject_of_fn: Optional[Callable] = None) -> dict:
        """聚合元图：`学科 × 类型` 节点 + 组合间边（权重 = 边数）。

        `mode=sample&k=N` 返回**度优先抽样骨架**（top-K 度节点及其相互边）。
        响应固定带 `note`：**概览，非全图**（铁律 #66）。
        """
        t0 = time.monotonic()
        idx = self._index()

        def ntype(nid):
            n = idx["nodes"].get(nid) or {}
            return node_type_of(n.get("labels")) if node_type_of else \
                ((n.get("labels") or ["Unknown"])[0])

        def nsubj(nid):
            n = idx["nodes"].get(nid) or {}
            return subject_of_fn((n.get("props") or {}).get("domain"), nid) if subject_of_fn else ""

        if mode == "sample":
            k, _ = _clamp(k, 10, HARD["nodes"])
            deg = {}
            for nid in idx["nodes"]:
                deg[nid] = len(idx["adj"].get(nid, []))
            top = sorted(deg, key=lambda x: (-deg[x], x))[:k]
            keep = set(top)
            nodes = [{"id": nid, "subject": nsubj(nid), "type": ntype(nid), "degree": deg[nid]}
                     for nid in top]
            edges = []
            for e in idx["edges"]:
                if e["source"] in keep and e["target"] in keep:
                    edges.append({"source": e["source"], "target": e["target"],
                                  "type": e["type"], "kind": e["kind"]})
            return {"status": OK, "note": "概览（抽样骨架，非全图）", "mode": "sample",
                    "k": k, "nodes": nodes, "edges": edges,
                    "stats": {"n_nodes": len(nodes), "n_edges": len(edges),
                              "full_nodes": len(idx["nodes"]), "full_edges": len(idx["edges"]),
                              "elapsed_ms": _ms(t0)}}

        gnode, gedge = {}, {}
        for nid in idx["nodes"]:
            key = (nsubj(nid), ntype(nid))
            gnode.setdefault(key, 0)
            gnode[key] += 1
        for e in idx["edges"]:
            a, b = (nsubj(e["source"]), ntype(e["source"])), (nsubj(e["target"]), ntype(e["target"]))
            if a == b:
                continue
            kk = tuple(sorted([a, b]))
            gedge[kk] = gedge.get(kk, 0) + 1
        nodes = [{"id": "%s::%s" % (s, t), "subject": s, "type": t, "n_nodes": c}
                 for (s, t), c in sorted(gnode.items())]
        edges = [{"source": "%s::%s" % (a[0], a[1]), "target": "%s::%s" % (b[0], b[1]),
                  "weight": w, "type": "aggregate"}
                 for (a, b), w in sorted(gedge.items(), key=lambda kv: (-kv[1], kv[0]))]
        return {"status": OK, "note": "概览（学科×类型聚合元图，非全图）", "mode": "aggregate",
                "nodes": nodes, "edges": edges,
                "stats": {"n_nodes": len(nodes), "n_edges": len(edges),
                          "full_nodes": len(idx["nodes"]), "full_edges": len(idx["edges"]),
                          "elapsed_ms": _ms(t0)}}


# ----------------------------------------------------------------------------
# NetworkX 兜底后端（只提供原语；查询逻辑全在基类）
# ----------------------------------------------------------------------------

class NXBackend(Backend):
    """把归一化 JSON 载入 nx.MultiDiGraph，供 `all_nodes` / `all_edges` 原语使用。

    `G` 仍保留（可视化统计 `graph_stats()` 直接用它遍历），但**查询语义**已统一到
    基类的索引实现 ⇒ 与 Neo4j 后端不存在第二份逻辑。
    """

    name = "networkx"

    def __init__(self, path: str = DEFAULT_INPUT):
        import json
        import networkx as nx  # 仅兜底路径需要的依赖
        self.path = os.path.abspath(path)
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._raw_nodes = data.get("nodes", [])
        self._raw_edges = data.get("edges", [])
        self.G = nx.MultiDiGraph()
        for n in self._raw_nodes:
            attrs = dict(n.get("props") or {})
            attrs["labels"] = n.get("labels", [])
            self.G.add_node(n["id"], **attrs)
        for e in self._raw_edges:
            attrs = dict(e.get("props") or {})
            attrs["type"] = e["type"]
            attrs["kind"] = e.get("kind", attrs.get("kind", ""))
            self.G.add_edge(e["source"], e["target"], key=e["id"], **attrs)

    def source_identity(self):
        return ("file", self.path)

    def all_nodes(self) -> List[dict]:
        return [{"id": n["id"], "labels": list(n.get("labels") or []),
                 "props": dict(n.get("props") or {})} for n in self._raw_nodes]

    def all_edges(self) -> List[dict]:
        return [{"id": e.get("id"), "source": e.get("source"), "target": e.get("target"),
                 "type": e.get("type"), "kind": e.get("kind"),
                 "props": dict(e.get("props") or {})} for e in self._raw_edges]


# ----------------------------------------------------------------------------
# Neo4j 后端
# ----------------------------------------------------------------------------

class Neo4jBackend(Backend):
    """通过 neo4j 驱动执行 Cypher。

    ★ Phase 43：本后端**只提供两条原语**（`all_nodes` / `all_edges`）——
    一次拉全库到内存（**正确性优先，性能未优化**）。子图/搜索/概览的逻辑与
    NetworkX 后端**完全同一份实现**，杜绝"双实现语义漂移"（方案 §11 风险表）。
    生产化需要把过滤下推为 Cypher 时，须**另起方案**并用同一份契约测试验收。
    """

    name = "neo4j"

    def __init__(self, uri: str, user: str, password: str,
                 database: Optional[str] = None):
        from neo4j import GraphDatabase
        self.uri = uri
        self.database = database
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.driver.verify_connectivity()

    def _run(self, qname: str, **params) -> List[dict]:
        with self.driver.session(database=self.database) as s:
            return [dict(r) for r in s.run(CYPHER[qname], **params)]

    def source_identity(self):
        return ("neo4j", "%s|%s" % (self.uri, self.database))

    def all_nodes(self) -> List[dict]:
        return [{"id": r["id"], "labels": list(r.get("labels") or []),
                 "props": dict(r.get("props") or {})} for r in self._run("all_nodes")]

    def all_edges(self) -> List[dict]:
        out = []
        for r in self._run("all_edges"):
            props = dict(r.get("props") or {})
            out.append({"id": r.get("id"), "source": r.get("source"), "target": r.get("target"),
                        "type": r.get("type"), "kind": r.get("kind") or props.get("kind") or "",
                        "props": props})
        return out

    def get_node(self, node_id: str) -> Optional[dict]:
        rows = self._run("get_node", id=node_id)
        return rows[0] if rows else None

    def get_neighbors(self, node_id: str) -> List[dict]:
        return self._run("get_neighbors", id=node_id)

    def subgraph_by_confidence(self, threshold: float) -> Dict[str, list]:
        rows = self._run("subgraph_by_confidence", threshold=threshold)
        nodes = sorted({r["start"] for r in rows} | {r["end"] for r in rows})
        return {"nodes": nodes, "edges": rows}

    def paths_between(self, a: str, b: str, cutoff: int = 5) -> List[list]:
        return [r["path"] for r in self._run("paths_between", a=a, b=b)]

    def list_by_type(self, t: str) -> List[dict]:
        return self._run("list_by_type", t=t)

    def filter_edges_by_kind(self, kind: str) -> List[dict]:
        return self._run("filter_edges_by_kind", kind=kind)

    def close(self) -> None:
        try:
            self.driver.close()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# 后端工厂
# ----------------------------------------------------------------------------

def get_backend(which: str = "auto", uri: str = "bolt://localhost:7687",
                user: str = "neo4j", password: str = "neo4j",
                database: Optional[str] = None,
                input_path: str = DEFAULT_INPUT) -> Backend:
    """按需返回后端：auto 优先 Neo4j，不可用则回退 NetworkX。"""
    if which in ("auto", "neo4j"):
        try:
            return Neo4jBackend(uri, user, password, database)
        except Exception as e:
            if which == "neo4j":
                print(f"[WARN] Neo4j 不可用（{e}），仍尝试 NetworkX 兜底。")
            else:
                print(f"[提示] 未检测到可用 Neo4j（{type(e).__name__}），"
                      f"回退 NetworkX 兜底。")
    # 兜底
    try:
        return NXBackend(input_path)
    except FileNotFoundError:
        print(f"[SKIP] 未找到 {input_path}，请先运行：python etl_pipeline.py")
        raise SystemExit(0)
    except ImportError:
        print("[SKIP] 缺少 networkx，无法使用兜底后端。pip install networkx")
        raise SystemExit(0)


# ----------------------------------------------------------------------------
# CLI 示例
# ----------------------------------------------------------------------------

def _demo(b: Backend) -> None:
    print(f"\n后端：{b.name}")
    print(f"图规模：节点 {b.node_count} / 边 {b.edge_count}  ·  snapshot_id={b.snapshot_id()}")

    print("\n--- 1) get_node('MX:thm:gauss_bonnet') ---")
    print(b.get_node("MX:thm:gauss_bonnet"))

    print("\n--- 2) get_neighbors('MX:def:levi_civita') ---")
    for nb in b.get_neighbors("MX:def:levi_civita"):
        print(f"    {nb['direction']:>3}  {nb['type']:<12} -> {nb['id']} "
              f"(kind={nb.get('kind')}, conf={nb.get('confidence')})")

    print("\n--- 3) subgraph_by_confidence(0.95) ---")
    sg = b.subgraph_by_confidence(0.95)
    print(f"    节点 {len(sg['nodes'])} 个，边 {len(sg['edges'])} 条")
    for e in sg["edges"][:6]:
        print(f"    {e['start']} -[{e['type']}]-> {e['end']} conf={e['confidence']}")

    print("\n--- 4) paths_between_bounded('MX:thm:gauss_bonnet', 'MX:def:manifold') ---")
    pr = b.paths_between_bounded("MX:thm:gauss_bonnet", "MX:def:manifold")
    print(f"    status={pr['status']}  paths={len(pr['paths'])}  "
          f"truncated={pr['truncated']}  elapsed={pr['elapsed_ms']}ms")
    for p in pr["paths"][:3]:
        print("    " + " -> ".join(p))

    print("\n--- 5) list_by_type('Symbol')（前 5）---")
    for n in b.list_by_type("Symbol")[:5]:
        print(f"    {n['id']}  labels={n.get('labels')}")

    print("\n--- 6) filter_edges_by_kind('llm_inferred') ---")
    for e in b.filter_edges_by_kind("llm_inferred"):
        print(f"    {e['start']} -[{e['type']}]-> {e['end']} conf={e['confidence']}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="公式知识图谱查询层（Neo4j + NetworkX 兜底）")
    ap.add_argument("--backend", default="auto", choices=["auto", "neo4j", "nx"])
    ap.add_argument("--uri", default=os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    ap.add_argument("--user", default=os.environ.get("NEO4J_USER", "neo4j"))
    ap.add_argument("--password", default=os.environ.get("NEO4J_PASSWORD", "neo4j"))
    ap.add_argument("--database", default=os.environ.get("NEO4J_DATABASE", None))
    ap.add_argument("--input", default=DEFAULT_INPUT)
    args = ap.parse_args(argv)

    which = "nx" if args.backend == "nx" else args.backend
    b = get_backend(which, args.uri, args.user, args.password,
                    args.database, args.input)
    try:
        _demo(b)
    finally:
        b.close()
    print("\n[Done] 查询示例执行完毕。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
