"""L26：验证自定义 JSONL RPC 的协议边界。"""

import io
import json
import threading
import time
import unittest

from rpc import (
    RpcProtocolError,
    RpcServer,
    decode_request,
    encode_message,
    validate_request,
)
from rpc_client import RpcClient


class RpcCodecTests(unittest.TestCase):
    def test_encode_and_decode_preserve_unicode_and_embedded_newline(self):
        message = {
            "id": "req-中文",
            "type": "prompt",
            "payload": {"question": "第一行\n第二行\u2028结束"},
        }

        encoded = encode_message(message)

        self.assertTrue(encoded.endswith(b"\n"))
        self.assertEqual(encoded.count(b"\n"), 1)
        self.assertEqual(decode_request(encoded), message)

    def test_decode_rejects_missing_or_extra_line_ending(self):
        with self.assertRaises(RpcProtocolError) as missing:
            decode_request(b'{"id":"req-1"}')
        self.assertEqual(missing.exception.code, "MISSING_NEWLINE")

        with self.assertRaises(RpcProtocolError) as extra:
            decode_request(b'{"id":"req-1"}\n\n')
        self.assertEqual(extra.exception.code, "MULTIPLE_LINES")

    def test_decode_rejects_invalid_utf8_and_malformed_json(self):
        with self.assertRaises(RpcProtocolError) as encoding:
            decode_request(b"\xff\n")
        self.assertEqual(encoding.exception.code, "INVALID_UTF8")

        with self.assertRaises(RpcProtocolError) as malformed:
            decode_request(b'{"id":"req-1"\n')
        self.assertEqual(malformed.exception.code, "INVALID_JSON")


class RpcValidationTests(unittest.TestCase):
    def test_validate_accepts_the_three_initial_commands(self):
        prompt = validate_request(
            {
                "id": "req-1",
                "type": "prompt",
                "payload": {"question": "读取 README"},
            },
            set(),
            False,
        )
        state = validate_request(
            {"id": "req-2", "type": "get_state", "payload": {}},
            {"req-1"},
            False,
        )
        abort = validate_request(
            {
                "id": "req-3",
                "type": "abort",
                "payload": {"target_id": "req-1"},
            },
            {"req-1", "req-2"},
            True,
        )

        self.assertEqual(prompt["payload"]["question"], "读取 README")
        self.assertEqual(state["type"], "get_state")
        self.assertEqual(abort["payload"]["target_id"], "req-1")

    def test_validate_rejects_missing_duplicate_and_unknown_requests(self):
        cases = [
            ({"type": "get_state", "payload": {}}, set(), False, "INVALID_REQUEST"),
            (
                {"id": "req-1", "type": "get_state", "payload": {}},
                {"req-1"},
                False,
                "DUPLICATE_ID",
            ),
            (
                {"id": "req-2", "type": "unknown", "payload": {}},
                set(),
                False,
                "UNKNOWN_COMMAND",
            ),
        ]

        for request, known_ids, busy, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(RpcProtocolError) as caught:
                    validate_request(request, known_ids, busy)
                self.assertEqual(caught.exception.code, code)

    def test_validate_rejects_empty_question_and_busy_prompt(self):
        with self.assertRaises(RpcProtocolError) as empty:
            validate_request(
                {
                    "id": "req-1",
                    "type": "prompt",
                    "payload": {"question": "   "},
                },
                set(),
                False,
            )
        self.assertEqual(empty.exception.code, "INVALID_ARGUMENT")

        with self.assertRaises(RpcProtocolError) as busy:
            validate_request(
                {
                    "id": "req-2",
                    "type": "prompt",
                    "payload": {"question": "继续"},
                },
                {"req-1"},
                True,
            )
        self.assertEqual(busy.exception.code, "BUSY")


