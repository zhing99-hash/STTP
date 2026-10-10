# Phase 32 报告 · Claim / Evidence 完整对象化（第 22 轮）

- **日期**：2026-10-10
- **主题**：把「已算出的证据明细」持久化为**一等对象** —— 让「证据可追溯」从一个**说辞**变成**可测量、可断言、可回归的不变量**
- **代码基线**：Phase 31（`9657 / 48712`）
- **本轮结果**：规模 **不变**（`9657 / 48712`，**非破坏性**，只加字段）；**全图证据对象覆盖 1.18% → 100%**；门禁 **67 → 70 条**；北极星 **99.6%（不变，口径留档）**，新增 **T9 证据可追溯 = 100.0%**

---

## 一、起点：为什么上一轮的收尾建议不是好方向

第 21 轮收尾时我建议「Claim/Evidence 对象化 + **repr 串生成链根治**」。按铁律 #34（**负结果优先：先侦察再投入**），先做了 **3 轮只读侦察**（`06_PoC/_recon_phase32_*.py`）：

| 侦察对象 | 结论 |
|---|---|
| **repr 串生成链** | **负结果**：`normalized.json` 全 props 扫描 **零 repr 残留**；`build_phase11/11b/12.py` 是**一次性迁移脚本**、`evidence` 原样透传；`build_neo4j_ready.py` 的 `str()` 在**遗留 CSV 路径**（未回流权威图）→ **当前不活跃** |
| **「verifier」是不是证据** | **否**：`verification_level/scope/verifier/verified` 已 **100% 覆盖**，但那是「谁验的 / 怎么验的」的**名字**；真正的证据数据 `evidence` 仅 **1.18%（575 条）**、`created_at` 仅 **1.9%** |
| **明细能否取回** | **能**：`build_ctx` **早已算出每条边的证据明细串**（如「独立解析 CH4 中 C=1 vs 记录 1」），**除判否外全部被丢弃**；量化 `level≥rule_checked` 共 **34771**，已可取回 **10595（30.5%）**，缺口集中在 A4（24104，未穿针） |

→ **决定性判断**：本轮 ≈ **「持久化已算出的东西」，不是「重新算」** → 可行性高、风险低、价值实。
→ 同时纠正一个认知：**repr 链虽然今天不活跃，但本轮要引入「嵌套对象列表」，风险会从「潜伏」变「激活」→ 必须顺手根治**（有动机，非投机）。

---

## 二、一等对象的定义

落在**边** `props` 上（`apply_delta` 是**合并**语义，故非破坏性）：

```json
{
  "claim": "物理量「Energy」的单位是「joule」",
  "verification_evidence": [
    { "kind": "recompute",
      "impl": "dimension_table(量纲) × 单位符号解析",
      "detail": "量纲复算一致：Energy={'L': 2.0, 'M': 1.0, 'T': -2.0} vs J={'M': 1.0, 'L': 2.0, 'T': -2.0}",
      "indep": true }
  ],
  "evidence_at": "2026-10-10T14:04:12+08:00"
}
```

**两处关键设计**：

1. **`kind`/`indep` 由 `scope` 唯一决定**（`verification_model.SCOPE_KIND`），而 `scope` 由 `classify` 唯一给出 —— 证据的**档位语义**与判级模型**构造上不可能分歧**（铁律 #36，第 21 轮 A11 教训的正向固化）。
2. **`detail` 由 `classify` 在判定现场给出**（新增 `R(..., detail=...)` 通道，**46 处 `return R(...)` 全部穿针**），`build_evidence()` **只搬用不重推**。

受控词表 `EVIDENCE_KINDS`（6 类）：`recompute` / `cross_source` / `source_assertion` / `model_prediction` / `construction` / `rebuttal`。

★ **「可追溯」≠「独立」**：`indep` 表达「是否**独立于提出者**」（确定性复算 / 第二独立源 → 真；来源自述 / 模型预测 / 构造律 → 假）。这是北极星「证据可追溯」的判据核心，**两级分列，不得混淆**。

