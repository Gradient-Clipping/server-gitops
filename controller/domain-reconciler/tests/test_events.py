from __future__ import annotations

import copy
import datetime as dt
import io
import json
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from domain_reconciler.reconciler import (
    ADOPT_ANNOTATION,
    MANAGED_ANNOTATION,
    REQUEST_ANNOTATION,
    CloudflareApiError,
    CloudflareClient,
    DesiredHost,
    DomainReconciler,
    HealthState,
    HostResult,
    KubernetesIngressSource,
    RateLimited,
    SafetyConflict,
    TencentCloudClient,
    ZoneConfig,
    safe_error,
)
from domain_reconciler.runtime import ConfigFile, HostScheduler, run_controller
from domain_reconciler.watch import IngressWatch, WatchExpired


DIRECT = ZoneConfig("example.org", "direct", "1.2.3.4", "a" * 32)
EDGE = ZoneConfig(
    "example.com", "edgeone", "1.2.3.4", "b" * 32, edgeone_zone_id="zone-test"
)


def host(name="www.example.org", zone=DIRECT):
    return DesiredHost(name, zone, True, "default/web")


def ingress(name="www.example.org", version="1"):
    return {
        "metadata": {
            "namespace": "default",
            "name": name,
            "resourceVersion": version,
            "annotations": {MANAGED_ANNOTATION: "enabled", ADOPT_ANNOTATION: "true"},
        },
        "spec": {"rules": [{"host": name}]},
    }


def source():
    result = object.__new__(KubernetesIngressSource)
    result.annotation = MANAGED_ANNOTATION
    result.adopt_annotation = ADOPT_ANNOTATION
    result.timeout = 15
    result.context = None
    return result


def dns_record(desired, value=None):
    return {
        "id": desired.hostname,
        "name": desired.hostname,
        "type": "CNAME" if desired.zone.mode == "edgeone" else "A",
        "content": value or desired.zone.origin,
        "ttl": 1,
        "proxied": False,
    }


def edge_domain(desired, cert_status="deployed"):
    return {
        "DomainName": desired.hostname,
        "DomainStatus": "online",
        "Cname": "target.example.net",
        "OriginDetail": {"OriginType": "IP_DOMAIN", "Origin": desired.zone.origin},
        "Certificate": {
            "Mode": "eofreecert",
            "List": [
                {
                    "Status": cert_status,
                    "ExpireTime": (
                        dt.datetime.now(dt.UTC) + dt.timedelta(days=30)
                    ).isoformat(),
                }
            ],
        },
    }


class FakeDNS(CloudflareClient):
    def __init__(self, records):
        super().__init__("test-token")
        self.records = records
        self.writes = []

    def _request(self, action, method, path, *, query=None, payload=None):
        self.before_request("cloudflare." + action)
        if action == "ListDnsRecords":
            batch = [
                r
                for r in self.records
                if (r["name"].endswith("example.org") == ("a" * 32 in path))
            ]
            return {
                "result": batch,
                "result_info": {"total_pages": 1, "total_count": len(batch)},
            }
        self.writes.append((action, path, payload))
        return {"result": {"id": "test-id"}}


class FakeEdge(TencentCloudClient):
    def __init__(self, domains=()):
        super().__init__("test-id", "test-key")
        self.domains = list(domains)
        self.writes = []

    def call(self, service, action, payload):
        self.before_request("edgeone." + action)
        if action == "DescribeAccelerationDomains":
            return {
                "AccelerationDomains": self.domains,
                "TotalCount": len(self.domains),
            }
        self.writes.append((action, payload))
        return {"RequestId": "test-id"}


