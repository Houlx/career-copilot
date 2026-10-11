"""Run the backend and frontend together for local development (Linux/macOS)."""

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def stop(processes):
    # Include descendants such as Uvicorn's reloader and npm's Vite process.
    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    for process in processes:
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def main():
    if shutil.which("npm") is None:
        print("找不到 npm，请先安装 Node.js。", file=sys.stderr)
        return 1
    if not (ROOT / "frontend/node_modules").is_dir():
        print("请先运行 npm ci --prefix frontend 安装前端依赖。", file=sys.stderr)
        return 1

    stopping = False

    def request_stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    commands = {
        "后端": [
            sys.executable,
            "-m",
            "uvicorn",
            "career_copilot.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--reload",
        ],
        "前端": ["npm", "--prefix", "frontend", "run", "dev", "--", "--strictPort"],
    }
    processes = []
    print("启动前后端；打开 http://127.0.0.1:5173/profile，按 Ctrl+C 一起停止。", flush=True)
    try:
        for command in commands.values():
            processes.append(subprocess.Popen(command, cwd=ROOT, start_new_session=True))
        while not stopping:
            for name, process in zip(commands, processes, strict=True):
                code = process.poll()
                if code is not None:
                    print(f"{name}已退出（退出码 {code}），正在停止其他服务。", file=sys.stderr)
                    return code if code > 0 else 1
            time.sleep(0.2)
        return 0
    except OSError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1
    finally:
        stop(processes)


if __name__ == "__main__":
    sys.exit(main())
