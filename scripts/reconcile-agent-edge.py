#!/usr/bin/env python3
"""Reconcile only the Agent preview/admin EdgeOne rule; keep credentials private."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if not hasattr(dt, "UTC"):
    dt.UTC = dt.timezone.utc
sys.path.insert(0, str(ROOT / "controller/domain-reconciler/src"))
from domain_reconciler.reconciler import TencentCloudClient, TencentApiError  # noqa: E402

ZONE = "zone-3solmvkeru39"
NAME = "Lazy Campus Agent interactive endpoints"
HOSTS = ("preview.lazycampus.com", "agent.lazycampus.com")
CONDITION = "${http.request.host} in ['preview.lazycampus.com', 'agent.lazycampus.com']"
PREVIOUS_CONDITION = "${http.request.host} in ['preview.lazycampus.com', 'agent-admin.lazycampus.com']"
DESCRIPTION = ["Managed by server-gitops/scripts/reconcile-agent-edge.py"]


def desired_rule():
    return {
        "RuleName": NAME, "Status": "enable", "Description": DESCRIPTION,
        "Branches": [{"Condition": CONDITION, "Actions": [
            {"Name": "WebSocket", "WebSocketParameters": {"Switch": "on", "Timeout": 120}},
            {"Name": "Cache", "CacheParameters": {"NoCache": {"Switch": "on"}}},
            {"Name": "OfflineCache", "OfflineCacheParameters": {"Switch": "off"}},
        ]}],
    }


def includes(actual, expected):
    """Allow EdgeOne-generated IDs/default fields, never extra branches/actions."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(includes(actual.get(key), value) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(includes(a, e) for a, e in zip(actual, expected))
    return actual == expected


def select_rule(rules):
    matches = [rule for rule in rules if rule.get("RuleName") == NAME]
    if len(matches) > 1:
        raise ValueError("Multiple matching rules; refusing an ambiguous update")
    if matches:
        rule = matches[0]
        branches = rule.get("Branches", [])
        if (rule.get("Description") != DESCRIPTION or len(branches) != 1
                or branches[0].get("Condition") not in {CONDITION, PREVIOUS_CONDITION} or not rule.get("RuleId")):
            raise ValueError("Existing rule is not the exclusively managed Agent rule")
        return rule
    return None


def reconcile(client, mode):
    rules, offset = [], 0
    for _ in range(100):
        result = client.call("teo", "DescribeL7AccRules", {"ZoneId": ZONE, "Offset": offset, "Limit": 100})
        page = result.get("Rules") or []
        rules.extend(page)
        offset += len(page)
        if not page or offset >= result.get("TotalCount", 0):
            break
    else:
        raise ValueError("EdgeOne rule listing exceeded its bounded page limit")
    existing = select_rule(rules)
    desired = desired_rule()
    matches = existing is not None and includes(existing, desired)
    if mode == "check":
        if not matches:
            raise ValueError("Agent EdgeOne rule is missing or differs from the versioned configuration")
        return "verified"
    if mode == "plan":
        return "unchanged" if matches else "update" if existing else "create"
    if mode != "apply":
        raise ValueError("Unsupported reconciliation mode")
    if matches:
        return "unchanged"
    if existing:
        desired["RuleId"] = existing["RuleId"]
        client.call("teo", "ModifyL7AccRule", {"ZoneId": ZONE, "Rule": desired})
        return "updated"
    client.call("teo", "CreateL7AccRules", {"ZoneId": ZONE, "Rules": [desired]})
    return "created"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    directory = Path(os.environ.get("SECRET_DIR", "/etc/platform-secrets"))
    client = TencentCloudClient((directory / "tencentcloud-secret-id").read_text().strip(),
                               (directory / "tencentcloud-secret-key").read_text().strip())
    action = reconcile(client, "apply" if args.apply else "check" if args.check else "plan")
    print(json.dumps({"rule": NAME, "hosts": HOSTS, "action": action,
                      "websocket": "on", "idle_timeout_seconds": 120, "cache": "disabled"}))


if __name__ == "__main__":
    try:
        main()
    except TencentApiError as error:
        print(f"EdgeOne request failed: {error.action} {error.code} request={error.request_id}", file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        print(f"Agent EdgeOne reconciliation failed ({type(error).__name__}); raw output withheld", file=sys.stderr)
        sys.exit(1)
