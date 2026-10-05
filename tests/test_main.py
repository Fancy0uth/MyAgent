import unittest
from unittest.mock import patch

import main


class MainModeTests(unittest.TestCase):
    def test_default_mode_builds_mock_client(self):
        client = main.build_model_client("mock", "deepseek-flash")

        self.assertEqual(client.__class__.__name__, "MockModelClient")

    def test_real_mode_builds_deepseek_client_explicitly(self):
        with patch("main.DeepSeekModelClient") as constructor:
            client = main.build_model_client("real", "deepseek-flash")

        constructor.assert_called_once_with(model="deepseek-flash")
        self.assertIs(client, constructor.return_value)


class EventSummaryTests(unittest.TestCase):
    def test_format_event_summary_is_readable(self):
        summary = main.format_event_summary(
            [
                {"event": "run_started", "run_id": "run-001"},
                {"event": "model_started", "request_number": 1},
                {
                    "event": "tool_finished",
                    "tool_name": "read_file",
                    "status": "completed",
                    "duration_ms": 0.25,
                },
                {
                    "event": "run_finished",
                    "status": "completed",
                    "duration_ms": 1.5,
                },
            ]
        )

        self.assertIn("事件摘要：", summary)
        self.assertIn("[run_started] run-001", summary)
        self.assertIn("[model_started] request=1", summary)
        self.assertIn(
            "[tool_finished] read_file status=completed duration=0.25ms",
            summary,
        )
        self.assertIn(
            "[run_finished] status=completed duration=1.50ms",
            summary,
        )


class WriteProposalDisplayTests(unittest.TestCase):
    def test_format_write_proposal_shows_review_information(self):
        summary = main.format_write_proposal(
            {
                "proposal_id": "proposal-001",
                "path": "buggy_original.py",
                "status": "pending",
                "old_sha256": "old-hash",
                "new_sha256": "new-hash",
                "diff": "-return 1\n+return 2\n",
            }
        )

        self.assertIn("proposal-001", summary)
        self.assertIn("buggy_original.py", summary)
        self.assertIn("status=pending", summary)
        self.assertIn("old-hash", summary)
        self.assertIn("-return 1", summary)
        self.assertIn("+return 2", summary)


if __name__ == "__main__":
    unittest.main()
