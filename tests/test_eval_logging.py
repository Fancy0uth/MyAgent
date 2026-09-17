import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


evaluation_module = importlib.import_module("evals.run_evals")


class EvaluationLoggingTests(unittest.TestCase):
    def test_append_run_record_writes_one_json_object_per_line(self):
        append_run_record = getattr(
            evaluation_module,
            "append_run_record",
            None,
        )
        self.assertIsNotNone(append_run_record)
        if append_run_record is None:
            return

        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "nested" / "eval_runs.jsonl"
            append_run_record(
                {"run_id": "run-001", "status": "completed"},
                log_path=log_path,
            )
            append_run_record(
                {"run_id": "run-002", "status": "failed"},
                log_path=log_path,
            )

            records = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(
            records,
            [
                {"run_id": "run-001", "status": "completed"},
                {"run_id": "run-002", "status": "failed"},
            ],
        )

    def test_evaluation_main_writes_task_summary_record(self):
        self.assertTrue(
            hasattr(evaluation_module, "RUN_LOG"),
            "run_evals.py 应定义运行记录文件路径",
        )
        if not hasattr(evaluation_module, "RUN_LOG"):
            return

        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            tasks_path = directory_path / "tasks.jsonl"
            log_path = directory_path / "runs" / "eval_runs.jsonl"
            tasks_path.write_text(
                json.dumps(
                    {
                        "id": "task-001",
                        "question": "任务数据保存在哪里？",
                        "expected_contains": ["SQLite"],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            fake_result = {
                "status": "completed",
                "answer": "数据保存在 SQLite，依据是 README.md:3。",
                "model_requests": 2,
                "tool_executions": 1,
            }

            with patch.object(evaluation_module, "TASKS_FILE", tasks_path), \
                    patch.object(evaluation_module, "RUN_LOG", log_path), \
                    patch.object(evaluation_module, "run_agent", return_value=fake_result), \
                    patch.object(sys, "argv", ["run_evals.py"]):
                evaluation_module.main()

            record = json.loads(log_path.read_text(encoding="utf-8").strip())

        self.assertEqual(record["task_id"], "task-001")
        self.assertEqual(record["mode"], "mock")
        self.assertEqual(record["model"], "mock")
        self.assertEqual(record["status"], "completed")
        self.assertTrue(record["passed"])
        self.assertEqual(record["model_requests"], 2)
        self.assertEqual(record["tool_executions"], 1)
        self.assertIn("duration_ms", record)


if __name__ == "__main__":
    unittest.main()
