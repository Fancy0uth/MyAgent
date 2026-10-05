"""L24：验证显式扩展、事件订阅和工具执行前检查。"""

import json
import unittest
from unittest.mock import patch

from agent import run_agent
from events import EventRecorder
from extensions import (
    ExtensionError,
    ExtensionManager,
    protect_validation_files,
)


class ToolThenFinalClient:
    def __init__(self, tool_name="list_files", arguments=None):
        self.tool_name = tool_name
        self.arguments = {} if arguments is None else arguments
        self.requests = []

    def respond(self, messages):
        self.requests.append(messages)
        if len(self.requests) == 1:
            return {
                "type": "tool_call",
                "id": "extension-call-1",
                "name": self.tool_name,
                "arguments": self.arguments,
            }
        return {"type": "final", "content": "扩展测试完成"}


class ExtensionManagerTests(unittest.TestCase):
    def test_event_subscriber_receives_events_in_order(self):
        manager = ExtensionManager()
        recorder = EventRecorder()
        seen = []
        manager.subscribe("tool_started", lambda event: seen.append(event["tool_name"]))
        manager.attach(recorder)

        recorder.emit(
            "tool_started",
            run_id="run-001",
            tool_call_id="call-001",
            tool_name="read_file",
        )
        recorder.emit(
            "tool_started",
            run_id="run-001",
            tool_call_id="call-002",
            tool_name="search_text",
        )

        self.assertEqual(seen, ["read_file", "search_text"])

    def test_event_callback_error_is_not_swallowed(self):
        manager = ExtensionManager()
        recorder = EventRecorder()

        def broken_callback(_event):
            raise RuntimeError("扩展回调失败")

        manager.subscribe("tool_started", broken_callback)
        manager.attach(recorder)

        with self.assertRaises(RuntimeError):
            recorder.emit(
                "tool_started",
                run_id="run-001",
                tool_call_id="call-001",
                tool_name="read_file",
            )

    def test_before_tool_can_block_without_dispatching_the_tool(self):
        manager = ExtensionManager()
        manager.register_before_tool(
            lambda _call: {
                "error": "NO_SEARCH",
                "message": "本次扩展禁止搜索",
            }
        )
        client = ToolThenFinalClient("search_text", {"query": "SQLite"})

        with patch("tools.dispatch_tool") as dispatch:
            result = run_agent(
                "搜索资料",
                model_client=client,
                extension_manager=manager,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["tool_executions"], 0)
        dispatch.assert_not_called()
        self.assertTrue(
            any(event["event"] == "tool_blocked" for event in result["events"])
        )
        # 工具结果后，模型还会继续返回最终答复，所以不能只看 history 的最后一项。
        self.assertTrue(
            any(
                "NO_SEARCH" in message["content"]
                for message in result["history"]
                if message.get("role") == "tool"
            )
        )

    def test_before_tool_exception_fails_without_executing_tool(self):
        manager = ExtensionManager()

        def broken_check(_call):
            raise RuntimeError("检查器崩溃")

        manager.register_before_tool(broken_check)
        client = ToolThenFinalClient()

        with patch("tools.dispatch_tool") as dispatch:
            result = run_agent(
                "查看资料",
                model_client=client,
                extension_manager=manager,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "EXTENSION_ERROR")
        self.assertEqual(result["tool_executions"], 0)
        dispatch.assert_not_called()

    def test_explicit_read_only_tool_is_dispatchable_and_has_definition(self):
        manager = ExtensionManager()

        def count_items(prefix=""):
            return {"ok": True, "count": len(prefix)}

        manager.register_read_only_tool(
            "count_items",
            count_items,
            {
                "type": "function",
                "function": {
                    "name": "count_items",
                    "description": "计算输入前缀的字符数。",
                    "parameters": {
                        "type": "object",
                        "properties": {"prefix": {"type": "string"}},
                        "required": [],
                    },
                },
            },
        )

        result = manager.dispatch_tool(
            json.dumps(
                {
                    "id": "call-count",
                    "name": "count_items",
                    "arguments": {"prefix": "abc"},
                }
            )
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["count"], 3)
        self.assertIn(
            "count_items",
            [
                definition["function"]["name"]
                for definition in manager.tool_definitions()
            ],
        )
        manager.close()

    def test_duplicate_tool_names_are_rejected(self):
        manager = ExtensionManager()
        definition = {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "重复工具",
                "parameters": {"type": "object", "properties": {}},
            },
        }

        with self.assertRaises(ExtensionError) as caught:
            manager.register_read_only_tool("read_file", lambda: {}, definition)

        self.assertEqual(caught.exception.code, "DUPLICATE_TOOL")

    def test_validation_file_extension_is_explicit_and_does_not_replace_policy(self):
        self.assertIsNone(
            protect_validation_files(
                {"name": "edit_file", "arguments": {"path": "buggy.py"}}
            )
        )
        blocked = protect_validation_files(
            {"name": "edit_file", "arguments": {"path": "tests/test_mean.py"}}
        )
        self.assertEqual(blocked["error"], "PROTECTED_TEST_FILE")


if __name__ == "__main__":
    unittest.main()
