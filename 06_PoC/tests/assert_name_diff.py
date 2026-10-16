# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""断言**名字集合**比对（铁律 #87）。

为什么需要它
------------
"加了 N 条断言"这种说法在本项目里被证明**不可靠**：
一次提交可能是"加 2 条、删 1 条"，总数净 +1 —— 只看总数就会把一次删除漏掉。
故核对一律按**名字集合**（每个断言的 ID token）做差集，并显式分列
`新增 / 删除 / 改名`；**只要出现删除就必须逐条给出理由**（断言过期应重写而非删除，铁律 #74）。

用法
----
    python 06_PoC/tests/assert_name_diff.py <旧文件> <新文件> [<旧> <新> ...]
    python 06_PoC/tests/assert_name_diff.py --pair a.py:b.py --json out.json

与仓库里"取旧版本"的正确姿势（避免工作区 CRLF 干扰，见铁律 #69/#72）配合：

    git cat-file blob HEAD:06_PoC/tests/xxx.py > /tmp/old_xxx.py
    python 06_PoC/tests/assert_name_diff.py /tmp/old_xxx.py 06_PoC/tests/xxx.py

支持两种断言注册写法（本仓库现有两种）：
  * Python：`rep.add(cond, NAME, detail)` / `rep.skip(NAME, detail)`（`ast` 解析，能处理多行与 f-string）
  * ESM   ：`check(NAME, cond, detail)`

自检（工具自身的仪器化）
------------------------
    python 06_PoC/tests/assert_name_diff.py --selfcheck
    bash sttp.sh gatecheck        # ★ 已挂进这里（D51 同族：没有目标就没有人会跑它）
在临时目录造**已知答案**的样本对，逐项断言「差异分列 + 退出码」正确 ——
含 #87 的**原始动机场景**（条目总数不变，但确实删掉了断言）。

退出码：`0` = 无删除；`1` = **有删除**（须人工说明，含"同 ID 家族内"按全名补报的删除）；
`2` = 用法错误。

