# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""量纲一致性审计器（Phase 40 · R09）。

复评 R09 完成定义：「指数、量纲向量、量名语义分离；**矛盾方程能被检测**」。

旧实现（`dimension_table._solve_from_parquet`）只做「**恰好一个未知量**」的消元：
某个量一旦被某方程求出，**其余方程不会反过来检验它** ⇒ 无法发现矛盾方程
（复评原话：「某个量一旦求出，其他已知量方程不会自动反证它」）。

本器做**一般线性一致性检验**：对方程 Σ exp_i·dim(Q_i) = 0 逐条求残差。

两档口径（各自独立报告，不得混为一谈）
--------------------------------------
* **Tier 1（独立）**：用 `si_base_table`（独立 SI/ISO 80000 编纂）判残差。
  优点：**与被校验对象不同源**（打破同源自洽）；缺点：覆盖有限（基表未收录的量 → 不可判定）。
* **Tier 2（自洽）**：用 `dimension_table`（PhiB 反解表）判残差。
  优点：覆盖全；缺点：与「求解子集」同源 —— 但本档检验的是**求解时未参与的量/方程**，
  属**留出外**自洽（仍有价值，只是证据强度低于 Tier 1，须分列）。

三态（铁律 #19/#30：不可判定 ≠ 假）
    homogeneous    全部量已知且残差为 0
    contradiction  全部量已知但残差非 0 —— **真·矛盾方程**（硬缺陷）
    undetermined   存在量纲未知的量 —— **不可判定**，不计入矛盾，也不计入通过
