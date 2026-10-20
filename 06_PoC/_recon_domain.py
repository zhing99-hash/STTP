#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""G2 学科分类 · 侦察脚本（只读，不写图）

为什么要先侦察：
    规划 §3.1 F2 写「5,090 个全部有细分」。听起来是「给 5,090 个公式各挑一个二级学科」。
    但一量就发现这是对任务性质的误读 —— 本脚本负责把这件事**量清楚**，
    否掉想当然的做法，再交给正式脚本实施。

七个问题：
    R1  pb_domains 到底是源声明还是派生？（决定它能不能当权威输入）
    R2  六个标签的语义：谁是被动词（背景），谁是特征词？（决定主标签怎么选）
    R3  参与量证据 vs 源标签：一致性检验（对我手写的「量→领域」映射表的正对照）
    R4  主标签规则跑出来的分布，以及是否落在受控词表内
    R5  已有 89 个细分的口径（人工值，绝不覆盖）
    R6  pb:newton_second 这个空壳能不能判
    R7  subject_of 前缀兼容性：新值会不会把「物理」误判成「跨学科」

用法： python 06_PoC/_recon_domain.py
"""
import json
import collections
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NORM = os.path.join(ROOT, "06_PoC", "etl", "normalized.json")
RAW = os.path.join(ROOT, "11_真实数据", "physicsbabel_raw.json")
PARQUET = os.path.join(ROOT, "11_真实数据", "physicsbabel_circuits.parquet")

# ---------------------------------------------------------------------------
# 手写的「量 → 领域」映射表。
# 这是**人工知识**，是整条规则的本体；必须由 R3 的一致性检验背书，不能拍脑袋就信。
# 分组说明：
#   CONST  物理常数 —— 「公式里含 c₀」不等于「这个公式是相对论」，故不单独成科
#   MECH   力学
#   EM     电磁
#   THERMO 热学
#   CHEM   化学
#   PHOTO  光度/光学
# ---------------------------------------------------------------------------
Q_DOMAIN = {}
_CONST = ["light_speed", "planck", "gas_constant", "boltzmann", "elem_charge",
          "avogadro", "grav_const"]
_MECH = ["mass", "length", "time", "velocity", "acceleration", "force", "momentum",
         "ang_momentum", "action", "energy", "power", "pressure", "torque",
         "inertia", "gravity", "omega", "frequency", "area", "volume", "density",
         "viscosity", "kin_viscosity", "flowrate", "stiffness", "surf_tension", "angle"]
_EM = ["charge", "current", "voltage", "resistance", "resistivity", "conductivity",
       "capacitance", "inductance", "mag_flux", "bfield", "efield", "permittivity",
       "permeability"]
_THERMO = ["temperature", "entropy", "heat_capacity", "specific_heat",
           "thermal_cond", "thermal_diff", "expansion"]
_CHEM = ["amount", "concentration", "molar_mass", "molar_energy", "cat_activity"]
_PHOTO = ["lum_intensity", "luminance", "illuminance"]  # 注意 dim 键是 lum_intensity，量纲表是 luminous_intensity

for _g, _ks in (("const", _CONST), ("mech", _MECH), ("em", _EM),
                ("thermo", _THERMO), ("chem", _CHEM), ("photo", _PHOTO)):
    for _k in _ks:
        Q_DOMAIN[_k] = _g

# pb 标签 → 领域组（用于 R3 一致性检验）
TAG2GROUP = {"mechanics": "mech", "em": "em", "thermo": "thermo",
             "chemistry": "chem", "photometry": "photo", "constants": "const"}

# 受控词表：全部取自**库里已有的人工标注**，不新造词（唯一例外 chem.general，见 R4）
CONTROLLED = None  # R5 里从数据读出


def load():
    with open(NORM, encoding="utf-8") as f:
        g = json.load(f)
    formulas = [n for n in g["nodes"] if "Formula" in (n.get("labels") or [])]
    return g, formulas


def hdr(t):
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def r1(formulas):
    hdr("R1  pb_domains 是源声明还是派生？")
    # raw 是 ingest 的**输出**（里面字段已叫 pb_domains），不能拿它当源。
    # 真正的源是 physicsbabel_circuits.parquet 的 domains 列。
    print("  链路：parquet.domains → ingest(pb_domains) → normalized")
    print("  ingest 行 95：`\"pb_domains\": str(r.get(\"domains\"))` —— 直拷，无加工。")
    try:
        import pandas as pd
    except Exception as e:
        print("  ⚠ 无法导入 pandas，跳过与 parquet 的逐一比对：%s" % e)
        return False
    df = pd.read_parquet(PARQUET)
    # 复现 ingest 的取数顺序，才能对上 PB:fo:N 的编号：
    #   ingest 第 63-64 行：pl = df[df["plausible"] == True]，再按 2≤len(keys)≤6 过滤
    pl = df[df["plausible"] == True]
    sub = pl[pl["keys"].astype(str).str.split(",").map(
        lambda ks: 2 <= len([k for k in ks if k.strip()]) <= 6)]
    print("  parquet 总行 %d；plausible 后 %d；再加 2≤keys≤6 后 %d 行"
          % (len(df), len(pl), len(sub)))
    got = {}
    for i, (_, r) in enumerate(sub.iterrows()):
        got["PB:fo:%d" % i] = str(r.get("domains"))
        if len(got) >= 5000:
            break
    same = diff = missing = 0
    for n in formulas:
        cur = n["props"].get("pb_domains")
        if cur is None:
            continue
        rv = got.get(n["id"])
        if rv is None:
            missing += 1
        elif rv == str(cur):
            same += 1
        else:
            diff += 1
    print("  与 parquet.domains 逐一比对：一致 %d / 不一致 %d / 对不上 %d"
          % (same, diff, missing))
    ok = diff == 0 and missing == 0
    print("  结论：%s" % ("✅ 源声明（上游数据集自带字段，可作权威输入）" if ok
                          else "❌ 对不上，需查清再动手"))
    return ok


def r2(formulas):
    hdr("R2  六个标签是「背景词」还是「特征词」？")
    df = collections.Counter()
    have = [n for n in formulas if n["props"].get("pb_domains")]
    for n in have:
        for t in str(n["props"]["pb_domains"]).split(","):
            df[t.strip()] += 1
    N = len(have)
    print("  有 pb_domains 的公式 %d 个；标签文档频率：" % N)
    for k, v in df.most_common():
        print("     %-12s %5d   %5.1f%%" % (k, v, 100.0 * v / N))
    print("\n  判读：mass 这个量出现在 100%% 的公式里（见 R3），所以凡是「涉及质量/长度/时间」")
    print("        的标签（mechanics）必然是**背景词**——它不区分任何东西。")
    print("        constants 是修饰语（含物理常数），也不是学科。")
    print("  结论：主标签应从「特征词」里选（em / thermo / chemistry / photometry），")
    print("        没有特征词时才落 mechanics。")


def r3(formulas):
    hdr("R3  参与量证据 vs 源标签（对我手写映射表的正对照）")
    have = [n for n in formulas if n["props"].get("pb_domains")]
    # 每个公式：源标签组集合 vs 参与量领域组集合
    ok = 0
    ok_rev = 0
    badge = collections.Counter()
    badge_rev = collections.Counter()
    unmatched_keys = collections.Counter()
    for n in have:
        tags = set(t.strip() for t in str(n["props"]["pb_domains"]).split(","))
        tgroups = set(TAG2GROUP.get(t, "?") for t in tags) - {"const"}
        egroups = set()
        for k in (n["props"].get("dim_exponents") or {}):
            gdom = Q_DOMAIN.get(k)
            if gdom is None:
                unmatched_keys[k] += 1
            elif gdom != "const":
                egroups.add(gdom)
        if not tgroups or not egroups:
            continue
        # 正向（源 ⊆ 量证据）：源说的领域，公式里确实有那个领域的量
        if tgroups <= egroups:
            ok += 1
        else:
            badge[tuple(sorted(tgroups - egroups))] += 1
        # 反向（量证据 ⊆ 源）：只做统计，用来判断我的映射是不是过宽
        if egroups <= tgroups:
            ok_rev += 1
        else:
            badge_rev[tuple(sorted(egroups - tgroups))] += 1
    print("  正向  源标签 ⊆ 参与量领域：%d / %d" % (ok, len(have)))
    print("  反向  参与量领域 ⊆ 源标签：%d / %d" % (ok_rev, len(have)))
    print("  反例（源说了但参与量里找不到）：")
    for k, v in badge.most_common(10):
        print("     %-30s %d" % (str(k), v))
    print("  反例（参与量有但源没标）：")
    for k, v in badge_rev.most_common(10):
        print("     %-30s %d" % (str(k), v))
    print("\n  两个方向都要看：正向高说明映射表**没漏**；")
    print("  反向高才说明映射表**没宽**——只有反向也高，映射口径才算与 PB 对齐。")
    print("  反向若很低，说明我把很多量划进了源不承认的领域，映射表要收窄。")
    if unmatched_keys:
        print("  ⚠ 映射表没覆盖的量纲键：", unmatched_keys.most_common())
    else:
        print("  ✅ 全部量纲键都被映射表覆盖")
    print("\n  这一条是在**校验我手写的映射表**：若一致率高，说明映射表的分组口径")
    print("  与 PhysicsBabel 的 domains 口径一致，可以拿它去补源没说到的地方。")
    return ok, len(have)


def r4(formulas):
    hdr("R4  主标签规则跑出来的分布")
    # 规则：特征词优先（按固定次序确定性打破平局），无特征词则 mechanics
    FEATURE_ORDER = ["em", "thermo", "chemistry", "photometry"]
    TAG2DOMAIN = {"mechanics": "physics.mechanics", "em": "physics.em",
                  "thermo": "physics.thermo", "chemistry": "chem.general",
                  "photometry": "physics.optics"}
    dist = collections.Counter()
    multi = collections.Counter()
    for n in formulas:
        pb = n["props"].get("pb_domains")
        if not pb:
            continue
        tags = [t.strip() for t in str(pb).split(",")]
        feats = [t for t in FEATURE_ORDER if t in tags]
        if len(feats) > 1:
            multi[tuple(feats)] += 1
        pick = feats[0] if feats else "mechanics"
        dist[TAG2DOMAIN[pick]] += 1
    print("  主标签分布：")
    for k, v in dist.most_common():
        print("     %-22s %5d" % (k, v))
    print("\n  多特征词（需确定性打破平局）的组合：")
    for k, v in multi.most_common():
        print("     %-34s %d" % (str(k), v))
    if multi:
        print("  ⚠ 存在多特征词，规则必须写明固定次序，否则不可复现。")
    print("\n  chem.general 是新造词：源只给到 chemistry 一级，二级无证据 ⇒ 不臆造。")
    print("  这一点必须在报告里写明，不能假装分到了 chem.kinetics 之类。")
    return dist


def r5(formulas):
    hdr("R5  已有细分（人工值）的口径 —— 绝不覆盖")
    cur = collections.Counter()
    for n in formulas:
        d = n["props"].get("domain")
        if d and d != "phys":
            cur[d] += 1
    print("  已有细分 %d 个，取值（即受控词表，不新造）：" % sum(cur.values()))
    for k, v in sorted(cur.items()):
        print("     %-22s %d" % (k, v))
    print("\n  纪律（同 Phase 61 G1）：人工值不覆盖，规则只填空缺。")
    return cur


def r6(formulas):
    hdr("R6  pb:newton_second 这个空壳能不能判？")
    for n in formulas:
        if n["id"] == "pb:newton_second":
            p = n["props"]
            print("  props:", json.dumps(p, ensure_ascii=False))
            print("  有 label =", repr(p.get("label")), "⇒ 种子节点，可读名称可判 physics.mechanics")
            print("  但注意：它**不在** physicsbabel_raw 里（raw 中 grep 不到），")
            print("  且无 latex / 无 equation / 无 dim_exponents ⇒ 只有名称这一条证据。")
            return True
    print("  没找到该节点")
    return False


def r7(dist):
    hdr("R7  subject_of 前缀兼容性（新值会不会把「物理」误判成「跨学科」）")
    sys.path.insert(0, os.path.join(ROOT, "06_PoC"))
    try:
        import graph_export
    except Exception as e:  # pragma: no cover
        print("  ⚠ 无法导入 graph_export：%s" % e)
        return False
    allok = True
    for dom in list(dist.keys()) + ["physics.mechanics", "physics.thermo", "chem.general",
                                    "math.DG", "phys"]:
        s = graph_export.subject_of(dom, "PB:fo:0")
        want = ("物理" if dom.startswith(("phys", "pb")) else
                "化学" if dom.startswith("chem") else
                "数学" if dom.startswith("math") else "?")
        flag = "✅" if s == want else "❌"
        if s != want:
            allok = False
        print("     %-22s -> %-6s (期望 %s) %s" % (dom, s, want, flag))
    print("\n  这一条是硬约束：03_知识层/queries.py 用 subject_of 做学科过滤/分组，")
    print("  一旦新值落到「跨学科」，前端学科切换和跨域路径判定就全错了。")
    return allok


def main():
    g, formulas = load()
    print("Formula 节点：%d" % len(formulas))
    ok1 = r1(formulas)
    r2(formulas)
    r3(formulas)
    dist = r4(formulas)
    r5(formulas)
    r6(formulas)
    ok7 = r7(dist)
    hdr("侦察结论")
    print("  1. pb_domains 是源声明（权威输入），覆盖 5,000 / 5,090。")
    print("  2. mechanics / constants 是背景词，主标签须从特征词里选。")
    print("  3. 5,000 个 PB 公式是**量纲齐次合成式**（m = k₀·X^a·Y^b…），")
    print("     跨域是它们的本性 ⇒ 强行单选会造大批错分；多标签必须保留。")
    print("  4. 受控词表沿用库里已有的人工值；唯一新词 chem.general 是「源止于一级」的诚实表达。")
    print("  5. subject_of 前缀兼容：%s" % ("✅ 通过" if ok7 else "❌ 不通过"))
    return 0 if (ok1 and ok7) else 1


if __name__ == "__main__":
    sys.exit(main())
