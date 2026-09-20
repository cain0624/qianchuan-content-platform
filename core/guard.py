# -*- coding: utf-8 -*-
"""
合规预检闸（生成侧前置闸）

设计铁律（踩过的坑，别改回去）：
  R1 检测必须基于【原始文本】，改写才作用于【累积文本】。
     否则上游改写把违规句摘掉后，下游规则再也看不到它 → 高风险被静默放行。
  R2 改写按 pattern 长度降序执行，避免短词先替换破坏长词（如「保本」吃掉「保本保息」）。
  R3 每轮改写后【重新检测】，收敛即止；超过最大轮次仍不通过则 BLOCK（整条不下发）。
"""
from __future__ import annotations

import json
import math
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_FILE = os.path.join(ROOT, "data", "compliance.json")

with open(RULES_FILE, "r", encoding="utf-8") as f:
    _C = json.load(f)

RULES = _C["rules"]
_META = _C["_meta"]
LAYER_WEIGHTS = _META["layerWeights"]
RISK_LEVELS = _META["riskLevels"]
CATEGORY_WEIGHTS = _META["categoryWeights"]
FORMAT_RULES = _C["formatRules"]
PLATFORM_NOTES = _C["platformNotes"]
REQUIRED_DISCLAIMERS = _C["requiredDisclaimers"]

VERDICT_ORDER = {"PASS": 0, "NEED_REVIEW": 1, "FAIL": 2, "BLOCK": 3}


def _risk_score(items: list[dict]) -> int:
    if not items:
        return 0
    raw = 0.0
    for it in items:
        raw += it["weight"] * (1 + 0.6 * (it["count"] - 1))
    score = min(100, round(raw * 14))
    if any(it["layer"] == "红线类" for it in items):
        score = max(score, 80)
    return score


def _risk_level(score: int, items: list[dict]) -> dict:
    if score >= 80 or any(i["layer"] == "红线类" for i in items):
        return RISK_LEVELS[0]
    if score >= 60:
        return RISK_LEVELS[1]
    if score >= 40:
        return RISK_LEVELS[2]
    return RISK_LEVELS[3]


def scan(text: str) -> list[dict]:
    """对单段文本扫描全部规则，返回命中明细（基于原始文本）。"""
    hits = []
    if not text:
        return hits
    for rule in RULES:
        matched = []
        for p in rule["patterns"]:
            if p in text:
                matched.append(p)
        if matched:
            # 长词优先去重：短词是已命中长词子串时丢弃（「稳赚」被「稳赚不赔」吃掉）
            #
            # 排序必须是**完全确定**的：先按长度降序，同长度时按规则里 patterns 的书写顺序。
            # 不能写成 sorted(set(matched), key=len) —— set 的迭代顺序取决于字符串 hash，
            # 而 CPython 默认开启 hash 随机化（PYTHONHASHSEED 随机），于是**同一个程序在
            # 不同进程里会给出不同的顺序**。表现就是「同一段文案，本地后端和线上引擎
            # 列出的命中词顺序不一样」，甚至重启一次后端顺序就变。
            order = {p: i for i, p in enumerate(rule["patterns"])}
            matched = sorted(set(matched), key=lambda x: (-len(x), order.get(x, 0)))
            kept = [m for m in matched if not any(m != k and m in k for k in matched)]
            kept.sort(key=lambda x: (-len(x), order.get(x, 0)))
            hits.append({
                "id": rule["id"],
                "type": rule["type"],
                "layer": rule["layer"],
                "weight": LAYER_WEIGHTS.get(rule["layer"], 1),
                "law": rule["law"],
                "content": kept[:3],
                "count": len(kept),
                "suggestion": rule["suggestion"],
                "rewrite": rule.get("rewrite", {}),
            })
    return hits


def auto_rewrite(text: str, hits: list[dict]) -> tuple[str, list[str]]:
    """按长词优先执行替换，返回 (改写后文本, 改写记录)。"""
    pairs: list[tuple[str, str]] = []
    for h in hits:
        for k, v in h["rewrite"].items():
            if k in text:
                pairs.append((k, v))
    pairs.sort(key=lambda x: -len(x[0]))
    out, log = text, []
    used = set()
    for k, v in pairs:
        if k in out and k not in used:
            out = out.replace(k, v)
            log.append(f"「{k}」→「{v}」")
            used.add(k)
    return out, log


def check_lengths(fields: dict) -> list[dict]:
    items = []
    for key, spec in FORMAT_RULES.items():
        if key == "imageTextCoverRatio":
            continue
        val = fields.get(key)
        if isinstance(val, str) and len(val) > spec["limit"]:
            items.append({"type": "文字排版风险", "layer": "提示类", "weight": LAYER_WEIGHTS["提示类"],
                          "field": key, "content": [f"{key} 长度 {len(val)} > 上限 {spec['limit']}"],
                          "count": 1, "law": "平台封面规格与审核要求", "suggestion": spec["note"], "rewrite": {}})
    return items


