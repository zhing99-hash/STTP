# STTP 数据集（v1）· 第三方离线校验说明

本目录是一份**自包含**的图谱数据集切片：拿目录、跑 `python3 verify_dataset.py`，
不需要 clone 仓库、不需要装依赖、不需要联网。

    nodes.jsonl      节点，每行一个 JSON 对象
    edges.jsonl      边，每行一个 JSON 对象
    manifest.json    清单：源版本 / 每个文件的 sha256 与字节数 / 计数 / 来源许可 / 已知边界
    SHA256SUMS       与 `sha256sum -c` 兼容的校验清单（不装 python 也能查）
    verify_dataset.py 离线校验器（判据 V0–V9，只依赖标准库）
    ATTRIBUTION.md   来源署名表（CC BY 来源的署名义务落在这里）
    README.md / LICENSE / NOTICE / DATA_LICENSES.md

## 1. 校验（推荐先做这一步）

    python3 verify_dataset.py            # 校验本目录
    echo $?                              # 0 = 全部判据通过

任何一条判据红都会以 `[FAIL] <判据ID> <原因>` 打出来，退出码非 0。

没有 python 时至少可以查字节完整性：

    sha256sum -c SHA256SUMS

## 2. 用 wget 取整份目录

    wget -r -np -nH --cut-dirs=1 -R "index.html*" <数据集URL>/
    cd <目录> && python3 verify_dataset.py

也可以逐个取（清单里 `files[].path` 就是文件名）：

    wget -q -O nodes.jsonl <数据集URL>/nodes.jsonl
    wget -q -O edges.jsonl <数据集URL>/edges.jsonl
    wget -q -O manifest.json <数据集URL>/manifest.json
    wget -q -O SHA256SUMS <数据集URL>/SHA256SUMS
    wget -q -O verify_dataset.py <数据集URL>/verify_dataset.py
    wget -q -O ATTRIBUTION.md <数据集URL>/ATTRIBUTION.md
    wget -q -O README.md <数据集URL>/README.md
    wget -q -O LICENSE <数据集URL>/LICENSE
    wget -q -O NOTICE <数据集URL>/NOTICE
    wget -q -O DATA_LICENSES.md <数据集URL>/DATA_LICENSES.md
    python3 verify_dataset.py

## 3. 记录格式（`sttp-dataset/v1`）

节点：

    {"id":"…","type":"Formula","label":"…","labels":["Formula","Entity"],
     "subject":"物理","domain":"…","formula":"E=mc^2","confidence":0.95,
     "attrs":{…原始属性全量…},
     "license":{"source":"curated_seed","redistribution":"redistributable"}}

边：

    {"id":"…","source":"…","target":"…","type":"has_symbol","kind":"has_symbol",
     "confidence":0.9,"explicit_or_inferred":"explicit","verified":true,
     "verified_strict":false,"verification_level":"source_asserted",
     "evidence":[…],"attrs":{…原始属性全量…},
     "license":{"source":"ChEBI","redistribution":"redistributable"}}

* `type` / `subject` 与可视化投影 `formula-graph-view/v1` 同口径（同一份投影代码）。
* `attrs` 是原始属性的**全量透传**，便于无损回放；`evidence` 同时提升到边顶层便于筛选。
* `license.redistribution` 只有 `redistributable` 才会出现在对外包里（清单 `license_profile`
  为 `internal_research` 时则可能包含 `internal_only` —— 那种包**不得对外分发**）。

## 4. 这份包**不能**证明什么（已知边界）

* 上游原始快照（约 82 MB）按仓库惯例不入包，所以 `manifest.source.sha256` 只是**登记值**，
  本目录里没有它的字节可供重算（`verify_dataset.py` 的 V8 也如实只核对形态）。
* 来源三态（`redistributable / internal_only / forbidden`）的**判定规则**在 STTP 仓库的
  `11_真实数据/source_registry.py` 里。本包含的是**判定结果**（每条记录的 `license` 字段）
  与**逐文件哈希**；它能证明"包内自洽、未被篡改、没有混进非 redistributable 的记录"，
  但无法独立重算出"某个上游来源到底该不该是 redistributable"。
* 每个文件的 sha256 覆盖的是**字节**，不覆盖语义：语义正确性由 `manifest.counts`、
  引用闭合（V4）与上游溯源（V8）共同约束。
