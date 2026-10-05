"""L26：学习版 JSONL RPC 的协议校验与异步服务端。"""

from __future__ import annotations

import copy
import json
import queue
import threading
from typing import Any, BinaryIO, Callable, Optional

from agent import run_agent
from events import EVENT_NAMES, EventRecorder


COMMANDS = frozenset({"prompt", "get_state", "abort"})


class RpcProtocolError(ValueError):
    """表示一条 RPC 记录不符合学习版协议。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def decode_request(line: bytes) -> dict:
    """严格解码一条以 LF 结尾的 JSONL 请求。"""
    if not isinstance(line, bytes):
        raise RpcProtocolError("INVALID_INPUT", "RPC 输入必须是 bytes")
    if not line.endswith(b"\n"):
        raise RpcProtocolError("MISSING_NEWLINE", "每条 RPC 记录必须以 LF 结尾")
    if line.count(b"\n") != 1:
        raise RpcProtocolError("MULTIPLE_LINES", "一次只能解码一条 RPC 记录")

    try:
        text = line[:-1].decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise RpcProtocolError("INVALID_UTF8", "RPC 记录不是有效的 UTF-8") from exc

    try:
        request = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RpcProtocolError("INVALID_JSON", f"RPC 记录不是合法 JSON：{exc.msg}") from exc

    if not isinstance(request, dict):
        raise RpcProtocolError("INVALID_REQUEST", "RPC 顶层必须是对象")
    return request


def encode_message(message: dict) -> bytes:
    """把一个响应对象编码成一条不带调试文本的 JSONL。"""
    if not isinstance(message, dict):
        raise RpcProtocolError("INVALID_MESSAGE", "RPC 响应必须是对象")
    try:
        text = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise RpcProtocolError("INVALID_MESSAGE", "RPC 响应无法序列化") from exc
    return (text + "\n").encode("utf-8")


def validate_request(
    request: dict,
    known_ids: set[str],
    busy: bool,
) -> dict:
    """校验命令、ID、payload 和当前忙碌状态，并返回副本。"""
    if not isinstance(request, dict):
        raise RpcProtocolError("INVALID_REQUEST", "RPC 请求必须是对象")

    request_id = request.get("id")
    command = request.get("type")
    payload = request.get("payload", {})
    if not isinstance(request_id, str) or not request_id.strip():
        raise RpcProtocolError("INVALID_REQUEST", "id 必须是非空字符串")
    if request_id in known_ids:
        raise RpcProtocolError("DUPLICATE_ID", f"请求 ID 已经使用：{request_id}")
    if not isinstance(command, str) or command not in COMMANDS:
        raise RpcProtocolError("UNKNOWN_COMMAND", f"未知 RPC 命令：{command!r}")
    if not isinstance(payload, dict):
        raise RpcProtocolError("INVALID_ARGUMENT", "payload 必须是对象")
    if command == "prompt":
        question = payload.get("question")
        if not isinstance(question, str) or not question.strip():
            raise RpcProtocolError("INVALID_ARGUMENT", "prompt.question 必须是非空字符串")
        if busy:
            raise RpcProtocolError("BUSY", "当前已有 prompt 正在运行")
    if command == "abort":
        target_id = payload.get("target_id")
        if target_id is not None and (
            not isinstance(target_id, str) or not target_id.strip()
        ):
            raise RpcProtocolError(
                "INVALID_ARGUMENT",
                "abort.target_id 必须是非空字符串",
            )
    return copy.deepcopy(request)


class RpcServer:
    """把 JSONL 请求连接到现有 Agent 循环的最小服务端。"""

    def __init__(
        self,
        run_fn: Callable[..., dict] = run_agent,
        run_kwargs: Optional[dict[str, Any]] = None,
    ) -> None:
        self.run_fn = run_fn
        self.run_kwargs = {} if run_kwargs is None else dict(run_kwargs)
        self._known_ids: set[str] = set()
        self._messages: queue.Queue[dict] = queue.Queue()
        self._lock = threading.Lock()
        self._active_request_id: Optional[str] = None
        self._cancel_event: Optional[threading.Event] = None
        self._worker: Optional[threading.Thread] = None
        self._state: dict[str, Any] = {
            "status": "idle",
            "active_request_id": None,
            "run_id": None,
        }

    def _is_busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def _push(self, request_id: str, message_type: str, payload: dict) -> None:
        self._messages.put(
            {
                "request_id": request_id,
                "type": message_type,
                "payload": copy.deepcopy(payload),
            }
        )

    def _start_prompt(self, request: dict) -> dict:
        request_id = request["id"]
        question = request["payload"]["question"].strip()
        cancel_event = threading.Event()
        recorder = EventRecorder()
        for event_name in EVENT_NAMES:
            recorder.subscribe(
                event_name,
                lambda event, request_id=request_id: self._push(
                    request_id,
                    "event",
                    event,
                ),
            )

        def worker() -> None:
            options = dict(self.run_kwargs)
            options["event_recorder"] = recorder
            options["cancel_event"] = cancel_event
            try:
                result = self.run_fn(question, **options)
                with self._lock:
                    self._state = {
                        "status": result.get("status", "unknown"),
                        "active_request_id": request_id,
                        "run_id": result.get("run_id"),
                    }
                self._push(request_id, "result", result)
            except Exception as exc:
                self._push(
                    request_id,
                    "result",
                    {
                        "status": "failed",
                        "error": "RPC_RUN_ERROR",
                        "message": str(exc),
                    },
                )
            finally:
                with self._lock:
                    self._active_request_id = None
                    self._cancel_event = None
                    self._worker = None
                    self._state = {
                        "status": "idle",
                        "active_request_id": None,
                        "run_id": None,
                    }

        with self._lock:
            self._active_request_id = request_id
            self._cancel_event = cancel_event
            self._state = {
                "status": "running",
                "active_request_id": request_id,
                "run_id": None,
            }
            self._worker = threading.Thread(
                target=worker,
                name=f"myagent-rpc-{request_id}",
                daemon=True,
            )
            worker_thread = self._worker
        worker_thread.start()
        return {
            "request_id": request_id,
            "type": "accepted",
            "payload": {"status": "accepted", "request_id": request_id},
        }

    def handle_request(self, request: dict) -> list[dict]:
        """处理一条已经解析的请求，异步 prompt 只立即返回 accepted。"""
        with self._lock:
            busy = self._is_busy()
            known_ids = set(self._known_ids)
        try:
            request = validate_request(request, known_ids, busy)
        except RpcProtocolError as exc:
            request_id = request.get("id") if isinstance(request, dict) else None
            return [
                {
                    "request_id": request_id,
                    "type": "error",
                    "payload": {"error": exc.code, "message": exc.message},
                }
            ]

        with self._lock:
            self._known_ids.add(request["id"])

        command = request["type"]
        request_id = request["id"]
        if command == "get_state":
            with self._lock:
                state = copy.deepcopy(self._state)
            return [
                {"request_id": request_id, "type": "result", "payload": state}
            ]
        if command == "abort":
            target_id = request["payload"].get("target_id")
            with self._lock:
                active_id = self._active_request_id
                cancel_event = self._cancel_event
            if active_id is None:
                return [
                    {
                        "request_id": request_id,
                        "type": "result",
                        "payload": {"status": "idle", "message": "当前没有运行"},
                    }
                ]
            if target_id is not None and target_id != active_id:
                return [
                    {
                        "request_id": request_id,
                        "type": "error",
                        "payload": {
                            "error": "TARGET_NOT_FOUND",
                            "message": "target_id 不是当前运行",
                        },
                    }
                ]
            if cancel_event is not None:
                cancel_event.set()
            return [
                {
                    "request_id": request_id,
                    "type": "accepted",
                    "payload": {
                        "status": "abort_requested",
                        "target_id": active_id,
                    },
                }
            ]
        return [self._start_prompt(request)]

    def poll(self) -> list[dict]:
        """取出已经产生的事件/结果，不阻塞。"""
        messages = []
        while True:
            try:
                messages.append(self._messages.get_nowait())
            except queue.Empty:
                return messages

    def serve(self, input_stream: BinaryIO, output_stream: BinaryIO) -> None:
        """在独立读取线程中接收输入，同时持续串行输出事件。"""
        incoming: queue.Queue[object] = queue.Queue()
        end_of_input = object()

        def reader() -> None:
            while True:
                raw_line = input_stream.readline()
                if raw_line == b"":
                    incoming.put(end_of_input)
                    return
                incoming.put(raw_line)

        threading.Thread(target=reader, name="myagent-rpc-reader", daemon=True).start()
        input_finished = False

        def write_pending() -> None:
            for response in self.poll():
                output_stream.write(encode_message(response))
                output_stream.flush()

        while not input_finished or self._is_busy() or not self._messages.empty():
            write_pending()
            try:
                item = incoming.get(timeout=0.02)
            except queue.Empty:
                continue
            if item is end_of_input:
                input_finished = True
                continue
            try:
                request = decode_request(item)  # type: ignore[arg-type]
                responses = self.handle_request(request)
            except RpcProtocolError as exc:
                responses = [
                    {
                        "request_id": None,
                        "type": "error",
                        "payload": {"error": exc.code, "message": exc.message},
                    }
                ]
            for response in responses:
                output_stream.write(encode_message(response))
                output_stream.flush()

        write_pending()


__all__ = [
    "RpcProtocolError",
    "RpcServer",
    "decode_request",
    "encode_message",
    "validate_request",
]
