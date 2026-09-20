# -*- coding: utf-8 -*-
"""
封面渲染器 —— 统一模板结构 + 多套视觉风格，多尺寸输出。
纯 Pillow 渲染，无浏览器依赖，离线可跑。

统一骨架（所有模板共用）：
  Zone A 标签条 / Zone B 主标题 / Zone C 副标题 / Zone D 角标徽章 / Zone E 底部信息条
  Zone F 主播挂件区（年轻化模板启用：主播头像 + 姓名头衔 + 机构 Logo）
模板之间只在【视觉层】分化：配色、字体族、对齐方式、装饰语言、角标位置。

四个 family：
  classic —— 深色厚底 / 金融专业感（左对齐 + 底部信息条 + 分割线）
  youth   —— 浅色渐变底 / 渐变大字 / 星光点缀 / 圆角图标卡（居中 + 四角挂件）
  xhs     —— 小红书笔记：浅底超大黑字 + 关键词荧光高亮 + 作者行 + 话题标签带
  douyin  —— 抖音竖屏：高饱和渐变底 + 超大描边标题 + 话题条 + 页码 / 时长角标

前两个 family 服务于「直播间封面」，共用五分区骨架；后两个是「平台封面」，
按各自平台的 UI 遮挡规则（platform.safeArea）内缩排版，安全区以外的内容会被平台 UI 盖住。

三个维度正交：
  platform  决定画布规格 + 平台安全区 + 推荐尺寸（live / xhs / dy_image / dy_video）
  family    决定视觉语言（配色、字体、装饰、对齐）
  layoutMode 决定排版模式（有没有主播半身像 → person / graphic）
"""
from __future__ import annotations

import json
import math
import os
import re
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_FILE = os.path.join(ROOT, "data", "templates.json")

with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
    _TPL = json.load(f)

SPEC = _TPL["_spec"]
TEMPLATES: list[dict] = _TPL["templates"]
TPL_BY_ID = {t["id"]: t for t in TEMPLATES}
FAMILIES = SPEC.get("families", [])
FONT_FAMILIES = SPEC["typography"]["fontFamilies"]
SCALE = SPEC["typography"]["scaleByShortSide"]
SIZES = {k: tuple(v) for k, v in SPEC["sizes"].items()}
SAFE_MARGIN = SPEC["safeMargin"]
ZONE_SPEC = SPEC["zones"]
ANCHOR_SPEC = SPEC.get("anchorMaterials", {})

# 平台预设：决定画布规格 / 平台安全区 / 推荐尺寸
PLATFORMS: list[dict] = SPEC.get("platforms", [])
PLATFORM_BY_ID = {p["id"]: p for p in PLATFORMS}
# 平台专属风格族：这些族不走 classic / youth 的五分区骨架，用自己的平台排版器
PLATFORM_FAMILIES = {"xhs", "douyin"}

# 年轻化模板的字阶（相对短边）——比经典版更大更跳，视觉更"年轻"
YOUTH_SCALE = {
    "title": 0.118,
    "subtitle": 0.052,
    "badge": 0.034,
    "label": 0.030,
    "footpill": 0.026,
    "name": 0.036,
    "role": 0.024,
}
YOUTH_FONT = "sans"

# 平台封面的字阶（相对短边）——信息流里是缩略图，字号必须更狠
XHS_SCALE = {
    "title": 0.130, "sub": 0.049, "label": 0.031, "tail": 0.027,
    "name": 0.033, "role": 0.025, "topic": 0.029, "badge": 0.032, "page": 0.026,
}
DY_SCALE = {
    "title": 0.150, "sub": 0.055, "topic": 0.029,
    "name": 0.033, "role": 0.024, "badge": 0.033, "page": 0.029,
}
XHS_MARGIN = 0.058

SEP_RE = re.compile(r"(?<=[ ·|｜/、，,：:—\-])")
LABEL_SPLIT_RE = re.compile(r"\s*[·|｜/]\s*")


def family_of(tpl: dict) -> str:
    return tpl.get("family") or "classic"


def templates_of(family: str | None = None) -> list[dict]:
    if not family or family == "all":
        return list(TEMPLATES)
    return [t for t in TEMPLATES if family_of(t) == family]


# ---------------------------------------------------------------- 字体
# 字体随站点分发（`fonts/` 下的子集化 ttf），不再引用 /System/Library 下的系统字体。
# 原因：这个渲染器现在既能跑在本地 Python 进程里，也能跑在浏览器的 Pyodide 里 ——
# 后者没有 macOS 系统字体。相对路径一律按 ROOT 解析，于是"本地进程的 <repo>/fonts"
# 与"浏览器虚拟文件系统里的 /fonts"保持同构，业务代码不需要知道自己在哪。
FONT_DIR = os.path.join(ROOT, "fonts")


def font_path(family: str, weight: str = "regular") -> str:
    """取某个视觉族某字重的字体文件绝对路径。"""
    cfg = FONT_FAMILIES[family]
    files = cfg.get("files") or {}
    rel = files.get(weight) or files.get("regular") or cfg.get("path")
    if not rel:
        raise KeyError(f"字体族 {family} 未配置 {weight} 字重")
    return rel if os.path.isabs(rel) else os.path.join(ROOT, rel)


@lru_cache(maxsize=256)
def _font(family: str, weight: str, size: int) -> ImageFont.FreeTypeFont:
    path = font_path(family, weight)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"字体缺失：{path}\n"
            f"用 `python tools/subset_fonts.py` 重新生成 fonts/（见 README「字体」一节）"
        )
    return ImageFont.truetype(path, max(8, size))


# ---------------------------------------------------------------- 工具
def _rgba(c, a=255):
    if len(c) == 4:
        return tuple(c)
    return (c[0], c[1], c[2], a)


def _mix(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def _gradient(size, c_top, c_bottom) -> Image.Image:
    """垂直渐变。"""
    w, h = size
    base = Image.new("RGB", (1, h))
    px = base.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = _mix(c_top, c_bottom, t)
    return base.resize((w, h), Image.BILINEAR).convert("RGBA")


def _gradient_h(size, c_left, c_right) -> Image.Image:
    """水平渐变（渐变标题 / 渐变胶囊用）。"""
    w, h = size
    base = Image.new("RGB", (w, 1))
    px = base.load()
    for x in range(w):
        t = x / max(1, w - 1)
        px[x, 0] = _mix(c_left, c_right, t)
    return base.resize((w, h), Image.BILINEAR).convert("RGBA")


def _wrap_balance(text: str, font, max_w: int, max_lines: int) -> list[str]:
    """均衡逐字折行：把字符尽量平均分配到各行，且每行不超宽。"""
    n = len(text)
    lines, i = [], 0
    while i < n and len(lines) < max_lines:
        rows_left = max_lines - len(lines)
        target = max(1, math.ceil((n - i) / rows_left))
        cur = ""
        for j in range(i, n):
            if font.getlength(cur + text[j]) > max_w and cur:
                break
            cur += text[j]
            if len(cur) >= target:
                break
        if not cur:
            cur = text[i]
        lines.append(cur)
        i += len(cur)
    if i < n and lines:
        last = lines[-1]
        while last and font.getlength(last + "…") > max_w:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


def _wrap_cjk(text: str, font, max_w: int, max_lines: int = 2) -> list[str]:
    """中文标题折行：优先在空格 / 间隔符处断行，保证语义停顿；否则均衡逐字折行。"""
    text = (text or "").strip()
    if not text:
        return []
    if font.getlength(text) <= max_w:
        return [text]

    parts = [p for p in SEP_RE.split(text) if p]
    lines, cur = [], ""
    for p in parts:
        if not cur:
            cur = p
        elif font.getlength(cur + p) <= max_w and len(lines) < max_lines - 1:
            cur += p
        else:
            lines.append(cur.rstrip())
            cur = p
    if cur:
        lines.append(cur.rstrip())

    if len(lines) > max_lines or any(font.getlength(l) > max_w for l in lines):
        lines = _wrap_balance(text, font, max_w, max_lines)
    return lines[:max_lines]


def _fit_font(family, weight, size, lines_src, max_w, max_lines=2):
    """自适应字号：若按当前字号折行后放不下，则逐档降字号。"""
    s = size
    for _ in range(5):
        f = _font(family, weight, s)
        lines = _wrap_cjk(lines_src, f, max_w, max_lines)
        if lines and all(f.getlength(l) <= max_w for l in lines):
            return f, lines
        s = int(s * 0.92)
    f = _font(family, weight, s)
    return f, _wrap_cjk(lines_src, f, max_w, max_lines)


def _ellipsize(text: str, font, max_w: int) -> str:
    t = (text or "").strip()
    while t and font.getlength(t) > max_w:
        t = t[:-1]
    return t


def _square_crop(im: Image.Image) -> Image.Image:
    w, h = im.size
    s = min(w, h)
    return im.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))


# ---------------------------------------------------------------- 装饰层（经典）
def _deco_gold_rule(layer, W, H, M, pal, u):
    d = ImageDraw.Draw(layer, "RGBA")
    r = int(W * 0.30)
    d.ellipse([W - r, -int(r * 0.55), W + r, int(r * 0.75)], fill=_rgba(pal["accent"], 20))
    d.ellipse([W - int(r * 0.55), -int(r * 0.30), W + int(r * 0.45), int(r * 0.55)], fill=_rgba(pal["accent"], 22))
    d.ellipse([-int(W * 0.22), H - int(H * 0.20), int(W * 0.30), H + int(H * 0.16)], fill=_rgba(pal["accent"], 14))
    x = int(W * 0.865)
    d.line([x, int(H * 0.10), x, int(H * 0.90)], fill=_rgba(pal["accent"], 40), width=max(1, int(u * 0.004)))


def _deco_warm_blocks(layer, W, H, M, pal, u):
    d = ImageDraw.Draw(layer, "RGBA")
    r = int(W * 0.05)
    d.rounded_rectangle([int(W * 0.70), -int(H * 0.10), int(W * 1.18), int(H * 0.26)], r,
                        fill=_rgba(pal["accent"], 26))
    d.rounded_rectangle([int(W * 0.80), int(H * 0.76), int(W * 1.15), int(H * 1.12)], r,
                        fill=_rgba(pal["accent"], 20))
    d.rounded_rectangle([M - int(u * 0.055), int(H * 0.28), M - int(u * 0.055) + max(3, int(u * 0.014)), int(H * 0.74)],
                        int(u * 0.007), fill=_rgba(pal["accent"], 140))


def _deco_data_grid(layer, W, H, M, pal, u):
    d = ImageDraw.Draw(layer, "RGBA")
    step = int(min(W, H) * 0.055)
    for x in range(0, W + step, step):
        d.line([x, 0, x, H], fill=_rgba(pal["accent"], 12), width=1)
    for y in range(0, H + step, step):
        d.line([0, y, W, y], fill=_rgba(pal["accent"], 12), width=1)
    bx, by, bw, gap = int(W * 0.72), int(H * 0.20), int(W * 0.035), int(W * 0.022)
    for i, hh in enumerate([0.06, 0.10, 0.15, 0.21]):
        x0 = bx + i * (bw + gap)
        y0 = by + int(H * 0.21) - int(H * hh)
        d.rounded_rectangle([x0, y0, x0 + bw, by + int(H * 0.21)], int(bw * 0.28),
                            fill=_rgba(pal["accent"], 60 + i * 45))


