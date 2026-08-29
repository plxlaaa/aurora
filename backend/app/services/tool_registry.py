"""工具注册与聚合。

统一管理四类工具来源：
- 内置工具：read_skill / run_skill / run_code
- Skills：目录摘要 + 按需读取（read_skill）+ 执行（run_skill）
- MCP 工具：原生 function 定义注入
- 自定义工具：HTTP / Python

职责：
1. collect_tools()   —— 输出全部可用工具的 OpenAI function definitions
2. build_directory() —— 生成注入 system prompt 的工具目录 + 文本协议说明
3. dispatch()        —— 按工具名统一路由执行
4. snapshot()        —— 前端工具中心数据
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("aiassistant.tools")

BUILTIN_TOOL_NAMES = ("read_skill", "run_skill", "run_code")

_REACT_INSTRUCTIONS = """\
## 工具调用方式
当你需要使用工具完成任务时，若本次请求提供了原生工具调用通道则优先使用原生工具调用；
否则严格按以下格式输出调用标记（每次只调用一个工具，标记外可以有简短说明文字）：
<tool_call>{"name": "工具名", "arguments": {"参数名": "值"}}</tool_call>
系统会执行工具并以 <tool_result name="工具名">...</tool_result> 的 user 消息返回结果，
收到结果后请继续推理直至给出最终回答。"""


class ToolRegistry:
    def __init__(self, skills, mcp, custom, executor=None) -> None:
        self.skills = skills
        self.mcp = mcp
        self.custom = custom
        self.executor = executor
        self._tools_cache: dict[str, Any] | None = None

    # -- function definitions --
    def collect_tools(self) -> list[dict[str, Any]]:
        if self._tools_cache is not None:
            return self._tools_cache
        defs: list[dict[str, Any]] = [self._builtin_definitions()[n] for n in BUILTIN_TOOL_NAMES]
        defs += self.custom.function_definitions()
        defs += self.mcp.function_definitions()
        self._tools_cache = defs
        return defs

    def invalidate_tools_cache(self) -> None:
        """在工具配置变更后调用，以便下次调用 collect_tools 时重新构建。"""
        self._tools_cache = None

    @staticmethod
    def _builtin_definitions() -> dict[str, dict[str, Any]]:
        return {
            "read_skill": {
                "type": "function",
                "function": {
                    "name": "read_skill",
                    "description": "读取指定 Skill 的完整使用说明（参数结构、入口、使用文档）。"
                    "使用任何 Skill 前必须先调用本工具了解用法。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Skill 名称"}
                        },
                        "required": ["name"],
                    },
                },
            },
            "run_skill": {
                "type": "function",
                "function": {
                    "name": "run_skill",
                    "description": "执行指定 Skill（先 read_skill 了解参数结构）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Skill 名称"},
                            "arguments": {
                                "type": "object",
                                "description": "Skill 参数（结构见 read_skill 返回）",
                            },
                        },
                        "required": ["name"],
                    },
                },
            },
            "run_code": {
                "type": "function",
                "function": {
                    "name": "run_code",
                    "description": "在沙箱中执行一段 Python 代码并返回 stdout/stderr/exit_code。"
                    "代码应为完整脚本，需要结果时用 print() 输出。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string", "description": "Python 代码"}
                        },
                        "required": ["code"],
                    },
                },
            },
        }

    # -- 工具目录 --
    def build_directory(self) -> str:
        """生成 system prompt 使用的工具目录文本（空分组省略）。"""
        sections: list[str] = ["# 可用工具目录\n你可以使用以下工具："]

        builtin = self._builtin_definitions()
        lines = [f"- {n}: {builtin[n]['function']['description']}" for n in BUILTIN_TOOL_NAMES]
        sections.append("## 内置工具\n" + "\n".join(lines))

        skills = self.skills.directory_entries()
        if skills:
            lines = [f"- {s['name']}: {s['description'] or '（无描述）'}" for s in skills]
            sections.append(
                "## Skills\n" + "\n".join(lines)
                + "\n（使用前先 read_skill 查看用法，再用 run_skill 执行）"
            )

        mcp_tools = self.mcp.function_definitions()
        if mcp_tools:
            lines = [
                f"- {d['function']['name']}: {d['function']['description']}" for d in mcp_tools
            ]
            sections.append("## MCP 工具\n" + "\n".join(lines))

        custom = [t for t in self.custom.directory_entries() if t.get("enabled", True)]
        if custom:
            lines = [f"- {t['name']}: {t['description']}" for t in custom]
            sections.append("## 自定义工具\n" + "\n".join(lines))

        return "\n\n".join(sections) + "\n\n" + _REACT_INSTRUCTIONS

    # -- 执行分发 --
    async def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "read_skill":
            return self.skills.read_skill(str(arguments.get("name", "")))
        if name == "run_skill":
            skill_name = str(arguments.get("name", ""))
            skill_args = arguments.get("arguments")
            if not isinstance(skill_args, dict):
                skill_args = {}
            return await self.skills.execute(skill_name, skill_args)
        if name == "run_code":
            code = str(arguments.get("code", ""))
            if not code.strip():
                return {"error": "code 不能为空"}
            exec_result = await self.executor.execute(code)
            return {
                "stdout": exec_result.stdout[:8000],
                "stderr": exec_result.stderr[:4000],
                "exit_code": exec_result.exit_code,
            }
        if name.startswith("mcp_"):
            return await self.mcp.dispatch(name, arguments)
        custom_tool = self.custom.get(name)
        if custom_tool is not None:
            return await self.custom.execute(name, arguments)
        # 兼容历史会话中直接以 skill 名发起的调用
        if self.skills.get(name) is not None:
            return await self.skills.execute(name, arguments)
        raise KeyError(f"未知工具: {name}")

    # -- 前端快照 --
    def snapshot(self) -> dict[str, Any]:
        # 使用已缓存的 tools 列表（若有）
        if self._tools_cache is None:
            self.collect_tools()
        builtin = self._builtin_definitions()
        items = [
            {
                "name": n,
                "source": "builtin",
                "description": builtin[n]["function"]["description"],
                "enabled": True,
            }
            for n in BUILTIN_TOOL_NAMES
        ]
        items += [
            {
                "name": t["name"],
                "source": "skill",
                "description": t.get("description", ""),
                "enabled": t.get("enabled", True),
            }
            for t in self.skills.list()
        ]
        items += [
            {
                "name": t["name"],
                "source": "custom",
                "description": t.get("description", ""),
                "type": t.get("type"),
                "enabled": t.get("enabled", True),
                "parameters": t.get("parameters"),
            }
            for t in self.custom.list()
        ]
        return {"builtin": list(BUILTIN_TOOL_NAMES), "tools": items}

    def name_exists(self, name: str) -> bool:
        """供自定义工具保存时的名称冲突校验。"""
        if name in BUILTIN_TOOL_NAMES or name.startswith("mcp_"):
            return True
        if self.skills.get(name) is not None:
            return True
        if self.custom.get(name) is not None:
            return True
        for d in self.mcp.function_definitions():
            if d["function"]["name"] == name:
                return True
        return False


def result_json(result: Any) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)
