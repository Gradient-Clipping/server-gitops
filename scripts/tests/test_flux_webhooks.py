import argparse
import copy
import importlib.util
import pathlib
import unittest
from unittest.mock import patch

path = pathlib.Path(__file__).resolve().parents[1] / "flux_webhooks.py"
spec = importlib.util.spec_from_file_location("flux_webhooks", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.config = module.catalog()
        self.config["hooks"] = self.config["hooks"][:1]
        self.hook = self.config["hooks"][0]
        self.url = "https://hooks.example.invalid/hook/test"
        self.args = argparse.Namespace(server="test", apply=True, refresh_secret=False)
        self.existing = module.desired_hook(self.hook, self.url)
        self.existing["id"] = 123

    def reconcile(self, existing):
        calls = []

        def api(path, method="GET", payload=None):
            calls.append((path, method, payload))
            return copy.deepcopy(existing) if method == "GET" else {}

        with patch.object(module, "endpoints", return_value={self.hook["name"]: self.url}), \
             patch.object(module, "runtime_token", return_value="test-token"), \
             patch.object(module, "gh_api", side_effect=api), patch("builtins.print"):
            module.reconcile(self.config, self.args)
        return calls

    def test_existing_configuration_does_not_write(self):
        self.assertEqual([c[1] for c in self.reconcile([self.existing])], ["GET"])

    def test_unrelated_hooks_are_preserved_when_creating(self):
        other = copy.deepcopy(self.existing)
        other["config"]["url"] = "https://other.example.invalid/events"
        calls = self.reconcile([other])
        self.assertEqual([c[1] for c in calls], ["GET", "POST"])
        self.assertEqual(calls[1][2]["config"]["secret"], "test-token")

    def test_security_and_event_drift_is_corrected_in_place(self):
        self.existing["config"]["insecure_ssl"] = "1"
        self.existing["events"] = ["*"]
        calls = self.reconcile([self.existing])
        self.assertEqual(calls[1][1], "PATCH")
        self.assertTrue(calls[1][0].endswith("/123"))
        self.assertEqual(calls[1][2]["events"], ["push"])
        self.assertEqual(calls[1][2]["config"]["insecure_ssl"], "0")

    def test_duplicate_managed_hooks_fail_before_mutation(self):
        with self.assertRaisesRegex(RuntimeError, "Duplicate managed hooks"):
            self.reconcile([self.existing, self.existing])

    def test_dry_run_does_not_write(self):
        self.args.apply = False
        self.assertEqual([c[1] for c in self.reconcile([])], ["GET"])

    def test_explicit_secret_refresh_updates_existing_hook(self):
        self.args.refresh_secret = True
        self.assertEqual([c[1] for c in self.reconcile([self.existing])], ["GET", "PATCH"])


if __name__ == "__main__":
    unittest.main()
