"""用户自定义工具（HTTP 模板型 / Python 代码型）。

- 存储：config/custom_tools.yaml，由本模块全权管理读写。
- HTTP 型：URL {param} 占位替换，GET 走 query、其余方法走 JSON/form body。
- Python 型：用户代码约定定义 run(args) -> dict；网关拼接固定 runner 后
  交给沙箱执行器（Executor）运行，受 sandbox.yaml 限流约束。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import yaml

logger = logging.getLogger("aiassistant.custom_tools")

NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
BUILTIN_NAMES = {"read_skill", "run_skill", "run_code"}
VALID_TYPES = {"http", "python"}
MAX_RESULT_CHARS = 8000
_DEFAULT_TIMEOUT = 20.0

# 拼接在用户代码之后的固定 runner：调用 run(args) 并以 JSON 打印结果
_PYTHON_RUNNER = '''

import json as _json

try:
    _result = run(args)
    _error = None
except BaseException as _exc:  # noqa: BLE001
    _result = None
    _error = f"{type(_exc).__name__}: {_exc}"
print(_json.dumps({"result": _result, "error": _error}, ensure_ascii=False, default=str))
'''


def validate_tool(tool: dict[str, Any]) -> str | None:
    """校验工具定义，返回错误信息；合法时返回 None。"""
    name = tool.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        return "工具名须以字母开头，仅含字母/数字/下划线，长度 1-64"
    if name in BUILTIN_NAMES:
        return f"工具名与内置工具冲突: {name}"
    if tool.get("type") not in VALID_TYPES:
        return "type 必须为 http 或 python"
    if not str(tool.get("description", "")).strip():
        return "description 不能为空"
    params = tool.get("parameters")
    if params is None:
        tool["parameters"] = {"type": "object", "properties": {}}
    elif not isinstance(params, dict) or params.get("type") != "object":
        return "parameters 必须为 type=object 的 JSON Schema"

    if tool["type"] == "http":
        cfg = tool.get("http")
        if not isinstance(cfg, dict) or not str(cfg.get("url", "")).strip():
            return "http 工具必须提供 url"
        method = str(cfg.get("method", "GET")).upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
            return f"不支持的 HTTP 方法: {method}"
        cfg["method"] = method
        if not isinstance(cfg.get("headers", {}), dict):
            return "headers 必须为对象"
    else:
        code = tool.get("code")
        if not isinstance(code, str) or "def run(" not in code:
            return "python 工具代码必须定义 run(args) 函数"
    return None


class CustomToolsManager:
    def __init__(self, config_dir: str, executor=None) -> None:
        self.path = Path(config_dir) / "custom_tools.yaml"
        self._executor = executor
        self._tools: dict[str, dict[str, Any]] = {}
        self._load()

    # -- 持久化 --
    def _load(self) -> None:
        if not self.path.exists():
            self._tools = {}
            return
        try:
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            tools = data.get("tools", [])
            self._tools = {t["name"]: t for t in tools if isinstance(t, dict) and t.get("name")}
        except Exception as exc:  # noqa: BLE001
            logger.warning("加载 custom_tools.yaml 失败: %s", exc)
            self._tools = {}

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"tools": list(self._tools.values())}
        self.path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    # -- CRUD --
    def list(self) -> list[dict[str, Any]]:
        """元数据列表（不含代码/请求头等敏感细节）。"""
        return [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "type": t.get("type"),
                "enabled": t.get("enabled", True),
                "parameters": t.get("parameters"),
            }
            for t in self._tools.values()
        ]

    def get(self, name: str) -> dict[str, Any] | None:
        return self._tools.get(name)

    def save(self, tool: dict[str, Any], *, overwrite: bool = False) -> None:
        err = validate_tool(tool)
        if err:
            raise ValueError(err)
        if not overwrite and tool["name"] in self._tools:
            raise ValueError(f"自定义工具已存在: {tool['name']}")
        self._tools[tool["name"]] = tool
        self._persist()

    def delete(self, name: str) -> bool:
        if name in self._tools:
            del self._tools[name]
            self._persist()
            return True
        return False

    def set_enabled(self, name: str, enabled: bool) -> bool:
        tool = self._tools.get(name)
        if tool is None:
            return False
        tool["enabled"] = enabled
        self._persist()
        return True

    # -- 工具定义 --
    def function_definitions(self) -> list[dict[str, Any]]:
        defs: list[dict[str, Any]] = []
        for t in self._tools.values():
            if not t.get("enabled", True):
                continue
            defs.append(
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters")
                        or {"type": "object", "properties": {}},
                    },
                }
            )
        return defs

    def directory_entries(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "type": t.get("type"),
                "enabled": t.get("enabled", True),
            }
            for t in self._tools.values()
        ]

    # -- 执行 --
    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"自定义工具不存在: {name}")
        if not tool.get("enabled", True):
            raise PermissionError(f"自定义工具已禁用: {name}")
        if tool["type"] == "http":
            result = await self._run_http(tool, arguments)
        else:
            result = await self._run_python(tool, arguments)
        return _truncate_result(result)

    async def _run_http(self, tool: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        cfg = tool.get("http") or {}
        url: str = cfg["url"]
        leftover = dict(arguments or {})
        # URL {param} 占位替换（URL 编码），替换过的参数不再进入 query/body
        for key in list(leftover):
            token = "{" + key + "}"
            if token in url:
                url = url.replace(token, quote(str(leftover.pop(key)), safe=""))
        if "{" in url and "}" in url:
            return {"error": f"URL 中存在未提供的参数占位: {url}"}

        method = str(cfg.get("method", "GET")).upper()
        headers = {
            k: _fill_template(str(v), arguments or {})
            for k, v in (cfg.get("headers") or {}).items()
        }
        kwargs: dict[str, Any] = {"headers": headers, "timeout": float(cfg.get("timeout", _DEFAULT_TIMEOUT))}
        if method in {"GET", "HEAD"}:
            if leftover:
                kwargs["params"] = leftover
        else:
            body_mode = cfg.get("body_mode", "json")
            if body_mode == "form":
                kwargs["data"] = {k: str(v) for k, v in leftover.items()}
            elif leftover:
                kwargs["json"] = leftover

        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                resp = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            return {"error": f"HTTP 请求失败: {exc}"}
        text = resp.text[:MAX_RESULT_CHARS]
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}: {text}"}
        try:
            return {"result": resp.json()}
        except ValueError:
            return {"result": text}

    async def _run_python(self, tool: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        inner = json.dumps(arguments or {}, ensure_ascii=False)
        script = tool["code"] + f"\n\nimport json\nargs = json.loads({inner!r})\n" + _PYTHON_RUNNER
        if self._executor is None:
            return {"error": "执行器未配置"}
        exec_result = await self._executor.execute(script, script=True)
        if exec_result.timed_out:
            return {"error": "执行超时"}
        if exec_result.exit_code != 0:
            detail = (exec_result.stderr or exec_result.stdout).strip()[:2000]
            return {"error": f"执行失败(exit={exec_result.exit_code}): {detail}"}
        out = exec_result.stdout.strip()
        try:
            parsed = json.loads(out)
        except ValueError:
            return {"error": f"runner 输出非法: {out[:500]}"}
        if parsed.get("error"):
            return {"error": str(parsed["error"])}
        return {"result": parsed.get("result")}


def _fill_template(text: str, arguments: dict[str, Any]) -> str:
    for key, value in arguments.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def _truncate_result(result: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(result, ensure_ascii=False)
    if len(text) <= MAX_RESULT_CHARS:
        return result
    return {"result": text[:MAX_RESULT_CHARS] + "…（结果过长已截断）"}
