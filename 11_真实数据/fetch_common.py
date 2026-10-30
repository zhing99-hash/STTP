#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
"""真实数据源抓取公共层（规划 F17）

为什么现在才建，以及为什么必须建
--------------------------------
Phase 62—63 每次抓新数据，都在**新的 ingest 脚本里重写一遍 HTTP**：

    openalex_ingest.py   _opener / _get_json / time.sleep(0.35)
    chebi_synonym_fetch  （又一份）
    _recon_papers.py     urllib.request.Request + User-Agent（第三份）

三份实现的差别都不写在文档里，只写在各自的注释里 —— 例如 `openalex_ingest` 特意用
`ProxyHandler({})` **绕过代理**（代理对 api.openalex.org 会 502），而侦察脚本用默认
opener 走系统代理。这是 2026-10-20 实测出来的环境事实，**复制粘贴一次就可能丢一次**。

G4 论文线要抓 80 次 + 分页，再写第四份 HTTP 就不是"临时脚本"，是把一个已知环境坑
复制成第五份。所以这一层的价值不在"省代码"，在**让环境事实只写一次**。

迁移范围（截至 2026-10-21，**只有一处**）
----------------------------------------
    ✅ openalex_ingest.py    已改为薄转发到本层
    ❌ chebi_synonym_fetch.py   仍自带 opener（且显式走代理 PROXY 常量）
    ❌ 06_PoC/_recon_papers.py  仍自带 urllib.request.Request（走系统代理）

★ 为什么把没做完的事写在这儿：本层自己的标题叫"公共层"，读的人自然会以为已经
  单一来源了 —— 而这三份里恰好有两份对代理的处理**互相矛盾**。不写清楚，就等于
  让人以为"环境事实只写一次"已经成立，从而不再怀疑下一份新脚本里的 opener。
  （与 D92 同源：宣称覆盖 ≠ 实测覆盖；骗外人容易，骗过自己也容易，唯独骗不过一次运行。）

谁必须走本层：**任何会重复请求外部 API 的抓取**。一次性探索脚本可以例外，
但例外要写在它自己的文件头里，别放在这里。

本层提供三件事（都不在标准库里，都是踩过的坑）
---------------------------------------------
1. `get_json` —— 礼貌限速 + **按成因分类的失败** + 统计：
   * 5xx/超时/短 Retry-After 的 429 ⇒ 退避重试
   * 403/404 ⇒ **立即失败**：重试一个 403 只会烧配额并把"已经拒绝"伪装成"网络抖动"
   * **配额耗尽的 429 ⇒ 立即失败，不重试**（D96）：这类 429 说得清清楚楚 ——
     `Retry-After: 80202`、`X-RateLimit-Remaining-USD: 0`、body 里 "Insufficient
     budget"。再退避 30 秒 × 3 次也救不回来，只会把一次 80 条的检索变成
     一个跑了两小时、产出 65 条失败的运行。
   * 统计进 `STATS`，**外面能看见** —— 不允许抓取层静默失败（D70 通律 6：预期事件
     要分类 + 留痕，不许 `except: pass`）
2. `invert_abstract` —— OpenAlex 的摘要是 `abstract_inverted_index`（词 → 位置表），
   本仓库 `to_raw()` 从未保留它 ⇒ 188 条 raw 摘要全为 0（R5 实测）。还原函数只此一份。
3. `--selfcheck` —— 离线可跑的判据。别小看它：invert 写错一个 off-by-one 不会报错，
   只会让你得到一堆**看起来通顺但词序漂移**的摘要。

用法
----
    from fetch_common import get_json, invert_abstract, STATS
    j = get_json(url)                     # 直连、限速、可重试
    s = invert_abstract(w.get("abstract_inverted_index"))

    python 11_真实数据/fetch_common.py --selfcheck     # 离线自检，RC=0 才可用
"""
from __future__ import annotations

import json
import os
import random
import ssl
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

POLITE_MAILTO = "sttp-etl@example.org"
USER_AGENT = "STTP-ETL/0.1 (research; mailto:%s)" % POLITE_MAILTO

