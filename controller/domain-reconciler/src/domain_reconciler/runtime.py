"""Single-writer controller: meaningful events, per-host retries, hourly drift audit."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .reconciler import (
    DesiredHost,
    DomainReconciler,
    HealthState,
    HostResult,
    KubernetesIngressSource,
    load_zones,
    log,
    safe_error,
)
from .watch import IngressWatch


class HostScheduler:
    def __init__(
        self, interval: float, now: float, jitter: Callable[[], float] | None = None
    ) -> None:
        self.interval = interval
        self.next_audit = now + interval
        self.hosts: dict[str, DesiredHost] = {}
        self.due: dict[str, float] = {}
        self.attempts: dict[str, int] = {}
        self.errors: set[str] = set()
        self.changed: set[str] = set()
        self.jitter = jitter or (lambda: random.uniform(1, 1.2))

    def update(self, desired: list[DesiredHost], now: float) -> None:
        current = {host.hostname: host for host in desired}
        for name, host in current.items():
            if self.hosts.get(name) != host:
                self.due[name] = now
                self.attempts.pop(name, None)
                self.changed.add(name)
        # Removal/unmanagement drops local work only. Remote resources are never deleted.
        for name in self.hosts.keys() - current.keys():
            self.due.pop(name, None)
            self.attempts.pop(name, None)
            self.errors.discard(name)
            self.changed.discard(name)
        self.hosts = current

    def take_due(self, now: float) -> tuple[list[DesiredHost], str]:
        audit = now >= self.next_audit
        if audit:
            self.next_audit = now + self.interval
            for name in self.hosts:
                self.due[name] = min(self.due.get(name, now), now)
        names = sorted(name for name, due in self.due.items() if due <= now)
        reason = (
            "hourly_audit"
            if audit
            else "ingress_or_config_change"
            if self.changed.intersection(names)
            else "retry"
        )
        for name in names:
            self.due.pop(name)
            self.changed.discard(name)
        return [self.hosts[name] for name in names], reason

    def complete(self, results: dict[str, HostResult], now: float) -> None:
        for name, result in results.items():
            if result.state == "error":
                self.errors.add(name)
            else:
                self.errors.discard(name)
            if result.state == "ready":
                self.attempts.pop(name, None)
                continue
            attempt = self.attempts.get(name, 0)
            delay = max(min(300, 15 * 2 ** min(attempt, 5)), result.retry_after)
            self.due[name] = now + delay * self.jitter()
            self.attempts[name] = attempt + 1


class ConfigFile:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.content: bytes | None = None
        self.zones = ()

    def read(self):
        content = self.path.read_bytes()
        if content != self.content:
            # Kubernetes replaces the projected directory atomically. Parse the exact
            # bytes checked here, even if another mount update happens during the read.
            zones = load_zones(self.path, content=content)
            self.zones, self.content = zones, content
            log("info", "domain_config_loaded", zones=[zone.domain for zone in zones])
        return self.zones


def run_controller(
    source: KubernetesIngressSource,
    reconciler: DomainReconciler,
    config_path: str,
    state: HealthState,
    stop: threading.Event,
    interval: int,
    run_once: bool = False,
) -> None:
    config = ConfigFile(config_path)
    if run_once:
        desired = source.list_desired_hosts(config.read())
        results = reconciler.reconcile_batch(desired)
        state.set_ready(all(result.state != "error" for result in results.values()))
        log(
            "info",
            "reconcile_once_completed",
            desired_hosts=len(desired),
            cloud_api_calls=dict(reconciler.request_counts()),
        )
        return
    watch = IngressWatch(source, stop)
    scheduler = HostScheduler(interval, time.monotonic())
    watch.thread.start()
    next_heartbeat = time.monotonic() + 300
    previous_error = None
    initialized = False
    try:
        while not stop.is_set():
            try:
                zones = (
                    config.read()
                )  # local projected file; no Kubernetes/cloud request
                items, healthy = watch.snapshot()
                healthy = healthy and watch.thread.is_alive()
                if healthy:
                    desired = source.desired_hosts(items, zones)
                    scheduler.update(desired, time.monotonic())
                    hosts, reason = scheduler.take_due(time.monotonic())
                    if hosts:
                        before = reconciler.request_counts()
                        results = reconciler.reconcile_batch(hosts)
                        scheduler.complete(results, time.monotonic())
                        log(
                            "info",
                            "reconcile_batch_completed",
                            reason="startup" if not initialized else reason,
                            selected_hosts=len(hosts),
                            desired_hosts=len(desired),
                            ready=sum(r.state == "ready" for r in results.values()),
                            pending=sum(r.state == "pending" for r in results.values()),
                            errors=sum(r.state == "error" for r in results.values()),
                            cloud_api_calls=dict(reconciler.request_counts() - before),
                            dry_run=reconciler.dry_run,
                        )
                    initialized = (
                        True  # zero managed hosts is valid, and causes no deletions
                    )
                state.set_ready(initialized and healthy and not scheduler.errors)
                previous_error = None
            except Exception as exc:
                state.set_ready(False)
                error = safe_error(exc)
                if error != previous_error:
                    log("error", "controller_input_failed", **error)
                previous_error = error
            now = time.monotonic()
            if now >= next_heartbeat:
                log(
                    "info",
                    "controller_idle_status",
                    desired_hosts=len(scheduler.hosts),
                    queued_hosts=len(scheduler.due),
                    errors=len(scheduler.errors),
                    ready=state.ready(),
                    next_audit_seconds=max(0, round(scheduler.next_audit - now)),
                    cloud_api_calls_total=dict(reconciler.request_counts()),
                )
                next_heartbeat = now + 300
            # Five-second local coalescing window; stable ticks make zero API calls.
            stop.wait(5)
    finally:
        stop.set()
        watch.thread.join(timeout=1)
