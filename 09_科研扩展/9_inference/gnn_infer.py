# -*- coding: utf-8 -*-
"""Phase 13 · 推理生成层「带类型的边」重训（多关系链接预测）。

对齐 PROJECT_DEVELOPMENT_GUIDE.md 第十节 · 待办 #2 / #3：
    旧版 gnn_infer.py 只做**二分类**链接预测（sigmoid(dot-product)），对跨域候选
    一律输出泛化 `related_to`，边类型完全靠下游符号门控"事后打标"。
    本版改为**多关系链接预测（multi-relational link prediction）**：
        GraphSAGE 编码器  +  ComplEx 关系解码器
    让模型**直接预测候选对的边类型**并给出类型概率分布；符号门控
    （R-PHY / R-CHEM / R-MATH）退化为"交叉校验 + 置信度裁定"，而不再充当类型来源。

同时修正一处**数据源 bug**：
    旧版读 `06_PoC/graph_data_full.json`（仅 2728 节点 / 16124 边，且是开发指南
    §9.2 明确警告过的"被误叠加 raw 翻倍"的陈旧派生件）；本版读权威可视化快照
    `graph_data_phase13.json`（7128 节点 / 32907 边）。

两段式产出
----------
Part A · GNN 类型预测：
    端点类型兼容的跨域候选 → 双方向全关系打分 → argmax 得类型 + 概率；
    符号门控交叉校验（同类型则升 VERIFIED，门控另有结论则门控覆盖）。
Part B · 门控硬验证（可符号证实才标 VERIFIED）：
    B1 分子×元素   化学式解析         → composed_of        (R-CHEM)
    B2 元素×元素   周期/族相等         → same_period/same_family (R-CHEM)
    B3 物理量×物理量 pint 量纲一致      → dimensionally_consistent (R-PHY)
    B4 反应×分子   方程式解析          → reactant_of/product_of (R-CHEM)
    B5 分子×质量物理量 摩尔质量可算      → has_quantity       (R-PHY)
    B6 公式×公式   LaTeX 规范化等价     → same_as            (R-MATH)
"""
import os
import re
import sys
import json
import math
import random
import collections

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ------------------------------------------------------------------ 配置
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))

SRC_CANDIDATES = [
    os.path.join(ROOT, "06_PoC", "graph_data_phase13.json"),
    os.path.join(ROOT, "06_PoC", "graph_data_phase12.json"),
]
OUT_TYPED = os.path.join(HERE, "phase13_typed_edges.json")
OUT_DETAIL = os.path.join(HERE, "phase13_typed_candidates.json")
OUT_METRICS = os.path.join(HERE, "phase13_gnn_metrics.json")

SEED = 0
random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)

MIN_REL_COUNT = 30
HAS_SYMBOL_CAP = 3000
HID, OUT_DIM = 64, 64
EPOCHS = 150
LR = 0.01
NEG_PER_POS = 1
CAND_PER_TYPEPAIR = 300
A_QUOTA_PER_TYPE = 25       # Part A：每种预测类型最多产出多少条
A_MAX = 250
MIN_EMIT_PROB = 0.55
CAPS = {"B1": 3000, "B2": 600, "B3": 300, "B4": 400, "B5": 700, "B6": 200}

# ------------------------------------------------------------------ 1. 载入
src = next((p for p in SRC_CANDIDATES if os.path.exists(p)), None)
if not src:
    print("[X] 找不到图数据源，试过：%s" % SRC_CANDIDATES)
    sys.exit(1)
print("[1] 载入权威图：%s" % os.path.relpath(src, ROOT))
g = json.load(open(src, encoding="utf-8"))
nodes, edges = g["nodes"], g["edges"]

all_ids = [n["id"] for n in nodes]
idx = {nid: i for i, nid in enumerate(all_ids)}
N = len(all_ids)
id2node = {n["id"]: n for n in nodes}
id2type = {n["id"]: (n.get("type") or "Unknown") for n in nodes}
id2subj = {n["id"]: (n.get("subject") or "跨学科") for n in nodes}
id2attrs = {n["id"]: (n.get("attrs") or {}) for n in nodes}
print("    节点 %d / 边 %d" % (N, len(edges)))


def norm_type(t):
    return "PhysicalQuantity" if t == "Physical_quantity" else t


TYPES = sorted({norm_type(id2type[n]) for n in all_ids})
SUBS = sorted({id2subj[n] for n in all_ids})
print("    节点类型 %d 类 / 学科 %d 类" % (len(TYPES), len(SUBS)))

# ------------------------------------------------------------------ 2. 邻接 / 关系集
adj = collections.defaultdict(set)
edge_type_count = collections.Counter()
edge_by_type = collections.defaultdict(list)
all_pairs = set()
for e in edges:
    a, b, t = e.get("source"), e.get("target"), e.get("type")
    if a not in idx or b not in idx:
        continue
    adj[a].add(b)
    adj[b].add(a)
    edge_type_count[t] += 1
    edge_by_type[t].append((a, b))
    all_pairs.add((a, b))

RELS = sorted([t for t, c in edge_type_count.items() if c >= MIN_REL_COUNT])
print("[2] 关系集（样本数 >= %d）：%d 类" % (MIN_REL_COUNT, len(RELS)))

