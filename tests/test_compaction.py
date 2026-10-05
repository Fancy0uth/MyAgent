"""L20：验证固定摘要器和手动压缩上下文。"""

import copy
import json
import tempfile
import unittest

from agent import run_agent
from context import ContextBuildError, build_compacted_context
from sessions import SessionStore


def entry(entry_id, entry_type, payload, parent_id=None):
    return {
        "schema_version": 1,
        "id": entry_id,
        "parent_id": parent_id,
        "timestamp": "2026-09-28T00:00:00+00:00",
        "type": entry_type,
        "payload": payload,
    }


def budget(*, max_input_chars=10000, max_tool_result_chars=1000):
    return {
        "max_input_chars": max_input_chars,
        "max_tool_result_chars": max_tool_result_chars,
        "reserved_output_tokens": 500,
    }


class CompactionTests(unittest.TestCase):
    def test_summary_has_fixed_fields_and_recent_task_is_preserved(self):
        active_path = [
            entry(
                "u-old",
                "user_message",
                {"role": "user", "content": "请分析项目数据保存在哪里"},
            ),
            entry(
                "a-old",
                "assistant_message",
                {"role": "assistant", "content": "我会先读取 README。"},
                "u-old",
            ),
            entry(
                "a-call",
                "assistant_message",
                {
                    "role": "assistant",
                    "content": None,
                    "tool_call": {
                        "id": "call-old",
                        "name": "read_file",
                        "arguments": {
                            "path": "README.md",
                            "start_line": 1,
                            "end_line": 3,
                        },
                    },
                },
                "a-old",
            ),
            entry(
                "t-old",
                "tool_result",
                {
                    "role": "tool",
                    "tool_call_id": "call-old",
                    "content": json.dumps(
                        {
                            "ok": True,
                            "result": {
                                "path": "README.md",
                                "content": "数据保存在 SQLite 数据库。",
                            },
                        },
                        ensure_ascii=False,
                    ),
                },
                "a-call",
            ),
            entry(
                "u-current",
                "user_message",
                {"role": "user", "content": "继续解释刚才的结果"},
                "t-old",
            ),
        ]
        original_path = copy.deepcopy(active_path)

        result = build_compacted_context(
            active_path,
            [],
            [],
            budget(),
            keep_recent_groups=1,
        )

        summary = result["compaction"]["summary"]
        self.assertEqual(
            set(summary),
            {
                "goal",
                "constraints",
                "completed_facts",
                "unfinished_items",
                "failures",
                "key_files",
                "next_step",
            },
        )
        self.assertEqual(summary["goal"], "请分析项目数据保存在哪里")
        self.assertIn("README.md", summary["key_files"])
        self.assertTrue(summary["completed_facts"])
        self.assertIn("继续解释刚才的结果", result["messages"][-1]["content"])
        self.assertEqual(result["messages"][0]["role"], "user")
        self.assertIn("历史摘要", result["messages"][0]["content"])
        self.assertTrue(any(item["reason"] == "compacted" for item in result["omitted"]))
        self.assertEqual(active_path, original_path)

    def test_recent_tool_call_and_result_stay_as_one_group(self):
        active_path = [
            entry("u1", "user_message", {"role": "user", "content": "读取资料"}),
            entry(
                "a1",
                "assistant_message",
                {
                    "role": "assistant",
                    "content": None,
                    "tool_call": {
                        "id": "call-1",
                        "name": "read_file",
                        "arguments": {"path": "README.md", "start_line": 1, "end_line": 3},
                    },
                },
                "u1",
            ),
            entry(
                "t1",
                "tool_result",
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": '{"ok": true}',
                },
                "a1",
            ),
            entry(
                "u2",
                "user_message",
                {"role": "user", "content": "总结结果"},
                "t1",
            ),
        ]

        result = build_compacted_context(
            active_path,
            [],
            [],
            budget(),
            keep_recent_groups=2,
        )

        recent_ids = result["compaction"]["recent_entry_ids"]
        self.assertEqual(recent_ids, ["a1", "t1", "u2"])
        self.assertEqual(
            result["messages"][-3]["tool_call"]["id"],
            "call-1",
        )
        self.assertEqual(result["messages"][-2]["tool_call_id"], "call-1")
        self.assertEqual(result["messages"][-2]["content"], '{"ok": true}')

    def test_incomplete_tool_group_stops_compaction_instead_of_splitting_it(self):
        active_path = [
            entry("u1", "user_message", {"role": "user", "content": "读取资料"}),
            entry(
                "a1",
                "assistant_message",
                {
                    "role": "assistant",
                    "content": None,
                    "tool_call": {
                        "id": "call-missing",
                        "name": "read_file",
                        "arguments": {},
                    },
                },
                "u1",
            ),
        ]

        with self.assertRaises(ContextBuildError) as caught:
            build_compacted_context(
                active_path,
                [],
                [],
                budget(),
                keep_recent_groups=1,
            )
        self.assertEqual(caught.exception.code, "COMPACTION_UNSAFE")

    def test_oversized_tool_result_stops_compaction_without_truncating(self):
        active_path = [
            entry("u1", "user_message", {"role": "user", "content": "读取资料"}),
            entry(
                "a1",
                "assistant_message",
                {
                    "role": "assistant",
                    "content": None,
                    "tool_call": {
                        "id": "call-large",
                        "name": "read_file",
                        "arguments": {},
                    },
                },
                "u1",
            ),
            entry(
                "t1",
                "tool_result",
                {
                    "role": "tool",
                    "tool_call_id": "call-large",
                    "content": "x" * 101,
                },
                "a1",
            ),
        ]

        with self.assertRaises(ContextBuildError) as caught:
            build_compacted_context(
                active_path,
                [],
                [],
                budget(max_tool_result_chars=100),
                keep_recent_groups=1,
            )
        self.assertEqual(caught.exception.code, "COMPACTION_GROUP_TOO_LARGE")

    def test_repeated_compaction_is_deterministic_and_keeps_source_history(self):
        active_path = [
            entry("u1", "user_message", {"role": "user", "content": "第一个目标"}),
            entry("u2", "user_message", {"role": "user", "content": "当前目标"}, "u1"),
        ]
        original_path = copy.deepcopy(active_path)

        first = build_compacted_context(
            active_path,
            [],
            [],
            budget(),
            keep_recent_groups=1,
        )
        second = build_compacted_context(
            active_path,
            [],
            [],
            budget(),
            keep_recent_groups=1,
        )

        self.assertEqual(first["compaction"], second["compaction"])
        self.assertEqual(active_path, original_path)

    def test_agent_uses_compaction_only_when_explicitly_enabled(self):
        class CapturingClient:
            def __init__(self):
                self.requests = []

            def respond(self, messages):
                self.requests.append(copy.deepcopy(messages))
                return {"type": "final", "content": "完成"}

        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(f"{directory}/session.jsonl")
            store.append(
                "user_message",
                {"role": "user", "content": "旧任务"},
            )
            store.append(
                "assistant_message",
                {"role": "assistant", "content": "旧回答"},
            )
            client = CapturingClient()

            result = run_agent(
                "新任务",
                model_client=client,
                session_store=store,
                use_compaction=True,
                compaction_keep_recent_groups=1,
            )

        self.assertEqual(result["status"], "completed")
        self.assertIn("历史摘要", client.requests[0][0]["content"])
        self.assertEqual(client.requests[0][-1]["content"], "新任务")


if __name__ == "__main__":
    unittest.main()
