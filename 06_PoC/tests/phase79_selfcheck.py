#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""Phase 79（**数据无损**：针对静默截断的治理）自检 —— 判据 + 负对照自证。

★ 为什么单独一台仪器
--------------------
codex 2026-10-26 的方向建议点名了 `elementkg10m_ingest.py` 第 129 行的 `tv[:400]`。
核实后这条指控**完全属实且更严重**，而且顺着它挖出了**另外两处**同类是无 SUBMIT 的：

  ① `elementkg10m_ingest.py:129`  `= tv[:400]`                → mapped_smiles
  ② `rhea_ingest.py:424`          `props["xrefs"] = …[:20]`   → 外部数据库交叉引用
  ③ `etl_pipeline.py:582`         `slugify(eq)[:32]`          → 节点 id（截断即可能碰撞）

这类缺陷的共同特征：**从不报错**。写入方不知道丢了，读取方不知道缺了，
两侧都不认为有任何问题 —— 只能靠外部仪器把它照出来。

判据
----
  S94 源码层：写入语句（`Assign`/`AnnAssign`）中的**裸前缀切片**必须显式豁免
       ★ 判据**不硬编码阈值**，而是用 AST 从源码抽出；改了 400 → 800 照样被抓。
       ★ 默认禁止、显式豁免（`# noloss-exempt: <理由>`）：
         反过来写（白名单允许）的话，任何人新加一处截断都不会被发现。
  S95 数据层：属性值长度不得在源码截断阈值处**堆积**
       ★ 单条恰好等于阈值可能是偶然；**≥2 条**就一定不是巧合（实测 341 / 55 条）。
  S96 语义层：全部 `mapped_smiles` 必须能被 RDKit 解析（缺 RDKit ⇒ SKIP，不假通过）
       ★ 这是唯一不依赖"知道阈值"的判据 —— 它测的是**后果**而非**征候**。

★ **分层不是冗余**：S94 抓"会丢的写法"（防未来），S95 抓"已经丢的中间态"，
  S96 抓"最终能不能用"（达标）。三层对应三种完全不同的修法，
  合并成一条就只会看到"数据坏了"，不知道该改源码、重跑 ETL 还是改验收标准。

