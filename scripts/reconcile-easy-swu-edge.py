#!/usr/bin/env python3
"""Reconcile the Easy SWU EdgeOne origin timeouts; never log secret values."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
# Ubuntu's host Python 3.10 uses timezone.utc; the controller image uses 3.13.
if not hasattr(dt, "UTC"):
    dt.UTC = dt.timezone.utc
sys.path.insert(0, str(ROOT / "controller/domain-reconciler/src"))
from domain_reconciler.reconciler import TencentCloudClient, TencentApiError  # noqa: E402

ZONE = "zone-3solmvkeru39"
WATERMARK_PATH = "/api/v1/admin/watermarks/decode"
WATERMARK_CONDITION = (
    "${http.request.host} in ['easy-admin.lazycampus.com'] "
    f"and ${{http.request.uri.path}} in ['{WATERMARK_PATH}']"
)
# EdgeOne accepts only a prefixed `not` before the operand: `not (${...} in [...])`
# and `${...} not in [...]` are both rejected as ConfigConditionSyntaxError.
# The conditions must stay mutually exclusive, because RulePriority is
# output-only and rule ordering cannot be relied on to protect the longer
# watermark decode budget.
UPSTREAM_CONDITION = (
    "${http.request.host} in ['easy-api.lazycampus.com', 'easy-admin.lazycampus.com'] "
    f"and not ${{http.request.uri.path}} in ['{WATERMARK_PATH}']"
)

RULES = [
    {
        "RuleName": "Easy SWU upstream timeout",
        "Status": "enable",
        "Description": ["Managed by server-gitops/scripts/reconcile-easy-swu-edge.py"],
        "Branches": [
            {
                "Condition": UPSTREAM_CONDITION,
                "Actions": [
                    {
                        "Name": "HTTPUpstreamTimeout",
                        "HTTPUpstreamTimeoutParameters": {"ResponseTimeout": 40},
                    }
                ],
            }
        ],
    },
    {
        "RuleName": "Easy SWU watermark decode timeout",
        "Status": "enable",
        "Description": ["Managed by server-gitops/scripts/reconcile-easy-swu-edge.py"],
        "Branches": [
            {
                "Condition": WATERMARK_CONDITION,
                "Actions": [
                    {
                        "Name": "HTTPUpstreamTimeout",
                        "HTTPUpstreamTimeoutParameters": {"ResponseTimeout": 120},
                    }
                ],
            }
        ],
    },
]


def includes(actual, expected) -> bool:
    """True when every versioned field is present on the rule EdgeOne returned."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            includes(actual.get(key), value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(includes(item, wanted) for item, wanted in zip(actual, expected))
        )
    return actual == expected


def response_timeout(rule: dict) -> int:
    return rule["Branches"][0]["Actions"][0]["HTTPUpstreamTimeoutParameters"]["ResponseTimeout"]


def fetch_rules(client) -> list:
    rules = []
    offset = 0
    while True:
        result = client.call(
            "teo", "DescribeL7AccRules", {"ZoneId": ZONE, "Offset": offset, "Limit": 100}
        )
        page = result.get("Rules") or []
        rules.extend(page)
        offset += len(page)
        if not page or offset >= result.get("TotalCount", 0):
            break
    return rules


def find_existing(rules: list, desired: dict) -> dict | None:
    name = desired["RuleName"]
    matches = [rule for rule in rules if rule.get("RuleName") == name]
    if len(matches) > 1:
        raise ValueError(f"Multiple rules named {name}; refusing an ambiguous update")
    if not matches:
        return None
    actual = [branch.get("Condition") for branch in matches[0].get("Branches", [])]
    expected = [branch["Condition"] for branch in desired["Branches"]]
    if actual != expected:
        raise ValueError(f"Existing rule {name} covers unrelated traffic; refusing to overwrite")
    return matches[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Apply the versioned rules to EdgeOne")
    mode.add_argument("--check", action="store_true", help="Verify the active rules without changing them")
    args = parser.parse_args()

    directory = Path(os.environ.get("SECRET_DIR", "/etc/platform-secrets"))
    read = lambda name: (directory / name).read_text().strip()
    client = TencentCloudClient(read("tencentcloud-secret-id"), read("tencentcloud-secret-key"))
    plan = [(desired, find_existing(fetch_rules(client), desired)) for desired in RULES]

    if args.check:
        drift = [desired["RuleName"] for desired, found in plan if not includes(found, desired)]
        if drift:
            print(
                "Easy SWU EdgeOne rules are missing or differ from the versioned configuration: "
                + ", ".join(drift),
                file=sys.stderr,
            )
            sys.exit(1)
        print(
            "Easy SWU EdgeOne origin timeouts verified: "
            f"campus API {response_timeout(RULES[0])}s, "
            f"watermark decode {response_timeout(RULES[1])}s."
        )
        return

    if not args.apply:
        print(
            json.dumps(
                [
                    {
                        "rule": desired["RuleName"],
                        "action": "update" if found else "create",
                        "response_timeout": response_timeout(desired),
                    }
                    for desired, found in plan
                ],
                ensure_ascii=False,
            )
        )
        return

    for desired, found in plan:
        if found:
            client.call(
                "teo",
                "ModifyL7AccRule",
                {"ZoneId": ZONE, "Rule": dict(desired, RuleId=found["RuleId"])},
            )
        else:
            client.call("teo", "CreateL7AccRules", {"ZoneId": ZONE, "Rules": [desired]})
    print("Easy SWU EdgeOne origin timeout rules applied.")


if __name__ == "__main__":
    try:
        main()
    except TencentApiError as error:
        # API error messages may echo request values; print identifiers only.
        print(
            f"EdgeOne request failed: {error.action} {error.code} request={error.request_id}",
            file=sys.stderr,
        )
        sys.exit(1)