★ Phase 55（D62）追加的一层：「同 ID 家族」内的变化**在 ID 层不可见**
（`norm("A05-1 …") == "A05"` ⇒ `phase45_install_selfcheck.py` 的 31 条断言共用一个 ID），
故本工具在碰撞组内部**按全名补差**（且按**重数**补，不是集合差 —— 见
`blind_group_changes()` 的说明），把"不可见"变回"可见"，并打印结构性告警。
**根治**＝断言 ID 改成互不歧义；本层只是不再让盲区静默（盲区清单见报告 §已知边界）。
"""
import argparse
import ast
import io
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter

# 断言名形如 `S3 必需 DOM id 齐备` / `B4k-客户端等待预算…` / `F40a`
_ID_RE = re.compile(r"^\s*([A-Za-z]{1,4}\d+[a-z]?)")
_JS_CHECK_RE = re.compile(r'\bcheck\(\s*(["\'`])((?:\\\1|(?!\1).)*)\1')


def norm(name):
    """取断言名里的 ID token；取不到则退化为前 24 个字符。"""
    m = _ID_RE.match(name or "")
    return m.group(1) if m else (name or "").strip()[:24]


def from_python(path):
    """`ast` 解析：取 `rep.add` 的第 2 个实参、`rep.skip`/`rep.check` 的第 1 个实参。"""
    src = io.open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        attr = getattr(node.func, "attr", None)
        if attr not in ("add", "skip", "check", "ok"):
            continue
        if attr == "add":
            name = node.args[1] if len(node.args) >= 2 else None
        else:
            name = node.args[0] if len(node.args) >= 1 else None
        if isinstance(name, ast.Constant) and isinstance(name.value, str):
            out.append(name.value)
        elif isinstance(name, ast.JoinedStr) and name.values:
            head = name.values[0]
            if isinstance(head, ast.Constant):
                out.append(head.value)
    return out


def from_js(path):
    src = io.open(path, encoding="utf-8").read()
    return [m.group(2) for m in _JS_CHECK_RE.finditer(src)]


def norm_collisions(raw):
    """返回 `{归一化 ID: [原始名, ...]}` —— 只要出现"**两条断言共用一个 ID**"就报出来。

    ★ 为什么必须**显式**检测（Phase 55 · D62）：`norm()` 的正则
      `^[A-Za-z]{1,4}\\d+[a-z]?` **会吞掉尾随数字** ⇒ `F42b2` 与 `F42b` 归一化后同名。
      后果不是报错，而是**静默**：两条断言共用一个名字 ⇒ **删掉其中一条，在名字集合
      差分上完全不可见** —— 铁律 #87 的执法工具在这一点上是瞎的（执法者自身有盲区）。
      Phase 54 真踩到过：采集器报「条目 +9 / 唯一 ID +8」，**这个不一致本身就是信号**，
      但当时靠人肉发现。本函数把那个信号变成可断言的事实（S7）。

    ★ 为什么不直接以「有碰撞」为失败：本仓现在就有 6 个文件、约 88 条断言落在同 ID 家族里
      （含 `phase45_install_selfcheck.py` 的 30 条同类）⇒ 一遇碰撞即失败会让 `gatecheck`
      常红，那是**把噪音当信号**。故碰撞走「显式列出 + 结构性告警」，真正的修补在
      `blind_group_changes()`（把族内变化按全名补回可见）。
    """
    by: dict[str, list[str]] = {}
    for n in raw:
        by.setdefault(norm(n), []).append(n)
    return {k: v for k, v in by.items() if len(v) > 1}


def blind_group_changes(old_raw, new_raw):
    """在**归一化碰撞组内部**按**全名**补差 ⇒ 把"同 ID 家族里的变化"从隐形变回可见。

    ★ 为什么必须有这一层（Phase 55 · D62 的真实体量）：
      `norm()` 把 `A3 节点计数` / `A3 边计数` / `A3 投影计数` 全归成 `A3`
      ⇒ **ID 层差分对"删掉其中一条"完全不可见**。登记 D62 时只认识到"尾随数字被吞"
      这一种成因，本轮实扫才发现真正的大头是**同家族命名**：本仓 6 个文件、约 **88 条**
      断言落在同 ID 家族里，最极端的是 `phase45_install_selfcheck.py`
      —— **30 条断言共用一个 ID `A05`**（`norm("A05-1 …") == "A05"`）⇒
      那个文件上 `names` 实际上是**瞎的**。
    ★ 必须是**多重集**差，不能是集合差（本轮实现时自己踩到、随后关上的盲区）：
      实扫 `phase45_install_selfcheck.py` 显示 31 条目 ⇒ 唯一 ID 只有 **1** 个，
      且组内存在**字面完全重复**的条目（`A05-13 …` 出现 3 次、`A05-14 …` 3 次）。
      集合差对"删掉 3 条重复中的 1 条"**依然隐形** —— 那就等于把盲区从 ID 层
      平移到了全名层，并没有关上。`Counter` 差按**重数**报 ⇒ 一条不漏（由 S7c 构造性守住）。
    ★ 代价（如实说明）：描述改写在这一层会被判成"删除 + 新增"（工具无法区分
      "改写描述"与"换掉断言"）。故 `main()` 同时打印**结构性告警**并建议把 ID 改成
      互不歧义 —— 那才是根治；本函数只是让"不可见"变回"可见"。
    """
    o_keys, n_keys = norm_collisions(old_raw), norm_collisions(new_raw)
    added, removed = [], []
    for k in sorted(set(o_keys) | set(n_keys)):
        co = Counter(x for x in old_raw if norm(x) == k)
        cn = Counter(x for x in new_raw if norm(x) == k)
        for name, cnt in (cn - co).items():
            added += [name] * cnt
        for name, cnt in (co - cn).items():
            removed += [name] * cnt
    return sorted(added), sorted(removed)


def collect(path):
    raw = from_js(path) if path.endswith((".mjs", ".js")) else from_python(path)
    return raw, [norm(n) for n in raw]


def diff_pair(old_p, new_p):
    old_raw, old = collect(old_p)
    new_raw, new = collect(new_p)
    so, sn = set(old), set(new)
    blind_added, blind_removed = blind_group_changes(old_raw, new_raw)
    return {
        "new_file": new_p,
        "old_entries": len(old_raw), "old_ids": len(so),
        "new_entries": len(new_raw), "new_ids": len(sn),
        "added": sorted(sn - so),
        "removed": sorted(so - sn),
        "old_collisions": norm_collisions(old_raw),
        "new_collisions": norm_collisions(new_raw),
        # 碰撞组内按**全名**补的差（ID 层不可见的部分）
        "blind_added": blind_added,
        "blind_removed": blind_removed,
    }


class _SelfRep:
    """极简报告器 —— 只为让**本脚本自己的断言**进入可采集集合。

    ★ 为什么不用裸 `list[tuple]`：本脚本是铁律 #87 的**执法工具**，但它的自检断言
      若不以 `rep.add(cond, NAME, detail)` 形式登记，`bash sttp.sh names` 就
      **采集不到它们** ⇒ 将来 S2（#87 的**原始动机场景**）被悄悄删掉也没人会发现
      —— 「谁来核对核对器」仍然是个缺口（自指的一次失败）。
      与 `phase43_*_selfcheck.py` / `a4_offline_render.py` 的 `Rep` **同形**：
      采集器认的是调用形状（`attr in {add, skip, check, ok}`），不是输出文本。
    """

    def __init__(self):
        self.items = []

    def add(self, cond, name, detail=""):
        self.items.append((name, bool(cond), detail))

    @property
    def ok(self):
        return all(p for _, p, _ in self.items)

    def report(self):
        for name, passed, detail in self.items:
            print("  [%s] %s" % ("PASS" if passed else "FAIL", name))
            print("         %s" % detail)


def selfcheck() -> int:
    """**已知答案的样本对**自检 —— 「谁来核对核对器」（铁律 #87 / #88 / #90）。

    为什么需要它：本脚本是铁律 #87 的**执法工具**（改断言文件后必须核对**名字集合**
    而不是总数）。但它自身一直**没有断言覆盖** ⇒ 工具坏了没人会知道，而它给出的
    「无删除」结论会被直接采信。这正是铁律 #90 说的情况：**「全绿」也可能是测量器坏了**
    —— 凡用探针核对被测对象，必须在同一次运行内自证探针真会数到东西。

    做法：在**临时目录**造内容已知的样本对，逐项断言「差异分列 + 退出码」正确，
    并**构造性**证明判据真会红 —— 尤其是 S2：**条目总数不变**（3→3）但确实删了 2 条，
    这正是 #87 的原始动机场景（只看总数会把这次删除漏掉）。临时目录用完即删（fail-closed：
    删除失败会抛，不静默）。
    """
    tmp = tempfile.mkdtemp(prefix="sttp_names_selfcheck_")

    def mk(name, ids):
        p = os.path.join(tmp, name)
        if name.endswith((".mjs", ".js")):
            body = "\n".join('check("%s 样本断言", true, "");' % i for i in ids)
        else:
            body = "\n".join('rep.add(True, "%s 样本断言", "")' % i for i in ids)
        io.open(p, "w", encoding="utf-8", newline="\n").write(body + "\n")
        return p

    rep = _SelfRep()
    try:
        # S1 · 正例：加 1 删 1
        r1 = diff_pair(mk("old1.py", ["A1", "A2", "A3"]), mk("new1.py", ["A1", "A3", "A4"]))
        rep.add(r1["added"] == ["A4"] and r1["removed"] == ["A2"],
                "S1 加 1 删 1 ⇒ added=[A4] / removed=[A2]（分列正确）",
                "added=%s removed=%s" % (r1["added"], r1["removed"]))

        # S2 · ★ #87 的**原始动机本身**：条目总数不变，但确实删掉了 2 条
        r2 = diff_pair(mk("old2.py", ["A1", "A2", "A3"]), mk("new2.py", ["A1", "A4", "A5"]))
        same_count = (r2["old_entries"] == r2["new_entries"] == 3)
        rep.add(same_count and r2["added"] == ["A4", "A5"] and r2["removed"] == ["A2", "A3"],
                "S2 ★条目总数不变（3→3）但实删 2 条 ⇒ 仍必须报出 added=[A4,A5] / removed=[A2,A3]",
                "entries %d→%d · added=%s · removed=%s"
                % (r2["old_entries"], r2["new_entries"], r2["added"], r2["removed"]))

        # S3 · 负对照：只加不删 ⇒ removed 必须为空（证明 S1/S2 的删除检测不是恒真）
        r3 = diff_pair(mk("old3.py", ["A1", "A2"]), mk("new3.py", ["A1", "A2", "A3"]))
        rep.add(r3["added"] == ["A3"] and r3["removed"] == [],
                "S3 负对照：只加不删 ⇒ removed 为空（证明删除检测非恒真）",
                "added=%s removed=%s" % (r3["added"], r3["removed"]))

        # S4 · ESM 分支同样被解析（`check("NAME", …)` 与 Python 的 `rep.add` 两条通道）
        r4 = diff_pair(mk("old4.mjs", ["B1"]), mk("new4.mjs", ["B1", "B2"]))
        rep.add(r4["added"] == ["B2"] and r4["removed"] == [],
                "S4 ESM `check(\"NAME\", …)` 分支同样可解析（第二条注册通道未被遗漏）",
                "added=%s removed=%s" % (r4["added"], r4["removed"]))

        # S5 · 可红性：把判据人为换成"只比条目总数" ⇒ 在 S2 场景上**必然漏报**
        #      （直接构造朴素判据，证明它确实抓不到 S2 的删除）
        naive_says_unchanged = (r2["old_entries"] == r2["new_entries"])
        rep.add(naive_says_unchanged and len(r2["removed"]) == 2,
                "S5 可红性：朴素判据（只比总数）在 S2 上判「无变化」⇒ 证明名字集合差分不可省",
                "朴素判据 => 无变化；集合判据 => 删除 %d 条" % len(r2["removed"]))

        # S6 · ★ 自指：本自检的断言**必须可被自己采集**（否则"谁来核对核对器"仍是缺口）
        #      做法：把本文件自身当被测对象跑一遍解析，断言 S* ID 全在里面。
        #      ★ Phase 55（D62）：扩到 S7/S8 —— 新加的断言若采集不到，本文件就会重演
        #        "自己写下的守卫悄悄消失而无人知晓"（自指的第二次失败）。
        own = from_python(os.path.abspath(__file__))
        own_ids = {norm(n) for n in own}
        want = {"S1", "S2", "S3", "S4", "S5", "S7", "S7b", "S7c", "S8"}
        missing = sorted(want - own_ids)
        rep.add(not missing,
                "S6 ★自指：本自检的全部断言可被**自身解析器**采集（#87 的执法工具须可被执法）",
                "采集到 %s" % sorted(want & own_ids)
                + ("；缺 %s" % missing if missing else ""))

        # S7 · ★ D62：`norm()` 会**吞掉尾随数字** ⇒ `F42b2` 与 `F42b` 同 ID。
        #      要求的**不是**"报个错"，而是两层都要工作：
        #        ① 碰撞被**显式列出**（否则人不知道这里差分不可靠）；
        #        ② 组内按**全名**补差（否则"删掉其中一条"仍然隐形 —— 盲区没被关上）。
        r7 = diff_pair(mk("old7.py", ["F42b"]), mk("new7.py", ["F42b", "F42b2"]))
        col7 = r7["new_collisions"]
        _nm = col7.get("F42b") or []
        rep.add(len(_nm) == 2 and len(set(_nm)) == 2
                and any(n.startswith("F42b2") for n in _nm)
                and r7["new_ids"] == 1 and r7["new_entries"] == 2,
                "S7 ★归一化碰撞必须被**显式列出**（F42b2 与 F42b 同 ID ⇒ 条目数≠唯一 ID 数）",
                "collisions=%s ｜ 条目 %d ⇒ 唯一 ID %d"
                % (col7, r7["new_entries"], r7["new_ids"]))

        # S7b · ★ 关键的一半：**组内按全名补差**必须真的报出那条被删的断言。
        #      没有它，S7 只是"把不可靠说出来"，盲区依然关着（删掉 F42b 仍然无人知晓）。
        r7b = diff_pair(mk("old7b.py", ["F42b", "F42b2"]), mk("new7b.py", ["F42b2"]))
        rep.add(r7b["removed"] == [] and any(x.startswith("F42b 样本") for x in r7b["blind_removed"]),
                "S7b ★组内补差：删掉同 ID 家族中的一条（ID 层看不见）⇒ 必须按**全名**报出",
                "ID 层删除=%s（应为空）｜族内删除=%s" % (r7b["removed"], r7b["blind_removed"]))

        # S7c · ★ 第二轮盲区：组内**字面重复**的条目。S7b 只覆盖"同族但名字不同"；
        #      而实扫发现 `phase45_install_selfcheck.py` 的 A05 组里 `A05-13 …` 出现 3 次
        #      字面相同的条目 ⇒ 若用**集合**差，"删掉 3 条重复中的 1 条"仍然隐形 ——
        #      那只是把盲区从 ID 层平移到全名层。本断言钉死"按**重数**补差"。
        r7c = diff_pair(mk("old7c.py", ["F42c", "F42c"]), mk("new7c.py", ["F42c"]))
        rep.add(r7c["removed"] == [] and r7c["blind_removed"] == ["F42c 样本断言"],
                "S7c ★重数补差：删掉**字面重复**条目中的一条 ⇒ 仍必须报出（集合差会漏掉它）",
                "ID 层删除=%s（应为空）｜族内删除=%s（应为 1 条）"
                % (r7c["removed"], r7c["blind_removed"]))

        # S8 · 负对照：无碰撞时两表必须为空（证明 S7/S7b 的检测非恒真）
        r8 = diff_pair(mk("old8.py", ["G1", "G2"]), mk("new8.py", ["G1", "G2"]))
        rep.add(r8["old_collisions"] == {} and r8["new_collisions"] == {}
                and r8["blind_added"] == [] and r8["blind_removed"] == [],
                "S8 负对照：无碰撞 ⇒ 两表皆空（证明 S7/S7b 的检测不是恒真）",
                "old=%s new=%s blind=(%s,%s)"
                % (r8["old_collisions"], r8["new_collisions"],
                   r8["blind_added"], r8["blind_removed"]))
    finally:
        shutil.rmtree(tmp)          # ★ 删不掉即抛（fail-closed），不静默留垃圾

    print("== assert_name_diff 自检（仪器的仪器 · 铁律 #87/#88/#90）==")
    rep.report()
    ok = rep.ok
    print("结论:", "自检通过（工具不会静默漏报删除）" if ok else "自检失败 —— 工具本身不可信")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="断言名字集合比对（铁律 #87）")
    ap.add_argument("paths", nargs="*", help="成对的 <旧> <新> 文件路径")
    ap.add_argument("--pair", action="append", default=[],
                    help="`<旧>:<新>` 形式（可重复）")
    ap.add_argument("--selfcheck", action="store_true",
                    help="跑**已知答案样本对**自检（工具自身的仪器化；铁律 #87/#88/#90）")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()

    if args.selfcheck:
        return selfcheck()

    pairs = [p.split(":", 1) for p in args.pair if ":" in p]
    rest = list(args.paths)
    while len(rest) >= 2:
        pairs.append([rest.pop(0), rest.pop(0)])
    if rest or not pairs:
        ap.print_usage()
        print("★ 用法：assert_name_diff.py <旧文件> <新文件> [--pair 旧:新] [--json out.json]")
        return 2

    reports, any_removed = [], False
    for old_p, new_p in pairs:
        r = diff_pair(old_p, new_p)
        reports.append(r)
        any_removed = any_removed or bool(r["removed"]) or bool(r["blind_removed"])
        print("文件: %s" % r["new_file"])
        print("  条目 %d→%d ｜ 唯一 ID %d→%d"
              % (r["old_entries"], r["new_entries"], r["old_ids"], r["new_ids"]))
        print("  新增 ID（%d）: %s" % (len(r["added"]), ", ".join(r["added"]) or "（无）"))
        print("  删除 ID（%d）: %s" % (len(r["removed"]), ", ".join(r["removed"]) or "（无）"))
        # ★ Phase 55（D62）：同 ID 家族内的变化 —— ID 层**看不见**，这里按全名补报。
        if r["blind_added"] or r["blind_removed"]:
            print("  ★ **同 ID 家族内**的变化（ID 层不可见，已按全名补报）：")
            print("      族内新增（%d）: %s" % (len(r["blind_added"]),
                                            ", ".join(r["blind_added"]) or "（无）"))
            print("      族内删除（%d）: %s" % (len(r["blind_removed"]),
                                            ", ".join(r["blind_removed"]) or "（无）"))
        if r["removed"] or r["blind_removed"]:
            print("  ★ 有删除 —— 按铁律 #87/#74 必须**逐条**说明理由"
                  "（断言过期应重写，不应直接删）")
        # ★ 结构性告警（不阻塞）：同 ID 家族让 ID 层差分**不可靠**。根治＝改成互不歧义的 ID。
        for side in ("old", "new"):
            col = r["%s_collisions" % side]
            if col:
                print("  ⚠ %s 侧 **%d 组同 ID 家族**（ID 层差分对它们不可靠；"
                      "组内变化已按全名补报，但描述改写会被判成删+增）："
                      % ("旧" if side == "old" else "新", len(col)))
                for k, names in sorted(col.items()):
                    rep_c = Counter(names)
                    dup = {n: c for n, c in rep_c.items() if c > 1}
                    print("       %-6s ⇐ %d 条（唯一 %d）%s"
                          % (k, len(names), len(rep_c),
                             "；★ 字面重复 %s" % dup if dup else ""))
                    print("           %s" % names)
    if args.json_out:
        io.open(args.json_out, "w", encoding="utf-8", newline="\n").write(
            json.dumps(reports, ensure_ascii=False, indent=2))
        print("已写出: %s" % args.json_out)
    return 1 if any_removed else 0


if __name__ == "__main__":
    sys.exit(main())
