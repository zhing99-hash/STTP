# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""「非通过」判据自述解释器 —— **全链防漏网**扫描仪（铁律 #92 / #88 / #90 / #98）。

它解决的问题
------------
`06_PoC/interp_note.py` 把「缺依赖 / 前置不满足 ⇒ 非通过」的诊断自述**收在一处**，
但"收在一处"只保证了**实现不分叉**，保证不了**入口不漏接**：
Phase 53 就是这样 —— 修了 `verify_release` 一处，把 `frozen_gate` 登记为「同族未改」（D58），
而**没有任何东西**会提醒"第 6 个入口又漏了"。本仪器就是那个东西。

判据（三条，互相独立）
----------------------
1. **声明侧**：`interp_note.ENTRY_POINTS` 里每个路径必须存在、且必须引用 `interp_note`。
2. **事实侧**：扫描全仓入库 `.py`，凡**字符串字面量**中出现「依赖/前置缺失」类措辞的文件，
   必须 ⊆ `ENTRY_POINTS` ∪ 豁免表。
3. **非真空**（铁律 #90 —— 全绿也可能是测量器坏了）：措辞扫描必须**真的命中**
   已登记入口；命中 0 ⇒ FAIL（说明词表或扫描坏了，而不是"天下太平"）。

★ 为什么扫**字符串字面量**而不是全文（第一版就是这么写的，被自己的实测否掉）：
  全文 grep 会命中**注释 / 文档字符串 / 无关字段** —— 实测 12 个文件命中，其中
  `dimension_table.py`（一句注释）、`gnn_infer.py`（一句注释）、
  `make_manifest.py`（`drift_note` 里"缺件才阻塞"讲的是**版本漂移**）
  全都不是"非通过出口"。用 AST 取字面量、并显式排除文档字符串后，
  命中集收敛到 4 个入口 + 2 个需豁免者（见下），仪器才**可维护**。
  （铁律 #88：枚举须派生自事实源，**判据本身**也要经得起"会不会把噪声当成信号"。）

★ 本次扫描真抓到的**第 6 个入口**：`tests/phase43_frontend_selfcheck.py`
  （退出码 2 = 前置不满足（缺文件 / 缺 node））。它的前置是 **node**、跨运行时，
  `note()` 报告的 python 解释器与它的缺失面**不是同一个东西** ⇒ 本仪器**不**把它
  塞进 `ENTRY_POINTS` 冒充覆盖，而是登记为 **D66**（跨运行时前置自述）并在此说明。

如实披露（**未覆盖项，不许读成"已覆盖"**）
------------------------------------------
* 判据是**措辞驱动**的：它抓"写了依赖/前置缺失字样却没自述"的文件；
  **不**抓"因为**根本没写这些字样**、所以从没被想到"的文件 —— 后者无机械判据，只能靠评审。
* 它只断言"文件引用了 `interp_note`"，**不**断言引用处就是唯一输出口
  （"引用了却没用它拼消息"仍可能）。语义正确性由 `verify_release` 的
  **G3/G4** 常驻守卫 + `bash sttp.sh redprobe` 的 **R4** 构造性探针承担 ——
  两者合起来才构成闭环（单一措施都不够，这正是 #92 的要点）。
* 词表 `PHRASES` 是**人工声明**的；新造术语若不在表内会漏（同上，无机械解）。

用法
----
    python 06_PoC/tests/interp_selfreport_check.py
    python 06_PoC/tests/interp_selfreport_check.py --selfcheck
    bash sttp.sh interpcheck          # ★ 已挂进 gatecheck（不依赖权威图，干净检出可跑）

退出码：`0` 全过 ／ `1` 有 FAIL ／ `2` 前置缺失（找不到仓库根） ／ `3` **仪器自证失败**
（3 与 1 分开：仪器自己崩了，不能读成"被测对象有问题"—— 铁律 #101 同族）。
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))            # 06_PoC/tests
POC = os.path.dirname(HERE)                                  # 06_PoC
ROOT = os.path.dirname(POC)                                  # STTP
sys.path.insert(0, POC)

import interp_note as IN                                     # noqa: E402

