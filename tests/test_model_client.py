import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

import model_client


class FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


class FakeOpenAIClient:
    def __init__(self, response):
        self.chat = SimpleNamespace(
            completions=FakeCompletions(response),
        )


class ModelClientTests(unittest.TestCase):
    def test_mock_client_import_does_not_require_api_key(self):
        repo_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        clean_environment = os.environ.copy()
        clean_environment.pop("DEEPSEEK_API_KEY", None)
        clean_environment["PYTHONPATH"] = repo_path

        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from model_client import MockModelClient; MockModelClient(); print('ok')",
                ],
                cwd=directory,
                env=clean_environment,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")

    def test_deepseek_client_normalizes_tool_call_response(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                id="call-123",
                                function=SimpleNamespace(
                                    name="read_file",
                                    arguments='{"path": "README.md", "start_line": 1, "end_line": 3}',
                                ),
                            )
                        ],
                    )
                )
            ]
        )
        fake_client = FakeOpenAIClient(response)
        client_class = getattr(model_client, "DeepSeekModelClient", None)
        self.assertIsNotNone(client_class, "model_client.py 应提供 DeepSeekModelClient")

        client = client_class(api_client=fake_client)
        result = client.respond([{"role": "user", "content": "读取 README"}])

        self.assertEqual(
            result,
            {
                "type": "tool_call",
                "id": "call-123",
                "name": "read_file",
                "arguments": {
                    "path": "README.md",
                    "start_line": 1,
                    "end_line": 3,
                },
            },
        )
        request = fake_client.chat.completions.kwargs
        self.assertEqual(request["messages"][1], {"role": "user", "content": "读取 README"})
        self.assertEqual(request["messages"][0]["role"], "system")
        self.assertFalse(request["stream"])
        self.assertEqual(request["tool_choice"], "auto")

    def test_deepseek_client_normalizes_multiple_tool_calls(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                id="call-list",
                                function=SimpleNamespace(
                                    name="list_files",
                                    arguments="{}",
                                ),
                            ),
                            SimpleNamespace(
                                id="call-search",
                                function=SimpleNamespace(
                                    name="search_text",
                                    arguments='{"query": "SQLite"}',
                                ),
                            ),
                        ],
                    )
                )
            ]
        )
        client_class = getattr(model_client, "DeepSeekModelClient", None)
        self.assertIsNotNone(client_class)

        client = client_class(api_client=FakeOpenAIClient(response))

        self.assertEqual(
            client.respond([{"role": "user", "content": "查资料"}]),
            {
                "type": "tool_calls",
                "calls": [
                    {
                        "id": "call-list",
                        "name": "list_files",
                        "arguments": {},
                    },
                    {
                        "id": "call-search",
                        "name": "search_text",
                        "arguments": {"query": "SQLite"},
                    },
                ],
            },
        )

    def test_deepseek_client_converts_internal_tool_history(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="已读取工具结果。",
                        tool_calls=None,
                    )
                )
            ]
        )
        fake_client = FakeOpenAIClient(response)
        client_class = getattr(model_client, "DeepSeekModelClient", None)
        self.assertIsNotNone(client_class)
        client = client_class(api_client=fake_client)

        client.respond(
            [
                {"role": "user", "content": "读取 README"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_call": {
                        "type": "tool_call",
                        "id": "call-123",
                        "name": "read_file",
                        "arguments": {"path": "README.md", "start_line": 1, "end_line": 3},
                    },
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-123",
                    "content": '{"ok": true}',
                },
            ]
        )

        messages = fake_client.chat.completions.kwargs["messages"]
        self.assertEqual(messages[2]["role"], "assistant")
        self.assertEqual(messages[2]["tool_calls"][0]["id"], "call-123")
        self.assertEqual(messages[2]["tool_calls"][0]["function"]["name"], "read_file")
        self.assertEqual(messages[3]["tool_call_id"], "call-123")

    def test_deepseek_client_wraps_timeout_as_structured_error(self):
        class APITimeoutError(Exception):
            pass

        class FailingCompletions:
            def create(self, **kwargs):
                raise APITimeoutError()

        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=FailingCompletions())
        )
        client_class = getattr(model_client, "DeepSeekModelClient", None)
        error_class = getattr(model_client, "ModelClientError", None)
        self.assertIsNotNone(client_class)
        self.assertIsNotNone(error_class)

        client = client_class(api_client=fake_client)

        with self.assertRaises(error_class) as context:
            client.respond([{"role": "user", "content": "测试超时"}])

        self.assertEqual(context.exception.code, "MODEL_TIMEOUT")

    def test_deepseek_client_normalizes_final_response(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="答案是 README.md:3。", tool_calls=None)
                )
            ]
        )
        client_class = getattr(model_client, "DeepSeekModelClient", None)
        self.assertIsNotNone(client_class, "model_client.py 应提供 DeepSeekModelClient")

        client = client_class(api_client=FakeOpenAIClient(response))

        self.assertEqual(
            client.respond([{"role": "user", "content": "数据在哪里？"}]),
            {"type": "final", "content": "答案是 README.md:3。"},
        )


if __name__ == "__main__":
    unittest.main()
