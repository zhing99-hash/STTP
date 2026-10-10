# -*- coding: utf-8 -*-
"""Phase 9 · 任务1：对 120 条 related_to 跨域假设生成自然语言假设句并再筛。

再筛逻辑：
- (分子, 反应) 且分子式出现在反应方程 -> 升级为已验证桥 reactant_of/product_of (R-CHEM)。
- (分子, 反应) 无方程匹配 -> 保留为待复核假设 (同属生化/化学网络)。
- (元素符号, 化学式) -> 保留为待复核假设。
- (Wikidata 桩, 化学概念) / (数学概念, 化学实体) -> 剔除 (GNN 跨域伪影, 无可校验语义)。
产出：phase9_refined_raw.json (供 Aura 增量与 viz), phase9_hypotheses.json, phase9_hypotheses.md。
"""
import json, os, re, collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # STTP
FULL = os.path.join(ROOT, "06_PoC", "graph_data_full.json")
RAW = os.path.join(HERE, "phase9_raw.json")
OUT_RAW = os.path.join(HERE, "phase9_refined_raw.json")
OUT_DETAIL = os.path.join(HERE, "phase9_hypotheses.json")
OUT_MD = os.path.join(HERE, "phase9_hypotheses.md")

g = json.load(open(FULL, encoding="utf-8"))
nodes = {n["id"]: n for n in g["nodes"]}
attrs = {nid: (n.get("attrs") or {}) for nid, n in nodes.items()}
labels = {nid: (n.get("label") or nid) for nid, n in nodes.items()}
raw = json.load(open(RAW, encoding="utf-8"))
raw_edges = raw["edges"]


def parse_eq(eq):
    try:
        sides = re.split(r"=>|->|→|=", eq)
        left = sides[0]; right = sides[1] if len(sides) > 1 else ""
        def toks(s):
            return [re.sub(r"^\d+\s*", "", p).strip() for p in s.split("+")]
        return [x for x in toks(left) if x], [x for x in toks(right) if x]
    except Exception:
        return [], []


def mol_formula(nid):
    a = attrs.get(nid, {})
    return a.get("formula") or a.get("molecular_formula")


def decide(u, v):
    """返回 (decision, new_type, verified, nl, plausibility)"""
    tu = nodes.get(u, {}).get("type", "Unknown")
    tv = nodes.get(v, {}).get("type", "Unknown")
    su = nodes.get(u, {}).get("subject", "")
    sv = nodes.get(v, {}).get("subject", "")
    lu, lv = labels[u], labels[v]

    # (分子, 反应) 或 (反应, 分子)
    if (tu == "Molecule" and tv == "Reaction") or (tv == "Molecule" and tu == "Reaction"):
        m, r = (u, v) if tu == "Molecule" else (v, u)
        fm = mol_formula(m)
        req = attrs.get(r, {}).get("equation", "")
        req_label = labels[r]
        if fm and req:
            left, right = parse_eq(req)
            if fm in left:
                return ("UPGRADE", "reactant_of", True,
                        "已验证：%s（分子式 %s）在反应 %s 的方程式「%s」中明确作为反应物出现，构成真实跨域桥（R-CHEM 符号校验）。"
                        % (lu, fm, req_label, req), "high")
            if fm in right:
                return ("UPGRADE", "product_of", True,
                        "已验证：%s（分子式 %s）在反应 %s 的方程式「%s」中明确作为产物出现，构成真实跨域桥（R-CHEM 符号校验）。"
                        % (lu, fm, req_label, req), "high")
        return ("KEEP", "related_to", False,
                "假设：%s（%s）与反应 %s 同属生物化学/化学网络，可能以反应物或产物参与；建议以反应计量式进一步复核其计量关系。"
                % (lu, fm or "分子", req_label), "medium")

    # (元素符号, 化学式/反应式)
    if (tu == "Symbol" and tv in ("Formula", "Equation")) or (tv == "Symbol" and tu in ("Formula", "Equation")):
        s, f = (u, v) if tu == "Symbol" else (v, u)
        return ("KEEP", "related_to", False,
                "假设：元素 %s 可能出现在化合物/反应式 %s 中，构成元素—组成关联，建议以组成数据复核。"
                % (labels[s], labels[f]), "medium")

    # (Wikidata 桩, 化学概念)
    if (u.startswith("WD:") or u.startswith("wd:")) and tv in ("Molecule",) or \
       (v.startswith("WD:") or v.startswith("wd:")) and tu in ("Molecule",):
        return ("DROP", None, False,
                "剔除：Wikidata 实体 %s 与化学概念 %s 仅由 Phase 5 对齐桩关联，无可供符号校验的语义关系（GNN 跨域伪影），不予保留。"
                % (u, lv), "none")

    # (数学概念/符号/定理, 化学实体) -> GNN 伪影
    math_pre = ("MX:thm", "MX:def", "MX:lemma", "MX:math", "MX:sym", "MX:eq", "MX:fo")
    chem_pre = ("MX:chem", "MO:", "BC:", "IC:", "OM:", "PC:")
    if (u.startswith(math_pre) and v.startswith(chem_pre)) or (v.startswith(math_pre) and u.startswith(chem_pre)):
        return ("DROP", None, False,
                "剔除：数学概念 %s 与化学实体 %s 无直接语义关联（GNN 跨域伪影），不予保留。" % (lu, lv), "none")

    # 其它：保留为待复核
    return ("KEEP", "related_to", False,
            "假设：%s（%s）与 %s（%s）跨域关联，建议人工复核其量纲/组成/语义关系。"
            % (lu, su or "?", lv, sv or "?"), "low")


