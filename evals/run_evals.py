"""读取评测任务，并检查 Agent 的回答。"""

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# 因为本文件在 evals/ 目录下，需要把项目根目录加入模块搜索路径。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agent import run_agent
from model_client import DeepSeekModelClient, MockModelClient


TASKS_FILE = Path(__file__).with_name("tasks.jsonl")

# 运行记录属于本地生成物，和评测题目分开保存。
RUNS_DIR = PROJECT_ROOT / "runs"
RUN_LOG = RUNS_DIR / "eval_runs.jsonl"


def append_run_record(
    record: dict,
    log_path: Optional[Path] = None,
) -> None:
    """向 JSONL 记录文件追加一条记录，不覆盖以前的运行结果。"""
    # 默认路径在调用时读取，这样测试或其他调用方可以传入临时路径。
    if log_path is None:
        log_path = RUN_LOG

    # parents=True 允许第一次运行时自动创建 runs/ 目录。
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as file:
        # JSONL 的规则是“一行一个 JSON 对象”，因此每次写完都换行。
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_tasks(path: Path) -> list[dict]:
    """读取 JSONL 文件，每一行转换成一个字典。"""
    tasks = []

    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue

            try:
                task = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"第 {line_number} 行不是合法 JSON"
                ) from exc

            if not isinstance(task, dict):
                raise ValueError(f"第 {line_number} 行必须是 JSON 对象")

            tasks.append(task)

    return tasks


def evaluate_answer(task: dict, result: dict) -> tuple[bool, str]:
    """检查一次 Agent 结果是否符合评测任务要求。"""
    if result.get("status") != "completed":
        return False, f"Agent 状态是 {result.get('status')}"

    answer = result.get("answer")
    if not isinstance(answer, str):
        return False, "Agent 没有返回文本答案"

    # expected_contains 中的内容必须全部出现。
    required_words = task.get("expected_contains", [])
    missing_words = [
        word for word in required_words
        if word not in answer
    ]

    if missing_words:
        return False, f"缺少内容：{missing_words}"

    # expected_any 中只要出现一种即可。
    alternative_words = task.get("expected_any", [])
    if alternative_words and not any(
        word in answer for word in alternative_words
    ):
        return False, f"没有出现任何一个候选内容：{alternative_words}"

    return True, "回答符合预期"


def create_client(use_real_model: bool):
    """根据命令行参数选择模拟模型或真实模型。"""
    if use_real_model:
        return DeepSeekModelClient()

    return MockModelClient()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="运行 Agent 评测任务"
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="使用真实 DeepSeek 模型，会产生 API 消耗",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="只运行前几条任务，例如 --limit 2",
    )
    args = parser.parse_args()

    tasks = load_tasks(TASKS_FILE)

    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit 必须大于或等于 1")
        tasks = tasks[:args.limit]

    client = create_client(args.real)
    mode = "real" if args.real else "mock"
    model_name = getattr(client, "model", "mock")
    run_id = (
        "run-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:6]
    )

    passed = 0

    print(f"评测模式：{mode}")
    print(f"模型：{model_name}")
    print(f"任务数量：{len(tasks)}")
    print(f"运行记录：{RUN_LOG}")
    print()

    for task in tasks:
        task_id = task.get("id", "unknown")
        question = task.get("question", "")

        # perf_counter 适合测量耗时，因为它不受系统时钟调整影响。
        started_at = time.perf_counter()
        result = run_agent(
            question,
            model_client=client,
        )
        duration_ms = round(
            (time.perf_counter() - started_at) * 1000,
            2,
        )

        ok, message = evaluate_answer(task, result)

        # 这里保存“摘要”而不是完整 history，避免记录文件泄露资料内容。
        record = {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "model": model_name,
            "task_id": task_id,
            "event": "evaluation_task",
            "status": result.get("status"),
            "passed": ok,
            "model_requests": result.get("model_requests", 0),
            "tool_executions": result.get("tool_executions", 0),
            "duration_ms": duration_ms,
        }
        if result.get("error") is not None:
            record["error"] = result["error"]

        append_run_record(record)

        if ok:
            passed += 1
            print(f"[PASS] {task_id} - {message}")
        else:
            print(f"[FAIL] {task_id} - {message}")
            print(f"       问题：{question}")
            print(f"       回答：{result.get('answer')}")

    print()
    print(f"结果：{passed}/{len(tasks)} 通过")

    if passed != len(tasks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
