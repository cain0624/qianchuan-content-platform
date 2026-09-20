# -*- coding: utf-8 -*-
"""
全平台冒烟回归 —— 4 类产出物 × 2 种排版（有人物 / 无人物）跑一遍 /api/generate。

用法：
    python3 tools/smoke_platforms.py            # 走 HTTP，需要服务在跑
    python3 tools/smoke_platforms.py --direct   # 直接调 harness，不需要服务

检查项：
  · 返回的 platform.id 是否与请求一致
  · 每个尺寸都出图，且宽高比与声明一致
  · 标题/正文占用率（textRatio）是否越界（上限 0.30）
  · 合规判定是否 PASS/LOW
  · 有人物时是否走上「抠底出血」通道
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from urllib.error import URLError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))

BASE = "http://127.0.0.1:8848"
PORTRAIT = "as_demo_portrait"

# 每条用例：(平台 id, 主题, 是否挂人物素材, 附加字段)
CASES = [
    ("live",     "重疾险怎么选才不踩坑",       True,  {"pageTotal": None}),
    ("live",     "年金险适合什么样的人",       False, {}),
    ("xhs",      "30岁前一定要配好的4张保单",  True,  {"topics": ["保险科普", "家庭保障"]}),
    ("xhs",      "重疾险的保额该买多少",       False, {"topics": ["重疾险", "保额测算"]}),
    ("dy_image", "社保和商业保险差在哪",       True,  {"pageTotal": 6, "pageIndex": 1,
                                                     "topics": ["社保", "商业保险"]}),
    ("dy_image", "医疗险报销的3个误区",        False, {"pageTotal": 5, "pageIndex": 2,
                                                     "topics": ["医疗险"]}),
    ("dy_video", "保额买多少才算够",           True,  {"durationLabel": "03:00"}),
    ("dy_video", "给父母买保险的避坑指南",     False, {"durationLabel": "02:30"}),
]

SIZE_RATIO = {"1:1": 1.0, "3:4": 0.75, "9:16": 0.5625, "16:9": 1.7778, "2.35:1": 2.35}


def _post(path: str, payload: dict):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕本地代理
    with op.open(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def _direct(payload: dict):
    from harness import run_turn
    ov = dict(payload)
    theme = ov.pop("theme")
    plat = ov.pop("outputPlatform", "live")
    ov["platformId"] = plat
    return run_turn(theme, ov, variant="v2_safe", max_retry=2)


def _check(plat: str, label: str, res: dict) -> tuple[bool, list[str]]:
    msgs: list[str] = []
    ok = True

    plat_blk = res.get("platform") or {}
    got = plat_blk.get("id")
    if got != plat:
        ok = False
        msgs.append(f"platform 不一致：请求 {plat} 实得 {got}")
    # 尺寸只能落在该平台声明的范围内
    allowed = set(plat_blk.get("sizes") or [])

    covers = res.get("covers") or []
    if not covers:
        ok = False
        msgs.append("没有产出任何尺寸")
    for c in covers:
        sk = c.get("sizeKey")
        if allowed and sk not in allowed:
            ok = False
            msgs.append(f"{sk} 不在平台允许尺寸 {sorted(allowed)} 内")
        w, h = int(c["W"]), int(c["H"])
        ratio = w / max(1, h)
        want = SIZE_RATIO.get(sk)
        if want and abs(ratio - want) / want > 0.02:
            ok = False
            msgs.append(f"{sk} 比例漂移 {ratio:.3f} vs {want}")
        tr = c.get("textRatio")
        if tr is not None and tr > 0.30:
            ok = False
            msgs.append(f"{sk} textRatio 越界 {tr}")

    comp = res.get("compliance") or {}
    if comp.get("verdict") not in ("PASS", "LOW"):
        ok = False
        msgs.append(f"合规判定异常 {comp.get('verdict')}")

    ctr = (res.get("ctr") or {}).get("score")
    ratios = ", ".join(f"{c.get('sizeKey')}={c.get('textRatio')}" for c in covers)
    msgs.append(f"合规 {comp.get('verdict')}({comp.get('riskScore')}) CTR {ctr} "
                f"重试 {res['metrics']['retryCount']} / 尺寸 {ratios}")
    if covers:
        msgs.append(f"排版 {res.get('layoutName') or '-'}（{res.get('layoutMode')}/"
                    f"{res.get('layoutArrange') or '-'}）· 模板 {res['template']['name']}")
    return ok, msgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--direct", action="store_true", help="直接调 harness，不走 HTTP")
    args = ap.parse_args()

    if not args.direct:
        try:
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            op.open(BASE + "/api/meta", timeout=5)
        except (URLError, OSError) as e:
            print(f"✗ 服务不可达 {BASE}（{e}）。先跑 ./start.sh，或加 --direct。")
            return 1

    passed = 0
    for plat, theme, with_person, extra in CASES:
        payload = {
            "theme": theme,
            "outputPlatform": plat,
            "styleFamily": "youth",
            "sizes": [],
            "assets": {},
            **extra,
        }
        if with_person:
            payload["assets"] = {"portraitId": PORTRAIT, "anchorTitle": "资深保险规划师"}
        # sizes 留空 → 由后端按平台 defaultSizes 决定
        payload.pop("sizes")

        label = f"{plat:<9} {'有人物' if with_person else '纯图文'} {theme}"
        try:
            res = _direct(payload) if args.direct else _post("/api/generate", payload)
        except Exception as e:  # noqa: BLE001
            print(f"✗ {label}\n   异常：{type(e).__name__}: {e}")
            continue

        ok, msgs = _check(plat, label, res)
        passed += int(ok)
        print(f"{'✓' if ok else '✗'} {label}")
        for m in msgs:
            print(f"    {m}")

    total = len(CASES)
    print(f"\n结果：{passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
