from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "deploy/kubernetes/app/reconcile_sandbox.py"
if not SOURCE.exists():
    SOURCE = ROOT / "clusters/easy-platform/apps/lazycampus-agent/reconcile_sandbox.py"
spec = importlib.util.spec_from_file_location("sandbox_rollout", SOURCE)
rollout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rollout)


def fixtures():
    desired = {"serviceAccountName": "agent-reader", "runtimeClassName": "agent-gvisor",
               "containers": [{"name": "opencode", "image": "example/opencode:1.0.1", "env": [{"name": "FOO", "value": "bar"}],
                 "args": ["serve"], "resources": {"requests": {"cpu": "100m"}, "limits": {"cpu": "1500m", "ephemeral-storage": "2Gi"}},
                 "volumeMounts": [{"name": "workspace", "mountPath": "/workspace"},
                                  {"name": "workspace", "mountPath": "/workspace/data", "subPath": "data", "readOnly": True}]}],
               "volumes": [{"name": "workspace", "persistentVolumeClaim": {"claimName": "agent-workspace"}}]}
    sandbox = {"metadata": {"name": rollout.NAME, "namespace": rollout.NAMESPACE, "uid": "sandbox-uid", "resourceVersion": "10"},
               "spec": {"operatingMode": "Running", "podTemplate": {"metadata": {"labels": {"app": "agent"}}, "spec": desired}}}
    actual = deepcopy(desired)
    actual.update({"nodeName": "easy-platform-1", "restartPolicy": "Always", "dnsPolicy": "ClusterFirst",
                   "nodeSelector": {"kubernetes.io/hostname": "easy-platform-1"},
                   "schedulerName": "default-scheduler", "overhead": {"memory": "64Mi"}, "enableServiceLinks": True,
                   "tolerations": [{"key": "node.kubernetes.io/not-ready", "operator": "Exists", "effect": "NoExecute", "tolerationSeconds": 300}]})
    container = actual["containers"][0]
    container.update({"imagePullPolicy": "IfNotPresent", "terminationMessagePath": "/dev/termination-log", "terminationMessagePolicy": "File"})
    container["resources"]["limits"]["cpu"] = "1.5"
    container["resources"]["requests"]["ephemeral-storage"] = "2Gi"
    container["volumeMounts"].append({"name": "kube-api-access-abc", "mountPath": "/var/run/secrets/kubernetes.io/serviceaccount", "readOnly": True})
    actual["volumes"].append({"name": "kube-api-access-abc", "projected": {"sources": [
        {"serviceAccountToken": {"path": "token"}}, {"configMap": {"name": "kube-root-ca.crt"}}, {"downwardAPI": {"items": []}}]}})
    pod = {"metadata": {"name": rollout.NAME, "namespace": rollout.NAMESPACE, "uid": "pod-uid", "resourceVersion": "20",
            "ownerReferences": [{"apiVersion": "agents.x-k8s.io/v1beta1", "kind": "Sandbox", "name": rollout.NAME,
                                 "uid": "sandbox-uid", "controller": True}]}, "spec": actual}
    return sandbox, pod


class RolloutTests(unittest.TestCase):
    def applied(self, sandbox, pod):
        sandbox["metadata"].setdefault("annotations", {})[rollout.STATE_KEY] = json.dumps({
            "hash": rollout.fingerprint(sandbox), "pod_uid": pod["metadata"]["uid"], "phase": "applied"})

    def test_defaults_and_service_account_injection_do_not_trigger_rollout(self):
        sandbox, pod = fixtures()
        action, _ = rollout.plan(sandbox, pod)
        self.assertEqual(action, "record-applied")
        self.applied(sandbox, pod)
        self.assertEqual(rollout.plan(sandbox, pod)[0], "unchanged")

    def test_image_change_and_removed_settings_trigger_replacement(self):
        for field in ("image", "env", "args", "resources", "volumeMounts"):
            with self.subTest(field=field):
                sandbox, pod = fixtures()
                self.applied(sandbox, pod)
                desired = sandbox["spec"]["podTemplate"]["spec"]["containers"][0]
                if field == "image":
                    desired[field] = "example/opencode:1.0.2"
                else:
                    del desired[field]
                self.assertEqual(rollout.plan(sandbox, pod)[0], "replace-pod")

    def test_unknown_added_volume_is_not_ignored(self):
        sandbox, pod = fixtures()
        pod["spec"]["volumes"].append({"name": "extra", "emptyDir": {}})
        self.assertEqual(rollout.plan(sandbox, pod)[0], "replace-pod")

    def test_initial_comparison_detects_removed_env_args_and_mounts(self):
        for field in ("env", "args", "volumeMounts", "resources"):
            with self.subTest(field=field):
                sandbox, pod = fixtures()
                del sandbox["spec"]["podTemplate"]["spec"]["containers"][0][field]
                self.assertEqual(rollout.plan(sandbox, pod)[0], "replace-pod")

    def test_foreign_owner_is_refused(self):
        sandbox, pod = fixtures()
        pod["metadata"]["ownerReferences"][0]["uid"] = "foreign"
        with self.assertRaisesRegex(ValueError, "foreign"):
            rollout.plan(sandbox, pod)

    def test_pending_new_pod_is_recorded_without_second_deletion(self):
        sandbox, pod = fixtures()
        sandbox["metadata"]["annotations"] = {rollout.STATE_KEY: json.dumps({
            "hash": rollout.fingerprint(sandbox), "pod_uid": "old-pod-uid", "phase": "pending"})}
        self.assertEqual(rollout.plan(sandbox, pod)[0], "record-applied")

    def test_delete_is_scoped_and_has_uid_resource_version_preconditions(self):
        sandbox, pod = fixtures()
        pod["spec"]["containers"][0]["image"] = "example/old:1"
        calls = []

        class FakeAPI:
            def request(self, path, method="GET", payload=None, content_type=None):
                calls.append((path, method, payload))
                return deepcopy(sandbox if path == rollout.SANDBOX_PATH else pod)

        self.assertEqual(rollout.reconcile(FakeAPI())["action"], "replace-pod")
        deletes = [call for call in calls if call[1] == "DELETE"]
        self.assertEqual(len(deletes), 1)
        self.assertEqual(deletes[0][0], rollout.POD_PATH)
        self.assertEqual(deletes[0][2]["preconditions"], {"uid": "pod-uid", "resourceVersion": "20"})
        self.assertFalse(any("persistentvolume" in call[0] for call in calls))


if __name__ == "__main__":
    unittest.main()
