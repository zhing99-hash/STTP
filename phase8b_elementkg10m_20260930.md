# Phase 8.B · ElementKG 2.0 全量 10M CSV 接入（2026-09-30）

## Objective
用户下载了官方全量 ElementKG 2.0 数据集，评估并接入真实化学核心，把骨架喂实（选项 A 的延续）。

## Key Reasoning
- 全量 `10m_elementkg_release.csv`（10.15M 行 / 818MB）是标准三元组（head_type/head_value/relation/tail_type/tail_value）。
- profiling：73% 是实验方案文本（literal，价值低）；真实结构化主体是 Molecule 1.22M / Reaction 628k / 反应物-试剂-产物 ~96万 / functionalGroup 373 / element 221。
- 实体→实体边拓扑：Reaction─PRODUCES→Product─IS_MOLECULE→Molecule(PUBCHEM全属性)；Reactant/Reagent─PARTICIPATES_IN/USED_IN→Reaction；Molecule─HAS_FUNCTIONALGROUP→functionalGroup─HAS_ELEMENT→element。这是真实「反应-分子-官能团-元素」网络。
- **Aura 免费实例 ~5万节点上限** → 不能全量灌；抽**有界真实化学核心子集**（800 反应 + 其分子/官能团/元素 + 骨架桥接），既喂实又可控。

## Conclusions / Outcomes
- `11_真实数据/elementkg10m_ingest.py`（两遍流式扫描，顺序无关连接）：抽得 **1602 节点 / 4992 边** = 713 真实分子(PUBCHEM 分子式/分子量/标准SMILES/InChIKey/精确质量) + 800 真实反应 + 76 官能团 + 13 元素 + 6 骨架分子 same_as 桥接。
- Aura 幂等推送：**2077 节点 / 7593 边**（reaction 809 / molecule 753 / functional_group 76 / element 137），校验 6/6 全绿；数学推导链 + 45 低置信边全保留。
- 本地全量图 `graph_data_full.json`（2077/7597）起 viz（http://127.0.0.1:8765/，pid 11192）。
- 前端补 FunctionalGroup 节点样式 + reagent_of/has_functionalgroup/has_element 边配色；graph_export TYPE_PRIORITY 加 FunctionalGroup。
- 看板 + 报告（`07_交付物/Phase8B_ElementKG全量报告_20260930.md`）+ 记忆日志均更新。

## Key Pitfalls
- 单遍顺序连接会因反应行晚于反应物/产物/分子行而解析出 0 分子 → 改为缓冲全部 reaction↔实体、实体↔分子 映射，再按反应出现序取前 N（顺序无关）。
- CSV 仅含 ~13 个有属性元素（其余 118 来自 Phase 8 OWL），故 Phase 8.B 元素桥接仅 13 条，与 Phase 8 互补不重复。
- 18 个「悬空端点」全为预期桥接（same_as 指向 Aura 已有 EK:el:*/PC:mol:*），推上去后自动解析。

## 后续可选
- 扩反应规模 800 → 5k~10k（仍在上限内）；接入合成语料做 LLM 边推断；真实反应↔物理量跨域链；补全全部 118 元素 + 官能团。
