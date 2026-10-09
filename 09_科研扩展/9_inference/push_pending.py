# -*- coding: utf-8 -*-
"""**全部离线增量**的顺序推送编排（可安全重复执行）。

2026-10-09 起累计**十四项修复**已推送到 Aura（云端与本地严格一致。
最初推到 7382 节点 / 40207 边；本轮连通性审计收尾后本地为 7339 节点 / 40249 边）。
本编排保留为**幂等重放通道** —— 换实例、回滚、
或将来新增 delta 时直接重跑即可。各步骤：

    1) 元素层数据质量修复 -> 06_PoC/etl/neo4j/phase13_elementfix_delta.json
                              （氧元素回图 + EK2:el:S 纠正 + 14 个元素原子量）
    2) GNN 带类型边重训   -> 09_科研扩展/9_inference/phase13_typed_edges.json
                              （3600 条带类型边，3404 条门控 VERIFIED）
    3) 元素节点规范化去重 -> 06_PoC/etl/neo4j/phase15_elementmerge_delta.json
                              （四套命名空间 EK/EK2/EL/IC/BC 统一到 EK:el:<Symbol>，
                                删 20 个别名节点、改挂 2917 条边）
    4) 元素周期表位置修复 -> 06_PoC/etl/neo4j/phase16_periodic_delta.json
                              （118 个元素对齐 IUPAC period/group/block/series，
                                修正 O 的 atomic_number，删 308 条语义错误的 same_family 边）
    5) 元素性质物理量化   -> 06_PoC/etl/neo4j/phase16_trends_delta.json
                              （12 个 PQ:el:<prop> 物理量节点 + 1089 条
                                Element-[has_quantity]-> 边）
    6) Phase 7 广度切片补齐 -> 06_PoC/etl/neo4j/phase17_seed_delta.json
                              （化学平衡切片 41 节点/68 边 + 数论切片 20 节点/45 边；
                                纯新增，无删除，与既有图无 id 冲突）
    7) Phase 8 真实文献子图 -> 06_PoC/etl/neo4j/phase18_seed_delta.json
                              （OpenAlex 数学文献：181 Paper + 176 cites + 178 discusses
                                + 5 part_of + 2 领域概念；纯新增。**依赖步骤 6**
                                —— part_of 的父/子节点来自步骤 6 的数论切片）
    8) NIST CODATA 常量真实化 -> 06_PoC/etl/neo4j/phase18b_codata_delta.json
                              （13 个既有常量补 unit/uncertainty/出处 + 3 项值修正
                                + 新增 10 个跨学科桥常量 + 2 条跨源对齐边。**引用
                                EK:el:* 无关，但 same_as 目标为 curated 常量**）
    9) PubChem 分子校验 + count 修正 -> 06_PoC/etl/neo4j/phase19_pubchem_delta.json
                              （713 个分子补 pubchem_cid + 6 条跨源对齐边
                                + 修正 19 条 composed_of 的 count。**必须排在步骤 3 之后**
                                —— 修正边引用 EK:el:* 规范节点，且云端 Aura 推边主键为
                                (source,type,target) 的 MERGE，故可命中步骤 3 改挂后的边）
   10) 文献层多源交叉校验 -> 06_PoC/etl/neo4j/phase21_literature_delta.json
                              （163 篇 Paper 补 Crossref/DataCite 独立核验字段
                                + 39 条一致性 flag；**纯属性增强，0 新增节点/边**）
   11) ChEMBL 化学·药物层 -> 06_PoC/etl/neo4j/phase22_chembl_delta.json
                              （命中分子补 chembl_id / max_phase / first_approval /
                                ATC / 类药性描述符；**纯属性增强，0 新增节点/边**）

   12) 连通性审计修复     -> 06_PoC/etl/neo4j/phase23_audit_fix_delta.json
                              （82 处符号学科标注纠偏[math.symbol→phys/chem.symbol] +
                                14 条元素/量符号接线边 + 2 条边 + **DETACH DELETE
                                42 个孤儿 EK2:rxn 反应节点**。由
                                `06_PoC/sync_seed_delta.py` 从重跑后的 seed 生成）
   13) 连通性审计收尾     -> 06_PoC/etl/neo4j/phase24_audit_fix_delta.json
                              （MC:fo:power_rule-[same_as]->MX:math:power_rule 接回
                                3 节点孤岛 + RT:pq:light_speed-[has_unit]->UN:mps +
                                **DETACH DELETE RT:un:c**（把光速常量误标成 Unit））
   14) 标签规范化         -> 06_PoC/etl/neo4j/phase24_label_fix_delta.json
                              （9 个节点标签收敛到受控词表：Physical_quantity→
                                PhysicalQuantity ×7、ElementEntity→Molecule、
                                PhysicsEntity→Formula。由
                                `03_知识层/normalize_labels.py` 生成。**注意**：
                                推送器以 (source,type,target) 为边主键，标签更新走
                                节点 SET，不受边主键影响）

推送**之后**还有两个收尾步（共 **16 步**）：

   15) 边对账（**清**）  -> 06_PoC/reconcile_aura_edges.py
                            （以本地 normalized.json 为准，删云端独有的历史残留三元组、
                              删同三元组 kind 不一致的变体、补本地独有边。**推送只能加，
                              历史上被替换掉的旧方案残留不会被自动清理** —— 2026-10-09
                              实测云端多 155 个唯一三元组 + 39 条 kind 变体。
                              用 --skip-reconcile 跳过）
   16) 反向导出（**快照**）-> 09_科研扩展/9_inference/export_aura.py
                            （产出 viz 快照，默认 06_PoC/graph_data_aura.json。
                              用 --skip-export 跳过）

**顺序不可颠倒**，原因见每步注释。全部脚本幂等，可安全重跑。

用法：
    python 09_科研扩展/9_inference/push_pending.py            # 打印计划（不连库）
    python 09_科研扩展/9_inference/push_pending.py --execute  # 实际推送
    python 09_科研扩展/9_inference/push_pending.py --execute --skip-export
    python 09_科研扩展/9_inference/push_pending.py --execute --skip-reconcile
"""
import os
import sys
import json
import argparse
import subprocess

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PY = os.environ.get("STTP_PYTHON") or sys.executable

