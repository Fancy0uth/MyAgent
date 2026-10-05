"""可预览、需确认、会检查冲突的受控写入工具。"""

import difflib
import hashlib
import os
import uuid
from pathlib import Path
from typing import Any, Dict

from policy import file_sha256, resolve_editable_path


MAX_WRITE_BYTES = 256 * 1024


def _failure(error: str, message: str) -> dict:
    """统一生成失败结果，避免把异常直接暴露给调用方。"""
    return {"ok": False, "error": error, "message": message}


def _text_sha256(content: str) -> str:
    """计算待写入文本按 UTF-8 编码后的哈希。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _make_diff(path: str, old_content: str, new_content: str) -> str:
    """生成供用户审核的统一格式差异。"""
    return "".join(
        difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


class WriteProposalStore:
    """在内存中保存待确认的写入提议。"""

    def __init__(self) -> None:
        self._proposals: Dict[str, dict] = {}

    def propose_write(
        self,
        workspace: Path,
        relative_path: str,
        content: str,
    ) -> dict:
        """生成提议，不修改任何文件。"""
        if not isinstance(content, str):
            return _failure("INVALID_ARGUMENT", "content 必须是字符串")

        if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
            return _failure("CONTENT_TOO_LARGE", "写入内容超过 256 KiB")

        try:
            target = resolve_editable_path(workspace, relative_path)
        except ValueError as exc:
            return _failure("INVALID_ARGUMENT", str(exc))
        except PermissionError as exc:
            return _failure("ACCESS_DENIED", str(exc))
        except FileNotFoundError as exc:
            return _failure("FILE_NOT_FOUND", str(exc))

        if not target.parent.is_dir():
            return _failure("FILE_NOT_FOUND", "目标文件的父目录不存在")

        workspace_path = Path(workspace).resolve()
        display_path = target.relative_to(workspace_path).as_posix()
        old_content = ""
        old_sha256 = None
        old_exists = target.exists()

        if old_exists:
            if not target.is_file():
                return _failure("INVALID_ARGUMENT", "目标必须是普通文件")
            try:
                old_content = target.read_text(encoding="utf-8")
                old_sha256 = file_sha256(target)
            except UnicodeDecodeError:
                return _failure("READ_ERROR", "旧文件不是 UTF-8 文本")
            except OSError:
                return _failure("READ_ERROR", "读取旧文件失败")

        if old_exists and old_content == content:
            return _failure("NO_CHANGE", "新内容与旧内容相同")

        proposal_id = f"proposal-{uuid.uuid4().hex[:8]}"
        new_sha256 = _text_sha256(content)
        diff = _make_diff(display_path, old_content, content)

        # 内部记录绝对路径用于真正执行；返回结果只展示工作区内的相对路径。
        self._proposals[proposal_id] = {
            "workspace": workspace_path,
            "target": target,
            "path": display_path,
            "content": content,
            "old_sha256": old_sha256,
            "new_sha256": new_sha256,
            "status": "pending",
        }

        return {
            "ok": True,
            "status": "pending",
            "proposal_id": proposal_id,
            "path": display_path,
            "old_exists": old_exists,
            "old_sha256": old_sha256,
            "new_sha256": new_sha256,
            "old_content": old_content,
            "new_content": content,
            "diff": diff,
        }

    def apply_proposal(self, proposal_id: str, confirmation: Any) -> dict:
        """确认后执行提议，并在写入后重新读取验证。"""
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return _failure("PROPOSAL_NOT_FOUND", "写入提议不存在")

        if proposal["status"] == "applied":
            return _failure("ALREADY_APPLIED", "写入提议已经执行过")

        if not isinstance(confirmation, dict):
            return _failure("INVALID_CONFIRMATION", "确认信息必须是对象")

        if confirmation.get("approved") is not True:
            return _failure("NOT_APPROVED", "用户没有确认这次写入")

        expected_confirmation = {
            "proposal_id": proposal_id,
            "path": proposal["path"],
            "old_sha256": proposal["old_sha256"],
            "new_sha256": proposal["new_sha256"],
        }
        actual_confirmation = {
            key: confirmation.get(key) for key in expected_confirmation
        }
        if actual_confirmation != expected_confirmation:
            return _failure(
                "CONFIRMATION_MISMATCH",
                "确认信息与当前提议的路径或内容不一致",
            )

        try:
            target = resolve_editable_path(
                proposal["workspace"],
                proposal["path"],
            )
        except (ValueError, PermissionError, FileNotFoundError) as exc:
            return _failure("ACCESS_DENIED", str(exc))

        if target != proposal["target"] or not target.parent.is_dir():
            return _failure("WRITE_CONFLICT", "目标路径已经发生变化")

        if target.exists() and not target.is_file():
            return _failure("WRITE_CONFLICT", "目标不再是普通文件")

        current_sha256 = file_sha256(target) if target.exists() else None
        if current_sha256 != proposal["old_sha256"]:
            return _failure(
                "WRITE_CONFLICT",
                "文件在确认后发生变化，未覆盖外部修改",
            )

        temporary_path = target.with_name(f".{target.name}.{proposal_id}.tmp")
        try:
            # 先写同目录临时文件，再用 os.replace 原子替换目标文件。
            with temporary_path.open("x", encoding="utf-8", newline="") as file:
                file.write(proposal["content"])
            os.replace(temporary_path, target)

            actual_content = target.read_text(encoding="utf-8")
            actual_sha256 = file_sha256(target)
            if (
                actual_content != proposal["content"]
                or actual_sha256 != proposal["new_sha256"]
            ):
                return _failure("WRITE_VERIFY_FAILED", "写入后的内容校验失败")
        except (OSError, UnicodeError):
            return _failure("WRITE_ERROR", "写入文件失败")
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

        proposal["status"] = "applied"
        return {
            "ok": True,
            "status": "applied",
            "proposal_id": proposal_id,
            "path": proposal["path"],
            "new_sha256": proposal["new_sha256"],
        }


# 这两个函数给简单调用方使用；测试则创建独立 Store，避免提议相互污染。
_DEFAULT_STORE = WriteProposalStore()


def propose_write(workspace: Path, relative_path: str, content: str) -> dict:
    """使用默认提议存储生成写入提议。"""
    return _DEFAULT_STORE.propose_write(workspace, relative_path, content)


def apply_proposal(proposal_id: str, confirmation: Any) -> dict:
    """使用默认提议存储执行已确认的写入提议。"""
    return _DEFAULT_STORE.apply_proposal(proposal_id, confirmation)
