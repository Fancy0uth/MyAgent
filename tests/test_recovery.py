"""L17：验证会话恢复、只读重试和写入结果未知时的安全行为。"""

import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent
from agent import recover_agent
from model_client import MockModelClient
from sessions import SessionStore
from tools import ROOT as READ_ONLY_ROOT


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.session_path = self.root / "session.jsonl"
        self.store = SessionStore(self.session_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _start_pending_run(self, name="read_file", arguments=None):
        """写出一个已落盘调用、但尚无工具结果的中断现场。"""
        call = {
            "id": "call-before-crash",
            "name": name,
            "arguments": arguments or {"path": "README.md"},
        }
        self.store.append(
            "run_context",
            {
                "run_id": "run-test",
                "workspace": str(READ_ONLY_ROOT.resolve()),
                "permission_profile": "read_only",
                "provider": "MockModelClient",
            },
        )
        self.store.append("user_message", {"role": "user", "content": "读取 README"})
        assistant = {"role": "assistant", "content": None, "tool_call": call}
        self.store.append("assistant_message", assistant)
        self.store.append("tool_call", call)
        return call

    def test_dangling_tool_call_is_not_filled_with_a_fake_result(self):
        call = self._start_pending_run()

        recovered = recover_agent(
            self.store,
            model_client=MockModelClient(),
            workspace_root=READ_ONLY_ROOT,
            permission_profile="read_only",
        )

        self.assertEqual(recovered["status"], "recovery_required")
        self.assertEqual(recovered["pending_tool_calls"][0]["id"], call["id"])
        self.assertEqual(len(recovered["history"]), 2)
        self.assertFalse(any(item.get("role") == "tool" for item in recovered["history"]))
        self.assertEqual(recovered["reason"], "read_only_retry_budget_required")

    def test_read_only_call_retries_only_with_explicit_budget_and_keeps_call_id(self):
        call = self._start_pending_run()
        with patch.object(agent, "dispatch_tool", wraps=agent.dispatch_tool) as dispatch:
            recovered = recover_agent(
                self.store,
                model_client=MockModelClient(),
                workspace_root=READ_ONLY_ROOT,
                permission_profile="read_only",
                read_only_retry_budget=1,
            )

        dispatch.assert_called_once()
        dispatched_request = json.loads(dispatch.call_args.args[0])
        self.assertEqual(dispatched_request["id"], call["id"])
        self.assertEqual(recovered["status"], "ready_to_resume")
        self.assertEqual(recovered["pending_tool_calls"], [])
        self.assertEqual(recovered["history"][-1]["tool_call_id"], call["id"])
        self.assertEqual(self.store.load()[-1]["type"], "tool_result")

    def test_write_or_command_call_is_never_automatically_retried(self):
        self._start_pending_run("run_tests", {"case_id": "mean_case"})
        with patch.object(agent, "dispatch_tool") as dispatch:
            recovered = recover_agent(
                self.store,
                model_client=MockModelClient(),
                workspace_root=READ_ONLY_ROOT,
                permission_profile="read_only",
                read_only_retry_budget=10,
            )

        dispatch.assert_not_called()
        self.assertEqual(recovered["status"], "recovery_required")
        self.assertEqual(recovered["reason"], "unknown_side_effect_result")

    def test_final_answer_saved_without_status_is_not_requested_again(self):
        self.store.append(
            "run_context",
            {
                "run_id": "run-final",
                "workspace": str(READ_ONLY_ROOT.resolve()),
                "permission_profile": "read_only",
                "provider": "MockModelClient",
            },
        )
        self.store.append("user_message", {"role": "user", "content": "問題"})
        self.store.append("assistant_message", {"role": "assistant", "content": "已完成"})

        class MustNotRun:
            def respond(self, messages):
                raise AssertionError("恢復已保存的最終回答不應再次呼叫模型")

        recovered = recover_agent(
            self.store,
            model_client=MustNotRun(),
            workspace_root=READ_ONLY_ROOT,
            permission_profile="read_only",
        )

        self.assertEqual(recovered["status"], "completed")
        self.assertTrue(recovered["status_missing"])
        self.assertEqual(recovered["answer"], "已完成")

    def test_workspace_permission_or_provider_mismatch_blocks_resume(self):
        self._start_pending_run("read_file", {"path": "README.md"})
        recovered = recover_agent(
            self.store,
            model_client=object(),
            workspace_root=self.root,
            permission_profile="workspace_write_confirmed",
        )

        self.assertEqual(recovered["status"], "recovery_required")
        self.assertFalse(recovered["context_checks"]["workspace_matches"])
        self.assertFalse(recovered["context_checks"]["permission_profile_matches"])
        self.assertFalse(recovered["context_checks"]["provider_available"])

    def test_approved_write_intent_is_reconciled_by_hash_without_replay(self):
        workspace = self.root / "workspace"
        workspace.mkdir()
        target = workspace / "example.py"
        old_content = "value = 1\n"
        new_content = "value = 2\n"
        target.write_text(new_content, encoding="utf-8")

        import hashlib

        old_hash = hashlib.sha256(old_content.encode()).hexdigest()
        new_hash = hashlib.sha256(new_content.encode()).hexdigest()
        proposal = {
            "ok": True,
            "proposal_id": "proposal-123",
            "path": "example.py",
            "old_sha256": old_hash,
            "new_sha256": new_hash,
        }
        self.store.record_operation_intent(
            "operation-123",
            proposal,
            tool_call_id="edit-call-1",
            workspace_root=workspace,
        )
        self.store.record_operation_approval(
            "operation-123",
            {
                "approved": True,
                "proposal_id": proposal["proposal_id"],
                "path": proposal["path"],
                "old_sha256": old_hash,
                "new_sha256": new_hash,
            },
        )

        recovered = recover_agent(
            self.store,
            workspace_root=workspace,
            permission_profile="workspace_write_confirmed",
        )

        self.assertEqual(recovered["status"], "recovery_required")
        operation = recovered["pending_operations"][0]
        self.assertEqual(operation["state"], "effect_observed_result_missing")
        self.assertFalse(operation["safe_to_replay"])
        self.assertEqual(target.read_text(encoding="utf-8"), new_content)

    def test_recorded_write_result_repairs_missing_tool_message_from_ledger(self):
        workspace = self.root / "workspace"
        workspace.mkdir()
        old_content = "value = 1\n"
        new_content = "value = 2\n"
        target = workspace / "example.py"
        target.write_text(new_content, encoding="utf-8")
        old_hash = hashlib.sha256(old_content.encode()).hexdigest()
        new_hash = hashlib.sha256(new_content.encode()).hexdigest()
        call = {
            "id": "edit-call-2",
            "name": "propose_edit",
            "arguments": {"path": "example.py"},
        }
        self.store.append(
            "run_context",
            {
                "run_id": "run-write",
                "workspace": str(workspace.resolve()),
                "permission_profile": "workspace_write_confirmed",
                "provider": "MockModelClient",
            },
        )
        self.store.append("user_message", {"role": "user", "content": "编辑文件"})
        self.store.append(
            "assistant_message",
            {"role": "assistant", "content": None, "tool_call": call},
        )
        self.store.append("tool_call", call)
        proposal = {
            "ok": True,
            "proposal_id": "proposal-456",
            "path": "example.py",
            "old_sha256": old_hash,
            "new_sha256": new_hash,
        }
        self.store.record_operation_intent(
            "operation-456",
            proposal,
            tool_call_id=call["id"],
            workspace_root=workspace,
        )
        confirmation = {
            "approved": True,
            "proposal_id": proposal["proposal_id"],
            "path": proposal["path"],
            "old_sha256": old_hash,
            "new_sha256": new_hash,
        }
        self.store.record_operation_approval("operation-456", confirmation)
        self.store.record_operation_result("operation-456", {"ok": True})

        recovered = recover_agent(
            self.store,
            model_client=MockModelClient(),
            workspace_root=workspace,
            permission_profile="workspace_write_confirmed",
        )

        self.assertEqual(recovered["status"], "ready_to_resume")
        self.assertEqual(recovered["pending_tool_calls"], [])
        self.assertEqual(recovered["reconstructed_tool_results"], 1)
        self.assertEqual(recovered["history"][-1]["tool_call_id"], call["id"])

    def test_recovery_blocks_when_a_file_changed_after_recorded_success(self):
        workspace = self.root / "workspace"
        workspace.mkdir()
        target = workspace / "example.py"
        old_content = "value = 1\n"
        new_content = "value = 2\n"
        target.write_text(new_content, encoding="utf-8")
        old_hash = hashlib.sha256(old_content.encode()).hexdigest()
        new_hash = hashlib.sha256(new_content.encode()).hexdigest()
        proposal = {
            "ok": True,
            "proposal_id": "proposal-789",
            "path": "example.py",
            "old_sha256": old_hash,
            "new_sha256": new_hash,
        }
        self.store.record_operation_intent(
            "operation-789",
            proposal,
            tool_call_id="edit-call-3",
            workspace_root=workspace,
        )
        self.store.record_operation_approval(
            "operation-789",
            {
                "approved": True,
                "proposal_id": proposal["proposal_id"],
                "path": proposal["path"],
                "old_sha256": old_hash,
                "new_sha256": new_hash,
            },
        )
        self.store.record_operation_result("operation-789", {"ok": True})
        target.write_text("value = 3\n", encoding="utf-8")

        recovered = recover_agent(
            self.store,
            workspace_root=workspace,
            permission_profile="workspace_write_confirmed",
        )

        self.assertEqual(recovered["status"], "recovery_required")
        self.assertEqual(
            recovered["pending_operations"][0]["state"],
            "file_version_changed_after_recorded_write",
        )

    def test_branch_selection_does_not_resume_completed_ancestor_run(self):
        self.store.append(
            "run_context",
            {
                "run_id": "completed-before-branch",
                "workspace": str(READ_ONLY_ROOT.resolve()),
                "permission_profile": "read_only",
                "provider": "MockModelClient",
            },
        )
        user = self.store.append(
            "user_message", {"role": "user", "content": "旧用户请求"}
        )
        self.store.append(
            "assistant_message", {"role": "assistant", "content": "旧最终回答"}
        )
        self.store.append("run_status", {"status": "completed"})
        self.store.select_active_node(user["id"])

        recovered = recover_agent(
            self.store,
            model_client=MockModelClient(),
            workspace_root=READ_ONLY_ROOT,
            permission_profile="read_only",
        )

        self.assertEqual(recovered["status"], "recovery_required")
        self.assertEqual(recovered["reason"], "branch_selected")
        self.assertEqual(
            [message["content"] for message in recovered["history"]],
            ["旧用户请求"],
        )

    def test_recovery_uses_active_messages_but_checks_all_branch_operations(self):
        shared = self.store.append(
            "user_message", {"role": "user", "content": "共同任务"}
        )
        call = {"id": "edit-on-a", "name": "propose_edit", "arguments": {}}
        answer_a = self.store.append(
            "assistant_message",
            {"role": "assistant", "content": None, "tool_call": call},
            parent_id=shared["id"],
        )
        self.store.append("tool_call", call, parent_id=answer_a["id"])
        self.store.record_operation_intent(
            "operation-on-a",
            {
                "ok": True,
                "proposal_id": "proposal-on-a",
                "path": "sample.py",
                "old_sha256": None,
                "new_sha256": "a" * 64,
            },
            tool_call_id=call["id"],
            workspace_root=self.root,
        )
        answer_b = self.store.append(
            "assistant_message",
            {"role": "assistant", "content": "B 私有回答"},
            parent_id=shared["id"],
        )
        self.store.select_active_node(answer_b["id"])

        recovered = recover_agent(
            self.store,
            model_client=MockModelClient(),
            workspace_root=self.root,
            permission_profile="read_only",
        )

        contents = [message.get("content") for message in recovered["history"]]
        self.assertIn("B 私有回答", contents)
        self.assertNotIn(None, contents[1:])
        self.assertEqual(
            recovered["pending_operations"][0]["operation_id"], "operation-on-a"
        )

    def test_selected_branch_reports_unresolved_calls_without_retrying(self):
        user = self.store.append(
            "user_message", {"role": "user", "content": "继续分析"}
        )
        call = {
            "id": "unfinished-branch-call",
            "name": "read_file",
            "arguments": {"path": "README.md"},
        }
        assistant = self.store.append(
            "assistant_message",
            {"role": "assistant", "content": None, "tool_call": call},
            parent_id=user["id"],
        )
        self.store.select_active_node(assistant["id"])

        with patch.object(agent, "dispatch_tool") as dispatch:
            recovered = recover_agent(
                self.store,
                model_client=MockModelClient(),
                workspace_root=READ_ONLY_ROOT,
                permission_profile="read_only",
                read_only_retry_budget=5,
            )

        dispatch.assert_not_called()
        self.assertEqual(recovered["status"], "recovery_required")
        self.assertEqual(
            recovered["reason"], "branch_selected_with_pending_tool_calls"
        )
        self.assertEqual(
            [call["id"] for call in recovered["pending_tool_calls"]],
            ["unfinished-branch-call"],
        )


if __name__ == "__main__":
    unittest.main()
