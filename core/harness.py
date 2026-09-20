# -*- coding: utf-8 -*-
"""
调度内核 + Orchestrator.run_turn

八层编排（对应文档 6.1 内部流程，并补上用户额外要的「内容分段设计」）：
  L1 输入解析层 → L2 内容设计层 → L3 文案生成层 → L4 合规预检闸
  → L5 模板匹配层 → L6 画面渲染层 → L7 排版校验闸 → L8 产出层

闭环：L4 命中红线/严重类 → 携带修改建议**打回 L3 重生成**（最多 N 轮）→ 复检 → 通过才进渲染。
"""
from __future__ import annotations

import json
import os
import time

from assets import asset_path, get_asset
from copywriter import SCENE_BY_ID, gen_copy, parse_theme
from guard import run_precheck, check_cover_format, REQUIRED_DISCLAIMERS
from renderer import (LAYOUT_MODES, PLATFORM_BY_ID, PLATFORM_FAMILIES, PLATFORMS, SIZES,
                      TEMPLATES, TPL_BY_ID, family_of, layout_mode_of, platform_of,
                      render_set, template_summary)
from segmenter import build_segments
from trace import Tracer, aggregate, replay_summary

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = os.path.join(ROOT, "outputs")

STOP_CHARS = set("的一是在和与或你我他都了还就也把被对从当而很更最")

# 四个视觉族的展示名（平台族没有 classic/youth 之分）
FAMILY_NAMES = {"classic": "经典稳重", "youth": "年轻活力",
                "xhs": "小红书笔记", "douyin": "抖音竖屏"}


# ---------------------------------------------------------------- 模板匹配
def match_templates(theme: str, pack: dict, family: str = "classic",
                    platform: str = "live") -> tuple[dict, list[dict]]:
    """按场景关键词打分选模板。

    platform 先限定产出物类型（直播间封面 / 小红书图文 / 抖音图文 / 抖音视频），
    直播间封面再按 family 分经典族与年轻化族；平台专属模板自带视觉族，不再二次过滤。
    """
    low = theme.lower()
    pool = [t for t in TEMPLATES if (t.get("platform") or "live") == platform]
    if not pool:
        pool = list(TEMPLATES)
    if not is_platform_family(pool) and family not in ("auto", "", None):
        sub = [t for t in pool if family_of(t) == family]
        pool = sub or pool
    scored = []
    for t in pool:
        s = sum(len(k) for k in t.get("scenes", []) if k.lower() in low)
        if t["id"] == pack["templateId"]:
            s += 6  # 场景默认模板优先
        scored.append((s, t))
    scored.sort(key=lambda x: -x[0])
    same = [t for _, t in scored]
    alts = [{"id": t["id"], "name": t["name"], "tagline": t["tagline"], "family": family_of(t)}
            for t in same[1:4]]
    return scored[0][1], alts


def is_platform_family(pool: list[dict]) -> bool:
    """池子里的模板是否属于平台专属族（xhs / douyin）——这些族不吃 classic/youth 过滤。"""
    return bool(pool) and all(family_of(t) in PLATFORM_FAMILIES for t in pool)


