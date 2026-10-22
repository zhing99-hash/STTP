# 数据集导出（F5 · Phase 70）

> **一句话**：`bash sttp.sh dataset --e2e` 产出一个**可被第三方 `wget` 下来、断网校验**的数据集目录
> （JSONL + 清单 + sha256 + 随包校验器），并把这条链路本身跑一遍证明它真的成立。

---

## 1. 为什么要有这个目录

规划里 F5 的验收原文是「目录可被第三方 `wget` 后**离线校验通过**」。
这句话可以拆成三个**必须真做**的动作，少一个就只剩自述：

| 动作 | 怎么证明 | 判据 |
|---|---|---|
| 真的能 `wget` 下来 | 起 127.0.0.1 静态服务，按清单逐文件纯 HTTP GET | `wget_offline_e2e.py` E1/E5 |
| 下下来还是同一份 | 下载副本与**源目录**逐字节比 sha256（不跟清单比） | E2 |
| 断网也能校验 | 子进程里用 `sitecustomize` **打断 `socket.connect`** 再跑校验器 | E3 |
| 校验器不是空跑 | 在下载副本上改一个字节，必须变红 | E4 |

第三条是本目录存在的主要理由：**「我没看到它联网」不等于离线**。
只有把 socket 打断、它还能跑完，才叫离线。

## 2. 怎么用

```bash
bash sttp.sh dataset              # 构建到 06_PoC/dataset/dist/（约 16 MB，不入库）
bash sttp.sh dataset --e2e        # 构建 + 真的下载一遍 + 断网校验（F5 验收动作）
bash sttp.sh dataset --selfcheck  # 只跑许可判据可红性自检（合成图，秒级）
python 06_PoC/dataset/verify_dataset.py 06_PoC/dataset/dist   # 单独校验已有产物
```

第三方拿到目录后只需要：

```bash
python3 verify_dataset.py      # V0–V9，全过才退 0
sha256sum -c SHA256SUMS        # 没有 python 时的最低限度
```

## 3. 里面有什么

| 文件 | 作用 |
|---|---|
| `export_dataset.py` | 构建器：读权威快照 → 许可分区 → 投影为记录 → 写 JSONL/清单/校验和 → **自验后才提升** |
| `verify_dataset.py` | **随包**的离线校验器（V0–V9）。**只依赖标准库**，不 import 仓库任何模块 |
| `wget_offline_e2e.py` | F5 端到端：静态服务 + 逐文件下载 + 断网校验 + 负对照 |
| `DIST_README.md` | 会被复制进 `dist/README.md`，是**给第三方看**的说明（含 wget 命令与格式） |
| `dist/` | 构建产物（`.gitignore` 排除：`06_PoC/dataset/dist*`） |

## 4. 三条「不造第二套口径」

1. **许可**：直接调 `06_PoC/release/make_sample.py::license_partition`。来源三态表只有
   `11_真实数据/source_registry.py` 一份；这里不重写「谁能再分发」。
2. **投影**：直接调 `06_PoC/graph_export.py::project_node / project_edge`，与可视化
   `formula-graph-view/v1` 同一份投影代码。
3. **产物清单**：`export_dataset.py::FILES` 一处声明，写盘 / 清单 / 校验都从它派生，
   并用 **S4 反向断言**「声明集 == 实际写出集」防止三处漂移。

> 反向教训（同一天踩到）：S2（许可后置审计）第一次跑就红了 —— 我把**投影后的记录**
> 喂给了 `audit_redistribution`，而它要的是带 `props.source` 的**规范图**，于是 16,251 条
> 被读成「来源 `<none>`」。**判据没错，是接线错**。判据第一次运行就把自己的接线抓出来，
> 这是它该有的样子。

## 5. 判据一览

构建侧（`export_dataset.py`）：

| ID | 断言 |
|---|---|
| S1 | 清单登记的源快照 sha256 == 构建后**重新现算**值 |
| S2 | 许可后置审计 0 违规（`audit_redistribution` 独立重算，不听分区函数自述） |
| S3 | 分区计数自洽（`excluded == 总 - 保留`） |
| S4 | 清单声明集 == 目录实际写出集（双向） |
| S5 | **确定性**：同输入二次全量重建逐字节相同 |
| S6 | 随包校验器在临时产物上 0 FAIL（不通过 ⇒ **不提升**，RC=3） |
| S7 | 每条边的两端都在导出节点集内 |

校验侧（`verify_dataset.py`，V0–V9）：清单可解析 / 文件集齐备 / 逐文件 sha256 / JSONL 结构与行数 /
引用闭合与 id 唯一 / 许可一致 / 计数一致 / `SHA256SUMS` 自洽 / 上游登记形态 / 署名落地。

自检：`--selfcheck` E1–E5（许可判据可红）、`verify_dataset.py --selftest` VS1–VS9（九条判据逐项注入缺陷）。

## 6. 已知边界（写在清单 `boundary` 里，不藏在文档）

* 上游快照约 82 MB 不入包 ⇒ **V8 只能核对登记值，不能重算上游哈希**。
* 三态判定规则在仓库里；包里只有**判定结果** + 逐文件哈希。它证明「包内自洽、未被篡改、
  没有混进非 redistributable 的记录」，但无法独立重算「某个上游到底该不该可再分发」。
* sha256 覆盖**字节**不覆盖语义：语义由 counts（V6）、引用闭合（V4）、上游溯源（V8）共同约束。
