"""模型客户端：离线模拟客户端和 DeepSeek 真实客户端。"""

import json
import os
from typing import Any, Dict, List


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出允许读取的资料文件。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取允许文件的指定行范围。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对文件路径"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                "required": ["path", "start_line", "end_line"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_text",
            "description": "在允许读取的资料文件中搜索字面文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                },
                "required": ["query"],
            },
        },
    },
]


# 这是给真实模型看的“工作说明”：模型知道什么时候应该调用工具，
# 也知道最终答案必须引用工具实际读到的文件和行号。
SYSTEM_PROMPT = """
你是一个只读代码仓库助手。
只能通过提供的工具获取资料，不能猜测没有读取到的内容，也不能执行代码或修改文件。
当需要了解仓库内容时，先调用合适的工具：list_files、search_text 或 read_file。
最终回答必须根据工具返回的真实内容，并使用相对路径和行号引用依据，例如 README.md:3。
如果资料不足，请明确说明资料不足，不要编造答案。
""".strip()


class ModelClientError(Exception):
    """模型请求或模型响应错误，并携带可供 Agent 判断的错误码。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class MockModelClient:
    """按历史记录返回固定的工具调用和最终回答。"""

    def _assistant_called(self, messages, call_id: str, name: str) -> bool:
        for message in messages:
            if message.get("role") != "assistant":
                continue
            call = message.get("tool_call")
            if (
                isinstance(call, dict)
                and call.get("id") == call_id
                and call.get("name") == name
            ):
                return True
        return False

    def _tool_result(self, messages, call_id: str):
        for message in messages:
            if (
                message.get("role") == "tool"
                and message.get("tool_call_id") == call_id
            ):
                try:
                    return json.loads(message["content"])
                except (KeyError, TypeError, json.JSONDecodeError) as exc:
                    raise ValueError("工具结果不是合法的 JSON") from exc
        return None

    def respond(self, messages):
        if not isinstance(messages, list) or not messages:
            raise ValueError("模型需要非空的消息历史")

        list_result = self._tool_result(messages, "mock-call-1")
        if list_result is None:
            return {
                "type": "tool_call",
                "id": "mock-call-1",
                "name": "list_files",
                "arguments": {},
            }

        if not self._assistant_called(messages, "mock-call-1", "list_files"):
            raise ValueError("缺少 list_files 的 assistant 工具调用记录")
        list_data = list_result.get("result", {})
        if not list_result.get("ok") or "README.md" not in list_data.get("files", []):
            raise ValueError("list_files 没有返回预期的 README.md")

        read_result = self._tool_result(messages, "mock-call-2")
        if read_result is None:
            return {
                "type": "tool_call",
                "id": "mock-call-2",
                "name": "read_file",
                "arguments": {
                    "path": "README.md",
                    "start_line": 1,
                    "end_line": 3,
                },
            }

        if not self._assistant_called(messages, "mock-call-2", "read_file"):
            raise ValueError("缺少 read_file 的 assistant 工具调用记录")
        content = read_result.get("result", {}).get("content", "")
        if not read_result.get("ok") or "3: " not in content:
            raise ValueError("read_file 没有返回带行号的 README 内容")

        return {
            "type": "final",
            "content": "根据读取到的资料，相关信息位于 README.md:3。",
        }


class DeepSeekModelClient:
    """通过 OpenAI 兼容接口调用 DeepSeek，并转换为 Agent 内部格式。"""

    def __init__(self, model: str = "deepseek-flash", api_client: Any = None):
        if not isinstance(model, str) or not model.strip():
            raise ValueError("模型名称必须是非空字符串")

        self.model = model.strip()
        if api_client is not None:
            self.client = api_client
            return

        # 只有明确创建真实客户端时才读取 .env、检查密钥和导入 SDK。
        from dotenv import load_dotenv
        from openai import OpenAI

        load_dotenv()
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError(
                "未读取到 DEEPSEEK_API_KEY，请在项目根目录的 .env 文件中设置。"
            )

        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
            # 关闭 SDK 的隐藏重试，让 Agent 的请求预算可以准确统计。
            max_retries=0,
        )

    @staticmethod
    def _to_api_messages(messages: List[dict]) -> List[dict]:
        """将 Agent 的内部历史转换为 Chat Completions 消息格式。"""
        if not isinstance(messages, list) or not messages:
            raise ValueError("模型需要非空的消息历史")

        # Agent 内部只保存用户问题和工具历史；每次请求都补充系统说明。
        # 这样 MockModelClient 不需要依赖这段提示词，两个客户端仍然可以替换。
        has_system_message = any(
            isinstance(message, dict) and message.get("role") == "system"
            for message in messages
        )
        api_messages = [] if has_system_message else [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

        for message in messages:
            if not isinstance(message, dict):
                raise ValueError("消息历史中的每一项必须是对象")

            role = message.get("role")
            if role in {"user", "system"}:
                api_messages.append(
                    {"role": role, "content": message.get("content", "")}
                )
                continue

            if role == "assistant":
                tool_calls = message.get("tool_calls")
                if tool_calls is None and message.get("tool_call") is not None:
                    # 第 4 关的历史用 tool_call 表示单个调用；这里兼容旧格式。
                    tool_calls = [message["tool_call"]]

                if tool_calls is None:
                    api_messages.append(
                        {"role": "assistant", "content": message.get("content")}
                    )
                    continue

                if not isinstance(tool_calls, list) or not tool_calls:
                    raise ValueError("assistant 工具调用记录格式无效")

                api_tool_calls = []
                for tool_call in tool_calls:
                    if not isinstance(tool_call, dict):
                        raise ValueError("assistant 工具调用记录格式无效")
                    call_id = tool_call.get("id")
                    name = tool_call.get("name")
                    arguments = tool_call.get("arguments")
                    if (
                        not isinstance(call_id, str)
                        or not call_id.strip()
                        or not isinstance(name, str)
                        or not name.strip()
                        or not isinstance(arguments, dict)
                    ):
                        raise ValueError("assistant 工具调用缺少有效字段")

                    api_tool_calls.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(
                                    arguments,
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    )

                api_messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": api_tool_calls,
                    }
                )
                continue

            if role == "tool":
                call_id = message.get("tool_call_id")
                if not isinstance(call_id, str) or not call_id.strip():
                    raise ValueError("tool 消息缺少 tool_call_id")
                api_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": message.get("content", ""),
                    }
                )
                continue

            raise ValueError(f"不支持的消息角色：{role}")

        return api_messages

    @staticmethod
    def _normalize_one_tool_call(tool_call: Any) -> Dict[str, Any]:
        call_id = getattr(tool_call, "id", None)
        function = getattr(tool_call, "function", None)
        name = getattr(function, "name", None)
        raw_arguments = getattr(function, "arguments", None)
        if (
            not isinstance(call_id, str)
            or not call_id.strip()
            or not isinstance(name, str)
            or not name.strip()
        ):
            raise ValueError("模型工具调用缺少有效的 id 或 name")

        if isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                raise ValueError("模型工具参数不是合法的 JSON") from exc
        else:
            arguments = raw_arguments

        if not isinstance(arguments, dict):
            raise ValueError("模型工具参数必须是 JSON 对象")

        return {
            "id": call_id,
            "name": name,
            "arguments": arguments,
        }

    @classmethod
    def _normalize_tool_calls(cls, tool_calls: Any) -> Dict[str, Any]:
        if not isinstance(tool_calls, list) or not tool_calls:
            raise ValueError("模型工具调用格式无效")

        normalized_calls = [
            cls._normalize_one_tool_call(tool_call)
            for tool_call in tool_calls
        ]
        if len(normalized_calls) == 1:
            # 保留单工具调用的旧格式，Agent 因此可以兼容第 4 关代码。
            return {"type": "tool_call", **normalized_calls[0]}
        return {"type": "tool_calls", "calls": normalized_calls}

    @staticmethod
    def _api_error_code(error: Exception) -> str:
        # 使用 SDK 异常类名分类，不把异常原文（可能包含请求细节）直接输出。
        return {
            "APITimeoutError": "MODEL_TIMEOUT",
            "APIConnectionError": "MODEL_CONNECTION_ERROR",
            "AuthenticationError": "AUTHENTICATION_ERROR",
            "RateLimitError": "RATE_LIMITED",
            "BadRequestError": "INVALID_MODEL_REQUEST",
        }.get(error.__class__.__name__, "MODEL_API_ERROR")

    def respond(self, messages: List[dict]) -> Dict[str, Any]:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self._to_api_messages(messages),
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                # 第 5 关先使用非流式请求，逻辑更容易观察和调试。
                stream=False,
            )
        except Exception as exc:
            error_code = self._api_error_code(exc)
            messages_by_code = {
                "MODEL_TIMEOUT": "DeepSeek 请求超时",
                "MODEL_CONNECTION_ERROR": "无法连接 DeepSeek",
                "AUTHENTICATION_ERROR": "DeepSeek API Key 鉴权失败",
                "RATE_LIMITED": "DeepSeek 请求过于频繁或额度不足",
                "INVALID_MODEL_REQUEST": "DeepSeek 拒绝了请求参数",
            }
            raise ModelClientError(
                error_code,
                messages_by_code.get(error_code, "DeepSeek 请求失败"),
            ) from exc

        try:
            choice = response.choices[0]
            message = choice.message
        except (AttributeError, IndexError, TypeError) as exc:
            raise ModelClientError(
                "INVALID_MODEL_RESPONSE",
                "DeepSeek 返回中缺少有效的 choices[0].message",
            ) from exc

        if getattr(choice, "finish_reason", None) == "length":
            raise ModelClientError("OUTPUT_TRUNCATED", "DeepSeek 输出被长度限制截断")

        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            try:
                return self._normalize_tool_calls(tool_calls)
            except ValueError as exc:
                raise ModelClientError("INVALID_MODEL_RESPONSE", str(exc)) from exc

        refusal = getattr(message, "refusal", None)
        if refusal:
            raise ModelClientError("MODEL_REFUSAL", "DeepSeek 拒绝生成该回答")

        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise ModelClientError(
                "INVALID_MODEL_RESPONSE",
                "DeepSeek 没有返回文本回答或工具调用",
            )
        return {"type": "final", "content": content}