class SchedulerTests(unittest.TestCase):
    def test_stable_hosts_only_run_at_startup_and_hourly(self):
        scheduler = HostScheduler(3600, 0, lambda: 1)
        scheduler.update([host()], 0)
        selected, _ = scheduler.take_due(0)
        self.assertEqual(selected, [host()])
        scheduler.complete({host().hostname: HostResult("ready")}, 1)
        for now in range(2, 3600):
            scheduler.update([host()], now)
            self.assertEqual(scheduler.take_due(now)[0], [])
        self.assertEqual(scheduler.take_due(3600), ([host()], "hourly_audit"))

    def test_pending_host_backoff_does_not_wake_ready_hosts(self):
        scheduler = HostScheduler(3600, 0, lambda: 1)
        ready, pending = host(), host("new.example.org")
        scheduler.update([ready, pending], 0)
        scheduler.take_due(0)
        scheduler.complete(
            {
                ready.hostname: HostResult("ready"),
                pending.hostname: HostResult("pending"),
            },
            0,
        )
        now = 0
        for delay in (15, 30, 60, 120, 240, 300, 300):
            self.assertEqual(scheduler.take_due(now + delay - 1)[0], [])
            now += delay
            self.assertEqual(scheduler.take_due(now)[0], [pending])
            scheduler.complete({pending.hostname: HostResult("pending")}, now)

    def test_changes_are_coalesced_and_do_not_postpone_hourly_audit(self):
        scheduler = HostScheduler(3600, 0, lambda: 1)
        scheduler.update([host()], 0)
        scheduler.take_due(0)
        scheduler.complete({host().hostname: HostResult("ready")}, 0)
        changed = DesiredHost(host().hostname, DIRECT, True, "default/web", "new")
        scheduler.update([changed], 100)
        scheduler.update([changed], 101)
        self.assertEqual(scheduler.take_due(105)[0], [changed])
        self.assertEqual(scheduler.next_audit, 3600)

    def test_removed_hosts_drop_retries_and_errors(self):
        scheduler = HostScheduler(3600, 0)
        scheduler.update([host()], 0)
        scheduler.take_due(0)
        scheduler.complete({host().hostname: HostResult("error")}, 1)
        scheduler.update([], 2)
        self.assertFalse(scheduler.errors)
        self.assertFalse(scheduler.due)
        self.assertEqual(scheduler.take_due(3600)[0], [])

    def test_retry_after_is_a_minimum(self):
        scheduler = HostScheduler(3600, 0, lambda: 1.1)
        scheduler.update([host()], 0)
        scheduler.take_due(0)
        scheduler.complete({host().hostname: HostResult("error", 600)}, 10)
        self.assertEqual(scheduler.take_due(610)[0], [])
        self.assertEqual(scheduler.take_due(671)[0], [host()])


