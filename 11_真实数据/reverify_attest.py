# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
"""reverify_attest.py —— Phase 73：证明「验证器改过，但老证据的判据语义没变」

问题
----
`verch`（验证器 `code_hash`）是**整文件**摘要。给验证器加一个只服务新边类型的
`if` 分支，也能让**全部** 3.2 万条老证据翻 `stale`（=「已变更」）。这是**第二次**
踩同一个坑（Phase 61 踩过并 revert，提交信息里就写着「会让 32,609 条证据全部 stale」）。

处置的两条错路
--------------
* **重写哈希**：把 `vch` 改成当前值 ⇒ 状态直接变 valid，但**没有任何证据**说明语义没变。
  这正是 `evidence_deps` 里 `hash_rewritten_without_reverify` 那条堵口要防的。
* **重跑 3.2 万次判定**：成本高、且多数判定的输入（外部来源原文）**根本不在仓库里**，
  重跑只能重跑一个"读不到输入"的空壳 —— 假动作（铁律 #30）。

本文件走第三条：**把「语义没变」做成可重算的机械证明**。
证明成立 ⇒ 生成一条 attestation（按「版本对 + 排除范围」记账，1 条覆盖 3.2 万条）；
证据上带 `rva` 指回它；运行时 `evidence_status` 当场核对版本对是否仍然对齐。
证明不成立 ⇒ **拒绝生成**（fail-closed），证据继续判 stale。

证明方法（结构白名单，不用"看起来是纯新增"这种印象判断）
----------------------------------------------------------
把旧 blob 与新 blob 都取成 AST（**不用 diff 文本**：文本插入可以改变缩进归属），逐项验：

| # | 断言 | 反例（必须拒） |
|---|---|---|
| P1 | 顶层 `def`/`class` **一个都没少** | 删掉一个分支函数 |
| P2 | 保留下来的顶层 `def`：`args`/`decorator_list` 逐字相同 | 改默认参数（调用方语义全变） |
| P3 | 保留函数的**语句序列**里，旧序列是新序列的**子序列**（插入-only） | 改一行既有语句 |
| P4 | 每处插入的语句形状 ∈ 白名单（见 `_shapes`） | 插入 `return` / 无条件赋值 |
| P5 | 控制流插入的守卫里**必须出现**被排除的边类型字面量 | 无守卫的 `if` |
| P6 | 模块级字典常量：老键**逐键保同值**，只允许加新键 | 改掉某个老档位的取值 |
| P7 | 新增顶层名**不与既有名冲突** | 用同名函数覆盖旧实现 |

★ P5 是本文件的**要害**：它把「新增分支不会影响老证据」从`我说它不会`变成
  `分支的进入条件里根本不包含老证据的类型`。范围（`exclude_types`）写在 attestation 上，
  谁都能拿去对；`--check` 每次都会拿 git 历史里的真 blob 重算一遍。
★ 证明**不覆盖**「插入的代码会不会间接改到共享状态」（如插入的循环 `ctx["x"].add(...)`）。
  P4 的白名单只放行守卫式插入与**新名**定义，正是为了把这类间接影响挡在外面。

用法
----
    python 11_真实数据/reverify_attest.py --from-rev <git rev> --exclude-type mentioned_in [--emit]
    python 11_真实数据/reverify_attest.py --check      # 重算全部 attestation（证明不成立 ⇒ RC=1）
    python 11_真实数据/reverify_attest.py --selfcheck  # 7 条构造性自检（含 4 个负对照）
"""
from __future__ import annotations

import argparse
import ast
import collections
import datetime
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import evidence_deps as ed                              # noqa: E402

POC = os.path.join(ROOT, "06_PoC")
if POC not in sys.path:                                 # 铁律 #92：非通过出口须自述解释器
    sys.path.insert(0, POC)
import interp_note as interp                            # noqa: E402

# 允许「版本对」变更的验证器文件（新增验证器时必须显式登记：铁律 #49 反面）
VERIFIER_FILES = collections.OrderedDict([
    ("verification_model", "11_真实数据/verification_model.py"),
])

# P4 白名单里允许的插入语句形状（注释写清「为什么它可以对老证据无影响」）
_SHAPES = ("import", "newdef", "newconst", "guarded_if", "guarded_for", "docstring")


