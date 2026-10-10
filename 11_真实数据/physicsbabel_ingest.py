# -*- coding: utf-8 -*-
"""PhysicsBabel 真实物理方程接入（Phase 9 任务2）。
取 plausible=True 的维度分析方程，构建：
- Formula 节点(PB:fo:<i>)：方程本身。
- PhysicalQuantity 节点：mapped 量直接用已有 PQ:/MX:phy: 节点（真实跨域网桥）；其余建 PB:pq:<name>。
- has_symbol 边：Formula -> 物理量。
- dimensionally_consistent 边：**仅在两物理量量纲严格相等时**建立（依据 dimension_table
  从 PhysicsBabel exponents 消元反解的真值表）；方程**齐次性**另记为 Formula 属性
  `dim_homogeneous` + `dim_scope=equation_homogeneity`，不再伪装成量-量边。
输出 11_真实数据/physicsbabel_raw.json（graph_export 原始格式）。

⚠ P0-1 修复（2026-10-10 · 可信性修复轮）
    旧版把「方程维度自洽（整式齐次）」**误写**为「方程内前两个参与量**量纲一致**」并标
    verified=True —— 这是错误命题，曾沉淀 1130 条脏边。本版按真值表严格重建。
"""
import json, os, re
import pandas as pd

import dimension_table as dm

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
    qname_by_id = {}          # 节点 id -> PhysicsBabel 量名（用于量纲真值表查询）
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
        hom = dm.is_homogeneous(keys, exp)
        fprops = {"name": eq, "latex": eq, "domain": "phys",
                  "source": "PhysicsBabel", "equation": eq,
                  "dim_exponents": exp, "pb_domains": str(r.get("domains"))}
        if hom is not None:
            # 方程级齐次性（真事实）。三态：True/False 已知；None=含未知量纲量，不写。
            fprops["dim_homogeneous"] = hom
            fprops["dim_scope"] = "equation_homogeneity"
            fprops["dim_verified_by"] = "dimension_table(PhysicsBabel exponents)"
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
        # ⚠ P0-1 修复（2026-10-10）：**删除**原「取前两个参与量建 dimensionally_consistent
        # + verified=True」。那是错误命题——方程齐次（整式）≠ 两量量纲一致，且未做任何
        # 量纲比对。齐次性已改记于 Formula 属性 dim_homogeneous；量-量「量纲一致」边
        # 改在循环外、按真值表**严格相等**统一建立（见下）。
        for k, qid in zip(keys, qnodes):
            qname_by_id.setdefault(qid, k)
        accepted += 1

    # ── 量层面的 dimensionally_consistent：**仅量纲严格相等**才建（P0-1 修复核心）──
    # 依据 dimension_table（从 PhysicsBabel exponents 消元反解的真量纲向量）。
    # 例：energy↔torque（均 ML²T⁻²）、action↔ang_momentum↔planck（均 ML²T⁻¹）成立；
    # 而 mass vs length、force vs energy 等**不再**误连。未知量纲者不参与（宁缺勿滥）。
    id_by_name = {}
    for qid, k in qname_by_id.items():
        id_by_name.setdefault(k, qid)
    dc_pairs = dm.same_dimension_pairs(set(qname_by_id.values()))
    for na, nb in dc_pairs:
        ida, idb = id_by_name.get(na), id_by_name.get(nb)
        if not ida or not idb or ida == idb:
            continue
        da = dm.dim_of(na)
        edges.append({"id": "PB:dc:%s:%s" % (ida, idb),
                      "source": ida, "target": idb,
                      "type": "dimensionally_consistent", "kind": "dimension_table",
                      "props": {"confidence": 0.98,
                                "explicit_or_inferred": "inferred",
                                "verified": True,
                                "verification_gate": "R-PHY",
                                "verification_scope": "dimensional_only",
                                "source": "dimension_table",
                                "dim_vector": {k: da.get(k, 0) for k in dm.DIM if da.get(k)},
                                "rationale": "量纲严格相等（消元反解自 PhysicsBabel exponents）"}})

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