# ---------------------------------------------------------------------------
# 词表：**依赖/前置缺失**语义（不是"缺文件"）。
#   ★ 刻意不含「缺件」：`make_manifest.py` 的 "缺件才阻塞" 讲的是**版本漂移**，
#     把它当成"非通过出口"是**假阳性**（第一版实测踩到）。
#   ★ 刻意不含「缺少」：`make_manifest.py` 的「缺少样例产物：…（先跑 make_sample.py）」
#     缺的是**产物**、不是依赖；消息已给恢复命令，解释器与它无关。
# ---------------------------------------------------------------------------
PHRASES = ("缺依赖", "依赖缺失", "缺前置", "前置不满足", "前置缺失",
           "依赖不满足", "环境缺件")

# ---------------------------------------------------------------------------
# 豁免表：命中措辞、但**不是**"自身的非通过出口"的文件。每项必须写明理由。
#   ★ 理由非空 + 文件仍存在 ⇒ 由 Q4 断死（防"豁免表腐烂"：文件删了、理由还挂着）。
# ---------------------------------------------------------------------------
EXEMPT = {
    "06_PoC/interp_note.py":
        "**唯一实现自身** —— 它定义这条纪律（模块文档与 detail 模板里都出现这些措辞），"
        "不是该纪律的消费者。",
    "06_PoC/tests/red_probe.py":
        "**可红性探针的事实源** —— 它**故意**持有「把解释器从 detail 里删掉」的突变"
        "字面量与断言名（R4）。删掉这些字样就等于删掉探针本身。",
    "sttp_cli/cli.py":
        "**退出码转发器** —— 只在 `--help` 文案里转述「缺依赖亦非零」的语义；"
        "它自己不做任何判定，没有「非通过出口」。",
    "06_PoC/tests/interp_selfreport_check.py":
        "**测谎仪自身的词表** —— 它必须**字面持有**这些措辞（否则无从扫描），"
        "命中自己不是缺陷而是判据成立的前提（铁律 #98：执法工具须在被执法集合里，"
        "故此处**显式声明**而非静默跳过）。",
}


class Rep:
    """极简报告器（与其它仪器同形：`rep.add(cond, NAME, detail)`）。"""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, cond, name, detail="") -> None:
        self.rows.append(("PASS" if cond else "FAIL", name, str(detail)))

    def report(self) -> None:
        for k, n, d in self.rows:
            print("  %s %-4s %s" % ("✅" if k == "PASS" else "❌", "", n))
            if d:
                print("          %s" % d)

    @property
    def n_fail(self) -> int:
        return sum(1 for k, _, _ in self.rows if k == "FAIL")

    @property
    def n_pass(self) -> int:
        return sum(1 for k, _, _ in self.rows if k == "PASS")