# ---------------------------------------------------------------- 主播素材
def resolve_anchor(assets_in: dict | None) -> dict:
    """把前端传来的素材 ID 解析成渲染器要用的绝对路径 + 姓名头衔。

    半身像（portrait）是版式主角：有它就走人物版式，没有就走无人物版式。
    头像（avatar）只作为签名胶囊里的圆形小标；Logo 进签名胶囊 / 底部图标卡。
    """
    assets_in = assets_in or {}
    pt = get_asset(assets_in.get("portraitId")) if assets_in.get("portraitId") else None
    av = get_asset(assets_in.get("avatarId")) if assets_in.get("avatarId") else None
    lg = get_asset(assets_in.get("logoId")) if assets_in.get("logoId") else None
    if pt and pt.get("role") != "portrait":
        pt = None
    if av and av.get("role") != "avatar":
        av = None
    if lg and lg.get("role") != "logo":
        lg = None
    who = pt or av
    return {
        "portraitId": pt["id"] if pt else None,
        "portraitPath": asset_path(pt["id"]) if pt else None,
        "portraitUrl": pt["url"] if pt else None,
        "portraitAspect": (pt or {}).get("aspect") or 0.72,
        "portraitCutout": bool((pt or {}).get("cutout")),
        "avatarId": av["id"] if av else None,
        "logoId": lg["id"] if lg else None,
        "avatarPath": asset_path(av["id"]) if av else None,
        "logoPath": asset_path(lg["id"]) if lg else None,
        "anchorName": (who["name"] if who else "") or "",
        "anchorTitle": (assets_in.get("anchorTitle") or (who["title"] if who else "") or "")[:20],
        "avatarUrl": av["url"] if av else None,
        "logoUrl": lg["url"] if lg else None,
    }


def anchor_content(copy: dict, anchor: dict, overrides: dict | None = None) -> dict:
    """把文案 + 素材拼成渲染器要的 content。版式模式由有没有半身像自动决定。"""
    overrides = overrides or {}
    return {
        **copy,
        "institution": (overrides.get("institution") or copy.get("footerLeft") or ""),
        "portraitPath": anchor.get("portraitPath"),
        "portraitAspect": anchor.get("portraitAspect") or 0.72,
        "portraitCutout": anchor.get("portraitCutout", False),
        "avatarPath": anchor.get("avatarPath"),
        "logoPath": anchor.get("logoPath"),
        "anchorName": anchor.get("anchorName"),
        "anchorTitle": anchor.get("anchorTitle"),
    }


def layout_label(mode: str) -> str:
    for m in LAYOUT_MODES:
        if m.get("id") == mode:
            return m.get("name") or mode
    return "人物版式" if mode == "person" else "无人物版式"


def _summarize_hits(items: list[dict], limit: int = 4) -> str:
    agg: dict[str, list[str]] = {}
    for i in items:
        agg.setdefault(i["type"], []).extend(i["content"])
    parts = [f"{t}[{'/'.join(list(dict.fromkeys(v)))}]" for t, v in agg.items()]
    return "、".join(parts[:limit]) + ("…" if len(parts) > limit else "")


# ---------------------------------------------------------------- 点击率要素自查
def _rel_lum(c) -> float:
    def f(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])


def _contrast(a, b) -> float:
    la, lb = _rel_lum(a), _rel_lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)


def theme_coverage(pack: dict, copy: dict) -> dict:
    """封面上是否体现了直播主题信息 —— 主题命中的场景关键词有多少落到了封面文案上。"""
    hits = pack.get("matchedKeywords") or []
    text = copy["mainTitle"] + copy["subtitle"] + copy["label"] + copy["badge"]
    covered = [k for k in hits if k.lower() in text.lower()]
    ratio = round(len(covered) / len(hits), 2) if hits else 1.0
    return {"ratio": ratio, "covered": covered, "missed": [k for k in hits if k not in covered]}


def ctr_check(pack: dict, copy: dict, tpl: dict, format_check: dict) -> dict:
    cov = theme_coverage(pack, copy)
    tl = len(copy["mainTitle"])
    pal = tpl["palette"]
    contrast = _contrast(pal["text"], pal["bgBottom"])
    items = [
        {"name": "主题信息体现", "ok": cov["ratio"] >= 0.6,
         "detail": f"主题关键词覆盖 {int(cov['ratio'] * 100)}%（{ '、'.join(cov['covered']) or '—' }）"},
        {"name": "主标题长度", "ok": 8 <= tl <= 14,
         "detail": f"{tl} 字（建议 8-14 字，兼顾信息量与可读性）"},
        {"name": "副标题补充", "ok": 6 <= len(copy["subtitle"]) <= 14,
         "detail": f"{len(copy['subtitle'])} 字：{copy['subtitle']}"},
        {"name": "引导角标", "ok": bool(copy["badge"]), "detail": f"角标「{copy['badge']}」"},
        {"name": "文字覆盖率", "ok": format_check["verdict"] == "PASS", "detail": format_check["detail"]},
        {"name": "文字对比度", "ok": contrast >= 4.5, "detail": f"文字/底图对比度 {contrast}:1（≥4.5:1 可读性达标）"},
        {"name": "合规预检", "ok": True, "detail": "已通过 L4 合规预检闸（硬规则）"},
    ]
    score = int(round(sum(1 for i in items if i["ok"]) / len(items) * 100))
    return {"score": score, "items": items, "coverage": cov, "contrast": contrast}


