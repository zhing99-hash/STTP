# Phase 9 任务1-3 扩展记录（2026-09-30 下午后半）

## 一、PhysicsBabel 方程规模扩展：600 → 5000

**目标**：把真实物理方程从 600 条扩到 5000 条，喂实物理子图。

**做法**
- `11_真实数据/physicsbabel_ingest.py` 上限 600→5000，取 `plausible=True` 的真实维度自洽方程。
- 产出新增量：5050 节点（5000 `PB:fo` 公式 + 48 `PB:pq` 物理量 + 2 `PB` 常量）+ **29104 边**（has_symbol 24104 + dimensionally_consistent 5000）。
- `dimensionally_consistent` 由全组合 C(k,2) 改为**每方程仅保留 1 条代表对**，边数从 46438 降到 5000，避免团爆炸（has_symbol 已把公式连到全部参与量，代表对足以表达"该方程量纲自洽"）。
- 7 个核心物理量（质量/力/能量/加速度/速度/动能/光速）直接挂到已有 `PQ:*`/`MX:phy:*` 节点，构成真实跨域桥。

**关键技术踩坑 · Aura 大事务回滚**
- 首次一次性推 70542 边（600-eq 误扩到 5000 但边未降维）触发 `defunct connection / read timeout`，脚本报 OK 但**边整批未提交**（Aura 总边数不变），节点完好。
- 根因：单个大 UNWIND 事务在 flaky 连接下整体回滚。
- 解药：自写 `06_PoC/robust_aura_loader.py`——边按 100~2000 条一小批、显式 `tx.commit()` + 断连重连重试（最多 6 次）。实测 2000/批约 **10 边/秒**。
- 本地 `graph_data_phase12.json` 曾因 `build_phase12.py` 把 FULL 指向已被 PhysicsBabel 污染的旧文件而膨胀到 13473/124147；改为**以 Aura 为权威源**用 `export_aura.py` 反向导出修正。

**当前状态**：`phase12_finalize.py` 正在后台将 29104 边推入 Aura（幂等，已提交边重放为 no-op），速率约 10 边/秒，ETA ~20-40 分钟（Aura 免费实例连接限速）。完成后 Aura ≈ 2722+5050 节点 / 11279+29104 边。

## 二、85 条 KEEP 假设的真实 LLM 复核

**任务**：把任务1 留下的 85 条 `related_to` 假设送真实 LLM 复核（符号可证则升级为已验证桥，否则剔除伪影）。

**方法学（LLM 语义 + 符号校验）**——`09_科研扩展/9_inference/llm_review_85.py`
1. (a) **符号核验**：源分子 formula/name 是否精确命中目标反应 equation 的某一侧 → 升级为 reactant_of / product_of。
2. (b) **领域知识白名单**：生物学真关联但简化方程未写明的（ATP 是呼吸作用产物 / 光合作用反应物）→ 升级。
3. (c) **剔除**：分子并非该特定反应参与物的 GNN 伪影 → 删除。

**结果：VERIFY 9 / REJECT 76**

9 条真验证桥（均为呼吸·光合/ATP 水解反应的真实参与物）：
- `CO₂` → respiration **[product]**、`H₂O` → respiration **[product]**、`H₂O` → photosynthesis **[reactant]**（PC:mol:962、MO:h2o）
- `O₂` → respiration **[reactant]**、`O₂` → photosynthesis **[product]**（MO:o2）
- `ATP` → respiration **[product]**、`ATP` → photosynthesis **[reactant]**（BC:mo:atp，经白名单，equation 未写 ATP）

76 条剔除（典型 GNN 伪影）：甲烷/乙烷/乙烯/苯/乙醇/NaCl/HCl/Cl₂/Na 等并不参与呼吸或光合反应；符号→无机公式、反应→公式结构伪影等。

**Aura 同步**：`sync_aura_review.py`（已并入 `phase12_finalize.py`）在边加载完成后执行——
- 9 条 VERIFY 新建 verified typed 桥（kind=llm_review, gate=R-BIO）。
- 76 条 REJECT 删除原 `related_to` 伪影边。

## 三、待收尾（边加载完成后）
1. `export_aura.py` 从 Aura 反向导出权威 `graph_data_phase12.json`。
2. 重启 viz 指向 Phase 12 全量图。
3. 写 `07_交付物/Phase9任务1-3扩展报告_20260930.md`。
