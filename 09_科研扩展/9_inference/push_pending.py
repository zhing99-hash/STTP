# -*- coding: utf-8 -*-
"""**全部离线增量**的顺序推送编排（网络恢复后执行）。

本机自 2026-10-08 起离线，累计五项修复只落在本地，Aura 仍是旧数据：

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

**顺序不可颠倒**，原因见每步注释。全部脚本幂等，可安全重跑。

用法：
    python 09_科研扩展/9_inference/push_pending.py            # 打印计划（不连库）
    python 09_科研扩展/9_inference/push_pending.py --execute  # 实际推送
    python 09_科研扩展/9_inference/push_pending.py --execute --skip-export
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
# 通用 delta 推送器（upsert 节点 / DETACH DELETE / MERGE 边 / DELETE 边）
PUSH_DELTA = os.path.join(ROOT, "11_真实数据", "push_element_merge.py")
LOADER = os.path.join(ROOT, "06_PoC", "robust_aura_loader.py")
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
    ap.add_argument("--skip-export", action="store_true", help="推送后不从 Aura 反向导出权威快照")
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
    print("  步骤 8  反向导出权威快照  %s" % os.path.relpath(EXPORT, ROOT))
    print("-" * 74)
    print("  顺序理由 1  步骤 2 的带类型边引用步骤 1 创建的节点（EK2:el:* 等）。")
    print("  顺序理由 2  步骤 3 会 DETACH DELETE 步骤 1 创建的 EK2:el:* 别名节点，")
    print("              并把步骤 2 落在别名上的边改挂到 EK:el:*；必须在 1、2 之后。")
    print("  顺序理由 3  步骤 4 写入的 period/group/block/series 落在 EK:el:* 规范节点上，")
    print("              步骤 5 的 has_quantity 边同样引用 EK:el:*；均需在 3 之后。")
    print("  顺序理由 4  步骤 7 的 part_of 边引用步骤 6 建立的概念节点（NT:mc:*），")
    print("              故步骤 7 必须在 6 之后。")
    print("  注意 1      推送完成后务必执行最后一步（反向导出）；否则本地快照与 Aura 分叉，")
    print("              下一次 export 会用 Aura 旧数据覆盖本地修复。")
    print("  注意 2      Aura 免费实例连接池有限，边分批 %d/批；失败自动重连重试。" % a.batch)
    print("  注意 3      步骤 6、7 为纯新增，与步骤 1–5 无依赖，彼此需保持 6 在 7 前。")

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
    if not a.skip_export:
        run([PY, EXPORT], "步骤 8 · 从 Aura 反向导出权威快照")
    print("\n[DONE] 全部离线增量推送完成。建议再跑：bash sttp.sh check")


if __name__ == "__main__":
    main()
