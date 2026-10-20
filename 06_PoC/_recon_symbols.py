# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""G1 侦察：`symbols` 回填到底能拿到什么（先量，后写）。

要回答四个问题，答不上来就不许动 delta：
  R1 图上 `has_symbol` 边指向的是**符号**还是**物理量**？（决定回填语义）
  R2 PhysicsBabel 的 `symbols` 列与 `keys` 列**是否逐位对齐**？
  R3 能否用「共现签名」把量名 → 字形对上（不靠逐位对齐）？唯一解占比多少？
  R4 若只按边回填（不碰 parquet），`symbols` 覆盖率能到多少？差的那部分是哪些节点？
"""
import json
import os
import sys
import collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
PARQUET = os.path.join(ROOT, "11_真实数据", "physicsbabel_circuits.parquet")


def load_graph():
    with open(NORM, encoding="utf-8") as f:
        g = json.load(f)
    return g["nodes"], g["edges"]


def main():
    nodes, edges = load_graph()
    by_id = {n["id"]: n for n in nodes}
    print("=" * 92)
    print("R1  边端点语义")
    print("=" * 92)
    tgt_kind = collections.Counter()
    for e in edges:
        if e.get("type") != "has_symbol":
            continue
        t = by_id.get(e["target"]) or {}
        tgt_kind[":".join(t.get("labels") or ["MISSING"])] += 1
    for k, v in tgt_kind.most_common():
        print("   目标标签 %-40s %6d" % (k, v))
    has_sym = sum(v for k, v in tgt_kind.items() if "Symbol" in k)
    print("   ⇒ 指向真 Symbol 节点的边：%d / %d（%.2f%%）"
          % (has_sym, sum(tgt_kind.values()), 100.0 * has_sym / max(1, sum(tgt_kind.values()))))

    # 目标节点上到底有没有字形
    with_glyph = sum(1 for e in edges if e.get("type") == "has_symbol"
                     and (by_id.get(e["target"]) or {}).get("props", {}).get("symbol"))
    print("   ⇒ 目标节点自带 `symbol`（字形）的边：%d" % with_glyph)

    print()
    print("=" * 92)
    print("R2  PhysicsBabel symbols 列与 keys 列是否逐位对齐")
    print("=" * 92)
    try:
        import pandas as pd
    except Exception as exc:  # noqa: BLE001
        print("   [SKIP] pandas 不可用：%s" % exc)
        return 0
    df = pd.read_parquet(PARQUET)
    df = df[df["plausible"] == True]  # noqa: E712
    n_ok = n_bad = 0
    pos_ok = pos_bad = 0
    samples = []
    for _, r in df.head(4000).iterrows():
        keys = [k.strip() for k in str(r["keys"]).split(",") if k.strip()]
        toks = [t.strip() for t in str(r["symbols"]).split("·") if t.strip()]
        if len(keys) != len(toks):
            n_bad += 1
            if len(samples) < 5:
                samples.append((keys, toks))
        else:
            n_ok += 1
            # 逐位猜：key 首字母（忽略大小写）== 字形首字母？
            good = sum(1 for k, t in zip(keys, toks)
                       if k[:1].lower() == t[:1].lower())
            if good == len(keys):
                pos_ok += 1
            else:
                pos_bad += 1
    print("   长度一致 %d / 不一致 %d" % (n_ok, n_ok + n_bad))
    for keys, toks in samples:
        print("     长度不符样例 keys=%s symbols=%s" % (keys, toks))
    print("   逐位首字母全对 %d / 逐位不符 %d  ⇒ 逐位对齐%s"
          % (pos_ok, pos_bad, "成立" if pos_bad == 0 else "不成立（必须换方法）"))

    print()
    print("=" * 92)
    print("R3  共现签名：量名 ↔ 字形 能不能唯一对上")
    print("=" * 92)
    # 只取图上真在的公式：以 equation 文本对齐，不靠下标
    fo_eq = {}
    for n in nodes:
        if n["id"].startswith("PB:fo:"):
            eq = (n.get("props") or {}).get("equation")
            if eq:
                fo_eq.setdefault(eq, []).append(n["id"])
    name_sig = collections.defaultdict(set)     # 量名 → 出现的 equation 集合
    glyph_sig = collections.defaultdict(set)    # 字形 → 出现的 equation 集合
    eq_glyphs = {}
    hit = 0
    for _, r in df.iterrows():
        eq = str(r["equation"])
        if eq not in fo_eq:
            continue
        hit += 1
        keys = [k.strip() for k in str(r["keys"]).split(",") if k.strip()]
        toks = [t.strip() for t in str(r["symbols"]).split("·") if t.strip()]
        eq_glyphs[eq] = toks
        for k in keys:
            name_sig[k].add(eq)
        for t in toks:
            glyph_sig[t].add(eq)
    print("   图上 PB 公式与 parquet 按 equation 文本对上：%d（图内 %d）"
          % (hit, len(fo_eq)))
    uniq, amb, none = 0, 0, 0
    amb_samples = []
    mapping = {}
    for name, s in name_sig.items():
        cand = [g for g, gs in glyph_sig.items() if gs == s]
        if len(cand) == 1:
            uniq += 1
            mapping[name] = cand[0]
        elif len(cand) > 1:
            amb += 1
            if len(amb_samples) < 8:
                amb_samples.append((name, cand))
        else:
            none += 1
    print("   量名 %d 个：唯一解 %d ／ 多解 %d ／ 无解 %d（%.1f%% 唯一）"
          % (len(name_sig), uniq, amb, none, 100.0 * uniq / max(1, len(name_sig))))
    for nm, cand in amb_samples:
        print("     多解样例 %-16s → %s" % (nm, cand))
    print("   正对照（人工已知）：" + "  ".join(
        "%s→%s" % (k, mapping.get(k, "?"))
        for k in ("mass", "length", "time", "force", "energy", "velocity", "acceleration")))
    print("   反向多义（同名字形给多个量）：" + str(
        sum(1 for g in mapping.values() if list(mapping.values()).count(g) > 1)))

    print()
    print("=" * 92)
    print("R4  只按边回填的覆盖率上限")
    print("=" * 92)
    formulas = [n for n in nodes if "Formula" in (n.get("labels") or [])]
    srcs = set()
    for e in edges:
        if e.get("type") == "has_symbol":
            srcs.add(e["source"])
    print("   公式节点 %d；有 has_symbol 出边的 %d（%.1f%%）"
          % (len(formulas), len(srcs), 100.0 * len(srcs) / max(1, len(formulas))))
    noedge = [n for n in formulas if n["id"] not in srcs]
    pre = collections.Counter(":".join(n["id"].split(":")[:2]) for n in noedge)
    print("   无出边的公式按前缀：%s" % pre.most_common(8))
    return 0


if __name__ == "__main__":
    sys.exit(main())
