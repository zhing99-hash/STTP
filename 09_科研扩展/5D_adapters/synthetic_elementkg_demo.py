# -*- coding: utf-8 -*-
"""5D 演示：用 ElementKG 适配器加载 30 个合成反应，验证 ETL 接口正确。"""
import json
import os
from elementkg_adapter import ElementKGAdapter, synthetic_elementkg_sample

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "elementkg_synthetic.json")

if __name__ == "__main__":
    recs = synthetic_elementkg_sample(30)
    out = ElementKGAdapter().convert(recs)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[OK] ElementKG 合成样本: {out['reaction_count']} 反应 / {out['molecule_count']} 物质")
    print(f"     节点 {len(out['nodes'])} / 边 {len(out['edges'])}")
    print(f"     写出 -> {os.path.relpath(OUT)}")
    # 断言：反应节点应为 30，边应含 reactant_of/product_of
    assert out["reaction_count"] == 30
    assert any(e["type"] == "product_of" for e in out["edges"])
    print("[ASSERT] 接口正确：Reaction 节点 + 超边关系存在")
    print("[END] exit=0")
