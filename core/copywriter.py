# -*- coding: utf-8 -*-
"""
封面文案生成子 Agent + 直播主题解析。

输入粒度：**直播主题**（其余全部自动推断）—— 对应用户诉求「用户只需要输入直播主题」。
产出：mainTitle / subtitle / badge / label / footerLeft / footerRight / style / templateId / imagePrompt
"""
from __future__ import annotations

import hashlib
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB_FILE = os.path.join(ROOT, "data", "knowledge.json")

with open(KB_FILE, "r", encoding="utf-8") as f:
    KB = json.load(f)

SCENES = KB["scenes"]
HOOKS = KB["hooks"]
FALLBACK = KB["fallbackScene"]
SCENE_BY_ID = {s["id"]: s for s in SCENES}

# 长句口语标志：出现这些说明主题是"一句话描述"，不是"标题"
_LONG_FORM = ["我想", "讲讲", "聊聊", "说说", "关于", "那些事", "是什么", "有什么用", "谈一谈"]
_TITLE_LIMIT = 14
_PUNCT = r"[，。！？、,.!?；;：:\-—…\s]+"

# 「激进版」诱饵词：仅用于复现文档里的 Badcase（生成 → 检测 → 打回重做 闭环演示）
RISKY_INJECT = {
    "rate_down": ([" 稳赚不赔", " 保本保收益", " 全网最低价"], ["仅限今天", "最后名额"]),
    "retire": ([" 保证领取", " 养老无忧"], ["即将停售", "最后一天"]),
    "family": ([" 什么病都能赔", " 确诊即赔"], ["仅限今天", "最后名额"]),
    "myth": ([" 全网最低价", " 第一品牌"], ["最后名额"]),
    "qa": ([" 保本稳赚"], ["仅限今天"]),
    "edu": ([" 翻倍不是梦"], ["最后一天"]),
}


def _h(seed: str) -> int:
    return int(hashlib.md5(seed.encode("utf-8")).hexdigest()[:8], 16)


def _pick(items: list, seed: str, avoid: list | None = None):
    avoid = avoid or []
    pool = [i for i in items if i not in avoid] or list(items)
    return pool[_h(seed) % len(pool)]


# ---------------------------------------------------------------- 主题解析
def parse_theme(theme: str) -> dict:
    """只吃一句直播主题，推断出场景 / 受众 / 核心词 / 推荐模板。"""
    theme = (theme or "").strip()
    low = theme.lower()
    scores = []
    for s in SCENES:
        hit = [k for k in s["keywords"] if k.lower() in low]
        # 长关键词权重更高（「年金」比「钱」更能定位场景）
        score = sum(1 + len(k) * 0.35 for k in hit)
        scores.append((score, hit, s))
    scores.sort(key=lambda x: -x[0])
    top_score, hits, scene = scores[0]

    if top_score <= 0:
        scene = SCENE_BY_ID[FALLBACK]
        hits = []

    # 核心词：优先选场景里与主题字面相关的 coreWord
    core = scene["coreWords"][0]
    for cw in scene["coreWords"]:
        if any(ch in theme for ch in cw[:2]):
            core = cw
            break

    return {
        "theme": theme,
        "sceneId": scene["id"],
        "sceneName": scene["name"],
        "audience": scene["audience"],
        "templateId": scene["templateId"],
        "label": scene["label"],
        "coreWord": core,
        "matchedKeywords": hits[:6],
        "confidence": round(min(1.0, top_score / 6.0), 2) if top_score > 0 else 0.3,
        "runnerUp": [{"sceneId": s[2]["id"], "name": s[2]["name"], "score": round(s[0], 1)} for s in scores[1:3]],
    }


def _compress(theme: str, limit: int = _TITLE_LIMIT) -> str:
    """把一句话主题压成标题长度：按标点断句，取信息量最高的短句组合。"""
    parts = [p.strip() for p in re.split(_PUNCT, theme) if p.strip()]
    if not parts:
        return theme[:limit]
    parts.sort(key=len, reverse=True)
    out = parts[0]
    for p in parts[1:]:
        if len(out) + len(p) + 1 <= limit:
            out = f"{out} {p}"
    if len(out) > limit:
        out = out[:limit]
    return out


