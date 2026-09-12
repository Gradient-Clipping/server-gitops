import base64
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap_agent_sso as bootstrap
import run_agent_sso_bootstrap as runner


class AgentSSOBootstrapTests(unittest.TestCase):
    def test_secret_is_retained_scoped_idempotent_and_never_printed(self):
        resources = {}

        def command(args, content=None):
            if args[2] == "get":
                return json.dumps(resources[args[6]]) if args[6] in resources else ""
            self.assertEqual(args, ["k3s", "kubectl", "create", "-f", "-"])
            resource = json.loads(content)
            self.assertEqual(resource["metadata"]["name"], "agent-admin-oidc-secret")
            resources[resource["metadata"]["namespace"]] = resource
            return ""

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "secret"
            output = io.StringIO()
            with patch.object(bootstrap, "PATH", path), patch.object(bootstrap, "command", side_effect=command), contextlib.redirect_stdout(output):
                bootstrap.main()
                retained = path.read_bytes()
                bootstrap.main()
            self.assertEqual(path.read_bytes(), retained)
            self.assertEqual(set(resources), {"identity-system", "lazycampus-agent"})
            value = retained.decode().strip()
            self.assertGreaterEqual(len(value), 48)
            self.assertNotIn(value, output.getvalue())
            self.assertNotIn(base64.b64encode(value.encode()).decode(), output.getvalue())
            self.assertEqual([json.loads(line)["created_secrets"] for line in output.getvalue().splitlines()], [2, 0])

    def test_mismatch_refuses_rotation_without_writing_existing_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "secret"
            path.write_text("a" * 64)
            with patch.object(bootstrap, "PATH", path), patch.object(bootstrap, "command", return_value='{"data":{"OIDC_CLIENT_SECRET":"different"}}') as command:
                with self.assertRaisesRegex(ValueError, "explicit rotation"):
                    bootstrap.main()
            self.assertEqual(command.call_count, 1)
            self.assertEqual(path.read_text(), "a" * 64)

    def test_gate_rejects_stale_production_and_non_successful_validation(self):
        config = {"repository": "Gradient-Clipping/server-gitops", "sourceBranch": "main", "productionBranch": "production",
                  "validationWorkflow": ".github/workflows/validate.yml", "validationJob": "validate"}
        revision = "a" * 40
        valid = {"head_sha": revision, "head_branch": "main", "event": "push", "path": config["validationWorkflow"],
                 "head_repository": {"full_name": config["repository"]}, "status": "completed", "conclusion": "success"}
        responses = [valid, {"jobs": [{"name": "validate", "conclusion": "success"}]}, {"object": {"sha": revision}}]
        with patch.object(runner, "api", side_effect=responses):
            runner.gate(config, revision, 123)
        with patch.object(runner, "api", side_effect=responses[:2] + [{"object": {"sha": "b" * 40}}]), self.assertRaises(ValueError):
            runner.gate(config, revision, 123)
        for change in ({"event": "pull_request"}, {"conclusion": "failure"}, {"status": "in_progress"}):
            with patch.object(runner, "api", side_effect=[{**valid, **change}, responses[1]]), self.assertRaises(ValueError):
                runner.gate(config, revision, 123)


if __name__ == "__main__":
    unittest.main()