# ---------------------------------------------------------------------------
# 1. git blob 取源
# ---------------------------------------------------------------------------
def _git(args, cwd=ROOT):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True)


def blob_at(rev, rel, cwd=ROOT):
    r = _git(["show", "%s:%s" % (rev, rel)], cwd)
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace")


def blob_hash(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def revs_for_file(rel, cwd=ROOT, limit=400):
    r = _git(["log", "--format=%H", "-n", str(limit), "--", rel], cwd)
    if r.returncode != 0:
        return []
    return [x for x in r.stdout.decode("utf-8", "replace").split() if x]


def find_rev_for_hash(rel, wanted, cwd=ROOT):
    """反查「blob 哈希 == wanted」的提交（**fail-closed**：找不到 ⇒ None，不猜）。

    ★ 为什么不要求调用方给 rev：`vch` 记的是**内容摘要**，不是提交号。让调用方
      填提交号等于让它手抄一个"我记得是这次改的"，那是记忆不是证据。
    """
    for rev in revs_for_file(rel, cwd):
        b = blob_at(rev, rel, cwd)
        if b is not None and blob_hash(b) == wanted:
            return rev
    return None


# ---------------------------------------------------------------------------
# 2. AST 证明
# ---------------------------------------------------------------------------
def _dump(n):
    return ast.dump(n, annotate_fields=True, include_attributes=False)


def _tops(tree):
    """顶层定义表：`{"f:name": node}` / `{"a:NAME": node}`（两类分开，避免同名混淆）。"""
    out = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out["f:" + n.name] = n
        elif isinstance(n, ast.ClassDef):
            out["c:" + n.name] = n
        elif isinstance(n, ast.Assign):
            for tg in n.targets:
                if isinstance(tg, ast.Name):
                    out["a:" + tg.id] = n
    return out


def _is_subsequence(needle, hay):
    """`needle` 是否为 `hay` 的子序列（保序，字典常量按「同名占位」比）。

    返回 `(ok, pairs, inserted)`：`pairs` 是 `[(旧下标, 新下标)]`（用于对被匹配到的
    字典语句单独查 P6），`inserted` 是新序列里**没被匹配到**的下标（即插入的语句）。
    """
    keys = [_norm_key(x) for x in hay]
    j, pairs = 0, []
    for i, s in enumerate(needle):
        k = _norm_key(s)
        while j < len(hay) and keys[j] != k:
            j += 1
        if j >= len(hay):
            return False, [], []
        pairs.append((i, j))
        j += 1
    used = {b for _, b in pairs}
    return True, pairs, [i for i in range(len(hay)) if i not in used]


def _src_seg(node):
    """把一个 AST 子树里出现的**字符串常量**收集起来（用于 P5 的守卫字面量检查）。"""
    vals = []
    for x in ast.walk(node):
        if isinstance(x, ast.Constant) and isinstance(x.value, str):
            vals.append(x.value)
    return vals


def _mentions(node, tokens):
    """守卫里是否出现被排除类型 —— **精确相等**，不做子串匹配。

    ★ 子串匹配会放过 `if kind == "zz_kind"`（因为 "zz" 是 "zz_kind" 的子串），
      正好是本判据最容易被骗过的方式。边类型是**标识符**，按标识符比。
    """
    want = set(tokens)
    return any(v in want for v in _src_seg(node))


def _has_negative(node):
    """守卫里出现 `!=` / `not` / `is not` ⇒ **反向守卫**（`if t != "zz"` 会对老证据成立）。

    ★ 极性必须查：`if t != "zz": mutate()` 里同样含 `zz` 字面量，`_mentions` 会放过它，
      但它恰恰对**老证据**生效 —— 这是本判据最容易被"看起来合规"骗过的地方。
    """
    for x in ast.walk(node):
        if isinstance(x, (ast.NotEq, ast.Not, ast.IsNot)):
            return True
    return False


def _assign_names(stmts):
    out = []
    for s in stmts:
        if isinstance(s, ast.Assign):
            out += [t.id for t in s.targets if isinstance(t, ast.Name)]
    return out


def _is_optional_import(stmt, old_leaves):
    """可选导入惯用法：`try: import X; NEW=... except: NEW=...`。

    放行条件（三条同时满足）：
      * `body[0]` 是 import；
      * `body` 余下与全部 `handler` 体**只有**简单赋值，且目标名**都是新名**（不覆盖旧实现）；
      * handler 里没有 `return` / `raise`（不得改变导入失败时的控制流去向）。
    """
    if not (stmt.body and isinstance(stmt.body[0], (ast.Import, ast.ImportFrom))):
        return False
    rest, hstmt = list(stmt.body[1:]), []
    for h in stmt.handlers:
        hstmt += list(h.body)
    for s in rest + hstmt:
        if not isinstance(s, ast.Assign):
            return False
    return all(n not in old_leaves for n in _assign_names(rest + hstmt))


def _shape_of(stmt, exclude_types, is_module_level, old_leaves=()):
    """判定一条**插入语句**属于白名单里的哪种形状；不合规 ⇒ None。"""
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        return "import" if is_module_level else None
    if is_module_level and isinstance(stmt, ast.Try):
        return "import" if _is_optional_import(stmt, set(old_leaves)) else None
    if is_module_level and isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return "newdef"
    if is_module_level and isinstance(stmt, ast.Assign):
        return "newconst"
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) \
            and isinstance(stmt.value.value, str):
        return "docstring"
    if isinstance(stmt, ast.If):
        # P5：守卫须**正向**点名被排除的边类型（`==`），否则分支会对老证据也生效
        if _mentions(stmt.test, exclude_types) and not _has_negative(stmt.test):
            return "guarded_if"
        return None
    if isinstance(stmt, ast.For):
        body = stmt.body
        # 「跳过式」循环：首条必须是 `if <与排除类型不等>: continue`
        if body and isinstance(body[0], ast.If) and _mentions(body[0].test, exclude_types):
            if any(isinstance(x, ast.NotEq) for x in ast.walk(body[0].test)) \
                    and any(isinstance(x, ast.Continue) for x in body[0].body):
                return "guarded_for"
        return None
    return None


