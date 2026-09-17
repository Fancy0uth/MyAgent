"""命令行入口：选择模型模式，收集问题并展示 Agent 结果。"""

import argparse
import json
import uuid

from agent import run_agent
from model_client import DeepSeekModelClient, MockModelClient
# 保留第 3 关的导出，方便旧的手动分发测试继续使用。
from tools import dispatch_tool


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
    question = input("请输入你想查询的问题：").strip()
    result = run_agent(
        question,
        model_client=client,
        max_model_requests=args.max_model_requests,
        max_tool_executions=args.max_tool_executions,
    )
    print("\nAgent 执行结果：")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
