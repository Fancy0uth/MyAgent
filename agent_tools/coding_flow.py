"""L14 的受控 Coding Agent 修复闭环。"""

import json
import uuid
from pathlib import Path
from typing import Any, Callable, List, Optional

from agent_tools.edit import EditProposalStore
from agent_tools.execution import TestExecutor
from policy import resolve_editable_path
from sessions import SessionLoadError, SessionStore


MAX_WORKSPACE_FILE_BYTES = 256 * 1024
MAX_READ_LINES = 80


def _failure(error: str, message: str) -> dict:
    """生成统一的工具失败结果。"""
    return {"ok": False, "error": error, "message": message}


def _flow_result(
    status: str,
    history: List[dict],
    model_requests: int,
    tool_executions: int,
    tests_run: int,
    edits_applied: int,
    **fields: Any,
) -> dict:
    """统一生成修复闭环的结果。"""
    result = {
        "status": status,
        "history": history,
        "model_requests": model_requests,
        "tool_executions": tool_executions,
        "tests_run": tests_run,
        "edits_applied": edits_applied,
    }
    result.update(fields)
    return result


def _valid_line_number(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def read_workspace_file(
    workspace: Path,
    path: str,
    start_line: int = 1,
    end_line: int = MAX_READ_LINES,
) -> dict:
    """读取实验工作区内的文件，返回带行号的文本。"""
    if not _valid_line_number(start_line) or not _valid_line_number(end_line):
        return _failure("INVALID_ARGUMENT", "行号必须是大于或等于 1 的整数")
    if end_line < start_line:
        return _failure("INVALID_RANGE", "结束行不能小于起始行")
    if end_line - start_line + 1 > MAX_READ_LINES:
        return _failure("INVALID_RANGE", "一次最多读取 80 行")

    try:
        target = resolve_editable_path(workspace, path)
    except ValueError as exc:
        return _failure("INVALID_ARGUMENT", str(exc))
    except PermissionError as exc:
        return _failure("ACCESS_DENIED", str(exc))
    except FileNotFoundError as exc:
        return _failure("FILE_NOT_FOUND", str(exc))

    if not target.exists():
        return _failure("FILE_NOT_FOUND", "目标文件不存在")
    if not target.is_file():
        return _failure("INVALID_ARGUMENT", "目标必须是普通文件")

    try:
        if target.stat().st_size > MAX_WORKSPACE_FILE_BYTES:
            return _failure("FILE_TOO_LARGE", "文件超过 256 KiB 限制")
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return _failure("READ_ERROR", "文件不是可读取的 UTF-8 文本")
    except OSError:
        return _failure("READ_ERROR", "读取文件失败")

    lines = text.splitlines()
    if not lines:
        return {
            "ok": True,
            "path": Path(path).as_posix(),
            "start_line": None,
            "end_line": None,
            "content": "",
        }
    if start_line > len(lines):
        return _failure("INVALID_RANGE", "起始行超过文件实际行数")

    actual_end = min(end_line, len(lines))
    content = "\n".join(
        f"{number}: {lines[number - 1]}"
        for number in range(start_line, actual_end + 1)
    )
    return {
        "ok": True,
        "path": Path(path).as_posix(),
        "start_line": start_line,
        "end_line": actual_end,
        "content": content,
    }


def _validate_tool_call(response: Any) -> Optional[dict]:
    """校验 Mock/真实适配器返回的单个工具调用。"""
    if not isinstance(response, dict):
        return None
    if response.get("type") != "tool_call":
        return None

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
        return None
    return {"id": call_id, "name": name, "arguments": arguments}


def _append_tool_message(
    history: List[dict],
    call_id: str,
    result: dict,
    session_store: Optional[SessionStore] = None,
) -> None:
    """把工具结果放回模型历史，保持调用 ID 一致。"""
    message = {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(result, ensure_ascii=False),
    }
    history.append(message)
    if session_store is not None:
        session_store.append("tool_result", message)


def _confirmation_required_result(
    history: List[dict],
    model_requests: int,
    tool_executions: int,
    tests_run: int,
    edits_applied: int,
    proposal: dict,
) -> dict:
    """用户还没有确认时暂停，而不是猜测用户同意。"""
    return _flow_result(
        "waiting_confirmation",
        history,
        model_requests,
        tool_executions,
        tests_run,
        edits_applied,
        reason="edit_confirmation_required",
        message="等待用户确认编辑提议",
        pending_proposal=proposal,
    )


def run_coding_flow(
    task: str,
    workspace: Path,
    model_client: Any,
    test_executor: TestExecutor,
    edit_store: Optional[EditProposalStore] = None,
    confirm_edit: Optional[Callable[[dict], Optional[dict]]] = None,
    max_model_requests: int = 8,
    max_tool_executions: int = 12,
    session_store: Optional[SessionStore] = None,
) -> dict:
    """运行读取、测试、编辑、确认、再测试的最小修复闭环。

    confirm_edit 必须由可信调用方提供；它接收完整提议并返回绑定了
    proposal_id、路径和哈希的确认对象。没有确认回调时绝不会写文件。
    """
    if not isinstance(task, str) or not task.strip():
        return _flow_result(
            "failed", [], 0, 0, 0, 0,
            error="INVALID_ARGUMENT",
            message="修复任务必须是非空字符串",
        )
    if not hasattr(model_client, "respond"):
        return _flow_result(
            "failed", [], 0, 0, 0, 0,
            error="INVALID_ARGUMENT",
            message="model_client 必须提供 respond 方法",
        )
    if not isinstance(max_model_requests, int) or max_model_requests < 1:
        return _flow_result(
            "failed", [], 0, 0, 0, 0,
            error="INVALID_ARGUMENT",
            message="模型请求预算必须大于 0",
        )
    if not isinstance(max_tool_executions, int) or max_tool_executions < 1:
        return _flow_result(
            "failed", [], 0, 0, 0, 0,
            error="INVALID_ARGUMENT",
            message="工具执行预算必须大于 0",
        )

    store = edit_store if edit_store is not None else EditProposalStore()
    history: List[dict] = [{"role": "user", "content": task.strip()}]
    model_requests = 0
    tool_executions = 0
    tests_run = 0
    edits_applied = 0
    validation_passed = False

    if session_store is not None:
        try:
            # 记录恢复检查所需的环境身份，不把 Key 或文件正文写进日志。
            session_store.append(
                "run_context",
                {
                    "run_id": f"coding-{uuid.uuid4().hex[:8]}",
                    "workspace": str(Path(workspace).resolve()),
                    "permission_profile": "workspace_write_confirmed",
                    "provider": type(model_client).__name__,
                },
            )
            session_store.append(
                "user_message",
                {"role": "user", "content": task.strip()},
            )
        except SessionLoadError as exc:
            return _flow_result(
                "failed",
                history,
                model_requests,
                tool_executions,
                tests_run,
                edits_applied,
                error="SESSION_WRITE_ERROR",
                message=str(exc),
            )

    while True:
        if model_requests >= max_model_requests:
            return _flow_result(
                "stopped",
                history,
                model_requests,
                tool_executions,
                tests_run,
                edits_applied,
                reason="model_call_limit",
                message="已达到模型请求次数上限",
            )

        model_requests += 1
        try:
            response = model_client.respond(history)
        except Exception as exc:
            return _flow_result(
                "failed",
                history,
                model_requests,
                tool_executions,
                tests_run,
                edits_applied,
                error="MODEL_ERROR",
                message=str(exc),
            )

        if isinstance(response, dict) and response.get("type") == "final":
            answer = response.get("content")
            if not isinstance(answer, str) or not answer.strip():
                return _flow_result(
                    "failed",
                    history,
                    model_requests,
                    tool_executions,
                    tests_run,
                    edits_applied,
                    error="INVALID_MODEL_RESPONSE",
                    message="最终回答必须是非空字符串",
                )
            if not validation_passed:
                return _flow_result(
                    "failed",
                    history,
                    model_requests,
                    tool_executions,
                    tests_run,
                    edits_applied,
                    error="UNVERIFIED_REPAIR",
                    message="没有测试通过证据，不能宣称修复完成",
                )
            history.append({"role": "assistant", "content": answer})
            if session_store is not None:
                try:
                    session_store.append(
                        "assistant_message",
                        history[-1],
                    )
                    session_store.append(
                        "run_status",
                        {"status": "completed"},
                    )
                except SessionLoadError as exc:
                    return _flow_result(
                        "recovery_required",
                        history,
                        model_requests,
                        tool_executions,
                        tests_run,
                        edits_applied,
                        error="FINAL_RESULT_NOT_PERSISTED",
                        message=f"最终回答未能完整保存：{exc}",
                    )
            return _flow_result(
                "completed",
                history,
                model_requests,
                tool_executions,
                tests_run,
                edits_applied,
                answer=answer,
            )

        call = _validate_tool_call(response)
        if call is None:
            return _flow_result(
                "failed",
                history,
                model_requests,
                tool_executions,
                tests_run,
                edits_applied,
                error="INVALID_MODEL_RESPONSE",
                message="修复模型必须返回 tool_call 或 final",
            )

        history.append(
            {
                "role": "assistant",
                "content": None,
                "tool_call": call,
            }
        )
        if session_store is not None:
            try:
                session_store.append("assistant_message", history[-1])
                session_store.append("tool_call", call)
            except SessionLoadError as exc:
                return _flow_result(
                    "recovery_required",
                    history,
                    model_requests,
                    tool_executions,
                    tests_run,
                    edits_applied,
                    error="TOOL_CALL_NOT_PERSISTED",
                    message=f"工具调用未能完整保存，未执行该调用：{exc}",
                )

        if tool_executions >= max_tool_executions:
            return _flow_result(
                "stopped",
                history,
                model_requests,
                tool_executions,
                tests_run,
                edits_applied,
                reason="tool_call_limit",
                message="已达到工具执行次数上限",
            )

        tool_executions += 1
        name = call["name"]
        arguments = call["arguments"]
        tool_result: dict

        if name == "read_workspace_file":
            try:
                tool_result = read_workspace_file(workspace, **arguments)
            except TypeError:
                tool_result = _failure("INVALID_ARGUMENT", "读取参数不正确")

        elif name == "run_tests":
            if set(arguments) != {"case_id"}:
                tool_result = _failure(
                    "INVALID_ARGUMENT",
                    "run_tests 只接受 case_id",
                )
            else:
                try:
                    tool_result = test_executor.run_tests(arguments["case_id"])
                except (TypeError, ValueError):
                    tool_result = _failure("INVALID_ARGUMENT", "case_id 参数不正确")
                tests_run += 1
                tool_result = dict(tool_result)
                tool_result["passed"] = bool(
                    tool_result.get("ok")
                    and tool_result.get("exit_code") == 0
                    and not tool_result.get("timed_out", False)
                )
                if tool_result["passed"]:
                    validation_passed = True

        elif name == "propose_edit":
            if set(arguments) != {"path", "old_text", "new_text"}:
                tool_result = _failure(
                    "INVALID_ARGUMENT",
                    "propose_edit 需要 path、old_text 和 new_text",
                )
            else:
                try:
                    proposal = store.propose_edit(
                        workspace,
                        arguments["path"],
                        arguments["old_text"],
                        arguments["new_text"],
                    )
                except TypeError:
                    proposal = _failure("INVALID_ARGUMENT", "编辑参数不正确")

                if not proposal.get("ok"):
                    tool_result = proposal
                else:
                    # 业务操作 ID 独立于模型 call ID，模型重试时也不会被误当作同一写入。
                    operation_id = f"operation-{uuid.uuid4().hex[:12]}"
                    proposal["operation_id"] = operation_id
                    if session_store is not None:
                        try:
                            session_store.record_operation_intent(
                                operation_id,
                                proposal,
                                tool_call_id=call["id"],
                                workspace_root=workspace,
                            )
                        except SessionLoadError as exc:
                            return _flow_result(
                                "failed",
                                history,
                                model_requests,
                                tool_executions,
                                tests_run,
                                edits_applied,
                                error="SESSION_WRITE_ERROR",
                                message=str(exc),
                                pending_proposal=proposal,
                            )

                    if confirm_edit is None:
                        _append_tool_message(history, call["id"], proposal, session_store)
                        return _confirmation_required_result(
                            history,
                            model_requests,
                            tool_executions,
                            tests_run,
                            edits_applied,
                            proposal,
                        )

                    try:
                        confirmation = confirm_edit(proposal)
                    except Exception as exc:
                        return _flow_result(
                            "failed",
                            history,
                            model_requests,
                            tool_executions,
                            tests_run,
                            edits_applied,
                            error="CONFIRMATION_ERROR",
                            message=str(exc),
                            pending_proposal=proposal,
                        )

                    if session_store is not None:
                        try:
                            # 批准与路径、提议 ID、新旧哈希绑定后才允许真正执行。
                            session_store.record_operation_approval(
                                operation_id,
                                confirmation,
                            )
                        except SessionLoadError as exc:
                            return _flow_result(
                                "failed",
                                history,
                                model_requests,
                                tool_executions,
                                tests_run,
                                edits_applied,
                                error="SESSION_WRITE_ERROR",
                                message=str(exc),
                                pending_proposal=proposal,
                            )

                    if not isinstance(confirmation, dict) or confirmation.get("approved") is not True:
                        _append_tool_message(history, call["id"], proposal, session_store)
                        return _confirmation_required_result(
                            history,
                            model_requests,
                            tool_executions,
                            tests_run,
                            edits_applied,
                            proposal,
                        )

                    try:
                        applied = store.apply_proposal(
                            proposal["proposal_id"],
                            confirmation,
                        )
                    except Exception as exc:
                        applied = _failure("APPLY_ERROR", str(exc))

                    if session_store is not None:
                        try:
                            session_store.record_operation_result(
                                operation_id,
                                applied,
                            )
                        except SessionLoadError as exc:
                            # 文件可能已经改变，但结果账目没能落盘；不回传成功供模型继续。
                            return _flow_result(
                                "recovery_required",
                                history,
                                model_requests,
                                tool_executions,
                                tests_run,
                                edits_applied,
                                error="OPERATION_RESULT_NOT_PERSISTED",
                                message=(
                                    "编辑执行后无法保存操作结果；请先检查目标文件和操作账本，"
                                    f"不要直接重放。详情：{exc}"
                                ),
                                pending_proposal=proposal,
                            )
                    if not applied.get("ok"):
                        _append_tool_message(history, call["id"], applied, session_store)
                        return _flow_result(
                            "recovery_required",
                            history,
                            model_requests,
                            tool_executions,
                            tests_run,
                            edits_applied,
                            error=applied.get("error", "WRITE_ERROR"),
                            message=applied.get("message", "编辑没有应用"),
                            pending_proposal=proposal,
                        )

                    tool_result = {
                        "ok": True,
                        "applied": True,
                        "operation_id": operation_id,
                        "proposal_id": applied["proposal_id"],
                        "path": applied["path"],
                    }
                    edits_applied += 1

        else:
            tool_result = _failure("UNKNOWN_TOOL", f"未知修复工具：{name}")

        _append_tool_message(history, call["id"], tool_result, session_store)
