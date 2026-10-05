"""受控运行预登记测试的工具。

这里故意不接受模型传入的 Shell 字符串，只接受已经登记好的 case_id。
当前实现是受控的本地子进程执行器，不等同于操作系统级沙箱；更强的
容器隔离会在后续课程单独实现。
"""

from dataclasses import dataclass
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from threading import Event
from typing import Callable, Dict, Optional, Tuple


DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_OUTPUT_BYTES = 16 * 1024
_READ_SIZE = 64 * 1024


def _failure(error: str, message: str, case_id: Optional[str] = None) -> dict:
    """统一生成执行器自身的失败结果。"""
    result = {"ok": False, "error": error, "message": message}
    if case_id is not None:
        result["case_id"] = case_id
    return result


@dataclass(frozen=True)
class TestCase:
    """由可信程序预先登记的一组固定测试参数。"""

    case_id: str
    workspace: Path
    argv: Tuple[str, ...]
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("case_id 必须是非空字符串")
        if isinstance(self.argv, str) or not self.argv:
            raise ValueError("argv 必须是非空参数序列，不能是 Shell 字符串")
        if any(not isinstance(argument, str) or not argument for argument in self.argv):
            raise ValueError("argv 中的每个参数都必须是非空字符串")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes 必须大于 0")

        # 统一保存为 Path，方便后续检查工作区目录。
        object.__setattr__(self, "workspace", Path(self.workspace))
        object.__setattr__(self, "argv", tuple(self.argv))


class TestCaseRegistry:
    """保存可信代码预先登记的测试案例，不接受模型临时注册。"""

    def __init__(self) -> None:
        self._cases: Dict[str, TestCase] = {}

    def register(self, case: TestCase) -> None:
        """登记一个案例；重复 ID 会被拒绝，避免静默替换命令。"""
        if case.case_id in self._cases:
            raise ValueError(f"测试案例已经存在：{case.case_id}")
        self._cases[case.case_id] = case

    def get(self, case_id: str) -> Optional[TestCase]:
        """按 ID 查找案例。"""
        return self._cases.get(case_id)


class TestExecutor:
    """执行登记的测试，并返回可供 Agent 判断的结构化结果。"""

    def __init__(self, registry: TestCaseRegistry) -> None:
        self._registry = registry

    def run_tests(
        self,
        case_id: str,
        *,
        cancel_event: Optional[Event] = None,
    ) -> dict:
        """运行预登记案例；case_id 之外不接受命令、路径或 Shell 参数。"""
        if not isinstance(case_id, str) or not case_id.strip():
            return _failure("INVALID_CASE", "case_id 必须是非空字符串")

        case = self._registry.get(case_id)
        if case is None:
            return _failure("INVALID_CASE", "测试案例未登记", case_id)

        if not case.workspace.is_dir() or case.workspace.is_symlink():
            return _failure("INVALID_CASE", "测试工作区不存在或不是普通目录", case_id)

        # 启动前就取消时，不创建任何子进程。
        if cancel_event is not None and cancel_event.is_set():
            return {
                "ok": False,
                "error": "CANCELLED",
                "message": "测试在启动前已取消",
                "case_id": case_id,
                "cancelled": True,
            }

        environment = _build_safe_environment()
        started_at = time.monotonic()

        try:
            process = subprocess.Popen(
                list(case.argv),
                cwd=case.workspace,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                # POSIX 下让测试及其子进程成为独立进程组，超时时可以一起结束。
                start_new_session=(os.name == "posix"),
            )
        except OSError as exc:
            return _failure("EXECUTION_ERROR", f"无法启动测试：{exc}", case_id)

        (
            stdout_bytes,
            stderr_bytes,
            truncated,
            timed_out,
            cancelled,
        ) = _collect_output(
            process,
            timeout_seconds=case.timeout_seconds,
            max_output_bytes=case.max_output_bytes,
            cancel_event=cancel_event,
        )
        duration_ms = int((time.monotonic() - started_at) * 1000)

        return {
            # ok 表示“执行器成功拿到了测试结果”；测试本身失败由 exit_code 表示。
            "ok": True,
            "case_id": case.case_id,
            "exit_code": process.returncode,
            "stdout": stdout_bytes.decode("utf-8", errors="replace"),
            "stderr": stderr_bytes.decode("utf-8", errors="replace"),
            "timed_out": timed_out,
            "cancelled": cancelled,
            "truncated": truncated,
            "duration_ms": duration_ms,
        }


