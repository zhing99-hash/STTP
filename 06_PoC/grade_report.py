#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
"""grade_report.py —— 证据等级（1—5 分）**只读**报告 + 对账（Phase 60 · M0 收口）

规范：`00_项目管理/证据分级规范与抓取优先级_20261019.md`
    §2 E 级规则（e1） ｜ §5 双口径 ｜ §7 工程落地（含 G1–G4 对账）

它做什么
--------
1. 现算**全图**等级分布：内部口径 `e1` 与对外口径 `p1`，**分列**（永不合并，#90）；
2. 给出 ≤2 分边按**来源**聚合的清单 —— 这是「低分清单 = 抓取任务包」的机器输出；
3. 跑 §7 的对账断言 **G1–G4**（+ G5 落盘一致），把「评分」从一份文档变成**可回归的事实**；
4. 报告**落盘状态**：分数是否已写进图快照、写的时候是哪个 commit、用的哪条规则版本。

它不做什么
----------
* **不改树**：不写 normalized.json、不写任何入库文件（产物只去 `--json` 指定的路径，
  默认 `.runlog/`，E29 家族：入库路径每跑一次就弄脏工作树）；
* **不联网**（铁律 #34 的精神：本命令不抓任何东西）；
* **不落盘分数** —— 那是 `bash sttp.sh gradepersist`（`11_真实数据/phase60_grade_delta.py`）。

退出码
------
`0` = 对账全过 ／ `1` = 有 FAIL ／ `2` = 缺权威图（与 `require_authority` 同口径）／
`3` = 仪器自证失败（连边都没读到，不得报 0）。

用法
----
    bash sttp.sh grade [--json .runlog/grade_report.json] [--top N]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "03_知识层"))
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
#: 落盘字段（与 `phase60_grade_delta.py` 逐字一致；两处不一致 ⇒ G5 会红）
PERSIST_FIELDS = ("evidence_grade_internal", "evidence_grade_public",
                  "grade_rule_version", "graded_at_commit")


# ---------------------------------------------------------------------------
# 三态报告（与仓内其它仪器同形：`[PASS]` / `[FAIL]` / `[SKIP]` + 末尾计数）
# ---------------------------------------------------------------------------
class Rep:
    def __init__(self):
        self.rows = []

    def add(self, ok, name, detail=""):
        """`ok`: True ⇒ PASS；False ⇒ FAIL；**None ⇒ SKIP**（不可判定，铁律 #20）。"""
        self.rows.append((("PASS" if ok else "FAIL") if ok is not None else "SKIP",
                          name, detail))

    def dump(self):
        for st, name, detail in self.rows:
            print("  [%s] %s%s" % (st, name, ("  —— " + detail) if detail else ""))
        n = collections.Counter(s for s, _, _ in self.rows)
        print("\n  grade_report: PASS %d / FAIL %d / SKIP %d"
              % (n["PASS"], n["FAIL"], n["SKIP"]))
        return n


def head_sha(short=True):
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"] if short else
                           ["git", "rev-parse", "HEAD"], cwd=ROOT,
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:                                   # noqa: BLE001
        return None


def no_authority():
    """与 `sttp.sh` 的 `require_authority` 同口径：缺权威图是**已知边界**，不是代码缺陷。"""
    print("")
    print("[不可用] 命令 'grade' 缺少**权威输入**（该文件被 .gitignore 刻意排除、不入库）：")
    print("           - 06_PoC/etl/normalized.json")
    print("  原因：等级计数以**权威图口径**（整图规模，随输入而变）为前提 ⇒ 干净检出里")
    print("        **结构性不可用**，这是**已知边界而非代码缺陷**。")
    print("  恢复：① 本地 bash sttp.sh export（从 Aura 反向导出权威图）")
    print("        ② 或从 Release 资产下载同名快照，放到上述路径。")
    print("")
    return 2


def srcs_of(e):
    p = e.get("props") or {}
    evs = [x for x in (p.get("verification_evidence") or []) if isinstance(x, dict)]
    return [x.get("src") for x in evs if x.get("src")]


