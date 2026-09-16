"""命令行入口：收集用户参数，生成 JSON，再统一分发工具调用。"""

import json
import uuid

from agent import run_agent
# 保留第 3 关的导出，方便旧的手动分发测试继续使用。
from tools import dispatch_tool


TOOL_NAMES = {
    "1": "read_file",
    "2": "list_files",
    "3": "search_text",
}


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


def main() -> None:
    print("main.py 已启动")
    question = input("请输入你想查询的问题：").strip()
    result = run_agent(question)
    print("\nAgent 执行结果：")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
