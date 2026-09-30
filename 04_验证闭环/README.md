# Phase 3 — LLM 推断边与校验闭环

> 版本：v1.0 | 日期：2026-09-28 | 状态：实现完成
> 适用：跨学科公式知识图谱（化学/物理/数学）Phase 3

---

## 目录

1. [概述](#1-概述)
2. [模块架构](#2-模块架构)
3. [文件清单](#3-文件清单)
4. [运行方式](#4-运行方式)
5. [LLM 后端配置](#5-llm-后端配置)
6. [三道门禁详解](#6-三道门禁详解)
7. [置信度策略](#7-置信度策略)
8. [与 Phase 0/2 的关系](#8-与-phase-02-的关系)
9. [与图谱 Schema 的对应](#9-与图谱-schema-的对应)

---

## 1. 概述

Phase 3 实现**LLM 推断边与校验闭环**的完整管道：

```
LLM 提出候选关系边
  ↓（inferred，带初始置信度与理由）
三道门禁自动验证（R-CHEM / R-PHY / R-MATH）
  ↓（真实 SymPy / pint / RDKit 执行）
通过者 → 置信度确认/上调 → 回写图谱
拒绝者 → 置信度下调 → 标记 rejected
```

核心设计原则：
- **无 API Key 也能运行**：所有核心路径基于确定性 heuristic 目录，零依赖外部 API
- **缺库不崩**：RDKit / pint / SymPy 任一缺失时，对应 gate 返回 `NEEDS_REVIEW`，绝不崩溃
- **真实门禁**：所有门禁必须真正调用 SymPy / pint / RDKit，不可造假

---

## 2. 模块架构

```
04_验证闭环/
├── llm_hypothesis.py      # LLM 候选边生成器
├── verification_loop.py    # 三道门禁校验闭环
├── phase3_demo.py         # 端到端演示（运行入口）
└── README.md              # 本文件
```

### 2.1 llm_hypothesis.py

**类**：`LLMHypothesisGenerator(backend='heuristic')`

**核心方法**：

| 方法 | 说明 |
|------|------|
| `generate(graph=None, k=None)` | 生成候选边列表（目录 + 图谱内补充） |
| `LLMHypothesisGenerator.summarize(candidates)` | 返回候选集统计 |

**候选来源**：

1. **内置候选目录**：12 条预置候选（数学/物理/化学各含真假混合）
2. **图谱内补充**（当传入 `graph` 参数时）：基于 Gauss-Bonnet ↔ Riemann Curvature 共享几何语义，提出 `derived_from` 候选

**Backend 切换**：

```python
from llm_hypothesis import LLMHypothesisGenerator

# 确定性模式（默认，无需 API key）
gen = LLMHypothesisGenerator(backend="heuristic")

# API 优先模式（缺失 key 时自动降级）
gen = LLMHypothesisGenerator(backend="api")
```

### 2.2 verification_loop.py

**类**：`VerificationLoop`

**核心方法**：

| 方法 | 说明 |
|------|------|
| `verify(candidate)` | 单条候选边执行完整校验闭环 |
| `verify_all(candidates)` | 批量执行 |
| `summary(results)` | 生成汇总统计 |

**门禁路由**：

```
candidate['type']
  ├─ chemical_reaction / reactant_of / product_of
  │    └─ gate_chem()  → RDKit 原子守恒
  ├─ dimensionally_consistent
  │    └─ gate_phy()   → pint 量纲齐次性
  ├─ derived_from / proves
  │    └─ gate_math()  → SymPy 符号等式
  └─ 其他
       └─ NEEDS_REVIEW（无自动门禁）
```

---

## 3. 文件清单

| 文件 | 行数 | 说明 |
|------|------|------|
| `llm_hypothesis.py` | ~420 | 候选边生成器（含 API 降级守卫） |
| `verification_loop.py` | ~330 | 三道门禁 + 置信度策略 |
| `phase3_demo.py` | ~230 | 端到端演示（运行入口） |
| `README.md` | — | 本文档 |

---

## 4. 运行方式

### 4.1 直接运行演示

```bash
cd 04_验证闭环
python phase3_demo.py
```

**预期输出**：
- 候选边总数：16 条（目录 14 条 + 图谱内 2 条）
- VERIFIED ≥ 6 条（真命题通过三道门禁）
- REJECTED ≥ 6 条（假命题被门禁拦截）
- `06_PoC/etl/with_inferred.json` 生成（追加了 VERIFIED 推断边）

### 4.2 在项目 ETL 管道中嵌入

```python
import sys, os
sys.path.insert(0, "04_验证闭环")

from llm_hypothesis import LLMHypothesisGenerator
from verification_loop import VerificationLoop

# 1. 生成候选
gen = LLMHypothesisGenerator(backend="heuristic")
candidates = gen.generate(graph=normalized_graph, k=20)

# 2. 校验
vl = VerificationLoop()
results = vl.verify_all(candidates)

# 3. 汇总
summary = vl.summary(results)
print(f"VERIFIED: {summary['verified_count']}, REJECTED: {summary['rejected_count']}")

# 4. 回写（追加到 normalized.json）
new_edges = build_verified_edges(results)  # 见 phase3_demo.py
# 追加到图谱 edges[] ...
```

---

## 5. LLM 后端配置

### 5.1 Heuristic 模式（默认，无需配置）

```python
gen = LLMHypothesisGenerator(backend="heuristic")
```
- 使用内置候选目录（14 条，含真假混合）
- 零依赖，零 API 调用
- **推荐用于**：开发/演示/CI 测试

### 5.2 API 模式（需配置环境变量）

```bash
export OPENAI_API_KEY=sk-xxxxx   # Linux/macOS
set OPENAI_API_KEY=sk-xxxxx      # Windows CMD
$env:OPENAI_API_KEY="sk-xxxxx"   # Windows PowerShell
```

```python
gen = LLMHypothesisGenerator(backend="api")
```
- 优先调用 OpenAI Chat Completions API
- 缺失 `OPENAI_API_KEY` 或调用失败时 → **自动降级为 heuristic 模式并打印友好提示**
- **绝不因 API 异常崩溃**

### 5.3 添加新候选（扩展 heuristic 目录）

编辑 `llm_hypothesis.py` 中的 `_CATALOG` 列表，每条添加：

```python
{
    "id": "HYPO-DOMAIN-NNN",
    "source": "节点ID",
    "target": "节点ID",
    "type": "derived_from",          # 化学: chemical_reaction
    "confidence": 0.70,              # 初始置信度
    "explicit_or_inferred": "inferred",
    "domain": "math",                # math / physics / chemistry
    "rationale": "该候选边的理由说明",
    # ── 门禁 payload（按 type 选择）─
    # 数学类：
    "lhs": "(a+b)**2",
    "rhs": "a**2 + 2*a*b + b**2",
    "vars": ["a", "b"],
    # 或物理类：
    "lhs": "ureg.newton",
    "rhs": "ureg.kg * ureg.m / ureg.s**2",
    # 或化学类：
    "smiles": "C.O=O>>O=C=O",
}
```

---

## 6. 三道门禁详解

### 6.1 R-CHEM（化学关卡）— RDKit

| 检查项 | 说明 |
|--------|------|
| 工具 | RDKit `rdChemReactions.ReactionFromSmarts` |
| 原理 | 反应物原子计数字典 == 产物原子计数字典 |
| 错误码 | R-CHEM-01（原子守恒违反）/ R-CHEM-02（SMARTS 解析失败） |

**示例**：
```
C.O=O>>O=C=O       → VERIFIED（C:1,O:2 = C:1,O:2）
C.O=O>>O=C         → REJECTED（R-CHEM-01：O 原子差 1）
```

### 6.2 R-PHY（物理关卡）— pint

| 检查项 | 说明 |
|--------|------|
| 工具 | `pint.UnitRegistry` + `dimensionality` 比较 |
| 原理 | 方程左右两边量纲字符串完全一致 |
| 错误码 | R-PHY-01（量纲不一致）/ R-PHY-02（单位解析失败） |

**示例**：
```
ureg.newton  == ureg.kg * ureg.m / ureg.s**2  → VERIFIED（均为 MLT⁻²）
ureg.newton  == ureg.kg * ureg.m / ureg.s      → REJECTED（R-PHY-01：MLT⁻² ≠ MLT⁻¹）
```

### 6.3 R-MATH（数学关卡）— SymPy

| 检查项 | 说明 |
|--------|------|
| 工具 | `sympy.simplify(lhs - rhs)` |
| 原理 | LHS - RHS 化简后 == 0 |
| 错误码 | R-MATH-01（等式不成立）/ R-MATH-03（SymPy 解析失败） |

**示例**：
```
(a+b)**2 == a**2 + 2*a*b + b**2  → VERIFIED（LHS - RHS = 0）
(a+b)**2 == a**2 + 2*a*b + 2     → REJECTED（R-MATH-01：LHS - RHS = -1）
```

---

## 7. 置信度策略

| 判决 | 策略 | 含义 |
|------|------|------|
| `VERIFIED` | `max(initial, 0.9)` | 通过门禁，置信度提升至 ≥ 0.9 |
| `REJECTED` | `initial × 0.2` | 被门禁拒绝，置信度大幅下调 |
| `NEEDS_REVIEW` | `initial × 0.5` | 无自动门禁，保持低置信待人工审核 |

**可视化对照**（来自校验规范 v1.0）：

| 置信度 | 视觉 | 入库操作 |
|--------|------|---------|
| 0.9–1.0 | 实线，深色 | 正常入库 |
| 0.5–0.89 | 实线，正常色 | 正常入库 |
| 0.3–0.49 | 虚线，灰色 | 降权入库 |
| < 0.3 | 虚线，浅灰 | 拒绝或人工审核 |

---

## 8. 与 Phase 0/2 的关系

### Phase 0（门禁规范）
- Phase 3 的三道门禁（R-CHEM / R-PHY / R-MATH）**完全遵循** Phase 0 的校验规范
- 代码逻辑与 `校验规范与实现.md` 中的伪代码一一对应
- 错误码体系（R-CHEM-01/02、R-PHY-01/02、R-MATH-01/03）与 Phase 0 一致

### Phase 2（ETL 管道）
- Phase 3 消费 `06_PoC/etl/normalized.json`（Phase 2 产出）
- Phase 3 将 VERIFIED 推断边**追加**到 `06_PoC/etl/with_inferred.json`
- `with_inferred.json` 在原图基础上追加 `_phase3_meta` 元信息

### 数据流

```
Phase 2 ETL → normalized.json → Phase 3 加载
                                    ↓
                         LLMHypothesisGenerator
                                    ↓
                         VerificationLoop（三道门禁）
                                    ↓
                      ┌────────────┴────────────┐
                      ↓                          ↓
               VERIFIED 边                  REJECTED 边
               (confidence ↑)              (confidence ↓)
                      ↓                          ↓
              追加到 with_inferred.json      记录错误码
                                              → 人工审核队列
```

---

## 9. 与图谱 Schema 的对应

### 9.1 边字段映射

| Schema 字段 | Phase 3 产出 | 说明 |
|------------|------------|------|
| `id` | `INFERRED:<候选ID>` | 追加 `INFERRED:` 前缀区分 |
| `source` / `target` | 来自候选 | 节点全局 ID |
| `type` | 来自候选 | derived_from / proves / chemical_reaction 等 |
| `kind` | `llm_inferred` | 标注为 LLM 推断 |
| `confidence` | `final_confidence` | 校验后置信度 |
| `explicit_or_inferred` | `inferred` | 推断边 |
| `verification_gate` | `R-CHEM` / `R-PHY` / `R-MATH` | 执行的门禁 |
| `verification_evidence` | 门禁证据字符串 | 详细理由 |
| `verification_error_codes` | `["R-CHEM-01"]` 等 | 错误码列表 |
| `verified` | `True` | 验证通过标记 |
| `verified_at` | ISO 时间戳 | 验证时间 |

### 9.2 支持的边类型（与 Schema 一致）

```
显式引用类：
  derived_from      ← 数学推导依赖（R-MATH 门禁）
  proves           ← 证明关系（R-MATH 门禁）
  dimensionally_consistent ← 量纲一致（R-PHY 门禁）
  chemical_reaction ← 化学反应（R-CHEM 门禁）
  reactant_of       ← 反应物（R-CHEM 门禁）
  product_of       ← 生成物（R-CHEM 门禁）
```

---

## 附录：错误码速查

| 错误码 | 门禁 | 含义 |
|--------|------|------|
| R-CHEM-00 | R-CHEM | RDKit 未安装 |
| R-CHEM-01 | R-CHEM | 原子守恒违反（配平失败） |
| R-CHEM-02 | R-CHEM | SMARTS 解析失败 |
| R-PHY-00 | R-PHY | pint 未安装 |
| R-PHY-01 | R-PHY | 量纲不一致（齐次性失败） |
| R-PHY-02 | R-PHY | 表达式含未知单位 |
| R-PHY-03 | R-PHY | 表达式解析失败 |
| R-MATH-00 | R-MATH | SymPy 未安装 |
| R-MATH-01 | R-MATH | 等式不成立（LHS - RHS ≠ 0） |
| R-MATH-03 | R-MATH | SymPy 解析失败 |
| GATE-CRASH | N/A | 门禁执行异常（需人工审核） |

---

*文档版本：v1.0 | 最后更新：2026-09-28 | 维护者：验证与推理专家*
