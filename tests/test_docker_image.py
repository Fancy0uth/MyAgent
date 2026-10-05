"""L15 Docker 镜像的最小验收测试。

这些测试只验证镜像本身，不会把 Docker 命令接入 Agent，也不会传入真实密钥。
"""

import os
import shutil
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGE_TAG = "myagent-sandbox:py313"


def _docker_executable() -> str | None:
    """优先使用 PATH 中的 docker，也兼容 Docker Desktop 的用户级安装。"""

    return shutil.which("docker") or str(Path.home() / ".docker" / "bin" / "docker")


def _docker_environment() -> dict[str, str]:
    """让 Docker CLI 能找到同目录下的 credential helper。"""

    environment = os.environ.copy()
    user_bin = str(Path.home() / ".docker" / "bin")
    environment["PATH"] = user_bin + os.pathsep + environment.get("PATH", "")
    return environment


class DockerContextTests(unittest.TestCase):
    """不需要启动 Docker 的构建上下文检查。"""

    def test_dockerignore_excludes_secrets_and_runtime_data(self) -> None:
        dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")

        self.assertIn(".env", dockerignore)
        self.assertIn(".git", dockerignore)
        self.assertIn("runs", dockerignore)
        self.assertIn("workspaces", dockerignore)


class DockerImageTests(unittest.TestCase):
    """构建并运行一次项目专属的最小隔离镜像。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.docker = _docker_executable()
        if cls.docker is None:
            raise unittest.SkipTest("找不到 Docker CLI")

        version = subprocess.run(
            [cls.docker, "version"],
            env=_docker_environment(),
            capture_output=True,
            text=True,
            check=False,
        )
        if version.returncode != 0:
            raise unittest.SkipTest("Docker CLI 存在，但 Docker Engine 尚未运行")

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
                "Docker 镜像构建失败\n"
                f"stdout:\n{build.stdout}\n"
                f"stderr:\n{build.stderr}"
            )

    def test_image_runs_python_and_bash_as_non_root(self) -> None:
        result = subprocess.run(
            [
                self.docker,
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=16m",
                "--workdir",
                "/workspace",
                IMAGE_TAG,
                "bash",
                "-lc",
                "printf 'uid=%s\\n' \"$(id -u)\"; "
                "python -c 'print(\"python-ok\")'; pwd",
            ],
            env=_docker_environment(),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("python-ok", result.stdout)
        self.assertIn("/workspace", result.stdout)
        self.assertNotIn("uid=0", result.stdout)


if __name__ == "__main__":
    unittest.main()
