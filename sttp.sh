#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

# ============================================================================
# STTP 项目一体化入口
#   bash sttp.sh check        连通性 + 图谱数据自检
#   bash sttp.sh stats        打印本地图谱规模与类型分布
#   bash sttp.sh viz          启动可视化服务 (默认 127.0.0.1:8765)
#   bash sttp.sh export       从 Aura 反向导出 viz 快照 (默认 graph_data_aura.json)
#   bash sttp.sh push <delta> 单 delta 健壮推送到 Aura
#   bash sttp.sh pushall [--execute]                离线增量顺序编排（16 步：推送→对账→导出）
#   bash sttp.sh reconcile [--dry-run|--report-only] 云端/本地边对账（默认清理，使云端==本地）
#   bash sttp.sh probe [--only k1,k2] [--rounds N]  真实数据源可达性探测
#   bash sttp.sh bridge [--dry] [--out <p>]         生成跨域桥 delta（Phase 29）
#   bash sttp.sh webbook [--limit N|--only A,B]     抓取 NIST WebBook 热化学数据
#   bash sttp.sh t7 [--legacy-t7]                   北极星 T7 跨域桥达标率（含反向对照）
#   bash sttp.sh recompute [--apply]                生成 Phase 31 复算全覆盖 delta（默认 dry-run）
#   bash sttp.sh evidence [--dry]                   生成 Phase 32 Claim/Evidence 对象化 delta
#   bash sttp.sh indep [--dry]                      生成 Phase 33 T9-i 证据独立攻坚 delta
#   bash sttp.sh alias [--dry]                      生成 Phase 34 T4 跨源名称对齐 delta（ChEBI 同义词）
#   bash sttp.sh synfetch                          拉取 ChEBI OLS4 同义词缓存（Phase 34）
#   bash sttp.sh noise                              独立实现的语义噪声审计器（跨实现交叉核对）
#   bash sttp.sh scopeaudit                         北极星口径自审 + 占位实体审计（Phase 35，只读）
#   bash sttp.sh mnxrecov                           MetaNetX 对 Rhea 反应覆盖率现算（Phase 35，只读）
#   bash sttp.sh reach35                            第二源可达性穷举侦察（Phase 35，只读）
#   bash sttp.sh rescope                            验证语义校正 delta（Phase 36，GPT6 复评 R03/R04/R05；默认 dry-run）
#   bash sttp.sh release                            构建可复现发布包（样例 + 清单 + fail-closed 校验，Phase 37 R06）
#   bash sttp.sh sample [--port N]                  只用**离线样例**起可视化（页面与查询同版本，单命令）
#   bash sttp.sh release-full                       对完整快照跑全量发布校验（--release 门禁 + manifest 现算比对）
#   bash sttp.sh eval [--split test]                科学任务评测集：构建冻结任务集 + 规则基线 + 非真空自检（Phase 38 R11）
#   bash sttp.sh gnneval [--gnn-seeds 1,2]           推理增量评测协议 v2：P1/P2 + 基线对照 + 种子方差（Phase 39 R11续）
#   bash sttp.sh gatecheck                          门禁「非真空」自检（注入假边，断言真会红）
#   bash sttp.sh dimaudit                           量纲审计：独立 SI 基表对照 + 矛盾方程检测 + 判据自检（Phase 40 R09）
#   bash sttp.sh lineage                            证据血缘审计：来源登记 + 变更定位 + 判据自检（Phase 41 R10）
#   bash sttp.sh evdeps [--report|--check|--emit-registry] A04 验证依赖绑定：参考表/验证器依赖推导 + 状态机自检（Phase 45）
#   bash sttp.sh subgraph [--json OUT]              局部图谱查询自检：真 HTTP 响应体 + V1–V18 正/反例（Phase 43 / 复评 A06）
#   bash sttp.sh frontend [--json OUT]              前端（局部图谱浏览）自检：静态结构/无障碍/模块图/跨层一致性/Node 逻辑/HTTP（Phase 43 步骤 B）
#   bash sttp.sh verify       跑仓库自带 verify_deploy.py
# 说明：export / pushall / reconcile / probe 的额外参数原样透传。
# ============================================================================
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# ---- 加载 .env ----
if [ -f "$ROOT/.env" ]; then
  while IFS= read -r _line || [ -n "$_line" ]; do
    case "$_line" in \#*|'') continue ;; esac
    case "$_line" in *=*) ;; *) continue ;; esac
    _k="${_line%%=*}"; _v="${_line#*=}"
    _k="$(printf '%s' "$_k" | tr -d '[:space:]\r')"
    _v="$(printf '%s' "$_v" | tr -d '\r')"
    case "$_k" in
      NEO4J_URI|NEO4J_USER|NEO4J_PASSWORD|NEO4J_DATABASE|GRAPH_DATA_FILE|GRAPH_SOURCE_FILE|VIZ_PORT|STTP_PYTHON)
        export "$_k=$_v" ;;
    esac
  done < "$ROOT/.env"
