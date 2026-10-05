"""Agent 执行循环：兼容离线模拟模型和真实模型客户端。"""

import json
import time
import uuid
from pathlib import Path
from threading import Event
from typing import Any, Callable, Dict, List, Optional

from agent_tools.coding_flow import run_coding_flow
from context import (
    DEFAULT_CONTEXT_BUDGET,
    ContextBuildError,
    build_compacted_context,
    build_context,
)
from events import EventRecorder
from extensions import ExtensionError, ExtensionManager
from input_queue import InstructionQueue
from model_client import ModelClientError, MockModelClient, TOOL_DEFINITIONS
from policy import file_sha256, resolve_editable_path
from sessions import SessionLoadError, SessionStore
from streaming import StreamCancelled, StreamProtocolError, consume_stream
from tools import ROOT as READ_ONLY_ROOT, dispatch_tool


# 这些错误会作为 tool 消息回传给模型，让模型有机会修正调用。
# 例如模型把文件名写错时，可以根据 FILE_NOT_FOUND 重新选择文件。
RECOVERABLE_TOOL_ERRORS = {
    "UNKNOWN_TOOL",
    "INVALID_ARGUMENT",
    "INVALID_RANGE",
    "FILE_NOT_FOUND",
}

READ_ONLY_RECOVERY_TOOLS = frozenset({"list_files", "read_file", "search_text"})


def _resolved_path_or_none(value: Any) -> Optional[Path]:
    """把日志中的路径安全地解析；坏路径只会阻止恢复，不会中断检查。"""
    if not isinstance(value, (str, Path)):
        return None
    try:
        return Path(value).resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _run_result(
    status: str,
    history: List[dict],
    model_requests: int,
    tool_executions: int,
    answer: Optional[str] = None,
    error: Optional[str] = None,
    message: Optional[str] = None,
    reason: Optional[str] = None,
) -> dict:
    # status 表示运行处于什么状态，reason 单独说明为什么进入这个状态。
    # 这样“停止”可以统一处理，同时还能区分模型预算和工具预算。
    result = {
        "status": status,
        "answer": answer,
        "history": history,
        "model_requests": model_requests,
        "tool_executions": tool_executions,
    }
    if reason is not None:
        result["reason"] = reason
    if error is not None:
        result["error"] = error
    if message is not None:
        result["message"] = message
    return result


def _failed(
    history: List[dict],
    model_requests: int,
    tool_executions: int,
    error: str,
    message: str,
) -> dict:
    return _run_result(
        "failed",
        history,
        model_requests,
        tool_executions,
        error=error,
        message=message,
    )


def _finish_with_events(
    result: dict,
    recorder: EventRecorder,
    run_id: str,
    run_started_at: float,
) -> dict:
    """补充运行结束事件，并把事件快照放进最终结果。"""
    finish_fields = {"status": result["status"]}
    for field_name in ("reason", "error"):
        if field_name in result:
            finish_fields[field_name] = result[field_name]

    # 所有正常运行的返回路径都经过这里，确保最后一定有 run_finished。
    # 用 perf_counter 测量本地耗时，适合计算持续时间，不受系统时钟调整影响。
    finish_fields["duration_ms"] = round(
        (time.perf_counter() - run_started_at) * 1000,
        2,
    )
    recorder.emit("run_finished", run_id=run_id, **finish_fields)
    result["run_id"] = run_id
    result["events"] = recorder.snapshot()
    return result


def _validate_tool_call(call: Any) -> Optional[dict]:
    """校验模型返回的单个工具调用，并去掉不参与分发的字段。"""
    if not isinstance(call, dict):
        return None

    call_id = call.get("id")
    name = call.get("name")
    arguments = call.get("arguments")
    if (
        not isinstance(call_id, str)
        or not call_id.strip()
        or not isinstance(name, str)
        or not name.strip()
        or not isinstance(arguments, dict)
    ):
        return None

    return {"id": call_id, "name": name, "arguments": arguments}


def _record_not_executed(
    history: List[dict],
    call_id: str,
    persist_entry: Optional[Callable[[str, dict], None]] = None,
) -> None:
    """在历史中记录一个因预算不足而没有执行的调用。"""
    not_executed = {
        "ok": False,
        "call_id": call_id,
        "error": "NOT_EXECUTED",
        "message": "工具预算不足，当前调用未执行",
        "executed": False,
    }
    message = {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(not_executed, ensure_ascii=False),
    }
    history.append(message)
    if persist_entry is not None:
        persist_entry("tool_result", message)


def _history_to_context_entries(history: List[dict]) -> List[dict]:
    """把运行中的消息转成上下文构造器能识别的最小记录。"""
    type_by_role = {
        "user": "user_message",
        "assistant": "assistant_message",
        "tool": "tool_result",
    }
    records = []
    for index, message in enumerate(history):
        if not isinstance(message, dict):
            raise ContextBuildError(
                "INVALID_MESSAGE",
                f"第 {index + 1} 条运行消息必须是对象",
            )
        entry_type = type_by_role.get(message.get("role"))
        if entry_type is None:
            raise ContextBuildError(
                "INVALID_MESSAGE",
                f"第 {index + 1} 条运行消息的 role 无效",
            )
        records.append(
            {
                "id": f"request-message-{index}",
                "type": entry_type,
                "payload": message,
            }
        )
    return records


