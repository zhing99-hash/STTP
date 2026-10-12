#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""STTP 推理增量评测协议 v2（Phase 39 · GPT6 复评 R11「新候选研究收益」）。

复评对旧 GNN 评测的五条指摘（原文要点，见 07_交付物/外部评审/）：

  C1  切分在**每种关系内部独立**进行 ⇒ 同一实体对可通过**另一种关系**同时出现在
      训练邻接中（合成数据实测 20/20）。不足以支持「从未见过的实体对」的更强说法。
      ⇒ 建议明确两个协议：关系留出补全 / 实体对（簇）分组留出的发现任务。
  C2  MRR 仍是每条正例抽 250 个**全局随机**负例（有重复采样、未用同类型候选池）
      ⇒ 既不是全实体排名，也不可与标准**全量 filtered MRR** 比较。
      应报告候选空间、过滤规则、随机种子，并增加**唯一且类型一致的困难负例**。
  C3  缺少**规则检索 / 相似度 / 简单图方法基线** ⇒ 无法判断 GNN 是否提供增量。
  C4  训练入口读**展示层投影**（graph_data_phase12/13.json）⇒ 研究样本应来自
      **版本化规范数据**。
  C5  历史 MRR 不能因切分源码修好就自动成为新协议下的结果 ⇒ 需重跑。

本脚本的对应设计：

  * 数据源 = ``06_PoC/etl/normalized.json``（版本化规范数据；**记录 sha256 钉死版本**）
  * **协议 P1 · 关系留出补全（RHC）**：每关系按无向对留出 10%（上限）；训练邻接仅用训练边；
    额外给出**跨关系泄漏诊断**（测试实体对通过其他关系出现在训练邻接中的比例）。
  * **协议 P2 · 实体对留出发现（EPD）**：在 P1 测试对中进一步要求「训练子图内两端点
    无任何直连」（EPD-1）与「且无共同邻居 / 非传导」（EPD-2）。
  * **全量 filtered MRR**：候选空间 = **全部实体**（标准口径，过滤已知真三元组）与
    **类型一致实体**（困难负例口径）两档并报；头尾双向。
  * 候选空间 / 过滤规则 / 随机种子 **显式写入报告**。
  * 基线：``random``（下界）· ``simple_graph``（CN / AA / RA）· ``feature_similarity`` ·
    ``rule_retrieval``（schema + 度先验）。GNN 与基线在**同一测试集、同一候选空间、
    同一过滤规则**下对照 ⇒ ΔMRR 才是「增量」。
  * ``--selfcheck``：随机打分必须使指标崩塌（判分器非真空）。

输出：``12_评测/gnn_eval_v2_report.json``（schema=``sttp-gnn-eval/v2``）。
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import random
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "06_PoC"))
try:
    import graph_export as gx                     # 权威 类型/学科 映射（铁律 #3）
except Exception as e:                            # noqa: BLE001
    print("[X] 无法载入 06_PoC/graph_export.py：%s" % e)
    sys.exit(1)

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
OUT_JSON = os.path.join(ROOT, "12_评测", "gnn_eval_v2_report.json")

SEED = 0
MIN_REL_COUNT = 30          # 关系样本下限（与 gnn_infer 一致）
HAS_SYMBOL_CAP = 3000       # 训练集下采样上限（与 gnn_infer 一致）
NEG_PER_POS = 1
HID, OUT_DIM = 64, 64
LR = 0.01
BEST_MARK = None


# ====================================================================== 0. 工具
def sha256_file(path, buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(buf), b""):
            h.update(chunk)
    return h.hexdigest()


def norm_type(t):
    """遗留拼写兼容（防御性 no-op）；权威收敛在 03_知识层/normalize_labels.py。"""
    return "PhysicalQuantity" if t == "Physical_quantity" else t


# ====================================================================== 1. 载入规范数据
def load_norm(path):
    if not os.path.exists(path):
        print("[X] 找不到规范数据：%s" % path)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    return doc


def build_graph(doc):
    nodes, edges = doc["nodes"], doc["edges"]
    ids = [n["id"] for n in nodes]
    idx = {nid: i for i, nid in enumerate(ids)}
    id2type = {}
    id2subj = {}
    id2attrs = {}
    for n in nodes:
        props = n.get("props") or {}
        id2type[n["id"]] = norm_type(gx.pick_type(n.get("labels")))
        id2subj[n["id"]] = gx.subject_of(props.get("domain"), n["id"])
        id2attrs[n["id"]] = props
    return nodes, edges, ids, idx, id2type, id2subj, id2attrs


# ====================================================================== 2. 关系集 + P1 切分
def build_relations(edges, idx):
    by_type = collections.defaultdict(list)
    for e in edges:
        a, b, t = e.get("source"), e.get("target"), e.get("type")
        if a in idx and b in idx and a != b and t:
            by_type[t].append((a, b))
    return by_type


