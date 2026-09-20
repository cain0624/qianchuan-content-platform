#!/usr/bin/env python3
"""组装可发布的站点目录（dist/）并生成引擎清单 py-manifest.json

发布目录长这样，和本地项目是**同构**的：

    dist/
    ├── index.html            ← 必须在根！Pages 的站点根就是仓库根，
    │                            放到子目录里访问 / 会得到目录列表（状态码还是 200）
    ├── py-manifest.json      ← 告诉浏览器该把哪些文件装进虚拟文件系统
    ├── api_facade.py         ← 浏览器内的门面（server.py 的等价物）
    ├── core/*.py             ← 与本地完全同一份业务代码
    ├── data/*.json
    ├── fonts/*.ttf[.gz]
    ├── assets/               ← 注意 _builtin/ 以下划线开头，必须配 .nojekyll
    ├── static/{app.js,bridge.js,style.css}
    └── .nojekyll             ← 没有它 Jekyll 会吃掉 _builtin/ 目录

index.html 里引用的是 ./static/...，于是「本地从 FastAPI 的 / 看」与
「Pages 从 /<repo>/ 看」路径完全一致，一份文件两边都对。

用法：
    python tools/build_site.py --repo <user>/<repo> [--out dist]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 需要随站点分发的文件（相对项目根的路径）
STATIC_FILES = ["static/app.js", "static/bridge.js", "static/style.css"]
DATA_DIRS = ["core", "data", "assets"]
FONT_DIR = "fonts"
ROOT_FILES = ["api_facade.py"]

# 同时带上完整源码，让仓库本身是个可读的项目（README / server.py / tools）
EXTRA_FILES = ["server.py", "daemon.py", "run.sh", "start.sh", "README.md"]
EXTRA_DIRS = ["tools"]

SKIP_NAMES = {"__pycache__", ".DS_Store", ".server.pid", "outputs", "dist", ".git"}


def copy_file(src: str, dst: str) -> int:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return os.path.getsize(dst)


def copy_tree(src_dir: str, dst_dir: str, skip_top: set[str] | None = None) -> list[str]:
    """递归复制，返回相对路径列表。"""
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(src_dir):
        dirnames[:] = [d for d in dirnames if d not in SKIP_NAMES]
        rel_dir = os.path.relpath(dirpath, src_dir)
        for fn in filenames:
            if fn in SKIP_NAMES or fn.endswith((".bak", ".pyc", ".orig")):
                continue
            rel = os.path.normpath(os.path.join(rel_dir, fn)).replace(os.sep, "/")
            if rel.startswith("."):
                continue
            if skip_top and rel in skip_top:
                continue
            dst = os.path.join(dst_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(dirpath, fn), dst)
            out.append(f"{os.path.basename(dst_dir)}/{rel}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="<user>/<repo>，用于生成 jsdelivr CDN 前缀")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--branch", default="main")
    ap.add_argument("--no-cdn", action="store_true", help="不给字体配 CDN，只走站点自身")
    args = ap.parse_args()

    out = args.out
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out, exist_ok=True)

    print(f"组装 → {out}")

    # ---- 引擎必需的目录（core / data / assets）
    core_files: list[str] = []
    for d in DATA_DIRS:
        src = os.path.join(ROOT, d)
        if not os.path.isdir(src):
            print(f"  ⚠ 缺少目录 {d}")
            continue
        got = copy_tree(src, os.path.join(out, d))
        core_files.extend(got)
        print(f"  {d}/  {len(got)} 个文件")

    # ---- 字体（大文件，单独分组，首次生成时才加载）
    fonts_manifest: dict[str, list[dict]] = {}
    fam_of = {"sans": "sans", "serif": "serif"}
    font_src_dir = os.path.join(ROOT, FONT_DIR)
    font_names = sorted(os.listdir(font_src_dir)) if os.path.isdir(font_src_dir) else []
    for fn in font_names:
        if not fn.endswith(".ttf"):        # .gz 在下面按 ttf 配对补上
            continue
        src = os.path.join(font_src_dir, fn)
        fam = fam_of.get(fn.split("-")[0])
        if not fam:
            continue
        entry = {"rel": f"{FONT_DIR}/{fn}", "gz": False,
                 "bytes": copy_file(src, os.path.join(out, FONT_DIR, fn))}
        base_gz = os.path.join(font_src_dir, fn + ".gz")
        if os.path.isfile(base_gz):
            entry["gz"] = True
            entry["gzBytes"] = copy_file(base_gz, os.path.join(out, FONT_DIR, fn + ".gz"))
        fonts_manifest.setdefault(fam, []).append(entry)
    for fam, items in fonts_manifest.items():
        total = sum(i.get("gzBytes") or i["bytes"] for i in items)
        print(f"  fonts/{fam}  {len(items)} 个字重，传输 {total/1024/1024:.2f} MB（gzip）")

    # ---- 前端静态资源 + 门面
    for rel in STATIC_FILES + ROOT_FILES:
        src = os.path.join(ROOT, rel)
        if os.path.isfile(src):
            copy_file(src, os.path.join(out, rel))
    # index.html 复制到根
    copy_file(os.path.join(ROOT, "static", "index.html"), os.path.join(out, "index.html"))
    print("  前端与门面已复制")

    # ---- 完整源码（让仓库本身可读）
    for rel in EXTRA_FILES:
        src = os.path.join(ROOT, rel)
        if os.path.isfile(src):
            copy_file(src, os.path.join(out, rel))
    for d in EXTRA_DIRS:
        src = os.path.join(ROOT, d)
        if os.path.isdir(src):
            copy_tree(src, os.path.join(out, d))

    # ---- .nojekyll：不加的话 Jekyll 会忽略 assets/_builtin/ 这种下划线开头的目录
    open(os.path.join(out, ".nojekyll"), "w").close()

    # ---- 清单
    bases: list[str] = []
    if not args.no_cdn:
        # jsdelivr 能从 GitHub 仓库直接取文件，实测比 Pages 本身快得多（大文件尤其明显）。
        # 放在第一位，失败再回落站点自身的相对路径。
        bases.append(f"https://cdn.jsdelivr.net/gh/{args.repo}@{args.branch}/")
    bases.append("./")

    manifest = {
        "generatedBy": "tools/build_site.py",
        "repo": args.repo,
        "bases": bases,
        "core": sorted(core_files),
        "fonts": fonts_manifest,
    }
    with open(os.path.join(out, "py-manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"  py-manifest.json：core {len(core_files)} 项，"
          f"字体 {sum(len(v) for v in fonts_manifest.values())} 项，bases {bases}")

    # ---- 体检
    total = 0
    n = 0
    for dirpath, dirnames, filenames in os.walk(out):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            total += os.path.getsize(os.path.join(dirpath, fn))
            n += 1
    print(f"\n共 {n} 个文件 / {total/1024/1024:.2f} MB")
    for must in ("index.html", ".nojekyll", "py-manifest.json", "api_facade.py",
                 "core/renderer.py", "fonts/sans-regular.ttf"):
        p = os.path.join(out, must)
        print(f"  {'✓' if os.path.exists(p) else '✗'} {must}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
