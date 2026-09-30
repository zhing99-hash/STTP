# -*- coding: utf-8 -*-
"""
公式知识图谱 —— 三道验证关卡 PoC 演示脚本
=============================================
演示化学配平（RDKit）、物理量纲（pint）、数学符号（SymPy）三类校验。

运行方法：
    python validate_demo.py

依赖（自动检测，缺库时给出友好提示）：
    pip install rdkit-pypi sympy pint

小样例：
  化学：碳燃烧（C + O2 → CO2，配平 vs 未配平）
  物理：F = ma（齐次）vs F = mv（量纲不一致）
  数学：(x+1)^2 = x^2 + 2x + 1（成立）vs (x+1)^2 = x^2 + 2x + 2（不成立）
"""

import sys
import traceback
import io

# Windows 控制台默认 GBK，强制 UTF-8 输出
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
from typing import Tuple, Optional, Any

# =============================================================================
# 第 0 步：依赖检测与 Banner
# =============================================================================

DEPENDENCIES = {
    "sympy": None,
    "pint":  None,
    "rdkit": None,
}

def check_dependencies():
    """检测所需依赖，缺失时记录（不崩溃）。"""
    deps_found = {}
    for name, module_name in [("sympy", "sympy"), ("pint", "pint"), ("rdkit", "rdkit")]:
        try:
            mod = __import__(module_name)
            deps_found[name] = getattr(mod, "__version__", "installed")
        except ImportError:
            deps_found[name] = None
    return deps_found


DEPS = check_dependencies()


def banner():
    sep = "=" * 66
    print(f"\n{sep}")
    print(f"  公式知识图谱 — 三道验证关卡 PoC 演示")
    print(f"  验证与推理专家 | 2026-09-28")
    print(f"{sep}")
    for name, ver in DEPS.items():
        status = f"✓ {ver}" if ver else "✗ 未安装"
        print(f"  {name:8s}: {status}")
    print(sep)
    missing = [k for k, v in DEPS.items() if v is None]
    if missing:
        print(f"\n  ⚠ 缺少依赖库：{', '.join(missing)}")
        print(f"    安装命令：pip install {' '.join(missing)}")
        print(f"    部分演示（已安装库）将被跳过。\n")


# =============================================================================
# 辅助工具
# =============================================================================

def section(title: str):
    print(f"\n{'─' * 66}")
    print(f"  ▶ {title}")
    print(f"{'─' * 66}")


def verdict(passed: bool, label: str = ""):
    icon = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {icon}  {label}")


def note(msg: str):
    print(f"  ℹ  {msg}")


def detail(msg: str):
    print(f"      {msg}")


def error_msg(msg: str):
    print(f"  ⚠  {msg}")


# =============================================================================
# GATE 1：化学方程式配平检查（RDKit）
# =============================================================================