def run_agent(
    question: str,
    model_client: Any = None,
    max_model_requests: int = 5,
    max_tool_executions: int = 10,
    event_recorder: Optional[EventRecorder] = None,
    cancel_event: Optional[Event] = None,
    session_store: Optional[SessionStore] = None,
    context_instructions: Optional[List[dict]] = None,
    context_budget: Optional[dict] = None,
    use_compaction: bool = False,
    compaction_keep_recent_groups: int = 2,
    use_streaming: bool = False,
    on_text_delta: Optional[Callable[[str], None]] = None,
    instruction_queue: Optional[InstructionQueue] = None,
    extension_manager: Optional[ExtensionManager] = None,
) -> dict:
    """运行 Agent 循环，并返回状态、回答、历史和预算统计。

    L19 新增的两个可选参数只控制“发给模型的请求上下文”：
    - context_instructions：本次请求明确允许参考的指令记录；
    - context_budget：输入、工具结果和输出预留预算。
    - use_compaction：明确开启 L20 固定摘要压缩；默认保持 L19 行为；
    - compaction_keep_recent_groups：压缩时保留的最近完整消息组数量。
    - use_streaming：明确使用客户端的流式接口；默认保持完整响应接口；
    - on_text_delta：收到文本片段时的可选回调，不负责执行工具。
    - instruction_queue：运行期间接收 Steering / Follow-up 的可选队列；
      不传入时，原有调用方式和执行流程保持不变。
    - extension_manager：可选的显式扩展管理器；扩展只能观察、检查或增加
      已注册的只读工具，不会自动获得文件写入或命令执行权限。
    """
    if not isinstance(question, str) or not question.strip():
        return _failed([], 0, 0, "INVALID_ARGUMENT", "问题必须是非空字符串")
    if not isinstance(max_model_requests, int) or max_model_requests < 1:
        return _failed([], 0, 0, "INVALID_ARGUMENT", "模型请求预算必须大于 0")
    if not isinstance(max_tool_executions, int) or max_tool_executions < 1:
        return _failed([], 0, 0, "INVALID_ARGUMENT", "工具执行预算必须大于 0")
    if instruction_queue is not None and not isinstance(
        instruction_queue,
        InstructionQueue,
    ):
        return _failed(
            [],
            0,
            0,
            "INVALID_ARGUMENT",
            "instruction_queue 必须是 InstructionQueue",
        )
    if extension_manager is not None and not isinstance(
        extension_manager,
        ExtensionManager,
    ):
        return _failed(
            [],
            0,
            0,
            "INVALID_ARGUMENT",
            "extension_manager 必须是 ExtensionManager",
        )

    active_entries = session_store.get_active_path() if session_store is not None else []
    history_entry_ids = [
        entry["id"]
        for entry in active_entries
        if entry["type"] in {"user_message", "assistant_message", "tool_result"}
    ]
    stored_history = [
        entry["payload"]
        for entry in active_entries
        if entry["type"] in {"user_message", "assistant_message", "tool_result"}
    ]
    pending_tool_calls = (
        session_store.get_unresolved_tool_calls() if session_store is not None else []
    )
    if pending_tool_calls:
        result = _run_result(
            "recovery_required",
            stored_history,
            0,
            0,
            reason="pending_tool_calls",
            message="活动历史包含未完成的工具调用；请先检查并恢复该运行",
        )
        result["pending_tool_calls"] = pending_tool_calls
        return result

    client = model_client if model_client is not None else MockModelClient()
    # 没有传入记录器时自动创建一个，因此 main.py 也能直接看到事件结果。
    recorder = event_recorder if event_recorder is not None else EventRecorder()
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    run_started_at = time.perf_counter()
    if extension_manager is not None:
        extension_manager.attach(recorder)
    recorder.emit("run_started", run_id=run_id)

    def persist_entry(entry_type: str, payload: dict) -> None:
        """会话记录是可选的；不传 store 时，原有运行行为保持不变。"""
        if session_store is not None:
            session_store.append(entry_type, payload)

    # Provider 只接收活动分支的聊天消息；运行元数据和兄弟分支不会进入历史。
    history: List[dict] = stored_history

    # 恢复前要能核对这次运行依赖的 Provider、工作区和权限配置。
    persist_entry(
        "run_context",
        {
            "run_id": run_id,
            "workspace": str(READ_ONLY_ROOT.resolve()),
            "permission_profile": "read_only",
            "provider": type(client).__name__,
            "history_entry_ids": history_entry_ids,
        },
    )

    def finish(result: dict) -> dict:
        """统一补充 run_finished，避免某条返回路径漏记结束事件。"""
        # 取消或预算停止时，调用方可以从这里拿到尚未消费的消息；
        # 未消费消息不会悄悄消失，也不会被自动重放。
        if instruction_queue is not None:
            result["pending_instructions"] = instruction_queue.pending()
        # 最后一条状态记录放在本次运行的消息之后，便于以后读取会话时判断结局。
        status_payload = {"status": result["status"]}
        for field_name in ("reason", "error", "message"):
            if field_name in result:
                status_payload[field_name] = result[field_name]
        persist_entry("run_status", status_payload)
        return _finish_with_events(result, recorder, run_id, run_started_at)

    def cancellation_result() -> dict:
        """生成统一的取消结果；取消不是模型错误，也不是预算停止。"""
        return _run_result(
            "cancelled",
            history,
            model_requests,
            tool_executions,
            reason="user_cancelled",
            message="用户请求取消，Agent 未继续发起新的调用",
        )

    history.append({"role": "user", "content": question.strip()})
    model_requests = 0
    tool_executions = 0
    persist_entry("user_message", history[-1])

    def append_instruction(entry: dict) -> None:
        """把已消费的队列消息转换为普通 user 消息交给模型。"""
        label = "Steering" if entry["kind"] == "steering" else "Follow-up"
        message = {
            "role": "user",
            "content": f"{label} 指令：{entry['content']}",
        }
        history.append(message)
        persist_entry("user_message", message)

    def apply_next_steering() -> bool:
        """在下一次模型请求前最多注入一条 Steering。"""
        if instruction_queue is None:
            return False
        entry = instruction_queue.pop_next("steering")
        if entry is None:
            return False
        append_instruction(entry)
        return True

    def schedule_after_final() -> str:
        """在最终回答边界决定是否继续一轮，并保持消息优先级稳定。

        Steering 比 Follow-up 优先，因为它仍然是在修正当前任务；只有没有
        Steering 时，才会取一条 Follow-up 开始自然的后续工作。
        """
        if instruction_queue is None or not instruction_queue.pending():
            return "none"
        if model_requests >= max_model_requests:
            return "budget"

        entry = instruction_queue.pop_next("steering")
        if entry is None:
            entry = instruction_queue.pop_next("follow_up")
        if entry is None:
            return "none"
        append_instruction(entry)
        return "continue"

    while True:
        # 在每次模型请求前检查取消标记，避免取消后继续消耗模型预算。
        if cancel_event is not None and cancel_event.is_set():
            return finish(cancellation_result())

        if model_requests >= max_model_requests:
            return finish(
                _run_result(
                    "stopped",
                    history,
                    model_requests,
                    tool_executions,
                    # 这是模型请求次数用完，不是工具执行次数用完。
                    reason="model_call_limit",
                    message="已达到模型请求次数上限",
                ),
            )

        # 工具批次已经完整结束后，才允许把新的 Steering 放进下一轮上下文。
        # 因此它不会插入当前正在执行的工具调用中间。
        apply_next_steering()

        # 每一轮都重新从完整运行历史构造请求上下文。这样保存的历史仍然
        # 完整保留，但模型只会看到本轮预算允许的、完整的消息组。
        try:
            context_args = (
                _history_to_context_entries(history),
                [] if context_instructions is None else context_instructions,
                (
                    TOOL_DEFINITIONS
                    if extension_manager is None
                    else extension_manager.tool_definitions()
                ),
                DEFAULT_CONTEXT_BUDGET if context_budget is None else context_budget,
            )
            if use_compaction:
                request_context = build_compacted_context(
                    *context_args,
                    keep_recent_groups=compaction_keep_recent_groups,
                )
            else:
                request_context = build_context(*context_args)
        except ContextBuildError as exc:
            return finish(
                _failed(
                    history,
                    model_requests,
                    tool_executions,
                    exc.code,
                    str(exc),
                ),
            )

        # 请求开始前就计数：即使网络超时，这次请求也消耗预算。
        model_requests += 1
        model_started_at = time.perf_counter()
        recorder.emit(
            "model_started",
            run_id=run_id,
            request_number=model_requests,
        )

        def emit_model_finished(status: str, **fields: Any) -> None:
            """给当前模型请求补上本次请求耗时。"""
            recorder.emit(
                "model_finished",
                run_id=run_id,
                status=status,
                duration_ms=round(
                    (time.perf_counter() - model_started_at) * 1000,
                    2,
                ),
                **fields,
            )

        stream_started = False
        try:
            if use_streaming:
                stream_method = getattr(client, "stream", None)
                if not callable(stream_method):
                    raise ModelClientError(
                        "STREAM_UNSUPPORTED",
                        "当前模型客户端没有提供 stream() 接口",
                    )

                stream_started_at = time.perf_counter()
                recorder.emit(
                    "model_stream_started",
                    run_id=run_id,
                    request_number=model_requests,
                )
                stream_started = True

                def handle_text_delta(text: str) -> None:
                    """只转发文本片段；工具必须等完整响应后才会处理。"""
                    recorder.emit(
                        "model_text_delta",
                        run_id=run_id,
                        request_number=model_requests,
                        text=text,
                    )
                    if on_text_delta is not None:
                        on_text_delta(text)

                response = consume_stream(
                    stream_method(request_context["messages"]),
                    on_text_delta=handle_text_delta,
                    is_cancelled=(
                        None
                        if cancel_event is None
                        else cancel_event.is_set
                    ),
                )
                recorder.emit(
                    "model_stream_finished",
                    run_id=run_id,
                    request_number=model_requests,
                    status="completed",
                    duration_ms=round(
                        (time.perf_counter() - stream_started_at) * 1000,
                        2,
                    ),
                    response_type=response.get("type"),
                )
            else:
                response = client.respond(request_context["messages"])
        except StreamCancelled:
            recorder.emit(
                "model_stream_finished",
                run_id=run_id,
                request_number=model_requests,
                status="cancelled",
                duration_ms=round(
                    (time.perf_counter() - model_started_at) * 1000,
                    2,
                ),
            )
            return finish(cancellation_result())
        except ModelClientError as exc:
            if stream_started:
                recorder.emit(
                    "model_stream_finished",
                    run_id=run_id,
                    request_number=model_requests,
                    status="failed",
                    duration_ms=round(
                        (time.perf_counter() - model_started_at) * 1000,
                        2,
                    ),
                    error=exc.code,
                )
            emit_model_finished("failed", error=exc.code)
            return finish(
                _failed(
                    history,
                    model_requests,
                    tool_executions,
                    exc.code,
                    str(exc),
                ),
            )
        except StreamProtocolError as exc:
            if stream_started:
                recorder.emit(
                    "model_stream_finished",
                    run_id=run_id,
                    request_number=model_requests,
                    status="failed",
                    duration_ms=round(
                        (time.perf_counter() - model_started_at) * 1000,
                        2,
                    ),
                    error=exc.code,
                )
            emit_model_finished("failed", error=exc.code)
            return finish(
                _failed(
                    history,
                    model_requests,
                    tool_executions,
                    exc.code,
                    str(exc),
                ),
            )
        except Exception as exc:
            if stream_started:
                recorder.emit(
                    "model_stream_finished",
                    run_id=run_id,
                    request_number=model_requests,
                    status="failed",
                    duration_ms=round(
                        (time.perf_counter() - model_started_at) * 1000,
                        2,
                    ),
                    error="MODEL_ERROR",
                )
            emit_model_finished("failed", error="MODEL_ERROR")
            return finish(
                _failed(
                    history,
                    model_requests,
                    tool_executions,
                    "MODEL_ERROR",
                    str(exc),
                ),
            )

        if not isinstance(response, dict):
            emit_model_finished("failed", error="INVALID_MODEL_RESPONSE")
            return finish(
                _failed(
                    history,
                    model_requests,
                    tool_executions,
                    "INVALID_MODEL_RESPONSE",
                    "模拟模型返回的结果必须是对象",
                ),
            )

        response_type = response.get("type")
        if response_type == "final":
            answer = response.get("content")
            if not isinstance(answer, str) or not answer.strip():
                emit_model_finished("failed", error="INVALID_MODEL_RESPONSE")
                return finish(
                    _failed(
                        history,
                        model_requests,
                        tool_executions,
                        "INVALID_MODEL_RESPONSE",
                        "最终回答必须是非空字符串",
                    ),
                )
            emit_model_finished("completed", response_type="final")
            assistant_message = {"role": "assistant", "content": answer}
            history.append(assistant_message)
            persist_entry("assistant_message", assistant_message)

            next_step = schedule_after_final()
            if next_step == "continue":
                continue
            if next_step == "budget":
                return finish(
                    _run_result(
                        "stopped",
                        history,
                        model_requests,
                        tool_executions,
                        answer=answer,
                        reason="model_call_limit",
                        message="当前回答已经完成，但仍有待处理指令且模型预算已耗尽",
                    ),
                )
            return finish(
                _run_result(
                    "completed",
                    history,
                    model_requests,
                    tool_executions,
                    answer=answer,
                ),
            )

        if response_type == "tool_call":
            raw_calls = [response]
        elif response_type == "tool_calls":
            raw_calls = response.get("calls")
        else:
            emit_model_finished("failed", error="INVALID_MODEL_RESPONSE")
            return finish(
                _failed(
                    history,
                    model_requests,
                    tool_executions,
                    "INVALID_MODEL_RESPONSE",
                    "模型返回的 type 必须是 tool_call、tool_calls 或 final",
                ),
            )

        if not isinstance(raw_calls, list) or not raw_calls:
            emit_model_finished("failed", error="INVALID_MODEL_RESPONSE")
            return finish(
                _failed(
                    history,
                    model_requests,
                    tool_executions,
                    "INVALID_MODEL_RESPONSE",
                    "模型工具调用列表不能为空",
                ),
            )

        calls = []
        for raw_call in raw_calls:
            call = _validate_tool_call(raw_call)
            if call is None:
                emit_model_finished("failed", error="INVALID_MODEL_RESPONSE")
                return finish(
                    _failed(
                        history,
                        model_requests,
                        tool_executions,
                        "INVALID_MODEL_RESPONSE",
                        "工具调用必须包含有效的 id、name 和 arguments",
                    ),
                )
            calls.append(call)

        emit_model_finished(
            "completed",
            response_type=response_type,
            tool_calls=len(calls),
        )

        # 多个调用必须保存在同一条 assistant 历史中，供下一次 API 请求关联。
        if len(calls) == 1:
            assistant_message = {
                "role": "assistant",
                "content": None,
                "tool_call": calls[0],
            }
        else:
            assistant_message = {
                "role": "assistant",
                "content": None,
                "tool_calls": calls,
            }
        history.append(assistant_message)
        persist_entry("assistant_message", assistant_message)
        for call in calls:
            persist_entry("tool_call", call)

        fatal_tool_error = None
        for call_index, call in enumerate(calls):
            call_id = call["id"]
            name = call["name"]
            arguments = call["arguments"]

            # 工具调用也要单独检查取消，避免模型刚返回调用后仍继续执行工具。
            if cancel_event is not None and cancel_event.is_set():
                return finish(cancellation_result())

            if tool_executions >= max_tool_executions:
                # 已经不能执行时，为当前及剩余调用留下明确的“未执行”记录。
                for pending_call in calls[call_index:]:
                    _record_not_executed(
                        history,
                        pending_call["id"],
                        persist_entry,
                    )
                return finish(
                    _run_result(
                        "stopped",
                        history,
                        model_requests,
                        tool_executions,
                        # 剩余工具调用已经记录为未执行，整个运行在这里停止。
                        reason="tool_call_limit",
                        message="已达到工具执行次数上限，剩余调用未执行",
                    ),
                )

            request_json = json.dumps(
                {
                    "id": call_id,
                    "name": name,
                    "arguments": arguments,
                },
                ensure_ascii=False,
            )
            if extension_manager is not None:
                try:
                    blocked_result = extension_manager.before_tool(call)
                except ExtensionError as exc:
                    return finish(
                        _failed(
                            history,
                            model_requests,
                            tool_executions,
                            exc.code,
                            str(exc),
                        ),
                    )
                if blocked_result is not None:
                    # 被扩展拒绝的调用要回传模型，但不计入真正执行次数，
                    # 也不调用底层 dispatcher。
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(
                            {
                                "ok": False,
                                "call_id": call_id,
                                **blocked_result,
                            },
                            ensure_ascii=False,
                        ),
                    }
                    history.append(tool_message)
                    persist_entry("tool_result", tool_message)
                    recorder.emit(
                        "tool_blocked",
                        run_id=run_id,
                        tool_call_id=call_id,
                        tool_name=name,
                        status="blocked",
                        error=blocked_result["error"],
                        message=blocked_result["message"],
                    )
                    continue

            # 只有通过执行前检查的调用，才会进入“已开始”事件和真正的工具分发。
            tool_started_at = time.perf_counter()
            try:
                recorder.emit(
                    "tool_started",
                    run_id=run_id,
                    tool_call_id=call_id,
                    tool_name=name,
                )
            except Exception as exc:
                # 事件扩展失败时停止在真正分发之前，不能吞掉异常继续执行工具。
                return finish(
                    _failed(
                        history,
                        model_requests,
                        tool_executions,
                        "EXTENSION_ERROR",
                        str(exc),
                    ),
                )

            if extension_manager is not None:
                tool_result = extension_manager.dispatch_tool(request_json)
            else:
                tool_result = dispatch_tool(request_json)
            tool_executions += 1
            tool_message = {
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(tool_result, ensure_ascii=False),
            }
            history.append(tool_message)
            persist_entry("tool_result", tool_message)

            tool_finished_fields = {
                "status": "completed" if tool_result.get("ok", False) else "failed",
                "duration_ms": round(
                    (time.perf_counter() - tool_started_at) * 1000,
                    2,
                ),
            }
            if not tool_result.get("ok", False):
                tool_finished_fields["error"] = tool_result.get(
                    "error",
                    "TOOL_ERROR",
                )
            recorder.emit(
                "tool_finished",
                run_id=run_id,
                tool_call_id=call_id,
                tool_name=name,
                **tool_finished_fields,
            )

            if tool_result.get("ok", False):
                continue

            error_code = tool_result.get("error", "TOOL_ERROR")
            error_message = tool_result.get("message", "工具执行失败")
            if error_code not in RECOVERABLE_TOOL_ERRORS and fatal_tool_error is None:
                fatal_tool_error = (error_code, error_message)

        if fatal_tool_error is not None:
            return finish(
                _failed(
                    history,
                    model_requests,
                    tool_executions,
                    fatal_tool_error[0],
                    fatal_tool_error[1],
                ),
            )

        # 成功结果或可恢复错误都会回到模型，让模型决定下一步。


