import unittest

from agent import run_agent
from model_client import MockModelClient


class RepeatingToolClient:
    def respond(self, messages):
        return {
            "type": "tool_call",
            "id": f"repeat-{len(messages)}",
            "name": "list_files",
            "arguments": {},
        }


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


if __name__ == "__main__":
    unittest.main()
