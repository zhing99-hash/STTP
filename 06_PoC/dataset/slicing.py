# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""STTP 数据集切片导出（Phase 78 · F8）—— 按学科/来源/验证等级切子集，且**可对账**。

为什么要单独起一个模块
----------------------
F5（Phase 70/71）的 `export_dataset.py` 只做**全量**包：
`build_into(src, profile, out)` 一次性把整张图投影成一个包，无法只要其中一块。
F8 要的是「按某个维度切一片出去」。若在 `export_dataset.py` 里加分支，会让
「全量构建」与「子集构建」两套口径互相污染（D102 同族：两份实现一起漂移）。

★ 动工前的量化侦察得到三条硬事实（详见 PROJECT_DEVELOPMENT_GUIDE.md §0.3.36，
  权威图 9,750 节点 / 48,813 边，全部现算）：

  1. `props.domain` **不是学科轴** —— 它与节点类型高度相关
     （`physics.*` 里 4,721/4,790 是 Formula；`phys.*` 是量纲实体）。
     ⇒ 照字面实现「按学科导出」，使用者拿到的其实是「按节点类型导出」。
  2. `domain` 有**同义异写**：`chem`(4,429) 与 `chemistry`(9) 并存
     ⇒ 必须**显式映射表**；按前缀自动推断会把它们当成两个学科，且不报错。
  3. 边的两端**常分属不同学科**：细粒度口径跨界 90.55%，顶层前缀未归一化
     51.96%，**归一化后 5.32%**。
     ⇒ 边的归属规则必须写死，否则切片会静默丢掉一半的边。

★ 三条命名纪律（本模块统一，避免同名异义）：
  · `src` / `tgt`　　：边的**拓扑端点**（节点 id）
  · `origin`　　　　 ：**数据来源**（mathxiv / ChEBI / PhysicsBabel …）
  · `basis`　　　　　：等级口径 ∈ {internal_e1, public_p1}
  （规范图里节点与边的 `props.source` 都是「数据来源」，而边**顶层**的
   `source` 是**起点节点 id** —— 两者同名异义，极易写错。）

判据（本文件 `--selfcheck`，合成图，秒级，**不依赖权威图**）
------------------------------------------------------------
  S70 学科守恒：Σ 各学科切片命中节点 + unclassified == 全图节点数
  S71 边守恒　：Σ 各学科切片边数 == 全图边数（每条边恰好归属一片）
  S72 片内自洽：每片 internal + crossing == 该片边数
  S73 同义异写合并：`chem` 与 `chemistry` 归入同一学科桶，不各立一片
  S74 缺失归入：无 `props.domain` 的节点落 unclassified，且**计入**守恒
  S75 映射完备：出现的 domain 前缀**全部**在映射表内（出现未登记 ⇒ 必须红）
  S76 来源守恒：Σ 各来源切片命中节点 == 全图节点数
  S77 口径必选：grade 维度**不指定** basis ⇒ 报错（不默认，防「对外分其实是内部分」）
  S78 空片分说：「谓词合法但无匹配」与「谓词本身不成立」必须给出**不同**的
       status / error_code 组合（D115 同族：四句空话不能合并成一句）

  S79 谓词契约：bool 谓词放行、三态字符串必须被抓（D123）
  S87 版本确定性：同输入 ⇒ 同 `slice_dataset_id`（跑两次逐字符相同）
  S88 谓词入指纹：只改切片谓词（同数据）⇒ 版本号**必须变**（否则两片撞号）
  S89 parent 可追溯：切片版本里必须能读出「从哪版全量切的」；无 parent ⇒ 抛错
  S90 落盘幂等：同输入重复导出 ⇒ 台账**不追加**、文件逐字节相同（E29 家族）
  S91 落盘一致：盘上 JSONL 的行集合 == 内存切片的 id 集合（不多不少）
  S92 台账无原机路径：`slices.jsonl` 是**入库**文件，不得含绝对路径 / 盘符 

  ★ 三条守恒分开报而**不合并**：S70 挂是「分桶漏了」，S71 挂是「边归属规则错了」，
    S72 挂是「计数位置错了」—— 三种修法完全不同，合成一条就只会看到「守恒红」。
