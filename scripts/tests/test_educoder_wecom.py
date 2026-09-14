import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("educoder_edge", ROOT / "scripts/reconcile-educoder-edge.py")
edge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edge)


class CallbackDeploymentTests(unittest.TestCase):
    def test_previous_callback_rule_can_be_migrated_but_unrelated_hosts_cannot(self):
        legacy = {"RuleName": edge.LEGACY_NAME, "RuleId": "rule-owned",
                  "Branches": [{"Condition": edge.LEGACY_CONDITION}]}
        self.assertEqual(edge.managed_rules([legacy]), [legacy])
        current = edge.desired_rule("a" * 64)
        with self.assertRaises(ValueError):
            edge.managed_rules([legacy, current])
        legacy["Branches"][0]["Condition"] = "${http.request.host} in ['unrelated.example']"
        with self.assertRaises(ValueError):
            edge.managed_rules([legacy])

    def test_edge_rule_is_host_scoped_and_disables_cache(self):
        rule = edge.desired_rule("a" * 64)
        self.assertEqual(rule["RuleName"], "WeCom KF Callback")
        branch, = rule["Branches"]
        self.assertEqual(branch["Condition"], "${http.request.host} in ['kf.lazycampus.com']")
        actions = {action["Name"]: action for action in branch["Actions"]}
        self.assertEqual(actions["Cache"]["CacheParameters"], {"NoCache": {"Switch": "on"}})
        self.assertEqual(actions["OfflineCache"]["OfflineCacheParameters"], {"Switch": "off"})
        header, = actions["ModifyRequestHeader"]["ModifyRequestHeaderParameters"]["HeaderActions"]
        self.assertEqual(header, {"Action": "set", "Name": "X-Educoder-Origin-Key", "Value": "a" * 64})

    def test_invalid_origin_key_is_rejected_without_echo(self):
        with self.assertRaises(ValueError) as error:
            edge.desired_rule("not-a-valid-secret")
        self.assertNotIn("not-a-valid-secret", str(error.exception))

    def test_admin_and_public_https_keep_callback_exact_and_origin_protected(self):
        base = ROOT / "clusters/easy-platform/apps/wecom-kf"
        ingress = (base / "ingress.yaml").read_text()
        self.assertIn("/admin", ingress)
        self.assertIn("path: /callbacks/wecom/kf\n            pathType: Exact", ingress)
        network = (base / "network-policy.yaml").read_text()
        self.assertIn("443", network)
        self.assertIn("169.254.0.0/16", network)
        nginx = (ROOT / "host/nginx/educoder-wecom").read_text()
        self.assertIn("access_log off;", nginx)
        self.assertIn("return 403;", nginx)
        self.assertIn("return 404;", nginx)