def _dict_extension_ok(old_node, new_node):
    """P6：老键逐键保同值，只允许新增键。返回 (ok, added_keys|why)。"""
    if not (isinstance(new_node, ast.Assign) and isinstance(new_node.value, ast.Dict)):
        return False, "不是字典字面量"
    if not (isinstance(old_node, ast.Assign) and isinstance(old_node.value, ast.Dict)):
        return False, "旧版不是字典字面量"
    def kv(d):
        out = {}
        for k, v in zip(d.keys, d.values):
            if isinstance(k, ast.Constant):
                out[k.value] = _dump(v)
        return out
    a, b = kv(old_node.value), kv(new_node.value)
    lost = [k for k in a if k not in b]
    changed = [k for k in a if k in b and a[k] != b[k]]
    if lost or changed:
        return False, "老键丢失 %s / 取值被改 %s" % (lost[:3], changed[:3])
    return True, sorted(k for k in b if k not in a)


def _norm_key(n):
    """模块级语句的比较键：**字典常量按「同名占位」比**，取值差异交给 P6 单独查。

    ★ 若不这样处理，「给 SCOPE_KIND 加一个键」会被判成「既有语句被改」⇒ 所有合法
      扩展都过不了；而若直接跳过字典语句，又会漏掉「偷偷改掉某个老档位取值」。
      两个要求靠「占位比序列 + 逐键比取值」同时满足。
    """
    if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict):
        names = [t.id for t in n.targets if isinstance(t, ast.Name)]
        if len(names) == 1:
            return "DICT:" + names[0]
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return "DEF:" + n.name
    if isinstance(n, ast.ClassDef):
        return "CLASS:" + n.name
    if isinstance(n, ast.Assign):
        names = [t.id for t in n.targets if isinstance(t, ast.Name)]
        if len(names) == 1:
            return "ASSIGN:" + names[0]
    return _dump(n)


def _is_subsequence_norm(needle, hay):
    keys = [_norm_key(x) for x in hay]
    j, used = 0, []
    for s in needle:
        k = _norm_key(s)
        while j < len(hay) and keys[j] != k:
            j += 1
        if j >= len(hay):
            return False, []
        used.append(j)
        j += 1
    return True, [i for i in range(len(hay)) if i not in set(used)]


