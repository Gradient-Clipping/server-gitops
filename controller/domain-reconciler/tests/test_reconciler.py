from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from domain_reconciler.reconciler import (
    DesiredHost,
    DomainReconciler,
    SafetyConflict,
    ZoneConfig,
    load_zones,
    normalize_hostname,
    record_name,
    select_zone,
)


class FakeTencentClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def call(
        self, service: str, action: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((service, action, payload))
        return {"RequestId": "mutate"}


class FakeCloudflareClient:
    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self.records = records or []
        self.calls: list[tuple[str, str, str | None, dict[str, Any] | None]] = []

    def list_dns_records(self, zone_id: str, hostname: str) -> list[dict[str, Any]]:
        self.calls.append(("list", zone_id, hostname, None))
        return self.records

    def create_dns_record(
        self, zone_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(("create", zone_id, None, payload))
        return {"result": {"id": "created"}}

    def overwrite_dns_record(
        self, zone_id: str, record_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(("overwrite", zone_id, record_id, payload))
        return {"result": {"id": record_id}}


class ConfigurationTests(unittest.TestCase):
    def test_load_and_select_longest_zone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "zones.json"
            path.write_text(
                json.dumps(
                    {
                        "zones": [
                            {
                                "domain": "example.com",
                                "mode": "direct",
                                "origin": "1.2.3.4",
                                "cloudflareZoneId": "a" * 32,
                            },
                            {
                                "domain": "apps.example.com",
                                "mode": "edgeone",
                                "origin": "1.2.3.4",
                                "cloudflareZoneId": "b" * 32,
                                "edgeoneZoneId": "zone-test",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            zones = load_zones(path)

        self.assertEqual(
            select_zone("api.apps.example.com", zones).domain, "apps.example.com"
        )
        self.assertEqual(select_zone("www.example.com", zones).domain, "example.com")

    def test_hostname_and_record_name(self) -> None:
        self.assertEqual(normalize_hostname("WWW.Example.COM."), "www.example.com")
        self.assertEqual(record_name("example.com", "example.com"), "@")
        self.assertEqual(record_name("a.b.example.com", "example.com"), "a.b")


class DnsReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.zone = ZoneConfig(
            domain="example.com",
            mode="direct",
            origin="1.2.3.4",
            cloudflare_zone_id="a" * 32,
        )

    def desired(self, adopt: bool = True) -> DesiredHost:
        return DesiredHost("www.example.com", self.zone, adopt, "default/web")

    def test_creates_missing_record(self) -> None:
        client = FakeCloudflareClient()
        ready = DomainReconciler(FakeTencentClient(), client)._ensure_dns(
            self.desired(), "A", "1.2.3.4"
        )
        self.assertFalse(ready)
        self.assertEqual([call[0] for call in client.calls], ["list", "create"])
        self.assertFalse(client.calls[-1][3]["proxied"])

    def test_matching_record_is_ready(self) -> None:
        client = FakeCloudflareClient(
            [
                {
                    "id": "record-1",
                    "type": "A",
                    "content": "1.2.3.4",
                    "ttl": 1,
                    "proxied": False,
                }
            ]
        )
        ready = DomainReconciler(FakeTencentClient(), client)._ensure_dns(
            self.desired(), "A", "1.2.3.4"
        )
        self.assertTrue(ready)
        self.assertEqual([call[0] for call in client.calls], ["list"])

    def test_adopts_existing_record_in_place(self) -> None:
        client = FakeCloudflareClient(
            [
                {
                    "id": "record-7",
                    "type": "A",
                    "content": "1.2.3.4",
                    "ttl": 1,
                    "proxied": False,
                }
            ]
        )
        ready = DomainReconciler(FakeTencentClient(), client)._ensure_dns(
            self.desired(adopt=True), "CNAME", "www.example.com.eo.dnse.test"
        )
        self.assertFalse(ready)
        self.assertEqual(client.calls[-1][0], "overwrite")
        self.assertEqual(client.calls[-1][2], "record-7")
        self.assertEqual(client.calls[-1][3]["type"], "CNAME")
        self.assertFalse(client.calls[-1][3]["proxied"])

    def test_refuses_unapproved_adoption(self) -> None:
        client = FakeCloudflareClient(
            [
                {
                    "id": "record-7",
                    "type": "A",
                    "content": "1.2.3.4",
                    "ttl": 1,
                    "proxied": False,
                }
            ]
        )
        with self.assertRaises(SafetyConflict):
            DomainReconciler(FakeTencentClient(), client)._ensure_dns(
                self.desired(adopt=False), "CNAME", "www.example.com.eo.dnse.test"
            )
        self.assertEqual([call[0] for call in client.calls], ["list"])

    def test_refuses_ambiguous_records(self) -> None:
        client = FakeCloudflareClient(
            [
                {
                    "id": "record-1",
                    "type": "A",
                    "content": "1.2.3.4",
                    "ttl": 1,
                    "proxied": False,
                },
                {
                    "id": "record-2",
                    "type": "AAAA",
                    "content": "2001:db8::1",
                    "ttl": 1,
                    "proxied": False,
                },
            ]
        )
        with self.assertRaises(SafetyConflict):
            DomainReconciler(FakeTencentClient(), client)._ensure_dns(
                self.desired(), "A", "1.2.3.4"
            )
        self.assertEqual([call[0] for call in client.calls], ["list"])


class EdgeOriginTests(unittest.TestCase):
    def test_null_host_header_is_equivalent_to_default(self) -> None:
        zone = ZoneConfig(
            domain="example.com",
            mode="edgeone",
            origin="1.2.3.4",
            cloudflare_zone_id="a" * 32,
            edgeone_zone_id="zone-test",
        )
        desired = DesiredHost("www.example.com", zone, True, "default/web")
        existing = {
            "OriginDetail": {
                "OriginType": "IP_DOMAIN",
                "Origin": "1.2.3.4",
                "HostHeader": None,
            },
            "IPv6Status": "follow",
        }
        self.assertEqual(DomainReconciler._edge_origin_drift(desired, existing), [])


if __name__ == "__main__":
    unittest.main()
