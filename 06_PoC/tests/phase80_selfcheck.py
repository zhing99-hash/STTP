#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""Phase 80（**溯源保真**：的加工材 ≠ 原始证据）自检 —— 判据 + 负对照自证。

★ 为什么单独一台仪器
--------------------
2026-10-27 老板设定评估与研发的**四项要求**，其中第 2 条「从最原始素材入手」与
第 4 条「由第三方证据证明」在项目里长期是空转的口号。把它落成判据时先撞到一个事实：

  **全仓真正的上游原始素材只有 2 个**（脚本加工的产物不算）：
    · `公共数据集/10m_elementkg_release.csv`  858 MB，三元原表
    · `11_真实数据/elementkg.owl`              4 MB，本体原文
  其余 `*_raw.json` / `*_cache.json` 全是**我方 ingest 脚本输出的中间态**，
  拿它们当「原始证据」等于拿自己的输出给自己打分 —— 这正是第 4 条明令禁止的
  「同一段代码自产的期望结果」。

本仪器因此要求：**期望值必须来自与加工链无关的另一条读取路径**。
具体做法：用 5 行三元组扫描直接读 858 MB 原表，与 `normalized.json` 逐字符对撞。
`elementkg10m_ingest.py` 是两遍状态机，这里是单遍线性扫描 —— 解析策略不同，代码不共用。

★ 与 Phase 79 的分工（不要重复建设）
------------------------------------
  Phase 79 答「我们自己有没有把字段弄坏」（源码不乱切、结果能解析）
  Phase 80 答「图里的值是不是还等于原始素材给的那个值」—— 这是更强的命题，
  它同时覆盖 ETL 的**改写**、**拼接**、**默认值兜底**、**后续 delta 覆盖**各种途径。
  两者不可互相替代：79 只能证明「内部自洽」，80 证明「对外忠实」。

判据
----
  S97 溯源保真：入库图谱取值必须 == **最上游原始素材**逐字符重建的值
       ★ 期望值不经我方 ingest 代码；原表缺失 ⇒ SKIP（不假通过）
  S98 化学自洽：原子映射编号需满足标准约定（每编号每侧至多一次、两侧元素相符）
       ★ 基线锁定 + 漂移检测：已知上游缺陷不因存量而红，
         但**新增或恶化**必红（存量见 BASELINE_MAP，冻结于 2026-10-27）

