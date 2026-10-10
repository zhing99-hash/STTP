# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Phase 8-B1 · NIST CODATA 权威物理常量真实化。

背景
----
图谱中 13 个 `Constant` 节点是早期手工录入的（curated_seed）：
  * 值大体正确，但 **12/13 缺 `unit`**、**全部缺 `uncertainty` 与出处**；
  * 个别是**过时/低精度的旧值**：
      - `TH:pq:gas_const = 8.314`（CODATA 2022 为 8.314 462 618…，精度差 5.5e-5）
      - `EM:pq:permittivity = 8.854187817e-12`（CODATA 2014 旧值）
      - `EM:pq:permeability = 1.25663706212e-06`（CODATA 2018 旧值）
  * 另有 2 个 PhysicsBabel 空壳常量（`PB:pq:grav_const` / `PB:pq:planck`，`value=None`）。

本模块把「手抄常量」换成**权威真实拉取**：NIST CODATA 2022 完整清单
（`physics.nist.gov/cuu/Constants/Table/allascii.txt`，固定宽度 ASCII 表，
含 Quantity / Value / Uncertainty / Unit 四列）。

用法
----
    python 11_真实数据/codata_ingest.py --fetch     # 拉取 + 解析 -> codata2022_raw.json
    python 11_真实数据/codata_ingest.py --check     # 与图谱既有常量逐项比对（只读）
    python 11_真实数据/codata_ingest.py --build     # 产出 delta（补 unit/uncertainty/出处 + 值修正）
