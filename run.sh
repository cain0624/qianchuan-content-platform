#!/usr/bin/env bash
# 启动 AI 直播封面生成 Agent
cd "$(dirname "$0")" || exit 1

# 注意：非交互 shell 里 $USER 可能为空，统一用 $HOME 定位
VENV_PY="$HOME/.workbuddy/binaries/python/envs/default/bin/python"

PY=""
for cand in "$VENV_PY" \
            "$HOME/.workbuddy/binaries/python/versions/3.13.12/bin/python3" \
            "$(command -v python3)"; do
  [ -n "$cand" ] && [ -x "$cand" ] && PY="$cand" && break
done

if [ -z "$PY" ]; then
  echo "未找到可用的 python3，请先安装依赖："
  echo "  \$HOME/.workbuddy/binaries/python/envs/default/bin/pip install fastapi uvicorn pillow"
  exit 1
fi

if ! "$PY" -c "import fastapi, uvicorn, PIL, multipart" >/dev/null 2>&1; then
  echo "[warn] $PY 缺少依赖（fastapi/uvicorn/pillow/python-multipart），正在尝试安装…"
  "$PY" -m pip install -q fastapi uvicorn pillow python-multipart -i https://pypi.tuna.tsinghua.edu.cn/simple
fi

echo "python: $PY"
echo "启动 http://127.0.0.1:${PORT:-8848}"
exec "$PY" server.py
