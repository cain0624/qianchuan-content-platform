#!/usr/bin/env python3
"""报告预览工具。

用法：
    python scripts/preview.py               # 把 reports/ 下所有 JSON 报告转成 HTML，并生成索引页 index.html
    python scripts/preview.py 报告.json     # 只转指定报告
    python scripts/preview.py 报告.md       # 旧版 Markdown 报告也能转
"""
import html as _html
import json
import sys
from pathlib import Path

from report import TPL, _esc, _to_html_from_dict


def json_to_html(jf: Path, out: Path):
    d = json.loads(jf.read_text(encoding="utf-8"))
    meta = {
        "input": d.get("input", ""),
        "vl_enabled": d.get("summary", {}).get("vl_enabled", False),
        "files": d.get("summary", {}).get("files", {}),
    }
    html_str = _to_html_from_dict(d, meta)
    out.write_text(html_str, encoding="utf-8")
    return out


def _md_to_html(md_path: Path, out: Path):
    import markdown
    md_text = md_path.read_text(encoding="utf-8")
    md = markdown.Markdown(extensions=["tables", "sane_lists"])
    body = md.convert(md_text)
    out.write_text(TPL.format(title=md_path.stem, body=body, nav=""), encoding="utf-8")
    return out


def build_index(reports_dir: Path):
    """扫描所有 JSON 报告，确保每份都有 HTML，并生成索引页。"""
    jfs = sorted(reports_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    cards = []
    for jf in jfs:
        html_file = jf.with_suffix(".html")
        if not html_file.exists():
            try:
                json_to_html(jf, html_file)
            except Exception as e:
                print(f"跳过 {jf.name}（转换失败：{e}）")
                continue
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        s = d.get("summary", {})
        files = s.get("files", {})
        has_material = any(files.get(k, 0) for k in ("text", "image", "video"))
        counts = s.get("by_severity", {})
        verdict = s.get("verdict", "")
        vcls = "ok" if verdict == "通过" else "bad"
        if not has_material:
            note = '<span style="color:#94A3B8;">⚠ 无物料扫描（该报告不构成审核结论）</span>'
        else:
            note = f"高风险 {counts.get('high',0)} / 中风险 {counts.get('medium',0)} / 低风险 {counts.get('low',0)}"
        cards.append(f"""
  <a class="rep" href="{_html.escape(html_file.name)}">
    <div class="rep-head"><b>{_html.escape(jf.with_suffix('').name)}</b>
      <span class="verdict {vcls}">{_html.escape(verdict)}</span></div>
    <div class="rep-meta">{_html.escape(d.get('generated_at',''))} ｜ {_html.escape(d.get('input',''))}</div>
    <div class="rep-meta">{note}</div>
  </a>""")

    index = TPL.format(
        title="千川内容平台 · 报告索引",
        body=f"""<h1>千川内容平台 · 审核报告索引</h1>
<p>共 {len(cards)} 份报告，点击卡片查看详情。</p>
{''.join(cards)}
<hr><em>重新审核后回到本页刷新即可看到新报告；卡片按时间倒序排列。</em>""",
        nav="")
    (reports_dir / "index.html").write_text(index, encoding="utf-8")
    return reports_dir / "index.html"


if __name__ == "__main__":
    reports_dir = Path("reports")
    args = sys.argv[1:]
    outputs = []
    if not args:
        outputs.append(build_index(reports_dir))
    else:
        for a in args:
            p = Path(a)
            out = p.with_suffix(".html")
            if p.suffix == ".json":
                outputs.append(json_to_html(p, out))
            else:
                outputs.append(_md_to_html(p, out))
    for o in outputs:
        print(f"已生成预览：{o}")
