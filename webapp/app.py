"""广告法物料合规检测 - Web 服务。

启动（在 ~/.zcode/skills/ad-review 下）：
    .venv/bin/python -m uvicorn webapp.app:app --host 127.0.0.1 --port 8765
"""
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
UPLOADS_DIR = SKILL_ROOT / "uploads"
WEB_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(SCRIPTS_DIR))
from review import run_review  # noqa: E402

app = FastAPI(title="广告法物料合规检测")

JOBS = {}  # job_id -> {"status","stage","message","report_html","error","started_at","finished_at","summary"}
LOCK = threading.Lock()

ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".mp4", ".mov", ".webm",
                ".mkv", ".avi", ".txt", ".md", ".docx", ".pdf", ".xlsx", ".pptx"}


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


def _new_job() -> str:
    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    with LOCK:
        JOBS[job_id] = {"status": "uploading", "stage": "", "message": "",
                        "report_html": None, "error": None, "summary": None,
                        "label": None, "started_at": None, "finished_at": None}
    return job_id


def _job(job_id: str) -> dict:
    with LOCK:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return job


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(default=[]),
                 text: str = Form(default=""),
                 name: str = Form(default="")):
    job_id = _new_job()
    job_dir = UPLOADS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    rejected = []
    for f in files:
        fname = Path(f.filename or "").name  # 去掉路径部分
        ext = Path(fname).suffix.lower()
        if ext not in ALLOWED_EXTS:
            rejected.append(fname)
            continue
        dest = job_dir / fname
        i = 1
        while dest.exists():  # 重名加序号
            dest = job_dir / f"{Path(fname).stem}-{i}{ext}"
            i += 1
        with dest.open("wb") as w:
            shutil.copyfileobj(f.file, w)
        saved += 1
    if text.strip():
        (job_dir / "粘贴文案.txt").write_text(text, encoding="utf-8")
        saved += 1
    if saved == 0:
        msg = "没有可审核的文件"
        if rejected:
            msg += f"；以下文件类型不支持：{', '.join(rejected[:5])}"
        raise HTTPException(400, msg)
    with LOCK:
        JOBS[job_id]["label"] = (name.strip() or f"网页上传-{job_id[:15]}")
        JOBS[job_id]["status"] = "ready"
        JOBS[job_id]["message"] = f"已接收 {saved} 个物料"
    return {"job": job_id, "saved": saved, "rejected": rejected}


def _run_job(job_id: str, folder: Path, label: str):
    job = _job(job_id)

    def progress(stage, message):
        with LOCK:
            job["stage"] = stage
            job["message"] = message

    with LOCK:
        job["status"] = "running"
        job["started_at"] = time.time()
    try:
        r = run_review(str(folder), label=label, progress=progress)
        with LOCK:
            job["status"] = "done"
            job["report_html"] = r["html"]
            job["summary"] = r["summary"]
            job["finished_at"] = time.time()
            job["message"] = f"完成：{r['summary']['verdict']}"
    except Exception as e:
        with LOCK:
            job["status"] = "error"
            job["error"] = f"{type(e).__name__}: {e}"
            job["finished_at"] = time.time()


@app.post("/api/review/{job_id}")
def start_review(job_id: str):
    job = _job(job_id)
    with LOCK:
        if job["status"] == "running":
            return {"started": False, "message": "已在检测中"}
        job["status"] = "running"
    folder = UPLOADS_DIR / job_id
    label = job.get("label") or f"网页上传-{job_id[:15]}"
    t = threading.Thread(target=_run_job, args=(job_id, folder, label), daemon=True)
    t.start()
    return {"started": True}


@app.get("/api/status/{job_id}")
def status(job_id: str):
    job = _job(job_id)
    with LOCK:
        elapsed = None
        if job.get("started_at"):
            end = job.get("finished_at") or time.time()
            elapsed = round(end - job["started_at"], 1)
        return {
            "status": job["status"],
            "stage": job["stage"],
            "message": job["message"],
            "error": job["error"],
            "summary": job["summary"],
            "elapsed": elapsed,
            "report_ready": bool(job.get("report_html")),
        }


@app.get("/report/{job_id}")
def report(job_id: str):
    job = _job(job_id)
    if not job.get("report_html"):
        raise HTTPException(404, "报告还没生成")
    return FileResponse(job["report_html"], media_type="text/html")