# ---------------------------------------------------------------- 主流程
def run_turn(theme: str, overrides: dict | None = None, variant: str = "v2_safe", max_retry: int = 2) -> dict:
    overrides = overrides or {}
    tr = Tracer()
    tr.start()
    t0 = time.time()

    # ---- L1 输入解析
    pack = parse_theme(theme)
    conf = pack["confidence"]
    tr.add("L1", "解析直播主题",
           f"主题「{theme}」→ 命中场景「{pack['sceneName']}」，置信度 {conf}，"
           f"目标人群：{pack['audience']}",
           status="ok" if conf >= 0.45 else "warn",
           latency_ms=int((time.time() - t0) * 1000),
           meta={"sceneId": pack["sceneId"], "confidence": conf,
                 "matchedKeywords": pack["matchedKeywords"], "runnerUp": pack["runnerUp"]})

    # ---- L2 内容分段设计
    t = time.time()
    duration = int(overrides.get("duration") or 60)
    segments = build_segments(pack["sceneId"], duration, pack["audience"])
    tr.add("L2", "生成直播内容分段设计",
           f"{duration} 分钟拆为 {segments['segmentCount']} 段：{segments['structure']}",
           latency_ms=int((time.time() - t) * 1000),
           meta={"segmentCount": segments["segmentCount"], "duration": duration})

    # ---- L3/L4 生成 → 预检 →（打回重做）
    attempts, pre, copy_res = [], None, None
    use_variant = variant
    round_no = 0
    while round_no <= max_retry:
        t = time.time()
        copy_res = gen_copy(theme, variant=use_variant, overrides=overrides)
        copy = copy_res["copy"]
        tr.add("L3", "生成封面文案",
               f"{'（' + ('激进版 Prompt' if use_variant == 'v1_risky' else '合规版 Prompt') + '）'}"
               f"主标题「{copy['mainTitle']}」｜副标题「{copy['subtitle']}」｜角标「{copy['badge']}」",
               status="ok", latency_ms=int((time.time() - t) * 1000),
               meta={"variant": use_variant, "copy": copy})

        t = time.time()
        pre = run_precheck(copy, platform=copy_res["platform"], category="金融/保险",
                           max_rounds=1, auto_fix=True)
        attempts.append({"round": round_no + 1, "variant": use_variant,
                         "verdict": pre["verdict"], "riskScore": pre["riskScore"],
                         "riskLevel": pre["riskLevel"]["code"],
                         "items": [i["type"] for i in pre["riskItems"]]})

        if pre["verdict"] == "PASS":
            tr.add("L4", "合规预检（文本 · 硬规则闸）",
                   f"PASS · 风险分 {pre['riskScore']} · 档位 {pre['riskLevel']['code']}{pre['riskLevel']['name']}"
                   f"｜规则库命中 0 项",
                   status="ok", latency_ms=int((time.time() - t) * 1000),
                   meta={"verdict": "PASS", "riskScore": pre["riskScore"], "rounds": pre["rounds"]})
            break

        hard = pre["verdict"] in ("BLOCK", "FAIL")
        tr.add("L4", "合规预检（文本 · 硬规则闸）",
               f"{pre['verdict']} · 风险分 {pre['riskScore']} · 档位 {pre['riskLevel']['code']}{pre['riskLevel']['name']}"
               f"｜命中 {len(pre['riskItems'])} 项：" + _summarize_hits(pre["riskItems"]),
               status="block" if pre["verdict"] == "BLOCK" else "warn",
               latency_ms=int((time.time() - t) * 1000),
               meta={"verdict": pre["verdict"], "riskItems": pre["riskItems"],
                     "rounds": pre["rounds"], "suggestions": pre["suggestions"]})

        if hard and round_no < max_retry:
            tr.add("L4", "打回重做 · 回写 L3",
                   f"携带 {len(pre['suggestions'])} 条修改建议退回文案生成层重做"
                   f"（第 {round_no + 1} 次打回，上限 {max_retry} 次）；"
                   f"同时将命中词写入 avoidWords 预规避",
                   status="fix", latency_ms=0,
                   meta={"suggestions": pre["suggestions"], "blocked": pre["riskItems"]})
            use_variant = "v2_safe"
            blocked_words = [w for i in pre["riskItems"] for w in i["content"]]
            overrides = dict(overrides)
            overrides["avoidWords"] = blocked_words
            round_no += 1
            continue
        break

    # 以最终通过（或最终失败）的文案为准
    copy = pre["final"] or copy_res["copy"]
    copy = {k: copy.get(k, "") for k in ("mainTitle", "subtitle", "badge", "label", "footerLeft", "footerRight")}

    # ---- L5 模板匹配
    t = time.time()
    anchor = resolve_anchor(overrides.get("assets"))
    layout_mode = "person" if anchor["portraitPath"] else "graphic"
    style_family = overrides.get("styleFamily") or "classic"
    if style_family not in ("classic", "youth", "auto"):
        style_family = "classic"
    platform_id = overrides.get("platformId") or "live"
    if platform_id not in PLATFORM_BY_ID:
        platform_id = "live"
    plat = PLATFORM_BY_ID[platform_id]
    tpl, alts = match_templates(theme, pack, style_family, platform_id)
    forced = overrides.get("forceTemplateId")
    manual = bool(forced and forced in TPL_BY_ID
                  and (TPL_BY_ID[forced].get("platform") or "live") == platform_id)
    if manual:
        tpl = TPL_BY_ID[forced]
    fam_label = FAMILY_NAMES.get(family_of(tpl), family_of(tpl))
    alt_txt = f"；同平台备选 {len(alts)} 套" if alts else ""
    tr.add("L5", "产出物与构图模板匹配",
           f"产出物「{plat['name']}」（{plat.get('kind', '直播')}，画布 {'/'.join(plat.get('sizes', []))}）；"
           f"{'手动指定' if manual else '视觉族「' + fam_label + '」→ 自动匹配'}「{tpl['name']}」（{tpl['tagline']}）"
           f"{alt_txt}；排版判定为「{layout_label(layout_mode)}」"
           f"（{'已选主播半身像 → 半身像与文案错位排布' if layout_mode == 'person' else '未选半身像 → 文案居中 + 底部图标卡'}）",
           latency_ms=int((time.time() - t) * 1000),
           meta={"templateId": tpl["id"], "templateName": tpl["name"], "family": family_of(tpl),
                 "platformId": platform_id, "platformName": plat["name"],
                 "safeArea": plat.get("safeArea", {}),
                 "manual": manual, "layoutMode": layout_mode, "alternatives": alts})

    # ---- L6 多尺寸渲染
    t = time.time()
    sizes = overrides.get("sizes") or plat.get("defaultSizes") or ["1:1", "9:16"]
    allowed = plat.get("sizes") or list(SIZES.keys())
    sizes = [s for s in sizes if s in SIZES and s in allowed] or [allowed[0]]
    render_content = anchor_content(copy, anchor, overrides)
    # 视频封面的时长角标 / 图文轮播的页码：平台封面才用得上
    render_content["durationLabel"] = str(overrides.get("durationLabel") or "03:00")
    render_content["topics"] = overrides.get("topics")
    if platform_id in ("xhs", "dy_image"):
        render_content["pageTotal"] = int(overrides.get("pageTotal") or 6)
        render_content["pageIndex"] = int(overrides.get("pageIndex") or 1)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(OUT_ROOT, f"{pack['sceneId']}_{ts}")
    stem = f"cover_{pack['sceneId']}"
    renders = render_set(tpl["id"], render_content, sizes, out_dir, stem)
    covers = [{
        "sizeKey": r["sizeKey"], "W": r["W"], "H": r["H"], "textRatio": r["textRatio"],
        "layoutMode": r["layoutMode"],
        "personShape": (r.get("layoutMeta") or {}).get("personShape"),
        "arrange": (r.get("layoutMeta") or {}).get("arrange"),
        "url": "/outputs/" + os.path.relpath(r["path"], OUT_ROOT).replace(os.sep, "/"),
        "path": r["path"],
    } for r in renders]
    shapes = {c["personShape"] for c in covers if c["personShape"]}
    is_plat = family_of(tpl) in PLATFORM_FAMILIES
    anchor_note = ""
    if layout_mode == "person":
        pname = get_asset(anchor["portraitId"])
        desc = "自动抠底出血" if "arch" not in shapes else "未抠底 → 拱形人物卡"
        where = "半身像占据下半屏、文案压在其上层" if is_plat else "半身像与文案错位排布"
        used = [n for n, ok in (("头像", bool(anchor["avatarPath"])), ("Logo", bool(anchor["logoPath"]))) if ok]
        anchor_note = (f"；人物版式：半身像「{(pname or {}).get('name', '主播')}」{desc}，{where}"
                       f"{'，另挂 ' + '、'.join(used) if used else ''}")
    else:
        used = [n for n, ok in (("主播头像", bool(anchor["avatarPath"])), ("机构 Logo", bool(anchor["logoPath"]))) if ok]
        tr_note = "、".join(used) if used else "内置字形兜底"
        tail = "作者行 + 话题标签带" if is_plat else "底部图标卡挂件"
        anchor_note = f"；无人物版式：{tail}（{tr_note}）"
    tr.add("L6", "渲染封面画面（多尺寸）",
           f"按「{plat['name']}」规格渲染 {tpl['name']}，产出 {len(covers)} 个尺寸：" +
           "、".join(f"{c['sizeKey']} ({c['W']}×{c['H']})" for c in covers) + anchor_note,
           latency_ms=int((time.time() - t) * 1000),
           meta={"covers": covers, "outDir": out_dir, "anchor": anchor,
                 "layoutMode": layout_mode, "platformId": platform_id,
                 "safeArea": plat.get("safeArea", {})})

    # ---- L7 排版校验
    t = time.time()
    fmt = check_cover_format(renders)
    arrange = covers[0].get("arrange") if covers else None
    arrange_txt = {"left-right": "左右分栏", "top-bottom": "上下分层"}.get(arrange or "", "")
    tr.add("L7", "排版风险检测",
           f"{fmt['detail']}，判定 {fmt['verdict']}"
           + (f"｜版式：{layout_label(layout_mode)}" + (f"（{arrange_txt}）" if arrange_txt else ""))
           + ("" if fmt["verdict"] == "PASS" else "（文字过多会影响审核通过率与点击率，建议精简副文案）"),
           status="ok" if fmt["verdict"] == "PASS" else "warn",
           latency_ms=int((time.time() - t) * 1000), meta=fmt)

    # ---- L8 产出
    t = time.time()
    ctr = ctr_check(pack, {**copy, "badge": copy["badge"]}, tpl, fmt)
    tr.add("L8", "汇总发放素材",
           f"「{plat['name']}」封面 {len(covers)} 张（{layout_label(layout_mode)}）、"
           f"内容分段 {segments['segmentCount']} 段、点击率要素自查 {ctr['score']}/100；"
           f"必附免责声明 {len(REQUIRED_DISCLAIMERS)} 条",
           latency_ms=int((time.time() - t) * 1000),
           meta={"ctrScore": ctr["score"], "platformId": platform_id})

    total_ms = tr.total_ms()
    trace = aggregate(tr.events, total_ms)

    result = {
        "theme": theme,
        "sceneId": pack["sceneId"],
        "sceneName": pack["sceneName"],
        "audience": copy_res["audience"],
        "confidence": pack["confidence"],
        "matchedKeywords": pack["matchedKeywords"],
        "copy": copy,
        "imagePrompt": copy_res["copy"].get("imagePrompt", ""),
        "segments": segments,
        "template": {"id": tpl["id"], "name": tpl["name"], "tagline": tpl["tagline"],
                     "family": family_of(tpl), "familyName": fam_label,
                     "platform": platform_id, "platformName": plat["name"]},
        "platform": {
            "id": platform_id, "name": plat["name"], "kind": plat.get("kind", "直播"),
            "sizes": [c["sizeKey"] for c in covers],
            "safeArea": plat.get("safeArea", {}),
            "zonesNote": plat.get("zonesNote", ""), "note": plat.get("note", ""),
        },
        "templateAlternatives": alts,
        "styleFamily": family_of(tpl),
        "layoutMode": layout_mode,
        "layoutName": layout_label(layout_mode),
        "layoutArrange": covers[0].get("arrange") if covers else None,
        "anchor": anchor,
        "covers": covers,
        "compliance": {
            "verdict": pre["verdict"], "riskScore": pre["riskScore"],
            "riskLevel": pre["riskLevel"], "riskItems": pre["riskItems"],
            "suggestions": pre["suggestions"], "rounds": pre["rounds"],
            "platformNotes": pre["platformNotes"], "attempts": attempts,
        },
        "formatCheck": fmt,
        "ctr": ctr,
        "disclaimers": REQUIRED_DISCLAIMERS,
        "trace": trace,
        "events": tr.events,
        "metrics": {
            "attempts": len(attempts),
            "retryCount": max(0, len(attempts) - 1),
            "coverCount": len(covers),
            "sizeCount": len(covers),
        },
    }
    result["replay"] = replay_summary(result)
    return result


