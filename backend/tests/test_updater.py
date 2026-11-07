"""自更新服务测试：不触外网，全部 mock httpx / 文件系统层面。"""
from __future__ import annotations

import tarfile
from pathlib import Path

import httpx
import pytest

from app.services import updater


# ---------- 版本比较 ----------
def test_is_newer_basic():
    assert updater.is_newer("1.4.0", "1.3.0")
    assert updater.is_newer("v1.4.1", "1.4.0")
    assert not updater.is_newer("1.3.0", "1.4.0")
    assert not updater.is_newer("1.4.0", "1.4.0")
    assert updater.is_newer("2.0", "1.9.9")


# ---------- 渠道链顺序：镜像在前，官方兜底；token 绝不发给镜像 ----------
FAKE_ASSET = {"name": "aurora-1.4.0-linux-amd64.tar.gz", "id": 12345, "tag": "v1.4.0"}


def test_channel_urls_order():
    urls = updater.channel_urls(FAKE_ASSET, repo="o/r")
    labels = [c for c, _, _ in urls]
    assert labels[0].startswith("镜像")
    assert labels[-1] == "官方 github.com"
    # 镜像 URL 形如 https://ghfast.top/https://github.com/...
    assert urls[0][1].startswith("https://ghfast.top/https://github.com/")
    assert urls[-1][1] == "https://github.com/o/r/releases/download/v1.4.0/aurora-1.4.0-linux-amd64.tar.gz"


def test_channel_urls_no_token_to_mirrors(monkeypatch):
    monkeypatch.setattr(updater, "GITHUB_TOKEN", "super-secret")
    urls = updater.channel_urls(FAKE_ASSET, repo="o/r")
    for label, url, headers in urls:
        if label.startswith("镜像"):
            assert "Authorization" not in headers, f"token 泄露给镜像：{label}"
        else:
            assert headers.get("Authorization") == "Bearer super-secret"
    # 有 token 且有 asset id 时，官方多一条 API 端点通道
    assert any("API" in label for label, _, _ in urls)


def test_channel_urls_no_token_official_plain(monkeypatch):
    monkeypatch.setattr(updater, "GITHUB_TOKEN", "")
    urls = updater.channel_urls(FAKE_ASSET, repo="o/r")
    for label, url, headers in urls:
        assert "Authorization" not in headers


# ---------- 多渠道下载：前两个失败自动切到下一个 ----------
def test_download_via_channels_fallback(tmp_path, monkeypatch):
    dest = tmp_path / "a.tar.gz"
    payload = b"x" * 4096

    class FakeResp:
        def raise_for_status(self):
            pass

        def iter_bytes(self, n):
            return iter([payload])

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    calls = []

    def fake_stream(self, method, url, **kw):
        calls.append(url)
        if len(calls) <= 2:
            raise httpx.ConnectError("mirror down")
        return FakeResp()

    monkeypatch.setattr(httpx.Client, "stream", fake_stream)
    results = updater.download_via_channels(FAKE_ASSET, dest)
    assert dest.read_bytes() == payload
    assert len(calls) == 3  # 两个镜像失败后第三个成功
    assert [r.ok for r in results] == [False, False, True]
    assert not dest.with_suffix(dest.suffix + ".part").exists()


def test_download_via_channels_all_fail(tmp_path, monkeypatch):
    def fake_stream(self, method, url, **kw):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx.Client, "stream", fake_stream)
    with pytest.raises(RuntimeError, match="所有下载渠道均失败"):
        updater.download_via_channels(FAKE_ASSET, tmp_path / "a.tar.gz")


def test_download_sha_mismatch_rejected(tmp_path, monkeypatch):
    dest = tmp_path / "a.tar.gz"

    class FakeResp:
        def raise_for_status(self):
            pass

        def iter_bytes(self, n):
            return iter([b"tampered" * 512])

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(httpx.Client, "stream",
                        lambda self, m, url, **kw: FakeResp())
    with pytest.raises(RuntimeError, match="所有下载渠道均失败"):
        updater.download_via_channels(FAKE_ASSET, dest, expected_sha256="deadbeef")
    # 校验失败 = 文件未落地
    assert not dest.exists()