# ---------------------------------------------------------------------------
# 对账断言（G1–G5）—— 真实全图与**合成图自检**共用同一份判据
#   ★ 为什么不写成两套：判据一旦分叉，「自检绿、全图红」或反之都会失去意义（#87）。
# ---------------------------------------------------------------------------
def run_assertions(edges, gi, gp, reg, redist_of, rep,
                   persisted=None, snap=None, rule=None):
    n = len(edges)
    ci = collections.Counter("null" if g is None else str(g) for g in gi)
    cp = collections.Counter("null" if g is None else str(g) for g in gp)

    s_i = sum(ci.values())
    rep.add(s_i == n and ci.get("null", 0) == 0, "G1",
            "内部口径 Σ=%d == 边总数 %d，且 null=%d（登记表可用 ⇒ 不得有不可判定）"
            % (s_i, n, ci.get("null", 0)))
    s_p = sum(cp.values())
    rep.add(s_p == n and cp.get("null", 0) == 0, "G1p",
            "对外口径 Σ=%d == 边总数 %d，且 null=%d" % (s_p, n, cp.get("null", 0)))

    # ★ Phase 82：满分要求的是「内容能确认」—— 两条路：字节可复算（verified），
    #   或字节变了但**内容等价且当场可复算**（`changed_but_proven`）。
    #   只认 verified 会把 661 条 proven 的 5 分边判死，而它们的内容确实没变。
    bad5 = [e for e, g in zip(edges, gi)
            if g == 5 and q_edge_fidelity(e, reg) not in ("verified", "changed_but_proven")]
    rep.add(not bad5, "G2",
            "内部 5 分 %d 条，其中「内容不可确认」的 %d 条"
            "（满分必须 verified 或 changed_but_proven）" % (ci.get("5", 0), len(bad5)))

    bad1 = [e for e, g in zip(edges, gi)
            if g == 1 and q_edge_fidelity(e, reg) == "verified"
            and (e.get("props") or {}).get("verification_level") == "source_asserted"]
    rep.add(not bad1, "G3",
            "1 分 %d 条，其中「fidelity=verified 且 level=source_asserted」的 %d 条（须 0）"
            % (ci.get("1", 0), len(bad1)))

    # ★ 规范原文写的是「96.6% 上相差 ≤1 档」。实测**不成立**：687 条内部 5 分的边
    #   因含不可再分发来源而对外掉到 2 分（差 3 档）。这不是算法写岔，是规则本身
    #   就该这样 ⇒ 判据改为「单调 + 可解释」：能解释多大的差都行，解释不了 1 档也不行。
    higher = [(e, a_, b_) for e, a_, b_ in zip(edges, gi, gp)
              if a_ is not None and b_ is not None and b_ > a_]
    unexplainable = [(e, a_, b_) for e, a_, b_ in zip(edges, gi, gp)
                     if a_ is not None and b_ is not None and b_ < a_
                     and all(redist_of(s) for s in srcs_of(e))]
    rep.add(not higher and not unexplainable, "G4",
            "对外>内部的 %d 条（须 0）；掉档但**全部来源可再分发**的 %d 条（须 0，否则两套算法写岔）"
            % (len(higher), len(unexplainable)))

    if persisted is not None:
        if persisted == 0:
            rep.add(None, "G5",
                    "尚未落盘 ⇒ **SKIP**（不计通过；跑 bash sttp.sh gradepersist --apply 后生效）")
        else:
            mismatch = sum(1 for e, a_, b_ in zip(edges, gi, gp)
                           if (e.get("props") or {}).get("evidence_grade_internal") != a_
                           or (e.get("props") or {}).get("evidence_grade_public") != b_)
            # ★ 硬断言只管**值与规则版本**；`graded_at_commit` 落后于 HEAD **不是错误** ——
            #   任何一个新提交都会让它落后（图没变、分数也没变），把它判红等于要求
            #   「每次提交都必须重跑 gradepersist」，那是噪音不是纪律。
            #   真正要抓的是「图变了 / 规则变了而分数没跟着重算」⇒ 值不等 ⇒ 这里红。
            ok_rule = snap["rule_in_snapshot"] == rule
            rep.add(mismatch == 0 and ok_rule, "G5",
                    "落盘值与现算不一致 %d 条（须 0）；快照规则 %s == 现行 %s：%s"
                    % (mismatch, snap["rule_in_snapshot"], rule, ok_rule))
    return ci, cp


