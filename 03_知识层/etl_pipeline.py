# -*- coding: utf-8 -*-
"""
公式知识图谱 · ETL 主脚本（Phase 2：多源接入 & 图库化）
================================================================

本脚本实现《02_数据层/ETL管道设计.md》中 Extract → Transform → Load 的核心代码，
把各数据源归一化为**符合《图Schema_v0.1》的 nodes / edges**，并写出 Neo4j 导入格式。

设计要点
--------
1. 多源适配器框架（SourceAdapter）：每个源一个子类，统一 run() 接口。
   - MathXivAdapter   : 完整实现（端到端跑通 06_PoC/sample_mathxiv.json）
   - MathGraphAdapter : CSV 惰性读取 + 1% 采样（duckdb/polars，try 守卫）
   - PhysicsBabelAdapter : 读 equation 列的桩实现
   - ElementKGAdapter    : 解析 KG 导出（三元组/点边表）的桩实现
2. 节点全局 id 约定 ``<source>:<local_id>``，source 用缩写：
   MX=MathXiv, MG=math-graph, PB=PhysicsBabel, EK=ElementKG, WD=Wikidata。
3. 每条边强制携带 ``confidence`` / ``explicit_or_inferred`` / ``source``。
   ``explicit_or_inferred`` 由原始 ``kind`` 映射：
       explicit_citation -> explicit
       llm_inferred      -> inferred
   （原始 kind 一并保留在边上，便于区分“规则推断 vs 大模型推断”）
4. definition_bank -> 生成 Symbol 节点 + ``defines`` 边；
   公式节点出现的符号 -> ``has_symbol`` 边。

依赖
----
核心 MathXiv 路径**仅依赖标准库**（json/csv/regex），可选依赖全部 try 守卫：
   - networkx : queries.py 的兜底图查询（本脚本不强制）
   - polars / duckdb : math-graph 大文件惰性采样
   - neo4j    : load_neo4j.py 写库

运行
----
    python etl_pipeline.py                      # 默认跑 MathXiv 样例
    python etl_pipeline.py --source all         # 跑全部适配器
    python etl_pipeline.py --source mathgraph --data-dir <目录> --sample 0.01
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import random
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ----------------------------------------------------------------------------
# 全局常量
# ----------------------------------------------------------------------------

SCHEMA_VERSION = "v0.1"

# 源系统 -> 全局 id 缩写
SOURCES: Dict[str, str] = {
    "mathxiv": "MX",
    "mathgraph": "MG",
    "physicsbabel": "PB",
    "elementkg": "EK",
    "wikidata": "WD",
}

# 原始边 kind -> explicit_or_inferred 枚举（见《图Schema》§3）
#   注意：schema 的三值枚举为 explicit / inferred / llm_inferred；
#   本管道按任务约定归并为 explicit / inferred 两值，原始 kind 保留在边上，
#   因此“llm_inferred”的信息并未丢失（可用 kind 过滤还原）。
KIND_TO_FLAG: Dict[str, str] = {
    "explicit_citation": "explicit",
    "explicit": "explicit",
    "defines": "explicit",
    "reactant_of": "explicit",
    "product_of": "explicit",
    "part_of": "explicit",
    "formal": "inferred",        # 由确定性规则 / AST 解析得到
    "inferred": "inferred",
    "has_symbol": "inferred",    # 符号出现为确定性解析结果
    "same_as": "inferred",
    "shares_symbol": "inferred",
    "llm_inferred": "inferred",  # 任务约定：大模型推断 -> inferred
}

# 写出 CSV 的列定义（Neo4j admin import 表头）
NODE_HEADER = [
    ":ID", ":LABEL", "local_id", "name", "type", "latex", "informal", "proof",
    "symbols", "meaning", "domain", "source", "source_ref", "wikidata_qid",
    "confidence", "explicit_or_inferred", "created_at", "version",
]

REL_HEADER = [
    ":START_ID", ":END_ID", ":TYPE", "kind", "confidence",
    "explicit_or_inferred", "source", "evidence", "created_at",
]

# 路径基准：脚本位于 03_知识层/，项目根为其父目录
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_SAMPLE = os.path.join(ROOT, "06_PoC", "sample_mathxiv.json")
DEFAULT_OUT_DIR = os.path.join(ROOT, "06_PoC", "etl")
# 桩适配器的小样本 fixture（无真实数据时也能演示映射与归一化流程）
SAMPLES_DIR = os.path.join(DEFAULT_OUT_DIR, "samples")
DEFAULT_PB_SAMPLE = os.path.join(SAMPLES_DIR, "physicsbabel_sample.json")
DEFAULT_EK_SAMPLE = os.path.join(SAMPLES_DIR, "elementkg_sample.json")
DEFAULT_MG_DIR = os.path.join(SAMPLES_DIR, "mathgraph")


# ----------------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------------

def now_iso() -> str:
    """返回带时区（东八区）的 ISO8601 时间戳。"""
    tz = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.now(tz).isoformat(timespec="seconds")


def slugify(sym: str) -> str:
    """把 LaTeX 符号转成可作 id 的 slug：``\\nabla``->``nabla``，``T_p M``->``t_p_m``。"""
    s = (sym or "").strip()
    s = s.replace("\\", " ")
    s = re.sub(r"[^0-9A-Za-z]+", "_", s)
    s = s.strip("_").lower()
    return s or "sym"


def gid(source_key: str, local_id: str) -> str:
    """构造全局节点 id：``<source_abbr>:<local_id>``。"""
    abbr = SOURCES.get(source_key, source_key.upper())
    return f"{abbr}:{local_id}"


def _csv_encode(v: Any) -> str:
    """结构化值 → **可反向解析**的 JSON 串（Phase 32 根治「repr 串生成链」）。

    ⚠ 绝不能对 dict / list 用 `str()`：那产出的是 **Python repr**（单引号、True/False/None），
    **不是合法 JSON** —— 下游 `json.loads` 静默失败，属性形态在链路上「漂移」为不可解析字符串
    （「属性形态 → 静默降级」，即 repr 串生成链）。JSON 可 `json.loads` 往返，repr 不可。
    """
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple, set)):
        return json.dumps(list(v), ensure_ascii=False)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return str(v)


def _join_list(v: Any) -> str:
    """列表属性 -> CSV 单元格。

    标量列表用分号分隔；**一旦含结构体**（dict/list）则整体走 JSON —— 否则
    `";".join(str(x))` 会把 dict 变成 repr 串（Phase 32 修复）。
    """
    if v is None:
        return ""
    if isinstance(v, (list, tuple, set)):
        if any(isinstance(x, (dict, list, tuple, set)) for x in v):
            return json.dumps(list(v), ensure_ascii=False)
        return ";".join(str(x) for x in v)
    return str(v)


def _cell(v: Any) -> str:
    """任意值 -> CSV 单元格字符串（None -> 空串）。"""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple, set)):
        return _join_list(v)
    return str(v)


# ----------------------------------------------------------------------------
# normalize：全局 id / 属性挂靠 / 置信度标注
# ----------------------------------------------------------------------------

def normalize(source_key: str, nodes: List[dict], edges: List[dict],
              default_domain: str = "math") -> Tuple[List[dict], List[dict]]:
    """把“适配器本地记录”归一化为符合图 Schema 的全局 nodes / edges。

    参数
    ----
    source_key : 源系统键（如 "mathxiv"），用于推导全局 id 前缀与 source 属性
    nodes : [{"local_id":..., "labels":[...], "props":{...}}, ...]
    edges : [{"source":local_id, "target":local_id, "type":..., "kind":...,
              "props":{...}}, ...]

    返回
    ----
    (nodes, edges)：全局化后的规范记录
    """
    ts = now_iso()
    out_nodes: List[dict] = []
    for n in nodes:
        local_id = n["local_id"]
        props = dict(n.get("props") or {})
        props.setdefault("source", source_key)
        props.setdefault("created_at", ts)
        props.setdefault("version", SCHEMA_VERSION)
        props.setdefault("domain", default_domain)
        props.setdefault("confidence", 1.0)
        # 节点默认视为显式抽取
        props.setdefault("explicit_or_inferred", "explicit")
        out_nodes.append({
            "id": gid(source_key, local_id),
            "local_id": local_id,
            "labels": list(n.get("labels") or []),
            "props": props,
        })

    out_edges: List[dict] = []
    for e in edges:
        kind = e.get("kind") or e.get("type") or "depends_on"
        flag = KIND_TO_FLAG.get(kind, e.get("explicit_or_inferred", "inferred"))
        props = dict(e.get("props") or {})
        props["kind"] = kind
        props["explicit_or_inferred"] = flag
        props.setdefault("confidence", float(e.get("confidence", 0.0) or 0.0))
        props.setdefault("source", source_key)
        props.setdefault("created_at", ts)
        s = gid(source_key, e["source"])
        t = gid(source_key, e["target"])
        out_edges.append({
            "id": f"{s}->{t}[{e.get('type', kind)}]",
            "source": s,
            "target": t,
            "type": e.get("type", "derived_from"),
            "kind": kind,
            "props": props,
        })
    return out_nodes, out_edges


# ----------------------------------------------------------------------------
# 适配器基类
# ----------------------------------------------------------------------------

class SourceAdapter:
    """数据源适配器基类：extract（抽取）-> transform（映射）-> run（归一化）。"""

    key = "base"
    abbr = "XX"

    def extract(self, **kwargs) -> Any:
        raise NotImplementedError

    def transform(self, raw: Any) -> Tuple[List[dict], List[dict]]:
        raise NotImplementedError

    def run(self, **kwargs) -> Tuple[List[dict], List[dict]]:
        raw = self.extract(**kwargs)
        nodes, edges = self.transform(raw)
        return normalize(self.key, nodes, edges)


# ----------------------------------------------------------------------------
# ① MathXiv 适配器（完整实现）
# ----------------------------------------------------------------------------

class MathXivAdapter(SourceAdapter):
    """MathXiv / ArxiTeX 风格 JSON 适配器（端到端实现）。

    输入结构（见 06_PoC/sample_mathxiv.json）：
        nodes[] : {id, type, latex, informal_text, proof, symbols[]}
        dependencies[] : {source, target, kind, confidence}
        definition_bank[] : {symbol, definition, node_ref?}
    """

    key = "mathxiv"
    abbr = "MX"

    # MathXiv type -> 额外 Schema 标签
    TYPE_LABEL = {
        "definition": "Definition",
        "lemma": "Lemma",
        "theorem": "Theorem",
        "proposition": "Proposition",
        "corollary": "Corollary",
    }

    def __init__(self, path: str = DEFAULT_SAMPLE):
        self.path = path

    # -- Extract ------------------------------------------------------------
    def extract(self, path: Optional[str] = None, **kwargs) -> dict:
        path = path or self.path
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"[MX] 已读取样例：{path}")
        return data

    # -- Transform ----------------------------------------------------------
    def transform(self, data: dict) -> Tuple[List[dict], List[dict]]:
        nodes: List[dict] = []
        edges: List[dict] = []
        domain = data.get("domain", "math")
        paper = data.get("paper_id", "")

        # (a) 工件节点：definition/lemma/theorem ... -> Formula(+精化标签)
        for n in data.get("nodes", []):
            local = n["id"]
            typ = (n.get("type") or "statement").lower()
            labels = ["Formula"]
            extra = self.TYPE_LABEL.get(typ)
            if extra:
                labels.append(extra)
            props = {
                "name": typ,                       # 人类可读名（占位，可被标题增强）
                "type": typ,
                "latex": n.get("latex", ""),
                "informal": n.get("informal_text", ""),
                "proof": n.get("proof"),
                "symbols": n.get("symbols", []),
                "domain": domain,
                "source_ref": paper,
            }
            nodes.append({"local_id": local, "labels": labels, "props": props})

        # (b) 依赖边：工件 -> 工件，语义为 derived_from
        for d in data.get("dependencies", []):
            edges.append({
                "source": d["source"],
                "target": d["target"],
                "type": "derived_from",
                "kind": d.get("kind", "explicit_citation"),
                "props": {
                    "confidence": float(d.get("confidence", 0.0)),
                    "evidence": f"paper:{paper}",
                },
            })

        # (c) definition_bank -> Symbol 节点 + defines 边
        sym_index: Dict[str, str] = {}   # 原始符号 -> 本地 Symbol id

        def ensure_symbol(raw_sym: str, meaning: str = "") -> str:
            """惰性创建 Symbol 节点，返回其本地 id。"""
            if raw_sym in sym_index:
                return sym_index[raw_sym]
            sym_local = f"sym:{slugify(raw_sym)}"
            sym_index[raw_sym] = sym_local
            nodes.append({
                "local_id": sym_local,
                "labels": ["Symbol"],
                "props": {
                    "name": raw_sym,
                    "latex": raw_sym,
                    "meaning": meaning,
                    "scope": "local",
                    "symbol_type": "variable",
                    "source_ref": paper,
                },
            })
            return sym_local

        for entry in data.get("definition_bank", []):
            raw_sym = entry.get("symbol", "")
            if not raw_sym:
                continue
            sym_local = ensure_symbol(raw_sym, entry.get("definition", ""))
            ref = entry.get("node_ref")
            if ref:
                # 被定义工件 -> Symbol：defines（显式，来自 definition_bank）
                edges.append({
                    "source": ref,
                    "target": sym_local,
                    "type": "defines",
                    "kind": "defines",
                    "props": {"confidence": 0.95, "evidence": "definition_bank"},
                })

        # (d) has_symbol 边：公式中出现该符号（确定性解析 -> inferred）
        for n in data.get("nodes", []):
            for raw_sym in n.get("symbols", []):
                if not raw_sym:
                    continue
                sym_local = ensure_symbol(raw_sym)
                edges.append({
                    "source": n["id"],
                    "target": sym_local,
                    "type": "has_symbol",
                    "kind": "has_symbol",
                    "props": {"confidence": 1.0, "evidence": "symbol_scan"},
                })

        return nodes, edges


# ----------------------------------------------------------------------------
# ② math-graph 适配器（CSV 惰性读取 + 1% 采样，try 守卫）
# ----------------------------------------------------------------------------

class MathGraphAdapter(SourceAdapter):
    """uw-math-ai/math-graph（8.46 GB CSV）适配器。

    真实场景用 duckdb / polars 惰性扫描 + 1% 伯努利采样，禁止整表入内存。
    缺少 duckdb/polars 或未提供数据目录时，**跳过并提示**，不中断主流程。
    """

    key = "mathgraph"
    abbr = "MG"

    STATEMENT_FILES = ["statement_informal.csv"]
    DEP_FILES = ["formal_dependency.csv", "informal_dependency.csv"]

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir

    def _pick_engine(self) -> Optional[str]:
        try:
            import duckdb  # noqa: F401
            return "duckdb"
        except ImportError:
            pass
        try:
            import polars  # noqa: F401
            return "polars"
        except ImportError:
            pass
        print("[MG] 缺少 duckdb/polars，无法对大文件惰性采样；跳过 math-graph。"
              "  可运行: pip install duckdb polars")
        return None

    def extract(self, data_dir: Optional[str] = None, sample: float = 0.01,
                **kwargs) -> dict:
        d = data_dir or self.data_dir
        if not d or not os.path.isdir(d):
            print(f"[MG] 未提供有效数据目录（{d or 'None'}），返回空结果（桩）。")
            return {"statements": [], "dependencies": []}

        engine = self._pick_engine()
        if engine is None:
            return {"statements": [], "dependencies": []}

        stmt_path = self._find(d, self.STATEMENT_FILES)
        dep_paths = [self._find(d, [f]) for f in self.DEP_FILES]

        statements: List[dict] = []
        dependencies: List[dict] = []

        if stmt_path:
            statements = self._sample_csv(stmt_path, engine, sample)
            print(f"[MG] statement 采样 {len(statements)} 行（{engine}, p={sample}）")
        for dp, fname in zip(dep_paths, self.DEP_FILES):
            if dp:
                rows = self._sample_csv(dp, engine, sample)
                for r in rows:
                    dependencies.append({
                        "source": str(r.get("source_id", "")),
                        "target": str(r.get("target_id", "")),
                        "kind": ("explicit_citation" if "formal" in fname
                                 else "llm_inferred"),
                        "confidence": float(r.get("confidence", 0.9) or 0.9),
                    })
                print(f"[MG] {fname} 采样 {len(rows)} 行（{engine}, p={sample}）")

        return {"statements": statements, "dependencies": dependencies}

    @staticmethod
    def _find(root: str, names: List[str]) -> Optional[str]:
        for base, _dirs, files in os.walk(root):
            for name in names:
                if name in files:
                    return os.path.join(base, name)
        return None

    @staticmethod
    def _sample_csv(path: str, engine: str, sample: float) -> List[dict]:
        """用 duckdb USING SAMPLE 或 polars 采样读取。"""
        if engine == "duckdb":
            import duckdb
            con = duckdb.connect()
            try:
                q = (f"SELECT * FROM read_csv_auto('{path}') "
                     f"USING SAMPLE {sample * 100} PERCENT (bernoulli)")
                return con.execute(q).fetchdf().to_dict("records")
            finally:
                con.close()
        # polars
        import polars as pl
        df = pl.scan_csv(path, infer_schema_length=1000).collect()
        n = max(1, int(len(df) * sample))
        return df.sample(n=n, seed=42).to_dicts()

    def transform(self, raw: dict) -> Tuple[List[dict], List[dict]]:
        nodes: List[dict] = []
        edges: List[dict] = []
        ids = set()
        for s in raw.get("statements", []):
            local = str(s.get("id", ""))
            if not local:
                continue
            ids.add(local)
            nodes.append({
                "local_id": local,
                "labels": ["Formula", "Statement"],
                "props": {
                    "type": (s.get("type") or "statement"),
                    "latex": s.get("latex", ""),
                    "informal": s.get("informal_text", ""),
                    "domain": "math",
                    "source_ref": "math-graph",
                },
            })
        for d in raw.get("dependencies", []):
            if d["source"] in ids and d["target"] in ids:
                edges.append({
                    "source": d["source"], "target": d["target"],
                    "type": "derived_from", "kind": d["kind"],
                    "props": {"confidence": d["confidence"]},
                })
        return nodes, edges


# ----------------------------------------------------------------------------
# ③ PhysicsBabel 适配器（读 equation 列，桩实现）
# ----------------------------------------------------------------------------

class PhysicsBabelAdapter(SourceAdapter):
    """PhysicsBabel / Vashy（2.32M 方程）适配器桩。

    读取含 ``equation`` 列的 JSON/CSV 记录，生成 Formula(Equation) 节点与 Symbol 节点，
    并按“共享符号”生成 shares_symbol 边。无数据时返回空结果，不报错。
    """

    key = "physicsbabel"
    abbr = "PB"

    def __init__(self, path: Optional[str] = None, sample: float = 0.01):
        self.path = path
        self.sample = sample

    def extract(self, path: Optional[str] = None, **kwargs) -> List[dict]:
        path = path or self.path
        if not path or not os.path.exists(path):
            print(f"[PB] 未提供方程数据（{path or 'None'}），返回空结果（桩）。")
            return []
        rows: List[dict] = []
        try:
            if path.endswith(".json"):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                result = data if isinstance(data, list) else data.get("equations", [])
            elif path.endswith(".csv"):
                with open(path, "r", encoding="utf-8", newline="") as f:
                    result = list(csv.DictReader(f))
            else:
                result = []
            rows = list(result)
        except Exception as e:  # 桩：任何读取异常都不致命
            print(f"[PB] 读取失败（{e}），返回空结果。")
            return []
        # 仅对“大表”做采样；小 fixture 全量处理，便于验证映射
        if len(rows) > 1000:
            k = max(1, int(len(rows) * self.sample))
            rows = random.sample(rows, k)
        print(f"[PB] 读取 {len(rows)} 条方程")
        return rows

    def transform(self, raw: List[dict]) -> Tuple[List[dict], List[dict]]:
        nodes: List[dict] = []
        edges: List[dict] = []
        created: set = set()        # 已创建的 Symbol 本地 id
        sym_of: Dict[str, str] = {}  # 符号 -> 首个方程本地 id
        for r in raw:
            eq = r.get("equation_str") or r.get("equation") or r.get("latex")
            if not eq:
                continue
            eid = str(r.get("equation_id") or r.get("id") or slugify(eq)[:32])
            nodes.append({
                "local_id": eid,
                "labels": ["Formula", "Equation"],
                "props": {
                    "type": "equation",
                    "latex": str(eq),
                    "informal": r.get("informal_text", ""),
                    "domain": r.get("domain", "physics"),
                    "is_named_law": r.get("is_named_law", False),
                    "source_ref": "physicsbabel",
                },
            })
            for sym in (r.get("symbols") or []):
                sid = f"sym:{slugify(sym)}"
                if sid not in created:
                    created.add(sid)
                    nodes.append({
                        "local_id": sid, "labels": ["Symbol"],
                        "props": {"name": str(sym), "latex": str(sym),
                                  "meaning": "", "scope": "global",
                                  "symbol_type": "variable"},
                    })
                edges.append({
                    "source": eid, "target": sid, "type": "has_symbol",
                    "kind": "has_symbol", "props": {"confidence": 1.0},
                })
                # 共享符号：同符号的方程之间建 shares_symbol
                if sym in sym_of and sym_of[sym] != eid:
                    edges.append({
                        "source": eid, "target": sym_of[sym],
                        "type": "shares_symbol", "kind": "shares_symbol",
                        "props": {"confidence": 0.6, "symbol": str(sym)},
                    })
                else:
                    sym_of[sym] = eid
        return nodes, edges


# ----------------------------------------------------------------------------
# ④ ElementKG 适配器（解析 KG 导出，桩实现）
# ----------------------------------------------------------------------------

class ElementKGAdapter(SourceAdapter):
    """ElementKG 2.0 适配器桩。

    支持两种导出形态：
      - 三元组表 CSV：``head, head_type, relation, tail, tail_type``
      - 点边 JSON：``{"entities":[...], "relations":[...]}``
    映射为 Element/Molecule/Reaction 节点与 reactant_of/product_of 边。无数据返回空。
    """

    key = "elementkg"
    abbr = "EK"

    TYPE_MAP = {
        "element": "Element", "functional_group": "Molecule",
        "molecule": "Molecule", "reaction": "Reaction",
        "experiment": "Experiment", "substance": "Molecule",
    }
    REL_MAP = {
        "reactant_of": ("reactant_of", "reactant_of"),
        "product_of": ("product_of", "product_of"),
        "part_of": ("part_of", "part_of"),
    }

    def __init__(self, path: Optional[str] = None, sample: float = 0.01):
        self.path = path
        self.sample = sample

    def extract(self, path: Optional[str] = None, **kwargs) -> dict:
        path = path or self.path
        if not path or not os.path.exists(path):
            print(f"[EK] 未提供 KG 导出（{path or 'None'}），返回空结果（桩）。")
            return {"entities": [], "relations": []}
        try:
            if path.endswith(".json"):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                ents = data.get("entities") or data.get("nodes") or []
                rels = data.get("relations") or data.get("edges") or []
            else:
                ents, rels, seen = [], [], set()
                with open(path, "r", encoding="utf-8", newline="") as f:
                    for row in csv.DictReader(f):
                        h, t = row.get("head"), row.get("tail")
                        for k, ty in ((h, row.get("head_type")), (t, row.get("tail_type"))):
                            if k and k not in seen:
                                seen.add(k)
                                ents.append({"id": k, "type": ty or "molecule"})
                        rels.append({"head": h, "tail": t,
                                     "relation": row.get("relation")})
        except Exception as e:
            print(f"[EK] 读取失败（{e}），返回空结果。")
            return {"entities": [], "relations": []}
        print(f"[EK] 读取实体 {len(ents)} / 关系 {len(rels)}（桩）")
        return {"entities": ents, "relations": rels}

    def transform(self, raw: dict) -> Tuple[List[dict], List[dict]]:
        nodes: List[dict] = []
        edges: List[dict] = []
        ids = set()
        for e in raw.get("entities", []):
            local = str(e.get("id", ""))
            if not local:
                continue
            ids.add(local)
            typ = (e.get("type") or "molecule").lower()
            labels = ["Element"] if typ == "element" else (
                ["Reaction"] if typ == "reaction" else ["Molecule"])
            nodes.append({
                "local_id": local, "labels": labels,
                "props": {"name": e.get("name", local),
                          "type": typ, "domain": "chemistry",
                          "source_ref": "elementkg"},
            })
        for r in raw.get("relations", []):
            h, t = r.get("head"), r.get("tail")
            rel = (r.get("relation") or "").lower()
            if h not in ids or t not in ids:
                continue
            rtype, kind = self.REL_MAP.get(rel, (rel or "related", rel or "related"))
            edges.append({
                "source": h, "target": t, "type": rtype, "kind": kind,
                "props": {"confidence": 0.9},
            })
        return nodes, edges


# ----------------------------------------------------------------------------
# 适配器注册表
# ----------------------------------------------------------------------------

def build_adapters(args: argparse.Namespace) -> List[SourceAdapter]:
    registry = {
        "mathxiv": lambda: MathXivAdapter(args.sample_file),
        # 未显式指定数据目录时，回退到 06_PoC/etl/samples 下的极小 fixture
        "mathgraph": lambda: MathGraphAdapter(args.data_dir or DEFAULT_MG_DIR),
        "physicsbabel": lambda: PhysicsBabelAdapter(
            args.data_dir or DEFAULT_PB_SAMPLE, args.sample),
        "elementkg": lambda: ElementKGAdapter(
            args.data_dir or DEFAULT_EK_SAMPLE, args.sample),
    }
    if args.source == "all":
        return [f() for f in registry.values()]
    return [registry[args.source]()]


# ----------------------------------------------------------------------------
# 写出函数（Neo4j 导入格式）
# ----------------------------------------------------------------------------

def _node_row(n: dict) -> List[str]:
    p = n["props"]
    return [
        n["id"],
        ";".join(n["labels"]),
        n["local_id"],
        _cell(p.get("name")),
        _cell(p.get("type")),
        _cell(p.get("latex")),
        _cell(p.get("informal")),
        _cell(p.get("proof")),
        _join_list(p.get("symbols")),
        _cell(p.get("meaning")),
        _cell(p.get("domain")),
        _cell(p.get("source")),
        _cell(p.get("source_ref")),
        _cell(p.get("wikidata_qid")),
        _cell(p.get("confidence")),
        _cell(p.get("explicit_or_inferred")),
        _cell(p.get("created_at")),
        _cell(p.get("version")),
    ]


def _rel_row(e: dict) -> List[str]:
    p = e["props"]
    return [
        e["source"],
        e["target"],
        e["type"],
        _cell(p.get("kind")),
        _cell(p.get("confidence")),
        _cell(p.get("explicit_or_inferred")),
        _cell(p.get("source")),
        _cell(p.get("evidence")),
        _cell(p.get("created_at")),
    ]


def write_nodes_csv(nodes: List[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(NODE_HEADER)
        for n in nodes:
            w.writerow(_node_row(n))
    print(f"[OUT] nodes.csv      -> {path}  ({len(nodes)} 行)")


def write_relationships_csv(edges: List[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(REL_HEADER)
        for e in edges:
            w.writerow(_rel_row(e))
    print(f"[OUT] relationships.csv -> {path}  ({len(edges)} 行)")


def write_normalized_json(nodes: List[dict], edges: List[dict], path: str) -> None:
    """写出归一化结果（供 load_neo4j.py / queries.py 消费）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"schema_version": SCHEMA_VERSION, "generated_at": now_iso(),
               "nodes": nodes, "edges": edges}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[OUT] normalized.json -> {path}")


