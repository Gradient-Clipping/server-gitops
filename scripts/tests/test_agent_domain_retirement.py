from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "controller/domain-reconciler/src"))
import retire_agent_admin_domain as retirement


class Edge:
    def __init__(self):
        self.previous = {"DomainName": retirement.OLD, "DomainStatus": "online", "Cname": "owned.edge.example"}
        self.other = {"DomainName": retirement.NEW, "DomainStatus": "online"}
        self.writes = []

    def call(self, service, action, payload):
        if action == "DescribeAccelerationDomains":
            rows = [self.other] + ([self.previous] if self.previous else [])
            return {"AccelerationDomains": rows, "TotalCount": len(rows)}
        self.writes.append((action, deepcopy(payload)))
        if action == "ModifyAccelerationDomainStatuses":
            self.previous["DomainStatus"] = "offline"
        elif action == "DeleteAccelerationDomains":
            self.previous = None
        else:
            raise AssertionError(action)
        return {}


class DNS:
    def __init__(self):
        self.records = [{"id": "a" * 32, "name": retirement.OLD, "type": "CNAME", "content": "owned.edge.example", "proxied": False}]
        self.writes = []

    def list_dns_records(self, zone, host):
        assert zone == retirement.CF_ZONE and host == retirement.OLD
        return deepcopy(self.records)

    def _request(self, action, method, path):
        self.writes.append((action, method, path))
        self.records = []


class RetirementTests(unittest.TestCase):
    def test_deletion_is_scoped_backed_up_and_idempotent(self):
        edge, dns = Edge(), DNS()
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary) / "before.json"
            result = retirement.retire(edge, dns, apply=True, snapshot=snapshot, pause=lambda _: None)
            self.assertTrue(result["dns_removed"] and result["edgeone_removed"])
            saved = json.loads(snapshot.read_text())
            self.assertEqual(saved["edgeone"]["DomainStatus"], "online")
            self.assertEqual(saved["dns"][0]["name"], retirement.OLD)
            self.assertEqual(edge.other["DomainName"], retirement.NEW)
            self.assertEqual([call[0] for call in edge.writes], ["ModifyAccelerationDomainStatuses", "DeleteAccelerationDomains"])
            self.assertTrue(all(call[1]["DomainNames"] == [retirement.OLD] and call[1]["Force"] is False for call in edge.writes))
            self.assertEqual(dns.writes, [("DeleteDnsRecord", "DELETE", f"/zones/{retirement.CF_ZONE}/dns_records/" + "a" * 32)])
            retirement.retire(edge, dns, apply=True, snapshot=snapshot)
            self.assertEqual(len(edge.writes), 2)
            self.assertEqual(json.loads(snapshot.read_text()), saved)

    def test_unknown_dns_ownership_or_ambiguous_records_refuse_all_writes(self):
        for changes in ({"name": retirement.NEW}, {"type": "TXT"}, {"proxied": True}, {"content": "someone-else.example"}):
            edge, dns = Edge(), DNS()
            dns.records[0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                retirement.retire(edge, dns, apply=True)
            self.assertFalse(edge.writes or dns.writes)
        edge, dns = Edge(), DNS()
        dns.records.append(deepcopy(dns.records[0]))
        with self.assertRaises(ValueError):
            retirement.retire(edge, dns, apply=True)
        self.assertFalse(edge.writes or dns.writes)

    def test_readonly_check_never_disables_or_deletes(self):
        edge, dns = Edge(), DNS()
        with self.assertRaises(ValueError):
            retirement.retire(edge, dns)
        self.assertFalse(edge.writes or dns.writes)


if __name__ == "__main__":
    unittest.main()
