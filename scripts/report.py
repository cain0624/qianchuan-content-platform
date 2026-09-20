"""合并 finding，产出 JSON + Markdown + HTML 报告。"""
import html as _html
import json
from datetime import datetime

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
SEV_LABEL = {"high": "高风险(必改)", "medium": "中风险(建议)", "low": "低风险(提示)"}
SEV_CLASS = {"high": "h", "medium": "m", "low": "l"}

TPL = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  :root{{--high:#DC2626;--high-bg:#FEF2F2;--mid:#D97706;--mid-bg:#FFFBEB;--low:#0891B2;--low-bg:#ECFEFF;}}
  *{{box-sizing:border-box;}}
  body{{margin:0;background:#F1F5F9;font-family:-apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
       color:#0F172A;line-height:1.7;}}
  .wrap{{max-width:920px;margin:0 auto;padding:32px 20px 80px;}}
  .card{{background:#fff;border-radius:16px;box-shadow:0 1px 3px rgba(15,23,42,.08),0 8px 24px rgba(15,23,42,.06);
        padding:36px 40px;}}
  h1{{font-size:28px;margin:0 0 20px;padding-bottom:16px;border-bottom:2px solid #E2E8F0;}}
  h2{{font-size:20px;margin:32px 0 16px;display:flex;align-items:center;gap:10px;}}
  h2.bad::before,h2.warn::before,h2.info::before{{content:"";width:6px;height:22px;border-radius:3px;display:inline-block;}}
  h2.bad::before{{background:var(--high);}}
  h2.warn::before{{background:var(--mid);}}
  h2.info::before{{background:var(--low);}}
  h3{{font-size:16px;margin:22px 0 0;padding:10px 14px;border-radius:8px 8px 0 0;font-family:ui-monospace,Menlo,monospace;}}
  h3.h{{background:var(--high-bg);color:var(--high);border-left:4px solid var(--high);}}
  h3.m{{background:var(--mid-bg);color:var(--mid);border-left:4px solid var(--mid);}}
  h3.l{{background:var(--low-bg);color:var(--low);border-left:4px solid var(--low);}}
  ul{{margin:0 0 8px;padding:14px 18px 14px 34px;background:#F8FAFC;border-radius:0 0 8px 8px;
      border:1px solid #E2E8F0;border-top:none;}}
  li{{margin:6px 0;}}
  li strong{{color:#334155;}}
  li.sug{{list-style:none;background:#FFF8E1;border-left:4px solid #FDB022;border-radius:8px;
         padding:10px 14px;margin:8px 0 8px -6px;font-weight:600;color:#7C4A03;}}
  li.sug strong{{color:#B45309;}}
  code{{background:#E2E8F0;padding:2px 6px;border-radius:4px;font-size:13px;font-family:ui-monospace,Menlo,monospace;}}
  .meta{{background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:16px 20px;margin-bottom:24px;}}
  .meta ul{{background:none;border:none;padding:0 0 0 20px;border-radius:0;}}
  .verdict{{display:inline-block;padding:4px 14px;border-radius:999px;font-weight:700;font-size:15px;}}
  .verdict.bad{{background:var(--high);color:#fff;}}
  .verdict.ok{{background:#16A34A;color:#fff;}}
  hr{{border:none;border-top:1px solid #E2E8F0;margin:32px 0 16px;}}
  em{{color:#64748B;font-size:13px;}}
  .nav{{position:fixed;top:16px;right:16px;background:#fff;border-radius:10px;box-shadow:0 2px 8px rgba(15,23,42,.12);
       padding:8px;font-size:13px;max-height:80vh;overflow:auto;}}
  .nav a{{display:block;color:#2563EB;text-decoration:none;padding:4px 10px;border-radius:6px;max-width:220px;
         overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
  .nav a:hover{{background:#EFF6FF;}}
  .nav .cap{{padding:4px 10px;color:#64748B;font-weight:600;}}
  @media(max-width:1300px){{.nav{{display:none;}}}}
</style></head><body><div class="wrap"><div class="card">
{body}
</div></div>
{nav}
</body></html>"""


def _esc(s) -> str:
    return _html.escape(str(s or ""))


def _dedupe(findings: list) -> list:
    seen = {}
    for f in findings:
        key = (f.get("clause", ""), f.get("excerpt", ""), f.get("source_file", ""))
        if key not in seen:
            seen[key] = f
    return list(seen.values())


def _assign_ids(findings: list) -> list:
    for i, f in enumerate(findings, 1):
        f["id"] = f"F-{i:03d}"
    return findings


def build(findings: list, meta: dict) -> tuple:
    """返回 (json_str, md_str, html_str)。"""
    findings = _assign_ids(_dedupe(findings))
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.get("severity", "low"), 2), f.get("id", "")))

    counts = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        counts[f.get("severity", "low")] = counts.get(f.get("severity", "low"), 0) + 1

    has_material = any(meta.get("files", {}).get(k, 0) for k in ("text", "image", "video"))
    passed = counts["high"] == 0 and counts["medium"] == 0 and has_material
    verdict = "通过" if passed else ("需整改" if findings else "未审核")

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "input": meta.get("input", ""),
        "summary": {
            "total_findings": len(findings),
            "by_severity": counts,
            "verdict": verdict,
            "vl_enabled": meta.get("vl_enabled", False),
            "files": meta.get("files", {}),
        },
        "findings": findings,
    }
    json_str = json.dumps(report, ensure_ascii=False, indent=2)
    md_str = _to_markdown(report, findings, counts, meta)
    html_str = _to_html(report, findings, counts, meta)
    return json_str, md_str, html_str


def _to_markdown(report, findings, counts, meta) -> str:
    has_material = any(meta.get("files", {}).get(k, 0) for k in ("text", "image", "video"))
    lines = []
    lines.append("# 广告法审核报告\n")
    lines.append(f"- 审核时间：{report['generated_at']}")
    lines.append(f"- 输入路径：`{report['input']}`")
    lines.append(f"- 视觉审核：{'已启用（视觉大模型）' if meta.get('vl_enabled') else '未启用（缺 API key，图片/视频未审核）'}")
    files = meta.get("files", {})
    lines.append(f"- 物料：文本 {files.get('text',0)} / 图片 {files.get('image',0)} / 视频 {files.get('video',0)} / 其他 {files.get('other',0)}")
    verdict = report["summary"]["verdict"]
    if not has_material:
        lines.append(f"- 结论：**{verdict}**（该路径下没有可审核的物料）\n")
    else:
        lines.append(f"- 结论：**{verdict}** ｜ 高风险 {counts['high']} / 中风险 {counts['medium']} / 低风险 {counts['low']}\n")

    if not has_material:
        lines.append("该路径下没有找到可审核的物料，本报告不构成审核结论。\n")
    elif not findings:
        lines.append("未发现明显违规。仍建议人工复核一遍。\n")
    else:
        for sev in ("high", "medium", "low"):
            group = [f for f in findings if f.get("severity") == sev]
            if not group:
                continue
            lines.append(f"\n## {SEV_LABEL[sev]}（{len(group)} 条）\n")
            for f in group:
                lines.append(f"### {f['id']} ｜ {f.get('category','')} ｜ {f.get('clause','')}")
                lines.append(f"- 物料：`{f.get('source_file','')}`（{f.get('source_type','')}）{('　位置：'+f['location']) if f.get('location') and f['location']!='—' else ''}")
                if f.get("excerpt"):
                    lines.append(f"- 原文/画面：{f['excerpt']}")
                lines.append(f"- 说明：{f.get('explanation','')}")
                if f.get("similar_cases"):
                    cs = "；".join(f"{c['id']}《{c['title']}》（{c.get('违反条款','')}）" for c in f["similar_cases"])
                    lines.append(f"- 相似案例：{cs}")
                if f.get("suggestion"):
                    lines.append(f"- 修改建议：{f['suggestion']}")
                lines.append("")

    lines.append("\n---\n*本报告由 ad-review 种子知识库自动生成，高风险项务必过法务复核；引用的案例为种子库（标 #种子库/待补），请用真实案例补充。*")
    return "\n".join(lines)


def _to_html_from_dict(d: dict, meta: dict) -> str:
    """从已保存的报告 JSON 重建 HTML（供 preview.py 转换历史报告）。"""
    findings = d.get("findings", [])
    counts = d.get("summary", {}).get("by_severity", {"high": 0, "medium": 0, "low": 0})
    return _to_html(d, findings, counts, meta)


def _to_html(report, findings, counts, meta) -> str:
    has_material = any(meta.get("files", {}).get(k, 0) for k in ("text", "image", "video"))
    files = meta.get("files", {})
    verdict = report["summary"]["verdict"]
    vcls = "ok" if verdict == "通过" else "bad"

    out = []
    out.append("<h1>千川内容平台 · 审核报告</h1>")
    out.append('<ul class="meta">')
    out.append(f"<li>审核时间：{_esc(report['generated_at'])}</li>")
    out.append(f"<li>输入路径：<code>{_esc(report['input'])}</code></li>")
    out.append(f"<li>视觉审核：{'已启用（视觉大模型）' if meta.get('vl_enabled') else '未启用（缺 API key，图片/视频未审核）'}</li>")
    out.append(f"<li>物料：文本 {files.get('text',0)} / 图片 {files.get('image',0)} / 视频 {files.get('video',0)} / 其他 {files.get('other',0)}</li>")
    out.append(f"<li>结论：<span class='verdict {vcls}'>{_esc(verdict)}</span>"
               + (f"　高风险 {counts['high']} / 中风险 {counts['medium']} / 低风险 {counts['low']}" if has_material else "　（该路径下没有可审核的物料）")
               + "</li>")
    out.append("</ul>")

    nav_items = []
    if not has_material:
        out.append("<p>该路径下没有找到可审核的物料，本报告不构成审核结论。</p>")
    elif not findings:
        out.append("<p>未发现明显违规。仍建议人工复核一遍。</p>")
    else:
        for sev in ("high", "medium", "low"):
            group = [f for f in findings if f.get("severity") == sev]
            if not group:
                continue
            cls = SEV_CLASS[sev]
            out.append(f"<h2 class=\"{'bad' if sev=='high' else ('warn' if sev=='medium' else 'info')}\">{SEV_LABEL[sev]}（{len(group)} 条）</h2>")
            for f in group:
                loc = f"　位置：{_esc(f['location'])}" if f.get("location") and f["location"] != "—" else ""
                out.append(f"<h3 id=\"{_esc(f['id'])}\" class=\"{cls}\">{_esc(f['id'])} ｜ {_esc(f.get('category',''))} ｜ {_esc(f.get('clause',''))}</h3>")
                out.append("<ul>")
                out.append(f"<li><strong>物料：</strong><code>{_esc(f.get('source_file',''))}</code>（{_esc(f.get('source_type',''))}）{loc}</li>")
                if f.get("excerpt"):
                    out.append(f"<li><strong>原文/画面：</strong>{_esc(f['excerpt'])}</li>")
                out.append(f"<li><strong>说明：</strong>{_esc(f.get('explanation',''))}</li>")
                if f.get("similar_cases"):
                    cs = "；".join(
                        f"{_esc(c['id'])}《{_esc(c['title'])}》（{_esc(c.get('违反条款',''))}）"
                        for c in f["similar_cases"])
                    out.append(f"<li><strong>相似案例：</strong>{cs}</li>")
                if f.get("suggestion"):
                    out.append(f"<li class=\"sug\"><strong>修改建议：</strong>{_esc(f['suggestion'])}</li>")
                out.append("</ul>")
                nav_items.append((f["id"], f.get("category", "")))

    out.append("<hr>")
    out.append("<em>本报告由 ad-review 种子知识库自动生成，高风险项务必过法务复核；引用的案例为种子库（标 #种子库/待补），请用真实案例补充。</em>")

    nav = ""
    if nav_items:
        links = "".join(
            f"<a href=\"#{_esc(fid)}\" title=\"{_esc(cat)}\">{_esc(fid)} {_esc(cat)}</a>"
            for fid, cat in nav_items)
        nav = f'<div class="nav"><div class="cap">{len(nav_items)} 条发现</div>{links}</div>'

    return TPL.format(title=f"千川内容平台 · 审核报告", body="\n".join(out), nav=nav)
