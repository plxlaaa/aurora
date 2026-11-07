"""自更新服务：检查新版本 → 多渠道下载 → 校验 → 备份 → 应用 → 重启。

下载渠道策略（用户核心诉求）：
    加速镜像优先（ghfast / gh-proxy / ghproxy / moeyy），
    逐渠道尝试，超时或失败自动切换下一个，
    全部失败后回退官方 github.com 直连。

安全边界：
    - 永不触碰 data/、*.local.yaml、.venv、venv、日志与上传目录；
    - 覆盖前对 backend/app、frontend/dist、config 做备份（保留最近 3 份）；
    - tar 解压前做路径穿越校验；
    - 应用失败自动回滚备份。
"""
from __future__ import annotations

import hashlib
import logging
import os
import platform
import shutil
import sys
import tarfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.core.version import APP_VERSION, UPDATE_REPO

log = logging.getLogger(__name__)

# ---------- 渠道配置 ----------
DEFAULT_MIRRORS = [
    "https://ghfast.top",
    "https://gh-proxy.com",
    "https://ghproxy.net",
    "https://github.moeyy.xyz",
]
OFFICIAL_BASE = "https://github.com"
OFFICIAL_API = "https://api.github.com"

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
MIRRORS = [
    m.strip().rstrip("/")
    for m in os.getenv("AURORA_UPDATE_MIRRORS", "").split(",")
    if m.strip()
] or DEFAULT_MIRRORS
DOWNLOAD_TIMEOUT = float(os.getenv("AURORA_UPDATE_TIMEOUT", "300"))
CHECK_TIMEOUT = float(os.getenv("AURORA_UPDATE_CHECK_TIMEOUT", "15"))
BACKUP_KEEP = 3

APP_ROOT = Path(__file__).resolve().parents[3]  # <install>/backend/app/services → <install>
BACKUP_DIR = APP_ROOT / "data" / "backups"


# ---------- 数据结构 ----------
@dataclass
class ChannelResult:
    channel: str
    ok: bool
    detail: str = ""
    elapsed: float = 0.0


@dataclass
class ReleaseInfo:
    tag: str
    name: str = ""
    notes: str = ""
    assets: list[dict[str, Any]] = field(default_factory=list)
    published_at: str = ""

    @property
    def version(self) -> str:
        return self.tag.lstrip("vV")


# ---------- 工具 ----------
def current_version() -> str:
    return APP_VERSION


def is_newer(remote: str, local: str) -> bool:
    """宽松 semver 比较：remote > local 才返回 True。"""
    def parts(v: str) -> list[int]:
        out = []
        for seg in v.lstrip("vV").split("."):
            digits = "".join(ch for ch in seg if ch.isdigit())
            out.append(int(digits) if digits else 0)
        return out

    try:
        return parts(remote) > parts(local)
    except Exception:
        return False


def _pick_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """按当前平台挑最合适的 tar.gz 资产；找不到平台匹配则取唯一 tar.gz。"""
    sysname = platform.system().lower()  # linux / darwin / windows
    machine = platform.machine().lower()
    arch_alias = {"x86_64": ("x86_64", "amd64"), "amd64": ("x86_64", "amd64"),
                  "aarch64": ("aarch64", "arm64"), "arm64": ("aarch64", "arm64")}
    arches = arch_alias.get(machine, (machine,))

    tars = [a for a in assets if str(a.get("name", "")).endswith((".tar.gz", ".tgz"))]
    for a in tars:
        n = str(a.get("name", "")).lower()
        if sysname in n and any(x in n for x in arches):
            return a
    if len(tars) == 1:
        return tars[0]
    return tars[0] if tars else None


def channel_urls(asset: dict[str, Any], repo: str = UPDATE_REPO) -> list[tuple[str, str, dict[str, str]]]:
    """生成渠道→(URL, 请求头) 列表：加速镜像在前，官方兜底。

    安全约束：GitHub token 绝不发给第三方镜像，仅官方直连携带。
    私有仓库：官方通道优先走 API asset 端点（octet-stream），无 token 时
    回退 browser_download_url（仅公开库可用）。
    """
    dl_path = f"/{repo}/releases/download/{asset.get('tag', '')}/{asset['name']}"
    asset_id = asset.get("id", "")
    out: list[tuple[str, str, dict[str, str]]] = []
    for m in MIRRORS:
        out.append((f"镜像 {m}", f"{m}/{OFFICIAL_BASE}/{dl_path.lstrip('/')}", _plain_headers()))
    if GITHUB_TOKEN and asset_id:
        out.append(("官方 github.com (API)", f"{OFFICIAL_API}/repos/{repo}/releases/assets/{asset_id}",
                    _auth_headers(accept="application/octet-stream")))
    out.append(("官方 github.com", f"{OFFICIAL_BASE}{dl_path}",
                _auth_headers() if GITHUB_TOKEN else _plain_headers()))
    return out


def _plain_headers() -> dict[str, str]:
    return {"User-Agent": "aurora-updater"}