def p1_split(by_type, idx, *, test_frac=0.10, cap_test=100, seed=SEED):
    rng = random.Random(seed)
    train_by_rel = collections.defaultdict(list)
    test_by_rel = collections.defaultdict(list)
    for r in sorted(by_type):
        groups = collections.defaultdict(list)
        for a, b in by_type[r]:
            groups[(a, b) if a <= b else (b, a)].append((a, b))
        keys = sorted(groups)
        rng.shuffle(keys)
        n_test = max(1, min(cap_test, int(len(keys) * test_frac)))
        test_keys = set(keys[:n_test])
        for k in keys:
            (test_by_rel if k in test_keys else train_by_rel)[r].extend(groups[k])
    return train_by_rel, test_by_rel


def build_train_adj(train_by_rel):
    adj = collections.defaultdict(set)
    for r in train_by_rel:
        for a, b in train_by_rel[r]:
            adj[a].add(b)
            adj[b].add(a)
    return adj


# ====================================================================== 3. 特征
BASE_DIMS = ["M", "L", "T", "I", "K", "N", "J"]


def build_features(ids, idx, id2type, id2subj, id2attrs, train_by_rel, adj, rels):
    types = sorted({id2type[i] for i in ids})
    subs = sorted({id2subj[i] for i in ids})
    edge_types = sorted({r for r in train_by_rel})
    tih = {t: i for i, t in enumerate(types)}
    sih = {s: i for i, s in enumerate(subs)}
    eih = {t: i for i, t in enumerate(edge_types)}
    n_et = len(edge_types)
    n, nt, ns = len(ids), len(types), len(subs)
    dim = nt + ns + 2 + n_et + n_et + 3 + 1 + len(BASE_DIMS) + 1 + 2

    out_et = collections.defaultdict(collections.Counter)
    in_et = collections.defaultdict(collections.Counter)
    for r in train_by_rel:
        for a, b in train_by_rel[r]:
            out_et[a][r] += 1
            in_et[b][r] += 1

    X = np.zeros((n, dim), dtype=np.float32)
    for i, nid in enumerate(ids):
        p = id2attrs[nid]
        t = id2type[nid]
        off = 0
        X[i, tih[t]] = 1.0
        off += nt
        X[i, off + sih[id2subj[nid]]] = 1.0
        off += ns
        X[i, off] = math.log1p(len(adj.get(nid, ())))
        off += 1
        X[i, off] = 1.0 if (p.get("latex") or p.get("formula")) else 0.0
        off += 1
        tot_o = sum(out_et[nid].values()) or 1
        tot_i = sum(in_et[nid].values()) or 1
        for et, c in out_et[nid].items():
            if et in eih:
                X[i, off + eih[et]] = c / tot_o
        off += n_et
        for et, c in in_et[nid].items():
            if et in eih:
                X[i, off + eih[et]] = c / tot_i
        off += n_et
        if t == "Element":
            for k, key, den in ((0, "atomic_number", 118.0), (1, "period", 7.0), (2, "group", 18.0)):
                try:
                    X[i, off + k] = min(float(p.get(key) or 0) / den, 1.0)
                except (TypeError, ValueError):
                    pass
        off += 3
        X[i, off] = 1.0 if t == "Element" else 0.0
        off += 1
        if t == "Unit":
            dstr = str(p.get("dimension") or "")
            for j, name in enumerate(BASE_DIMS):
                if name in dstr:
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
    return X, types, subs, edge_types


# ====================================================================== 4. 全图正例索引（filtered 用）
def build_pos_index(by_type, idx, rels):
    rmap = {r: i for i, r in enumerate(rels)}
    pos_u = collections.defaultdict(set)
    pos_v = collections.defaultdict(set)
    for r in rels:
        ri = rmap[r]
        for a, b in by_type[r]:
            ui, vi = idx[a], idx[b]
            pos_u[(ri, ui)].add(vi)
            pos_v[(ri, vi)].add(ui)
    return rmap, pos_u, pos_v


# ====================================================================== 5. 泄漏诊断 + EPD
def leakage_diagnosis(test_by_rel, adj, idx, rmap):
    """P1 测试对中，有多少在**训练邻接**里已通过（任意关系的）边直连。

    ⚠ 注意：``adj`` 以**字符串节点 id** 为键（与 build_train_adj 一致），
    绝不可用整数 idx 查 —— 否则会全部 miss，伪造出「0% 泄漏」的假象
    （2026-10-12 实测踩中：0% 泄漏 + 100% 非传导两个假信号互相自洽）。
    """
    total = 0
    leaked = 0
    by_rel = {}
    for r in sorted(test_by_rel):
        tot = lk = 0
        for a, b in test_by_rel[r]:
            tot += 1
            if b in adj.get(a, ()) or a in adj.get(b, ()):
                lk += 1
        by_rel[r] = {"n": tot, "leaked": lk}
        total += tot
        leaked += lk
    return {"n_test_pairs": total, "leaked_pairs": leaked,
            "leak_rate": round(leaked / total, 4) if total else None,
            "by_relation": by_rel}