NEO4J_DIR = os.path.join(ROOT, "06_PoC", "etl", "neo4j")
ELEMENT_FIX = os.path.join(ROOT, "11_真实数据", "push_element_fix.py")
TYPED_DELTA = os.path.join(HERE, "phase13_typed_edges.json")
MERGE_DELTA = os.path.join(NEO4J_DIR, "phase15_elementmerge_delta.json")
PERIODIC_DELTA = os.path.join(NEO4J_DIR, "phase16_periodic_delta.json")
TRENDS_DELTA = os.path.join(NEO4J_DIR, "phase16_trends_delta.json")
SEED_DELTA = os.path.join(NEO4J_DIR, "phase17_seed_delta.json")
PAPER_DELTA = os.path.join(NEO4J_DIR, "phase18_seed_delta.json")
CODATA_DELTA = os.path.join(NEO4J_DIR, "phase18b_codata_delta.json")
PUBCHEM_DELTA = os.path.join(NEO4J_DIR, "phase19_pubchem_delta.json")
LIT_DELTA = os.path.join(NEO4J_DIR, "phase21_literature_delta.json")
CHEMBL_DELTA = os.path.join(NEO4J_DIR, "phase22_chembl_delta.json")
AUDIT_FIX = os.path.join(NEO4J_DIR, "phase23_audit_fix_delta.json")
AUDIT_FIX2 = os.path.join(NEO4J_DIR, "phase24_audit_fix_delta.json")
LABEL_FIX = os.path.join(NEO4J_DIR, "phase24_label_fix_delta.json")
# 通用 delta 推送器（upsert 节点 / DETACH DELETE / MERGE 边 / DELETE 边）
PUSH_DELTA = os.path.join(ROOT, "11_真实数据", "push_element_merge.py")
LOADER = os.path.join(ROOT, "06_PoC", "robust_aura_loader.py")
RECONCILE = os.path.join(ROOT, "06_PoC", "reconcile_aura_edges.py")
EXPORT = os.path.join(HERE, "export_aura.py")


def run(cmd, label):
    print("\n" + "=" * 72)
    print("[RUN] %s" % label)
    print("      " + " ".join(cmd))
    print("=" * 72)
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print("[ERR] %s 失败（exit=%s），中止后续步骤。" % (label, r.returncode))
        sys.exit(r.returncode)