"""

import argparse
import json
import os
import re
import socket
import ssl
import sys
import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASCII_TXT = os.path.join(HERE, "codata2022_allascii.txt")
RAW = os.path.join(HERE, "codata2022_raw.json")
NORMALIZED = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")

SRC_URL = "https://physics.nist.gov/cuu/Constants/Table/allascii.txt"
SOURCE_TAG = "nist_codata_2022"


# ----------------------------------------------------------------------------
# HTTP（原始 socket，绕开 urllib 代理：本机 http_proxy 会干扰）
# ----------------------------------------------------------------------------
def http_get(host, path, timeout=30, limit=800000, retries=3):
    last = None
    for i in range(retries):
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((host, 443), timeout=timeout) as s:
                with ctx.wrap_socket(s, server_hostname=host) as ss:
                    ss.sendall((
                        "GET %s HTTP/1.1\r\nHost: %s\r\n"
                        "User-Agent: STTP/1.0 (academic knowledge-graph; contact: local)\r\n"
                        "Accept: text/plain,*/*\r\nConnection: close\r\n\r\n" % (path, host)
                    ).encode())
                    buf = b""
                    while len(buf) < limit:
                        try:
                            c = ss.recv(65536)
                        except socket.timeout:
                            break
                        if not c:
                            break
                        buf += c
            head, _, body = buf.partition(b"\r\n\r\n")
            if "chunked" in head.decode("latin-1", "replace").lower():
                out, i = b"", 0
                while i < len(body):
                    j = body.find(b"\r\n", i)
                    if j < 0:
                        break
                    try:
                        n = int(body[i:j].split(b";")[0], 16)
                    except ValueError:
                        break
                    if n == 0:
                        break
                    out += body[j + 2: j + 2 + n]
                    i = j + 2 + n + 2
                body = out
            status = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
            if "200" not in status:
                raise RuntimeError("HTTP %s" % status)
            return body
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError("拉取失败：%s" % last)


# ----------------------------------------------------------------------------
# 解析：NIST 固定宽度 ASCII 表
# ----------------------------------------------------------------------------
def _to_float(tok):
    """'6.674 30 e-11' / '299 792 458' / '8.314 462 618...' / '1.054 571 817... e-34' -> float

    注意：NIST 用 `...` 表示「精确值在此处截断」。省略号**可能出现在指数之前**
    （如 `1.054 571 817... e-34`），故必须剥掉任意位置的 `...`，不能只剥尾部，
    否则 float() 抛错 → 整行被丢弃（曾致 reduced Planck / Stefan-Boltzmann "未命中"）。
    """
    t = tok.replace(" ", "")
    t = t.replace("...", "")               # 剥任意位置的截断省略号
    t = re.sub(r"\(exact\)", "", t, flags=re.I)
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def parse_allascii(text):
    lines = text.splitlines()
    # 定位表头
    hdr_i = None
    for i, ln in enumerate(lines):
        if "Quantity" in ln and "Value" in ln and "Uncertainty" in ln and "Unit" in ln:
            hdr_i = i
            break
    if hdr_i is None:
        raise RuntimeError("未找到表头行")
    hdr = lines[hdr_i]
    c_unit = hdr.index("Unit")
    c_unc = hdr.index("Uncertainty")
    c_val = hdr.index("Value")
    # 列切分点：NIST allascii 固定宽度。实测（表头 Value@65 / Uncertainty@87 / Unit@109）
    #   name = [0, 60)   value = [60, 85)   uncertainty = [85, 108)   unit = [108, )
    # 相对表头的偏移：-5 / -2 / -1，用表头 index 推导以免硬编码。
    cut_name = c_val - 5
    cut_val = c_unc - 2
    cut_unc = c_unit - 1

    out = []
    for ln in lines[hdr_i + 2:]:
        if not ln.strip():
            continue
        if set(ln.strip()) <= set("-"):
            continue
        name = ln[:cut_name].strip()
        val_s = ln[cut_name:cut_val].strip()
        unc_s = ln[cut_val:cut_unc].strip()
        unit = ln[cut_unc:].strip()
        if not name or not val_s:
            continue
        exact = "exact" in val_s.lower() or "exact" in unc_s.lower()
        truncated = "..." in val_s
        val_s_clean = re.sub(r"\(exact\)", "", val_s, flags=re.I).strip()
        unc_s_clean = re.sub(r"\(exact\)", "", unc_s, flags=re.I).strip()
        value = _to_float(val_s_clean)
        if value is None:
            continue
        out.append({
            "quantity": name,
            "value": value,
            "value_raw": val_s,
            "value_truncated": bool(truncated),
            "uncertainty": None if exact else _to_float(unc_s_clean),
            "uncertainty_raw": unc_s,
            "unit": unit or None,
            "exact": bool(exact),
            "codata_year": 2022,
            "source": SOURCE_TAG,
            "source_url": SRC_URL,
        })
    return out


# ----------------------------------------------------------------------------
# 图谱既有常量 ↔ CODATA 量名映射（显式声明，避免模糊匹配）
# ----------------------------------------------------------------------------
# 注：`EM:pq:coulomb_const` CODATA 无直接条目（由 k = 1/(4πε₀) 派生）；
#     `MA:pq:euler_e` 是数学常量，非 CODATA 范畴 —— 二者不参与值核校。
CONST_MAP = {
    "CM:pq:grav_const": "Newtonian constant of gravitation",
    "TH:pq:gas_const": "molar gas constant",
    "EM:pq:permittivity": "vacuum electric permittivity",
    "EM:pq:permeability": "vacuum mag. permeability",   # 注意 CODATA 表用缩写 mag.
    "QM:pq:planck_const": "Planck constant",
    "QM:pq:reduced_planck": "reduced Planck constant",
    "RT:pq:light_speed": "speed of light in vacuum",
    "SM:pq:boltz_const": "Boltzmann constant",
    "CE:pq:faraday_const": "Faraday constant",
    # 图谱缺失、但属「跨学科桥」的高价值常量 → 新增
}
# 需新增的 CODATA 常量（图谱尚无，且支撑 数学↔物理↔化学 桥）
CONST_ADD = {
    "CO:pq:avogadro": ("Avogadro constant", "N_A"),
    "CO:pq:elementary_charge": ("elementary charge", "e"),
    "CO:pq:electron_mass": ("electron mass", "m_e"),
    "CO:pq:proton_mass": ("proton mass", "m_p"),
    "CO:pq:vacuum_impedance": ("characteristic impedance of vacuum", "Z_0"),
    "CO:pq:stefan_boltzmann": ("Stefan-Boltzmann constant", "sigma"),
    "CO:pq:rydberg": ("Rydberg constant", "R_inf"),
    "CO:pq:atomic_mass": ("atomic mass constant", "m_u"),
    "CO:pq:fine_structure": ("fine-structure constant", "alpha"),
    "CO:pq:standard_gravity": ("standard acceleration of gravity", "g_n"),
}


# 空壳 / 重复常量 → 权威常量节点（跨源对齐边，语义为「同指一物理量」，非合并）
CONST_ALIAS = {
    "PB:pq:grav_const": "CM:pq:grav_const",    # PhysicsBabel 的 G 符号 == curated 引力常数
    "PB:pq:planck": "QM:pq:planck_const",      # PhysicsBabel 的 h 符号 == curated 普朗克常数
}


# ----------------------------------------------------------------------------
# 常量定义关系 + 单位（2026-10-09 跨学科连通性审计补齐）
# ----------------------------------------------------------------------------
# ⚠ 缺陷背景：原实现**只给新常量建节点、未建任何边** → 10 个所谓「跨学科桥常量」
#    在图上全部 deg=0，不可达、不参与 GNN 消息传递，**桥接价值实际未落地**。
#    连通性审计（06_PoC/connectivity_audit.py）把这一问题暴露出来。
# 下列关系全部取自 **CODATA/NIST 标准定义式**（非猜测），方向 `a --derived_from--> b`
# 读作「a 由 b 导出 / 以 b 定义」，note 写明定义式以便复核。
CONST_DERIV = [
    ("TH:pq:gas_const",        "CO:pq:avogadro",          "R = N_A·k_B"),
    ("TH:pq:gas_const",        "SM:pq:boltz_const",       "R = N_A·k_B"),
    ("CE:pq:faraday_const",    "CO:pq:avogadro",          "F = N_A·e"),
    ("CE:pq:faraday_const",    "CO:pq:elementary_charge", "F = N_A·e"),
    ("CO:pq:atomic_mass",      "CO:pq:avogadro",          "m_u = M_u/N_A"),
    ("CO:pq:fine_structure",   "CO:pq:elementary_charge", "α = e²/(4πε₀ħc)"),
    ("CO:pq:fine_structure",   "EM:pq:permittivity",      "α = e²/(4πε₀ħc)"),
    ("CO:pq:fine_structure",   "QM:pq:reduced_planck",    "α = e²/(4πε₀ħc)"),
    ("CO:pq:fine_structure",   "RT:pq:light_speed",       "α = e²/(4πε₀ħc)"),
    ("CO:pq:stefan_boltzmann", "SM:pq:boltz_const",       "σ = 2π⁵k⁴/(15h³c²)"),
    ("CO:pq:stefan_boltzmann", "QM:pq:planck_const",      "σ = 2π⁵k⁴/(15h³c²)"),
    ("CO:pq:stefan_boltzmann", "RT:pq:light_speed",       "σ = 2π⁵k⁴/(15h³c²)"),
    ("CO:pq:vacuum_impedance", "EM:pq:permeability",      "Z_0 = μ₀c"),
    ("CO:pq:vacuum_impedance", "RT:pq:light_speed",       "Z_0 = μ₀c"),
    ("CO:pq:rydberg",          "CO:pq:fine_structure",    "R_∞ = α²m_e c/(2h)"),
    ("CO:pq:rydberg",          "CO:pq:electron_mass",     "R_∞ = α²m_e c/(2h)"),
    ("CO:pq:rydberg",          "QM:pq:planck_const",      "R_∞ = α²m_e c/(2h)"),
    ("CO:pq:rydberg",          "RT:pq:light_speed",       "R_∞ = α²m_e c/(2h)"),
]

# 常量 → 图谱既有 Unit 节点（单位取自 CODATA 表 `unit` 列）
CONST_UNIT = [
    ("CO:pq:elementary_charge", "EM:un:coulomb", "C"),
    ("CO:pq:electron_mass",     "UN:kg",         "kg"),
    ("CO:pq:proton_mass",       "UN:kg",         "kg"),
    ("CO:pq:atomic_mass",       "UN:kg",         "kg"),
    ("CO:pq:standard_gravity",  "UN:mps2",       "m/s^2"),
    ("CO:pq:vacuum_impedance",  "EM:un:ohm",     "ohm"),
]


# ----------------------------------------------------------------------------
def load_raw():
    if not os.path.exists(RAW):
        print("[ERR] 缺少 %s，先跑 --fetch" % os.path.basename(RAW))
        sys.exit(2)
    return json.load(open(RAW, encoding="utf-8"))


def load_graph_nodes():
    d = json.load(open(NORMALIZED, encoding="utf-8"))
    return d["nodes"] if isinstance(d, dict) else d


def rel_diff(a, b):
    if a is None or b is None:
        return None
    if a == b:
        return 0.0
    denom = max(abs(a), abs(b))
    return abs(a - b) / denom if denom else None


def cmd_fetch(_a):
    print("拉取 NIST CODATA 2022 完整清单 …")
    body = http_get("physics.nist.gov", "/cuu/Constants/Table/allascii.txt")
    text = body.decode("utf-8", "replace")
    open(ASCII_TXT, "w", encoding="utf-8").write(text)
    print("  原始表已存 %s（%d B / %d 行）" % (
        os.path.relpath(ASCII_TXT, ROOT), len(body), len(text.splitlines())))
    items = parse_allascii(text)
    json.dump({
        "meta": {"source": SOURCE_TAG, "url": SRC_URL, "codata_year": 2022,
                 "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
                 "count": len(items)},
        "constants": items,
    }, open(RAW, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("  解析出 %d 条常量 -> %s" % (len(items), os.path.relpath(RAW, ROOT)))
    print()
    print("  抽查（与本轮目标相关）：")
    want = list(CONST_MAP.values()) + [v[0] for v in CONST_ADD.values()]
    idx = {it["quantity"]: it for it in items}
    for q in want:
        it = idx.get(q)
        if it:
            print("    %-42s %-22s %-12s %s" % (
                q[:42], ("%g" % it["value"])[:22], (it["unit"] or "-")[:12],
                "exact" if it["exact"] else ("±%g" % it["uncertainty"] if it["uncertainty"] else "")))
        else:
            print("    %-42s <未命中>" % q)


def cmd_check(_a):
    raw = load_raw()
    idx = {it["quantity"]: it for it in raw["constants"]}
    nodes = load_graph_nodes()
    byid = {n["id"]: n for n in nodes}
    print("=" * 96)
    print("图谱既有常量 vs CODATA 2022 逐项核校")
    print("=" * 96)
    print("  %-24s %-18s %-18s %-10s %s" % ("图谱 id", "图谱值", "CODATA 2022", "相对误差", "判定"))
    print("  " + "-" * 92)
    bad = 0
    for nid, q in CONST_MAP.items():
        n = byid.get(nid)
        it = idx.get(q)
        if not n:
            print("  %-24s <图谱无此节点>" % nid)
            continue
        gv = (n.get("props") or {}).get("value")
        if it is None:
            print("  %-24s %-18s <CODATA 未命中>" % (nid, str(gv)[:18]))
            continue
        cv = it["value"]
        rd = rel_diff(gv, cv)
        if gv is None:
            verdict = "❌ 空值"
            bad += 1
        elif rd == 0:
            verdict = "✅ 一致"
        elif rd is not None and rd < 1e-8:
            # 差异极小：多为 CODATA 换版（如 ε₀/μ₀ 由 2014/2018 值变为 2022 值）
            verdict = "⚠ 版本差异（补为 2022）"
            bad += 1
        else:
            verdict = "❌ 显著偏差（补为 2022）"
            bad += 1
        print("  %-24s %-18s %-18s %-10s %s" % (
            nid, str(gv)[:18], ("%g" % cv)[:18],
            ("%.2e" % rd) if rd is not None else "-", verdict))
    print("  " + "-" * 92)
    print("  待修正 / 补全：%d 项；另 %d 项 CODATA 常量图谱尚无（拟新增）" % (bad, len(CONST_ADD)))


def cmd_build(_a):
    raw = load_raw()
    idx = {it["quantity"]: it for it in raw["constants"]}
    nodes = load_graph_nodes()
    byid = {n["id"]: n for n in nodes}

    now = datetime.datetime.now().isoformat(timespec="seconds")
    upd_nodes, new_nodes = [], []

    # 1) 修正既有常量节点
    for nid, q in CONST_MAP.items():
        n = byid.get(nid)
        it = idx.get(q)
        if not n or not it:
            continue
        props = dict(n.get("props") or {})
        props.update({
            "value": it["value"],
            "unit": it["unit"],
            "uncertainty": it["uncertainty"],
            "value_exact": it["exact"],
            "value_truncated": it.get("value_truncated", False),
            "value_raw": it["value_raw"],
            "codata_quantity": q,
            "codata_year": it["codata_year"],
            "source": SOURCE_TAG,
            "source_url": SRC_URL,
            "verified_at": now,
        })
        upd_nodes.append({"id": nid, "labels": n.get("labels") or ["Constant", "Entity"], "props": props})

    # 2) 新增图谱缺失的高价值常量
    for nid, (q, sym) in CONST_ADD.items():
        it = idx.get(q)
        if not it:
            print("  [WARN] CODATA 未命中新增量：%s" % q)
            continue
        if nid in byid:
            continue
        new_nodes.append({
            "id": nid, "labels": ["Constant", "Entity"],
            "props": {
                "id": nid, "name": q, "symbol": sym, "ntype": "constant",
                "domain": "physics", "value": it["value"], "unit": it["unit"],
                "uncertainty": it["uncertainty"], "value_exact": it["exact"],
                "value_truncated": it.get("value_truncated", False),
                "value_raw": it["value_raw"], "codata_quantity": q,
                "codata_year": it["codata_year"], "confidence": 0.99,
                "explicit_or_inferred": "explicit", "source": SOURCE_TAG,
                "source_url": SRC_URL, "created_at": now, "verified_at": now,
            },
        })

    # 3) 跨源对齐边：PhysicsBabel 常量符号 == 权威 CODATA 常量（同指一物理量）
    edges = []
    for alias, canon in CONST_ALIAS.items():
        if alias not in byid or canon not in byid:
            print("  [WARN] 别名节点缺失，跳过 same_as：%s -> %s" % (alias, canon))
            continue
        edges.append({
            "id": "same_as|%s|%s" % (alias, canon),
            "source": alias, "target": canon, "type": "same_as",
            "kind": "cross_source_alignment",
            "props": {
                "explicit_or_inferred": "inferred",
                "alignment_source": "codata",
                "method": "quantity_match",
                "codata_quantity": CONST_MAP.get(canon),
                "confidence": 0.95,
                "kind": "cross_source_alignment",
                "note": "PhysicsBabel 常量符号与权威 CODATA 常量同指一物理量",
            },
        })

    # 4) 常量「定义关系 + 单位」边（2026-10-09 连通性审计补齐；消除新常量孤立问题）
    #    没有这一步，新增的 10 个常量就是 deg=0 的死节点。
    n_deriv = n_unit = 0
    for a, b, note in CONST_DERIV:
        if a not in byid or b not in byid:
            print("  [WARN] 常量定义边端点缺失，跳过：%s -> %s" % (a, b))
            continue
        edges.append({
            "id": "derived_from|%s|%s" % (a, b),
            "source": a, "target": b, "type": "derived_from",
            "kind": "constant_derivation",
            "props": {
                "explicit_or_inferred": "explicit", "confidence": 0.99,
                "source": SOURCE_TAG, "relation_source": "CODATA/NIST 标准定义式",
                "definition": note, "kind": "constant_derivation",
                "note": "常量间的定义关系（%s）" % note,
            },
        })
        n_deriv += 1
    for a, b, unit in CONST_UNIT:
        if a not in byid or b not in byid:
            print("  [WARN] 常量单位边端点缺失，跳过：%s -> %s" % (a, b))
            continue
        edges.append({
            "id": "has_unit|%s|%s" % (a, b),
            "source": a, "target": b, "type": "has_unit",
            "kind": "constant_unit",
            "props": {
                "explicit_or_inferred": "explicit", "confidence": 0.99,
                "source": SOURCE_TAG, "unit": unit, "kind": "constant_unit",
                "note": "CODATA 表列出的单位",
            },
        })
        n_unit += 1

    delta = {
        "meta": {
            "phase": "Phase8b-codata",
            "source": "11_真实数据/codata_ingest.py",
            "reason": "NIST CODATA 2022 权威常量真实化：补 unit/uncertainty/出处 + 值修正 + 新增跨学科桥常量 + 跨源对齐",
            "generated_at": now,
        },
        "nodes": upd_nodes + new_nodes,
        "delete_nodes": [],
        "edges": edges,
        "delete_edges": [],
    }
    out = os.path.join(ROOT, "06_PoC", "etl", "neo4j", "phase18b_codata_delta.json")
    json.dump(delta, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("  delta 已写出：%s" % os.path.relpath(out, ROOT))
    print("    修正既有常量 %d 个 / 新增常量 %d 个 / 跨源对齐边 %d 条 / 定义边 %d 条 / 单位边 %d 条"
          % (len(upd_nodes), len(new_nodes), len(edges) - n_deriv - n_unit, n_deriv, n_unit))
    for n in new_nodes:
        print("      + %-26s %-30s %s %s" % (n["id"], n["props"]["name"][:30],
                                            ("%g" % n["props"]["value"])[:14], n["props"]["unit"] or ""))
    for e in edges:
        print("      ~ %s  same_as  %s" % (e["source"], e["target"]))


def main():
    ap = argparse.ArgumentParser(description="NIST CODATA 2022 常量真实化")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fetch", action="store_true", help="拉取并解析 CODATA 表")
    g.add_argument("--check", action="store_true", help="与图谱既有常量核校（只读）")
    g.add_argument("--build", action="store_true", help="产出 delta")
    a = ap.parse_args()
    if a.fetch:
        cmd_fetch(a)
    elif a.check:
        cmd_check(a)
    elif a.build:
        cmd_build(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
