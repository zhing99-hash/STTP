# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""
gnn_model.py — Phase 5.B  GraphSAGE-style GNN for link prediction.

Pure-PyTorch implementation (NO torch_geometric / DGL / cudf).
Runs on CPU with torch 2.x.

Pipeline:
  1. Extract 29-dim node features (type one-hot + label-hash embedding + norm degree)
  2. 2-layer GraphSAGE message passing  ->  32-dim node embeddings
  3. Dot-product + sigmoid link prediction (BCEWithLogitsLoss)
  4. Inference over all non-edges to propose candidate edges.

Author: Phase5.B subagent (GNN dependency inference)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent  # .../STTP
DATA_PATH = REPO_ROOT / "06_PoC" / "etl" / "neo4j" / "neo4j_ready.json"

SEED = 20260928
torch.manual_seed(SEED)
np.random.seed(SEED)

# ----------------------------------------------------------------------------
# Node type vocabulary (12 categories, per task enumeration 0..11)
# NOTE: task text said "11+16+1=28" but enumerated 12 categories (0..11);
# we honour the explicit enumeration -> 12 + 16 + 1 = 29-dim features.
# ----------------------------------------------------------------------------
TYPE_INDEX = {
    "Formula": 0,
    "MathConcept": 1,
    "Symbol": 2,
    "Definition": 3,
    "Theorem": 4,
    "Lemma": 5,
    "Element": 6,
    "Molecule": 7,
    "Reaction": 8,
    "Unit": 9,
    "PhysicalQuantity": 10,
    "Entity": 11,
}
NUM_TYPES = len(TYPE_INDEX)          # 12
HASH_EMB_DIM = 16
DEG_DIM = 1
FEAT_DIM = NUM_TYPES + HASH_EMB_DIM + DEG_DIM   # 29

# Most-specific label wins (Definition > Symbol > Formula > ... > Entity)
_TYPE_PRIORITY = [
    "Theorem", "Lemma", "Definition", "MathConcept", "Symbol",
    "Molecule", "PhysicalQuantity", "Formula", "Element", "Reaction",
    "Unit", "Entity",
]


def primary_type(node: dict) -> str:
    """Map a node's label set to a single primary type index label."""
    labels = set(node.get("labels", []))
    for t in _TYPE_PRIORITY:
        if t in labels:
            return t
    return "Entity"


def stable_hash(s: str) -> int:
    """Deterministic, process-independent hash (hash() is salted per run)."""
    return int.from_bytes(hashlib.md5(s.encode("utf-8")).digest()[:8], "big")


def hash_embedding(node_id: str, dim: int = HASH_EMB_DIM) -> np.ndarray:
    """Deterministic 16-dim embedding seeded by the node id."""
    rng = np.random.default_rng(stable_hash(node_id) & 0xFFFFFFFF)
    return rng.standard_normal(dim).astype(np.float32)


# ----------------------------------------------------------------------------
# Data loading & feature extraction
# ----------------------------------------------------------------------------
def load_graph(data_path: str | Path = DATA_PATH):
    """Load neo4j_ready.json and return (nodes, edges)."""
    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["nodes"], data["edges"]


def build_features_and_adj(nodes, edges):
    """
    Returns:
      x        : (N, FEAT_DIM) float32 tensor of node features
      adj      : list[list[int]]  undirected neighbor index lists
      idx2id   : list[str] node id for each index
      id2idx   : dict[str,int]
      existing : set of (min,max) undirected edge index pairs
      node_types : list[str] primary type per node
    """
    N = len(nodes)
    idx2id = [n["id"] for n in nodes]
    id2idx = {nid: i for i, nid in enumerate(idx2id)}

    # degrees (undirected)
    deg = [0] * N
    edge_pairs = []
    for e in edges:
        a = id2idx[e["source"]]
        b = id2idx[e["target"]]
        edge_pairs.append((a, b))
        deg[a] += 1
        deg[b] += 1
    max_deg = max(deg) if max(deg) > 0 else 1

    # features
    node_types = [primary_type(n) for n in nodes]
    feats = np.zeros((N, FEAT_DIM), dtype=np.float32)
    for i, n in enumerate(nodes):
        # 1) type one-hot
        t = node_types[i]
        feats[i, TYPE_INDEX[t]] = 1.0
        # 2) label-hash embedding
        feats[i, NUM_TYPES:NUM_TYPES + HASH_EMB_DIM] = hash_embedding(n["id"])
        # 3) normalized degree
        feats[i, NUM_TYPES + HASH_EMB_DIM] = deg[i] / max_deg

    # undirected adjacency (each edge added both ways)
    adj = [[] for _ in range(N)]
    existing = set()
    for a, b in edge_pairs:
        if a == b:
            continue
        adj[a].append(b)
        adj[b].append(a)
        existing.add((min(a, b), max(a, b)))

    x = torch.tensor(feats, dtype=torch.float32)
    return x, adj, idx2id, id2idx, existing, node_types


