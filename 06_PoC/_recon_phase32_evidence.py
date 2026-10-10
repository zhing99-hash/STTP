# -*- coding: utf-8 -*-
"""第 22 轮侦察 ①：Claim/Evidence 现状测绘（只读）。

回答四个问题：
  Q1 边属性现状：verification_* / source / kind 这些字段在 48712 条边上的分布与形态
  Q2 evidence[] 覆盖面：多少条边带证据？结构长什么样？是否只落跨域桥边？
  Q3 verifier 字段：谁在写？覆盖率？
  Q4 「可溯源可撤销」：撤回是否有留痕通道（*_withdrawn / 台账）
"""
import json, collections, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")


def main():
    d = json.load(open(P, encoding="utf-8"))
    E = d["edges"]
    N = {n["id"]: n for n in d["nodes"]}
    print("规模: %d 节点 / %d 边" % (len(N), len(E)))
    print()

    # ---------- Q1 边属性字段全貌 ----------
    keycnt = collections.Counter()
    nested_shape = collections.Counter()
    for e in E:
        props = e.get("props") or {}
        for k, v in props.items():
            keycnt[k] += 1
            if isinstance(v, (dict, list)):
                nested_shape["%s:%s" % (k, type(v).__name__)] += 1
    print("== Q1 边 props 字段覆盖（共 %d 条边）==" % len(E))
    for k, v in keycnt.most_common(40):
        print("   %-32s %6d  (%.1f%%)" % (k, v, 100.0 * v / len(E)))
    print()
    print("   嵌套/容器型字段:")
    for k, v in nested_shape.most_common(20):
        print("     %-40s %d" % (k, v))
    print()

    # ---------- Q2 evidence 覆盖面 ----------
    print("== Q2 evidence[] 覆盖面 ==")
    has_ev = [e for e in E if (e.get("props") or {}).get("evidence")]
    print("   带 evidence 字段的边: %d / %d (%.2f%%)" % (len(has_ev), len(E), 100.0 * len(has_ev) / len(E)))
    # 按 type 分布
    ct = collections.Counter(e.get("type") for e in has_ev)
    print("   按 type:", dict(ct.most_common(15)))
    # 按 level 分布
    cl = collections.Counter((e.get("props") or {}).get("verification_level") for e in has_ev)
    print("   按 level:", dict(cl))
    # 形态样例
    for e in has_ev[:4]:
        p = e["props"]
        ev = p["evidence"]
        print("   --- %s --%s--> %s" % (e["source"], e.get("type"), e["target"]))
        print("       evidence type=%s len=%s" % (type(ev).__name__, len(ev) if isinstance(ev, (list, dict, str)) else "-"))
        print("       sample:", json.dumps(ev, ensure_ascii=False)[:400])
    print()

    # 也检查顶层（非 props）是否有 evidence
    top_ev = [e for e in E if e.get("evidence")]
    print("   （顶层 evidence 字段）:", len(top_ev))
    print()

    # ---------- Q3 verifier ----------
    print("== Q3 verifier 字段 ==")
    vcnt = collections.Counter()
    for e in E:
        props = e.get("props") or {}
        v = props.get("verifier") if "verifier" in props else e.get("verifier")
        vcnt[v if v is not None else "<absent>"] += 1
    for k, v in vcnt.most_common(20):
        print("   %-40s %d" % (k, v))
    print()

    # ---------- Q4 撤回留痕通道 ----------
    print("== Q4 撤回留痕通道 ==")
    wd = collections.Counter()
    for e in E:
        props = e.get("props") or {}
        for k in props:
            if "withdraw" in k or "revok" in k or "retract" in k:
                wd[k] += 1
    print("   带 withdrawn/revoked 字样的边字段:", dict(wd) if wd else "无")
    # 查目录下 phase*_withdrawn.json
    etl = os.path.join(ROOT, "06_PoC", "etl", "neo4j")
    if os.path.isdir(etl):
        files = [f for f in os.listdir(etl) if "withdraw" in f]
        print("   撤回台账文件:", files)
    print()

    # ---------- 附带：verification_scope 分布（对照 A 段口径）----------
    print("== 附: verification_scope 分布 ==")
    sc = collections.Counter((e.get("props") or {}).get("verification_scope") for e in E)
    for k, v in sc.most_common(40):
        print("   %-42s %d" % (k, v))


if __name__ == "__main__":
    main()