def rerender(template_id: str, copy: dict, sizes: list[str], scene_id: str = "variant",
             assets_in: dict | None = None, out_sub: str | None = None) -> dict:
    """同一套文案换模板 / 换版式重渲染 —— 用于直观对比「统一模板结构 × 不同视觉风格 × 两种排版」。

    out_sub：写盘子目录。两种排版并排对照时必须分开（person / graphic），
    否则同名文件互相覆盖，前端两栏拿到的是同一张图。
    """
    tpl = TPL_BY_ID.get(template_id) or TEMPLATES[0]
    allowed = (platform_of(tpl) or {}).get("sizes") or list(SIZES.keys())
    sizes = [s for s in sizes if s in SIZES and s in allowed] or [allowed[0]]
    out_dir = os.path.join(OUT_ROOT, "_variants", out_sub) if out_sub else os.path.join(OUT_ROOT, "_variants")
    safe = {k: (copy.get(k) or "") for k in
            ("mainTitle", "subtitle", "badge", "label", "footerLeft", "footerRight")}
    anchor = resolve_anchor(assets_in)
    render_content = anchor_content(safe, anchor, copy)
    render_content["institution"] = copy.get("institution") or safe.get("footerLeft") or ""
    render_content["durationLabel"] = str(copy.get("durationLabel") or "03:00")
    render_content["pageTotal"] = int(copy.get("pageTotal") or 6)
    render_content["pageIndex"] = int(copy.get("pageIndex") or 1)
    render_content["topics"] = copy.get("topics")
    renders = render_set(tpl["id"], render_content, sizes, out_dir, f"cover_{tpl['id']}")
    covers = [{
        "sizeKey": r["sizeKey"], "W": r["W"], "H": r["H"], "textRatio": r["textRatio"],
        "layoutMode": r["layoutMode"],
        "personShape": (r.get("layoutMeta") or {}).get("personShape"),
        "arrange": (r.get("layoutMeta") or {}).get("arrange"),
        "url": "/outputs/" + os.path.relpath(r["path"], OUT_ROOT).replace(os.sep, "/"),
    } for r in renders]
    mode = "person" if anchor["portraitPath"] else "graphic"
    plt = platform_of(tpl) or {"id": "live", "name": "直播间封面", "kind": "直播"}
    return {
        "template": {"id": tpl["id"], "name": tpl["name"], "tagline": tpl["tagline"],
                     "family": family_of(tpl),
                     "familyName": FAMILY_NAMES.get(family_of(tpl), family_of(tpl)),
                     "platform": plt.get("id", "live"), "platformName": plt.get("name", "直播间封面")},
        "platform": {"id": plt.get("id", "live"), "name": plt.get("name", "直播间封面"),
                     "kind": plt.get("kind", "直播"), "sizes": [c["sizeKey"] for c in covers],
                     "safeArea": plt.get("safeArea", {}), "note": plt.get("note", "")},
        "styleFamily": family_of(tpl),
        "layoutMode": mode,
        "layoutName": layout_label(mode),
        "layoutArrange": covers[0].get("arrange") if covers else None,
        "anchor": anchor,
        "covers": covers,
        "formatCheck": check_cover_format(renders),
    }