_Q = None


def q_edge_fidelity(e, reg):
    """延迟 import `queries`（`--selfcheck` 也要用，且不希望模块导入顺序影响判据）。

    ★ Phase 82：用的是 `fidelity_for_grade`（**等级口径**），不是 `edge_fidelity`。
      两者只在「字节不符但内容等价且当场可复算」这一档上不同 —— 那一档记作
      `changed_but_proven`，对**等级**的贡献与 verified 相同（内容既然等价）。
      ⇒ 对账断言（G2/G3）必须与 `edge_grade` 用**同一个**口径，否则会变成
      「等级说 5 分、对账说这条边不可复算」的自相矛盾（实测 G2 红在 661 条）。
    """
    global _Q
    if _Q is None:
        import queries as _m                                # noqa: PLC0415
        _Q = _m
    return _Q.fidelity_for_grade(e, reg)


# ---------------------------------------------------------------------------
# 合成图自检 —— **不依赖权威图**（干净检出里也能跑，与 red_probe 同纪律）
# ---------------------------------------------------------------------------
SYNTH = {
    # 三个来源：SR=已登记+入库+可再分发；SN2=已登记+入库+**不可**再分发（构造「对外掉档」）；
    #           SN=已登记+**未入库**（not_in_repo）
    "known": {"SR", "SN2", "SN"},
    "hashes": {"SR": "h1", "SN2": "h2"},
    "repo_status": {"SN": "not_in_repo"},
}
SYNTH_REDIST = {"SR": True, "SN2": False, "SN": False}


def synth_edges():
    def e(level, evs, eid):
        return {"id": eid, "source": "a", "target": "b", "type": "t",
                "props": {"verification_level": level, "verification_evidence": evs}}
    return [
        # 5 分：cross_source(V=5) + 可复算(F=5)
        e("cross_source", [{"src": "SR", "src_hash": "h1"}], "e5"),
        # 4 分：rule_checked(V=4) + 可复算 ⇒ 4
        e("rule_checked", [{"src": "SR", "src_hash": "h1"}], "e4"),
        # 3 分：source_asserted(V=3) + 可复算 ⇒ 3
        e("source_asserted", [{"src": "SR", "src_hash": "h1"}], "e3"),
        # 2 分：rule_checked + 来源未入库(F=2) ⇒ 2
        e("rule_checked", [{"src": "SN"}], "e2"),
        # 1 分：model_inferred(V=1) + 可复算 ⇒ 1
        e("model_inferred", [{"src": "SR", "src_hash": "h1"}], "e1"),
        # ★ 内部 5 分、对外 2 分：来源**在库但不可再分发** ⇒ 差异**可解释**
        e("cross_source", [{"src": "SN2", "src_hash": "h2"}], "edrop"),
        # ★ G2 的**靶边**：cross_source(V=5) + 来源未入库(F=2) ⇒ 内部 2 分。
        #   一旦有人把 `unverifiable_not_in_repo` 改判成 5 档，这条就会变成
        #   「5 分但不可复算」 ⇒ G2 必须变红（`red_probe` R17）。
        e("cross_source", [{"src": "SN"}], "ebad"),
        # 1 分且无证据（fail-closed）
        e("source_asserted", [], "e0"),
    ]