def run_precheck(fields: dict, platform: str = "douyin", category: str = "金融/保险",
                 max_rounds: int = 2, auto_fix: bool = True) -> dict:
    """
    fields: {mainTitle, subtitle, badge, label, footerLeft, footerRight}
    返回：{verdict, riskScore, riskLevel, riskItems, suggestions, rounds:[...], final:{...}, platformNotes}
    """
    original = {k: (v or "") for k, v in fields.items()}
    cur = dict(original)
    rounds, all_items, suggestions = [], [], []

    for r in range(max_rounds + 1):
        # R1：检测永远基于【当前累积文本】的原始副本，不做过滤
        detect_text = dict(cur)
        items = []
        for key, text in detect_text.items():
            if key in ("label", "footerLeft", "footerRight"):
                # 标签与署名允许存在品牌词，不做红线扫描，仅记录
                continue
            for h in scan(text):
                h = dict(h)
                h["field"] = key
                items.append(h)
        items += check_lengths(cur)

        if not items:
            verdict = "PASS"
        elif any(i["layer"] == "红线类" for i in items):
            verdict = "BLOCK"
        elif any(i["layer"] == "严重类" for i in items):
            verdict = "FAIL"
        else:
            verdict = "NEED_REVIEW"

        rounds.append({
            "round": r,
            "input": dict(cur),
            "verdict": verdict,
            "hits": [{"type": i["type"], "layer": i["layer"], "field": i.get("field", ""), "content": i["content"]} for i in items],
        })

        if verdict == "PASS":
            break

        # 红线类不做字面改写 —— 改写会破坏语义，必须整条打回重新生成
        if verdict == "BLOCK" or not auto_fix or r == max_rounds:
            all_items = items
            break

        # 仅【提示类】做字面改写（SANITIZE）。
        # 红线类 / 严重类一律打回重生成 —— 字面替换会破坏语义（实测会把「保本稳赚」改成 15 字长句并触发排版风险）。
        fixable = [i for i in items if i["layer"] == "提示类" and i.get("rewrite")]
        fixed, log = {}, []
        for key in ("mainTitle", "subtitle", "badge"):
            fh = [i for i in fixable if i.get("field") == key]
            if not fh:
                continue
            newtext, lg = auto_rewrite(cur[key], fh)
            limit = FORMAT_RULES.get(key, {}).get("limit")
            if lg and (limit is None or len(newtext) <= limit):
                fixed[key] = newtext
                log += lg
        if not fixed:
            all_items = items
            break
        cur.update(fixed)
        suggestions += log

    risk_score = _risk_score(all_items)
    if not all_items and rounds and rounds[-1]["verdict"] == "PASS":
        risk_score = 0

    # 汇总建议
    for i in all_items:
        if i["suggestion"] not in suggestions:
            suggestions.append(i["suggestion"])

    final_verdict = "PASS" if not all_items else ("BLOCK" if any(i["layer"] == "红线类" for i in all_items)
                                                  else "FAIL" if any(i["layer"] == "严重类" for i in all_items)
                                                  else "NEED_REVIEW")

    return {
        "verdict": final_verdict,
        "riskScore": risk_score,
        "riskLevel": _risk_level(risk_score, all_items),
        "riskItems": [
            {"type": i["type"], "layer": i["layer"], "field": i.get("field", ""),
             "content": i["content"], "rule": i.get("law", ""), "suggestion": i["suggestion"]}
            for i in all_items
        ],
        "suggestions": suggestions,
        "rounds": rounds,
        "original": original,
        "final": {} if final_verdict == "BLOCK" else dict(cur),
        "platformNotes": PLATFORM_NOTES.get(platform, {}),
        "category": category,
    }


def check_cover_format(renders: list[dict]) -> dict:
    """排版风险：文字覆盖占比。"""
    limit = FORMAT_RULES["imageTextCoverRatio"]["limit"]
    worst = max((r.get("textRatio", 0) for r in renders), default=0)
    return {
        "textRatioMax": round(worst, 4),
        "limit": limit,
        "verdict": "PASS" if worst <= limit else "NEED_REVIEW",
        "detail": f"最大文字覆盖占比 {round(worst * 100, 1)}%（上限 {int(limit * 100)}%）",
    }


if __name__ == "__main__":
    bad = {"mainTitle": "全网最低价 保本稳赚", "subtitle": "一次见效 什么病都赔", "badge": "仅限今天",
           "label": "保险科普 · 直播间", "footerLeft": "安心保险研究院", "footerRight": "点击预约 · 开播提醒"}
    good = {"mainTitle": "利率下行期 钱该放哪", "subtitle": "三个确定性 看清长期现金流", "badge": "9月22日 20:00",
            "label": "保险科普 · 直播间", "footerLeft": "安心保险研究院", "footerRight": "点击预约 · 开播提醒"}
    for name, c in (("BAD", bad), ("GOOD", good)):
        r = run_precheck(c)
        print(f"[{name}] verdict={r['verdict']} score={r['riskScore']} level={r['riskLevel']['name']} rounds={len(r['rounds'])}")
        print("  final:", r["final"])
        print("  items:", [i["type"] for i in r["riskItems"]])
