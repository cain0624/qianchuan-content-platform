#!/usr/bin/env bash
# 后台常驻启动 —— 脱离当前 shell 的进程组，避免会话/任务结束时被一起回收。
# 用法：./start.sh          启动（已在跑则跳过）
#       ./start.sh stop     停止
#       ./start.sh status   查看状态
cd "$(dirname "$0")" || exit 1

PORT="${PORT:-8848}"
PIDFILE=".server.pid"
LOG="${LOG:-/tmp/insurance-live-cover-agent.log}"

running() { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; }

case "${1:-start}" in
  stop)
    if running; then
      pkill -f "insur.*server.py|server.py" >/dev/null 2>&1
      sleep 1
    fi
    rm -f "$PIDFILE"
    running && echo "停止失败，仍在监听 $PORT" || echo "已停止"
    ;;
  status)
    if running; then
      echo "运行中 http://127.0.0.1:$PORT  pid=$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t | head -1)"
    else
      echo "未运行"
    fi
    ;;
  *)
    if running; then echo "已在监听 $PORT，无需重复启动"; exit 0; fi
    # setsid 语义：用 python 起一个脱离父进程组的新会话
    PORT="$PORT" nohup ./run.sh >"$LOG" 2>&1 </dev/null &
    echo $! >"$PIDFILE"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      sleep 1
      if running; then
        echo "已启动 http://127.0.0.1:$PORT  pid=$(cat "$PIDFILE")  log=$LOG"
        exit 0
      fi
    done
    echo "启动超时，请查看日志：$LOG"; tail -20 "$LOG"; exit 1
    ;;
esac
