# -*- coding: utf-8 -*-
"""Phase 7i — 数论垂直切片（路线图 Phase 7 清单中最后一个数学子领域）。

覆盖：同余 / 模运算 / 欧拉定理 / 费马小定理 / 中国剩余定理 / 威尔逊定理 /
算数基本定理 / 欧几里得算法 / Basel 问题（Σ1/n²=π²/6）/ 模 n 单位群。

跨学科桥（本项目北极星是「跨学科」，故不只做孤立切片）：
  * 数论 ↔ 代数：大量共用符号 a/n，并锚到 MA 切片
  * 数论 ↔ 几何/代数：Basel 问题的结果含 π —— 同时接到 MA:sy:pi 与 MG:sy:pi
  * 数论 ↔ 数学史：欧拉定理 ↔ MA:fo:euler_identity（同以欧拉命名）

类型上使用了图 Schema v0.1 已声明的 `MathConcept`（graph_export.TYPE_PRIORITY 与
graph_view.TYPE_STYLE 均已支持，零 schema 改动）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_common as sc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "seed_math_numbertheory.json")
nodes, edges = [], []

# --------------------------------------------------------------------------
# 公式
# --------------------------------------------------------------------------
nodes += [
    sc.n("NT:fo:congruence", "formula", "Congruence modulo n",
         "math.numbertheory", latex=r"a\equiv b\pmod{n}\iff n\mid(a-b)",
         informal="同余式定义：a、b 之差被 n 整除。"),
    sc.n("NT:fo:euler_theorem", "formula", "Euler's theorem",
         "math.numbertheory", latex=r"a^{\varphi(n)}\equiv 1\pmod{n}\quad(\gcd(a,n)=1)",
         informal="若 a 与 n 互质，则 a 的 φ(n) 次幂模 n 余 1。"),
    sc.n("NT:fo:fermat_little", "formula", "Fermat's little theorem",
         "math.numbertheory", latex=r"a^{p-1}\equiv 1\pmod{p}",
         informal="欧拉定理在 n 为素数时的特例（φ(p)=p−1）。"),
    sc.n("NT:fo:crt", "formula", "Chinese remainder theorem",
         "math.numbertheory",
         latex=r"x\equiv a_i\pmod{n_i}\ \Rightarrow\ \exists!\,x\ \big(\mathrm{mod}\ \textstyle\prod_i n_i\big)",
         informal="两两互质的模下，同余方程组有唯一解。"),
    sc.n("NT:fo:wilson", "formula", "Wilson's theorem",
         "math.numbertheory", latex=r"(p-1)!\equiv-1\pmod{p}",
         informal="p 为素数的充要条件：(p−1)! ≡ −1 (mod p)。"),
    sc.n("NT:fo:fundamental_arithmetic", "formula", "Fundamental theorem of arithmetic",
         "math.numbertheory", latex=r"n=\prod_{i}p_i^{e_i}",
         informal="任一大于 1 的整数可唯一分解为素数幂之积。"),
    sc.n("NT:fo:euclid_algorithm", "formula", "Euclidean algorithm",
         "math.numbertheory", latex=r"\gcd(a,b)=\gcd(b,\ a\bmod b)",
         informal="辗转相除求最大公因数。"),
    sc.n("NT:fo:basel", "formula", "Basel problem",
         "math.numbertheory", latex=r"\sum_{n=1}^{\infty}\frac{1}{n^{2}}=\frac{\pi^{2}}{6}",
         informal="欧拉解决的级数求和，把数论/分析结果与 π 联系起来。"),
    sc.n("NT:fo:unit_group", "formula", "Multiplicative group of integers modulo n",
         "math.numbertheory",
         latex=r"(\mathbb{Z}/n\mathbb{Z})^{\times}=\{a\bmod n:\gcd(a,n)=1\}",
         informal="模 n 的单位构成的乘法群，阶为 φ(n)，是欧拉定理的群论表述。"),
]

# --------------------------------------------------------------------------
# 概念（MathConcept）
# --------------------------------------------------------------------------
nodes += [
    sc.n("NT:mc:congruence", "mathconcept", "Congruence modulo n",
         "math.numbertheory", informal="整数上的等价关系。"),
    sc.n("NT:mc:modular_arithmetic", "mathconcept", "Modular arithmetic",
         "math.numbertheory", informal="模运算（有限域/环上的算术）。"),
    sc.n("NT:mc:totient", "mathconcept", "Euler's totient function",
         "math.numbertheory", informal="φ(n) 为小于 n 且与 n 互质的正整数个数。"),
    sc.n("NT:mc:prime", "mathconcept", "Prime number",
         "math.numbertheory", informal="大于 1 且仅被 1 与自身整除的整数。"),
    sc.n("NT:mc:gcd", "mathconcept", "Greatest common divisor",
         "math.numbertheory", informal="两整数最大的公共因数。"),
]

# --------------------------------------------------------------------------
# 符号
# --------------------------------------------------------------------------
nodes += [
    sc.n("NT:sy:a", "symbol", "a (integer)", "math.symbol", latex="a"),
    sc.n("NT:sy:n", "symbol", "n (modulus)", "math.symbol", latex="n"),
    sc.n("NT:sy:p", "symbol", "p (prime)", "math.symbol", latex="p"),
    sc.n("NT:sy:phi", "symbol", "φ(n) (totient)", "math.symbol", latex=r"\varphi(n)"),
    sc.n("NT:sy:cong", "symbol", "≡ (congruence)", "math.symbol", latex=r"\equiv"),
    sc.n("NT:sy:Z", "symbol", "ℤ (integers)", "math.symbol", latex=r"\mathbb{Z}"),
]

# --------------------------------------------------------------------------
# 边：公式 -> 符号
# --------------------------------------------------------------------------
SYM = {
    "NT:fo:congruence": ["NT:sy:a", "NT:sy:n", "NT:sy:cong", "MA:sy:a", "MA:sy:n"],
    "NT:fo:euler_theorem": ["NT:sy:a", "NT:sy:n", "NT:sy:phi", "NT:sy:cong", "MA:sy:a", "MA:sy:n"],
    "NT:fo:fermat_little": ["NT:sy:a", "NT:sy:p", "NT:sy:cong", "MA:sy:a"],
    "NT:fo:crt": ["NT:sy:n", "NT:sy:cong", "MA:sy:n"],
    "NT:fo:wilson": ["NT:sy:p", "NT:sy:cong"],
    "NT:fo:fundamental_arithmetic": ["NT:sy:n", "NT:sy:p"],
    "NT:fo:euclid_algorithm": ["NT:sy:a", "MA:sy:b", "NT:sy:n"],
    # 跨域：Basel 问题结果含 π —— 同时锚到代数与几何两个切片的 π 符号
    "NT:fo:basel": ["MA:sy:pi", "MG:sy:pi", "MX:sym:pi"],
    "NT:fo:unit_group": ["NT:sy:a", "NT:sy:n", "NT:sy:Z"],
}
for fo, syms in SYM.items():
    for s in syms:
        edges.append(sc.e(fo, s, "has_symbol", "formula_symbol"))

# 公式 -> 概念（defines）
for fo, mc in [
    ("NT:fo:congruence", "NT:mc:congruence"),
    ("NT:fo:euler_theorem", "NT:mc:totient"),
    ("NT:fo:fundamental_arithmetic", "NT:mc:prime"),
    ("NT:fo:euclid_algorithm", "NT:mc:gcd"),
    ("NT:fo:unit_group", "NT:mc:modular_arithmetic"),
]:
    edges.append(sc.e(fo, mc, "defines", "formula_concept"))

# 推导 / 关联
edges += [
    sc.e("NT:fo:fermat_little", "NT:fo:euler_theorem", "derived_from", "special_case",
         note="φ(p)=p−1，故费马小定理是欧拉定理的特例"),
    sc.e("NT:fo:euler_theorem", "NT:fo:congruence", "derived_from", "built_on_congruence"),
    sc.e("NT:fo:crt", "NT:fo:congruence", "derived_from", "built_on_congruence"),
    sc.e("NT:fo:wilson", "NT:fo:congruence", "derived_from", "built_on_congruence"),
    sc.e("NT:fo:unit_group", "NT:fo:euler_theorem", "derived_from", "group_form_of_euler"),
    sc.e("NT:mc:modular_arithmetic", "NT:mc:congruence", "derived_from", "built_on_congruence"),
    sc.e("NT:mc:totient", "NT:mc:prime", "related_to", "totient_of_prime"),
    sc.e("NT:mc:gcd", "NT:fo:fundamental_arithmetic", "related_to", "coprime_factorization"),
    # 数学史桥：同以欧拉命名
    sc.e("NT:fo:euler_theorem", "MA:fo:euler_identity", "related_to", "eponym_euler",
         note="两者均冠名欧拉；此边表达跨子领域的命名/历史关联"),
]

EXTERNAL = {
    "MA:sy:a", "MA:sy:b", "MA:sy:n", "MA:sy:pi", "MG:sy:pi", "MX:sym:pi",
    "MA:fo:euler_identity",
}

if __name__ == "__main__":
    sc.build_and_write(OUT, "7i", "Number theory vertical slice", nodes, edges,
                       extra_external=EXTERNAL)
