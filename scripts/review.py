#!/usr/bin/env python3
"""广告法物料审核主流水线。

用法（CLI）：
    uv run python scripts/review.py --input <文件夹或文件> [--kb kb] [--out reports]

也可作为库调用（Web 端用）：
    from review import run_review
    run_review(input_path, progress=callback) -> {"json","md","html","base","summary"}
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml

SKILL_ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(SKILL_ROOT / ".env")
    load_dotenv(Path("config.env"))
except Exception:
    pass

from extract_text import walk_input
from rules_match import load_rules, match_text
from case_retrieve import CaseIndex
import vl_review
from vl_review import review_image, review_video
from report import build


def load_config() -> dict:
    cfg = {}
    p = SKILL_ROOT / "config.yml"
    if p.exists():
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return cfg


def run_review(input_path: str, kb: str = None, out: str = None,
               no_vl: bool = False, label: str = None, progress=None) -> dict:
    """跑完整审核流水线，返回报告路径与摘要。progress(stage, message) 用于进度上报。"""

    def p(stage, msg=""):
        if progress:
            try:
                progress(stage, msg)
            except Exception:
                pass

    kb = kb or str(SKILL_ROOT / "kb")
    out = out or str(SKILL_ROOT / "reports")
    cfg = load_config()
    model = cfg.get("VL_MODEL", "Qwen/Qwen3-VL-30B-A3B-Instruct")
    base_url = cfg.get("VL_BASE_URL", "https://api.siliconflow.cn/v1")
    key_env = cfg.get("VL_API_KEY_ENV", "SILICONFLOW_API_KEY")
    case_topk = int(cfg.get("case_topk", 5))
    api_key = os.environ.get(key_env, "") or os.environ.get("DASHSCOPE_API_KEY", "")
    vl_enabled = bool(api_key) and not no_vl
    vl_review.configure(base_url, api_key)

    input_path_obj = Path(input_path).expanduser()
    if not input_path_obj.exists():
        raise FileNotFoundError(f"输入路径不存在：{input_path_obj.resolve()}")

    p("load_kb", "加载规则与案例库…")
    rules = load_rules(kb)
    case_idx = CaseIndex(kb)

    p("scan", f"扫描物料 {input_path}…")
    inv = walk_input(str(input_path_obj))
    files_meta = {"text": len(inv["text"]), "image": len(inv["image"]),
                  "video": len(inv["video"]), "other": len(inv["other"])}
    auditable = files_meta["text"] + files_meta["image"] + files_meta["video"]
    if auditable == 0:
        other_exts = sorted({f.suffix.lower() or "(无扩展名)" for f in inv["other"]})
        msg = "该路径下没有可审核的物料"
        if other_exts:
            msg += f"；发现 {len(inv['other'])} 个不支持的文件（{', '.join(other_exts[:5])}）"
        raise ValueError(msg + "。支持：图片 jpg/jpeg/png/webp，视频 mp4/mov/webm，文本 txt/md/docx/pdf/xlsx/pptx")

    p("rules", f"文本规则匹配（文本 {files_meta['text']} 个）…")
    findings = []
    for path, _kind, text in inv["text"]:
        findings.extend(match_text(text, path.name, rules))

    p("cases", f"相似案例检索（{len(findings)} 条发现）…")
    for f in findings:
        q = f"{f.get('category','')} {f.get('clause','')} {f.get('excerpt','')}"
        f["similar_cases"] = case_idx.retrieve(q, topk=case_topk)

    if vl_enabled:
        total_media = files_meta["image"] + files_meta["video"]
        done_media = 0
        p("vl", f"视觉模型审核（共 {total_media} 个图/视频）…")
        context = _build_context(findings)
        for img in inv["image"]:
            done_media += 1
            p("vl", f"视觉模型审核 {done_media}/{total_media}：{img.name}")
            vl_findings = review_image(img, model, context)
            for vf in vl_findings:
                q = f"{vf.get('category','')} {vf.get('clause','')} {vf.get('excerpt','')}"
                vf["similar_cases"] = case_idx.retrieve(q, topk=case_topk)
            findings.extend(vl_findings)
        for vid in inv["video"]:
            done_media += 1
            p("vl", f"视觉模型审核 {done_media}/{total_media}：{vid.name}")
            vl_findings = review_video(vid, model, context)
            for vf in vl_findings:
                q = f"{vf.get('category','')} {vf.get('clause','')} {vf.get('excerpt','')}"
                vf["similar_cases"] = case_idx.retrieve(q, topk=case_topk)
            findings.extend(vl_findings)
    else:
        p("vl", "跳过视觉模型审核（未配置 API key）")
        for img in inv["image"]:
            findings.append({
                "severity": "low", "category": "图片审核", "clause": "—",
                "source_file": img.name, "source_type": "image",
                "excerpt": "（图片未审核）", "location": "—",
                "explanation": "未配置视觉模型 API key，图片画面未审核。",
                "suggestion": "在 .env 配置 key 后重跑。", "similar_cases": [],
            })

    p("report", "生成报告…")
    meta = {"input": str(input_path_obj), "vl_enabled": vl_enabled, "files": files_meta}
    json_str, md_str, html_str = build(findings, meta)

    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = label or input_path_obj.name
    safe_name = "".join(c for c in name if c not in '/\\:*?"<>|').strip() or "物料"
    base = out_dir / f"{ts}-{safe_name}"
    base.with_suffix(".json").write_text(json_str, encoding="utf-8")
    base.with_suffix(".md").write_text(md_str, encoding="utf-8")
    base.with_suffix(".html").write_text(html_str, encoding="utf-8")

    summary = {
        "verdict": json.loads(json_str)["summary"]["verdict"],
        "by_severity": json.loads(json_str)["summary"]["by_severity"],
        "total_findings": json.loads(json_str)["summary"]["total_findings"],
        "files": files_meta,
    }
    p("done", f"完成：{summary['verdict']}")
    return {"json": str(base.with_suffix(".json")), "md": str(base.with_suffix(".md")),
            "html": str(base.with_suffix(".html")), "base": str(base), "summary": summary}


def _build_context(findings: list, max_chars: int = 1200) -> str:
    parts = []
    for f in findings[:15]:
        parts.append(f"- [{f.get('severity','')}] {f.get('clause','')}：{f.get('excerpt','')}")
    txt = "\n".join(parts)
    if len(txt) > max_chars:
        txt = txt[:max_chars] + "…"
    return txt


def main():
    ap = argparse.ArgumentParser(description="广告法物料审核")
    ap.add_argument("--input", required=True, help="待审物料文件夹或单个文件路径")
    ap.add_argument("--kb", default=None, help="知识库目录（默认 <skill>/kb）")
    ap.add_argument("--out", default=None, help="报告输出目录（默认 <skill>/reports）")
    ap.add_argument("--no-vl", action="store_true", help="跳过图片/视频视觉模型审核")
    args = ap.parse_args()
    try:
        r = run_review(args.input, kb=args.kb, out=args.out, no_vl=args.no_vl)
    except (FileNotFoundError, ValueError) as e:
        print(f"\n错误：{e}")
        sys.exit(2)
    s = r["summary"]
    print(f"\n结论：{s['verdict']} ｜ 高风险 {s['by_severity']['high']} / "
          f"中风险 {s['by_severity']['medium']} / 低风险 {s['by_severity']['low']}")
    print(f"\n报告已生成：")
    print(f"  {r['html']}  ← 浏览器直接打开预览")
    print(f"  {r['md']}")
    print(f"  {r['json']}")
    print(f"\nREPORT_HTML={r['html']}")
    print(f"REPORT_MD={r['md']}")


if __name__ == "__main__":
    main()