# 处理 related_to 边
refined_edges = []
detail = []
counts = collections.Counter()
upgrade_new = []  # (s,t,new_type,props)

for e in raw_edges:
    if e["type"] != "related_to":
        # ⚠ P0 守卫（2026-10-10，第 19 轮）：不再**原样透传**上游的 has_quantity 边。
        #   分子 → 物理量的 has_quantity 只允许锚定**质量类**物理量（见 `gnn_infer.py` B5：
        #   唯一目标是 molar_mass）。上游曾把同一条「分子摩尔质量/分子量属质量类物理量」
        #   理由**原样粘贴**到 能量 / 动能 / 内能 等 8 个互斥目标上，产出 42 条
        #   **自相矛盾**的跨域边（实测 `水 --has_quantity--> 动能`）。此处源头拦截。
        if e["type"] == "has_quantity":
            tl = str(e.get("target_label") or "").lower()
            if not ("molar" in tl or "mass" in tl or "weight" in tl):
                detail.append({"source": e["source"], "target": e["target"],
                               "decision": "DROP",
                               "hypothesis": "剔除：分子→物理量的 has_quantity 只允许锚定质量类"
                                             "（目标 %s 非质量类，属上游误标）" % e.get("target_label")})
                counts["DROP"] += 1
                continue
        refined_edges.append(e)  # 其余已验证(dim/has_quantity-质量类) 原样保留
        continue
    u, v = e["source"], e["target"]
    decision, new_type, verified, nl, plaus = decide(u, v)
    counts[decision] += 1
    base_props = dict(e["props"])
    if decision == "DROP":
        detail.append({"source": u, "target": v, "decision": "DROP", "hypothesis": nl})
        continue
    if decision == "UPGRADE":
        props = {
            "confidence": 0.9, "explicit_or_inferred": "inferred", "verified": True,
            "verification_gate": "R-CHEM", "status": "VERIFIED",
            "source": "Phase9.GNN+LLM", "rationale": nl, "gnn_score": base_props.get("gnn_score"),
            "plausibility": plaus, "domain": "cross_domain",
        }
        refined_edges.append({"id": e["id"], "source": u, "target": v, "type": new_type,
                              "kind": "llm_inferred_gnn", "props": props})
        upgrade_new.append((u, v, new_type, props))
        detail.append({"source": u, "target": v, "decision": "UPGRADE", "new_type": new_type,
                       "hypothesis": nl})
        continue
    # KEEP
    props = dict(base_props)
    props["verified"] = False
    props["status"] = "NEEDS_REVIEW"
    props["rationale"] = nl
    props["plausibility"] = plaus
    refined_edges.append({"id": e["id"], "source": u, "target": v, "type": "related_to",
                          "kind": "llm_inferred_gnn", "props": props})
    detail.append({"source": u, "target": v, "decision": "KEEP", "hypothesis": nl})

json.dump({"nodes": [], "edges": refined_edges}, open(OUT_RAW, "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
json.dump({"counts": dict(counts), "total_related_to": sum(counts.values()),
           "hypotheses": detail}, open(OUT_DETAIL, "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

# Markdown 报告（人类复核用）
md = ["# Phase 9 任务1 · 跨域假设自然语言化与再筛", ""]
md.append("原始 related_to 假设 %d 条，再筛结果：%s" % (sum(counts.values()), dict(counts)))
md.append("")
for tag, title in (("UPGRADE", "## 一、升级为已验证跨域桥（R-CHEM 符号校验）"),
                   ("KEEP", "## 二、保留为待复核假设"),
                   ("DROP", "## 三、剔除（GNN 跨域伪影）")):
    items = [d for d in detail if d["decision"] == tag]
    md.append(title + "（%d 条）" % len(items))
    md.append("")
    for d in items:
        extra = " → `%s`" % d["new_type"] if d.get("new_type") else ""
        md.append("- `%s` → `%s`%s  %s" % (d["source"], d["target"], extra, d["hypothesis"]))
    md.append("")
open(OUT_MD, "w", encoding="utf-8").write("\n".join(md))

print("related_to 总数: %d" % sum(counts.values()))
print("再筛: %s" % dict(counts))
print("精炼后边(含 60 原验证): %d" % len(refined_edges))
print("  -> %s" % OUT_RAW)
print("  -> %s" % OUT_DETAIL)
print("  -> %s" % OUT_MD)