def write_jsonl(nodes: List[dict], edges: List[dict], path: str) -> None:
    """写出标准化中间格式 JSON Lines（见接入规范第 6 节）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for n in nodes:
            f.write(json.dumps({"record": "node", "id": n["id"],
                                "labels": n["labels"], "props": n["props"]},
                               ensure_ascii=False) + "\n")
        for e in edges:
            f.write(json.dumps({"record": "edge", "id": e["id"],
                                "source": e["source"], "target": e["target"],
                                "kind": e["kind"], "props": e["props"]},
                               ensure_ascii=False) + "\n")
    print(f"[OUT] normalized.jsonl -> {path}  ({len(nodes)+len(edges)} 行)")


def write_load_cypher(path: str, node_count: int, edge_count: int) -> None:
    """生成 load.cypher：neo4j-admin import 用法 + MERGE/CREATE 示例 + Q-id 去重。"""
    cypher = f"""// ============================================================
// 公式知识图谱 · Neo4j 导入脚本 (load.cypher)
// 由 etl_pipeline.py 自动生成 ｜ 节点 {node_count} 条 / 边 {edge_count} 条
// Schema 版本：{SCHEMA_VERSION}
// ============================================================

// ------------------------------------------------------------
// (A) 首建：neo4j-admin database import（离线、大批量、性能最佳）
//     需停库后执行；CSV 表头为 :ID / :LABEL / :START_ID / :END_ID / :TYPE。
// ------------------------------------------------------------
// neo4j-admin database import full formula-graph \\
//   --nodes=Formula=nodes.csv \\
//   --nodes=Symbol=nodes.csv \\
//   --relationships=relationships.csv \\
//   --delimiter=, \\
//   --id-type=STRING \\
//   --skip-duplicate-nodes=true \\
//   --overwrite-destination=true

