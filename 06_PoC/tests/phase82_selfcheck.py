# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""phase82_selfcheck.py —— V18 第五档 `changed_but_proven` 的**判据与负对照**

⚠️ 本轮**只写判据，不开闸**。
   `queries.FIDELITY_CLASSES` 仍是四类、`edge_fidelity()` 一字未改、服务端
   `/api/subgraph` 仍只报四分类。本文件测的是 `11_真实数据/fidelity_proven.py`
   这套**影子口径**：它能不能把 `changed` 正确二分，以及**能不能被打假**。
   开闸（V18d 改成 `changed_unproven == 0` + 服务端披露第五档）是下一步，
   必须在本文件全绿之后。

■ 为什么要在「开闸前」写负对照
   S3 的教训（D145）是反过来的：先跑了真用例、发现闸门红了，才回头讨论判据
   该不该改。顺序一颠倒，人就容易为了「让闸门变绿」而放宽判据 —— 那是安全性
   净损失。所以这里的顺序是：**先钉死「真变更必须仍红」，再谈放行**。

■ 回放而不是改仓库
   S3 那次的真实状态（`element_reference.py` 带一行注释）仍完整躺在 git 历史里：
     0878538 = 带注释的那一版（AST 与当前 HEAD 相同，字节 +67）
     1fe029…  = 图上记录的登记哈希（= 49fe18c 回退后的内容）
   本自检**不动工作区**：把登记快照里的 src 哈希换成 0878538 口径，就精确复现了
   「字节变了 / 内容没变」这一现场。这样负对照用的是**真文件真历史**，
   不是构造样本（D139 同族要求）。

判据
----
  S100  回放 S3：changed 边必须**全部**被真实证明覆盖（proven == changed）
  S100b 守恒：proven + unproven == changed，且 == 独立统计的 changed 边数
  S101  复算：三张真实证明 `check_content_attestation` 全 True（取 git 真 blob 重跑 AST）
  S102  非真空：proven 必须 > 100（一条也算过 = 判据形同虚设）
  S103  撤回证明 ⇒ proven == 0（"有工具"不等于"已证明"）
  NEG-S104 伪造 `composite.from` ⇒ 覆盖 0
  NEG-S105 伪造 `composite.to` ⇒ 覆盖 0
  NEG-S106 串族（src 证明改成 ref 身份）⇒ 覆盖 0
  NEG-S107 少一张（只留 B5）⇒ B2 的 5 条边必须仍 unproven
  NEG-S108 篡改 `files[].to_hash` ⇒ 复算拒 ⇒ 覆盖 0
  NEG-S109 `to_is_head=False` 而调用方要求当前 ⇒ 覆盖 0（历史版本对不放行）
  NEG-S110 空证明表 ⇒ 覆盖 0
  NEG-S111 多来源边只覆盖一个 ⇒ 仍 changed（最容易漏的口子）
  NEG-S112 篡改 `from_rev`（借别的提交的版本对）⇒ 复算拒
  S113  现状回放：HEAD（已回退）下 changed == 0，且 proven == 0（不会把"没变"算成"证明过"）
  S114  生产口径未动：`FIDELITY_CLASSES` 仍四类，`edge_fidelity` 仍报 changed