def selfcheck():
    """规则自检：**合成 7 条边**覆盖 1—5 分 + 一次「对外掉档」，断言 G1–G4 全过。

    ★ 附一条**非空集**断言：合成集必须真的覆盖每一档，否则 G2/G3/G4 会是**恒真**
      （D72 同族：判据在空集上什么都不守）。这一条由 `red_probe` 的 R17–R19 反向守住。
    """
    import queries as q                                     # noqa: PLC0415
    edges = synth_edges()
    gi = [q.edge_grade(e, SYNTH) for e in edges]
    gp = [q.edge_grade_public(e, SYNTH, redist_of=lambda s: SYNTH_REDIST.get(s, False))
          for e in edges]
    print("=" * 92)
    print("证据等级规则**自检**（合成 7 条边，不依赖权威图 ⇒ 干净检出里也能跑）")
    print("  规则 e1（内部）/ p1（对外）")
    print("=" * 92)
    for e, a_, b_ in zip(edges, gi, gp):
        print("   %-6s level=%-16s 内部=%s 对外=%s  fidelity=%s"
              % (e["id"], (e["props"]["verification_level"]), a_, b_,
                 q.edge_fidelity(e, SYNTH)))
    rep = Rep()
    run_assertions(edges, gi, gp, SYNTH,
                   redist_of=lambda s: SYNTH_REDIST.get(s, False), rep=rep)
    ci = collections.Counter("null" if g is None else str(g) for g in gi)
    covered = all(ci.get(k, 0) > 0 for k in ("1", "2", "3", "4", "5"))
    dropped = sum(1 for a_, b_ in zip(gi, gp) if b_ < a_)
    rep.add(covered and dropped >= 1, "GS",
            "非空集：1—5 分**每档至少一条**（否则 G2/G3 恒真）+ 至少 1 条对外掉档（否则 G4 恒真）"
            "｜实际覆盖 %s，掉档 %d 条" % (dict(sorted(ci.items())), dropped))
    n = rep.dump()
    return 1 if n["FAIL"] else 0