def gate1_chem():
    """化学关卡演示：RDKit 反应原子守恒检查"""
    section("GATE 1 — 化学方程式配平检查（RDKit）")

    if DEPS.get("rdkit") is None:
        error_msg("RDKit 未安装，跳过化学关卡演示")
        error_msg("安装：pip install rdkit-pypi")
        return

    from rdkit import Chem
    from rdkit.Chem import rdChemReactions

    # ── 内部函数 ────────────────────────────────────────────────────────────
    def count_atoms(mols):
        """统计一组分子中各元素原子总数。"""
        counts = {}
        for mol in mols:
            if mol is None:
                continue
            for atom in mol.GetAtoms():
                counts[atom.GetSymbol()] = counts.get(atom.GetSymbol(), 0) + 1
        return counts

    def check_balance(smarts: str, strict: bool = True) -> Tuple[bool, Optional[dict], Optional[dict], Optional[str]]:
        """
        检查化学反应式是否满足原子守恒。

        Returns:
            (is_balanced, reactant_counts, product_counts, error_msg)
        """
        try:
            rxn = rdChemReactions.ReactionFromSmarts(smarts)
        except Exception as e:
            return False, None, None, f"SMARTS 解析异常: {e}"

        if not rxn:
            return False, None, None, f"无法解析 SMARTS: {smarts}"

        r_counts = count_atoms(rxn.GetReactants())
        p_counts = count_atoms(rxn.GetProducts())

        if strict:
            is_balanced = (r_counts == p_counts)
        else:
            is_balanced = all(p_counts.get(el, 0) <= r_counts.get(el, 0)
                              for el in p_counts)

        return is_balanced, r_counts, p_counts, None

    # ── 测试用例 ────────────────────────────────────────────────────────────
    # 注意：SMILES 中 CO2 必须写成 O=C=O（否则 RDKit 报 unclosed ring 错误）
    test_cases = [
        {
            "label": "碳燃烧（配平正确）",
            "smarts": "C.O=O>>O=C=O",
            "expect_balanced": True,
        },
        {
            "label": "碳燃烧（产物少一个 O，未配平）",
            "smarts": "C.O=O>>O=C",
            "expect_balanced": False,
        },
        {
            "label": "甲烷燃烧（配平正确）",
            "smarts": "[CH4].O=O.O=O>>C(=O)=O.O.O",  # CH4 + 2O2 -> CO2 + 2H2O
            "expect_balanced": True,
        },
        {
            "label": "甲烷燃烧（氧气不足，正确识别为未配平）",
            "smarts": "[CH4].O=O>>C(=O)=O.O.O",  # CH4 + O2 -> CO2 + 2H2O（少 1 个 O）
            "expect_balanced": False,
        },
    ]

    for tc in test_cases:
        ok, r_counts, p_counts, err = check_balance(tc["smarts"])

        if err:
            verdict(False, tc["label"])
            detail(f"错误：{err}")
            continue

        label_detail = f"{tc['label']}  |  {tc['smarts']}"
        passed = (ok == tc["expect_balanced"])
        verdict(passed, label_detail)
        detail(f"  反应物原子：{r_counts}")
        detail(f"  产物原子：  {p_counts}")
        if not ok:
            diff = {el: r_counts.get(el, 0) - p_counts.get(el, 0)
                    for el in set(list(r_counts) + list(p_counts)) if r_counts.get(el, 0) != p_counts.get(el, 0)}
            detail(f"  原子差值：  {diff}  ← 配平失败原因")


# =============================================================================
# GATE 2：物理量量纲齐次性校验（pint）
# =============================================================================

def gate2_physics():
    """物理关卡演示：pint 量纲齐次性校验"""
    section("GATE 2 — 物理量量纲齐次性校验（pint）")

    if DEPS.get("pint") is None:
        error_msg("pint 未安装，跳过物理关卡演示")
        error_msg("安装：pip install pint")
        return

    import pint

    ureg = pint.UnitRegistry()
    Q_  = ureg.Quantity  # 速记

    # ── 内部函数 ────────────────────────────────────────────────────────────
    def dim_of(expr_str: str):
        """将表达式字符串转为 pint.Quantity 并返回其量纲字符串（规范化）。"""
        expr_str = expr_str.strip()
        # 纯标识符：直接作为单位
        if expr_str.isidentifier():
            try:
                # 用 repr() 规范化（避免 pint 0.25.x str() 对 [current] 排序不一致）
                return repr(getattr(ureg, expr_str).dimensionality)
            except Exception:
                return None
        # 带运算符：尝试 eval
        try:
            val = eval(expr_str, {"ureg": ureg, "Quantity": Q_,
                                  "sqrt": lambda x: x**0.5,
                                  "sin": Q_, "cos": Q_, "exp": Q_, "log": Q_})
            return repr(val.dimensionality)
        except Exception:
            return None

    def check_homogeneous(lhs: str, rhs: str) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """
        检验 lhs 和 rhs 量纲是否齐次。
        Returns: (homogeneous, lhs_dim, rhs_dim, error_msg)
        """
        lhs_dim = dim_of(lhs)
        rhs_dim = dim_of(rhs)
        if lhs_dim is None:
            return False, None, None, f"无法解析左侧表达式: {lhs}"
        if rhs_dim is None:
            return False, lhs_dim, None, f"无法解析右侧表达式: {rhs}"
        homogeneous = (lhs_dim == rhs_dim)
        return homogeneous, lhs_dim, rhs_dim, None

    def _fmt_dim(d):
        """将 repr(dim) 格式化为人类可读字符串。"""
        if not d:
            return "<未知>"
        # repr(dim) 形如 UnitsContainer({'[current]': -1, ...})
        import re
        # 提取 key 和幂次
        parts = re.findall(r"'(\[[^']+\])':\s*([-\d]+)", d)
        readable = []
        for base, exp in parts:
            base_name = base.strip('[]')
            exp_i = int(exp)
            if exp_i == 1:
                readable.append(base_name)
            elif exp_i == -1:
                readable.append(f"1/{base_name}")
            else:
                readable.append(f"{base_name}^{exp_i}")
        return "·".join(readable) if readable else d

    # ── 测试用例 ────────────────────────────────────────────────────────────
    test_cases = [
        # (label, lhs, rhs, expect_homogeneous)
        ("F = m × a（牛顿第二定律，齐次）",
         "ureg.newton", "ureg.kg * ureg.m / ureg.s**2", True),

        ("F = m × v（量纲不一致，故意错误）",
         "ureg.newton", "ureg.kg * ureg.m / ureg.s", False),

        ("动能定理：E = ½mv²（齐次）",
         "ureg.joule", "0.5 * ureg.kg * (ureg.m/ureg.s)**2", True),

        ("功：W = F × d（齐次）",
         "ureg.joule", "ureg.newton * ureg.m", True),

        ("欧姆定律：V = I × R（齐次）",
         "ureg.volt", "ureg.ampere * ureg.ohm", True),

        ("错误的功：W = F / d（量纲不一致）",
         "ureg.joule", "ureg.newton / ureg.m", False),

        ("万有引力：F = GmM/r²（齐次）",
         "ureg.newton",
         "ureg.kg**2 * ureg.m**3 / ureg.kg / ureg.s**2 / ureg.m**2", True),
    ]

    for label, lhs, rhs, expect in test_cases:
        ok, ld, rd, err = check_homogeneous(lhs, rhs)
        if err:
            verdict(False, label)
            detail(f"错误：{err}")
            continue

        # 简化量纲显示
        def short(d):
            return _fmt_dim(d) if d else "<解析失败>"

        passed = (ok == expect)
        verdict(passed, label)
        detail(f"  左侧量纲：{short(ld)}")
        detail(f"  右侧量纲：{short(rd)}")
        if not ok:
            detail(f"  量纲不一致！← 违反量纲齐次性原则")


