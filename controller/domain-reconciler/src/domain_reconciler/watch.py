"""Maintain an Ingress LIST/WATCH snapshot without polling external providers."""

from __future__ import annotations

import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

from .reconciler import KubernetesIngressSource, ReconcileError, log, safe_error


class WatchExpired(ReconcileError):
    pass


class IngressWatch:
    def __init__(self, source: KubernetesIngressSource, stop: threading.Event) -> None:
        self.source = source
        self.stop = stop
        self.lock = threading.Lock()
        self.items: dict[str, Mapping[str, Any]] = {}
        self.version = ""
        self.healthy = False
        self.thread = threading.Thread(
            target=self.run, name="ingress-watch", daemon=True
        )

    @staticmethod
    def key(item: Mapping[str, Any]) -> str:
        metadata = item["metadata"]
        return str(metadata["namespace"]) + "/" + str(metadata["name"])

    def snapshot(self) -> tuple[list[Mapping[str, Any]], bool]:
        with self.lock:
            return list(self.items.values()), self.healthy

    def set_healthy(self, value: bool) -> None:
        with self.lock:
            self.healthy = value

    def relist(self) -> None:
        self.set_healthy(False)
        items, version = self.source.list_snapshot()
        snapshot = {self.key(item): item for item in items}
        with self.lock:
            self.items = snapshot
            self.version = version
            self.healthy = True
        log("info", "ingress_watch_listed", ingresses=len(items))

    def accept(self, event: Mapping[str, Any]) -> None:
        kind = event.get("type")
        item = event.get("object")
        if not isinstance(item, Mapping):
            raise ReconcileError("invalid watch event")
        if kind == "ERROR":
            if item.get("code") == 410:
                raise WatchExpired("Ingress resourceVersion expired")
            raise ReconcileError("Kubernetes watch reported an error")
        if kind not in {"ADDED", "MODIFIED", "DELETED", "BOOKMARK"}:
            raise ReconcileError("unknown watch event type")
        version = str(item.get("metadata", {}).get("resourceVersion", ""))
        if not version:
            raise ReconcileError("watch event has no resourceVersion")
        with self.lock:
            if kind in {"ADDED", "MODIFIED"}:
                self.items[self.key(item)] = item
            elif kind == "DELETED":
                self.items.pop(self.key(item), None)
            self.version = version

    def stream(self) -> None:
        query = urllib.parse.urlencode(
            {
                "watch": "true",
                "allowWatchBookmarks": "true",
                "resourceVersion": self.version,
                "timeoutSeconds": 120,
            }
        )
        request = self.source._request("/apis/networking.k8s.io/v1/ingresses?" + query)
        try:
            response = urllib.request.urlopen(
                request, context=self.source.context, timeout=135
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 410:
                raise WatchExpired("Ingress resourceVersion expired") from None
            raise
        with response:
            self.set_healthy(True)
            for line in response:
                if self.stop.is_set():
                    return
                if line.strip():
                    self.accept(json.loads(line))

    def run(self) -> None:
        failures = 0
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                if not self.version:
                    self.relist()
                self.stream()
                # A normal server timeout reconnects at the latest bookmark/event RV.
                if time.monotonic() - started >= 30:
                    failures = 0
                    continue
            except WatchExpired:
                self.version = ""
                log("info", "ingress_watch_expired")
            except Exception as exc:
                log("warning", "ingress_watch_disconnected", **safe_error(exc))
            self.set_healthy(False)
            failures += 1
            delay = min(60, 2 ** min(failures - 1, 6)) * random.uniform(1, 1.2)
            self.stop.wait(delay)
