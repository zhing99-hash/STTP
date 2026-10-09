# -*- coding: utf-8 -*-
"""Neo4j 属性消毒（DB 边界统一收敛点）。

## 为什么需要它
Neo4j 的属性值**只接受 primitive（str/bool/int/float/null）或同质 primitive 数组**。
本地图谱是 NetworkX，属性想放什么就放什么，**完全不校验**；于是 delta 里会夹带：
  - `alias_sources`  = dict        （元素去重时记录的「别名 -> 来源」映射）
  - `gnn_type_probs` = list[dict]  （GNN 多关系预测的 {type, p} 概率分布）
这类值推到 Aura 会抛 `CypherTypeException` / `Statement.TypeError`。

## 危害形态（2026-10-09 首次全量推送实测）
异常不是「跳过这一条」，而是**整批事务失败**，而推送器对反复失败的批次是
「重试 6 次后放弃该批」——于是静默丢数据：
  - 步骤 2：2 个边批（600 条带类型边）被放弃
  - 步骤 3/4：节点 upsert 批被放弃 → 15 个规范元素属性 + **118 个元素的 period/group 全部没写进云端**
而脚本仍以 exit=0 结束，日志里只有 `[retry]` 行容易淹没 → **典型的「静默降级」**。

## 处置策略
在**推送到 DB 的唯一边界**做消毒：嵌套容器一律 `json.dumps` 成字符串（**无损**，仍可读回解析），
同质 primitive 数组保持数组形态。本地图谱**不动**（NetworkX 侧继续用 dict，语义更自然）。

## 用法
    from neo4j_props import sanitize_props, sanitize_row, report

    rows = [sanitize_row(r) for r in rows]
    print(report())        # 打印本次共消毒了多少个字段
"""
import json

__all__ = ["sanitize_props", "sanitize_row", "sanitize_rows", "report", "reset"]

# 统计：本次进程内被 JSON 化的字段数（按 键名 归类），便于把「消毒」这件事显性化
_STATS = {}


def reset():
    _STATS.clear()


def _is_primitive(v):
    return v is None or isinstance(v, (str, bool, int, float))


def _is_homogeneous_primitive_list(vals):
    if not vals:
        return True
    if not all(isinstance(x, (str, int, float)) for x in vals):
        return False
    return len({type(x) for x in vals}) <= 1      # bool 是 int 子类，混用会被视为异构


def sanitize_props(props, where="?"):
    """返回可安全写入 Neo4j 的 props 副本。"""
    out = {}
    for k, v in (props or {}).items():
        if _is_primitive(v):
            out[k] = v
        elif isinstance(v, dict):
            out[k] = json.dumps(v, ensure_ascii=False, sort_keys=True)
            _STATS[(where, k, "dict")] = _STATS.get((where, k, "dict"), 0) + 1
        elif isinstance(v, (list, tuple)):
            vals = list(v)
            if _is_homogeneous_primitive_list(vals):
                out[k] = vals
            else:
                out[k] = json.dumps(vals, ensure_ascii=False)
                _STATS[(where, k, "list<non-primitive>")] = \
                    _STATS.get((where, k, "list<non-primitive>"), 0) + 1
        else:
            out[k] = str(v)
            _STATS[(where, k, type(v).__name__)] = _STATS.get((where, k, type(v).__name__), 0) + 1
    return out


def sanitize_row(rec, where="?"):
    """对 delta 记录（含 props 字段）做消毒；无 props 时原样返回。"""
    props = rec.get("props")
    if not props:
        return rec
    rec = dict(rec)
    rec["props"] = sanitize_props(props, where=where)
    return rec


def sanitize_rows(rows, where="?"):
    return [sanitize_row(r, where=where) for r in rows]


def report():
    if not _STATS:
        return "  [props] 无需消毒（全部为 primitive / 同质 primitive 数组）"
    lines = ["  [props] ⚠ 检测到非 primitive 属性值，已在 DB 边界 JSON 化（本地图谱不受影响）："]
    for (where, k, kind), c in sorted(_STATS.items(), key=lambda x: -x[1]):
        lines.append("            %-10s %-22s %-20s x%d" % (where, k, kind, c))
    return "\n".join(lines)