运行： bash sttp.sh phase82
"""
from __future__ import annotations

import ast
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
for p in (os.path.join(ROOT, "11_真实数据"), os.path.join(ROOT, "03_知识层"),
          os.path.join(ROOT, "06_PoC")):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import queries as Q                                        # noqa: E402
import source_registry as sr                               # noqa: E402
import reverify_attest as ra                               # noqa: E402
import evidence_deps as ed                                 # noqa: E402
import fidelity_proven as fp                               # noqa: E402

#: S3 那次「加注释」的提交（AST 与当前 HEAD 相同，字节不同）
S3_REV = "0878538"
#: 图上记录的登记哈希（对应 49fe18c 回退后的内容）
REC_HASH = "1fe029a4cdc898eb6baf2070bdea914565258200da4a908a7711c66037235f71"
#: 三个 src 身份（同一个文件充当三个来源登记）
SRC_IDS = ("Phase13.Gate.B2", "Phase13.Gate.B5", "element_reference")


class Report:
    def __init__(self):
        self.rows = []

    def add(self, ok, name, detail=""):
        self.rows.append((bool(ok), name, detail or ""))
        return bool(ok)

    def render(self, title):
        print("=" * 78)
        print("  " + title)
        print("=" * 78)
        for ok, name, detail in self.rows:
            print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                                   ("  —— " + detail) if detail else ""))
        npass = sum(1 for ok, _, _ in self.rows if ok)
        nfail = len(self.rows) - npass
        print()
        print("  phase82_selfcheck（V18 第五档判据，**未开闸**）: PASS %d / FAIL %d"
              % (npass, nfail))
        return 0 if nfail == 0 else 1


def _split(edges, reg, atts, require_head=False):
    """统一入口：所有负对照都在**同一份回放快照**上跑，只换证明表。"""
    return fp.split_changed(
        edges, reg, atts, require_head=require_head,
        now=lambda k, o: ra.composite_hash_at_rev(k, o, S3_REV, ROOT), base=ROOT)


def main():
    rep = Report()
    g = json.load(open(os.path.join(ROOT, "06_PoC", "etl", "normalized.json"),
                       encoding="utf-8"))
    edges = g.get("edges") or []

    # ---------- 回放快照：字节换成 0878538，图上记录值不变 ----------
    hm = sr.hash_map(base=ROOT)
    rsm = sr.repo_status_map(base=ROOT)
    hm2 = dict(hm)
    for sid in SRC_IDS:
        hm2[sid] = ra.composite_hash_at_rev("src", sid, S3_REV, ROOT)
    reg = Q.build_stale_registry(sr.ids(), hm2, rsm)

    # 现状快照（HEAD 已回退）：用于 S113
    reg_now = Q.build_stale_registry(sr.ids(), hm, rsm)

    # ---------- 真实证明：三张（内存里造，不动权威表） ----------
    atts = {}
    built = []
    for sid in SRC_IDS:
        a, why = ra.build_content_attestation("src", sid, from_hash=REC_HASH,
                                              to_rev=S3_REV, base=ROOT)
        built.append((sid, a, why))
        if a:
            atts[a["id"]] = a
    rep.add(all(a for _, a, _ in built) and len(atts) == len(SRC_IDS),
            "S101 三张真实证明可生成（%s 的 %d 个 src 身份）" % (S3_REV, len(SRC_IDS)),
            "; ".join("%s:%s" % (sid, why) for sid, a, why in built if not a) or "ok")

    checks = []
    for sid, a, _ in built:
        if a:
            ok, detail = ra.check_content_attestation(a, base=ROOT)
            checks.append((sid, ok, detail))
    rep.add(bool(checks) and all(ok is True for _, ok, _ in checks),
            "S101b 复算：取 git 真 blob 重跑 ast_identical ⇒ 三张全成立",
            "; ".join("%s=%s" % (sid, ok) for sid, ok, _ in checks))

    # ---------- S100 正向：回放 S3 ----------
    r = _split(edges, reg, atts)
    rep.add(r["n_changed"] > 0 and r["n_proven"] == r["n_changed"] and r["n_unproven"] == 0,
            "S100 回放 S3：changed 边全部被真实证明覆盖（proven == changed）",
            "changed=%d / proven=%d / unproven=%d"
            % (r["n_changed"], r["n_proven"], r["n_unproven"]))

    ind_changed = sum(1 for e in edges if Q.edge_fidelity(e, reg) == "changed")
    rep.add(r["conserved"] and r["n_changed"] == ind_changed,
            "S100b 守恒：proven + unproven == changed == 独立统计（无漏项/无重复）",
            "split=%d / 独立=%d / conserved=%s"
            % (r["n_changed"], ind_changed, r["conserved"]))

    # ★ 负对照只在**回放态下的 changed 子集**上跑：断言的对象是「这些边怎么被二分」，
    #   不关心另外 4.8 万条没变的边。全图跑 14 遍会让这条仪器从 12 秒涨到 110 秒，
    #   而慢仪器在门禁里会被外部超时杀掉 —— 那反而制造污染（见 D146）。
    raw_changed = [e for e in edges if Q.edge_fidelity(e, reg) == "changed"]

    # ---------- S102 非真空 ----------
    rep.add(r["n_proven"] > 100,
            "S102 非真空：proven 必须 > 100（只覆盖一两条也算过 ⇒ 判据形同虚设）",
            "proven=%d" % r["n_proven"])

    # ---------- S103 撤回证明 ----------
    r0 = _split(raw_changed, reg, {})
    rep.add(r0["n_proven"] == 0 and r0["n_unproven"] == r["n_changed"],
            "S103 撤回全部证明 ⇒ proven == 0（「有工具」不等于「已证明」）",
            "proven=%d / unproven=%d" % (r0["n_proven"], r0["n_unproven"]))

    # ---------- 负对照：逐项伪造 ----------
    def forge(mut, tag, name):
        d = copy.deepcopy(atts)
        mut(d)
        rr = _split(raw_changed, reg, d)
        rep.add(rr["n_proven"] == 0,
                "%s %s ⇒ 必须完全失效" % (tag, name),
                "proven=%d（应为 0）unproven=%d" % (rr["n_proven"], rr["n_unproven"]))
        return rr

    def _patch_all(d, fn):
        for k in list(d):
            fn(d[k])

    forge(lambda d: _patch_all(d, lambda a: a["composite"].__setitem__(
              "from", REC_HASH[:-1] + ("0" if REC_HASH[-1] != "0" else "1"))),
          "NEG-S104", "伪造 composite.from（末位改一个字符）")

    forge(lambda d: _patch_all(d, lambda a: a["composite"].__setitem__(
              "to", str(a["composite"]["to"])[:-1] + "0")),
          "NEG-S105", "伪造 composite.to")

    forge(lambda d: _patch_all(d, lambda a: a.__setitem__("object_kind", "ref")),
          "NEG-S106", "串族（src 证明改成 ref 身份）")

    forge(lambda d: _patch_all(d, lambda a: a["files"][0].__setitem__(
              "to_hash", "0" * 12)),
          "NEG-S108", "篡改 files[].to_hash（逐文件复算不认）")

    forge(lambda d: _patch_all(d, lambda a: a.__setitem__("from_rev", S3_REV)),
          "NEG-S112", "篡改 from_rev（借别的提交的版本对）")

    # NEG-S107：少一张（B2）—— B2 的 5 条边必须仍 unproven
    d107 = {k: v for k, v in atts.items() if v.get("object_id") != "Phase13.Gate.B2"}
    r107 = _split(raw_changed, reg, d107)
    b2_edges = [e for e in edges
                if any(isinstance(ev, dict) and ev.get("src") == "Phase13.Gate.B2"
                       for ev in ((e.get("props") or {}).get("verification_evidence") or []))]
    b2_unproven = [x for x in r107["unproven"]
                   if any(m.get("src") == "Phase13.Gate.B2" for m in x["detail"]["misses"])]
    rep.add(bool(b2_edges) and len(b2_unproven) == len(b2_edges) and r107["n_proven"] > 0,
            "NEG-S107 少一张证明（B2）⇒ 只由 B2 支撑的边仍 unproven（D138 实证）",
            "B2 边 %d / 未覆盖 %d / 其余 proven %d"
            % (len(b2_edges), len(b2_unproven), r107["n_proven"]))

    # NEG-S109：历史版本对（to_is_head=False）在"当前"口径下必须不生效
    r109 = _split(raw_changed, reg, atts, require_head=True)
    rep.add(r109["n_proven"] == 0,
            "NEG-S109 to_is_head=False 而调用方要求当前 ⇒ 覆盖 0（历史版本对不放行）",
            "proven=%d（应为 0）" % r109["n_proven"])

    # NEG-S110：空表
    rep.add(_split(raw_changed, reg, {})["n_proven"] == 0,
            "NEG-S110 空证明表 ⇒ 覆盖 0", "proven=0")

    # NEG-S111：多来源边只覆盖一个 —— 用**真实来源 + 真实哈希**合成一条双边
    fake = {"id": "FAKE:MULTISRC:1", "type": "proves", "source": "T:a", "target": "T:b",
            "props": {"verification_evidence": [
                {"src": "Phase13.Gate.B2", "src_hash": REC_HASH},
                {"src": "Phase13.Gate.B5", "src_hash": REC_HASH}]}}
    d111 = {k: v for k, v in atts.items() if v.get("object_id") != "Phase13.Gate.B2"}
    r111 = _split([fake], reg, d111)
    miss_src = [m.get("src") for m in (r111["unproven"][0]["detail"]["misses"]
                                       if r111["unproven"] else [])]
    rep.add(r111["n_proven"] == 0 and "Phase13.Gate.B2" in miss_src,
            "NEG-S111 多来源边只覆盖一个 ⇒ 仍 changed（不许「四条覆盖一条就放行」）",
            "proven=%d / 未覆盖来源=%s" % (r111["n_proven"], miss_src))
    # 同一合成边，两张都给 ⇒ 必须 proven（证明上一行不是"恒红"）
    r111b = _split([fake], reg, atts)
    rep.add(r111b["n_proven"] == 1,
            "S111b 同一条合成边两张都给 ⇒ proven（上一条不是恒红）",
            "proven=%d" % r111b["n_proven"])

    # ---------- S113 现状回放 ----------
    rnow = fp.split_changed(edges, reg_now, atts, require_head=False, base=ROOT)
    rep.add(rnow["n_changed"] == 0 and rnow["n_proven"] == 0,
            "S113 现状（HEAD 已回退）⇒ changed == 0 且 proven == 0"
            "（不把「没变」算成「证明过」）",
            "changed=%d / proven=%d" % (rnow["n_changed"], rnow["n_proven"]))

    # ---------- S114 生产口径未动 ----------
    rep.add(Q.FIDELITY_CLASSES == ("verified", "changed", "unverifiable_no_source",
                                   "unverifiable_not_in_repo"),
            "S114 生产口径未动：FIDELITY_CLASSES 仍四类（本轮只出判据，**不开闸**）",
            "FIDELITY_CLASSES=%s" % (Q.FIDELITY_CLASSES,))

    print()
    print("[事实] 回放快照：src 哈希取自 %s（带注释那版，AST 与 HEAD 相同）" % S3_REV)
    print("       图上记录值 %s…（= 49fe18c 回退后内容）" % REC_HASH[:12])
    print("       本文件**不改**工作区、不写权威证明表、不改任何生产判据。")
    return rep.render("Phase 82 · V18 第五档 changed_but_proven：判据与负对照")


if __name__ == "__main__":
    raise SystemExit(main())