def ast_proof(old_src, new_src, exclude_types):
    """返回 `(proof|None, why)`。`proof` 是**可逐项复核**的结构化证据，不是一句"我看过了"。"""
    try:
        ot, nt = ast.parse(old_src), ast.parse(new_src)
    except SyntaxError as exc:
        return None, "语法错误：%s" % exc
    if not exclude_types:
        return None, "未给出被排除的边类型（P5 无从检查）"

    o, n = _tops(ot), _tops(nt)
    gone = [k for k in o if k not in n]
    if gone:                                                  # P1
        return None, "P1 顶层定义被删：%s" % gone[:5]

    proof = {"method": "ast_insertion_only", "exclude_types": sorted(exclude_types),
             "changed_defs": [], "added_defs": [], "added_consts": [],
             "dict_extensions": {}, "guards": [], "module_insertions": []}
    old_leaves = {k.split(":", 1)[1] for k in o}

    # ---- 0. 模块级：插入-only + 每处插入须在白名单内 + 新名不得覆盖旧名（P7）
    ok, ins_idx = _is_subsequence_norm(ot.body, nt.body)
    if not ok:
        return None, "P3 模块级既有语句被改/删（不是插入-only）"
    for i in ins_idx:
        stmt = nt.body[i]
        sh = _shape_of(stmt, exclude_types, is_module_level=True, old_leaves=old_leaves)
        if sh is None:
            return None, "P4 模块级插入了白名单外的语句：%s（行 %s）" % (
                type(stmt).__name__, getattr(stmt, "lineno", "?"))
        proof["module_insertions"].append({"shape": sh, "line": getattr(stmt, "lineno", None)})
        if sh == "newdef":
            leaf = getattr(stmt, "name", "")
            if leaf in old_leaves:                            # P7
                return None, "P7 新增的顶层 %s 与既有名同名（会覆盖旧实现）" % leaf
            proof["added_defs"].append("f:" + leaf)
        elif sh == "newconst":
            for tg in stmt.targets:
                if isinstance(tg, ast.Name):
                    if tg.id in old_leaves:
                        return None, "P7 新增常量 %s 与既有名同名" % tg.id
                    proof["added_consts"].append("a:" + tg.id)

    # ---- 1. 保留下来的既有定义
    def scan_def(key, old_node, new_node):
        if isinstance(old_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _dump(old_node.args) != _dump(new_node.args):          # P2
                return "P2 %s 的参数表被改" % key
            if _dump(ast.Module(body=list(old_node.decorator_list), type_ignores=[])) != \
               _dump(ast.Module(body=list(new_node.decorator_list), type_ignores=[])):
                return "P2 %s 的装饰器被改" % key
        ok2, pairs, ins2 = _is_subsequence(old_node.body, new_node.body)   # P3
        if not ok2:
            return "P3 %s 不是插入-only（有既有语句被改/删）" % key
        for oi, ni in pairs:
            ob, nb = old_node.body[oi], new_node.body[ni]
            if isinstance(ob, ast.Assign) and isinstance(ob.value, ast.Dict):
                okd, info = _dict_extension_ok(ob, nb)
                if not okd:                                  # P6（函数体内的字典扩展）
                    return "P6 %s 第 %d 条语句：%s" % (key, oi, info)
                if info:
                    proof["dict_extensions"]["%s@%d" % (key, ni)] = info
        for i in ins2:                                                # P4/P5
            sh = _shape_of(new_node.body[i], exclude_types, is_module_level=False)
            if sh is None:
                blk = new_node.body[i]
                if isinstance(blk, (ast.If, ast.For)):
                    return ("P5 %s 插入的 %s（行 %s）守卫未**正向**点名被排除类型 %s"
                            % (key, type(blk).__name__, getattr(blk, "lineno", "?"),
                               ",".join(exclude_types)))
                return "P4 %s 插入了白名单外的语句：%s（行 %s）" % (
                    key, type(blk).__name__, getattr(blk, "lineno", "?"))
            if sh in ("guarded_if", "guarded_for"):
                proof["guards"].append({"fn": key, "shape": sh,
                                        "line": getattr(new_node.body[i], "lineno", None)})
        proof["changed_defs"].append(key)
        return None

    for k in o:
        if _dump(o[k]) == _dump(n[k]):
            continue
        if isinstance(o[k], ast.Assign) and isinstance(o[k].value, ast.Dict):
            okd, info = _dict_extension_ok(o[k], n[k])                # P6
            if not okd:
                return None, "P6 %s：%s" % (k, info)
            proof["dict_extensions"][k] = info
            proof["changed_defs"].append(k)
            continue
        if isinstance(o[k], ast.Assign):
            return None, "P6 %s 是赋值且被改（非白名单字典扩展）" % k
        why = scan_def(k, o[k], n[k])
        if why:
            return None, why

    proof["n_stmt_insertions"] = len(proof["guards"])
    return proof, "ok"


# ---------------------------------------------------------------------------
# 3. attestation 生成 / 复算
# ---------------------------------------------------------------------------
def attestation_id(verifier, frm, to, exclude_types):
    key = "|".join([verifier, frm or "", to or "", ",".join(sorted(exclude_types or []))])
    return "rva:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def build_attestation(verifier, frm, to, exclude_types, from_rev, to_rev=None):
    rel = VERIFIER_FILES.get(verifier)
    if rel is None:
        return None, "未登记的验证器 %s" % verifier
    rev = from_rev or find_rev_for_hash(rel, frm)
    if rev is None:
        return None, "反查不到 blob %s 对应的提交（fail-closed）" % frm
    old_src = blob_at(rev, rel)
    new_src = blob_at("HEAD", rel)
    if old_src is None or new_src is None:
        return None, "取不到 blob（%s / HEAD）" % rev
    if blob_hash(old_src) != frm:
        return None, "旧 blob 哈希不符（%s ≠ %s）" % (blob_hash(old_src), frm)
    cur = blob_hash(new_src)
    if to is not None and cur != to:
        return None, "当前 blob 哈希不符（%s ≠ %s）" % (cur, to)
    proof, why = ast_proof(old_src, new_src, exclude_types)
    if proof is None:
        return None, why
    stamp = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    return {
        "id": attestation_id(verifier, frm, cur, exclude_types),
        "kind": "code_equivalent",
        "verifier": verifier,
        "path": rel,
        "from_code_hash": frm, "to_code_hash": cur,
        "exclude_types": sorted(exclude_types),
        "from_rev": rev[:12], "to_rev": subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            capture_output=True).stdout.decode().strip(),
        "at": stamp,
        "proof": proof,
        "note": ("证明的是「被排除的边类型之外，验证器行为不变」。"
                 "范围由 exclude_types 表达；`--check` 会拿 git 历史里的真 blob 重算。"),
    }, "ok"


