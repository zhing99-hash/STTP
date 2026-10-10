# -*- coding: utf-8 -*-
"""第 22 轮侦察 ③：证据明细可取回率（只读）。

对全图分类，统计：level 分布 × scope 分布 × 「ctx 里是否有明细串可取回」。
目的是判断「Claim/Evidence 对象化」到底是「持久化已算出的东西」还是「重新算」。
"""
import os, sys, json, collections
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "11_真实数据"))
import verification_model as vm

# scope -> ctx 容器（A 段确定性复算的证据明细来源）
SCOPE_TO_CTX = {
    "dimensional_strict_equal": "dim_ok",
    "period_authority_equal": "period_ok",
    "family_authority_equal": "family_ok",
    "formula_count_independent_recheck": "composed_ok",
    "formula_count_cross_source": "composed_ok",
    "formula_count_node_recheck": "composed_ok",
    "molar_mass_independent_recompute": "mass_ok",
    "molar_mass_cross_source": "mass_ok",
    "unit_dimension_recompute": "unit_ok",
    "symbol_expr_recompute": "sym_expr_ok",
}

RANK = vm.RANK


def main():
    nodes, edges = vm.load_norm()
    ctx = vm.build_ctx(nodes, edges)
    print("规模 %d 节点 / %d 边" % (len(nodes), len(edges)))
    print()

    lvl_sc = collections.Counter()
    detail_have = collections.Counter()
    detail_miss = collections.Counter()
    no_ctx_scope = collections.Counter()

    for e in edges:
        r = vm.classify(e, ctx)
        if r is None:
            continue
        lv = r["verification_level"]
        sc = r["verification_scope"]
        lvl_sc[(lv, sc)] += 1
        eid = e.get("id") or "%s|%s|%s" % (e.get("type"), e.get("source"), e.get("target"))
        cont = SCOPE_TO_CTX.get(sc)
        if cont:
            entry = ctx[cont].get(eid)
            if entry is not None and (len(entry) >= 2 and entry[1]):
                detail_have[sc] += 1
            else:
                detail_miss[sc] += 1
        elif RANK.get(lv, 0) >= 4:
            # rule_checked+ 但不属于可映射的 A 段容器 → 需内联 note
            if r.get("note"):
                detail_have[sc] += 1
            else:
                no_ctx_scope[sc] += 1

    print("== 按 (level, scope) 分布 ==")
    by_lv = collections.defaultdict(int)
    for (lv, sc), c in lvl_sc.items():
        by_lv[lv] += c
    for lv in ("unverified", "model_inferred", "source_asserted", "by_construction",
               "rule_checked", "cross_source", "human_reviewed"):
        print("   %-18s %6d" % (lv, by_lv.get(lv, 0)))
    print()

    print("== level>=rule_checked 的 scope：明细可取回情况 ==")
    print("   %-42s %8s %8s %8s" % ("scope", "total", "have", "miss"))
    tot_have = tot_miss = 0
    for (lv, sc), c in sorted(lvl_sc.items(), key=lambda x: -x[1]):
        if RANK.get(lv, 0) < 4:
            continue
        h = detail_have.get(sc, 0)
        m = detail_miss.get(sc, 0)
        # 只报该 scope 的行（同 scope 可能跨 level）
    # 汇总 per-scope
    sc_tot = collections.Counter()
    for (lv, sc), c in lvl_sc.items():
        if RANK.get(lv, 0) >= 4:
            sc_tot[sc] += c
    for sc, c in sc_tot.most_common():
        h = detail_have.get(sc, 0)
        m = detail_miss.get(sc, 0)
        n = no_ctx_scope.get(sc, 0)
        tot_have += h
        tot_miss += (m + n)
        flag = "OK" if (m + n) == 0 else "⚠"
        print("   %-42s %8d %8d %8d %s" % (sc, c, h, m + n, flag))
    print()
    T = tot_have + tot_miss
    print("== 结论 ==")
    print("   level>=rule_checked 合计 %d；其中可从 ctx/note 取回明细 %d (%.1f%%)；缺 %d (%.1f%%)"
          % (T, tot_have, 100.0 * tot_have / max(T, 1), tot_miss, 100.0 * tot_miss / max(T, 1)))
    print()
    print("   缺明细的 scope 明细:", dict(detail_miss.most_common(10)))
    print("   无 ctx 容器且无 note 的 scope:", dict(no_ctx_scope.most_common(10)))


if __name__ == "__main__":
    main()