"""
import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import dimension_table as dt            # noqa: E402
import si_base_table as si              # noqa: E402

PARQUET = os.path.join(HERE, "physicsbabel_circuits.parquet")
DIM = dt.DIM
TOL = 1e-7


def load_equations(parquet_path=PARQUET, max_n=None):
    """从 PhysicsBabel parquet 读出可信方程（keys + 指数表）。

    与 `_solve_from_parquet` 同一取数口径（`plausible==True`，2..6 个参与量，
    指数键集合须与参与量严格一致）——**故意复用同一过滤**，使「矛盾」不会被
    取数差异掩盖。
    """
    import pandas as pd
    df = pd.read_parquet(parquet_path)
    pl = df[df["plausible"] == True]                                   # noqa: E712
    eqs = []
    for _, r in pl.iterrows():
        keys = [k.strip() for k in str(r["keys"]).split(",") if k.strip()]
        if len(keys) < 2 or len(keys) > 6:
            continue
        try:
            exp = json.loads(str(r["exponents"]))
        except Exception:                                              # noqa: BLE001
            continue
        if set(exp) != set(keys):
            continue
        eqs.append((tuple(keys), {k: float(v) for k, v in exp.items()}))
        if max_n and len(eqs) >= max_n:
            break
    return eqs


def _residual(keys, exp, dim_of_fn):
    """返回 (残差向量 | None, 未知量列表)。None 表示不可判定。"""
    acc = collections.defaultdict(float)
    unknown = []
    for k in keys:
        d = dim_of_fn(k)
        if d is None:
            unknown.append(k)
            continue
        e = float(exp.get(k, 0.0))
        for dim, p in d.items():
            acc[dim] += e * p
    if unknown:
        return None, unknown
    return {d: acc.get(d, 0.0) for d in DIM}, []


def check_equations(eqs, dim_of_fn):
    """对每方程判三态，返回汇总 + 矛盾样例（最多留 50 条）。"""
    n_hom = n_con = n_und = 0
    contra = []
    for keys, exp in eqs:
        res, unknown = _residual(keys, exp, dim_of_fn)
        if res is None:
            n_und += 1
            continue
        if all(abs(v) < TOL for v in res.values()):
            n_hom += 1
        else:
            n_con += 1
            if len(contra) < 50:
                contra.append({
                    "keys": list(keys),
                    "exponents": exp,
                    "residual": {d: round(v, 6) for d, v in res.items() if abs(v) > TOL},
                })
    n_total = len(eqs)
    return {
        "n_equations": n_total,
        "homogeneous": n_hom,
        "contradiction": n_con,
        "undetermined": n_und,
        "contradiction_rate_of_decidable": round(n_con / (n_hom + n_con), 6)
        if (n_hom + n_con) else None,
        "examples": contra,
    }


# ---------------------------------------------------------------------------- #
# 非真空自检（铁律 #14/#38/#54）：判据必须真的会红
# ---------------------------------------------------------------------------- #
def selfcheck():
    """合成 3 条方程，断言三态分类正确：
       (1) 齐次（力的定义）→ homogeneous
       (2) 矛盾（力=质量，缺加速度）→ contradiction
       (3) 含未知量 → undetermined
       并把「全部判成齐次」的哑判据暴露出来。
    """
    def dim_of(name):
        return {"force": {"M": 1, "L": 1, "T": -2},
                "mass": {"M": 1},
                "acceleration": {"L": 1, "T": -2}}.get(name)

    eqs = [
        (("force", "mass", "acceleration"), {"force": 1.0, "mass": -1.0, "acceleration": -1.0}),  # 齐次
        (("force", "mass"), {"force": 1.0, "mass": -1.0}),                                        # 矛盾
        (("force", "mystery"), {"force": 1.0, "mystery": -1.0}),                                  # 含未知
    ]
    r = check_equations(eqs, dim_of)
    ok = (r["homogeneous"] == 1 and r["contradiction"] == 1 and r["undetermined"] == 1)
    return {"pass": ok, "result": {k: r[k] for k in
                                   ("n_equations", "homogeneous", "contradiction", "undetermined")}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="输出 JSON 报告到该路径")
    ap.add_argument("--max-eq", type=int, default=None, help="只取前 N 条方程（调试）")
    ap.add_argument("--selfcheck", action="store_true", help="只跑判据非真空自检")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    sc = selfcheck()
    if a.selfcheck:
        print("SELFCHECK", "PASS" if sc["pass"] else "FAIL", json.dumps(sc["result"]))
        sys.exit(0 if sc["pass"] else 1)

    print("[1] 判据非真空自检：%s" % ("PASS" if sc["pass"] else "FAIL"))
    eqs = load_equations(max_n=a.max_eq)
    print("[2] 载入方程 %d 条（PhysicsBabel plausible）" % len(eqs))

    tier1 = check_equations(eqs, si.dim_of)       # 独立 SI 基表
    tier2 = check_equations(eqs, dt.dim_of)       # PhiB 反解表
    print("[3] Tier 1（独立 SI 基表）：齐次 %d / 矛盾 %d / 不可判定 %d（可判定矛盾率 %s）"
          % (tier1["homogeneous"], tier1["contradiction"], tier1["undetermined"],
             tier1["contradiction_rate_of_decidable"]))
    print("[4] Tier 2（PhiB 反解表）：齐次 %d / 矛盾 %d / 不可判定 %d（可判定矛盾率 %s）"
          % (tier2["homogeneous"], tier2["contradiction"], tier2["undetermined"],
             tier2["contradiction_rate_of_decidable"]))

    rep = {
        "schema": "sttp-dim-consistency/v1",
        "source": "11_真实数据/physicsbabel_circuits.parquet（plausible 方程）",
        "tier1_independent_si": tier1,
        "tier2_reverse_solved": tier2,
        "selfcheck": sc,
        "note": "Tier1 与被校验对象不同源（强证据）；Tier2 为留出外自洽（与求解子集同源，须分列）。"
                "三态：不可判定既不计矛盾也不计通过。",
    }
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print("    → %s" % a.json)
    # 独立档若出现矛盾 → 非零退出（这是硬缺陷）；否则 0（含「不可判定」不判错）
    return 1 if tier1["contradiction"] else 0


if __name__ == "__main__":
    sys.exit(main())