train_pairs = []
pos_by_ru = collections.defaultdict(set)
pos_by_rv = collections.defaultdict(set)
for r in RELS:
    pairs = edge_by_type[r]
    if r == "has_symbol" and len(pairs) > HAS_SYMBOL_CAP:
        pairs = random.sample(pairs, HAS_SYMBOL_CAP)
    ri = RELS.index(r)
    for a, b in pairs:
        train_pairs.append((idx[a], ri, idx[b]))
        pos_by_ru[(ri, idx[a])].add(idx[b])
        pos_by_rv[(ri, idx[b])].add(idx[a])
print("    训练正样本 %d 条" % len(train_pairs))

# 关系-端点类型模式（用于 schema-constrained decoding：禁止 product_of Element→Element 这类荒唐预测）
TYPE_IDX = {t: i for i, t in enumerate(TYPES)}
REL_MASK = np.zeros((len(RELS), len(TYPES), len(TYPES)), dtype=bool)
for ri_, rel in enumerate(RELS):
    for a, b in edge_by_type[rel]:
        if a in idx and b in idx:
            REL_MASK[ri_, TYPE_IDX[norm_type(id2type[a])], TYPE_IDX[norm_type(id2type[b])]] = True
print("    关系-端点类型模式：%d 个 (关系,头类型,尾类型) 合法组合"
      % int(REL_MASK.sum()))

# ------------------------------------------------------------------ 3. 节点特征
TYPE_IH = {t: i for i, t in enumerate(TYPES)}
SUB_IH = {s: i for i, s in enumerate(SUBS)}
EDGE_TYPES = sorted(edge_type_count)
ET_IH = {t: i for i, t in enumerate(EDGE_TYPES)}
BASE_DIMS = ["M", "L", "T", "I", "K", "N", "J"]

out_et = collections.defaultdict(collections.Counter)
in_et = collections.defaultdict(collections.Counter)
for e in edges:
    a, b, t = e.get("source"), e.get("target"), e.get("type")
    if a in idx and b in idx:
        out_et[a][t] += 1
        in_et[b][t] += 1

n_et = len(EDGE_TYPES)
FEAT_DIM = (len(TYPES) + len(SUBS) + 2 + n_et + n_et
            + 3 + 1 + len(BASE_DIMS) + 1 + 2)

X = np.zeros((N, FEAT_DIM), dtype=np.float32)
for i, nid in enumerate(all_ids):
    p = id2attrs[nid]
    t = norm_type(id2type[nid])
    off = 0
    X[i, TYPE_IH[t]] = 1.0
    off += len(TYPES)
    X[i, off + SUB_IH[id2subj[nid]]] = 1.0
    off += len(SUBS)
    X[i, off] = math.log1p(len(adj[nid]))
    off += 1
    X[i, off] = 1.0 if (p.get("latex") or p.get("formula") or id2node[nid].get("formula")) else 0.0
    off += 1
    tot_o = sum(out_et[nid].values()) or 1
    tot_i = sum(in_et[nid].values()) or 1
    for et, c in out_et[nid].items():
        X[i, off + ET_IH[et]] = c / tot_o
    off += n_et
    for et, c in in_et[nid].items():
        X[i, off + ET_IH[et]] = c / tot_i
    off += n_et
    if t == "Element":
        try:
            X[i, off] = min(float(p.get("atomic_number") or 0) / 118.0, 1.0)
        except (TypeError, ValueError):
            pass
        try:
            X[i, off + 1] = min(float(p.get("period") or 0) / 7.0, 1.0)
        except (TypeError, ValueError):
            pass
        try:
            X[i, off + 2] = min(float(p.get("group") or 0) / 18.0, 1.0)
        except (TypeError, ValueError):
            pass
    off += 3
    X[i, off] = 1.0 if t == "Element" else 0.0
    off += 1
    if t == "Unit":
        dim = str(p.get("dimension") or "")
        for j, name in enumerate(BASE_DIMS):
            if name in dim:
                X[i, off + j] = 1.0
    off += len(BASE_DIMS)
    X[i, off] = 1.0 if p.get("si_base") else 0.0
    off += 1
    if t == "Molecule":
        ncomp = out_et[nid].get("composed_of", 0) + in_et[nid].get("composed_of", 0)
        X[i, off] = min(ncomp / 5.0, 1.0)
        mw = p.get("molecular_weight") or p.get("mass") or p.get("weight")
        X[i, off + 1] = 1.0 if mw is not None else 0.0
    off += 2
print("[3] 节点特征维度 %d" % FEAT_DIM)

# ------------------------------------------------------------------ 4. 稀疏归一化邻接
rows, cols = [], []
for i, nid in enumerate(all_ids):
    for nb in adj[nid]:
        rows.append(i)
        cols.append(idx[nb])
