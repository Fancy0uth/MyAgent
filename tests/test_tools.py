import json
import unittest

from main import dispatch_tool, get_user_input


class InputAndDispatchTests(unittest.TestCase):
    def test_user_input_builds_read_file_json(self):
        answers = iter(["1", "README.md", "1", "3"])

        request_json = get_user_input(input_fn=lambda _prompt: next(answers))
        request = json.loads(request_json)

        self.assertEqual(request["name"], "read_file")
        self.assertEqual(
            request["arguments"],
            {"path": "README.md", "start_line": 1, "end_line": 3},
        )
        self.assertTrue(request["id"])

    def test_user_input_builds_list_files_json(self):
        answers = iter(["2"])

        request_json = get_user_input(input_fn=lambda _prompt: next(answers))
        request = json.loads(request_json)

        self.assertEqual(request["name"], "list_files")
        self.assertEqual(request["arguments"], {})

    def test_user_input_builds_search_text_json(self):
        answers = iter(["3", "SQLite"])

        request_json = get_user_input(input_fn=lambda _prompt: next(answers))
        request = json.loads(request_json)

        self.assertEqual(request["name"], "search_text")
        self.assertEqual(request["arguments"], {"query": "SQLite"})

    def test_dispatcher_runs_read_file_and_keeps_call_id(self):
        request_json = json.dumps(
            {
                "id": "call-001",
                "name": "read_file",
                "arguments": {
                    "path": "README.md",
                    "start_line": 1,
                    "end_line": 3,
                },
            }
        )

        result = dispatch_tool(request_json)

        self.assertTrue(result["ok"])
        self.assertEqual(result["call_id"], "call-001")
        self.assertIn("1: 项目名称", result["result"]["content"])

    def test_dispatcher_lists_files(self):
        request_json = json.dumps(
            {"id": "call-002", "name": "list_files", "arguments": {}}
        )

        result = dispatch_tool(request_json)

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["files"], ["README.md"])

    def test_dispatcher_searches_text(self):
        request_json = json.dumps(
            {
                "id": "call-003",
                "name": "search_text",
                "arguments": {"query": "SQLite"},
            }
        )

        result = dispatch_tool(request_json)

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["matches"][0]["path"], "README.md")
        self.assertEqual(result["result"]["matches"][0]["line"], 3)

    def test_dispatcher_rejects_unknown_tool(self):
        request_json = json.dumps(
            {
                "id": "call-004",
                "name": "delete_file",
                "arguments": {},
            }
        )

        result = dispatch_tool(request_json)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "UNKNOWN_TOOL")

    def test_dispatcher_rejects_invalid_json(self):
        result = dispatch_tool("不是 JSON")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "INVALID_ARGUMENT")

    def test_dispatcher_exposes_tool_error_at_top_level(self):
        request_json = json.dumps(
            {
                "id": "call-005",
                "name": "read_file",
                "arguments": {
                    "path": "README.md",
                    "start_line": 3,
                    "end_line": 1,
                },
            }
        )

        result = dispatch_tool(request_json)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "INVALID_RANGE")
        self.assertEqual(result["result"]["error"], "INVALID_RANGE")


if __name__ == "__main__":
    unittest.main()
