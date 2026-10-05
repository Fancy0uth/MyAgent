import threading
import unittest

from agent import run_agent
from events import EventRecorder
from model_client import MockModelClient


class RepeatingToolClient:
    def respond(self, messages):
        return {
            "type": "tool_call",
            "id": f"repeat-{len(messages)}",
            "name": "list_files",
            "arguments": {},
        }


class CancelAfterModelResponseClient:
    def __init__(self, cancel_event):
        self.cancel_event = cancel_event

    def respond(self, messages):
        # 模拟模型返回工具调用后，外部请求取消。
        self.cancel_event.set()
        return {
            "type": "tool_call",
            "id": "cancel-call-1",
            "name": "list_files",
            "arguments": {},
        }


class AgentEventIntegrationTests(unittest.TestCase):
    def test_run_agent_records_model_and_tool_events(self):
        recorder = EventRecorder()

        result = run_agent(
            "任务数据保存在哪里？",
            model_client=MockModelClient(),
            event_recorder=recorder,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["events"], recorder.snapshot())
        self.assertTrue(result["run_id"])
        self.assertTrue(all(event["run_id"] == result["run_id"] for event in result["events"]))
        self.assertEqual(
            [event["event"] for event in result["events"]],
            [
                "run_started",
                "model_started",
                "model_finished",
                "tool_started",
                "tool_finished",
                "model_started",
                "model_finished",
                "tool_started",
                "tool_finished",
                "model_started",
                "model_finished",
                "run_finished",
            ],
        )

        measured_events = [
            event
            for event in result["events"]
            if event["event"] in {
                "model_finished",
                "tool_finished",
                "run_finished",
            }
        ]
        # duration_ms 是观测数据，应该存在且不能是负数。
        self.assertTrue(all(event["duration_ms"] >= 0 for event in measured_events))

    def test_cancel_before_model_request_stops_without_calling_model(self):
        cancel_event = threading.Event()
        cancel_event.set()
        recorder = EventRecorder()

        result = run_agent(
            "查看资料",
            model_client=MockModelClient(),
            cancel_event=cancel_event,
            event_recorder=recorder,
        )

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["reason"], "user_cancelled")
        self.assertEqual(result["model_requests"], 0)
        self.assertEqual(
            [event["event"] for event in recorder.snapshot()],
            ["run_started", "run_finished"],
        )

    def test_cancel_before_tool_execution_does_not_start_tool(self):
        cancel_event = threading.Event()
        recorder = EventRecorder()

        result = run_agent(
            "查看资料",
            model_client=CancelAfterModelResponseClient(cancel_event),
            cancel_event=cancel_event,
            event_recorder=recorder,
        )

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["reason"], "user_cancelled")
        self.assertEqual(result["model_requests"], 1)
        self.assertEqual(result["tool_executions"], 0)
        self.assertNotIn(
            "tool_started",
            [event["event"] for event in recorder.snapshot()],
        )

    def test_tool_events_keep_call_id_and_name(self):
        recorder = EventRecorder()

        run_agent(
            "查看资料",
            model_client=MockModelClient(),
            event_recorder=recorder,
        )

        tool_started = [
            event
            for event in recorder.snapshot()
            if event["event"] == "tool_started"
        ]
        tool_finished = [
            event
            for event in recorder.snapshot()
            if event["event"] == "tool_finished"
        ]

        # 开始和结束事件必须指向同一个工具调用，便于定位单次执行。
        self.assertEqual(
            [(event["tool_call_id"], event["tool_name"]) for event in tool_started],
            [
                ("mock-call-1", "list_files"),
                ("mock-call-2", "read_file"),
            ],
        )
        self.assertEqual(
            [(event["tool_call_id"], event["tool_name"]) for event in tool_finished],
            [
                ("mock-call-1", "list_files"),
                ("mock-call-2", "read_file"),
            ],
        )

    def test_run_finished_records_budget_stop(self):
        recorder = EventRecorder()

        result = run_agent(
            "重复调用工具",
            model_client=RepeatingToolClient(),
            max_model_requests=1,
            event_recorder=recorder,
        )

        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["reason"], "model_call_limit")
        self.assertEqual(recorder.snapshot()[-1]["event"], "run_finished")
        self.assertEqual(recorder.snapshot()[-1]["status"], "stopped")
        self.assertEqual(
            recorder.snapshot()[-1]["reason"],
            "model_call_limit",
        )


if __name__ == "__main__":
    unittest.main()
