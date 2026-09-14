"""Retire only the superseded callback hostname after its replacement is healthy."""
import json
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
import urllib.request

import datetime as dt
import sys

ROOT = Path(__file__).resolve().parents[1]
if not hasattr(dt, "UTC"):
    dt.UTC = dt.timezone.utc
sys.path.insert(0, str(ROOT / "controller/domain-reconciler/src"))
from domain_reconciler.reconciler import DomainReconciler, TencentCloudClient, CloudflareClient

OLD = "educoder.lazycampus.com"
NEW = "kf.lazycampus.com"
CF_ZONE = "b3587d327b62aaef899b5936e4d6d272"
EO_ZONE = "zone-3solmvkeru39"
SNAPSHOT = Path("/var/lib/wecom-kf-bootstrap/domain-retirement/educoder.json")


def preflight():
    result = subprocess.run(["k3s", "kubectl", "get", "ingress", "-A", "-o", "json"], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError("Could not inspect Ingress ownership")
    hosts = {rule.get("host") for item in json.loads(result.stdout)["items"] for rule in item.get("spec", {}).get("rules", [])}
    nginx = Path("/etc/nginx/sites-available/educoder-wecom").read_text()
    if OLD in hosts or NEW not in hosts or OLD in nginx or "server_name " + NEW + ";" not in nginx:
        raise ValueError("Ingress and host routing must already use only the replacement hostname")
    with urllib.request.urlopen("https://" + NEW + "/healthz", timeout=20) as response:
        state = json.load(response)
        if (response.status != 200 or response.geturl() != "https://" + NEW + "/healthz"
                or state.get("mode") != "callback-only"
                or state.get("execution_enabled") is not False
                or state.get("message_processing_enabled") is not False):
            raise ValueError("Replacement callback endpoint is not healthy")



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
        raise ValueError("DNS records do not match the exclusively managed previous callback domain")
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


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    preflight()
    directory = Path(os.environ.get("SECRET_DIR", "/etc/platform-secrets"))
    read = lambda name: (directory / name).read_text().strip()
    tencent = TencentCloudClient(read("tencentcloud-secret-id"), read("tencentcloud-secret-key"))
    cloudflare = CloudflareClient(read("cloudflare-api-token"))
    print(json.dumps(retire(tencent, cloudflare, apply=args.apply)))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Callback domain retirement failed: {type(error).__name__} {getattr(error, 'code', '')}", file=sys.stderr)
        sys.exit(1)
