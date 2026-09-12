"""Bounded rollover for the single GitOps-managed Sandbox; never touches PVCs."""
from __future__ import annotations
import argparse
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import urllib.error
import urllib.request

NAMESPACE = "lazycampus-agent"
NAME = "campus-sandbox"
STATE_KEY = "platform.lazycampus.com/applied-sandbox-template"
PAUSE_KEY = "platform.lazycampus.com/sandbox-rollout"
SA = Path("/var/run/secrets/kubernetes.io/serviceaccount")
SANDBOX_PATH = f"/apis/agents.x-k8s.io/v1beta1/namespaces/{NAMESPACE}/sandboxes/{NAME}"
POD_PATH = f"/api/v1/namespaces/{NAMESPACE}/pods/{NAME}"
POD_FIELDS = {"containers", "initContainers", "volumes", "runtimeClassName", "serviceAccountName",
              "automountServiceAccountToken", "securityContext", "imagePullSecrets", "nodeSelector",
              "affinity", "dnsConfig", "hostNetwork", "hostPID", "hostIPC"}
CONTAINER_FIELDS = {"image", "command", "args", "env", "envFrom", "resources", "volumeMounts",
                    "volumeDevices", "workingDir", "lifecycle", "ports", "securityContext",
                    "startupProbe", "readinessProbe", "livenessProbe"}


def fingerprint(sandbox):
    content = json.dumps(sandbox["spec"]["podTemplate"], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode()).hexdigest()


def owned_pod(sandbox, pod):
    if sandbox["metadata"].get("name") != NAME or sandbox["metadata"].get("namespace") != NAMESPACE:
        raise ValueError("Unexpected Sandbox identity")
    if pod["metadata"].get("name") != NAME or pod["metadata"].get("namespace") != NAMESPACE:
        raise ValueError("Unexpected Pod identity")
    owners = [item for item in pod["metadata"].get("ownerReferences", []) if item.get("controller")]
    if len(owners) != 1 or any(owners[0].get(key) != value for key, value in {
        "apiVersion": "agents.x-k8s.io/v1beta1", "kind": "Sandbox", "name": NAME,
        "uid": sandbox["metadata"]["uid"],
    }.items()):
        raise ValueError("Refusing to operate on a foreign or unowned Pod")


def without_service_account_injection(spec, desired):
    spec = deepcopy(spec)
    explicit = {item["name"] for item in desired.get("volumes", [])}
    injected = set()
    for volume in spec.get("volumes", []):
        name = volume.get("name", "")
        sources = volume.get("projected", {}).get("sources", [])
        # Ignore only the standard admission-injected token/configmap/downwardAPI volume.
        if (name not in explicit and name.startswith("kube-api-access-") and sources
                and any("serviceAccountToken" in source for source in sources)
                and all(set(source) <= {"serviceAccountToken", "configMap", "downwardAPI"} for source in sources)):
            injected.add(name)
    spec["volumes"] = [item for item in spec.get("volumes", []) if item.get("name") not in injected]
    for group in ("containers", "initContainers"):
        for container in spec.get(group, []):
            container["volumeMounts"] = [item for item in container.get("volumeMounts", []) if not (
                item.get("name") in injected
                and item.get("mountPath") == "/var/run/secrets/kubernetes.io/serviceaccount"
                and item.get("readOnly") is True)]
    return spec


def matches(desired, actual, path=()):
    """Compare specified fields and meaningful removals; retain Kubernetes defaults."""
    if isinstance(desired, dict):
        if not isinstance(actual, dict):
            return False
        keys = set(desired)
        if path == ():
            keys |= POD_FIELDS & set(actual)
        elif len(path) == 2 and path[0] in ("containers", "initContainers"):
            keys |= CONTAINER_FIELDS & set(actual)
        elif path and path[-1] in ("resources", "limits", "requests", "env", "nodeSelector"):
            keys |= set(actual)
        for key in keys:
            if key not in desired:
                if actual.get(key) not in (None, False, "", [], {}):
                    return False
            elif key not in actual or not matches(desired[key], actual[key], (*path, key)):
                return False
        return True
    if isinstance(desired, list):
        if not isinstance(actual, list) or len(desired) != len(actual):
            return False
        field = path[-1] if path else ""
        key = "mountPath" if field == "volumeMounts" else "name"
        if desired and all(isinstance(item, dict) and key in item for item in desired + actual):
            lookup = {item[key]: item for item in actual}
            if len(lookup) != len(actual) or {item[key] for item in desired} != set(lookup):
                return False
            return all(matches(item, lookup[item[key]], (*path, item[key])) for item in desired)
        return all(matches(expected, current, (*path, str(index))) for index, (expected, current) in enumerate(zip(desired, actual)))
    if (len(path) >= 2 and path[-2] in ("limits", "requests")) or (path and path[-1] == "sizeLimit"):
        return quantity(desired) == quantity(actual)
    return desired == actual


def quantity(value):
    units = {"": 1, "n": Decimal("1e-9"), "u": Decimal("1e-6"), "m": Decimal("1e-3"),
             "k": 1000, "K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4,
             "P": 1000**5, "E": 1000**6, "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3,
             "Ti": 1024**4, "Pi": 1024**5, "Ei": 1024**6}
    match = re.fullmatch(r"([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)([A-Za-z]*)", str(value))
    if not match or match[2] not in units:
        return str(value)
    try:
        return Decimal(match[1]) * units[match[2]]
    except InvalidOperation:
        return str(value)


