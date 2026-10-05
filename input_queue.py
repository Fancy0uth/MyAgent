"""L22：运行期间接收 Steering 和 Follow-up 指令的小型线程安全队列。"""

from copy import deepcopy
from threading import Lock
from typing import Dict, List, Optional
import uuid


ALLOWED_INSTRUCTION_KINDS = frozenset({"steering", "follow_up"})


class InstructionQueueError(ValueError):
    """表示队列收到不合法的指令。"""


class InstructionQueue:
    """按提交顺序保存运行中的新指令。

    队列本身不理解指令内容，也不会自动调用模型；它只提供三个边界：
    提交、按类型取出一条、查看仍未消费的消息。
    """

    def __init__(self) -> None:
        self._entries: List[Dict[str, str]] = []
        self._lock = Lock()

    def submit(self, kind: str, content: str) -> dict:
        """提交一条待处理指令，并返回它的可追踪记录。"""
        if kind not in ALLOWED_INSTRUCTION_KINDS:
            raise InstructionQueueError(
                "指令类型必须是 steering 或 follow_up"
            )
        if not isinstance(content, str) or not content.strip():
            raise InstructionQueueError("指令内容必须是非空字符串")

        entry = {
            "id": f"instruction-{uuid.uuid4().hex[:8]}",
            "kind": kind,
            "content": content.strip(),
            "status": "pending",
        }
        with self._lock:
            self._entries.append(entry)
        return deepcopy(entry)

    def pop_next(self, kind: Optional[str] = None) -> Optional[dict]:
        """按 FIFO 取出一条消息，并把它标记为 consumed。"""
        if kind is not None and kind not in ALLOWED_INSTRUCTION_KINDS:
            raise InstructionQueueError(
                "指令类型必须是 steering 或 follow_up"
            )

        with self._lock:
            for entry in self._entries:
                if entry["status"] != "pending":
                    continue
                if kind is not None and entry["kind"] != kind:
                    continue
                entry["status"] = "consumed"
                return deepcopy(entry)
        return None

    def pending(self, kind: Optional[str] = None) -> List[dict]:
        """返回仍未消费的消息，不改变队列状态。"""
        if kind is not None and kind not in ALLOWED_INSTRUCTION_KINDS:
            raise InstructionQueueError(
                "指令类型必须是 steering 或 follow_up"
            )

        with self._lock:
            return [
                deepcopy(entry)
                for entry in self._entries
                if entry["status"] == "pending"
                and (kind is None or entry["kind"] == kind)
            ]
