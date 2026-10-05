"""命令行入口：选择模型模式，收集问题并展示 Agent 结果。"""

import argparse
import json
import uuid

from agent import run_agent
from model_client import DeepSeekModelClient, MockModelClient
from sessions import SessionStore
# 保留第 3 关的导出，方便旧的手动分发测试继续使用。
from tools import dispatch_tool
from ui import run_repl


TOOL_NAMES = {
    "1": "read_file",
    "2": "list_files",
    "3": "search_text",
}

DEFAULT_MODEL = "deepseek-flash"
DEFAULT_MAX_MODEL_REQUESTS = 5
DEFAULT_MAX_TOOL_EXECUTIONS = 10


def build_model_client(mode: str, model: str):
    """根据模式创建客户端；只有 real 模式才会初始化真实模型。"""
    if mode == "mock":
        return MockModelClient()
    if mode == "real":
        # 真实模式必须由 --real 明确开启，避免普通运行产生网络请求和费用。
        return DeepSeekModelClient(model=model)
    raise ValueError("模式必须是 mock 或 real")


def _ask_non_empty(input_fn, prompt: str) -> str:
    while True:
        value = input_fn(prompt).strip()
        if value:
            return value
        print("输入不能为空，请重新输入。")


def _ask_positive_integer(input_fn, prompt: str) -> int:
    while True:
        raw_value = input_fn(prompt).strip()
        try:
            value = int(raw_value)
        except ValueError:
            print("请输入整数。")
            continue
        if value < 1:
            print("请输入大于或等于 1 的数字。")
            continue
        return value


def get_user_input(input_fn=input) -> str:
    """通过交互式提示获取参数，并自动生成工具调用 JSON。"""
    print("请选择要执行的功能：")
    print("1. 读取文件")
    print("2. 列出文件")
    print("3. 搜索文本")

    while True:
        choice = input_fn("请输入编号：").strip()
        if choice in TOOL_NAMES:
            tool_name = TOOL_NAMES[choice]
            break
        if choice in TOOL_NAMES.values():
            tool_name = choice
            break
        print("选项无效，请输入 1、2 或 3。")

    arguments = {}
    if tool_name == "read_file":
        path = _ask_non_empty(input_fn, "请输入文件路径：")
        start_line = _ask_positive_integer(input_fn, "请输入起始行：")
        while True:
            end_line = _ask_positive_integer(input_fn, "请输入结束行：")
            if end_line >= start_line:
                break
            print("结束行不能小于起始行，请重新输入。")
        arguments = {
            "path": path,
            "start_line": start_line,
            "end_line": end_line,
        }
    elif tool_name == "search_text":
        arguments = {"query": _ask_non_empty(input_fn, "请输入搜索关键词：")}

    request = {
        "id": f"call-{uuid.uuid4().hex[:8]}",
        "name": tool_name,
        "arguments": arguments,
    }
    return json.dumps(request, ensure_ascii=False)


def format_event_summary(events: list[dict]) -> str:
    """把事件列表转换成适合终端快速阅读的摘要。"""
    # 事件的完整数据仍然会在下面以 JSON 输出；这里只负责提供一条
    # 人类容易扫读的时间线，方便观察 Agent 走过了哪些步骤。
    if not isinstance(events, list) or not events:
        return "事件摘要：\n（没有事件）"

    lines = ["事件摘要："]
    for event in events:
        event_name = event.get("event", "unknown")

        if event_name == "run_started":
            lines.append(f"[run_started] {event.get('run_id', '-')}")
        elif event_name == "model_started":
            lines.append(
                f"[model_started] request={event.get('request_number', '-')}"
            )
        elif event_name == "model_finished":
            lines.append(
                f"[model_finished] status={event.get('status', '-')} "
                f"duration={event.get('duration_ms', 0):.2f}ms"
            )
        elif event_name == "tool_started":
            lines.append(
                f"[tool_started] {event.get('tool_name', '-')} "
                f"call={event.get('tool_call_id', '-')}"
            )
        elif event_name == "tool_finished":
            lines.append(
                f"[tool_finished] {event.get('tool_name', '-')} "
                f"status={event.get('status', '-')} "
                f"duration={event.get('duration_ms', 0):.2f}ms"
            )
        elif event_name == "run_finished":
            lines.append(
                f"[run_finished] status={event.get('status', '-')} "
                f"duration={event.get('duration_ms', 0):.2f}ms"
            )
        else:
            # 如果以后新增事件类型，摘要仍然能显示它，而不是静默丢失。
            lines.append(f"[{event_name}]")

    return "\n".join(lines)


def format_write_proposal(proposal: dict) -> str:
    """把写入提议格式化成用户可以审核的文本。"""
    lines = [
        "写入提议：",
        f"proposal_id={proposal.get('proposal_id', '-')}",
        f"path={proposal.get('path', '-')}",
        f"status={proposal.get('status', '-')}",
        f"old_sha256={proposal.get('old_sha256', '-')}",
        f"new_sha256={proposal.get('new_sha256', '-')}",
        "差异：",
        proposal.get("diff", "（没有差异）"),
    ]
    return "\n".join(lines)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="只读代码仓库 Agent")
    parser.add_argument(
        "--real",
        action="store_true",
        help="明确启用 DeepSeek 真实模型；默认使用离线模拟模型",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"真实模式使用的模型，默认是 {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--max-model-requests",
        type=int,
        default=DEFAULT_MAX_MODEL_REQUESTS,
        help=f"最多请求模型的次数，默认是 {DEFAULT_MAX_MODEL_REQUESTS}",
    )
    parser.add_argument(
        "--max-tool-executions",
        type=int,
        default=DEFAULT_MAX_TOOL_EXECUTIONS,
        help=f"最多执行工具的次数，默认是 {DEFAULT_MAX_TOOL_EXECUTIONS}",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="进入可测试的终端交互模式；默认仍只处理一个问题",
    )
    parser.add_argument(
        "--session",
        help="交互模式使用的 JSONL 会话文件；不提供时 /resume 不可用",
    )
    args = parser.parse_args(argv)

    mode = "real" if args.real else "mock"
    try:
        client = build_model_client(mode, args.model)
    except (ImportError, ValueError) as exc:
        print(f"无法启动 {mode} 模式：{exc}")
        return

    # 在模型调用前显示关键运行配置，方便确认是否真的打开了 real 模式。
    print(
        f"运行模式：{mode}；模型：{args.model if mode == 'real' else 'mock'}；"
        f"模型预算：{args.max_model_requests}；工具预算：{args.max_tool_executions}"
    )
    print("main.py 已启动")

    if args.interactive:
        # UI 只负责输入和展示；真正的 Agent 循环仍由 run_agent 执行。
        session_store = SessionStore(args.session) if args.session else None
        run_repl(
            session_store=session_store,
            run_kwargs={
                "model_client": client,
                "max_model_requests": args.max_model_requests,
                "max_tool_executions": args.max_tool_executions,
            },
            recover_kwargs={"model_client": client},
        )
        return

    question = input("请输入你想查询的问题：").strip()
    result = run_agent(
        question,
        model_client=client,
        max_model_requests=args.max_model_requests,
        max_tool_executions=args.max_tool_executions,
    )

    # 先显示简短事件时间线，再显示完整历史和事件 JSON，兼顾观察和调试。
    print("\n" + format_event_summary(result.get("events", [])))
    print("\nAgent 执行结果：")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
