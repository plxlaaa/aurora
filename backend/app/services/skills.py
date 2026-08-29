"""Skills 框架（文档 6）。

- 加载：启动时扫描 config/skills/*/manifest.yaml，构建 name→manifest 注册表。
- 消费：工具目录仅含 name/description；AI 通过 read_skill 按需读取完整说明，
        通过 run_skill 执行入口（stdin 传 JSON 参数、stdout 读 JSON 结果）。
- 管理：zip 整包导入（防 zip slip）、文件树浏览、文本文件在线读写、删除。
- 执行：以子进程调用 entry（run.py），限时超时；环境遵循 sandbox.mode。
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import shutil
import uuid
import zipfile
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

# zip 导入安全上限
ZIP_MAX_ENTRIES = 500
ZIP_MAX_TOTAL_BYTES = 20 * 1024 * 1024
ZIP_MAX_FILE_BYTES = 5 * 1024 * 1024
# 在线编辑单文件上限
MAX_TEXT_FILE_BYTES = 256 * 1024
NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".yaml", ".yml", ".json", ".sh", ".js", ".ts",
    ".html", ".css", ".csv", ".toml", ".ini", ".cfg", ".xml", ".sql",
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

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """修改 skill 的 enabled 开关并写回 manifest；返回是否找到。"""
        if not self.skills_dir.exists():
            return False
        for manifest_path in sorted(self.skills_dir.glob("*/manifest.yaml")):
            try:
                with open(manifest_path, encoding="utf-8") as fh:
                    manifest = yaml.safe_load(fh) or {}
            except Exception as exc:  # noqa: BLE001
                logger.warning("读取 manifest 失败 %s: %s", manifest_path, exc)
                continue
            if manifest.get("name") != name:
                continue
            manifest["enabled"] = enabled
            with open(manifest_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(manifest, fh, allow_unicode=True, sort_keys=False)
            self.reload()
            return True
        return False

    def list(self) -> list[dict[str, Any]]:
        return [
            {"name": s.name, "enabled": s.enabled, "description": s.manifest.get("description", "")}
            for s in self._skills.values()
        ]

    def directory_entries(self) -> list[dict[str, Any]]:
        """工具目录用的 Skill 摘要清单（仅名称与描述，按需读取详情）。"""
        return [
            {"name": s.name, "description": s.manifest.get("description", "")}
            for s in self._skills.values()
            if s.enabled
        ]

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def function_definitions(self) -> list[dict[str, Any]]:
        """保留兼容：schema 转换逻辑供测试与未来直连模式使用；主流程不再注入。"""
        return [s.to_function_definition() for s in self._skills.values() if s.enabled]

    def read_skill(self, name: str) -> dict[str, Any]:
        """read_skill 内置工具：返回 Skill 的完整使用说明。"""
        skill = self._skills.get(name)
        if skill is None:
            available = ", ".join(sorted(self._skills)) or "（无）"
            return {"error": f"skill 不存在: {name}。可用 skills: {available}"}
        doc_name = skill.manifest.get("doc", "SKILL.md")
        doc = ""
        if doc_name:
            doc_path = self._safe_path(skill.root, str(doc_name))
            if doc_path.exists():
                try:
                    doc = doc_path.read_text(encoding="utf-8", errors="replace")[:20000]
                except OSError as exc:
                    logger.warning("读取 skill 文档失败 %s: %s", doc_path, exc)
        return {
            "name": skill.name,
            "description": skill.manifest.get("description", ""),
            "entry": skill.manifest.get("entry", "run.py"),
            "timeout": skill.timeout,
            "parameters": skill.manifest.get("parameters", []),
            "doc": doc,
            "doc_note": (
                "以上 parameters 为 run_skill 的 arguments 字段结构；"
                "doc 为使用文档。调用 run_skill(name=..., arguments={...}) 执行。"
            ),
        }

    # -- 文件管理 --
    @staticmethod
    def _safe_path(root: Path, rel: str) -> Path:
        """规范化相对路径并强制限制在 root 内，防路径穿越。"""
        rel = str(rel or "").lstrip("/\\")
        target = (root / rel).resolve()
        root_resolved = root.resolve()
        if target != root_resolved and root_resolved not in target.parents:
            raise ValueError(f"路径越界: {rel}")
        return target

    def import_zip(self, data: bytes, *, expected_name: str | None = None) -> dict[str, Any]:
        """导入 zip 压缩包为 Skill；校验 manifest、防 zip slip、超限拦截。

        返回 {"name": ..., "files": n}；失败抛 ValueError。
        """
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ValueError(f"非法 zip 文件: {exc}") from exc

        names = zf.namelist()
        if not names or len(names) > ZIP_MAX_ENTRIES:
            raise ValueError(f"zip 条目数超限（1-{ZIP_MAX_ENTRIES}）")
        total = sum(info.file_size for info in zf.infolist())
        if total > ZIP_MAX_TOTAL_BYTES:
            raise ValueError(f"zip 解压后总大小超限（>{ZIP_MAX_TOTAL_BYTES // (1024 * 1024)}MB）")

        # 定位 manifest：先看根目录，再看唯一顶层目录前缀
        prefix = ""
        manifest_name = None
        for cand in ("manifest.yaml", "manifest.yml"):
            if cand in names:
                manifest_name = cand
                break
        if manifest_name is None:
            first_parts = {n.split("/")[0] for n in names if "/" in n}
            if len(first_parts) == 1:
                prefix = next(iter(first_parts)) + "/"
                for cand in ("manifest.yaml", "manifest.yml"):
                    if prefix + cand in names:
                        manifest_name = prefix + cand
                        break
        if manifest_name is None:
            raise ValueError("zip 中未找到 manifest.yaml / manifest.yml")

        try:
            manifest = yaml.safe_load(zf.read(manifest_name).decode("utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"manifest 解析失败: {exc}") from exc
        skill_name = manifest.get("name")
        if not isinstance(skill_name, str) or not NAME_RE.match(skill_name):
            raise ValueError("manifest 缺少合法 name 字段（字母开头，字母/数字/下划线）")
        if expected_name and expected_name != skill_name:
            raise ValueError(f"manifest name={skill_name} 与指定名称 {expected_name} 不一致")
        if skill_name in {"read_skill", "run_skill", "run_code"}:
            raise ValueError("skill 名与内置工具冲突")

        target = self.skills_dir / skill_name
        backup = None
        if target.exists():
            # 用唯一后缀而非秒级时间戳，避免同一秒内重复导入时备份互相覆盖
            backup = self.skills_dir / f".bak-{skill_name}-{uuid.uuid4().hex[:8]}"
            target.rename(backup)

        files_written = 0
        try:
            for info in zf.infolist():
                rel = info.filename
                if rel.endswith("/"):
                    continue
                if prefix and rel.startswith(prefix):
                    rel = rel[len(prefix):]
                if rel.startswith("/") or ".." in Path(rel).parts:
                    raise ValueError(f"zip 条目路径非法: {info.filename}")
                if info.file_size > ZIP_MAX_FILE_BYTES:
                    raise ValueError(f"文件过大: {info.filename}")
                dest = self._safe_path(target, rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                files_written += 1
            self.reload()
        except Exception:
            # 失败回滚：删除半成品并还原备份
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            if backup is not None and backup.exists():
                backup.rename(target)
            raise

        # 导入成功后清理旧备份
        if backup is not None and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        logger.info("导入 skill %s（%d 个文件）", skill_name, files_written)
        return {"name": skill_name, "files": files_written}

    def list_files(self, name: str) -> list[dict[str, Any]]:
        """返回 Skill 目录文件树（相对路径 + 大小）。"""
        skill = self._skills.get(name)
        if skill is None:
            raise KeyError(f"skill 不存在: {name}")
        out: list[dict[str, Any]] = []
        for path in sorted(skill.root.rglob("*")):
            if path.is_file():
                out.append(
                    {
                        "path": str(path.relative_to(skill.root)),
                        "size": path.stat().st_size,
                        "text": path.suffix.lower() in TEXT_SUFFIXES,
                    }
                )
        return out

    def read_file(self, name: str, rel_path: str) -> str:
        skill = self._skills.get(name)
        if skill is None:
            raise KeyError(f"skill 不存在: {name}")
        path = self._safe_path(skill.root, rel_path)
        if not path.is_file():
            raise FileNotFoundError(f"文件不存在: {rel_path}")
        if path.suffix.lower() not in TEXT_SUFFIXES:
            raise ValueError("仅支持读取文本文件")
        if path.stat().st_size > MAX_TEXT_FILE_BYTES:
            raise ValueError("文件过大，仅支持读取 256KB 以内的文本文件")
        return path.read_text(encoding="utf-8", errors="replace")

    def write_file(self, name: str, rel_path: str, content: str) -> None:
        skill = self._skills.get(name)
        if skill is None:
            raise KeyError(f"skill 不存在: {name}")
        path = self._safe_path(skill.root, rel_path)
        if path.suffix.lower() not in TEXT_SUFFIXES:
            raise ValueError("仅支持写入文本文件")
        data = content.encode("utf-8")
        if len(data) > MAX_TEXT_FILE_BYTES:
            raise ValueError("内容超过 256KB 上限")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.reload()

    def delete(self, name: str) -> bool:
        """删除整个 Skill 目录（管理操作，前端已二次确认）。"""
        skill = self._skills.pop(name, None)
        if skill is None:
            return False
        shutil.rmtree(skill.root, ignore_errors=True)
        logger.info("已删除 skill %s", name)
        return True

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
