"""案例库 BM25 检索。加载 kb/cases/*.md，按 finding 的 excerpt+category 检索相似案例。"""
from pathlib import Path
import re
import jieba
from rank_bm25 import BM25Okapi

_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)


def _parse_case(path: Path):
    raw = path.read_text(encoding="utf-8")
    m = _FRONT.match(raw)
    meta = {}
    body = raw
    if m:
        import yaml
        meta = yaml.safe_load(m.group(1)) or {}
        body = m.group(2)
    title = (body.lstrip("#").split("\n", 1)[0]).strip() or path.stem
    keywords = meta.get("关键词", [])
    if isinstance(keywords, str):
        keywords = [keywords]
    return {
        "id": meta.get("id", path.stem),
        "file": path.name,
        "title": title,
        "行业": meta.get("行业", ""),
        "违规类型": meta.get("违规类型", ""),
        "违反条款": meta.get("违反条款", ""),
        "处罚": meta.get("处罚", ""),
        "keywords": keywords,
        "body": body,
    }


def _tokenize(text: str) -> list:
    return [t for t in jieba.lcut(text) if t.strip()]


class CaseIndex:
    def __init__(self, kb_dir: str):
        cdir = Path(kb_dir) / "cases"
        self.cases = []
        corpus = []
        for p in sorted(cdir.glob("*.md")):
            c = _parse_case(p)
            doc = " ".join(c["keywords"]) + " " + c["title"] + " " + c["body"]
            self.cases.append(c)
            corpus.append(_tokenize(doc))
        self.bm25 = BM25Okapi(corpus) if corpus else None

    def retrieve(self, query: str, topk: int = 5) -> list:
        if not self.bm25:
            return []
        scores = self.bm25.get_scores(_tokenize(query))
        ranked = sorted(zip(self.cases, scores), key=lambda x: x[1], reverse=True)
        out = []
        for c, s in ranked[:topk]:
            if s <= 0:
                break
            out.append({
                "id": c["id"],
                "title": c["title"],
                "行业": c["行业"],
                "违反条款": c["违反条款"],
                "处罚": c["处罚"],
                "score": round(float(s), 3),
            })
        return out
