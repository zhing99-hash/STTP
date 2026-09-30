# -*- coding: utf-8 -*-
"""PhysicsBabel 真实物理方程接入（Phase 9 任务2）。
取 plausible=True 的维度分析方程，构建：
- Formula 节点(PB:fo:<i>)：方程本身。
- PhysicalQuantity 节点：mapped 量直接用已有 PQ:/MX:phy: 节点（真实跨域网桥）；其余建 PB:pq:<name>。
- has_symbol 边：Formula -> 物理量。
- dimensionally_consistent 边：方程内物理量两两相连（PhysicsBabel plausible=维度自洽，R-PHY 已验证）。
输出 11_真实数据/physicsbabel_raw.json（graph_export 原始格式）。
"""
import json, os, re
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PARQUET = os.path.join(HERE, "physicsbabel_circuits.parquet")
OUT = os.path.join(HERE, "physicsbabel_raw.json")
MAX_EQ = 5000  # 接受方程上限（控制图规模，Aura 免费实例约 5 万节点上限）

# PhysicsBabel 量名 -> 已有图谱节点 id（直接连边 = 真实跨域桥）
MAP = {
    "mass": "PQ:mass", "force": "PQ:force", "energy": "PQ:energy",
    "acceleration": "PQ:accel", "velocity": "PQ:vel",
    "kinetic_energy": "PQ:ke", "light_speed": "PQ:c",
}
# 已知常量（无对应节点则建 PB:pq）
CONSTANTS = {"planck", "boltzmann", "gas_constant", "avogadro",
             "grav_const", "elem_charge", "speed_of_light", "light_speed"}


def main():
    df = pd.read_parquet(PARQUET)
    pl = df[df["plausible"] == True]
    print("plausible 方程总数:", len(pl))
    nodes, edges = {}, []
    nid = [0]

    def add_node(nid_str, ntype, props):
        if nid_str not in nodes:
            nodes[nid_str] = {"id": nid_str, "labels": ["Entity", ntype],
                              "props": props}
        else:
            nodes[nid_str]["props"].update(props)
        return nid_str

    accepted = 0
    for _, r in pl.iterrows():
        if accepted >= MAX_EQ:
            break
        keys = [k.strip() for k in str(r["keys"]).split(",") if k.strip()]
        if len(keys) < 2 or len(keys) > 6:
            continue
        eq = str(r["equation"])
        try:
            exp = json.loads(str(r["exponents"]))
        except Exception:
            exp = {}
        foi = "PB:fo:%d" % nid[0]
        nid[0] += 1
        fprops = {"name": eq, "latex": eq, "domain": "phys",
                  "source": "PhysicsBabel", "equation": eq,
                  "dim_exponents": exp, "pb_domains": str(r.get("domains"))}
        add_node(foi, "Formula", fprops)
        qnodes = []
        for k in keys:
            if k in MAP:
                # 已存在于图谱中的物理量：直接按 id 引用（真实跨域桥），不重复建节点
                qid = MAP[k]
            else:
                qid = "PB:pq:" + k
                is_const = k in CONSTANTS
                add_node(qid, "Constant" if is_const else "PhysicalQuantity",
                         {"name": k, "domain": "phys", "source": "PhysicsBabel",
                          "dimension": exp.get(k)})
            qnodes.append(qid)
            edges.append({"id": "PB:hs:%s:%s" % (foi, qid), "source": foi,
                          "target": qid, "type": "has_symbol", "kind": "real",
                          "props": {"confidence": 0.95,
                                    "explicit_or_inferred": "explicit",
                                    "verified": True, "verification_gate": "R-PHY",
                                    "source": "PhysicsBabel",
                                    "rationale": "PhysicsBabel 方程中 %s 为参与量" % k}})
        # 方程内物理量两两 dimensionally_consistent（规模化时仅保留 1 条代表对，避免 C(k,2) 团爆炸；
        # has_symbol 已把公式连到全部参与量，1 条代表对即可表达“该方程量纲自洽”）
        if len(qnodes) >= 2:
            a, b = qnodes[0], qnodes[1]
            edges.append({"id": "PB:dc:%s:%s:%s" % (foi, a, b),
                          "source": a, "target": b,
                          "type": "dimensionally_consistent", "kind": "real",
                          "props": {"confidence": 0.9,
                                    "explicit_or_inferred": "inferred",
                                    "verified": True,
                                    "verification_gate": "R-PHY",
                                    "source": "PhysicsBabel",
                                    "rationale": "方程 %s 中量纲自洽" % eq[:30]}})
        accepted += 1

    out = {"nodes": list(nodes.values()), "edges": edges}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("接受方程:", accepted)
    print("节点:", len(out["nodes"]), "  边:", len(out["edges"]))
    from collections import Counter
    print("节点类型:", Counter(n["labels"][1] for n in out["nodes"]))
    print("边类型:", Counter(e["type"] for e in out["edges"]))
    print("  ->", OUT)


if __name__ == "__main__":
    main()
