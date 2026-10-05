"""工作区策略的离线测试。"""

import tempfile
import unittest
from pathlib import Path

from policy import file_sha256, prepare_workspace, resolve_editable_path


class WorkspacePolicyTests(unittest.TestCase):
    """验证原始文件和实验副本之间的边界。"""

    def test_prepare_workspace_copies_file_without_changing_original(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source = temp_root / "buggy_original.py"
            workspaces_root = temp_root / "workspaces"
            original_content = "return 1\n"
            source.write_text(original_content, encoding="utf-8")
            original_hash = file_sha256(source)

            workspace = prepare_workspace(
                "run-001",
                source_path=source,
                workspaces_root=workspaces_root,
            )
            copied_file = workspace / source.name

            self.assertEqual(copied_file.read_text(encoding="utf-8"), original_content)
            self.assertEqual(file_sha256(source), original_hash)

            # 修改实验副本后，原始文件内容仍然必须保持不变。
            copied_file.write_text("return 2\n", encoding="utf-8")
            self.assertEqual(source.read_text(encoding="utf-8"), original_content)

    def test_prepare_workspace_rejects_path_like_run_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source = temp_root / "buggy_original.py"
            source.write_text("return 1\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                prepare_workspace(
                    "../outside",
                    source_path=source,
                    workspaces_root=temp_root / "workspaces",
                )

    def test_prepare_workspace_does_not_overwrite_existing_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source = temp_root / "buggy_original.py"
            workspaces_root = temp_root / "workspaces"
            source.write_text("return 1\n", encoding="utf-8")

            prepare_workspace(
                "run-001",
                source_path=source,
                workspaces_root=workspaces_root,
            )

            with self.assertRaises(FileExistsError):
                prepare_workspace(
                    "run-001",
                    source_path=source,
                    workspaces_root=workspaces_root,
                )

    def test_resolve_editable_path_allows_file_inside_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source = temp_root / "buggy_original.py"
            source.write_text("return 1\n", encoding="utf-8")
            workspace = prepare_workspace(
                "run-001",
                source_path=source,
                workspaces_root=temp_root / "workspaces",
            )

            editable_file = resolve_editable_path(workspace, "buggy_original.py")

            self.assertEqual(editable_file, (workspace / source.name).resolve())

    def test_resolve_editable_path_rejects_original_and_parent_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source = temp_root / "buggy_original.py"
            source.write_text("return 1\n", encoding="utf-8")
            workspace = prepare_workspace(
                "run-001",
                source_path=source,
                workspaces_root=temp_root / "workspaces",
            )

            with self.assertRaises(PermissionError):
                resolve_editable_path(workspace, str(source))

            with self.assertRaises(PermissionError):
                resolve_editable_path(workspace, "../test_mean_test.py")

    def test_resolve_editable_path_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source = temp_root / "buggy_original.py"
            outside_file = temp_root / "outside.py"
            source.write_text("return 1\n", encoding="utf-8")
            outside_file.write_text("secret\n", encoding="utf-8")
            workspace = prepare_workspace(
                "run-001",
                source_path=source,
                workspaces_root=temp_root / "workspaces",
            )
            link = workspace / "outside.py"
            link.symlink_to(outside_file)

            with self.assertRaises(PermissionError):
                resolve_editable_path(workspace, "outside.py")

    def test_resolve_editable_path_rejects_validation_test_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            tests_dir = workspace / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_mean.py").write_text("assert True\n", encoding="utf-8")

            with self.assertRaises(PermissionError):
                resolve_editable_path(workspace, "tests/test_mean.py")


if __name__ == "__main__":
    unittest.main()
