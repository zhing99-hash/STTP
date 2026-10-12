# STTP · 跨学科公式知识图谱（数理化拓扑图）

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

> **S**cience **T**opology **T**hrough **P**hysics — 把数学公式、物理规律、化学物质与物理世界（元素 / 分子 / 反应 / 单位 / 物理量）连成**一张可推理的知识图谱**。

本项目把"公式"与"物理世界"打通：从 E=mc²、F=ma、动能定理，一路连到甲烷燃烧反应、原子组成、国际单位制，并在图谱上用 **GNN 推断 + LLM 假设 + 符号校验（SymPy / pint / RDKit-等价逻辑）** 自动发现并验证跨域边。

---

## 1. 架构（四层）

```
数据层 (MathXiv / PhysicsBabel / ElementKG 2.0 / PubChem / Wikidata)
        │  分而治之的异构数据源适配器
        ▼
知识层 (Neo4j 图数据库 / 本地 NetworkX)
        │  节点: Formula / MathConcept / Symbol / PhysicalQuantity /
        │        Element / Molecule / Reaction / Unit / Constant / FunctionalGroup
        │  边:   derived_from / proves / defines / has_symbol /
        │        dimensionally_consistent / reactant_of / product_of /
        │        composed_of / same_as / has_unit / ...
        ▼
推理生成层 (GNN 链接预测 + LLM 假设 + 符号校验门控)
        │  对 55 万跨域候选对排名，三道门控降权不可证边
        ▼
交互层 (D3 / Cytoscape.js 可视化 + 标准库 HTTP 服务)
```

- **Schema v0.1**：全局 id 约定 `<source>:<local_id>`（MX/MG/PB/EK/WD/PC/PQ/CM/EM/TH/BC/OM/IC…）。
- 每条边带 `confidence / source / explicit_or_inferred / verified / verification_gate`，推断边一律**标注置信度并降权**（可证才证，不可证诚实保留 `NEEDS_REVIEW`）。

---

## 2. 仓库结构

| 目录 | 内容 |
|------|------|
| `00_项目管理/` | 团队分工、看板与进度 |
| `01_架构与数据模型/` | 架构、Schema 与数据源技术文档 |
| `03_知识层/` | Neo4j 知识层脚本、`.env.example`、本地部署说明 |
| `06_PoC/` | 端到端 PoC：`graph_export.py`（权威 raw→viz 转换器）、`viz_server.py`、`build_phase*.py`（各阶段构建器）、`robust_aura_loader.py` |
| `06_PoC/release/` | **可复现发布包**：离线样例（380 节点 / 701 边）+ 发布清单 + fail-closed 校验器；见其 [README](06_PoC/release/README.md) |
| `07_交付物/` | 各 Phase 部署 / 接入 / 推理报告 |
| `08_部署包/neo4j/` | 自包含 Docker + Neo4j 部署包（`deploy.py` 一键编排） |
| `09_科研扩展/9_inference/` | Phase 9 推理生成层：GNN 推断、LLM 假设与复核、Aura 导出 |
| `10_种子数据/` | 13 个垂直切片种子脚本（`build_*_mechanics.py` 等）+ 共享构造器 `seed_common.py` |
| `11_真实数据/` | 真实数据源适配器：`elementkg_ingest.py`（ElementKG 2.0 OWL）、`physicsbabel_ingest.py`（PhysicsBabel parquet）、`pubchem_mol_ingest.py`（PubChem） |
| `12_评测/` | **科学任务评测集**：40 题冻结任务集（热力学-化学平衡-数学）+ 规则基线运行器 + 非真空自检；见其 [README](12_评测/README.md) |
| `*.md`（根） | 各 Phase 进展与归档 |

> 大体积原始数据集（ElementKG 2.0 全集 CSV、合成语料、PhysicsBabel parquet、OWL 本体）及生成的导出图 JSON **不纳入版本库**，见 `.gitignore` 与 `07_交付物/` 内各数据源文档。

---

## 3. 快速开始

### 3.0 离线可复现（推荐第一站：**不需要任何数据文件**）

完整图（`normalized.json`，48 MB）与阶段快照**不入库**。为了让第三人能从文档独立运行，
仓库自带一个**自包含的离线研究样例**（跨域桥核心切片：380 节点 / 701 边 / 154 条跨学科边）：

```bash
bash sttp.sh release     # ① 构建样例 → 生成发布清单 → fail-closed 校验（25 条断言）
bash sttp.sh sample      # ② 只用样例起可视化 → http://127.0.0.1:8765/
```

