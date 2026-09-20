# -*- coding: utf-8 -*-
"""
直播内容分段设计子 Agent。

用户只给一句主题，这里产出可直接照着开的**分段脚本骨架**：
时段 / 段落目标 / 话术要点 / 画面呈现 / 该段的合规红线提示。
"""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB_FILE = os.path.join(ROOT, "data", "knowledge.json")

with open(KB_FILE, "r", encoding="utf-8") as f:
    KB = json.load(f)
SCENE_BY_ID = {s["id"]: s for s in KB["scenes"]}

# 按段落语义推断画面呈现形式
FORM_RULES = [
    (["暖场", "开场"], "主播口播 + 主题字板（口播位预留）"),
    (["拆解", "讲机制", "算", "看条款", "读条款", "定节奏", "打底", "讲顺序", "认知校准"], "口播 + 图文要点板（关键数字放大）"),
    (["对比", "工具", "选工具", "谈工具", "配预算"], "三层资金图 / 对比表（横向对比，不做优劣结论）"),
    (["案例", "推演", "复盘"], "案例卡片（标注「示例，不构成投保建议」）"),
    (["答疑", "问答", "快答"], "弹幕上屏 + 快问快答（问题-回答 双栏）"),
    (["引导", "行动"], "引导条 + 预约入口（配合固定角标常驻）"),
]


def _form(name: str) -> str:
    for keys, form in FORM_RULES:
        if any(k in name for k in keys):
            return form
    return "口播 + 图文要点板"


def _mmss(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m // 60:02d}:{m % 60:02d}"


def build_segments(scene_id: str, duration: int = 60, audience: str = "") -> dict:
    scene = SCENE_BY_ID.get(scene_id) or KB["scenes"][0]
    src = scene["segments"]
    total_w = sum(s["weight"] for s in src)

    segs, acc = [], 0.0
    for i, s in enumerate(src):
        m = s["weight"] / total_w * duration
        start, end = acc, acc + m
        acc = end
        segs.append({
            "no": i + 1,
            "name": s["name"],
            "start": _mmss(start),
            "end": _mmss(end),
            "minutes": round(m, 1),
            "goal": s["goal"],
            "points": s["points"],
            "form": _form(s["name"]),
            "compliance": s["compliance"],
            "namePlate": s["name"].split(" · ")[-1],
        })
    # 修正累计误差：最后一段收回总时长
    segs[-1]["end"] = _mmss(duration)

    return {
        "sceneId": scene["id"],
        "sceneName": scene["name"],
        "audience": audience or scene["audience"],
        "duration": duration,
        "segmentCount": len(segs),
        "segments": segs,
        "structure": " -> ".join(s["namePlate"] for s in segs),
    }


if __name__ == "__main__":
    from copywriter import parse_theme
    for t in ["利率下行，普通人怎么守住钱袋子", "养老规划", "家庭保障怎么配置才合理"]:
        pack = parse_theme(t)
        r = build_segments(pack["sceneId"], 60, pack["audience"])
        print(f"== {t} ==  {r['duration']}min / {r['segmentCount']} 段")
        print(f"   结构: {r['structure']}")
        for s in r["segments"]:
            print(f"   [{s['start']}-{s['end']}] {s['name']}  ({s['minutes']}min) -> {s['goal']}")
        print()
