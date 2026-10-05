"""L14 修复闭环的离线测试。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import recover_agent, run_coding_flow
from agent_tools.edit import EditProposalStore
from agent_tools.execution import TestCase, TestCaseRegistry, TestExecutor
from sessions import SessionLoadError, SessionStore


class RepairMockClient:
    """根据真实工具结果推进流程，而不是盲目返回固定成功答案。"""

    def respond(self, messages):
        tool_messages = [
            message for message in messages if message.get("role") == "tool"
        ]
        if not tool_messages:
            return {
                "type": "tool_call",
                "id": "read-1",
                "name": "read_workspace_file",
                "arguments": {
                    "path": "buggy.py",
                    "start_line": 1,
                    "end_line": 20,
                },
            }

        last_tool = tool_messages[-1]
        last_result = json.loads(last_tool["content"])
        last_call = next(
            message
            for message in reversed(messages)
            if message.get("role") == "assistant"
        )
        last_name = last_call["tool_call"]["name"]

        if last_name == "read_workspace_file":
            return {
                "type": "tool_call",
                "id": "test-1",
                "name": "run_tests",
                "arguments": {"case_id": "mean_case"},
            }

        if last_name == "run_tests":
            if last_result.get("exit_code") != 0:
                return {
                    "type": "tool_call",
                    "id": "edit-1",
                    "name": "propose_edit",
                    "arguments": {
                        "path": "buggy.py",
                        "old_text": "return sum(values) / (len(values) + 1)",
                        "new_text": "return sum(values) / len(values)",
                    },
                }
            return {
                "type": "final",
                "content": "测试已通过，Bug 已修复。",
            }

        if last_name == "propose_edit" and last_result.get("applied"):
            return {
                "type": "tool_call",
                "id": "test-2",
                "name": "run_tests",
                "arguments": {"case_id": "mean_case"},
            }

        return {"type": "final", "content": "我无法继续。"}


class FinalTooEarlyClient:
    """故意在没有测试通过证据时声称完成。"""

    def respond(self, messages):
        return {"type": "final", "content": "已经修复。"}


class CodingFlowTests(unittest.TestCase):
    """验证读取、测试、编辑、确认和再次测试的真实连接。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        (self.workspace / "buggy.py").write_text(
            "def mean(values):\n"
            "    return sum(values) / (len(values) + 1)\n",
            encoding="utf-8",
        )
        (self.workspace / "test_mean_test.py").write_text(
            "import unittest\n"
            "from buggy import mean\n\n"
            "class MeanTests(unittest.TestCase):\n"
            "    def test_single_value(self):\n"
            "        self.assertEqual(mean([5]), 5.0)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )

        registry = TestCaseRegistry()
        registry.register(
            TestCase(
                case_id="mean_case",
                workspace=self.workspace,
                argv=(
                    sys.executable,
                    "-m",
                    "unittest",
                    "test_mean_test.py",
                    "-v",
                ),
            )
        )
        self.executor = TestExecutor(registry)
        self.edit_store = EditProposalStore()

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _approve(proposal):
        return {
            "approved": True,
            "proposal_id": proposal["proposal_id"],
            "path": proposal["path"],
            "old_sha256": proposal["old_sha256"],
            "new_sha256": proposal["new_sha256"],
        }

    def test_mock_agent_completes_repair_loop_using_real_test_results(self):
        result = run_coding_flow(
            "修复 mean 函数并确保测试通过",
            workspace=self.workspace,
            model_client=RepairMockClient(),
            test_executor=self.executor,
            edit_store=self.edit_store,
            confirm_edit=self._approve,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["tests_run"], 2)
        self.assertEqual(result["edits_applied"], 1)
        self.assertIn("测试已通过", result["answer"])
        self.assertIn(
            "return sum(values) / len(values)",
            (self.workspace / "buggy.py").read_text(encoding="utf-8"),
        )

    def test_missing_confirmation_waits_without_writing(self):
        result = run_coding_flow(
            "修复 mean 函数",
            workspace=self.workspace,
            model_client=RepairMockClient(),
            test_executor=self.executor,
            edit_store=self.edit_store,
        )

        self.assertEqual(result["status"], "waiting_confirmation")
        self.assertIn("pending_proposal", result)
        self.assertIn(
            "len(values) + 1",
            (self.workspace / "buggy.py").read_text(encoding="utf-8"),
        )

    def test_final_answer_without_passing_test_is_rejected(self):
        result = run_coding_flow(
            "修复 mean 函数",
            workspace=self.workspace,
            model_client=FinalTooEarlyClient(),
            test_executor=self.executor,
            edit_store=self.edit_store,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "UNVERIFIED_REPAIR")

    def test_session_store_records_write_identity_approval_and_result(self):
        session_store = SessionStore(self.workspace / "session.jsonl")

        result = run_coding_flow(
            "修复 mean 函数并确保测试通过",
            workspace=self.workspace,
            model_client=RepairMockClient(),
            test_executor=self.executor,
            edit_store=self.edit_store,
            confirm_edit=self._approve,
            session_store=session_store,
        )

        self.assertEqual(result["status"], "completed")
        entries = session_store.load()
        by_type = {}
        for entry in entries:
            by_type.setdefault(entry["type"], []).append(entry["payload"])

        intent = by_type["operation_intent"][0]
        approval = by_type["operation_approval"][0]
        operation_result = by_type["operation_result"][0]
        self.assertEqual(approval["operation_id"], intent["operation_id"])
        self.assertTrue(approval["approved"])
        self.assertEqual(operation_result["outcome"], "succeeded")
        self.assertNotIn("new_content", intent)

    def test_write_completed_but_result_not_saved_requires_reconciliation(self):
        session_store = SessionStore(self.workspace / "session.jsonl")
        with patch.object(
            SessionStore,
            "record_operation_result",
            side_effect=SessionLoadError("WRITE_ERROR", "模拟结果落盘失败"),
        ):
            result = run_coding_flow(
                "修复 mean 函数并确保测试通过",
                workspace=self.workspace,
                model_client=RepairMockClient(),
                test_executor=self.executor,
                edit_store=self.edit_store,
                confirm_edit=self._approve,
                session_store=session_store,
            )

        self.assertEqual(result["status"], "recovery_required")
        self.assertIn(
            "return sum(values) / len(values)",
            (self.workspace / "buggy.py").read_text(encoding="utf-8"),
        )
        recovered = recover_agent(
            session_store,
            model_client=RepairMockClient(),
            workspace_root=self.workspace,
            permission_profile="workspace_write_confirmed",
        )
        self.assertEqual(recovered["status"], "recovery_required")
        self.assertEqual(
            recovered["pending_operations"][0]["state"],
            "effect_observed_result_missing",
        )
        self.assertFalse(recovered["pending_operations"][0]["safe_to_replay"])


if __name__ == "__main__":
    unittest.main()
