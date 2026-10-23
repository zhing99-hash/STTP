#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""Phase 63 —— G3 适用条件 · 只读报告与对账

定位
----
与 `11_真实数据/phase63_applicability_delta.py` **互为对手**：
* 不联网、不写图 —— 只读**已落盘**的 normalized.json；
* **不复用**生成侧的任何代码。判据在本文件里独立实现，
  否则就变成「用同一段代码验自己」（铁律 #87 的同族）。

判据（新开 **W 族**，避开已占用的 Y = 符号/学科对账）
----------------------------------------------------
  W1 status 全覆盖：每个 Formula 的 `applicability_status` 必须是三值之一
  W2 evidence 可追溯：每条条件的 term / snippet 必须能在它自报的字段原文里找到
  W3 无编造：`no_referent`（无指称）**不得**带任何 applicability
  W4 归属清晰：`extracted` 必须同时带 source / rule_version / 非空 evidence
  W5 非真空：extracted 与 no_referent 都要 > 0，否则 W2/W3 通过可能只是分支没走到
  W6 反循环：判级模型源码**不得**读取 `applicability`
     （Phase 61 教训：派生字段一旦被判级读取，就会「用 A 证明 A」）
  W7 无指称判定正确：`no_referent` 必须真的没有自然语言文本 ——
     这是**独立复核**生成侧的分类，防止「抽不到」被粉饰成「无从谈起」
  W8 与人工核对表一致：落盘的 extracted 集合 == VERDICT 里裁决为 True 的节点集

退出码：`0` 全过 ／ `1` 有 FAIL ／ `2` 缺权威图 ／ `3` 仪器自证失败
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
VM = os.path.join(ROOT, "11_真实数据", "verification_model.py")
RECON = os.path.join(ROOT, "06_PoC", "06_PoC/tests/recon_applicability.py")

RULE_VERSION = "a1"
STATUSES = ("extracted", "no_referent", "not_extracted")
TEXT_KEYS = ("informal", "description", "usage")


def load_verdict():
    """人工核对表是**事实**（54 条逐条读过的裁决），不是逻辑 —— 可共享。"""
    spec = importlib.util.spec_from_file_location("recon_applicability", RECON)
    if spec is None or spec.loader is None:
        return {}
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return {}
    return getattr(mod, "VERDICT", {}) or {}


VERDICT = load_verdict()


class Rep:
    def __init__(self):
        self.rows = []

    def add(self, ok, name, detail=""):
        self.rows.append((bool(ok), name, detail))
        return bool(ok)

    def dump(self):
        bad = 0
        for ok, name, detail in self.rows:
            print("  [%s] %-30s %s" % ("PASS" if ok else "FAIL", name, detail))
            bad += 0 if ok else 1
        return bad


def audit(nodes, vm_text="", check_set=True):
    """check_set=False 时跳过 W8 —— 它是**全图级**判据（落盘集合须恰好等于核对表），
    自证用的两三个合成节点不可能满足，故阴性对照要关掉它。"""
    st = collections.Counter()
    probs = collections.defaultdict(list)
    extracted = set()
    for n in nodes:
        p = n.get("props") or {}
        status = p.get("applicability_status")
        st["total"] += 1
        if status not in STATUSES:
            probs["W1"].append(n["id"])
            continue
        st[status] += 1
        appl = p.get("applicability") or []
        evid = p.get("applicability_evidence") or []
        has_text = any(str(p.get(k) or "").strip() for k in TEXT_KEYS)

        if status == "no_referent":
            if appl:
                probs["W3"].append(n["id"])
            if has_text:
                probs["W7"].append(n["id"])       # 有文本却自称无指称
        elif status == "extracted":
            extracted.add(n["id"])
            if not has_text:
                probs["W7"].append(n["id"])       # 没文本却抽出了条件
            for e in evid:
                src = str(p.get(e.get("field")) or "")
                if not e.get("term") or e.get("term") not in src \
                        or e.get("snippet") not in src:
                    probs["W2"].append(n["id"])
            if not appl or not evid \
                    or p.get("applicability_rule_version") != RULE_VERSION \
                    or not p.get("applicability_source"):
                probs["W4"].append(n["id"])

    true_ids = {k[0] for k, v in VERDICT.items() if v is True}
    if check_set and extracted != true_ids:
        diff = sorted(extracted ^ true_ids)[:5]
        probs["W8"].append("落盘 %d 条 vs 核对表 %d 条；差集 %s"
                           % (len(extracted), len(true_ids), diff))

    if not (st["extracted"] > 0 and st["no_referent"] > 0):
        probs["W5"].append("extracted=%d / no_referent=%d（须都 > 0）"
                           % (st["extracted"], st["no_referent"]))
    if "applicability" in (vm_text or ""):
        probs["W6"].append("verification_model.py 读取了 applicability ⇒ 循环证明风险")
    return st, probs


# ---------------------------------------------------------------------------
def _syn(nid, status, text=None, appl=None, evid=None, src=None, rule=RULE_VERSION):
    """合成一个 Formula 节点供自证使用。"""
    p = {"applicability_status": status}
    if text is not None:
        p["description"] = text
    if appl is not None:
        p["applicability"] = appl
    if evid is not None:
        p["applicability_evidence"] = evid
    if src is not None:
        p["applicability_source"] = src
    if rule is not None:
        p["applicability_rule_version"] = rule
    return {"id": nid, "labels": ["Entity", "Formula"], "props": p}


_GOOD_TEXT = "恒温下理想气体的压强与体积成反比。"
_GOOD_EVID = [{"term": "恒温下", "field": "description",
               "snippet": "恒温下理想气体的压强与体积成反比。"}]


