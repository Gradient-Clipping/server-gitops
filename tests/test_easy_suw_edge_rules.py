"""Guards the versioned Easy SWU EdgeOne timeout rules; no network access."""
import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/reconcile-easy-swu-edge.py"


def load_module():
    spec = importlib.util.spec_from_file_location("reconcile_easy_swu_edge", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EasySwuEdgeRulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def rules(self):
        return {rule["RuleName"]: rule for rule in self.module.RULES}

    def timeout(self, name):
        actions = self.rules()[name]["Branches"][0]["Actions"]
        return actions[0]["HTTPUpstreamTimeoutParameters"]["ResponseTimeout"]

    def test_campus_api_budget_is_forty_seconds(self):
        self.assertEqual(self.timeout("Easy SWU upstream timeout"), 40)

    def test_watermark_decode_keeps_a_longer_budget_than_the_campus_api(self):
        self.assertEqual(self.timeout("Easy SWU watermark decode timeout"), 120)
        self.assertGreater(
            self.timeout("Easy SWU watermark decode timeout"),
            self.timeout("Easy SWU upstream timeout"),
        )

    def test_general_rule_excludes_the_watermark_path(self):
        # EdgeOne assigns RulePriority itself and rejects `not (...)` and
        # `not in`, so only the prefixed `and not X in [...]` form compiles and
        # only mutual exclusion can protect the watermark budget.
        general = self.rules()["Easy SWU upstream timeout"]["Branches"][0]["Condition"]
        watermark = self.rules()["Easy SWU watermark decode timeout"]["Branches"][0]["Condition"]
        self.assertIn(f"and not ${{http.request.uri.path}} in ['{self.module.WATERMARK_PATH}']", general)
        self.assertIn(self.module.WATERMARK_PATH, watermark)

    def test_each_rule_sets_exactly_one_timeout_action(self):
        for name, rule in self.rules().items():
            with self.subTest(rule=name):
                self.assertEqual(len(rule["Branches"]), 1)
                self.assertEqual(
                    [action["Name"] for action in rule["Branches"][0]["Actions"]],
                    ["HTTPUpstreamTimeout"],
                )

    def test_includes_ignores_fields_edgeone_returns(self):
        self.assertTrue(
            self.module.includes({"RuleName": "x", "RuleId": "rule-1"}, {"RuleName": "x"})
        )
        self.assertFalse(self.module.includes({"RuleName": "y"}, {"RuleName": "x"}))


if __name__ == "__main__":
    unittest.main()
