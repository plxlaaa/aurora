"""桌面模式自举逻辑测试：env 推导、pid、sys.path 注入、就绪探测。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from app import desktop


@pytest.fixture()
def clean_env(monkeypatch):
    for key in ("AI_ASSISTANT_CONFIG_DIR", "AI_ASSISTANT_DATA_DIR",
                "AI_ASSISTANT_UPLOAD_DIR", "AI_ASSISTANT_SKILLS_DIR",
                "AI_ASSISTANT_FRONTEND_DIR"):
        monkeypatch.delenv(key, raising=False)


def test_bootstrap_env_defaults(clean_env, tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "INSTALL_ROOT", tmp_path)
    root = desktop._bootstrap_env()
    assert os.environ["AI_ASSISTANT_CONFIG_DIR"] == str(tmp_path / "config")
    assert os.environ["AI_ASSISTANT_DATA_DIR"] == str(tmp_path / "data" / "sessions")
    assert os.environ["AI_ASSISTANT_FRONTEND_DIR"] == str(tmp_path / "frontend" / "dist")
    assert root == tmp_path / "data" and root.is_dir()


def test_bootstrap_env_respects_existing(clean_env, monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, "INSTALL_ROOT", tmp_path)
    monkeypatch.setenv("AI_ASSISTANT_CONFIG_DIR", "/opt/keep/me")
    desktop._bootstrap_env()
    assert os.environ["AI_ASSISTANT_CONFIG_DIR"] == "/opt/keep/me"


def test_write_pid(clean_env, tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "INSTALL_ROOT", tmp_path)
    root = desktop._bootstrap_env()
    desktop._write_pid(root)
    assert (root / "aurora.pid").read_text(encoding="ascii") == str(os.getpid())


def test_wait_ready_timeout():
    assert desktop._wait_ready("http://127.0.0.1:1", timeout=1.5) is False


def test_syspath_injection(clean_env, tmp_path, monkeypatch):
    sp = desktop.BACKEND_DIR / "site-packages"
    monkeypatch.setattr(desktop, "BACKEND_DIR", tmp_path)  # tmp 下无 site-packages
    before = list(sys.path)
    desktop._bootstrap_paths()
    assert sys.path == before  # 无 site-packages 目录时不改动
    (tmp_path / "site-packages").mkdir()
    desktop._bootstrap_paths()
    assert str(tmp_path / "site-packages") in sys.path
    assert sp  # 静默引用避免 lint
