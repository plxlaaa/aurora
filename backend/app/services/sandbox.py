"""沙箱 / 本地可切换执行环境（文档 8）。

统一入口 execute() 依据 sandbox.mode 路由：
- sandbox：docker run --rm 临时容器，带内存/CPU/网络/只读安全限制。
- local：仅在 local.allow=true 时于宿主 local.workdir 执行，同样限时。

安全参数全部来自 sandbox.yaml 并强制注入。
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("aiassistant.sandbox")


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    mode: str = ""


@dataclass
class SandboxConfig:
    docker: dict[str, Any] = field(default_factory=dict)
    local: dict[str, Any] = field(default_factory=dict)


class Executor:
    def __init__(self, settings) -> None:
        self._settings = settings

    def _cfg(self) -> SandboxConfig:
        sb = self._settings.sandbox()
        docker = dict(sb.get("docker", {}))
        local = dict(sb.get("local", {}))
        env_workdir = os.environ.get("AI_ASSISTANT_SANDBOX_LOCAL_DIR")
        if env_workdir:
            local["workdir"] = env_workdir
            mount = docker.get("mount")
            if mount and ":" in mount:
                host_part = mount.split(":", 1)[0]
                docker["mount"] = f"{env_workdir}:{mount.split(':', 1)[1]}"
        return SandboxConfig(
            docker=docker,
            local=local,
        )

    async def execute(self, code: str, *, script: bool = False) -> ExecResult:
        mode = self._settings.sandbox_mode()
        if mode == "sandbox":
            return await self._run_sandbox(code, script=script)
        if mode == "local":
            return await self._run_local(code, script=script)
        return ExecResult("", f"未知执行模式: {mode}", -1, mode=mode)

    # -- sandbox 模式 --
    async def _run_sandbox(self, code: str, *, script: bool) -> ExecResult:
        d = self._cfg().docker
        image = d.get("image", "python:3.12-slim")
        workdir = d.get("workdir", "/workspace")
        timeout = float(d.get("timeout", 30))
        network = d.get("network", "none")
        mount = d.get("mount")
        read_only = bool(d.get("read_only", True))

        def build_cmd(script_path: str | None) -> list[str]:
            cmd: list[str] = ["docker", "run", "--rm"]
            cmd += ["--memory", str(d.get("mem_limit", "512m"))]
            cmd += ["--cpus", str(int(d.get("cpu_quota", 50000)) / 100000)]
            cmd += ["--network", network]
            if read_only:
                cmd += ["--read-only"]
            if mount:
                cmd += ["-v", mount]
            if d.get("env"):
                for k, v in d["env"].items():
                    cmd += ["-e", f"{k}={v}"]
            cmd += ["-w", workdir]
            # 挂载参数必须在镜像名之前，否则 docker 会将其视为容器命令参数（挂载失效）
            if script_path is not None:
                cmd += ["-v", f"{script_path}:/tmp/sandbox_script.py:ro"]
            cmd += [image, "python3"]
            if script_path is not None:
                cmd += ["/tmp/sandbox_script.py"]
            else:
                cmd += ["-c", code]
            return cmd

        tmp = None
        try:
            if script:
                tmp = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".py", prefix="sandbox_", encoding="utf-8"
                )
                tmp.write(code)
                tmp.flush()
                cmd = build_cmd(tmp.name)
            else:
                cmd = build_cmd(None)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ExecResult("", f"执行超时（>{timeout}s）", -1, timed_out=True, mode="sandbox")
            return ExecResult(
                stdout.decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace"),
                proc.returncode or 0,
                mode="sandbox",
            )
        except FileNotFoundError:
            return ExecResult("", "docker 未安装或不可用", -1, mode="sandbox")
        except Exception as exc:  # noqa: BLE001
            return ExecResult("", f"沙箱执行失败: {exc}", -1, mode="sandbox")
        finally:
            if tmp is not None:
                try:
                    tmp.close()
                except Exception:  # noqa: BLE001
                    pass

    # -- local 模式 --
    async def _run_local(self, code: str, *, script: bool) -> ExecResult:
        lc = self._cfg().local
        if not lc.get("allow", False):
            return ExecResult(
                "",
                "local 模式未开启（sandbox.local.allow=false），拒绝执行",
                -1,
                mode="local",
            )
        workdir = lc.get("workdir", "/opt/aiassistant/sandbox")
        timeout = float(lc.get("timeout", 30))
        os.makedirs(workdir, exist_ok=True)
        try:
            if script:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".py", prefix="local_", encoding="utf-8"
                ) as tmp:
                    tmp.write(code)
                    tmp.flush()
                    proc = await asyncio.create_subprocess_exec(
                        "python3", tmp.name,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=workdir,
                    )
            else:
                proc = await asyncio.create_subprocess_exec(
                    "python3", "-c", code,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=workdir,
                )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ExecResult("", f"执行超时（>{timeout}s）", -1, timed_out=True, mode="local")
            return ExecResult(
                stdout.decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace"),
                proc.returncode or 0,
                mode="local",
            )
        except Exception as exc:  # noqa: BLE001
            return ExecResult("", f"local 执行失败: {exc}", -1, mode="local")
