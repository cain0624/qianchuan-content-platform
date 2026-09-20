# -*- coding: utf-8 -*-
"""
AI 直播封面生成 Agent · 服务入口
FastAPI + 零构建静态前端。无 API Key 也能完整跑通。
"""
from __future__ import annotations

import json
import os
import sys

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "core"))

from assets import ASSETS_DIR, ROLES, add_asset, delete_asset, list_assets, update_asset  # noqa: E402
from copywriter import SCENES, gen_copy, parse_theme  # noqa: E402
from guard import (REQUIRED_DISCLAIMERS, PLATFORM_NOTES, run_precheck,  # noqa: E402
                   scan, _C)
from harness import OUT_ROOT, layout_label, rerender, run_turn  # noqa: E402
from renderer import (FAMILIES, LAYOUT_MODES, PERSON_GEO, SIZES, ZONE_SPEC,  # noqa: E402
                      family_of, platform_summary, template_summary)
from segmenter import SCENE_BY_ID  # noqa: E402

app = FastAPI(title="AI 直播封面生成 Agent", version="2.0")
os.makedirs(OUT_ROOT, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=OUT_ROOT), name="outputs")
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")
app.mount("/static", StaticFiles(directory=os.path.join(ROOT, "static")), name="static")

# ---------------------------------------------------------------- 数据回流（内存累计）
STORE: dict = {"logs": [], "violations": {}, "retries": {}}

# 尺寸的中文说明（前端选尺寸时给依据，而不是只甩一个比例）
SIZE_META = {
    "1:1": {"label": "1:1 方图", "use": "社群 / 视频号 / 抖音主页"},
    "3:4": {"label": "3:4 竖版", "use": "小红书笔记首图 / 抖音图文"},
    "9:16": {"label": "9:16 全屏", "use": "抖音 / 视频号 / 小红书视频"},
    "16:9": {"label": "16:9 横版", "use": "视频号横版 / B 站 / 直播预约页"},
    "2.35:1": {"label": "2.35:1 宽幅", "use": "公众号封面首图"},
}


def _record(result: dict):
    comp = result["compliance"]
    STORE["logs"].append({
        "ts": result.get("ts", ""),
        "theme": result["theme"],
        "scene": result["sceneName"],
        "template": result["template"]["name"],
        "verdict": comp["verdict"],
        "riskScore": comp["riskScore"],
        "retries": result["metrics"]["retryCount"],
        "covers": result["metrics"]["coverCount"],
        "ctr": result["ctr"]["score"],
    })
    STORE["logs"] = STORE["logs"][-200:]
    for att in comp["attempts"]:
        for t in att["items"]:
            STORE["violations"][t] = STORE["violations"].get(t, 0) + 1
    r = result["metrics"]["retryCount"]
    key = "0轮（一次成型）" if r == 0 else ("1轮" if r == 1 else ("2轮" if r == 2 else "≥3轮"))
    STORE["retries"][key] = STORE["retries"].get(key, 0) + 1


class GenReq(BaseModel):
    theme: str
    variant: str = "v2_safe"
    badge: str | None = None
    institution: str | None = None
    host: str | None = None
    duration: int = 60
    sizes: list[str] = ["1:1", "3:4", "9:16"]
    platform: str = "douyin"
    audience: str | None = None
    maxRetry: int = 2
    styleFamily: str = "youth"
    assets: dict | None = None
    templateId: str | None = None
    # 产出物类型：live 直播间封面 / xhs 小红书图文 / dy_image 抖音图文 / dy_video 抖音视频封面
    outputPlatform: str = "live"
    pageTotal: int | None = None
    pageIndex: int | None = None
    durationLabel: str | None = None
    topics: list[str] | None = None


class PrecheckReq(BaseModel):
    text: str
    platform: str = "douyin"