# ---------- 安全解压 ----------
def test_safe_extract_rejects_traversal(tmp_path):
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tf:
        info = tarfile.TarInfo("../../outside.txt")
        data = b"boom"
        import io
        tf.addfile(info, io.BytesIO(data))
    with pytest.raises(RuntimeError, match="非法路径"):
        updater._safe_extract(evil, tmp_path / "out")


def test_safe_extract_ok(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_text("ok")
    tar = tmp_path / "good.tar.gz"
    with tarfile.open(tar, "w:gz") as tf:
        tf.add(src, arcname="pkg")
    out = tmp_path / "out"
    updater._safe_extract(tar, out)
    assert (out / "pkg" / "sub" / "f.txt").read_text() == "ok"


# ---------- 备份：打 Tar 包 + 只保留最近 3 份 ----------
def test_make_backup_prunes(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "BACKUP_DIR", tmp_path / "backups")
    fake_root = tmp_path / "app"
    (fake_root / "backend" / "app").mkdir(parents=True)
    (fake_root / "backend" / "app" / "main.py").write_text("v")
    monkeypatch.setattr(updater, "APP_ROOT", fake_root)

    for _ in range(5):
        updater.make_backup()
    backups = sorted((tmp_path / "backups").glob("aurora-backup-*.tar.gz"))
    assert len(backups) == updater.BACKUP_KEEP


# ---------- apply_files：不碰密钥与数据 ----------
def test_apply_files_skips_local_yaml(tmp_path, monkeypatch):
    fake_root = tmp_path / "app"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "models.local.yaml").write_text("secret: keep")
    monkeypatch.setattr(updater, "APP_ROOT", fake_root)

    staging = tmp_path / "staging"
    (staging / "backend" / "app").mkdir(parents=True)
    (staging / "backend" / "app" / "x.py").write_text("new")
    (staging / "config").mkdir()
    (staging / "config" / "models.local.yaml").write_text("secret: FROM_TARBALL")
    (staging / "config" / "sandbox.yaml").write_text("mode: sandbox")
    (staging / "backend" / "requirements.txt").write_text("fastapi\n")

    steps = updater.apply_files(staging)
    assert (fake_root / "backend" / "app" / "x.py").read_text() == "new"
    assert (fake_root / "config" / "models.local.yaml").read_text() == "secret: keep"
    assert (fake_root / "config" / "sandbox.yaml").read_text() == "mode: sandbox"
    assert any("未触碰" in s for s in steps)


# ---------- check_update 错误处理 ----------
def test_check_update_404_private_repo(monkeypatch):
    def fake_get(self, url, **kw):
        return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    out = updater.check_update()
    assert "error" in out
    assert "GITHUB_TOKEN" in out["error"]


def test_check_update_network_error(monkeypatch):
    def fake_get(self, url, **kw):
        raise httpx.ConnectError("no net")

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    out = updater.check_update()
    assert "error" in out


def test_check_update_ok(monkeypatch):
    payload = {
        "tag_name": "v9.9.9",
        "name": "Aurora v9.9.9",
        "body": "notes",
        "published_at": "2026-01-01T00:00:00Z",
        "assets": [{"name": "aurora-9.9.9-linux-amd64.tar.gz", "size": 123,
                    "browser_download_url": "https://github.com/x/y/releases/download/v9.9.9/aurora-9.9.9-linux-amd64.tar.gz"}],
    }
    resp = httpx.Response(200, json=payload,
                          request=httpx.Request("GET", "https://api.github.com/x"))

    def fake_get(self, url, **kw):
        return resp

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    out = updater.check_update()
    assert out["has_update"] is True
    assert out["latest"] == "9.9.9"
    assert out["asset"]["name"].endswith(".tar.gz")


# ---------- apply_update：已是最新则 no-op ----------
def test_apply_update_noop(monkeypatch):
    payload = {"tag_name": "v0.0.1", "name": "", "body": "", "published_at": "", "assets": []}
    resp = httpx.Response(200, json=payload, request=httpx.Request("GET", "https://api.github.com/x"))

    def fake_get(self, url, **kw):
        return resp

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    out = updater.apply_update()
    assert out["ok"] is True and out.get("noop") is True
