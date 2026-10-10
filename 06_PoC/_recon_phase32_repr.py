# -*- coding: utf-8 -*-
"""第 22 轮侦察 ②：repr 串生成链 + evidence/created_at 真实形态（只读）。"""
import json, collections, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")


def main():
    d = json.load(open(P, encoding="utf-8"))
    E = d["edges"]
    print("规模: %d 边" % len(E))
    print()

    # ---- A. evidence 值形态分类 ----
    shapes = collections.Counter()
    repr_like = []
    for e in E:
        ev = (e.get("props") or {}).get("evidence")
        if ev is None:
            shapes["<absent>"] += 1
            continue
        if isinstance(ev, list):
            shapes["list"] += 1
        elif isinstance(ev, dict):
            shapes["dict"] += 1
        elif isinstance(ev, str):
            s = ev.strip()
            if (s.startswith("[") and s.endswith("]")) or \
               (s.startswith("{") and s.endswith("}")) or \
               (s.startswith("(") and s.endswith(")")):
                shapes["REPR-STR"] += 1
                repr_like.append((e, s))
            else:
                shapes["str"] += 1
        else:
            shapes[type(ev).__name__] += 1
    print("== A. evidence 值形态 ==")
    for k, v in shapes.most_common():
        print("   %-10s %d" % (k, v))
    print()
    if repr_like:
        print("   ⚠ repr 串样例（前 10）:")
        for e, s in repr_like[:10]:
            print("      %s --%s--> %s : %s" % (e["source"], e.get("type"), e["target"], s[:110]))
    else:
        print("   ✅ 无 repr 串形态的 evidence")
    print()

    # ---- B. created_at 覆盖与形态 ----
    ca = [e for e in E if (e.get("props") or {}).get("created_at")]
    print("== B. created_at ==")
    print("   覆盖 %d / %d (%.1f%%)" % (len(ca), len(E), 100.0 * len(ca) / len(E)))
    cshape = collections.Counter()
    for e in ca:
        v = (e.get("props") or {}).get("created_at")
        cshape[type(v).__name__] += 1
    print("   形态:", dict(cshape))
    # 谁带 created_at
    csrc = collections.Counter((e.get("props") or {}).get("source") for e in ca)
    print("   按 source:", dict(csrc.most_common(10)))
    # sample
    for e in ca[:3]:
        print("   样例:", json.dumps((e.get("props") or {}).get("created_at"), ensure_ascii=False)[:80])
    print()

    # ---- C. 任意 repr 串残留扫描（全 props，找 str 化的 list/dict）----
    print("== C. 全 props repr 串残留扫描 ==")
    hit = collections.Counter()
    samples = {}
    for e in E:
        for k, v in (e.get("props") or {}).items():
            if isinstance(v, str):
                s = v.strip()
                if len(s) > 3 and ((s[0] == "[" and s[-1] == "]") or (s[0] == "{" and s[-1] == "}")):
                    hit[k] += 1
                    samples.setdefault(k, (e["source"], e.get("type"), e["target"], s[:90]))
    if hit:
        for k, v in hit.most_common():
            print("   ⚠ %-28s %4d 例: %s" % (k, v, samples[k][3]))
    else:
        print("   ✅ 无 repr 串残留")
    print()

    # ---- D. graph_export 的 evidence 出口 ----
    print("== D. 节点侧字段也存在性 ==")
    kn = collections.Counter()
    for n in d["nodes"]:
        for k in (n.get("props") or {}):
            kn[k] += 1
    for k in ("evidence", "verification_evidence", "created_at", "verification_level", "verifier"):
        print("   节点 props.%s = %d" % (k, kn.get(k, 0)))

    # ---- E. 撤回台账 vs 图上留痕 ----
    print()
    print("== E. 撤回台账 ==")
    etl = os.path.join(ROOT, "06_PoC", "etl", "neo4j")
    for fn in ("phase30_withdrawn.json", "phase31_withdrawn.json"):
        fp = os.path.join(etl, fn)
        if os.path.exists(fp):
            w = json.load(open(fp, encoding="utf-8"))
            tot = sum(len(v) for v in w.values()) if isinstance(w, dict) else len(w)
            keys = list(w.keys()) if isinstance(w, dict) else "-"
            print("   %s : %d 条  keys=%s" % (fn, tot, keys))
            # 看单条结构
            first = (w[list(w.keys())[0]][0] if isinstance(w, dict) and w else None)
            print("      单条结构:", json.dumps(first, ensure_ascii=False)[:300])


if __name__ == "__main__":
    main()
