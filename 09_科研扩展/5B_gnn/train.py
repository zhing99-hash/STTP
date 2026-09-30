"""
train.py — Phase 5.B GNN training (link prediction).

Loads the 36-node / 52-edge graph, trains a 2-layer GraphSAGE model to
distinguish real edges (positive) from sampled non-edges (negative) via
BCEWithLogitsLoss on a dot-product scorer.

Outputs:
  - checkpoint.pt      (model state + hyperparams + feature meta)
  - loss_curve.png     (training loss curve, English labels)
  - prints loss every 50 epochs
"""

import time

import matplotlib
matplotlib.use("Agg")  # headless / CPU
import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn.functional as F

from gnn_model import (
    SEED, FEAT_DIM, load_graph, build_features_and_adj, build_model, LinkPredictor,
)

# ----------------------------- hyperparams ----------------------------------
EPOCHS = 200
LR = 0.01
HIDDEN_DIM = 32
DROPOUT = 0.2
NEG_RATIO = 1.0           # 1:1 negative sampling
WEIGHT_DECAY = 1e-4
PRINT_EVERY = 50

SCRIPT_DIR = __file__  # placeholder (unused)
from pathlib import Path
OUT_DIR = Path(__file__).resolve().parent
CHECKPOINT = OUT_DIR / "checkpoint.pt"
LOSS_PNG = OUT_DIR / "loss_curve.png"


def sample_negatives(N, existing, n_neg, rng):
    """Sample n_neg unique undirected non-edge pairs (i<j, not in existing)."""
    neg = set()
    attempts = 0
    max_attempts = n_neg * 50 + 1000
    while len(neg) < n_neg and attempts < max_attempts:
        i = rng.integers(0, N)
        j = rng.integers(0, N)
        if i == j:
            attempts += 1
            continue
        key = (min(i, j), max(i, j))
        if key in existing or key in neg:
            attempts += 1
            continue
        neg.add(key)
        attempts += 1
    return list(neg)


def main():
    t0 = time.time()
    nodes, edges = load_graph()
    x, adj, idx2id, id2idx, existing, ntypes = build_features_and_adj(nodes, edges)
    N = x.shape[0]

    # positive pairs
    pos = list(existing)
    n_pos = len(pos)
    n_neg = int(n_pos * NEG_RATIO)

    rng = np.random.default_rng(SEED)
    neg = sample_negatives(N, existing, n_neg, rng)

    # tensors
    pos_a = torch.tensor([p[0] for p in pos], dtype=torch.long)
    pos_b = torch.tensor([p[1] for p in pos], dtype=torch.long)
    neg_a = torch.tensor([p[0] for p in neg], dtype=torch.long)
    neg_b = torch.tensor([p[1] for p in neg], dtype=torch.long)

    all_a = torch.cat([pos_a, neg_a])
    all_b = torch.cat([pos_b, neg_b])
    all_y = torch.cat([torch.ones(n_pos), torch.zeros(n_neg)])

    model = build_model(in_dim=FEAT_DIM, hidden_dim=HIDDEN_DIM, dropout=DROPOUT)
    predictor = LinkPredictor(emb_dim=HIDDEN_DIM)
    opt = torch.optim.Adam(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)

    losses = []
    best_loss = float("inf")
    for epoch in range(1, EPOCHS + 1):
        model.train()
        opt.zero_grad()
        emb = model(x, adj)
        scores = predictor.score(emb, all_a, all_b)
        loss = F.binary_cross_entropy_with_logits(scores, all_y)
        loss.backward()
        opt.step()
        losses.append(loss.item())
        if loss.item() < best_loss:
            best_loss = loss.item()
        if epoch % PRINT_EVERY == 0 or epoch == 1:
            with torch.no_grad():
                pred = torch.sigmoid(scores)
                acc = ((pred > 0.5).float() == all_y).float().mean().item()
            print(f"[epoch {epoch:3d}/{EPOCHS}] loss={loss.item():.4f} "
                  f"acc={acc:.3f} (best={best_loss:.4f})")

    # ---- loss curve ----
    plt.figure(figsize=(7, 4))
    plt.plot(range(1, EPOCHS + 1), losses, color="#1f77b4", linewidth=1.8)
    plt.title("Phase5.B GraphSAGE Link-Prediction Training Loss")
    plt.xlabel("Epoch")
    plt.ylabel("BCEWithLogits Loss")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(LOSS_PNG, dpi=120)
    plt.close()

    # ---- save checkpoint ----
    torch.save({
        "state_dict": model.state_dict(),
        "hyperparams": {
            "in_dim": FEAT_DIM,
            "hidden_dim": HIDDEN_DIM,
            "dropout": DROPOUT,
            "epochs": EPOCHS,
            "lr": LR,
            "seed": SEED,
        },
        "node_types": ntypes,
        "idx2id": idx2id,
    }, CHECKPOINT)

    elapsed = time.time() - t0
    print(f"\nDone. final_loss={losses[-1]:.4f}  best_loss={best_loss:.4f}")
    print(f"checkpoint -> {CHECKPOINT}")
    print(f"loss curve -> {LOSS_PNG}")
    print(f"elapsed={elapsed:.1f}s  (CPU)")


if __name__ == "__main__":
    main()
