"""Codex 兼容工具链（反代模式专用）。

场景：接入的模型是 codex 反代（模型侧已自带 codex 系统提示词），
网关只需提供与 OpenAI Codex 一致的工具执行环境——不注入 Aurora 自身的
工具目录与 ReAct 文本协议，改以标准 function calling（tools 数组）提供：

    shell        在会话工作区执行任意 shell 命令
    apply_patch  解析并应用 Codex patch 语法（Add/Update/Delete/Move）
    read_file    读取工作区内文本文件
    list_dir     列出工作区目录
    update_plan  维护任务计划（回显确认，前端表格化展示）

安全边界：
    - 所有文件路径与命令 cwd 限制在会话工作区 data/workspaces/<session_id>/ 内
      （resolve + 前缀校验，拒绝绝对路径与 .. 穿越）；
    - shell 执行复用 Executor：sandbox 模式挂载工作区进临时容器（无网络/限额），
      local 模式在宿主工作区执行；
    - 工具结果恒为 dict，序列化后以 role=tool 回填。
"""
from __future__ import annotations

import json
import logging
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.sandbox import ExecResult, Executor

logger = logging.getLogger("aiassistant.codex")

MAX_PATCH_BYTES = 512 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
DEFAULT_TIMEOUT_S = 60.0

# 会话工作区根目录（与 sessions 数据同根：data/workspaces）
from app.services.sessions import DATA_DIR as _SESSIONS_DATA_DIR  # noqa: E402

WORKSPACE_ROOT = Path(
    os.environ.get("AI_ASSISTANT_WORKSPACE_DIR",
                   str(Path(_SESSIONS_DATA_DIR).parent / "workspaces"))
)

CODEX_TOOL_NAMES = ("shell", "apply_patch", "read_file", "list_dir", "update_plan")


# ---------- OpenAI function definitions（与 Codex CLI 工具语义对齐） ----------
def definitions() -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {
            "name": "shell",
            "description": (
                "Runs the given shell command in the session workspace and returns "
                "its stdout/stderr and exit code. Prefer relative paths inside the workspace."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "array", "items": {"type": "string"},
                                "description": "命令与参数，如 [\"python\",\"-V\"]"},
                    "workdir": {"type": "string", "description": "工作区内相对目录（可选）"},
                    "timeout_ms": {"type": "integer", "description": "超时毫秒（可选，默认 60000）"},
                },
                "required": ["command"],
            },
        }},
        {"type": "function", "function": {
            "name": "apply_patch",
            "description": (
                "Apply a patch to the workspace files. Use the Codex patch format: "
                "*** Begin Patch, *** Add File: <path>, *** Update File: <path> "
                "(with '-' removed lines and '+' added lines), "
                "*** Delete File: <path>, optionally '*** Move to: <new path>', "
                "and *** End Patch."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "patch": {"type": "string", "description": "完整 patch 文本"},
                },
                "required": ["patch"],
            },
        }},
        {"type": "function", "function": {
            "name": "read_file",
            "description": "Read a text file from the session workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }},
        {"type": "function", "function": {
            "name": "list_dir",
            "description": "List entries of a directory in the session workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "相对路径，默认根目录"}},
            },
        }},
        {"type": "function", "function": {
            "name": "update_plan",
            "description": "Maintain the task plan: explain what remains, mark steps done/in_progress.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {"type": "array", "items": {
                        "type": "object",
                        "properties": {
                            "step": {"type": "string"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                        },
                        "required": ["step", "status"],
                    }},
                },
                "required": ["plan"],
            },
        }},
    ]


# ---------- 会话工作区 ----------
def session_workspace(session_id: str | None) -> Path:
    """会话工作区目录；session_id 缺失时用 shared 兜底。"""
    safe = "".join(ch for ch in str(session_id or "shared")
                   if ch.isalnum() or ch in "-_")
    ws = WORKSPACE_ROOT / (safe or "shared")
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _resolve_in_workspace(ws: Path, rel: str) -> Path:
    """把工作区内相对路径解析为绝对路径，拒绝穿越。"""
    rel = str(rel or "").strip()
    if rel.startswith("/") or rel.startswith("\\"):
        raise ValueError(f"拒绝绝对路径: {rel}")
    if rel.startswith("~") or (len(rel) >= 2 and rel[1] == ":"):
        raise ValueError(f"拒绝绝对路径: {rel}")
    rel = rel.lstrip("/\\")
    target = (ws / rel).resolve()
    if target != ws and ws.resolve() not in target.parents:
        raise ValueError(f"路径越出工作区: {rel}")
    return target