class RpcServerTests(unittest.TestCase):
    def test_prompt_is_accepted_before_result_and_events_keep_request_id(self):
        started = threading.Event()
        release = threading.Event()

        def fake_run(question, event_recorder, cancel_event):
            self.assertEqual(question, "读取 README")
            self.assertFalse(cancel_event.is_set())
            started.set()
            event_recorder.emit("run_started", run_id="run-rpc")
            release.wait(timeout=1)
            return {"status": "completed", "run_id": "run-rpc", "answer": "完成"}

        server = RpcServer(run_fn=fake_run)
        accepted = server.handle_request(
            {
                "id": "req-1",
                "type": "prompt",
                "payload": {"question": "读取 README"},
            }
        )
        self.assertEqual(accepted[0]["type"], "accepted")
        self.assertTrue(started.wait(timeout=1))

        state = server.handle_request(
            {"id": "req-state", "type": "get_state", "payload": {}}
        )[0]
        self.assertEqual(state["payload"]["status"], "running")

        busy = server.handle_request(
            {
                "id": "req-2",
                "type": "prompt",
                "payload": {"question": "第二个问题"},
            }
        )[0]
        self.assertEqual(busy["type"], "error")
        self.assertEqual(busy["payload"]["error"], "BUSY")

        release.set()
        messages = self._wait_for_result(server, "req-1")
        self.assertTrue(
            any(
                message["request_id"] == "req-1"
                and message["type"] == "event"
                for message in messages
            )
        )
        self.assertTrue(
            any(
                message["request_id"] == "req-1"
                and message["type"] == "result"
                for message in messages
            )
        )

    def test_abort_sets_cancel_event_and_returns_cancelled_result(self):
        started = threading.Event()

        def fake_run(_question, event_recorder, cancel_event):
            started.set()
            while not cancel_event.is_set():
                time.sleep(0.005)
            event_recorder.emit("run_finished", run_id="run-cancel", status="cancelled")
            return {
                "status": "cancelled",
                "reason": "user_cancelled",
                "run_id": "run-cancel",
            }

        server = RpcServer(run_fn=fake_run)
        server.handle_request(
            {
                "id": "req-cancel",
                "type": "prompt",
                "payload": {"question": "长任务"},
            }
        )
        self.assertTrue(started.wait(timeout=1))

        abort = server.handle_request(
            {
                "id": "req-abort",
                "type": "abort",
                "payload": {"target_id": "req-cancel"},
            }
        )[0]
        self.assertEqual(abort["type"], "accepted")
        messages = self._wait_for_result(server, "req-cancel")
        results = [message for message in messages if message["type"] == "result"]
        self.assertEqual(results[-1]["payload"]["status"], "cancelled")

    def test_serve_writes_only_jsonl_and_eof_drains_result(self):
        def fake_run(question, **_kwargs):
            return {"status": "completed", "answer": question}

        server = RpcServer(run_fn=fake_run)
        input_stream = io.BytesIO(
            encode_message(
                {
                    "id": "req-pipe",
                    "type": "prompt",
                    "payload": {"question": "中文\n问题"},
                }
            )
        )
        output_stream = io.BytesIO()

        server.serve(input_stream, output_stream)

        records = [
            json.loads(line)
            for line in output_stream.getvalue().split(b"\n")
            if line
        ]
        self.assertEqual(records[0]["type"], "accepted")
        self.assertTrue(any(record["type"] == "result" for record in records))
        self.assertTrue(all(record["request_id"] == "req-pipe" for record in records))

    def test_rpc_client_delegates_protocol_requests_without_running_agent_itself(self):
        server = RpcServer(run_fn=lambda *_args, **_kwargs: {"status": "completed"})
        client = RpcClient(server)

        responses = client.request(
            {"id": "req-client", "type": "get_state", "payload": {}}
        )

        self.assertEqual(responses[0]["request_id"], "req-client")
        self.assertEqual(responses[0]["payload"]["status"], "idle")

    @staticmethod
    def _wait_for_result(server, request_id):
        observed = []
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            messages = server.poll()
            observed.extend(messages)
            if any(
                message["request_id"] == request_id
                and message["type"] == "result"
                for message in observed
            ):
                return observed
            time.sleep(0.005)
        return observed


if __name__ == "__main__":
    unittest.main()
