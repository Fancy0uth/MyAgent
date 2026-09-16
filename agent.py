"""第四关：使用模拟模型执行工具调用循环。"""

import json
from typing import Any, Dict, List, Optional

from model_client import MockModelClient
from tools import dispatch_tool


def _run_result(
    status: str,
    history: List[dict],
    model_requests: int,
    tool_executions: int,
    answer: Optional[str] = None,
    error: Optional[str] = None,
    message: Optional[str] = None,
) -> dict:
    result = {
        "status": status,
        "answer": answer,
        "history": history,
        "model_requests": model_requests,
        "tool_executions": tool_executions,
    }
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


def run_agent(
    question: str,
    model_client: Any = None,
    max_model_requests: int = 5,
    max_tool_executions: int = 10,
) -> dict:
    """运行离线 Agent 循环，并返回状态、回答、历史和预算统计。"""
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
                "stopped_limit",
                history,
                model_requests,
                tool_executions,
                message="已达到模型请求次数上限",
            )

        try:
            response = client.respond(history)
        except Exception as exc:
            return _failed(
                history,
                model_requests,
                tool_executions,
                "MODEL_ERROR",
                str(exc),
            )

        model_requests += 1
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

        if response_type != "tool_call":
            return _failed(
                history,
                model_requests,
                tool_executions,
                "INVALID_MODEL_RESPONSE",
                "模型返回的 type 必须是 tool_call 或 final",
            )

        call_id = response.get("id")
        name = response.get("name")
        arguments = response.get("arguments")
        if (
            not isinstance(call_id, str)
            or not call_id.strip()
            or not isinstance(name, str)
            or not name.strip()
            or not isinstance(arguments, dict)
        ):
            return _failed(
                history,
                model_requests,
                tool_executions,
                "INVALID_MODEL_RESPONSE",
                "工具调用必须包含有效的 id、name 和 arguments",
            )

        history.append(
            {
                "role": "assistant",
                "content": None,
                "tool_call": response,
            }
        )

        if tool_executions >= max_tool_executions:
            return _run_result(
                "stopped_limit",
                history,
                model_requests,
                tool_executions,
                message="已达到工具执行次数上限，未执行当前调用",
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

        if not tool_result.get("ok", False):
            return _failed(
                history,
                model_requests,
                tool_executions,
                tool_result.get("error", "TOOL_ERROR"),
                tool_result.get("message", "工具执行失败"),
            )
