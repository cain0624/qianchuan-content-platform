"""视觉大模型审核（图/视频）。走 OpenAI 兼容端点（硅基流动/通义等），本地文件用 base64。"""
import base64
import json
import mimetypes
import os
import subprocess
import tempfile
from pathlib import Path

# 由 review.py 通过 configure() 注入
_BASE_URL = "https://api.siliconflow.cn/v1"
_API_KEY = ""


def configure(base_url: str, api_key: str):
    global _BASE_URL, _API_KEY
    _BASE_URL = base_url
    _API_KEY = api_key


def _client():
    from openai import OpenAI
    return OpenAI(api_key=_API_KEY, base_url=_BASE_URL), _API_KEY


SYSTEM_PROMPT = """你是广告法合规审核员，专门审核广告物料（海报/长图/视频帧）是否违反《中华人民共和国广告法》及关联法规。
请逐项检查画面文字与视觉内容，覆盖以下维度：
- 极限词/绝对化用语（最、第一、顶级、唯一、国家级、100% 等）
- 虚假或引人误解的表述（免费/限量/中奖/虚假功效等）
- 贬低竞争对手
- 引证数据失实或无来源
- 医疗/药品/器械功效保证（包治/根治/治愈率/有效率）
- 保健食品/普通食品宣称疗效
- 金融理财收益保证（保本/保收益/稳赚/零风险）
- 教育培训效果保证（包过/保过/包就业）
- 酒类诱导饮酒/宣称功效
- 房地产升值/回报承诺、以时间表示距离
- 招商加盟收益保证
- 未成年保护/劝诱购买
- 低俗/性暗示/不良导向
- 误导性视觉（如夸大前后对比图、PS效果、虚假截图）
- AI 生成内容是否携带标识（无标识即风险）

仅输出一个 JSON 数组，不要任何额外文字。每个元素：
{"severity":"high|medium|low","category":"类目","clause":"条款","excerpt":"画面文字或画面描述","location":"画面位置","explanation":"为何违规","suggestion":"具体修改建议"}
若无明显违规，返回空数组 []。"""


def _data_url(path: Path) -> str:
    """统一转成 JPEG data URL。部分提供商对 PNG/超大图支持不佳，JPEG 兼容性最好。"""
    from PIL import Image
    import io
    img = Image.open(path).convert("RGB")
    # 限制长边到 1600，控制请求体大小
    max_side = 1600
    if max(img.size) > max_side:
        ratio = max_side / max(img.size)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    data = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{data}"


def _call_vl(content_parts: list, model: str) -> list:
    """调用 VL，解析 JSON findings。异常向上抛，由调用方兜底成提示 finding。"""
    client, key = _client()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content_parts},
        ],
        temperature=0.1,
    )
    text = resp.choices[0].message.content.strip()
    return _parse_json_findings(text)


def _parse_json_findings(text: str) -> list:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if "```" in text[3:] else text
        text = text.lstrip("json").strip("`\n ")
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "findings" in data:
            return data["findings"]
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    return []


def _ffmpeg_available() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


def _extract_frames(video: Path, n: int = 6) -> list:
    """用 ffmpeg 均匀抽取 n 帧到临时目录，返回图片路径列表。"""
    if not _ffmpeg_available():
        return []
    tmp = Path(tempfile.mkdtemp(prefix="adrev_"))
    # 先取时长
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, check=True,
        )
        duration = float(probe.stdout.strip())
    except Exception:
        return []
    pts = []
    for i in range(n):
        t = duration * (i + 0.5) / n
        out = tmp / f"frame_{i:02d}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-q:v", "3", str(out)],
            capture_output=True, check=False,
        )
        if out.exists():
            pts.append(out)
    return pts


def _note(path: Path, kind: str, message: str) -> dict:
    return {
        "severity": "low",
        "category": f"{kind}审核",
        "clause": "—",
        "source_file": path.name,
        "source_type": kind,
        "excerpt": f"（{kind}未完成审核）",
        "location": "—",
        "explanation": message,
        "suggestion": "修复后重跑以完成审核。",
    }


def review_image(path: Path, model: str, context: str = "") -> list:
    parts = [
        {"type": "text", "text": f"参考规则与相似案例摘要：\n{context}\n\n请审核这张广告图片。"},
        {"type": "image_url", "image_url": {"url": _data_url(path)}},
    ]
    try:
        findings = _call_vl(parts, model)
    except Exception as e:
        return [_note(path, "image", f"图片审核调用失败：{type(e).__name__}: {e}")]
    for f in findings:
        f.setdefault("source_file", path.name)
        f.setdefault("source_type", "image")
        f.setdefault("excerpt", "")
        f.setdefault("location", "")
        f.setdefault("severity", "medium")
        f.setdefault("category", "")
        f.setdefault("clause", "")
        f.setdefault("explanation", "")
        f.setdefault("suggestion", "")
    return findings


def review_video(path: Path, model: str, context: str = "") -> list:
    frames = _extract_frames(path, 6)
    if not frames:
        return [{
            "severity": "low",
            "category": "视频审核",
            "clause": "—",
            "source_file": path.name,
            "source_type": "video",
            "excerpt": "（视频未审核）",
            "location": "—",
            "explanation": "未安装 ffmpeg 或抽帧失败，视频内容未审核。",
            "suggestion": "安装 ffmpeg（brew install ffmpeg）后重跑，以审核视频画面。",
        }]
    parts = [{"type": "text", "text": f"参考规则与相似案例摘要：\n{context}\n\n以下是该视频均匀抽取的6帧，请据此审核。"}]
    for f in frames:
        parts.append({"type": "image_url", "image_url": {"url": _data_url(f)}})
    try:
        findings = _call_vl(parts, model)
    except Exception as e:
        return [_note(path, "video", f"视频审核调用失败：{type(e).__name__}: {e}")]
    for f in findings:
        f.setdefault("source_file", path.name)
        f.setdefault("source_type", "video")
        f.setdefault("excerpt", "")
        f.setdefault("location", f"帧序列")
        f.setdefault("severity", "medium")
        f.setdefault("category", "")
        f.setdefault("clause", "")
        f.setdefault("explanation", "")
        f.setdefault("suggestion", "")
    return findings
