# -*- coding: utf-8 -*-
"""
主播素材库 —— 主播半身像 / 主播头像 / 机构 Logo 的上传、自动裁切与索引管理。

三个角色的处理策略完全不同，因为它们在封面上的用法不同：

  portrait 主播半身像  ← 版式主角。决定封面走「人物版式」还是「无人物版式」
      1) 去头顶留白（只裁纯色留白，不碰画面）
      2) 按 0.72 画幅取头肩（图够高就截上部，图偏方/偏宽就收窄两边）
      3) 可选自动抠底：从四边泛洪抠掉纯色背景 → 羽化 → 裁掉多余透明边
      4) 长边缩到 1400 落盘，保留透明通道
  avatar   主播头像    ← 版式配角。只出现在「签名胶囊」里的圆形小头像
      中心方裁 + 512，渲染时圆形裁切（任何原图都不变形）
  logo     机构 Logo   ← 等比容纳 + 12% 内边距贴到 512 透明画布

设计要点：
- 素材落盘在 assets/，索引写在 assets/assets.json（纯文本，便于人工排查）
- 抠底失败（背景不纯 / 抠掉了主体）会自动放弃并回退到「拱形人物卡」，绝不产出破图
- 首次启动植入示例素材，保证「没上传也能看到完整效果」
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import uuid

from PIL import Image, ImageDraw, ImageFilter, ImageOps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(ROOT, "assets")
INDEX_FILE = os.path.join(ASSETS_DIR, "assets.json")

ROLES = {
    "portrait": {"name": "主播半身像", "shape": "cutout", "slot": "main",
                 "hint": "版式主角：决定用「人物版式」还是「无人物版式」"},
    "avatar": {"name": "主播头像", "shape": "circle", "slot": "signature",
               "hint": "签名胶囊里的圆形小头像"},
    "logo": {"name": "机构 Logo", "shape": "contain", "slot": "corner",
             "hint": "底部信息条 / 签名胶囊里的机构标识"},
}

# 半身像画幅（宽/高）。0.72 ≈ 3:4.2，正好容纳头 + 肩 + 胸
PORTRAIT_ASPECT = 0.72
MAX_SIDE = {"portrait": 1400, "avatar": 512, "logo": 512}
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
MAX_BYTES = 8 * 1024 * 1024

# 抠底参数
KEY_SAMPLE = 520          # 泛洪前先把图缩到长边这么多，速度与精度平衡
KEY_TOL = 30              # 与边角色的容差
KEY_MIN, KEY_MAX = 0.05, 0.92   # 抠掉比例超出这个范围 → 判定失败，放弃抠底


def _ensure():
    os.makedirs(ASSETS_DIR, exist_ok=True)
    if not os.path.exists(INDEX_FILE):
        _write_index([])


def _read_index() -> list[dict]:
    _ensure()
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_index(items: list[dict]):
    os.makedirs(ASSETS_DIR, exist_ok=True)
    tmp = INDEX_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, INDEX_FILE)


def _safe_stem(filename: str) -> str:
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    stem = re.sub(r"[^\w\u4e00-\u9fa5-]+", "_", stem).strip("_")
    return (stem or "asset")[:40]


# ---------------------------------------------------------------- 基础图像处理
def _square_crop(im: Image.Image) -> Image.Image:
    w, h = im.size
    side = min(w, h)
    return im.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))


def _contain(im: Image.Image, canvas: int, pad_ratio: float = 0.12) -> Image.Image:
    inner = int(canvas * (1 - pad_ratio * 2))
    w, h = im.size
    scale = min(inner / w, inner / h)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    out = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    out.paste(im, ((canvas - nw) // 2, (canvas - nh) // 2), im if im.mode == "RGBA" else None)
    return out


def _dist(a, b) -> int:
    return max(abs(a[i] - b[i]) for i in range(3))


def _trim_headroom(im: Image.Image, tol: int = 20, max_ratio: float = 0.16) -> Image.Image:
    """裁掉顶部的大片纯色留白（人像照常见的头顶留白）。

    只裁「整行都与边角色相近」的行，且最多裁 max_ratio —— 背景一复杂就整行不等，
    直接返回原图，属于保守策略，宁可少裁也不裁错。
    """
    w, h = im.size
    probe = im.convert("RGB").resize((64, 64), Image.BILINEAR)
    px = probe.load()
    corners = [px[1, 1], px[62, 1], px[1, 62], px[62, 62]]
    base = tuple(sorted(c[i] for c in corners)[1] for i in range(3))
    if max(_dist(c, base) for c in corners) > tol * 1.8:
        return im
    cut = 0
    for y in range(64):
        if max(_dist(px[x, y], base) for x in range(0, 64, 2)) > tol:
            break
        cut = y + 1
    cut = int(cut / 64 * h)
    cut = min(cut, int(h * max_ratio))
    # 至少留 55% 高度，避免误裁把人裁没
    if cut <= 0 or h - cut < h * 0.55:
        return im
    return im.crop((0, cut, w, h))


def _halfbody_crop(im: Image.Image, aspect: float = PORTRAIT_ASPECT) -> Image.Image:
    """按半身画幅取景：优先保住头部，其次保住肩胸。

    - 图够高（竖版全身照）：从顶部截取，得到「头 → 胸/腰」的一段
    - 图偏方 / 偏宽：左右收窄，保留整个高度
    """
    w, h = im.size
    need_h = int(round(w / aspect))
    if need_h <= h:
        return im.crop((0, 0, w, need_h))
    need_w = max(1, int(round(h * aspect)))
    x0 = max(0, (w - need_w) // 2)
    return im.crop((x0, 0, x0 + need_w, h))


def _remove_bg(im: Image.Image, tol: int = KEY_TOL) -> Image.Image | None:
    """自动抠底：从四边泛洪抠掉与边角同色的背景。

    只抠【与画布边界连通】的区域，所以人物内部的白色衬衫不会被误伤。
    抠掉比例离谱（几乎全透明 / 几乎没抠掉）时返回 None，由调用方回退。
    """
    work = im.convert("RGBA")
    scale = min(1.0, KEY_SAMPLE / max(work.size))
    if scale < 1.0:
        work = work.resize((max(1, int(work.width * scale)), max(1, int(work.height * scale))),
                           Image.LANCZOS)
    w, h = work.size
    px = work.load()
    corners = [px[1, 1], px[w - 2, 1], px[1, h - 2], px[w - 2, h - 2]]
    base = tuple(sorted(c[i] for c in corners)[1] for i in range(3))
    if max(_dist(c, base) for c in corners) > tol * 1.6:
        return None  # 四角不同色 → 背景不纯，不冒险

    for seed in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        try:
            ImageDraw.floodfill(work, seed, (0, 0, 0, 0), thresh=tol)
        except Exception:
            pass

    alpha = work.getchannel("A")
    transparent = alpha.histogram()[0] / float(w * h)
    if not (KEY_MIN < transparent < KEY_MAX):
        return None

    # 羽化：抠底边缘会带锯齿，模糊一下再回贴，得到自然过渡
    feather = max(1, int(min(w, h) * 0.010))
    soft = alpha.filter(ImageFilter.GaussianBlur(feather))
    if scale < 1.0:
        soft = soft.resize(im.size, Image.LANCZOS)
        feather = max(1, int(min(im.size) * 0.008))
        soft = soft.filter(ImageFilter.GaussianBlur(feather))
    out = im.convert("RGBA")
    if "A" in im.getbands():
        from PIL import ImageChops
        out.putalpha(ImageChops.multiply(soft, im.getchannel("A")))
    else:
        out.putalpha(soft)

    # 裁掉多余透明边 —— 否则人物在封面上会显得很小
    box = out.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
    if box:
        pad = int(min(out.size) * 0.012)
        box = (max(0, box[0] - pad), max(0, box[1] - pad),
               min(out.width, box[2] + pad), min(out.height, box[3] + pad))
        out = out.crop(box)
    return out


def _fit_max_side(im: Image.Image, limit: int) -> Image.Image:
    if max(im.size) <= limit:
        return im
    s = limit / max(im.size)
    return im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)


# ---------------------------------------------------------------- 各角色的处理
def process(data: bytes, role: str, cutout: bool = True) -> dict:
    """把上传的原始字节处理成规范化的素材 PNG（内存中返回，由调用方落盘）。"""
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)
    role = role if role in ROLES else "avatar"

    if role == "logo":
        return {"image": _contain(im.convert("RGBA"), MAX_SIDE["logo"]),
                "keepAlpha": True, "cutout": False}

    if role == "portrait":
        im = im.convert("RGBA")
        im = _trim_headroom(im)
        im = _halfbody_crop(im)
        im = _fit_max_side(im, MAX_SIDE["portrait"] * 2)   # 抠底在更大画布上做，边缘更准
        did = False
        if cutout:
            keyed = _remove_bg(im)
            if keyed is not None:
                im, did = keyed, True
        im = _fit_max_side(im, MAX_SIDE["portrait"])
        return {"image": im, "keepAlpha": True, "cutout": did}

    # avatar：中心方裁
    return {"image": _square_crop(im.convert("RGB")).resize((MAX_SIDE["avatar"],) * 2, Image.LANCZOS),
            "keepAlpha": False, "cutout": False}


# ---------------------------------------------------------------- CRUD
def add_asset(data: bytes, filename: str, role: str = "portrait",
              name: str = "", title: str = "", note: str = "",
              cutout: bool = True) -> dict:
    role = role if role in ROLES else "portrait"
    ext = os.path.splitext(filename or "")[1].lower()
    if ext and ext not in ALLOWED_EXT:
        raise ValueError(f"不支持的格式 {ext}，请上传 {'/'.join(sorted(ALLOWED_EXT))}")
    if len(data) > MAX_BYTES:
        raise ValueError(f"文件过大（{len(data) / 1048576:.1f}MB），请压缩到 8MB 以内")

    proc = process(data, role, cutout=cutout)
    img = proc["image"]
    aid = f"as_{uuid.uuid4().hex[:10]}"
    fname = f"{aid}_{_safe_stem(filename)}.png"
    fpath = os.path.join(ASSETS_DIR, fname)
    img.save(fpath, "PNG", optimize=True)

    item = {
        "id": aid,
        "role": role,
        "roleName": ROLES[role]["name"],
        "name": (name or "").strip()[:16] or {"portrait": "主播", "avatar": "主播", "logo": "机构"}[role],
        "title": (title or "").strip()[:20],
        "note": note,
        "file": fname,
        "url": f"/assets/{fname}",
        "w": img.width,
        "h": img.height,
        "aspect": round(img.width / img.height, 4),
        "cutout": bool(proc["cutout"]),
        "bytes": os.path.getsize(fpath),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "builtin": False,
    }
    items = _read_index()
    items.insert(0, item)
    _write_index(items)
    return item


def list_assets(role: str | None = None) -> list[dict]:
    _seed_demo()
    items = _read_index()
    if role:
        items = [i for i in items if i["role"] == role]
    return items


def get_asset(aid: str) -> dict | None:
    for i in _read_index():
        if i["id"] == aid:
            return i
    return None


def asset_path(aid: str) -> str | None:
    a = get_asset(aid)
    if not a:
        return None
    p = os.path.join(ASSETS_DIR, a["file"])
    return p if os.path.exists(p) else None


def update_asset(aid: str, **fields) -> dict | None:
    items = _read_index()
    for i in items:
        if i["id"] == aid:
            for k in ("name", "title", "role"):
                if fields.get(k) is not None:
                    i[k] = str(fields[k])[:20]
            if i.get("role") in ROLES:
                i["roleName"] = ROLES[i["role"]]["name"]
            _write_index(items)
            return i
    return None


def delete_asset(aid: str) -> bool:
    items = _read_index()
    keep, hit = [], None
    for i in items:
        if i["id"] == aid:
            hit = i
        else:
            keep.append(i)
    if not hit:
        return False
    if not hit.get("builtin"):
        try:
            os.remove(os.path.join(ASSETS_DIR, hit["file"]))
        except OSError:
            pass
    _write_index(keep)
    return True


# ---------------------------------------------------------------- 示例素材
def _demo_portrait(w: int = 1008, h: int = 1400) -> Image.Image:
    """生成一张示例主播半身像（扁平插画风 + 透明底），保证 demo 开箱即见人物版式。"""
    S = 2  # 超采样后缩小，模拟抗锯齿
    W, H = w * S, h * S
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im, "RGBA")

    SKIN = (246, 214, 186, 255)
    SKIN_D = (226, 186, 152, 255)
    HAIR = (52, 40, 46, 255)
    COAT = (52, 62, 118, 255)
    COAT_D = (38, 46, 92, 255)
    SHIRT = (246, 247, 252, 255)
    ACC = (108, 92, 246, 255)
    GOLD = (238, 190, 96, 255)

    def E(x0, y0, x1, y1, c):
        d.ellipse([x0 * W, y0 * H, x1 * W, y1 * H], fill=c)

    def R(x0, y0, x1, y1, c, r=0.0):
        d.rounded_rectangle([x0 * W, y0 * H, x1 * W, y1 * H], r * W, fill=c)

    def P(pts, c):
        d.polygon([(x * W, y * H) for x, y in pts], fill=c)

    # 肩 / 上半身：从脖子下方一直出血到画布底
    R(0.105, 0.420, 0.895, 1.02, COAT, r=0.090)
    # 内里衬衫 V 区
    P([(0.395, 0.420), (0.605, 0.420), (0.500, 0.640)], SHIRT)
    # 西装翻领
    P([(0.325, 0.420), (0.500, 0.420), (0.418, 0.665), (0.272, 0.535)], COAT_D)
    P([(0.675, 0.420), (0.500, 0.420), (0.582, 0.665), (0.728, 0.535)], COAT_D)
    # 领口丝巾（呼应主题色）
    P([(0.452, 0.420), (0.548, 0.420), (0.528, 0.505), (0.472, 0.505)], ACC)
    # 脖子
    R(0.432, 0.310, 0.568, 0.455, SKIN_D, r=0.028)
    # 后发（在头后，只垂到下巴下方一点）
    E(0.302, 0.082, 0.698, 0.368, HAIR)
    # 耳朵 + 耳环
    E(0.310, 0.243, 0.352, 0.322, SKIN)
    E(0.648, 0.243, 0.690, 0.322, SKIN)
    E(0.318, 0.320, 0.344, 0.356, GOLD)
    E(0.656, 0.320, 0.682, 0.356, GOLD)
    # 脸
    E(0.325, 0.115, 0.675, 0.362, SKIN)
    # 前发：刘海 + 两侧鬓发
    E(0.313, 0.072, 0.687, 0.214, HAIR)
    P([(0.313, 0.148), (0.356, 0.098), (0.350, 0.238), (0.306, 0.210)], HAIR)
    P([(0.687, 0.148), (0.644, 0.098), (0.650, 0.238), (0.694, 0.210)], HAIR)
    # 眉毛 / 眼 / 腮红 / 嘴
    d.line([(0.388 * W, 0.242 * H), (0.440 * W, 0.232 * H)], fill=(74, 56, 58, 255),
           width=int(0.011 * W))
    d.line([(0.612 * W, 0.242 * H), (0.560 * W, 0.232 * H)], fill=(74, 56, 58, 255),
           width=int(0.011 * W))
    E(0.402, 0.250, 0.446, 0.288, (46, 40, 48, 255))
    E(0.554, 0.250, 0.598, 0.288, (46, 40, 48, 255))
    E(0.370, 0.292, 0.416, 0.320, (240, 176, 168, 150))
    E(0.584, 0.292, 0.630, 0.320, (240, 176, 168, 150))
    d.arc([0.464 * W, 0.278 * H, 0.536 * W, 0.336 * H], start=20, end=160,
          fill=(198, 108, 108, 255), width=int(0.010 * W))
    return im.resize((w, h), Image.LANCZOS)


def _demo_avatar(size: int = MAX_SIDE["avatar"]) -> Image.Image:
    """生成一张干净的主播示例头像（渐变底 + 扁平人像剪影），避免 demo 时出现破图。"""
    im = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(im)
    c1, c2 = (108, 92, 246), (59, 130, 246)
    for y in range(size):
        t = y / (size - 1)
        d.line([0, y, size, y], fill=(int(c1[0] + (c2[0] - c1[0]) * t),
                                      int(c1[1] + (c2[1] - c1[1]) * t),
                                      int(c1[2] + (c2[2] - c1[2]) * t)))
    d = ImageDraw.Draw(im, "RGBA")
    d.ellipse([size * 0.30, size * 0.20, size * 0.70, size * 0.60], fill=(255, 255, 255, 240))
    d.ellipse([size * 0.11, size * 0.60, size * 0.89, size * 1.26], fill=(255, 255, 255, 240))
    d.ellipse([size * 0.42, size * 0.72, size * 0.58, size * 0.88], fill=(108, 92, 246, 255))
    return im


def _demo_logo(size: int = MAX_SIDE["logo"]) -> Image.Image:
    """生成一枚示例机构 Logo（三柱增长图形），对应参考图右侧的图标卡语言。"""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    bars = [(0.20, 0.46, (59, 130, 246)), (0.42, 0.62, (108, 92, 246)), (0.64, 0.82, (245, 158, 11))]
    bw = size * 0.16
    for xr, hr, col in bars:
        x0, y0 = size * xr, size * (0.86 - hr)
        d.rounded_rectangle([x0, y0, x0 + bw, size * 0.86], bw * 0.34, fill=col + (255,))
    return im


BUILTIN_PORTRAIT = os.path.join(ASSETS_DIR, "_builtin", "portrait.png")


def _builtin_portrait() -> Image.Image:
    """内置主播半身像。

    优先用 assets/_builtin/portrait.png —— 由 tools/set_builtin_portrait.py
    把一张真人照走完处理链（去留白 → 取头肩 → 抠底 → 裁透明边）后落盘的内置源；
    没有这个文件就回落到程序化绘制的扁平插画，保证任何环境都起得来。
    """
    if os.path.exists(BUILTIN_PORTRAIT):
        try:
            return Image.open(BUILTIN_PORTRAIT).convert("RGBA")
        except Exception:
            pass
    return _demo_portrait()


def _seed_demo():
    """首次调用时植入示例素材（幂等）。"""
    _ensure()
    items = _read_index()
    have = {i["id"] for i in items}
    added = False

    if "as_demo_portrait" not in have:
        f = "as_demo_portrait_示例主播半身像.png"
        im = _builtin_portrait()   # 有内置源（真人照）就用它，否则回落到程序化插画
        im.save(os.path.join(ASSETS_DIR, f), "PNG", optimize=True)
        items.append({
            "id": "as_demo_portrait", "role": "portrait", "roleName": ROLES["portrait"]["name"],
            "name": "林晓", "title": "资深保险规划师",
            "note": "内置主播半身像（真人照，已去背），可直接删掉",
            "file": f, "url": f"/assets/{f}", "w": im.width, "h": im.height,
            "aspect": round(im.width / im.height, 4), "cutout": True,
            "bytes": os.path.getsize(os.path.join(ASSETS_DIR, f)),
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "builtin": True,
        })
        added = True

    if "as_demo_host" not in have:
        f = "as_demo_host_示例主播头像.png"
        _demo_avatar().save(os.path.join(ASSETS_DIR, f), "PNG", optimize=True)
        items.append({
            "id": "as_demo_host", "role": "avatar", "roleName": ROLES["avatar"]["name"],
            "name": "林晓", "title": "资深保险规划师", "note": "内置示例素材，可直接删掉",
            "file": f, "url": f"/assets/{f}", "w": MAX_SIDE["avatar"], "h": MAX_SIDE["avatar"],
            "aspect": 1.0, "cutout": False,
            "bytes": os.path.getsize(os.path.join(ASSETS_DIR, f)),
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "builtin": True,
        })
        added = True

    if "as_demo_logo" not in have:
        f = "as_demo_logo_示例机构标识.png"
        _demo_logo().save(os.path.join(ASSETS_DIR, f), "PNG", optimize=True)
        items.append({
            "id": "as_demo_logo", "role": "logo", "roleName": ROLES["logo"]["name"],
            "name": "安心保险研究院", "title": "", "note": "内置示例素材，可直接删掉",
            "file": f, "url": f"/assets/{f}", "w": MAX_SIDE["logo"], "h": MAX_SIDE["logo"],
            "aspect": 1.0, "cutout": False,
            "bytes": os.path.getsize(os.path.join(ASSETS_DIR, f)),
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "builtin": True,
        })
        added = True

    if added:
        _write_index(items)


if __name__ == "__main__":
    import glob
    _seed_demo()
    for a in list_assets():
        print(f"{a['id']:18s} {a['role']:8s} {a['name']:8s} {a['w']}x{a['h']} "
              f"aspect={a.get('aspect')} cutout={a.get('cutout')} {a['file']}")
    # 回归：模拟一张「白底半身照」，验证自动抠底
    src = Image.new("RGB", (900, 1200), (248, 250, 252))
    d = ImageDraw.Draw(src)
    d.ellipse([250, 120, 650, 620], fill=(32, 40, 70))
    d.rectangle([280, 560, 620, 1200], fill=(32, 40, 70))
    src.save("/tmp/_probe_white_bg.png")
    with open("/tmp/_probe_white_bg.png", "rb") as fh:
        it = add_asset(fh.read(), "probe_white_bg.png", "portrait", "抠底探针",
                       "测试", cutout=True)
    print("→ 抠底结果:", it["w"], "x", it["h"], "aspect", it["aspect"], "cutout", it["cutout"])
    delete_asset(it["id"])
    print("清理探针 ok")
