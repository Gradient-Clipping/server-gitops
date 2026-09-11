import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("production_gate", root / "scripts/production_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class ProductionGateTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((root / "config/production.json").read_text())
        self.sha = "a" * 40
        self.previous = "b" * 40
        self.run = {"head_sha": self.sha, "head_branch": "main", "event": "push",
                    "path": ".github/workflows/validate.yml", "run_attempt": 1,
                    "head_repository": {"full_name": self.config["repository"]}}
        self.jobs = [{"name": "validate", "conclusion": "success"}]

    def test_accepts_only_successful_exact_source(self):
        gate.validate_run(self.config, self.sha, self.run, self.jobs, 1)
        for field, value in [("head_sha", "c" * 40), ("head_branch", "untrusted"),
                             ("event", "pull_request"), ("path", ".github/workflows/other.yml"),
                             ("head_repository", {"full_name": "untrusted/fork"})]:
            run = {**self.run, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                gate.validate_run(self.config, self.sha, run, self.jobs)
        for conclusion in ["failure", "cancelled", "skipped", None]:
            with self.subTest(conclusion=conclusion), self.assertRaises(ValueError):
                gate.validate_run(self.config, self.sha, self.run, [{"name": "validate", "conclusion": conclusion}])
        with self.assertRaises(ValueError):
            gate.validate_run(self.config, self.sha, self.run, self.jobs, 2)

    def promote(self, source=None, production=None, comparison="ahead", missing=False):
        calls = []

        def api(path, method="GET", payload=None):
            calls.append((path, method, copy.deepcopy(payload)))
            if method != "GET":
                return {}
            if "/actions/runs/1/jobs?" in path:
                return {"jobs": self.jobs}
            if path.endswith("/actions/runs/1"):
                return self.run
            if path.endswith("/git/ref/heads/main"):
                return {"object": {"sha": source or self.sha}}
            if path.endswith("/git/ref/heads/production"):
                if missing:
                    raise gate.ApiError(404)
                return {"object": {"sha": production or self.previous}}
            if "/compare/" in path:
                return {"status": comparison}
            raise AssertionError(path)

        with patch.object(gate, "api", side_effect=api), patch("builtins.print"):
            result = gate.promote(self.config, self.sha, 1, 1)
        return result, calls

    def test_stale_revision_never_writes(self):
        result, calls = self.promote(source="c" * 40)
        self.assertEqual(result, "stale")
        self.assertTrue(all(method == "GET" for _, method, _ in calls))

    def test_fast_forward_is_not_forced(self):
        result, calls = self.promote()
        self.assertEqual(result, "promoted")
        self.assertEqual(calls[-1][1:], ("PATCH", {"sha": self.sha, "force": False}))

    def test_existing_revision_is_idempotent(self):
        result, calls = self.promote(production=self.sha)
        self.assertEqual(result, "unchanged")
        self.assertTrue(all(method == "GET" for _, method, _ in calls))

    def test_branch_creation_requires_validation(self):
        result, calls = self.promote(missing=True)
        self.assertEqual(result, "created")
        self.assertEqual(calls[-1][2], {"ref": "refs/heads/production", "sha": self.sha})
        self.jobs[0]["conclusion"] = "failure"
        with self.assertRaises(ValueError):
            self.promote(missing=True)

    def test_divergence_or_rollback_is_rejected(self):
        for status in ["behind", "diverged"]:
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.promote(comparison=status)


if __name__ == "__main__":
    unittest.main()