def derive_epd(test_by_rel, adj, idx):
    """P2 实体对留出：从 P1 测试对筛出「训练子图内两端点无直连」（EPD-1）
    以及「且无共同邻居 / 非传导」（EPD-2）。``adj`` 以字符串 id 为键。"""
    epd1, epd2 = [], []
    for r in sorted(test_by_rel):
        for a, b in test_by_rel[r]:
            na, nb = adj.get(a, set()), adj.get(b, set())
            if b in na or a in nb:                    # 训练子图内仍有跨关系直连
                continue
            epd1.append((r, a, b))
            if not (na & nb):                         # 无共同邻居 → 非传导
                epd2.append((r, a, b))
    return epd1, epd2


def epd_stats(pairs, adj, idx):
    """EPD 样本的邻域规模诊断：验证「无共同邻居」不是因端点邻域为空造成的假象。"""
    if not pairs:
        return {"n": 0}
    da, db, inter, empty_side = [], [], [], 0
    for r, a, b in pairs:
        na, nb = adj.get(a, set()), adj.get(b, set())
        da.append(len(na)); db.append(len(nb)); inter.append(len(na & nb))
        if not na or not nb:
            empty_side += 1
    n = len(pairs)
    return {"n": n,
            "mean_deg_fixed": round(sum(da) / n, 2),
            "mean_deg_other": round(sum(db) / n, 2),
            "mean_common_neighbors": round(sum(inter) / n, 3),
            "pairs_with_empty_side": empty_side,
            "note": "mean_common_neighbors≈0 且 empty_side 低，才说明「非传导」是结构事实而非空邻域假象"}


def diag_selfcheck():
    """判据非真空自检（铁律 #14）：合成一个「两关系共享同一实体对」的小图，
    断言泄漏诊断确实能报出泄漏；再断言无泄漏时它报 0。"""
    idx = {"a": 0, "b": 1, "c": 2, "d": 3}
    # 情形 1：(a,b) 在 r1 被留出，但经 r2 仍在训练邻接 → 应报 1 泄漏
    adj_leak = {"a": {"b"}, "b": {"a"}, "c": {"d"}, "d": {"c"}}
    d1 = leakage_diagnosis({"r1": [("a", "b")]}, adj_leak, idx, {"r1": 0})
    # 情形 2：训练邻接不含 (a,b) → 应报 0 泄漏
    adj_clean = {"c": {"d"}, "d": {"c"}}
    d2 = leakage_diagnosis({"r1": [("a", "b")]}, adj_clean, idx, {"r1": 0})
    ok = (d1["leaked_pairs"] == 1 and d1["leak_rate"] == 1.0
          and d2["leaked_pairs"] == 0 and d2["leak_rate"] == 0.0)
    return {"pass": ok, "leak_detected": d1, "clean_reported_zero": d2}


# ====================================================================== 6. GNN 模型
def train_gnn(X, adj, ids, idx, rels, train_by_rel, pos_u, pos_v, *, epochs, seed=SEED):
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    torch.manual_seed(seed)
    # 可复现性：CPU 稀疏 mm 的多线程归约是非确定源（实测同 seed 两次 MRR 0.1064 vs 0.1069）。
    # 固定单线程 + 确定性算法后，同输入同 seed 才逐位可复现。
    try:
        torch.set_num_threads(1)
    except Exception:                                                      # noqa: BLE001
        pass
    try:
        torch.use_deterministic_algorithms(True, warn_only=True)
    except Exception:                                                      # noqa: BLE001
        pass
    n = len(ids)
    rmap = {r: i for i, r in enumerate(rels)}

    rows, cols = [], []
    for r in train_by_rel:
        for a, b in train_by_rel[r]:
            rows.append(idx[a]); cols.append(idx[b])
            rows.append(idx[b]); cols.append(idx[a])
    ii = torch.tensor([rows, cols], dtype=torch.long)
    vv = torch.ones(len(rows), dtype=torch.float32)
    A = torch.sparse_coo_tensor(ii, vv, (n, n)).coalesce()
    deg = torch.sparse.sum(A, 1).to_dense()
    dinv = 1.0 / torch.sqrt(torch.clamp(deg, min=1.0))
    av = A.values() * dinv[A.indices()[0]] * dinv[A.indices()[1]]
    An = torch.sparse_coo_tensor(A.indices(), av, (n, n))
    XT = torch.tensor(X)

    class Encoder(nn.Module):
        def __init__(self, infx, hid=HID, out=OUT_DIM):
            super().__init__()
            self.l1 = nn.Linear(infx * 2, hid)
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

    enc = Encoder(X.shape[1])
    dec = ComplEx(len(rels))
    opt = torch.optim.Adam(list(enc.parameters()) + list(dec.parameters()), lr=LR)

    rng = random.Random(seed + 1)
    train_pairs = []
    for r in rels:
        pairs = train_by_rel.get(r, [])
        if r == "has_symbol" and len(pairs) > HAS_SYMBOL_CAP:
            pairs = rng.sample(pairs, HAS_SYMBOL_CAP)
        ri = rmap[r]
        for a, b in pairs:
            train_pairs.append((idx[a], ri, idx[b]))
    pu = torch.tensor([t[0] for t in train_pairs], dtype=torch.long)
    pr = torch.tensor([t[1] for t in train_pairs], dtype=torch.long)
    pv = torch.tensor([t[2] for t in train_pairs], dtype=torch.long)

    def sample_neg(k):
        us, rs, vs = [], [], []
        for _ in range(k):
            u, r, v = train_pairs[rng.randrange(len(train_pairs))]
            if rng.random() < 0.5:
                u2 = rng.randrange(n)
                while u2 in pos_v[(r, v)]:
                    u2 = rng.randrange(n)
                us.append(u2); rs.append(r); vs.append(v)
            else:
                v2 = rng.randrange(n)
                while v2 in pos_u[(r, u)]:
                    v2 = rng.randrange(n)
                us.append(u); rs.append(r); vs.append(v2)
        return (torch.tensor(us, dtype=torch.long), torch.tensor(rs, dtype=torch.long),
                torch.tensor(vs, dtype=torch.long))

    for ep in range(epochs):
        if ep % 10 == 0:
            nu, nr, nv = sample_neg(len(train_pairs))
        enc.train(); dec.train()
        opt.zero_grad()
        H = enc(XT)
        sp = dec.score(H, pu, pr, pv)
        sn = dec.score(H, nu, nr, nv)
        loss = (F.binary_cross_entropy_with_logits(sp, torch.ones_like(sp))
                + F.binary_cross_entropy_with_logits(sn, torch.zeros_like(sn)))
        loss.backward()
        opt.step()
    enc.eval(); dec.eval()
    with torch.no_grad():
        H = enc(XT)
    return H.detach().numpy(), dec.R.detach().numpy(), dec.b.detach().numpy(), len(train_pairs)


