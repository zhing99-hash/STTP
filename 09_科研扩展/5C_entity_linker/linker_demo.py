# -*- coding: utf-8 -*-
"""
linker_demo.py — Phase 5.C 实体链接演示

流程：
  1. 从现有 36 个 MX 节点 (06_PoC/etl/neo4j/neo4j_ready.json) 提取实体
  2. 贴上不同 source 标签，构建模拟多源节点池（MX / MG / PB / EK / WD）
     - MG : mathgraph statement_informal 样本
     - PB : physicsbabel 样本
     - EK : elementkg 样本 + 镜像节点
     - WD : 为关键 MX 概念构造的镜像实体（制造“真实匹配”）
  3. 选 5 个 MX 节点作为 query，跑 EntityLinker，产出 ≥10 条对齐候选
     （其中 ≥3 条 high-confidence >= 0.7）
  4. 写出 data/nodes_pool.json、data/sample_nodes.json、aligned_candidates.json

说明：纯本地数据 + 算法，不发起任何网络请求（Phase 5.A 的网络查询另行处理）。
"""

import json
import os

from entity_linker import EntityLinker, coarse_domain, HIGH_CONF_THRESHOLD

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
POOL_PATH = os.path.join(DATA_DIR, "nodes_pool.json")
SAMPLE_PATH = os.path.join(DATA_DIR, "sample_nodes.json")
OUT_PATH = os.path.join(HERE, "aligned_candidates.json")
MX_JSON = os.path.join(HERE, "..", "..", "06_PoC", "etl", "neo4j", "neo4j_ready.json")

# ---------------------------------------------------------------------------
# 1) MX 节点 -> 实体 dict
# ---------------------------------------------------------------------------
# 给 llm_hypothesis 类节点（chem/phy/math）补上可读 label 与定义文本
_PRETTY_LABEL = {
    "co2": "carbon dioxide", "methane": "methane", "combustion": "combustion",
    "newton2": "newton's second law", "acceleration": "acceleration",
    "kinetic_energy": "kinetic energy", "energy": "energy",
    "binomial": "binomial theorem", "expand": "expansion",
    "pythagorean_identity": "pythagorean identity", "trig_unit": "trigonometric unit circle",
    "derivative_power": "power function derivative", "power_rule": "power rule",
    "m": "manifold", "t_p_m": "tangent space", "g": "metric tensor",
    "nabla": "levi civita connection", "r": "riemann curvature tensor",
    "k": "gaussian curvature", "chi_m": "euler characteristic", "pi": "pi",
    "n": "dimension", "p": "point", "rho_alpha": "bump function",
    "x": "variable x", "y": "variable y", "z": "variable z", "c": "constant",
}
_DEF_TEXT = {
    # chem
    "co2": "Carbon dioxide is a chemical molecule with formula CO2, a linear triatomic gas.",
    "methane": "Methane is a chemical molecule with formula CH4, the simplest alkane.",
    "combustion": "Combustion is a chemical reaction of a substance with oxygen releasing heat and light.",
    # phy
    "newton2": "Newton's second law of motion states that force equals mass times acceleration, F = m a.",
    "acceleration": "Acceleration is the rate of change of velocity with respect to time.",
    "kinetic_energy": "Kinetic energy is the energy that a body possesses due to its motion.",
    "energy": "Energy is the capacity to do work; it can take kinetic, potential, and other forms.",
    # math
    "binomial": "The binomial theorem gives the algebraic expansion of powers of a binomial expression.",
    "expand": "Expansion is the algebraic rewriting of an expression as a sum of simpler terms.",
    "pythagorean_identity": "The Pythagorean identity states that sin^2 x + cos^2 x = 1 in trigonometry.",
    "trig_unit": "The trigonometric unit circle is a circle of radius one used to define sine and cosine.",
    "derivative_power": "The derivative of a power function x^n is n x raised to the power n minus one.",
    "power_rule": "The power rule gives the derivative of x^n as n x^(n-1).",
    # sym (light definitions so symbol nodes also carry signal)
    "m": "M denotes a smooth manifold in differential geometry.",
    "t_p_m": "T_p M denotes the tangent space at point p of manifold M.",
    "g": "g denotes the Riemannian metric tensor on a manifold.",
    "nabla": "Nabla denotes the Levi-Civita connection or covariant derivative operator.",
    "r": "R denotes the Riemann curvature tensor measuring non-commutativity of covariant derivatives.",
    "k": "K denotes the Gaussian curvature of a surface.",
    "chi_m": "Chi of M denotes the Euler characteristic of manifold M.",
    "pi": "Pi is the mathematical constant, the ratio of a circle's circumference to its diameter.",
    "n": "n denotes the dimension of a manifold.",
    "p": "p denotes a point on a manifold.",
    "rho_alpha": "Rho alpha denotes a bump function used in a partition of unity.",
    "x": "x denotes an independent variable.",
    "y": "y denotes a dependent variable.",
    "z": "z denotes a complex or spatial variable.",
    "c": "c denotes an arbitrary constant.",
}