# 2 字连接词：断在它们「之前」，保证绝不切断一个词（"增额寿|到底适合谁"）
_CONNECTORS = [
    "到底", "其实", "究竟", "就是", "不是", "只是", "而是", "是否", "如何", "怎么",
    "怎样", "哪些", "什么", "为什么", "真的", "如果", "因为", "所以", "别再", "有没有",
    "值不值", "能不能", "要不要", "该不该", "怎么选", "怎么看", "划不划算",
]
# 单字虚词：同样断在它们之前
_BOUND_CHARS = "怎如什为别先再而要和与把被对从当在不没无的了是在也都很就会能可才又还更最这那有"


def _fit_len(text: str, limit: int) -> str:
    """按长度裁剪时优先保留完整语义块，不要在词中间下刀。"""
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    parts = re.split(r"([ ·|｜/、，,：:])", t)
    out = ""
    for p in parts:
        if len(out) + len(p) <= limit:
            out += p
        else:
            break
    out = out.strip(" ·|｜/、，,：:")
    return out or t[:limit]


def _balance_title(t: str) -> str:
    """长标题在语义边界插一个空格，引导渲染器断在那 —— 否则折行会断在词中间（「怎么配 / 置才合理」）。"""
    t = t.strip()
    n = len(t)
    if n <= 7 or " " in t:
        return t
    # 1) 优先断在 2 字连接词之前，且尽量靠近中间
    cands = [i for w in {c for c in _CONNECTORS} for i in {t.find(w)}
             if 2 <= i <= n - 3]
    if cands:
        i = min(cands, key=lambda x: abs(x - n / 2))
        return t[:i] + " " + t[i:]
    # 2) 其次断在单字虚词之前
    cands = [i for i in range(2, min(n - 2, 10)) if t[i] in _BOUND_CHARS]
    if cands:
        i = min(cands, key=lambda x: abs(x - n / 2))
        return t[:i] + " " + t[i:]
    # 3) 找不到语义边界就原样返回，交给渲染器的均衡折行（硬切中点反而更糟）
    return t


def build_main_title(theme: str, pack: dict, seed: str) -> str:
    """主标题：主题本身够像标题就直接用（最大程度体现主题信息），否则用句式模板扩展。"""
    t = re.sub(_PUNCT, " ", theme).strip()
    t = re.sub(r"\s+", " ", t)
    if 6 <= len(t) <= 12 and not any(w in t for w in _LONG_FORM):
        return _balance_title(t)[:_TITLE_LIMIT]
    scene = SCENE_BY_ID[pack["sceneId"]]
    pat = _pick(scene["titlePatterns"], seed)
    title = pat.replace("{core}", pack["coreWord"])
    if len(title) > _TITLE_LIMIT:
        title = _compress(theme)
    return title[:_TITLE_LIMIT]


# ---------------------------------------------------------------- 文案生成
def gen_copy(theme: str, variant: str = "v2_safe", overrides: dict | None = None) -> dict:
    """
    variant: v2_safe（默认，合规版） / v1_risky（激进版，用于复现 Badcase 与 A/B 对照）
    overrides: {duration, host, institution, platform, sizes, audience, badge}
    """
    overrides = overrides or {}
    pack = parse_theme(theme)
    scene = SCENE_BY_ID[pack["sceneId"]]
    seed = theme + "|" + variant

    main_title = build_main_title(theme, pack, seed)
    # 副标题：优先挑长度就合规格的句式，避免生成后再硬截（会在词中间下刀）
    sub_pool = [p for p in scene["subtitlePatterns"] if len(p) <= 14] or scene["subtitlePatterns"]
    subtitle = _pick(sub_pool, seed + "sub", avoid=[main_title])
    subtitle = _fit_len(subtitle, 14)

    # 角标：优先用户指定的开播时间，其次场景默认，最后通用池
    badge = overrides.get("badge") or scene_badge(theme)

    copy = {
        "mainTitle": main_title,
        "subtitle": _fit_len(subtitle, 14),
        "badge": badge,
        "label": pack["label"],
        "footerLeft": overrides.get("institution") or HOOKS["footerLeftDefault"],
        "footerRight": _pick(HOOKS["footerRight"], seed + "fr"),
    }

    # 激进版：注入文档里的典型 Badcase 词，用于演示「生成 → 检测 → 打回重做」
    if variant == "v1_risky":
        suf, badges = RISKY_INJECT.get(pack["sceneId"], ([], []))
        copy["mainTitle"] = (copy["mainTitle"] + suf[_h(seed) % len(suf)]).strip()
        copy["badge"] = badges[_h(seed + "b") % len(badges)]
        if pack["sceneId"] in ("family", "qa"):
            copy["subtitle"] = "健康告知不用写高血压 公司查不到"

    copy["imagePrompt"] = build_image_prompt(copy, pack, scene)

    return {
        "pack": pack,
        "copy": copy,
        "variant": variant,
        "duration": int(overrides.get("duration") or 60),
        "sizes": overrides.get("sizes") or ["1:1", "3:4", "9:16"],
        "platform": overrides.get("platform") or "douyin",
        "host": overrides.get("host") or "",
        "institution": copy["footerLeft"],
        "audience": overrides.get("audience") or pack["audience"],
    }


