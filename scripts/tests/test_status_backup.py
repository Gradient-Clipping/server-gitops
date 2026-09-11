import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("status_backup", Path(__file__).parents[1] / "verify-status-backup.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RestoreIsolationTests(unittest.TestCase):
    def test_extracts_only_status_and_replaces_database(self):
        result = module.extract(["-- Current Database: `other`\n", "DROP TABLE other;\n", "-- Current Database: `lazycampus_status`\n", "CREATE DATABASE `lazycampus_status`;\n", "USE `lazycampus_status`;\n", "CREATE TABLE `components` (id INT);\n", "-- Current Database: `other2`\n", "DROP TABLE ignored;\n"], "status_restore_0123456789abcdef")
        self.assertNotIn("lazycampus_status", result)
        self.assertNotIn("DROP TABLE", result)
        self.assertIn("USE `status_restore_0123456789abcdef`", result)

    def test_rejects_production_target(self):
        with self.assertRaises(ValueError):
            module.extract([], "lazycampus_status")

    def test_rejects_cross_database_restore(self):
        with self.assertRaises(ValueError):
            module.extract(["-- Current Database: `lazycampus_status`\n", "USE `mysql`;\n"], "status_restore_0123456789abcdef")

    def test_never_applies_global_dump_epilogue(self):
        result = module.extract(["-- Current Database: `lazycampus_status`\n", "CREATE TABLE `components` (id INT);\n", "/*!80000 SET GLOBAL INNODB_STATS_AUTO_RECALC=@OLD_INNODB_STATS_AUTO_RECALC */;\n"], "status_restore_0123456789abcdef")
        self.assertNotIn("SET GLOBAL", result)

    def test_rejects_destructive_server_level_statement(self):
        with self.assertRaises(ValueError):
            module.extract(["-- Current Database: `lazycampus_status`\n", "DROP DATABASE `lazycampus_status`;\n"], "status_restore_0123456789abcdef")