fi

PY="${STTP_PYTHON:-python}"
GRAPH="${GRAPH_DATA_FILE:-06_PoC/graph_data_phase22.json}"
PORT="${VIZ_PORT:-8765}"

# ---- 前置守卫（Phase 43 步骤 B · D25；铁律 #53/#63）--------------------------------
# 部分命令的断言以**权威图口径**（9657 节点 / 48712 边）为前提，而权威输入被 `.gitignore`
# 刻意排除、**不入库**。此前这些命令在**干净检出**（clone）里会抛**裸 `FileNotFoundError`**
# （RC=1）——评审者会误读成「代码坏了」。现统一改为：**明确原因 + 不适用清单 + RC=2**
# （与 `subgraph`/`release` 的既有口径一致）。
# 恢复方式：① 本地 `bash sttp.sh export` 从 Aura 反向导出；② 或从 Release 资产下载同名快照。
require_authority() {   # 用法: require_authority <命令名> <相对路径>...
  local cmd="$1"; shift
  local _missing=() _p
  for _p in "$@"; do [ -e "$ROOT/$_p" ] || _missing+=("$_p"); done
  [ ${#_missing[@]} -eq 0 ] && return 0
  {
    echo ""
    echo "[不可用] 命令 '$cmd' 缺少**权威输入**（以下文件被 .gitignore 刻意排除、不入库）："
    for _p in "${_missing[@]}"; do echo "           - $_p"; done
    echo "  原因：本命令的断言以**权威图口径**（9657 节点 / 48712 边）为前提 ⇒ 干净检出里"
    echo "        **结构性不可用**，这是**已知边界而非代码缺陷**（见 PROJECT_DEVELOPMENT_GUIDE.md §0.3.1）。"
    echo "  恢复：① 本地 bash sttp.sh export（从 Aura 反向导出权威图）"
    echo "        ② 或从 Release 资产下载同名快照，放到上述路径。"
    echo "  干净检出里**可运行**的替代验证："
    echo "        - bash sttp.sh frontend        （可运行；缺图处记 SKIP 而**不假通过**）"
    echo "        - bash sttp.sh check           （环境自检）"
    echo "        - $PY 06_PoC/release/verify_release.py --release   （28/0/0，RC=0）"
    echo ""
  } >&2
  exit 2
}
NORM_GRAPH="06_PoC/etl/normalized.json"

case "${1:-help}" in
  check)
    echo "== Python =="
    "$PY" -c "import sys;print(sys.version)"
    "$PY" -c "
import importlib
need=['neo4j','pint','pandas','sympy','numpy','networkx','rdflib','torch']
bad=[]
for m in need:
    try: importlib.import_module(m)
    except Exception: bad.append(m)
print('依赖缺失:', bad if bad else '无（全部就绪）')
"
    echo "== Aura 连通性 =="
    "$PY" - <<'EOF'
import os
from neo4j import GraphDatabase
uri=os.environ.get("NEO4J_URI"); usr=os.environ.get("NEO4J_USER")
pw=os.environ.get("NEO4J_PASSWORD"); db=os.environ.get("NEO4J_DATABASE")
print("URI =", uri)
try:
    d=GraphDatabase.driver(uri, auth=(usr,pw))
    with d.session(database=db) as s:
        n=s.run("MATCH (n) RETURN count(n) AS n").single()["n"]
        e=s.run("MATCH ()-[r]->() RETURN count(r) AS e").single()["e"]
    d.close()
    print(f"Aura 在线: {n} 节点 / {e} 边")
except Exception as ex:
    print("Aura 连接失败:", type(ex).__name__, ex)
EOF
    echo "== 本地图谱数据 =="
    if [ -f "$GRAPH" ]; then
      "$PY" - "$GRAPH" <<'EOF'
import json,sys
from collections import Counter
p=sys.argv[1]
g=json.load(open(p,encoding="utf-8"))
print(f"{p}: {len(g['nodes'])} 节点 / {len(g['edges'])} 边")
print("节点类型:", Counter(n.get('type') for n in g['nodes']).most_common(8))
EOF
    else
      echo "缺失: $GRAPH"
    fi
    ;;
  stats)
    require_authority stats "$GRAPH"
    "$PY" - "$GRAPH" <<'EOF'
