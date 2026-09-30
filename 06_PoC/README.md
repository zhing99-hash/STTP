# 06_PoC · Phase 4 可视化交互闭环（README）

> 本目录把可视化前端（`graph_view.html`）对接到真实图谱数据与查询层
> （`03_知识层/queries.py` 的 NetworkX 后端），形成 **查询 → 渲染** 的交互闭环。
> 不改动 Phase 0–3 已交付源码，仅新增/升级本目录的可视化相关文件。

---

## 1. 文件清单（Phase 4 新增 / 升级）

| 文件 | 类型 | 说明 |
|---|---|---|
| `graph_export.py` | 新增 | 图谱导出：`etl/with_inferred.json` → `graph_data.json`（前端友好格式） |
| `graph_view.html` | **就地升级** | 单文件自包含前端：`fetch('./graph_data.json')` + Cytoscape.js + MathJax |
| `viz_server.py` | 新增 | 标准库 `http.server` 轻量后端（**不引入 Flask**），暴露页面 / 数据 / 查询 API |
| `graph_data.json` | 产物 | 导出结果：**22 节点 / 52 边**（type 非空） |
| `README.md` | 本文 | Phase 4 用法说明 |

数据源：`etl/with_inferred.json`（Phase 3 回写结果，22 节点 / 52 边，含
`verified` / `verification_gate` 字段）。回退：`etl/normalized.json`。

---

## 2. 快速开始

```powershell
cd 01tuopu\06_PoC

# ① 导出前端数据（生成 graph_data.json）
python graph_export.py

# ② 启动轻量后端（默认 127.0.0.1:8765）
python viz_server.py
#   或指定端口： python viz_server.py --port 8080

# ③ 浏览器打开
#   http://127.0.0.1:8765/
```

> 也可以纯静态打开（无查询 API，路径查询走前端 BFS 兜底）：
> 在 `06_PoC` 目录执行 `python -m http.server 8000`，访问
> `http://127.0.0.1:8000/graph_view.html`。
> ⚠️ **不要用 `file://` 直接双击打开**——浏览器 CORS 会拦截 `fetch('./graph_data.json')`。

---

## 3. `graph_export.py`

| 参数 | 默认 | 说明 |
|---|---|---|
| `--input` | `etl/with_inferred.json`（回退 `normalized.json`） | 源图谱 JSON |
| `--out` | `graph_data.json` | 输出路径 |

**输出结构**

```jsonc
{
  "schema": "formula-graph-view/v1",
  "nodes": [
    { "id": "...", "label": "...", "type": "Symbol|Definition|Theorem|Lemma|Formula",
      "labels": ["Formula","Theorem"], "subject": "数学", "domain": "math.DG",
      "formula": "<latex>", "confidence": 1.0, "attrs": { ...原始 props... } }
  ],
  "edges": [
    { "source": "...", "target": "...", "type": "derived_from",
      "kind": "explicit_citation|llm_inferred|...", "confidence": 0.96,
      "explicit_or_inferred": "explicit|inferred",
      "verified": true, "gate": "R-MATH", "evidence": "..." }
  ],
  "meta": { "node_count": 22, "edge_count": 52, "node_types": {...}, ... }
}
```

**要点**

- `type` **来自节点的 `labels` 字段**（取最具体标签），并对空值做强制修正 +
  告警——修复此前 Kuzu 演示的空类型问题。自检行会打印 `type 非空 : 通过 ✅`。
- 不做任何裁剪：`nodes` / `edges` 与源文件一一对应（22 / 52），便于核对。
- `meta.dangling_endpoints` 列出「出现在边上但不在节点表内」的端点
  （本图为 14 个 Phase 3 LLM 假设涉及的外部实体），前端会为其合成 ghost 节点。

---

## 4. `graph_view.html`（前端）

**数据加载**：页面载入即 `fetch('./graph_data.json')`，随后初始化 Cytoscape.js。

**控制面板**

| 控件 | 作用 |
|---|---|
| **节点搜索** | 按 `id / 名称 / 类型 / LaTeX` 子串匹配 → 高亮 + 居中（回车或点「搜索」） |
| **节点类型** | checkbox 过滤（Definition / Theorem / Lemma / Symbol ...，来自真实数据） |
| **边类型 type** | checkbox 过滤（`derived_from` / `defines` / `has_symbol` / `chemical_reaction` / `dimensionally_consistent` / `proves`） |
| **最小置信度** | 滑块，隐藏 `confidence <` 阈值的边 |
| **仅显示 verified 推断边** | 只保留 `explicit_or_inferred=inferred` 且 `verified=true` 的边 |
| **explicit/inferred 区别着色** | 开：蓝实线=explicit、橙虚线=inferred；关：按边类型着色（线型仍区分） |
| **显示 ghost 外部节点** | 是否渲染边端点合成出的外部实体 |
| **布局切换** | `cose` / `grid` / `concentric` / `circle` |
| **路径查询** | 输入起终点 id → 高亮最短路径（见下） |