def _deco_time_arc(layer, W, H, M, pal, u):
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow, "RGBA")
    r = int(min(W, H) * 0.46)
    gd.ellipse([W // 2 - r, H - int(r * 1.25), W // 2 + r, H + int(r * 0.55)],
               fill=_rgba(pal["accent"], 62))
    glow = glow.filter(ImageFilter.GaussianBlur(max(2, int(min(W, H) * 0.075))))
    layer.alpha_composite(glow)
    d = ImageDraw.Draw(layer, "RGBA")
    ty = int(H * 0.90)
    for i in range(9):
        x0 = M + i * int((W - 2 * M) / 9)
        d.line([x0, ty, x0, ty + (int(u * 0.028) if i % 2 == 0 else int(u * 0.016))],
               fill=_rgba(pal["accent"], 90), width=max(1, int(u * 0.003)))


def _deco_bubble(layer, W, H, M, pal, u):
    d = ImageDraw.Draw(layer, "RGBA")
    for rx, ry, rw, rh in [(0.66, 0.14, 0.30, 0.10), (0.74, 0.27, 0.22, 0.075), (0.70, 0.80, 0.26, 0.085)]:
        x0, y0 = int(W * rx), int(H * ry)
        d.rounded_rectangle([x0, y0, x0 + int(W * rw), y0 + int(H * rh)],
                            int(H * rh * 0.42), outline=_rgba(pal["accent"], 60), width=max(2, int(u * 0.004)))
    for rx, ry in [(0.62, 0.10), (0.955, 0.35), (0.58, 0.93)]:
        r = int(min(W, H) * 0.017)
        d.ellipse([int(W * rx) - r, int(H * ry) - r, int(W * rx) + r, int(H * ry) + r],
                  fill=_rgba(pal["accent"], 55))


# ---------------------------------------------------------------- 装饰层（年轻化）
def _star4(d, cx, cy, R, fill, ratio=0.22):
    """四角星（参考图的点缀星），R 为外径。"""
    r = R * ratio
    pts = []
    for i in range(4):
        a = math.radians(i * 90 - 90)
        pts.append((cx + R * math.cos(a), cy + R * math.sin(a)))
        a2 = math.radians(i * 90 - 45)
        pts.append((cx + r * math.cos(a2), cy + r * math.sin(a2)))
    d.polygon(pts, fill=fill)


def _deco_youth_sparkle(layer, W, H, M, pal, u):
    """浅色渐变底 + 双侧柔光 + 点阵微纹理 + 四角星点缀。"""
    acc, acc2 = pal["accent"], pal.get("accent2", pal["accent"])
    spark = [tuple(c) for c in pal.get("spark", [acc, acc2])]

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow, "RGBA")
    r1 = int(W * 0.46)
    gd.ellipse([W - int(r1 * 0.72), -int(r1 * 0.42), W + int(r1 * 0.30), int(r1 * 0.62)], fill=_rgba(acc, 46))
    r2 = int(W * 0.40)
    gd.ellipse([-int(r2 * 0.45), H - int(r2 * 0.62), int(r2 * 0.62), H + int(r2 * 0.30)], fill=_rgba(acc2, 40))
    gd.ellipse([-int(W * 0.08), int(H * 0.30), int(W * 0.24), int(H * 0.72)], fill=_rgba(spark[-1], 22))
    glow = glow.filter(ImageFilter.GaussianBlur(max(3, int(u * 0.085))))
    layer.alpha_composite(glow)

    d = ImageDraw.Draw(layer, "RGBA")
    # 点阵微纹理（只在四角留白处，不干扰文字）
    step = int(u * 0.062)
    for x in range(int(M * 0.4), W, step):
        for y in range(int(M * 0.4), H, step):
            if int(H * 0.24) < y < int(H * 0.76):
                continue
            d.ellipse([x, y, x + max(1, int(u * 0.005)), y + max(1, int(u * 0.005))],
                      fill=_rgba(acc, 16))

    # 四角星与圆点
    stars = [
        (0.135, 0.300, 0.019, spark[0], 0.24),
        (0.862, 0.222, 0.015, spark[1], 0.24),
        (0.905, 0.640, 0.011, spark[-1], 0.22),
        (0.098, 0.795, 0.014, spark[0], 0.24),
        (0.760, 0.905, 0.010, spark[1], 0.22),
    ]
    for rx, ry, rr, col, ratio in stars:
        R = max(3, int(u * rr))
        _star4(d, W * rx, H * ry, R, _rgba(col, 235), ratio)
    dots = [(0.205, 0.845, 0.007, spark[-1]), (0.795, 0.145, 0.006, spark[0]),
            (0.930, 0.430, 0.005, spark[1]), (0.065, 0.470, 0.006, spark[1])]
    for rx, ry, rr, col in dots:
        R = max(2, int(u * rr))
        d.ellipse([W * rx - R, H * ry - R, W * rx + R, H * ry + R], fill=_rgba(col, 210))


# ---------------------------------------------------------------- 装饰层（平台封面）
def _blob_layer(W, H, blobs, blur):
    """把若干 (cx, cy, rx, ry, color, alpha) 柔光斑合成一层后模糊返回。"""
    g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(g, "RGBA")
    for cx, cy, rx, ry, col, a in blobs:
        gd.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=_rgba(col, a))
    return g.filter(ImageFilter.GaussianBlur(max(3, int(blur))))


def _deco_xhs_paper(layer, W, H, M, pal, u):
    """奶白纸感：对角柔光 + 极淡方格 + 顶部 accent 细条。"""
    acc = pal["accent"]
    hl = pal.get("highlight", acc)
    layer.alpha_composite(_blob_layer(W, H, [
        (W * 1.02, H * 0.06, W * 0.62, W * 0.50, hl, 74),
        (-W * 0.06, H * 0.94, W * 0.52, W * 0.40, acc, 30),
    ], u * 0.095))

    d = ImageDraw.Draw(layer, "RGBA")
    step = max(8, int(u * 0.058))
    faint = _rgba(pal.get("divider", acc), 16)
    for x in range(step, W, step):
        d.line([x, 0, x, H], fill=faint, width=1)
    for y in range(step, H, step):
        d.line([0, y, W, y], fill=faint, width=1)

    bar = max(3, int(u * 0.011))
    d.rectangle([0, 0, W, bar], fill=_rgba(acc))
    d.rectangle([W - int(W * 0.30), bar, W, bar + max(2, int(u * 0.004))],
                fill=_rgba(pal.get("accent2", acc)))


def _deco_dy_aurora(layer, W, H, M, pal, u):
    """极光：两个大色斑 + 一道斜向光带 + 星点。"""
    acc, acc2 = pal["accent"], pal.get("accent2", pal["accent"])
    spark = [tuple(c) for c in pal.get("spark", [acc, acc2])]
    layer.alpha_composite(_blob_layer(W, H, [
        (W * 0.88, H * 0.14, W * 0.66, H * 0.24, acc, 108),
        (W * 0.10, H * 0.62, W * 0.60, H * 0.20, acc2, 96),
        (W * 0.46, H * 0.94, W * 0.80, H * 0.16, spark[-1], 44),
    ], u * 0.11))

    d = ImageDraw.Draw(layer, "RGBA")
    # 斜向光带
    band = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band, "RGBA")
    bw = int(W * 0.16)
    bd.polygon([(W * 0.16, H), (W * 0.16 + bw, H), (W * 0.62 + bw, 0), (W * 0.62, 0)],
               fill=_rgba(acc, 26))
    bd.polygon([(W * 0.34, H), (W * 0.34 + bw // 2, H), (W * 0.80 + bw // 2, 0), (W * 0.80, 0)],
               fill=_rgba(spark[-1], 20))
    layer.alpha_composite(band.filter(ImageFilter.GaussianBlur(max(6, int(u * 0.05)))))

    for rx, ry, rr, col in [(0.145, 0.215, 0.014, spark[0]), (0.885, 0.365, 0.011, spark[1]),
                            (0.215, 0.760, 0.009, spark[0]), (0.820, 0.845, 0.012, spark[1]),
                            (0.500, 0.095, 0.008, spark[-1])]:
        R = max(3, int(u * rr))
        _star4(d, W * rx, H * ry, R, _rgba(col, 236), 0.24)


def _deco_dy_rays(layer, W, H, M, pal, u):
    """放射光束：从画布下方中心向外发散 + 同心圆环，营造"炸"的势能。"""
    acc, acc2 = pal["accent"], pal.get("accent2", pal["accent"])
    cx, cy = W * 0.5, H * 0.90
    rays = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rays, "RGBA")
    R = int(max(W, H) * 1.25)
    for i in range(16):
        a0 = math.radians(i * 22.5 - 90)
        a1 = a0 + math.radians(9)
        rd.polygon([(cx, cy),
                    (cx + R * math.cos(a0), cy + R * math.sin(a0)),
                    (cx + R * math.cos(a1), cy + R * math.sin(a1))],
                   fill=_rgba(pal.get("highlight", acc2), 34 if i % 2 == 0 else 20))
    layer.alpha_composite(rays.filter(ImageFilter.GaussianBlur(max(5, int(u * 0.045)))))

    d = ImageDraw.Draw(layer, "RGBA")
    for k, a in ((0.86, 60), (1.16, 44), (1.52, 30)):
        rr = int(W * k)
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=_rgba(acc2, a),
                  width=max(2, int(u * 0.006)))

    layer.alpha_composite(_blob_layer(W, H, [
        (W * 0.82, H * 0.12, W * 0.46, W * 0.30, acc2, 70),
        (W * 0.12, H * 0.30, W * 0.40, W * 0.26, acc, 44),
    ], u * 0.10))


DECOS = {
    "gold-rule": _deco_gold_rule,
    "warm-blocks": _deco_warm_blocks,
    "data-grid": _deco_data_grid,
    "time-arc": _deco_time_arc,
    "bubble": _deco_bubble,
    "youth-sparkle": _deco_youth_sparkle,
    "xhs-paper": _deco_xhs_paper,
    "dy-aurora": _deco_dy_aurora,
    "dy-rays": _deco_dy_rays,
}

# 中文字形在包围盒内的平均填充率，用于估算文字覆盖率
_INK_FILL = 0.62


# ---------------------------------------------------------------- 年轻化绘制件
def _soft_card(img: Image.Image, x, y, side, radius_ratio, shadow_alpha=68, shadow_dy=0.065):
    """带柔和投影的白色圆角卡（参考图里两侧浮起的图标卡）。"""
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh, "RGBA")
    sd.rounded_rectangle([x, y + side * shadow_dy, x + side, y + side + side * shadow_dy],
                         int(side * radius_ratio), fill=(15, 23, 42, shadow_alpha))
    sh = sh.filter(ImageFilter.GaussianBlur(max(2, side * 0.085)))
    img.alpha_composite(sh)
    ImageDraw.Draw(img, "RGBA").rounded_rectangle(
        [x, y, x + side, y + side], int(side * radius_ratio), fill=(255, 255, 255, 255))


def _paste_avatar(img: Image.Image, cx, cy, r, path, pal):
    """圆形嵌入主播头像；无素材时用渐变底 + 人像字形兜底。"""
    r = int(r)
    cx, cy = int(cx), int(cy)
    size = r * 2
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
    if path and os.path.exists(path):
        try:
            av = Image.open(path)
            av = av.convert("RGB")
            av = _square_crop(av).resize((size, size), Image.LANCZOS)
            img.paste(av, (cx - r, cy - r), mask)
        except Exception:
            path = None
    if not path or not os.path.exists(path or ""):
        av = _gradient_h((size, size), pal["accent2"], pal["accent"]).convert("RGB")
        img.paste(av, (cx - r, cy - r), mask)
        d = ImageDraw.Draw(img, "RGBA")
        w = int(size * 0.30)
        d.ellipse([cx - w / 2, cy - r * 0.62, cx + w / 2, cy - r * 0.62 + w], fill=(255, 255, 255, 238))
        d.ellipse([cx - r * 0.60, cy + r * 0.10, cx + r * 0.60, cy + r * 1.70], fill=(255, 255, 255, 238))
    # 细描边，让头像在纯白卡上仍有边界
    ImageDraw.Draw(img, "RGBA").ellipse([cx - r, cy - r, cx + r, cy + r],
                                        outline=_rgba(pal["accent"], 46), width=max(1, int(r * 0.035)))


