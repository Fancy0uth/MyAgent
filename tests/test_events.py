import unittest

from events import EventRecorder


class EventRecorderTests(unittest.TestCase):
    def test_records_events_in_the_order_they_are_emitted(self):
        recorder = EventRecorder()

        recorder.emit("run_started", run_id="run-001")
        recorder.emit(
            "tool_started",
            run_id="run-001",
            tool_call_id="call-001",
            tool_name="read_file",
        )
        recorder.emit("run_finished", run_id="run-001", status="completed")

        # 事件顺序本身就是调试 Agent 执行过程的重要信息。
        self.assertEqual(
            [event["event"] for event in recorder.snapshot()],
            ["run_started", "tool_started", "run_finished"],
        )

    def test_tool_event_contains_tool_identity(self):
        recorder = EventRecorder()

        recorder.emit(
            "tool_finished",
            run_id="run-001",
            tool_call_id="call-001",
            tool_name="read_file",
            status="completed",
        )

        self.assertEqual(
            recorder.snapshot()[0],
            {
                "event": "tool_finished",
                "run_id": "run-001",
                "tool_call_id": "call-001",
                "tool_name": "read_file",
                "status": "completed",
            },
        )

    def test_rejects_unknown_event_name(self):
        recorder = EventRecorder()

        with self.assertRaises(ValueError):
            recorder.emit("unknown_event", run_id="run-001")

    def test_rejects_missing_run_id(self):
        recorder = EventRecorder()

        with self.assertRaises(ValueError):
            recorder.emit("run_started", run_id="")

    def test_rejects_tool_event_without_tool_identity(self):
        recorder = EventRecorder()

        with self.assertRaises(ValueError):
            recorder.emit("tool_started", run_id="run-001")


if __name__ == "__main__":
    unittest.main()