def gnn_row(H, R, bvec, r_idx, fixed, direction):
    """全实体打分向量（向量化）。direction='tail'：score(fixed, r, c)；'head'：score(c, r, fixed)。"""
    d = OUT_DIM
    hf = H[fixed]
    hr = R[r_idx]
    re_c, im_c = H[:, :d], H[:, d:]
    if direction == "tail":
        re_f, im_f = hf[:d], hf[d:]
        re_r, im_r = hr[:d], hr[d:]
        return re_c @ (re_r * re_f + re_r * im_f) + im_c @ (im_r * re_f - im_r * im_f) + bvec[r_idx]
    re_f, im_f = hf[:d], hf[d:]
    re_r, im_r = hr[:d], hr[d:]
    return re_c @ (re_r * re_f + im_r * im_f) + im_c @ (re_r * im_f - im_r * re_f) + bvec[r_idx]


# ====================================================================== 7. 基线
def make_baselines(X, adj, ids, idx, id2type, rels, rel_mask_fn, rng_seed, A_csr, deg_arr):
    """返回 {name: row_fn(fixed, r_idx, direction) -> np.ndarray(N)}。全部在训练子图上。"""
    n = len(ids)
    logdeg = np.log(np.maximum(deg_arr, 2.0))
    max_logdeg = float(logdeg.max()) or 1.0
    cache = {}

    def cn_row(u):
        key = ("cn", u)
        if key not in cache:
            cache[key] = np.asarray((A_csr[u] @ A_csr.T).toarray()).ravel().astype(np.float64)
        return cache[key]

    w_aa = np.zeros(n, dtype=np.float64)
    for i in range(n):
        d = deg_arr[i]
        w_aa[i] = 1.0 / math.log(d) if d > 2 else (1.0 if d == 2 else 0.0)
    Aw_aa = A_csr.multiply(w_aa).tocsr()
    w_ra = np.zeros(n, dtype=np.float64)
    for i in range(n):
        w_ra[i] = 1.0 / deg_arr[i] if deg_arr[i] > 0 else 0.0
    Aw_ra = A_csr.multiply(w_ra).tocsr()

    def aa_row(u):
        key = ("aa", u)
        if key not in cache:
            cache[key] = np.asarray((Aw_aa[u] @ A_csr.T).toarray()).ravel().astype(np.float64)
        return cache[key]

    def ra_row(u):
        key = ("ra", u)
        if key not in cache:
            cache[key] = np.asarray((Aw_ra[u] @ A_csr.T).toarray()).ravel().astype(np.float64)
        return cache[key]

    def feat_sim_row(u):
        key = ("fs", u)
        if key not in cache:
            x = X[u]
            nx = float(np.linalg.norm(x)) or 1.0
            sims = (X @ x) / (np.linalg.norm(X, axis=1) * nx + 1e-9)
            cache[key] = sims.astype(np.float64)
        return cache[key]

    rng = np.random.RandomState(rng_seed)

    def random_row(u, r_idx, direction):
        return rng.rand(n)

    def cn(u, r_idx, direction):
        return cn_row(u)

    def aa(u, r_idx, direction):
        return aa_row(u)

    def ra(u, r_idx, direction):
        return ra_row(u)

    def feature_similarity(u, r_idx, direction):
        return feat_sim_row(u)

    def rule_retrieval(u, r_idx, direction):
        tu = id2type[ids[u]]
        mask = rel_mask_fn(r_idx, direction, u)
        return mask * 1.0 + 0.5 * (logdeg / max_logdeg)

    return {
        "random": random_row,
        "simple_graph_cn": cn,
        "simple_graph_aa": aa,
        "simple_graph_ra": ra,
        "feature_similarity": feature_similarity,
        "rule_retrieval": rule_retrieval,
    }


