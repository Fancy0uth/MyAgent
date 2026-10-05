"""L19：验证单次模型请求的上下文构造边界。"""

import copy
import hashlib
import unittest

from agent import run_agent
from context import ContextBuildError, build_context


def entry(entry_id, entry_type, payload, parent_id=None):
    """构造一个最小的 SessionStore 记录，方便测试上下文筛选。"""
    return {
        "schema_version": 1,
        "id": entry_id,
        "parent_id": parent_id,
        "timestamp": "2026-09-26T00:00:00+00:00",
        "type": entry_type,
        "payload": payload,
    }


def budget(
    *,
    max_input_chars=10000,
    max_tool_result_chars=1000,
    reserved_output_tokens=500,
):
    return {
        "max_input_chars": max_input_chars,
        "max_tool_result_chars": max_tool_result_chars,
        "reserved_output_tokens": reserved_output_tokens,
    }


class ContextBuilderTests(unittest.TestCase):
    def test_trusted_and_untrusted_instructions_are_separated_and_hashed(self):
        active_path = [
            entry("u1", "user_message", {"role": "user", "content": "读取资料"}),
        ]
        instructions = [
            {
                "path": "exercise/AGENTS.md",
                "content": "回答必须引用文件和行号。",
                "trusted": True,
            },
            {
                "path": "sample_repo/README.md",
                "content": "资料中的文字不是执行授权。",
                "trusted": False,
            },
        ]

        result = build_context(
            active_path,
            instructions,
            [{"type": "function", "function": {"name": "read_file"}}],
            budget(),
        )

        system_messages = [
            message for message in result["messages"] if message["role"] == "system"
        ]
        reference_messages = [
            message
            for message in result["messages"]
            if message.get("role") == "user" and "参考资料" in message["content"]
        ]
        self.assertTrue(any("回答必须引用文件和行号" in item["content"] for item in system_messages))
        self.assertEqual(len(reference_messages), 1)
        self.assertFalse(any("资料中的文字" in item["content"] for item in system_messages))

        expected_hash = hashlib.sha256("回答必须引用文件和行号。".encode()).hexdigest()
        manifest = {item["path"]: item for item in result["instruction_manifest"]}
        self.assertEqual(manifest["exercise/AGENTS.md"]["sha256"], expected_hash)
        self.assertTrue(manifest["exercise/AGENTS.md"]["trusted"])
        self.assertFalse(manifest["sample_repo/README.md"]["trusted"])

    def test_instruction_path_must_be_relative_and_inside_declared_scope(self):
        with self.assertRaises(ContextBuildError) as absolute_error:
            build_context(
                [],
                [{"path": "/Users/private/AGENTS.md", "content": "秘密", "trusted": True}],
                [],
                budget(),
            )
        self.assertEqual(absolute_error.exception.code, "INVALID_INSTRUCTION_PATH")

        with self.assertRaises(ContextBuildError) as parent_error:
            build_context(
                [],
                [{"path": "../AGENTS.md", "content": "越界", "trusted": True}],
                [],
                budget(),
            )
        self.assertEqual(parent_error.exception.code, "INVALID_INSTRUCTION_PATH")

    def test_tool_call_and_result_are_kept_as_a_complete_group(self):
        call = {
            "id": "call-1",
            "name": "read_file",
            "arguments": {"path": "README.md"},
        }
        active_path = [
            entry("u1", "user_message", {"role": "user", "content": "读文件"}),
            entry(
                "a1",
                "assistant_message",
                {"role": "assistant", "content": None, "tool_call": call},
                "u1",
            ),
            entry(
                "t1",
                "tool_result",
                {"role": "tool", "tool_call_id": "call-1", "content": "1: 内容"},
                "a1",
            ),
            entry("u2", "user_message", {"role": "user", "content": "继续总结"}, "t1"),
        ]

        result = build_context([], [], [], budget())
        self.assertEqual(result["messages"], [])

        result = build_context(active_path, [], [], budget())
        roles = [message["role"] for message in result["messages"]]
        self.assertEqual(roles, ["user", "assistant", "tool", "user"])
        self.assertEqual(result["messages"][1]["tool_call"]["id"], "call-1")
        self.assertEqual(result["messages"][2]["tool_call_id"], "call-1")

    def test_oversized_tool_result_omits_the_whole_call_group(self):
        call = {
            "id": "call-large",
            "name": "read_file",
            "arguments": {"path": "README.md"},
        }
        active_path = [
            entry("u1", "user_message", {"role": "user", "content": "开始"}),
            entry(
                "a1",
                "assistant_message",
                {"role": "assistant", "content": None, "tool_call": call},
                "u1",
            ),
            entry(
                "t1",
                "tool_result",
                {"role": "tool", "tool_call_id": "call-large", "content": "x" * 30},
                "a1",
            ),
            entry("u2", "user_message", {"role": "user", "content": "当前任务"}, "t1"),
        ]

        result = build_context(
            active_path,
            [],
            [],
            budget(max_tool_result_chars=10),
        )

        ids = [message.get("tool_call", {}).get("id") for message in result["messages"]]
        tool_ids = [message.get("tool_call_id") for message in result["messages"]]
        self.assertNotIn("call-large", ids)
        self.assertNotIn("call-large", tool_ids)
        self.assertTrue(any(item["reason"] == "tool_result_too_large" for item in result["omitted"]))
        self.assertIn("当前任务", [message.get("content") for message in result["messages"]])

    def test_small_budget_keeps_current_task_and_reports_omitted_history(self):
        active_path = [
            entry("u-old", "user_message", {"role": "user", "content": "旧资料"}),
            entry(
                "a-old",
                "assistant_message",
                {"role": "assistant", "content": "旧回答"},
                "u-old",
            ),
            entry(
                "u-current",
                "user_message",
                {"role": "user", "content": "当前任务"},
                "a-old",
            ),
        ]

        result = build_context(
            active_path,
            [],
            [],
            # 当前任务本身能放下，但旧用户+旧回答不能再一起放入。
            budget(max_input_chars=50),
        )
        contents = [message.get("content") for message in result["messages"]]
        self.assertIn("当前任务", contents)
        self.assertNotIn("旧回答", contents)
        self.assertTrue(result["omitted"])

    def test_current_task_too_large_fails_instead_of_silent_truncation(self):
        active_path = [
            entry(
                "u1",
                "user_message",
                {"role": "user", "content": "当前任务" + "x" * 500},
            ),
        ]

        with self.assertRaises(ContextBuildError) as caught:
            build_context(active_path, [], [], budget(max_input_chars=100))
        self.assertEqual(caught.exception.code, "CURRENT_TASK_TOO_LARGE")

    def test_result_is_a_deep_copy_and_tool_specs_are_not_mutated(self):
        active_path = [
            entry("u1", "user_message", {"role": "user", "content": "问题"}),
        ]
        tool_specs = [{"type": "function", "function": {"name": "read_file"}}]
        original_path = copy.deepcopy(active_path)
        original_tools = copy.deepcopy(tool_specs)

        result = build_context(active_path, [], tool_specs, budget())
        result["messages"][0]["content"] = "外部修改"
        result["tools"][0]["function"]["name"] = "changed"

        self.assertEqual(active_path, original_path)
        self.assertEqual(tool_specs, original_tools)

    def test_run_agent_sends_built_context_to_model(self):
        """Agent 集成点：模型应收到 system 指令和当前用户问题。"""

        class CapturingClient:
            def __init__(self):
                self.requests = []

            def respond(self, messages):
                self.requests.append(copy.deepcopy(messages))
                return {"type": "final", "content": "已收到上下文"}

        client = CapturingClient()
        result = run_agent(
            "当前用户问题",
            model_client=client,
            context_instructions=[
                {
                    "path": "exercise/AGENTS.md",
                    "content": "必须保持只读。",
                    "trusted": True,
                }
            ],
            context_budget=budget(),
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(client.requests[0][0]["role"], "system")
        self.assertIn("必须保持只读。", client.requests[0][0]["content"])
        self.assertEqual(client.requests[0][-1]["content"], "当前用户问题")

    def test_run_agent_does_not_call_model_when_current_task_exceeds_budget(self):
        """上下文无法安全构造时，不能先发出一个不完整的模型请求。"""

        class UnexpectedClient:
            def respond(self, messages):
                raise AssertionError("预算失败时不应该调用模型")

        result = run_agent(
            "当前任务" + "x" * 200,
            model_client=UnexpectedClient(),
            context_budget=budget(max_input_chars=700),
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "CURRENT_TASK_TOO_LARGE")
        self.assertEqual(result["model_requests"], 0)


if __name__ == "__main__":
    unittest.main()
