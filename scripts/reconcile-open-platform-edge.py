#!/usr/bin/env python3
"""Reconcile only the open platform's EdgeOne rule; never log secret values."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
# Ubuntu's host Python 3.10 uses timezone.utc; the controller image uses 3.13.
if not hasattr(dt, "UTC"):
    dt.UTC = dt.timezone.utc
sys.path.insert(0, str(ROOT / "controller/domain-reconciler/src"))
from domain_reconciler.reconciler import TencentCloudClient, TencentApiError  # noqa: E402

ZONE = "zone-3solmvkeru39"
NAME = "Lazy Campus open platform"
CONDITION = "${http.request.host} in ['platform.lazycampus.com']"


def desired_rule(origin_key: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{64}", origin_key):
        raise ValueError("Origin credential must be 64 lowercase hexadecimal characters")
    return {
        "RuleName": NAME,
        "Status": "enable",
        "Description": ["Managed by server-gitops/scripts/reconcile-open-platform-edge.py"],
        "Branches": [{
            "Condition": CONDITION,
            "Actions": [
                {"Name": "Cache", "CacheParameters": {"NoCache": {"Switch": "on"}}},
                {"Name": "OfflineCache", "OfflineCacheParameters": {"Switch": "off"}},
                {"Name": "ClientIPHeader", "ClientIPHeaderParameters": {"Switch": "on", "HeaderName": "EO-Connecting-IP"}},
                {"Name": "ModifyRequestHeader", "ModifyRequestHeaderParameters": {
                    "HeaderActions": [{"Action": "set", "Name": "X-Platform-Origin-Key", "Value": origin_key}]
                }},
            ],
        }],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply the versioned rule to EdgeOne")
    args = parser.parse_args()
    directory = Path(os.environ.get("SECRET_DIR", "/etc/platform-secrets"))
    read = lambda name: (directory / name).read_text().strip()
    client = TencentCloudClient(read("tencentcloud-secret-id"), read("tencentcloud-secret-key"))
    rules = []
    offset = 0
    while True:
        result = client.call("teo", "DescribeL7AccRules", {"ZoneId": ZONE, "Offset": offset, "Limit": 100})
        page = result.get("Rules") or []
        rules.extend(page)
        offset += len(page)
        if not page or offset >= result.get("TotalCount", 0):
            break
    matches = [rule for rule in rules if rule.get("RuleName") == NAME]
    if len(matches) > 1:
        raise ValueError("Multiple matching rules; refusing an ambiguous update")
    if matches and any(branch.get("Condition") != CONDITION for branch in matches[0].get("Branches", [])):
        raise ValueError("Existing rule includes unrelated traffic; refusing to overwrite")
    desired = desired_rule(read("platform-origin-key"))
    if not args.apply:
        print(json.dumps({"host": "platform.lazycampus.com", "action": "update" if matches else "create", "cache": "disabled", "origin_key": "redacted"}))
        return
    if matches:
        desired["RuleId"] = matches[0]["RuleId"]
        client.call("teo", "ModifyL7AccRule", {"ZoneId": ZONE, "Rule": desired})
    else:
        client.call("teo", "CreateL7AccRules", {"ZoneId": ZONE, "Rules": [desired]})
    print("Open platform EdgeOne cache and origin authentication rule applied.")


if __name__ == "__main__":
    try:
        main()
    except TencentApiError as error:
        # API error messages may echo request-header values; print identifiers only.
        print(f"EdgeOne request failed: {error.action} {error.code} request={error.request_id}", file=sys.stderr)
        sys.exit(1)