# ----------------------------------------------------------------------------
# GraphSAGE layers (pure torch message passing + neighbor mean aggregation)
# ----------------------------------------------------------------------------
class GraphSAGELayer(nn.Module):
    """
    h_v^(l+1) = ReLU( W_l · concat( h_v^(l), mean_{u in N(v)} h_u^(l) ) )
    """

    def __init__(self, in_dim: int, out_dim: int, dropout: float = 0.2):
        super().__init__()
        self.linear = nn.Linear(in_dim * 2, out_dim, bias=True)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor, adj: list[list[int]]) -> torch.Tensor:
        N, fin = x.shape
        # neighbor mean aggregation
        neigh = torch.zeros_like(x)
        for i in range(N):
            nb = adj[i]
            if nb:
                neigh[i] = x[nb].mean(dim=0)
            # else: zero vector (no neighbors)
        combined = torch.cat([x, neigh], dim=1)          # (N, 2*in_dim)
        out = F.relu(self.linear(combined))
        out = self.dropout(out)
        return out


class GraphSAGE(nn.Module):
    """2-layer GraphSAGE producing 32-dim node embeddings."""

    def __init__(self, in_dim: int = FEAT_DIM, hidden_dim: int = 32,
                 dropout: float = 0.2):
        super().__init__()
        self.layer1 = GraphSAGELayer(in_dim, hidden_dim, dropout)
        self.layer2 = GraphSAGELayer(hidden_dim, hidden_dim, dropout)

    def forward(self, x: torch.Tensor, adj: list[list[int]]) -> torch.Tensor:
        h = self.layer1(x, adj)
        h = self.layer2(h, adj)
        return h                                      # (N, hidden_dim)


class LinkPredictor(nn.Module):
    """
    Link decoder with two scorers:

      score()       : raw dot product  <h_a, h_b>   -> used for TRAINING
                     (margin-style; lets the loss drive below 0.5 and learn
                     a clean embedding space).
      score_cosine(): L2-normalized dot product (cosine) -> used at INFERENCE.
                     Bounds scores to [-1, 1] so sigmoid gives a well-spread,
                     non-saturated confidence distribution (no constant 1.0s).

    confidence = sigmoid(score_cosine)
    """

    def __init__(self, emb_dim: int = 32):
        super().__init__()
        self.emb_dim = emb_dim

    def score(self, emb: torch.Tensor, a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
        """Raw dot product (training)."""
        return (emb[a] * emb[b]).sum(dim=1)

    def score_cosine(self, emb: torch.Tensor, a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
        """Cosine similarity (inference), bounded in [-1, 1]."""
        e = F.normalize(emb, p=2, dim=1)
        return (e[a] * e[b]).sum(dim=1)

    def conf(self, emb: torch.Tensor, a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
        return torch.sigmoid(self.score_cosine(emb, a, b))


def build_model(in_dim: int = FEAT_DIM, hidden_dim: int = 32, dropout: float = 0.2):
    return GraphSAGE(in_dim=in_dim, hidden_dim=hidden_dim, dropout=dropout)


# ----------------------------------------------------------------------------
# Vocabulary-aware candidate edge-type heuristic (for inferred edges)
# ----------------------------------------------------------------------------
def infer_edge_type(src_type: str, tgt_type: str) -> str:
    """
    Best-guess semantic edge type from the two nodes' primary types.
    Falls back to 'derived_from'. This is a *heuristic label* for an
    inferred structural link; the true relation still needs review.
    """
    s, t = src_type, tgt_type
    pair = {s, t}
    # symbol participating in a formula/definition -> has_symbol
    if "Symbol" in pair and pair & {"Definition", "Formula", "Theorem", "Lemma", "MathConcept"}:
        return "has_symbol"
    # concept defines something
    if "MathConcept" in pair and t in ("Definition", "Formula", "Theorem", "Lemma"):
        return "defines"
    # a theorem/lemma supports a formula/definition -> proves
    if (s in ("Theorem", "Lemma") and t in ("Definition", "Formula")) or \
       (t in ("Theorem", "Lemma") and s in ("Definition", "Formula")):
        return "proves"
    # two definitions / formulas -> derivation dependency
    if pair <= {"Definition", "Formula"}:
        return "derived_from"
    # physical quantities -> dimensional consistency
    if pair == {"PhysicalQuantity"}:
        return "dimensionally_consistent"
    # molecules -> chemical relation
    if pair == {"Molecule"}:
        return "derived_from"
    return "derived_from"


if __name__ == "__main__":
    nodes, edges = load_graph()
    x, adj, idx2id, id2idx, existing, ntypes = build_features_and_adj(nodes, edges)
    model = build_model()
    with torch.no_grad():
        emb = model(x, adj)
    print("nodes:", len(nodes), "edges:", len(edges))
    print("feature dim:", x.shape, "embedding dim:", emb.shape)
    print("existing undirected pairs:", len(existing))
    print("sample node types:", ntypes[:6])
