import json
import tempfile
import unittest
from pathlib import Path

from evals.run_compatibility import (
    VALID_STATUSES,
    load_compatibility_tasks,
    run_compatibility,
    write_results,
)


class CompatibilityRunnerTests(unittest.TestCase):
    def test_statuses_and_result_contract_are_explicit(self):
        self.assertEqual(
            VALID_STATUSES,
            {
                "not_run",
                "subset_verified",
                "fixed_contract_compatible",
                "not_applicable",
            },
        )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "results.jsonl"
            results = run_compatibility(
                "myagent",
                Path("evals/compatibility_tasks.jsonl"),
                output,
            )

            self.assertGreaterEqual(len(results), 4)
            for result in results:
                self.assertTrue(
                    {
                        "task_id",
                        "implementation",
                        "status",
                        "observations",
                        "differences",
                        "command",
                        "timestamp",
                    }.issubset(result)
                )
                self.assertIn(result["status"], VALID_STATUSES)
                serialized = json.dumps(result, ensure_ascii=False)
                self.assertNotRegex(serialized, r"(?:sk|dk)-[A-Za-z0-9_-]{12,}")
                self.assertNotRegex(serialized, r"[A-Z0-9_]*(?:API_KEY|TOKEN)\s*=\s*[^\s]+")

            self.assertEqual(len(output.read_text(encoding="utf-8").splitlines()), len(results))

    def test_pi_mode_is_not_reported_as_pass_when_native_fixture_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "pi-results.jsonl"
            results = run_compatibility(
                "pi",
                Path("evals/compatibility_tasks.jsonl"),
                output,
            )

        self.assertTrue(results)
        self.assertTrue(all(result["implementation"] == "pi" for result in results))
        self.assertTrue(
            all(result["status"] in {"not_run", "subset_verified", "not_applicable"} for result in results)
        )

    def test_writer_appends_instead_of_overwriting_previous_results(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "results.jsonl"
            write_results(output, [{"task_id": "one"}])
            write_results(output, [{"task_id": "two"}])
            lines = output.read_text(encoding="utf-8").splitlines()

        self.assertEqual([json.loads(line)["task_id"] for line in lines], ["one", "two"])

    def test_task_file_is_jsonl_and_has_no_duplicate_ids(self):
        tasks = load_compatibility_tasks(Path("evals/compatibility_tasks.jsonl"))
        ids = [task["id"] for task in tasks]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(task.get("kind") for task in tasks))


if __name__ == "__main__":
    unittest.main()
