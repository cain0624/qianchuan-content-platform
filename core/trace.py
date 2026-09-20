# -*- coding: utf-8 -*-
"""Trace：事件构造 / 分层耗时 / 状态聚合 / 复盘。"""
from __future__ import annotations

import time

LAYERS = [
    {"id": "L1", "name": "输入解析层", "agent": "主题解析器"},
    {"id": "L2", "name": "内容设计层", "agent": "内容分段设计 Agent"},
    {"id": "L3", "name": "文案生成层", "agent": "封面文案生成子 Agent"},
    {"id": "L4", "name": "合规预检闸", "agent": "合规检测（生成侧前置）"},
    {"id": "L5", "name": "模板匹配层", "agent": "构图模板匹配器"},
    {"id": "L6", "name": "画面渲染层", "agent": "封面画面生成子 Agent"},
    {"id": "L7", "name": "排版校验闸", "agent": "排版风险检测"},
    {"id": "L8", "name": "产出层", "agent": "素材发放器"},
]
LAYER_BY_ID = {l["id"]: l for l in LAYERS}


class Tracer:
    def __init__(self):
        self.events: list[dict] = []
        self._t0 = None

    def start(self):
        self._t0 = time.time()

    def add(self, layer: str, action: str, detail: str, status: str = "ok",
            latency_ms: int = 0, meta: dict | None = None, agent: str | None = None):
        meta = meta or {}
        meta.setdefault("layerName", LAYER_BY_ID.get(layer, {}).get("name", layer))
        ev = {
            "seq": len(self.events) + 1,
            "layer": layer,
            "agent": agent or LAYER_BY_ID.get(layer, {}).get("agent", "Orchestrator"),
            "action": action,
            "detail": detail,
            "status": status,          # ok | warn | fix | block | skip
            "latency_ms": latency_ms,
            "meta": meta,
        }
        self.events.append(ev)
        return ev

    def total_ms(self) -> int:
        return int((time.time() - self._t0) * 1000) if self._t0 else 0


def aggregate(events: list[dict], total_ms: int) -> dict:
    by_layer: dict[str, dict] = {}
    for e in events:
        b = by_layer.setdefault(e["layer"], {"layer": e["layer"], "name": e["meta"].get("layerName", e["layer"]),
                                             "steps": 0, "ms": 0, "statuses": []})
        b["steps"] += 1
        b["ms"] += e["latency_ms"]
        b["statuses"].append(e["status"])
    stats = {"ok": 0, "warn": 0, "fix": 0, "block": 0, "skip": 0}
    for e in events:
        stats[e["status"]] = stats.get(e["status"], 0) + 1
    return {
        "total_ms": total_ms,
        "steps": len(events),
        "layers": [by_layer[l["id"]] for l in LAYERS if l["id"] in by_layer],
        "statusStats": stats,
        "gateHits": [e for e in events if e["layer"] in ("L4", "L7")],
        "retries": [e for e in events if e["status"] == "fix"],
    }


def replay_summary(result: dict) -> list[str]:
    """复盘：一句话讲清这次做了什么、卡在哪。"""
    out = []
    tr = result.get("trace", {})
    st = tr.get("statusStats", {})
    out.append(f"链路共 {tr.get('steps', 0)} 步，耗时 {tr.get('total_ms', 0)}ms，覆盖 {len(tr.get('layers', []))} 层。")
    if st.get("fix"):
        out.append(f"发生 {st['fix']} 次打回重做：生成侧命中合规红线，携带修改建议重生成后通过。")
    elif st.get("block"):
        out.append("存在未通过的硬闸命中项，需人工处理。")
    else:
        out.append("一次成型：文案生成即通过合规预检，无打回。")
    fc = result.get("formatCheck") or {}
    arrange = {"left-right": "左右分栏", "top-bottom": "上下分层"}.get(
        result.get("layoutArrange") or "", "")
    plat = (result.get("platform") or {}).get("name") or "直播间封面"
    sizes = "、".join(c.get("sizeKey", "") for c in (result.get("covers") or []))
    out.append(f"产出物「{plat}」{'（' + sizes + '）' if sizes else ''}·"
               f"排版 {result.get('layoutName', '无人物版式')}"
               f"{'（' + arrange + '）' if arrange else ''}，"
               f"{fc.get('detail', '-')}，判定 {fc.get('verdict', '-')}。")
    ctr = result.get("ctr") or {}
    out.append(f"点击率要素自查得分 {ctr.get('score', '-')}/100。")
    return out