def check_attestations(path=None, base=ROOT):
    """重算**每一条** attestation 的证明；返回 `[(id, ok|None, detail)]`。"""
    p = path or os.path.join(base, ed.ATTESTATION_OUT)
    if not os.path.exists(p):
        return []
    rows = []
    for a in ed.load_attestations(p).values():
        if a.get("kind") != "code_equivalent":
            rows.append((a.get("id"), False, "未知 kind=%s" % a.get("kind")))
            continue
        rel = VERIFIER_FILES.get(a.get("verifier"))
        if rel is None:
            rows.append((a.get("id"), False, "验证器未登记：%s" % a.get("verifier")))
            continue
        old_src = blob_at(a.get("from_rev") or "", rel, base)
        if old_src is None:
            rev = find_rev_for_hash(rel, a.get("from_code_hash"), base)
            old_src = blob_at(rev, rel, base) if rev else None
        new_src = blob_at("HEAD", rel, base)
        if old_src is None or new_src is None:
            rows.append((a.get("id"), None, "取不到 blob ⇒ 本次测不到（不冒充通过）"))
            continue
        if blob_hash(old_src) != a.get("from_code_hash"):
            rows.append((a.get("id"), False, "from blob 已不是 %s" % a.get("from_code_hash")))
            continue
        if blob_hash(new_src) != a.get("to_code_hash"):
            rows.append((a.get("id"), False,
                         "to blob 已变（%s → %s）⇒ 证明过期，须重开" %
                         (a.get("to_code_hash"), blob_hash(new_src))))
            continue
        proof, why = ast_proof(old_src, new_src, a.get("exclude_types") or [])
        rows.append((a.get("id"), proof is not None, why if proof is None else
                     "插入 %d 处守卫、改 %d 个定义、新增 %d 个定义"
                     % (proof.get("n_stmt_insertions", 0), len(proof.get("changed_defs") or []),
                        len(proof.get("added_defs") or []))))
    return rows