**视觉编码**

- 节点：填充色 = 学科（数学蓝 / 物理橙 / 化学绿 / 跨学科灰紫），描边色 + 形状 =
  类型，尺寸 ~ 度数；ghost 节点描边为虚线。
- 边：**宽度 ~ confidence**（越高越粗）；颜色按来源（蓝 explicit / 橙 inferred）；
  **线型：实线 = explicit，虚线 = inferred**。
- **tooltip**：鼠标悬停边显示 `type / kind / confidence / gate / evidence / rationale`。
- **详情面板**：点击节点，用 **MathJax** 渲染 `formula`（LaTeX），并展示 id、
  定义、证明、来源、度数、相连关系（可点击跳转）。

**路径查询闭环**

1. `GET /api/path?src=&dst=`（后端 `queries.paths_between`）；成功即用返回值。
2. 无后端 / 请求失败 → **前端 BFS 兜底**（有向，≤5 跳）。
3. 高亮最短路径，并列出前 5 条路径。

**离线降级**：Cytoscape.js / MathJax 走 CDN。**离线时**自动降级为纯文本图谱
（列出全部节点与边），并提示改用 `viz_server.py`——**不会崩溃**。

---

## 5. `viz_server.py`（后端）

仅用 Python 标准库 `http.server`（**无 Flask**）。查询层通过 `sys.path` 注入
`03_知识层`，并对 `networkx` / `queries` 做 import 守卫。

| 方法 | 路径 | 说明 | 响应 |
|---|---|---|---|
| GET | `/` | 返回 `graph_view.html` | `text/html` |
| GET | `/graph_data.json` | 导出的图谱数据（缺失时即时调用 `graph_export`） | `application/json` |
| GET | `/api/neighbors?node=<id>` | 调 `queries.get_neighbors`（NetworkX 后端） | `{node, degree, neighbors[], edges[]}` |
| GET | `/api/path?src=<id>&dst=<id>` | 调 `queries.paths_between`（NetworkX 后端） | `{src, dst, count, paths[][]}` |
| GET | `/api/stats` | 图谱统计 | `{backend, nodes, edges, node_types, edge_types, explicit_or_inferred, verified_edges}` |

- 绑定 `127.0.0.1:<port>`（默认 **8765**，可用 `--port` / 环境变量 `VIZ_PORT`）。
- 所有响应带 **CORS** 头（`Access-Control-Allow-Origin: *`）便于跨源打开。
- 未知路径 → **404**（JSON）；`Ctrl+C` 优雅退出。
- `/api/stats` / `/api/neighbors` 的节点数包含边端点合成的隐式节点
  （NetworkX 后端会为「仅在边上出现」的实体建点），故为 **36 节点**；
  而 `graph_data.json` 严格为节点表中的 **22 节点**。二者语义不同，见下方说明。

---

## 6. 与 `queries.py` / Neo4j 的关系

- **接口一致性**：`viz_server.py` 直接调用 `queries.py` 的公开函数
  （`get_neighbors` / `paths_between`），产出结构与双后端一致。
  Neo4j 可用时，`queries.get_backend("auto")` 会优先走 Cypher；本 PoC 默认
  用 `get_backend("nx", input_path=with_inferred.json)` 走 **NetworkX 兜底**，
  无需 JVM/数据库即可完整演示闭环。
- **数据源口径**：前端 `graph_data.json` 与查询层后端都指向
  `with_inferred.json`，保证「看到的图」与「查到的路径」同源。
- **生产态**：接入真实 Neo4j 时，仅需把 `get_backend` 切到 `auto` / `neo4j`
  （或用 `queries.cypher` 里的 Cypher 重实现端点），前端无需改动。

### 关于「22 节点 vs 36 节点」

`with_inferred.json` 的节点表 22 个；但 Phase 3 的 LLM 假设边引用了 14 个
**外部实体**（如 `MX:phy:kinetic_energy`、`MX:chem:combustion`），它们未收录在
`nodes[]` 中。于是：

- `graph_data.json`：严格 **22 节点 / 52 边**（与源文件一致，便于核对）。
- 前端：为 14 个外部端点合成 **ghost 节点**（虚线描边），使 52 条边全部可见。
- NetworkX 查询层：`MultiDiGraph` 会将这些端点建成隐式节点 → **36 节点**。

这是数据侧「边端点未入库」的既有现象，不是导出/渲染缺陷；如需彻底消除，
应在 Phase 2/3 的 ETL 中把 LLM 假设涉及的外部实体一并注册为节点。

---

## 7. 依赖

- Python 3.11，`networkx`（已装）。
- 前端 CDN：Cytoscape.js 3.30.2、MathJax 3（**需联网**；离线自动降级）。
- **不引入 Flask** 或任何第三方 Web 框架。

---

*Phase 4 · 可视化与前端专家 · 2026-09-28*
