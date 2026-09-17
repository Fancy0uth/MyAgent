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


if __name__ == "__main__":
    unittest.main()
