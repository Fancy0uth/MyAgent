"""L16：把一次 Agent 会话保存为可校验的追加式 JSONL。"""

from __future__ import annotations

import copy
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Optional


DEFAULT_ENTRY_TYPES = frozenset(
    {
        "user_message",
        "assistant_message",
        "tool_call",
        "tool_result",
        "run_status",
        "run_context",
        "operation_intent",
        "operation_approval",
        "operation_result",
        "branch_selection",
    }
)


class SessionLoadError(ValueError):
    """会话文件不能安全加载时抛出的错误。"""

    def __init__(self, code: str, message: str, line_number: Optional[int] = None):
        self.code = code
        self.line_number = line_number
        detail = f"{code}: {message}"
        if line_number is not None:
            detail = f"{detail}（第 {line_number} 行）"
        super().__init__(detail)


class SessionStore:
    """单写入者使用的追加式会话树。

    每次 append 都写入一整行 JSON。只有文件成功 flush 并同步到磁盘后，
    才会更新内存中的索引和活动叶子，这样写入失败不会伪造一条历史记录。
    """

    def __init__(
        self,
        path: os.PathLike[str] | str,
        schema_version: int = 1,
        allowed_types: Optional[Iterable[str]] = None,
    ):
        if isinstance(schema_version, bool) or not isinstance(schema_version, int):
            raise ValueError("schema_version 必须是整数")
        if schema_version < 1:
            raise ValueError("schema_version 必须大于 0")

        self.path = Path(path)
        self.schema_version = schema_version
        self.allowed_types = frozenset(
            allowed_types if allowed_types is not None else DEFAULT_ENTRY_TYPES
        )
        self._last_id: Optional[str] = None
        self._known_ids: set[str] = set()
        self._entries_by_id: dict[str, dict] = {}
        self._entry_order: dict[str, int] = {}

    def _read_and_validate(self) -> list[dict]:
        """读取整个文件并验证版本、字段、ID 和父图。"""
        if not self.path.exists():
            return []

        try:
            text = self.path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise SessionLoadError("INVALID_ENCODING", "会话文件不是有效的 UTF-8") from exc
        except OSError as exc:
            raise SessionLoadError("READ_ERROR", f"无法读取会话文件：{exc}") from exc

        if text and not text.endswith("\n"):
            # 最后一行没有换行，说明可能是进程在写完前退出；不能静默丢掉它。
            raise SessionLoadError(
                "PARTIAL_TAIL",
                "最后一行没有完整写入（文件末尾缺少换行符）",
            )

        entries: list[dict] = []
        known_ids: set[str] = set()

        for line_number, line in enumerate(text.splitlines(), start=1):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SessionLoadError(
                    "INVALID_JSON",
                    f"这一行不是合法 JSON：{exc.msg}",
                    line_number,
                ) from exc

            if not isinstance(entry, dict):
                raise SessionLoadError(
                    "INVALID_ENTRY",
                    "每一行必须是 JSON 对象",
                    line_number,
                )

            required_fields = {
                "schema_version",
                "id",
                "parent_id",
                "timestamp",
                "type",
                "payload",
            }
            if not required_fields.issubset(entry):
                raise SessionLoadError(
                    "INVALID_ENTRY",
                    "记录缺少必需字段",
                    line_number,
                )

            version = entry["schema_version"]
            if (
                isinstance(version, bool)
                or not isinstance(version, int)
                or version != self.schema_version
            ):
                raise SessionLoadError(
                    "UNSUPPORTED_SCHEMA",
                    f"只支持 schema_version={self.schema_version}",
                    line_number,
                )

            entry_id = entry["id"]
            if not isinstance(entry_id, str) or not entry_id.strip():
                raise SessionLoadError(
                    "INVALID_ID",
                    "id 必须是非空字符串",
                    line_number,
                )
            if entry_id in known_ids:
                raise SessionLoadError(
                    "DUPLICATE_ID",
                    f"发现重复的 id：{entry_id}",
                    line_number,
                )

            parent_id = entry["parent_id"]
            if parent_id is not None and (
                not isinstance(parent_id, str) or not parent_id.strip()
            ):
                raise SessionLoadError(
                    "INVALID_PARENT",
                    "parent_id 必须是 null 或非空字符串",
                    line_number,
                )

            timestamp = entry["timestamp"]
            entry_type = entry["type"]
            payload = entry["payload"]
            if not isinstance(timestamp, str) or not timestamp.strip():
                raise SessionLoadError(
                    "INVALID_ENTRY",
                    "timestamp 必须是非空字符串",
                    line_number,
                )
            if not isinstance(entry_type, str) or entry_type not in self.allowed_types:
                raise SessionLoadError(
                    "INVALID_TYPE",
                    f"不支持的记录类型：{entry_type!r}",
                    line_number,
                )
            if not isinstance(payload, dict):
                raise SessionLoadError(
                    "INVALID_PAYLOAD",
                    "payload 必须是 JSON 对象",
                    line_number,
                )

            entries.append(entry)
            known_ids.add(entry_id)

        self._validate_parent_graph(entries)

        return entries

    @staticmethod
    def _validate_parent_graph(entries: list[dict]) -> None:
        """验证父节点存在、图无环，且父节点在追加顺序上早于子节点。"""
        entries_by_id = {entry["id"]: entry for entry in entries}
        order = {entry["id"]: index for index, entry in enumerate(entries)}

        for index, entry in enumerate(entries):
            parent_id = entry["parent_id"]
            if parent_id is None:
                if index != 0:
                    raise SessionLoadError(
                        "BROKEN_PARENT",
                        "只有会话首条记录可以没有父节点",
                        index + 1,
                    )
                continue
            if parent_id not in entries_by_id:
                raise SessionLoadError(
                    "ORPHAN_PARENT",
                    f"找不到父记录：{parent_id}",
                    index + 1,
                )

        # 每个节点只有一个父节点，沿父指针迭代即可检查环，避免长会话
        # 触发 Python 递归深度限制。
        state: dict[str, int] = {}
        for entry in entries:
            start_id = entry["id"]
            if state.get(start_id) == 2:
                continue

            path: list[str] = []
            current_id: Optional[str] = start_id
            while current_id is not None:
                current_state = state.get(current_id, 0)
                if current_state == 1:
                    raise SessionLoadError(
                        "CYCLE_DETECTED",
                        f"父记录关系包含循环：{current_id}",
                        order[current_id] + 1,
                    )
                if current_state == 2:
                    break
                state[current_id] = 1
                path.append(current_id)
                current_id = entries_by_id[current_id]["parent_id"]

            for entry_id in path:
                state[entry_id] = 2

        for index, entry in enumerate(entries):
            parent_id = entry["parent_id"]
            if parent_id is not None and order[parent_id] >= index:
                raise SessionLoadError(
                    "PARENT_ORDER",
                    "父记录必须先于子记录写入",
                    index + 1,
                )

    def _refresh_state(self) -> list[dict]:
        """写入前重新读取文件，避免使用过期的父 ID 或重复 ID。"""
        entries = self._read_and_validate()
        self._known_ids = {entry["id"] for entry in entries}
        self._entries_by_id = {entry["id"]: entry for entry in entries}
        self._entry_order = {
            entry["id"]: index for index, entry in enumerate(entries)
        }
        self._last_id = entries[-1]["id"] if entries else None
        return entries

    def load(self) -> list[dict]:
        """加载并验证全部记录；文件不存在时返回空列表。"""
        return self._refresh_state()

    def get_active_path(self) -> list[dict]:
        """返回从根到当前活动叶子的记录副本。"""
        self._refresh_state()
        path: list[dict] = []
        current_id = self._last_id
        while current_id is not None:
            entry = self._entries_by_id[current_id]
            path.append(entry)
            current_id = entry["parent_id"]
        path.reverse()
        return copy.deepcopy(path)

    def get_model_history(self) -> list[dict[str, Any]]:
        """返回活动路径上的聊天消息，不包含运行元数据或兄弟分支。"""
        return [
            copy.deepcopy(entry["payload"])
            for entry in self.get_active_path()
            if entry["type"] in {"user_message", "assistant_message", "tool_result"}
        ]

    def get_unresolved_tool_calls(self) -> list[dict[str, Any]]:
        """返回活动聊天历史中尚无对应工具结果的调用。"""
        pending: dict[str, dict[str, Any]] = {}
        for message in self.get_model_history():
            if message.get("role") == "assistant":
                calls = message.get("tool_calls")
                if not isinstance(calls, list):
                    single_call = message.get("tool_call")
                    calls = [single_call] if isinstance(single_call, dict) else []
                for call in calls:
                    if not isinstance(call, dict):
                        continue
                    call_id = call.get("id")
                    if isinstance(call_id, str) and call_id:
                        pending[call_id] = {
                            "id": call_id,
                            "name": call.get("name"),
                            "arguments": copy.deepcopy(call.get("arguments")),
                        }
            elif message.get("role") == "tool":
                call_id = message.get("tool_call_id")
                if isinstance(call_id, str):
                    pending.pop(call_id, None)
        return list(pending.values())

    def select_active_node(self, node_id: str) -> dict:
        """追加分支导航记录，使指定聊天节点成为当前上下文位置。"""
        self._refresh_state()
        entry = self._entries_by_id.get(node_id) if isinstance(node_id, str) else None
        if entry is None or entry["type"] not in {
            "user_message",
            "assistant_message",
            "tool_result",
        }:
            raise SessionLoadError(
                "INVALID_BRANCH_TARGET",
                "活动节点必须是存在的用户消息、助手消息或工具结果",
            )
        return self.append(
            "branch_selection",
            {"selected_node_id": node_id},
            parent_id=node_id,
        )

    def render_tree(self) -> str:
        """以缩进文本展示聊天分支，隐藏运行和操作元数据。"""
        entries = self._refresh_state()
        by_id = self._entries_by_id
        visible_types = {"user_message", "assistant_message", "tool_result"}
        visible_ids = {
            entry["id"] for entry in entries if entry["type"] in visible_types
        }
        children: dict[Optional[str], list[str]] = {}

        for entry in entries:
            entry_id = entry["id"]
            if entry_id not in visible_ids:
                continue

            parent_id = entry["parent_id"]
            while parent_id is not None and parent_id not in visible_ids:
                parent_id = by_id[parent_id]["parent_id"]
            children.setdefault(parent_id, []).append(entry_id)

        def display(entry: dict) -> str:
            payload = entry["payload"]
            role = payload.get("role")
            if role == "user":
                label = "用户"
            elif role == "assistant":
                label = "助手"
            elif role == "tool":
                call_id = payload.get("tool_call_id")
                label = f"工具结果 {call_id}" if call_id else "工具结果"
            else:
                label = entry["type"]

            content = payload.get("content")
            if content is None and isinstance(payload.get("tool_call"), dict):
                content = f"调用工具 {payload['tool_call'].get('name', '未知工具')}"
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, sort_keys=True)
            content = content.replace("\r", " ").replace("\n", " ↵ ")
            return f"{label}：{content}"

        lines: list[str] = []
        stack = [(entry_id, 0) for entry_id in reversed(children.get(None, []))]
        while stack:
            entry_id, depth = stack.pop()
            lines.append(f"{'  ' * depth}{display(by_id[entry_id])}")
            for child_id in reversed(children.get(entry_id, [])):
                stack.append((child_id, depth + 1))
        return "\n".join(lines) if lines else "（空会话）"

    def append(
        self,
        entry_type: str,
        payload: dict[str, Any],
        *,
        parent_id: Optional[str] = None,
        entry_id: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> dict:
        """追加一条记录，并返回已经写入的记录副本。"""
        if not isinstance(entry_type, str) or entry_type not in self.allowed_types:
            raise SessionLoadError("INVALID_TYPE", f"不支持的记录类型：{entry_type!r}")
        if not isinstance(payload, dict):
            raise SessionLoadError("INVALID_PAYLOAD", "payload 必须是 JSON 对象")

        # 先让标准库检查 payload 能否安全编码为 JSON，避免写出半条记录。
        try:
            json.dumps(payload, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise SessionLoadError("INVALID_PAYLOAD", "payload 不能编码为 JSON") from exc

        existing_entries = self._refresh_state()
        if parent_id is None:
            parent_id = existing_entries[-1]["id"] if existing_entries else None
        elif not isinstance(parent_id, str) or not parent_id.strip():
            raise SessionLoadError(
                "INVALID_PARENT",
                "parent_id 必须是 null 或非空字符串",
            )
        elif parent_id not in self._entries_by_id:
            raise SessionLoadError(
                "ORPHAN_PARENT",
                f"找不到父记录：{parent_id}",
            )

        new_id = entry_id or f"entry-{uuid.uuid4().hex[:12]}"
        if not isinstance(new_id, str) or not new_id.strip():
            raise SessionLoadError("INVALID_ID", "id 必须是非空字符串")
        if new_id in self._known_ids:
            raise SessionLoadError("DUPLICATE_ID", f"发现重复的 id：{new_id}")

        new_timestamp = timestamp or datetime.now(timezone.utc).isoformat()
        if not isinstance(new_timestamp, str) or not new_timestamp.strip():
            raise SessionLoadError("INVALID_ENTRY", "timestamp 必须是非空字符串")

        entry = {
            "schema_version": self.schema_version,
            "id": new_id,
            "parent_id": parent_id,
            "timestamp": new_timestamp,
            "type": entry_type,
            "payload": payload,
        }
        serialized = json.dumps(entry, ensure_ascii=False, allow_nan=False)

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise SessionLoadError("WRITE_ERROR", f"无法写入会话文件：{exc}") from exc

        # 只有写入成功后才推进内存状态。
        self._known_ids.add(new_id)
        self._entries_by_id[new_id] = entry
        self._entry_order[new_id] = len(existing_entries)
        self._last_id = new_id
        return entry

    def record_operation_intent(
        self,
        operation_id: str,
        proposal: dict[str, Any],
        *,
        tool_call_id: str,
        workspace_root: os.PathLike[str] | str,
        permission_profile: str = "workspace_write_confirmed",
    ) -> dict:
        """先把写入提议的身份与哈希记到账本，不保存文件正文。"""
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise SessionLoadError("INVALID_OPERATION", "operation_id 必须是非空字符串")
        if not isinstance(tool_call_id, str) or not tool_call_id.strip():
            raise SessionLoadError("INVALID_OPERATION", "tool_call_id 必须是非空字符串")
        if not isinstance(proposal, dict) or proposal.get("ok") is not True:
            raise SessionLoadError("INVALID_OPERATION", "只能记录有效的写入提议")

        proposal_id = proposal.get("proposal_id")
        path = proposal.get("path")
        old_sha256 = proposal.get("old_sha256")
        new_sha256 = proposal.get("new_sha256")
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            raise SessionLoadError("INVALID_OPERATION", "提议缺少 proposal_id")
        if not isinstance(path, str) or not path.strip():
            raise SessionLoadError("INVALID_OPERATION", "提议缺少相对路径")
        windows_path = PureWindowsPath(path)
        if (
            Path(path).is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or ".." in Path(path).parts
            or ".." in windows_path.parts
        ):
            raise SessionLoadError("INVALID_OPERATION", "提议路径必须位于工作区内")
        if old_sha256 is not None and not self._is_sha256(old_sha256):
            raise SessionLoadError("INVALID_OPERATION", "旧文件哈希格式错误")
        if not self._is_sha256(new_sha256):
            raise SessionLoadError("INVALID_OPERATION", "新内容哈希格式错误")
        if not isinstance(permission_profile, str) or not permission_profile.strip():
            raise SessionLoadError("INVALID_OPERATION", "权限配置名称必须是非空字符串")

        entries = self.load()
        if any(
            entry["type"] == "operation_intent"
            and entry["payload"].get("operation_id") == operation_id
            for entry in entries
        ):
            raise SessionLoadError("DUPLICATE_OPERATION", "operation_id 已经使用")

        payload = {
            "operation_id": operation_id,
            "tool_call_id": tool_call_id,
            "proposal_id": proposal_id,
            "path": Path(path).as_posix(),
            "old_sha256": old_sha256,
            "new_sha256": new_sha256,
            "workspace": str(Path(workspace_root).resolve()),
            "permission_profile": permission_profile,
        }
        return self.append("operation_intent", payload)

    def record_operation_approval(
        self,
        operation_id: str,
        confirmation: Any,
    ) -> dict:
        """持久化与提议绑定的批准或拒绝，再允许调用方考虑执行。"""
        intent = self._find_operation_intent(operation_id)
        expected = {
            "proposal_id": intent["proposal_id"],
            "path": intent["path"],
            "old_sha256": intent["old_sha256"],
            "new_sha256": intent["new_sha256"],
        }
        supplied = {
            key: confirmation.get(key) if isinstance(confirmation, dict) else None
            for key in expected
        }
        binding_matches = supplied == expected
        approved = (
            isinstance(confirmation, dict)
            and confirmation.get("approved") is True
            and binding_matches
        )
        return self.append(
            "operation_approval",
            {
                "operation_id": operation_id,
                "approved": approved,
                "binding_matches": binding_matches,
                **supplied,
            },
        )

    def record_operation_result(
        self,
        operation_id: str,
        result: dict[str, Any],
    ) -> dict:
        """记录写入结果；无法确定是否产生副作用时保留 outcome=unknown。"""
        intent = self._find_operation_intent(operation_id)
        if not isinstance(result, dict):
            raise SessionLoadError("INVALID_OPERATION", "操作结果必须是对象")

        if result.get("ok") is True:
            outcome = "succeeded"
        elif result.get("error") in {
            "ALREADY_APPLIED",
            "ACCESS_DENIED",
            "CONFIRMATION_MISMATCH",
            "INVALID_CONFIRMATION",
            "NOT_APPROVED",
            "PROPOSAL_NOT_FOUND",
            "WRITE_CONFLICT",
        }:
            outcome = "no_effect"
        else:
            # 写入异常可能发生在替换之后；不能把它当作“肯定没写”。
            outcome = "unknown"

        return self.append(
            "operation_result",
            {
                "operation_id": operation_id,
                "tool_call_id": intent["tool_call_id"],
                "proposal_id": intent["proposal_id"],
                "outcome": outcome,
                "error": result.get("error"),
                "path": intent["path"],
                "new_sha256": intent["new_sha256"],
            },
        )

    @staticmethod
    def _is_sha256(value: Any) -> bool:
        if not isinstance(value, str) or len(value) != 64:
            return False
        try:
            int(value, 16)
        except ValueError:
            return False
        return True

    def _find_operation_intent(self, operation_id: str) -> dict:
        for entry in reversed(self.load()):
            if (
                entry["type"] == "operation_intent"
                and entry["payload"].get("operation_id") == operation_id
            ):
                return entry["payload"]
        raise SessionLoadError("OPERATION_NOT_FOUND", "找不到对应的操作提议")
