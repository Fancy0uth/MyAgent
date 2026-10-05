"""L10 的文件策略：保留原始样例，只在实验副本中工作。"""

import hashlib
import re
import shutil
from pathlib import Path, PureWindowsPath


PROJECT_ROOT = Path(__file__).resolve().parent
ORIGINAL_FIXTURE = PROJECT_ROOT / "fixtures" / "mean_case" / "buggy_original.py"
WORKSPACES_ROOT = PROJECT_ROOT / "workspaces"

# run_id 只允许普通字母、数字、下划线和短横线，避免 ../ 这类路径穿越。
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def is_protected_test_path(relative_path: str) -> bool:
    """判断一个工作区相对路径是否属于验收测试文件。"""
    if not isinstance(relative_path, str):
        return False
    normalized = relative_path.replace("\\", "/")
    parts = tuple(part for part in normalized.split("/") if part not in {"", "."})
    return "tests" in parts or any(
        part.startswith("test_") or part.endswith("_test.py")
        for part in parts
    )


def file_sha256(path: Path) -> str:
    """计算文件的 SHA-256 哈希，用来确认文件内容是否发生变化。"""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_workspace(
    run_id: str,
    source_path: Path = ORIGINAL_FIXTURE,
    workspaces_root: Path = WORKSPACES_ROOT,
) -> Path:
    """为一次实验创建副本，并返回这个实验目录。"""
    if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ValueError("run_id 只能包含字母、数字、下划线和短横线")

    source = Path(source_path)
    workspace_root = Path(workspaces_root)
    if not source.is_file() or source.is_symlink():
        raise FileNotFoundError("原始夹具必须是存在的普通文件")

    # 先创建 workspaces，再在里面创建本次独立运行目录。
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace = workspace_root / run_id
    if workspace.exists():
        raise FileExistsError(f"实验目录已经存在：{run_id}")

    workspace.mkdir()
    shutil.copy2(source, workspace / source.name)
    return workspace


def resolve_editable_path(workspace: Path, relative_path: str) -> Path:
    """把工作区内的相对路径解析成允许修改的绝对路径。"""
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("relative_path 必须是非空字符串")

    raw_path = relative_path.strip()
    posix_path = Path(raw_path)
    windows_path = PureWindowsPath(raw_path)

    # 同时检查 macOS/Linux 和 Windows 风格路径，避免换一种写法绕过检查。
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
    ):
        raise PermissionError("只允许使用工作区内的相对路径")

    if ".." in posix_path.parts or ".." in windows_path.parts:
        raise PermissionError("不允许访问工作区的上级目录")

    # 测试文件是验收依据，底层策略直接保护它们；扩展只是额外提醒，
    # 即使扩展没有加载，写入工具也不能绕过这里。
    if is_protected_test_path(raw_path):
        raise PermissionError("验收测试文件受保护，不能修改")

    workspace_path = Path(workspace)
    if not workspace_path.is_dir() or workspace_path.is_symlink():
        raise FileNotFoundError("实验工作区不存在或不是普通目录")

    resolved_workspace = workspace_path.resolve()
    candidate = workspace_path / posix_path
    resolved_candidate = candidate.resolve()

    # resolve() 会跟随已有符号链接；如果结果跑出工作区，就拒绝它。
    try:
        resolved_candidate.relative_to(resolved_workspace)
    except ValueError as exc:
        raise PermissionError("目标路径超出当前实验工作区") from exc

    if resolved_candidate == resolved_workspace:
        raise PermissionError("目标必须是工作区内的文件路径")

    # 即使符号链接最终仍在工作区内，也拒绝它，保持策略简单可解释。
    current = workspace_path
    for part in posix_path.parts:
        if part in ("", "."):
            continue
        current = current / part
        if current.is_symlink():
            raise PermissionError("不允许通过符号链接修改文件")

    return resolved_candidate