class BatchTests(unittest.TestCase):
    def test_twelve_hosts_need_three_reads_and_zero_writes(self):
        desired = [host(f"h{i}.example.com", EDGE) for i in range(10)] + [
            host(),
            host("example.org"),
        ]
        dns = FakeDNS(
            [
                dns_record(h, "target.example.net" if h.zone == EDGE else None)
                for h in desired
            ]
        )
        edge = FakeEdge([edge_domain(h) for h in desired if h.zone == EDGE])
        reconciler = DomainReconciler(edge, dns)
        self.assertTrue(
            all(
                r.state == "ready" for r in reconciler.reconcile_batch(desired).values()
            )
        )
        self.assertEqual(
            reconciler.request_counts(),
            {"cloudflare.ListDnsRecords": 2, "edgeone.DescribeAccelerationDomains": 1},
        )
        self.assertEqual(dns.writes + edge.writes, [])

    def test_only_selected_host_is_mutated_from_zone_snapshot(self):
        desired = host()
        unrelated = dns_record(host("other.example.org"), "9.9.9.9")
        dns = FakeDNS([dns_record(desired, "9.9.9.9"), unrelated])
        result = DomainReconciler(FakeEdge(), dns).reconcile_batch([desired])
        self.assertEqual(result[desired.hostname].state, "pending")
        self.assertEqual(len(dns.writes), 1)
        self.assertEqual(dns.writes[0][2]["name"], desired.hostname)

    def test_failure_in_one_zone_does_not_block_another(self):
        dns = FakeDNS([dns_record(host())])
        original = dns.list_dns_records
        dns.list_dns_records = lambda zone: (
            original(zone)
            if zone == DIRECT.cloudflare_zone_id
            else (_ for _ in ()).throw(
                CloudflareApiError("ListDnsRecords", "403", "denied")
            )
        )
        results = DomainReconciler(FakeEdge(), dns).reconcile_batch(
            [host(), host("www.example.com", EDGE)]
        )
        self.assertEqual(results[host().hostname].state, "ready")
        self.assertEqual(results["www.example.com"].state, "error")
        self.assertFalse(dns.writes)

    def test_applying_certificate_is_pending_without_reapplication(self):
        desired = host("www.example.com", EDGE)
        dns = FakeDNS([dns_record(desired, "target.example.net")])
        edge = FakeEdge([edge_domain(desired, "applying")])
        reconciler = DomainReconciler(edge, dns)
        for _ in range(2):
            self.assertEqual(
                reconciler.reconcile_batch([desired])[desired.hostname].state, "pending"
            )
        self.assertFalse(edge.writes)
        edge.domains[0]["Certificate"]["List"][0]["Status"] = "deployed"
        self.assertEqual(
            reconciler.reconcile_batch([desired])[desired.hostname].state, "ready"
        )

    def test_expired_or_unknown_certificate_is_not_ready(self):
        for cert in (
            {},
            {"Status": "deployed"},
            {"Status": "deployed", "ExpireTime": "invalid"},
            {"Status": "deployed", "ExpireTime": "2000-01-01T00:00:00Z"},
        ):
            with self.subTest(cert=cert):
                self.assertFalse(DomainReconciler._certificate_ready({"List": [cert]}))

    def test_invalid_edge_snapshot_cannot_create_domains(self):
        desired = host("www.example.com", EDGE)
        edge = FakeEdge()
        edge.call = Mock(return_value={})
        dns = FakeDNS([dns_record(desired, "target.example.net")])
        results = DomainReconciler(edge, dns).reconcile_batch([desired])
        self.assertEqual(results[desired.hostname].state, "error")
        self.assertEqual(edge.call.call_count, 1)
        self.assertFalse(dns.writes)


class ProviderTests(unittest.TestCase):
    def test_duplicate_pagination_record_cannot_mask_missing_records(self):
        client = CloudflareClient("test")
        client._request = Mock(
            return_value={
                "result": [dns_record(host())],
                "result_info": {"total_pages": 2, "total_count": 2},
            }
        )
        with self.assertRaises(CloudflareApiError):
            client.list_dns_records("zone")

    def test_cloudflare_pagination_lists_zone_without_hostname_filter(self):
        client = CloudflareClient("test")
        client._request = Mock(
            side_effect=[
                {
                    "result": [dns_record(host())],
                    "result_info": {"total_pages": 2, "total_count": 2},
                },
                {
                    "result": [dns_record(host("other.example.org"))],
                    "result_info": {"total_pages": 2, "total_count": 2},
                },
            ]
        )
        self.assertEqual(len(client.list_dns_records(DIRECT.cloudflare_zone_id)), 2)
        self.assertEqual(
            [c.kwargs["query"] for c in client._request.call_args_list],
            [{"page": 1, "per_page": 100}, {"page": 2, "per_page": 100}],
        )

    def test_incomplete_cloudflare_snapshot_is_rejected(self):
        for document in (
            {"result": None},
            {"result": []},
            {"result": [], "result_info": {"total_pages": 2, "total_count": 2}},
            {"result": [None], "result_info": {"total_pages": 1, "total_count": 1}},
            {"result": [], "result_info": {"total_pages": 1, "total_count": 1}},
        ):
            with self.subTest(document=document):
                client = CloudflareClient("test")
                client._request = Mock(return_value=document)
                with self.assertRaises(CloudflareApiError):
                    client.list_dns_records("zone")

    def test_429_respects_retry_after_across_hosts_and_zones(self):
        client = CloudflareClient("test")
        error = urllib.error.HTTPError(
            "https://example.com",
            429,
            "throttled",
            {"Retry-After": "600"},
            io.BytesIO(),
        )
        with patch("urllib.request.urlopen", side_effect=error) as urlopen:
            for zone in ("one", "two"):
                with self.assertRaises(RateLimited) as raised:
                    client.list_dns_records(zone)
                self.assertGreater(raised.exception.retry_after, 599)
            self.assertEqual(urlopen.call_count, 1)
            self.assertEqual(client.request_counts["cloudflare.ListDnsRecords"], 1)

    def test_logs_never_include_exception_messages(self):
        self.assertEqual(
            safe_error(ValueError("Authorization: secret")),
            {"error_type": "ValueError"},
        )
        self.assertNotIn(
            "secret",
            json.dumps(
                safe_error(CloudflareApiError("ListDnsRecords", "403", "secret"))
            ),
        )