# ====================================================================== 8. 全量 filtered MRR
def eval_filtered(pairs_by_rel, row_fn, rmap, pos_u, pos_v, n, *, candidates=None,
                  verbose=False):
    """pairs_by_rel: {rel: [(a,b)]}；row_fn(fixed_idx, r_idx, direction)->score vector。

    candidates: None=全实体；否则 dict r->set(idx) 类型一致候选（困难负例口径）。
    返回 {mrr, hits1, hits10, n} 与逐对 rank（供 bootstrap）。
    """
    mrr_sum = 0.0
    h1 = h10 = cnt = 0
    per_pair = []
    for r in sorted(pairs_by_rel):
        ri = rmap[r]
        cand = candidates.get(r) if candidates else None
        for a, b in pairs_by_rel[r]:
            ui, vi = idx_of(a), idx_of(b)
            for direction, fixed, true in (("tail", ui, vi), ("head", vi, ui)):
                raw = np.asarray(row_fn(fixed, ri, direction), dtype=np.float64)
                true_s = raw[true]                    # 先保存真值分（过滤会改写）
                s = raw.copy()
                if cand is not None:
                    keep = np.zeros(n, dtype=bool)
                    keep[list(cand)] = True
                    keep[true] = True
                    s[~keep] = -np.inf
                filt = (pos_u.get((ri, fixed), set()) if direction == "tail"
                        else pos_v.get((ri, fixed), set()))
                for t in filt:
                    if t != true:
                        s[t] = -np.inf
                s[fixed] = -np.inf
                s[true] = true_s
                rank = 1 + int(np.sum(s > true_s))
                inv = 1.0 / rank
                mrr_sum += inv
                h1 += 1 if rank <= 1 else 0
                h10 += 1 if rank <= 10 else 0
                cnt += 1
                per_pair.append(inv)
    return ({"mrr": round(mrr_sum / cnt, 4) if cnt else 0.0,
             "hits1": round(h1 / cnt, 4) if cnt else 0.0,
             "hits10": round(h10 / cnt, 4) if cnt else 0.0,
             "n": cnt}, per_pair)


_IDX = None
_ID2 = None


def idx_of(nid):
    return _IDX[nid]


def bootstrap_ci(a_pairs, b_pairs, n_boot=1000, seed=SEED):
    """配对 bootstrap：GNN(a) − 基线(b) 的 MRR 差的 95% CI。"""
    a = np.asarray(a_pairs); b = np.asarray(b_pairs)
    if len(a) == 0 or len(a) != len(b):
        return None
    rng = np.random.RandomState(seed)
    diffs = []
    k = len(a)
    for _ in range(n_boot):
        sel = rng.randint(0, k, k)
        diffs.append(float(a[sel].mean() - b[sel].mean()))
    diffs = np.sort(np.array(diffs))
    return {"delta_mrr": round(float(a.mean() - b.mean()), 4),
            "ci95": [round(float(diffs[int(0.025 * n_boot)]), 4),
                     round(float(diffs[int(0.975 * n_boot)]), 4)],
            "p_le_0": round(float((diffs <= 0).mean()), 4)}


