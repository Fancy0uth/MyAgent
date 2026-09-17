"""Agent 执行循环：兼容离线模拟模型和真实模型客户端。"""

import json
from typing import Any, Dict, List, Optional

from model_client import ModelClientError, MockModelClient
from tools import dispatch_tool


# 这些错误会作为 tool 消息回传给模型，让模型有机会修正调用。
# 例如模型把文件名写错时，可以根据 FILE_NOT_FOUND 重新选择文件。
RECOVERABLE_TOOL_ERRORS = {
    "UNKNOWN_TOOL",
    "INVALID_ARGUMENT",
    "INVALID_RANGE",
    "FILE_NOT_FOUND",
}


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


def _record_not_executed(history: List[dict], call_id: str) -> None:
    """在历史中记录一个因预算不足而没有执行的调用。"""
    not_executed = {
        "ok": False,
        "call_id": call_id,
        "error": "NOT_EXECUTED",
        "message": "工具预算不足，当前调用未执行",
        "executed": False,
    }
    history.append(
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps(not_executed, ensure_ascii=False),
        }
    )


def run_agent(
    question: str,
    model_client: Any = None,
    max_model_requests: int = 5,
    max_tool_executions: int = 10,
) -> dict:
    """运行 Agent 循环，并返回状态、回答、历史和预算统计。"""
    if not isinstance(question, str) or not question.strip():
        return _failed([], 0, 0, "INVALID_ARGUMENT", "问题必须是非空字符串")
    if not isinstance(max_model_requests, int) or max_model_requests < 1:
        return _failed([], 0, 0, "INVALID_ARGUMENT", "模型请求预算必须大于 0")
    if not isinstance(max_tool_executions, int) or max_tool_executions < 1:
        return _failed([], 0, 0, "INVALID_ARGUMENT", "工具执行预算必须大于 0")

    client = model_client if model_client is not None else MockModelClient()
    history: List[dict] = [{"role": "user", "content": question.strip()}]
    model_requests = 0
    tool_executions = 0

    while True:
        if model_requests >= max_model_requests:
            return _run_result(
                "stopped",
                history,
                model_requests,
                tool_executions,
                # 这是模型请求次数用完，不是工具执行次数用完。
                reason="model_call_limit",
                message="已达到模型请求次数上限",
            )

        # 请求开始前就计数：即使网络超时，这次请求也消耗预算。
        model_requests += 1
        try:
            response = client.respond(history)
        except ModelClientError as exc:
            return _failed(
                history,
                model_requests,
                tool_executions,
                exc.code,
                str(exc),
            )
        except Exception as exc:
            return _failed(
                history,
                model_requests,
                tool_executions,
                "MODEL_ERROR",
                str(exc),
            )

        if not isinstance(response, dict):
            return _failed(
                history,
                model_requests,
                tool_executions,
                "INVALID_MODEL_RESPONSE",
                "模拟模型返回的结果必须是对象",
            )

        response_type = response.get("type")
        if response_type == "final":
            answer = response.get("content")
            if not isinstance(answer, str) or not answer.strip():
                return _failed(
                    history,
                    model_requests,
                    tool_executions,
                    "INVALID_MODEL_RESPONSE",
                    "最终回答必须是非空字符串",
                )
            history.append({"role": "assistant", "content": answer})
            return _run_result(
                "completed",
                history,
                model_requests,
                tool_executions,
                answer=answer,
            )

        if response_type == "tool_call":
            raw_calls = [response]
        elif response_type == "tool_calls":
            raw_calls = response.get("calls")
        else:
            return _failed(
                history,
                model_requests,
                tool_executions,
                "INVALID_MODEL_RESPONSE",
                "模型返回的 type 必须是 tool_call、tool_calls 或 final",
            )

        if not isinstance(raw_calls, list) or not raw_calls:
            return _failed(
                history,
                model_requests,
                tool_executions,
                "INVALID_MODEL_RESPONSE",
                "模型工具调用列表不能为空",
            )

        calls = []
        for raw_call in raw_calls:
            call = _validate_tool_call(raw_call)
            if call is None:
                return _failed(
                    history,
                    model_requests,
                    tool_executions,
                    "INVALID_MODEL_RESPONSE",
                    "工具调用必须包含有效的 id、name 和 arguments",
                )
            calls.append(call)

        # 多个调用必须保存在同一条 assistant 历史中，供下一次 API 请求关联。
        if len(calls) == 1:
            history.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_call": calls[0],
                }
            )
        else:
            history.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": calls,
                }
            )

        fatal_tool_error = None
        for call_index, call in enumerate(calls):
            call_id = call["id"]
            name = call["name"]
            arguments = call["arguments"]

            if tool_executions >= max_tool_executions:
                # 已经不能执行时，为当前及剩余调用留下明确的“未执行”记录。
                for pending_call in calls[call_index:]:
                    _record_not_executed(history, pending_call["id"])
                return _run_result(
                    "stopped",
                    history,
                    model_requests,
                    tool_executions,
                    # 剩余工具调用已经记录为未执行，整个运行在这里停止。
                    reason="tool_call_limit",
                    message="已达到工具执行次数上限，剩余调用未执行",
                )

            request_json = json.dumps(
                {
                    "id": call_id,
                    "name": name,
                    "arguments": arguments,
                },
                ensure_ascii=False,
            )
            tool_result = dispatch_tool(request_json)
            tool_executions += 1
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(tool_result, ensure_ascii=False),
                }
            )

            if tool_result.get("ok", False):
                continue

            error_code = tool_result.get("error", "TOOL_ERROR")
            error_message = tool_result.get("message", "工具执行失败")
            if error_code not in RECOVERABLE_TOOL_ERRORS and fatal_tool_error is None:
                fatal_tool_error = (error_code, error_message)

        if fatal_tool_error is not None:
            return _failed(
                history,
                model_requests,
                tool_executions,
                fatal_tool_error[0],
                fatal_tool_error[1],
            )

        # 成功结果或可恢复错误都会回到模型，让模型决定下一步。