# ★ OpenAlex 要求自报 mailto，否则会被丢进慢速池（polite pool）
# ★ 2026-10-21 实测的**真实配额**（之前那条注释写错了因果，见 D96）：
#   OpenAlex 对没有 api_key 的请求按**出口 IP** 结算，不是按速率：
#       X-RateLimit-Limit           1000 次/天
#       X-RateLimit-Limit-USD       $0.1/天   ← 这条才是真闸门
#       X-RateLimit-Cost-Required-USD   $0.001/次（带 title_and_abstract.search 的
#                                      请求，无 search 的便宜请求扣得少）
#   ⇒ 一天约 100 次检索就把整条 IP 的当日额度烧光，然后返回的是
#     `Retry-After: 80202`（22 小时）而不是秒级退避能救回来的东西。
#   本轮 80 条 × 1 次 ≈ 用掉 80% 额度，**同一个日历天内不要重跑**。
#   限速 0.5s 只为礼貌，跟配额无关 —— 别再把它当成防 429 的手段。
DEFAULT_MIN_INTERVAL = 0.5
DEFAULT_TIMEOUT = 30

STATS = {"requests": 0, "cache_hits": 0, "retries": 0, "failures": 0,
         "throttled": 0, "budget_blocked": 0}

_last_call = [0.0]

# 判为「配额耗尽」的最小 Retry-After（秒）。低于它 ⇒ 瞬时限流，退避有意义；
# 高于它 ⇒ 服务端明说了"今天别来了"，退避只是浪费时间。**阈值必须有依据**：
# OpenAlex 的配额窗是"到 UTC 午夜"，最短也是几分钟起跳，取 600s 足够分离两种形态。
BUDGET_RETRY_AFTER_MIN = 600.0


class FetchError(RuntimeError):
    """抓取失败（已重试到上限，或遇到不可重试状态码）。"""


class BudgetExhausted(FetchError):
    """配额耗尽：清楚 country-of-origin 地告诉调用方**什么时候能重试**。

    分开一个子类的理由不是面向对象洁癖：调用方必须能把"网络不好"和"今天额度没了"
    区分开 —— 前者重试有意义，后者重试只会在日志里堆垃圾并把 80 条全都跑成失败。
    """

    def __init__(self, msg, *, retry_after=None, reset_at=None, headers=None):
        super().__init__(msg)
        self.retry_after = retry_after
        self.reset_at = reset_at
        self.headers = headers or {}


def classify_throttle(headers: dict | None, body: str = "", retry_after=None) -> dict:
    """把一次 HTTP 429 的**成因**判出来（纯函数，离线可测）。

    返回 {"kind": ..., "retry_after": float|None, "reset_at": int|None, "why": str}

    三种 kind：
      * "budget"   —— 当日额度耗尽。**绝对不要重试**。
      * "transient"—— 瞬时突发限流。退避后重试有意义。
      * "unknown"  —— 判不出来（没有头）。保守按 transient 处理，重试次数由调用方限。

    ★ 为什么不能只看状态码：429 是一个"我没给你数据"的笼统说法，它的**成因**决定
      下一步动作。把两者混为一谈，就会对着"今天额度没了"做退避重试 —— 2026-10-21
      的第一次修法就是这个错（D96），结果把 80 条的检索跑成了两小时 + 65 条失败。
    """
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    ra = None
    if retry_after is None:
        retry_after = h.get("retry-after")
    if retry_after is not None:
        try:
            ra = float(retry_after)
        except (TypeError, ValueError):
            ra = None

    why_bits = []
    # 判据 1：`Remaining-USD` 归零。这是最直接的一条 —— 没钱就是没钱。
    rem_usd = h.get("x-ratelimit-remaining-usd")
    prepaid = h.get("x-ratelimit-prepaid-remaining-usd")
    budget_zero = False
    for v in (rem_usd, prepaid):
        if v is not None:
            try:
                budget_zero = budget_zero or float(v) <= 0
            except (TypeError, ValueError):
                pass
    if budget_zero:
        why_bits.append("剩余额度 USD=0")

    # 判据 2：`Remaining` 计数归零
    rem = h.get("x-ratelimit-remaining")
    try:
        if rem is not None and int(rem) <= 0:
            why_bits.append("剩余次数=0")
    except (TypeError, ValueError):
        pass

    # 判据 3：body 明说预算
    if "insufficient budget" in (body or "").lower():
        why_bits.append("body:insufficient budget")

    # 判据 4：Retry-After 长到不像瞬时抖动
    if ra is not None and ra >= BUDGET_RETRY_AFTER_MIN:
        why_bits.append("Retry-After=%.0fs" % ra)

    is_budget = (budget_zero
                 or (ra is not None and ra >= BUDGET_RETRY_AFTER_MIN)
                 or "insufficient budget" in (body or "").lower())

    reset_at = None
    rst = h.get("x-ratelimit-reset")
    if rst is not None:
        try:
            reset_at = int(float(rst))
        except (TypeError, ValueError):
            reset_at = None

    if not why_bits and ra is None:
        kind = "unknown"
    elif is_budget:
        kind = "budget"
    else:
        kind = "transient"
    return {"kind": kind, "retry_after": ra, "reset_at": reset_at,
            "why": "；".join(why_bits) or "无可用配额头"}


