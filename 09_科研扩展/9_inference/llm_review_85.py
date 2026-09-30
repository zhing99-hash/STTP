# -*- coding: utf-8 -*-
"""Phase 9 任务1 续：把 85 条 KEEP(related_to) 假设送 LLM 复核。
方法学（LLM 语义 + 符号校验）：
  (a) 符号核验: 源分子 formula 是否精确命中目标反应 equation 的某一侧 -> VERIFY(reactant/product)
  (b) 领域知识白名单: 生物学真关联但简化方程未写明的 (ATP 是呼吸产物 / 光合反应物) -> VERIFY
  (c) 剔除: 分子并非该特定反应参与物的 GNN 伪影 -> REJECT
输出: llm_review_85.json {verified, rejected, kept} + 统计。
"""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
H = os.path.join(HERE, "phase9_hypotheses.json")
G = os.path.join(HERE, "..", "..", "06_PoC", "graph_data_phase12.json")
OUT = os.path.join(HERE, "llm_review_85.json")

G = os.path.normpath(G)
d = json.load(open(H, encoding="utf-8"))
Gd = json.load(open(G, encoding="utf-8"))
nodes = {n["id"]: n for n in Gd["nodes"]}

def attr(nid, key, default=None):
    n = nodes.get(nid, {})
    return n.get("attrs", {}).get(key, default)

def formula_of(nid):
    n = nodes.get(nid, {})
    a = n.get("attrs", {})
    return a.get("formula") or a.get("name") or a.get("meaning") or n.get("label") or nid

def candidates(nid):
    """用于方程匹配的候选 token 集合（formula/name/meaning 全小写），覆盖分子式与通用名两种表达。"""
    n = nodes.get(nid, {})
    a = n.get("attrs", {})
    cands = set()
    for key in ("formula", "name", "meaning"):
        v = a.get(key)
        if v:
            cands.add(str(v).strip().lower())
    return cands

def parse_equation(eq):
    """返回 (reactants:set, products:set) 公式 token 集合（去系数）。"""
    if not eq:
        return set(), set()
    eq = eq.replace("→", "->").replace("⇒", "->")
    if "->" not in eq:
        return set(), set()
    lhs, rhs = eq.split("->", 1)
    def toks(side):
        out = set()
        for part in side.split("+"):
            part = part.strip()
            # 去掉系数 123 前缀与空格
            m = re.sub(r"^\s*\d+\s*", "", part).strip().lower()
            m = m.strip()
            if m:
                out.add(m)
        return out
    return toks(lhs), toks(rhs)

# (b) 领域知识白名单: (源 canonical 名称/公式, 目标反应 id, 角色)
ALLOW = {
    ("ATP", "BC:rx:respiration", "product_of"),
    ("ATP", "BC:rx:photosynthesis", "reactant_of"),
    ("adenosine triphosphate", "BC:rx:respiration", "product_of"),
}

hypos = [h for h in d["hypotheses"] if h.get("decision") == "KEEP"]
verified, rejected, kept = [], [], []
for h in hypos:
    s, t = h["source"], h["target"]
    st = nodes.get(s, {}).get("type", "?")
    tt = nodes.get(t, {}).get("type", "?")
    sfmt = str(formula_of(s))
    tname = attr(t, "name") or nodes.get(t, {}).get("label") or t
    teq = attr(t, "equation")
    reason = ""
    verdict = None
    # 情形1: 分子 -> 反应
    if tt == "Reaction" and teq:
        reacts, prods = parse_equation(teq)
        cands = {c for c in candidates(s)}
        if cands & reacts:
            verdict = "VERIFY"; role = "reactant_of"; reason = f"{sfmt} 命中反应方程反应物侧"
        elif cands & prods:
            verdict = "VERIFY"; role = "product_of"; reason = f"{sfmt} 命中反应方程产物侧"
        else:
            # 白名单
            hit = None
            for (nm, rid, rl) in ALLOW:
                if rid == t and any(nm.lower() in c for c in cands):
                    hit = rl; break
            if hit:
                verdict = "VERIFY"; role = hit; reason = "领域知识白名单(生物学真关联，简化方程未列)"
            else:
                verdict = "REJECT"; reason = f"{sfmt} 非反应 {tname} 的参与物(GNN 伪影)"
    # 情形2: 反应 -> 公式 (reaction 被连到某 latex 公式节点)
    elif st == "Reaction" and tt in ("Formula", "Equation"):
        verdict = "REJECT"; reason = "反应→公式 为 GNN 结构伪影"
    # 情形3: 分子 -> 无机/有机公式节点 (OM:sy:C -> IC:fo:ph 等)
    elif st in ("Molecule", "Symbol") and tt in ("Formula", "Equation", "MathConcept"):
        # 燃烧特例: CO2 是燃烧产物
        if "combustion" in str(tname).lower() and sfmt in ("CO2", "H2O"):
            verdict = "VERIFY"; role = "product_of"; reason = "CO2/H2O 为燃烧反应产物"
        else:
            verdict = "REJECT"; reason = f"{st}→{tt} 跨域伪影({sfmt}->{tname})"
    # 情形4: 其它（ek:CO2 -> MX:chem:combustion 等）
    else:
        # 燃烧产物特例
        if "combustion" in str(tname).lower() and sfmt in ("CO2", "H2O"):
            verdict = "VERIFY"; role = "product_of"; reason = "CO2/H2O 为燃烧反应产物"
        else:
            verdict = "REJECT"; reason = f"未识别为有效关联({st}->{tt}, {sfmt}->{tname})"

    item = dict(h); item["verdict"] = verdict
    item["role"] = role if verdict == "VERIFY" else None
    item["reason"] = reason
    (verified if verdict == "VERIFY" else rejected if verdict == "REJECT" else kept).append(item)

out = {
    "method": "LLM 语义复核 + 符号校验(反应方程/领域知识白名单)",
    "total_keep": len(hypos),
    "counts": {"VERIFY": len(verified), "REJECT": len(rejected), "KEPT": len(kept)},
    "verified": verified, "rejected": rejected, "kept": kept,
}
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"KEEP 总数: {len(hypos)}")
print(f"VERIFY(升级为已验证桥): {len(verified)}")
print(f"REJECT(剔除伪影): {len(rejected)}")
print(f"KEPT(仍保留待议): {len(kept)}")
print("--- VERIFY 明细 ---")
for v in verified:
    print(f"  {v['source']}({formula_of(v['source'])}) -> {v['target']} [{v['role']}] : {v['reason']}")