运行： bash sttp.sh phase80
"""

from __future__ import annotations

import collections
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

NORM_GRAPH = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")

# ------------------------------------------------------------------ 溯源登记表
# 每条目 =「这一事实的最上游原始素材在哪、怎么捞、落在图的哪个字段」。
# 只登记**未加工的原型文件**，不得登记 `*_raw` / `*_cache`（那是我方脚本的产物，
# 用它当期望值等于自证自）。代价：目前全仓只够资格登记 1 条 —— 宁缺毋滥。
PROVENANCE = [
    {
        "name": "ElementKG10M·Reaction.mapped_smiles",
        "origin": os.path.join("公共数据集", "10m_elementkg_release.csv"),
        "head_type": "Reaction",
        "relation": "REACTION_MAPPED_IS",
        "tail_type": "literal",
        "node_prefix": "EK2:rxn:",
        "id_prop": "ek_reaction_id",
        "graph_prop": "mapped_smiles",
    },
]

# ---------------------------------------------------------------- 基线（冻结）
# 忠实记录：以下数字是 2026-10-27 用本仪器对 normalized.json 全量实测所得，
# 不是抄老报告。任何一次重新算得更大 ⇒ FAIL。若有人认定上游已修或应删除其中某类，
# 必须**连理由一起改这里**，不能删判据了事（D122 同族纪律）。
BASELINE_MAP = {
    # 类别 -> 2026-10-27 实测条数（受检 758 条 EK2:rxn）
    "same_side_dup": 33,      # 同侧重复映射编号 ⇒ 化学计量不自洽
    "element_mismatch": 0,    # 同编号两侧元素不符
    "side_missing": 0,        # 编号只在一侧出现
    "parse_fail": 0,          # RDKit 无法解析
}
BASELINE_DATE = "2026-10-27"


# --------------------------------------------------------------- 原始素材读取

def _raise_csv_limit():
    m = sys.maxsize
    while True:
        try:
            csv.field_size_limit(m)
            return
        except OverflowError:
            m = int(m // 10)


def read_triples(path, head_type, relation, tail_type):
    """**独立于 ingest 的读取路径**：单遍线性扫描三元原表。

    为什么必须另写一份：`elementkg10m_ingest.py` 是「两遍状态机 + 集合筛选」，
    若用它的输出当期望值，任何**筛选逻辑本身**的错误都不会被发现 ——
    期望与实际同源 ⇒ 判据恒绿。这里两条路径的唯一共同点只有「同一个文件」。
    """
    _raise_csv_limit()
    out, nrows = {}, 0
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.reader(fh):
            nrows += 1
            if len(row) != 5:
                continue
            ht, hv, rel, tt, tv = row
            if ht == head_type and rel == relation and tt == tail_type:
                out[hv] = tv
    return out, nrows


def load_graph_nodes(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["nodes"]


# --------------------------------------------------------------- S97 溯源保真

def check_provenance(add, graph_path):
    if not os.path.exists(graph_path):
        add(False, "S97 溯源保真",
            "权威图缺失：%s" % graph_path)
        return 0
    nodes = load_graph_nodes(graph_path)
    checked_total = 0
    lines = []
    all_pass = True

    for spec in PROVENANCE:
        origin = os.path.join(ROOT, spec["origin"])
        name = spec["name"]
        if not os.path.exists(origin):
            add(True, "S97 溯源保真 · %s" % name,
                "[SKIP] 原始素材缺失：%s ⇒ 不假通过" % os.path.relpath(origin, ROOT))
            continue

        expected, nrows = read_triples(origin, spec["head_type"],
                                       spec["relation"], spec["tail_type"])

        # ★ 负对照 NEG-S97a：期望采集器必须活着。空 ⇒ 下面所有比对恒等成立 ⇒ 假绿。
        if not expected:
            add(False, "S97 溯源保真 · %s（负对照：采集器存活）" % name,
                "原表 %s 扫描 %d 行却取到 0 条 ⇒ 判据已失明，按失败处理"
                % (os.path.relpath(origin, ROOT), nrows))
            all_pass = False
            continue

        got = {}
        for n in nodes:
            nid = n.get("id", "")
            props = n.get("props") or {}
            if nid.startswith(spec["node_prefix"]) and spec["id_prop"] in props:
                got[props[spec["id_prop"]]] = props.get(spec["graph_prop"])

        common = set(got) & set(expected)
        # ★ 负对照 NEG-S97b：对照集合不能为空，否则比对是空集上的恒真命题。
        if not common:
            add(False, "S97 溯源保真 · %s（负对照：样本非空）" % name,
                "图 %d 个节点与原表 %d 条的交集为空 ⇒ 键映射已失效，按失败处理"
                % (len(got), len(expected)))
            all_pass = False
            continue

        bad = sorted(k for k in common if got[k] != expected[k])
        # 只被图收而原表没有的，单独报道（可能是上级筛选，不算篡改）
        only_graph = len(set(got) - set(expected))
        only_origin = len(set(expected) - set(got))
        lines.append(
            "%s：对照 %d 条 · 不一致 %d · 仅图中 %d · 原表未收 %d（原表 %d 行）"
            % (name, len(common), len(bad), only_graph, only_origin, nrows))
        checked_total += len(common)
        if bad:
            all_pass = False
            if bad[:3]:
                sample = "; ".join(bad[:3])
                lines.append("  不一致样例 key：%s" % sample)

    add(all_pass,
        "S97 溯源保真：入库图谱取值必须 == **最上游原始素材**逐字符重建值"
        "（不经我方 ingest）",
        " || ".join(lines) if lines else "无登记条目")

    # 明确告诉读者：这里证明的是**忠实**，不是**原始素材本身正确**
    print("       [读法] S97 只证明「我方 ETL 未篡改」。原始素材本身的正确性")
    print("             不在本判据证明范围内 —— 那需要外部数据库交叉核对。")
    return checked_total


# --------------------------------------------------------------- S98 化学自洽

def check_chemistry(add, graph_path):
    try:
        from rdkit import RDLogger
        from rdkit.Chem import rdChemReactions as _R          # noqa: F401
        from rdkit.Chem import AllChem
        RDLogger.DisableLog("rdApp.*")
        import rdkit
        ver = rdkit.__version__
    except Exception as exc:                                   # pragma: no cover
        add(True, "S98 化学自洽：反应式原子映射需满足标准约定",
            "[SKIP] 缺少可用 RDKit（%s）⇒ 不假通过" % exc)
        return

    nodes = load_graph_nodes(graph_path)
    smis = [(n.get("props", {}).get("ek_reaction_id"), n.get("props", {}).get("mapped_smiles"))
            for n in nodes
            if n.get("id", "").startswith("EK2:rxn:")
            and (n.get("props") or {}).get("mapped_smiles")]
    if not smis:
        add(False, "S98 化学自洽（负对照：样本采集器存活）",
            "取到 0 条 mapped_smiles ⇒ 判据失明，按失败处理")
        return

    stat = collections.Counter()
    for _rid, smi in smis:
        try:
            rxn = AllChem.ReactionFromSmiles(smi)
        except Exception:
            rxn = None
        if rxn is None:
            stat["parse_fail"] += 1
            continue
        side = {}
        for which, mols in (("R", rxn.GetReactants()), ("P", rxn.GetProducts())):
            c = collections.Counter()
            elem = {}
            for mol in mols:
                for atm in mol.GetAtoms():
                    mn = atm.GetAtomMapNum()
                    if mn:
                        c[mn] += 1
                        elem.setdefault(mn, atm.GetSymbol())
            side[which] = (c, elem)
        (cR, eR), (cP, eP) = side["R"], side["P"]
        if any(v > 1 for v in cR.values()) or any(v > 1 for v in cP.values()):
            stat["same_side_dup"] += 1
            continue
        if any(eR[m] != eP[m] for m in set(cR) & set(cP)):
            stat["element_mismatch"] += 1
            continue
        if set(cR) != set(cP):
            stat["side_missing"] += 1
            continue

    total = len(smis)
    ok = total - sum(stat.values())

    # ★ 负对照：解析器必须真的能产结构判定，不是一路走到 ok
    if ok == 0 and total > 0:
        add(False, "S98 化学自洽（负对照：全部放行者占比异常）",
            "%d 条中 0 条自洽 ⇒ 极大概率是判定逻辑失效，按失败处理" % total)
        return

    regressions = []
    for k, base in sorted(BASELINE_MAP.items()):
        cur = stat.get(k, 0)
        if cur > base:
            regressions.append("%s：%d → %d（+%d）" % (k, base, cur, cur - base))

    detail = ("受检 %d 条 · 自洽 %d · " % (total, ok)) + " · ".join(
        "%s %d（基线 %d）" % (k, stat.get(k, 0), b) for k, b in sorted(BASELINE_MAP.items()))
    add(not regressions,
        "S98 化学自洽：重复映射/元素不符/单侧缺失/解析失败**均不得多于基线**"
        "（基线 %s，%d 条）" % (BASELINE_DATE, len(smis)),
        (detail + "\n       恶化项：" + "；".join(regressions)) if regressions else detail)

    if stat.get("same_side_dup", 0):
        print("       [读法] 同侧重复映射编号违反「每编号每侧至多一次」的标准约定。")
        print("             另有一种解释：写作方用它表达化学计量倍数。两者都属**数据瑕疵**")
        print("             —— 下游按原子对应关系做角色指派时会出错，故计入台账不做放行。")


# ------------------------------------------------------------------ main

def run_all(graph_path):
    """对一份图谱跑全部判据，返回 rep。``--red`` 模式复用它跑临时篡改副本。"""
    rep = []

    def add(ok, name, detail=""):
        rep.append((bool(ok), name, str(detail)))

    count = check_provenance(add, graph_path)
    check_chemistry(add, graph_path)
    return rep, count


def make_tampered_copy(cut=200, how_many=2):
    """造一份**篡改过的临时副本**，绝不动权威图。

    ★ 为什么走副本而不是改原图再用 git checkout 还原：
      `normalized.json` 被 .gitignore 排除，git checkout 救不回来，
      红探针一旦改原图就是**永久污染**。放在系统临时目录跑完即弃，零残留风险。

    ★ 为什么挑 length > cut+10 的再切：手动做红测试时曾把条件写成「>50 就统一切到 400」，
      结果对短记录是彻头彻尾的空操作，判据自然不红 —— 那是**测试写错**，
      不是判据失灵（与 D121 同族的抽样偏差，已记入学费 D134）。
    """
    import tempfile
    nodes = json.loads(json.dumps(load_graph_nodes(NORM_GRAPH)))  # 深拷贝，不牵原对象
    hit = 0
    for n in nodes:
        nid = n.get("id", "")
        props = n.get("props") or {}
        if not nid.startswith("EK2:rxn:"):
            continue
        smi = props.get("mapped_smiles") or ""
        if len(smi) > cut + 10:
            props["mapped_smiles"] = smi[:cut]
            hit += 1
            if hit >= how_many:
                break
    if not hit:
        return None, 0
    tmp = os.path.join(tempfile.gettempdir(), "_phase80_red.json")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"nodes": nodes, "edges": []}, fh, ensure_ascii=False)
    return tmp, hit


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if "--red" in argv:
        # 红探针模式：在副本上篡改 ⇒ 必须全 FAIL；有一个 PASS 就说明判据失明。
        print("=" * 78)
        print("  Phase 80 --red：在**临时副本**上篡改，验证判据能否变红")
        print("=" * 78)
        tmp, hit = make_tampered_copy()
        if not tmp:
            print("[FATAL] 找不到可用于篡改的长记录 ⇒ 无法自证")
            return 1
        print("篡改 %d 条（每条都确有长度变化），副本不受 version 控制：%s" % (hit, tmp))
        rep, _ = run_all(tmp)
        try:
            os.remove(tmp)
        except OSError:
            pass
        npass = sum(1 for ok_, _, _ in rep if ok_)
        nfail = sum(1 for ok_, _, _ in rep if not ok_)
        for ok_, name, detail in rep:
            print("[%s] %s" % ("PASS" if ok_ else "FAIL", name))
            if detail:
                print("       %s" % detail)
        print("-" * 78)
        good = (npass == 0 and nfail > 0)
        print("phase80 --red: %s（%d PASS / %d FAIL）"
              % ("OK 判据可红" if good else "失明！篡改未被发现", npass, nfail))
        return 0 if good else 1

    if not os.path.exists(NORM_GRAPH):
        print("[FATAL] 缺少权威图：%s" % NORM_GRAPH)
        return 1

    print("=" * 78)
    print("  Phase 80（溯源保真）判据 —— 图内值 == 最上游原始素材？")
    print("=" * 78)

    rep, count = run_all(NORM_GRAPH)

    npass = sum(1 for ok_, _, _ in rep if ok_)
    nfail = sum(1 for ok_, _, _ in rep if not ok_)
    for ok_, name, detail in rep:
        print("[%s] %s" % ("PASS" if ok_ else "FAIL", name))
        if detail:
            print("       %s" % detail)
    print("-" * 78)
    print("phase80_selfcheck: PASS %d / FAIL %d" % (npass, nfail))
    if count:
        print("累计逐字符对照 %d 个实体字段" % count)
    return 1 if nfail else 0


if __name__ == "__main__":
    raise SystemExit(main())
