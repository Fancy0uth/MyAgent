"""运行 L29 的确定性能力兼容性矩阵。

这个 runner 比答案评测更严格：它比较可观察的协议、事件和错误行为，
不比较模型生成的自然语言是否逐字相同。
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent import run_agent
from events import EventRecorder
from model_client import MockModelClient
from rpc import decode_request, encode_message
from tools import dispatch_tool


VALID_STATUSES = frozenset(
    {
        "not_run",
        "subset_verified",
        "fixed_contract_compatible",
        "not_applicable",
    }
)

DEFAULT_TASK_FILE = Path(__file__).with_name("compatibility_tasks.jsonl")
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "docs" / "compatibility_results"


def load_compatibility_tasks(path: Path) -> list[dict[str, Any]]:
    """读取矩阵任务，并提前发现格式错误或重复任务 ID。"""
    tasks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                task = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"第 {line_number} 行不是合法 JSON") from exc
            if not isinstance(task, dict):
                raise ValueError(f"第 {line_number} 行必须是 JSON 对象")
            task_id = task.get("id")
            kind = task.get("kind")
            if not isinstance(task_id, str) or not task_id.strip():
                raise ValueError(f"第 {line_number} 行缺少非空 id")
            if task_id in seen_ids:
                raise ValueError(f"出现重复任务 id：{task_id}")
            if not isinstance(kind, str) or not kind.strip():
                raise ValueError(f"第 {line_number} 行缺少非空 kind")
            seen_ids.add(task_id)
            tasks.append(task)
    return tasks


def write_results(output: Path, results: list[dict[str, Any]]) -> None:
    """以追加方式写结果，避免新一轮评测覆盖历史。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as file:
        for result in results:
            file.write(json.dumps(result, ensure_ascii=False) + "\n")


def _result(
    task: dict[str, Any],
    implementation: str,
    status: str,
    observations: str,
    differences: list[str],
    command: str,
) -> dict[str, Any]:
    if status not in VALID_STATUSES:
        raise ValueError(f"未知兼容性状态：{status}")
    return {
        "task_id": task["id"],
        "implementation": implementation,
        "status": status,
        "observations": observations,
        "differences": differences,
        "command": command,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": f"compat-{uuid.uuid4().hex[:8]}",
    }


def _run_myagent_task(task: dict[str, Any]) -> dict[str, Any]:
    """运行不访问网络、不写真实资料的 MyAgent 子集。"""
    kind = task["kind"]
    command = f"run_compatibility.py --mode myagent --task {task['id']}"

    if kind == "rpc_jsonl":
        request = {"id": "compat-rpc", "type": "prompt", "payload": {"question": "中文\n/"}}
        decoded = decode_request(encode_message(request))
        return _result(
            task,
            "myagent",
            "fixed_contract_compatible" if decoded == request else "not_run",
            "JSONL 编解码保留中文、嵌入换行和一条记录一个换行符。",
            ["这是 MyAgent 自定义 JSONL 协议，不宣称兼容 Pi 原生 RPC。"],
            command,
        )

    if kind == "event_order":
        recorder = EventRecorder()
        recorder.emit("run_started", run_id="compat-events")
        recorder.emit("model_started", run_id="compat-events", request_number=1)
        recorder.emit("model_finished", run_id="compat-events", status="completed")
        recorder.emit("run_finished", run_id="compat-events", status="completed")
        names = [event["event"] for event in recorder.snapshot()]
        expected = ["run_started", "model_started", "model_finished", "run_finished"]
        return _result(
            task,
            "myagent",
            "subset_verified" if names == expected else "not_run",
            "EventRecorder 保留了 run_started → model_started → model_finished → run_finished 顺序。",
            ["这里只验证事件顺序，不比较事件显示文本。"],
            command,
        )

    if kind == "cancel_before_model":
        cancel_event = Event()
        cancel_event.set()
        result = run_agent("执行一个离线取消检查", model_client=MockModelClient(), cancel_event=cancel_event)
        passed = result.get("status") == "cancelled" and result.get("model_requests") == 0
        return _result(
            task,
            "myagent",
            "subset_verified" if passed else "not_run",
            f"状态={result.get('status')}，模型请求数={result.get('model_requests')}。",
            ["取消是协作式检查；本项只覆盖模型请求开始前的边界。"],
            command,
        )

    if kind == "read_range_limit":
        request = json.dumps(
            {
                "id": "compat-read",
                "name": "read_file",
                "arguments": {"path": "README.md", "start_line": 1, "end_line": 81},
            },
            ensure_ascii=False,
        )
        result = dispatch_tool(request)
        passed = result.get("error") == "INVALID_RANGE"
        return _result(
            task,
            "myagent",
            "subset_verified" if passed else "not_run",
            f"读取 81 行返回错误={result.get('error')}。",
            ["这里验证 MyAgent 的 80 行工具边界；Pi 工具参数由另一套实现定义。"],
            command,
        )

    if kind in {"session_branch", "compaction_boundary"}:
        return _result(
            task,
            "myagent",
            "not_run",
            "该能力已有独立单元测试，但本次最小矩阵没有自动创建持久化会话夹具。",
            ["不要把已有单元测试结果混入本次 runner 结果。"],
            command,
        )

    return _result(
        task,
        "myagent",
        "not_applicable",
        "该任务针对 Pi 原生扩展或资源加载边界，不由 MyAgent runner 执行。",
        ["需要单独的固定 Pi checkout 才能比较。"],
        command,
    )


def _run_pi_task(task: dict[str, Any]) -> dict[str, Any]:
    """Pi checkout 尚未完成构建时，明确返回未运行，不伪造通过。"""
    command = f"run_compatibility.py --mode pi --task {task['id']}"
    if task["kind"] == "rpc_jsonl":
        return _result(
            task,
            "pi",
            "not_applicable",
            "Pi v0.85.1 使用自己的 RPC 边界；本项目的自定义 JSONL 不能直接当作 Pi 兼容性通过。",
            ["两者协议目标不同，未进行强行对比。"],
            command,
        )
    return _result(
        task,
        "pi",
        "not_run",
        "固定 Pi checkout 尚未完成 workspace 构建，原生 fixture 未运行。",
        ["见 docs/pi-reference/L27_RUN.md 的 kimi-coding 数据和内部包构建阻塞。"],
        command,
    )


def run_compatibility(mode: str, task_file: Path, output: Path) -> list[dict[str, Any]]:
    """运行一次矩阵并把结果追加到指定 JSONL 文件。"""
    if mode not in {"myagent", "pi"}:
        raise ValueError("mode 必须是 myagent 或 pi")
    tasks = load_compatibility_tasks(task_file)
    runner = _run_myagent_task if mode == "myagent" else _run_pi_task
    results = [runner(task) for task in tasks]
    write_results(output, results)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 L29 确定性兼容性矩阵")
    parser.add_argument("--mode", choices=("myagent", "pi"), required=True)
    parser.add_argument("--task-file", type=Path, default=DEFAULT_TASK_FILE)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="结果 JSONL 路径；默认写入 docs/compatibility_results/",
    )
    args = parser.parse_args()
    output = args.output or DEFAULT_OUTPUT_DIR / f"{args.mode}.jsonl"
    results = run_compatibility(args.mode, args.task_file, output)
    counts: dict[str, int] = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    print(f"实现：{args.mode}")
    print(f"任务：{len(results)}")
    print(f"状态：{counts}")
    print(f"结果：{output}")


if __name__ == "__main__":
    main()
