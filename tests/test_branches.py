"""L18 会话树、分支与活动历史测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from agent import recover_agent, run_agent
from sessions import SessionLoadError, SessionStore
from tools import ROOT as READ_ONLY_ROOT


def make_entry(entry_id, parent_id, entry_type="user_message"):
    return {
        "schema_version": 1,
        "id": entry_id,
        "parent_id": parent_id,
        "timestamp": "2026-09-24T00:00:00+00:00",
        "type": entry_type,
        "payload": {"role": "user", "content": entry_id},
    }


def write_entries(path, entries):
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in entries),
        encoding="utf-8",
    )


class TemporarySessionTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.session_path = Path(self.temp_dir.name) / "session.jsonl"

    def tearDown(self):
        self.temp_dir.cleanup()


class SessionGraphTests(TemporarySessionTestCase):
    def test_append_can_make_two_children_of_one_node(self):
        store = SessionStore(self.session_path)
        root = store.append("user_message", {"role": "user", "content": "共同问题"})

        branch_a = store.append(
            "assistant_message",
            {"role": "assistant", "content": "A"},
            parent_id=root["id"],
        )
        branch_b = store.append(
            "assistant_message",
            {"role": "assistant", "content": "B"},
            parent_id=root["id"],
        )

        loaded = SessionStore(self.session_path).load()
        self.assertEqual(branch_a["parent_id"], root["id"])
        self.assertEqual(branch_b["parent_id"], root["id"])
        self.assertEqual(
            [entry["id"] for entry in loaded],
            [root["id"], branch_a["id"], branch_b["id"]],
        )

    def test_load_rejects_orphan_parent(self):
        write_entries(self.session_path, [make_entry("child", "missing")])
        with self.assertRaises(SessionLoadError) as caught:
            SessionStore(self.session_path).load()
        self.assertEqual(caught.exception.code, "ORPHAN_PARENT")

    def test_load_reports_cycle_before_parent_order_error(self):
        write_entries(
            self.session_path,
            [make_entry("first", "second"), make_entry("second", "first")],
        )
        with self.assertRaises(SessionLoadError) as caught:
            SessionStore(self.session_path).load()
        self.assertEqual(caught.exception.code, "CYCLE_DETECTED")


class SessionBranchTests(TemporarySessionTestCase):
    def test_selected_branch_survives_reopen_and_excludes_sibling(self):
        store = SessionStore(self.session_path)
        root = store.append("user_message", {"role": "user", "content": "共同问题"})
        answer_a = store.append(
            "assistant_message",
            {"role": "assistant", "content": "A 私有内容"},
            parent_id=root["id"],
        )
        store.append(
            "assistant_message",
            {"role": "assistant", "content": "B 私有内容"},
            parent_id=root["id"],
        )
        store.select_active_node(answer_a["id"])

        reopened = SessionStore(self.session_path)
        path_ids = [entry["id"] for entry in reopened.get_active_path()]
        contents = [message.get("content") for message in reopened.get_model_history()]

        self.assertIn(answer_a["id"], path_ids)
        self.assertNotIn("B 私有内容", contents)
        self.assertIn("A 私有内容", contents)

    def test_selection_rejects_unknown_id_and_non_chat_type(self):
        store = SessionStore(self.session_path)
        user = store.append("user_message", {"role": "user", "content": "问题"})
        metadata = store.append("run_context", {"run_id": "run-1"})

        for invalid_id in ("missing", metadata["id"]):
            with self.subTest(invalid_id=invalid_id):
                with self.assertRaises(SessionLoadError) as caught:
                    store.select_active_node(invalid_id)
                self.assertEqual(caught.exception.code, "INVALID_BRANCH_TARGET")

        self.assertEqual(store.get_model_history()[0]["content"], "问题")
        self.assertEqual(store.get_active_path()[-1]["id"], metadata["id"])
        self.assertEqual(user["type"], "user_message")

    def test_model_history_preserves_tool_call_and_matching_result(self):
        store = SessionStore(self.session_path)
        user = store.append("user_message", {"role": "user", "content": "读文件"})
        call = {
            "id": "call-1",
            "name": "read_file",
            "arguments": {"path": "README.md"},
        }
        assistant = store.append(
            "assistant_message",
            {"role": "assistant", "content": None, "tool_call": call},
            parent_id=user["id"],
        )
        result = {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": "文件内容",
        }
        store.append("tool_result", result, parent_id=assistant["id"])

        history = store.get_model_history()
        self.assertEqual(history[1]["tool_call"]["id"], "call-1")
        self.assertEqual(history[2]["tool_call_id"], "call-1")

    def test_render_tree_shows_both_chat_branches_without_run_metadata(self):
        store = SessionStore(self.session_path)
        root = store.append("user_message", {"role": "user", "content": "共同问题"})
        store.append("run_context", {"run_id": "hidden-run"}, parent_id=root["id"])
        store.append(
            "assistant_message",
            {"role": "assistant", "content": "A 答案"},
            parent_id=root["id"],
        )
        store.append(
            "assistant_message",
            {"role": "assistant", "content": "B 答案"},
            parent_id=root["id"],
        )

        rendered = store.render_tree()
        self.assertIn("共同问题", rendered)
        self.assertIn("A 答案", rendered)
        self.assertIn("B 答案", rendered)
        self.assertNotIn("hidden-run", rendered)
        self.assertLess(rendered.index("共同问题"), rendered.index("A 答案"))
        self.assertLess(rendered.index("共同问题"), rendered.index("B 答案"))
        a_line = next(line for line in rendered.splitlines() if "A 答案" in line)
        b_line = next(line for line in rendered.splitlines() if "B 答案" in line)
        self.assertTrue(a_line.startswith("  "))
        self.assertTrue(b_line.startswith("  "))


class CaptureFinalClient:
    def __init__(self):
        self.messages = None

    def respond(self, messages):
        self.messages = [dict(message) for message in messages]
        return {"type": "final", "content": "分支 A 已继续。"}


class AgentBranchTests(TemporarySessionTestCase):
    def test_run_agent_sends_only_selected_branch_to_model(self):
        store = SessionStore(self.session_path)
        root = store.append("user_message", {"role": "user", "content": "共同问题"})
        answer_a = store.append(
            "assistant_message",
            {"role": "assistant", "content": "A 私有内容"},
            parent_id=root["id"],
        )
        store.append(
            "assistant_message",
            {"role": "assistant", "content": "B 私有内容"},
            parent_id=root["id"],
        )
        store.select_active_node(answer_a["id"])
        client = CaptureFinalClient()

        result = run_agent("继续 A", model_client=client, session_store=store)

        contents = [message.get("content") for message in client.messages]
        self.assertEqual(result["status"], "completed")
        self.assertIn("A 私有内容", contents)
        self.assertNotIn("B 私有内容", contents)
        self.assertEqual(contents[-1], "继续 A")

    def test_run_agent_requires_recovery_for_selected_unfinished_tool_call(self):
        store = SessionStore(self.session_path)
        user = store.append("user_message", {"role": "user", "content": "读文件"})
        call = {
            "id": "unfinished-call",
            "name": "read_file",
            "arguments": {"path": "README.md"},
        }
        assistant = store.append(
            "assistant_message",
            {"role": "assistant", "content": None, "tool_call": call},
            parent_id=user["id"],
        )
        store.select_active_node(assistant["id"])
        before = store.load()
        client = CaptureFinalClient()

        result = run_agent("继续这个会话", model_client=client, session_store=store)

        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(result["reason"], "pending_tool_calls")
        self.assertEqual(
            [pending["id"] for pending in result["pending_tool_calls"]],
            ["unfinished-call"],
        )
        self.assertIsNone(client.messages)
        self.assertEqual(store.load(), before)

    def test_run_agent_requires_recovery_for_partial_multi_tool_results(self):
        store = SessionStore(self.session_path)
        user = store.append("user_message", {"role": "user", "content": "读两个文件"})
        calls = [
            {"id": "call-one", "name": "read_file", "arguments": {"path": "a"}},
            {"id": "call-two", "name": "read_file", "arguments": {"path": "b"}},
        ]
        assistant = store.append(
            "assistant_message",
            {"role": "assistant", "content": None, "tool_calls": calls},
            parent_id=user["id"],
        )
        first_result = store.append(
            "tool_result",
            {"role": "tool", "tool_call_id": "call-one", "content": "结果 A"},
            parent_id=assistant["id"],
        )
        store.select_active_node(first_result["id"])
        before = store.load()
        client = CaptureFinalClient()

        result = run_agent("继续第二个工具", model_client=client, session_store=store)

        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(result["reason"], "pending_tool_calls")
        self.assertEqual(
            [pending["id"] for pending in result["pending_tool_calls"]],
            ["call-two"],
        )
        self.assertIsNone(client.messages)
        self.assertEqual(store.load(), before)


class SimulatedProcessCrash(BaseException):
    """模拟模型请求过程中进程退出，不触发 run_agent 的常规异常处理。"""


class CrashAfterRecordingClient:
    def __init__(self):
        self.messages = None

    def respond(self, messages):
        self.messages = [dict(message) for message in messages]
        raise SimulatedProcessCrash()


class AgentRecoveryBranchTests(TemporarySessionTestCase):
    def test_recovery_restores_branch_context_used_by_interrupted_run(self):
        store = SessionStore(self.session_path)
        root = store.append("user_message", {"role": "user", "content": "共同问题"})
        answer_a = store.append(
            "assistant_message",
            {"role": "assistant", "content": "A 私有上下文"},
            parent_id=root["id"],
        )
        store.append(
            "assistant_message",
            {"role": "assistant", "content": "B 私有上下文"},
            parent_id=root["id"],
        )
        store.select_active_node(answer_a["id"])
        client = CrashAfterRecordingClient()

        with self.assertRaises(SimulatedProcessCrash):
            run_agent("继续 A", model_client=client, session_store=store)

        recovered = recover_agent(
            store,
            model_client=client,
            workspace_root=READ_ONLY_ROOT,
            permission_profile="read_only",
        )

        sent_contents = [message.get("content") for message in client.messages]
        recovered_contents = [
            message.get("content") for message in recovered["history"]
        ]
        self.assertEqual(sent_contents, ["共同问题", "A 私有上下文", "继续 A"])
        self.assertEqual(recovered["status"], "ready_to_resume")
        self.assertEqual(recovered_contents, sent_contents)
        self.assertNotIn("B 私有上下文", recovered_contents)


if __name__ == "__main__":
    unittest.main()