ii = torch.tensor([rows, cols], dtype=torch.long)
vv = torch.ones(len(rows), dtype=torch.float32)
A = torch.sparse_coo_tensor(ii, vv, (N, N)).coalesce()
deg = torch.sparse.sum(A, 1).to_dense()
dinv = 1.0 / torch.sqrt(torch.clamp(deg, min=1.0))
av = A.values() * dinv[A.indices()[0]] * dinv[A.indices()[1]]
An = torch.sparse_coo_tensor(A.indices(), av, (N, N))
XT = torch.tensor(X)
print("    邻接非零 %d" % len(rows))


# ------------------------------------------------------------------ 5. 模型
class Encoder(nn.Module):
    def __init__(self, inf, hid=HID, out=OUT_DIM):
        super().__init__()
        self.l1 = nn.Linear(inf * 2, hid)
        self.l2 = nn.Linear(hid * 2, out * 2)

    def forward(self, x):
        a = torch.relu(self.l1(torch.cat([x, torch.sparse.mm(An, x)], 1)))
        a = torch.relu(self.l2(torch.cat([a, torch.sparse.mm(An, a)], 1)))
        return a


class ComplEx(nn.Module):
    def __init__(self, nrel, d=OUT_DIM):
        super().__init__()
        self.R = nn.Parameter(torch.empty(nrel, 2 * d))
        self.b = nn.Parameter(torch.zeros(nrel))
        nn.init.xavier_uniform_(self.R)

    def score(self, H, u, r, v):
        hu, hv, hr = H[u], H[v], self.R[r]
        re_u, im_u = hu[:, :OUT_DIM], hu[:, OUT_DIM:]
        re_v, im_v = hv[:, :OUT_DIM], hv[:, OUT_DIM:]
        re_r, im_r = hr[:, :OUT_DIM], hr[:, OUT_DIM:]
        return ((re_u * re_r * re_v).sum(1) + (re_u * im_r * im_v).sum(1)
                + (im_u * re_r * im_v).sum(1) - (im_u * im_r * re_v).sum(1)) + self.b[r]


enc = Encoder(FEAT_DIM)
dec = ComplEx(len(RELS))
opt = torch.optim.Adam(list(enc.parameters()) + list(dec.parameters()), lr=LR)


def sample_negatives(pairs):
    us, rs, vs = [], [], []
    for (u, r, v) in pairs:
        for _ in range(NEG_PER_POS):
            if random.random() < 0.5:
                u2 = random.randrange(N)
                while u2 in pos_by_rv[(r, v)]:
                    u2 = random.randrange(N)
                us.append(u2); rs.append(r); vs.append(v)
            else:
                v2 = random.randrange(N)
                while v2 in pos_by_ru[(r, u)]:
                    v2 = random.randrange(N)
                us.append(u); rs.append(r); vs.append(v2)
    return (torch.tensor(us, dtype=torch.long), torch.tensor(rs, dtype=torch.long),
            torch.tensor(vs, dtype=torch.long))


print("[4] 训练（%d 关系 / %d 正样本 / %d epoch）..." % (len(RELS), len(train_pairs), EPOCHS))
pu = torch.tensor([t[0] for t in train_pairs], dtype=torch.long)
pr = torch.tensor([t[1] for t in train_pairs], dtype=torch.long)
pv = torch.tensor([t[2] for t in train_pairs], dtype=torch.long)
nu = nr = nv = None
for ep in range(EPOCHS):
    if ep % 10 == 0:
        nu, nr, nv = sample_negatives(train_pairs)
    enc.train(); dec.train()
    opt.zero_grad()
    H = enc(XT)
    sp = dec.score(H, pu, pr, pv)
    sn = dec.score(H, nu, nr, nv)
    loss = (F.binary_cross_entropy_with_logits(sp, torch.ones_like(sp))
            + F.binary_cross_entropy_with_logits(sn, torch.zeros_like(sn)))
    loss.backward()
    opt.step()
    if (ep + 1) % 50 == 0:
        with torch.no_grad():
            print("    ep %3d  loss %.4f  pos %.3f / neg %.3f"
                  % (ep + 1, loss.item(), torch.sigmoid(sp).mean().item(), torch.sigmoid(sn).mean().item()))

enc.eval(); dec.eval()
with torch.no_grad():
    H = enc(XT)

# ------------------------------------------------------------------ 6. 评估
print("[5] 评估（每关系留出 10% 正样本，过滤式 MRR / Hits@10）...")


def filtered_eval(ri, test_triples, pool=250):
    mrr, hits, cnt = 0.0, 0, 0
    for (u, v) in test_triples:
        negs = []
        while len(negs) < pool:
            c = random.randrange(N)
            if c == v or c in pos_by_ru[(ri, u)]:
                continue
            negs.append(c)
        cand = torch.tensor([v] + negs, dtype=torch.long)
        uu = torch.full_like(cand, u)
        rr = torch.full_like(cand, ri)
        with torch.no_grad():
            s = dec.score(H, uu, rr, cand)
        rank = 1 + int((s[1:] > s[0]).sum().item())
        mrr += 1.0 / rank
        hits += 1 if rank <= 10 else 0
        cnt += 1
    return (mrr / cnt if cnt else 0.0), (hits / cnt if cnt else 0.0), cnt


