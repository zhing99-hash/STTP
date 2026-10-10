#!/usr/bin/env bash
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
#   bash sttp.sh noise                              独立实现的语义噪声审计器（跨实现交叉核对）
#   bash sttp.sh gatecheck                          门禁「非真空」自检（注入假边，断言真会红）
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
      NEO4J_URI|NEO4J_USER|NEO4J_PASSWORD|NEO4J_DATABASE|GRAPH_DATA_FILE|VIZ_PORT|STTP_PYTHON)
        export "$_k=$_v" ;;
    esac
  done < "$ROOT/.env"
fi

PY="${STTP_PYTHON:-python}"
GRAPH="${GRAPH_DATA_FILE:-06_PoC/graph_data_phase22.json}"
PORT="${VIZ_PORT:-8765}"

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
  noise)
    # 独立实现的语义噪声审计器（只读，用于与 verification_model 交叉核对）
    shift
    "$PY" 06_PoC/semantic_noise_audit.py "$@"
    ;;
  gatecheck)
    # 门禁「非真空」自检：注入已知假边，断言不变量真的会红（铁律 #14）
    shift
    "$PY" 06_PoC/_gate_selfcheck_phase31.py "$@"
    ;;
  verify)
    "$PY" 08_部署包/neo4j/verify_deploy.py
    ;;
  *)
    awk 'NR>=3 && /^# =====/{exit} NR>=3{sub(/^# ?/,""); print}' "$0"
    ;;
esac