import json,sys
from collections import Counter
g=json.load(open(sys.argv[1],encoding="utf-8"))
print(f"节点 {len(g['nodes'])} / 边 {len(g['edges'])}")
print("节点类型:", Counter(n.get('type') for n in g['nodes']).most_common())
print("边类型  :", Counter(e.get('type') for e in g['edges']).most_common())
EOF
    ;;
  viz)
    echo "启动可视化: http://127.0.0.1:$PORT/  (数据源 $GRAPH)"
    GRAPH_DATA_FILE="$GRAPH" VIZ_PORT="$PORT" "$PY" 06_PoC/viz_server.py
    ;;
  export)
    shift
    "$PY" 09_科研扩展/9_inference/export_aura.py "$@"
    ;;
  push)
    "$PY" 06_PoC/robust_aura_loader.py --input "${2:?用法: bash sttp.sh push <delta.json>}"
    ;;
  pushall)
    shift
    "$PY" 09_科研扩展/9_inference/push_pending.py "$@"
    ;;
  reconcile)
    shift
    "$PY" 06_PoC/reconcile_aura_edges.py "$@"
    ;;
  probe)
    shift
    "$PY" 11_真实数据/probe_sources.py "$@"
    ;;
  bridge)
    # 生成跨域桥 delta（Phase 29）：默认 dry-run，加 --apply 交给 apply_delta 落盘
    shift
    "$PY" 11_真实数据/phase29_bridge_delta.py "$@"
    ;;
  webbook)
    # 抓取 NIST Chemistry WebBook 气相热化学表（增量缓存）
    shift
    "$PY" 11_真实数据/webbook_ingest.py "$@"
    ;;
  t7)
    # 北极星 T7（跨域桥）达标率；--legacy-t7 打印旧口径反向对照
    shift
    require_authority t7 "$NORM_GRAPH"
    "$PY" 06_PoC/task_trust_audit.py "$@"
    ;;
  dedup)
    # Phase 30 去伪存真：确定性反驳语义噪声边 + 重建单位/数学桥（默认 dry-run，--apply 交 apply_delta）
    shift
    "$PY" 11_真实数据/phase30_dedup_delta.py "$@"
    ;;
  recompute)
    # Phase 31 复算维度全覆盖：确定性复算从「仅模型产物」推广到所有来源的语义边
    # （默认 dry-run；加 --apply 交给 apply_delta 落盘）
    shift
    "$PY" 11_真实数据/phase31_recompute_delta.py "$@"
    ;;
  evidence)
    # Phase 32 Claim/Evidence 完整对象化：把已算出的证据明细持久化为一等对象
    # （非破坏性，只加字段 claim/verification_evidence/evidence_at；默认 dry-run）
    shift
    "$PY" 11_真实数据/phase32_evidence_delta.py "$@"
    ;;
  indep)
    # Phase 33 T9-i 证据独立攻坚：ChEBI×Rhea 跨源侧别校验 + CODATA 定义式数值复算，
    # 把 T4/T6 的 source_asserted 升为**独立证据**（非破坏性，只更新判级与证据对象；默认 dry-run）
    shift
    "$PY" 11_真实数据/phase33_indep_delta.py "$@"
    ;;
  alias)
    # Phase 34 T4 残差跨源名称对齐：ChEBI 本体同义词（OLS4）× Rhea 方程侧别
    # （非破坏性，只更新判级与证据对象；默认 dry-run）
    shift
    "$PY" 11_真实数据/phase34_alias_delta.py "$@"
    ;;
  synfetch)
    # Phase 34 数据获取：经代理从 ChEBI OLS4 拉取残差参与物同义词缓存（幂等 / 可重试）
    shift
    "$PY" 11_真实数据/chebi_synonym_fetch.py "$@"
    ;;
  noise)
    # 独立实现的语义噪声审计器（只读，用于与 verification_model 交叉核对）
    shift
    require_authority noise "$NORM_GRAPH"
    "$PY" 06_PoC/semantic_noise_audit.py "$@"
    ;;
  scopeaudit)
    # Phase 35 北极星口径自审：分母内/外占比 + 反向对照 + 占位实体审计（只读）
    shift
    require_authority scopeaudit "$NORM_GRAPH"
    "$PY" 06_PoC/_recon_phase35_scope.py "$@"
    ;;
  mnxrecov)
    # Phase 35 MetaNetX 对 Rhea 反应的覆盖率现算（只读；铁律 #44 数字由仪器现算）
    shift
    "$PY" 06_PoC/_recon_phase35_mnx_recov.py "$@"
    ;;
  reach35)
    # Phase 35 第二源可达性穷举侦察（只读 HTTP 探测；铁律 #34 负结果优先）
    shift
    "$PY" 06_PoC/_recon_phase35_reachability.py "$@"
    ;;
  rescope)
    # Phase 36 验证语义校正（GPT6 复评 R03/R04/R05）：共享词典重定 + 算子桥正名
    # + 标签直通移除；全量重算（含预期降级）生成 delta（默认 dry-run）
    shift
    "$PY" 11_真实数据/phase36_rescope_delta.py "$@"
    ;;
  gatecheck)
    # 门禁「非真空」自检：注入已知假边，断言不变量真的会红（铁律 #14）
    shift
    require_authority gatecheck "$NORM_GRAPH"
    "$PY" 06_PoC/_gate_selfcheck_phase31.py "$@"
    ;;
  release)
    # Phase 37（R06）：可复现发布包 = 离线样例 → 发布清单 → fail-closed 校验。
    # 全流程幂等；任一步失败即非零退出（验证样例可与页面/查询同版本独立运行）。
    # 额外参数（如 --node-cap N）原样透传给采样器。
    shift
    set -e
    echo "== [0/4] 许可判据非真空自检（A03：合成图注入 ⇒ 白名单必须真剔除）=="
    "$PY" 06_PoC/release/make_sample.py --selfcheck
    echo "== [1/4] 构建离线样例（public_demo：只打包可再分发内容）=="
    "$PY" 06_PoC/release/make_sample.py "$@"
    echo "== [2/4] 生成发布清单 =="
    "$PY" 06_PoC/release/make_manifest.py
    echo "== [3/4] 发布校验（fail-closed）=="
    "$PY" 06_PoC/release/verify_release.py --release
    ;;
  sample)
    # 只用**入库的离线样例**起可视化：页面(GRAPH_DATA_FILE) 与查询(GRAPH_SOURCE_FILE)
    # 指向同一份切片的两种投影 —— 单命令即可离线运行，无需 48 MB 的 normalized.json。
    shift
    _port="$PORT"
    if [ "${1:-}" = "--port" ] && [ -n "${2:-}" ]; then _port="$2"; shift 2; fi
    _sn="06_PoC/release/sample/sample_normalized.json"
    _sg="06_PoC/release/sample/sample_graph.json"
    if [ ! -f "$_sg" ] || [ ! -f "$_sn" ]; then
      echo "缺少离线样例，请先运行：bash sttp.sh release"
      exit 2
    fi
    echo "启动离线样例可视化: http://127.0.0.1:$_port/"
    echo "  页面数据: $_sg"
    echo "  查询数据: $_sn"
    GRAPH_DATA_FILE="$_sg" GRAPH_SOURCE_FILE="$_sn" VIZ_PORT="$_port" "$PY" 06_PoC/viz_server.py
    ;;
  release-full)
    # 对完整快照（不入库）跑全量校验：manifest 现算比对（含规范图哈希）+ 主图 fail-closed 门禁。
    # ★ 铁律 #62：发布链必须 fail-closed 且**逐项传递退出码**（任一步非零 ⇒ 整体非零）；
    #   不得让后一步的通过掩盖前一步的失败。
    shift
    echo "== [1/2] 发布校验（--full --release；含规范图 sha256 与 manifest 现算比对）=="
    "$PY" 06_PoC/release/verify_release.py --full --release "$@" || exit $?
    echo "== [2/2] 主图 fail-closed 门禁 =="
    "$PY" 06_PoC/frozen_gate.py --release || exit $?
    ;;
  eval)
    # Phase 38（R11）：科学任务评测集 —— 构建冻结任务集（幂等）→ 规则基线 → 非真空自检。
    # 任一步失败即非零退出；额外参数（如 --split test）透传给运行器。
    shift
    set -e
    require_authority eval "$NORM_GRAPH"
    echo "== [1/3] 构建/校验冻结任务集 =="
    "$PY" 12_评测/build_tasks.py
    echo "== [2/3] 规则基线评测 =="
    "$PY" 12_评测/eval_runner.py --json 12_评测/eval_report.json "$@"
    echo "== [3/3] 非真空自检（判分器必须会红）=="
    "$PY" 12_评测/eval_runner.py --selfcheck
    ;;
  gnneval)
    # Phase 39（R11 续）：推理增量评测协议 v2 —— P1 关系留出补全 / P2 实体对发现。
    # 单线程确定性训练；额外参数透传（如 --gnn-seeds 1,2）。
    shift
    set -e
    require_authority gnneval "$NORM_GRAPH"
    echo "== [1/2] 推理增量评测（全量 filtered MRR + 4 类基线对照）=="
    "$PY" 12_评测/gnn_eval_v2.py --epochs 120 --cap-test 100 "$@"
    echo "== [2/2] 非真空自检（判据必须会红）=="
    "$PY" 12_评测/gnn_eval_v2.py --quick --selfcheck
    ;;
  dimaudit)
    # Phase 40（R09）：量纲审计 —— 独立 SI 基表对照 + 矛盾方程检测 + 判据非真空自检。
    # 任一步发现「非约定性分歧」或「矛盾方程」即非零退出。
    # ★ 铁律 #62：逐项传递退出码（否则第 1/2 步的分歧会被第 3 步的通过掩盖）。
    shift
    require_authority dimaudit "11_真实数据/physicsbabel_circuits.parquet"
    echo "== [1/3] 反解表 vs 独立 SI 基表 =="
    "$PY" 11_真实数据/dimension_table.py --compare || exit $?
    echo "== [2/3] 矛盾方程检测（一般线性一致性）=="
    "$PY" 11_真实数据/dimension_consistency.py --json 11_真实数据/dim_consistency_report.json "$@" || exit $?
    echo "== [3/3] 判据非真空自检 =="
    "$PY" 11_真实数据/dimension_consistency.py --selfcheck || exit $?
    ;;

  lineage)
    # Phase 41（R10）：证据血缘/版本/失效审计 —— 来源登记表 + 变更定位 + 判据非真空自检。
    # 若某来源内容哈希与证据记录不一致（来源已变更）则非零退出。
    # ★ 铁律 #62：逐项传递退出码。
    shift
    require_authority lineage "$NORM_GRAPH"
    echo "== [1/3] 来源登记表（版本 + locator + 内容哈希 + 血缘）=="
    "$PY" 11_真实数据/source_registry.py --check || exit $?
    echo "== [2/3] 来源变更 → 受影响断言（含血缘传递）=="
    "$PY" 11_真实数据/lineage_invalidation.py --json "$@" || exit $?
    echo "== [3/3] 失效机制判据非真空自检 =="
    "$PY" 11_真实数据/lineage_invalidation.py --selfcheck || exit $?
    ;;
  evdeps)
    # Phase 45（复评 A04）：验证依赖绑定 —— 参考表/验证器依赖推导 + 证据生命周期状态机。
    # ★ 不依赖权威图的部分（参考表登记 / AST 依赖 / 状态机 / 五类变异定位）在干净检出里**可运行**；
    #   只有 `--report`（需 normalized.json）走 require_authority 守卫。
    shift
    if [ "${1:-}" = "--check" ]; then
      "$PY" 11_真实数据/evidence_deps.py --check || exit $?
    elif [ "${1:-}" = "--emit-registry" ]; then
      "$PY" 11_真实数据/evidence_deps.py --emit-registry || exit $?
    else
      require_authority evdeps "$NORM_GRAPH"
      echo "== 参考表登记 =="
      "$PY" 11_真实数据/evidence_deps.py --emit-registry || exit $?
      echo "== 依赖推导报告 =="
      "$PY" 11_真实数据/evidence_deps.py "$@" || exit $?
      echo "== 判据自检（七段 · 构造性非真空）=="
      "$PY" 11_真实数据/evidence_deps.py --check || exit $?
    fi
    ;;
  verify)
    "$PY" 08_部署包/neo4j/verify_deploy.py
    ;;
  subgraph)
    # Phase 43 / 复评 A06：局部图谱查询（/api/search · /api/subgraph · /api/path · /api/overview）
    # 真 HTTP 响应体断言 + 契约 JSON Schema 校验 + V1–V18 正/反例（反例必须变红）。
    # Neo4j 不可达 ⇒ 记 SKIP（不计入通过数，铁律 #20/#37）。
    shift
    set -e
    "$PY" 06_PoC/tests/phase43_subgraph_selfcheck.py "$@"
    ;;
  frontend)
    # Phase 43 步骤 B：前端「局部图谱查询浏览」自检。
    # 静态结构 + 无障碍 + 模块图完整性 + **跨层一致性**（前端常量 ↔ 后端契约）
    # + Node 纯逻辑断言（缺 node ⇒ SKIP，不计通过）+ HTTP MIME 层。
    # ★ 真实浏览器渲染**未实测**（零第三方依赖）⇒ 脚本内如实记 SKIP。
    shift
    set -e
    "$PY" 06_PoC/tests/phase43_frontend_selfcheck.py "$@"
    ;;
  *)
    awk 'NR>=3 && /^# =====/{exit} NR>=3{sub(/^# ?/,""); print}' "$0"
    ;;
esac
