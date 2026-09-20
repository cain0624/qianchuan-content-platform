# -*- coding: utf-8 -*-
"""
模型分流 + 离线合成器。

设计原则：**无 API Key 时链路必须完整可演示**。
所以离线不是"降级"，而是主路径：知识库 + 句式模板负责产出事实与结构，
可选的真实模型只负责在已有事实上做润色 —— 不允许它自造新的收益数字或承诺。
"""
from __future__ import annotations

import json
import os
import re
import urllib.request


def api_key() -> str | None:
    return os.getenv("LIVE_COVER_API_KEY") or os.getenv("OPENAI_API_KEY") or None


def base_url() -> str:
    return os.getenv("LIVE_COVER_BASE_URL") or "https://api.openai.com/v1"


def model_name() -> str:
    return os.getenv("LIVE_COVER_MODEL") or "gpt-4o-mini"


def has_api_key() -> bool:
    return bool(api_key())


def complete(system: str, user: str, timeout: int = 40, temperature: float = 0.6) -> str | None:
    """调用 OpenAI 兼容接口。失败一律返回 None，由调用方回落到离线合成器。"""
    key = api_key()
    if not key:
        return None
    payload = {
        "model": model_name(),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
    }
    req = urllib.request.Request(
        base_url().rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except Exception:
        return None


def extract_json(text: str) -> dict | None:
    """从模型输出里抠出 JSON（容忍 ```json 包裹与前后废话）。"""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    raw = m.group(1) if m else None
    if raw is None:
        m = re.search(r"(\{.*\})", text, re.S)
        raw = m.group(1) if m else text
    try:
        return json.loads(raw)
    except Exception:
        return None


def _section(text: str, tag: str) -> str:
    """按【tag】切片，取到下一个【为止。离线合成器用它只搬运事实，不生成新事实。"""
    m = re.search(rf"【{re.escape(tag)}】(.*?)(?=【|\Z)", text or "", re.S)
    return m.group(1).strip() if m else ""
