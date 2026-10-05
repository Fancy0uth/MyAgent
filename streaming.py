"""L21：把模型流式事件安全地组装成 Agent 能处理的完整响应。"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Optional


class StreamProtocolError(ValueError):
    """流式事件不符合协议时抛出的稳定错误。"""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


class StreamCancelled(Exception):
    """流式响应在结束前被取消。"""


class StreamAssembler:
    """按事件顺序累积文本和多个工具调用。"""

    def __init__(self) -> None:
        self._started = False
        self._finished = False
        self._text_parts: list[str] = []
        self._tool_calls: dict[str, dict[str, Any]] = {}
        self._tool_order: list[str] = []
        self._usage: Optional[dict] = None

    def feed(self, event: dict) -> Optional[dict]:
        """消费一个事件；只有 finish 事件才会返回完整模型响应。"""
        if not isinstance(event, dict):
            raise StreamProtocolError("INVALID_EVENT", "流事件必须是对象")

        event_type = event.get("type")
        if event_type == "start":
            if self._started:
                raise StreamProtocolError("DUPLICATE_START", "流不能重复开始")
            self._started = True
            return {"type": "started"}

        if not self._started:
            raise StreamProtocolError("MISSING_START", "流必须先发送 start 事件")
        if self._finished:
            raise StreamProtocolError(
                "DUPLICATE_FINISH",
                "流已经结束，不能继续发送事件",
            )

        if event_type == "text_delta":
            text = event.get("text")
            if not isinstance(text, str):
                raise StreamProtocolError(
                    "INVALID_TEXT_DELTA",
                    "文本片段必须是字符串",
                )
            self._text_parts.append(text)
            return {"type": "text_delta", "text": text}

        if event_type == "tool_call_delta":
            return self._feed_tool_call_delta(event)

        if event_type == "usage":
            usage = event.get("usage")
            if not isinstance(usage, dict):
                raise StreamProtocolError("INVALID_USAGE", "usage 必须是对象")
            self._usage = dict(usage)
            return {"type": "usage", "usage": dict(usage)}

        if event_type == "finish":
            return self._finish(event.get("reason"))

        raise StreamProtocolError(
            "UNKNOWN_EVENT",
            f"不支持的流事件类型：{event_type!r}",
        )

    def _feed_tool_call_delta(self, event: dict) -> dict:
        call_id = event.get("call_id")
        if not isinstance(call_id, str) or not call_id.strip():
            raise StreamProtocolError(
                "INVALID_TOOL_CALL",
                "工具片段必须包含非空 call_id",
            )

        name = event.get("name")
        if name is not None and (
            not isinstance(name, str) or not name.strip()
        ):
            raise StreamProtocolError(
                "INVALID_TOOL_CALL",
                "工具名称必须是非空字符串",
            )

        arguments_delta = event.get("arguments_delta", "")
        if not isinstance(arguments_delta, str):
            raise StreamProtocolError(
                "INVALID_TOOL_ARGUMENTS",
                "工具参数片段必须是字符串",
            )

        if call_id not in self._tool_calls:
            self._tool_calls[call_id] = {
                "id": call_id,
                "name": name,
                "arguments_parts": [],
            }
            self._tool_order.append(call_id)
        elif name is not None:
            old_name = self._tool_calls[call_id]["name"]
            if old_name is not None and old_name != name:
                raise StreamProtocolError(
                    "TOOL_NAME_CHANGED",
                    f"工具调用 {call_id} 的名称发生变化",
                )
            self._tool_calls[call_id]["name"] = name

        self._tool_calls[call_id]["arguments_parts"].append(arguments_delta)
        return {
            "type": "tool_call_delta",
            "call_id": call_id,
            "name": name,
            "arguments_delta": arguments_delta,
        }

    def _finish(self, reason: Any) -> dict:
        if self._finished:
            raise StreamProtocolError("DUPLICATE_FINISH", "流不能重复结束")
        if not isinstance(reason, str) or not reason.strip():
            raise StreamProtocolError("INVALID_FINISH", "结束事件缺少 reason")

        # 即使当前拼出来的 JSON 恰好合法，length 也表示模型输出被截断，
        # 不能把抢救出的参数当成完整工具请求执行。
        if reason == "length":
            self._finished = True
            raise StreamProtocolError(
                "OUTPUT_TRUNCATED",
                "模型流在完整响应前被长度限制截断",
            )

        if reason not in {"stop", "tool_calls"}:
            raise StreamProtocolError(
                "INVALID_FINISH",
                f"不支持的结束原因：{reason}",
            )

        if reason == "stop":
            if self._tool_calls:
                raise StreamProtocolError(
                    "INVALID_FINISH",
                    "包含工具调用片段时不能以 stop 结束",
                )
            content = "".join(self._text_parts)
            if not content.strip():
                raise StreamProtocolError(
                    "INVALID_FINAL_RESPONSE",
                    "正常结束时没有文本内容",
                )
            response = {"type": "final", "content": content}
        else:
            if not self._tool_calls:
                raise StreamProtocolError(
                    "INVALID_FINISH",
                    "tool_calls 结束时没有工具调用",
                )
            calls = [self._complete_tool_call(call_id) for call_id in self._tool_order]
            response = (
                {"type": "tool_call", **calls[0]}
                if len(calls) == 1
                else {"type": "tool_calls", "calls": calls}
            )

        self._finished = True
        return {
            "type": "completed",
            "response": response,
            "usage": dict(self._usage) if self._usage is not None else None,
        }

    def _complete_tool_call(self, call_id: str) -> dict:
        call = self._tool_calls[call_id]
        name = call["name"]
        if not isinstance(name, str) or not name.strip():
            raise StreamProtocolError(
                "INVALID_TOOL_CALL",
                f"工具调用 {call_id} 缺少名称",
            )

        raw_arguments = "".join(call["arguments_parts"])
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise StreamProtocolError(
                "INVALID_TOOL_ARGUMENTS",
                f"工具调用 {call_id} 的参数不是完整 JSON",
            ) from exc
        if not isinstance(arguments, dict):
            raise StreamProtocolError(
                "INVALID_TOOL_ARGUMENTS",
                f"工具调用 {call_id} 的参数必须是 JSON 对象",
            )
        return {"id": call_id, "name": name, "arguments": arguments}


def consume_stream(
    events: Iterable[dict],
    *,
    on_text_delta: Optional[Callable[[str], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> dict:
    """消费完整事件流；未收到正常 finish 前不会返回工具调用。"""
    assembler = StreamAssembler()
    for event in events:
        if is_cancelled is not None and is_cancelled():
            raise StreamCancelled("流式响应已取消")

        update = assembler.feed(event)
        if update is None:
            continue
        if update["type"] == "text_delta" and on_text_delta is not None:
            on_text_delta(update["text"])
        if update["type"] == "completed":
            if is_cancelled is not None and is_cancelled():
                raise StreamCancelled("流式响应已取消")
            return update["response"]

    raise StreamProtocolError(
        "STREAM_INCOMPLETE",
        "事件流结束前没有收到 finish 事件",
    )