def _auth_headers(accept: str = "application/vnd.github+json") -> dict[str, str]:
    h = {"Accept": accept, "User-Agent": "aurora-updater"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


# ---------- 检查更新 ----------
def fetch_latest_release(timeout: float = CHECK_TIMEOUT) -> ReleaseInfo:
    url = f"{OFFICIAL_API}/repos/{UPDATE_REPO}/releases/latest"
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url, headers=_auth_headers())
        resp.raise_for_status()
        data = resp.json()
    return ReleaseInfo(
        tag=str(data.get("tag_name", "")),
        name=str(data.get("name", "")),
        notes=str(data.get("body", ""))[:2000],
        assets=list(data.get("assets") or []),
        published_at=str(data.get("published_at", "")),
    )


def check_update(timeout: float = CHECK_TIMEOUT) -> dict[str, Any]:
    """检查更新。私有库无 token 时 GitHub API 返回 404 → 提示配置 token。"""
    try:
        rel = fetch_latest_release(timeout)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return {"error": "未找到 Release（私有仓库需配置 GITHUB_TOKEN，或仓库尚无发布版本）"}
        return {"error": f"GitHub API 错误：HTTP {e.response.status_code}"}
    except Exception as e:
        return {"error": f"检查更新失败：{e}"}

    asset = _pick_asset(rel.assets)
    return {
        "current": APP_VERSION,
        "latest": rel.version,
        "tag": rel.tag,
        "name": rel.name,
        "notes": rel.notes,
        "published_at": rel.published_at,
        "has_update": is_newer(rel.version, APP_VERSION),
        "asset": {"name": asset["name"], "size": asset.get("size", 0),
                  "url": asset.get("browser_download_url", "")} if asset else None,
        "mirrors": MIRRORS,
    }


# ---------- 多渠道下载 ----------
def download_via_channels(asset: dict[str, Any], dest: Path,
                          expected_sha256: str = "") -> list[ChannelResult]:
    """逐渠道下载，加速镜像优先，官方兜底。全部失败抛 RuntimeError。"""
    results: list[ChannelResult] = []
    for label, url, headers in channel_urls(asset):
        t0 = time.monotonic()
        try:
            with httpx.Client(timeout=httpx.Timeout(10.0, read=DOWNLOAD_TIMEOUT),
                              follow_redirects=True) as client:
                with client.stream("GET", url, headers=headers) as resp:
                    resp.raise_for_status()
                    tmp = dest.with_suffix(dest.suffix + ".part")
                    sha = hashlib.sha256()
                    total = 0
                    with open(tmp, "wb") as f:
                        for chunk in resp.iter_bytes(1 << 16):
                            f.write(chunk)
                            sha.update(chunk)
                            total += len(chunk)
            if total < 1024:
                raise RuntimeError(f"下载体积异常（{total}B），疑似渠道错误页")
            digest = sha.hexdigest()
            if expected_sha256 and digest != expected_sha256:
                raise RuntimeError(f"sha256 校验失败：{digest}")
            tmp.rename(dest)
            results.append(ChannelResult(label, True, f"{total} 字节", time.monotonic() - t0))
            return results
        except Exception as e:  # noqa: BLE001 —— 渠道失败必须继续下一个
            results.append(ChannelResult(label, False, str(e)[:200], time.monotonic() - t0))
            log.warning("下载渠道失败 %s: %s", label, e)
            if dest.with_suffix(dest.suffix + ".part").exists():
                dest.with_suffix(dest.suffix + ".part").unlink(missing_ok=True)
    raise RuntimeError("所有下载渠道均失败（含官方直连）")