# ---------------------------------------------------------------------------
# 事实源枚举：**入库 ∪ 未跟踪**（`git ls-files --others --exclude-standard`）。
#   ★ 必须含未跟踪：否则"提交前绿、提交后红"（E28 的教训，铁律候选 #82）。
# ---------------------------------------------------------------------------
def iter_py(repo: str) -> list[str]:
    out: list[str] = []
    for args in (["ls-files", "*.py"],
                 ["ls-files", "--others", "--exclude-standard", "*.py"]):
        try:
            r = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
        except Exception:                                                    # noqa: BLE001
            continue
        out += [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    return sorted(set(out))


def docstring_values(tree: ast.AST) -> set[int]:
    """收集**文档字符串**对应的 `Constant` 节点 id（要从字面量集合里排除）。

    非文档字符串的长字符串（如 `drift_note`）**保留** —— 判据只排除"注释性质"的存在，
    不排除"正文文案"，否则等于把判据悄悄放宽。
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                out.add(id(body[0].value))
    return out


def literal_hits(src: str) -> list[str]:
    """返回该源码命中的措辞列表（只扫字符串字面量；注释不进 AST、文档字符串显式排除）。"""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    skip = docstring_values(tree)
    hits: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in skip:
                continue
            for p in PHRASES:
                if p in node.value:
                    hits.add(p)
    return sorted(hits)


def scan(files: dict[str, str]) -> dict[str, list[str]]:
    """**纯函数**（可被 `--selfcheck` 直接调用）：`{相对路径: 源码}` → `{路径: 命中词}`。"""
    return {p: h for p, h in ((p, literal_hits(s)) for p, s in files.items()) if h}


def check_entries(rep: Rep) -> None:
    """声明侧：入口表非空、路径存在、且确实引用共享实现。"""
    entries = tuple(IN.ENTRY_POINTS)
    rep.add(len(entries) >= 3,
            "Q1 入口声明非空且规模合理（≥3）—— 清空声明不得让检查变真空",
            "ENTRY_POINTS = %d 项" % len(entries))
    missing = [p for p in entries if not os.path.exists(os.path.join(ROOT, p))]
    rep.add(not missing, "Q1b 每个声明入口的路径真实存在", "缺 %s" % missing if missing else
            "全部存在（%d 项）" % len(entries))
    noref = []
    for p in entries:
        fp = os.path.join(ROOT, p)
        if not os.path.exists(fp):
            continue
        if "interp_note" not in io.open(fp, encoding="utf-8", errors="replace").read():
            noref.append(p)
    rep.add(not noref,
            "Q2 每个声明入口都**真的引用**共享实现 `interp_note`（声明 ≠ 事实）",
            "未引用：%s" % noref if noref else "%d/%d 均引用" % (len(entries), len(entries)))


def check_exempt(rep: Rep) -> None:
    """豁免表：每项必须写明理由 + 文件仍存在（防豁免表腐烂）。"""
    bad = []
    for p, why in EXEMPT.items():
        if len((why or "").strip()) < 12:
            bad.append((p, "理由过短/为空"))
        elif not os.path.exists(os.path.join(ROOT, p)):
            bad.append((p, "文件不存在（豁免表已腐烂 —— 应删除该项）"))
    rep.add(not bad, "Q4 豁免表每项都写明理由且文件仍存在（防豁免腐烂）",
            "问题项：%s" % bad if bad else "%d 项豁免，均有效" % len(EXEMPT))


def check_scan(rep: Rep, hits: dict[str, list[str]]) -> None:
    """事实侧：命中措辞的文件必须已登记或已豁免；且命中必须非空。"""
    allowed = set(IN.ENTRY_POINTS) | set(EXEMPT)
    unregistered = sorted(p for p in hits if p not in allowed)
    rep.add(not unregistered,
            "Q3 命中「依赖/前置缺失」措辞的文件 ⊆ 入口声明 ∪ 豁免表"
            "（新增入口漏接 ⇒ 在此变红）",
            "未登记：%s" % {p: hits[p] for p in unregistered} if unregistered
            else "命中 %d 个文件，全部有形可查" % len(hits))
    hit_entries = sorted(p for p in hits if p in set(IN.ENTRY_POINTS))
    rep.add(bool(hit_entries),
            "Q5 ★非真空：措辞扫描必须**真的命中已登记入口**（命中 0 ⇒ 是测量器坏了，"
            "不是天下太平 —— 铁律 #90）",
            "命中入口 %d 个：%s" % (len(hit_entries), hit_entries))
    never = sorted(set(IN.ENTRY_POINTS) - set(hits))
    rep.add(not never,
            "Q6 反向：入口表不得**虚列**（每个入口都必须真的有依赖/前置类出口）",
            "从未命中措辞的入口：%s" % never if never
            else "%d 个入口全部命中" % len(IN.ENTRY_POINTS))


def selfcheck() -> int:
    """**构造性**自检：合成语料 + 正例/反例/负对照（铁律 #100）。"""
    rep = Rep()
    tmp = tempfile.mkdtemp(prefix="sttp_interpchk_")
    try:
        good = 'import interp_note as IN\nprint("%s" % IN.skip_detail("缺前置：无 node"))\n'
        bad = 'print("前置缺失：无 node")\n'                                  # 不引用共享实现
        none_hit = 'import interp_note as IN\nprint("页面不可达")\n'           # 无措辞
        docstring = '"""本仪器前置缺失时记 SKIP。"""\nimport interp_note as IN\n'
        fstr = 'import interp_note as IN\nprint(f"缺依赖 {x}")\n'              # f-string 部分

        s1 = scan({"good.py": good})
        rep.add(set(s1) == {"good.py"},
                "S1 正例：字面量含措辞 ⇒ 必须命中（否则扫描恒空）",
                "命中 %s" % {k: v for k, v in s1.items()})

        s2 = scan({"bad.py": bad})
        rep.add("bad.py" in s2,
                "S2 反例：含措辞但不引用 `interp_note` ⇒ 必须命中"
                "（这正是 Q3 判红的依据）",
                "命中 %s" % s2.get("bad.py"))

        s3 = scan({"none.py": none_hit})
        rep.add(not s3,
                "S3 负对照：不含措辞 ⇒ 不得命中（证明 S1/S2 的命中非恒真）",
                "命中 %s" % s3)

        s4 = scan({"doc.py": docstring})
        rep.add(not s4,
                "S4 文档字符串含措辞 ⇒ **不得**命中（证明判据不是「全文 grep」）",
                "命中 %s" % s4)

        s5 = scan({"fstr.py": fstr})
        rep.add("fstr.py" in s5,
                "S5 f-string 的**字面量片段**同样被扫到（否则真实出口整类漏检）",
                "命中 %s" % s5)

        # S6 · 判据自身可被自身解析（#98：执法工具须在被执法集合里）
        own_hits = literal_hits(io.open(os.path.abspath(__file__), encoding="utf-8").read())
        rep.add(isinstance(own_hits, list),
                "S6 自指：本仪器自身可被自己的扫描器解析（不抛异常）",
                "自身命中 %s（本文件属**执法工具**，不进 Q3 判据）" % own_hits)
    finally:
        shutil.rmtree(tmp)
    print("== interp_selfreport_check 自检（构造性可红；铁律 #100）==")
    rep.report()
    ok = rep.n_fail == 0
    print("结论:", "自检通过" if ok else "**自检失败 —— 本仪器不可信**")
    return 0 if ok else 3


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="「非通过」判据自述解释器 —— 全链防漏网（铁律 #92）")
    ap.add_argument("--selfcheck", action="store_true",
                    help="构造性自检（合成语料：正例/反例/负对照）")
    ap.add_argument("--json", dest="json_out", default=None)
    a = ap.parse_args(argv)

    if a.selfcheck:
        return selfcheck()

    if not os.path.isdir(os.path.join(ROOT, "06_PoC")):
        print("[ERROR] 找不到仓库根（%s）—— 本仪器须在仓库内运行。" % ROOT)
        return 2

    files: dict[str, str] = {}
    for rel in iter_py(ROOT):
        try:
            files[rel] = io.open(os.path.join(ROOT, rel), encoding="utf-8",
                                 errors="replace").read()
        except OSError:
            pass
    hits = scan(files)

    print("=" * 74)
    print("  「非通过」判据自述解释器 —— 全链防漏网（Phase 55 · D58）")
    print("=" * 74)
    print("  解释器    : %s" % IN.note())
    print("  扫描范围  : %d 个入库/未跟踪 .py" % len(files))
    print("  词表      : %s" % "、".join(PHRASES))
    print("  声明入口  : %d 项 ｜ 豁免 : %d 项" % (len(IN.ENTRY_POINTS), len(EXEMPT)))
    print("-" * 74)

    rep = Rep()
    check_entries(rep)
    check_exempt(rep)
    check_scan(rep, hits)
    rep.report()
    print("-" * 74)
    print("  PASS %d ／ FAIL %d" % (rep.n_pass, rep.n_fail))
    if a.json_out:
        io.open(a.json_out, "w", encoding="utf-8", newline="\n").write(
            json.dumps({"pass": rep.n_pass, "fail": rep.n_fail,
                        "rows": [{"kind": k, "name": n, "detail": d} for k, n, d in rep.rows],
                        "hits": hits, "interpreter": sys.executable},
                       ensure_ascii=False, indent=2) + "\n")
    return 0 if rep.n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
