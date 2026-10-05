"""可预览写入流程的离线测试。"""

import tempfile
import unittest
from pathlib import Path

from agent_tools.write import WriteProposalStore
from policy import prepare_workspace


class WriteProposalTests(unittest.TestCase):
    """验证提议、确认、冲突和写后核验。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_root = Path(self.temp_dir.name)
        source = temp_root / "buggy_original.py"
        source.write_text("return 1\n", encoding="utf-8")
        self.workspace = prepare_workspace(
            "run-001",
            source_path=source,
            workspaces_root=temp_root / "workspaces",
        )
        self.target = self.workspace / source.name
        self.store = WriteProposalStore()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_propose_write_only_previews_change(self):
        proposal = self.store.propose_write(
            self.workspace,
            "buggy_original.py",
            "return 2\n",
        )

        self.assertTrue(proposal["ok"])
        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(proposal["path"], "buggy_original.py")
        self.assertIn("-return 1", proposal["diff"])
        self.assertIn("+return 2", proposal["diff"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), "return 1\n")

    def test_rejected_proposal_does_not_write(self):
        proposal = self.store.propose_write(
            self.workspace,
            "buggy_original.py",
            "return 2\n",
        )

        result = self.store.apply_proposal(
            proposal["proposal_id"],
            {
                "approved": False,
                "proposal_id": proposal["proposal_id"],
                "path": proposal["path"],
                "old_sha256": proposal["old_sha256"],
                "new_sha256": proposal["new_sha256"],
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "NOT_APPROVED")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "return 1\n")

    def test_confirmation_must_match_proposal(self):
        proposal = self.store.propose_write(
            self.workspace,
            "buggy_original.py",
            "return 2\n",
        )

        result = self.store.apply_proposal(
            proposal["proposal_id"],
            {
                "approved": True,
                "proposal_id": proposal["proposal_id"],
                "path": "another.py",
                "old_sha256": proposal["old_sha256"],
                "new_sha256": proposal["new_sha256"],
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "CONFIRMATION_MISMATCH")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "return 1\n")

    def test_apply_writes_after_exact_confirmation(self):
        proposal = self.store.propose_write(
            self.workspace,
            "buggy_original.py",
            "return 2\n",
        )

        result = self.store.apply_proposal(
            proposal["proposal_id"],
            {
                "approved": True,
                "proposal_id": proposal["proposal_id"],
                "path": proposal["path"],
                "old_sha256": proposal["old_sha256"],
                "new_sha256": proposal["new_sha256"],
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "return 2\n")

    def test_apply_detects_external_change_before_writing(self):
        proposal = self.store.propose_write(
            self.workspace,
            "buggy_original.py",
            "return 2\n",
        )
        self.target.write_text("changed elsewhere\n", encoding="utf-8")

        result = self.store.apply_proposal(
            proposal["proposal_id"],
            {
                "approved": True,
                "proposal_id": proposal["proposal_id"],
                "path": proposal["path"],
                "old_sha256": proposal["old_sha256"],
                "new_sha256": proposal["new_sha256"],
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "WRITE_CONFLICT")
        self.assertEqual(
            self.target.read_text(encoding="utf-8"),
            "changed elsewhere\n",
        )

    def test_apply_can_create_new_file(self):
        proposal = self.store.propose_write(
            self.workspace,
            "notes.txt",
            "new note\n",
        )

        self.assertIsNone(proposal["old_sha256"])
        result = self.store.apply_proposal(
            proposal["proposal_id"],
            {
                "approved": True,
                "proposal_id": proposal["proposal_id"],
                "path": proposal["path"],
                "old_sha256": proposal["old_sha256"],
                "new_sha256": proposal["new_sha256"],
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            (self.workspace / "notes.txt").read_text(encoding="utf-8"),
            "new note\n",
        )


if __name__ == "__main__":
    unittest.main()
