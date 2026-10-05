import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeliveryDocumentationTests(unittest.TestCase):
    def test_readme_documents_all_user_facing_entry_points(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for required in (
            "python main.py",
            "python main.py --real",
            "python main.py --interactive",
            "--session",
            "RPC",
            "python evals/run_evals.py",
        ):
            self.assertIn(required, readme)

    def test_delivery_docs_do_not_contain_secret_values_or_fake_native_pass(self):
        paths = [
            ROOT / "README.md",
            ROOT / "docs" / "SOURCE_MAP.md",
            ROOT / "docs" / "FAILURE_REVIEW.md",
            ROOT / "docs" / "COMPATIBILITY.md",
        ]
        text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        self.assertNotRegex(text, r"(?:sk|dk)-[A-Za-z0-9_-]{12,}")
        self.assertNotRegex(text, r"(?:DEEPSEEK|OPENAI|ANTHROPIC)_API_KEY\s*=\s*[^\s<]+")
        failure_review = (ROOT / "docs" / "FAILURE_REVIEW.md").read_text(encoding="utf-8")
        self.assertNotIn("Pi 原生构建：PASS", failure_review)

    def test_source_map_and_failure_review_have_required_evidence_sections(self):
        source_map = (ROOT / "docs" / "SOURCE_MAP.md").read_text(encoding="utf-8")
        for required in ("agent.py", "rpc.py", "pi-reference", "implemented", "blocked"):
            self.assertIn(required, source_map)

        failure_review = (ROOT / "docs" / "FAILURE_REVIEW.md").read_text(encoding="utf-8")
        for required in ("输入", "可观察轨迹", "诊断", "修改", "回归命令", "结果"):
            self.assertIn(required, failure_review)


if __name__ == "__main__":
    unittest.main()