// ------------------------------------------------------------
// (B) 唯一性约束（必须先建，MERGE 才能高效防重）
// ------------------------------------------------------------
CREATE CONSTRAINT formula_id IF NOT EXISTS FOR (n:Formula) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT symbol_id  IF NOT EXISTS FOR (n:Symbol)  REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT def_id     IF NOT EXISTS FOR (n:Definition) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT pq_id      IF NOT EXISTS FOR (n:PhysicalQuantity) REQUIRE n.id IS UNIQUE;

// ------------------------------------------------------------
// (C) 增量写入：APOC 批量 MERGE 节点（幂等）
// ------------------------------------------------------------
// 读完 normalized.json 后由 load_neo4j.py 逐批调用；等价示意：
CALL apoc.periodic.iterate(
  "UNWIND $rows AS row RETURN row",
  "CALL apoc.merge.node(row.labels, {{id: row.id}}, row.props) YIELD node RETURN node",
  {{batchSize: 500, params: {{rows: $rows}}}}
);

// 无 APOC 时的等价写法（按标签分别 MERGE）：
// UNWIND $rows AS row
// MERGE (n:Formula {{id: row.id}})
// SET n += row.props;

// ------------------------------------------------------------
// (D) 增量写入：边（动态类型用 apoc.merge.relationship）
// ------------------------------------------------------------
MATCH (a {{id: $start}}), (b {{id: $end}})
CALL apoc.merge.relationship(
  a, $type, {{}}, $props, b
) YIELD rel
SET rel += $props
RETURN rel;

