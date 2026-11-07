"""Aurora 桌面模式：本地原生窗口（不开浏览器）。

用法：
    python -m app.desktop

行为：
    1. 后台线程启动 FastAPI/uvicorn（127.0.0.1 随机空闲端口）；
    2. 等待 /api/health 就绪；
    3. pywebview 创建原生窗口加载前端（Windows 用 WebView2，Win10+ 自带）；
    4. 未安装 pywebview 时优雅降级：打开默认浏览器并保持进程。
"""
from __future__ import annotations

import logging
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn

logger = logging.getLogger("aiassistant.desktop")

WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 840
READY_TIMEOUT_S = 30.0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_ready(base_url: str, timeout: float = READY_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout
    probe = f"{base_url}/api/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(probe, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001 —— 服务未就绪属预期，轮询即可
            time.sleep(0.3)
    return False


def _serve(app, port: int) -> None:
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def main() -> int:
    from app.main import app

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    t = threading.Thread(target=_serve, args=(app, port), daemon=True)
    t.start()
    if not _wait_ready(base_url):
        print("后端启动超时，请查看日志排查。", file=sys.stderr)
        return 1

    try:
        import webview  # pywebview
    except ImportError:
        print("未安装 pywebview，降级为浏览器模式。")
        webbrowser.open(base_url)
        print(f"Aurora 运行于 {base_url}，关闭本进程即停止服务。")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return 0

    webview.create_window(
        "Aurora", base_url, width=WINDOW_WIDTH, height=WINDOW_HEIGHT,
        min_size=(960, 640),
    )
    logger.info("桌面窗口就绪: %s", base_url)
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