class RerenderReq(BaseModel):
    """前端仍按 copy 传字段；这里用别名接收，避免字段名遮蔽 BaseModel.copy 触发告警。"""
    model_config = ConfigDict(populate_by_name=True)
    templateId: str
    copyFields: dict = Field(default_factory=dict, alias="copy")
    sizes: list[str] = ["1:1", "3:4", "9:16"]
    sceneId: str = "variant"
    assets: dict | None = None


class AssetPatch(BaseModel):
    name: str | None = None
    title: str | None = None
    role: str | None = None


# ---------------------------------------------------------------- API
@app.get("/")
def index():
    return FileResponse(os.path.join(ROOT, "static", "index.html"))


@app.get("/api/meta")
def meta():
    return {
        "scenes": [{"id": s["id"], "name": s["name"], "audience": s["audience"],
                    "keywords": s["keywords"][:10], "templateId": s["templateId"],
                    "sceneCount": len(s["segments"])} for s in SCENES],
        "templates": template_summary(),
        "families": FAMILIES,
        "platforms": [{"id": k, "name": v["name"], "strict": v["strict"]} for k, v in PLATFORM_NOTES.items()],
        # 产出物平台（直播间封面 / 小红书图文 / 抖音图文 / 抖音视频封面）—— 与上面的合规平台不是一回事
        "outputPlatforms": platform_summary(),
        "sizes": list(SIZES.keys()),
        "sizeDims": {k: list(v) for k, v in SIZES.items()},
        "sizeMeta": SIZE_META,
        "disclaimers": REQUIRED_DISCLAIMERS,
        "ruleCount": len(_C["rules"]),
        "ruleTypes": [{"type": r["type"], "layer": r["layer"], "law": r["law"],
                       "sample": r["patterns"][:6]} for r in _C["rules"]],
        "zones": ZONE_SPEC,
        "layoutModes": LAYOUT_MODES,
        "personGeometry": PERSON_GEO,
        "anchorRoles": [{"id": k, **v} for k, v in ROLES.items()],
    }


@app.get("/api/rules")
def rules():
    from renderer import SPEC
    return {"templates": template_summary(), "zones": SPEC["zones"], "families": FAMILIES,
            "typography": SPEC["typography"], "safeMargin": SPEC["safeMargin"]}


# ---------------------------------------------------------------- 主播素材库
@app.get("/api/assets")
def assets_list(role: str | None = None):
    items = list_assets(role)
    return {"items": items, "counts": {r: len([i for i in list_assets() if i["role"] == r]) for r in ROLES},
            "roles": [{"id": k, **v} for k, v in ROLES.items()]}


@app.post("/api/assets/upload")
async def assets_upload(file: UploadFile = File(...),
                        role: str = Form("portrait"),
                        name: str = Form(""),
                        title: str = Form(""),
                        cutout: str = Form("1")):
    data = await file.read()
    if not data:
        raise HTTPException(400, "文件为空")
    on = str(cutout).strip().lower() not in ("0", "false", "no", "off", "")
    try:
        item = add_asset(data, file.filename or "asset.png", role=role, name=name,
                         title=title, cutout=on)
    except Exception as e:
        raise HTTPException(400, f"素材处理失败：{e}")
    return {"ok": True, "item": item}


@app.patch("/api/assets/{aid}")
def assets_patch(aid: str, req: AssetPatch):
    item = update_asset(aid, name=req.name, title=req.title, role=req.role)
    if not item:
        raise HTTPException(404, "素材不存在")
    return {"ok": True, "item": item}


@app.delete("/api/assets/{aid}")
def assets_delete(aid: str):
    if not delete_asset(aid):
        raise HTTPException(404, "素材不存在")
    return {"ok": True}