# ---------------------------------------------------------------- HTTP
def _opener(direct: bool = True):
    """direct=True ⇒ 显式绕过系统代理。

    ★ 环境事实（2026-10-20 实测）：本机 http_proxy 指向 127.0.0.1:10808，
      对 api.openalex.org 会返回 CONNECT 502；**直连 1.2s 就通**。
      所以默认值是"绕过代理"，不是"跟随代理"。写成 default 容易忘，
      写在这里只有一处，忘了也只错一处。
    """
    ctx = ssl.create_default_context()
    handlers = [urllib.request.HTTPSHandler(context=ctx)]
    if direct:
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def _sleep_politely(min_interval: float):
    gap = time.time() - _last_call[0]
    if 0 < gap < min_interval:
        time.sleep(min_interval - gap)
    _last_call[0] = time.time()


def get_json(url: str, *, timeout: int = DEFAULT_TIMEOUT, tries: int = 3,
             min_interval: float = DEFAULT_MIN_INTERVAL, direct: bool = True,
             cache_dir: str | None = None):
    """GET → JSON。仅对**可重试**错误退避重试；统计留痕；缓存可选（默认关闭）。

    cache 默认关：开了会往工作树落文件，而本仓库的交付要求「干净树才可发布」，
    让抓取层默认有副作用是给自己挖坑。需要断点续跑时显式传 `cache_dir`。
    """
    key = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        import hashlib
        key = os.path.join(cache_dir, hashlib.sha256(url.encode("utf-8")).hexdigest()[:24] + ".json")  # noloss-exempt: 缓存文件名用 hash 前缀，非数据
        if os.path.exists(key):
            STATS["cache_hits"] += 1
            with open(key, encoding="utf-8") as f:
                return json.load(f)

    last = None
    retry_after = None
    for attempt in range(tries):
        if attempt:
            STATS["retries"] += 1
        _sleep_politely(min_interval)
        STATS["requests"] += 1
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            })
            with _opener(direct).open(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            if key:
                with open(key, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
            return data
        except urllib.error.HTTPError as e:
            last = "HTTP %s" % e.code
            # ★ 403/404 不可重试：重试只会烧配额并把"已经拒绝"伪装成"网络不好"
            if e.code not in (429, 500, 502, 503, 504):
                STATS["failures"] += 1
                raise FetchError("%s（不可重试）" % last)
            if e.code == 429:
                STATS["throttled"] += 1
                try:
                    body429 = e.read().decode("utf-8", "replace")
                except Exception:                       # noqa: BLE001
                    body429 = ""
                verdict = classify_throttle(e.headers, body429)
                # ★★ D96：配额耗尽 ⇒ **立刻放弃**，不进重试循环。
                #   这不是"早点失败显得干净"，是省下 (tries-1) 次注定失败的请求
                #   并把真实原因（含何时重置）交给调用方决定。
                if verdict["kind"] == "budget":
                    STATS["failures"] += 1
                    STATS["budget_blocked"] += 1
                    raise BudgetExhausted(
                        "OpenAlex 当日额度已耗尽（%s），%s"
                        % (verdict["why"], _human_reset(verdict["retry_after"])),
                        retry_after=verdict["retry_after"],
                        reset_at=verdict["reset_at"], headers=dict(e.headers or {}),
                    )
                retry_after = verdict["retry_after"]
            else:
                retry_after = None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = "%s: %s" % (type(e).__name__, e)
            retry_after = None
        if attempt + 1 < tries:
            # 只到这一步的 429 才是"瞬时"的：退避重试有意义。
            # （预算型 429 在上面的分支已经 raise 出去了，不会走到这里。）
            if last.startswith("HTTP 429") and retry_after:
                wait = min(90.0, max(1.0, retry_after))
            else:
                wait = min(30.0, 2.0 * (2 ** attempt))
            time.sleep(wait + random.random() * 0.5)
            retry_after = None
    STATS["failures"] += 1
    raise FetchError("重试 %d 次仍失败：%s" % (tries, last))


def _human_reset(retry_after) -> str:
    """把 Retry-After 秒数说成人话（含**绝对时刻**）。

    只报"请 80202 秒后再试"等于让人自己算 —— 而这个数字算错一天的代价是白等
    或白跑。报出本地时刻，人和脚本都能直接用。
    """
    try:
        secs = float(retry_after)
    except (TypeError, ValueError):
        return "服务端未给出重置时间"
    if secs <= 0:
        return "服务端未给出重置时间"
    reset_dt = time.time() + secs
    local = time.strftime("%Y-%m-%d %H:%M", time.localtime(reset_dt))
    utc = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(reset_dt))
    return "约 %.1f 小时后可用（本地 %s ／ %s）" % (secs / 3600.0, local, utc)


# ------------------------------------------------------- OpenAlex 摘要
def invert_abstract(aix: dict | None) -> str:
    """`abstract_inverted_index`（词 → 位置列表）→ 原文文本。

    还原是**按位置填槽**而不是按 dict 遍历顺序：Python 3.7+ 的 dict 保序会让人
    误以为可以按 key 顺序拼，实际位置才是语序。若某位置缺词（上游填空位），
    跳过而不是补空串 —— 补空串会让句子里出现一堆双空格，看不出来但会污染
    后续任何基于子串的定位句。
    """
    if not aix:
        return ""
    slots = {}
    for word, poss in aix.items():
        for p in (poss or []):
            slots[p] = word
    if not slots:
        return ""
    out = []
    for i in range(max(slots) + 1):
        w = slots.get(i)
        if w:
            out.append(w)
    return " ".join(out)


def abstract_len(aix: dict | None) -> int:
    """摘要的**词数**（不还原文本也能算；用来统计覆盖率，省一次 join）。"""
    return sum(len(p or []) for p in (aix or {}).values())


# ---------------------------------------------------------------- 自检
# 说明：样例是**手工构造**的，故意包含三种会让错误实现出错的形状：
#   ① dict 遍历顺序 ≠ 语序（'equation' 在 'the' 之前声明，位置却在后面）
#   ② 位置空洞（索引不连续）
#   ③ Unicode（Schrödinger / Ångström）—— UTF-8 与 JSON escape 处理错会在这一步露出来
_A = {
    "The": [0], "equation": [6], "was": [3], "derived": [4], "by": [5],
    "Schrödinger": [7], "in": [8], "Ångström": [9],
}
_A_TEXT = "The was derived by equation Schrödinger in Ångström"


def _check(ok: bool, name: str, detail: str = "") -> bool:
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  — " + detail) if detail else ""))
    return ok


def selfcheck() -> int:
    """离线自检（`python fetch_common.py --selfcheck`）。

    ★ 设计约束：**不联网**。HTTP 那部分（限速/重试）的判据不在这里 —— 它要靠
    `--net` 才测得动，而离线自检必须秒级且结果稳定（Phase 63 立下的纪律：
    「便宜到有人愿意跑」）。这里只守**纯函数**契约。
    """
    print("=" * 74)
    print("fetch_common 离线自检（纯函数契约；HTTP 部分需 --net）")
    print("=" * 74)
    # ---- D96：429 的成因分类。样例头是今天从 OpenAlex 响应里**逐字抄下来**的 ----
    _H_BUDGET = {
        "Retry-After": "80202",
        "X-RateLimit-Limit": "1000",
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Limit-USD": "0.1",
        "X-RateLimit-Remaining-USD": "0",
        "X-RateLimit-Prepaid-Remaining-USD": "0",
        "X-RateLimit-Cost-Required-USD": "0.001",
        "X-RateLimit-Reset": "80202",
    }
    _H_TRANSIENT = {"Retry-After": "3", "X-RateLimit-Remaining": "17",
                    "X-RateLimit-Remaining-USD": "0.017"}
    _BODY_BUDGET = ('{"error":"Rate limit exceeded","message":"Insufficient budget. '
                    "This request has no API key, so it counts against the free daily "
                    'budget shared by everyone on your network\'s IP address"}')
    v_b = classify_throttle(_H_BUDGET, _BODY_BUDGET)
    v_t = classify_throttle(_H_TRANSIENT, "")
    v_none = classify_throttle({}, "")
    v_onlybody = classify_throttle({}, _BODY_BUDGET)

    rs = [
        _check(invert_abstract(_A) == _A_TEXT, "F1 倒排摘要按位置还原（不按 dict 顺序）",
               "得到：%r" % invert_abstract(_A)[:60]),  # noloss-exempt: 错误信息截断，不入图
        _check(invert_abstract(None) == "" and invert_abstract({}) == "",
               "F2 空输入 → 空串（None 与 {} 都要扛）"),
        _check(abstract_len(_A) == 8, "F3 词数 = 位置条目数（样例故意缺位 1、2）",
               "= %d" % abstract_len(_A)),
        _check(invert_abstract({"only": [5]}) == "only",
               "F4 高位单槽：不从 0 起也能出词", invert_abstract({"only": [5]})),
        _check(" ".join(invert_abstract(_A).split()) == invert_abstract(_A),
               "F5 不产生多余空白（空洞被跳过而非补空串）"),
        _check("Zzz" not in invert_abstract({"a": [0], "b": [2]}),
               "F6 空洞位置不插入占位符", invert_abstract({"a": [0], "b": [2]})),

        _check(v_b["kind"] == "budget" and v_b["retry_after"] == 80202.0,
               "F7 配额耗尽型 429 判为 budget（真头：Retry-After=80202 ／ USD=0）",
               "%s / %s" % (v_b["kind"], v_b["retry_after"])),
        _check(v_t["kind"] == "transient" and v_t["retry_after"] == 3.0,
               "F8 秒级 Retry-After 判为 transient（可重试）",
               "%s / %s" % (v_t["kind"], v_t["retry_after"])),
        _check(v_none["kind"] == "unknown",
               "F9 没有配额头 ⇒ unknown（保守按可重试处理，由调用方限次数）", v_none["why"]),
        _check(v_onlybody["kind"] == "budget",
               "F10 只有 body 说 insufficient budget 也能判出来（头可能不被代理转发）"),
        # ---- 负对照：证明上面四条不是恒真 ----
        _check(classify_throttle({"Retry-After": str(int(BUDGET_RETRY_AFTER_MIN - 1))},
                                 "insufficient budget")["kind"] == "budget",
               "F11 Retry-After 虽短但 body 认账 ⇒ 仍是 budget（三条判据是**或**，不是且）",
               "阈值 %ds" % int(BUDGET_RETRY_AFTER_MIN)),
        _check(classify_throttle({"Retry-After": str(int(BUDGET_RETRY_AFTER_MIN))},
                                 "")["kind"] == "budget",
               "F12 仅凭超长 Retry-After（无别的信号）也判 budget"),
        _check(classify_throttle({"Retry-After": "2", "X-RateLimit-Remaining": "900",
                                  "X-RateLimit-Remaining-USD": "0.09"},
                                 "")["kind"] == "transient",
               "F13 负对照：额度还足 + 短 Retry-After ⇒ transient（不是 budget）"),
        _check(isinstance(v_b["reset_at"], int) and v_b["reset_at"] == 80202,
               "F14 reset_at 从 X-RateLimit-Reset 取出且是整数（脚本可据此排期）"),
    ]
    bad = sum(1 for x in rs if not x)
    print("-" * 74)
    print("  合计 %d 条，通过 %d，失败 %d" % (len(rs), len(rs) - bad, bad))
    return 1 if bad else 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selfcheck" in argv:
        return selfcheck()
    print(__doc__)
    print("提示：加 --selfcheck 跑离线自检。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
