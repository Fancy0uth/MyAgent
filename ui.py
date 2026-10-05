"""L25：把 Agent 事件转换成可测试的终端状态，并提供最小 REPL。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from agent import recover_agent, run_agent
from events import EVENT_NAMES, EventRecorder
from sessions import SessionStore


@dataclass
class TerminalUIState:
    """终端显示所需的状态；它不执行工具，也不调用模型。"""

    status: str = "idle"
    run_id: Optional[str] = None
    current_tool: Optional[str] = None
    last_tool: Optional[str] = None
    tool_count: int = 0
    blocked_tool_count: int = 0
    text: str = ""
    answer: str = ""
    last_error: Optional[str] = None
    errors: list[str] = field(default_factory=list)
    pending_instruction_count: int = 0

    def reset(self) -> None:
        """清空当前屏幕状态；不会删除磁盘上的会话记录。"""
        self.status = "idle"
        self.run_id = None
        self.current_tool = None
        self.last_tool = None
        self.tool_count = 0
        self.blocked_tool_count = 0
        self.text = ""
        self.answer = ""
        self.last_error = None
        self.errors.clear()
        self.pending_instruction_count = 0

    def _remember_error(self, error: Any) -> None:
        """保留一轮运行中出现过的错误，避免后一个错误覆盖前一个。"""
        if not isinstance(error, str) or not error:
            return
        self.last_error = error
        if error not in self.errors:
            self.errors.append(error)

    def apply_event(self, event: dict[str, Any]) -> None:
        """消费一个事件，只更新界面状态，不改变 Agent 的执行状态。"""
        if not isinstance(event, dict):
            return

        event_name = event.get("event")
        if event_name not in EVENT_NAMES:
            return

        self.run_id = event.get("run_id", self.run_id)
        if event_name == "run_started":
            self.status = "running"
        elif event_name == "tool_started":
            self.current_tool = event.get("tool_name")
            self.last_tool = self.current_tool
        elif event_name == "tool_finished":
            self.current_tool = None
            self.last_tool = event.get("tool_name", self.last_tool)
            self.tool_count += 1
            if event.get("status") == "failed":
                self._remember_error(event.get("error", "TOOL_ERROR"))
        elif event_name == "tool_blocked":
            self.current_tool = None
            self.last_tool = event.get("tool_name", self.last_tool)
            self.blocked_tool_count += 1
            self._remember_error(event.get("error", "TOOL_BLOCKED"))
        elif event_name == "model_text_delta":
            text = event.get("text")
            if isinstance(text, str):
                self.text += text
        elif event_name == "run_finished":
            self.status = event.get("status", self.status)
            if event.get("error"):
                self._remember_error(event["error"])

    def set_answer(self, answer: Any) -> None:
        """保存模型最终答案；非字符串结果不直接拼进终端。"""
        self.answer = answer if isinstance(answer, str) else ""

    def set_pending_instructions(self, instructions: Any) -> None:
        """显示尚未消费的队列消息数量。"""
        self.pending_instruction_count = (
            len(instructions) if isinstance(instructions, list) else 0
        )

    def render(self) -> str:
        """生成简单、稳定的文本界面；不依赖终端宽度或第三方库。"""
        tool_name = self.current_tool or self.last_tool or "无"
        lines = [
            "终端状态：",
            f"状态：{self.status}",
            f"工具：{tool_name}",
            f"已完成工具数：{self.tool_count}",
            f"被拦截工具数：{self.blocked_tool_count}",
        ]
        if self.text:
            lines.extend(["模型流式输出：", self.text])
        if self.answer:
            lines.extend(["最终回答：", self.answer])
        if self.errors:
            lines.append("错误：" + "、".join(self.errors))
        if self.pending_instruction_count:
            lines.append(
                f"未消费指令：{self.pending_instruction_count} 条"
            )
        return "\n".join(lines)


def parse_input_line(raw_line: str) -> tuple[str, str]:
    """把一行输入分成命令/普通问题和剩余文本。"""
    if not isinstance(raw_line, str):
        return "prompt", ""
    text = raw_line.strip()
    if not text:
        return "prompt", ""
    if not text.startswith("/"):
        return "prompt", text

    parts = text[1:].split(maxsplit=1)
    command = parts[0].lower() if parts else ""
    payload = parts[1] if len(parts) == 2 else ""
    return command, payload


def _subscribe_state(recorder: EventRecorder, state: TerminalUIState) -> None:
    """让一个运行的所有固定事件都同步更新当前 UI 状态。"""
    for event_name in EVENT_NAMES:
        recorder.subscribe(event_name, state.apply_event)


def run_repl(
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    run_fn: Callable[..., dict] = run_agent,
    recover_fn: Callable[..., dict] = recover_agent,
    session_store: Optional[SessionStore] = None,
    run_kwargs: Optional[dict[str, Any]] = None,
    recover_kwargs: Optional[dict[str, Any]] = None,
) -> str:
    """运行最小终端循环；核心 Agent 通过依赖注入传入，方便测试。"""
    state = TerminalUIState()
    options = {} if run_kwargs is None else dict(run_kwargs)
    resume_options = {} if recover_kwargs is None else dict(recover_kwargs)
    compact_next = False

    output_fn("输入问题开始运行；命令：/new /compact /resume /exit")
    while True:
        command, payload = parse_input_line(input_fn("你> "))

        if command == "exit":
            output_fn("已退出终端会话。")
            return "exited"

        if command == "new":
            state.reset()
            compact_next = False
            output_fn("已重置当前界面状态；磁盘会话记录未删除。")
            continue

        if command == "compact":
            compact_next = True
            output_fn("下一次问题将启用上下文压缩。")
            continue

        if command == "resume":
            if session_store is None:
                output_fn("当前没有配置会话存储，无法恢复。")
                continue
            recovery = recover_fn(session_store, **resume_options)
            output_fn(
                "恢复检查："
                f" status={recovery.get('status', 'unknown')}"
                f" reason={recovery.get('reason', '-')}"
            )
            if recovery.get("message"):
                output_fn(recovery["message"])
            continue

        if command == "unknown":
            output_fn("未知命令，请使用 /new、/compact、/resume 或 /exit。")
            continue

        if command != "prompt" or not payload:
            output_fn("问题不能为空，请重新输入。")
            continue

        state.reset()
        recorder = EventRecorder()
        _subscribe_state(recorder, state)
        request_options = dict(options)
        request_options["event_recorder"] = recorder
        request_options["use_compaction"] = compact_next
        result = run_fn(payload, **request_options)
        compact_next = False
        state.set_answer(result.get("answer"))
        state.set_pending_instructions(result.get("pending_instructions"))
        output_fn(state.render())
