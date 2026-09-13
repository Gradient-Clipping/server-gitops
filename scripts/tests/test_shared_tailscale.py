import importlib.util
import json
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("shared_tailscale", ROOT / "scripts/bootstrap_shared_tailscale.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class StateMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "legacy"
        self.target = self.root / "shared"
        self.backups = self.root / "backups"
        self.source.mkdir()
        self.payload = b'{"_machinekey":"synthetic-device-key","_current-profile":"synthetic-profile"}'
        (self.source / "tailscaled.state").write_bytes(self.payload)
        (self.source / "profile-data").mkdir()
        (self.source / "profile-data/preferences").write_bytes(b"synthetic-route-preferences")
        self.permissions = patch.object(bootstrap, "secure_tree")
        self.permissions.start()
        self.addCleanup(self.permissions.stop)

    def migrate(self, **options):
        return bootstrap.prepare_state(self.source, self.target, self.backups, **options)

    def test_preserves_full_offline_state_and_backup_without_changing_source(self):
        self.assertEqual(self.migrate(), "migrated")
        self.assertEqual((self.target / "tailscaled.state").read_bytes(), self.payload)
        self.assertEqual((self.source / "tailscaled.state").read_bytes(), self.payload)
        self.assertEqual((self.target / "profile-data/preferences").read_bytes(), b"synthetic-route-preferences")
        copies = list(self.backups.glob("*/tailscaled.state"))
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].read_bytes(), self.payload)
        self.assertFalse(list(self.root.glob(".tailscale-staging-*")))

    def test_retry_never_replaces_a_running_devices_updated_state(self):
        self.migrate()
        updated = b'{"_machinekey":"synthetic-device-key","rotated":"new-device-state"}'
        (self.target / "tailscaled.state").write_bytes(updated)
        self.assertEqual(self.migrate(), "existing")
        self.assertEqual((self.target / "tailscaled.state").read_bytes(), updated)
        self.assertEqual(len(list(self.backups.iterdir())), 1)

    def test_failure_never_publishes_a_partial_mountable_directory(self):
        with patch.object(bootstrap, "secure_tree", side_effect=OSError("permission failure")):
            with self.assertRaises(OSError):
                self.migrate()
        self.assertFalse(self.target.exists())
        self.assertEqual((self.source / "tailscaled.state").read_bytes(), self.payload)
        self.assertEqual(len(list(self.backups.glob("*/tailscaled.state"))), 1)
        self.assertFalse(list(self.root.glob(".tailscale-staging-*")))

    def test_invalid_legacy_state_does_not_silently_create_new_identity(self):
        (self.source / "tailscaled.state").write_text("{}")
        with self.assertRaises(ValueError):
            self.migrate(fresh=True)
        self.assertFalse(self.target.exists())

    def test_incomplete_existing_target_is_never_overwritten(self):
        self.target.mkdir()
        (self.target / "tailscaled.state").write_bytes(b"partial")
        with self.assertRaises(ValueError):
            self.migrate()
        self.assertEqual((self.target / "tailscaled.state").read_bytes(), b"partial")

    def test_fresh_enrollment_must_be_explicit(self):
        missing = self.root / "absent"
        with self.assertRaises(ValueError):
            bootstrap.prepare_state(missing, self.target, self.backups)
        self.assertFalse(self.target.exists())
        self.assertEqual(bootstrap.prepare_state(missing, self.target, self.backups, fresh=True), "fresh")
        self.assertTrue(self.target.is_dir())

    @unittest.skipIf(os.name == "nt", "Linux production symlink checks")
    def test_state_symlinks_are_refused(self):
        (self.source / "redirect").symlink_to(self.root)
        with self.assertRaises(ValueError):
            self.migrate()
        self.assertFalse(self.target.exists())

    @unittest.skipIf(os.name == "nt", "Linux production symlink checks")
    def test_redirected_target_path_is_refused(self):
        self.target.symlink_to(self.root / "elsewhere")
        with self.assertRaises(ValueError):
            self.migrate()

    def test_a_scaled_down_legacy_deployment_template_still_blocks_migration(self):
        deployment = {"spec": {"replicas": 0, "template": {"spec": {
            "volumes": [{"persistentVolumeClaim": {"claimName": "easy-swu-tailscale"}}]}}}}
        with self.assertRaisesRegex(ValueError, "template"):
            bootstrap.assert_legacy_stopped([deployment], [])

    def test_terminating_legacy_pods_block_migration_until_deleted(self):
        pod = {"metadata": {"deletionTimestamp": "2026-09-14T00:00:00Z"}, "spec": {
            "volumes": [{"persistentVolumeClaim": {"claimName": "easy-swu-tailscale"}}]}}
        with self.assertRaisesRegex(ValueError, "terminating"):
            bootstrap.assert_legacy_stopped([], [pod])

    def test_direct_legacy_host_mount_also_blocks_migration(self):
        pod = {"spec": {"volumes": [{"hostPath": {"path": str(bootstrap.LEGACY)}}]}}
        with self.assertRaises(ValueError):
            bootstrap.assert_legacy_stopped([], [pod])

    def test_unrelated_api_mounts_do_not_block_migration(self):
        spec = {"volumes": [{"name": "api-tmp", "emptyDir": {}}]}
        bootstrap.assert_legacy_stopped([{"spec": {"template": {"spec": spec}}}], [{"spec": spec}])

    def test_kubectl_errors_never_repeat_secret_payloads(self):
        from types import SimpleNamespace
        with patch.object(bootstrap.subprocess, "run", return_value=SimpleNamespace(
                returncode=1, stdout="synthetic-secret", stderr="synthetic-secret")):
            with self.assertRaises(RuntimeError) as context:
                bootstrap.command(["k3s", "kubectl", "apply"], "synthetic-secret")
        self.assertNotIn("synthetic-secret", str(context.exception))


if __name__ == "__main__":
    unittest.main()