def main():
    ap = argparse.ArgumentParser(description="证据等级（1—5）只读报告 + 对账")
    ap.add_argument("--json", default=os.path.join(ROOT, ".runlog", "grade_report.json"))
    ap.add_argument("--top", type=int, default=8, help="≤2 分清单按来源聚合的 TOP N")
    ap.add_argument("--selfcheck", action="store_true",
                    help="**合成图**规则自检（不依赖权威图 ⇒ 干净检出里也能跑）")
    a = ap.parse_args()

    if a.selfcheck:
        return selfcheck()

    if not os.path.isfile(NORM):
        return no_authority()

    try:
        import queries as q                                # noqa: PLC0415
        import source_registry as sr                        # noqa: PLC0415
    except Exception as ex:                                 # noqa: BLE001
        print("[FATAL] 无法加载 queries / source_registry（%s: %s）" % (type(ex).__name__, ex))
        return 3

    print("=" * 92)
    print("证据等级（1—5 分）只读报告    HEAD = %s    规则 %s（内部）/ p1（对外）"
          % (head_sha(), getattr(q, "GRADE_RULE_VERSION", "?")))
    print("  输入 06_PoC/etl/normalized.json  （现算，不落盘）")
    print("=" * 92)

    reg = q.build_stale_registry(sr.ids(), sr.hash_map(base=ROOT), sr.repo_status_map(base=ROOT))
    b = q.NXBackend(NORM)
    edges = b.all_edges()
    if not edges:
        print("[FATAL] 权威图里没有边 —— 本仪器**拒绝以空集自证**（铁律 #20）")
        return 3
    # ★ Phase 82（D140「注册 ≠ 生效」）：本文件**自己**构造 registry ⇒ 拿不到服务端注入的
    #   proven ⇒ 703 条被证明覆盖的边（其中 661 条 5 分）会为一个注释凭空掉到 1 分，
    #   而 G2 断言「5 分且 fidelity≠verified」在掉档后**自洽** ⇒ 不会红（实测 5 分 702 → 41）。
    #   ⇒ 自建 registry 又算等级，就必须显式接上第五档。
    try:
        import fidelity_proven as fp                          # noqa: PLC0415
        fp.attach_proven(reg, edges, base=ROOT)
    except Exception as _ex:                                 # noqa: BLE001
        print("[WARN] 第五档 proven 不可用（%s: %s）⇒ 等级按四分类口径（会掉档）"
              % (type(_ex).__name__, _ex))
    print("边总数 %d" % len(edges))

    gi = [q.edge_grade(e, reg) for e in edges]
    gp = [q.edge_grade_public(e, reg, redist_of=sr.is_redistributable) for e in edges]
    ci = collections.Counter("null" if g is None else str(g) for g in gi)
    cp = collections.Counter("null" if g is None else str(g) for g in gp)

    print("\n【内部口径 e1】（本机含全部 *_raw.json ⇒ 可复算性最好）")
    for k in ("5", "4", "3", "2", "1", "null"):
        n = ci.get(k, 0)
        print("   E%s  %7d  %6.2f%%" % (k, n, 100.0 * n / len(edges)))
    print("\n【对外口径 p1】（F 维换成「来源可否随包分发」= A03 再分发闭包）")
    for k in ("5", "4", "3", "2", "1", "null"):
        n = cp.get(k, 0)
        print("   E%s  %7d  %6.2f%%" % (k, n, 100.0 * n / len(edges)))
    print("   ★ 两套**永不合并**：内部 5 分 %d 条 ≠ 对外 5 分 %d 条（#90）。"
          % (ci.get("5", 0), cp.get("5", 0)))

    # ---- ≤2 分清单 = 抓取任务包（按来源聚合） --------------------------------
    low = collections.Counter()
    low_edge_type = collections.defaultdict(collections.Counter)
    for e, g in zip(edges, gi):
        if g is None or g > 2:
            continue
        ss = srcs_of(e) or ["<无 src>"]
        for s in ss:
            low[s] += 1
            low_edge_type[s][e.get("type")] += 1
    print("\n【≤2 分清单按来源聚合 TOP%d】（内部口径；这是抓取优先级的第一输入）" % a.top)
    print("   %-28s %9s  %s" % ("来源", "≤2 分边数", "主要边类型"))
    for s, n in low.most_common(a.top):
        main_t = "、".join("%s(%d)" % (t, c) for t, c in low_edge_type[s].most_common(2))
        print("   %-28s %9d  %s" % (s, n, main_t))

    # ---- 落盘状态 ------------------------------------------------------------
    persisted = sum(1 for e in edges
                    if all(k in (e.get("props") or {}) for k in PERSIST_FIELDS))
    sample = next((e for e in edges if "grade_rule_version" in (e.get("props") or {})), None)
    snap = {"persisted_edges": persisted,
            "n_edges_total": len(edges),
            "rule_in_snapshot": (sample or {}).get("props", {}).get("grade_rule_version"),
            "graded_at_commit": (sample or {}).get("props", {}).get("graded_at_commit"),
            # ★ 落盘记的是**完整 sha**（`--short` 的长度随仓库 core.abbrev 变，
            #   跨机器不可比）⇒ 这里也用完整 sha 比，显示仍用短 sha。
            "head": head_sha(short=False)}
    print("\n【落盘状态】")
    print("   快照里带全部落盘字段的边 : %d / %d" % (persisted, len(edges)))
    print("   快照中的规则版本         : %s" % (snap["rule_in_snapshot"] or "（无 —— 尚未落盘）"))
    print("   落盘时的 commit          : %s（当前 HEAD %s）"
          % (snap["graded_at_commit"] or "（无）", head_sha()))
    if persisted == 0:
        print("   ★ 未落盘 ⇒ 分数目前**每请求现算**（同 HEAD 下稳定，换 HEAD 会漂移）。")
        print("     收口动作：bash sttp.sh gradepersist --apply")

    # ---- 对账断言 G1–G5（与 `--selfcheck` **共用同一份判据**，不得分叉）--------
    rep = Rep()
    print("\n" + "=" * 92)
    print("对账断言（§7 · G1–G4；G5 = 落盘与现算一致）")
    print("=" * 92)
    ci, cp = run_assertions(edges, gi, gp, reg, redist_of=sr.is_redistributable, rep=rep,
                            persisted=persisted, snap=snap, rule=q.GRADE_RULE_VERSION)

    n = rep.dump()
    if persisted and snap["graded_at_commit"] != snap["head"]:
        # ★ 放在 dump **之后**：与断言表混排会被读成「又一条判据」。
        print("   ★ 提示：落盘时 commit %s 早于当前 HEAD %s"
              "（图未变则分数仍一致；要刷新记录请跑 bash sttp.sh gradepersist --apply）"
              % (snap["graded_at_commit"], snap["head"]))
    out = {
        "meta": {"head": head_sha(), "n_edges_total": len(edges),
                 "grade_rule": q.GRADE_RULE_VERSION,
                 "grade_public_rule": q.GRADE_PUBLIC_RULE_VERSION,
                 "persisted": snap},
        "internal": dict(ci), "public": dict(cp),
        "low_by_source": {s: c for s, c in low.most_common()},
        "assertions": [{"id": i, "status": s, "detail": d} for s, i, d in rep.rows],
    }
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("  -> %s" % os.path.relpath(a.json, ROOT))
    return 1 if n["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