# =============================================================================
# GATE 3：数学等式符号验证（SymPy）
# =============================================================================

def gate3_math():
    """数学关卡演示：SymPy 符号等式验证"""
    section("GATE 3 — 数学等式符号验证（SymPy）")

    if DEPS.get("sympy") is None:
        error_msg("SymPy 未安装，跳过数学关卡演示")
        error_msg("安装：pip install sympy")
        return

    import sympy as sp
    from sympy.parsing.sympy_parser import (
        parse_expr,
        standard_transformations,
        implicit_multiplication_application,
        convert_xor,
    )

    TRANSFORMS = standard_transformations + (
        implicit_multiplication_application,
        convert_xor,
    )

    # ── 内部函数 ────────────────────────────────────────────────────────────
    def parse_math(expr_str: str, symbols: list) -> Optional[sp.Expr]:
        """解析数学表达式字符串为 SymPy 符号表达式。"""
        try:
            # positive=True 使对数恒等式 log(x*y)=log(x)+log(y) 可正确化简
            local_dict = {s: sp.Symbol(s, real=True, positive=True) for s in symbols}
            local_dict.update(sp.__dict__)
            return parse_expr(expr_str, transformations=TRANSFORMS,
                              local_dict=local_dict, evaluate=True)
        except Exception:
            return None

    def verify_eq(lhs_str: str, rhs_str: str, symbols: list) -> Tuple[bool, Optional[sp.Expr], Optional[str]]:
        """
        验证 LHS = RHS 是否在符号层面恒成立。
        Returns: (is_valid, simplified_diff, error_msg)
        """
        lhs = parse_math(lhs_str, symbols)
        rhs = parse_math(rhs_str, symbols)
        if lhs is None:
            return False, None, f"无法解析左侧：{lhs_str}"
        if rhs is None:
            return False, None, f"无法解析右侧：{rhs_str}"

        diff = sp.simplify(lhs - rhs)
        is_valid = (diff == 0)
        if not is_valid:
            # 尝试更强化简
            diff2 = sp.simplify(sp.expand(diff))
            is_valid = (diff2 == 0)
            if not is_valid:
                diff3 = sp.simplify(sp.factor(diff))
                is_valid = (diff3 == 0)

        return is_valid, diff, None

    # ── 测试用例 ────────────────────────────────────────────────────────────
    test_cases = [
        # (label, lhs, rhs, vars, expect_valid)
        ("完全平方公式：(x+1)² = x² + 2x + 1",
         "(x+1)**2", "x**2 + 2*x + 1", ["x"], True),

        ("完全平方公式（故意错）：(x+1)² = x² + 2x + 2",
         "(x+1)**2", "x**2 + 2*x + 2", ["x"], False),

        ("平方差公式：a² - b² = (a+b)(a-b)",
         "a**2 - b**2", "(a+b)*(a-b)", ["a", "b"], True),

        ("勾股定理（符号化简验证）：sin²θ + cos²θ = 1",
         "sin(theta)**2 + cos(theta)**2", "1", ["theta"], True),

        ("指数恒等式：e^(a+b) = e^a × e^b",
         "exp(a+b)", "exp(a)*exp(b)", ["a", "b"], True),

        ("对数恒等式：log(x) + log(y) = log(x*y)",
         "log(x) + log(y)", "log(x*y)", ["x", "y"], True),

        ("对数恒等式（反向）：log(x*y) = log(x) + log(y)",
         "log(x*y)", "log(x) + log(y)", ["x", "y"], True),

        ("求导验证：(x³)' = 3x²",
         "3*x**2", "diff(x**3, x)", ["x"], True),

        ("积分验证：∫2x dx = x² + C",
         "x**2", "integrate(2*x, x)", ["x"], True),
    ]

    for tc in test_cases:
        label, lhs_s, rhs_s, syms, expect = tc
        ok, diff, err = verify_eq(lhs_s, rhs_s, syms)

        if err:
            verdict(False, label)
            detail(f"错误：{err}")
            continue

        passed = (ok == expect)
        verdict(passed, label)
        if not ok and diff is not None:
            detail(f"  LHS - RHS 化简结果：{diff}  ← 不等于 0")