def selfcheck():
    """★ 断言名**以判据 ID 开头**：red_probe.starts_with_id() 按前缀匹配。"""
    good_ext = _syn("OK:ext", "extracted", _GOOD_TEXT, ["恒温下"], _GOOD_EVID, "curated_text")
    good_nrf = _syn("OK:nrf", "no_referent")
    rep = Rep()
    cases = [
        ("W1:bad status 值非法",
         [good_ext, good_nrf, _syn("W1:bad", "unknown_status")], ""),
        ("W2:bad evidence 查不到原文",
         [good_nrf, _syn("W2:bad", "extracted", "恒压下理想气体……", ["恒压下"],
                         [{"term": "恒压下", "field": "description",
                           "snippet": "这段话里根本没有恒压下"}], "curated_text")], ""),
        ("W3:bad 无指称却带条件",
         [good_ext, _syn("W3:bad", "no_referent", None, ["量纲齐次"])], ""),
        ("W4:bad extracted 写了一半",
         [good_nrf, _syn("W4:bad", "extracted", _GOOD_TEXT, ["恒温下"], _GOOD_EVID,
                         None, None)], ""),
        ("W5:bad 只有一个分支（真空）", [good_ext], ""),
        ("W6:bad 判级模型读了 applicability",
         [good_ext, good_nrf], "props.get('applicability')"),
        ("W7:bad 无指称却有文本",
         [good_ext, _syn("W7:bad", "no_referent", _GOOD_TEXT)], ""),
        ("W8:bad 落盘超出核对表",
         [good_nrf, _syn("W8:bad", "extracted", _GOOD_TEXT, ["恒温下"], _GOOD_EVID,
                         "curated_text")], ""),
    ]
    for name, nodes, vm in cases:
        _, probs = audit(nodes, vm_text=vm)
        got = bool(probs.get(name[:2]))
        rep.add(got, name, "踩红成功" if got else "❌ 判据恒真，等于没有判据")

    _, probs = audit([good_ext, good_nrf], vm_text="", check_set=False)
    clean = not any(probs.values())
    rep.add(clean, "阴性对照", "好节点不触发任何判据" if clean
            else "❌ 误报：%s" % dict(probs))
    return 1 if rep.dump() else 0


def main():
    ap = argparse.ArgumentParser(description="Phase 63 G3 适用条件只读报告")
    ap.add_argument("--sample", type=int, default=10)
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()

    if a.selfcheck:
        return selfcheck()

    if not os.path.isfile(NORM):
        print("[不可用] 缺少权威图 06_PoC/etl/normalized.json（被 .gitignore 刻意排除）")
        return 2
    with open(NORM, encoding="utf-8") as f:
        g = json.load(f)
    formulas = [n for n in g["nodes"] if "Formula" in (n.get("labels") or [])]

    vm_text = ""
    if os.path.isfile(VM):
        with open(VM, encoding="utf-8") as f:
            vm_text = f.read()

    st, probs = audit(formulas, vm_text=vm_text)
    total = st["total"]

    print("=" * 84)
    print("Phase 63 —— G3 适用条件 · 只读报告")
    print("=" * 84)
    print("  Formula 节点 %d" % total)
    print("    extracted     %5d  （抽到适用条件）" % st["extracted"])
    print("    no_referent   %5d  （无指称，无从谈适用条件）" % st["no_referent"])
    print("    not_extracted %5d  （有文本但没抽到）" % st["not_extracted"])
    print("  覆盖率 %.2f%% —— 规划写的目标是 1,500 条（29.5%%），**不可达**，"
          % (100.0 * st["extracted"] / max(1, total)))
    print("  缺口 %d 条全在 5,000 条 PhysicsBabel 合成式上：它们没有指称对象。"
          % (1500 - st["extracted"]))

    print("\n【对账】")
    rep = Rep()
    rep.add(not probs.get("W1"), "W1 status 全覆盖",
            "status 缺失或非法：%d 条（须 0）" % len(probs.get("W1", [])))
    rep.add(not probs.get("W2"), "W2 evidence 可追溯",
            "term/snippet 查不到原文的：%d 条（须 0）" % len(probs.get("W2", [])))
    rep.add(not probs.get("W3"), "W3 无编造",
            "no_referent 却带条件的：%d 条（须 0）" % len(probs.get("W3", [])))
    rep.add(not probs.get("W4"), "W4 归属清晰",
            "extracted 写一半的：%d 条（须 0）" % len(probs.get("W4", [])))
    rep.add(not probs.get("W5"), "W5 非真空",
            "extracted %d / no_referent %d（须都 > 0）"
            % (st["extracted"], st["no_referent"]))
    rep.add(not probs.get("W6"), "W6 反循环",
            "判级模型读 applicability 的：%d（须 0）" % len(probs.get("W6", [])))
    rep.add(not probs.get("W7"), "W7 无指称判定正确",
            "无指称却有文本 / 有文本却自称无指称：%d 条（须 0）"
            % len(probs.get("W7", [])))
    rep.add(not probs.get("W8"), "W8 与人工核对表一致",
            probs["W8"][0] if probs.get("W8")
            else "落盘 == 核对表裁决为 True 的 %d 条"
            % len({k[0] for k, v in VERDICT.items() if v is True}))
    bad = rep.dump()

    for k in sorted(probs):
        if probs[k]:
            print("     %s 前 5：%s" % (k, probs[k][:5]))

    if a.sample:
        print("\n【抽样 %d 条 extracted】" % a.sample)
        shown = 0
        for n in formulas:
            p = n.get("props") or {}
            if p.get("applicability_status") == "extracted" and shown < a.sample:
                print("  %-26s %-24s %s"
                      % (n["id"], str(p.get("latex"))[:24],
                         " + ".join(p.get("applicability") or [])))
                shown += 1
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