def expected_spec(spec):
    spec = deepcopy(spec)
    if spec.get("runtimeClassName") == "agent-gvisor":
        # The pinned, GitOps-managed RuntimeClass injects this scheduling constraint.
        spec.setdefault("nodeSelector", {}).setdefault("kubernetes.io/hostname", "easy-platform-1")
    for group in ("containers", "initContainers"):
        for container in spec.get(group, []):
            resources = container.get("resources", {})
            for key, value in resources.get("limits", {}).items():
                resources.setdefault("requests", {}).setdefault(key, value)
    return spec


def plan(sandbox, pod):
    if sandbox.get("spec", {}).get("operatingMode") != "Running" or sandbox["metadata"].get("deletionTimestamp"):
        return "paused", None
    if sandbox["metadata"].get("annotations", {}).get(PAUSE_KEY) == "paused":
        return "paused", None
    if pod is None:
        return "waiting-for-controller", None
    owned_pod(sandbox, pod)
    if pod["metadata"].get("deletionTimestamp"):
        return "waiting-for-deletion", None
    digest = fingerprint(sandbox)
    uid = pod["metadata"]["uid"]
    state_value = sandbox["metadata"].get("annotations", {}).get(STATE_KEY)
    state = json.loads(state_value) if state_value else None
    if state is not None and (not isinstance(state, dict) or state.get("phase") not in ("applied", "pending")):
        raise ValueError("Invalid rollout state; refusing to infer the applied revision")
    desired = expected_spec(sandbox["spec"]["podTemplate"]["spec"])
    equal = matches(desired, without_service_account_injection(pod["spec"], desired))
    if state and state.get("hash") == digest and state.get("pod_uid") == uid and state["phase"] == "applied" and equal:
        return "unchanged", None
    if equal and (state is None or (state.get("hash") == digest and state.get("pod_uid") != uid)):
        return "record-applied", {"hash": digest, "pod_uid": uid, "phase": "applied"}
    # The state lives on Sandbox metadata, never on propagated PodTemplate metadata.
    # Even removed template fields therefore trigger exactly one replacement.
    return "replace-pod", {"hash": digest, "pod_uid": uid, "phase": "pending"}


class API:
    def __init__(self):
        self.base = "https://" + os.getenv("KUBERNETES_SERVICE_HOST", "10.43.0.1") + ":" + os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        self.context = ssl.create_default_context(cafile=str(SA / "ca.crt"))

    def request(self, path, method="GET", payload=None, content_type="application/json"):
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": "Bearer " + (SA / "token").read_text().strip(), "Content-Type": content_type,
        })
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and method == "GET":
                return None
            if exc.code == 409:
                raise RuntimeError("Object changed concurrently; retry on the next scheduled run") from None
            raise RuntimeError(f"Kubernetes {method} failed with HTTP {exc.code}; response omitted") from None


def reconcile(api, dry_run=False):
    sandbox = api.request(SANDBOX_PATH)
    if sandbox is None:
        return {"action": "sandbox-absent"}
    pod = api.request(POD_PATH)
    action, state = plan(sandbox, pod)
    result = {"action": action, "sandbox": NAME, "dry_run": dry_run}
    if not state or dry_run:
        return result
    metadata = sandbox["metadata"]
    operations = [{"op": "test", "path": "/metadata/uid", "value": metadata["uid"]},
                  {"op": "test", "path": "/metadata/resourceVersion", "value": metadata["resourceVersion"]}]
    if "annotations" not in metadata:
        operations.append({"op": "add", "path": "/metadata/annotations", "value": {}})
    operations.append({"op": "add", "path": "/metadata/annotations/" + STATE_KEY.replace("~", "~0").replace("/", "~1"),
                       "value": json.dumps(state, sort_keys=True, separators=(",", ":"))})
    api.request(SANDBOX_PATH, "PATCH", operations, "application/json-patch+json")
    if action == "replace-pod":
        current_sandbox = api.request(SANDBOX_PATH)
        current_pod = api.request(POD_PATH)
        if (current_sandbox is None or current_pod is None
                or current_sandbox["metadata"]["uid"] != metadata["uid"]
                or fingerprint(current_sandbox) != state["hash"]
                or current_sandbox["spec"].get("operatingMode") != "Running"
                or current_sandbox["metadata"].get("annotations", {}).get(PAUSE_KEY) == "paused"
                or current_pod["metadata"]["uid"] != state["pod_uid"]):
            return {"action": "state-changed-before-delete"}
        owned_pod(current_sandbox, current_pod)
        if current_pod["metadata"].get("deletionTimestamp"):
            return {"action": "waiting-for-deletion"}
        api.request(POD_PATH, "DELETE", {"apiVersion": "v1", "kind": "DeleteOptions",
            "preconditions": {"uid": current_pod["metadata"]["uid"], "resourceVersion": current_pod["metadata"]["resourceVersion"]},
            "propagationPolicy": "Background"})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(reconcile(API(), dry_run=args.dry_run)))
    except (RuntimeError, ValueError, OSError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
        raise SystemExit(1) from None