// ------------------------------------------------------------
// (E) Align 后按 wikidata_qid 去重（Dedupe 阶段）
// ------------------------------------------------------------
// 同 Q-id 的 Symbol / MathConcept 合并为一节点，保留多来源溯源：
MATCH (n)
WHERE n.wikidata_qid IS NOT NULL
WITH n.wikidata_qid AS qid, COLLECT(n) AS nodes
WHERE SIZE(nodes) > 1
CALL apoc.refactor.mergeNodes(nodes,
     {{properties: 'discard', mergeRels: true}}) YIELD node
RETURN count(node) AS merged;

// 去重前可先确认候选簇：
// MATCH (n) WHERE n.wikidata_qid IS NOT NULL
// RETURN n.wikidata_qid AS qid, count(*) AS c ORDER BY c DESC LIMIT 20;

// ------------------------------------------------------------
// (F) 示例查询
// ------------------------------------------------------------
// F1. 某公式的显式引用依赖
MATCH (f:Formula {{id: 'MX:thm:riemann_curvature'}})-[r:derived_from]->(g)
WHERE r.explicit_or_inferred = 'explicit'
RETURN g.id, r.confidence ORDER BY r.confidence DESC;

// F2. 只看 LLM 推断边（需人工/符号校验）
MATCH (a)-[r]->(b) WHERE r.kind = 'llm_inferred'
RETURN a.id, b.id, r.confidence ORDER BY r.confidence ASC;

