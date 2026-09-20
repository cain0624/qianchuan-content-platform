"""加载规则 YAML，对文本做硬匹配，产出 finding 列表。"""
from pathlib import Path
import yaml

KB_RULES_DIR = "kb/rules"


def load_rules(kb_dir: str):
    base = Path(kb_dir) / "rules"
    extreme = yaml.safe_load((base / "极限词.yml").read_text(encoding="utf-8")) or {}
    clauses = yaml.safe_load((base / "广告法条款.yml").read_text(encoding="utf-8")) or {}
    internet = yaml.safe_load((base / "互联网广告管理办法.yml").read_text(encoding="utf-8")) or {}
    cats = yaml.safe_load((base / "敏感类目.yml").read_text(encoding="utf-8")) or {}
    return {
        "extreme": extreme.get("words", []),
        "clauses": clauses.get("clauses", []),
        "internet": internet.get("rules", []),
        "categories": cats.get("categories", []),
    }


def _find_context(text: str, term: str, span: int = 30) -> str:
    i = text.find(term)
    if i < 0:
        return term
    s = max(0, i - span)
    e = min(len(text), i + len(term) + span)
    snippet = text[s:e].replace("\n", " ").strip()
    return f"…{snippet}…"


def match_text(text: str, source: str, rules: dict) -> list:
    """对一段文本跑全部规则，返回 finding 列表。"""
    findings = []
    seen = set()  # (clause, excerpt) 去重

    def add(severity, category, clause, excerpt, explanation, suggestion=""):
        key = (clause, excerpt)
        if key in seen:
            return
        seen.add(key)
        findings.append({
            "severity": severity,
            "category": category,
            "clause": clause,
            "source_file": source,
            "source_type": "text",
            "excerpt": excerpt,
            "explanation": explanation,
            "suggestion": suggestion,
        })

    if not text:
        return findings

    # 极限词
    for w in rules["extreme"]:
        term = w["term"]
        if term in text:
            add(
                w.get("severity", "high"),
                "极限词/绝对化用语",
                w.get("clause", "广告法第9条"),
                _find_context(text, term),
                f"命中极限词'{term}'：{w.get('note', '')}",
                suggestion=f"删除或改写'{term}'，如需表述效果改用具体数据并标注来源",
            )

    # 广告法条款触发词
    for c in rules["clauses"]:
        for kw in c.get("触发词", []):
            if kw in text:
                add(
                    c.get("severity", "medium"),
                    c.get("类目", "广告法"),
                    c.get("条款号", ""),
                    _find_context(text, kw),
                    f"触发《{c.get('条款号', '')}》相关表述'{kw}'：{c.get('禁止行为', '')}",
                    suggestion=f"核对是否符合{c.get('条款号','')}要求，必要时删除该表述",
                )

    # 敏感类目
    for cat in rules["categories"]:
        hit = [t for t in cat.get("触发词", []) if t in text]
        if hit:
            redlines = "；".join(cat.get("红线", []))
            add(
                cat.get("severity", "medium"),
                f"敏感类目-{cat['类目']}",
                "敏感类目特别规定",
                _find_context(text, hit[0]),
                f"疑似{cat['类目']}广告，触发词：{hit[:3]}。红线：{redlines}",
                suggestion=f"按{cat['类目']}类目特别规定逐项核对（详见红线）",
            )

    # 互联网广告管理办法
    for r in rules["internet"]:
        for kw in r.get("触发词", []):
            if kw in text:
                add(
                    r.get("severity", "medium"),
                    "互联网广告",
                    r.get("要点", "互联网广告管理办法"),
                    _find_context(text, kw),
                    f"触发互联网广告规则'{r.get('要点','')}'：{r.get('说明','')}",
                    suggestion=f"按'{r.get('要点','')}'要求整改",
                )

    return findings
