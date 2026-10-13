# DATA_LICENSES.md —— STTP 数据来源与许可登记

> 原则：**Apache-2.0 只覆盖 STTP 原创软件代码，不覆盖、也不重新许可任何上游科学数据。**
> 未明确授权再分发的数据，**不打包进发布物**；代码仓库（git）默认不含上游数据快照
> （`06_PoC/etl/normalized.json`、`graph_data_phase*.json` 等均不入库，见 `.gitignore`）。
> 本表按「实际取用了什么、存的什么字段、能否再分发」逐源登记；标注「待核对」的源，
> 在核对完成前**不进入任何对外分发的数据包**。

最后更新：2026-10-12（随 Apache-2.0 迁移补全，工单 R08）。

## 一、已核对条款的源

| 来源 | 官方条款 | STTP 取用内容 | 再分发立场 |
|---|---|---|---|
| Rhea | **CC BY 4.0**（rhea-db.org/help/license-disclaimer） | 反应方程文本、反应 ID、参与物名称 | 可再分发，须署名 + 版本 + 许可链接 + 修改说明 |
| ChEBI | **CC BY 4.0**（受 EMBL-EBI 使用条款约束） | 本体主名、同义词（OLS4 API 缓存 `chebi_synonym_cache.json`）、分子式 | 可再分发，须署名；与 Rhea 分别登记 |
| ChEMBL | **CC BY-SA 3.0 Unported** | （当前图内如有取用，仅限标识符级引用） | ⚠ 改编数据对外分发须遵守署名 + 相同方式共享；发布前专项核对 |
| Wikidata | **CC0** | 论文条目存在性、P921 主题词 | 可再分发 |
| Crossref / OpenAlex | 元数据 **CC0**（OpenAlex 主体 CC0） | DOI 元数据、参考文献关系（`cites`） | 可再分发 |
| CODATA / NIST 常数 | NIST 数据通常可自由使用（附免责声明） | 基本物理常数数值与单位 | 可再分发，附 NIST 来源声明 |
| Reactome | **CC BY 4.0** | 通路/反应映射（本轮仅可达性侦察，未入库） | 可再分发，须署名 |
| MetaNetX / MNXref | 学术用途免费（见其条款） | 仅做覆盖率侦察，未入库 | 入库前须再核条款 |
| ChEBI OLS4 API | EMBL-EBI 服务条款 | 同义词检索 | 缓存文件随源条款 |

## 二、条款未完全核对、暂不打包再分发的源

| 来源 | 现状 | 处理 |
|---|---|---|
| MathXiv / ArxiTeX | 项目内加工语料，含 arXiv 派生内容；arXiv 各论文许可不一 | **待核对**；不外发原始文本，仅保留内部 ID 与自建结构化结果 |
| PhysicsBabel | 上游数据集，条款未见明示 | **待核对**；不入发布包 |
| ElementKG 1.0 / 2.0 | 上游研究数据集，条款未见明示 | **待核对**；不入发布包 |
| PubChem | 主体可自由使用，部分字段来自第三方 | 逐字段核对前不外发快照 |
| KEGG | 订阅/受限条款 | **不打包、不分发**；仅保留可达性记录 |
| SABIO-RK / BioCyc / MetaCyc / zbMATH / DBLP / OpenAIRE / DataCite / Semantic Scholar | 各有服务条款 | 仅侦察记录，未入库；接入前先核条款 |
| DLMF | NIST，可自由使用（附声明） | 仅函数恒等式文本引用；随 NIST 声明 |

## 三、发布物打包纪律

1. **代码包**：只含 Apache-2.0 原创代码 + `06_PoC/vendor/`（第三方软件，各带许可，
   见 `NOTICE` 与 `06_PoC/vendor/licenses/`）。
2. **演示数据**：只放 STTP 自有/可再分发来源（CC BY/CC0）的小型样例，随附本清单。
3. **完整研究快照**：通过 Release 分发时，逐文件标注来源与许可；含 CC BY-SA（ChEMBL）
   改编内容时单独成包并满足相同方式共享要求。
4. 任何新源接入前，先在本表登记条款结论（「可再分发 / 仅内部 / 禁止」三态）。

---

## 四、本表如何被**程序执行**（Phase 42 · A03）

> ★ 铁律 #64：「政策」必须由程序执行 —— 写在文档里的许可边界若无程序判据，等于没有。

| 环节 | 程序 | 断言 |
|---|---|---|
| 三态表（唯一权威） | `11_真实数据/source_registry.py`：`REDISTRIBUTION` 字典 + `redistribution_closure()`（血缘闭包取上游最严者）+ 自检 ⑧⑨⑩ | ⑧ 覆盖全部来源（未列出 = `forbidden`，fail-closed）⑨ 派生不得宽于上游 ⑩ 与本表文本不脱钩 |
| 打包过滤 | `06_PoC/release/make_sample.py --profile {public_demo,internal_research}`；**默认 `public_demo`** | `--selfcheck` 合成图注入 12 条：未登记/缺来源/`internal_only` 必须真被剔除（非真空） |
| 发布复查 | `06_PoC/release/verify_release.py` A4–A6 | A4 manifest profile 必须 `public_demo`；**A5 独立重算**样例内每条边/每个节点的来源血缘闭包（0 违规）；A6 剔除计数非真空 |

**后果（如实披露）**：全图 48712 边中，`PhysicsBabel`（24104 边）与 `ElementKG2.0`（7797 边）
属本表 §二「待核对、不入发布包」⇒ 发布样例按政策只能给出 **170 节点 / 255 边**
（剔除 43295 边 / 6820 节点）。这是政策生效后的**真实可分发规模**，不是缺陷。
