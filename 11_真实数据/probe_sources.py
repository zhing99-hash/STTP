# -*- coding: utf-8 -*-
"""probe_sources.py —— 外部数据源「可达性」探测器（项目铁律的可执行版本）。

为什么需要它
------------
本项目的出口是**按「域 + 路径」白名单放行**的，且 **TLS 可握手 ≠ HTTP 可读**：
  - 裸 TCP `create_connection` 会误报「通」（TCP 成功但 TLS 被 RST/超时）；
  - 只测到 TLS 握手又会把「路径级拦截」误判为「可达」
    （`export.arxiv.org/` 根 ✅，但 `/api/query` 被按路径拦截：TLS 成功、响应 0 字节）；
  - 单次探测会把「策略拦截」误判为「抖动」——本机出口是**直连被拦 + 代理约 1/3 成功率**
    的抖动通道，必须多轮复测。

因此本脚本对每个目标做 **三层递进 × 多轮复测 × 双通道（直连 / 代理）**：
  L1 TCP 443  →  L2 TLS 握手  →  L3 真实 HTTP 请求并**读到响应体**

判定口径
--------
  OK         读到响应体（HTTP 状态码可接受）
  PATH_BLOCK  TLS 成功但响应体为空 / 超时（→ 路径级拦截）
  TLS_BLOCK   TCP 通但 TLS 失败
  TCP_BLOCK   连 TCP 都通不了

用法
----
    python 11_真实数据/probe_sources.py                    # 探测内置候选清单
    python 11_真实数据/probe_sources.py --only crossref chembl
    python 11_真实数据/probe_sources.py --rounds 3 --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))

# ── 候选源清单：(键, 标签, 主机, 请求路径, 判定可接受的状态码) ────────────────────
TARGETS = [
    # 已知可达（基线，用于确认通道本身正常）
    ("openalex",   "OpenAlex（基线·已知可达）",     "api.openalex.org",          "/works?per-page=1",                                   (200,)),
    ("nist",       "NIST CODATA（基线·已知可达）",  "physics.nist.gov",          "/cuu/Constants/Table/allascii.txt",                    (200,)),
    # 本轮候选：文献层多源
    ("crossref",   "Crossref（文献层候选）",        "api.crossref.org",          "/works?rows=1",                                        (200,)),
    ("datacite",   "DataCite（文献层候选）",        "api.datacite.org",          "/dois?page[size]=1",                                   (200,)),
    ("s2",         "Semantic Scholar（文献候选）",  "api.semanticscholar.org",   "/graph/v1/paper/search?query=test&limit=1",            (200,)),
    ("europepmc",  "EuropePMC（文献候选）",         "www.ebi.ac.uk",             "/europepmc/webservices/rest/search?query=test&format=json&pageSize=1", (200,)),
    # 本轮候选：化学层
    ("chembl",     "ChEMBL（化学·药物分子层）",     "www.ebi.ac.uk",             "/chembl/api/data/molecule.json?limit=1",               (200,)),
    ("uniprot",    "UniProt（蛋白层候选）",         "rest.uniprot.org",          "/uniprotkb/search?query=insulin&size=1&format=json",   (200,)),
    # 已知被拦（对照，用于确认判定口径有效）
    ("wikidata",   "Wikidata（已知被拦·对照）",     "query.wikidata.org",        "/sparql?query=SELECT%20*%20WHERE%7B%7D%20LIMIT%201&format=json", (200,)),
]

ENTRY = "0.0.0.0"


def _proxy():
    for k in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"):
        v = os.environ.get(k)
        if v:
            v = v.strip()
            if "://" in v:
                v = v.split("://", 1)[1]
            if ":" in v:
                h, p = v.rsplit(":", 1)
                return (h, int(p))
    return None


def _try(host: str, path: str, accept, use_proxy, timeout):
    """返回 (layer, status, detail)。layer ∈ {OK, PATH_BLOCK, TLS_BLOCK, TCP_BLOCK}"""
    import http.client
    # L3a：走代理（CONNECT 隧道后发真实 HTTP 请求，读响应体）
    if use_proxy:
        try:
            c = http.client.HTTPSConnection(use_proxy[0], use_proxy[1], timeout=timeout)
            c.set_tunnel(host, 443)
            c.connect()
        except Exception as e:
            return ("TCP_BLOCK" if "refused" in str(e).lower() else "PATH_BLOCK", None,
                    "proxy tunnel: %s" % e)
        try:
            c.request("GET", path, headers={"User-Agent": "sttp-probe/1.0",
                                            "Accept": "application/json,text/plain,*/*"})
            r = c.getresponse()
            body = r.read(256)
            c.close()
            if r.status in accept:
                return ("OK", r.status, "%d B body" % len(body))
            return ("PATH_BLOCK", r.status, "http %s" % r.status)
        except Exception as e:
            return ("PATH_BLOCK", None, "request: %s" % e)
    # L1/L2/L3b：直连
    try:
        raw = socket.create_connection((host, 443), timeout=timeout)
    except Exception as e:
        return ("TCP_BLOCK", None, "%s" % e)
    try:
        ctx = ssl.create_default_context()
        t = ctx.wrap_socket(raw, server_hostname=host)
    except Exception as e:
        try:
            raw.close()
        except Exception:
            pass
        return ("TLS_BLOCK", None, "%s" % type(e).__name__)
    try:
        import http.client
        c = http.client.HTTPSConnection(host, 443, timeout=timeout, context=ctx)
        c.sock = t
        c.request("GET", path, headers={"User-Agent": "sttp-probe/1.0",
                                        "Accept": "application/json,text/plain,*/*"})
        r = c.getresponse()
        body = r.read(256)
        c.close()
        if r.status in accept:
            return ("OK", r.status, "%d B body" % len(body))
        return ("PATH_BLOCK", r.status, "http %s" % r.status)
    except Exception as e:
        return ("PATH_BLOCK", None, "request: %s" % e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None, help="只探测指定键")
    ap.add_argument("--rounds", type=int, default=3, help="每通道复测轮数（默认 3）")
    ap.add_argument("--timeout", type=float, default=12.0)
    ap.add_argument("--json", default=None, help="把结果写为 JSON")
    a = ap.parse_args()

    proxy = _proxy()
    print("=" * 78)
    print("STTP 数据源可达性探测 · 三层递进 × %d 轮 × 双通道" % a.rounds)
    print("出口代理：%s" % ("%s:%d" % proxy if proxy else "（未配置）"))
    print("=" * 78)

    rows = []
    for key, label, host, path, accept in TARGETS:
        if a.only and key not in a.only:
            continue
        rec = {"key": key, "label": label, "host": host, "path": path}
        for ch, up in (("direct", None), ("proxy", proxy)):
            if ch == "proxy" and not proxy:
                rec[ch] = {"verdict": "N/A", "ok": 0}
                continue
            layers, ok, first_ok = [], 0, None
            for i in range(a.rounds):
                layer, st, detail = _try(host, path, accept, up, a.timeout)
                layers.append(layer)
                if layer == "OK":
                    ok += 1
                    if first_ok is None:
                        first_ok = "http %s · %s" % (st, detail)
                time.sleep(0.4)
            # 判定：全 OK = 可达；有 OK 有失败 = 抖动；全失败取众数层
            if ok == a.rounds:
                verdict = "OK"
            elif ok > 0:
                verdict = "FLAKY"
            else:
                verdict = max(set(layers), key=layers.count)
            rec[ch] = {"verdict": verdict, "ok": ok, "rounds": a.rounds,
                       "layers": layers, "detail": first_ok}
        d, p = rec["direct"], rec.get("proxy", {})
        best = p.get("verdict") if p.get("verdict") in ("OK", "FLAKY") else d["verdict"]
        rec["best"] = best
        mark = {"OK": "✅", "FLAKY": "🟡", "PATH_BLOCK": "⛔", "TLS_BLOCK": "⛔",
                "TCP_BLOCK": "⛔", "N/A": "·"}.get(best, "?")
        print("\n%s %s  (%s)" % (mark, label, host))
        print("   直连 %-10s %d/%d  %s" % (d["verdict"], d["ok"], a.rounds,
                                          "".join(x[0] for x in d["layers"])))
        if p:
            print("   代理 %-10s %d/%d  %s" % (p.get("verdict"), p.get("ok", 0), a.rounds,
                                              "".join(x[0] for x in p.get("layers", []))))
        if d.get("detail") or (p or {}).get("detail"):
            print("   证据：%s" % (d.get("detail") or p.get("detail")))
        rows.append(rec)

    print("\n" + "=" * 78)
    print("汇总（best）")
    for r in rows:
        print("   %-11s %s" % (r["key"], r["best"]))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"probed_at": time.strftime("%Y-%m-%d %H:%M:%S"), "proxy": proxy,
                       "results": rows}, f, ensure_ascii=False, indent=2)
        print("\n已写入 %s" % a.json)


if __name__ == "__main__":
    main()
