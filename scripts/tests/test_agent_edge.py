from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("agent_edge", ROOT / "scripts/reconcile-agent-edge.py")
edge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edge)
import run_agent_edge as runner  # noqa: E402


class Client:
    def __init__(self, rules):
        self.rules, self.calls = rules, []

    def call(self, service, action, payload):
        self.calls.append((service, action, deepcopy(payload)))
        if action == "DescribeL7AccRules":
            return {"Rules": self.rules, "TotalCount": len(self.rules)}
        return {}


def live_rule():
    rule = edge.desired_rule()
    rule["RuleId"] = "rule-agent"
    rule["Priority"] = 8
    rule["Branches"][0]["BranchId"] = "branch-generated"
    return rule


class AgentEdgeTests(unittest.TestCase):
    def test_scopes_two_agent_hosts_and_enables_interactive_transport(self):
        rule = edge.desired_rule()
        self.assertEqual(rule["Branches"][0]["Condition"], "${http.request.host} in ['preview.lazycampus.com', 'agent.lazycampus.com']")
        actions = {item["Name"]: item for item in rule["Branches"][0]["Actions"]}
        self.assertEqual(set(actions), {"WebSocket", "Cache", "OfflineCache"})
        self.assertEqual(actions["WebSocket"]["WebSocketParameters"], {"Switch": "on", "Timeout": 120})
        self.assertEqual(actions["Cache"]["CacheParameters"], {"NoCache": {"Switch": "on"}})

    def test_default_plan_is_read_only_and_apply_is_idempotent(self):
        client = Client([])
        self.assertEqual(edge.reconcile(client, "plan"), "create")
        self.assertEqual([call[1] for call in client.calls], ["DescribeL7AccRules"])
        self.assertEqual(edge.reconcile(client, "apply"), "created")
        self.assertEqual(client.calls[-1], ("teo", "CreateL7AccRules", {"ZoneId": edge.ZONE, "Rules": [edge.desired_rule()]}))
        client = Client([live_rule()])
        self.assertEqual(edge.reconcile(client, "apply"), "unchanged")
        self.assertEqual(edge.reconcile(client, "check"), "verified")
        self.assertTrue(all(call[1] == "DescribeL7AccRules" for call in client.calls))

    def test_update_only_our_rule_preserves_unrelated_rules(self):
        current = live_rule()
        current["Branches"][0]["Actions"][0]["WebSocketParameters"]["Switch"] = "off"
        unrelated = {"RuleId": "rule-other", "RuleName": "Other product", "Branches": []}
        client = Client([unrelated, current])
        self.assertEqual(edge.reconcile(client, "apply"), "updated")
        payload = client.calls[-1][2]
        self.assertEqual(client.calls[-1][1], "ModifyL7AccRule")
        self.assertEqual(payload["Rule"]["RuleId"], "rule-agent")
        self.assertEqual(unrelated, {"RuleId": "rule-other", "RuleName": "Other product", "Branches": []})

    def test_refuses_ambiguous_foreign_or_broader_rule(self):
        cases = [[live_rule(), live_rule()]]
        for change in ("description", "condition", "branches"):
            rule = live_rule()
            if change == "description":
                rule["Description"] = ["Unmanaged"]
            elif change == "condition":
                rule["Branches"][0]["Condition"] = "${http.request.host} in ['lazycampus.com']"
            else:
                rule["Branches"].append(deepcopy(rule["Branches"][0]))
            cases.append([rule])
        for rules in cases:
            with self.subTest(rules=rules):
                client = Client(rules)
                with self.assertRaises(ValueError):
                    edge.reconcile(client, "apply")
                self.assertEqual([call[1] for call in client.calls], ["DescribeL7AccRules"])

    def test_check_catches_missing_rule_and_unexpected_actions(self):
        with self.assertRaises(ValueError):
            edge.reconcile(Client([]), "check")
        current = live_rule()
        current["Branches"][0]["Actions"].append({"Name": "Unexpected"})
        with self.assertRaises(ValueError):
            edge.reconcile(Client([current]), "check")

    def test_known_previous_hostname_rule_migrates_without_creating_a_duplicate(self):
        current = live_rule()
        current["Branches"][0]["Condition"] = edge.PREVIOUS_CONDITION
        client = Client([current])
        self.assertEqual(edge.reconcile(client, "apply"), "updated")
        self.assertEqual(client.calls[-1][1], "ModifyL7AccRule")
        self.assertEqual(client.calls[-1][2]["Rule"]["RuleId"], current["RuleId"])

    def test_gate_requires_exact_successful_production_revision(self):
        config = {"repository": "Gradient-Clipping/server-gitops", "sourceBranch": "main", "productionBranch": "production",
                  "validationWorkflow": ".github/workflows/validate.yml", "validationJob": "validate"}
        revision = "a" * 40
        valid = {"head_sha": revision, "head_branch": "main", "event": "push", "path": config["validationWorkflow"],
                 "head_repository": {"full_name": config["repository"]}, "status": "completed", "conclusion": "success"}
        responses = [valid, {"jobs": [{"name": "validate", "conclusion": "success"}]}, {"object": {"sha": revision}}]
        with patch.object(runner, "api", side_effect=responses):
            runner.gate(config, revision, 123)
        responses[-1] = {"object": {"sha": "b" * 40}}
        with patch.object(runner, "api", side_effect=responses), self.assertRaises(ValueError):
            runner.gate(config, revision, 123)
        invalid = {**valid, "event": "pull_request"}
        with patch.object(runner, "api", side_effect=[invalid, responses[1]]), self.assertRaises(ValueError):
            runner.gate(config, revision, 123)


if __name__ == "__main__":
    unittest.main()
