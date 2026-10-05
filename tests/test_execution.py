"""受控测试执行器的离线测试。"""

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_tools.execution import TestCase, TestCaseRegistry, TestExecutor


class TestExecutionTests(unittest.TestCase):
    """验证 run_tests 只运行预登记命令，并返回真实执行结果。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = TestCaseRegistry()
        self.executor = TestExecutor(self.registry)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _register(self, code, **options):
        self.registry.register(
            TestCase(
                case_id=options.pop("case_id", "sample"),
                workspace=self.workspace,
                argv=(sys.executable, "-c", code),
                **options,
            )
        )

    def test_unknown_case_is_rejected_before_starting_process(self):
        result = self.executor.run_tests("missing")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "INVALID_CASE")

    def test_success_returns_exit_code_and_stdout(self):
        self._register("print('tests passed')")

        result = self.executor.run_tests("sample")

        self.assertTrue(result["ok"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("tests passed", result["stdout"])
        self.assertEqual(result["stderr"], "")
        self.assertFalse(result["timed_out"])
        self.assertFalse(result["truncated"])
        self.assertGreaterEqual(result["duration_ms"], 0)

    def test_failure_returns_nonzero_exit_code_and_stderr(self):
        self._register(
            "import sys; print('assertion failed', file=sys.stderr); sys.exit(3)"
        )

        result = self.executor.run_tests("sample")

        self.assertTrue(result["ok"])
        self.assertEqual(result["exit_code"], 3)
        self.assertIn("assertion failed", result["stderr"])
        self.assertFalse(result["timed_out"])

    def test_timeout_terminates_long_running_process(self):
        self._register(
            "import time; time.sleep(2)",
            timeout_seconds=0.1,
        )

        result = self.executor.run_tests("sample")

        self.assertTrue(result["ok"])
        self.assertTrue(result["timed_out"])
        self.assertIsNotNone(result["exit_code"])
        self.assertLess(result["duration_ms"], 1500)

    def test_long_output_is_truncated_without_failing_process(self):
        self._register(
            "print('x' * 1000)",
            max_output_bytes=128,
        )

        result = self.executor.run_tests("sample")

        self.assertTrue(result["ok"])
        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["stdout"].encode("utf-8")), 128)

    def test_child_does_not_receive_model_api_key(self):
        self._register(
            "import os; print(os.environ.get('DEEPSEEK_API_KEY', 'missing'))"
        )

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "secret"}):
            result = self.executor.run_tests("sample")

        self.assertTrue(result["ok"])
        self.assertEqual(result["stdout"].strip(), "missing")

    def test_model_cannot_replace_the_registered_command(self):
        self._register("print('fixed command')")

        # run_tests 只接受 case_id，不接受模型传入的 command 参数。
        with self.assertRaises(TypeError):
            self.executor.run_tests("sample", "rm -rf /")

    def test_cancel_before_start_does_not_launch_process(self):
        started = self.workspace / "started.txt"
        self._register(
            f"from pathlib import Path; Path({str(started)!r}).write_text('yes')"
        )
        cancel_event = threading.Event()
        cancel_event.set()

        result = self.executor.run_tests("sample", cancel_event=cancel_event)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "CANCELLED")
        self.assertTrue(result["cancelled"])
        self.assertFalse(started.exists())

    def test_cancel_running_process_group_stops_child_process(self):
        started = self.workspace / "started.txt"
        child_finished = self.workspace / "child-finished.txt"
        child_code = (
            "from pathlib import Path; import time; "
            f"time.sleep(1); Path({str(child_finished)!r}).write_text('done')"
        )
        parent_code = (
            "import subprocess, sys, time; "
            f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
            f"open({str(started)!r}, 'w').write(str(child.pid)); "
            "time.sleep(10)"
        )
        self._register(parent_code)
        cancel_event = threading.Event()
        result_holder = {}

        def run_test():
            result_holder["result"] = self.executor.run_tests(
                "sample",
                cancel_event=cancel_event,
            )

        worker = threading.Thread(target=run_test)
        worker.start()
        for _ in range(100):
            if started.exists():
                break
            time.sleep(0.01)
        cancel_event.set()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        result = result_holder["result"]
        self.assertTrue(result["ok"])
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["timed_out"])
        self.assertIsNotNone(result["exit_code"])
        time.sleep(0.2)
        self.assertFalse(child_finished.exists())


if __name__ == "__main__":
    unittest.main()
