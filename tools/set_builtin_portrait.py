# -*- coding: utf-8 -*-
"""把一张真人半身照设为产品内置的「默认主播半身像」。

为什么需要这个脚本：
  内置素材原来是 core/assets.py 里用 Pillow 现画的扁平插画（_demo_portrait）。
  换成真人照之后不能直接把原图丢进 assets/ —— 必须走项目自己的处理链
  （去头顶留白 → 取 0.72 头肩画幅 → 抠底 → 裁掉多余透明边），
  否则透明边一大，人物在封面里就缩得很小，出血立绘也接不上。

产物：
  assets/_builtin/portrait.png                  内置源，素材被删后重新植入时用它
  assets/as_demo_portrait_示例主播半身像.png      当前生效的内置素材
  assets/assets.json 里 as_demo_portrait 记录      w / h / aspect / cutout / bytes 同步

用法：
  python tools/set_builtin_portrait.py <图片路径>
  python tools/set_builtin_portrait.py            # 不给路径 = 用已有内置源重刷
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import assets as A  # noqa: E402

BUILTIN_DIR = os.path.join(A.ASSETS_DIR, "_builtin")
BUILTIN_SRC = os.path.join(BUILTIN_DIR, "portrait.png")
DEMO_FILE = "as_demo_portrait_示例主播半身像.png"
DEMO_ID = "as_demo_portrait"


def build(src: str | None = None) -> dict:
    """把 src 处理成内置半身像并落盘；src=None 时复用已有内置源。"""
    os.makedirs(BUILTIN_DIR, exist_ok=True)
    if src:
        with open(src, "rb") as fh:
            proc = A.process(fh.read(), "portrait", cutout=True)
        im = proc["image"]
        im.save(BUILTIN_SRC, "PNG", optimize=True)
    else:
        if not os.path.exists(BUILTIN_SRC):
            raise SystemExit(f"内置源不存在：{BUILTIN_SRC}，请传入一张图片路径")
        from PIL import Image
        im = Image.open(BUILTIN_SRC).convert("RGBA")
        proc = {"cutout": True}

    dst = os.path.join(A.ASSETS_DIR, DEMO_FILE)
    im.save(dst, "PNG", optimize=True)

    # 同步索引（list_assets 会顺带把缺失的内置素材补上）
    A.list_assets()
    items = A._read_index()
    hit = None
    for it in items:
        if it["id"] != DEMO_ID:
            continue
        it.update({
            "file": DEMO_FILE,
            "url": f"/assets/{DEMO_FILE}",
            "w": im.width,
            "h": im.height,
            "aspect": round(im.width / im.height, 4),
            "cutout": bool(proc.get("cutout", True)),
            "bytes": os.path.getsize(dst),
            "note": "内置主播半身像（真人照，已去背），可直接删掉",
            "builtin": True,
        })
        hit = it
    if hit is None:
        raise SystemExit("索引里没有 as_demo_portrait 记录，请先启动一次服务让它植入")
    A._write_index(items)
    return hit


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else None
    it = build(src)
    print(f"内置半身像已更新：{it['file']}")
    print(f"  {it['w']}x{it['h']}  aspect={it['aspect']}  cutout={it['cutout']}  {it['bytes'] / 1024:.0f}KB")