def emit(path=None, **kw):
    att, why = build_attestation(**kw)
    if att is None:
        print("[拒绝] %s" % why, file=sys.stderr)
        return 1
    p = path or os.path.join(ROOT, ed.ATTESTATION_OUT)
    payload = {
        "schema": "sttp-reverify-attestation/v1",
        "phase": 73,
        "note": ("每条 = 一个「验证器版本对 + 排除范围」。**不是许可**："
                 "`reverify_attest.py --check` 每次都会拿 git 历史真 blob 重算 AST 证明；"
                 "算不出来 ⇒ 整条不成立 ⇒ 引用它的证据回落 stale。"),
        "attestations": [att],
    }
    with open(p, "w", encoding="utf-8", newline="") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("-> %s" % os.path.relpath(p, ROOT))
    print("   %s  %s → %s  排除 %s" % (att["id"], att["from_code_hash"],
                                    att["to_code_hash"], att["exclude_types"]))
    print("   证明：%s" % json.dumps(att["proof"], ensure_ascii=False)[:300])
    return 0


# ---------------------------------------------------------------------------
# 4. 自检（构造性 + 负对照）
# ---------------------------------------------------------------------------
_BASE = '''\
import os


def classify(t, kind, ctx):
    if t == "a":
        return "A"
    return "B"


def build_ctx(nodes):
    ctx = {"x": {}}
    for n in nodes:
        ctx["x"][n] = 1
    return ctx


SCOPE_KIND = {
    "a": ("source_assertion", False),
}


TEMPLATES = {
    "a": "「{s}」a「{t}」",
}
'''


def _mut(**kw):
    """在 `_BASE` 上做**受控变异**，生成自检用的旧/新源码对。"""
    s = _BASE
    if kw.get("safe"):
        s = s.replace('''    if t == "a":
        return "A"''', '''    if t == "a":
        return "A"
    if t == "zz" and kind == "zz_kind":
        return "Z"''')
        s = s.replace('''    ctx = {"x": {}}''', '''    for e in nodes:
        if e.get("type") != "zz":
            continue
        e["zz"] = 1
    ctx = {"x": {}}''')
        s = s.replace('''TEMPLATES = {
    "a"''', '''TEMPLATES = {
    "zz": "「{s}」zz「{t}」",
    "a"''')
        s = s.replace("import os\n", "import os\ntry:\n    from nope import x\nexcept Exception:\n    x = None\n", 1)
        s = s.replace('''def build_ctx(nodes):''', '''def new_helper(a, b):
    return a


def build_ctx(nodes):''')
    if kw.get("deleted"):
        s = s.replace('''def build_ctx(nodes):''', '''def build_ctx_gone(nodes):''')
    if kw.get("modified"):
        s = s.replace('''    return "B"''', '''    return "C"''')
    if kw.get("unguarded"):
        s = s.replace('''    if t == "a":
        return "A"''', '''    if t == "a":
        return "A"
    if kind == "zz_kind":
        return "Z"''')
    if kw.get("dictvalue"):
        s = s.replace('"a": ("source_assertion", False),', '"a": ("recompute", True),')
    if kw.get("shadow"):
        s = s.replace('''SCOPE_KIND = {''', '''def classify(t, kind, ctx):
    return "HIJACK"


SCOPE_KIND = {''')
    if kw.get("ret"):
        s = s.replace('''    if t == "a":
        return "A"''', '''    if t == "a":
        return "A"
    return "B"''')
    return s


