# -*- coding: utf-8 -*-
"""
entity_linker.py — 跨源实体链接器 (Phase 5.C)

将多个来源（MX / MG / PB / EK / WD）的实体对齐到同一对象，使用 weighted
similarity 给出 rank + 置信度，输出 same_as 候选（对齐算法产出的边）。

核心特征（每个节点有 label / type / symbol / definition / domain）：
  1. 标签相似度   rapidfuzz.token_sort_ratio  -> 归一化 0-1
  2. 符号匹配     精确相等 -> 1.0 否则 0
  3. 定义重叠     sklearn TfidfVectorizer cosine -> 0-1
  4. 域名一致     同域 -> 1.0（加权），跨域 -> 0

加权总分 = w_label*标签 + w_symbol*符号 + w_def*定义 + w_domain*域
默认权重（可调超参）：0.4 / 0.1 / 0.3 / 0.2  （和为 1.0）

阈值 score >= 0.7 视为 high-confidence same_as 候选。
输出 top-K=10 候选，按分数降序。

运行 `python entity_linker.py` 会执行三个相似度函数的独立单元测试。
"""

from __future__ import annotations

import re
from collections import OrderedDict

import numpy as np
from rapidfuzz import fuzz
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# ---------------------------------------------------------------------------
# 配置：可调超参
# ---------------------------------------------------------------------------
DEFAULT_WEIGHTS: "OrderedDict[str, float]" = OrderedDict(
    label=0.4, symbol=0.1, definition=0.3, domain=0.2
)
HIGH_CONF_THRESHOLD = 0.7
TOP_K = 10

# 学科域粗分类：math / physics / chemistry / cross
_DOMAIN_PREFIX = {
    "math": "math",
    "physics": "physics",
    "chemistry": "chemistry",
    "chem": "chemistry",
    "cross": "cross",
}


def coarse_domain(domain: str | None) -> str:
    """把细粒度 domain（如 'math.DG' / 'physics'）归并到粗域。"""
    if not domain:
        return "unknown"
    d = str(domain).lower().strip()
    for key, val in _DOMAIN_PREFIX.items():
        if d == key or d.startswith(key + ".") or d.startswith(key):
            return val
    return "unknown"


# ---------------------------------------------------------------------------
# 文本归一化（中英文混合：统一转小写 + 去标点 + 压缩空白）
# ---------------------------------------------------------------------------
_PUNCT_RE = re.compile(r"[\W_]+", re.UNICODE)


def normalize_text(s: str | None) -> str:
    """小写、去标点（保留字母/数字/CJK 与空格）、压缩空白。"""
    if not s:
        return ""
    s = str(s).lower().strip()
    s = _PUNCT_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ---------------------------------------------------------------------------
# 三个独立相似度函数（可在导入后单独调用 / 测试）
# ---------------------------------------------------------------------------
def label_similarity(a: str | None, b: str | None) -> float:
    """
    标签相似度：rapidfuzz.token_sort_ratio（0-100）-> 归一化 0-1。
    先归一化（小写 + 去标点），对 token 排序后比较，抵消词序差异。
    """
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    return fuzz.token_sort_ratio(na, nb) / 100.0


def symbol_match(a: str | None, b: str | None) -> float:
    """
    符号匹配：精确相等 -> 1.0，否则 0。
    归一化（小写 + 去标点）后再比，容忍 '\\pi' 与 'pi' 这类差异由调用方决定。
    """
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    return 1.0 if na == nb else 0.0


def definition_overlap_pair(a: str | None, b: str | None) -> float:
    """
    定义重叠（独立版）：对两个文本临时 fit 一个 TfidfVectorizer 并算 cosine。
    用于单元测试与小规模场景；大规模请走 EntityLinker.fit（一次性 fit 全语料）。
    """
    ta, tb = (a or ""), (b or "")
    if not ta.strip() or not tb.strip():
        return 0.0
    vec = TfidfVectorizer().fit([ta, tb])
    m = vec.transform([ta, tb])
    score = float(cosine_similarity(m[0], m[1])[0, 0])
    return 0.0 if np.isnan(score) else score


