"""L09 的最小事件记录器：先定义事件格式，再接入 Agent 循环。"""

from typing import Any, Callable, Dict, List


# 事件名称由程序固定，避免调用方随意写出无法统一处理的名称。
EVENT_NAMES = frozenset(
    {
        "run_started",
        "model_started",
        "model_finished",
        "model_stream_started",
        "model_text_delta",
        "model_stream_finished",
        "tool_started",
        "tool_blocked",
        "tool_finished",
        "run_finished",
    }
)

TOOL_EVENT_NAMES = frozenset(
    {"tool_started", "tool_blocked", "tool_finished"}
)


class EventRecorder:
    """校验并按发生顺序保存 Agent 事件。"""

    def __init__(self) -> None:
        self._events: List[dict] = []
        self._listeners: Dict[str, List[Callable[[dict], None]]] = {}

    def subscribe(self, event: str, callback: Callable[[dict], None]) -> None:
        """注册同步事件监听器；监听器异常会继续向调用方传播。"""
        if event not in EVENT_NAMES:
            raise ValueError(f"未知事件名称：{event}")
        if not callable(callback):
            raise ValueError("事件回调必须是可调用对象")
        self._listeners.setdefault(event, []).append(callback)

    def emit(
        self,
        event: str,
        run_id: str,
        **fields: Any,
    ) -> Dict[str, Any]:
        """记录一个事件，并返回本次保存的事件副本。"""
        if event not in EVENT_NAMES:
            raise ValueError(f"未知事件名称：{event}")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id 必须是非空字符串")

        if event in TOOL_EVENT_NAMES:
            # 工具事件必须能定位到具体工具调用，后面才能关联工具结果。
            tool_call_id = fields.get("tool_call_id")
            tool_name = fields.get("tool_name")
            if (
                not isinstance(tool_call_id, str)
                or not tool_call_id.strip()
                or not isinstance(tool_name, str)
                or not tool_name.strip()
            ):
                raise ValueError(
                    "工具事件必须包含非空的 tool_call_id 和 tool_name"
                )

        record: Dict[str, Any] = {
            "event": event,
            "run_id": run_id,
            **fields,
        }
        self._events.append(record)
        # 回调在事件写入后、下一步敏感操作前同步执行；不吞掉异常，
        # 这样 tool_started 的回调失败时，Agent 不会继续分发工具。
        for callback in self._listeners.get(event, []):
            callback(dict(record))
        return dict(record)

    def snapshot(self) -> List[Dict[str, Any]]:
        """返回当前事件列表的副本，避免外部直接改写内部记录。"""
        return [dict(event) for event in self._events]
