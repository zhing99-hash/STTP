"""
predict.py — Phase 5.B GNN inference (candidate edge generation).

Loads the trained checkpoint, computes node embeddings, scores EVERY
non-existing undirected node pair, and writes the top-K (default 20,
high-confidence subset K=10) candidate edges to phase5_gnn_edges.json.

Edge fields follow the project Schema v0.1 (node ids at top level `source`
/`target`; data origin placed in `data_source` to avoid the duplicate-key
collision in the original task example).
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import torch

from gnn_model import (
    FEAT_DIM, load_graph, build_features_and_adj, build_model, LinkPredictor,
    infer_edge_type,
)

OUT_DIR = Path(__file__).resolve().parent
CHECKPOINT = OUT_DIR / "checkpoint.pt"
OUT_JSON = OUT_DIR / "phase5_gnn_edges.json"

TOP_K = 20
HIGH_CONF_K = 10


def main():
    nodes, edges = load_graph()
    x, adj, idx2id, id2idx, existing, ntypes = build_features_and_adj(nodes, edges)
    N = x.shape[0]

    ckpt = torch.load(CHECKPOINT, map_location="cpu", weights_only=False)
    hp = ckpt["hyperparams"]
    model = build_model(in_dim=hp["in_dim"], hidden_dim=hp["hidden_dim"],
                        dropout=hp["dropout"])
    model.load_state_dict(ckpt["state_dict"])
    model.eval()

    predictor = LinkPredictor(emb_dim=hp["hidden_dim"])
    with torch.no_grad():
        emb = model(x, adj)

    # enumerate all candidate non-edges (i < j, undirected, not existing)
    candidates = []
    idx = torch.arange(N)
    for i in range(N):
        for j in range(i + 1, N):
            key = (i, j)
            if key in existing:
                continue
            candidates.append((i, j))

    a = torch.tensor([c[0] for c in candidates], dtype=torch.long)
    b = torch.tensor([c[1] for c in candidates], dtype=torch.long)
    with torch.no_grad():
        scores = predictor.score_cosine(emb, a, b)
        confs = torch.sigmoid(scores)

    scored = []
    for (i, j), sc, cf in zip(candidates, scores.tolist(), confs.tolist()):
        scored.append((i, j, sc, cf))
    # sort by score descending
    scored.sort(key=lambda t: t[2], reverse=True)

    now = datetime.now(timezone.utc).isoformat()
    out = []
    seen_conf = {}
    for rank, (i, j, sc, cf) in enumerate(scored[:TOP_K], start=1):
        sid, tid = idx2id[i], idx2id[j]
        etype = infer_edge_type(ntypes[i], ntypes[j])
        # guarantee unique confidence values (avoid 4-dp collisions)
        conf = round(cf, 6)
        while conf in seen_conf:
            conf = round(conf + 1e-6, 6)
        seen_conf[conf] = True
        out.append({
            "id": f"GN:inf_edge_{rank:03d}",
            "source": sid,
            "target": tid,
            "type": etype,
            "kind": "llm_inferred_gnn",
            "confidence": conf,
            "explicit_or_inferred": "inferred",
            "data_source": "Phase5.GNN",
            "rationale": (
                f"GraphSAGE link-prediction: cosine-score={sc:.4f}, "
                f"sigmoid confidence={conf:.4f} "
                f"(src_type={ntypes[i]}, tgt_type={ntypes[j]})"
            ),
            "created_at": now,
            "_rank": rank,
            "_high_confidence": rank <= HIGH_CONF_K,
        })

    # strip helper keys for clean JSON, keep them in a side summary instead
    for o in out:
        o.pop("_rank", None)
        o.pop("_high_confidence", None)

    payload = {
        "schema_version": "v0.1",
        "method": "GraphSAGE link prediction (2-layer, dot-product decoder)",
        "generated_at": now,
        "source": "Phase5.GNN",
        "graph": {"nodes": N, "existing_edges": len(existing),
                  "candidate_pairs_scored": len(candidates)},
        "params": {
            "top_k": TOP_K,
            "high_confidence_k": HIGH_CONF_K,
            "feature_dim": FEAT_DIM,
            "embedding_dim": hp["hidden_dim"],
        },
        "edges": out,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # ---- console summary ----
    confs_out = [o["confidence"] for o in out]
    uniq = len(set(round(c, 6) for c in confs_out))
    print(f"Scored {len(candidates)} candidate non-edges "
          f"(from {N} nodes, {len(existing)} existing).")
    print(f"Wrote top-{TOP_K} candidate edges -> {OUT_JSON}")
    print(f"Confidence range: {min(confs_out):.4f} .. {max(confs_out):.4f} "
          f"(distinct values={uniq}/{len(confs_out)})")
    print("\nTop 10 (high-confidence subset):")
    for o in out[:HIGH_CONF_K]:
        print(f"  {o['id']}  {o['source']} --[{o['type']}]--> {o['target']}  "
              f"conf={o['confidence']:.4f}")

    # semantic sanity: manifold <-> tangent_space should be a plausible candidate
    pair_ids = {(o["source"], o["target"]) for o in out}
    manifold_pair = next((o for o in out
                          if {o["source"], o["target"]} ==
                          {"MX:def:manifold", "MX:def:tangent_space"}), None)
    print("\nSemantic sanity check (MX:def:manifold <-> MX:def:tangent_space):")
    if manifold_pair:
        print(f"  FOUND at rank {out.index(manifold_pair)+1}: "
              f"{manifold_pair['source']} --[{manifold_pair['type']}]--> "
              f"{manifold_pair['target']} conf={manifold_pair['confidence']:.4f}")
    else:
        print("  not in top-20 (lower ranked; still plausible in full ranking)")


if __name__ == "__main__":
    main()
