"""L24：显式注册扩展、事件监听和工具执行前检查。"""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Optional

import model_client
import tools
from events import EVENT_NAMES, EventRecorder
from policy import is_protected_test_path


EventCallback = Callable[[dict], None]
BeforeToolHook = Callable[[dict], Optional[dict]]
Tool = Callable[..., dict]


class ExtensionError(ValueError):
    """扩展注册或执行失败时抛出的稳定错误。"""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


class ExtensionManager:
    """只管理调用方显式注册的扩展，不自动导入任何文件。"""

    def __init__(self) -> None:
        self._event_callbacks: Dict[str, List[EventCallback]] = {}
        self._before_tool_hooks: List[BeforeToolHook] = []
        self._tools: Dict[str, Tool] = {}
        self._tool_definitions: Dict[str, dict] = {}

    def subscribe(self, event: str, callback: EventCallback) -> None:
        """订阅固定事件名称；事件回调由 EventRecorder 同步调用。"""
        if event not in EVENT_NAMES:
            raise ExtensionError("UNKNOWN_EVENT", f"未知事件：{event}")
        if not callable(callback):
            raise ExtensionError("INVALID_CALLBACK", "事件回调必须是可调用对象")
        self._event_callbacks.setdefault(event, []).append(callback)

    def attach(self, recorder: EventRecorder) -> None:
        """把当前已注册的监听器接到一个事件记录器上。"""
        if not isinstance(recorder, EventRecorder):
            raise ExtensionError("INVALID_RECORDER", "recorder 必须是 EventRecorder")
        for event, callbacks in self._event_callbacks.items():
            for callback in callbacks:
                recorder.subscribe(event, callback)

    def register_before_tool(self, hook: BeforeToolHook) -> None:
        """注册工具执行前检查；返回 None 表示允许，返回错误对象表示拒绝。"""
        if not callable(hook):
            raise ExtensionError("INVALID_CALLBACK", "before_tool 必须是可调用对象")
        self._before_tool_hooks.append(hook)

    def before_tool(self, call: dict) -> Optional[dict]:
        """依次执行检查器，任何一个拒绝都会阻止这次工具分发。"""
        if not isinstance(call, dict):
            raise ExtensionError("INVALID_TOOL_CALL", "工具调用必须是对象")
        for hook in self._before_tool_hooks:
            try:
                decision = hook(copy.deepcopy(call))
            except Exception as exc:
                raise ExtensionError("EXTENSION_ERROR", str(exc)) from exc
            if decision is None:
                continue
            if not isinstance(decision, dict):
                raise ExtensionError(
                    "INVALID_BEFORE_TOOL_RESULT",
                    "before_tool 必须返回 None 或错误对象",
                )
            error = decision.get("error")
            message = decision.get("message")
            if (
                not isinstance(error, str)
                or not error.strip()
                or not isinstance(message, str)
                or not message.strip()
            ):
                raise ExtensionError(
                    "INVALID_BEFORE_TOOL_RESULT",
                    "拒绝结果必须包含非空 error 和 message",
                )
            return {
                "ok": False,
                "error": error,
                "message": message,
            }
        return None

    def register_read_only_tool(
        self,
        name: str,
        handler: Tool,
        definition: dict,
    ) -> None:
        """显式注册一个额外的纯只读工具，并同步提供模型工具定义。"""
        if not isinstance(name, str) or not name.strip():
            raise ExtensionError("INVALID_TOOL", "工具名称必须是非空字符串")
        if not callable(handler):
            raise ExtensionError("INVALID_TOOL", "工具处理函数必须是可调用对象")
        if name in tools.TOOL_REGISTRY or name in self._tools:
            raise ExtensionError("DUPLICATE_TOOL", f"工具已经存在：{name}")
        if not isinstance(definition, dict):
            raise ExtensionError("INVALID_TOOL_DEFINITION", "工具定义必须是对象")
        function = definition.get("function")
        if (
            definition.get("type") != "function"
            or not isinstance(function, dict)
            or function.get("name") != name
        ):
            raise ExtensionError(
                "INVALID_TOOL_DEFINITION",
                "工具定义必须是与名称一致的 function 定义",
            )

        self._tools[name] = handler
        self._tool_definitions[name] = copy.deepcopy(definition)
        # DeepSeekModelClient 当前从共享 TOOL_DEFINITIONS 读取定义；显式注册
        # 时同步追加，close() 会撤销它，避免扩展永久污染后续运行。
        model_client.TOOL_DEFINITIONS.append(copy.deepcopy(definition))

    def tool_definitions(self) -> list[dict]:
        """返回基础工具和当前扩展工具定义的深拷贝。"""
        definitions = [
            copy.deepcopy(definition)
            for definition in model_client.TOOL_DEFINITIONS
            if definition["function"]["name"] not in self._tool_definitions
        ]
        definitions.extend(copy.deepcopy(list(self._tool_definitions.values())))
        return definitions

    def dispatch_tool(self, request_json: str) -> dict:
        """使用基础分发器执行工具，并额外提供当前扩展工具。"""
        return tools.dispatch_tool(request_json, extra_tools=self._tools)

    def close(self) -> None:
        """撤销当前管理器注册的模型定义；不会自动卸载其他扩展。"""
        extension_names = set(self._tool_definitions)
        model_client.TOOL_DEFINITIONS[:] = [
            definition
            for definition in model_client.TOOL_DEFINITIONS
            if definition.get("function", {}).get("name") not in extension_names
        ]
        self._tools.clear()
        self._tool_definitions.clear()


def protect_validation_files(call: dict) -> Optional[dict]:
    """示例扩展：提醒禁止编辑测试文件，但底层 policy 仍是最终防线。"""
    name = call.get("name")
    arguments = call.get("arguments")
    path = arguments.get("path") if isinstance(arguments, dict) else None
    if name in {"write_file", "edit_file"} and is_protected_test_path(path):
        return {
            "error": "PROTECTED_TEST_FILE",
            "message": "扩展禁止修改验收测试文件",
        }
    return None
