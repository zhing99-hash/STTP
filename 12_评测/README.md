# 12_评测 —— R11 固定任务评测集与规则基线

> 复评 R11 完成定义：**公开候选空间、拆分规则、随机种子和逐类结果**。
> 本目录把「专题任务」变成**可检查、可复现、可失败**的评测件。

## 这是什么

STTP 的北极星（T1–T8 任务级可信完成率 / 证据可追溯 / 独立）测的是**数据与验证覆盖**。
复评明确指出它「不能替代独立任务正确率」。本目录补上**第三组指标**：

| 指标组 | 位置 | 用途 |
|---|---|---|
| 数据与验证覆盖 | `06_PoC/task_trust_audit.py` | 检查数据治理进度 |
| **科学任务表现** | **本目录** | 衡量使用者能否得到正确结论 |
| 新候选研究收益 | （GNN 盲评，后续） | 衡量 GNN/LLM 是否提供增量 |

## 三个文件

| 文件 | 作用 |
|---|---|
| `build_tasks.py` | 定义 40 题**冻结任务集**（热力学-化学平衡-数学方法）+ 确定性分层切分；产出 `tasks.json` |
| `tasks.json` | 冻结任务集（含 `expected` 期望答案/拒答、切分、权威来源）——**评测的输入契约** |
| `eval_runner.py` | **规则基线**运行器：只读规范图 + 真值表，逐题作答/拒答，产出 `eval_report.json` |

## 快速开始

```bash
PY="${STTP_PYTHON:-python}"

# 1) 生成（幂等）或校验任务集
"$PY" 12_评测/build_tasks.py            # 写 tasks.json
"$PY" 12_评测/build_tasks.py --check    # 只校验

# 2) 跑规则基线
"$PY" 12_评测/eval_runner.py --json 12_评测/eval_report.json
"$PY" 12_评测/eval_runner.py --split test    # 只跑冻结测试集
"$PY" 12_评测/eval_runner.py --strict        # 任一困难反例答错 → 非零退出
"$PY" 12_评测/eval_runner.py --selfcheck     # 非真空自检（注入扰动，断言判分器会红）
```

## 任务集构成（40 题）

| 类别 | 题数 | 说明 |
|---|---|---|
| `data_verification` 数据查证 | 10 | 常量/元素真值与 CODATA/IUPAC 核对 |
| `unit_state` 单位与状态检查 | 8 | 量纲齐次、无量纲性、同量纲异义 |
| `numeric` 有限数值计算 | 8 | K↔ΔG°、pKa、Henderson、ΔG=ΔH−TΔS |
| `method_selection` 方法选择 | 6 | 选 K 还是 Q、选哪个关系式、算子桥类型 |
| `refusal` 条件不足时拒答 | 8 | 缺条件必须**拒答**，不得硬算 |

**6 型困难反例**（复评指定）全覆盖：温度缺失 / 物相混淆 / 质子化态不一致 /
同分子式异构体 / 对数定义域 / 量纲相同但物理意义不同。

## 三件套披露（复评要求逐条对应）

| 要求 | 本目录的实现 |
|---|---|
| **候选空间** | `tasks.json.solution_space`：规范图 `06_PoC/etl/normalized.json` + `element_reference.py` + `dimension_table.py`；**不访问网络、不用 LLM** |
| **拆分规则** | 按类别分层；困难题与基本题各自对半交替分入 dev/test（`assign_split()`） |
| **随机种子** | `seed=20261012`（冻结）；**基线本身确定，seed 对基线无影响**，仅为后续 GNN 对比留位 |
| **逐类结果** | `eval_report.json.summary.by_category / by_split / by_hardness / hard_kinds` |

## 判分

每题结果 ∈ `answer` / `refuse`；判分 ∈ `correct` / `incorrect` / `false_answer`（该拒却答）/ `false_refusal`（该答却拒）。
`expected.kind ∈ {value, choice, boolean, refusal}`；`value` 支持 `rel_tol` / `abs_tol`。

## 诚实边界（**必读**）

1. **任务与规则同源**：任务集与基线规则由同一轮编写，`test` 切分是**准留出**（quasi-held-out），
   不是严格独立留出。基线修正后重跑通过 **不能** 当作泛化证据。
2. **100% 是自洽性结果**：规则基线在**自建冻结集**上全对，只说明「基线与其任务集自洽」，
   **不构成**跨域任务正确率的一般证明，也**不能**与北极星 99.6% 直接比较（口径不同）。
3. **学科人员外部审查待进行**：任务与答案锚定外部权威源（CODATA/SI/IUPAC/标准热力学关系），
   做到了「不由被测系统生成」，但复评要求的「学科人员已审」**尚未满足**。
4. **样本小**：40 题，95% Wilson 区间宽（[91.2%, 100%]）；不代表全学科可靠性。

## 相关

- 复评报告：`07_交付物/外部评审/STTP_复评与Apache2.0许可意见_20261011.md`（R11）
- 本轮报告：`07_交付物/Phase38_科学任务评测集报告_20261012.md`
- 可复现发布包：`06_PoC/release/`