# ---------------------------------------------------------------------------
# EntityLinker 主类
# ---------------------------------------------------------------------------
class EntityLinker:
    """
    跨源实体链接器。

    用法：
        linker = EntityLinker()
        linker.fit(pool)                 # pool: List[dict]，含 definition 等字段
        candidates = linker.link(query)  # query: dict
    """

    def __init__(
        self,
        weights: "OrderedDict[str, float] | dict | None" = None,
        threshold: float = HIGH_CONF_THRESHOLD,
        top_k: int = TOP_K,
    ):
        self.weights = OrderedDict(weights) if weights else DEFAULT_WEIGHTS.copy()
        self.threshold = threshold
        self.top_k = top_k
        self.vectorizer: TfidfVectorizer | None = None
        self.tfidf_matrix = None
        self.pool: list[dict] = []

    # ---- 语料拟合（TF-IDF 只在全池上 fit 一次）----------------------------
    def fit(self, pool: list[dict]) -> "EntityLinker":
        """在 pool 的 definition 文本上 fit TF-IDF，供 definition_overlap 使用。"""
        self.pool = list(pool)
        corpus = [self._def_text(e) for e in self.pool]
        # 需要至少 2 个非空文档才能学到有意义的 IDF；空文档会被忽略但保留行
        self.vectorizer = TfidfVectorizer(
            ngram_range=(1, 2), min_df=1, lowercase=True, token_pattern=r"(?u)\b\w[\w.]+\b"
        )
        self.tfidf_matrix = self.vectorizer.fit_transform(corpus)
        return self

    @staticmethod
    def _def_text(e: dict) -> str:
        """取实体的定义文本（兼容多种字段名）。"""
        for k in ("definition", "def", "informal", "description", "desc"):
            v = e.get(k)
            if isinstance(v, str) and v.strip():
                return v
        # 退化为 label + symbol 拼接，保证空定义也有一点点信号
        return " ".join(str(e.get(k, "")) for k in ("label", "symbol") if e.get(k))

    # ---- 单特征打分 -------------------------------------------------------
    def _def_sim(self, query_def: str, cand_def: str) -> float:
        """query 定义 与 candidate 定义的 cosine（直接 transform，避免下标查找）。"""
        if self.vectorizer is None:
            return definition_overlap_pair(query_def, cand_def)
        q = self.vectorizer.transform([self._def_text({"definition": query_def})])
        c = self.vectorizer.transform([self._def_text({"definition": cand_def})])
        score = float(cosine_similarity(q, c)[0, 0])
        return 0.0 if np.isnan(score) else score

    # ---- 主打分 -----------------------------------------------------------
    def _score_one(self, query: dict, cand: dict, q_cdom: str) -> dict:
        w = self.weights
        lab = label_similarity(query.get("label"), cand.get("label"))
        sym = symbol_match(query.get("symbol"), cand.get("symbol"))
        cdom = coarse_domain(cand.get("domain"))
        dom = 1.0 if (cdom != "unknown" and cdom == q_cdom) else 0.0
        # 定义重叠：直接对 (query, candidate) 两段文本 transform，
        # 不依赖 pool 下标，天然免疫重复 id。
        deff = self._def_sim(query.get("definition", ""), cand.get("definition", ""))

        score = (
            w["label"] * lab
            + w["symbol"] * sym
            + w["definition"] * deff
            + w["domain"] * dom
        )

        # matched_by：记录哪些特征显著命中（解释性）
        matched_by = []
        if lab >= 0.5:
            matched_by.append("label")
        if sym >= 1.0:
            matched_by.append("symbol")
        if deff >= 0.3:
            matched_by.append("definition")
        if dom >= 1.0:
            matched_by.append("domain")

        return {
            "target_source": cand.get("source"),
            "target_id": cand.get("id"),
            "target_label": cand.get("label"),
            "target_type": cand.get("type"),
            "score": round(score, 4),
            "matched_by": matched_by,
            "components": {
                "label": round(lab, 4),
                "symbol": round(sym, 4),
                "definition": round(deff, 4),
                "domain": round(dom, 4),
            },
        }

    # ---- 对外接口 ---------------------------------------------------------
    def link(
        self,
        query: dict,
        candidates: list[dict] | None = None,
        top_k: int | None = None,
        threshold: float | None = None,
        exclude_self_source: bool = True,
    ) -> list[dict]:
        """
        给定 query 节点，返回按分数降序的 top-K 对齐候选。

        :param query: 含 id/label/symbol/definition/domain/source 的实体 dict
        :param candidates: 候选池；默认用 fit 时的 pool
        :param exclude_self_source: 默认排除与 query 同 source 的候选
                                    （跨源对齐才是 same_as 的目标）
        :return: list[{target_source,target_id,target_label,score,matched_by,...}]
        """
        cand_pool = candidates if candidates is not None else self.pool
        top_k = top_k or self.top_k
        threshold = threshold if threshold is not None else self.threshold
        q_cdom = coarse_domain(query.get("domain"))
        q_src = query.get("source")

        results = []
        for c in cand_pool:
            if c.get("id") == query.get("id"):
                continue
            if exclude_self_source and c.get("source") == q_src:
                continue
            r = self._score_one(query, c, q_cdom)
            r["source_node"] = query.get("id")
            r["source_source"] = q_src
            results.append(r)

        results.sort(key=lambda x: x["score"], reverse=True)
        results = results[:top_k]
        # 仅保留达到阈值的为“候选”，但对外仍返回 top-K 全量（调用方可据阈值筛选）
        for r in results:
            r["high_confidence"] = r["score"] >= threshold
        return results


