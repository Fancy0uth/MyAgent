"""在项目专属 Docker 容器中受控执行 bash 命令。

这个模块是 L15 的隔离执行器。它和 ``execution.py`` 中的
``TestExecutor`` 有意分开：固定测试仍然是默认安全路径，只有明确
创建并调用 ``SandboxExecutor`` 时，才会开放容器内的 bash。
"""

from pathlib import Path
import os
import shutil
import subprocess
import time
from threading import Event
from typing import Dict, Optional, Tuple
from uuid import uuid4

from .execution import (
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    _collect_output,
)


DEFAULT_SANDBOX_IMAGE = "myagent-sandbox:py313"


def _failure(error: str, message: str) -> dict:
    """统一返回执行器自己的参数或启动错误。"""

    return {"ok": False, "error": error, "message": message}


def _resolve_docker_executable() -> str:
    """查找 Docker CLI，并兼容 Docker Desktop 的用户级安装路径。"""

    executable = shutil.which("docker")
    if executable:
        return executable

    user_executable = Path.home() / ".docker" / "bin" / "docker"
    if user_executable.is_file():
        return str(user_executable)

    # 让 Popen 最终生成清晰的“找不到 docker”错误，而不是在这里
    # 把机器环境问题伪装成参数错误。
    return "docker"


class SandboxExecutor:
    """在临时 Docker 容器中执行一次受限 bash 命令。"""

    def __init__(
        self,
        *,
        image: str = DEFAULT_SANDBOX_IMAGE,
        workspace_root: Path,
        docker_executable: Optional[str] = None,
    ) -> None:
        if not isinstance(image, str) or not image.strip():
            raise ValueError("image 必须是非空字符串")

        root = Path(workspace_root)
        if root.is_symlink() or not root.is_dir():
            raise ValueError("workspace_root 必须是已经存在的普通目录")

        self._image = image
        self._workspace_root = root.resolve()
        self._docker = docker_executable or _resolve_docker_executable()

    def run_bash(
        self,
        command: str,
        workspace: Path,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        cancel_event: Optional[Event] = None,
    ) -> dict:
        """在隔离容器中运行命令，并返回结构化结果。

        ``command`` 会作为数据传给容器内的 ``bash -lc``；宿主机侧
        启动 Docker 仍使用参数数组和 ``shell=False``，所以命令不会
        先经过宿主机 Shell。
        """
        if not isinstance(command, str) or not command.strip():
            return _failure("INVALID_COMMAND", "command 必须是非空字符串")
        if not isinstance(timeout_seconds, (int, float)) or isinstance(
            timeout_seconds, bool
        ) or timeout_seconds <= 0:
            return _failure("INVALID_TIMEOUT", "timeout_seconds 必须大于 0")
        if not isinstance(max_output_bytes, int) or isinstance(
            max_output_bytes, bool
        ) or max_output_bytes <= 0:
            return _failure("INVALID_OUTPUT_LIMIT", "max_output_bytes 必须大于 0")

        workspace_path, workspace_error = self._resolve_workspace(workspace)
        if workspace_error is not None:
            return workspace_error
        assert workspace_path is not None

        # 启动前取消时，不创建 Docker 容器。
        if cancel_event is not None and cancel_event.is_set():
            return {
                "ok": False,
                "error": "CANCELLED",
                "message": "命令在启动前已取消",
                "cancelled": True,
            }

        container_name = f"myagent-sandbox-{uuid4().hex[:12]}"
        docker_args = self._build_docker_args(
            container_name=container_name,
            command=command,
            workspace=workspace_path,
        )
        started_at = time.monotonic()

        try:
            process = subprocess.Popen(
                docker_args,
                env=self._docker_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                # Docker 客户端也使用独立进程组，取消时不会把当前 Python
                # 进程或无关进程一起终止。
                start_new_session=(os.name == "posix"),
            )
        except OSError as exc:
            return _failure("EXECUTION_ERROR", f"无法启动 Docker：{exc}")

        def stop_container() -> None:
            """停止容器本身；不能只杀掉本地 docker 客户端。"""

            self._docker_action("kill", container_name)

        try:
            (
                stdout_bytes,
                stderr_bytes,
                truncated,
                timed_out,
                cancelled,
            ) = _collect_output(
                process,
                timeout_seconds=float(timeout_seconds),
                max_output_bytes=max_output_bytes,
                cancel_event=cancel_event,
                on_terminate=stop_container,
            )
        finally:
            # 正常结束时 --rm 会自动清理；异常、取消或竞态情况下再执行
            # 一次 rm -f，确保不会留下本次任务的容器。
            self._docker_action("rm", "--force", container_name)

        return {
            "ok": True,
            "exit_code": process.returncode,
            "stdout": stdout_bytes.decode("utf-8", errors="replace"),
            "stderr": stderr_bytes.decode("utf-8", errors="replace"),
            "timed_out": timed_out,
            "cancelled": cancelled,
            "truncated": truncated,
            "duration_ms": int((time.monotonic() - started_at) * 1000),
        }

    def _resolve_workspace(self, workspace: Path) -> Tuple[Optional[Path], Optional[dict]]:
        """只允许挂载 workspace_root 内的普通目录。"""
        try:
            candidate = Path(workspace)
        except TypeError:
            return None, _failure("WORKSPACE_NOT_ALLOWED", "workspace 必须是路径")

        if candidate.is_symlink() or not candidate.is_dir():
            return None, _failure(
                "WORKSPACE_NOT_ALLOWED",
                "workspace 必须是已经存在的普通目录",
            )

        resolved = candidate.resolve()
        try:
            resolved.relative_to(self._workspace_root)
        except ValueError:
            return None, _failure(
                "WORKSPACE_NOT_ALLOWED",
                "workspace 必须位于配置的 workspace_root 内",
            )
        return resolved, None

    def _build_docker_args(
        self,
        *,
        container_name: str,
        command: str,
        workspace: Path,
    ) -> list[str]:
        """构造 Docker 参数数组；这里不使用宿主机 Shell。"""
        return [
            self._docker,
            "run",
            "--rm",
            "--name",
            container_name,
            "--init",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--workdir",
            "/workspace",
            "--mount",
            # bind mount 默认可写；这里不写无效的 `rw` 字段，避免
            # Docker 把它当成缺少等号的 mount 配置而拒绝启动。
            f"type=bind,src={workspace},dst=/workspace",
            self._image,
            "bash",
            "-lc",
            command,
        ]

    def _docker_environment(self) -> Dict[str, str]:
        """只为 Docker 客户端保留必要变量，不传递模型凭据。"""
        current_path = os.environ.get("PATH", "")
        user_bin = str(Path.home() / ".docker" / "bin")
        allowed_names = ("HOME", "LANG", "LC_ALL", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")
        environment = {
            name: os.environ[name]
            for name in allowed_names
            if name in os.environ
        }
        environment["PATH"] = user_bin + os.pathsep + current_path
        environment.setdefault("HOME", str(Path.home()))
        return environment

    def _docker_action(self, action: str, *arguments: str) -> None:
        """执行清理动作；失败时不覆盖原始测试结果。"""
        try:
            subprocess.run(
                [self._docker, action, *arguments],
                env=self._docker_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