---

## 三、结果

### 3.1 覆盖与形态

| 指标 | Phase 31 | **Phase 32** |
|---|---:|---:|
| 全图带证据对象的边 | 575（1.18%） | **48712（100.0%）** |
| 证据条目总数 | 575 | **49635** |
| `detail` 为空的证据条目 | — | **0** |
| `impl` 与 `verifier` 不一致 | — | **0** |
| 并入的遗留证据（Phase 29 WebBook 等） | — | 575 条边（**原样保留**，转对象形式） |

**证据 kind 分布**：`recompute 31931` / `source_assertion 14163` / `cross_source 3389` / `model_prediction 136` / `construction 16`。

**（level, kind）严格 1:1**（零异常）：`rule_checked↔recompute 31481`、`source_asserted↔source_assertion 13795`、`cross_source↔cross_source 3290`、`model_inferred↔model_prediction 135`、`by_construction↔construction 11`。

> 生成项恰为 **48712** 条（= 边数）；多出的 923 条即遗留证据并入（其中 175 条 Phase 29 的「独立复算 + PubChem 第二源」**原样保留**）。

### 3.2 判级分布：**零变化**（本轮不改口径）

```
model_inferred 135 / source_asserted 13795 / by_construction 11
rule_checked 31481 / cross_source 3290          （verified_strict 34771）
```

逐项与 Phase 31 完全一致 —— 这**证明了本轮确实只做对象化**。
过程中**主动撤掉**了一处「顺手加的新升档分支」（`rationale_target_consistent`）：那会改变判级、超出本轮范围（「宁缺勿滥」）。

### 3.3 北极星：T9 把「证据可追溯」补成仪器

北极星原话是「跨域结论正确 **且证据可追溯**」—— 但第 18~21 轮**只仪器化了「正确」**（T1–T8 档位），「证据可追溯」**长期无仪器**。

| 指标 | 值 |
|---|---:|
| **T1–T8 北极星（结论正确率）** | 40482 / 40628 = **99.6%**（口径不变，与 Phase 31 可比） |
| **T9 证据可追溯**（达标边带可追溯证据链） | 40482 / 40482 = **100.0%** |
| **T9-i 证据独立**（达标边带 `indep=真` 证据） | 34771 / 40482 = **85.9%** ← **下一轮靶子** |
| 全图边带证据对象 | 48712 / 48712 = **100.0%**（Phase 31 为 1.2%） |
| 全图边带独立证据 | 34771 / 48712 = **71.4%** |
| **北极星（正确且可追溯）** | 99.6% × 100.0% = **99.6%** |

★ **T9-i = 85.9% 是本轮最有信息量的新数字**：它量化了「有多少『达标结论』其实只靠**单一来源断言**」——T4/T6/T8 的最低档是 `source_asserted`，这些边「达标」却**不独立**。
★ **口径留档（铁律 #16）**：T9 是**新增的横向不变量**，**不进入** T1–T8 的分母 → headline 数字保持可比（99.6%）。

---

## 四、源头根治：repr 串生成链（有动机地关闭）

侦察确认它今天是**惰性**的；但本轮引入**嵌套对象列表**后任何一处 `str()` 都会立刻制造 repr 残留 → 必须在同一轮根治。

| 位置 | 原写法 | 修法 |
|---|---|---|
| `03_知识层/build_neo4j_ready.py` `edge_row` | `str(p.get("evidence",""))` ← 对 `list[dict]` 会产出 **Python repr**（单引号 / True/False/None，非法 JSON） | 新增 `_csv_encode()` → **JSON 往返可解析**；并补 `claim`/`verification_evidence`/`evidence_at` 三列（含 `REL_COLUMNS`） |
| `03_知识层/etl_pipeline.py` `_join_list` | `";".join(str(x) for x in v)` ← 遇 dict 同样产出 repr | 一旦含结构体**整体走 JSON** |
| **门禁守卫（新）** | — | `no_repr_residue`：全图扫描「形如容器、非合法 JSON、却能被 `ast.literal_eval` 还原」的字符串 → 实测 **78 万+ 属性值，0 残留** |

