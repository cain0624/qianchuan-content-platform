# -*- coding: utf-8 -*-
"""把 run.sh 拉成脱离当前进程组的常驻进程。

为什么需要它：直接 `nohup ./run.sh &` 起的子进程仍在当前进程组里，
上层 shell / 任务被回收时会连坐；而 launchctl 在沙箱内不可用。
这里用 `setsid()` 开一个新会话（进程组组长就是自己），再 exec 服务进程，
会话结束时的进程组 kill 就打不到它了。

用法：
    python3 daemon.py           启动（已在监听则跳过）
    python3 daemon.py stop      停止
    python3 daemon.py status    查看
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.getenv("PORT", "8848"))
LOG = os.getenv("LOG", "/tmp/insurance-live-cover-agent.log")
PIDFILE = os.path.join(ROOT, ".server.pid")


def _listening() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def _pid() -> int | None:
    try:
        with open(PIDFILE) as f:
            p = int(f.read().strip())
        os.kill(p, 0)
        return p
    except (OSError, ValueError):
        return None


def status() -> int:
    if _listening():
        print(f"运行中 http://127.0.0.1:{PORT}  pid={_pid() or '?'}")
        return 0
    print("未运行")
    return 1


def stop() -> int:
    p = _pid()
    if p:
        try:
            os.kill(p, signal.SIGTERM)
        except OSError:
            pass
    # 服务是 uvicorn 单进程，端口释放即视为停掉
    for _ in range(12):
        if not _listening():
            try:
                os.remove(PIDFILE)
            except OSError:
                pass
            print("已停止")
            return 0
        time.sleep(0.5)
    print(f"停止失败，仍在监听 {PORT}")
    return 1


def start() -> int:
    if _listening():
        print(f"已在监听 {PORT}，无需重复启动")
        return 0
    pid = os.fork()
    if pid > 0:
        # 父进程：等子进程把端口起来后回报
        for _ in range(20):
            time.sleep(0.5)
            if _listening():
                print(f"已启动 http://127.0.0.1:{PORT}  pid={pid}  log={LOG}")
                return 0
        print(f"启动超时，见日志 {LOG}")
        return 1
    # 子进程：新会话 → 与调用者进程组彻底脱钩 → 再 exec 服务
    os.setsid()
    os.chdir(ROOT)
    fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))
    os.execv("/bin/bash", ["/bin/bash", os.path.join(ROOT, "run.sh")])


if __name__ == "__main__":
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "start").lower()
    sys.exit({"start": start, "stop": stop, "status": status}.get(cmd, status)())
