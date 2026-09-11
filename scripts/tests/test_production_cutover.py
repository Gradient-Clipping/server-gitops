import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

root = Path(__file__).resolve().parents[2]
with patch.object(sys, "path", [str(root / "scripts"), *sys.path]):
    spec = importlib.util.spec_from_file_location("production_cutover", root / "scripts/production_cutover.py")
    cutover = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cutover)


class CutoverTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((root / "config/production.json").read_text())
        self.sha = "a" * 40
        self.healthy = {"spec": {}, "status": {"conditions": [{"type": "Ready", "status": "True"}],
                                              "lastAppliedRevision": "main@sha1:" + self.sha}}
        self.source = {"spec": {"url": "ssh://git@github.com/" + self.config["repository"] + ".git",
                                "ref": {"branch": "main"}}, "status": self.healthy["status"]}

    def test_prepare_requires_a_validated_matching_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint = Path(temporary) / "checkpoint.json"
            with patch.object(cutover, "get", side_effect=[self.source, self.healthy, self.healthy]), \
                 patch.object(cutover, "ref", side_effect=[self.sha, "b" * 40]), \
                 patch.object(cutover, "suspend") as suspend:
                with self.assertRaises(ValueError):
                    cutover.prepare(self.config, checkpoint)
                suspend.assert_not_called()
                self.assertFalse(checkpoint.exists())

    def test_prepare_records_before_suspending_and_never_stops_workloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint = Path(temporary) / "checkpoint.json"
            observed = []

            def suspend(kind, name, value):
                self.assertTrue(checkpoint.exists())
                observed.append((kind, name, value))

            with patch.object(cutover, "get", side_effect=[self.source, self.healthy, self.healthy]), \
                 patch.object(cutover, "ref", return_value=self.sha), \
                 patch.object(cutover, "suspend", side_effect=suspend), patch("builtins.print"):
                cutover.prepare(self.config, checkpoint)
            self.assertEqual(observed, [("imageupdateautomation", "platform-images", True),
                                        ("kustomization", "flux-system", True)])
            with self.assertRaises(ValueError):
                cutover.prepare(self.config, checkpoint)

    def test_cancel_refuses_changed_branches(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint = Path(temporary) / "checkpoint.json"
            checkpoint.write_text(json.dumps({"server": cutover.SERVER,
                "repository": self.config["repository"], "baseline": self.sha}))
            with patch.object(cutover, "ref", return_value="b" * 40), patch.object(cutover, "suspend") as suspend:
                with self.assertRaises(ValueError):
                    cutover.cancel(self.config, checkpoint)
                suspend.assert_not_called()

    def test_finish_rejects_unvalidated_revision_before_cluster_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint = Path(temporary) / "checkpoint.json"
            checkpoint.write_text(json.dumps({"server": cutover.SERVER, "repository": self.config["repository"]}))
            with patch.object(cutover, "api", side_effect=[{"head_sha": "b" * 40}, {"jobs": []}]), \
                 patch.object(cutover, "kube") as kube:
                with self.assertRaises(ValueError):
                    cutover.finish(self.config, checkpoint, self.sha, 1)
                kube.assert_not_called()


if __name__ == "__main__":
    unittest.main()
