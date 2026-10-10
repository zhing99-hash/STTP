# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""_recon_phase35_mnx_recov.py —— Phase 35 只读复核：MetaNetX 对 Rhea 反应的真实覆盖
============================================================================
动机（铁律 #44：跨轮引用的数字必须由仪器现算）：
  本轮侦察结论一度写「MetaNetX 全库仅覆盖 **456** 个 Rhea 反应」。
  用仪器现算 `mnx_reac_xref.tsv` 实为 **1836** 个唯一 `rh:<id>` —— **记录数字有误，已订正**。
  但**结论不变**：我们图谱的 Rhea 反应集与 MNX 的 1836 个**几乎不相交**（939 中 45；残差 269 中 5）。

用法：python 06_PoC/_recon_phase35_mnx_recov.py
"""
from __future__ import annotations
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "11_真实数据"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import verification_model as vm

MNX = os.path.join(ROOT, ".runlog", "mnx25", "mnx_reac_xref.tsv")

# ---- 1) 本地图谱：T4 残差涉及的「反应节点」与其中的 Rhea 反应 ----
d = json.load(open(os.path.join(HERE, "etl", "normalized.json"), encoding="utf-8"))
nodes, edges = d["nodes"], d["edges"]
N = {n["id"]: n for n in nodes}
res = [e for e in edges if e["type"] in ("reactant_of", "product_of") and vm.indep_reason(e, N)]


def reaction_endpoint(e):
    s, t = N.get(e["source"], {}), N.get(e["target"], {})
    return (s, t) if "Reaction" in (s.get("labels") or []) else (t, s)


rx_src = collections.Counter()
rx_ids = set()
for e in res:
    rx, _p = reaction_endpoint(e)
    rx_src[(rx.get("props") or {}).get("source")] += 1
    rx_ids.add(rx["id"])

all_rhea = {int(re.search(r"(\d+)", n["id"]).group(1))
            for n in nodes
            if (n.get("props") or {}).get("source") == "Rhea" and re.search(r"\d+", n["id"])}
res_rhea = {int(re.search(r"(\d+)", i).group(1)) for i in rx_ids
            if i.startswith("RH:") and re.search(r"\d+", i)}

# ---- 2) MetaNetX reac_xref 的 Rhea 集 ----
mnx = set()
if os.path.exists(MNX):
    for ln in open(MNX, encoding="utf-8", errors="replace"):
        m = re.match(r"rh:(\d+)\t", ln)
        if m:
            mnx.add(int(m.group(1)))

print("=" * 88)
print("Phase 35 只读复核 · MetaNetX 对 Rhea 反应的覆盖（铁律 #44：数字由仪器现算）")
print("=" * 88)
print("T4 残差边 %d ；涉及唯一反应节点 %d" % (len(res), len(rx_ids)))
print("  反应端点来源分布：%s" % rx_src.most_common(8))
print("本图 Rhea 源节点 %d ；其中处于 T4 残差的 %d" % (len(all_rhea), len(res_rhea)))
print("MNX reac_xref 唯一 Rhea 反应 **%d**（旧记 456，**已订正**）" % len(mnx))
print("-" * 88)
print("★ 覆盖本图 Rhea 节点：%d / %d = %.1f%%" % (len(all_rhea & mnx), len(all_rhea),
      100.0 * len(all_rhea & mnx) / max(1, len(all_rhea))))
print("★ 覆盖 T4 残差 Rhea 反应：%d / %d = %.1f%%" % (len(res_rhea & mnx), len(res_rhea),
      100.0 * len(res_rhea & mnx) / max(1, len(res_rhea))))
print("  结论：MNX 的反应集与本图 Rhea 集**几乎不相交** → 不能作为 T4 的第二源（改轨依据成立）。")