# ---------- 安全解压 ----------
def _safe_extract(tar_path: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    base = target.resolve()
    with tarfile.open(tar_path, "r:gz") as tf:
        for member in tf.getmembers():
            dest = (target / member.name).resolve()
            if not str(dest).startswith(str(base)):
                raise RuntimeError(f"tar 包含非法路径：{member.name}")
            if member.issym() or member.islnk():
                raise RuntimeError(f"tar 包含链接成员，拒绝解压：{member.name}")
        tf.extractall(target, filter="data")  # noqa: S202 —— 已逐成员校验


# ---------- 备份与回滚 ----------
def _prune_backups() -> None:
    if not BACKUP_DIR.exists():
        return
    backups = sorted(BACKUP_DIR.glob("aurora-backup-*.tar.gz"))
    for old in backups[:-BACKUP_KEEP]:
        old.unlink(missing_ok=True)


def make_backup() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"aurora-backup-{APP_VERSION}-{stamp}-{uuid.uuid4().hex[:8]}.tar.gz"
    targets = [p for p in (APP_ROOT / "backend" / "app",
                           APP_ROOT / "frontend" / "dist",
                           APP_ROOT / "config") if p.exists()]
    with tarfile.open(dest, "w:gz") as tf:
        for p in targets:
            tf.add(p, arcname=str(p.relative_to(APP_ROOT)))
    _prune_backups()
    return dest


def rollback(backup: Path) -> None:
    _safe_extract(backup, APP_ROOT)


# ---------- 应用更新 ----------
def _copy_tree_skip_local(src: Path, dst: Path) -> None:
    """覆盖目录，但绝不覆盖 *.local.yaml（用户密钥层）。"""
    for root, _dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for fn in files:
            if fn.endswith(".local.yaml"):
                continue
            shutil.copy2(Path(root) / fn, dst / rel / fn)


def apply_files(staging: Path) -> list[str]:
    """将暂存目录中的新版本覆盖到安装目录（保护数据与密钥）。"""
    steps: list[str] = []
    if (staging / "backend" / "app").is_dir():
        dst = APP_ROOT / "backend" / "app"
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(staging / "backend" / "app", dst)
        steps.append("backend/app 已更新")
    if (staging / "frontend" / "dist").is_dir():
        dst = APP_ROOT / "frontend" / "dist"
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(staging / "frontend" / "dist", dst)
        steps.append("frontend/dist 已更新")
    if (staging / "config").is_dir():
        _copy_tree_skip_local(staging / "config", APP_ROOT / "config")
        steps.append("config 模板已更新（*.local.yaml 未触碰）")
    req = staging / "backend" / "requirements.txt"
    if req.is_file():
        shutil.copy2(req, APP_ROOT / "backend" / "requirements.txt")
        steps.append("requirements.txt 已同步")
    return steps


def install_deps() -> str:
    """用现有 venv 安装/更新依赖；无 venv 则跳过。"""
    for name in ("venv", ".venv"):
        venv = APP_ROOT / "backend" / name
        pip = venv / (Path("Scripts") / "pip.exe" if os.name == "nt" else Path("bin") / "pip")
        if pip.exists():
            import subprocess
            r = subprocess.run(
                [str(pip), "install", "-q", "-r", str(APP_ROOT / "backend" / "requirements.txt")],
                cwd=str(APP_ROOT), capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                return f"依赖安装告警：{r.stderr[-300:]}"
            return "依赖已更新"
    return "未发现 venv，跳过依赖安装"


def try_restart() -> str:
    """尽力重启：systemd 单元优先，否则提示手动重启。"""
    import subprocess
    unit = os.getenv("AURORA_SERVICE_NAME", "aiassistant-gateway")
    if shutil.which("systemctl"):
        try:
            r = subprocess.run(["systemctl", "cat", unit], capture_output=True, text=True)
            if r.returncode == 0:
                subprocess.run(["systemctl", "restart", unit], check=True, timeout=60)
                return f"已通过 systemd 重启服务 {unit}"
        except Exception as e:  # noqa: BLE001
            log.warning("systemd 重启失败：%s", e)
    return "无法自动重启（非 systemd 环境），请手动重启服务以加载新版本"


def apply_update(expected_tag: str = "", expected_sha256: str = "") -> dict[str, Any]:
    """完整更新流程：下载 → 备份 → 解压 → 覆盖 → 依赖 → 重启。"""
    report: dict[str, Any] = {"current": APP_VERSION, "steps": [], "channels": []}
    try:
        rel = fetch_latest_release()
    except Exception as e:  # noqa: BLE001
        return {**report, "ok": False, "error": f"获取最新版本失败：{e}"}
    if expected_tag and rel.tag != expected_tag:
        return {**report, "ok": False,
                "error": f"最新版本已变为 {rel.tag}（期望 {expected_tag}），为安全起见中止"}
    if not is_newer(rel.version, APP_VERSION):
        return {**report, "ok": True, "noop": True, "message": f"当前已是最新版本 {APP_VERSION}"}
    asset = _pick_asset(rel.assets)
    if not asset:
        return {**report, "ok": False, "error": "Release 中未找到可用的 tar.gz 资产"}
    asset = {**asset, "tag": rel.tag}
    report["target"] = rel.version

    work = Path("/tmp" if os.name != "nt" else os.environ.get("TEMP", ".")) / \
        f"aurora-update-{int(time.time())}"
    work.mkdir(parents=True, exist_ok=True)
    tarball = work / asset["name"]
    backup: Path | None = None
    try:
        report["channels"] = [
            {"channel": c.channel, "ok": c.ok, "detail": c.detail, "seconds": round(c.elapsed, 1)}
            for c in download_via_channels(asset, tarball, expected_sha256)]
        report["steps"].append(f"已下载 {asset['name']}")
        backup = make_backup()
        report["steps"].append(f"已备份当前版本 → {backup.name}")
        staging = work / "staging"
        _safe_extract(tarball, staging)
        inner = next(iter(staging.iterdir())) if staging.is_dir() else staging
        if not (inner / "backend").is_dir():
            inner = staging
        report["steps"].extend(apply_files(inner))
        report["steps"].append(install_deps())
        report["restart"] = try_restart()
        report["ok"] = True
        report["version_after"] = rel.version
    except Exception as e:  # noqa: BLE001
        report["ok"] = False
        report["error"] = str(e)[:400]
        if backup and backup.exists():
            try:
                rollback(backup)
                report["steps"].append("已回滚到更新前版本")
            except Exception as re_:  # noqa: BLE001
                report["steps"].append(f"回滚失败，请手动恢复：{backup}（{re_}）")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return report
