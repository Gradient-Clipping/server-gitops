import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
    spec = importlib.util.spec_from_file_location("run_agent_backup", ROOT / "scripts/run_agent_backup.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)


def cron_fixture():
    labels = ("workspace", "state", "published", "astrbot")
    volumes = [{"name": name, "persistentVolumeClaim": {"claimName": "agent-" + name}} for name in labels]
    volumes += [{"name": "backup", "hostPath": {"path": runner.BACKUPS, "type": "Directory"}}, {"name": "script", "configMap": {"name": "agent-backup-script", "defaultMode": 292}}]
    mounts = [{"name": name, "mountPath": "/source/" + name, "readOnly": True} for name in labels]
    mounts += [{"name": "backup", "mountPath": "/backups"}, {"name": "script", "mountPath": "/scripts", "readOnly": True}]
    container = {"name": "backup", "image": "ccr.ccs.tencentyun.com/lazycampus/agent-backend:1.0.3", "command": ["python", "/scripts/backup_agent.py"], "resources": {"requests": {"cpu": "30m", "memory": "64Mi"}, "limits": {"cpu": "500m", "memory": "192Mi"}}, "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}, "readOnlyRootFilesystem": True}, "volumeMounts": mounts}
    pod = {"automountServiceAccountToken": False, "restartPolicy": "Never", "nodeSelector": {"kubernetes.io/hostname": "easy-platform-1"}, "imagePullSecrets": [{"name": "tcr-auth"}], "securityContext": {"runAsUser": 1000, "runAsGroup": 1000, "runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}}, "containers": [container], "volumes": volumes}
    return {"apiVersion": "batch/v1", "kind": "CronJob", "metadata": {"name": "agent-backup", "namespace": "lazycampus-agent"}, "spec": {"jobTemplate": {"spec": {"backoffLimit": 1, "activeDeadlineSeconds": 1200, "ttlSecondsAfterFinished": 172800, "template": {"metadata": {"labels": {"app.kubernetes.io/name": "agent-backup"}}, "spec": pod}}}}}


class AgentBackupRunnerTests(unittest.TestCase):
    def test_job_has_unique_name_audit_provenance_and_exact_image(self):
        original = cron_fixture()
        untouched = copy.deepcopy(original)
        first = runner.make_job(original, "agent-backup-script-0123456789", "a" * 40, 42)
        second = runner.make_job(original, "agent-backup-script-0123456789", "a" * 40, 42)
        self.assertNotEqual(first["metadata"]["name"], second["metadata"]["name"])
        self.assertEqual(first["metadata"]["namespace"], "lazycampus-agent")
        self.assertEqual(first["metadata"]["annotations"]["platform.lazycampus.com/revision"], "a" * 40)
        self.assertEqual(first["spec"]["template"]["spec"]["volumes"][-1]["configMap"]["name"], "agent-backup-script-0123456789")
        self.assertEqual(original, untouched)
        # A scheduled Job has an owner reference, but no inherited pod labels.
        for metadata in ({"ownerReferences": [{"kind": "CronJob", "name": "agent-backup"}]}, first["metadata"]):
            with self.subTest(metadata=metadata):
                with self.assertRaises(ValueError):
                    runner.ensure_backup_idle([{"metadata": metadata, "status": {}}])
                for terminal in ("Complete", "Failed"):
                    runner.ensure_backup_idle([{"metadata": metadata, "status": {"conditions": [{"type": terminal, "status": "True"}]}}])
        runner.ensure_backup_idle([{"metadata": {"ownerReferences": [{"kind": "CronJob", "name": "another-backup"}]}}])

    def test_rejects_unrelated_pvc_or_writable_source(self):
        for change in ("pvc", "write"):
            cron = cron_fixture()
            pod = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]
            if change == "pvc":
                pod["volumes"][0]["persistentVolumeClaim"]["claimName"] = "mysql-data"
            else:
                pod["containers"][0]["volumeMounts"][0]["readOnly"] = False
            with self.assertRaises(ValueError):
                runner.make_job(cron, "agent-backup-script-0123456789", "a" * 40, 42)

    def test_rejects_extra_execution_capability(self):
        for field, value in (("env", [{"name": "UNEXPECTED", "value": "x"}]), ("lifecycle", {})):
            cron = cron_fixture()
            cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0][field] = value
            with self.assertRaises(ValueError):
                runner.make_job(cron, "agent-backup-script-0123456789", "a" * 40, 42)

    def test_stale_revision_is_rejected_before_job_creation(self):
        config = json.loads((ROOT / "config/production.json").read_text())
        revision = "a" * 40
        run = {"head_sha": revision, "head_branch": "main", "event": "push", "path": ".github/workflows/validate.yml", "head_repository": {"full_name": config["repository"]}, "status": "completed", "conclusion": "success"}
        with patch.object(runner, "api", side_effect=[run, {"jobs": [{"name": "validate", "conclusion": "success"}]}, {"object": {"sha": "b" * 40}}]), self.assertRaises(ValueError):
            runner.gate(config, revision, 42)

    def fixture_archive(self, root, *, bad_inner_hash=False, traversal=False, duplicate=False):
        source = root / "source.sqlite"
        connection = sqlite3.connect(source)
        try:
            connection.execute("CREATE TABLE records(id INTEGER)")
            connection.execute("INSERT INTO records VALUES(7)")
            connection.commit()
        finally:
            connection.close()
        payload = source.read_bytes()
        relative = "../outside.sqlite" if traversal else "state/agent.sqlite"
        manifest = {"format": 1, "files": [{"path": relative, "size": len(payload), "sha256": "0" * 64 if bad_inner_hash else hashlib.sha256(payload).hexdigest(), "kind": "sqlite-online"}]}
        if duplicate:
            manifest["files"].append(copy.deepcopy(manifest["files"][0]))
        archive_path = root / "agent-20260912T120000Z.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            for name, data in (("agent/manifest.json", json.dumps(manifest).encode()), ("agent/" + relative, payload)):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        summary = {"archive": archive_path.name, "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(), "files": 1, "sqlite_databases": 1, "bytes": archive_path.stat().st_size, "revision": "a" * 40, "validation_run": 42, "job": "agent-backup-manual-a-test"}
        (root / (archive_path.name + ".json")).write_text(json.dumps(summary))
        return summary, source

    def test_restores_checksums_and_sqlite_into_temporary_directory_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary, source = self.fixture_archive(root)
            before = source.read_bytes()
            result = runner.verify_backup(str(root), summary)
            self.assertEqual(result["files_verified"], 1)
            self.assertEqual(result["sqlite_verified"], 1)
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse(list(root.glob(".agent-verify-*")))
            record = json.loads((root / (summary["archive"] + ".json")).read_text())
            self.assertEqual(record["verification"]["revision"], "a" * 40)

    def test_refuses_modified_archive_or_inner_checksum(self):
        for invalid in ("outer", "inner", "duplicate"):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                summary, _ = self.fixture_archive(root, bad_inner_hash=invalid == "inner", duplicate=invalid == "duplicate")
                if invalid == "outer":
                    summary["sha256"] = "0" * 64
                with self.assertRaises(ValueError):
                    runner.verify_backup(str(root), summary)
                self.assertFalse(list(root.glob(".agent-verify-*")))

    def test_rejects_tar_path_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary, _ = self.fixture_archive(root, traversal=True)
            with self.assertRaises(ValueError):
                runner.verify_backup(str(root), summary)

    def test_failed_job_stops_wait_without_reading_logs(self):
        status = {"status": {"conditions": [{"type": "Failed", "status": "True"}]}}
        with patch.object(runner, "kube", return_value=json.dumps(status).encode()) as kube, self.assertRaises(RuntimeError):
            runner.wait_job("agent-backup-test")
        self.assertEqual(kube.call_count, 1)
        job = {"metadata": {"name": "agent-backup-test", "uid": "job-uid"}, "status": {"conditions": [{"type": "Complete", "status": "True"}]}}
        with patch.object(runner, "kube", return_value=json.dumps(job).encode()):
            self.assertEqual(runner.wait_job("agent-backup-test"), job)
        successful = {"metadata": {"name": "agent-backup-test-success", "ownerReferences": [{"kind": "Job", "name": "agent-backup-test", "uid": "job-uid", "controller": True}]}, "status": {"phase": "Succeeded", "containerStatuses": [{"name": "backup", "state": {"terminated": {"exitCode": 0}}}]}}
        failed = copy.deepcopy(successful)
        failed["metadata"]["name"] = "agent-backup-test-failed"
        failed["status"] = {"phase": "Failed", "containerStatuses": [{"name": "backup", "state": {"terminated": {"exitCode": 1}}}]}
        unrelated = copy.deepcopy(successful)
        unrelated["metadata"]["ownerReferences"][0]["uid"] = "other-job-uid"
        self.assertEqual(runner.successful_pod(job, [failed, unrelated, successful]), "agent-backup-test-success")
        for pods in ([failed, unrelated], [successful, copy.deepcopy(successful)]):
            with self.subTest(pods=pods), self.assertRaises(ValueError):
                runner.successful_pod(job, pods)


if __name__ == "__main__":
    unittest.main()
