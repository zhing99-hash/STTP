# `contracts/` —— 局部图谱查询的**机器可校验**契约

> Phase 43（P1-A）产物。四个 JSON Schema 描述 `06_PoC/viz_server.py` 四个只读端点的**响应体**。
> 目的：把「接口长什么样」从**文档里的散文**变成**可被程序断言的对象** —— 一旦实现漂移，自检立刻变红（铁律 #38「PASS 只说明当前没红」的反面用法：**要能红**）。

## 文件

| 文件 | `$id` | 端点 | 说明 |
|---|---|---|---|
| `subgraph.schema.json` | `sttp-subgraph/v1` | `GET /api/subgraph` | ★ 核心。五态信封 + `nodes/edges/expandable/stats` |
| `search.schema.json`   | `sttp-search/v1`   | `GET /api/search`   | 文本搜索（命中 + 分数） |
| `overview.schema.json` | `sttp-overview/v1` | `GET /api/overview` | 概览元图（`aggregate` / `sample`） |
| `error.schema.json`    | `sttp-error/v1`    | 全部端点 | 错误信封（`status/reason/error_code/snapshot_id`） |
| `evidence.schema.json` | `sttp/evidence.schema.json` | **（非端点）** | ★ **Phase 45 · A04** 版本化证据 v2：`evidence_id` / `claim_version` / `input_refs[]` / `reference_refs[]` / `verifier{dependency_versions}` / `status ∈ {valid,stale,needs_review,rejected}` / `status_reasons[]` / `supersedes_evidence_id`。由 `11_真实数据/evidence_deps.py --check` 与 `sttp.sh evdeps` 覆盖 |

## 校验器（零第三方依赖）

`06_PoC/tests/phase43_subgraph_selfcheck.py` 内置一个 **JSON-Schema 子集**校验器。

**已实现的关键字**（`validate()` 的完整支持集，共 13 个）：
`$ref` / `$defs` / `type`（含联合类型数组）/ `required` / `properties` / `additionalProperties` / `items` / `enum` / `pattern` / `minLength` / `minimum` / `maximum`

★ `additionalProperties` 是 **Phase 45 补齐**的（原为"已知边界"）：A04 契约用它约束 `evidence[].refs`
的**动态键**（`{ref_id: 哈希}`，`properties` 无法表达）。补齐后自检新增 **C5b2/C5b3** 反例/正例各一条，
证明该约束**真的被执行**（不是只加进"支持集"）。
（外加 `$schema` / `$id` / `title` / `description` 这类无校验语义的元数据）。

**未实现且不会被静默放过**：自检 **C5b** 会递归收集四份 schema 里出现的**全部**关键字，断言 ⊆ 上述支持集。
⇒ 若将来有人往 schema 里写了 `anyOf` / `const` / `minItems`，C5b 立刻变红 —— 因为**写了但不检查的约束比没写更危险**。

**为什么不用 `jsonschema` 包**：仓库定位是 Apache-2.0 的**零额外运行时依赖**研究原型（根目录无 `pyproject.toml`/`requirements` 声明此类依赖）；为校验 4 个小 schema 引入一个第三方依赖不划算，且会让「干净检出能否跑自检」多一个失败模式。

## 纪律

1. **契约是单源**：`queries.py` 的返回值是 schema 的**唯一**来源；`viz_server.py` 只做投影，**不得**在路由层增删字段。
2. **可红性必须被证明**：自检 `C5` 会**故意注入非法枚举值**，断言校验器**变红**。校验器恒真 = 比没有校验器更危险（铁律 #14「自检输入同源会一起静默通过」）。
3. **schema 不描述业务语义**（例如 stale 的定义、`why_shown` 的取值含义）——那些在 `00_项目管理/前端局部图谱浏览方案_20261013.md` §5.2 与 `07_交付物/Phase43_*.md`。
4. 改 schema ⇒ **必须**同步改 `phase43_subgraph_selfcheck.py` 的 C1–C4 与前端 `js/`（步骤 B）；两者都在 `bash sttp.sh subgraph` / `sttp.sh release` 的覆盖范围内。