def recover_agent(
    session_store: SessionStore,
    *,
    model_client: Any = None,
    workspace_root: Optional[Path] = None,
    permission_profile: Optional[str] = None,
    read_only_retry_budget: int = 0,
) -> dict:
    """重建最近一段消息并检查安全恢复条件；本函数不会调用模型。"""
    if (
        isinstance(read_only_retry_budget, bool)
        or not isinstance(read_only_retry_budget, int)
        or read_only_retry_budget < 0
    ):
        return {
            "status": "failed",
            "reason": "invalid_retry_budget",
            "message": "只读重试预算必须是大于或等于 0 的整数",
            "history": [],
            "pending_tool_calls": [],
            "pending_operations": [],
        }

    entries = session_store.load()
    if not entries:
        return {
            "status": "recovery_required",
            "reason": "empty_session",
            "message": "会话文件没有可恢复记录",
            "history": [],
            "pending_tool_calls": [],
            "pending_operations": [],
        }

    active_path = session_store.get_active_path()
    pending_operations = _inspect_pending_operations(
        entries,
        workspace_root=workspace_root,
        permission_profile=permission_profile,
    )

    # 导航到旧节点后，如果还没有新的 run_context，这只是选中了一个历史
    # 聊天位置，不是一个需要恢复或重放的中断运行。
    latest_selection = max(
        (
            index
            for index, entry in enumerate(active_path)
            if entry["type"] == "branch_selection"
        ),
        default=-1,
    )
    latest_active_context = max(
        (
            index
            for index, entry in enumerate(active_path)
            if entry["type"] == "run_context"
        ),
        default=-1,
    )
    if latest_selection > latest_active_context:
        selected_pending_calls = session_store.get_unresolved_tool_calls()
        if selected_pending_calls:
            selection_reason = "branch_selected_with_pending_tool_calls"
            selection_message = (
                "已选中历史分支，其中仍有未完成的工具调用；"
                "请先检查调用结果，恢复入口不会自动重放。"
            )
        elif pending_operations:
            selection_reason = "write_operation_needs_reconciliation"
            selection_message = "历史分支仍有写入操作需要核对。"
        else:
            selection_reason = "branch_selected"
            selection_message = "已选中历史分支；尚未开始新的中断运行。"
        return {
            "status": "recovery_required",
            "reason": selection_reason,
            "message": selection_message,
            "history": session_store.get_model_history(),
            "pending_tool_calls": selected_pending_calls,
            "pending_operations": pending_operations,
            "retried_read_only_calls": 0,
            "reconstructed_tool_results": 0,
            "context_checks": {
                "context_recorded": False,
                "workspace_matches": False,
                "permission_profile_matches": False,
                "provider_available": callable(getattr(model_client, "respond", None)),
                "provider_matches": False,
            },
            "status_missing": False,
        }

    # run_context 标出一次运行的开头；兼容旧 L16 文件时再退回最近用户消息。
    # 此处只在活动祖先路径中分析运行，不把兄弟分支拼入恢复历史。
    entries = active_path
    context_indexes = [
        index for index, entry in enumerate(entries)
        if entry["type"] == "run_context"
    ]
    last_status = max(
        (index for index, entry in enumerate(entries) if entry["type"] == "run_status"),
        default=-1,
    )
    latest_context = context_indexes[-1] if context_indexes else -1
    if latest_context > last_status:
        current_run_start = latest_context
        active_entries = entries[current_run_start:]
    else:
        user_indexes = [
            index for index, entry in enumerate(entries)
            if entry["type"] == "user_message" and index > last_status
        ]
        if user_indexes:
            current_run_start = user_indexes[-1]
            active_entries = entries[current_run_start:]
        else:
            # 如果记录以 run_status 结尾，保留该已结束运行的上下文供展示。
            previous_statuses = [
                index for index, entry in enumerate(entries)
                if entry["type"] == "run_status"
            ]
            end_index = previous_statuses[-1] if previous_statuses else len(entries)
            if latest_context >= 0:
                start_index = latest_context
            else:
                start_index = 0
            prior_users = [
                index for index, entry in enumerate(entries[:end_index])
                if entry["type"] == "user_message"
            ]
            if latest_context < 0:
                start_index = prior_users[-1] if prior_users else 0
            current_run_start = start_index
            active_entries = entries[current_run_start : end_index + 1]

    # L18 run_context 记录了本次请求实际使用的历史消息 ID。恢复时把这些
    # 活动分支祖先放回模型历史，但只用 active_entries 判断当前运行结局。
    history_entry_ids = []
    if (
        current_run_start < len(entries)
        and entries[current_run_start]["type"] == "run_context"
    ):
        recorded_ids = entries[current_run_start]["payload"].get("history_entry_ids")
        if isinstance(recorded_ids, list):
            history_entry_ids = [
                entry_id for entry_id in recorded_ids if isinstance(entry_id, str)
            ]
    history_entry_id_set = set(history_entry_ids)
    history: List[dict] = [
        entry["payload"]
        for entry in entries[:current_run_start]
        if entry["id"] in history_entry_id_set
        and entry["type"] in {"user_message", "assistant_message", "tool_result"}
        and isinstance(entry["payload"].get("role"), str)
    ]
    current_history: List[dict] = []
    calls: List[dict] = []
    completed_call_ids: set[str] = set()
    for entry in active_entries:
        payload = entry["payload"]
        if entry["type"] in {"user_message", "assistant_message"}:
            if isinstance(payload, dict) and isinstance(payload.get("role"), str):
                history.append(payload)
                current_history.append(payload)
                if payload.get("role") == "assistant":
                    single_call = payload.get("tool_call")
                    multiple_calls = payload.get("tool_calls")
                    if isinstance(single_call, dict):
                        calls.append(single_call)
                    elif isinstance(multiple_calls, list):
                        calls.extend(call for call in multiple_calls if isinstance(call, dict))
        elif entry["type"] == "tool_result":
            call_id = payload.get("tool_call_id")
            if isinstance(call_id, str):
                completed_call_ids.add(call_id)
            if isinstance(payload, dict) and payload.get("role") == "tool":
                history.append(payload)
                current_history.append(payload)

    # 操作账本若已明确落下成功/无副作用结果，可据此补齐协议消息；
    # outcome=unknown 时绝不合成工具成功结果。
    reconstructed_results = 0
    for entry in active_entries:
        if entry["type"] != "operation_result":
            continue
        operation = entry["payload"]
        call_id = operation.get("tool_call_id")
        outcome = operation.get("outcome")
        if (
            not isinstance(call_id, str)
            or call_id in completed_call_ids
            or outcome not in {"succeeded", "no_effect"}
        ):
            continue
        if outcome == "succeeded":
            result_content = {
                "ok": True,
                "applied": True,
                "operation_id": operation.get("operation_id"),
                "proposal_id": operation.get("proposal_id"),
                "path": operation.get("path"),
                "new_sha256": operation.get("new_sha256"),
            }
        else:
            result_content = {
                "ok": False,
                "operation_id": operation.get("operation_id"),
                "error": operation.get("error") or "OPERATION_NOT_APPLIED",
            }
        tool_message = {
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps(result_content, ensure_ascii=False),
        }
        try:
            session_store.append("tool_result", tool_message)
        except SessionLoadError:
            continue
        history.append(tool_message)
        current_history.append(tool_message)
        completed_call_ids.add(call_id)
        reconstructed_results += 1

    recorded_context = next(
        (
            entry["payload"]
            for entry in reversed(active_entries)
            if entry["type"] == "run_context"
        ),
        None,
    )
    requested_workspace = _resolved_path_or_none(workspace_root)
    recorded_workspace = (
        recorded_context.get("workspace") if isinstance(recorded_context, dict) else None
    )
    workspace_matches = bool(
        requested_workspace is not None
        and isinstance(recorded_workspace, str)
        and _resolved_path_or_none(recorded_workspace) == requested_workspace
    )
    permission_matches = bool(
        isinstance(recorded_context, dict)
        and isinstance(permission_profile, str)
        and permission_profile == recorded_context.get("permission_profile")
    )
    provider_available = callable(getattr(model_client, "respond", None))
    provider_matches = bool(
        provider_available
        and isinstance(recorded_context, dict)
        and type(model_client).__name__ == recorded_context.get("provider")
    )
    context_checks = {
        "context_recorded": isinstance(recorded_context, dict),
        "workspace_matches": workspace_matches,
        "permission_profile_matches": permission_matches,
        "provider_available": provider_available,
        "provider_matches": provider_matches,
    }

    pending_tool_calls = []
    seen_call_ids: set[str] = set()
    for call in calls:
        call_id = call.get("id")
        if not isinstance(call_id, str) or call_id in seen_call_ids:
            continue
        seen_call_ids.add(call_id)
        if call_id not in completed_call_ids:
            pending_tool_calls.append(
                {
                    "id": call_id,
                    "name": call.get("name"),
                    "arguments": call.get("arguments"),
                    "kind": (
                        "read_only"
                        if call.get("name") in READ_ONLY_RECOVERY_TOOLS
                        else "side_effect_or_unknown"
                    ),
                }
            )

    # 只允许在工作区与只读权限配置都一致时重试白名单里的纯读取工具。
    retry_count = 0
    retry_allowed = workspace_matches and permission_matches
    for pending in pending_tool_calls:
        if retry_count >= read_only_retry_budget:
            break
        if pending["kind"] != "read_only" or not retry_allowed:
            continue
        # 预算按“尝试次数”计算，即使分发器异常或结果未能落盘也消耗一次。
        retry_count += 1
        request = {
            "id": pending["id"],
            "name": pending["name"],
            "arguments": pending["arguments"],
        }
        try:
            result = dispatch_tool(json.dumps(request, ensure_ascii=False))
        except Exception:
            # 不写入伪造的 tool result；下次恢复仍会看到这条悬空调用。
            continue
        if not isinstance(result, dict) or result.get("call_id") != pending["id"]:
            continue
        tool_message = {
            "role": "tool",
            "tool_call_id": pending["id"],
            "content": json.dumps(result, ensure_ascii=False),
        }
        try:
            session_store.append("tool_result", tool_message)
        except SessionLoadError:
            # 工具是只读的，结果落盘失败不会造成业务副作用；保留悬空调用供重试。
            continue
        history.append(tool_message)
        current_history.append(tool_message)
        completed_call_ids.add(pending["id"])

    pending_tool_calls = [
        pending for pending in pending_tool_calls
        if pending["id"] not in completed_call_ids
    ]

    statuses = [
        entry["payload"].get("status")
        for entry in active_entries
        if entry["type"] == "run_status"
    ]
    final_answers = [
        message.get("content")
        for message in current_history
        if message.get("role") == "assistant"
        and "tool_call" not in message
        and "tool_calls" not in message
        and isinstance(message.get("content"), str)
    ]
    status_missing = bool(final_answers and not statuses)

    result = {
        "status": "recovery_required",
        "reason": None,
        "history": history,
        "pending_tool_calls": pending_tool_calls,
        "pending_operations": pending_operations,
        "retried_read_only_calls": retry_count,
        "reconstructed_tool_results": reconstructed_results,
        "context_checks": context_checks,
        "status_missing": status_missing,
    }
    if final_answers and not pending_tool_calls and not pending_operations:
        result["status"] = "completed"
        result["answer"] = final_answers[-1]
        result["reason"] = "final_answer_was_persisted"
        return result
    if pending_tool_calls:
        if any(call["kind"] != "read_only" for call in pending_tool_calls):
            result["reason"] = "unknown_side_effect_result"
        elif read_only_retry_budget == 0:
            result["reason"] = "read_only_retry_budget_required"
        elif not retry_allowed:
            result["reason"] = "recovery_context_mismatch"
        else:
            result["reason"] = "read_only_retry_budget_exhausted"
        return result
    if pending_operations:
        result["reason"] = "write_operation_needs_reconciliation"
        return result

    latest_status = statuses[-1] if statuses else None
    if latest_status == "completed":
        result["status"] = "completed"
        result["reason"] = "completed_status_was_persisted"
        return result
    if not all(
        context_checks[key]
        for key in (
            "context_recorded",
            "workspace_matches",
            "permission_profile_matches",
            "provider_available",
            "provider_matches",
        )
    ):
        result["reason"] = "recovery_context_mismatch"
        return result

    result["status"] = "ready_to_resume"
    result["reason"] = "context_and_history_validated"
    return result