`sample` 会把**页面数据**（`GRAPH_DATA_FILE`）与**查询后端**（`GRAPH_SOURCE_FILE`）
指向**同一份切片的两种投影**，并由校验器断言两者 **id 集合逐元素相等** ——
即「**页面和查询属于同一数据版本**」。已知反例（冻结 90 条，其中 65 条自包含、
样例模式可跑且 **SKIP=0**）见 [发布包 README](06_PoC/release/README.md)。

| 命令 | 用途 |
|---|---|
| `bash sttp.sh release` | 构建离线样例 + 发布清单 + fail-closed 校验 |
| `bash sttp.sh sample` | 仅用离线样例起可视化（单命令、离线、同版本） |
| `bash sttp.sh release-full` | 对完整快照跑全量校验（清单现算比对 + 主图 `--release` 门禁） |
| `bash sttp.sh eval` | 科学任务评测集：构建冻结任务集 + 规则基线 + 非真空自检 |

完整快照的获取与校验、依赖锁（`requirements.lock`）与口径披露，见
[`06_PoC/release/README.md`](06_PoC/release/README.md)。
科学任务评测集的任务构成、候选空间 / 拆分规则 / 随机种子与**诚实边界**，见
[`12_评测/README.md`](12_评测/README.md)。

### 3.1 本地 Neo4j（推荐先跑通）

```bash
# 1. 启动 Neo4j（Docker）
cd 08_部署包/neo4j
cp .env.example .env        # 按需修改密码/端口
docker compose up -d

# 2. 灌入图谱（自动建约束/索引 + 幂等 MERGE）
pip install neo4j pandas
python load_neo4j.py --input <your_graph.json>

# 3. 启动可视化前端
cd 06_PoC
python viz_server.py        # http://127.0.0.1:8765/
```

### 3.2 Aura 云（Neo4j 免费实例）

无 Docker / Java 环境时可用 AuraDB Free：

```bash
# 必须用 ssc 方案跳过自签名证书，并指定 database=<instance-id>
export NEO4J_URI="neo4j+ssc://<your-instance>.databases.neo4j.io"
export NEO4J_USER="<your-username>"
export NEO4J_PASSWORD="<your-password>"
export NEO4J_DATABASE="<your-database>"
python 06_PoC/robust_aura_loader.py --input <your_graph.json>
```

`graph_export.build_graph_data(raw_path)` 是**权威转换器**：所有 viz JSON 都必须经它生成（手工 merge 会破坏节点类型 / 学科着色）。

---

## 4. 数据来源

| 学科 | 数据源 | 备注 |
|------|--------|------|
| 数学 | MathXiv / ArxiTeX、uw-math-ai/math-graph | 公式 / 概念 / 推导链 |
| 物理 | PhysicsBabel（HuggingFace `RANDMEDIATION/PhysicsBabel`）、Vashy | 真实维度自洽方程，`plausible=True` 过滤 |
| 化学 | ElementKG 2.0（OWL 本体 + 10M 三元组 CSV）、PubChem PUG-REST | 真实元素 / 分子 / 反应 |
| 底座 | Wikidata SPARQL | `same_as` 跨源对齐 |

---

## 5. 推理生成层（Phase 9）

在真实大图（2000+ 节点 / 数万边）上：

1. **桥接导向 GraphSAGE** 链接预测（正样本 = 已有跨域边，负样本 = 跨域非边）。
2. 三类符号门控：**R-PHY**（pint 量纲）、**R-CHEM**（组成 / 反应方程解析）、**R-MATH**（SymPy）。
3. 产出跨域候选边，可符号证实的标 `VERIFIED`，其余诚实标 `NEEDS_REVIEW` 留给人工 / LLM 复核。

> 经验：真实数据已吃掉大部分可证跨域关系，纯符号可证的新桥有限——这正是"推断边须降权"原则的规模化落地。

---

## 6. 许可

本项目采用 **Apache License 2.0** 开源 —— 见仓库根 [`LICENSE`](LICENSE)；
第三方组件（前端 vendor 库）与数据来源的署名见 [`NOTICE`](NOTICE)。

**范围声明**：STTP **原创软件代码**采用 Apache-2.0；**第三方软件**（`06_PoC/vendor/`，
许可文本在 `06_PoC/vendor/licenses/`）与**科学数据**分别遵循各自许可，
详见 [`NOTICE`](NOTICE)、[`DATA_LICENSES.md`](DATA_LICENSES.md)。
上游科学数据（Rhea / ChEBI / ChEMBL / PubChem 等）的条款不因本项目的
Apache-2.0 而改变，本项目也不对其重新许可。

**许可证迁移**：2026-10-11 之前的历史版本按 **MIT** 分发（见 git 历史）；
自提交 `8e12d3e` 起，原创代码改用 Apache-2.0。此前已按 MIT 取得代码者，
其已获权利不受影响（详见 `NOTICE` 的迁移说明）。

```
Copyright 2026 zhing

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```
