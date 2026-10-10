# -*- coding: utf-8 -*-
"""_recon_phase35_scope.py —— Phase 35 只读侦察：**北极星口径自审 + 占位实体审计**
============================================================================
第 25 轮「改轨」后的两项**新维度**体检（只读，不改图）：

  ① **口径自审**：北极星的分母 = T1–T8 任务族并集。全图还有多少边**不在分母内**？
     它们的证据档位如何？把分母外的大族纳入后（反向对照）达标率是多少？
     —— 铁律 #16「改口径必须做反向对照」的机器化。

  ② **占位实体审计**：节点名字是否为匿名占位（`FG52` / `reaction_1` / `molecule_1000`
     / `name=None`）？这类边的**语义不可读** —— 既不该记「正确」也不该记「错误」，
     是此前**从未被测量**过的一个维度（铁律 #33：每个新维度都先问「这里有没有
     从未被检验过的断言」）。

用法：python 06_PoC/_recon_phase35_scope.py
"""
from __future__ import annotations
import collections
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))

import graph_export as gx                                      # noqa: E402
import verification_model as vm                                # noqa: E402

NORM = os.path.join(HERE, "etl", "normalized.json")
_PH = re.compile(r"^(FG|reaction|molecule|mol|rxn|rx)[_\-]?\d+$", re.I)


def is_placeholder(name) -> bool:
    """名字是否为**匿名占位**（形如 `FG52` / `reaction_1` / `molecule_1000`，或空）。

    ⚠ 与门禁 (w) 同谓词：**匿名判定只看名字**；「是否算占位实体」另需
    **命名空间**为 `EK2:`（ElementKG2.0）。否则会把 `WD:Q3552958` 这类
    「真实体但本图未存 name」的节点误判（第 25 轮初次运行时已实测到该假阳）。
    """
    s = str(name).strip() if name is not None else ""
    return (not s) or bool(_PH.match(s))


def is_placeholder_node(node) -> bool:
    """占位**实体** = `EK2:` 命名空间 **且** 名字匿名（与门禁 (w) 完全同口径）。"""
    return str(node.get("id") or "").startswith("EK2:") \
        and is_placeholder((node.get("props") or {}).get("name"))


def main():
    d = json.load(open(NORM, encoding="utf-8"))
    N, E = d["nodes"], d["edges"]
    by_id = {n["id"]: n for n in N}
    subj = {n["id"]: gx.subject_of((n.get("props") or {}).get("domain"), n["id"]) for n in N}
    print("=" * 92)
    print("Phase 35 口径自审 · 规模 %d 节点 / %d 边" % (len(N), len(E)))
    print("=" * 92)

    # ---------------- ① 口径自审 ----------------
    in_task = [e for e in E if vm.tasks_of(e, subj)]
    out_task = [e for e in E if not vm.tasks_of(e, subj)]
    print("\n【① 北极星分母自审】")
    print("  全图边 %d ｜ 分母内（并集）%d ｜ **分母外 %d（%.1f%%）**"
          % (len(E), len(in_task), len(out_task), 100.0 * len(out_task) / len(E)))
    rc = collections.Counter(vm.scope_reason(e, by_id, False) for e in out_task)
    lv = collections.Counter()
    for e in out_task:
        lv[(e.get("props") or {}).get("verification_level")] += 1
    print("  分母外按理由：")
    for k, v in rc.most_common():
        print("     %-44s %6d" % (k, v))
    print("  分母外按档位：%s" % dict(lv.most_common()))
    weak = sum(c for k, c in lv.items() if vm.RANK.get(k, -1) < vm.RANK["rule_checked"])
    print("  分母外**弱证据**（< rule_checked）：%d / %d = **%.1f%%**"
          % (weak, len(out_task), 100.0 * weak / len(out_task)))

    # 反向对照：把分母外的大族纳入（门槛 rule_checked）
    EXT = {"has_quantity": "rule_checked", "has_functionalgroup": "rule_checked",
           "reagent_of": "rule_checked", "has_element": "rule_checked"}
    ext = [e for e in out_task if e["type"] in EXT]
    n_ok = sum(1 for e in in_task + ext
               if vm.tasks_passed(e, subj) or
               (e["type"] in EXT and vm.RANK.get((e.get("props") or {}).get("verification_level"), -1)
                >= vm.RANK[EXT.get(e["type"], "rule_checked")]))
    print("\n  【反向对照 · 口径扩展】+%s，门槛 rule_checked：" % "/".join(sorted(EXT)))
    print("     分母 %d（+%d），达标 %d = **%.1f%%**  ← 现口径（T1–T8）= 99.6%%，两者**不得单读**"
          % (len(in_task) + len(ext), len(ext), n_ok, 100.0 * n_ok / (len(in_task) + len(ext))))

    # ---------------- ② 占位实体审计 ----------------
    print("\n【② 占位实体审计（新维度）】")
    ph_nodes = [n for n in N if is_placeholder_node(n)]
    ek2 = [n for n in N if n["id"].startswith("EK2:")]
    print("  占位**实体**（`EK2:` 且名字匿名）%d 个；ElementKG2.0 节点共 %d 个（占位率 %.0f%%）"
          % (len(ph_nodes), len(ek2), 100.0 * len(ph_nodes) / max(len(ek2), 1)))
    print("  （对照）全图 `name` 缺失的节点 %d 个 —— 其中含 `WD:` 等**真实体未存名**，"
          "故占位判定**必须**叠加命名空间约束，否则假阳"
          % sum(1 for n in N if not (n.get("props") or {}).get("name")))
    ph_ids = {n["id"] for n in ph_nodes}
    touch = [e for e in E if e["source"] in ph_ids or e["target"] in ph_ids]
    in_den = [e for e in touch if vm.tasks_of(e, subj)]
    print("  **牵连占位实体的边 %d 条（全图 %.1f%%）** —— 语义不可读"
          % (len(touch), 100.0 * len(touch) / len(E)))
    print("    按类型：%s" % dict(collections.Counter(e["type"] for e in touch).most_common()))
    print("    其中**在北极星分母内** %d 条（这些边**达标**却无法表述结论）；分母外 %d 条"
          % (len(in_den), len(touch) - len(in_den)))
    print("\n  样例：")
    for e in touch[:5]:
        s = (by_id.get(e["source"]) or {}).get("props") or {}
        t = (by_id.get(e["target"]) or {}).get("props") or {}
        print("     %-20s %-22s -> %-22s | %r -> %r"
              % (e["type"], e["source"][:22], e["target"][:22], s.get("name"), t.get("name")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
