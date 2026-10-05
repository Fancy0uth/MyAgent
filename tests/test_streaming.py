"""L21：验证流式文本、工具参数累积和取消边界。"""

import threading
import unittest
from types import SimpleNamespace

from agent import run_agent
from events import EventRecorder
from model_client import DeepSeekModelClient, MockModelClient
from streaming import (
    StreamAssembler,
    StreamCancelled,
    StreamProtocolError,
    consume_stream,
)


class StreamingTests(unittest.TestCase):
    def test_text_deltas_are_emitted_and_assembled_after_normal_finish(self):
        received = []
        result = consume_stream(
            [
                {"type": "start"},
                {"type": "text_delta", "text": "你好"},
                {"type": "text_delta", "text": "，世界"},
                {"type": "finish", "reason": "stop"},
            ],
            on_text_delta=received.append,
        )

        self.assertEqual(received, ["你好", "，世界"])
        self.assertEqual(result, {"type": "final", "content": "你好，世界"})

    def test_interleaved_tool_calls_keep_arguments_by_call_id(self):
        result = consume_stream(
            [
                {"type": "start"},
                {
                    "type": "tool_call_delta",
                    "call_id": "call-read",
                    "name": "read_file",
                    "arguments_delta": '{"path":"',
                },
                {
                    "type": "tool_call_delta",
                    "call_id": "call-search",
                    "name": "search_text",
                    "arguments_delta": '{"query":"',
                },
                {
                    "type": "tool_call_delta",
                    "call_id": "call-read",
                    "arguments_delta": "README.md" + '"}',
                },
                {
                    "type": "tool_call_delta",
                    "call_id": "call-search",
                    "arguments_delta": "SQLite" + '"}',
                },
                {"type": "finish", "reason": "tool_calls"},
            ]
        )

        self.assertEqual(result["type"], "tool_calls")
        self.assertEqual(
            result["calls"],
            [
                {
                    "id": "call-read",
                    "name": "read_file",
                    "arguments": {"path": "README.md"},
                },
                {
                    "id": "call-search",
                    "name": "search_text",
                    "arguments": {"query": "SQLite"},
                },
            ],
        )

    def test_incomplete_tool_json_is_rejected_before_any_response(self):
        with self.assertRaises(StreamProtocolError) as caught:
            consume_stream(
                [
                    {"type": "start"},
                    {
                        "type": "tool_call_delta",
                        "call_id": "call-partial",
                        "name": "read_file",
                        "arguments_delta": '{"path":"README.md"',
                    },
                    {"type": "finish", "reason": "tool_calls"},
                ]
            )

        self.assertEqual(caught.exception.code, "INVALID_TOOL_ARGUMENTS")

    def test_length_finish_is_rejected_even_when_json_happens_to_parse(self):
        with self.assertRaises(StreamProtocolError) as caught:
            consume_stream(
                [
                    {"type": "start"},
                    {
                        "type": "tool_call_delta",
                        "call_id": "call-truncated",
                        "name": "read_file",
                        "arguments_delta": '{"path":"README.md"}',
                    },
                    {"type": "finish", "reason": "length"},
                ]
            )

        self.assertEqual(caught.exception.code, "OUTPUT_TRUNCATED")

    def test_duplicate_finish_is_rejected(self):
        assembler = StreamAssembler()
        assembler.feed({"type": "start"})
        assembler.feed({"type": "text_delta", "text": "完成"})
        assembler.feed({"type": "finish", "reason": "stop"})

        with self.assertRaises(StreamProtocolError) as caught:
            assembler.feed({"type": "finish", "reason": "stop"})

        self.assertEqual(caught.exception.code, "DUPLICATE_FINISH")

    def test_cancellation_stops_before_finish(self):
        cancel_event = threading.Event()

        def events():
            yield {"type": "start"}
            yield {"type": "text_delta", "text": "还没结束"}
            cancel_event.set()
            yield {"type": "finish", "reason": "stop"}

        with self.assertRaises(StreamCancelled):
            consume_stream(events(), is_cancelled=cancel_event.is_set)

    def test_agent_does_not_execute_tool_after_stream_cancellation(self):
        cancel_event = threading.Event()

        class CancelledStreamingClient:
            def stream(self, messages):
                yield {"type": "start"}
                yield {
                    "type": "tool_call_delta",
                    "call_id": "call-cancelled",
                    "name": "list_files",
                    "arguments_delta": "{}",
                }
                cancel_event.set()
                yield {"type": "finish", "reason": "tool_calls"}

        result = run_agent(
            "查看资料",
            model_client=CancelledStreamingClient(),
            cancel_event=cancel_event,
            use_streaming=True,
        )

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["model_requests"], 1)
        self.assertEqual(result["tool_executions"], 0)

    def test_mock_streaming_agent_completes_and_reports_text_deltas(self):
        received = []
        recorder = EventRecorder()

        result = run_agent(
            "任务数据记录在哪里？",
            model_client=MockModelClient(),
            event_recorder=recorder,
            use_streaming=True,
            on_text_delta=received.append,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["tool_executions"], 2)
        self.assertTrue(received)
        self.assertTrue(
            any(event["event"] == "model_text_delta" for event in result["events"])
        )

    def test_deepseek_stream_adapter_normalizes_fake_sdk_chunks(self):
        class FakeCompletions:
            def __init__(self):
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                return iter(
                    [
                        SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    delta=SimpleNamespace(content="你好", tool_calls=None),
                                    finish_reason=None,
                                )
                            ]
                        ),
                        SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    delta=SimpleNamespace(content="！", tool_calls=None),
                                    finish_reason="stop",
                                )
                            ]
                        ),
                    ]
                )

        completions = FakeCompletions()
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        client = DeepSeekModelClient(api_client=fake_client)

        result = consume_stream(
            client.stream([{"role": "user", "content": "问候"}])
        )

        self.assertEqual(result, {"type": "final", "content": "你好！"})
        self.assertTrue(completions.kwargs["stream"])


if __name__ == "__main__":
    unittest.main()
