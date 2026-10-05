"""L25：验证终端 UI 状态和最小 REPL，不依赖真实终端或模型。"""

import unittest

from ui import TerminalUIState, parse_input_line, run_repl


class TerminalUIStateTests(unittest.TestCase):
    def test_state_tracks_tool_progress_and_final_answer(self):
        state = TerminalUIState()

        state.apply_event({"event": "run_started", "run_id": "run-001"})
        state.apply_event(
            {
                "event": "tool_started",
                "tool_name": "read_file",
                "tool_call_id": "call-001",
            }
        )
        state.apply_event(
            {
                "event": "model_text_delta",
                "text": "正在读取\nREADME",
            }
        )
        state.apply_event(
            {
                "event": "tool_finished",
                "tool_name": "read_file",
                "status": "completed",
            }
        )
        state.apply_event(
            {
                "event": "run_finished",
                "status": "completed",
            }
        )
        state.set_answer("README 已读取")

        rendered = state.render()

        self.assertEqual(state.status, "completed")
        self.assertEqual(state.current_tool, None)
        self.assertEqual(state.tool_count, 1)
        self.assertEqual(state.text, "正在读取\nREADME")
        self.assertIn("状态：completed", rendered)
        self.assertIn("工具：read_file", rendered)
        self.assertIn("README 已读取", rendered)

    def test_blocked_tool_and_error_are_visible(self):
        state = TerminalUIState()
        state.apply_event(
            {
                "event": "tool_blocked",
                "tool_name": "edit_file",
                "error": "PROTECTED_TEST_FILE",
                "message": "验收测试文件受保护",
            }
        )
        state.apply_event(
            {
                "event": "run_finished",
                "status": "failed",
                "error": "EXTENSION_ERROR",
            }
        )

        rendered = state.render()

        self.assertEqual(state.blocked_tool_count, 1)
        self.assertIn("被拦截工具数：1", rendered)
        self.assertIn("PROTECTED_TEST_FILE", rendered)
        self.assertIn("EXTENSION_ERROR", rendered)


class TerminalCommandTests(unittest.TestCase):
    def test_parse_commands_and_normal_prompt(self):
        self.assertEqual(parse_input_line("/new"), ("new", ""))
        self.assertEqual(parse_input_line("  /compact  "), ("compact", ""))
        self.assertEqual(parse_input_line("/resume"), ("resume", ""))
        self.assertEqual(parse_input_line("/exit"), ("exit", ""))
        self.assertEqual(parse_input_line("  读取 README  "), ("prompt", "读取 README"))
        self.assertEqual(parse_input_line("/unknown"), ("unknown", ""))

    def test_repl_passes_compaction_and_stops_on_exit(self):
        inputs = iter(["/compact", "读取 README", "/exit"])
        outputs = []
        calls = []

        def fake_run(question, **kwargs):
            calls.append((question, kwargs))
            recorder = kwargs["event_recorder"]
            recorder.emit("run_started", run_id="run-ui")
            recorder.emit("run_finished", run_id="run-ui", status="completed")
            return {
                "status": "completed",
                "answer": "读取完成",
                "events": recorder.snapshot(),
                "pending_instructions": [],
            }

        result = run_repl(
            input_fn=lambda _prompt: next(inputs),
            output_fn=outputs.append,
            run_fn=fake_run,
        )

        self.assertEqual(result, "exited")
        self.assertEqual(calls[0][0], "读取 README")
        self.assertTrue(calls[0][1]["use_compaction"])
        self.assertIn("读取完成", "\n".join(outputs))

    def test_resume_without_session_store_explains_boundary(self):
        inputs = iter(["/resume", "/exit"])
        outputs = []

        result = run_repl(
            input_fn=lambda _prompt: next(inputs),
            output_fn=outputs.append,
            run_fn=lambda *_args, **_kwargs: self.fail("不应调用模型"),
        )

        self.assertEqual(result, "exited")
        self.assertIn("没有配置会话存储", "\n".join(outputs))


if __name__ == "__main__":
    unittest.main()
