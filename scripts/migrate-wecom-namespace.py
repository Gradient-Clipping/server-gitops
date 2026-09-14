"""One-time, data-preserving migration from educoder-wecom to wecom-kf.

Run prepare before staging GitOps; run bank after both worker deployments are
scaled to zero. Workload activation and old namespace removal belong to GitOps.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time

OLD = "educoder-wecom"
NEW = "wecom-kf"
CLAIM = "wecom-kf-bank"
SECRETS = ("mysql-educoder-wecom", "educoder-wecom-runtime", "tcr-auth",
           "wecom-kf-api", "wecom-kf-deepseek", "wecom-kf-admin")


def kube(*args, body=None):
    result = subprocess.run(["k3s", "kubectl", *args],
                            input=json.dumps(body).encode() if body is not None else None,
                            capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError("Kubernetes migration operation failed; details suppressed")
    return result.stdout


def get(kind, name, namespace=None):
    scope = ["-n", namespace] if namespace else []
    return json.loads(kube(*scope, "get", kind, name, "-o", "json"))


def prepare():
    kube("apply", "-f", "-", body={"apiVersion": "v1", "kind": "Namespace",
                                  "metadata": {"name": NEW}})
    for name in SECRETS:
        original = get("secret", name, OLD)
        existing = json.loads(kube("-n", NEW, "get", "secret", name,
                                   "--ignore-not-found", "-o", "json") or b"null")
        if existing and (existing["data"] != original["data"] or existing["type"] != original["type"]):
            raise RuntimeError("Target secret differs; refusing to replace it")
        if not existing:
            kube("create", "-f", "-", body={"apiVersion": "v1", "kind": "Secret",
                 "metadata": {"name": name, "namespace": NEW},
                 "type": original["type"], "data": original["data"]})
        assert get("secret", name, NEW)["data"] == original["data"]
    volume = get("pvc", CLAIM, OLD)["spec"]["volumeName"]
    kube("patch", "pv", volume, "--type=merge", "-p",
         json.dumps({"spec": {"persistentVolumeReclaimPolicy": "Retain"}}))
    print("Six secrets copied and verified; original bank PV retained.")


def volume_directory(namespace):
    claim = get("pvc", CLAIM, namespace)
    pv = get("pv", claim["spec"]["volumeName"])
    ref = pv["spec"]["claimRef"]
    assert ref["namespace"] == namespace and ref["uid"] == claim["metadata"]["uid"]
    path = Path(pv["spec"]["local"]["path"])
    root = Path("/var/lib/rancher/k3s/storage").resolve()
    if path.is_symlink() or path.resolve().parent != root or not path.is_dir():
        raise RuntimeError("Unexpected volume path")
    return path


def inspect_bank(path):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return {"questions": db.execute("SELECT COUNT(*) FROM question_bank").fetchone()[0],
                "images": db.execute("SELECT COUNT(*) FROM question_images").fetchone()[0]}


def bank():
    for namespace in (OLD, NEW):
        deploy = get("deployment", "wecom-kf-workers", namespace)
        assert deploy["spec"]["replicas"] == 0
        pods = json.loads(kube("-n", namespace, "get", "pods", "-l",
                              "app.kubernetes.io/name=wecom-kf-workers", "-o", "json"))
        if pods["items"]:
            raise RuntimeError("Wait for all worker pods to terminate before copying")
    image = get("deployment", "wecom-kf-workers", NEW)["spec"]["template"]["spec"]["containers"][0]["image"]
    pod = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "bank-migration", "namespace": NEW},
           "spec": {"restartPolicy": "Never", "automountServiceAccountToken": False,
                    "imagePullSecrets": [{"name": "tcr-auth"}],
                    "securityContext": {"runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000,
                                        "runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
                    "containers": [{"name": "hold", "image": image,
                                    "command": ["python", "-c", "import time; time.sleep(1800)"],
                                    "resources": {"requests": {"cpu": "5m", "memory": "16Mi"},
                                                  "limits": {"cpu": "100m", "memory": "64Mi"}},
                                    "securityContext": {"allowPrivilegeEscalation": False,
                                                        "readOnlyRootFilesystem": True,
                                                        "capabilities": {"drop": ["ALL"]}},
                                    "volumeMounts": [{"name": "bank", "mountPath": "/data"}]}],
                    "volumes": [{"name": "bank", "persistentVolumeClaim": {"claimName": CLAIM}}]}}
    kube("create", "-f", "-", body=pod)
    kube("-n", NEW, "wait", "--for=condition=Ready", "pod/bank-migration", "--timeout=45s")
    source = volume_directory(OLD) / "question_bank.sqlite"
    target = volume_directory(NEW) / "question_bank.sqlite"
    if source.is_symlink() or not source.is_file() or target.exists() or target.is_symlink():
        raise RuntimeError("Missing source or existing target; no bank files overwritten")
    backup_dir = Path("/var/backups") / ("wecom-kf-namespace-" + time.strftime("%Y%m%dT%H%M%S"))
    backup_dir.mkdir(mode=0o700)
    backup = backup_dir / "question_bank.sqlite"
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as old:
        with sqlite3.connect(backup) as saved:
            old.backup(saved)
    stats = inspect_bank(backup)
    with sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True) as saved:
        with sqlite3.connect(target) as new:
            saved.backup(new)
    assert inspect_bank(target) == stats
    assert hashlib.sha256(backup.read_bytes()).digest() == hashlib.sha256(target.read_bytes()).digest()
    target.chmod(0o600)
    os.chown(target, 1000, 1000)
    kube("-n", NEW, "delete", "pod", "bank-migration", "--wait=true", "--timeout=45s")
    print(json.dumps({"backup": str(backup), "target": str(target), "sha256_equal": True, **stats}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "bank"))
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Root required")
    os.umask(0o077)
    {"prepare": prepare, "bank": bank}[args.phase]()
