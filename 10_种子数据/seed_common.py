# -*- coding: utf-8 -*-
"""Phase 7 种子切片共用工具：类型标签、节点/边构造、校验、写出。

用法：各切片 build_*.py 里 `import seed_common as sc`，调用 sc.n / sc.e / sc.build_and_write。
所有切片节点统一附加 Entity 标签；ntype 用全小写（供 Aura type_stats），
labels 用 CamelCase（命中 graph_export.TYPE_PRIORITY 与 graph_view.TYPE_STYLE）。
domain 驱动可视化学科着色（chem / phys / math）。
"""
import json
import os
from collections import Counter
from datetime import datetime

SOURCE = "curated_seed"
NOW = datetime.now().isoformat()
CONF = 1.0

TYPE_LABEL = {
    "element": "Element", "unit": "Unit", "physical_quantity": "PhysicalQuantity",
    "symbol": "Symbol", "formula": "Formula", "molecule": "Molecule",
    "reaction": "Reaction", "constant": "Constant",
}

# 基础图谱(Phase5 + Phase6)中已存在的 canonical id：
# 切片用 same_as 桥接或直接在边里引用，无需自建重复节点。
BASE_IDS = {
    # 物理量
    "PQ:mass", "PQ:energy", "PQ:c", "PQ:force", "PQ:accel", "PQ:vel", "PQ:ke",
    # 公式
    "FO:fma", "FO:ke", "FO:emc2",
    # 单位
    "UN:kg", "UN:m", "UN:s", "UN:j", "UN:mps", "UN:mps2",
    # 分子 / 元素 / 反应
    "MO:ch4", "MO:o2", "MO:co2", "MO:h2o", "EL:c", "EL:h", "EL:o", "RX:comb_ch4",
    # 符号
    "SY:c", "SY:m", "SY:a", "SY:v", "SY:e", "SY:f", "SY:ke",
}


def n(id, ntype, name, domain="", **props):
    p = {"name": name, "ntype": ntype, "domain": domain, "source": SOURCE,
         "confidence": CONF, "explicit_or_inferred": "explicit", "created_at": NOW}
    p.update(props)
    return {"id": id, "labels": ["Entity", TYPE_LABEL[ntype]], "props": p}


def e(src, tgt, etype, kind, **props):
    p = {"confidence": CONF, "explicit_or_inferred": "explicit",
         "kind": kind, "source": SOURCE, "created_at": NOW}
    p.update(props)
    return {"id": f"{src}->{tgt}[{etype}]", "source": src, "target": tgt,
            "type": etype, "kind": kind, "props": p}


def build_and_write(out_path, phase, note, nodes, edges, extra_external=None, pint_checks=None):
    ids = {x["id"] for x in nodes}
    known = BASE_IDS | (extra_external or set())
    assert len(ids) == len(nodes), f"节点 id 重复: {len(nodes) - len(ids)}"
    dangling = [x for x in edges if x["source"] not in ids | known
                or x["target"] not in ids | known]
    assert not dangling, f"悬空边: {dangling[:5]}"
    bad = [x["type"] for x in edges if not x["type"].islower()]
    assert not bad, f"边类型非全小写: {bad}"
    out = {"schema_version": "0.1", "phase": phase, "note": note,
           "nodes": nodes, "edges": edges}
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[OK] {os.path.basename(out_path)}: 节点 {len(nodes)} 边 {len(edges)}")
    print("     节点类型:", dict(Counter(x["labels"][-1] for x in nodes)))
    print("     边类型:", dict(Counter(x["type"] for x in edges)))
    if pint_checks:
        try:
            import pint
            u = pint.UnitRegistry()
            for label, ok in pint_checks(u):
                print(f"     [pint] {label}: {ok}")
        except Exception as ex:
            print(f"     [pint] 跳过: {type(ex).__name__}")
    return out
