# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""
PoC 主脚本：加载 MathXiv / ArxiTeX 风格的数学工件 JSON，
构建 NetworkX 拓扑图并用 matplotlib 渲染首张公式拓扑图。

- 优先读取同目录下的 sample_mathxiv.json（由数据工程 agent 产出）
- 若文件缺失，使用内置兜底样例，保证脚本始终可运行
- 节点按类型着色，边按 kind（explicit / inferred）区分线型，
  按 confidence 控制透明度，直观体现"可靠边 vs 推断边"

运行：python load_mathxiv_networkx.py
依赖：networkx, matplotlib
"""
import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_PATH = os.path.join(HERE, "sample_mathxiv.json")
OUT_DIR = os.path.join(HERE, "output")
OUT_PNG = os.path.join(OUT_DIR, "math_topo_graph.png")


def load_data():
    """加载样例数据；缺失则使用内置兜底。"""
    if os.path.exists(SAMPLE_PATH):
        with open(SAMPLE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"[OK] 已加载样例：{SAMPLE_PATH}")
        return data
    print(f"[WARN] 未找到 {SAMPLE_PATH}，使用内置兜底样例。")
    return FALLBACK


# 内置兜底样例（结构贴合 MathXiv/ArxiTeX 输出）
FALLBACK = {
    "paper_id": "fallback-demo",
    "nodes": [
        {"id": "def:group", "type": "definition", "latex": r"G\ \text{is a group if }(G,\cdot)\ \text{is associative, has identity }e,\ \text{and inverses.}", "informal_text": "群的定义", "symbols": ["G", "e"]},
        {"id": "def:subgroup", "type": "definition", "latex": r"H\le G\ \text{if }H\subseteq G\ \text{and }H\ \text{is a group under the same operation.}", "informal_text": "子群定义", "symbols": ["H", "G"]},
        {"id": "lem:identity_unique", "type": "lemma", "latex": r"\text{The identity }e\in G\ \text{is unique.}", "informal_text": "单位元唯一", "symbols": ["e", "G"]},
        {"id": "thm:lagrange", "type": "theorem", "latex": r"|H|\ \text{divides }|G|\ \text{for any }H\le G.", "informal_text": "拉格朗日定理", "symbols": ["H", "G"]},
        {"id": "thm:coset_partition", "type": "theorem", "latex": r"G\ \text{is partitioned by the left cosets of }H.", "informal_text": "陪集划分群", "symbols": ["G", "H"]},
        {"id": "def:coset", "type": "definition", "latex": r"gH=\{gh:h\in H\}\ \text{is a left coset.}", "informal_text": "陪集定义", "symbols": ["g", "H"]},
    ],
    "dependencies": [
        {"source": "lem:identity_unique", "target": "def:group", "kind": "explicit_citation", "confidence": 1.0},
        {"source": "def:subgroup", "target": "def:group", "kind": "explicit_citation", "confidence": 1.0},
        {"source": "def:coset", "target": "def:subgroup", "kind": "explicit_citation", "confidence": 1.0},
        {"source": "thm:coset_partition", "target": "def:coset", "kind": "explicit_citation", "confidence": 1.0},
        {"source": "thm:coset_partition", "target": "def:subgroup", "kind": "explicit_citation", "confidence": 0.95},
        {"source": "thm:lagrange", "target": "thm:coset_partition", "kind": "explicit_citation", "confidence": 1.0},
        {"source": "thm:lagrange", "target": "def:subgroup", "kind": "explicit_citation", "confidence": 1.0},
        {"source": "thm:lagrange", "target": "lem:identity_unique", "kind": "llm_inferred", "confidence": 0.62},
    ],
    "definition_bank": [
        {"symbol": "G", "definition": "一个群（集合+二元运算）"},
        {"symbol": "H", "definition": "G 的子群"},
        {"symbol": "e", "definition": "G 的单位元"},
        {"symbol": "g", "definition": "G 中的任意元素"},
    ],
}


def build_graph(data):
    import networkx as nx
    G = nx.DiGraph()

    type_color = {
        "theorem": "#e74c3c",
        "lemma": "#e67e22",
        "definition": "#2980b9",
    }
    for n in data.get("nodes", []):
        G.add_node(
            n["id"],
            label=n.get("id").split(":", 1)[-1],
            ntype=n.get("type", "unknown"),
            latex=n.get("latex", ""),
            informal=n.get("informal_text", ""),
            color=type_color.get(n.get("type"), "#7f8c8d"),
        )

    for e in data.get("dependencies", []):
        G.add_edge(
            e["source"],
            e["target"],
            kind=e.get("kind", "unknown"),
            confidence=float(e.get("confidence", 0.0)),
        )
    return G


def draw(G):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    os.makedirs(OUT_DIR, exist_ok=True)
    pos = _layout(G)

    plt.figure(figsize=(11, 8))
    # 边：显式=实线，推断=虚线；透明度随置信度
    for u, v, d in G.edges(data=True):
        style = "-" if d["kind"] == "explicit_citation" else "--"
        alpha = 0.25 + 0.7 * d["confidence"]
        color = "#27ae60" if d["kind"] == "explicit_citation" else "#c0392b"
        plt.plot([pos[u][0], pos[v][0]], [pos[u][1], pos[v][1]],
                 style, color=color, alpha=alpha, linewidth=1.6, zorder=1)

    # 节点
    for n, attr in G.nodes(data=True):
        x, y = pos[n]
        plt.scatter(x, y, s=900, c=attr["color"], edgecolors="white",
                    linewidths=1.5, zorder=2)
        plt.text(x, y, attr["label"], ha="center", va="center",
                 fontsize=8, color="white", fontweight="bold", zorder=3)

    # 图例
    legend = [
        Line2D([0], [0], color="#27ae60", lw=2, label="explicit citation"),
        Line2D([0], [0], color="#c0392b", lw=2, linestyle="--", label="LLM inferred"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#e74c3c", markersize=10, label="theorem"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#e67e22", markersize=10, label="lemma"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#2980b9", markersize=10, label="definition"),
    ]
    plt.legend(handles=legend, loc="upper left", fontsize=8, framealpha=0.9)
    plt.title("Math Formula Topology Graph (PoC - MathXiv/ArxiTeX style)",
              fontsize=12)
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(OUT_PNG, dpi=150)
    plt.close()
    print(f"[OK] 拓扑图已保存：{OUT_PNG}")


def _layout(G):
    """优先用 spring 布局；若节点过多回退到 kamada-kawai。"""
    import networkx as nx
    try:
        return nx.spring_layout(G, seed=42, k=1.6, iterations=200)
    except Exception:
        return nx.kamada_kawai_layout(G)


def summary(G):
    print(f"\n=== 图谱统计 ===")
    print(f"节点数: {G.number_of_nodes()}  边数: {G.number_of_edges()}")
    kinds = {}
    for _, _, d in G.edges(data=True):
        kinds[d["kind"]] = kinds.get(d["kind"], 0) + 1
    print(f"边类型分布: {kinds}")
    inferred = [(u, v, d["confidence"]) for u, v, d in G.edges(data=True)
                if d["kind"] == "llm_inferred"]
    if inferred:
        print(f"[!] 推断边（需置信度标注/降权）: {len(inferred)} 条")
        for u, v, c in inferred:
            print(f"   {u} -> {v}  conf={c}")


def main():
    try:
        import networkx  # noqa
        import matplotlib  # noqa
    except ImportError as e:
        print(f"[ERROR] 缺少依赖：{e}。请运行: pip install networkx matplotlib")
        return
    data = load_data()
    G = build_graph(data)
    summary(G)
    draw(G)
    print("\n[Done] PoC 主脚本执行完毕。")


if __name__ == "__main__":
    main()