def scene_badge(theme: str) -> str:
    return _pick(HOOKS["badgeOptions"], theme + "badge")


def build_image_prompt(copy: dict, pack: dict, scene: dict) -> str:
    """画面描述：交给文生图时使用。模板化渲染路径下作为补充说明留档。"""
    tones = {
        "tpl_trust_navy": "深蓝渐变背景，金色线条点缀，冷调自然光，专业沉稳的金融质感",
        "tpl_guard_warm": "暖米色背景，暖橙色块，柔和侧光，居家温馨感",
        "tpl_insight_teal": "深墨绿背景，细网格与数据柱状元素，清冷的分析感",
        "tpl_retire_gold": "深棕金背景，底部暖金光晕与时间刻度，岁月沉淀感",
        "tpl_qa_light": "白色到浅蓝渐变，明快干净，对话气泡元素",
    }
    tone = tones.get(pack["templateId"], tones["tpl_trust_navy"])
    return (
        f"{tone}。画面主体为题材相关意象（{pack['coreWord']}），不作夸张渲染；"
        f"构图留白供文字排版，画面上叠加中文主标题「{copy['mainTitle']}」、"
        f"副标题「{copy['subtitle']}」与角标「{copy['badge']}」。"
        f"不出现具体机构 LOGO、真实人物肖像与名人形象，避免版权与肖像权风险。"
    )


PROMPT_TEMPLATE = """你是「保险/金融直播封面文案策划师」。根据直播主题生成高点击率且合规的封面文案组合与画面描述。

【输入】
- 直播主题：{theme}
- 识别场景：{scene}
- 目标人群：{audience}

【文案规则】
- 主标题 ≤14字：结构 = 核心信息 + 利益点或悬念，必须能体现直播主题。
- 副标题 ≤14字：主题延展或人群指向，1 行可读。
- 角标 ≤12字：开播时间 / 预约提醒。
- 禁止收益承诺（保本 / 稳赚 / 零风险 / 保证领取）、绝对化用语（最 / 第一 / 唯一 / 顶级）、
  与存款理财做类比（存款 / 储蓄 / 银行理财）、诱导隐瞒告知（不用告知 / 公司查不到）。
- 分红、万能类必须体现利益不确定性。

【输出（严格 JSON，不要多余内容）】
{{"mainTitle":"","subtitle":"","badge":"","label":"","footerLeft":"","footerRight":"","imagePrompt":""}}
"""


def prompt_for(theme: str, pack: dict) -> str:
    return PROMPT_TEMPLATE.format(theme=theme, scene=pack["sceneName"], audience=pack["audience"])


if __name__ == "__main__":
    for t in ["利率下行，普通人怎么守住钱袋子", "养老规划", "家庭保障怎么配置才合理", "买保险别踩坑"]:
        r = gen_copy(t)
        print(f"{t}\n  -> 场景={r['pack']['sceneName']} 模板={r['pack']['templateId']} 置信={r['pack']['confidence']}")
        print(f"     主标题: {r['copy']['mainTitle']} | 副标题: {r['copy']['subtitle']} | 角标: {r['copy']['badge']}")
        print(f"     标签: {r['copy']['label']}\n")
        r1 = gen_copy(t, variant="v1_risky")
        print(f"     [v1激进版] {r1['copy']['mainTitle']} | {r1['copy']['subtitle']} | {r1['copy']['badge']}\n")
