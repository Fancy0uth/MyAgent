"""第四关使用的离线模拟模型。"""

import json


class MockModelClient:
    """按历史记录返回固定的工具调用和最终回答。"""

    def _assistant_called(self, messages, call_id: str, name: str) -> bool:
        for message in messages:
            if message.get("role") != "assistant":
                continue
            call = message.get("tool_call")
            if (
                isinstance(call, dict)
                and call.get("id") == call_id
                and call.get("name") == name
            ):
                return True
        return False

    def _tool_result(self, messages, call_id: str):
        for message in messages:
            if (
                message.get("role") == "tool"
                and message.get("tool_call_id") == call_id
            ):
                try:
                    return json.loads(message["content"])
                except (KeyError, TypeError, json.JSONDecodeError) as exc:
                    raise ValueError("工具结果不是合法的 JSON") from exc
        return None

    def respond(self, messages):
        if not isinstance(messages, list) or not messages:
            raise ValueError("模型需要非空的消息历史")

        list_result = self._tool_result(messages, "mock-call-1")
        if list_result is None:
            return {
                "type": "tool_call",
                "id": "mock-call-1",
                "name": "list_files",
                "arguments": {},
            }

        if not self._assistant_called(messages, "mock-call-1", "list_files"):
            raise ValueError("缺少 list_files 的 assistant 工具调用记录")
        list_data = list_result.get("result", {})
        if not list_result.get("ok") or "README.md" not in list_data.get("files", []):
            raise ValueError("list_files 没有返回预期的 README.md")

        read_result = self._tool_result(messages, "mock-call-2")
        if read_result is None:
            return {
                "type": "tool_call",
                "id": "mock-call-2",
                "name": "read_file",
                "arguments": {
                    "path": "README.md",
                    "start_line": 1,
                    "end_line": 3,
                },
            }

        if not self._assistant_called(messages, "mock-call-2", "read_file"):
            raise ValueError("缺少 read_file 的 assistant 工具调用记录")
        content = read_result.get("result", {}).get("content", "")
        if not read_result.get("ok") or "3: " not in content:
            raise ValueError("read_file 没有返回带行号的 README 内容")

        return {
            "type": "final",
            "content": "根据读取到的资料，相关信息位于 README.md:3。",
        }