def _paste_logo(img: Image.Image, box, path, pal):
    """等比容纳机构 Logo；无素材时用三柱增长图形兜底（参考图右侧图标卡语言）。"""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if path and os.path.exists(path):
        try:
            lg = Image.open(path).convert("RGBA")
            s = min(bw / lg.width, bh / lg.height)
            lg = lg.resize((max(1, int(lg.width * s)), max(1, int(lg.height * s))), Image.LANCZOS)
            img.alpha_composite(lg, (int(x0 + (bw - lg.width) / 2), int(y0 + (bh - lg.height) / 2)))
            return
        except Exception:
            pass
    d = ImageDraw.Draw(img, "RGBA")
    cols = [pal["accent2"], pal["accent"], tuple(pal.get("spark", [pal["accent2"]])[-1])]
    n = 3
    gapx = bw * 0.13
    bwidth = (bw - gapx * (n - 1)) / n
    for i, hf in enumerate([0.52, 0.72, 0.94]):
        bx = x0 + i * (bwidth + gapx)
        by = y0 + bh * (1 - hf)
        d.rounded_rectangle([bx, by, bx + bwidth, y0 + bh], bwidth * 0.36, fill=_rgba(cols[i % 3], 236))


def _brush_underline(img: Image.Image, cx, y, width, color, thick):
    """手绘感下划线：两端收锋 + 轻微起伏。"""
    d = ImageDraw.Draw(img, "RGBA")
    x0, x1 = cx - width / 2, cx + width / 2
    n = max(16, int(width / 9))
    for i in range(n):
        t = i / (n - 1)
        xa = x0 + width * t
        xb = x0 + width * min(1.0, t + 1 / (n - 1))
        taper = math.sin(math.pi * t) ** 0.35
        yy = y + math.sin(t * math.pi * 1.35) * thick * 0.55
        d.line([xa, yy, xb, yy], fill=_rgba(color, int(215 * min(1.0, taper + 0.25))),
               width=max(1, int(thick * (0.35 + 0.65 * taper))))


def _grad_text(img: Image.Image, xy, text, font, c1, c2, anchor="la"):
    """渐变填充文字：文字做遮罩，透过水平渐变。"""
    if not text:
        return 0
    w = max(2, int(font.getlength(text)) + 8)
    h = max(2, int(font.size * 1.55) + 8)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((4, 4), text, font=font, fill=255, anchor=anchor)
    grad = _gradient_h((w, h), c1, c2)
    img.paste(grad, (int(xy[0]) - 4, int(xy[1]) - 4), mask)
    return w - 8


def _grad_pill(img: Image.Image, box, radius, c1, c2, text, font, text_color, shadow=52):
    x0, y0, x1, y1 = [int(v) for v in box]
    w, h = x1 - x0, y1 - y0
    if w < 4 or h < 4:
        return
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh, "RGBA").rounded_rectangle([x0, y0 + h * 0.16, x1, y1 + h * 0.16],
                                                 int(radius), fill=(15, 23, 42, shadow))
    sh = sh.filter(ImageFilter.GaussianBlur(max(2, h * 0.22)))
    img.alpha_composite(sh)
    grad = _gradient_h((w, h), c1, c2)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], int(radius), fill=255)
    img.paste(grad, (x0, y0), mask)
    if text:
        ImageDraw.Draw(img, "RGBA").text((x0 + w // 2, y0 + h // 2), text, font=font,
                                         fill=_rgba(text_color), anchor="mm")


# ================================================================ 人物版式公用件
# 两种版式（layoutMode）：
#   person  —— 已选主播半身像：人物与文案错位排布，横版左右分栏、方/竖版上下分层
#   graphic —— 未选半身像：文案居中通栏 + 底部两侧圆角图标卡（参考图语言）
PERSON_GEO = SPEC.get("personGeometry", {})
LAYOUT_MODES = SPEC.get("layoutModes", [])


def layout_mode_of(content: dict) -> str:
    """有半身像走人物版式，没有就走无人物版式。这是两种排版的唯一开关。"""
    p = content.get("portraitPath")
    return "person" if (p and os.path.exists(p)) else "graphic"


def _aspect_key(W: int, H: int) -> str:
    if W > H * 1.35:
        return "wide"
    if H / W > 1.5:
        return "tall"
    return "square"


def _person_box(W, H, aspect, text_bottom, gap, key, cutout):
    """算出半身像的目标框 (top, bottom, w, h)。

    横版是左右分栏，人物和文案各占一边、互不抢纵向空间，所以人物直接顶到 topRatio；
    方版/竖版是上下分层，人物顶边必须让开文案块，取「文案底边 + 间距」与 topRatio 的较大者。

    抠底立绘走「宽度优先」：先按 maxWidthRatio 取目标宽度，再从画布底部裁掉超出部分
    （bleed 就是允许出血到画布下方多少比例）。半身像腰际本来就该落到画布外，宁可多裁一点，
    也不让人物被高度约束挤成一张小图 —— 这是封面人物有没有存在感的关键。

    未抠底的照片不能出血，必须完整落在画布内，改为拱形人物卡。
    """
    cfg = PERSON_GEO.get(key) or PERSON_GEO.get("square") or {}
    short = min(W, H)
    top_floor = int(H * cfg.get("topRatio", 0.44))
    max_w = int(W * cfg.get("maxWidthRatio", 0.6))
    top = top_floor if key == "wide" else max(top_floor, int(text_bottom + gap))

    if not cutout:
        bottom = H - int(short * 0.032)
        h = max(int(H * 0.18), bottom - top)
        w = int(h * aspect)
        if w > max_w:
            w, h = max_w, int(max_w / aspect)
            top = bottom - h
        return top, bottom, w, h

    bleed = cfg.get("bleed", 0.10)
    # 两个上限取小：宽度不超 max_w，底部出血不超 bleed
    h_by_width = int(max_w / aspect)
    h_by_bleed = H + int(H * bleed) - top
    h = max(int(H * 0.22), min(h_by_width, h_by_bleed))
    w = int(h * aspect)
    if w > max_w:
        w, h = max_w, int(max_w / aspect)
    return top, top + h, w, h


def _draw_glow(img, cx, cy, rx, ry, color, alpha, blur):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer, "RGBA").ellipse([cx - rx, cy - ry, cx + rx, cy + ry],
                                          fill=_rgba(color, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(max(3, int(blur))))
    img.alpha_composite(layer)


def _arch_mask(w, h, radius_ratio=0.46):
    """拱形遮罩：上圆下方（用于未抠底的照片，做成一张设计过的「人物卡」）。"""
    w, h = max(2, int(w)), max(2, int(h))
    r = int(w * radius_ratio)
    m = Image.new("L", (w, h + r), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h + r - 1], r, fill=255)
    return m.crop((0, 0, w, h))


def _cover_fit(im: Image.Image, w: int, h: int, top_bias: float = 0.0) -> Image.Image:
    """铺满目标框并保头部：按长边缩放后水平居中，纵向按 top_bias 决定留哪一头。"""
    w, h = max(1, int(w)), max(1, int(h))
    s = max(w / im.width, h / im.height)
    nw, nh = max(1, int(im.width * s)), max(1, int(im.height * s))
    im = im.resize((nw, nh), Image.LANCZOS)
    x0 = (nw - w) // 2
    y0 = int((nh - h) * top_bias)
    return im.crop((x0, y0, x0 + w, y0 + h))


def _paste_person(img, box, content, pal, W, H, key, cutout, short):
    """把半身像贴到封面：抠底立绘直接出血 + 底部柔化融入背景；未抠底则做成拱形人物卡。"""
    top, bottom, w, h = box
    path = content.get("portraitPath")
    if not path or not os.path.exists(path):
        return None
    try:
        src = Image.open(path).convert("RGBA")
    except Exception:
        return None

    if key == "wide":
        x = W - w if cutout else W - int(short * 0.052) - w
    else:
        x = (W - w) // 2 + int(W * 0.015)
    y = bottom - h
    acc, acc2 = pal["accent"], pal.get("accent2", pal["accent"])

    if cutout:
        # 人物身后一层柔光，让立绘与浅色底不再"贴纸感"。
        # 光心要落在画布内 —— 人物出血后 y+h 已经低于画布底，直接用 y+h*0.46 会把光晕推到看不见的地方。
        gy = min(y + h * 0.42, H - int(H * 0.06))
        _draw_glow(img, x + w * 0.5, gy, w * 0.74, h * 0.42, acc2, 40, short * 0.10)
        _draw_glow(img, x + w * 0.5, y + h * 0.20, w * 0.44, h * 0.22, acc, 34, short * 0.075)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        layer.alpha_composite(_cover_fit(src, w, h, 0.0), (0, 0))
        a = layer.getchannel("A")
        # 底部柔化：渐隐带按【画布坐标】计算，而不是按图层坐标。
        # 图层底部本来就在画布外（出血），按图层算会把整条渐变推到看不见的区域内，底部就变成硬切。
        fade_h = max(2, int(H * 0.17))
        bottom_alpha = 0.08          # 画布底边残留不透明度，让人物"溶进"背景而不是被切开
        ramp = Image.new("L", (1, h), 255)
        rp = ramp.load()
        for yy in range(h):
            cy = y + yy               # 该行在画布上的纵坐标
            if cy <= H - fade_h:
                continue
            t = min(1.0, max(0.0, (cy - (H - fade_h)) / float(fade_h)))
            rp[0, yy] = int(255 * ((1.0 - t) ** 1.35 * (1.0 - bottom_alpha) + bottom_alpha))
        a = ImageChops.multiply(a, ramp.resize((w, h)))
        a = a.filter(ImageFilter.GaussianBlur(max(1, int(short * 0.0022))))
        layer.putalpha(a)
        img.alpha_composite(layer, (max(0, x), max(0, y)))
        return (x, y, w, h, "cutout")

    # 未抠底：拱形人物卡 + 投影 + 内描边
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh, "RGBA").rounded_rectangle(
        [x, y + int(h * 0.022), x + w, y + h + int(h * 0.022)],
        int(w * 0.46), fill=(15, 23, 42, 62))
    sh = sh.filter(ImageFilter.GaussianBlur(max(2, int(short * 0.014))))
    img.alpha_composite(sh)
    card = _cover_fit(src, w, h, 0.06)
    # 未抠底的素材直接上拱形卡：带透明通道的先压到浅底上，避免透明处发黑
    flat = Image.new("RGB", card.size, tuple(pal["bgTop"]))
    flat.paste(card, (0, 0), card)
    img.paste(flat, (x + (w - card.width) // 2, y + (h - card.height) // 2),
              _arch_mask(card.width, card.height))
    ring_w = max(2, int(short * 0.0042))
    ring = Image.new("RGBA", (w, h + int(w * 0.46)), (0, 0, 0, 0))
    ImageDraw.Draw(ring, "RGBA").rounded_rectangle(
        [0, 0, w - 1, h + int(w * 0.46) - 1], int(w * 0.46),
        outline=(255, 255, 255, 232), width=ring_w)
    img.alpha_composite(ring.crop((0, 0, w, h)), (x, y))
    return (x, y, w, h, "arch")


def _signature_row(img, draw, x, y, max_w, content, pal, short, f_name, f_role,
                   align="left", **f_extra):
    """签名胶囊：机构 Logo + 圆形头像 + 姓名·头衔 ｜ 机构名。

    空间不够时按「机构名 → 头衔 → 头像」顺序依次让位，保证胶囊不溢出、不破版。
    """
    logo_p = content.get("logoPath")
    avat_p = content.get("avatarPath")
    name = (content.get("anchorName") or "").strip()
    role = (content.get("anchorTitle") or "").strip()
    inst = (content.get("institution") or content.get("footerLeft") or "").strip()
    if not name and not inst:
        return None

    h = f_name.size + int(short * 0.030)
    pad_x, pad_y = int(short * 0.026), int(short * 0.015)
    inner_h = h - pad_y * 2
    gap = int(short * 0.014)
    sep_gap = int(short * 0.016)

    marks = []
    if logo_p and os.path.exists(logo_p):
        marks.append(("logo", inner_h))
    if avat_p and os.path.exists(avat_p):
        marks.append(("avatar", int(inner_h * 0.96)))

    # 逐层降级计算宽度
    def measure(with_role, with_inst, with_marks):
        w = pad_x * 2
        if with_marks:
            w += sum(m[1] for m in marks) + gap * len(marks)
        if name:
            w += int(f_name.getlength(name))
        if with_role and role:
            w += sep_gap + int(f_role.getlength("·" + role))
        if with_inst and inst:
            w += sep_gap + int(f_role.getlength("｜" + inst))
        return w

    use_role, use_inst, use_marks = True, True, True
    w = measure(use_role, use_inst, use_marks)
    if w > max_w and inst:
        use_inst = False
        w = measure(use_role, use_inst, use_marks)
    if w > max_w and role:
        use_role = False
        w = measure(use_role, use_inst, use_marks)
    if w > max_w and marks:
        use_marks = False
        w = measure(use_role, use_inst, use_marks)
    if w > max_w:
        name = _ellipsize(name, f_name, max(6, max_w - pad_x * 2 - int(f_role.getlength("｜" + inst)) - sep_gap))
        w = measure(use_role, use_inst, use_marks)
    if w > max_w:
        return None
    if align == "center":
        x = x + (max_w - w) / 2

    # 胶囊底：白色半透 + 细描边 + 柔和投影，压在半身像上也读得清
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh, "RGBA").rounded_rectangle(
        [x, y + h * 0.10, x + w, y + h * 1.10], h // 2, fill=(15, 23, 42, 52))
    sh = sh.filter(ImageFilter.GaussianBlur(max(2, h * 0.24)))
    img.alpha_composite(sh)
    pill = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pill, "RGBA")
    pd.rounded_rectangle([0, 0, w - 1, h - 1], h // 2, fill=(255, 255, 255, 238),
                         outline=_rgba(pal["accent"], 52), width=max(1, int(short * 0.0022)))
    img.alpha_composite(pill, (int(x), int(y)))

    cx = x + pad_x
    ty = y + h // 2
    if use_marks:
        for kind, side in marks:
            mh = int(side)
            if kind == "logo":
                _paste_logo(img, (cx, ty - mh / 2, cx + mh, ty + mh / 2), logo_p, pal)
            else:
                _paste_avatar(img, cx + mh / 2, ty, mh / 2, avat_p, pal)
            cx += mh + gap
    d = ImageDraw.Draw(img, "RGBA")
    if name:
        d.text((cx, ty), name, font=f_name, fill=_rgba(pal["text"]), anchor="lm")
        cx += int(f_name.getlength(name))
    if use_role and role:
        d.text((cx + sep_gap, ty), "·" + role, font=f_role,
               fill=_rgba(pal["subtext"]), anchor="lm")
        cx += sep_gap + int(f_role.getlength("·" + role))
    if use_inst and inst:
        d.text((cx + sep_gap, ty), "｜" + inst, font=f_role,
               fill=_rgba(pal["subtext"]), anchor="lm")
    return {"box": [int(x), int(y), int(x + w), int(y + h)],
            "name": name, "role": role if use_role else "", "inst": inst if use_inst else ""}