def _build_safe_environment() -> Dict[str, str]:
    """只复制运行测试所需的少量环境变量，不继承模型凭据。"""
    allowed_names = (
        "PATH",
        "LANG",
        "LC_ALL",
        "PYTHONIOENCODING",
        "PYTHONUNBUFFERED",
    )
    environment = {
        name: os.environ[name]
        for name in allowed_names
        if name in os.environ
    }
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def _append_with_limit(
    buffer: bytearray,
    chunk: bytes,
    max_output_bytes: int,
) -> bool:
    """只保存上限内的字节，但返回值会标记是否丢弃了多余输出。"""
    remaining = max_output_bytes - len(buffer)
    if remaining <= 0:
        return True
    if len(chunk) > remaining:
        buffer.extend(chunk[:remaining])
        return True
    buffer.extend(chunk)
    return False


def _read_ready_pipes(
    selector: selectors.BaseSelector,
    stdout_buffer: bytearray,
    stderr_buffer: bytearray,
    max_output_bytes: int,
) -> bool:
    """读取当前可用数据；即使达到保存上限也继续排空管道。"""
    truncated = False
    for key, _ in selector.select(timeout=0):
        try:
            chunk = os.read(key.fd, _READ_SIZE)
        except OSError:
            chunk = b""

        if not chunk:
            selector.unregister(key.fileobj)
            key.fileobj.close()
            continue

        target = stdout_buffer if key.data == "stdout" else stderr_buffer
        if _append_with_limit(target, chunk, max_output_bytes):
            truncated = True
    return truncated


def _terminate_process(process: subprocess.Popen) -> None:
    """先结束进程组，必要时再强制结束，避免留下测试子进程。"""
    if process.poll() is not None:
        return

    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=0.2)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass


def _close_selector_pipes(selector: selectors.BaseSelector) -> None:
    """关闭仍登记在 selector 中的管道。"""
    for key in list(selector.get_map().values()):
        selector.unregister(key.fileobj)
        key.fileobj.close()


def _collect_output(
    process: subprocess.Popen,
    timeout_seconds: float,
    max_output_bytes: int,
    cancel_event: Optional[Event] = None,
    on_terminate: Optional[Callable[[], None]] = None,
) -> Tuple[bytes, bytes, bool, bool, bool]:
    """同时收集 stdout/stderr，并在超时前后都排空管道。

    ``on_terminate`` 是容器执行器提供的额外清理钩子。普通本地测试
    不需要它；Docker 执行器会先通过 Docker API 停止容器，再终止
    本地的 docker 客户端进程。
    """
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("测试进程没有正确连接输出管道")

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    truncated = False
    timed_out = False
    cancelled = False
    deadline = time.monotonic() + timeout_seconds

    def terminate() -> None:
        if on_terminate is not None:
            try:
                on_terminate()
            except Exception:
                # 清理钩子失败时仍然必须终止外层进程，不能让测试卡住。
                pass
        _terminate_process(process)

    try:
        while selector.get_map():
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                terminate()
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                terminate()
                break

            for key, _ in selector.select(timeout=min(remaining, 0.05)):
                try:
                    chunk = os.read(key.fd, _READ_SIZE)
                except OSError:
                    chunk = b""

                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue

                target = stdout_buffer if key.data == "stdout" else stderr_buffer
                if _append_with_limit(target, chunk, max_output_bytes):
                    truncated = True

        # 超时结束进程后，给已经产生的少量输出一个短暂的排空机会。
        if (timed_out or cancelled) and selector.get_map():
            drain_deadline = time.monotonic() + 0.2
            while selector.get_map() and time.monotonic() < drain_deadline:
                if not selector.select(timeout=0.02):
                    continue
                if _read_ready_pipes(
                    selector,
                    stdout_buffer,
                    stderr_buffer,
                    max_output_bytes,
                ):
                    truncated = True
    finally:
        _close_selector_pipes(selector)

    if process.poll() is None:
        process.wait()

    return (
        bytes(stdout_buffer),
        bytes(stderr_buffer),
        truncated,
        timed_out,
        cancelled,
    )


# 简单调用方可以使用默认注册表；具体案例必须由可信的 Python 配置代码登记。
_DEFAULT_REGISTRY = TestCaseRegistry()
_DEFAULT_EXECUTOR = TestExecutor(_DEFAULT_REGISTRY)


def register_test_case(case: TestCase) -> None:
    """向默认注册表登记固定测试案例。"""
    _DEFAULT_REGISTRY.register(case)


def run_tests(
    case_id: str,
    *,
    cancel_event: Optional[Event] = None,
) -> dict:
    """运行默认注册表中的测试案例。"""
    return _DEFAULT_EXECUTOR.run_tests(case_id, cancel_event=cancel_event)