class WatchTests(unittest.TestCase):
    def test_list_pagination_preserves_snapshot_resource_version(self):
        src = source()
        src._get_json = Mock(
            side_effect=[
                {
                    "items": [ingress()],
                    "metadata": {"resourceVersion": "9", "continue": "next"},
                },
                {
                    "items": [ingress("other.example.org")],
                    "metadata": {"resourceVersion": "9"},
                },
            ]
        )
        items, version = src.list_snapshot()
        self.assertEqual((len(items), version), (2, "9"))
        self.assertIn("continue=next", src._get_json.call_args_list[1].args[0])

    def test_projected_token_is_reread_on_reconnect(self):
        src = source()
        src.base_url = "https://example.test"
        with tempfile.TemporaryDirectory() as directory:
            src.token_path = Path(directory) / "token"
            src.token_path.write_text("first\n")
            self.assertEqual(
                src._request("/").get_header("Authorization"), "Bearer first"
            )
            src.token_path.write_text("second\n")
            self.assertEqual(
                src._request("/").get_header("Authorization"), "Bearer second"
            )

    def test_relist_bookmark_update_and_delete(self):
        src = source()
        src.list_snapshot = Mock(return_value=([ingress()], "1"))
        watch = IngressWatch(src, threading.Event())
        watch.relist()
        watch.accept(
            {"type": "BOOKMARK", "object": {"metadata": {"resourceVersion": "2"}}}
        )
        self.assertEqual(watch.version, "2")
        self.assertEqual(len(watch.snapshot()[0]), 1)
        changed = ingress(version="3")
        changed["spec"]["rules"][0]["host"] = "changed.example.org"
        watch.accept({"type": "MODIFIED", "object": changed})
        self.assertEqual(
            src.desired_hosts(watch.snapshot()[0], (DIRECT,))[0].hostname,
            "changed.example.org",
        )
        watch.accept({"type": "DELETED", "object": changed})
        self.assertEqual(watch.snapshot()[0], [])

    def test_stream_consumes_events_and_resumes_from_bookmark(self):
        src = source()
        src._request = Mock(return_value="request")
        watch = IngressWatch(src, threading.Event())
        watch.version = "9"
        response = io.BytesIO(
            (
                json.dumps({"type": "ADDED", "object": ingress(version="10")})
                + "\n"
                + json.dumps(
                    {
                        "type": "BOOKMARK",
                        "object": {"metadata": {"resourceVersion": "11"}},
                    }
                )
                + "\n"
            ).encode()
        )
        with patch("urllib.request.urlopen", return_value=response):
            watch.stream()
        self.assertEqual(watch.version, "11")
        self.assertTrue(watch.snapshot()[1])
        with patch("urllib.request.urlopen", return_value=io.BytesIO()):
            watch.stream()
        self.assertIn("resourceVersion=11", src._request.call_args.args[0])

    def test_http_and_event_410_require_relist(self):
        src = source()
        src._request = Mock(return_value="request")
        watch = IngressWatch(src, threading.Event())
        with self.assertRaises(WatchExpired):
            watch.accept({"type": "ERROR", "object": {"code": 410}})
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("url", 410, "Gone", {}, None),
        ):
            with self.assertRaises(WatchExpired):
                watch.stream()

    def test_watch_run_relists_after_expiration_and_recovers(self):
        src = source()
        src.list_snapshot = Mock(
            side_effect=[([ingress()], "1"), ([ingress("new.example.org")], "20")]
        )
        stop = threading.Event()
        watch = IngressWatch(src, stop)
        versions = []

        def stream():
            versions.append(watch.version)
            if len(versions) == 1:
                raise WatchExpired()
            stop.set()

        watch.stream = stream
        with patch.object(stop, "wait", return_value=False):
            watch.run()
        self.assertEqual(versions, ["1", "20"])
        self.assertEqual(src.list_snapshot.call_count, 2)

    def test_irrelevant_ingress_fields_do_not_change_desired_state(self):
        src = source()
        before = ingress()
        after = copy.deepcopy(before)
        after["metadata"]["resourceVersion"] = "100"
        after["metadata"]["annotations"][
            "kubectl.kubernetes.io/last-applied-configuration"
        ] = "changed"
        after["status"] = {"loadBalancer": {"ingress": [{"ip": "1.2.3.4"}]}}
        self.assertEqual(
            src.desired_hosts([before], (DIRECT,)),
            src.desired_hosts([after], (DIRECT,)),
        )
        after["metadata"]["annotations"][REQUEST_ANNOTATION] = "rescan"
        self.assertNotEqual(
            src.desired_hosts([before], (DIRECT,)),
            src.desired_hosts([after], (DIRECT,)),
        )

    def test_duplicate_ingress_conflict_still_fails_closed(self):
        other = ingress()
        other["metadata"]["name"] = "duplicate"
        with self.assertRaises(SafetyConflict):
            source().desired_hosts([ingress(), other], (DIRECT,))


