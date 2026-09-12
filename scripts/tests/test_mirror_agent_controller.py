import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("mirror_agent_controller", SCRIPTS / "mirror_agent_controller.py")
mirror = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mirror)


class ControllerMirrorTest(unittest.TestCase):
    def config(self):
        return {"repository": mirror.REPOSITORY, "sourceBranch": "main", "productionBranch": "production", "validationWorkflow": ".github/workflows/validate.yml", "validationJob": "validate"}

    def image_config(self):
        return f"images:\n  - name: {mirror.UPSTREAM}\n    newName: {mirror.TARGET.rsplit(':', 1)[0]}\n    newTag: v1.0.2\n    digest: {mirror.DIGEST}\npatches:\n  - target: ignored\n".encode()

    def test_uses_archived_literal_config_and_rejects_wrong_destination(self):
        contents = {mirror.FILES[0]: json.dumps(self.config()).encode(), mirror.FILES[1]: self.image_config()}
        self.assertEqual(mirror.read_config(contents), self.config())
        contents[mirror.FILES[1]] = self.image_config().replace(b"lazycampus/agent-sandbox-controller", b"other/agent-sandbox-controller")
        with self.assertRaises(ValueError):
            mirror.read_config(contents)

    def test_rejects_stale_production_even_with_successful_job(self):
        revision = "a" * 40
        run = {"head_sha": revision, "head_branch": "main", "event": "push", "path": ".github/workflows/validate.yml", "head_repository": {"full_name": mirror.REPOSITORY}, "status": "completed", "conclusion": "success"}
        responses = [run, {"jobs": [{"name": "validate", "conclusion": "success"}]}, {"object": {"sha": "b" * 40}}]
        with patch.object(mirror, "api", side_effect=responses), self.assertRaises(ValueError):
            mirror.require_production(self.config(), revision, 123)

    def test_rejects_failed_workflow_even_if_job_succeeded(self):
        revision = "a" * 40
        run = {"head_sha": revision, "head_branch": "main", "event": "push", "path": ".github/workflows/validate.yml", "head_repository": {"full_name": mirror.REPOSITORY}, "status": "completed", "conclusion": "failure"}
        with patch.object(mirror, "api", side_effect=[run, {"jobs": [{"name": "validate", "conclusion": "success"}]}]), self.assertRaises(ValueError):
            mirror.require_production(self.config(), revision, 123)

    def test_checksums_before_extracting_only_exact_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "test.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                for name, payload in (("crane.exe", b"synthetic executable"), ("../escape.txt", b"must not extract")):
                    member = tarfile.TarInfo(name)
                    member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
            with self.assertRaises(ValueError):
                mirror.extract_crane(archive_path, root, "0" * 64, "crane.exe")
            self.assertFalse((root / "crane.exe").exists())
            digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            executable = mirror.extract_crane(archive_path, root, digest, "crane.exe")
            self.assertEqual(executable.read_bytes(), b"synthetic executable")
            self.assertFalse((root.parent / "escape.txt").exists())

    def test_copy_only_after_gate_and_verifies_original_digest(self):
        calls = []
        with patch.object(mirror, "verify_upstream"), patch.object(mirror, "crane_digest", side_effect=[None, mirror.DIGEST]), patch.object(mirror, "command", side_effect=lambda *args, **kwargs: calls.append(args[0])):
            result = mirror.mirror("crane", {}, lambda: calls.append("gate"))
        self.assertEqual(result, "copied and verified")
        self.assertEqual(calls[0], "gate")
        self.assertEqual(calls[1], ["crane", "copy", "--no-clobber", "--jobs", "2", mirror.UPSTREAM + "@" + mirror.DIGEST, mirror.TARGET])

    def test_wrong_existing_target_never_overwritten(self):
        with patch.object(mirror, "verify_upstream"), patch.object(mirror, "crane_digest", return_value="sha256:" + "0" * 64), patch.object(mirror, "command") as command, self.assertRaises(ValueError):
            mirror.mirror("crane", {}, lambda: None)
        command.assert_not_called()

    def test_already_matching_target_needs_no_push(self):
        with patch.object(mirror, "verify_upstream"), patch.object(mirror, "crane_digest", return_value=mirror.DIGEST), patch.object(mirror, "command") as command:
            self.assertEqual(mirror.mirror("crane", {}, lambda: None), "already verified")
        command.assert_not_called()

    def test_registry_auth_failure_is_not_treated_as_missing_tag(self):
        error = mirror.CommandError("crane", 1, b"UNAUTHORIZED: synthetic-secret")
        with patch.object(mirror, "command", side_effect=error), self.assertRaises(mirror.CommandError) as caught:
            mirror.crane_digest("crane", mirror.TARGET, {}, allow_missing=True)
        self.assertNotIn("synthetic-secret", str(caught.exception))

    def test_target_must_match_after_copy(self):
        with patch.object(mirror, "verify_upstream"), patch.object(mirror, "crane_digest", side_effect=[None, "sha256:" + "0" * 64]), patch.object(mirror, "command"), self.assertRaises(ValueError):
            mirror.mirror("crane", {}, lambda: None)


if __name__ == "__main__":
    unittest.main()