_TYPE_MAP = {
    "def": "MathConcept", "lemma": "MathConcept", "thm": "MathConcept",
    "sym": "Symbol", "chem": "Molecule", "phy": "PhysicalQuantity", "math": "MathConcept",
}


def mx_node_to_entity(node: dict) -> dict:
    pid = node.get("id", "")
    parts = pid.split(":")
    cat = parts[1] if len(parts) >= 2 else ""
    name = parts[2] if len(parts) >= 3 else pid
    props = node.get("props", {}) or {}
    label = _PRETTY_LABEL.get(name, name.replace("_", " "))
    symbol = None
    if cat == "sym":
        symbol = name  # 符号节点用名字作为 symbol
    definition = props.get("informal") or _DEF_TEXT.get(name, "")
    return {
        "id": pid,
        "source": "MX",
        "label": label,
        "type": _TYPE_MAP.get(cat, "MathConcept"),
        "symbol": symbol,
        "definition": definition,
        "domain": props.get("domain") or ("chemistry" if cat == "chem" else
                                          "physics" if cat == "phy" else "math"),
    }


# ---------------------------------------------------------------------------
# 2) 其它源的样本 / 镜像节点（手工构造，模拟真实跨源数据）
# ---------------------------------------------------------------------------
def build_other_sources() -> list[dict]:
    nodes: list[dict] = []

    # ---- MG : mathgraph statement_informal 样本 ----
    mg = [
        ("mg:stmt:1", "fundamental theorem of calculus", "Formula", None,
         "math", "The fundamental theorem of calculus links differentiation and integration of continuous functions."),
        ("mg:stmt:2", "differentiation under the integral sign", "Formula", None,
         "math", "Differentiation under the integral sign exchanges derivative and integral operators."),
        ("mg:stmt:3", "increment equals integral of derivative", "Formula", None,
         "math", "The increment of a function equals the integral of its derivative over an interval."),
        ("mg:stmt:4", "definition of euler number e", "Formula", None,
         "math", "Euler's number e is defined as the limit of (1 + 1/n)^n as n tends to infinity."),
    ]
    for nid, lab, typ, sym, dom, dfn in mg:
        nodes.append({"id": nid, "source": "MG", "label": lab, "type": typ,
                      "symbol": sym, "definition": dfn, "domain": dom})

    # ---- PB : physicsbabel 样本 ----
    pb = [
        ("pb:newton_second", "newton's second law", "PhysicalQuantity", "F",
         "physics", "Force equals mass times acceleration: F = m a."),
        ("pb:work", "work", "PhysicalQuantity", "W",
         "physics", "Work is force times displacement: W = F d."),
        ("pb:gravitation", "law of gravitation", "PhysicalQuantity", "F",
         "physics", "Newton's law of gravitation: F = G m1 m2 / r^2."),
    ]
    for nid, lab, typ, sym, dom, dfn in pb:
        nodes.append({"id": nid, "source": "PB", "label": lab, "type": typ,
                      "symbol": sym, "definition": dfn, "domain": dom})

    # ---- EK : elementkg 样本 + 镜像 ----
    ek = [
        ("ek:element:H", "hydrogen", "Element", "H", "chemistry",
         "Hydrogen is the lightest chemical element with atomic number 1."),
        ("ek:element:O", "oxygen", "Element", "O", "chemistry",
         "Oxygen is a chemical element with atomic number 8, essential for respiration."),
        ("ek:molecule:H2", "hydrogen gas", "Molecule", None, "chemistry",
         "Hydrogen gas H2 is the diatomic molecule of hydrogen."),
        ("ek:molecule:O2", "oxygen gas", "Molecule", None, "chemistry",
         "Oxygen gas O2 is the diatomic molecule of oxygen."),
        ("ek:molecule:H2O", "water", "Molecule", None, "chemistry",
         "Water H2O is a molecule formed from hydrogen and oxygen."),
        ("ek:reaction:water_formation", "water formation", "Reaction", None, "chemistry",
         "Water formation is the chemical reaction combining hydrogen and oxygen into water."),
        # 镜像：与 MX:chem:co2 对应的真实匹配
        ("ek:molecule:CO2", "carbon dioxide", "Molecule", None, "chemistry",
         "Carbon dioxide is a molecule with formula CO2."),
    ]
    for nid, lab, typ, sym, dom, dfn in ek:
        nodes.append({"id": nid, "source": "EK", "label": lab, "type": typ,
                      "symbol": sym, "definition": dfn, "domain": dom})

    # ---- WD : 为关键 MX 概念构造的跨域镜像（制造“真实匹配”）----
    wd = [
        ("wd:Q_manifold", "manifold", "MathConcept", "M", "math",
         "A smooth manifold is a second-countable Hausdorff topological space equipped with a maximal smooth atlas of charts."),
        ("wd:Q_tangent_space", "tangent space", "MathConcept", None, "math",
         "The tangent space at a point is the vector space of all tangent vectors at that point on a manifold."),
        ("wd:Q_riemannian_metric", "riemannian metric", "MathConcept", "g", "math",
         "A Riemannian metric is a smooth assignment of a positive-definite inner product on each tangent space."),
        ("wd:Q_levi_civita", "levi civita connection", "MathConcept", "nabla", "math",
         "The Levi-Civita connection is the unique torsion-free, metric-compatible affine connection on a Riemannian manifold."),
        ("wd:Q_riemann_curvature", "riemann curvature tensor", "MathConcept", "R", "math",
         "The Riemann curvature tensor measures the failure of iterated covariant derivatives to commute."),
        ("wd:Q_gauss_bonnet", "gauss bonnet theorem", "MathConcept", None, "math",
         "The Gauss-Bonnet theorem states that the integral of Gaussian curvature over a closed surface equals 2pi times the Euler characteristic."),
        ("wd:Q_partition_of_unity", "partition of unity", "MathConcept", None, "math",
         "A partition of unity is a collection of smooth functions subordinate to an open cover of a manifold."),
        ("wd:Q_covariant_derivative", "covariant derivative", "MathConcept", "nabla", "math",
         "The covariant derivative is a way of differentiating vectors along manifolds compatible with the Levi-Civita connection."),
        ("wd:Q_pi", "pi", "Symbol", "pi", "math",
         "Pi is the mathematical constant representing the ratio of a circle's circumference to its diameter."),
        ("wd:Q_euler_number", "euler number", "MathConcept", None, "math",
         "Euler's number e is the base of the natural logarithm, defined as the limit of (1 + 1/n)^n as n tends to infinity."),
        ("wd:Q_euler_characteristic", "euler characteristic", "MathConcept", "chi", "math",
         "The Euler characteristic is a topological invariant counting vertices minus edges plus faces."),
        ("wd:Q_newton_second", "newton's second law", "PhysicalQuantity", "F", "physics",
         "Newton's second law states that force equals mass times acceleration: F = m a."),
        ("wd:Q_work", "work", "PhysicalQuantity", "W", "physics",
         "Work is the energy transferred when a force moves an object: W = F d."),
        ("wd:Q_gravitation", "law of gravitation", "PhysicalQuantity", "F", "physics",
         "Newton's law of gravitation gives the attractive force F = G m1 m2 / r^2 between masses."),
        ("wd:Q_hydrogen", "hydrogen", "Element", "H", "chemistry",
         "Hydrogen is a chemical element, the lightest, with atomic number 1."),
        ("wd:Q_oxygen", "oxygen", "Element", "O", "chemistry",
         "Oxygen is a chemical element with atomic number 8."),
        ("wd:Q_water", "water", "Molecule", None, "chemistry",
         "Water is a molecule with chemical formula H2O."),
        ("wd:Q_carbon_dioxide", "carbon dioxide", "Molecule", None, "chemistry",
         "Carbon dioxide is a molecule with formula CO2, a greenhouse gas."),
    ]
    for nid, lab, typ, sym, dom, dfn in wd:
        nodes.append({"id": nid, "source": "WD", "label": lab, "type": typ,
                      "symbol": sym, "definition": dfn, "domain": dom})

    return nodes


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    # 1) 加载 MX 节点
    with open(os.path.abspath(MX_JSON), encoding="utf-8") as f:
        mx_data = json.load(f)
    mx_entities = [mx_node_to_entity(n) for n in mx_data["nodes"]]

    # 2) 其它源
    other = build_other_sources()

    # 3) 合并为模拟多源节点池
    pool = mx_entities + other
    with open(POOL_PATH, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)

    # 3b) 小样本（20 个，3 个源：MX math / MG / PB）—— 从池中提取
    sample = (
        [e for e in mx_entities if e["type"] in ("MathConcept", "Symbol")][:13]
        + [e for e in other if e["source"] == "MG"]
        + [e for e in other if e["source"] == "PB"]
    )
    sample = sample[:20]
    with open(SAMPLE_PATH, "w", encoding="utf-8") as f:
        json.dump(sample, f, ensure_ascii=False, indent=2)

    # 4) 拟合 + 链接
    linker = EntityLinker()
    linker.fit(pool)

    # 5) 选 5 个 MX 节点作为 query（每个在池里都有对应实体）
    query_ids = [
        "MX:def:manifold",
        "MX:def:tangent_space",
        "MX:thm:gauss_bonnet",
        "MX:sym:pi",
        "MX:chem:co2",
        "MX:phy:newton2",
    ]
    id2ent = {e["id"]: e for e in pool}
    queries = [id2ent[q] for q in query_ids if q in id2ent]

    all_candidates = []
    for q in queries:
        cands = linker.link(q, top_k=10)
        all_candidates.extend(cands)
        print("\n" + "=" * 72)
        print(f"QUERY  {q['id']}  [{q['source']}]  label='{q['label']}'  domain={coarse_domain(q['domain'])}")
        print("-" * 72)
        print(f"{'rank':<5}{'target':<22}{'src':<4}{'score':<8}{'matched_by'}")
        for i, c in enumerate(cands, 1):
            hc = "★" if c["high_confidence"] else " "
            print(f"{i:<5}{c['target_id']:<22}{str(c['target_source']):<4}"
                  f"{c['score']:<8}{hc} {','.join(c['matched_by'])}")

    # 6) 写输出
    # 去重：同一 (source_node, target_id) 只保留最高分
    seen = {}
    for c in all_candidates:
        key = (c["source_node"], c["target_id"])
        if key not in seen or c["score"] > seen[key]["score"]:
            seen[key] = c
    dedup = sorted(seen.values(), key=lambda x: x["score"], reverse=True)

    out = {
        "metadata": {
            "algorithm": "weighted similarity (label/symbol/definition/domain)",
            "weights": dict(linker.weights),
            "threshold": HIGH_CONF_THRESHOLD,
            "top_k": linker.top_k,
            "pool_size": len(pool),
            "query_count": len(queries),
            "candidate_count": len(dedup),
            "high_confidence_count": sum(1 for c in dedup if c["high_confidence"]),
        },
        "candidates": [
            {
                "source_node": c["source_node"],
                "source_source": c["source_source"],
                "target_source": c["target_source"],
                "target_id": c["target_id"],
                "target_label": c["target_label"],
                "score": c["score"],
                "high_confidence": c["high_confidence"],
                "matched_by": c["matched_by"],
                "components": c["components"],
            }
            for c in dedup
        ],
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # 7) 汇总断言（自跑验证）
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("-" * 72)
    print(f"pool size              : {len(pool)}")
    print(f"queries               : {len(queries)}")
    print(f"aligned candidates    : {len(dedup)}  (要求 >= 10)")
    print(f"high-confidence(>=0.7): {out['metadata']['high_confidence_count']}  (要求 >= 3)")
    # 展示 top 真实匹配示例
    print("\nTop cross-source real matches (high-confidence):")
    real = [c for c in dedup if c["high_confidence"]][:8]
    for c in real:
        print(f"  {c['source_node']}  ~=  {c['target_source']}:{c['target_id']}"
              f"   score={c['score']}  by={','.join(c['matched_by'])}")

    ok = len(dedup) >= 10 and out["metadata"]["high_confidence_count"] >= 3
    print("\nVERIFY:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    main()