class RuntimeTests(unittest.TestCase):
    def config(self, path):
        path.write_text(
            json.dumps(
                {
                    "zones": [
                        {
                            "domain": "example.org",
                            "mode": "direct",
                            "origin": "1.2.3.4",
                            "cloudflareZoneId": "a" * 32,
                        }
                    ]
                }
            )
        )

    def test_projected_config_change_and_invalid_update(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "zones.json"
            self.config(path)
            config = ConfigFile(str(path))
            before = config.read()
            path.write_text(path.read_text().replace("1.2.3.4", "5.6.7.8"))
            self.assertNotEqual(config.read(), before)
            path.write_text("{invalid")
            with self.assertRaises(ValueError):
                config.read()
            self.config(path)
            self.assertEqual(config.read(), before)

    def test_runtime_idle_event_disconnect_and_hourly_audit(self):
        clock = SimpleNamespace(now=0)
        stop = threading.Event()

        def wait(seconds):
            clock.now += seconds
            if clock.now > 3610:
                stop.set()

        base = ingress()
        changed = copy.deepcopy(base)
        changed["metadata"]["annotations"][REQUEST_ANNOTATION] = "event-test"

        def snapshot():
            return [base if clock.now < 100 else changed], not 95 <= clock.now < 120

        watch = SimpleNamespace(snapshot=snapshot, thread=Mock())
        watch.thread.is_alive.return_value = True
        dns = FakeDNS([dns_record(host())])
        reconciler = DomainReconciler(FakeEdge(), dns)
        calls = []
        original = reconciler.reconcile_batch

        def reconcile(hosts):
            calls.append(clock.now)
            return original(hosts)

        reconciler.reconcile_batch = reconcile
        state = HealthState()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "zones.json"
            self.config(path)
            with (
                patch("domain_reconciler.runtime.IngressWatch", return_value=watch),
                patch(
                    "domain_reconciler.runtime.time.monotonic",
                    side_effect=lambda: clock.now,
                ),
                patch.object(stop, "wait", side_effect=wait),
            ):
                run_controller(source(), reconciler, str(path), state, stop, 3600)
        self.assertEqual(calls, [0, 120, 3600])
        self.assertEqual(reconciler.request_counts(), {"cloudflare.ListDnsRecords": 3})
        self.assertTrue(state.ready())


if __name__ == "__main__":
    unittest.main()