@app.post("/api/generate")
def generate(req: GenReq):
    import time
    overrides = {
        "badge": req.badge or None,
        "institution": req.institution or None,
        "host": req.host or None,
        "duration": req.duration,
        "sizes": req.sizes,
        "platform": req.platform,
        "audience": req.audience or None,
        "styleFamily": req.styleFamily or "youth",
        "assets": req.assets or {},
        "platformId": req.outputPlatform or "live",
        "pageTotal": req.pageTotal,
        "pageIndex": req.pageIndex,
        "durationLabel": req.durationLabel,
        "topics": req.topics,
    }
    if req.templateId:
        from renderer import TPL_BY_ID
        t = TPL_BY_ID.get(req.templateId)
        if t:
            overrides["forceTemplateId"] = t["id"]
    result = run_turn(req.theme, overrides, variant=req.variant,
                      max_retry=max(0, min(3, req.maxRetry)))
    result["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _record(result)
    return JSONResponse(result)


@app.post("/api/rerender")
def api_rerender(req: RerenderReq):
    """同文案换模板重渲染（可带主播素材）。"""
    return JSONResponse(rerender(req.templateId, req.copyFields, req.sizes, req.sceneId, req.assets))


class LayoutCompareReq(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    templateId: str
    copyFields: dict = Field(default_factory=dict, alias="copy")
    sizes: list[str] = ["1:1"]
    assets: dict | None = None


@app.post("/api/layout/compare")
def layout_compare(req: LayoutCompareReq):
    """同一套文案分别按「有人物素材」和「无人物素材」渲染，用于并排对照两种排版。

    两种版式必须写到不同子目录，否则同名文件互相覆盖，前端并排两栏会拿到同一张图。
    """
    base = dict(req.assets or {})
    with_person = dict(base)
    without = {k: v for k, v in base.items() if k != "portraitId"}
    person = rerender(req.templateId, req.copyFields, req.sizes, "cmp_person", with_person,
                      out_sub="person") if with_person.get("portraitId") else None
    graphic = rerender(req.templateId, req.copyFields, req.sizes, "cmp_graphic", without,
                       out_sub="graphic")
    return JSONResponse({
        "person": person,
        "graphic": graphic,
        "note": "同一套文案：有半身像 → 人物版式；无半身像 → 无人物版式（文案居中 + 底部图标卡）",
    })


@app.post("/api/precheck")
def precheck(req: PrecheckReq):
    """对抗样本压测：直接送一段文案进硬规则闸。"""
    fields = {"mainTitle": req.text, "subtitle": "", "badge": "", "label": "",
              "footerLeft": "", "footerRight": ""}
    res = run_precheck(fields, platform=req.platform, category="金融/保险", max_rounds=1, auto_fix=False)
    res["hits"] = scan(req.text)
    res.pop("rounds", None)
    return res


@app.get("/api/metrics")
def metrics():
    logs = STORE["logs"]
    n = len(logs)
    by = {"PASS": 0, "FAIL": 0, "BLOCK": 0, "NEED_REVIEW": 0}
    for l in logs:
        by[l["verdict"]] = by.get(l["verdict"], 0) + 1
    avg = round(sum(l["riskScore"] for l in logs) / n, 1) if n else 0
    by_scene: dict[str, int] = {}
    for l in logs:
        by_scene[l["scene"]] = by_scene.get(l["scene"], 0) + 1
    return {
        "total": n,
        "verdicts": by,
        "passRate": round(by["PASS"] / n, 3) if n else None,
        "avgRiskScore": avg,
        "avgCtr": round(sum(l["ctr"] for l in logs) / n, 1) if n else 0,
        "topViolations": sorted([{"type": k, "count": v} for k, v in STORE["violations"].items()],
                                key=lambda x: -x["count"])[:8],
        "retryDistribution": STORE["retries"],
        "byScene": sorted([{"scene": k, "count": v} for k, v in by_scene.items()], key=lambda x: -x["count"]),
        "logs": logs[-12:][::-1],
    }


@app.get("/api/health")
def health():
    from llm import has_api_key
    return {"ok": True, "model": "real" if has_api_key() else "offline-synthesizer"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8848"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
