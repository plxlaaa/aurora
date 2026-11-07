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
    # pywin32 需无条件补全（与 sp 是否已在 sys.path 无关）：
    # ._pth 隔离下 wheel 自带的 pywin32.pth 引导不被 site 处理，
    # win32\lib 不进 path / pywin32_system32 不注册 DLL 目录 →
    # mcp 的 Windows 分支 import pywintypes 崩，后端起不来（CI 实锤）
    if sp.is_dir():
        for sub in ("win32", os.path.join("win32", "lib"), "Pythonwin"):
            p = sp / sub
            if p.is_dir() and str(p) not in sys.path:
                sys.path.append(str(p))
        dll_dir = sp / "pywin32_system32"
        if dll_dir.is_dir():
            os.add_dll_directory(str(dll_dir))


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


def _selfcheck() -> int:
    """真机分步自检（python -m app.desktop --selfcheck），逐项 PASS/FAIL。"""
    import importlib

    def step(name: str, fn, optional: bool = False) -> bool:
        try:
            detail = fn()
            print(f"PASS {name}" + (f" | {detail}" if detail else ""), flush=True)
            return True
        except Exception as exc:  # noqa: BLE001
            tag = "WARN" if optional else "FAIL"
            print(f"{tag} {name}: {exc!r}", flush=True)
            if not optional:
                traceback.print_exc()
            return True if optional else False

    ok = True
    ok &= step("python 解释器", lambda: sys.executable)
    ok &= step("sys.path 含 backend", lambda: next(
        p for p in sys.path if Path(p).name == "backend"))
    ok &= step("sys.path 含 site-packages", lambda: next(
        p for p in sys.path if "site-packages" in Path(p).as_posix()))
    ok &= step("import fastapi", lambda: importlib.import_module("fastapi").__name__)
    ok &= step("import uvicorn", lambda: importlib.import_module("uvicorn").__name__)
    ok &= step("import httpx", lambda: importlib.import_module("httpx").__name__)

    def _pywin32_check() -> str:
        if os.name != "nt":
            raise RuntimeError("非 Windows")
        sp = Path(sys.path and next(
            p for p in sys.path if "site-packages" in Path(p).as_posix()))
        hits = sorted({str(p.relative_to(sp)) for p in sp.rglob("pywintypes*")})
        print(f"  pywintypes 文件: {hits or '无!'}", flush=True)
        if not hits:
            raise RuntimeError("site-packages 中无 pywintypes* 文件")
        importlib.import_module("pywintypes")
        return "pywintypes 可用"

    ok &= step("pywin32(pywintypes)", _pywin32_check,
               optional=(os.name != "nt"))
    ok &= step("env 推导 + data 可写", lambda: str(_bootstrap_env()))
    ok &= step("pid 写入", lambda: str(_write_pid(_bootstrap_env())))

    def _serve_check() -> str:
        port = _free_port()
        from app.main import app as fastapi_app
        threading.Thread(
            target=_serve, args=(fastapi_app, port), daemon=True,
        ).start()
        if not _wait_ready(f"http://127.0.0.1:{port}", timeout=20):
            raise RuntimeError("后端健康检查超时")
        return f"port={port}"

    ok &= step("后端起服 + /api/health", _serve_check)

    def _webview_check() -> str:
        webview = importlib.import_module("webview")
        return f"pywebview {getattr(webview, '__version__', '?')}"

    # 平台相关：Windows 必过（CI 断言无 WARN），Linux 沙箱可缺席
    ok &= step("import webview", _webview_check, optional=True)

    def _clr_check() -> str:
        clr = importlib.import_module("clr")
        clr.AddReference("System")
        return "pythonnet/.NET 初始化成功"

    ok &= step("pythonnet(clr) 初始化", _clr_check, optional=True)

    print("SELFCHECK", "PASSED" if ok else "FAILED", flush=True)
    return 0 if ok else 1


def main() -> int:
    # Windows 控制台/管道默认 locale 编码（cp1252/cp936）打中文会炸，
    # 统一切到 UTF-8（不可编码字符替换，不影响功能）
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    _bootstrap_paths()
    data_root = _bootstrap_env()
    _setup_logging(data_root)
    _write_pid(data_root)

    if "--selfcheck" in sys.argv:
        return _selfcheck()

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