# =============================================================================
# 综合演示：模拟 ETL 管道中的三道关卡
# =============================================================================

def gate_pipeline_demo():
    """模拟 ETL 管道：对一批候选边依次过三道关卡"""
    section("综合演示 — ETL 管道模拟（三道关卡流水线）")

    # 定义候选边集合（模拟 LLM 生成的候选关系）
    candidate_edges = [
        {
            "id": "E-CHEM-001",
            "domain": "chemistry",
            "label": "碳燃烧反应",
            "smarts": "C.O=O>>O=C=O",
            "expect_decision": "accept",
        },
        {
            "id": "E-CHEM-002",
            "domain": "chemistry",
            "label": "碳燃烧（未配平）",
            "smarts": "C.O=O>>O=C",          # 故意写错
            "expect_decision": "reject",
        },
        {
            "id": "E-PHY-001",
            "domain": "physics",
            "label": "牛顿第二定律",
            "lhs": "ureg.newton",
            "rhs": "ureg.kg * ureg.m / ureg.s**2",
            "expect_decision": "accept",
        },
        {
            "id": "E-PHY-002",
            "domain": "physics",
            "label": "F = mv（量纲错误）",
            "lhs": "ureg.newton",
            "rhs": "ureg.kg * ureg.m / ureg.s",  # 故意错
            "expect_decision": "reject",
        },
        {
            "id": "E-MATH-001",
            "domain": "math",
            "label": "完全平方公式",
            "lhs": "(x+1)**2",
            "rhs": "x**2 + 2*x + 1",
            "vars": ["x"],
            "expect_decision": "accept",
        },
        {
            "id": "E-MATH-002",
            "domain": "math",
            "label": "完全平方（故意错）",
            "lhs": "(x+1)**2",
            "rhs": "x**2 + 2*x + 2",
            "vars": ["x"],
            "expect_decision": "reject",
        },
    ]

    print()
    for edge in candidate_edges:
        dom = edge["domain"]
        gate1_ok = True
        gate2_ok = True
        gate3_ok = True
        rejection_reason = ""

        # Gate 1：化学
        if dom == "chemistry":
            if DEPS.get("rdkit") is None:
                gate1_ok = None
            else:
                from rdkit import Chem
                from rdkit.Chem import rdChemReactions

                def count_atoms(mols):
                    c = {}
                    for mol in mols:
                        for a in mol.GetAtoms():
                            c[a.GetSymbol()] = c.get(a.GetSymbol(), 0) + 1
                    return c

                try:
                    rxn = rdChemReactions.ReactionFromSmarts(edge["smarts"])
                    r_c = count_atoms(rxn.GetReactants())
                    p_c = count_atoms(rxn.GetProducts())
                    gate1_ok = (r_c == p_c)
                    if not gate1_ok:
                        rejection_reason = f"R-CHEM-01（原子守恒违反：反应物 {r_c}，产物 {p_c}）"
                except Exception as e:
                    gate1_ok = False
                    rejection_reason = f"R-CHEM-02（SMARTS 解析失败: {e}）"

        # Gate 2：物理
        if dom == "physics":
            if DEPS.get("pint") is None:
                gate2_ok = None
            else:
                import pint
                ureg = pint.UnitRegistry()
                def dim_of(s):
                    s = s.strip()
                    if s.isidentifier():
                        try:
                            return str(getattr(ureg, s).dimensionality)
                        except:
                            return None
                    try:
                        val = eval(s, {"ureg": ureg})
                        return str(val.dimensionality)
                    except:
                        return None

                ld = dim_of(edge["lhs"])
                rd = dim_of(edge["rhs"])
                if ld is None or rd is None:
                    gate2_ok = False
                    rejection_reason = "R-PHY-02（单位解析失败）"
                elif ld != rd:
                    gate2_ok = False
                    rejection_reason = f"R-PHY-01（量纲不一致：{ld} ≠ {rd}）"

        # Gate 3：数学
        if dom == "math":
            if DEPS.get("sympy") is None:
                gate3_ok = None
            else:
                import sympy as sp
                from sympy.parsing.sympy_parser import (
                    parse_expr, standard_transformations,
                    implicit_multiplication_application, convert_xor,
                )
                TRANSFORMS = standard_transformations + (
                    implicit_multiplication_application, convert_xor,
                )
                syms = edge.get("vars", [])
                local_dict = {s: sp.Symbol(s) for s in syms}
                local_dict.update(sp.__dict__)
                try:
                    lhs = parse_expr(edge["lhs"], transformations=TRANSFORMS,
                                     local_dict=local_dict, evaluate=True)
                    rhs = parse_expr(edge["rhs"], transformations=TRANSFORMS,
                                     local_dict=local_dict, evaluate=True)
                    diff = sp.simplify(lhs - rhs)
                    gate3_ok = (diff == 0)
                    if not gate3_ok:
                        rejection_reason = f"R-MATH-01（LHS - RHS = {diff}）"
                except Exception as e:
                    gate3_ok = False
                    rejection_reason = f"R-MATH-03（解析失败: {e}）"

        # 综合决策
        all_gates = [g for g in [gate1_ok, gate2_ok, gate3_ok] if g is not None]
        if None in [gate1_ok, gate2_ok, gate3_ok]:
            decision = "skip（库缺失）"
            icon = "⏭"
        elif all(all_gates):
            decision = "accept（入库）"
            icon = "✅"
        else:
            decision = f"reject（拒绝: {rejection_reason}）"
            icon = "❌"

        expected = edge["expect_decision"]
        match = "✓" if decision.startswith(expected) else "✗"
        print(f"  {icon} [{edge['id']}] {edge['label']}")
        print(f"      决策：{decision}  |  期望：{expected}  {match}")
        print()


# =============================================================================
# 主入口
# =============================================================================

def main():
    banner()

    gate1_chem()
    gate2_physics()
    gate3_math()
    gate_pipeline_demo()

    # 结语
    print("=" * 66)
    print("  PoC 演示结束")
    print("=" * 66)
    print("""
  核心要点：
  1. GATE 1（RDKit）  → 反应物原子总数 = 产物原子总数 → 原子守恒
  2. GATE 2（pint）   → 方程左右量纲一致 → 量纲齐次性
  3. GATE 3（SymPy）  → LHS - RHS 化简 = 0 → 符号等式成立

  这些关卡应嵌入 ETL 管道的 Transform 阶段，校验结果写入边属性：
    validated, validation_method, confidence, error_codes
  LLM 生成的候选边经过「假设→校验→入库/驳回」闭环，确保图谱质量。
  """)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("❌ 脚本执行异常：")
        traceback.print_exc()
        sys.exit(1)