---

## 五、仪器层：门禁 67 → **70 条**（+3）

| 不变量 | 断言 | 实测 |
|---|---|---|
| **(q) `evidence_traceable`** | 凡 `level ≥ rule_checked` 必须有 ≥1 条 `indep=真 && detail 非空 && impl == verifier` 的证据；**并断言覆盖率下限**（防证据链被悄悄清空） | 34771 / 34771（100.0%），缺 0 条 |
| **(r) `evidence_wellformed`** | 全图证据对象良构（非空列表 / `kind ∈ 词表` / `detail 非空` / `indep` 与 `kind`**语义配对**）；**覆盖率下限** | 48712 / 48712（100.0%），不良构 0 条 |
| **(s) `no_repr_residue`** | 全图属性值无 repr 残留（精确探测，见 §四） | 780618 个属性值，0 残留 |

### ★ 6 条不变量全部通过「**非真空自检**」（`bash sttp.sh gatecheck`）

**不改门禁代码**，直接 `run_case(case, mutated_graph)` 注入已知缺陷，正对照 PASS + 注入缺陷 FAIL：

```
(n) 注入 scope=symbol_expr_recompute 但源无符号        → FAIL ✅
(o) 注入 GNN 假 has_symbol（目标符号缺席）              → FAIL ✅
(p) 注入量纲互斥的假 dimensionally_consistent           → FAIL ✅
(q) 注入 rule_checked 但无证据链                        → FAIL ✅
(q) 注入有证据但 indep=假                               → FAIL ✅
(q) 注入证据 impl 与 verifier 不一致                    → FAIL ✅
(r) 注入非法 kind / 覆盖跌破下限 / indep-kind 不配对     → FAIL ✅ ×3
(s) 注入 Python repr 化的容器（str(dict)）               → FAIL ✅
(s) 正对照：合法 JSON 串 **不得误报**                    → PASS ✅
汇总：✅ 6 条不变量（n/o/p/q/r/s）全部**可红** —— 非真空，门禁有效
```

> **一条永远不会红的断言不是断言；`PASS` 只说明「当前没红」，不说明「有能力红」。**

### ★ 独立审计器交叉确认（铁律 #14）——并且**抓到了自己的一处假阳性**

`semantic_noise_audit.py`（**不 import `verification_model`**，自带枚举与符号复算）新增 **R9**：

- **R9a 良构**：独立判定 `level≥rule_checked` 边的证据链 → 34771 / 34771 带独立证据
- **R9b 抽验**：对 `impl` 自称 `symbol_in_source` 的证据，**用审计器自己的 `has_symbol` 重算** → 抽验 **222** 条
- **首次运行报出 2 条候选反驳**：`has_symbol|CE:fo:ph|CE:sy:pH`、`has_symbol|CE:fo:henderson|CE:sy:pH`

**诊断**：源 latex 是 `pH=-\log_{10}[H^{+}]`（**明文含 `pH`**），目标符号是 `\mathrm{pH}`；规范模型**剥掉包装命令** → `pH` → 命中；而审计器把 `\mathrm{pH}` 归一成 `mathrmpH` → 落空。
→ **这是审计器的假阳性**，正是铁律 #31「**包装命令须剥壳**」在**第二个实现**上的复现。
→ **修法**：审计器 `_norm_text` 增加**包装命令剥壳**（`\mathrm/\text/\operatorname/…`），并加**正对照**（`\mathrm{pH}`→`pH` 等 4 例）防回归。
→ 修后：**候选反驳 0 条**，正对照 ✅。

> 这条的价值在于：**独立审计器不是橡皮图章** —— 它先报出了分歧，才逼出这处归一化不一致。**独立性在于判据逻辑，不在于文本归一化**；两实现必须共用同一归一化约定。

---

## 六、结构指标