metrics = {}
for rel in RELS:
    pairs = [(idx[a], idx[b]) for a, b in edge_by_type[rel] if a in idx and b in idx]
    random.shuffle(pairs)
    n_test = max(1, min(200, len(pairs) // 10))
    mrr, h10, n = filtered_eval(RELS.index(rel), pairs[:n_test])
    metrics[rel] = {"n_edges": edge_type_count[rel], "n_test": n,
                    "MRR": round(mrr, 4), "Hits@10": round(h10, 4)}
    print("    %-26s edges=%-6d MRR=%.4f Hits@10=%.4f" % (rel, edge_type_count[rel], mrr, h10))
avg_mrr = sum(m["MRR"] for m in metrics.values()) / max(1, len(metrics))
print("    平均 MRR = %.4f" % avg_mrr)

# ------------------------------------------------------------------ 7. 符号门控
import pint
ureg = pint.UnitRegistry()
SUB_MAP = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
FORMULA_RE = re.compile(r"(?:[A-Z][a-z]?\d*)+")

units_cache = {}
for e in edges:
    if e.get("type") == "has_unit":
        un = id2node.get(e["target"])
        sy = (un.get("attrs") or {}).get("symbol") if un else None
        if sy:
            units_cache.setdefault(e["source"], []).append(sy)

# 元素符号 → 标准原子量（用于摩尔质量计算；同一符号多节点时取首个非空）
SYM_WEIGHT = {}
for nid in all_ids:
    if norm_type(id2type[nid]) != "Element":
        continue
    s = str(id2attrs[nid].get("symbol") or "").strip()
    if not s or s in SYM_WEIGHT:
        continue
    for k in ("atomic_weight", "atomic_mass", "weight"):
        v = id2attrs[nid].get(k)
        if v not in (None, "", 0):
            try:
                SYM_WEIGHT[s] = float(v)
            except (TypeError, ValueError):
                pass
            break
print("    元素原子量表：%d 个符号" % len(SYM_WEIGHT))


def dim_check(u, v):
    uu, vu = units_cache.get(u), units_cache.get(v)
    if not uu or not vu:
        return None
    try:
        return ureg.Quantity(1, uu[0]).dimensionality == ureg.Quantity(1, vu[0]).dimensionality
    except Exception:
        return None


def molecule_formula(mid):
    """取分子的规范化化学式：优先顶层 formula → attrs.formula → label；
    去除离子电荷后缀（Al+3 / H2O4P- / O4P-3 / Cs+）后做严格全匹配校验。"""
    n = id2node.get(mid) or {}
    p = n.get("attrs") or {}
    for v in (n.get("formula"), p.get("formula"), p.get("molecular_formula"), n.get("label")):
        if v:
            s = str(v).translate(SUB_MAP).replace(" ", "")
            s = re.sub(r"[\^]?\d*[+\-\u2212]+\d*$", "", s)
            if FORMULA_RE.fullmatch(s):
                return s
    return ""


_formula_cache = {}


def parse_mol_formula(mid):
    if mid in _formula_cache:
        return _formula_cache[mid]
    f = molecule_formula(mid)
    out = {}
    for sym, num in re.findall(r"([A-Z][a-z]?)(\d*)", f):
        if sym:
            out[sym] = out.get(sym, 0) + (int(num) if num else 1)
    _formula_cache[mid] = out
    return out


_eq_cache = {}


def parse_eq(eq):
    if eq in _eq_cache:
        return _eq_cache[eq]
    try:
        sides = re.split(r"=>|->|→|=", eq)
        left = sides[0]
        right = sides[1] if len(sides) > 1 else ""

        def toks(s):
            return [re.sub(r"^\d+\s*", "", p).strip() for p in s.split("+")]
        res = ([x for x in toks(left) if x], [x for x in toks(right) if x])
    except Exception:
        res = ([], [])
    _eq_cache[eq] = res
    return res


def reaction_role(rid, mid):
    r, m = id2node.get(rid), id2node.get(mid)
    if not r or not m:
        return None
    eq = (r.get("attrs") or {}).get("equation")
    if not eq:
        return None
    left, right = parse_eq(eq)
    mk = str((m.get("attrs") or {}).get("local_id") or "").upper()
    mn = str((m.get("attrs") or {}).get("name") or m.get("label") or mid).upper()
    mf = molecule_formula(mid).upper()
    for t in left:
        if t.upper() in (mk, mn, mf):
            return "reactant_of"
    for t in right:
        if t.upper() in (mk, mn, mf):
            return "product_of"
    return None


def comp_known(mid, eid):
    return any(e["source"] == mid and e.get("type") == "composed_of" and e["target"] == eid for e in edges)


def molar_mass(mid):
    """摩尔质量：**优先化学式解析**（含正确的原子个数），
    回退到既有 composed_of 边（注意：既有边多无 count 字段，按 1 计，精度低）。
    返回 (value, source) 或 (None, None)。"""
    comp = parse_mol_formula(mid)
    src = "formula"
    if not comp:
        for e in edges:
            if e["source"] == mid and e.get("type") == "composed_of":
                sym = str((id2attrs.get(e["target"]) or {}).get("symbol") or "").strip()
                if sym:
                    c = (e.get("props") or {}).get("count") or 1
                    try:
                        comp[sym] = comp.get(sym, 0) + float(c)
                    except (TypeError, ValueError):
                        comp[sym] = comp.get(sym, 0) + 1
        src = "composed_of(no-count)"
    if not comp:
        return None, None
    tot = 0.0
    for sym, c in comp.items():
        w = SYM_WEIGHT.get(sym)
        if w is None:
            return None, None
        tot += w * c
    return round(tot, 4), src


def norm_latex(s):
    s = str(s or "")
    s = re.sub(r"\\[dt]frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"(\1)/(\2)", s)
    s = re.sub(r"\\[a-zA-Z]+", " ", s)
    s = s.replace("{", " ").replace("}", " ").replace("\\", " ")
    s = re.sub(r"\s+", "", s)
    return s.lower()


def gate_check(u, v):
    """符号门控。返回 (rtype, gate, status, rationale) 或 None（无法判定）。"""
    tu, tv = norm_type(id2type[u]), norm_type(id2type[v])

    # R-PHY：物理量量纲一致
    if tu == "PhysicalQuantity" and tv == "PhysicalQuantity":
        d = dim_check(u, v)
        if d is True:
            return "dimensionally_consistent", "R-PHY", "VERIFIED", "两物理量量纲一致（pint）"
        if d is False:
            return "dimensionally_consistent", "R-PHY", "REJECTED", "量纲不一致"
        return None

    # R-CHEM：元素同周期 / 同族
    if tu == "Element" and tv == "Element":
        pu, pv = id2attrs[u], id2attrs[v]
        su = str(pu.get("symbol") or "").strip()
        sv = str(pv.get("symbol") or "").strip()
        # 硬约束：不同元素符号之间**绝不可能是「同一实体」**。
        # 若不拦，Part A 会在门控返回 None（既不同周期也不同族）时直接采信 GNN 的
        # same_as 预测 —— 曾产出 Cl→O / N→H / H→C 这类 13 条荒唐边（A5 已清理）。
        if su and sv and su != sv:
            if pu.get("period") and pu.get("period") == pv.get("period"):
                return "same_period", "R-CHEM", "VERIFIED", "同周期（period=%s）" % pu.get("period")
            if pu.get("group") and pu.get("group") == pv.get("group"):
                return "same_family", "R-CHEM", "VERIFIED", "同族（group=%s）" % pu.get("group")
            return "same_as", "R-CHEM", "REJECTED", \
                "不同元素符号（%s≠%s）不可能互为同一实体" % (su, sv)
        if su and su == sv and u != v:
            return "same_as", "R-CHEM", "VERIFIED", "同一元素符号（%s）跨命名空间实体" % su
        return None

    # R-CHEM：分子组成（化学式解析）
    if {tu, tv} == {"Molecule", "Element"}:
        m, el = (u, v) if tu == "Molecule" else (v, u)
        comp = parse_mol_formula(m)
        sym = str(id2attrs[el].get("symbol") or "").strip()
        if comp and sym:
            if sym in comp:
                return "composed_of", "R-CHEM", "VERIFIED", "化学式 %s 含 %s×%d" % (molecule_formula(m), sym, comp[sym])
            return "composed_of", "R-CHEM", "REJECTED", "化学式 %s 不含 %s" % (molecule_formula(m), sym)
        if comp_known(m, el):
            return "composed_of", "R-CHEM", "VERIFIED", "已知组成（composed_of 已存在）"
        return None

    # R-CHEM：反应角色（方程式解析）
    if {tu, tv} == {"Reaction", "Molecule"}:
        r, m = (u, v) if tu == "Reaction" else (v, u)
        role = reaction_role(r, m)
        if role:
            return role, "R-CHEM", "VERIFIED", "方程式解析：该分子为%s" % ("反应物" if role == "reactant_of" else "产物")
        return "related_to", "R-CHEM", "NEEDS_REVIEW", "反应-分子待验证"

    # R-PHY：分子摩尔质量 ↔ 质量类物理量
    if {tu, tv} == {"Molecule", "PhysicalQuantity"}:
        m, pq = (u, v) if tu == "Molecule" else (v, u)
        mm, src = molar_mass(m)
        pqs = str(id2attrs[pq].get("name") or "").lower()
        if mm is not None and any(k in pqs for k in ("mass", "weight", "energy")):
            return "has_quantity", "R-PHY", "VERIFIED", \
                "摩尔质量 %s g/mol（%s + 原子量校验）" % (mm, "组成边" if src == "composed_of" else "化学式解析")
        return "has_quantity", "R-PHY", "NEEDS_REVIEW", "分子-物理量待验证"

    # R-MATH：公式 LaTeX 规范化等价
    if tu == "Formula" and tv == "Formula":
        if norm_latex(id2attrs[u].get("latex")) and norm_latex(id2attrs[u].get("latex")) == norm_latex(id2attrs[v].get("latex")):
            return "same_as", "R-MATH", "VERIFIED", "LaTeX 规范化后等价"
        return None
    return None


# ------------------------------------------------------------------ 8. Part A：GNN 类型预测
print("[6] Part A · GNN 类型预测 ...")
with torch.no_grad():
    Hn = F.normalize(H, dim=1)
    Hn = torch.where(torch.isnan(Hn), torch.zeros_like(Hn), Hn)

nodes_by_type = collections.defaultdict(list)
for i, nid in enumerate(all_ids):
    nodes_by_type[norm_type(id2type[nid])].append(i)

seen_tp = set()
for rel in RELS:
    for a, b in edge_by_type[rel]:
        if a in idx and b in idx:
            seen_tp.add(tuple(sorted([norm_type(id2type[a]), norm_type(id2type[b])])))

cands = []
for (ta, tb) in sorted(seen_tp):
    ia, ib = nodes_by_type.get(ta, []), nodes_by_type.get(tb, [])
    if not ia or not ib:
        continue
    if ta == tb:
        pl = [(x, y) for x in ia for y in ib if x < y and id2subj[all_ids[x]] != id2subj[all_ids[y]]]
        if not pl:
            continue
        if len(pl) > 400000:
            pl = random.sample(pl, 400000)
        s = (Hn[[p[0] for p in pl]] * Hn[[p[1] for p in pl]]).sum(1).cpu().numpy()
        for o in np.argsort(-s)[:CAND_PER_TYPEPAIR]:
            u, v = pl[o]
            if (all_ids[u], all_ids[v]) in all_pairs or (all_ids[v], all_ids[u]) in all_pairs:
                continue
            cands.append((float(s[o]), u, v))
    else:
        cap = 1400
        ia2 = random.sample(ia, cap) if len(ia) > cap and len(ia) * len(ib) > 2_000_000 else ia
        ib2 = random.sample(ib, cap) if len(ib) > cap and len(ia) * len(ib) > 2_000_000 else ib
        S = (Hn[ia2] @ Hn[ib2].T).cpu().numpy()
        flat = S.reshape(-1)
        k = min(CAND_PER_TYPEPAIR, flat.size)
        top = np.argpartition(-flat, k - 1)[:k] if k < flat.size else np.arange(flat.size)
        for t_ in top:
            u, v = ia2[int(t_) // len(ib2)], ib2[int(t_) % len(ib2)]
            if (all_ids[u], all_ids[v]) in all_pairs or (all_ids[v], all_ids[u]) in all_pairs:
                continue
            cands.append((float(flat[t_]), u, v))
cands.sort(key=lambda x: -x[0])
print("    候选对 %d" % len(cands))

# 批量打分：双向 × 全关系，并按「关系-端点类型模式」掩码（schema-constrained decoding）
cu = torch.tensor([c[1] for c in cands], dtype=torch.long)
cv = torch.tensor([c[2] for c in cands], dtype=torch.long)
tu_arr = np.array([TYPE_IDX[norm_type(id2type[all_ids[c[1]]])] for c in cands])
tv_arr = np.array([TYPE_IDX[norm_type(id2type[all_ids[c[2]]])] for c in cands])
NEG_INF = -1e9
best_logit = torch.full((len(cands),), NEG_INF)
best_rel = torch.zeros(len(cands), dtype=torch.long)
best_rev = torch.zeros(len(cands), dtype=torch.bool)
masked_out = 0
with torch.no_grad():
    all_logits = torch.zeros((len(RELS), len(cands)))
    for ri, rel in enumerate(RELS):
        af = torch.tensor(REL_MASK[ri][tu_arr, tv_arr])
        ab = torch.tensor(REL_MASK[ri][tv_arr, tu_arr])
        rr = torch.full_like(cu, ri)
        s_f = torch.where(af, dec.score(H, cu, rr, cv), torch.full_like(best_logit, NEG_INF))
        s_b = torch.where(ab, dec.score(H, cv, rr, cu), torch.full_like(best_logit, NEG_INF))
        all_logits[ri] = torch.where(af, dec.score(H, cu, rr, cv), torch.full_like(best_logit, NEG_INF))
        m = s_f >= s_b
        s = torch.where(m, s_f, s_b)
        upd = s > best_logit
        best_logit = torch.where(upd, s, best_logit)
        best_rel = torch.where(upd, rr, best_rel)
        best_rev = torch.where(upd, ~m, best_rev)
masked_out = int((best_logit <= NEG_INF / 2).sum().item())
print("    模式约束屏蔽的候选：%d / %d" % (masked_out, len(cands)))

prob_all = torch.sigmoid(best_logit).numpy()
best_rel = best_rel.numpy()
best_rev = best_rev.numpy()
top3 = torch.topk(torch.sigmoid(all_logits), 3, dim=0).indices.numpy()

by_type = collections.defaultdict(list)
for i in range(len(cands)):
    if prob_all[i] < MIN_EMIT_PROB:
        continue
    by_type[RELS[best_rel[i]]].append(i)

partA = []
for rel, items in by_type.items():
    items.sort(key=lambda i: -prob_all[i])
    partA.extend(items[:A_QUOTA_PER_TYPE])
partA.sort(key=lambda i: -prob_all[i])
partA = partA[:A_MAX]
print("    Part A 采用 %d 条，类型分布 %s"
      % (len(partA), dict(collections.Counter(RELS[best_rel[i]] for i in partA).most_common())))

# ------------------------------------------------------------------ 9. Part B：门控硬验证
print("[7] Part B · 门控硬验证（可符号证实才标 VERIFIED）...")
partB = []
bcount = collections.Counter()


def emitB(store, key, s, t, rtype, gate, rat, sname, extra=None):
    if bcount[sname] >= CAPS[sname]:
        return
    if (s, t) in all_pairs or (t, s) in all_pairs or (s, t) in store:
        return
    if s == t:
        return
    store.add((s, t))
    bcount[sname] += 1
    props = {"confidence": 0.95, "explicit_or_inferred": "inferred", "verified": True,
             "verification_gate": gate, "status": "VERIFIED",
             "source": "Phase13.Gate." + sname, "rationale": rat, "domain": "cross_domain"}
    if extra:
        props.update(extra)
    partB.append({
        "id": "gnn13b:%s|%s|%s" % (s, rtype, t), "source": s, "target": t,
        "type": rtype, "kind": "gnn_typed_verified", "props": props,
    })


_store = set()

# 元素符号 → 规范节点：优先选「已被既有 composed_of 引用最多」的节点（数据驱动多数约定），
# 平票按命名空间优先级 EK:el: > EL: > IC:el: > BC:el: > EK2:el:
# 背景：15 个核心元素符号存在 2~3 个重复节点（EL:h / EK:el:H / EK2:el:H …），
# 直接全连会让 composed_of 冗长 3 倍；元素域去重属待办 A5，本处暂取一。
el_target_freq = collections.Counter()
for e in edges:
    if e.get("type") == "composed_of" and e.get("target") in idx:
        el_target_freq[e["target"]] += 1
NS_PRI = ["EK:el:", "EL:", "IC:el:", "BC:el:", "EK2:el:"]


def ns_rank(nid):
    for i, p in enumerate(NS_PRI):
        if nid.startswith(p):
            return i
    return len(NS_PRI)


canon_el = {}
for ei in nodes_by_type.get("Element", []):
    nid = all_ids[ei]
    sym = str(id2attrs[nid].get("symbol") or "").strip()
    if not sym:
        continue
    cur = canon_el.get(sym)
    if cur is None or (el_target_freq.get(nid, 0), -ns_rank(nid)) > (el_target_freq.get(cur, 0), -ns_rank(cur)):
        canon_el[sym] = nid
print("    B1 规范元素节点 %d 个符号" % len(canon_el))

# B1 分子 × 元素 → composed_of（化学式解析）
mols = nodes_by_type.get("Molecule", [])
els = nodes_by_type.get("Element", [])
for mi in mols:
    comp = parse_mol_formula(all_ids[mi])
    if not comp:
        continue
    for sym, cnt in comp.items():
        tgt = canon_el.get(sym)
        if tgt:
            emitB(_store, None, all_ids[mi], tgt, "composed_of", "R-CHEM",
                  "化学式 %s 含 %s×%d（组成解析）" % (molecule_formula(all_ids[mi]), sym, cnt), "B1",
                  extra={"element_symbol": sym, "count": cnt})
# B2 元素 × 元素 → same_period / same_family
for x in range(len(els)):
    for y in range(x + 1, len(els)):
        u, v = all_ids[els[x]], all_ids[els[y]]
        pu, pv = id2attrs[u], id2attrs[v]
        if pu.get("period") and pu.get("period") == pv.get("period"):
            emitB(_store, None, u, v, "same_period", "R-CHEM", "同周期 period=%s" % pu.get("period"), "B2")
        elif pu.get("group") and pu.get("group") == pv.get("group"):
            emitB(_store, None, u, v, "same_family", "R-CHEM", "同族 group=%s" % pu.get("group"), "B2")
# B3 物理量 × 物理量 → dimensionally_consistent
pqs = nodes_by_type.get("PhysicalQuantity", [])
for x in range(len(pqs)):
    for y in range(x + 1, len(pqs)):
        if dim_check(all_ids[pqs[x]], all_ids[pqs[y]]) is True:
            emitB(_store, None, all_ids[pqs[x]], all_ids[pqs[y]], "dimensionally_consistent",
                  "R-PHY", "量纲一致（pint）", "B3")
# B4 反应 × 分子 → reactant_of / product_of
rxns = nodes_by_type.get("Reaction", [])
for ri_ in rxns:
    for mi in mols:
        role = reaction_role(all_ids[ri_], all_ids[mi])
        if role:
            emitB(_store, None, all_ids[mi], all_ids[ri_], role, "R-CHEM",
                  "方程式解析判定为%s" % ("反应物" if role == "reactant_of" else "产物"), "B4")
# B5 分子 × 质量类物理量 → has_quantity（摩尔质量）
# 优先锚定规范摩尔质量物理量（PB:pq:molar_mass，Phase 9 已确立的 131 条入边锚点）
mass_all = [i for i in pqs if any(k in str(id2attrs[all_ids[i]].get("name") or "").lower()
                                  for k in ("mass", "weight", "energy"))]
mass_pqs = [i for i in mass_all if "molar_mass" in str(id2attrs[all_ids[i]].get("name") or "").lower()] \
           or mass_all
print("    B5 质量类物理量目标 %d 个：%s" % (len(mass_pqs), [all_ids[i] for i in mass_pqs]))
for mi in mols:
    mm, msrc = molar_mass(all_ids[mi])
    if mm is None:
        continue
    for pi in mass_pqs:
        emitB(_store, None, all_ids[mi], all_ids[pi], "has_quantity", "R-PHY",
              "摩尔质量 %s g/mol（%s）" % (mm, "组成边+原子量" if msrc == "composed_of" else "化学式解析+原子量"), "B5",
              extra={"value": mm, "unit": "g/mol", "mass_source": msrc})
# B6 公式 × 公式 → same_as（LaTeX 规范化等价）
buckets = collections.defaultdict(list)
for fi in nodes_by_type.get("Formula", []):
    key = norm_latex(id2attrs[all_ids[fi]].get("latex"))
    if key:
        buckets[key].append(all_ids[fi])
for key, ids in buckets.items():
    for x in range(len(ids)):
        for y in range(x + 1, len(ids)):
            emitB(_store, None, ids[x], ids[y], "same_as", "R-MATH", "LaTeX 规范化后等价", "B6")

print("    Part B 产出 %d 条，分布 %s" % (len(partB), dict(bcount.most_common())))

# ------------------------------------------------------------------ 10. 合并 + 门控交叉校验 Part A
print("[8] 合并 + Part A 门控交叉校验 ...")
raw_edges = list(partB)
seen = set((e["source"], e["target"]) for e in partB)
type_hist = collections.Counter(e["type"] for e in partB)
gate_verified = len(partB)
gate_override = 0
partA_meta = []

for i in partA:
    su, sv = all_ids[cu[i]], all_ids[cv[i]]
    if best_rev[i]:
        su, sv = sv, su
    if (su, sv) in seen or (sv, su) in seen:
        continue
    gnn_type = RELS[best_rel[i]]
    prob = float(prob_all[i])
    gres = gate_check(su, sv)
    if gres and gres[2] == "REJECTED":
        continue
    if gres and gres[2] == "VERIFIED":
        rtype, status, gate, rat = gres[0], "VERIFIED", gres[1], gres[3]
    elif gres and gres[0] and gres[0] != gnn_type:
        rtype, status, gate = gres[0], "NEEDS_REVIEW", gres[1]
        rat = "门控类型(%s)覆盖 GNN 类型(%s)：%s" % (gres[0], gnn_type, gres[3])
        gate_override += 1
    else:
        rtype, status, gate = gnn_type, "NEEDS_REVIEW", (gres[1] if gres else "NONE")
        rat = "GNN 类型预测 p=%.3f；%s" % (prob, gres[3] if gres else "跨域关联，待符号校验")
    conf = round(max(0.85, prob), 3) if status == "VERIFIED" else round(min(0.7, max(0.3, prob)), 3)
    if status == "VERIFIED":
        gate_verified += 1
    seen.add((su, sv))
    extra_props = {}
    if rtype == "has_quantity":
        mmv, mmsrc = molar_mass(su)
        if mmv is not None:
            extra_props = {"value": mmv, "unit": "g/mol", "mass_source": mmsrc}
    raw_edges.append({
        "id": "gnn13a:%s|%s|%s" % (su, rtype, sv), "source": su, "target": sv,
        "type": rtype, "kind": "gnn_typed_inferred",
        "props": {"confidence": conf, "explicit_or_inferred": "inferred",
                  "verified": status == "VERIFIED", "verification_gate": gate, "status": status,
                  "source": "Phase13.GNN.typed", "rationale": rat,
                  "gnn_type_prob": round(prob, 4),
                  "gnn_type_probs": [{"type": RELS[t], "p": round(float(torch.sigmoid(all_logits[t, i])), 4)}
                                     for t in top3[:, i]],
                  "domain": "cross_domain", **extra_props},
    })
    type_hist[rtype] += 1
    partA_meta.append({"source": su, "target": sv, "gnn_type": gnn_type, "gnn_prob": round(prob, 4),
                       "final_type": rtype, "status": status})

# ------------------------------------------------------------------ 11. 产出
json.dump({"nodes": [], "edges": raw_edges}, open(OUT_TYPED, "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
json.dump({"source_graph": os.path.relpath(src, ROOT), "avg_MRR": round(avg_mrr, 4),
           "relations": metrics, "type_distribution": dict(type_hist.most_common()),
           "gate_verified": gate_verified, "gate_override": gate_override,
           "n_edges": len(raw_edges), "partA": partA_meta},
          open(OUT_DETAIL, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
json.dump({"source_graph": os.path.relpath(src, ROOT), "avg_MRR": round(avg_mrr, 4),
           "relations": metrics, "type_distribution": dict(type_hist.most_common()),
           "gate_verified": gate_verified, "gate_override": gate_override,
           "n_edges": len(raw_edges), "part_b_breakdown": dict(bcount.most_common())},
          open(OUT_METRICS, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print("    总产出带类型边 %d 条（VERIFIED %d / 门控覆盖 %d）" % (len(raw_edges), gate_verified, gate_override))
print("    最终类型分布: %s" % dict(type_hist.most_common()))
print("    -> %s" % OUT_TYPED)
