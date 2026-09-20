#!/usr/bin/env python3
"""把 macOS 系统字体子集化，产出一份体积可控、可随站点分发的字体包。

为什么需要它：渲染器原来直接引用 /System/Library/Fonts 下的系统字体
（Hiragino Sans GB.ttc 23.5MB、Songti.ttc 66.9MB）。这两个文件线上不存在，
原样搬进仓库也不可能——国内访问 GitHub Pages 实测只有几十 KB/s，
70MB 的字体要下十几分钟。

做法：按「字符白名单」把字形裁掉，只留实际可能出现在封面上的字。
白名单 = ASCII + GB2312 全部汉字/符号 + 项目数据里出现过的所有字符。
裁完的字形与原字体逐字节同源，因此**本地与线上渲染完全一致**
（前提是本地也改用这份子集，见 --install-local）。

用法：
    python tools/subset_fonts.py --charset gb2312 --out fonts/
    python tools/subset_fonts.py --charset level1 --out fonts/     # 更小，覆盖 99.7%
    python tools/subset_fonts.py --report                          # 只报告体积
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 字体族 → 源文件 / 字重所在的 font-number / 输出名
# font-number 与 data/templates.json 里 _spec.typography.fontFamilies 的
# regular/bold 索引一致，改这里时必须同步改那份数据。
FONT_SOURCES = {
    "sans": {
        "src": "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "out": "sans",
        "weights": {"regular": 0, "bold": 2},
        "label": "现代黑体",
    },
    "serif": {
        "src": "/System/Library/Fonts/Supplemental/Songti.ttc",
        "out": "serif",
        "weights": {"regular": 6, "bold": 1},
        "label": "宋体",
    },
}


def gb2312_chars() -> set[str]:
    """GB2312 全集：6763 个汉字 + 682 个符号区字符。"""
    out: set[str] = set()
    for b1 in range(0xA1, 0xF8):
        for b2 in range(0xA1, 0xFF):
            try:
                out.add(bytes([b1, b2]).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    return out


def level1_chars() -> set[str]:
    """GB2312 一级汉字（0xB0A1-0xD7F9，3755 字）+ 全角符号区。

    一级汉字按拼音序排列，是日常文本覆盖率的拐点：3755 字覆盖约 99.7%，
    再加二级汉字（多 3008 字）只能多覆盖 0.2%，却要多背一半体积。
    """
    out: set[str] = set()
    for b1 in range(0xA1, 0xB0):          # A1-A9 符号区
        for b2 in range(0xA1, 0xFF):
            try:
                out.add(bytes([b1, b2]).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    for b1 in range(0xB0, 0xD8):          # B0-D7 一级汉字
        for b2 in range(0xA1, 0xFF):
            try:
                out.add(bytes([b1, b2]).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    return out


def project_chars() -> set[str]:
    """项目里出现过的所有字符（模板固定文案、知识库、合规文案…）。

    这一步保证「模板自带的字」永远不会缺字——用户主题是变量，
    但模板文案是常量，常量必须 100% 覆盖。
    """
    out: set[str] = set()
    for rel in ("data/templates.json", "data/knowledge.json", "data/compliance.json"):
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8") as f:
            out.update(f.read())
    for rel in ("core/copywriter.py", "core/segmenter.py", "core/guard.py"):
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                out.update(f.read())
    return out


def build_charset(name: str) -> set[str]:
    chars: set[str] = set(chr(c) for c in range(0x20, 0x7F))   # ASCII 可打印
    chars |= set("\u3000\u00a0\u200b")                          # 空白
    if name == "level1":
        chars |= level1_chars()
    elif name == "gb2312":
        chars |= gb2312_chars()
    else:
        raise SystemExit(f"未知字集 {name}")
    # 兜底：项目常量文案里的每个字都在
    proj = project_chars()
    missing = proj - chars
    chars |= proj
    print(f"  字集 {name}：基础 {len(chars) - len(missing)} 字 + 项目专有 {len(missing)} 字 = {len(chars)} 字")
    if missing:
        sample = "".join(sorted(missing))[:60]
        print(f"  项目专有字符示例：{sample}")
    return chars


def run_subset(src: str, font_number: int, chars_file: str, out_path: str) -> int:
    """调用 pyftsubset 裁字形。返回输出字节数（0 表示失败）。"""
    cmd = [
        sys.executable, "-m", "fontTools.subset", src,
        f"--font-number={font_number}",
        f"--text-file={chars_file}",
        f"--output-file={out_path}",
        # 只裁字形，不碰 hinting。
        #
        # 一开始为了省体积加了 --no-hinting / --desubroutinize / --recalc-bounds，
        # 结果渲染出来的字和系统字体有 ~2.3% 的像素差、而且最大通道差到 255（整块反色）。
        # 逐字排查发现 advance width 完全一致、只有 bbox 差 ±1px —— 也就是排版没变，
        # 是**边缘光栅化**变了。真凶是 --no-hinting：Hiragino 是 CFF 字体，hint 操作符
        # 存在 charstring 里，剥掉之后 FreeType 的抗锯齿边缘就挪了位置。
        #
        # 去掉这三个开关后体积只涨 1.7%（2467K → 2510K），但差异直接归零。
        # 顺带一提 --desubroutinize 在这里是**反向**优化：它把 CFF 子程序展开，
        # 反而比保留子程序更大，纯亏。结论是别为了几十 KB 去动字形数据。
        "--drop-tables+=GSUB,GPOS,GDEF,BASE,vhea,vmtx,VORG,feat,morx,meta",
        "--layout-features=",
    ]
    # 注意：不要加 --no-subset-tables+=cmap。那样 cmap 会保留全部码点，
    # 而字形已被裁掉，编译时按字形名回查就会 KeyError（CJK 字体的字形名
    # 形如 cid22354，报错信息长得完全不像"参数写错了"）。
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    ✗ subset 失败：{r.stderr.strip()[:400]}")
        return 0
    return os.path.getsize(out_path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--charset", default="gb2312", choices=["gb2312", "level1"])
    ap.add_argument("--out", default=os.path.join(ROOT, "fonts"))
    ap.add_argument("--report", action="store_true", help="只打印当前字体包体积")
    ap.add_argument("--no-gzip", action="store_true", help="不产出 .ttf.gz（线上传输用）")
    args = ap.parse_args()

    if args.report:
        print("当前 fonts/ 内容：")
        total = 0
        for fn in sorted(os.listdir(args.out)) if os.path.isdir(args.out) else []:
            p = os.path.join(args.out, fn)
            if os.path.isfile(p):
                sz = os.path.getsize(p)
                total += sz
                print(f"  {fn:28s} {sz/1024:9.1f} KB")
        print(f"  {'合计':28s} {total/1024/1024:9.2f} MB")
        return 0

    os.makedirs(args.out, exist_ok=True)
    chars = build_charset(args.charset)
    chars_file = os.path.join(args.out, "_charset.txt")
    with open(chars_file, "w", encoding="utf-8") as f:
        f.write("".join(sorted(chars)))

    print(f"\n目标目录：{args.out}")
    grand = 0
    grand_gz = 0
    manifest = {}
    for fam, cfg in FONT_SOURCES.items():
        src = cfg["src"]
        if not os.path.exists(src):
            print(f"  ⚠ 跳过 {fam}：源字体不存在 {src}")
            continue
        src_mb = os.path.getsize(src) / 1e6
        print(f"\n[{fam}] {cfg['label']} 源 {os.path.basename(src)} {src_mb:.1f}MB")
        for weight, fnum in cfg["weights"].items():
            out_name = f"{cfg['out']}-{weight}.ttf"
            out_path = os.path.join(args.out, out_name)
            size = run_subset(src, fnum, chars_file, out_path)
            if not size:
                continue
            grand += size
            gz_size = 0
            if not args.no_gzip:
                # 预压缩一份给线上用：浏览器侧用 DecompressionStream('gzip') 解开，
                # 比让 GitHub Pages / CDN 猜 content-type 再压更可控。
                with open(out_path, "rb") as f:
                    raw = f.read()
                gz = gzip.compress(raw, 9)
                with open(out_path + ".gz", "wb") as f:
                    f.write(gz)
                gz_size = len(gz)
                grand_gz += gz_size
            print(f"    ✓ {out_name:22s} {size/1024:8.1f} KB"
                  + (f"  → gz {gz_size/1024:7.1f} KB" if gz_size else "")
                  + f"  (index={fnum}, 原字体 {size/os.path.getsize(src)*100:.1f}%)")
            manifest[f"{fam}.{weight}"] = {
                "file": out_name, "index": fnum,
                "source": os.path.basename(src), "sourceIndex": fnum,
                "bytes": size, "gzipBytes": gz_size,
            }
    os.remove(chars_file)
    total_src = sum(os.path.getsize(c["src"]) for c in FONT_SOURCES.values() if os.path.exists(c["src"]))
    print(f"\n合计 {grand/1024/1024:.2f} MB"
          + (f"（gzip 后 {grand_gz/1024/1024:.2f} MB）" if grand_gz else "")
          + f"，原始系统字体 {total_src/1e6:.1f} MB")

    mf = os.path.join(args.out, "fonts.json")
    with open(mf, "w", encoding="utf-8") as f:
        json.dump({"charset": args.charset, "chars": len(chars), "fonts": manifest},
                  f, ensure_ascii=False, indent=2)
    print(f"清单已写入 {mf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