def load_json(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="实际推送（默认只打印计划）")
    ap.add_argument("--batch", type=int, default=500, help="边分批大小（Aura 免费实例建议 <=1000）")
    ap.add_argument("--skip-reconcile", action="store_true",
                    help="推送后不做云端/本地边对账（默认会做，使云端严格==本地）")
    ap.add_argument("--skip-export", action="store_true", help="推送后不从 Aura 反向导出 viz 快照")
    a = ap.parse_args()

    typed = load_json(TYPED_DELTA)
    n_typed = len(typed.get("edges", []))
    n_ver = sum(1 for e in typed.get("edges", []) if (e.get("props") or {}).get("verified"))

    fix = load_json(os.path.join(NEO4J_DIR, "phase13_elementfix_delta.json"))
    merge = load_json(MERGE_DELTA)
    periodic = load_json(PERIODIC_DELTA)
    trends = load_json(TRENDS_DELTA)
    seed = load_json(SEED_DELTA)
    paper = load_json(PAPER_DELTA)
    codata = load_json(CODATA_DELTA)
    pubchem = load_json(PUBCHEM_DELTA)
    lit = load_json(LIT_DELTA)
    chembl = load_json(CHEMBL_DELTA)
    audit = load_json(AUDIT_FIX)
    audit2 = load_json(AUDIT_FIX2)
    labelfix = load_json(LABEL_FIX)

    print("待推送离线增量（自 2026-10-08 离线起累计）")
    print("-" * 74)
    print("  步骤 1  元素层修复        节点 %-4d 边 %-4d 删边 %d"
          % (len(fix.get("nodes", [])), len(fix.get("edges", [])), len(fix.get("delete_edges", []))))
    print("  步骤 2  GNN 带类型边      边 %d（VERIFIED %d）" % (n_typed, n_ver))
    print("  步骤 3  元素节点去重      规范节点 %-3d 删除节点 %-3d 改挂边 %d"
          % (len(merge.get("nodes", [])), len(merge.get("delete_nodes", [])),
             len(merge.get("edges", []))))
    print("  步骤 4  周期表位置修复    节点 %-4d 删边 %d"
          % (len(periodic.get("nodes", [])), len(periodic.get("delete_edges", []))))
    print("  步骤 5  元素性质物理量化  节点 %-4d 边 %d"
          % (len(trends.get("nodes", [])), len(trends.get("edges", []))))
    print("  步骤 6  Phase 7 广度切片  节点 %-4d 边 %d（化学平衡 + 数论，纯新增）"
          % (len(seed.get("nodes", [])), len(seed.get("edges", []))))
    print("  步骤 7  Phase 8 文献子图  节点 %-4d 边 %d（OpenAlex Paper + 引用网，纯新增）"
          % (len(paper.get("nodes", [])), len(paper.get("edges", []))))
    print("  步骤 8  CODATA 常量真实化 节点 %-4d 边 %d（补 unit/uncertainty + 值修正 + 新增 10 常量）"
          % (len(codata.get("nodes", [])), len(codata.get("edges", []))))
    print("  步骤 9  PubChem 分子校验  节点 %-4d 边 %d（补 pubchem_cid + 修正 19 条 count）"
          % (len(pubchem.get("nodes", [])), len(pubchem.get("edges", []))))
    print("  步骤 10 文献层多源校验   节点 %-4d 边 %d（Crossref/DataCite 独立核验 + flag）"
          % (len(lit.get("nodes", [])), len(lit.get("edges", []))))
    print("  步骤 11 ChEMBL 药物层     节点 %-4d 边 %d（chembl_id / max_phase / ATC / 类药性）"
          % (len(chembl.get("nodes", [])), len(chembl.get("edges", []))))
    print("  步骤 12 连通性审计修复   节点改 %-4d 边 %-4d 删节点 %d（学科标注纠偏 + 接线边 + 清孤儿）"
          % (len(audit.get("nodes", [])), len(audit.get("edges", [])), len(audit.get("delete_nodes", []))))
    print("  步骤 13 连通性审计收尾   边 %-4d 删节点 %d（接回 MX:math 孤岛 + 删误标单位）"
          % (len(audit2.get("edges", [])), len(audit2.get("delete_nodes", []))))
    print("  步骤 14 标签规范化       节点 %-4d（legacy 标签 → 受控词表）"
          % len(labelfix.get("nodes", [])))
    print("  步骤 15 云端/本地边对账  %s（删残留/kind 变体 + 补本地独有边）"
          % os.path.relpath(RECONCILE, ROOT))
    print("  步骤 16 反向导出 viz 快照 %s（默认 graph_data_aura.json）"
          % os.path.relpath(EXPORT, ROOT))
    print("-" * 74)
    print("  顺序理由 1  步骤 2 的带类型边引用步骤 1 创建的节点（EK2:el:* 等）。")
    print("  顺序理由 2  步骤 3 会 DETACH DELETE 步骤 1 创建的 EK2:el:* 别名节点，")
    print("              并把步骤 2 落在别名上的边改挂到 EK:el:*；必须在 1、2 之后。")
    print("  顺序理由 3  步骤 4 写入的 period/group/block/series 落在 EK:el:* 规范节点上，")
    print("              步骤 5 的 has_quantity 边同样引用 EK:el:*；均需在 3 之后。")
    print("  顺序理由 4  步骤 7 的 part_of 边引用步骤 6 建立的概念节点（NT:mc:*），")
    print("              故步骤 7 必须在 6 之后。")
    print("  顺序理由 5  步骤 9 修正的 composed_of 边以 EK:el:* 为端点，需在步骤 3（元素去重）")
    print("              之后；Aura 推边主键是 (source,type,target) 的 MERGE，故能命中步骤 3")
    print("              改挂后的既有边（边被修正 count，其余元数据保留）。")
    print("  注意 1      推送完成后务必执行步骤 15/16（对账 + 反向导出）；否则本地快照与 Aura 分叉：")
    print("              (a) 历史残留只增不减，云端会慢慢变成「本地 ∪ 历代废弃物」；")
    print("              (b) 下一次 export 会读到分叉后的云端数据。")
    print("  注意 2      Aura 免费实例连接池有限，边分批 %d/批；失败自动重连重试。" % a.batch)
    print("  注意 3      步骤 6–11 均与步骤 1–5 无强依赖（除步骤 9 需在 3 之后），位置可调；")
    print("              但步骤 7 必须在步骤 6 之后（part_of 引用 NT:mc:*）。")
    print("              步骤 10/11 为**纯属性增强**（0 新增节点/边），位置任意，放最后最安全。")
    print("  注意 4      步骤 12 的判定基准是本地 06_PoC/etl/normalized.json —— 请确保所有 delta")
    print("              都已先本地 apply，否则对账会把「尚未本地化的新边」当成残留删掉。")

    if not a.execute:
        print("\n[DRY-RUN] 未连接数据库。加 --execute 实际推送。")
        return

    if not os.environ.get("NEO4J_PASSWORD"):
        print("[ERR] 未设置 NEO4J_PASSWORD，请先 source env.sh 或 bash sttp.sh push ...",
              file=sys.stderr)
        sys.exit(2)

    run([PY, ELEMENT_FIX], "步骤 1 · 元素层修复")
    run([PY, LOADER, "--input", TYPED_DELTA, "--batch", str(a.batch)], "步骤 2 · GNN 带类型边")
    run([PY, PUSH_DELTA, "--delta", MERGE_DELTA, "--batch", str(a.batch)], "步骤 3 · 元素节点规范化去重")
    run([PY, PUSH_DELTA, "--delta", PERIODIC_DELTA, "--batch", str(a.batch)], "步骤 4 · 元素周期表位置修复")
    run([PY, PUSH_DELTA, "--delta", TRENDS_DELTA, "--batch", str(a.batch)], "步骤 5 · 元素性质物理量化")
    run([PY, PUSH_DELTA, "--delta", SEED_DELTA, "--batch", str(a.batch)], "步骤 6 · Phase 7 广度切片补齐")
    run([PY, PUSH_DELTA, "--delta", PAPER_DELTA, "--batch", str(a.batch)], "步骤 7 · Phase 8 真实文献子图")
    run([PY, PUSH_DELTA, "--delta", CODATA_DELTA, "--batch", str(a.batch)], "步骤 8 · NIST CODATA 常量真实化")
    run([PY, PUSH_DELTA, "--delta", PUBCHEM_DELTA, "--batch", str(a.batch)], "步骤 9 · PubChem 分子校验 + count 修正")
    run([PY, PUSH_DELTA, "--delta", LIT_DELTA, "--batch", str(a.batch)], "步骤 10 · 文献层多源交叉校验（Crossref/DataCite）")
    run([PY, PUSH_DELTA, "--delta", CHEMBL_DELTA, "--batch", str(a.batch)], "步骤 11 · ChEMBL 化学·药物层")
    run([PY, PUSH_DELTA, "--delta", AUDIT_FIX, "--batch", str(a.batch)],
        "步骤 12 · 连通性审计修复（学科标注 + 接线边 + 清孤儿）")
    run([PY, PUSH_DELTA, "--delta", AUDIT_FIX2, "--batch", str(a.batch)],
        "步骤 13 · 连通性审计收尾（孤岛接回 + 删误标节点）")
    run([PY, PUSH_DELTA, "--delta", LABEL_FIX, "--batch", str(a.batch), "--set-labels"],
        "步骤 14 · 标签规范化（受控词表收敛；setLabels 整体替换）")
    if not a.skip_reconcile:
        run([PY, RECONCILE, "--batch", str(a.batch)],
            "步骤 15 · 云端/本地边对账（清理到严格一致）")
    if not a.skip_export:
        run([PY, EXPORT], "步骤 16 · 从 Aura 反向导出 viz 快照")
    print("\n[DONE] 全部离线增量推送完成。建议再跑：bash sttp.sh check")


if __name__ == "__main__":
    main()
