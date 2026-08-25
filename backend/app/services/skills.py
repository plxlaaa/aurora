"""Skills 框架（文档 6）。

- 加载：启动时扫描 config/skills/*/manifest.yaml，构建 name→manifest 注册表。
- 选择：将 manifests 注入主模型 tools/functions 定义，由模型决定调用。
- 执行：以子进程调用 entry（run.py），stdin 传 JSON 参数、stdout 读 JSON 结果，限时超时。
- 沙箱感知：执行环境遵循 sandbox.mode（sandbox/local）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("aiassistant.skills")

SKILLS_DIR = os.environ.get("AI_ASSISTANT_SKILLS_DIR", "/opt/aiassistant/config/skills")

JSON_SCHEMA_MAP = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}


class Skill:
    def __init__(self, name: str, manifest: dict[str, Any], root: Path) -> None:
        self.name = name
        self.manifest = manifest
        self.root = root
        self.entry = root / manifest.get("entry", "run.py")
        self.timeout = float(manifest.get("timeout", 15))
        self.enabled = bool(manifest.get("enabled", True))

    def to_function_definition(self) -> dict[str, Any]:
        props: dict[str, Any] = {}
        required: list[str] = []
        for param in self.manifest.get("parameters", []):
            ptype = JSON_SCHEMA_MAP.get(param.get("type", "string"), "string")
            props[param["name"]] = {
                "type": ptype,
                "description": param.get("description", ""),
            }
            if param.get("required"):
                required.append(param["name"])
        schema: dict[str, Any] = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.manifest.get("description", ""),
                "parameters": schema,
            },
        }


class SkillsManager:
    def __init__(self, skills_dir: str = SKILLS_DIR, executor=None) -> None:
        self.skills_dir = Path(skills_dir)
        self.executor = executor  # 沙箱感知执行器，见 sandbox.py
        self._skills: dict[str, Skill] = {}
        self.reload()

    def reload(self) -> None:
        found: dict[str, Skill] = {}
        if self.skills_dir.exists():
            for manifest_path in sorted(self.skills_dir.glob("*/manifest.yaml")):
                try:
                    with open(manifest_path, encoding="utf-8") as fh:
                        manifest = yaml.safe_load(fh) or {}
                    name = manifest.get("name")
                    if not name:
                        logger.warning("跳过缺少 name 的 manifest: %s", manifest_path)
                        continue
                    found[name] = Skill(name, manifest, manifest_path.parent)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("加载 skill manifest 失败 %s: %s", manifest_path, exc)
        self._skills = found
        logger.info("已加载 %d 个 skills: %s", len(found), list(found))

    def list(self) -> list[dict[str, Any]]:
        return [
            {"name": s.name, "enabled": s.enabled, "description": s.manifest.get("description", "")}
            for s in self._skills.values()
        ]

    def function_definitions(self) -> list[dict[str, Any]]:
        return [s.to_function_definition() for s in self._skills.values() if s.enabled]

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        skill = self._skills.get(name)
        if skill is None:
            raise KeyError(f"skill 不存在: {name}")
        if not skill.enabled:
            raise PermissionError(f"skill 已禁用: {name}")
        if not skill.entry.exists():
            raise FileNotFoundError(f"skill 入口不存在: {skill.entry}")

        return await self._run_entry(skill, arguments)

    async def _run_entry(
        self, skill: Skill, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """以子进程运行 skill entry；stdin 传参数，stdout 读 JSON。"""
        env = os.environ.copy()
        env["SKILL_NAME"] = skill.name
        env["SKILL_ARGUMENTS"] = json.dumps(arguments, ensure_ascii=False)
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", str(skill.entry),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(skill.root),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=json.dumps(arguments).encode("utf-8")),
                timeout=skill.timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return {"error": f"skill 执行超时（>{skill.timeout}s）"}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"skill 启动失败: {exc}"}

        out_text = stdout.decode("utf-8", errors="replace").strip()
        err_text = stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            return {"error": f"skill 退出码 {proc.returncode}: {err_text or out_text}"}
        try:
            return json.loads(out_text)
        except json.JSONDecodeError:
            return {"result": out_text}