def selfcheck():
    rows = []

    def add(cond, name, detail=""):
        # ★ `bool(cond)` 而不是 `cond is True`：判据里常有 `and proof.get("guards")`
        #   这种「返回列表」的尾巴，`is True` 会把它判成失败（本轮真踩过）。
        rows.append((None if cond is None else bool(cond), name, detail))

    proof, why = ast_proof(_BASE, _mut(safe=1), ["zz"])
    add(proof is not None,
        "S1 纯新增（守卫式分支 + 新函数 + 新常量 + 可选导入）⇒ 证明成立", why)
    add(proof is not None and len(proof.get("added_defs") or []) == 1
        and len(proof.get("dict_extensions") or {}) == 1 and proof.get("guards"),
        "S1b 证明**逐项**记下了改了什么（不是一句 ok）",
        json.dumps(proof or {}, ensure_ascii=False)[:160])

    p2, w2 = ast_proof(_BASE, _mut(deleted=1), ["zz"])
    add(p2 is None and "被删" in (w2 or ""), "S2 负对照：删/改名一个顶层定义 ⇒ 拒绝", w2 or "")

    p3, w3 = ast_proof(_BASE, _mut(modified=1), ["zz"])
    add(p3 is None and "P3" in (w3 or ""), "S3 负对照：改一行既有语句（非插入）⇒ 拒绝", w3 or "")

    p4, w4 = ast_proof(_BASE, _mut(unguarded=1), ["zz"])
    add(p4 is None and "P5" in (w4 or ""), "S4 负对照：插入 `if` 但守卫不含被排除类型 ⇒ 拒绝", w4 or "")

    p5, w5 = ast_proof(_BASE, _mut(dictvalue=1), ["zz"])
    add(p5 is None and "P6" in (w5 or ""), "S5 负对照：改掉既有档位的取值 ⇒ 拒绝", w5 or "")

    p6, w6 = ast_proof(_BASE, _mut(shadow=1), ["zz"])
    add(p6 is None, "S6 负对照：新增同名函数覆盖旧实现 ⇒ 拒绝", w6 or "")

    p7, w7 = ast_proof(_BASE, _mut(safe=1), [])
    add(p7 is None and "未给出" in (w7 or ""), "S7 负对照：不给排除范围 ⇒ 拒绝（不做无条件放行）", w7 or "")

    print("=" * 92)
    print("reverify_attest 自检（S1–S7；S2–S7 六个负对照）")
    print("=" * 92)
    bad = 0
    for ok, name, detail in rows:
        print("  %s %s%s" % ("✅" if ok else "❌", name, "" if not detail else "｜" + detail))
        bad += 0 if ok else 1
    print("  ⇒ %d/%d 通过" % (len(rows) - bad, len(rows)))
    return 0 if bad == 0 else 1


def main():
    ap = argparse.ArgumentParser(description="Phase 73 验证器等价性证明")
    ap.add_argument("--from-rev", default=None, help="旧 blob 对应提交（缺省自动反查 vch）")
    ap.add_argument("--from-hash", default=None, help="旧 vch（缺省从图里取唯一旧值）")
    ap.add_argument("--verifier", default="verification_model")
    ap.add_argument("--exclude-type", action="append", default=[],
                    help="新增分支只可能命中的边类型（可多次给）")
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()

    if a.selfcheck:
        return selfcheck()
    if a.check:
        rows = check_attestations()
        if not rows:
            print("[前置缺失] 没有等价性证明表（%s）⇒ 本检查无从判定" % ed.ATTESTATION_OUT)
            print("           %s" % interp.skip_detail(
                "引用 rva 的证据会一律回落 stale",
                "这是 fail-closed 的**正确**行为，不是缺陷；要消除它请先跑 "
                "`reverify_attest.py --emit` 生成证明"))
            return 2
        bad = 0
        for i, ok, detail in rows:
            print("  %s %s｜%s" % ("✅" if ok else ("❌" if ok is False else "⏭"), i, detail))
            bad += 1 if ok is False else 0
        print("⇒ %d 条，失败 %d" % (len(rows), bad))
        return 0 if bad == 0 else 1
    if not a.emit:
        print(__doc__)
        return 0
    frm = a.from_hash
    if frm is None:
        g = json.load(open(os.path.join(ROOT, "06_PoC", "etl", "normalized.json"), encoding="utf-8"))
        vs = collections.Counter()
        for e in g["edges"]:
            for ev in ((e.get("props") or {}).get("verification_evidence") or []):
                if isinstance(ev, dict) and ev.get("vfr") == a.verifier:
                    vs[ev.get("vch")] += 1
        if len(vs) != 1:
            print("[拒绝] 图里 %s 的 vch 不唯一：%s" % (a.verifier, dict(vs)), file=sys.stderr)
            return 2
        frm = next(iter(vs))
    return emit(from_rev=a.from_rev, verifier=a.verifier, frm=frm, to=None,
                exclude_types=a.exclude_type)


if __name__ == "__main__":
    sys.exit(main())