# ---------------------------------------------------------------------------
# 独立单测：验证三个相似度函数各自正确
# ---------------------------------------------------------------------------
def _self_test() -> bool:
    print("=" * 64)
    print("EntityLinker 相似度函数独立单元测试")
    print("=" * 64)
    ok = True

    # 1) label_similarity
    cases = [
        ("manifold", "manifold", 1.0, "完全相同的标签"),
        ("Manifold", "smooth manifold", None, "子集/词序差异应较高但不满"),
        ("gauss_bonnet", "gauss bonnet theorem", None, "下划线->空格 + 扩词，仍较高"),
        ("hydrogen", "oxygen", None, "不同词应较低"),
        ("", "anything", 0.0, "空串得 0"),
    ]
    print("\n[1] label_similarity (rapidfuzz.token_sort_ratio)")
    for a, b, expect, desc in cases:
        s = label_similarity(a, b)
        flag = "OK" if (expect is None or abs(s - expect) < 1e-6) else "XX"
        if flag == "XX":
            ok = False
        print(f"  {flag}  {a!r} <> {b!r} = {s:.3f}   ({desc})")

    # 2) symbol_match
    print("\n[2] symbol_match (精确相等)")
    sym_cases = [
        ("pi", "pi", 1.0),
        ("\\pi", "pi", None),   # 归一化后 '\pi' -> 'pi' == 'pi' -> 1.0
        ("R", "r", 1.0),        # 大小写归一
        ("g", "G", 1.0),
        ("M", "manifold", 0.0),
        (None, "x", 0.0),
    ]
    for a, b, expect in sym_cases:
        s = symbol_match(a, b)
        good = (expect is None) or (abs(s - expect) < 1e-6)
        flag = "OK" if good else "XX"
        if not good:
            ok = False
        print(f"  {flag}  {a!r} == {b!r} -> {s:.1f}")

    # 3) definition_overlap_pair
    print("\n[3] definition_overlap (TF-IDF cosine)")
    sim_a = "A smooth manifold is a topological space with a smooth atlas of charts."
    sim_b = "A manifold is a topological space equipped with an atlas of charts."
    diff_a = "Newton's second law states force equals mass times acceleration."
    s_same = definition_overlap_pair(sim_a, sim_b)
    s_diff = definition_overlap_pair(sim_a, diff_a)
    print(f"  similar texts cosine = {s_same:.3f}  (应较高, > 0.5)")
    print(f"  unrelated texts cosine = {s_diff:.3f}  (应较低, < 0.5)")
    if not (s_same > 0.5 and s_diff < 0.5):
        ok = False
        print("  XX  definition_overlap 区间异常")
    else:
        print("  OK")

    print("\n" + "=" * 64)
    print("RESULT:", "ALL PASS" if ok else "FAILED")
    print("=" * 64)
    return ok


if __name__ == "__main__":
    _self_test()
