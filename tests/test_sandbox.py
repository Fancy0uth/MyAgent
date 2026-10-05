"""L15 SandboxExecutor 的容器集成测试。

这些测试使用已经构建好的项目镜像，不调用真实模型，也不把主项目目录挂载进容器。
"""

import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_tools.sandbox import SandboxExecutor


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGE_TAG = "myagent-sandbox:py313"


def _docker_executable() -> str:
    return shutil.which("docker") or str(Path.home() / ".docker" / "bin" / "docker")


def _docker_environment() -> dict[str, str]:
    environment = os.environ.copy()
    user_bin = str(Path.home() / ".docker" / "bin")
    environment["PATH"] = user_bin + os.pathsep + environment.get("PATH", "")
    return environment


class SandboxExecutorTests(unittest.TestCase):
    """验证命令只在受限容器中执行。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.docker = _docker_executable()
        version = subprocess.run(
            [cls.docker, "version"],
            env=_docker_environment(),
            capture_output=True,
            text=True,
            check=False,
        )
        if version.returncode != 0:
            raise unittest.SkipTest("Docker Engine 尚未运行")

        image = subprocess.run(
            [cls.docker, "image", "inspect", IMAGE_TAG],
            env=_docker_environment(),
            capture_output=True,
            text=True,
            check=False,
        )
        if image.returncode != 0:
            build = subprocess.run(
                [cls.docker, "build", "--tag", IMAGE_TAG, "."],
                cwd=PROJECT_ROOT,
                env=_docker_environment(),
                capture_output=True,
                text=True,
                check=False,
                timeout=180,
            )
            if build.returncode != 0:
                raise AssertionError(
                    "测试所需的 Docker 镜像构建失败\n"
                    f"stdout:\n{build.stdout}\n"
                    f"stderr:\n{build.stderr}"
                )

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name) / "workspaces"
        self.workspace = self.workspace_root / "run-001"
        self.workspace.mkdir(parents=True)
        self.executor = SandboxExecutor(
            image=IMAGE_TAG,
            workspace_root=self.workspace_root,
            docker_executable=self.docker,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_success_runs_bash_and_python_inside_workspace(self) -> None:
        result = self.executor.run_bash(
            "printf 'hello\\n'; python -c 'print(\"python-ok\")'; pwd",
            self.workspace,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("hello", result["stdout"])
        self.assertIn("python-ok", result["stdout"])
        self.assertIn("/workspace", result["stdout"])
        self.assertFalse(result["timed_out"])
        self.assertFalse(result["cancelled"])

    def test_nonzero_exit_and_stderr_are_returned(self) -> None:
        result = self.executor.run_bash("printf 'failed\\n' >&2; exit 7", self.workspace)

        self.assertTrue(result["ok"])
        self.assertEqual(result["exit_code"], 7)
        self.assertIn("failed", result["stderr"])

    def test_workspace_file_persists_but_host_project_is_not_mounted(self) -> None:
        result = self.executor.run_bash(
            "printf 'saved' > marker.txt; test ! -e /Users",
            self.workspace,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual((self.workspace / "marker.txt").read_text(), "saved")

    def test_workspace_outside_allowed_root_is_rejected(self) -> None:
        outside = Path(self.temp_dir.name) / "outside"
        outside.mkdir()

        result = self.executor.run_bash("printf bad", outside)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "WORKSPACE_NOT_ALLOWED")

    def test_empty_command_is_rejected_before_starting_container(self) -> None:
        result = self.executor.run_bash("   ", self.workspace)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "INVALID_COMMAND")

    def test_api_key_is_not_passed_to_container(self) -> None:
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "not-for-container"}):
            result = self.executor.run_bash(
                "python -c 'import os; print(os.getenv(\"DEEPSEEK_API_KEY\", \"missing\"))'",
                self.workspace,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["stdout"].strip(), "missing")

    def test_container_network_is_disabled(self) -> None:
        result = self.executor.run_bash("test ! -s /proc/net/route", self.workspace)

        self.assertTrue(result["ok"])
        self.assertEqual(result["exit_code"], 0)

    def test_read_only_root_is_enforced(self) -> None:
        result = self.executor.run_bash(
            "touch /etc/not-writable",
            self.workspace,
        )

        self.assertTrue(result["ok"])
        self.assertNotEqual(result["exit_code"], 0)

    def test_output_limit_is_enforced(self) -> None:
        result = self.executor.run_bash(
            "python -c 'print(\"x\" * 1000)'",
            self.workspace,
            max_output_bytes=64,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["stdout"].encode("utf-8")), 64)

    def test_timeout_stops_container_before_delayed_file_write(self) -> None:
        delayed = self.workspace / "delayed.txt"
        result = self.executor.run_bash(
            "python -c 'import time; time.sleep(2)'; printf done > delayed.txt",
            self.workspace,
            timeout_seconds=0.2,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["timed_out"])
        self.assertFalse(delayed.exists())

    def test_cancel_stops_running_container(self) -> None:
        started = self.workspace / "started.txt"
        finished = self.workspace / "finished.txt"
        cancel_event = threading.Event()
        result_holder = {}

        def run_command() -> None:
            result_holder["result"] = self.executor.run_bash(
                "touch started.txt; python -c 'import time; time.sleep(10)'; "
                "printf done > finished.txt",
                self.workspace,
                cancel_event=cancel_event,
            )

        worker = threading.Thread(target=run_command)
        worker.start()
        for _ in range(100):
            if started.exists():
                break
            time.sleep(0.02)
        cancel_event.set()
        worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        result = result_holder["result"]
        self.assertTrue(result["ok"])
        self.assertTrue(result["cancelled"])
        time.sleep(0.3)
        self.assertFalse(finished.exists())


if __name__ == "__main__":
    unittest.main()