# ====================================================================== 9. main
def main():
    global _IDX
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=120)
    ap.add_argument("--cap-test", type=int, default=100)
    ap.add_argument("--min-rel", type=int, default=MIN_REL_COUNT)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--json", default=OUT_JSON)
    ap.add_argument("--quick", action="store_true", help="epochs=30 / cap-test=40（快速自检）")
    ap.add_argument("--gnn-seeds", default="",
                    help="逗号分隔的 GNN 训练种子（评估切分固定），用于种子方差；如 1,2")
    ap.add_argument("--selfcheck", action="store_true", help="非真空自检后退出")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if a.quick:
        a.epochs, a.cap_test = 30, 40

    print("=" * 78)
    print("STTP 推理增量评测协议 v2（Phase 39）")
    print("=" * 78)

    t0 = time.time()
    print("[1] 载入版本化规范数据：06_PoC/etl/normalized.json")
    doc = load_norm(NORM)
    src_sha = sha256_file(NORM)
    nodes, edges, ids, idx, id2type, id2subj, id2attrs = build_graph(doc)
    _IDX = idx
    print("    节点 %d / 边 %d   sha256=%s" % (len(ids), len(edges), src_sha[:16]))

    by_type = build_relations(edges, idx)
    rels = sorted([r for r, l in by_type.items() if len(l) >= a.min_rel])
    print("[2] 关系集（样本 >= %d）：%d 类 %s" % (a.min_rel, len(rels), rels))

    train_by_rel, test_by_rel = p1_split(by_type, idx, cap_test=a.cap_test, seed=a.seed)
    # 仅保留进入关系集的键
    train_by_rel = collections.defaultdict(list, {r: train_by_rel.get(r, []) for r in rels})
    test_by_rel = collections.defaultdict(list, {r: test_by_rel.get(r, []) for r in rels})
    adj = build_train_adj(train_by_rel)
    n_train = sum(len(v) for v in train_by_rel.values())
    n_test = sum(len(v) for v in test_by_rel.values())
    print("    P1 切分：训练 %d 条 / 测试 %d 条（按无向对留出 %.0f%%，上限 %d）"
          % (n_train, n_test, 10, a.cap_test))

    leak = leakage_diagnosis(test_by_rel, adj, idx, {r: i for i, r in enumerate(rels)})
    print("[3] 跨关系泄漏诊断：%d/%d 测试对在训练邻接中已直连（%.1f%%）"
          % (leak["leaked_pairs"], leak["n_test_pairs"], 100 * (leak["leak_rate"] or 0)))
    epd1, epd2 = derive_epd(test_by_rel, adj, idx)
    st1, st2 = epd_stats(epd1, adj, idx), epd_stats(epd2, adj, idx)
    print("    P2 EPD：对级留出 %d 条 / 严格非传导 %d 条" % (len(epd1), len(epd2)))
    print("        EPD 邻域诊断：两端点训练度 %.2f / %.2f，平均共同邻居 %.3f（空邻域 %d 对）"
          % (st1.get("mean_deg_fixed", 0), st1.get("mean_deg_other", 0),
             st1.get("mean_common_neighbors", 0), st1.get("pairs_with_empty_side", 0)))

    X, types, subs, edge_types = build_features(ids, idx, id2type, id2subj, id2attrs,
                                                train_by_rel, adj, rels)
    print("[4] 特征维度 %d（类型 %d / 学科 %d / 关系 %d）"
          % (X.shape[1], len(types), len(subs), len(edge_types)))

    rmap, pos_u, pos_v = build_pos_index(by_type, idx, rels)

    # 类型一致候选池（困难负例口径）
    type_of = {i: id2type[ids[i]] for i in range(len(ids))}
    cand_by_rel = {}
    for r in rels:
        ri = rmap[r]
        tails = collections.Counter()
        for aa, bb in by_type[r]:
            tails[type_of[idx[bb]]] += 1
        modal = tails.most_common(1)[0][0] if tails else None
        cand_by_rel[r] = {i for i in range(len(ids)) if type_of[i] == modal}

    def rel_mask_fn(ri, direction, fixed):
        # 粗略 schema 掩码：训练边中该关系出现过的 (头类型, 尾类型) 组合
        tu = type_of[fixed]
        ok = np.zeros(len(ids), dtype=np.float64)
        table = REL_MASK_TAIL if direction == "tail" else REL_MASK_HEAD
        for i in table.get((ri, tu), ()):
            ok[i] = 1.0
        return ok

    # 构建 schema 掩码（仅用训练边，避免测试边类型组合泄漏）
    global REL_MASK_TAIL, REL_MASK_HEAD
    REL_MASK_TAIL = collections.defaultdict(set)
    REL_MASK_HEAD = collections.defaultdict(set)
    for r in rels:
        ri = rmap[r]
        for aa, bb in train_by_rel.get(r, []):
            REL_MASK_TAIL[(ri, id2type[aa])].add(idx[bb])
            REL_MASK_HEAD[(ri, id2type[bb])].add(idx[aa])

    import scipy.sparse as sp
    n = len(ids)
    rows, cols = [], []
    for i, nid in enumerate(ids):
        for nb in adj.get(nid, ()):
            rows.append(i); cols.append(idx[nb])
    A_csr = sp.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, n))
    A_csr.data[:] = 1.0
    deg_arr = np.asarray(A_csr.sum(axis=1)).ravel()

    print("[5] 训练 GNN（%d epoch / %d 正样本）..." % (a.epochs, n_train))
    t1 = time.time()
    H, R, bvec, n_used = train_gnn(X, adj, ids, idx, rels, train_by_rel, pos_u, pos_v,
                                   epochs=a.epochs, seed=a.seed)
    print("    完成，用时 %.1fs（实投训练正样本 %d）" % (time.time() - t1, n_used))

    scorers = make_baselines(X, adj, ids, idx, id2type, rels, rel_mask_fn,
                             a.seed, A_csr, deg_arr)

    # ---------------- P1 评估（两档候选空间；同测试集/同过滤规则） ----------------
    def gnn_plain(fx, ri, d):
        return gnn_row(H, R, bvec, ri, fx, d)

    def gnn_masked(fx, ri, d):
        """GNN + schema 掩码解码（与 rule_retrieval 同约束，保证公平对比）。"""
        s = gnn_row(H, R, bvec, ri, fx, d)
        m = rel_mask_fn(ri, d, fx)
        if m.max() <= 0:
            return s
        return np.where(m > 0, s, -1e9)

    scorers["gnn_complex"] = gnn_plain
    scorers["gnn_complex_schema"] = gnn_masked
    GNN_NAMES = ["gnn_complex", "gnn_complex_schema"]

    print("[6] P1 关系留出补全 · 全量 filtered MRR（候选=全实体，过滤已知真三元组）")
    p1 = test_by_rel
    results_p1 = {}
    per_pair_store = {}
    for name in scorers:
        m, pp = eval_filtered(p1, scorers[name], rmap, pos_u, pos_v, n)
        results_p1[name] = m
        per_pair_store[name] = pp
        star = "  ★" if name in GNN_NAMES else ""
        print("    %-22s MRR=%.4f Hits1=%.4f Hits10=%.4f (n=%d)%s"
              % (name, m["mrr"], m["hits1"], m["hits10"], m["n"], star))

    print("[6b] P1 · 类型一致困难负例口径（候选 = 同尾类型实体全集）")
    results_p1_typed = {}
    for name in scorers:
        m2, _ = eval_filtered(p1, scorers[name], rmap, pos_u, pos_v, n, candidates=cand_by_rel)
        results_p1_typed[name] = m2
    for name, mm in results_p1_typed.items():
        print("    %-22s MRR=%.4f" % (name, mm["mrr"]))

    # ---------------- P2 评估 ----------------
    print("[7] P2 实体对留出发现（EPD）")
    p2_1 = collections.defaultdict(list)
    p2_2 = collections.defaultdict(list)
    for r, aa, bb in epd1:
        p2_1[r].append((aa, bb))
    for r, aa, bb in epd2:
        p2_2[r].append((aa, bb))
    results_p2 = {}
    for label, p2 in (("EPD1_pairheld", p2_1), ("EPD2_strict", p2_2)):
        if not p2:
            results_p2[label] = {"n_pairs": 0, "note": "样本为 0，不足以支持结论"}
            print("    %-16s 无可用样本" % label)
            continue
        entry = {"n_pairs": sum(len(v) for v in p2.values()), "methods": {}}
        for name in scorers:
            mm, _ = eval_filtered(p2, scorers[name], rmap, pos_u, pos_v, n)
            entry["methods"][name] = mm
        results_p2[label] = entry
        best_b = max((v["mrr"], k) for k, v in entry["methods"].items() if k not in GNN_NAMES)
        print("    %-16s n=%d  best-base=%.4f(%s)  GNN=%.4f  GNN+schema=%.4f"
              % (label, entry["n_pairs"], best_b[0], best_b[1],
                 entry["methods"]["gnn_complex"]["mrr"],
                 entry["methods"]["gnn_complex_schema"]["mrr"]))

    # ---------------- 增量判定 ----------------
    print("[8] 增量判定（GNN − 最强基线，配对 bootstrap 1000 次）")
    base_names = [k for k in scorers if k not in GNN_NAMES and k != "random"]
    best = max(base_names, key=lambda k: results_p1[k]["mrr"])
    print("    最强基线 = %s（MRR %.4f）" % (best, results_p1[best]["mrr"]))
    deltas = {}
    for gname in GNN_NAMES:
        dl = bootstrap_ci(per_pair_store[gname], per_pair_store[best], seed=a.seed)
        deltas[gname] = dl
        vd = ("显著增量" if dl["ci95"][0] > 0 else
              "无显著增量（CI 跨 0）" if dl["ci95"][1] > 0 else "显著劣于基线")
        print("    %-18s ΔMRR=%+.4f 95%%CI [%+.4f, %+.4f] P(Δ<=0)=%.3f => %s"
              % (gname, dl["delta_mrr"], dl["ci95"][0], dl["ci95"][1], dl["p_le_0"], vd))
    delta = deltas["gnn_complex_schema"]
    verdict = ("GNN 提供显著增量" if delta["ci95"][0] > 0 else
               "GNN 无显著增量（CI 跨 0）—— 保留规则方案" if delta["ci95"][1] > 0
               else "GNN 显著劣于规则基线 —— 保留规则方案")
    print("    => 结论（以 schema 掩码版为准）：%s" % verdict)

    # ---------------- GNN 训练种子方差（复评：单次运行不可支撑泛化结论） ----------------
    seed_sweep = [{"seed": a.seed,
                   "mrr_all": results_p1["gnn_complex"]["mrr"],
                   "mrr_typed": results_p1_typed["gnn_complex"]["mrr"],
                   "mrr_all_schema": results_p1["gnn_complex_schema"]["mrr"],
                   "mrr_typed_schema": results_p1_typed["gnn_complex_schema"]["mrr"]}]
    if a.gnn_seeds:
        print("[8b] GNN 训练种子方差（切分固定 seed=%d，仅变训练种子）" % a.seed)
        for s in [int(x) for x in a.gnn_seeds.split(",")]:
            Hs, Rs, bs, _ = train_gnn(X, adj, ids, idx, rels, train_by_rel, pos_u, pos_v,
                                      epochs=a.epochs, seed=s)

            def gp(fx, ri, d, _H=Hs, _R=Rs, _b=bs):
                return gnn_row(_H, _R, _b, ri, fx, d)

            def gm(fx, ri, d, _H=Hs, _R=Rs, _b=bs):
                sc = gnn_row(_H, _R, _b, ri, fx, d)
                m = rel_mask_fn(ri, d, fx)
                return np.where(m > 0, sc, -1e9) if m.max() > 0 else sc

            ma_, _ = eval_filtered(p1, gp, rmap, pos_u, pos_v, n)
            mt_, _ = eval_filtered(p1, gp, rmap, pos_u, pos_v, n, candidates=cand_by_rel)
            mas_, _ = eval_filtered(p1, gm, rmap, pos_u, pos_v, n)
            mts_, _ = eval_filtered(p1, gm, rmap, pos_u, pos_v, n, candidates=cand_by_rel)
            rec = {"seed": s, "mrr_all": ma_["mrr"], "mrr_typed": mt_["mrr"],
                   "mrr_all_schema": mas_["mrr"], "mrr_typed_schema": mts_["mrr"]}
            seed_sweep.append(rec)
            print("    seed=%d  all=%.4f / typed=%.4f  (schema: all=%.4f / typed=%.4f)"
                  % (s, rec["mrr_all"], rec["mrr_typed"], rec["mrr_all_schema"], rec["mrr_typed_schema"]))
        vals = [r["mrr_all_schema"] for r in seed_sweep]
        print("    schema 变体跨 %d 种子：全实体 MRR 均值 %.4f  范围 [%.4f, %.4f]（基线 rule=%.4f）"
              % (len(vals), sum(vals) / len(vals), min(vals), max(vals), results_p1[best]["mrr"]))

    # ---------------- 自检 ----------------
    sc = {"random_mrr": results_p1["random"]["mrr"],
          "gnn_mrr": results_p1["gnn_complex"]["mrr"],
          "gnn_gt_random": results_p1["gnn_complex"]["mrr"] > results_p1["random"]["mrr"],
          "random_near_baseline": results_p1["random"]["mrr"] < 0.05}
    diag = diag_selfcheck()
    print("[9] 非真空自检：random MRR=%.4f < gnn MRR=%.4f ? %s"
          % (sc["random_mrr"], sc["gnn_mrr"], sc["gnn_gt_random"]))
    print("    泄漏诊断判据自检（合成两关系共享对）：%s"
          % ("PASS" if diag["pass"] else "FAIL"))
    sc["leakage_diag_selfcheck"] = diag
    if a.selfcheck:
        ok = sc["gnn_gt_random"] and sc["random_near_baseline"] and diag["pass"]
        print("SELFCHECK", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)

    report = {
        "schema": "sttp-gnn-eval/v2",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "data_source": {"path": "06_PoC/etl/normalized.json", "sha256": src_sha,
                        "nodes": len(ids), "edges": len(edges)},
        "protocols": {
            "P1_relation_held_out": {
                "candidate_space": "全实体（filtered：过滤该关系下已知真三元组）",
                "filter_rule": "排除 (r,u) 的全部已知真尾（含训练集）后排名",
                "seed": a.seed, "test_frac": 0.10, "cap_test": a.cap_test,
                "n_train": n_train, "n_test": n_test,
                "leakage": leak,
                "results_all_entity": results_p1,
                "results_typed_candidate": results_p1_typed,
            },
            "P2_entity_pair_discovery": {
                "definition": "EPD1=训练子图内两端点无直连；EPD2=且无共同邻居（非传导）",
                "candidate_space": "全实体（filtered）",
                "epd1_stats": st1, "epd2_stats": st2,
                "results": results_p2,
            },
        },
        "gnn": {"model": "ComplEx + 2 层稀疏 GCN encoder", "epochs": a.epochs,
                "n_train_used": n_used, "dim": OUT_DIM,
                "seed_sweep": seed_sweep},
        "delta": {"best_baseline": best, "verdict": verdict,
                  "gnn_complex_schema": deltas["gnn_complex_schema"],
                  "gnn_complex_plain": deltas["gnn_complex"]},
        "selfcheck": sc,
        "caveats": [
            "P1 关系留出下跨关系信息对传导式链接预测未必违规，但须披露泄漏率。",
            "P2 严格非传导样本受图密度限制，规模可能很小。",
            "全量 filtered 候选=全实体，未做 schema 掩码；类型一致档另报。",
            "GNN 为单次主运行 + 种子扫描；结果对训练超参/种子敏感（30ep 与 120ep 差异显著）。",
        ],
    }
    with open(a.json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print("[10] 报告写入：%s（用时 %.1fs）" % (os.path.relpath(a.json, ROOT), time.time() - t0))


REL_MASK_TAIL = collections.defaultdict(set)
REL_MASK_HEAD = collections.defaultdict(set)

if __name__ == "__main__":
    main()
