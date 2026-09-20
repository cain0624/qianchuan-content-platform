# -*- coding: utf-8 -*-
"""浏览器内门面 —— server.py 路由层的纯函数等价物，跑在 Pyodide 里。

为什么要单独写一层：
    server.py 依赖 FastAPI / Pydantic / uvicorn，浏览器里跑不了。
    但真正的业务逻辑（解析主题、生成文案、硬规则合规闸、Pillow 渲染）
    全都在 core/ 里，**一行都不用改** —— 这一层只做翻译：
        HTTP 请求字典  →  对 core/ 的调用  →  信封
    于是「本地 FastAPI 后端」和「线上浏览器内引擎」跑的是同一份 core/，
    线上不是另写一遍的精简版，不存在两套实现漂移的问题。

信封约定（重要）：
    永远返回 {"ok": true, "data": ...} 或 {"ok": false, "error": ..., "kind": ...}，
    **不要把异常抛出去**。Pyodide 会把 Python 异常包成一大坨 traceback，
    前端只能看到一个 "PythonError"，拿不到有用信息。所以这里全量 try/except，
    异常一律转成信封里的 error 字符串，traceback 只截末尾几行。

和 server.py 的分工：
    路由参数解析、响应字段拼装各写一遍（壳不同），业务调用完全共用。
    两边的输出用 tools/parity_check.py 对拍，一旦漂移立刻报错。
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import traceback


def _bootstrap() -> str:
    """把 core/ 挂到 sys.path 上，返回项目根。

    core/ 内部的 import 是扁平的（`from copywriter import ...`、`from assets import ...`），
    和 server.py 的做法一致，所以这里也要把 core/ 目录本身加进去，
    而不是把 core 当成包来用。
    """
    root = os.path.dirname(os.path.abspath(__file__))
    core = os.path.join(root, "core")
    if core not in sys.path:
        sys.path.insert(0, core)
    return root


ROOT = _bootstrap()

# 尺寸的中文说明。**必须与 server.py 的 SIZE_META 逐字一致** ——
# parity_check 会比对 /api/meta 的完整输出，不一致会当场失败。
SIZE_META = {
    "1:1": {"label": "1:1 方图", "use": "社群 / 视频号 / 抖音主页"},
    "3:4": {"label": "3:4 竖版", "use": "小红书笔记首图 / 抖音图文"},
    "9:16": {"label": "9:16 全屏", "use": "抖音 / 视频号 / 小红书视频"},
    "16:9": {"label": "16:9 横版", "use": "视频号横版 / B 站 / 直播预约页"},
    "2.35:1": {"label": "2.35:1 宽幅", "use": "公众号封面首图"},
}

# 数据回流（内存累计，与 server.py 的 STORE 同构；刷新页面即清空）
STORE: dict = {"logs": [], "violations": {}, "retries": {}}


def _record(result: dict) -> None:
    """与 server.py._record 保持同一口径（后端回归指标）。"""
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


# ---------------------------------------------------------------- 各路由
def _meta(_payload, _method):
    from copywriter import SCENES
    from guard import REQUIRED_DISCLAIMERS, PLATFORM_NOTES, _C
    from renderer import (FAMILIES, LAYOUT_MODES, PERSON_GEO, SIZES, ZONE_SPEC,
                          platform_summary, template_summary)
    from assets import ROLES
    return {
        "scenes": [{"id": s["id"], "name": s["name"], "audience": s["audience"],
                    "keywords": s["keywords"][:10], "templateId": s["templateId"],
                    "sceneCount": len(s["segments"])} for s in SCENES],
        "templates": template_summary(),
        "families": FAMILIES,
        "platforms": [{"id": k, "name": v["name"], "strict": v["strict"]}
                      for k, v in PLATFORM_NOTES.items()],
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


def _rules(_payload, _method):
    from renderer import FAMILIES, SPEC, template_summary
    return {"templates": template_summary(), "zones": SPEC["zones"], "families": FAMILIES,
            "typography": SPEC["typography"], "safeMargin": SPEC["safeMargin"]}


def _assets_list(payload, _method):
    from assets import ROLES, list_assets
    role = (payload or {}).get("role")
    items = list_assets(role)
    return {"items": items,
            "counts": {r: len([i for i in list_assets() if i["role"] == r]) for r in ROLES},
            "roles": [{"id": k, **v} for k, v in ROLES.items()]}


def _assets_upload(payload, _method):
    """浏览器版的上传：bridge 把文件读成 base64 送进来，这里解码回 bytes。

    本地版走 multipart，这里走 JSON —— 但落到 add_asset 的参数完全一致。
    """
    from assets import add_asset
    p = payload or {}
    raw = p.get("b64") or ""
    if not raw:
        return _err("文件为空", "empty")
    try:
        data = base64.b64decode(raw)
    except Exception as e:
        return _err(f"base64 解码失败：{e}", "decode")
    if not data:
        return _err("文件为空", "empty")
    on = bool(p.get("cutout", True))
    try:
        item = add_asset(data, p.get("filename") or "asset.png", role=p.get("role") or "portrait",
                         name=p.get("name") or "", title=p.get("title") or "", cutout=on)
    except Exception as e:
        return _err(f"素材处理失败：{e}", "asset")
    return {"ok": True, "item": item}


def _assets_patch(payload, _method):
    from assets import update_asset
    p = payload or {}
    aid = p.get("id") or ""
    item = update_asset(aid, name=p.get("name"), title=p.get("title"), role=p.get("role"))
    if not item:
        return _err("素材不存在", "not_found")
    return {"ok": True, "item": item}


def _assets_delete(payload, _method):
    from assets import delete_asset
    aid = (payload or {}).get("id") or ""
    if not delete_asset(aid):
        return _err("素材不存在", "not_found")
    return {"ok": True}


def _generate(payload, _method):
    from harness import run_turn
    p = payload or {}
    theme = p.get("theme") or ""
    overrides = {
        "badge": p.get("badge") or None,
        "institution": p.get("institution") or None,
        "host": p.get("host") or None,
        "duration": p.get("duration", 60),
        "sizes": p.get("sizes") or ["1:1", "3:4", "9:16"],
        "platform": p.get("platform") or "douyin",
        "audience": p.get("audience") or None,
        "styleFamily": p.get("styleFamily") or "youth",
        "assets": p.get("assets") or {},
        "platformId": p.get("outputPlatform") or "live",
        "pageTotal": p.get("pageTotal"),
        "pageIndex": p.get("pageIndex"),
        "durationLabel": p.get("durationLabel"),
        "topics": p.get("topics"),
    }
    if p.get("templateId"):
        from renderer import TPL_BY_ID
        t = TPL_BY_ID.get(p["templateId"])
        if t:
            overrides["forceTemplateId"] = t["id"]
    mr = p.get("maxRetry", 2)
    try:
        mr = max(0, min(3, int(mr)))
    except (TypeError, ValueError):
        mr = 2
    result = run_turn(theme, overrides, variant=p.get("variant") or "v2_safe", max_retry=mr)
    result["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _record(result)
    return result


def _rerender(payload, _method):
    from harness import rerender
    p = payload or {}
    return rerender(p.get("templateId") or "", p.get("copy") or {},
                    p.get("sizes") or ["1:1", "3:4", "9:16"],
                    p.get("sceneId") or "variant", p.get("assets"))


def _layout_compare(payload, _method):
    from harness import rerender
    p = payload or {}
    base = dict(p.get("assets") or {})
    without = {k: v for k, v in base.items() if k != "portraitId"}
    tid = p.get("templateId") or ""
    copyf = p.get("copy") or {}
    sizes = p.get("sizes") or ["1:1"]
    person = (rerender(tid, copyf, sizes, "cmp_person", dict(base), out_sub="person")
              if base.get("portraitId") else None)
    graphic = rerender(tid, copyf, sizes, "cmp_graphic", without, out_sub="graphic")
    return {"person": person, "graphic": graphic,
            "note": "同一套文案：有半身像 → 人物版式；无半身像 → 无人物版式（文案居中 + 底部图标卡）"}


def _precheck(payload, _method):
    from guard import run_precheck, scan
    p = payload or {}
    text = p.get("text") or ""
    fields = {"mainTitle": text, "subtitle": "", "badge": "", "label": "",
              "footerLeft": "", "footerRight": ""}
    res = run_precheck(fields, platform=p.get("platform") or "douyin",
                       category="金融/保险", max_rounds=1, auto_fix=False)
    res["hits"] = scan(text)
    res.pop("rounds", None)
    return res


def _metrics(_payload, _method):
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
        "byScene": sorted([{"scene": k, "count": v} for k, v in by_scene.items()],
                          key=lambda x: -x["count"]),
        "logs": logs[-12:][::-1],
    }


def _health(_payload, _method):
    from llm import has_api_key
    return {"ok": True, "model": "real" if has_api_key() else "offline-synthesizer"}


# path → handler。路径参数型（/api/assets/<id>）在 _match 里单独处理。
ROUTES = {
    "/api/meta": _meta,
    "/api/rules": _rules,
    "/api/assets": _assets_list,
    "/api/assets/upload": _assets_upload,
    "/api/generate": _generate,
    "/api/rerender": _rerender,
    "/api/layout/compare": _layout_compare,
    "/api/precheck": _precheck,
    "/api/metrics": _metrics,
    "/api/health": _health,
}

_ASSET_ITEM_RE = re.compile(r"^/api/assets/([^/]+)$")


def _match(path: str, payload: dict, method: str):
    """返回 (handler, payload)。路径参数（/api/assets/<id>）在这里拆出来，
    这样前端两种环境下的 URL 写法完全一致，不需要 bridge 做任何改写。"""
    fn = ROUTES.get(path)
    if fn is not None:
        return fn, payload
    m = _ASSET_ITEM_RE.match(path)
    if m:
        aid = m.group(1)
        if method == "DELETE":
            return _assets_delete, {"id": aid}
        return _assets_patch, {**(payload or {}), "id": aid}
    return None, None


def _err(msg: str, kind: str = "error"):
    return {"__error__": (msg, kind)}


def _envelope(path: str, payload: dict, method: str) -> str:
    fn, pl = _match(path, payload, method)
    if fn is None:
        return json.dumps({"ok": False, "error": f"未知接口 {path}", "kind": "not_found"},
                          ensure_ascii=False)
    try:
        out = fn(pl, method)
    except Exception as e:
        tb = traceback.format_exc().strip().splitlines()
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}",
                           "kind": "exception", "traceback": "\n".join(tb[-4:])},
                          ensure_ascii=False)
    if isinstance(out, dict) and "__error__" in out:
        msg, kind = out["__error__"]
        return json.dumps({"ok": False, "error": msg, "kind": kind}, ensure_ascii=False)
    return json.dumps({"ok": True, "data": out}, ensure_ascii=False)


def call(path: str, payload_json: str = "{}", method: str = "POST") -> str:
    """入口（参数已经是字符串形式）。返回信封的 JSON 文本。"""
    try:
        payload = json.loads(payload_json) if payload_json else {}
    except Exception as e:
        return json.dumps({"ok": False, "error": f"参数不是合法 JSON：{e}", "kind": "bad_request"},
                          ensure_ascii=False)
    return _envelope(path, payload, method)


def call_fs(req_file: str) -> str:
    """从虚拟文件系统里的一个 JSON 文件读请求，再走 call。

    为什么不让 bridge 把参数直接拼进 Python 源码：
        JS 的 JSON.stringify 和 Python 的字符串字面量**不是**完全兼容 ——
        JSON 允许 `\\/` 这种转义（Python 不认），也不转义 U+2028/U+2029
        （Python 当作行分隔符，直接语法错误）。用户主题里出现这些字符的概率不高，
        但一旦出现就是"某些输入必崩"这种最难查的 bug。
        走文件则完全绕开转义问题，且中文按 UTF-8 读写，稳。
    """
    try:
        with open(req_file, "r", encoding="utf-8") as f:
            req = json.load(f)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"请求文件读取失败：{e}", "kind": "bad_request"},
                          ensure_ascii=False)
    payload = req.get("payload") or {}
    return _envelope(req.get("path") or "", payload, req.get("method") or "POST")


def url_to_fs(url: str) -> str:
    """把响应里的资源 URL 映射成虚拟文件系统里的真实路径。

    前端拿到的是 `/outputs/xxx/cover.png`、`/assets/xxx.png` 这种**绝对 URL**，
    因为它们本来就是给 <img src> 用的。浏览器里没有 HTTP 服务，得由 bridge
    从 Pyodide 的 FS 里把字节读出来转成 blob URL —— 这个函数负责换算前缀。
    """
    u = url.lstrip("/")
    return os.path.join(ROOT, u.replace("/", os.sep))


def ping() -> str:
    """给 bridge 做启动自检用：确认 core/ 真的能 import。"""
    try:
        from renderer import SPEC
        return json.dumps({"ok": True, "root": ROOT, "templates": len(SPEC.get("families", [])),
                           "version": SPEC.get("version")}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