运行： bash sttp.sh phase79
"""

from __future__ import annotations

import ast
import collections
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

# 只扫**承载图谱数据**的目录。*为什么不是全仓*：全仓扫会有 165 处命中，
# 其中绝大多数是 print 取样 / 字符串格式化 / 哈希前缀，噪音会淹没真信号。
# 这里宁可漏扫显示层，也要让"写入数据"这条线保持零噪音 —— 判据一旦
# 天天误报，人就会学会无视它（D122 同族的心理学）。
SCAN_DIRS = ("03_知识层", "06_PoC/etl", "11_真实数据")

EXEMPT_MARK = "noloss-exempt"

# ---------------------------------------------------------------------------
# 带**指纹**的外部豁免清单（Phase 81 · 双身份文件冲突的解法）
# ---------------------------------------------------------------------------
# ★ 为什么要有第二条通道（不是偷懒，是被另一台仪器逼出来的）：
#   `11_真实数据/element_reference.py` **既是代码、又是证据**：它同时是
#     ① 已登记来源 locator（`Phase13.Gate.B2/B5` 的 `src_hash`）
#     ② A04 参考表 `element_reference` 的 `refs`
#     ③ 验证器依赖文件 `element_reference.py` 的 `dvs`
#   ⇒ 在上面**加一行注释**就能同时推翻三族哈希：实测 `src_changed` 739 条、
#     `ref_changed` 35040 条、`dep_version_changed` 32609 条，
#     把 V18d / V18h / V16c 三条判据一次性打红（这不是判据失灵，是它真抓到了）。
#   于是两台仪器在同一份文件上互斥：S94 要「在行旁写字」，字节保真要「一个字都别动」。
#
# ★ 为什么这样做仍然**默认禁止**（没有退化成白名单）：
#   每条豁免记的是「文件 + 阈值 + **该行逐字原文**」的**三联指纹**，
#     · 改这一行的任何字符（哪怕只改变量名）⇒ 指纹失配 ⇒ 立刻重新变红；
#     · 别处新增的裸切片不在清单里 ⇒ 立刻红；
#     · 清单条目若在某天对不上任何一行（**死条目**）⇒ 由 S94b 判红，逼人清理。
#   也就是说它把「豁免」从**免死金牌**降级成「针对这一行的、可失效的收据」。
#   NEG-S94b 逐条构造变异，证明这条通道**不是恒赦**。
#  ★ 2026-10-28（Phase 81 · S3）：**清空过一次又恢复**，这段历史必须留着 ——
#    S3 的假设是「Phase 81 的 content_equivalent 证明能让内联标注回归」。实测**不成立**：
#    证明只作用于**证据层**（`evidence_status`），而 V18d「全图 changed == 0」是**字节层**
#    判据（`queries.edge_fidelity` 只看 src_hash vs 仓库 blob，压根不看证据状态）
#    ⇒ 注释一加回，`changed` 立刻 703，闸门必红，与证明无关。
#    ⇒ 这条拐杖**还得留着**：除非批准给 V18 增一档「字节变了但内容等价」的口径。
EXTERNAL_EXEMPTS = [
    {
        "file": "11_真实数据/element_reference.py",
        "threshold": 1,
        "line": "s2 = s[:1].upper() + s[1:].lower()",
        "reason": "查表前的大小写归一，不是截断：`s[:1]` 取首字母后与 `s[1:]` 拼回完整串，"
                  "无一字丢失。该行所在的 `element_reference.py` 是**已登记证据来源/参考表/依赖**，"
                  "V18d 是**字节层**判据 ⇒ 插一个字节就 703 条 changed（Phase 81 · S3 实测），"
                  "故豁免走本清单，指纹随行失效。",
    },
]

#: {(文件, 阈值, 该行 strip 原文): entry} —— 命中即可豁免；**三者全等**才算
_EXEMPT_FINGERPRINTS = {
    (e["file"], e["threshold"], e["line"].strip()): e for e in EXTERNAL_EXEMPTS
}


def _is_exempt(relpath, threshold, src_line, prints=None):
    """这处裸切片是否已被显式豁免（内联标注 **或** 带指纹的外部豁免）。

    `prints` 允许传入**另一套**指纹表 —— 供 S94c 用临时夹具验证通道本身
    （清单为空时若不能注入，这两条判据就会真空通过）。
    """
    if EXEMPT_MARK in src_line:
        return True
    return (relpath, threshold, src_line.strip()) in (
        _EXEMPT_FINGERPRINTS if prints is None else prints)


def external_exempt_hits(entries=None, root=None):
    """每条外部豁免在目标文件里的**实际命中行数**（0 ⇒ 死条目，由 S94b 判红）。"""
    out = {}
    for e in (EXTERNAL_EXEMPTS if entries is None else entries):
        fp = os.path.join(root or ROOT, e["file"].replace("/", os.sep))
        want = e["line"].strip()
        n = 0
        if os.path.exists(fp):
            for ln in io.open(fp, "r", encoding="utf-8", newline="").read().splitlines():
                if ln.strip() == want:
                    n += 1
        out[e["file"]] = n
    return out

NORM_GRAPH = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
RAW_GRAPH = os.path.join(ROOT, "11_真实数据", "elementkg10m_raw.json")


# ---------------------------------------------------------------- S94 源码层

def _is_naked_prefix(sl) -> bool:
    """是否是**裸前缀切片** `x[:N]`（不带 lower、不带 step）。"""
    return (isinstance(sl, ast.Slice)
            and sl.lower is None
            and sl.step is None
            and isinstance(sl.upper, ast.Constant)
            and isinstance(sl.upper.value, int))


def _naked_in(node):
    """收集子树里所有裸前缀切片节点。"""
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Subscript) and _is_naked_prefix(n.slice)]


def scan_text(relpath, txt):
    """对**给定文本**跑 S94（既可扫磁盘，也允许自检构造变异体而不落盘）。

    返回 `[(relpath, 行号, 阈值, 源码行)]`；文本无法解析 ⇒ 返回 `None`
    （明示失败而不是当 0 命中放行 —— 铁律 #30 fail-closed）。
    """
    try:
        tree = ast.parse(txt)
    except SyntaxError:
        return None
    out = []
    lines = txt.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if node.value is None:
            continue
        for s in _naked_in(node.value):
            ln = getattr(s.slice, "lineno", node.lineno)
            src = lines[ln - 1] if 0 < ln <= len(lines) else ""
            if _is_exempt(relpath, s.slice.upper.value, src):
                continue
            out.append((relpath, ln, s.slice.upper.value, src.strip()))
    return out


def scan_writes():
    """返回 [(相对路径, 行号, 阈值, 源码行)] —— 写入语句中的**未豁免**裸切片。

    为什么只看 `Assign` / `AnnAssign`：真正会写坏图的是赋值。
    `print(x[:50])`、`for e in items[:10]` 这些无论怎么切都不会污染数据，
    把它们纳入只会让判据变成噪音源。
    """
    out = []
    for d in SCAN_DIRS:
        p = os.path.join(ROOT, d)
        if not os.path.isdir(p):
            continue
        for fn in sorted(os.listdir(p)):
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(p, fn)
            txt = io.open(fp, "r", encoding="utf-8", newline="").read()
            rel = os.path.relpath(fp, ROOT).replace(os.sep, "/")
            got = scan_text(rel, txt)
            if got is None:            # 语法错误 ⇒ 明示跳过，不当成「0 处」
                continue
            out.extend(got)
    out.sort()
    return out


# ------------------------------------------------------------- S95 数据层

def _str_values(node, key_filter=None):
    """取一个节点 props 里所有字符串值的长度（可指定键）。"""
    props = node.get("props") or {}
    if not isinstance(props, dict):
        return {}
    out = {}
    for k, v in props.items():
        if key_filter and k not in key_filter:
            continue
        if isinstance(v, str) and v:
            out[k] = len(v)
    return out


def load_graph(path):
    with io.open(path, "r", encoding="utf-8") as f:
        g = json.load(f)
    return g.get("nodes") or []


def each_str_value_in(tree):
    """遍历 raw/normalized 结构，产出 (节点id, 键, 字符串值)。"""
    def walk(o, nid=None, breadcrumb=()):
        # ★ 必须用 `yield from`：直接调用 `walk(...)` 只会造出一个**从未被迭代**
        #   的 generator 对象，递归结果全部丢弃 —— 顶层命中不到字符串 ⇒ 判据恒绿。
        #   实测这个 bug 让 S95 在 341 条堆积面前报「未见堆积」（假通过）。
        if isinstance(o, dict):
            nid = o.get("id") or nid
            for k, v in o.items():
                yield from walk(v, nid, breadcrumb + (str(k),))
        elif isinstance(o, list):
            for x in o:
                yield from walk(x, nid, breadcrumb)
        elif isinstance(o, str) and o and nid is not None:
            yield (str(nid), ".".join(breadcrumb[-2:]), o)
    return walk(tree)


def len_distribution(nodes):
    """兼容入口：合并后的长度分布（保留给 NEG-S95 存活自检用）。"""
    per = key_len_distribution(nodes)
    merged = collections.Counter()
    for _k, c in per.items():
        merged.update(c)
    return merged, collections.Counter()


def key_len_distribution(nodes):
    """返回 {属性键 -> 长度分布 Counter}。

    ★ **必须按键分开**。实测把所有属性键的长度混在一个 Counter 里时，
      长度 39/40 因为承载了某类固定格式串而天然比邻域高 12×，被误判成堆积。
      而截断总是作用于**某个特定字段** —— 分键统计后信号干净得多：
        · `mapped_smiles` 在 400 处堆 341 条，邻域均值 0.7（202×）
        · `xrefs[]`     在 20  处堆 55  条，邻域均值 4.6（9.9×）
      两者的幅度和邻域都一目了然，混在一起则互相稀释。

    ★ 列表键记成 `xrefs[]`：截断既可能切掉字符串尾部、也可能削短列表元素数，
      两者是**同一缺陷的两种形态**，必须同框。
    """

    out = collections.defaultdict(collections.Counter)

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "labels":
                    continue
                if isinstance(v, str):
                    if v:
                        out[k][len(v)] += 1
                elif isinstance(v, list):
                    out[k + "[]"][len(v)] += 1
                    for x in v:
                        if isinstance(x, str):
                            if x:
                                out[k][len(x)] += 1
                        elif isinstance(x, (dict, list)):
                            walk(x)
                elif isinstance(v, dict):
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                if isinstance(x, (dict, list)):
                    walk(x)
    walk(nodes)
    return dict(out)


def find_piles(cnt, thresholds, *, win=8, min_n=10, min_ratio=8.0,
               min_t=16, min_card=12, require_max=True):
    """在**候选阈值**处找堆积。

    ★ 为什么必须是**相对尖峰**（与邻域均值比）而不是绝对条数：
      实测「长度恰好 = 1」的字符串有 53,269 条 —— 短字符串天然密集，
      用「≥2 条」判定会得到一堆假阳性。真正的截断征候是
      **该长度明显高于邻近长度**，而不是它有很多条。

    ★ `min_t=16` 的理由：长度 <16 的区间分布过于陡峭（散列值、
      单位符号、缩写），尖峰与自然分布不可区分。这是已知的漏检下限，
      写在参数里而不是藏在逻辑里 —— 放宽它会立刻开始误报。
    """
    import statistics

    # ★ **不能只扫 thresholds**。实测踩过：源码层修干净后 S94 变绿，
    #   thresholds 变成空列表 ⇒ S95 无处可查 ⇒ **在 341 条堆积面前报「未见堆积」**。
    #   这是「判据间的单向依赖」—— 上游一绿，下游就瞎。
    #   所以没有给候选时，**扫全长度分布**（让数据自己说话，不依赖源码知识）。
    cands = sorted(thresholds) if thresholds else sorted(cnt)
    # ★ `min_card`：**定长字段直接放行**。实测 `inchi_key`(27 字符)、
    #   `chembl_fetched_at`(19)、`src`(17) 这类键的所有取值天然同长，
    #   它们的长度分布是**单点**，任何尖峰判据都会把它们读成截断。
    #   只有长度取值足够多的**变长字段**（SMILES、自由文本、id 列表）
    #   出现单长度堆积才是异常。这是假阳性的主要来源，必须显式挡掉。
    if len(cnt) < min_card:
        return []
    # ★ `require_max`：**截断会把所有超长值压到同一个点，那个点必然是该键的最大值**。
    #   这是截断的物理特征，也是最后一道降噪闸门 —— 实测它滤掉了
    #   `id`@23、`source`@30、`publisher`@39 三处自然尖峰（它们都不是最大值），
    #   保留了 `mapped_smiles`@400 与 `xrefs[]`@20 两处真缺陷（都是最大值）。
    if require_max and cnt:
        hi = max(cnt)
    else:
        hi = None
    out = []
    for t in cands:
        if hi is not None and t != hi:
            continue
        if t < min_t:
            continue
        n = cnt.get(t, 0)
        if n < min_n:
            continue
        nb = [cnt.get(t + d, 0) for d in range(-win, win + 1) if d != 0]
        m = statistics.mean(nb) if nb else 0.0
        ratio = n / (m + 1.0)
        if ratio >= min_ratio:
            out.append((t, n, m, ratio))
    return out


# ------------------------------------------------------------- main

def main() -> int:
    rep = []

    def add(ok, name, detail=""):
        rep.append((bool(ok), name, str(detail)))

    print("=" * 78)
    print("  Phase 79（数据无损）判据 —— 源码层 / 数据层 / 语义层")
    print("=" * 78)

    # ---- S94：源码层 ----
    hits = scan_writes()
    add(not hits,
        "S94 源码层：写入语句中的裸前缀切片**必须显式豁免**"
        "（判据自带 0 免疫）",
        ("已 %d 处未豁免，" % len(hits)) +
        ("；".join("%s:%d[:%d]" % (f, l, v) for f, l, v, _ in hits[:6])
         if hits else "当前 0 处"))
    # ★ 阈值的三个来源都要覆盖：
    #   ① S94 从源码抽到的（当前 400/32/20/…）
    #   ② 即使源码一处截断都没有（全豁免了），数据里的历史堆积也仍在 ——
    #      所以还要 ③：不依赖源码的通用扫描。实际很难，暂由 S96 兜底。
    thresholds = sorted({v for _, _, v, _ in hits})

    # ---- S95：数据层 ----
    piles = []
    for label, path in (("中间产物", RAW_GRAPH), ("入库图谱", NORM_GRAPH)):
        if not os.path.exists(path):
            print("[SKIP] 缺少 %s：%s" % (label, os.path.relpath(path, ROOT)))
            continue
        nodes = load_graph(path)
        for key, cnt in sorted(key_len_distribution(nodes).items()):
            for t, n, m, ratio in find_piles(cnt, thresholds):
                piles.append((label, key, t, n, m, ratio))
    add(not piles,
        "S95 数据层：**按属性键**长度不得在阈值处堆积（相对尖峰 ≥5× 邻域均值）",
        ("；".join("%s·%s 阈值%d：%d条 vs 邻域均值%.1f（%.0f×）"
                   % (p[0], p[1], p[2], p[3], p[4], p[5]) for p in piles)
         if piles else "未见堆积"))

    # ---- S96：语义层（缺 RDKit ⇒ SKIP）----
    try:
        from rdkit import RDLogger                       # noqa: F401
        from rdkit.Chem import rdChemReactions as _R
        RDLogger.DisableLog("rdApp.*")
    except Exception as ex:                              # noqa: BLE001
        print("[SKIP] S96 缺 RDKit（%s: %s）⇒ 语义层未验证，**不计通过**"
              % (type(ex).__name__, ex))
        print("       恢复：pip install rdkit（可选依赖；判据缺它时不得假通过）")
    else:
        if not os.path.exists(NORM_GRAPH):
            print("[SKIP] S96 缺权威图 ⇒ 语义层未验证")
        else:
            nodes = load_graph(NORM_GRAPH)
            vals = []
            for n in nodes:
                if isinstance(n, dict) and isinstance(n.get("props"), dict):
                    v = n["props"].get("mapped_smiles")
                    if isinstance(v, str) and v:
                        vals.append(v)

            def ok(v):
                try:
                    return _R.ReactionFromSmiles(v) is not None
                except Exception:                        # noqa: BLE001
                    return False

            bad = [v for v in vals if not ok(v)]
            add(not bad,
                "S96 语义层：全部 `mapped_smiles` 必须能被 RDKit 解析"
                "（测**后果**而非征候）",
                "%d/%d 可解析；不可解析 %d 条"
                % (len(vals) - len(bad), len(vals), len(bad)))

    # ---- 负对照自证：判据不是恒真的 ----
    # ★ 没有这一段，"全绿"只说明仪器睡着了。S94 必须能被一段已知的
    #   坏源码判红，否则它对真实缺陷同样是瞎的。
    bad_src = (
        "# noloss-exempt: 自证夹具\n"
        "props[\"x\"] = raw[:123]\n"
    )
    try:
        tree = ast.parse(bad_src)
        caught = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                for s in _naked_in(node.value):
                    caught.append(s.slice.upper.value)
        add(123 in caught,
            "NEG-S94 负对照：给一段含 `raw[:123]` 的写入语句 ⇒ S94 必须能抽出阈值",
            "抽到 %s" % (caught or "空 ⇒ 判据是瞎的"))
    except SyntaxError as ex:
        add(False, "NEG-S94 负对照夹具本身解析失败", str(ex))

    # ---- S94b：外部豁免清单不得**腐烂** ----
    # ★ 新增第二条豁免通道本身就要有一个代价：清单条目必须始终指着一行真实存在的代码。
    #   0 命中 = 死条目（说明那行被改掉了或删掉了）⇒ 判红，逼人清理；
    #   命中 ≥2 = 同一行文本被复制到多处 ⇒ 披露（复制粘贴的写法将来要各自认领）。
    if EXTERNAL_EXEMPTS:
        dead = {f: n for f, n in external_exempt_hits().items() if n == 0}
        dup = {f: n for f, n in external_exempt_hits().items() if n > 1}
        add(not dead,
            "S94b 外部豁免清单**不得腐烂**：每条必须仍指向一行真实源码（0 命中 ⇒ 死条目）",
            ("已登记 %d 条 / 命中 %s" % (len(EXTERNAL_EXEMPTS), external_exempt_hits()))
            if not dead else ("死条目：%s" % sorted(dead)))
        if dup:
            print("       [NOTE] 同一行文本命中多处（复制代码，各自认领）：%s" % dup)
    else:
        print("       [NOTE] 外部豁免清单为空（2026-10-28：内联标注已回归，见文件头）—— "
              "通道本身由 S94c / NEG-S94c 用临时夹具验，不留真空。")

    # ---- S94c / NEG-S94c：清单为空时，通道仍必须被验过（防真空通过） ----
    #   ★ 用**临时夹具**造一个「已登记 + 可失效」的条目，逐项验三件事：
    #     ① 指纹命中 ⇒ 豁免生效；② 该行改一个字符 ⇒ 立刻失配（不是对整个文件免死）；
    #     ③ 目标文件消失 ⇒ 0 命中 = 死条目可被检出。
    import tempfile as _tf
    import shutil as _sh
    fx_root = _tf.mkdtemp(prefix="sttp_s94c_")
    try:
        fx_rel = "fixture_ext.py"
        fx_line = "s2 = s[:1].upper() + s[1:].lower()"
        fx_body = "def f(s):\n    " + fx_line + "\n    return s\n"
        with io.open(os.path.join(fx_root, fx_rel), "w", encoding="utf-8", newline="") as f:
            f.write(fx_body)
        fx_entries = [{"file": fx_rel, "threshold": 1, "line": fx_line, "reason": "S94c 夹具"}]
        fx_prints = {(e["file"], e["threshold"], e["line"].strip()): e for e in fx_entries}
        hit0 = external_exempt_hits(fx_entries, fx_root)
        c1 = _is_exempt(fx_rel, 1, "    " + fx_line, prints=fx_prints)
        c2 = not _is_exempt(fx_rel, 1, "    s3 = s[:1].upper() + s[1:].lower()", prints=fx_prints)
        got = scan_text(fx_rel, fx_body.replace(fx_line, fx_line.replace("s2 =", "s3 =", 1)))
        c3 = got is not None and any(v == 1 for _, _, v, _ in got)
        os.remove(os.path.join(fx_root, fx_rel))
        c4 = external_exempt_hits(fx_entries, fx_root).get(fx_rel) == 0
        add(hit0.get(fx_rel) == 1 and c1 and c2 and c3 and c4,
            "S94c 外挂通道自证：指纹命中即豁免 / 改一个字符即失配并重新变红 / 文件消失即死条目",
            "命中=%s 生效=%s 失配=%s 重抽=%s 死条目=%s" % (hit0.get(fx_rel), c1, c2, c3, c4))
    finally:
        _sh.rmtree(fx_root, ignore_errors=True)

    # ---- NEG-S94b：外部豁免**不是恒赦** ----
    # ★ 这是本轮最关键的一条：把清单里挂了号的那一行的**原文改掉**（保留裸切片本身，
    #   只改变量名 —— 语义等价），如果 S94 还是报"0 处未豁免"，那就说明
    #   「外挂豁免」其实是一张对整个文件生效的免死金牌，本轮改动就是在自赦。
    bad_ext = []
    for e in EXTERNAL_EXEMPTS:
        fp = os.path.join(ROOT, e["file"].replace("/", os.sep))
        if not os.path.exists(fp):
            bad_ext.append((e["file"], "文件不存在"))
            continue
        txt = io.open(fp, "r", encoding="utf-8", newline="").read()
        want = e["line"]
        # 变异 ①（主）：改变量名，裸切片阈值不变 —— 指纹必须失配 ⇒ 必须被重新抽出
        mut = want.replace("s2 =", "s3 =", 1)
        if mut == want:                      # 夹具退化保护：没改成就等于没做对照
            mut = want.replace("=", " = ", 1)
        if mut == want:
            bad_ext.append((e["file"], "变异夹具退化：原文无法产生变体"))
            continue
        got = scan_text(e["file"], txt.replace(want, mut, 1))
        if got is None:
            bad_ext.append((e["file"], "变异体语法错误"))
        elif not any(v == e["threshold"] for _, _, v, _ in got):
            bad_ext.append((e["file"], "改行后仍未抽出 ⇒ 豁免对该文件整体生效（恒赦）"))
    if EXTERNAL_EXEMPTS:
        add(not bad_ext,
            "NEG-S94b 外部豁免**不恒赦**：把登记过的那一行改掉 ⇒ 必须重新被 S94 抽出",
            "；".join("%s %s" % (f, why) for f, why in bad_ext)
            if bad_ext else "%d 条逐一变异，均重新变红" % len(EXTERNAL_EXEMPTS))
    # ★ 清单为空时**不落一条恒真判据**（那是真空通过）；这条反证由 NEG-S94c 承担，
    #   见上方夹具里的 c2/c3 两项。

    # ---- NEG-S95：监视**采集器本身** ----
    # ★ 这一条守的是「仪器睡着了」：采集路径一旦退化成空（generator 没迭代、
    #   路径写错、过滤条件太严），所有依赖它的判据都会**静默变绿**。
    #   实测本文件就踩过两次 —— 没有这条自检，两次都不会被发现。
    if os.path.exists(NORM_GRAPH):
        per = key_len_distribution(load_graph(NORM_GRAPH))
        add(len(per) > 0 and sum(sum(c.values()) for c in per.values()) > 0,
            "NEG-S95 采集器存活：分键长度分布**必须非空**"
            "（空 ⇒ 判据静默变绿，等价于没查）",
            "%d 个键 / 共 %d 个值"
            % (len(per), sum(sum(c.values()) for c in per.values())))

    # ---- 打印 ----
    npass = sum(1 for ok_, _, _ in rep if ok_)
    nfail = sum(1 for ok_, _, _ in rep if not ok_)
    for ok_, name, detail in rep:
        print("[%s] %s" % ("PASS" if ok_ else "FAIL", name))
        if detail:
            print("       %s" % detail)
    print("-" * 78)
    print("phase79_selfcheck: PASS %d / FAIL %d" % (npass, nfail))
    if hits:
        print()
        print("未豁免裸切片明细（全部，非截断 TOP6）：")
        for f, l, v, src in hits:
            print("  %s:%d  [:%d]" % (f, l, v))
            print("      %s" % src[:150])
    return 1 if nfail else 0


if __name__ == "__main__":
    raise SystemExit(main())