不变（本轮未增删边）：**连通分量 6**（最大 9628）、**5 个真孤岛 / 29 节点**、`connectivity_audit --strict` → **FROZEN-OK**、悬空/自环 **0/0**。

---

## 七、全链验收

| 项 | 结果 |
|---|---|
| `apply_delta` 自检 | **6/6 PASS**（节点 id 唯一 / 边 id 唯一 / 无悬空 / 无自环 / 全图无悬空 / 全图无自环） |
| `verification_model --audit` | 分层分布与 Phase 31 **完全一致**；复算不一致 0 |
| `frozen_gate` | **PASS 70 / FAIL 0 / SKIP 0** |
| `semantic_noise_audit` | **候选反驳 0 条**（含 R9） |
| `task_trust_audit` | **99.6%**（T1–T8）+ **T9 100% / T9-i 85.9%** |
| `connectivity_audit --strict` | **FROZEN-OK** |
| `gatecheck`（非真空自检） | **6 条不变量全部可红** |
| Aura | 推送 `phase32_evidence_delta.json` → 对账 → **严格一致**（详见 §八） |
| 产品化 | `.env` 切 `graph_data_phase32.json`；`sttp.sh` 新增 `evidence` 子命令 |

---

## 八、Aura 同步

`bash sttp.sh push 06_PoC/etl/neo4j/phase32_evidence_delta.json`（批次 300）→ 48712 条边属性更新。
`sanitize_props` 在**推送边界**把 `verification_evidence`（`list[dict]`）**无损 JSON 编码**（铁律 #11：嵌套属性推 DB 前消毒），本地图不动。

随后 `reconcile` + **属性维抽查**（撤回边消失 / 升档边档位落地 / 新增对象落地）。

---

## 九、本轮新增纪律（铁律 #41~#43）

41. **「可追溯」与「独立」必须分列**：把两者合成一个数会掩盖真相 —— T9=100% 会掩盖 T9-i=85.9%（14.1% 的达标结论只靠单一来源断言）。
42. **根治惰性缺陷需要「动机」**：repr 链已空转三轮，直到本轮**引入嵌套对象**才使它从「潜伏」变「激活」—— 在缺陷被激活的那一轮顺手修，比提前修更省力、也更可验证。
43. **独立审计器必须允许它「报错」**：若它从不与主实现分歧，就只是橡皮图章。本轮它报出 2 条 → 逼出 1 处真实的**归一化不一致**（铁律 #31 的第二个实现复现）。

---

## 十、遗留（承给第 23 轮）

1. 🔴 **T9-i 独立证据率 85.9%** → 5711 条达标边只靠**单一来源断言**（T4/T6/T8 门槛为 `source_asserted`）。**这是本轮量出的最大新靶子**。
   - 方向：给 `equation_sidedness`（Rhea/ChEBI 4534 条）等找**第二独立源**，或做**守恒复算**（元素/电荷守恒可确定性复算反应方向）。
2. **`evidence`（遗留字段）与新 `verification_evidence` 并存**：575 条遗留仍以旧形态存在（本轮**非破坏性**保留）。下一轮可考虑统一（含适配器写入侧）。
3. **`claim` 目前是模板生成的可读陈述**，尚未做「断言是否与边语义一致」的复算（可成为下一类断言）。
4. **5 个真孤岛（29 节点）** 仍待用**真证据**消除（微积分 16 / 几何三角 5 / 代数概念 3 / 电感 3 / 电容 2）—— **一律不补无证据的边**。
5. `reconcile_aura_edges.py` 仍缺**属性维**对账（只比三元组）；本轮仍以**手工**方式补做。
6. `IC:fo:ph` 与 `CE:fo:ph` 同概念两节点，口径待裁（铁律 #7）。

**下一轮（第 23 轮）建议**：**A（推荐）T9-i 攻坚 —— 给「单源断言」补独立证据**（守恒复算 + 第二源交叉）；
备选 **B** `source_asserted` 攻坚（13795 条 / 28.3%，绝对收益最高）；备选 **C** 孤岛消除（天花板低）。