def _inspect_pending_operations(
    entries: List[dict],
    *,
    workspace_root: Optional[Path],
    permission_profile: Optional[str],
) -> List[dict]:
    """核对尚无确定结果的写入账目，只观察文件，不执行写操作。"""
    intents: Dict[str, dict] = {}
    approvals: Dict[str, dict] = {}
    results: Dict[str, dict] = {}
    latest_operation_by_target: Dict[tuple[str, str], str] = {}
    for entry in entries:
        payload = entry["payload"]
        operation_id = payload.get("operation_id")
        if not isinstance(operation_id, str):
            continue
        if entry["type"] == "operation_intent":
            intents[operation_id] = payload
            if isinstance(payload.get("workspace"), str) and isinstance(payload.get("path"), str):
                latest_operation_by_target[(payload["workspace"], payload["path"])] = operation_id
        elif entry["type"] == "operation_approval":
            approvals[operation_id] = payload
        elif entry["type"] == "operation_result":
            results[operation_id] = payload

    pending = []
    current_workspace = _resolved_path_or_none(workspace_root)
    for operation_id, intent in intents.items():
        operation_result = results.get(operation_id)
        outcome = operation_result.get("outcome") if operation_result else None
        if outcome == "no_effect":
            continue
        if outcome == "succeeded":
            target_key = (intent.get("workspace"), intent.get("path"))
            if latest_operation_by_target.get(target_key) != operation_id:
                # 后续操作已在同一目标留下更新版本，不再拿旧哈希误判当前状态。
                continue

        approval = approvals.get(operation_id)
        operation = {
            "operation_id": operation_id,
            "tool_call_id": intent.get("tool_call_id"),
            "proposal_id": intent.get("proposal_id"),
            "path": intent.get("path"),
            "approved": bool(
                approval
                and approval.get("approved") is True
                and approval.get("binding_matches") is True
            ),
            "safe_to_replay": False,
            "state": "approval_not_recorded",
        }
        if not operation["approved"]:
            pending.append(operation)
            continue

        recorded_workspace = intent.get("workspace")
        same_workspace = bool(
            current_workspace is not None
            and isinstance(recorded_workspace, str)
            and _resolved_path_or_none(recorded_workspace) == current_workspace
        )
        same_profile = permission_profile == intent.get("permission_profile")
        if not same_workspace or not same_profile:
            operation["state"] = "recovery_context_mismatch"
            pending.append(operation)
            continue

        try:
            target = resolve_editable_path(current_workspace, intent["path"])
            current_hash = file_sha256(target) if target.is_file() else None
        except (
            KeyError,
            OSError,
            RuntimeError,
            ValueError,
            PermissionError,
            FileNotFoundError,
            TypeError,
        ):
            operation["state"] = "target_cannot_be_safely_inspected"
            pending.append(operation)
            continue

        if current_hash == intent.get("new_sha256"):
            if outcome == "succeeded":
                # 成功账本和当前文件版本一致，操作闭环完整。
                continue
            operation["state"] = "effect_observed_result_missing"
        elif current_hash == intent.get("old_sha256"):
            if outcome == "succeeded":
                operation["state"] = "file_version_changed_after_recorded_write"
                pending.append(operation)
                continue
            operation["state"] = "effect_not_observed_manual_decision_required"
        else:
            operation["state"] = (
                "file_version_changed_after_recorded_write"
                if outcome == "succeeded"
                else "external_change_conflict"
            )
        pending.append(operation)
    return pending