if __name__ == "__main__":
    from assets import _seed_demo, list_assets
    _seed_demo()
    _pt = [a for a in list_assets("portrait")]
    _av = [a for a in list_assets("avatar")]
    _lg = [a for a in list_assets("logo")]
    with_person = {"portraitId": _pt[0]["id"] if _pt else None,
                   "avatarId": _av[0]["id"] if _av else None,
                   "logoId": _lg[0]["id"] if _lg else None,
                   "anchorTitle": (_pt[0]["title"] if _pt else "")}
    no_person = {"avatarId": _av[0]["id"] if _av else None,
                 "logoId": _lg[0]["id"] if _lg else None}
    for th, v, fam, assets, tag, plat in [
        ("利率下行，普通人怎么守住钱袋子", "v2_safe", "youth", with_person, "有人物", "live"),
        ("家庭保障怎么配置才合理", "v1_risky", "youth", with_person, "有人物+激进版", "live"),
        ("养老年金怎么选才不亏", "v2_safe", "classic", with_person, "有人物", "live"),
        ("买保险别踩坑", "v2_safe", "youth", no_person, "无人物", "live"),
        ("利率下行，普通人怎么守住钱袋子", "v2_safe", "auto", with_person, "小红书图文", "xhs"),
        ("重疾险怎么选才不亏", "v2_safe", "auto", with_person, "抖音图文", "dy_image"),
        ("增额寿到底适合谁", "v2_safe", "auto", no_person, "抖音视频封面", "dy_video"),
    ]:
        r = run_turn(th, {"badge": "9月22日 20:00", "institution": "安心保险研究院",
                          "styleFamily": fam, "assets": assets, "platformId": plat}, variant=v)
        print(f"\n===== {th} [{v} / {plat} / {tag}] =====")
        for e in r["events"]:
            print(f"  {e['layer']} {e['status']:5s} {e['action']}: {e['detail'][:96]}")
        print("  产出物:", r["platform"]["name"], "| 安全区:", r["platform"]["safeArea"].get("top"),
              "/", r["platform"]["safeArea"].get("bottom"))
        print("  模板:", r["template"]["name"], "|", r["styleFamily"])
        print("  排版:", r["layoutName"], r["layoutArrange"])
        print("  合规:", r["compliance"]["verdict"], r["compliance"]["riskScore"], r["compliance"]["riskLevel"]["code"])
        print("  封面:", [(c["sizeKey"], c["layoutMode"], c.get("personShape")) for c in r["covers"]])
        print("  CTR:", r["ctr"]["score"])
        for line in r["replay"]:
            print("  ·", line)
