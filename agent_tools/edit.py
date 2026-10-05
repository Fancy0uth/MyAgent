"""可解释的精确文本编辑工具。

编辑工具只负责找到唯一的旧文本并生成提议；真正写文件时复用
L11 的 WriteProposalStore，因此仍然需要明确确认，并会检查文件冲突。
"""

from pathlib import Path
from typing import Any, Optional

from agent_tools.write import WriteProposalStore
from policy import resolve_editable_path


def _failure(error: str, message: str) -> dict:
    """统一生成失败结果，方便调用方用 ok 和 error 判断结果。"""
    return {"ok": False, "error": error, "message": message}


class EditProposalStore:
    """在内存中保存编辑提议，并把写入交给 WriteProposalStore。"""

    def __init__(self, write_store: Optional[WriteProposalStore] = None) -> None:
        # 复用 L11 的写入流程，避免编辑工具重新实现安全写入逻辑。
        self._write_store = write_store or WriteProposalStore()

    def propose_edit(
        self,
        workspace: Path,
        relative_path: str,
        old_text: str,
        new_text: str,
    ) -> dict:
        """查找唯一 old_text，生成替换后的待确认提议，但不修改文件。"""
        if not isinstance(old_text, str) or not old_text:
            return _failure("INVALID_ARGUMENT", "old_text 必须是非空字符串")

        if not isinstance(new_text, str):
            return _failure("INVALID_ARGUMENT", "new_text 必须是字符串")

        try:
            target = resolve_editable_path(workspace, relative_path)
        except ValueError as exc:
            return _failure("INVALID_ARGUMENT", str(exc))
        except PermissionError as exc:
            return _failure("ACCESS_DENIED", str(exc))
        except FileNotFoundError as exc:
            return _failure("FILE_NOT_FOUND", str(exc))

        if not target.exists():
            return _failure("FILE_NOT_FOUND", "目标文件不存在")
        if not target.is_file():
            return _failure("INVALID_ARGUMENT", "目标必须是普通文件")

        try:
            current_content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return _failure("READ_ERROR", "目标文件不是 UTF-8 文本")
        except OSError:
            return _failure("READ_ERROR", "读取目标文件失败")

        # count 是安全编辑的关键：0 次不能改，超过 1 次无法确定改哪一处。
        match_count = current_content.count(old_text)
        if match_count == 0:
            return _failure("NO_MATCH", "old_text 在目标文件中没有找到")
        if match_count > 1:
            return _failure(
                "AMBIGUOUS_MATCH",
                "old_text 在目标文件中出现多次，无法安全确定编辑位置",
            )

        # 已确认只有一个匹配，所以这里最多只替换一次。
        new_content = current_content.replace(old_text, new_text, 1)

        # L11 负责哈希、差异、提议 ID、确认、冲突检测和原子写入。
        proposal = self._write_store.propose_write(
            workspace,
            relative_path,
            new_content,
        )
        if not proposal.get("ok"):
            return proposal

        # 在通用写入提议上补充编辑语义，便于用户理解这次操作。
        proposal.update(
            {
                "operation": "edit",
                "match_count": match_count,
                "old_text": old_text,
                "new_text": new_text,
            }
        )
        return proposal

    def apply_proposal(self, proposal_id: str, confirmation: Any) -> dict:
        """复用 L11 的确认、冲突检查和写后验证流程。"""
        return self._write_store.apply_proposal(proposal_id, confirmation)


# 简单调用方可以使用模块级函数；测试和复杂流程可以创建独立 Store。
_DEFAULT_STORE = EditProposalStore()


def propose_edit(
    workspace: Path,
    relative_path: str,
    old_text: str,
    new_text: str,
) -> dict:
    """使用默认 Store 生成编辑提议。"""
    return _DEFAULT_STORE.propose_edit(
        workspace,
        relative_path,
        old_text,
        new_text,
    )


def apply_edit_proposal(proposal_id: str, confirmation: Any) -> dict:
    """使用默认 Store 执行已经确认的编辑提议。"""
    return _DEFAULT_STORE.apply_proposal(proposal_id, confirmation)