// F3. 公式用到的符号
MATCH (f:Formula {{id: 'MX:thm:gauss_bonnet'}})-[:has_symbol]->(s:Symbol)
RETURN s.name, s.meaning;
"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(cypher)
    print(f"[OUT] load.cypher    -> {path}")


# ----------------------------------------------------------------------------
# 统计
# ----------------------------------------------------------------------------

def print_stats(nodes: List[dict], edges: List[dict], source_key: str) -> None:
    print(f"\n=== [{source_key}] 归一化统计 ===")
    print(f"节点总数: {len(nodes)}   边总数: {len(edges)}")

    by_label: Dict[str, int] = {}
    for n in nodes:
        for lbl in n["labels"]:
            by_label[lbl] = by_label.get(lbl, 0) + 1
    print(f"节点标签分布: {dict(sorted(by_label.items(), key=lambda x: -x[1]))}")

    by_type: Dict[str, int] = {}
    for e in edges:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
    print(f"边类型分布:   {dict(sorted(by_type.items(), key=lambda x: -x[1]))}")

    by_kind: Dict[str, int] = {}
    by_flag: Dict[str, int] = {}
    for e in edges:
        by_kind[e["props"]["kind"]] = by_kind.get(e["props"]["kind"], 0) + 1
        by_flag[e["props"]["explicit_or_inferred"]] = \
            by_flag.get(e["props"]["explicit_or_inferred"], 0) + 1
    print(f"边 kind 分布: {dict(sorted(by_kind.items(), key=lambda x: -x[1]))}")
    print(f"explicit_or_inferred 分布: {by_flag}")

    llm = [e for e in edges if e["props"]["kind"] == "llm_inferred"]
    if llm:
        print(f"[!] llm_inferred 边 {len(llm)} 条（需 SymPy 校验，置信度已降权）:")
        for e in llm:
            print(f"    {e['source']} -[derived_from]-> {e['target']} "
                  f"conf={e['props']['confidence']}")


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="公式知识图谱 ETL 管道（多源接入 & 图库化）")
    p.add_argument("--source", default="mathxiv",
                   choices=["mathxiv", "mathgraph", "physicsbabel", "elementkg", "all"],
                   help="要运行的适配器（默认 mathxiv）")
    p.add_argument("--sample-file", default=DEFAULT_SAMPLE,
                   help="MathXiv 样例 JSON 路径")
    p.add_argument("--data-dir", default=None,
                   help="math-graph / PhysicsBabel / ElementKG 原始数据目录")
    p.add_argument("--sample", type=float, default=0.01,
                   help="超大源采样率（默认 0.01 = 1%%）")
    p.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                   help="产物输出目录（默认 06_PoC/etl）")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    print("=" * 68)
    print("公式知识图谱 · ETL 管道")
    print(f"  源: {args.source}   输出目录: {args.out_dir}")
    print("=" * 68)

    all_nodes: List[dict] = []
    all_edges: List[dict] = []

    for adapter in build_adapters(args):
        # 适配器参数按需注入（不同适配器接受不同 kwargs）
        kwargs: Dict[str, Any] = {}
        if isinstance(adapter, MathXivAdapter):
            kwargs["path"] = args.sample_file
        else:
            kwargs["data_dir"] = args.data_dir
            kwargs["path"] = args.data_dir
            kwargs["sample"] = args.sample
        try:
            nodes, edges = adapter.run(**kwargs)
        except Exception as e:  # 单源失败不影响整体
            print(f"[WARN] 适配器 {adapter.key} 运行失败：{e}")
            continue
        print_stats(nodes, edges, adapter.key)
        all_nodes.extend(nodes)
        all_edges.extend(edges)

    # 写出
    print("\n=== 写出产物 ===")
    write_nodes_csv(all_nodes, os.path.join(args.out_dir, "nodes.csv"))
    write_relationships_csv(all_edges, os.path.join(args.out_dir, "relationships.csv"))
    write_normalized_json(all_nodes, all_edges,
                          os.path.join(args.out_dir, "normalized.json"))
    write_jsonl(all_nodes, all_edges,
                os.path.join(args.out_dir, "normalized.jsonl"))
    write_load_cypher(os.path.join(args.out_dir, "load.cypher"),
                      len(all_nodes), len(all_edges))

    print(f"\n[Done] ETL 完成：节点 {len(all_nodes)} / 边 {len(all_edges)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
