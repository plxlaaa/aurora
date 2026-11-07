"""Codex 兼容工具链测试：patch 解析/应用、工作区隔离、定义格式、分发路由。"""
from __future__ import annotations

import pytest

from app.services import codex_tools
from app.services.codex_tools import (
    apply_patch,
    definitions,
    parse_patch,
    session_workspace,
)

PATCH_ADD = """*** Begin Patch
*** Add File: src/a.txt
+hello
+world
*** End Patch"""

PATCH_UPDATE = """*** Begin Patch
*** Update File: src/a.txt
@@
-hello
+hi
*** End Patch"""

PATCH_DELETE = """*** Begin Patch
*** Delete File: src/a.txt
*** End Patch"""

PATCH_MOVE = """*** Begin Patch
*** Update File: src/a.txt
*** Move to: src/b.txt
@@
-world
+hey
*** End Patch"""


# ---------- 解析 ----------
def test_parse_add_update_delete():
    ops = parse_patch(f"""*** Begin Patch
*** Add File: a.txt
+x
*** Update File: a.txt
@@
-x
+y
*** Delete File: b.txt
*** End Patch""")
    assert [(o.kind, o.path) for o in ops] == [("add", "a.txt"), ("update", "a.txt"), ("delete", "b.txt")]
    assert ops[0].content == "x"


def test_parse_requires_markers():
    with pytest.raises(ValueError):
        parse_patch("no markers here")


# ---------- 应用 ----------
def test_apply_add_update_delete(tmp_path):
    apply_patch(tmp_path, PATCH_ADD)
    assert (tmp_path / "src" / "a.txt").read_text() == "hello\nworld\n"

    apply_patch(tmp_path, PATCH_UPDATE)
    assert (tmp_path / "src" / "a.txt").read_text() == "hi\nworld\n"

    apply_patch(tmp_path, PATCH_DELETE)
    assert not (tmp_path / "src" / "a.txt").exists()


def test_apply_move(tmp_path):
    apply_patch(tmp_path, PATCH_ADD)
    out = apply_patch(tmp_path, PATCH_MOVE)
    assert out["results"][0]["op"] == "update"
    assert (tmp_path / "src" / "b.txt").read_text() == "hello\nhey\n"
    assert not (tmp_path / "src" / "a.txt").exists()


def test_apply_add_existing_fails(tmp_path):
    apply_patch(tmp_path, PATCH_ADD)
    out = apply_patch(tmp_path, PATCH_ADD)
    assert "已存在" in out["error"]


def test_apply_update_missing_context_fails(tmp_path):
    apply_patch(tmp_path, PATCH_ADD)
    out = apply_patch(tmp_path, """*** Begin Patch
*** Update File: src/a.txt
@@
-不存在的行
+替换
*** End Patch""")
    assert "error" in out


def test_apply_rejects_traversal(tmp_path):
    for evil in ("../../evil.txt", "/etc/passwd", "a/../../b.txt"):
        out = apply_patch(tmp_path, f"""*** Begin Patch
*** Add File: {evil}
+x
*** End Patch""")
        assert "error" in out, evil
        assert not (tmp_path.parent / "evil.txt").exists()


# ---------- 工作区 ----------
def test_session_workspace_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_tools, "WORKSPACE_ROOT", tmp_path)
    ws1 = session_workspace("s1")
    ws2 = session_workspace("s2")
    assert ws1 != ws2
    assert ws1.name == "s1"
    assert ws1.parent == tmp_path


# ---------- 定义与路由 ----------
def test_definitions_openai_format():
    defs = definitions()
    names = [d["function"]["name"] for d in defs]
    assert names == list(codex_tools.CODEX_TOOL_NAMES)
    for d in defs:
        assert d["type"] == "function"
        assert "parameters" in d["function"]


def test_registry_routes_codex_names():
    from app.services.tool_registry import ToolRegistry, codex_names

    assert "shell" in codex_names()
    registry = ToolRegistry(skills=None, mcp=None, custom=None, executor=None)
    # update_plan 不依赖 executor，可直接分发
    import asyncio
    out = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        registry.dispatch("update_plan", {"plan": [{"step": "x", "status": "done"}]})
    )
    assert out["ok"] is True and out["plan"][0]["step"] == "x"


def test_name_exists_includes_codex():
    from app.services.tool_registry import ToolRegistry

    class _EmptySkills:
        def get(self, name): return None

    class _EmptyMcp:
        def function_definitions(self): return []

    class _EmptyCustom:
        def get(self, name): return None

    registry = ToolRegistry(skills=_EmptySkills(), mcp=_EmptyMcp(),
                            custom=_EmptyCustom(), executor=None)
    assert registry.name_exists("shell")
    assert registry.name_exists("apply_patch")
    assert not registry.name_exists("codex_not_a_tool")