"""

import collections
import io
import json
import os
import sys

# ---------------------------------------------------------------- 常量

#: 学科归一化映射表版本。改映射 ⇒ **必须**升此版本（与 GRADE_RULE_VERSION 同族）。
DOMAIN_MAP_VERSION = "d1"

#: 顶层前缀 → 学科。★ **显式**表：同义异写（`chem`/`chemistry`）只能靠人认。
DOMAIN_PREFIX_MAP = {
    "physics": "physics",
    "phys": "physics",      # 量纲实体（quantity/unit/symbol），学科上仍属 physics
    "chem": "chemistry",
    "chemistry": "chemistry",
    "math": "math",
    "biology": "biology",
    "cross": "cross",       # 明确跨界，不归入任何单一学科
}

#: 无法归入任何学科的兜底桶（含 `props.domain` 缺失）。
UNCLASSIFIED = "unclassified"

#: 支持的切片维度。
DIMENSIONS = ("source", "domain", "grade")

#: 等级口径。★ 双口径**永不合并**（铁律 #90）。
GRADE_BASIS = ("internal_e1", "public_p1")

#: 空切片的三种说法（D115 同族：四句空话不能合并成一句）。
REASON_NO_MATCH = "no_match"                 # 谓词合法，但无实体满足
REASON_LICENSE_EXCLUDED = "license_excluded" # 有匹配，但被当前 profile 剔除
REASON_BAD_DIMENSION = "bad_dimension"       # 维度不存在 / 取值不合法


class SliceError(ValueError):
    """切片参数非法（对应 HTTP 400）。"""


# ---------------------------------------------------------------- 学科归一化

def normalize_domain(d, unmapped=None):
    """细粒度 domain → 顶层学科。

    `unmapped`：可选 `set`，收集**未登记**的顶层前缀（供 S74 判据检查）。
    ★ 为什么不抛异常而是记入集合：未登记前缀是**数据**问题，
      让整包构建崩掉会让使用者无从下手；但**必须**被判据看见。
    """
    if not isinstance(d, str) or not d.strip():
        return UNCLASSIFIED
    top = d.strip().split(".")[0]
    v = DOMAIN_PREFIX_MAP.get(top)
    if v is None:
        if unmapped is not None:
            unmapped.add(top)
        return UNCLASSIFIED
    return v


def domain_of_node(n, unmapped=None):
    return normalize_domain(((n.get("props") or {}).get("domain")), unmapped)


def origin_of_node(n):
    return (n.get("props") or {}).get("source")


def origin_of_edge(e):
    return (e.get("props") or {}).get("source")


# ---------------------------------------------------------------- 切片

def _mk(dim, value, basis, matched_nodes, matched_edges, attached_nodes,
        internal, crossing, status="ok", error_code=None, reason=None):
    return {
        "dim": dim,
        "value": value,
        "basis": basis,
        "status": status,                 # ok / no_result（业务态 ⇒ error_code 恒 null）
        "error_code": error_code,
        "reason": reason,
        "counts": {
            "nodes_matched": len(matched_nodes),
            "nodes_attached": len(attached_nodes),
            "nodes_total": len(set(matched_nodes) | set(attached_nodes)),
            "edges_matched": len(matched_edges),
            "edges_internal": internal,
            "edges_crossing": crossing,
        },
        "node_ids": sorted(set(matched_nodes) | set(attached_nodes)),
        "edge_ids": sorted(matched_edges),
    }


def slice_by_source(nodes, edges, value):
    """按**数据来源**切。

    节点按 `props.source` 命中；边按**自己的** `props.source` 命中
    （★ 不用端点推断 —— 边的来源与端点的来源是两件事）。
    """
    m_nodes = [n["id"] for n in nodes if origin_of_node(n) == value]
    m_edges, internal, crossing = [], 0, 0
    nid = set(m_nodes)
    for e in edges:
        if origin_of_edge(e) != value:
            continue
        m_edges.append(e.get("id"))
        hs, ht = e.get("source") in nid, e.get("target") in nid
        if hs and ht:
            internal += 1
        else:
            crossing += 1
    attached = set()
    for e in edges:
        if origin_of_edge(e) != value:
            continue
        if e.get("source") in nid and e.get("target") in nid:
            continue
        for k in (e.get("source"), e.get("target")):
            if k not in nid:
                attached.add(k)
    st = "ok" if (m_nodes or m_edges) else "ok"
    return _mk("source", value, None, m_nodes, m_edges, attached, internal, crossing,
               status=st, error_code=None,
               reason=None if (m_nodes or m_edges) else REASON_NO_MATCH)


def slice_by_domain(nodes, edges, value, basis=None):
    """按**学科**切（节点维度）。

    节点按归一化后的顶层学科命中；边按**起点端点的学科**归属
    （★ 确定性规则，保证每条边在全集内**恰好归属一片** ⇒ 可对账）。
    """
    unmapped = set()
    dom = {n["id"]: domain_of_node(n, unmapped) for n in nodes}
    m_nodes = [i for i, d in dom.items() if d == value]
    nid = set(m_nodes)
    m_edges, internal, crossing, attached = [], 0, 0, set()
    for e in edges:
        if dom.get(e.get("source")) != value:
            continue
        m_edges.append(e.get("id"))
        if dom.get(e.get("target")) == value:
            internal += 1
        else:
            crossing += 1
            if e.get("target") not in nid:
                attached.add(e.get("target"))
    return _mk("domain", value, basis, m_nodes, m_edges, attached, internal, crossing,
               status="ok", error_code=None,
               reason=None if (m_nodes or m_edges) else REASON_NO_MATCH)


def slice_by_grade(nodes, edges, min_grade, basis, grade_of):
    """按**验证等级**切（边维度）。

    `grade_of`：可调用对象 `edge -> Optional[int]`，由调用方注入
    （与 `node_type_of` / `redist_of` 同例，03/11 层不反向 import）。
    `basis`：**必填**，不指定 ⇒ `SliceError`（防「对外分其实是内部分」）。
    """
    if basis not in GRADE_BASIS:
        raise SliceError("grade 维度必须指定 basis ∈ %s" % (list(GRADE_BASIS),))
    if not isinstance(min_grade, int) or not 1 <= min_grade <= 5:
        raise SliceError("min_grade 必须是 1..5 的整数")
    m_edges, attached = [], set()
    for e in edges:
        g = grade_of(e)
        if g is None or g < min_grade:
            continue
        m_edges.append(e.get("id"))
        attached.add(e.get("source"))
        attached.add(e.get("target"))
    # 边维度下「命中节点」= 端点集合；无独立匹配概念 ⇒ matched_nodes 为空
    internal = crossing = 0
    for e in edges:
        g = grade_of(e)
        if g is None or g < min_grade:
            continue
        if (e.get("source") in attached) and (e.get("target") in attached):
            internal += 1
    crossing = len(m_edges) - internal
    return _mk("grade", ">=%d" % min_grade, basis, [], m_edges, attached,
               internal, crossing, status="ok", error_code=None,
               reason=None if m_edges else REASON_NO_MATCH)


# ---------------------------------------------------------------- 驱动

def _check_dim(dim):
    if dim not in DIMENSIONS:
        raise SliceError("未知切片维度 %r（可选：%s）" % (dim, list(DIMENSIONS)))


def build_slices(nodes, edges, dim, basis=None, *, grade_of=None, **kw):
    """按维度切成**全部**切片（不是切一片）—— 只有切全了才谈得上对账。

    返回 `{"dim", "rule_version", "basis", "total", "slices", "reconcile"}`。
    """
    _check_dim(dim)
    if dim == "grade":
        if basis not in GRADE_BASIS:
            raise SliceError("grade 维度必须指定 basis ∈ %s" % (list(GRADE_BASIS),))
        if grade_of is None:
            raise SliceError("grade 维度必须注入 grade_of")
        vals = [">=%d" % g for g in range(1, 6)]
        sl = [slice_by_grade(nodes, edges, g, basis, grade_of) for g in range(1, 6)]
    else:
        if dim == "source":
            vals = sorted({origin_of_node(n) for n in nodes} |
                          {origin_of_edge(e) for e in edges}, key=str)
            sl = [slice_by_source(nodes, edges, v) for v in vals]
        else:
            vals = sorted({domain_of_node(n) for n in nodes}, key=str)
            sl = [slice_by_domain(nodes, edges, v, basis) for v in vals]
    total = {"nodes": len(nodes), "edges": len(edges)}
    rec = reconcile(sl, total, dim)
    return {
        "dim": dim,
        "rule_version": DOMAIN_MAP_VERSION,
        "basis": basis,
        "total": total,
        "slices": sl,
        "reconcile": rec,
    }


def reconcile(slices, total, dim):
    """对账：把「各部分之和 == 全集」写成**可断言的结构**。

    ★ 为什么单独成函数：对账式若散落在打印语句里，就会出现
      「打印说守恒、实际不守恒」（D117：计数语句的位置错了，对账式就会撒谎）。
    """
    if dim == "grade":
        # 边维度：≥1..≥5 的切片**天然嵌套**，不守恒；守恒的是「按等级值分档」
        return {"applicable": False,
                "why": "grade 切片为嵌套区间（≥1 ⊇ ≥2 ⊇ …），不适用加总守恒；"
                       "改用「按等级值分档」视图核对 Σ == 边总数"}
    s_n = sum(s["counts"]["nodes_matched"] for s in slices)
    s_e = sum(s["counts"]["edges_matched"] for s in slices)
    per = [(s["value"],
            s["counts"]["edges_internal"] + s["counts"]["edges_crossing"]
            == s["counts"]["edges_matched"]) for s in slices]
    return {
        "applicable": True,
        "sum_nodes_matched": s_n,
        "total_nodes": total["nodes"],
        "nodes_ok": s_n == total["nodes"],
        "sum_edges_matched": s_e,
        "total_edges": total["edges"],
        "edges_ok": s_e == total["edges"],
        "per_slice_internal_plus_crossing_ok": all(ok for _, ok in per),
        "per_slice_detail": [{"value": v, "ok": ok} for v, ok in per],
        "ok": (s_n == total["nodes"] and s_e == total["edges"]
               and all(ok for _, ok in per)),
    }


def assert_bool_predicate(pred, sample):
    """断言 `pred` 在 `sample` 上返回**严格** bool（D123 的门禁）。

    ★ 为什么需要它：本仓库 `redistribution_closure` 返回三态字符串、
      `is_redistributable` 返回 bool，二者名字相近。误传前者给 `edge_grade_public`
      会让折扣恒为 5（不打折）⇒ **对外分数虚高**且**不报错**。
      实测虚高幅度：`≥5` 对外应为 15 条，误传后为 702 条（**47 倍**）。
      ⇒ 凡是「bool 谓词」形参，入口先用这个函数验一遍，别让它悄悄算错。
    """
    for s in sample:
        v = pred(s)
        if v is not True and v is not False:
            return False, "pred(%r) -> %r（非 bool）" % (s, v)
    return True, "全部返回严格 bool"


def grade_buckets(edges, grade_of):
    """按**等级值分档**（1..5 + null）计数 —— 这是 grade 维度可对账的视图。"""
    c = collections.OrderedDict((str(i), 0) for i in range(1, 6))
    c["null"] = 0
    for e in edges:
        g = grade_of(e)
        c["null" if g is None else str(g)] += 1
    return c


# ---------------------------------------------------------------- 切片版本口径
#
# ★ 为什么切片要**自己**一套版本，而不直接复用全量包的 `dataset_id`：
#   全量包的 `dataset_id = {format}+g{snapshot}.c{content}` 描述的是
#   「**整张图**按这个 profile 导出来的这一份」。切片是**另一个对象**：
#   同一张图、同一个 profile，切出来的「physics 学科」和「chemmistry 来源」
#   必须是**两个**版本号，否则两条引用会撞在同一个 id 上 —— 而对外的版本号
#   一旦撞号，就等于承诺「这两份是一样的」。
#
# ★ 切片的身份 = **parent（从哪版全量切的）× predicate（切什么）× content（切出来什么）**：
#
#     slice_dataset_id = {parent_dataset_id}/{dim}={value}#{slice_content8}
#                        [ + "@" + basis ]
#
#   · `parent_dataset_id` 必须**可追溯**（S89）：没有它，只能说「有一个切片」，
#     说不出「它属于哪一版全量」—— 而全量重切一次，同名切片的内容就变了。
#   · `predicate` 必须**进指纹**（S88）：不加的话「按 A 切」和「按 B 切」
#     若恰好命中同样多的记录，会算出同一个 content 指纹 ⇒ 两片撞号。
#   · `content` 取**实际写出的记录**（id 序列 + 每条记录的规范化串）的哈希，
#     而不是「命中条数」—— 条数相同内容不同是最常见的静默错误。

#: 切片台账文件名（**独立**于全量包的 `versions.jsonl`）。
#: ★ 为什么不共用一本账：`versions.jsonl` 的每行都代表「一次**全量**构建」，
#:   F9 的 `prev = rows[-1]` 用它判定「上一版是谁」。切片混进去会让
#:   「下一次全量构建的上一版」变成一个切片 ⇒ 直接破坏 F9 的 PATCH 判定链。
SLICE_REGISTRY_NAME = "slices.jsonl"

#: 切片包格式版本（人维护；记录字段集变了才升）。
SLICE_FORMAT_VERSION = "slice_v1.0.0"


class SliceVersionError(ValueError):
    """切片版本参数非法 / parent 缺失（fail-closed）。"""


def _canon(obj) -> str:
    """规范化 JSON —— 与 `versioning._canon` **同口径**（同输入必须同串）。
    ★ 不 import versioning 的那个：它属 06_PoC/dataset 内部工具，
      但两处必须**逐字符**一致，故这里写死同一组参数并配 S87 断言。
    """
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _digest8(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def slice_content_id(node_records, edge_records, predicate=None) -> str:
    """切片**内容**指纹：**谓词** + 记录 id 序列 + 每条记录的规范化串。

    ★ 为什么不用「条数」：条数相同、内容不同是最常见的静默错误
      （改了谓词、换了来源、重跑 ETL 后顺序变了都能造成）。
      这里把每条记录**本身**纳入哈希 ⇒ 少一条、换一条、改一个字段都会变。

    ★ 为什么谓词也要进指纹（**实测踩到过**）：起初只哈希记录内容，
      于是「physics 切片」与「chemistry 切片」若**恰好含相同的记录集合**
      （自检夹具里就是同一个 nrec/erec），会算出**同一个** `#856a0238`。
      版本号表面不同，只因路径段 `/domain=physics` vs `/domain=chemistry`
      不同 —— 也就是说「内容指纹」当时**没有承担它名字承诺的事**。
      把谓词纳入后，`#` 段才真正代表「这一片的身份」。
      ⇒ 这也让「同一片数据、不同谓词」这种偷懒复用**无法**蒙混过去。
    """
    def _rows(recs):
        out = []
        for r in recs:
            out.append(_canon(r))
        out.sort()
        return out
    payload = {
        "schema": "sttp-slice-content/v2",      # ★ v1→v2：谓词纳入指纹（破坏性变更）
        "predicate": dict(predicate or {}),
        "nodes": _rows(node_records),
        "edges": _rows(edge_records),
    }
    return _digest8(_canon(payload))


def slice_predicate(dim, value, basis=None) -> dict:
    """切片谓词（**身份的一部分**）。窄，且要能**打印出来给人看**。"""
    if dim not in DIMENSIONS:
        raise SliceVersionError("未知切片维度 %r（可选：%s）" % (dim, list(DIMENSIONS)))
    p = {"dim": dim, "value": value}
    if dim == "grade":
        if basis not in GRADE_BASIS:
            raise SliceVersionError("grade 维度必须指定 basis ∈ %s" % (list(GRADE_BASIS),))
        p["basis"] = basis
    return p


def slice_dataset_id(parent_dataset_id, predicate, content8) -> str:
    """`{parent}/{dim}={value}[@basis]#{content8}`。

    ★ 用 `/` 与 `#` 分隔（**不用** `+`/`.`）：`+`/`.` 是全量包
      `dataset_v1.1.0+gf8ebee052d1b.c02edb979` 里的分隔符，混用会让
      「这个 id 是全量还是切片」需要靠正则猜。两种对象两种写法，一眼可分。
    """
    if not isinstance(parent_dataset_id, str) or not parent_dataset_id.strip():
        raise SliceVersionError("切片必须有 parent_dataset_id（从哪版全量切的）；"
                                "收到 %r —— 没有它就无法追溯" % (parent_dataset_id,))
    pred = slice_predicate(predicate.get("dim"), predicate.get("value"),
                           predicate.get("basis"))
    if not isinstance(content8, str) or len(content8) != 8:
        raise SliceVersionError("slice content 指纹必须是 8 位十六进制，收到 %r" % (content8,))
    tag = "%s=%s" % (pred["dim"], pred["value"])
    if "basis" in pred:
        tag += "@" + pred["basis"]
    return "%s/%s#%s" % (parent_dataset_id.strip(), tag, content8)


def parse_slice_dataset_id(sid) -> dict:
    """拆切片 id。**形态不对 ⇒ 抛错**（不猜、不返回 None 让调用方自己判）。"""
    if not isinstance(sid, str) or "/" not in sid or "#" not in sid:
        raise SliceVersionError("不是切片 id（缺 `/` 或 `#`）：%r" % (sid,))
    parent, rest = sid.split("/", 1)
    body, content8 = rest.rsplit("#", 1)
    tag, _, basis = body.partition("@")
    dim, _, value = tag.partition("=")
    if not parent or not dim or not value or len(content8) != 8:
        raise SliceVersionError("切片 id 形态不完整：%r" % (sid,))
    r = {"parent_dataset_id": parent, "dim": dim, "value": value, "content8": content8}
    if basis:
        r["basis"] = basis
    return r


def slice_registry_path(root: str) -> str:
    return os.path.join(root, "06_PoC", "dataset", SLICE_REGISTRY_NAME)


def load_slice_registry(path) -> list:
    """读切片台账；坏行 **直接抛错**（跳过一行 = 少一条历史，看不出来）。"""
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
                raise SliceVersionError("切片台账第 %d 行不是合法 JSON：%s" % (i, e))
    return rows


def append_slice_registry(path, entry) -> None:
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(_canon(entry) + "\n")


def export_slice(out_dir, *, parent_dataset_id, dim, value, basis,
                 node_records, edge_records, counts, extra=None,
                 out_dir_rel=None) -> dict:
    """把一个切片**落盘**，返回台账条目（是否追加由调用方按幂等判定）。

    产物（都在 `out_dir` 下）：
      · `nodes.jsonl` / `edges.jsonl` —— 记录本体（**按 id 排序**，保证逐字节确定）
      · `slice.json` —— 切片 manifest（谓词 / parent / 计数 / 对账 / 文件哈希）
      · `SHA256SUMS` —— 文件哈希

    ★ **按 id 排序**：不排序的话，dict 迭代顺序一变产物就变，
      而 `content_id` 只对记录内容排序 —— 会导致「指纹没变但文件变了」。

    ★ `out_dir_rel` 是**入库用的相对路径**。为什么不直接把 `out_dir` 写进台账：
      台账是**入库**文件（切片版本是对外承诺），而绝对路径会把
      `C:\\Users\\<本机名>\\...` 写进版本库 —— 换台机器就失效，
      且违反仓库既有的「无原机路径」纪律（`install` 的 A05 判据就在查这个）。
      ⇒ 台账只存相对 ROOT 的 posix 路径；`None` ⇒ 不写该字段（宁可少一个字段，
        也不写一个到别处就错的字段）。
    """
    fmt = SLICE_FORMAT_VERSION
    nrec = sorted(node_records, key=lambda r: str(r.get("id")))
    erec = sorted(edge_records, key=lambda r: str(r.get("id")))
    pred = slice_predicate(dim, value, basis)          # ★ 先定谓词
    cid = slice_content_id(nrec, erec, pred)          # ★ 再算指纹（谓词参与）
    sid = slice_dataset_id(parent_dataset_id, pred, cid)

    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    paths = {}
    for name, recs in (("nodes.jsonl", nrec), ("edges.jsonl", erec)):
        p = os.path.join(out_dir, name)
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            for r in recs:
                f.write(_canon(r) + "\n")
        paths[name] = p

    import hashlib
    files = []
    for name in ("nodes.jsonl", "edges.jsonl"):
        with io.open(paths[name], "rb") as f:
            b = f.read()
        files.append({"path": name, "sha256": hashlib.sha256(b).hexdigest()[:12],
                      "bytes": len(b)})

    manifest = {
        "schema": "sttp-slice-manifest/v1",
        "format_version": fmt,
        "slice_dataset_id": sid,
        "parent_dataset_id": parent_dataset_id,
        "predicate": pred,
        "domain_map_version": DOMAIN_MAP_VERSION,
        "counts": dict(counts or {}),
        "files": files,
    }
    if extra:
        manifest.update(extra)
    mp = os.path.join(out_dir, "slice.json")
    with io.open(mp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    with io.open(mp, "rb") as f:
        mb = f.read()
    files.append({"path": "slice.json", "sha256": hashlib.sha256(mb).hexdigest()[:12],
                  "bytes": len(mb)})

    sums = os.path.join(out_dir, "SHA256SUMS")
    with io.open(sums, "w", encoding="utf-8", newline="\n") as f:
        for it in sorted(files, key=lambda x: x["path"]):
            f.write("%s  %s\n" % (it["sha256"], it["path"]))

    entry = {
        "slice_dataset_id": sid,
        "format_version": fmt,
        "parent_dataset_id": parent_dataset_id,
        "predicate": pred,
        "content_id": cid,
        "counts": dict(counts or {}),
        "files": files,
    }
    # ★ 只在**显式给了相对路径**时才写 out_dir（绝不写绝对路径，见 docstring）
    if out_dir_rel:
        entry["out_dir"] = out_dir_rel.replace(os.sep, "/")
    return entry


def should_append(rows, entry) -> bool:
    """幂等：台账里**已存在同一个 slice_dataset_id** ⇒ 不追加（S90）。

    ★ 为什么按 id 判而不是按「最后一次」判：切片是按需导出的，
      顺序任意；「最后一次」对切片没有「上一版」意义（那是全量台账的语义）。
    """
    sid = entry.get("slice_dataset_id")
    return not any(r.get("slice_dataset_id") == sid for r in (rows or ()))


# ---------------------------------------------------------------- 判据自检
def selfcheck() -> int:
    """合成图上证明 S70–S76 **有能力红**（负对照），不依赖权威图。"""
    rep = []

    def add(ok, name, detail=""):
        rep.append((bool(ok), name, detail))

    # ---- 合成图：3 学科、2 来源、等级 1..5 各一条 ----
    nodes = []
    for i, (nid, dom, org) in enumerate([
        ("n:1", "physics.em", "mathxiv"),
        ("n:2", "physics.mechanics", "mathxiv"),
        ("n:3", "chem.reaction", "ChEBI"),
        ("n:4", "chemistry.aq", "ChEBI"),      # ← 同义异写，应与 chem 合并
        ("n:5", "math.DG", "mathxiv"),
        ("n:6", None, "wikidata"),           # ← 无 domain ⇒ unclassified
    ]):
        nodes.append({"id": nid, "labels": ["Entity"], "props": {"domain": dom, "source": org}})
    edges = [
        {"id": "e:1", "source": "n:1", "target": "n:2", "type": "t", "props": {"source": "mathxiv"}},
        {"id": "e:2", "source": "n:3", "target": "n:4", "type": "t", "props": {"source": "ChEBI"}},
        {"id": "e:3", "source": "n:1", "target": "n:3", "type": "t", "props": {"source": "mathxiv"}},  # 跨界
        {"id": "e:4", "source": "n:5", "target": "n:6", "type": "t", "props": {"source": "mathxiv"}},  # 跨界
    ]
    GR = {"e:1": 5, "e:2": 4, "e:3": 2, "e:4": None}
    grade_of = lambda e: GR.get(e.get("id"))

    # ---- S70/S71/S72 三条守恒**分开**断言 ----
    r = build_slices(nodes, edges, "domain")
    rc = r["reconcile"]
    add(rc["nodes_ok"], "S70 学科守恒：Σ 各学科命中节点 == 全图节点数",
        "%d vs %d" % (rc["sum_nodes_matched"], rc["total_nodes"]))
    add(rc["edges_ok"], "S71 边守恒：Σ 各学科切片边数 == 全图边数（每条边恰归一片）",
        "%d vs %d" % (rc["sum_edges_matched"], rc["total_edges"]))
    add(rc["per_slice_internal_plus_crossing_ok"],
        "S72 片内自洽：每片 internal + crossing == 该片边数",
        "%s" % rc["per_slice_detail"])
    vals = [s["value"] for s in r["slices"]]
    add("chemistry" in vals and "chem" not in vals,
        "S73 同义异写已合并（chemistry 存在、chem 不独立出现）",
        "实际学科桶：%s" % vals)
    add("unclassified" in vals and
        [s for s in r["slices"] if s["value"] == "unclassified"][0]["counts"]["nodes_matched"] == 1,
        "S74 缺失 domain 落入 unclassified 且计入守恒",
        "unclassified 命中 1 条（n:6）")

    # ---- S75 映射完备（未登记前缀 ⇒ 红）----
    unmapped = set()
    normalize_domain("quantum.foo", unmapped)
    add(bool(unmapped) and "quantum" in unmapped,
        "S75 未登记的学科前缀会被**看见**（不静默归为 unclassified）",
        "捕获到未登记前缀：%s" % sorted(unmapped))

    # ---- S76 来源守恒 ----
    r2 = build_slices(nodes, edges, "source")
    rc2 = r2["reconcile"]
    add(rc2["nodes_ok"] and rc2["edges_ok"], "S76 来源维度双守恒",
        "节点 %d vs %d；边 %d vs %d"
        % (rc2["sum_nodes_matched"], rc2["total_nodes"],
           rc2["sum_edges_matched"], rc2["total_edges"]))

    # ---- S78 空切片必须与「条件不成立」分说 ----
    empty = slice_by_source(nodes, edges, "__definitely_not_a_source__")
    try:
        build_slices(nodes, edges, "__not_a_dimension__")
        add(False, "S78 非法维度 ⇒ **必须抛错**",
            "没抛错 ⇒ 「条件不成立」会被当成「空片」，使用者会反复重试")
    except SliceError as ex:
        add(empty["status"] == "ok" and empty["reason"] == REASON_NO_MATCH,
            "S78 空片分说：无匹配 ⇒ ok/no_match；非法维度 ⇒ 抛 SliceError",
            "空片 status=%s reason=%s；非法维度已抛：%s"
            % (empty["status"], empty["reason"], ex))

    # ---- 负对照：去掉 unclassified 桶 ⇒ 守恒必须红 ----
    mangled = [s for s in r["slices"] if s["value"] != UNCLASSIFIED]
    rc3 = reconcile(mangled, r["total"], "domain")
    add(not rc3["ok"], "NEG-1 去掉 unclassified 桶 ⇒ 守恒**必须红**",
        "nodes_ok=%s（须 False）" % rc3["nodes_ok"])

    # ---- 负对照：grade 不指定 basis ⇒ 必须抛错 ----
    try:
        slice_by_grade(nodes, edges, 3, None, grade_of)
        add(False, "NEG-2 grade 不指定 basis ⇒ **必须抛错**", "没有抛错 ⇒ 判据是恒真的")
    except SliceError as ex:
        add(True, "NEG-2 grade 不指定 basis ⇒ **必须抛错**", "已抛：%s" % ex)

    # ---- 负对照：跨界边重复计数 ⇒ 守恒必须红 ----
    dup = [dict(s) for s in r["slices"]]
    for s in dup:
        s["counts"] = dict(s["counts"])
        s["counts"]["edges_matched"] = s["counts"]["edges_matched"] + 1
    rc4 = reconcile(dup, r["total"], "domain")
    add(not rc4["ok"], "NEG-3 切片边数多算（跨界重复计数）⇒ 守恒**必须红**",
        "edges_ok=%s（须 False）" % rc4["edges_ok"])

    # ---- S79 谓词契约：bool 谓词放行、三态字符串必须被抓（D123）----
    good_pred = lambda s: True
    bad_pred = lambda s: "redistributable"      # 模拟误传 redistribution_closure
    ok1, d1 = assert_bool_predicate(good_pred, ["x", "y"])
    ok2, d2 = assert_bool_predicate(bad_pred, ["x", "y"])
    add(ok1 and not ok2,
        "S79 谓词契约：严格 bool 放行，三态字符串**必须被抓**（D123）",
        "good: %s | bad: %s" % (d1, d2))

    # ---- S87–S91 切片版本口径（合成数据，不落盘到仓库）----
    import shutil
    import tempfile

    PARENT = "dataset_v1.1.1+gf8ebee052d1b.cdf46b3f3"
    nrec = [{"id": "n:1", "name": "a"}, {"id": "n:2", "name": "b"}]
    erec = [{"id": "e:1", "s": "n:1", "t": "n:2"}]
    tmp = tempfile.mkdtemp(prefix="sttp-slice-")
    try:
        e1 = export_slice(os.path.join(tmp, "a"), parent_dataset_id=PARENT,
                          dim="domain", value="physics", basis=None,
                          node_records=nrec, edge_records=erec,
                          counts={"nodes": 2, "edges": 1})
        # S87 确定性：同输入再导一次，id 必须逐字符相同
        e2 = export_slice(os.path.join(tmp, "b"), parent_dataset_id=PARENT,
                          dim="domain", value="physics", basis=None,
                          node_records=list(reversed(nrec)),
                          edge_records=list(reversed(erec)),
                          counts={"nodes": 2, "edges": 1})
        add(e1["slice_dataset_id"] == e2["slice_dataset_id"],
            "S87 版本确定性：同输入（哪怕输入顺序不同）⇒ 同 slice_dataset_id",
            "%s vs %s" % (e1["slice_dataset_id"], e2["slice_dataset_id"]))
        # S91 落盘一致：文件逐字节相同
        same = True
        for name in ("nodes.jsonl", "edges.jsonl", "slice.json"):
            a = io.open(os.path.join(tmp, "a", name), "rb").read()
            b = io.open(os.path.join(tmp, "b", name), "rb").read()
            same = same and (a == b)
        add(same, "S91 落盘一致：同输入两次导出，三个文件逐字节相同", "顺序打乱也不得变")

        # S88 谓词入指纹：同数据、换谓词 ⇒ 版本号必须变
        e3 = export_slice(os.path.join(tmp, "c"), parent_dataset_id=PARENT,
                          dim="domain", value="chemistry", basis=None,
                          node_records=nrec, edge_records=erec,
                          counts={"nodes": 2, "edges": 1})
        add(e3["slice_dataset_id"] != e1["slice_dataset_id"],
            "S88 谓词入指纹：同数据换谓词（physics→chemistry）⇒ 版本号**必须变**",
            "%s vs %s" % (e1["slice_dataset_id"], e3["slice_dataset_id"]))
        # ★ 加强版：只比整个 id 会被路径段「蒙对」—— 必须断言 **# 段本身**也不同。
        #   实测教训：只比整串时，physics/chemistry 两片的 # 段曾完全相同
        #   （都是 856a0238），判据仍 PASS ⇒ 它当时证不了「内容指纹含谓词」。
        add(e3["content_id"] != e1["content_id"],
            "S88b 内容指纹本身含谓词：同数据不同谓词 ⇒ `#` 段**必须不同**",
            "#%s vs #%s" % (e1["content_id"], e3["content_id"]))

        # S89 parent 可追溯：无 parent ⇒ 抛错；有 parent ⇒ 能解析回来
        try:
            export_slice(os.path.join(tmp, "d"), parent_dataset_id=None,
                         dim="domain", value="physics", basis=None,
                         node_records=nrec, edge_records=erec, counts={})
            add(False, "S89 parent 可追溯：无 parent ⇒ **必须抛错**", "没抛错")
        except SliceVersionError as ex:
            back = parse_slice_dataset_id(e1["slice_dataset_id"])
            add(back["parent_dataset_id"] == PARENT and back["dim"] == "domain"
                and back["value"] == "physics",
                "S89 parent 可追溯：id 可反解出 parent/dim/value",
                "已抛：%s｜反解：%s" % (ex, back))

        # S90 幂等：同一 id 不重复追加
        rows = []
        a1 = should_append(rows, e1)
        rows.append(e1)
        a2 = should_append(rows, e2)
        add(a1 and not a2, "S90 落盘幂等：同一 slice_dataset_id 只追加一次",
            "首次 %s / 再次 %s" % (a1, a2))

        # S92v 合成侧：导出的台账条目**不得**含原机绝对路径
        # ★ 与真图侧 S92（phase78 读 slices.jsonl）分工：那条查**已落的盘**，
        #   这条查**刚导出的条目**。R59 必须打这条，因为探针要指向
        #   **会产生该缺陷的代码路径**（export_slice），而不是只读台账的仪器。
        blob = _canon(e1)
        hit = [t for t in ("C:\\\\", "C:/", "/Users/", "/home/", "D:\\\\") if t in blob]
        add(not hit, "S92v 导出条目无原机绝对路径（入库可移植）",
            "命中：%s｜out_dir=%r" % (hit or "无", e1.get("out_dir")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 78)
    print("  slicing.py --selfcheck：合成图上证明切片判据可红（S70–S79 + S87–S91 + 负对照）")
    print("=" * 78)
    npass = nfail = 0
    for ok, name, detail in rep:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
        if detail:
            print("         %s" % detail)
        npass += ok
        nfail += (not ok)
    print("-" * 78)
    print("  slicing: PASS %d / FAIL %d" % (npass, nfail))
    print("\n  结论：%s" % ("全部判据通过；负对照已证明这些判据**不是恒真**。"
                            if nfail == 0 else "存在 FAIL —— 先修，不要绕过。"))
    return 0 if nfail == 0 else 1


def run_export(src_path, out_dir, *, dim, value, basis=None, root=None,
               parent_dataset_id=None, dry=False) -> int:
    """CLI 主流程：真图 → 切一片 → 落盘 → 台账（幂等）。

    ★ 投影**复用** `export_dataset.project_records`：切片记录必须与全量包的
      记录**同形**，否则同一个节点在两份产物里长得不一样（D102 同族）。
    ★ `parent_dataset_id` 默认取 `versions.jsonl` 的**最后一条**（= 当前全量包）。
      取不到 ⇒ **抛错**，不猜一个假 parent。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    root = root or os.path.dirname(os.path.dirname(here))
    for p in (root, os.path.join(root, "06_PoC"), os.path.join(root, "03_知识层"),
              os.path.join(root, "11_真实数据")):
        if p not in sys.path:
            sys.path.insert(0, p)

    import export_dataset as ex
    import versioning as ver
    # ★ 非通过出口的自述必须有**唯一实现**（铁律 #92 的执法面）：
    #   本文件的两个 `[前置缺失]` 出口在 Phase 78 之前**没接这条纪律**，
    #   被 `interp_selfreport_check` 的 Q3 静态扫描抓出来（Phase 80 跑全链门禁才第一次咬住）。
    #   缺件分支在绿树上永不执行 ⇒ 没有人会想起它 —— 只有静态扫描能咬住。
    import interp_note as IN                          # noqa: E402

    if not os.path.exists(src_path):
        print("[前置缺失] 找不到规范图：%s（RC=2）" % src_path)
        print("        原因：%s" % IN.skip_detail("规范图缺件（对外包构建未执行）"))
        return 2

    if parent_dataset_id is None:
        rows = ver.load_registry(ver.registry_path(root))
        if not rows:
            print("[前置缺失] `versions.jsonl` 里没有全量包记录 ⇒ 不知道从哪版切（RC=2）")
            print("        原因：%s" % IN.skip_detail("版本台账为空，先跑 `bash sttp.sh dataset`"))
            print("        切片必须可追溯 parent；**不会**编一个假 parent。")
            return 2
        parent_dataset_id = rows[-1]["dataset_id"]
        print("  parent  : %s（取自 versions.jsonl 最后一条 seq=%s）"
              % (parent_dataset_id, rows[-1].get("seq")))

    nodes, edges = ex.load_source(src_path)
    # ★ grade 维度必须**注入**等级函数（03/11 层不反向 import，与 `redist_of` 同例）。
    #   这里由 CLI 层组装：内部口径用 edge_grade，对外口径用 edge_grade_public +
    #   ★ `is_redistributable`（**bool**）——不是 `redistribution_closure`（三态字符串，D123）。
    grade_of = None
    if dim == "grade":
        import queries as _q
        import source_registry as _sr
        reg = _q.build_stale_registry(_sr.ids(), _sr.hash_map(base=root),
                                      _sr.repo_status_map(base=root))
        if basis == "internal_e1":
            grade_of = lambda e: _q.edge_grade(e, reg)          # noqa: E731
        else:
            grade_of = lambda e: _q.edge_grade_public(              # noqa: E731
                e, reg, redist_of=_sr.is_redistributable)
    all_sl = build_slices(nodes, edges, dim, basis, grade_of=grade_of)
    hit = [x for x in all_sl["slices"]
           if x["value"] == value or (dim == "grade" and x["value"] == ">=%s" % value)]
    if not hit:
        print("[不成立] %s 维度里没有取值 %r ⇒ 不是「无数据」，是**条件不成立**"
              % (dim, value))
        print("        可选：%s" % [x["value"] for x in all_sl["slices"]])
        return 2
    sl = hit[0]
    print("  切片    : %s=%s  节点 %d（另带入 %d） 边 %d（内 %d / 跨界 %d）"
          % (dim, value, sl["counts"]["nodes_matched"], sl["counts"]["nodes_attached"],
             sl["counts"]["edges_matched"], sl["counts"]["edges_internal"],
             sl["counts"]["edges_crossing"]))

    # 用**全量同款投影**生成记录
    nid, eid = set(sl["node_ids"]), set(sl["edge_ids"])
    sn = [n for n in nodes if n.get("id") in nid]
    se = [e for e in edges if e.get("id") in eid]
    nrec, erec = ex.project_records(sn, se)

    if dry:
        print("\n  [--dry] 未落盘。预期 slice_dataset_id 见下：")
        pred = slice_predicate(dim, value, basis)
        print("    %s" % slice_dataset_id(parent_dataset_id, pred,
                                          slice_content_id(nrec, erec, pred)))
        return 0

    entry = export_slice(out_dir, parent_dataset_id=parent_dataset_id, dim=dim,
                         value=value, basis=basis, node_records=nrec,
                         edge_records=erec, counts=sl["counts"],
                         extra={"reconcile": all_sl["reconcile"]},
                         out_dir_rel=os.path.relpath(out_dir, root))
    print("  版本    : %s" % entry["slice_dataset_id"])
    rp = slice_registry_path(root)
    rows = load_slice_registry(rp)
    if should_append(rows, entry):
        entry["seq"] = (max((int(r.get("seq") or 0) for r in rows), default=0) + 1)
        append_slice_registry(rp, entry)
        print("  台账    : 已追加 %s（第 %d 条）" % (os.path.relpath(rp, root), entry["seq"]))
    else:
        print("  台账    : 同一 slice_dataset_id 已存在 ⇒ **不追加**（幂等）")
    print("  产物    : %s" % os.path.relpath(out_dir, root).replace(os.sep, "/"))
    return 0


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="STTP 数据集切片（Phase 78 · F8）")
    ap.add_argument("--selfcheck", action="store_true",
                    help="合成图自检（S70–S79 + S87–S91 + 负对照，不依赖权威图）")
    ap.add_argument("--src", default=None, help="规范图（默认 06_PoC/etl/normalized.json）")
    ap.add_argument("--out", default=None, help="切片输出目录（默认 06_PoC/dataset/slices/<dim>=<value>）")
    ap.add_argument("--dim", choices=list(DIMENSIONS), help="切片维度")
    ap.add_argument("--value", help="切片取值（学科名 / 来源名 / 等级下界 1..5）")
    ap.add_argument("--basis", choices=list(GRADE_BASIS),
                    help="grade 维度必填：internal_e1 或 public_p1")
    ap.add_argument("--parent", default=None,
                    help="parent_dataset_id（默认取 versions.jsonl 最后一条）")
    ap.add_argument("--dry", action="store_true", help="只算版本号，不落盘")
    a = ap.parse_args(argv)

    if a.selfcheck:
        return selfcheck()

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    src = a.src or os.path.join(root, "06_PoC", "etl", "normalized.json")
    if not a.dim or a.value is None:
        ap.print_help()
        print("\n例如：python 06_PoC/dataset/slicing.py --dim domain --value physics --out /tmp/phys")
        return 0
    val = a.value
    if a.dim == "grade":
        try:
            val = int(val)
        except ValueError:
            print("[参数非法] grade 维度的 --value 必须是 1..5 的整数，收到 %r" % (a.value,))
            return 2
    out = a.out or os.path.join(root, "06_PoC", "dataset", "slices",
                                "%s=%s" % (a.dim, val))
    print("=" * 78)
    print("  STTP 数据集切片（F8）—— %s=%s" % (a.dim, val))
    print("=" * 78)
    print("  输入    : %s" % os.path.relpath(src, root).replace(os.sep, "/"))
    # ★ 参数类错误必须是**一句可读的话**，不能把 traceback 甩给使用者
    #   （铁律 #92 同族：非通过出口要自述原因，否则读者会去查一个不存在的 bug）。
    try:
        return run_export(src, out, dim=a.dim, value=val, basis=a.basis,
                          root=root, parent_dataset_id=a.parent, dry=a.dry)
    except SliceError as ex:
        print("[参数非法] %s" % ex)
        print("        原因：grade 维度的等级是**边级**指标且有内部/对外两套口径，")
        print("              必须显式选一个 —— 不默认，避免「对外分数其实是内部分数」。")
        print("        正确用法：--dim grade --value 4 --basis public_p1")
        return 2
    except SliceVersionError as ex:
        print("[版本口径错误] %s" % ex)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
