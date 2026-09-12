import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("verification_retire", SCRIPTS / "run_agent_verification_retire.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
REVISION = "a" * 40


class RetirementTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE sessions(id TEXT PRIMARY KEY,owner TEXT,title TEXT,created REAL,updated REAL,memory TEXT);
            CREATE TABLE routes(id TEXT PRIMARY KEY,channel TEXT,user_id TEXT,session_id TEXT,selection TEXT,updated REAL);
            CREATE TABLE jobs(id TEXT PRIMARY KEY,route TEXT,session_id TEXT,status TEXT,created REAL,updated REAL,request TEXT);
            CREATE TABLE usage(id TEXT PRIMARY KEY,session_id TEXT,tokens INTEGER);
            CREATE TABLE deliveries(id TEXT PRIMARY KEY,path TEXT);
            INSERT INTO sessions VALUES('ses_test','owner','private prompt',1,2,'private memory');
            INSERT INTO sessions VALUES('ses_real','owner','real title',1,3,'real memory');
            INSERT INTO routes VALUES('verification:test','verification','synthetic','ses_test','["ses_test"]',2);
            INSERT INTO routes VALUES('qq:real','qq','private user','ses_real','["ses_real"]',3);
            INSERT INTO jobs VALUES('j-test','verification:test','ses_test','completed',1,2,'private request');
            INSERT INTO jobs VALUES('j-real','qq:real','ses_real','completed',1,3,'real request');
            INSERT INTO usage VALUES('u-test','ses_test',123);
            INSERT INTO deliveries VALUES('d-test','/workspace/deliveries/test.txt');
        """)

    def plan(self, ids=None, approved=None):
        return runner.retirement(self.db, REVISION, 42, ids or ["ses_test"], approved)

    def test_dry_run_omits_content_and_changes_nothing(self):
        before = list(self.db.iterdump())
        self.db.execute("PRAGMA query_only=ON")
        plan = self.plan()
        self.assertEqual(before, list(self.db.iterdump()))
        self.assertEqual(["ses_test"], plan["session_ids"])
        text = json.dumps(plan)
        for value in ("private prompt", "private memory", "private request", "private user", "real title", "qq:real"):
            self.assertNotIn(value, text)

    def test_apply_only_owner_and_verification_pointers_preserves_retention_clock(self):
        plan = self.plan()
        before = {table: [tuple(row) for row in self.db.execute("SELECT * FROM " + table)]
                  for table in ("jobs", "usage", "deliveries")}
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            report = self.plan(approved=plan)
        self.assertEqual({"applied": True, "retired_sessions": 1, "cleared_verification_routes": 1}, report)
        self.assertEqual(("verification", 2.0, "private prompt", "private memory"), tuple(self.db.execute(
            "SELECT owner,updated,title,memory FROM sessions WHERE id='ses_test'").fetchone()))
        self.assertEqual((None, "[]", 2.0), tuple(self.db.execute(
            "SELECT session_id,selection,updated FROM routes WHERE id='verification:test'").fetchone()))
        self.assertEqual(("ses_real", '["ses_real"]', 3.0), tuple(self.db.execute(
            "SELECT session_id,selection,updated FROM routes WHERE id='qq:real'").fetchone()))
        for table, rows in before.items():
            self.assertEqual(rows, [tuple(row) for row in self.db.execute("SELECT * FROM " + table)])
        self.assertEqual(["ses_real"], [row[0] for row in self.db.execute("SELECT id FROM sessions WHERE owner='owner'")])

    def test_rejects_real_history_current_and_selection_references(self):
        for sql in (
            "INSERT INTO jobs VALUES('mixed','qq:real','ses_test','completed',1,3,'private')",
            "UPDATE routes SET session_id='ses_test' WHERE id='qq:real'",
            "UPDATE routes SET selection='[\"ses_test\"]' WHERE id='qq:real'",
        ):
            with self.subTest(sql=sql):
                self.db.execute("SAVEPOINT test")
                self.db.execute(sql)
                with self.assertRaises(ValueError):
                    self.plan()
                self.db.execute("ROLLBACK TO test")
                self.db.execute("RELEASE test")

    def test_rejects_missing_route_or_forged_channel(self):
        for sql in (
            "INSERT INTO jobs VALUES('orphan','missing','ses_test','completed',1,3,'x')",
            "UPDATE routes SET channel='qq' WHERE id='verification:test'",
            "UPDATE routes SET id='looks-real' WHERE id='verification:test'",
        ):
            with self.subTest(sql=sql):
                self.db.execute("SAVEPOINT test")
                self.db.execute(sql)
                with self.assertRaises(ValueError):
                    self.plan()
                self.db.execute("ROLLBACK TO test")
                self.db.execute("RELEASE test")

    def test_rejects_active_and_unknown_job_status_even_before_session_assignment(self):
        for status in ("preparing", "queued", "running", "new-unknown-status"):
            with self.subTest(status=status):
                self.db.execute("INSERT INTO jobs VALUES('active','verification:test',NULL,?,1,3,'x')", (status,))
                with self.assertRaises(ValueError):
                    self.plan()
                self.db.execute("DELETE FROM jobs WHERE id='active'")

    def test_does_not_detach_real_session_selected_on_verification_route(self):
        self.db.execute("UPDATE routes SET session_id='ses_real' WHERE id='verification:test'")
        with self.assertRaises(ValueError):
            self.plan()

    def test_invalid_selection_aborts_reference_check(self):
        for selection in ("not-json", "{}", "[12]"):
            with self.subTest(selection=selection):
                self.db.execute("UPDATE routes SET selection=? WHERE id='qq:real'", (selection,))
                with self.assertRaises(ValueError):
                    self.plan()

    def test_requires_exact_explicit_eligible_ids(self):
        for ids in (["ses_missing"], ["ses_real"], ["ses_test", "ses_test"], ["not-an-id"]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.plan(ids=ids)
        with self.assertRaises(ValueError):
            runner.retirement(self.db, REVISION, 42, [])

    def test_stale_or_tampered_plan_changes_nothing(self):
        original = self.plan()
        tampered = copy.deepcopy(original)
        tampered["sessions"][0]["owner"] = "verification"
        with self.assertRaises(ValueError):
            self.plan(approved=tampered)
        self.db.execute("UPDATE sessions SET updated=4 WHERE id='ses_test'")
        with self.assertRaises(ValueError):
            self.plan(approved=original)
        self.assertEqual("owner", self.db.execute("SELECT owner FROM sessions WHERE id='ses_test'").fetchone()[0])

    def test_new_reference_after_dry_run_aborts(self):
        plan = self.plan()
        self.db.execute("INSERT INTO jobs VALUES('later','verification:test','ses_test','completed',1,4,'x')")
        with self.assertRaises(ValueError):
            self.plan(approved=plan)

    def test_transaction_rolls_back_if_route_update_fails(self):
        self.db.executescript("""CREATE TRIGGER fail_route BEFORE UPDATE ON routes BEGIN SELECT RAISE(ABORT,'test'); END;""")
        plan = self.plan()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                self.plan(approved=plan)
        self.assertEqual("owner", self.db.execute("SELECT owner FROM sessions WHERE id='ses_test'").fetchone()[0])

    def test_reviewed_file_requires_digest_revision_run_and_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "plan.json"
            payload = json.dumps(self.plan()).encode()
            path.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            self.assertEqual(self.plan(), runner.reviewed_plan(path, digest, REVISION, 42, ["ses_test"]))
            for values in (("0" * 64, REVISION, 42, ["ses_test"]), (digest, "b" * 40, 42, ["ses_test"]),
                           (digest, REVISION, 43, ["ses_test"]), (digest, REVISION, 42, ["ses_real"]),
                           (None, REVISION, 42, ["ses_test"])):
                with self.subTest(values=values), self.assertRaises(ValueError):
                    runner.reviewed_plan(path, *values)

    def test_remote_transaction_is_fixed_path_and_uses_archived_function(self):
        source = (SCRIPTS / "run_agent_verification_retire.py").read_text()
        code = runner.remote_source(source, REVISION, 42, ["ses_test"], self.plan()).decode()
        compile(code, "<remote>", "exec")
        self.assertIn("file:/state/agent.sqlite3?mode=", code)
        self.assertIn("'BEGIN IMMEDIATE'", code)
        self.assertIn("PRAGMA query_only=ON", code)
        self.assertNotIn("DELETE FROM", code)

    def test_gate_requires_successful_exact_production(self):
        config = {"repository": "Gradient-Clipping/server-gitops", "sourceBranch": "main", "productionBranch": "production",
                  "validationWorkflow": ".github/workflows/validate.yml", "validationJob": "validate"}
        run = {"head_sha": REVISION, "head_branch": "main", "event": "push", "path": ".github/workflows/validate.yml",
               "head_repository": {"full_name": config["repository"]}, "status": "completed", "conclusion": "success"}
        jobs = {"jobs": [{"name": "validate", "conclusion": "success"}]}
        with patch.object(runner, "api", side_effect=[run, jobs, {"object": {"sha": REVISION}}]):
            runner.gate(config, REVISION, 42)
        with patch.object(runner, "api", side_effect=[run, jobs, {"object": {"sha": "b" * 40}}]):
            with self.assertRaises(ValueError):
                runner.gate(config, REVISION, 42)
        run["conclusion"] = "failure"
        with patch.object(runner, "api", side_effect=[run, jobs]):
            with self.assertRaises(ValueError):
                runner.gate(config, REVISION, 42)


if __name__ == "__main__":
    unittest.main()
