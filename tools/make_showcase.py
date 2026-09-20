# -*- coding: utf-8 -*-
"""
生成 outputs/showcase/ 下的示例总览图（可重复执行）。

用法：
    python tools/make_showcase.py

产出：
    00_模板总览_10套视觉风格.png        两个风格族 × 每族 5 套（人物版式，同文案对比）
    01_两种排版对照.png                 同一文案 × 「有人物素材 / 没有人物素材」× 三档画幅
    10_年轻化_5套色系总览.png            年轻化 5 套色系（紫蓝/青蓝/珊瑚/薄荷/琥珀）
    11_同一文案_经典vs年轻.png           同一文案在经典族与年轻化族下的效果差异
    12_示例_多主题竖版.png               5 个典型主题的 9:16 竖版产出

「两种排版」由素材决定：content 里有 portraitPath 走人物版式，没有就走无人物版式。
本脚本两种都覆盖（PG_ 前缀 = 人物版式，GF_ 前缀 = 无人物版式）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))

from PIL import Image, ImageDraw  # noqa: E402

from assets import _seed_demo, asset_path, list_assets  # noqa: E402
from copywriter import gen_copy  # noqa: E402
from renderer import (PLATFORM_BY_ID, ROOT, TEMPLATES, _font, family_of,  # noqa: E402
                      render_set)

SHOW = os.path.join(ROOT, "outputs", "showcase")
BG = (244, 249, 255)
INK = (15, 23, 42)
MUTED = (100, 116, 139)
BLUE = (43, 107, 255)
PURPLE = (106, 59, 255)

# 场景 → 年轻化模板（色系与题材的匹配建议）
YOUTH_MAP = {
    "rate_down": "tpl_youth_violet", "family": "tpl_youth_coral", "myth": "tpl_youth_cyan",
    "retire": "tpl_youth_amber", "qa": "tpl_youth_cyan", "edu": "tpl_youth_coral",
}


def _font_bold(sz):
    return _font("sans", "bold", sz)


def _font_reg(sz):
    return _font("sans", "regular", sz)


def _anchor(with_portrait: bool = True) -> dict:
    """示例素材。with_portrait=True → 渲染时走人物版式；False → 无人物版式。"""
    _seed_demo()
    pts = list_assets("portrait")
    avs = list_assets("avatar")
    lgs = list_assets("logo")
    pt = pts[0] if pts else None
    av = avs[0] if avs else None
    lg = lgs[0] if lgs else None
    a = {
        "anchorName": (pt or av)["name"] if (pt or av) else "",
        "anchorTitle": (pt or av)["title"] if (pt or av) else "",
        "avatarPath": asset_path(av["id"]) if av else None,
        "logoPath": asset_path(lg["id"]) if lg else None,
        "institution": "安心保险研究院",
    }
    if with_portrait and pt:
        a.update({
            "portraitPath": asset_path(pt["id"]),
            "portraitAspect": pt.get("aspect") or 0.72,
            "portraitCutout": bool(pt.get("cutout")),
        })
    return a


def _grid(rows, thumb, pad, label_h, row_label_h, out_name):
    """rows: [(行标题, [(模板, 图片路径), ...])]，等宽方图网格。

    路径必须来自 render_set 的返回值 —— 它的文件名是 `{stem}_{尺寸}.png`，不含模板 id，
    按「前缀 + 模板 id + 尺寸」硬拼路径会永远拼不中，网格就只剩行标题。
    """
    cols = max(len(t) for _, t in rows)
    W = cols * thumb + pad * (cols + 1)
    H = len(rows) * (thumb + label_h + row_label_h) + pad * (len(rows) + 1)
    sheet = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sheet)
    f_row, f_lbl = _font_bold(14), _font_bold(15)
    y = pad
    for row_label, items in rows:
        d.text((pad, y + 7), row_label, font=f_row, fill=BLUE)
        ty = y + row_label_h
        for i, (t, p) in enumerate(items):
            if not p or not os.path.exists(p):
                print(f"     [warn] 缺图：{t['id']} → {p}")
                continue
            x = pad + i * (thumb + pad)
            sheet.paste(Image.open(p).resize((thumb, thumb)), (x, ty))
            d.text((x, ty + thumb + 8), t["name"], font=f_lbl, fill=INK)
        y = ty + thumb + label_h + pad
    sheet.save(os.path.join(SHOW, out_name))
    print("  →", out_name)


def _sized_sheet(title, rows, h, out_name, pad=18, label_h=36, cap_h=26):
    """rows: [(行标题, [(图片路径, 图注), ...])]，各图画幅不同，按统一高度 h 等比缩放后横排。"""
    widths = []
    for _lbl, items in rows:
        tot = pad
        for p, _c in items:
            im = Image.open(p)
            tot += int(h * im.width / im.height) + pad
        widths.append(tot)
    top = 58
    W = max(widths) + pad
    HH = top + len(rows) * (label_h + h + cap_h + pad) + pad
    sheet = Image.new("RGB", (W, HH), BG)
    d = ImageDraw.Draw(sheet)
    d.text((pad, 18), title, font=_font_bold(21), fill=INK)
    y = top
    for rl, items in rows:
        d.text((pad, y + 8), rl, font=_font_bold(15), fill=PURPLE)
        x, yy = pad, y + label_h
        for p, cap in items:
            im = Image.open(p)
            w = int(h * im.width / im.height)
            sheet.paste(im.resize((w, h)), (x, yy))
            if cap:
                d.text((x, yy + h + 6), cap, font=_font_reg(12), fill=MUTED)
            x += w + pad
        y = yy + h + cap_h + pad
    sheet.save(os.path.join(SHOW, out_name))
    print("  →", out_name)


def main():
    os.makedirs(SHOW, exist_ok=True)
    av_p = _anchor(True)     # 有人物素材 → 人物版式
    av_g = _anchor(False)    # 没有人物素材 → 无人物版式
    classic = [t for t in TEMPLATES if family_of(t) == "classic"]
    youth = [t for t in TEMPLATES if family_of(t) == "youth"]

    base = {"label": "保险科普 · 资产配置", "mainTitle": "利率下行期 三个确定性",
            "subtitle": "先看趋势 再定资金去向", "badge": "9月22日 20:00",
            "footerLeft": "安心保险研究院", "footerRight": "点击预约 · 开播提醒",
            "institution": "安心保险研究院",
            # 平台封面的角标：图文用轮播页码、视频用时长
            "durationLabel": "03:00", "pageTotal": 6, "pageIndex": 1}
    bp = {**base, **av_p}
    bg = {**base, **av_g}

    # ---- 00 全量模板总览（两个风格族，同文案，人物版式）
    ov = {t["id"]: render_set(t["id"], bp, ["1:1"], SHOW, f"ov_{t['id']}")[0]["path"]
          for t in classic + youth}
    _grid([("经典稳重 classic", [(t, ov[t["id"]]) for t in classic]),
           ("年轻活力 youth", [(t, ov[t["id"]]) for t in youth])],
          300, 14, 40, 30, "00_模板总览_10套视觉风格.png")

    # ---- 01 两种排版对照（同一文案 × 两种排版 × 三档画幅）
    sizes = ["16:9", "1:1", "9:16"]
    cap = {"16:9": "16:9 横版", "1:1": "1:1 方版", "9:16": "9:16 竖版"}
    row_p, row_g = [], []
    for sk in sizes:
        rp = render_set("tpl_youth_violet", bp, [sk], SHOW, "PG_")
        rg = render_set("tpl_youth_violet", bg, [sk], SHOW, "GF_")
        arr_p = (rp[0].get("layoutMeta") or {}).get("arrange")
        arr_txt = {"left-right": "左右分栏", "top-bottom": "上下分层"}.get(arr_p or "", "")
        shape = (rp[0].get("layoutMeta") or {}).get("personShape")
        shape_txt = {"cutout": "抠底出血立绘", "arch": "拱形人物卡"}.get(shape or "", "")
        row_p.append((rp[0]["path"], f"{cap[sk]} · {arr_txt} · {shape_txt}".strip(" ·")))
        row_g.append((rg[0]["path"], f"{cap[sk]} · 文案居中通栏 + 底部图标卡"))
    _sized_sheet("两种排版对照 —— 同一套文案，只差一个「主播半身像」",
                 [("有人物素材 · 人物版式：半身像与文案错位排布，人物在下层、文案压最上层", row_p),
                  ("没有人物素材 · 无人物版式：文案居中通栏 + 底部两侧圆角图标卡", row_g)],
                 430, "01_两种排版对照.png")

    # ---- 02/03/04 平台模板总览：小红书图文 / 抖音图文 / 抖音视频封面
    # 每个平台两张行：有半身像（人物版式）／无半身像（无人物版式），列是平台下的各套模板
    plat_sets = [
        ("xhs", "xhs", "02_平台模板_小红书图文.png"),
        ("dy_image", "dy_image", "03_平台模板_抖音图文.png"),
        ("dy_video", "dy_video", "04_平台模板_抖音视频.png"),
    ]
    for pid, _fam, out in plat_sets:
        plat = PLATFORM_BY_ID.get(pid) or {}
        tpls = [t for t in TEMPLATES if (t.get("platform") or "live") == pid]
        sizes = [s for s in (plat.get("sizes") or ["1:1"]) if s in ("1:1", "3:4", "9:16")] or ["3:4"]
        sa = plat.get("safeArea") or {}
        rows = []
        for withp, rl in ((True, "有半身像 · 人物版式：半身像占据下半屏出血，文案压在其上层"),
                          (False, "无半身像 · 无人物版式：文案居中 + 作者行 + 话题标签带")):
            items = []
            for t in tpls:
                rd = render_set(t["id"], bp if withp else bg, sizes[:1], SHOW,
                                f"pl_{t['id']}_{1 if withp else 0}")
                items.append((rd[0]["path"], f"{t['name']}   {rd[0]['W']}×{rd[0]['H']}"))
            rows.append((rl, items))
        title = (f"{plat.get('name', pid)} —— {plat.get('note', '')[:38]}"
                 f"｜平台安全区：顶部 {round((sa.get('top') or 0) * 100)}%、底部 {round((sa.get('bottom') or 0) * 100)}%")
        _sized_sheet(title, rows, 440, out)

    # ---- 10 年轻化 5 套色系
    _grid([("年轻活力 youth · 5 套色系（同一文案，只换配色）", [(t, ov[t["id"]]) for t in youth])],
          430, 20, 46, 34, "10_年轻化_5套色系总览.png")

    # ---- 11 同一文案 · 经典 vs 年轻
    c2 = {**base, "mainTitle": "养老年金 怎么选才不亏", "subtitle": "四十岁开始 还来得及",
          "label": "养老规划 · 专项直播", **av_p}
    pair = [("tpl_retire_gold", "经典稳重 · 暖金岁月（深色厚底 / 左对齐）"),
            ("tpl_youth_amber", "年轻活力 · 琥珀朝阳（浅色渐变 / 渐变大字）")]
    TH, PAD, LBL = 470, 20, 52
    sheet = Image.new("RGB", (TH * 2 + PAD * 3, TH + LBL + PAD * 2), BG)
    d = ImageDraw.Draw(sheet)
    for i, (tid, nm) in enumerate(pair):
        r = render_set(tid, c2, ["1:1"], SHOW, "cmp_" + tid)
        x = PAD + i * (TH + PAD)
        sheet.paste(Image.open(r[0]["path"]).resize((TH, TH)), (x, LBL))
        d.text((x, 16), nm, font=_font_bold(16), fill=INK)
    sheet.save(os.path.join(SHOW, "11_同一文案_经典vs年轻.png"))
    print("  → 11_同一文案_经典vs年轻.png")

    # ---- 12 多主题竖版（人物版式）
    themes = ["利率下行，普通人怎么守住钱袋子", "家庭保障怎么配置才合理", "买保险别踩坑",
              "养老年金怎么选才不亏", "增额寿到底适合谁"]
    outs = []
    for i, th in enumerate(themes):
        r = gen_copy(th, overrides={"badge": "9月22日 20:00", "institution": "安心保险研究院"})
        c = {**r["copy"], **av_p}
        tid = YOUTH_MAP.get(r["pack"]["sceneId"], "tpl_youth_violet")
        # stem 用序号而不是 sceneId：多个主题可能落进同一个场景，否则会互相覆盖
        rr = render_set(tid, c, ["9:16"], SHOW, f"dm_{i:02d}")
        outs.append((c["mainTitle"], r["pack"]["sceneName"], rr[0]["path"]))
        print(f"  · {th} → {c['mainTitle']} | {c['subtitle']}  [{r['pack']['sceneName']}]")

    H, PAD, LBL = 600, 16, 70
    CW = int(H * 9 / 16)
    W = len(outs) * CW + PAD * (len(outs) + 1)
    sheet = Image.new("RGB", (W, H + LBL + PAD * 2), BG)
    d = ImageDraw.Draw(sheet)
    for i, (mt, sc, p) in enumerate(outs):
        im = Image.open(p)
        w = int(H * im.width / im.height)
        x = PAD + i * (CW + PAD)
        sheet.paste(im.resize((w, H)), (x, LBL))
        d.text((x, 16), mt, font=_font_bold(18), fill=INK)
        d.text((x, 44), sc, font=_font_reg(14), fill=MUTED)
    sheet.save(os.path.join(SHOW, "12_示例_多主题竖版.png"))
    print("  → 12_示例_多主题竖版.png")

    # ---- 清理中间产物，只留总览
    for f in sorted(os.listdir(SHOW)):
        if f.startswith(("ov_", "cmp_", "dm_", "tmp_", "PG_", "GF_", "pl_")):
            os.remove(os.path.join(SHOW, f))
    print("\nshowcase:", sorted(os.listdir(SHOW)))


if __name__ == "__main__":
    main()
