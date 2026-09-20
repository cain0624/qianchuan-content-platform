#!/usr/bin/env python3
"""对拍：本地 FastAPI 后端 vs 浏览器内门面（api_facade）

为什么必须做这件事：
    server.py 和 api_facade.py 是同一份 core/ 的**两个壳**。业务逻辑共用，
    但路由层（参数解析、字段拼装）是各写一遍的 —— 这就存在漂移的可能。
    最危险的不是崩溃，而是**线上和本地给出不同的合规判定**：这类问题
    很难被发现（两边都不报错，只是结果不一样），所以要做确定性对拍。

对拍什么：
    给两边喂**完全相同的输入**，比对**确定性投影**。跳过随机/易变量
    （时间戳、输出文件名、耗时、trace 的 latency），比对文案与判定
    （场景、关键词、标题文案、合规 verdict/风险分/命中项、CTR 各项、分段、
     每个尺寸的文字覆盖）。

三种用法：
    python tools/parity_check.py                      # 本地后端 vs 本地门面
    python tools/parity_check.py --dump-projection p.json
    python tools/parity_check.py --against-browser p.json   # 本地后端 vs 浏览器导出的投影
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "core"))

BASE = os.getenv("PARITY_BASE", "http://127.0.0.1:8848")

# 「链路共 8 步，耗时 1237ms，覆盖 8 层。」—— 耗时部分每轮都不同，比对前抹平
_DUR_RE = re.compile(r"耗时\s*\d+\s*ms")

# 对拍用例：(名称, 路径, 方法, 请求体)
CASES = [    ("直播间封面·有人物", "/api/generate", "POST", {
        "theme": "利率下行，普通人怎么守住钱袋子", "variant": "v2_safe",
        "outputPlatform": "live", "styleFamily": "youth",
        "sizes": ["1:1", "3:4"], "platform": "alipay_finance", "maxRetry": 2,
        "assets": {"portraitId": "as_demo_portrait", "anchorTitle": "资深保险规划师"},
    }),
    ("直播间封面·纯图文", "/api/generate", "POST", {
        "theme": "年金险适合什么样的人", "variant": "v2_safe",
        "outputPlatform": "live", "styleFamily": "classic",
        "sizes": ["1:1"], "platform": "douyin", "maxRetry": 2, "assets": {},
    }),
    ("小红书图文", "/api/generate", "POST", {
        "theme": "30岁前一定要配好的4张保单", "variant": "v2_safe",
        "outputPlatform": "xhs", "styleFamily": "youth", "sizes": ["3:4"],
        "platform": "douyin", "maxRetry": 2, "assets": {},
        "topics": ["保险科普", "家庭保障"],
    }),
    ("抖音视频封面", "/api/generate", "POST", {
        "theme": "保额买多少才算够", "variant": "v2_safe",
        "outputPlatform": "dy_video", "styleFamily": "youth", "sizes": ["9:16"],
        "platform": "douyin", "maxRetry": 2, "assets": {},
        "durationLabel": "03:00",
    }),
    ("合规对抗样本", "/api/precheck", "POST",
     {"text": "全网最低价 保本稳赚 什么病都能赔", "platform": "douyin"}),
    ("合规对抗样本·干净", "/api/precheck", "POST",
     {"text": "看懂条款再签字，按需配置", "platform": "alipay_finance"}),
    ("元数据", "/api/meta", "GET", None),
    ("规则表", "/api/rules", "GET", None),
]


def http_call(path, body, method):
    data = None
    headers = {}
    if method != "GET" and body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"__http_error__": e.code, "body": e.read().decode("utf-8", "replace")[:300]}


def facade_call(path, body, method):
    import api_facade
    env = json.loads(api_facade.call(path, json.dumps(body or {}), method))
    if not env.get("ok"):
        return {"__facade_error__": env.get("error"), "kind": env.get("kind")}
    return env["data"]


def project_generate(d):
    """generate 结果的确定性投影。跳过 ts / url / 耗时 / trace 时延。"""
    def p_copy(c):
        return {k: v for k, v in (c or {}).items()}

    return {
        "sceneId": d.get("sceneId"),
        "sceneName": d.get("sceneName"),
        "confidence": d.get("confidence"),
        "audience": d.get("audience"),
        "matchedKeywords": d.get("matchedKeywords"),
        "runnerUp": d.get("runnerUp"),
        "template": {k: (d.get("template") or {}).get(k)
                     for k in ("id", "name", "family", "familyName", "platform", "platformName")},
        "platformBlock": {k: (d.get("platform") or {}).get(k) for k in ("id", "name", "kind")},
        "layoutMode": d.get("layoutMode"),
        "layoutName": d.get("layoutName"),
        "layoutArrange": d.get("layoutArrange"),
        "copy": p_copy(d.get("copy")),
        "compliance": {
            "verdict": (d.get("compliance") or {}).get("verdict"),
            "riskScore": (d.get("compliance") or {}).get("riskScore"),
            "riskLevel": (d.get("compliance") or {}).get("riskLevel"),
            "riskItems": (d.get("compliance") or {}).get("riskItems"),
            "suggestions": (d.get("compliance") or {}).get("suggestions"),
            "attempts": [
                {"round": a.get("round"), "variant": a.get("variant"), "verdict": a.get("verdict"),
                 "riskScore": a.get("riskScore"), "items": a.get("items")}
                for a in ((d.get("compliance") or {}).get("attempts") or [])
            ],
        },
        "ctr": {"score": (d.get("ctr") or {}).get("score"),
                "items": (d.get("ctr") or {}).get("items")},
        "segments": d.get("segments"),
        "disclaimers": d.get("disclaimers"),
        "imagePrompt": d.get("imagePrompt"),
        "covers": [{k: c.get(k) for k in ("sizeKey", "W", "H", "textRatio", "mainTitle",
                                          "subtitle", "badge", "label", "footerLeft", "footerRight")}
                   for c in (d.get("covers") or [])],
        "events": [{k: e.get(k) for k in ("layer", "agent", "action", "detail", "status")}
                   for e in (d.get("events") or [])],
        # 耗时天然不可能两边相同（后端要过网络、门面是进程内直调），
        # 所以把时间数字抹平再比 —— 否则每轮都报一堆假差异，真差异会被淹掉。
        "replay": [_DUR_RE.sub("耗时 Xms", x) for x in (d.get("replay") or [])],
        "trace": {"steps": (d.get("trace") or {}).get("steps"),
                  "layers": [{k: v for k, v in l.items() if k not in ("ms", "latency_ms")}
                             for l in ((d.get("trace") or {}).get("layers") or [])],
                  "statusStats": (d.get("trace") or {}).get("statusStats")},
    }


PROJECTORS = {
    "/api/generate": project_generate,
}

# /api/meta、/api/rules 里的这些字段是随运行环境变化的，比对时剔除
VOLATILE_PATHS = ()


def project(path, d):
    fn = PROJECTORS.get(path)
    if fn:
        return fn(d)
    return d


def diff(a, b, path="", out=None, limit=40):
    """递归比对，返回差异列表（截断到 limit 条）。"""
    out = out if out is not None else []
    if len(out) >= limit:
        return out
    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        out.append(f"{path}: 类型不同 {type(a).__name__} vs {type(b).__name__}")
        return out
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k}: 只有门面有 → {short(b[k])}")
            elif k not in b:
                out.append(f"{path}.{k}: 只有后端有 → {short(a[k])}")
            else:
                diff(a[k], b[k], f"{path}.{k}", out, limit)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: 列表长度不同 {len(a)} vs {len(b)}")
        for i in range(min(len(a), len(b))):
            diff(a[i], b[i], f"{path}[{i}]", out, limit)
    else:
        if a != b:
            out.append(f"{path}: {short(a)} ≠ {short(b)}")
    return out


def short(v, n=110):
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= n else s[:n] + "…"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-projection", help="把后端的投影写到文件")
    ap.add_argument("--against-browser", help="与浏览器导出的投影 JSON 对拍（跳过 HTTP 后端）")
    ap.add_argument("--only", help="只跑名称包含该子串的用例")
    args = ap.parse_args()

    health = None
    if not args.against_browser:
        try:
            health = http_call("/api/health", None, "GET")
        except Exception as e:
            print(f"✗ 连不上后端 {BASE}：{e}\n  先启动：python daemon.py start")
            return 2
        print(f"后端 {BASE} → {health}\n")

    browser = None
    if args.against_browser:
        with open(args.against_browser, encoding="utf-8") as f:
            browser = json.load(f)
        print(f"浏览器投影：{args.against_browser}（{len(browser)} 条）\n")

    cases = [c for c in CASES if not args.only or args.only in c[0]]
    dumped = {}
    ok = bad = 0
    for name, path, method, body in cases:
        if browser is not None:
            ref = (browser.get(name) or {}).get("projection")
            if ref is None:
                print(f"— {name}：浏览器投影里没有这条，跳过")
                continue
            left_label, right_label = "后端", "浏览器"
            left = project(path, http_call(path, body, method))
            right = ref
        else:
            left = project(path, http_call(path, body, method))
            right = project(path, facade_call(path, body, method))
            left_label, right_label = "后端", "门面"

        diffs = diff(left, right)
        mark = "✓" if not diffs else "✗"
        if diffs:
            bad += 1
            print(f"{mark} {name}")
            for line in diffs[:14]:
                print(f"     {left_label} {line}")
        else:
            ok += 1
            print(f"{mark} {name}　（{left_label} 与 {right_label} 逐字段一致）")
        dumped[name] = {"path": path, "method": method, "body": body, "projection": left}

    if args.dump_projection:
        with open(args.dump_projection, "w", encoding="utf-8") as f:
            json.dump(dumped, f, ensure_ascii=False, indent=1)
        print(f"\n投影已写入 {args.dump_projection}")

    print(f"\n结果：{ok}/{ok + bad} 一致")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
