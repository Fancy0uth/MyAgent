"""精确文本编辑提议的离线测试。"""

import tempfile
import unittest
from pathlib import Path

from agent_tools.edit import EditProposalStore
from policy import prepare_workspace


class EditProposalTests(unittest.TestCase):
    """验证唯一匹配、确认写入和冲突保护。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_root = Path(self.temp_dir.name)
        source = temp_root / "example.py"
        source.write_text(
            "def mean(values):\n"
            "    return sum(values) / len(values)\n",
            encoding="utf-8",
        )
        self.workspace = prepare_workspace(
            "run-001",
            source_path=source,
            workspaces_root=temp_root / "workspaces",
        )
        self.target = self.workspace / source.name
        self.store = EditProposalStore()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _confirmation(self, proposal):
        """按提议中的摘要字段构造精确确认信息。"""
        return {
            "approved": True,
            "proposal_id": proposal["proposal_id"],
            "path": proposal["path"],
            "old_sha256": proposal["old_sha256"],
            "new_sha256": proposal["new_sha256"],
        }

    def test_unique_match_creates_pending_proposal_without_writing(self):
        proposal = self.store.propose_edit(
            self.workspace,
            "example.py",
            "return sum(values) / len(values)",
            "return sum(values) / len(values) if values else 0",
        )

        self.assertTrue(proposal["ok"])
        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(proposal["operation"], "edit")
        self.assertEqual(proposal["match_count"], 1)
        self.assertIn("-    return sum(values)", proposal["diff"])
        self.assertIn("+    return sum(values)", proposal["diff"])
        self.assertEqual(
            self.target.read_text(encoding="utf-8"),
            "def mean(values):\n"
            "    return sum(values) / len(values)\n",
        )

    def test_no_match_returns_structured_error(self):
        result = self.store.propose_edit(
            self.workspace,
            "example.py",
            "return values[0]",
            "return 0",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "NO_MATCH")

    def test_repeated_match_returns_ambiguous_error(self):
        self.target.write_text("value\nvalue\n", encoding="utf-8")

        result = self.store.propose_edit(
            self.workspace,
            "example.py",
            "value",
            "changed",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "AMBIGUOUS_MATCH")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "value\nvalue\n")

    def test_empty_old_text_is_rejected(self):
        result = self.store.propose_edit(
            self.workspace,
            "example.py",
            "",
            "new text",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "INVALID_ARGUMENT")

    def test_exact_confirmation_applies_edit_and_preserves_other_text(self):
        proposal = self.store.propose_edit(
            self.workspace,
            "example.py",
            "return sum(values) / len(values)",
            "return sum(values) / len(values) if values else 0",
        )

        result = self.store.apply_proposal(
            proposal["proposal_id"],
            self._confirmation(proposal),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "applied")
        self.assertEqual(
            self.target.read_text(encoding="utf-8"),
            "def mean(values):\n"
            "    return sum(values) / len(values) if values else 0\n",
        )

    def test_conflict_after_proposal_is_not_overwritten(self):
        proposal = self.store.propose_edit(
            self.workspace,
            "example.py",
            "return sum(values) / len(values)",
            "return 0",
        )
        self.target.write_text("changed elsewhere\n", encoding="utf-8")

        result = self.store.apply_proposal(
            proposal["proposal_id"],
            self._confirmation(proposal),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "WRITE_CONFLICT")
        self.assertEqual(
            self.target.read_text(encoding="utf-8"),
            "changed elsewhere\n",
        )

    def test_unicode_and_missing_final_newline_are_preserved(self):
        self.target.write_text("# 平均值\nreturn 1", encoding="utf-8")

        proposal = self.store.propose_edit(
            self.workspace,
            "example.py",
            "return 1",
            "return 2",
        )
        result = self.store.apply_proposal(
            proposal["proposal_id"],
            self._confirmation(proposal),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            self.target.read_text(encoding="utf-8"),
            "# 平均值\nreturn 2",
        )


if __name__ == "__main__":
    unittest.main()
