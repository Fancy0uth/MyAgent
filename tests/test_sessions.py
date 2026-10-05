"""L16：追加式会话记录的验收测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from agent import run_agent
from model_client import MockModelClient
from sessions import SessionLoadError, SessionStore


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.session_path = Path(self.temp_dir.name) / "session.jsonl"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_append_and_load_preserve_unicode_and_parent_chain(self):
        store = SessionStore(self.session_path)

        first = store.append("user_message", {"content": "你好，SQLite 在哪里？"})
        second = store.append(
            "assistant_message",
            {"content": "我会先读取项目资料。"},
        )

        loaded = SessionStore(self.session_path).load()

        self.assertEqual(loaded, [first, second])
        self.assertIsNone(first["parent_id"])
        self.assertEqual(second["parent_id"], first["id"])
        self.assertEqual(self.session_path.read_text(encoding="utf-8").count("\n"), 2)

    def test_duplicate_id_is_rejected(self):
        store = SessionStore(self.session_path)
        store.append("user_message", {"content": "第一次"}, entry_id="fixed-id")

        with self.assertRaises(SessionLoadError) as context:
            store.append(
                "assistant_message",
                {"content": "重复 ID"},
                entry_id="fixed-id",
            )

        self.assertEqual(context.exception.code, "DUPLICATE_ID")

    def test_invalid_json_is_rejected(self):
        self.session_path.write_text("不是 JSON\n", encoding="utf-8")

        with self.assertRaises(SessionLoadError) as context:
            SessionStore(self.session_path).load()

        self.assertEqual(context.exception.code, "INVALID_JSON")

    def test_partial_final_line_is_reported(self):
        self.session_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "id": "entry-1",
                    "parent_id": None,
                    "timestamp": "2026-09-21T00:00:00+00:00",
                    "type": "user_message",
                    "payload": {"content": "未写完"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with self.assertRaises(SessionLoadError) as context:
            SessionStore(self.session_path).load()

        self.assertEqual(context.exception.code, "PARTIAL_TAIL")

    def test_wrong_schema_version_and_broken_parent_are_rejected(self):
        store = SessionStore(self.session_path)
        first = store.append("user_message", {"content": "第一条"})
        store.append("assistant_message", {"content": "第二条"})

        records = [
            json.loads(line)
            for line in self.session_path.read_text(encoding="utf-8").splitlines()
        ]
        records[0]["schema_version"] = 99
        self.session_path.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )

        with self.assertRaises(SessionLoadError) as version_context:
            SessionStore(self.session_path).load()
        self.assertEqual(version_context.exception.code, "UNSUPPORTED_SCHEMA")

        records[0]["schema_version"] = 1
        records[1]["parent_id"] = "missing-parent"
        self.session_path.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )

        with self.assertRaises(SessionLoadError) as parent_context:
            SessionStore(self.session_path).load()
        self.assertEqual(parent_context.exception.code, "ORPHAN_PARENT")
        self.assertEqual(records[0]["id"], first["id"])

    def test_run_agent_persists_messages_tool_results_and_final_status(self):
        store = SessionStore(self.session_path)

        result = run_agent(
            "任务数据保存在哪里？",
            model_client=MockModelClient(),
            session_store=store,
        )

        self.assertEqual(result["status"], "completed")
        entries = SessionStore(self.session_path).load()
        entry_types = [entry["type"] for entry in entries]

        self.assertEqual(entry_types[0], "run_context")
        self.assertIn("user_message", entry_types)
        self.assertIn("assistant_message", entry_types)
        self.assertIn("tool_call", entry_types)
        self.assertIn("tool_result", entry_types)
        self.assertEqual(entry_types[-1], "run_status")
        self.assertEqual(entries[-1]["payload"]["status"], "completed")


class SessionIntegrationDefaultsTests(unittest.TestCase):
    def test_run_agent_does_not_create_session_file_without_store(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "session.jsonl"

            result = run_agent("读取任务数据位置", model_client=MockModelClient())

            self.assertEqual(result["status"], "completed")
            self.assertFalse(session_path.exists())


if __name__ == "__main__":
    unittest.main()