# ---------- shell ----------
async def run_shell(executor: Executor, ws: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    command = arguments.get("command")
    if isinstance(command, str):
        parts = shlex.split(command)
    else:
        parts = [str(c) for c in (command or [])]
    parts = [p for p in parts if p]
    if not parts:
        return {"error": "command 不能为空"}
    try:
        cwd = _resolve_in_workspace(ws, str(arguments.get("workdir") or "."))
        if cwd.exists() and not cwd.is_dir():
            return {"error": f"workdir 不是目录: {arguments.get('workdir')}"}
        cwd.mkdir(parents=True, exist_ok=True)
    except ValueError as e:
        return {"error": str(e)}
    timeout = min(max(float(arguments.get("timeout_ms") or DEFAULT_TIMEOUT_S) / 1000.0, 1.0), 300.0)
    res = await executor.run_shell(" ".join(shlex.quote(p) for p in parts), cwd=str(cwd), timeout=timeout)
    out: dict[str, Any] = {
        "exit_code": res.exit_code,
        "stdout": res.stdout[:16000],
        "stderr": res.stderr[:8000],
        "mode": res.mode,
    }
    if res.timed_out:
        out["error"] = f"执行超时（>{timeout}s）"
    return out


# ---------- apply_patch ----------
@dataclass
class _Op:
    kind: str                     # add | update | delete
    path: str
    content: str = ""             # add 用
    hunks: list[tuple[list[str], list[str]]] = field(default_factory=list)  # update 用
    move_to: str = ""             # update 可选


def parse_patch(text: str) -> list[_Op]:
    """解析 Codex patch 文本；必须含 Begin/End 标记与至少一个操作。"""
    if "*** Begin Patch" not in str(text) or "*** End Patch" not in str(text):
        raise ValueError("patch 缺少 *** Begin Patch / *** End Patch 标记")
    ops: list[_Op] = []
    cur: _Op | None = None
    removed: list[str] = []
    added: list[str] = []

    def flush_hunk() -> None:
        nonlocal removed, added
        if cur is not None:
            if cur.kind == "update" and (removed or added):
                cur.hunks.append((removed, added))
            elif cur.kind == "add" and added and not cur.content:
                cur.content = "\n".join(added)
        removed, added = [], []

    for raw in str(text).splitlines():
        line = raw.rstrip("\n")
        if line.startswith("*** Begin Patch") or line.startswith("*** End Patch"):
            continue
        if line.startswith("*** Add File: "):
            flush_hunk()
            cur = _Op(kind="add", path=line[len("*** Add File: "):].strip())
            ops.append(cur)
        elif line.startswith("*** Update File: "):
            flush_hunk()
            cur = _Op(kind="update", path=line[len("*** Update File: "):].strip())
            ops.append(cur)
        elif line.startswith("*** Delete File: "):
            flush_hunk()
            cur = _Op(kind="delete", path=line[len("*** Delete File: "):].strip())
            ops.append(cur)
        elif line.startswith("*** Move to: "):
            if cur is not None and cur.kind == "update":
                cur.move_to = line[len("*** Move to: "):].strip()
        elif line.startswith("@@"):
            flush_hunk()
        elif line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
        else:
            # 上下文行：对 Add File 视为内容（codex 对新文件全用 + 前缀，此为容错）
            if cur is not None and cur.kind == "add":
                added.append(line)
            else:
                flush_hunk()
    flush_hunk()
    return ops


def _find_replace(content: str, removed: list[str], added: list[str]) -> tuple[str, bool]:
    """在 content 中查找 removed 行块替换为 added 行块；找不到返回 (原内容, False)。"""
    if not removed:
        # 无删除行 → 追加
        return (content.rstrip("\n") + "\n" + "\n".join(added) + "\n") if added else (content, True)
    target = "\n".join(removed)
    body = content
    idx = body.find(target)
    if idx < 0:
        return content, False
    return body[:idx] + "\n".join(added) + body[idx + len(target):], True


def apply_patch(ws: Path, patch_text: str) -> dict[str, Any]:
    if not str(patch_text).strip():
        return {"error": "patch 不能为空"}
    if len(str(patch_text)) > MAX_PATCH_BYTES:
        return {"error": f"patch 过大（>{MAX_PATCH_BYTES // 1024}KB）"}
    ops = parse_patch(patch_text)
    if not ops:
        return {"error": "未解析到有效操作（*** Add/Update/Delete File）"}
    results: list[dict[str, Any]] = []
    for op in ops:
        try:
            path = _resolve_in_workspace(ws, op.path)
        except ValueError as e:
            return {"error": str(e), "results": results}
        try:
            if op.kind == "add":
                if path.exists():
                    return {"error": f"文件已存在: {op.path}", "results": results}
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(op.content + "\n", encoding="utf-8")
                results.append({"op": "add", "path": op.path, "ok": True})
            elif op.kind == "delete":
                if not path.exists():
                    return {"error": f"文件不存在: {op.path}", "results": results}
                path.unlink()
                results.append({"op": "delete", "path": op.path, "ok": True})
            else:  # update
                if not path.exists():
                    return {"error": f"文件不存在: {op.path}", "results": results}
                content = path.read_text(encoding="utf-8", errors="replace")
                ok_all = True
                for removed, added in op.hunks:
                    content, ok = _find_replace(content, removed, added)
                    if not ok:
                        return {"error": f"未找到待替换内容（{op.path}）: {removed[:3]}",
                                "results": results}
                if op.move_to:
                    new_path = _resolve_in_workspace(ws, op.move_to)
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    new_path.write_text(content, encoding="utf-8")
                    path.unlink()          # 移动语义：写入新路径后删除旧文件
                    results.append({"op": "update", "path": op.path, "ok": True,
                                    "move_to": op.move_to})
                    continue
                path.write_text(content, encoding="utf-8")
                results.append({"op": "update", "path": op.path, "ok": True})
        except OSError as e:
            return {"error": f"文件操作失败: {e}", "results": results}
    return {"results": results}



# ---------- read / list / plan ----------
def read_file(ws: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        path = _resolve_in_workspace(ws, str(arguments.get("path") or ""))
    except ValueError as e:
        return {"error": str(e)}
    if not path.exists() or not path.is_file():
        return {"error": f"文件不存在: {arguments.get('path')}"}
    if path.stat().st_size > MAX_FILE_BYTES:
        return {"error": f"文件过大（>{MAX_FILE_BYTES // 1024 // 1024}MB）"}
    return {"path": str(arguments.get("path")),
            "content": path.read_text(encoding="utf-8", errors="replace")}


def list_dir(ws: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        path = _resolve_in_workspace(ws, str(arguments.get("path") or "."))
    except ValueError as e:
        return {"error": str(e)}
    if not path.exists():
        return {"error": f"目录不存在: {arguments.get('path')}"}
    entries = []
    for p in sorted(path.iterdir())[:500]:
        entries.append({"name": p.name, "type": "dir" if p.is_dir() else "file",
                        "size": p.stat().st_size if p.is_file() else None})
    return {"path": str(arguments.get("path") or "."), "entries": entries}


def update_plan(arguments: dict[str, Any]) -> dict[str, Any]:
    plan = arguments.get("plan")
    if not isinstance(plan, list):
        return {"error": "plan 必须是数组"}
    return {"ok": True, "plan": plan}


# ---------- 统一入口（供 ToolRegistry.dispatch 调用） ----------
async def execute(executor: Executor, name: str, arguments: dict[str, Any],
                  session_id: str | None) -> dict[str, Any]:
    if name == "update_plan":
        return update_plan(arguments)  # 无需工作区
    ws = session_workspace(session_id)
    if name == "shell":
        return await run_shell(executor, ws, arguments)
    if name == "apply_patch":
        return apply_patch(ws, str(arguments.get("patch") or ""))
    if name == "read_file":
        return read_file(ws, arguments)
    if name == "list_dir":
        return list_dir(ws, arguments)
    if name == "update_plan":
        return update_plan(arguments)
    return {"error": f"未知 codex 工具: {name}"}
