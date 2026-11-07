"""Aurora 桌面模式：本地原生窗口（不开浏览器）。

用法：
    python -m app.desktop
    （安装器快捷方式直指 runtime\\pythonw.exe -m app.desktop）

行为：
    1. 自举：把 site-packages（安装器布局）加入 sys.path，推导并设置
       AI_ASSISTANT_* 环境变量（仅当未设置时，服务器部署不受影响）；
    2. 后台线程启动 FastAPI/uvicorn（127.0.0.1 随机空闲端口）；
    3. 等待 /api/health 就绪；
    4. pywebview 创建原生窗口加载前端（Windows 走 WebView2）；
       任何窗口层失败自动降级为默认浏览器打开；
    5. 进程 PID 写入 data/aurora.pid，供「停止 Aurora」与卸载程序精确终止。

排障：全部输出写入 data\desktop.log。
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]          # .../backend
INSTALL_ROOT = Path(__file__).resolve().parents[2]         # 安装根（repo 布局 = 仓库根）

WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 840
READY_TIMEOUT_S = 30.0

logger = logging.getLogger("aiassistant.desktop")


def _bootstrap_paths() -> None:
    """安装器布局（backend/site-packages）下补全 sys.path——embeddable python
    的 ._pth 已由 CI 写入；此处为兜底（本地裸跑/升级残留布局）。"""
    sp = BACKEND_DIR / "site-packages"
    if sp.is_dir() and str(sp) not in sys.path:
        sys.path.append(str(sp))


def _bootstrap_env() -> Path:
    """推导 AI_ASSISTANT_* 环境变量（仅 setdefault，服务器部署的显式配置优先）。
    返回数据根目录（desktop.log 与 aurora.pid 的落点）。"""
    cfg = INSTALL_ROOT / "config"
    data = INSTALL_ROOT / "data"
    os.environ.setdefault("AI_ASSISTANT_CONFIG_DIR", str(cfg))
    os.environ.setdefault("AI_ASSISTANT_DATA_DIR", str(data / "sessions"))
    os.environ.setdefault("AI_ASSISTANT_UPLOAD_DIR", str(data / "uploads"))
    os.environ.setdefault("AI_ASSISTANT_SKILLS_DIR", str(cfg / "skills"))
    os.environ.setdefault("AI_ASSISTANT_FRONTEND_DIR", str(INSTALL_ROOT / "frontend" / "dist"))
    data.mkdir(parents=True, exist_ok=True)
    return data


def _setup_logging(data_root: Path) -> None:
    log_file = data_root / "desktop.log"
    handlers: list[logging.Handler] = [logging.FileHandler(log_file, encoding="utf-8")]
    if sys.stdout is not None:  # pythonw 下 stdout 为 None
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    sys.excepthook = lambda *exc: logger.error("未捕获异常: %s", "".join(traceback.format_exception(*exc)))


def _write_pid(data_root: Path) -> None:
    try:
        (data_root / "aurora.pid").write_text(str(os.getpid()), encoding="ascii")
    except OSError:
        logger.warning("PID 文件写入失败", exc_info=True)


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
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


def _serve(app, port: int) -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def _open_browser(base_url: str) -> None:
    webbrowser.open(base_url)


def main() -> int:
    _bootstrap_paths()
    data_root = _bootstrap_env()
    _setup_logging(data_root)
    _write_pid(data_root)

    from app.main import app

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    threading.Thread(target=_serve, args=(app, port), daemon=True).start()
    if not _wait_ready(base_url):
        logger.error("后端启动超时（%ss），进程退出", READY_TIMEOUT_S)
        return 1
    logger.info("后端就绪: %s", base_url)

    try:
        import webview  # pywebview（Windows: pythonnet + WebView2）
    except Exception:  # noqa: BLE001 —— ImportError 及 pythonnet 初始化失败均降级
        logger.warning("pywebview 不可用，降级为浏览器模式", exc_info=True)
        _open_browser(base_url)
        logger.info("Aurora 运行于 %s（浏览器模式），关闭本进程即停止服务", base_url)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return 0

    try:
        webview.create_window("Aurora", base_url, width=WINDOW_WIDTH,
                              height=WINDOW_HEIGHT, min_size=(960, 640))
        logger.info("桌面窗口启动")
        webview.start()
    except Exception:  # noqa: BLE001 —— WebView2 缺失/初始化失败等，降级保底
        logger.error("桌面窗口失败，降级为浏览器模式", exc_info=True)
        _open_browser(base_url)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        try:  # 正常退出时清掉 PID 文件
            (INSTALL_ROOT / "data" / "aurora.pid").unlink(missing_ok=True)
        except OSError:
            pass
