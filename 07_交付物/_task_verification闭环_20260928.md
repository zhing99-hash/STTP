# 任务完成报告：公式知识图谱三道验证关卡

**任务角色**：验证与推理专家
**完成时间**：2026-09-28
**状态**：✅ 全部完成

---

## 产出文件

| # | 文件路径 | 内容概要 |
|---|---------|---------|
| 1 | `04_验证闭环/校验规范与实现.md` | 三道门禁规则 + 可运行代码 + ETL 嵌入方案 + LLM 闭环 |
| 2 | `06_PoC/validate_demo.py` | 独立可运行 PoC 演示脚本（全绿 PASS） |

---

## 文件 1 要点：校验规范与实现.md

### 三道门禁规则

| 关卡 | 拒绝条件 | 工具 |
|------|---------|------|
| GATE1 化学 | R-CHEM-01 原子守恒违反 / R-CHEM-02 SMARTS 解析失败 | RDKit |
| GATE2 物理 | R-PHY-01 量纲不一致 / R-PHY-02 单位无法识别 | pint |
| GATE3 数学 | R-MATH-01 等式不成立 / R-MATH-03 SymPy 解析失败 | SymPy |

### 代码实现
- **(a) SymPy**：用 `parse_expr` 解析 LHS/RHS，`simplify(LHS-RHS)==0` 验证等式；用 `positive=True` 符号使对数恒等式正确化简
- **(b) pint 量纲**：用 `repr(quantity.dimensionality)` 比较（避免 pint 0.25.x `str()` 对 `[current]` 排序不一致问题）；`_fmt_dim()` 将 `UnitsContainer` 转为可读字符串
- **(c) RDKit 配平**：用 `ReactionFromSmarts` 解析，`GetReactants/Products` 遍历原子计数；注意 CO₂ 必须写为 `O=C=O`（而非 `CO2`），H₂O 无法在 SMARTS 反应中作为 `O.H` 独立存在

### 边类型分类
- `explicit_citation`：confidence=1.0，实线入库
- `llm_inferred`：confidence 按跨域/同域分 0.4/0.6，可视化虚线降权

### LLM 闭环
假设→校验→accept/demote/reject→反馈学习→更新 Prompt

---

## 文件 2 要点：validate_demo.py

### 运行结果（全绿）
- **GATE1 化学**：4 案例全部 PASS（碳燃烧配平/未配平、甲烷燃烧配平/未配平）
- **GATE2 物理**：7 案例全部 PASS（F=ma 齐次/F=mv 错误、动能、功、欧姆定律、万有引力）
- **GATE3 数学**：9 案例全部 PASS（完全平方、对数恒等式双向、勾股、指数、求导、积分）
- **ETL 管道模拟**：6 条候选边全部命中预期决策（accept/reject）

### 依赖处理
- 缺库友好提示，不崩溃
- `sys.stdout.reconfigure(encoding='utf-8')` 解决 Windows GBK 控制台 Unicode 问题
- numpy 降级至 <2（rdkit-pypi 2022.09.5 依赖 NumPy 1.x）

---

## 关键踩坑记录

1. **RDKit + NumPy 2.x 不兼容**：rdkit-pypi 2022.09.5 编译于 NumPy 1.x，升级 numpy 到 2.x 后报错 `_ARRAY_API not found`，需降级 `numpy<2`
2. **RDKit SMILES 限制**：`O=C=O` 是 CO₂ 的正确 SMILES，`CO2` 会报 `unclosed ring`；`O.H` 不是 H₂O 的合法反应物表示
3. **RDKit 多分子分隔**：`C(=O)=O.O` = 一个含 3 个 O 的分子；`C(=O)=O.O.O` = CO₂ + 2×H₂O（用 `.` 分隔不同分子）
4. **pint 0.25.x `str(dim)` 不稳定**：相同量纲 `ureg.volt` 和 `ureg.ohm*ureg.amp` 的 `str(dimensionality)` 排序不同，用 `repr()` 替代
5. **SymPy log 化简需 `positive=True`**：默认符号无法将 `log(x*y) - log(x) - log(y)` 化简为 0，需声明 `positive=True`
