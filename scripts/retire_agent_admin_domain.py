"""Retire only the superseded Agent hostname after its replacement is healthy."""
import json
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
import urllib.request

from domain_reconciler.reconciler import DomainReconciler

OLD = "agent-admin.lazycampus.com"
NEW = "agent.lazycampus.com"
CF_ZONE = "b3587d327b62aaef899b5936e4d6d272"
EO_ZONE = "zone-3solmvkeru39"
SNAPSHOT = Path("/var/lib/platform-agent-bootstrap/domain-retirement/agent-admin.json")


def preflight():
    result = subprocess.run(["k3s", "kubectl", "get", "ingress", "-A", "-o", "json"], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError("Could not inspect Ingress ownership")
    hosts = {rule.get("host") for item in json.loads(result.stdout)["items"] for rule in item.get("spec", {}).get("rules", [])}
    nginx = Path("/etc/nginx/sites-available/lazycampus-agent").read_text()
    if OLD in hosts or NEW not in hosts or OLD in nginx or "server_name " + NEW + ";" not in nginx:
        raise ValueError("Ingress and host routing must already use only the replacement hostname")
    with urllib.request.urlopen("https://" + NEW + "/auth/sso/health", timeout=20) as response:
        if response.status != 200 or json.load(response) != {"enabled": True}:
            raise ValueError("Replacement SSO endpoint is not healthy")
    # Neither this operation nor the hostname change deletes workloads, volumes or repositories.
    for name in ("workspace", "state", "published", "astrbot"):
        if not (Path("/srv/k3s-data/lazycampus-agent") / name).is_dir():
            raise ValueError("An Agent persistent data directory is missing")


def retire(tencent, cloudflare, *, apply=False, snapshot=SNAPSHOT, pause=time.sleep):
    reconciler = DomainReconciler(tencent, cloudflare)

    def domain():
        return reconciler._list_edge_domains(SimpleNamespace(edgeone_zone_id=EO_ZONE)).get(OLD)

    old = domain()
    records = cloudflare.list_dns_records(CF_ZONE, OLD)
    if not old and not records:
        return {"retired_hostname": OLD, "dns_removed": True, "edgeone_removed": True}
    if not apply:
        raise ValueError("Retired hostname still exists in DNS or EdgeOne")
    if old and old.get("DomainName") != OLD:
        raise ValueError("Unexpected EdgeOne retirement target")
    if len(records) > 1 or any(record.get("name") != OLD or record.get("type") != "CNAME" or record.get("proxied") is not False
                               or not old or record.get("content", "").rstrip(".") != old.get("Cname", "").rstrip(".") for record in records):
        raise ValueError("DNS records do not match the exclusively managed previous Agent domain")
    snapshot.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not snapshot.exists():
        descriptor = os.open(snapshot, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump({"hostname": OLD, "replacement": NEW, "dns": records, "edgeone": old,
                       "time": time.time(), "deleted_pvcs": [], "deleted_host_directories": [], "deleted_repositories": []}, stream)
    else:
        saved = json.loads(snapshot.read_text())
        if saved.get("hostname") != OLD or saved.get("replacement") != NEW:
            raise ValueError("Unexpected retirement recovery record")
    # Withdraw the obsolete public route even if EdgeOne retirement needs additional IAM permissions.
    for record in records:
        cloudflare._request("DeleteDnsRecord", "DELETE", f"/zones/{CF_ZONE}/dns_records/{record['id']}")
    if cloudflare.list_dns_records(CF_ZONE, OLD):
        raise ValueError("Previous DNS record still exists")
    if old:
        if old.get("DomainStatus") not in {"offline", "closing"}:
            tencent.call("teo", "ModifyAccelerationDomainStatuses", {"ZoneId": EO_ZONE, "DomainNames": [OLD], "Status": "offline", "Force": False})
        for _ in range(20):
            current = domain()
            if not current or current.get("DomainStatus") == "offline":
                break
            pause(3)
        else:
            raise ValueError("EdgeOne is still disabling the previous domain; retry the same managed operation")
    if old and domain():
        # Do not delete associated aliases, traffic policies or other resources implicitly.
        tencent.call("teo", "DeleteAccelerationDomains", {"ZoneId": EO_ZONE, "DomainNames": [OLD], "Force": False})
    for _ in range(20):
        if not domain():
            break
        pause(3)
    else:
        raise ValueError("EdgeOne deletion is still propagating; retry the same managed operation")
    if cloudflare.list_dns_records(CF_ZONE, OLD):
        raise ValueError("Previous DNS record still exists")
    return {"retired_hostname": OLD, "dns_removed": True, "edgeone_removed": True}
