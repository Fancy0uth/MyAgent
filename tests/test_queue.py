"""L22：验证 Steering / Follow-up 队列和 Agent 的受控插入。"""

import threading
import unittest
from unittest.mock import patch

from agent import run_agent
from input_queue import InstructionQueue, InstructionQueueError


class InstructionQueueTests(unittest.TestCase):
    def test_messages_keep_fifo_order_and_kind_separation(self):
        queue = InstructionQueue()
        first = queue.submit("steering", "先查看 README")
        follow_up = queue.submit("follow_up", "完成后再总结")
        second = queue.submit("steering", "然后搜索 SQLite")

        self.assertEqual(queue.pop_next("steering")["id"], first["id"])
        self.assertEqual(queue.pop_next("steering")["id"], second["id"])
        self.assertEqual(queue.pending("follow_up")[0]["id"], follow_up["id"])
        self.assertEqual(queue.pending("steering"), [])

    def test_invalid_kind_and_empty_content_are_rejected(self):
        queue = InstructionQueue()

        with self.assertRaises(InstructionQueueError):
            queue.submit("unknown", "这不是允许的指令类型")
        with self.assertRaises(InstructionQueueError):
            queue.submit("steering", "   ")

    def test_steering_is_applied_after_the_current_tool_batch(self):
        queue = InstructionQueue()
        requests = []

        class ToolThenFinalClient:
            def respond(self, messages):
                requests.append(messages)
                if len(requests) == 1:
                    return {
                        "type": "tool_calls",
                        "calls": [
                            {
                                "id": "call-list",
                                "name": "list_files",
                                "arguments": {},
                            }
                        ],
                    }
                return {"type": "final", "content": "已完成"}

        def fake_dispatch(_request_json):
            # 这个注入发生在工具分发期间，模拟用户在长工具运行时发来消息。
            queue.submit("steering", "请额外说明 README 的用途")
            return {"ok": True, "result": {"files": ["README.md"]}}

        with patch("agent.dispatch_tool", side_effect=fake_dispatch):
            result = run_agent(
                "查看项目文件",
                model_client=ToolThenFinalClient(),
                instruction_queue=queue,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["tool_executions"], 1)
        self.assertEqual(queue.pending(), [])
        self.assertIn(
            "Steering 指令：请额外说明 README 的用途",
            requests[1][-1]["content"],
        )

    def test_follow_up_starts_one_more_model_round_after_final_answer(self):
        queue = InstructionQueue()
        queue.submit("follow_up", "再用一句话总结")
        requests = []

        class FinalClient:
            def respond(self, messages):
                requests.append(messages)
                return {"type": "final", "content": f"第 {len(requests)} 轮完成"}

        result = run_agent(
            "完成初始任务",
            model_client=FinalClient(),
            max_model_requests=2,
            instruction_queue=queue,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["model_requests"], 2)
        self.assertIn("Follow-up 指令：再用一句话总结", requests[1][-1]["content"])
        self.assertEqual(result["pending_instructions"], [])

    def test_pending_follow_up_is_returned_when_model_budget_is_exhausted(self):
        queue = InstructionQueue()
        queued = queue.submit("follow_up", "预算够时继续做")

        class FinalClient:
            def respond(self, _messages):
                return {"type": "final", "content": "第一轮完成"}

        result = run_agent(
            "完成任务",
            model_client=FinalClient(),
            max_model_requests=1,
            instruction_queue=queue,
        )

        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["reason"], "model_call_limit")
        self.assertEqual(result["pending_instructions"][0]["id"], queued["id"])

    def test_cancellation_returns_all_unconsumed_messages(self):
        queue = InstructionQueue()
        first = queue.submit("steering", "第一条")
        second = queue.submit("follow_up", "第二条")
        cancel_event = threading.Event()
        cancel_event.set()

        result = run_agent(
            "开始任务",
            model_client=unittest.mock.Mock(),
            cancel_event=cancel_event,
            instruction_queue=queue,
        )

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(
            [item["id"] for item in result["pending_instructions"]],
            [first["id"], second["id"]],
        )


if __name__ == "__main__":
    unittest.main()
