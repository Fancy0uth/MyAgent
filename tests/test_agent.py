import json
import unittest
from types import SimpleNamespace

from agent import run_agent
from model_client import DeepSeekModelClient, MockModelClient


class RepeatingToolClient:
    def respond(self, messages):
        return {
            "type": "tool_call",
            "id": f"repeat-{len(messages)}",
            "name": "list_files",
            "arguments": {},
        }


class RecoverableErrorClient:
    def __init__(self):
        self.calls = 0

    def respond(self, messages):
        self.calls += 1
        if self.calls == 1:
            return {
                "type": "tool_call",
                "id": "bad-call",
                "name": "unknown_tool",
                "arguments": {},
            }
        return {"type": "final", "content": "模型已根据工具错误重新回答。"}


class ScriptedCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


class ScriptedOpenAIClient:
    def __init__(self, responses):
        self.completions = ScriptedCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)


def tool_response(call_id, name, arguments):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id=call_id,
                            function=SimpleNamespace(
                                name=name,
                                arguments=arguments,
                            ),
                        )
                    ],
                ),
            )
        ]
    )


def final_response(content):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=content, tool_calls=None),
            )
        ]
    )


class AgentLoopTests(unittest.TestCase):
    def test_mock_agent_uses_tool_results_and_finishes(self):
        result = run_agent(
            "任务数据记录在哪里？",
            model_client=MockModelClient(),
        )

        self.assertEqual(result["status"], "completed")
        self.assertIn("README.md:3", result["answer"])
        self.assertEqual(result["model_requests"], 3)
        self.assertEqual(result["tool_executions"], 2)
        self.assertTrue(
            any(message["role"] == "tool" for message in result["history"])
        )

    def test_agent_stops_without_an_extra_model_request_at_budget(self):
        result = run_agent(
            "重复调用工具",
            model_client=RepeatingToolClient(),
            max_model_requests=2,
        )

        self.assertEqual(result["status"], "stopped_limit")
        self.assertEqual(result["model_requests"], 2)
        self.assertEqual(result["tool_executions"], 2)

    def test_real_client_completes_three_tool_round_trip(self):
        fake_client = ScriptedOpenAIClient(
            [
                tool_response("call-1", "list_files", "{}"),
                tool_response("call-2", "search_text", '{"query": "SQLite"}'),
                tool_response(
                    "call-3",
                    "read_file",
                    '{"path": "README.md", "start_line": 1, "end_line": 3}',
                ),
                final_response("依据是 README.md:3。"),
            ]
        )
        client = DeepSeekModelClient(api_client=fake_client)

        result = run_agent("SQLite 数据保存在哪里？", model_client=client)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"], "依据是 README.md:3。")
        self.assertEqual(result["model_requests"], 4)
        self.assertEqual(result["tool_executions"], 3)
        self.assertEqual(len(fake_client.chat.completions.requests), 4)
        third_request_messages = fake_client.chat.completions.requests[2]["messages"]
        self.assertEqual(third_request_messages[-1]["tool_call_id"], "call-2")

    def test_agent_executes_all_tool_calls_in_one_model_response(self):
        multi_call_client = MultiToolCallClient()

        result = run_agent("同时查看资料", model_client=multi_call_client)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["tool_executions"], 2)
        tool_results = [
            message
            for message in result["history"]
            if message["role"] == "tool"
        ]
        self.assertEqual(
            [message["tool_call_id"] for message in tool_results],
            ["multi-1", "multi-2"],
        )

    def test_agent_returns_recoverable_tool_error_to_model(self):
        result = run_agent(
            "调用一个不存在的工具",
            model_client=RecoverableErrorClient(),
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["model_requests"], 2)
        self.assertEqual(result["tool_executions"], 1)
        self.assertIn("UNKNOWN_TOOL", result["history"][2]["content"])


class MultiToolCallClient:
    def __init__(self):
        self.calls = 0

    def respond(self, messages):
        self.calls += 1
        if self.calls == 1:
            return {
                "type": "tool_calls",
                "calls": [
                    {"id": "multi-1", "name": "list_files", "arguments": {}},
                    {
                        "id": "multi-2",
                        "name": "search_text",
                        "arguments": {"query": "SQLite"},
                    },
                ],
            }
        return {"type": "final", "content": "已完成。"}


class ThreeToolCallClient:
    def respond(self, messages):
        return {
            "type": "tool_calls",
            "calls": [
                {"id": "three-1", "name": "list_files", "arguments": {}},
                {"id": "three-2", "name": "list_files", "arguments": {}},
                {"id": "three-3", "name": "list_files", "arguments": {}},
            ],
        }


class ToolBudgetTests(unittest.TestCase):
    def test_all_remaining_calls_are_recorded_as_not_executed(self):
        result = run_agent(
            "测试多个工具的预算",
            model_client=ThreeToolCallClient(),
            max_tool_executions=1,
        )

        self.assertEqual(result["status"], "stopped_limit")
        tool_results = [
            message
            for message in result["history"]
            if message["role"] == "tool"
        ]
        self.assertEqual(
            [message["tool_call_id"] for message in tool_results],
            ["three-1", "three-2", "three-3"],
        )
        self.assertEqual(json.loads(tool_results[1]["content"])["executed"], False)
        self.assertEqual(json.loads(tool_results[2]["content"])["executed"], False)


if __name__ == "__main__":
    unittest.main()
