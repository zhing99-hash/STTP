# Phase 3 任务交付物 — 推理验证专家

## 任务概述
为「公式知识图谱项目」实现 Phase 3：LLM 推断边生成 + 三道门禁校验闭环。

## 项目根目录
`C:\Users\Administrator\.qclaw\workspace\01tuopu\04_验证闭环\`

## 交付物清单

### 1. `llm_hypothesis.py`（~420行）
**类**：`LLMHypothesisGenerator(backend='heuristic')`

**核心能力**：
- `backend='heuristic'`（默认）：内置候选目录，14条，含数学/物理/化学真假混合
- `backend='api'`：调用 OpenAI API，缺失 OPENAI_API_KEY 时**自动降级**为 heuristic
- `generate(graph=None, k=None)` 返回候选边列表
- 支持从图谱节点生成"共享符号 → derived_from"候选（Gauss-Bonnet ↔ Riemann Curvature）

**候选目录结构**（全部已验证通过真实门禁）：
| 类型 | 真（通过门禁） | 假（被门禁拒绝） |
|------|--------------|----------------|
| 化学 | C+O2→CO2、CH4+2O2→CO2+2H2O | C+O2→CO、H2+Cl2→HCl（非配平） |
| 物理 | F=ma、E=½mv² | F=mv（量纲错）、E=m+a（量纲错） |
| 数学 | (a+b)²=a²+2ab+b²、sin²θ+cos²θ=1、d(x³)/dx=3x² | (a+b)²=a²+2ab+b²+1、a²+b²=(a+b)² |

### 2. `verification_loop.py`（~330行）
**类**：`VerificationLoop`

**门禁路由**：
```
chemical_reaction/reactant_of/product_of  → R-CHEM（RDKit 原子守恒）
dimensionally_consistent                 → R-PHY（pint 量纲齐次性）
derived_from / proves                   → R-MATH（SymPy 符号等式）
其他                                    → NEEDS_REVIEW（无自动门禁）
```

**门禁函数**（均自带 import 守卫，缺库返回 NEEDS_REVIEW）：
- `gate_chem()`：调用 RDKit `rdChemReactions.ReactionFromSmarts`，比较原子计数
- `gate_phy()`：调用 pint `UnitRegistry`，比较 `dimensionality` 字符串
- `gate_math()`：调用 SymPy `simplify(lhs - rhs)`，验证是否等于 0

**置信度策略**：
- VERIFIED → `max(initial, 0.9)`
- REJECTED → `initial × 0.2`
- NEEDS_REVIEW → `initial × 0.5`

### 3. `phase3_demo.py`（~230行）
端到端演示脚本，**实际运行结果**：

```
总候选边数 : 16
判决分布：
  VERIFIED     8 条  ( 50.0%)
  REJECTED     8 条  ( 50.0%)
  NEEDS_REVIEW 0 条  (  0.0%)

门禁分布：R-CHEM=4, R-PHY=4, R-MATH=8
按学科：chemistry=4, physics=4, math=8

置信度（校验后）：avg=0.4944, min=0.06, max=0.9

追加到图谱：8 条 VERIFIED 推断边 → 52 条总边
输出文件：06_PoC/etl/with_inferred.json
```

**关键验证示例**：
- ✅ R-CHEM HYPO-CHEM-001（C+O2→CO2）：原子守恒 `{C:1,O:2}=={C:1,O:2}`，置信度 0.7→0.9
- ❌ R-CHEM HYPO-CHEM-002（C+O2→CO）：R-CHEM-01 拒绝，O 原子差 1，置信度 0.55→0.11
- ✅ R-PHY HYPO-PHY-001（F=ma）：量纲一致，置信度 0.8→0.9
- ❌ R-PHY HYPO-PHY-002（F=mv）：R-PHY-01 量纲不一致，置信度 0.4→0.08
- ✅ R-MATH HYPO-MATH-001（(a+b)²展开）：LHS-RHS=0，置信度 0.75→0.9
- ❌ R-MATH HYPO-MATH-002（+1 假命题）：LHS-RHS=-1≠0，R-MATH-01 拒绝

### 4. `README.md`
Phase 3 完整说明文档，包含：模块架构、运行方式、LLM 后端配置、三道门禁详解、置信度策略、与 Phase 0/2 关系、Schema 对应映射、错误码速查。

## 关键设计决策

1. **无 API 也能运行**：核心路径完全确定性，14条预置候选覆盖三个学科真假场景
2. **真实门禁**：RDKit/pint/SymPy 必须真正执行，不造假
3. **缺库不崩**：每个 gate 有 import 守卫，缺失库返回 NEEDS_REVIEW
4. **真实图谱结合**：支持基于 `normalized.json` 节点提出图谱内候选
5. **回写格式规范**：VERIFIED 推断边追加到 `with_inferred.json`，带 `verified`/`verification_gate`/`verification_evidence` 字段

## 验证结论
Phase 3 闭环完全打通：候选生成 → 门禁验证 → 置信度调整 → 图谱回写，三道门禁均可正常工作，8条通过/8条拒绝跨学科分布，演示了完整闭环能力。