# ---------------------------------------------------------------- 渲染：经典族
def _signature_line_classic(img, content, x, y, max_w, pal, short, f_name, f_role,
                            dry=False, align="left"):
    """经典族的签名行：accent 短规 + 圆形头像 + 姓名·头衔 ｜ 机构名（无白卡，贴合深底语言）。"""
    avat_p = content.get("avatarPath")
    name = (content.get("anchorName") or "").strip()
    role = (content.get("anchorTitle") or "").strip()
    inst = (content.get("institution") or content.get("footerLeft") or "").strip()
    if not name and not inst:
        return 0
    rule_h = max(2, int(short * 0.006))
    pad = int(short * 0.022)
    avat_d = int(short * 0.062) if (avat_p and os.path.exists(avat_p)) else 0
    h = rule_h + int(short * 0.020) + max(f_name.size, avat_d)

    def measure(with_role, with_inst):
        w = 0
        if avat_d:
            w += avat_d + pad
        if name:
            w += int(f_name.getlength(name))
        if with_role and role:
            w += int(f_role.getlength("  ·  " + role)) + pad
        if with_inst and inst:
            w += int(f_role.getlength("｜" + inst)) + pad
        return w

    use_role, use_inst = True, True
    if measure(use_role, use_inst) > max_w and inst:
        use_inst = False
    if measure(use_role, use_inst) > max_w and role:
        use_role = False
    if measure(use_role, use_inst) > max_w:
        name = _ellipsize(name, f_name, max(6, int(max_w * 0.5)))
    if dry:
        return h, measure(use_role, use_inst)
    if align == "center":
        x = x + max(0, (max_w - measure(use_role, use_inst)) // 2)

    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle([x, y, x + int(short * 0.075), y + rule_h], rule_h // 2,
                        fill=_rgba(pal["accent"]))
    ty = y + rule_h + int(short * 0.020)
    cy = ty + max(f_name.size, avat_d) / 2
    cx = x
    if avat_d:
        _paste_avatar(img, cx + avat_d / 2, cy, avat_d / 2, avat_p, pal)
        cx += avat_d + pad
    d = ImageDraw.Draw(img, "RGBA")
    if name:
        d.text((cx, cy), name, font=f_name, fill=_rgba(pal["text"]), anchor="lm")
        cx += int(f_name.getlength(name))
    if use_role and role:
        d.text((cx + pad, cy), "·  " + role, font=f_role, fill=_rgba(pal["subtext"]), anchor="lm")
        cx += int(f_role.getlength("  ·  " + role)) + pad
    if use_inst and inst:
        d.text((cx + pad, cy), "｜" + inst, font=f_role, fill=_rgba(pal["subtext"]), anchor="lm")
    return h


def _render_classic(tpl, content, W, H, max_text_w, draw_ctx=None):
    short = min(W, H)
    M = int(short * SAFE_MARGIN)
    pal = {k: (tuple(v) if isinstance(v, list) else v) for k, v in tpl["palette"].items()}
    fam = tpl.get("fontFamily", "sans")
    centered = tpl.get("align", "left") == "center"
    upright = H / W > 1.5

    person_mode = layout_mode_of(content) == "person"
    key = _aspect_key(W, H)
    cfg = PERSON_GEO.get(key) or {}
    cutout = bool(content.get("portraitCutout", True))
    aspect = min(1.15, max(0.36, float(content.get("portraitAspect") or 0.72)))
    if person_mode:
        max_text_w = int(W * cfg.get("textWidthRatio", 0.53)) - M if key == "wide" \
            else int(W * cfg.get("textWidthRatio", 0.86))

    f_label = _font(fam, "bold", int(short * SCALE["label"]))
    f_sub = _font(fam, "regular", int(short * SCALE["subtitle"]))
    f_badge = _font(fam, "bold", int(short * SCALE["badge"]))
    f_footer = _font(fam, "regular", int(short * SCALE["footer"]))
    f_name = _font(fam, "bold", int(short * 0.036))
    f_role = _font(fam, "regular", int(short * 0.024))

    f_title, title_lines = _fit_font(fam, "bold", int(short * SCALE["title"]),
                                     content.get("mainTitle", ""), max_text_w, 2)

    # ---- 先量位置（Zone B/C + 签名行），人物要按这个位置来放
    lab = (content.get("label") or "").strip()
    label_h = (f_label.size + int(short * 0.024)) if lab else 0
    subline = _ellipsize(content.get("subtitle", ""), f_sub, max_text_w)
    lh_title = f_title.size * 1.24
    gap = int(short * 0.030) if subline else 0
    th = (len(title_lines) - 1) * lh_title + f_title.size if title_lines else 0
    group_h = th + gap + (f_sub.size if subline else 0)

    sig_gap = int(short * 0.042)
    sig_h, sig_w = _signature_line_classic(None, content, 0, 0, max_text_w, pal, short,
                                           f_name, f_role, dry=True) if person_mode else (0, 0)

    if person_mode:
        if key == "wide":
            total = group_h + sig_gap + sig_h
            group_top = max(M + label_h + int(short * 0.05), int((H - total) / 2))
        else:
            group_top = M + label_h + int(short * 0.055)
    else:
        group_top = int(H * (0.455 if centered else 0.475) - group_h / 2)
        if upright:
            group_top = int(H * 0.44 - group_h / 2)
    group_bottom = group_top + group_h

    img = _gradient((W, H), pal["bgTop"], pal["bgBottom"])
    deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    DECOS.get(tpl.get("deco", "gold-rule"), _deco_gold_rule)(deco, W, H, M, pal, short)
    img = Image.alpha_composite(img, deco)
    zones: dict[str, list[int]] = {}
    meta: dict = {}

    # ---- Zone F：人物区（在文字下层先落位）
    if person_mode:
        sig_y = group_bottom + sig_gap
        gap = int(short * 0.02)
        pbox = _person_box(W, H, aspect, sig_y + max(sig_h, f_name.size), gap, key, cutout)
        if key != "wide":  # 补平人物被宽度上限压低后留下的空档
            slack = pbox[0] - (sig_y + max(sig_h, f_name.size)) - gap
            if slack > int(short * 0.06):
                shift = min(slack // 2, int(short * 0.14))
                group_top += shift
                group_bottom += shift
                sig_y += shift
                pbox = _person_box(W, H, aspect, sig_y + max(sig_h, f_name.size), gap, key, cutout)
        person = _paste_person(img, pbox, content, pal, W, H, key, cutout, short)
        if person is None:
            person_mode = False
        else:
            px, py, pw, ph, shape = person
            zones["F"] = [int(px), int(py), int(px + pw), int(py + ph)]
            meta = {"personShape": shape, "personBox": [int(px), int(py), int(pw), int(ph)],
                    "arrange": "left-right" if key == "wide" else "top-bottom"}

    draw = ImageDraw.Draw(img, "RGBA")

    if tpl.get("deco") == "gold-rule":
        yy = group_top - int(short * 0.048)
        x0 = (W - int(short * 0.135)) // 2 if centered else M
        draw.rounded_rectangle([x0, yy, x0 + int(short * 0.135), yy + max(2, int(short * 0.008))],
                               int(short * 0.004), fill=_rgba(pal["accent"]))

    y = group_top
    for ln in title_lines:
        w = f_title.getlength(ln)
        x = (W - w) / 2 if centered else M
        draw.text((x, y), ln, font=f_title, fill=_rgba(pal["text"]), anchor="la")
        b = zones.get("B", [int(x), y, int(x + w), y + f_title.size])
        zones["B"] = [min(b[0], int(x)), y, max(b[2], int(x + w)), y + f_title.size]
        y += lh_title
    title_bottom = y - lh_title + f_title.size if title_lines else group_top

    if subline:
        sy = int(title_bottom + gap)
        w = f_sub.getlength(subline)
        x = (W - w) / 2 if centered else M
        draw.text((x, sy), subline, font=f_sub, fill=_rgba(pal["subtext"]), anchor="la")
        zones["C"] = [int(x), sy, int(x + w), sy + f_sub.size]

    # ---- Zone A：标签条
    if lab:
        pad_x, pad_y = int(short * 0.024), int(short * 0.012)
        lw = int(f_label.getlength(lab)) + pad_x * 2
        lh = f_label.size + pad_y * 2
        lx = (W - lw) // 2 if centered else M
        draw.rounded_rectangle([lx, M, lx + lw, M + lh], lh // 2, fill=_rgba(pal["accent"], 40),
                               outline=_rgba(pal["accent"], 150), width=max(1, int(short * 0.0025)))
        draw.text((lx + lw // 2, M + lh // 2), lab, font=f_label, fill=_rgba(pal["label"]), anchor="mm")
        zones["A"] = [lx, M, lx + lw, M + lh]

    # ---- Zone D：角标徽章
    badge = (content.get("badge") or "").strip()
    if badge:
        pad_x, pad_y = int(short * 0.026), int(short * 0.014)
        bw = int(f_badge.getlength(badge)) + pad_x * 2
        bh = f_badge.size + pad_y * 2
        pos = tpl.get("badgePos", "top-right")
        if person_mode:
            # 人物版式：角标固定到右上角，避开签名行与人物区（否则必然叠字）
            bx, by = W - M - bw, M
        elif centered:
            bx, by = (W - bw) // 2, group_bottom + int(short * 0.058)
        elif pos == "top-right":
            bx, by = W - M - bw, M
        else:
            bx, by = M, group_bottom + int(short * 0.058)
        draw.rounded_rectangle([bx, by, bx + bw, by + bh], int(bh * 0.30), fill=_rgba(pal["badgeBg"]))
        draw.text((bx + bw // 2, by + bh // 2), badge, font=f_badge, fill=_rgba(pal["badgeText"]), anchor="mm")
        zones["D"] = [bx, by, bx + bw, by + bh]

    # ---- Zone E / G：签名行 或 底部信息条
    if person_mode:
        sig_y = group_bottom + sig_gap
        sig_x = M
        sh = _signature_line_classic(img, content, sig_x, sig_y, max_text_w, pal, short,
                                     f_name, f_role,
                                     align="center" if (centered and key != "wide") else "left")
        if sh:
            zones["G"] = [sig_x, int(sig_y), int(sig_x + max_text_w), int(sig_y + sh)]
    else:
        fl = (content.get("footerLeft") or "").strip()
        fr = (content.get("footerRight") or "").strip()
        fy = H - M - f_footer.size
        draw.line([M, fy - int(short * 0.026), W - M, fy - int(short * 0.026)],
                  fill=_rgba(pal["accent"], 70), width=max(1, int(short * 0.002)))
        if fl:
            draw.text((M, fy), fl, font=f_footer, fill=_rgba(pal["footerText"]), anchor="la")
        if fr:
            draw.text((W - M, fy), fr, font=f_footer, fill=_rgba(pal["footerText"]), anchor="ra")
        zones["E"] = [M, fy - int(short * 0.026), W - M, fy + f_footer.size]
    return img, zones, ("person" if person_mode else "graphic"), meta


# ---------------------------------------------------------------- 渲染：年轻化族
def _render_youth(tpl, content, W, H, max_text_w):
    """年轻化族入口：按有没有半身像，分派到两种排版。"""
    if layout_mode_of(content) == "person":
        return _youth_person(tpl, content, W, H)
    img, zones = _youth_graphic(tpl, content, W, H, max_text_w)
    return img, zones, "graphic", {}


def _youth_fonts(tpl, short, pal):
    fam = tpl.get("fontFamily", YOUTH_FONT)
    grad = pal.get("titleGradient") or [pal["accent2"], pal["accent"]]
    return {
        "family": fam,
        "title": _font(fam, "bold", int(short * YOUTH_SCALE["title"])),
        "sub": _font(fam, "bold", int(short * YOUTH_SCALE["subtitle"])),
        "badge": _font(fam, "bold", int(short * YOUTH_SCALE["badge"])),
        "lab": _font(fam, "bold", int(short * YOUTH_SCALE["label"])),
        "pill": _font(fam, "bold", int(short * YOUTH_SCALE["footpill"])),
        "name": _font(fam, "bold", int(short * YOUTH_SCALE["name"])),
        "role": _font(fam, "regular", int(short * YOUTH_SCALE["role"])),
        "gc": (tuple(grad[0]), tuple(grad[1])),
    }


def _youth_canvas(tpl, W, H, M, short, pal):
    img = _gradient((W, H), pal["bgTop"], pal["bgBottom"])
    deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    DECOS.get(tpl.get("deco", "youth-sparkle"), _deco_youth_sparkle)(deco, W, H, M, pal, short)
    return Image.alpha_composite(img, deco)


def _zone_label_youth(draw, content, M, short, pal, f_lab, zones, dry=False):
    """Zone A：编辑式两段标签条（深色粗体 ｜ 灰色说明）。返回占用高度。"""
    lab = (content.get("label") or "").strip()
    if not lab:
        return 0
    if dry:
        return f_lab.size
    parts = [p for p in LABEL_SPLIT_RE.split(lab) if p] or [lab]
    head, tail = parts[0], " · ".join(parts[1:])
    draw.text((M, M), head, font=f_lab, fill=_rgba(pal["text"]), anchor="la")
    ax = M + f_lab.getlength(head) + int(short * 0.018)
    if tail:
        draw.line([ax, M + int(short * 0.004), ax, M + int(f_lab.size * 0.98)],
                  fill=_rgba(pal["subtext"], 130), width=max(1, int(short * 0.0022)))
        draw.text((ax + int(short * 0.018), M), tail, font=f_lab,
                  fill=_rgba(pal["subtext"]), anchor="la")
    zones["A"] = [M, M, int(ax + int(short * 0.018) + f_lab.getlength(tail)), M + f_lab.size]
    return f_lab.size


def _zone_cta_youth(img, content, W, M, short, pal, F, zones, dry=False):
    """右上引导胶囊。返回胶囊高度。"""
    fr = (content.get("footerRight") or "").strip() or "点击预约 · 开播提醒"
    pw = int(F["pill"].getlength(fr)) + int(short * 0.058)
    ph = F["pill"].size + int(short * 0.034)
    if dry:
        return ph
    px0 = W - M - pw
    _grad_pill(img, [px0, M - int(short * 0.004), px0 + pw, M - int(short * 0.004) + ph],
               ph // 2, F["gc"][0], F["gc"][1], fr, F["pill"], (255, 255, 255))
    zones["E-right"] = [px0, M, px0 + pw, M + ph]
    return ph


def _youth_title_block(img, draw, content, x0, max_w, top, pal, F, zones,
                       centered=True, max_block_h=None, dry=False):
    """Zone B/C/D：主标题（渐变字 + 手绘下划线）、副标题、角标胶囊。

    dry=True 时只量不画 —— 人物版式需要先知道文案占多高，才能把人物放到它下面。
    返回 (标题行数, 标题底边, 整块底边)。
    """
    short = min(img.size)
    f_title, title_lines = _fit_font(F["family"], "bold", F["title"].size,
                                     content.get("mainTitle", ""), max_w, 2)
    subline = _ellipsize(content.get("subtitle", ""), F["sub"], max_w)
    badge = (content.get("badge") or "").strip()

    def block_h(f, lines):
        lh = f.size * 1.22
        th = (len(lines) - 1) * lh + f.size if lines else 0
        h = th + (int(short * 0.032) if lines else 0)
        if subline:
            h += int(short * 0.038) + F["sub"].size
        if badge:
            h += int(short * 0.040) + F["badge"].size + int(short * 0.028)
        return h

    if max_block_h:
        for _ in range(4):
            if block_h(f_title, title_lines) <= max_block_h:
                break
            f_title, title_lines = _fit_font(F["family"], "bold", int(f_title.size * 0.92),
                                             content.get("mainTitle", ""), max_w, 2)

    lh = f_title.size * 1.22
    if dry:
        th = (len(title_lines) - 1) * lh + f_title.size if title_lines else 0
        return title_lines, top + th, top + block_h(f_title, title_lines)

    y = top
    for ln in title_lines:
        w = f_title.getlength(ln)
        tx = (x0 + (max_w - w) / 2) if centered else x0
        _grad_text(img, (tx, y), ln, f_title, F["gc"][0], F["gc"][1], anchor="la")
        b = zones.get("B", [int(tx), y, int(tx + w), y + f_title.size])
        zones["B"] = [min(b[0], int(tx)), y, max(b[2], int(tx + w)), y + f_title.size]
        y += lh
    title_bottom = (y - lh + f_title.size) if title_lines else top

    if title_lines:
        last_w = f_title.getlength(title_lines[-1])
        last_x = (x0 + (max_w - last_w) / 2) if centered else x0
        _brush_underline(img, last_x + last_w * 0.67, title_bottom + int(short * 0.014),
                         min(last_w * 0.60, max_w * 0.52), pal["accent2"],
                         max(2, int(short * 0.0075)))

    cy = title_bottom + int(short * 0.032)
    if subline:
        w = F["sub"].getlength(subline)
        sx = (x0 + (max_w - w) / 2) if centered else x0
        draw.text((sx, cy), subline, font=F["sub"], fill=_rgba(pal["text"]), anchor="la")
        zones["C"] = [int(sx), int(cy), int(sx + w), int(cy + F["sub"].size)]
        cy += F["sub"].size + int(short * 0.038)

    if badge:
        bh = F["badge"].size + int(short * 0.028)
        bw = int(F["badge"].getlength(badge)) + int(short * 0.062)
        bx = (x0 + (max_w - bw) / 2) if centered else x0
        _grad_pill(img, [bx, cy, bx + bw, cy + bh], bh // 2, F["gc"][0], F["gc"][1],
                   badge, F["badge"], pal["badgeText"])
        zones["D"] = [int(bx), int(cy), int(bx + bw), int(cy + bh)]
        cy += bh
    return title_lines, title_bottom, cy


# ---------------------------------------------------------------- 无人物版式
def _youth_graphic(tpl, content, W, H, max_text_w) -> tuple[Image.Image, dict]:
    """无人物版式：文案居中通栏，底部两侧圆角图标卡（参考图的图标卡语言）。"""
    short = min(W, H)
    M = int(short * SAFE_MARGIN)
    pal = {k: (tuple(v) if isinstance(v, list) else v) for k, v in tpl["palette"].items()}
    F = _youth_fonts(tpl, short, pal)
    img = _youth_canvas(tpl, W, H, M, short, pal)
    draw = ImageDraw.Draw(img, "RGBA")
    zones: dict[str, list[int]] = {}

    _zone_label_youth(draw, content, M, short, pal, F["lab"], zones)
    _zone_cta_youth(img, content, W, M, short, pal, F, zones)

    f_title, title_lines = _fit_font(F["family"], "bold", F["title"].size,
                                     content.get("mainTitle", ""), max_text_w, 2)
    subline = _ellipsize(content.get("subtitle", ""), F["sub"], max_text_w)
    badge = (content.get("badge") or "").strip()
    lh = f_title.size * 1.22
    th = (len(title_lines) - 1) * lh + f_title.size if title_lines else 0
    underline_h = int(short * 0.032) if title_lines else 0
    gap_c = int(short * 0.038) if subline else 0
    badge_h = (F["badge"].size + int(short * 0.028)) if badge else 0
    badge_gap = int(short * 0.040) if badge else 0
    group_h = th + underline_h + gap_c + (F["sub"].size if subline else 0) + (badge_gap + badge_h if badge else 0)

    if H / W > 1.5:
        base = 0.425
    elif W > H * 1.35:
        base = 0.480
    else:
        base = 0.445
    group_top = int(H * base - group_h / 2)

    y = group_top
    for ln in title_lines:
        w = f_title.getlength(ln)
        _grad_text(img, ((W - w) / 2, y), ln, f_title, F["gc"][0], F["gc"][1], anchor="la")
        b = zones.get("B", [int((W - w) / 2), y, int((W + w) / 2), y + f_title.size])
        zones["B"] = [min(b[0], int((W - w) / 2)), y, max(b[2], int((W + w) / 2)), y + f_title.size]
        y += lh
    title_bottom = y - lh + f_title.size if title_lines else group_top

    if title_lines:
        last_w = f_title.getlength(title_lines[-1])
        cx = W / 2 + last_w * 0.17
        uw = min(last_w * 0.60, max_text_w * 0.52)
        _brush_underline(img, cx, title_bottom + int(short * 0.018), uw,
                         pal["accent2"], max(2, int(short * 0.0075)))

    cy = title_bottom + underline_h
    if subline:
        sy = int(cy + gap_c)
        w = F["sub"].getlength(subline)
        draw.text(((W - w) / 2, sy), subline, font=F["sub"], fill=_rgba(pal["text"]), anchor="la")
        zones["C"] = [int((W - w) / 2), sy, int((W + w) / 2), sy + F["sub"].size]
        cy = sy + F["sub"].size

    if badge:
        by = int(cy + badge_gap)
        bw = int(F["badge"].getlength(badge)) + int(short * 0.062)
        _grad_pill(img, [(W - bw) / 2, by, (W + bw) / 2, by + badge_h], badge_h // 2,
                   F["gc"][0], F["gc"][1], badge, F["badge"], pal["badgeText"])
        zones["D"] = [int((W - bw) / 2), by, int((W + bw) / 2), by + badge_h]

    # ---- Zone F：底部两侧圆角图标卡（左下主播头像卡 + 右下机构 Logo 卡）
    card = int(short * 0.132)
    radius = 0.30
    card_y = H - M - card
    ax0 = M
    _soft_card(img, ax0, card_y, card, radius)
    _paste_avatar(img, ax0 + card / 2, card_y + card / 2, card * 0.365,
                  content.get("avatarPath"), pal)

    lx0 = W - M - card
    _soft_card(img, lx0, card_y, card, radius, shadow_alpha=58)
    _paste_logo(img, (lx0 + card * 0.20, card_y + card * 0.20,
                      lx0 + card * 0.80, card_y + card * 0.80), content.get("logoPath"), pal)

    aname = (content.get("anchorName") or content.get("footerLeft") or "主播").strip()
    arole = (content.get("anchorTitle") or "").strip()
    name_x = ax0 + card + int(short * 0.030)
    name_y = card_y + card / 2 - (F["name"].size + F["role"].size + int(short * 0.008)) / 2
    name_w = max(int(F["name"].getlength(aname)), int(F["role"].getlength(arole)) if arole else 0)
    if arole:
        draw.text((name_x, name_y), aname, font=F["name"], fill=_rgba(pal["text"]), anchor="la")
        draw.text((name_x, name_y + F["name"].size + int(short * 0.008)), arole, font=F["role"],
                  fill=_rgba(pal["subtext"]), anchor="la")
    else:
        draw.text((name_x, card_y + card / 2), aname, font=F["name"], fill=_rgba(pal["text"]), anchor="lm")
    zones["F"] = [ax0, card_y, min(W - M, name_x + name_w + int(short * 0.02)), card_y + card]
    return img, zones


# ---------------------------------------------------------------- 人物版式
def _youth_person(tpl, content, W, H):
    """人物版式：半身像与文案错位排布。

    横版（16:9 / 2.35:1）→ 左右分栏：文案在左栏左对齐，人物在右侧出血
    方版 / 竖版（1:1 / 3:4 / 9:16）→ 上下分层：文案在上（方版左对齐、竖版居中），人物在下方

    顺序上「先量文案 → 再画人物 → 最后压文案」，所以人物永远在文字下层，
    主题信息绝不会被挡；人物也绝不会和文案抢位。
    """
    short = min(W, H)
    M = int(short * SAFE_MARGIN)
    pal = {k: (tuple(v) if isinstance(v, list) else v) for k, v in tpl["palette"].items()}
    F = _youth_fonts(tpl, short, pal)
    key = _aspect_key(W, H)
    cfg = PERSON_GEO.get(key) or {}
    cutout = bool(content.get("portraitCutout", True))
    aspect = min(1.15, max(0.36, float(content.get("portraitAspect") or 0.72)))

    img = _youth_canvas(tpl, W, H, M, short, pal)
    probe = ImageDraw.Draw(img, "RGBA")
    zones: dict[str, list[int]] = {}

    # ---- 1) 只量不画
    h_label = _zone_label_youth(probe, content, M, short, pal, F["lab"], zones, dry=True)
    label_bottom = M + h_label

    if key == "wide":
        text_x = M
        text_w = int(W * cfg.get("textWidthRatio", 0.53)) - M
        centered = False
        budget = None
    else:
        text_x = M
        text_w = int(W * cfg.get("textWidthRatio", 0.86))
        centered = True
        # 方版/竖版文案区在上段，给整块高度设预算，避免把人挤成一条
        budget = int(H * cfg.get("topRatio", 0.44)) - label_bottom - int(short * 0.02)

    _, _, block_h = _youth_title_block(img, probe, content, text_x, text_w, 0, pal, F, zones,
                                       centered=centered, max_block_h=budget, dry=True)
    sig_h = F["name"].size + int(short * 0.030)
    sig_gap = int(short * 0.034)

    if key == "wide":
        total = block_h + sig_gap + sig_h
        text_top = max(label_bottom + int(short * 0.05), int((H - total) / 2))
    else:
        text_top = max(label_bottom + int(short * 0.045), int(H * 0.152))
    sig_y = text_top + block_h + sig_gap

    # ---- 2) 画人物（在文字下层）
    gap = int(short * 0.02)
    pbox = _person_box(W, H, aspect, sig_y + sig_h, gap, key, cutout)
    # 人物被宽度上限压低时，文案与人物之间会留出空档 —— 把文案块下移一半补平
    if key != "wide":
        slack = pbox[0] - (sig_y + sig_h) - gap
        if slack > int(short * 0.06):
            shift = min(slack // 2, int(short * 0.14))
            text_top += shift
            sig_y += shift
            pbox = _person_box(W, H, aspect, sig_y + sig_h, gap, key, cutout)
    person = _paste_person(img, pbox, content, pal, W, H, key, cutout, short)
    if person is None:  # 素材读不出来 → 退回无人物版式，绝不破版
        fb = int(W * (0.70 if W > H * 1.35 else 0.86))
        g_img, g_zones = _youth_graphic(tpl, content, W, H, fb)
        return g_img, g_zones, "graphic", {"fallback": "素材不可读，已回落无人物版式"}
    px, py, pw, ph, shape = person

    # ---- 3) 画文字（最上层）
    zones = {}
    draw = ImageDraw.Draw(img, "RGBA")
    _zone_label_youth(draw, content, M, short, pal, F["lab"], zones)
    _zone_cta_youth(img, content, W, M, short, pal, F, zones)
    _youth_title_block(img, draw, content, text_x, text_w, text_top, pal, F, zones,
                       centered=centered, max_block_h=budget)
    if key == "wide":
        sig_x, sig_max = text_x, text_w
    else:
        sig_max = text_w
        sig_x = text_x
    sig = _signature_row(img, draw, sig_x, sig_y, sig_max, content, pal, short,
                         F["name"], F["role"],
                         align="left" if key == "wide" else "center")
    if sig:
        zones["G"] = sig["box"]
    zones["F"] = [int(px), int(py), int(px + pw), int(py + ph)]
    meta = {"personShape": shape, "personBox": [int(px), int(py), int(pw), int(ph)],
            "arrange": "left-right" if key == "wide" else "top-bottom"}
    return img, zones, "person", meta




# ---------------------------------------------------------------- 平台封面绘制件
def _pick_highlight(title: str, content: dict) -> list[str]:
    """挑标题里要打荧光笔的词：优先 content.highlightWords，否则取末段短词。"""
    words = content.get("highlightWords")
    if isinstance(words, list) and words:
        return [str(w) for w in words if w]
    t = (title or "").strip()
    if not t:
        return []
    parts = [p for p in re.split(r"[\s·|｜/、，,：:—\-]+", t) if p]
    if len(parts) < 2:
        return []
    cand = parts[-1]
    return [cand] if 2 <= len(cand) <= 8 else []


def _highlight_line(img, x, y, w, h, color, alpha=205, cover=0.62):
    """荧光笔：在文字下层压一条圆角色带（贴基线、盖住文字高度的 cover）。"""
    if w <= 2 or h <= 2:
        return
    bh = max(4, int(h * cover))
    bx = int(x - h * 0.09)
    by = int(y + h - bh - h * 0.07)
    bw = int(w + h * 0.20)
    lay = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    ImageDraw.Draw(lay).rounded_rectangle([0, 0, bw - 1, bh - 1],
                                          radius=max(2, int(bh * 0.34)), fill=_rgba(color, alpha))
    img.alpha_composite(lay, (bx, by))


def _grad_text_stroke(img, xy, text, font, c1, c2, stroke_c, stroke_w, anchor="la"):
    """渐变字 + 描边：先压一层纯色描边（含实心），再把渐变按字形遮罩贴上去。

    抖音封面压在高饱和底 / 画面帧上，没有描边的浅色字会直接糊掉。
    """
    if not text:
        return
    pad = max(0, int(stroke_w)) + 3
    bw = int(font.getlength(text)) + pad * 2 + 2
    asc, desc = font.getmetrics()
    bh = asc + desc + pad * 2 + 2
    box = (int(xy[0]) - pad, int(xy[1]) - pad)

    sl = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    ImageDraw.Draw(sl).text((pad, pad), text, font=font, anchor="la",
                            fill=_rgba(stroke_c), stroke_width=int(stroke_w),
                            stroke_fill=_rgba(stroke_c))
    img.alpha_composite(sl, box)

    ml = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(ml).text((pad, pad), text, font=font, anchor="la", fill=255)
    grad = _gradient_h((bw, bh), c1, c2)
    grad.putalpha(ImageChops.multiply(grad.getchannel("A"), ml))
    img.alpha_composite(grad, box)


def _topic_items(content: dict) -> list[str]:
    """话题标签：优先 content.topics，否则从领域标签拆词。"""
    explicit = content.get("topics")
    if isinstance(explicit, list) and explicit:
        return ["#" + str(t).lstrip("#") for t in explicit][:4]
    lab = (content.get("label") or "").strip()
    parts = [p for p in LABEL_SPLIT_RE.split(lab) if p] if lab else []
    words = [p for p in parts if 2 <= len(p) <= 7] or ["保险科普"]
    return ["#" + w for w in words[:3]]


def _fill_rrect(img, box, radius, fill=None, outline=None, width=0):
    """圆角矩形 —— 走独立图层 + alpha_composite。

    坑：RGBA 图像上用 `ImageDraw.Draw(im, "RGBA")` 填充带 alpha 的颜色**不会做混合**，
    而是把 alpha 原样写进像素；后续 `convert("RGB")` 丢掉 alpha，淡色底就变成一块纯色。
    凡是要半透明的填充都必须走这里。
    """
    x0, y0, x1, y1 = (int(v) for v in box)
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(lay).rounded_rectangle(
        [0, 0, w - 1, h - 1], radius=max(0, int(radius)),
        fill=_rgba(fill) if fill else None,
        outline=_rgba(outline) if outline else None, width=int(width))
    img.alpha_composite(lay, (x0, y0))


def _topic_row(img, draw, x, y, max_w, topics, font, short, bg=None, fg=(255, 255, 255),
               outline=None):
    """横排 `#话题` 胶囊，超宽自动换行。返回 (占用高度, 最右边界)。"""
    if not topics:
        return 0, int(x)
    ph = font.size + int(short * 0.028)
    gapx = int(short * 0.018)
    gapy = int(short * 0.014)
    cx, cy, right = int(x), int(y), int(x)
    for t in topics:
        tw = int(font.getlength(t)) + int(short * 0.040)
        if cx > x and cx + tw > x + max_w:
            cx, cy = int(x), cy + ph + gapy
        _fill_rrect(img, [cx, cy, cx + tw, cy + ph], ph // 2, fill=bg,
                    outline=outline, width=max(1, int(short * 0.0026)) if outline else 0)
        draw.text((cx + tw / 2, cy + ph / 2 + int(short * 0.003)), t, font=font,
                  fill=_rgba(fg), anchor="mm")
        right = max(right, cx + tw)
        cx += tw + gapx
    return cy + ph - y, right


def _page_badge(img, draw, right_x, y, text, pal, font, short, bg=None, fg=None):
    """右下角页码 / 时长角标。right_x 为右边界，返回 zone。"""
    tw = int(font.getlength(text)) + int(short * 0.044)
    th = font.size + int(short * 0.024)
    x0 = int(right_x - tw)
    _fill_rrect(img, [x0, y, x0 + tw, y + th], th // 2,
                fill=(bg or pal["badgeBg"]) + (236,))
    draw.text((x0 + tw / 2, y + th / 2 + int(short * 0.003)), text, font=font,
              fill=_rgba(fg or pal["badgeText"]), anchor="mm")
    return [x0, int(y), int(x0 + tw), int(y + th)]


def _account_row(img, draw, x, y, content, pal, f_name, f_role, short,
                 text_color=None, sub_color=None, ring=True):
    """账号行：圆形头像 + 昵称（+ 头衔）。返回 (zone, 头像边长)。"""
    av = int(short * 0.082)
    x = int(x)
    _paste_avatar(img, x + av / 2, y + av / 2, av / 2, content.get("avatarPath"), pal)
    if ring:
        ImageDraw.Draw(img, "RGBA").ellipse(
            [x, y, x + av, y + av], outline=(255, 255, 255, 225), width=max(2, int(short * 0.0034)))
    nm = (content.get("anchorName") or content.get("footerLeft") or "安心保险研究院").strip()
    rl = (content.get("anchorTitle") or "").strip()
    tx = x + av + int(short * 0.022)
    tc = _rgba(text_color or pal["text"])
    sc = _rgba(sub_color or pal["subtext"])
    if rl:
        total = f_name.size + f_role.size + int(short * 0.006)
        ty = int(y + (av - total) / 2)
        draw.text((tx, ty), nm, font=f_name, fill=tc, anchor="la")
        draw.text((tx, ty + f_name.size + int(short * 0.006)), rl, font=f_role, fill=sc, anchor="la")
        wmax = max(f_name.getlength(nm), f_role.getlength(rl))
    else:
        draw.text((tx, int(y + av / 2)), nm, font=f_name, fill=tc, anchor="lm")
        wmax = f_name.getlength(nm)
    return [x, int(y), int(tx + wmax), int(y + av)], av


def _platform_person_box(W, H, aspect, block_bottom, cutout, short, bleed=0.16, min_top=0.36):
    """平台封面的半身像框：文字块下方起、宽度优先、底部出血（抠底立绘才出血）。"""
    top = int(max(block_bottom + short * 0.032, H * min_top))
    bottom = H + int(H * bleed) if cutout else H - int(short * 0.030)
    h = max(int(H * 0.20), bottom - top)
    w = int(h * aspect)
    max_w = int(W * 0.86)
    if w > max_w:
        w = max_w
        h = int(w / aspect)
        top = bottom - h
    return top, bottom, w, h


def _platform_fonts(tpl, short, scale):
    fam = tpl.get("fontFamily", YOUTH_FONT)
    return fam, {k: _font(fam, "regular" if k in ("tail", "role") else "bold", int(short * v))
                 for k, v in scale.items()}


def _render_platform(tpl, content, W, H):
    """平台封面分发：xhs → 小红书笔记排版；douyin → 抖音竖屏排版。"""
    if family_of(tpl) == "xhs":
        return _render_xhs(tpl, content, W, H)
    return _render_douyin(tpl, content, W, H)


# ---------------------------------------------------------------- 渲染：小红书图文
def _render_xhs(tpl, content, W, H):
    """小红书笔记首图：作者行 + 超大黑体标题（关键词荧光高亮）+ 话题标签带。

    有半身像 → 标题块靠上，半身像在下半屏出血；无半身像 → 标题块居中偏上，底部留白由话题带收尾。
    """
    short = min(W, H)
    M = int(short * XHS_MARGIN)
    pal = {k: (tuple(v) if isinstance(v, list) else v) for k, v in tpl["palette"].items()}
    fam, F = _platform_fonts(tpl, short, XHS_SCALE)

    img = _gradient((W, H), pal["bgTop"], pal["bgBottom"])
    deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    DECOS.get(tpl.get("deco", "xhs-paper"), _deco_xhs_paper)(deco, W, H, M, pal, short)
    img = Image.alpha_composite(img, deco)
    draw = ImageDraw.Draw(img, "RGBA")
    zones: dict[str, list[int]] = {}
    person = layout_mode_of(content) == "person"
    text_w = W - M * 2

    # ---- Zone A 作者行 + 右上开播角标
    y = M
    a_zone, av = _account_row(img, draw, M, y, content, pal, F["name"], F["role"], short)
    zones["A"] = a_zone
    badge = (content.get("badge") or "").strip()
    if badge:
        bw = int(F["badge"].getlength(badge)) + int(short * 0.046)
        bh = F["badge"].size + int(short * 0.028)
        bx = int(W - M - bw)
        by = int(a_zone[1] + (av - bh) / 2)
        _grad_pill(img, [bx, by, bx + bw, by + bh], bh // 2, pal["accent"],
                   pal.get("accent2", pal["accent"]), badge, F["badge"], pal["badgeText"], shadow=40)
        zones["A2"] = [bx, by, bx + bw, by + bh]

    y += av + int(short * 0.026)
    draw.line([M, y, W - M, y], fill=_rgba(pal.get("divider", pal["subtext"]), 150),
              width=max(1, int(short * 0.0022)))
    y += int(short * 0.036)

    # ---- 底部信息带：话题标签 + 署名
    # 有半身像时，人物要出血占据下半屏，标签带必须跟着文字块走，否则会被人物压住；
    # 无人物版式才把标签带贴到画布底部收尾。
    inline_strip = person
    topics = _topic_items(content)
    topic_h = (F["topic"].size + int(short * 0.028)) if topics else 0
    foot = " · ".join(x for x in [(content.get("footerLeft") or "").strip(),
                                 (content.get("footerRight") or "").strip()] if x)
    foot_h = F["tail"].size if foot else 0
    strip_h = topic_h + (int(short * 0.024) + foot_h if (topic_h and foot_h) else foot_h)
    bottom_h = 0 if inline_strip else strip_h

    # ---- Zone B/C/D 内容块：标签条 + 主标题 + 副标题
    lab = (content.get("label") or "").strip()
    title = (content.get("mainTitle") or "").strip()
    sub = (content.get("subtitle") or "").strip()
    # 无人物版式下半屏没有半身像，标题再放大一档：小红书封面本来就是"一屏只读几个字"
    tsize = F["title"].size if person else int(F["title"].size * 1.12)
    f_title, lines = _fit_font(fam, "bold", tsize, title, text_w, 3)
    lh = int(f_title.size * 1.15)
    title_h = (len(lines) - 1) * lh + f_title.size if lines else 0
    hi = _pick_highlight(title, content)
    lab_h = F["label"].size if lab else 0
    sub_h = F["sub"].size if sub else 0
    block_h = (lab_h + int(short * 0.024) if lab_h else 0) + title_h \
        + (sub_h + int(short * 0.028) if sub_h else 0)

    avail_top = y
    avail = max(1, H - M - bottom_h - int(short * 0.020) - avail_top)
    if person:
        top = avail_top + max(0, int((avail - block_h) * 0.03))
    else:
        # 无人物版式下半屏整片空着，把内容块压到纵向偏中，避免底部 1/4 全是留白
        top = avail_top + max(0, int((avail - block_h) * 0.46))
    top = max(avail_top, min(top, avail_top + max(0, avail - block_h)))

    yy = top
    if lab:
        parts = [p for p in LABEL_SPLIT_RE.split(lab) if p] or [lab]
        head, tail = parts[0], " · ".join(parts[1:])
        draw.text((M, yy), head, font=F["label"], fill=_rgba(pal["accent"]), anchor="la")
        ax = M + F["label"].getlength(head)
        zx1 = int(ax)
        if tail:
            draw.text((int(ax + int(short * 0.016)), int(yy + (F["label"].size - F["tail"].size) / 2)),
                      tail, font=F["tail"], fill=_rgba(pal["subtext"]), anchor="la")
            zx1 = int(ax + int(short * 0.016) + F["tail"].getlength(tail))
        zones["B"] = [M, int(yy), zx1, int(yy + F["label"].size)]
        yy += lab_h + int(short * 0.024)

    # 荧光笔在下层，字压在上层
    for i, ln in enumerate(lines):
        if hi and any(w in ln for w in hi):
            _highlight_line(img, M, yy + i * lh, f_title.getlength(ln), f_title.size,
                            pal.get("highlight", pal["accent"]), alpha=200)
    for i, ln in enumerate(lines):
        draw.text((M, yy + i * lh), ln, font=f_title, fill=_rgba(pal["text"]), anchor="la")
    zones["C"] = [M, int(yy), int(M + max(f_title.getlength(l) for l in lines)), int(yy + title_h)]
    yy += title_h

    if sub:
        yy += int(short * 0.028)
        s = _ellipsize(sub, F["sub"], text_w)
        draw.text((M, yy), s, font=F["sub"], fill=_rgba(pal["subtext"]), anchor="la")
        zones["D"] = [M, int(yy), int(M + F["sub"].getlength(s)), int(yy + F["sub"].size)]
        yy += sub_h

    # ---- Zone E 信息带位置：有人物时跟在文字块后，人物再从信息带下方起出血
    strip_y = yy + int(short * 0.034) if inline_strip else H - M - bottom_h
    person_floor = strip_y + strip_h if inline_strip else yy

    # ---- Zone F 半身像（下半屏出血）
    shape = None
    if person:
        pbox = _platform_person_box(W, H, float(content.get("portraitAspect") or 0.72),
                                    person_floor, bool(content.get("portraitCutout")), short,
                                    bleed=0.16, min_top=0.40)
        res = _paste_person(img, pbox, content, pal, W, H, _aspect_key(W, H),
                            bool(content.get("portraitCutout")), short)
        if res:
            shape = res[4]
            zones["F"] = [res[0], res[1], res[0] + res[2], res[1] + res[3]]

    # ---- Zone E 信息带：话题标签 + 署名
    if topics:
        _, right = _topic_row(img, draw, M, strip_y, text_w, topics, F["topic"], short,
                              bg=pal.get("topicBg", pal.get("accentSoft")),
                              fg=pal.get("topicFg", pal["accent"]))
        zones["E"] = [M, int(strip_y), right, int(strip_y + topic_h)]
    if foot:
        fy = strip_y + (topic_h + int(short * 0.024) if topic_h else 0)
        fs = _ellipsize(foot, F["tail"], text_w)
        draw.text((M, fy), fs, font=F["tail"], fill=_rgba(pal["footerText"]), anchor="la")
        zones["E2"] = [M, int(fy), int(M + F["tail"].getlength(fs)), int(fy + F["tail"].size)]

    meta = {"arrange": "top-bottom" if person else None}
    if shape:
        meta["personShape"] = shape
    meta["safeArea"] = PLATFORM_BY_ID.get(tpl.get("platform"), {}).get("safeArea", {})
    return img, zones, ("person" if person else "graphic"), meta


# ---------------------------------------------------------------- 渲染：抖音竖屏
def _render_douyin(tpl, content, W, H):
    """抖音图文首图 / 视频封面：高饱和渐变底 + 超大描边标题，全部内容落在平台安全区内。

    安全区来自 platform.safeArea：顶部让开状态栏与搜索栏，底部让开昵称、文案、音乐条与评论框。
    有半身像 → 人物在下半屏出血、文字块上移；无半身像 → 文字块整体居中偏上。
    """
    short = min(W, H)
    plat = PLATFORM_BY_ID.get(tpl.get("platform")) or {}
    sa = plat.get("safeArea") or {}
    pad_top = int(H * float(sa.get("top", 0.06)))
    pad_bot = int(H * float(sa.get("bottom", 0.10)))
    M = int(short * 0.055)
    pal = {k: (tuple(v) if isinstance(v, list) else v) for k, v in tpl["palette"].items()}
    fam, F = _platform_fonts(tpl, short, DY_SCALE)

    img = _gradient((W, H), pal["bgTop"], pal["bgBottom"])
    deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    DECOS.get(tpl.get("deco", "dy-aurora"), _deco_dy_aurora)(deco, W, H, M, pal, short)
    img = Image.alpha_composite(img, deco)
    draw = ImageDraw.Draw(img, "RGBA")
    zones: dict[str, list[int]] = {}
    person = layout_mode_of(content) == "person"

    sx0, sx1 = M, W - M
    sw = sx1 - sx0
    cx = W / 2
    sy0 = pad_top + int(short * 0.016)
    sy1 = H - pad_bot - int(short * 0.016)
    stroke_c = tuple(pal.get("strokeColor", (0, 0, 0)))
    stroke_w = max(2, int(short * 0.0072))

    topics = _topic_items(content)
    topic_h = (F["topic"].size + int(short * 0.028)) if topics else 0
    title = (content.get("mainTitle") or "").strip()
    sub = (content.get("subtitle") or "").strip()
    badge = (content.get("badge") or "").strip()
    f_title, lines = _fit_font(fam, "bold", F["title"].size, title, sw, 3)
    lh = int(f_title.size * 1.14)
    title_h = (len(lines) - 1) * lh + f_title.size if lines else 0
    sub_h = F["sub"].size if sub else 0
    badge_h = (F["badge"].size + int(short * 0.028)) if badge else 0
    block_h = (topic_h + int(short * 0.026) if topic_h else 0) + title_h \
        + (sub_h + int(short * 0.030) if sub_h else 0) \
        + (badge_h + int(short * 0.032) if badge_h else 0)

    # 底部账号行（安全区内）—— 与人物可能重叠，靠白字 + 描边保证可读
    acc_h = int(short * 0.082)
    acc_y = sy1 - acc_h
    body_top = sy0
    body_bottom = acc_y - int(short * 0.026)
    avail = max(1, body_bottom - body_top)
    if person:
        top = body_top + max(0, int((avail - block_h) * 0.05))
    else:
        top = body_top + max(0, int((avail - block_h) * 0.34))
    top = max(body_top, min(top, body_bottom - block_h))

    y = top
    if topics:
        _topic_row(img, draw, sx0, y, sw, topics, F["topic"], short,
                   bg=pal.get("topicBg", pal.get("accentSoft")),
                   fg=pal.get("topicFg", (255, 255, 255)))
        y += topic_h + int(short * 0.026)

    gc = pal.get("titleGradient") or [[255, 255, 255], [255, 255, 255]]
    c1, c2 = tuple(gc[0]), tuple(gc[-1])
    for i, ln in enumerate(lines):
        _grad_text_stroke(img, (cx - f_title.getlength(ln) / 2, y + i * lh), ln, f_title,
                          c1, c2, stroke_c, stroke_w)
    tw_max = max(f_title.getlength(l) for l in lines) if lines else 0
    zones["C"] = [int(cx - tw_max / 2), int(y), int(cx + tw_max / 2), int(y + title_h)]
    y += title_h

    if sub:
        y += int(short * 0.030)
        s = _ellipsize(sub, F["sub"], sw)
        _grad_text_stroke(img, (cx - F["sub"].getlength(s) / 2, y), s, F["sub"],
                          tuple(pal.get("highlight", pal["accent2"])), tuple(pal.get("highlight", pal["accent2"])),
                          stroke_c, max(1, int(stroke_w * 0.52)))
        zones["D"] = [int(cx - F["sub"].getlength(s) / 2), int(y),
                      int(cx + F["sub"].getlength(s) / 2), int(y + F["sub"].size)]
        y += sub_h

    if badge:
        y += int(short * 0.032)
        bw = int(F["badge"].getlength(badge)) + int(short * 0.052)
        _grad_pill(img, [int(cx - bw / 2), int(y), int(cx + bw / 2), int(y + badge_h)],
                   badge_h // 2, pal["accent"], pal.get("accent2", pal["accent"]),
                   badge, F["badge"], pal["badgeText"], shadow=48)
        zones["D2"] = [int(cx - bw / 2), int(y), int(cx + bw / 2), int(y + badge_h)]
        y += badge_h

    # ---- Zone F 半身像：文字块下方起、底部出血
    shape = None
    if person:
        min_top = 0.34 if tpl.get("platform") == "dy_video" else 0.38
        pbox = _platform_person_box(W, H, float(content.get("portraitAspect") or 0.72),
                                    y, bool(content.get("portraitCutout")), short,
                                    bleed=0.18, min_top=min_top)
        res = _paste_person(img, pbox, content, pal, W, H, _aspect_key(W, H),
                            bool(content.get("portraitCutout")), short)
        if res:
            shape = res[4]
            zones["F"] = [res[0], res[1], res[0] + res[2], res[1] + res[3]]

    # ---- Zone E 底部账号行 + 右侧页码 / 时长角标
    av = int(short * 0.082)
    _paste_avatar(img, sx0 + av / 2, acc_y + av / 2, av / 2, content.get("avatarPath"), pal)
    ImageDraw.Draw(img, "RGBA").ellipse(
        [sx0, acc_y, sx0 + av, acc_y + av], outline=(255, 255, 255, 232),
        width=max(2, int(short * 0.0036)))
    nm = "@" + (content.get("anchorName") or content.get("footerLeft") or "安心保险研究院").strip()
    nm = _ellipsize(nm, F["name"], int(sw * 0.62))
    # 文字色跟模板走（暗底白字 / 亮底深字），再压一层与底色相反的描边，压在人物身上也读得清
    draw.text((int(sx0 + av + int(short * 0.020)), int(acc_y + av / 2)), nm, font=F["name"],
              fill=_rgba(pal["text"]), anchor="lm",
              stroke_width=max(1, int(short * 0.0034)), stroke_fill=_rgba(stroke_c, 215))
    zones["E"] = [sx0, acc_y, int(sx0 + av + int(short * 0.020) + F["name"].getlength(nm)),
                  acc_y + av]

    # 图文 → 轮播页码；视频 → 时长角标
    # 不要用 ▶ / ⏱ 这类符号：系统中文字体没有这些字形，会渲染成豆腐块。
    page = (content.get("pageLabel") or "").strip()
    if not page:
        if tpl.get("pageStyle") == "video":
            page = str(content["durationLabel"]) if content.get("durationLabel") else "完整版"
        elif content.get("pageTotal"):
            page = f"{content.get('pageIndex') or 1}/{content['pageTotal']}"
    if page:
        ph = F["page"].size + int(short * 0.024)
        zones["F2"] = _page_badge(img, draw, sx1, int(acc_y + (av - ph) / 2), page,
                                  pal, F["page"], short)

    meta = {"arrange": "top-bottom" if person else None}
    if shape:
        meta["personShape"] = shape
    meta["safeArea"] = sa
    meta["safePad"] = [pad_top, pad_bot]
    return img, zones, ("person" if person else "graphic"), meta


# ---------------------------------------------------------------- 主渲染
def render_cover(tpl: dict, content: dict, size_key: str = "1:1", out_path: str | None = None) -> dict:
    if size_key not in SIZES:
        size_key = "1:1"
    W, H = SIZES[size_key]
    fam = family_of(tpl)
    max_text_w = int(W * (0.58 if W > H * 1.35 else 0.87))
    if fam in PLATFORM_FAMILIES:
        img, zones, mode, meta = _render_platform(tpl, content, W, H)
    elif fam == "youth":
        max_text_w = int(W * (0.70 if W > H * 1.35 else 0.86))
        img, zones, mode, meta = _render_youth(tpl, content, W, H, max_text_w)
    else:
        img, zones, mode, meta = _render_classic(tpl, content, W, H, max_text_w, None)

    # 文字覆盖率（排版风险检测用）—— Zone F 是人物/图标卡画面，不算文字
    ink = 0.0
    for k, z in zones.items():
        if k == "F" or not isinstance(z, list) or len(z) != 4:
            continue
        ink += (z[2] - z[0]) * (z[3] - z[1]) * _INK_FILL
    text_ratio = round(ink / float(W * H), 4)

    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        img.convert("RGB").save(out_path, "PNG", optimize=True)

    return {"path": out_path, "sizeKey": size_key, "W": W, "H": H,
            "textRatio": text_ratio, "zoneBoxes": zones, "family": fam,
            "platform": tpl.get("platform") or "live",
            "layoutMode": mode, "layoutMeta": meta or {}}


def render_set(tpl_id: str, content: dict, size_keys: list[str], out_dir: str, stem: str) -> list[dict]:
    tpl = TPL_BY_ID.get(tpl_id) or TEMPLATES[0]
    out = []
    for sk in size_keys:
        p = os.path.join(out_dir, f"{stem}_{sk.replace(':', 'x').replace('.', '_')}.png")
        out.append(render_cover(tpl, content, sk, p))
    return out


def layout_summary() -> list[dict]:
    return LAYOUT_MODES


def platform_summary() -> list[dict]:
    """平台预设（前端做平台切换用）：尺寸、推荐尺寸、安全区、可用风格族。"""
    out = []
    for p in PLATFORMS:
        out.append({
            "id": p["id"], "name": p["name"], "kind": p.get("kind", "直播"),
            "short": p.get("short") or p["name"],
            "sizes": [s for s in p.get("sizes", []) if s in SIZES],
            "defaultSizes": [s for s in p.get("defaultSizes", []) if s in SIZES],
            "safeArea": p.get("safeArea", {"top": 0.0, "bottom": 0.0}),
            "families": p.get("families", []),
            "zonesNote": p.get("zonesNote", ""),
            "note": p.get("note", ""),
            "templates": [t["id"] for t in TEMPLATES if (t.get("platform") or "live") == p["id"]]
                         or [t["id"] for t in TEMPLATES if family_of(t) in p.get("families", [])],
        })
    return out


def platform_of(tpl: dict) -> dict:
    return PLATFORM_BY_ID.get(tpl.get("platform") or "live") or {}


def template_summary() -> list[dict]:
    out = []
    for t in TEMPLATES:
        p = t["palette"]
        tg = p.get("titleGradient") or [p.get("accent2", p["accent"]), p["accent"]]
        pid = t.get("platform") or "live"
        out.append({
            "id": t["id"], "name": t["name"], "tagline": t["tagline"], "scenes": t["scenes"],
            "family": family_of(t),
            "familyName": next((f["name"] for f in FAMILIES if f["id"] == family_of(t)), ""),
            "platform": pid,
            "platformName": (PLATFORM_BY_ID.get(pid) or {}).get("name", "直播间封面"),
            "platformKind": (PLATFORM_BY_ID.get(pid) or {}).get("kind", "直播"),
            "swatch": ["#%02x%02x%02x" % tuple(p["bgBottom"]),
                       "#%02x%02x%02x" % tuple(p["accent"]),
                       "#%02x%02x%02x" % tuple(tg[0]),
                       "#%02x%02x%02x" % tuple(tg[-1])],
        })
    return out


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.join(ROOT, "core"))
    from assets import asset_path, _seed_demo, list_assets

    _seed_demo()
    av = [a for a in list_assets("avatar")]
    lg = [a for a in list_assets("logo")]
    pt = [a for a in list_assets("portrait")]
    base = {"label": "保险科普 · 直播间", "mainTitle": "利率下行期 钱该放哪",
            "subtitle": "三个确定性 看清长期现金流", "badge": "9月22日 20:00",
            "footerLeft": "安心保险研究院", "footerRight": "点击预约 · 开播提醒",
            "institution": "安心保险研究院"}
    withp = {**base,
             "anchorName": pt[0]["name"] if pt else "", "anchorTitle": pt[0]["title"] if pt else "",
             "portraitPath": asset_path(pt[0]["id"]) if pt else None,
             "portraitAspect": pt[0]["aspect"] if pt else 0.72,
             "portraitCutout": True,
             "avatarPath": asset_path(av[0]["id"]) if av else None,
             "logoPath": asset_path(lg[0]["id"]) if lg else None}
    nop = {**base,
           "anchorName": av[0]["name"] if av else "", "anchorTitle": av[0]["title"] if av else "",
           "avatarPath": asset_path(av[0]["id"]) if av else None,
           "logoPath": asset_path(lg[0]["id"]) if lg else None}
    out = os.path.join(ROOT, "outputs", "_selftest")
    for t in TEMPLATES:
        for tag, c in (("person", withp), ("graphic", nop)):
            r = render_set(t["id"], c, ["1:1"], out, f"{t['id']}_{tag}")
            print(f"{t['id']:20s} {family_of(t):8s} {tag:8s} mode={r[0]['layoutMode']:8s} "
                  f"textRatio={r[0]['textRatio']}")

