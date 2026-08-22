from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import hmac
import http.server
import json
import os
import signal
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

MANAGED_ANNOTATION = "platform.lazycampus.com/domain-automation"
ADOPT_ANNOTATION = "platform.lazycampus.com/domain-adopt-existing"
MANAGED_VALUE = "enabled"
TRUTHY = frozenset({"1", "true", "yes", "on", "enabled"})
TENCENT_ENDPOINTS = {
    "teo": ("teo.tencentcloudapi.com", "2022-09-01"),
}
CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"
DNS_RECORD_COMMENT = "Managed by easy-platform domain-reconciler"


def log(level: str, event: str, **fields: Any) -> None:
    record = {
        "timestamp": dt.datetime.now(dt.UTC).isoformat(),
        "level": level,
        "event": event,
        **fields,
    }
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")), flush=True)


class ReconcileError(RuntimeError):
    pass


class SafetyConflict(ReconcileError):
    pass


class TencentApiError(ReconcileError):
    def __init__(
        self, action: str, code: str, message: str, request_id: str = ""
    ) -> None:
        self.action = action
        self.code = code
        self.request_id = request_id
        super().__init__(
            f"{action} failed ({code}): {message}; request_id={request_id or 'unknown'}"
        )


class CloudflareApiError(ReconcileError):
    def __init__(self, action: str, code: str, message: str) -> None:
        self.action = action
        self.code = code
        super().__init__(f"{action} failed ({code}): {message}")


@dataclasses.dataclass(frozen=True)
class ZoneConfig:
    domain: str
    mode: str
    origin: str
    cloudflare_zone_id: str
    ttl: int = 1
    edgeone_zone_id: str | None = None
    origin_protocol: str = "HTTP"
    http_origin_port: int = 80
    ipv6_status: str = "follow"
    certificate_mode: str = "eofreecert"
    host_header_overrides: tuple[tuple[str, str], ...] = ()

    def host_header_for(self, hostname: str) -> str:
        for managed_hostname, host_header in self.host_header_overrides:
            if managed_hostname == hostname:
                return host_header
        return hostname


@dataclasses.dataclass(frozen=True)
class DesiredHost:
    hostname: str
    zone: ZoneConfig
    adopt_existing: bool
    ingress: str


def normalize_hostname(value: str) -> str:
    hostname = value.strip().rstrip(".").lower()
    if not hostname or "*" in hostname:
        raise ReconcileError(f"unsupported hostname: {value!r}")
    try:
        return hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ReconcileError(f"invalid hostname: {value!r}") from exc


def hostname_belongs_to_zone(hostname: str, zone_domain: str) -> bool:
    return hostname == zone_domain or hostname.endswith(f".{zone_domain}")


def record_name(hostname: str, zone_domain: str) -> str:
    if hostname == zone_domain:
        return "@"
    suffix = f".{zone_domain}"
    if not hostname.endswith(suffix):
        raise ReconcileError(f"{hostname} does not belong to {zone_domain}")
    return hostname[: -len(suffix)]


def load_zones(path: str | Path) -> tuple[ZoneConfig, ...]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    zones: list[ZoneConfig] = []
    for item in raw.get("zones", []):
        domain = normalize_hostname(str(item["domain"]))
        mode = str(item["mode"]).lower()
        if mode not in {"direct", "edgeone"}:
            raise ReconcileError(f"unsupported mode {mode!r} for {domain}")
        cloudflare_zone_id = str(item.get("cloudflareZoneId", "")).strip()
        if len(cloudflare_zone_id) != 32:
            raise ReconcileError(f"valid cloudflareZoneId is required for {domain}")
        zone_id = item.get("edgeoneZoneId")
        if mode == "edgeone" and not zone_id:
            raise ReconcileError(f"edgeoneZoneId is required for {domain}")
        raw_host_headers = item.get("hostHeaderOverrides") or {}
        if not isinstance(raw_host_headers, Mapping):
            raise ReconcileError(f"hostHeaderOverrides must be an object for {domain}")
        host_headers: dict[str, str] = {}
        for raw_hostname, raw_host_header in raw_host_headers.items():
            hostname = normalize_hostname(str(raw_hostname))
            if not hostname_belongs_to_zone(hostname, domain):
                raise ReconcileError(
                    f"host header override {hostname} does not belong to {domain}"
                )
            if hostname in host_headers:
                raise ReconcileError(f"duplicate host header override for {hostname}")
            host_headers[hostname] = normalize_hostname(str(raw_host_header))
        ttl = int(item.get("ttl", 1))
        port = int(item.get("httpOriginPort", 80))
        if ttl != 1 and not 60 <= ttl <= 86400:
            raise ReconcileError(f"invalid Cloudflare TTL for {domain}")
        if not 1 <= port <= 65535:
            raise ReconcileError(f"invalid TTL or origin port for {domain}")
        zones.append(
            ZoneConfig(
                domain=domain,
                mode=mode,
                origin=str(item["origin"]),
                cloudflare_zone_id=cloudflare_zone_id,
                ttl=ttl,
                edgeone_zone_id=str(zone_id) if zone_id else None,
                origin_protocol=str(item.get("originProtocol", "HTTP")).upper(),
                http_origin_port=port,
                ipv6_status=str(item.get("ipv6Status", "follow")).lower(),
                certificate_mode=str(item.get("certificateMode", "eofreecert")).lower(),
                host_header_overrides=tuple(sorted(host_headers.items())),
            )
        )
    if not zones:
        raise ReconcileError("at least one zone must be configured")
    if len({zone.domain for zone in zones}) != len(zones):
        raise ReconcileError("zone domains must be unique")
    return tuple(sorted(zones, key=lambda zone: len(zone.domain), reverse=True))


def select_zone(hostname: str, zones: Iterable[ZoneConfig]) -> ZoneConfig:
    for zone in zones:
        if hostname_belongs_to_zone(hostname, zone.domain):
            return zone
    raise ReconcileError(f"no managed zone contains {hostname}")


class KubernetesIngressSource:
    def __init__(
        self,
        *,
        annotation: str = MANAGED_ANNOTATION,
        adopt_annotation: str = ADOPT_ANNOTATION,
        timeout: float = 15,
    ) -> None:
        host = os.environ["KUBERNETES_SERVICE_HOST"]
        port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        self.base_url = f"https://{host}:{port}"
        service_account = Path("/var/run/secrets/kubernetes.io/serviceaccount")
        self.token = (service_account / "token").read_text(encoding="utf-8").strip()
        self.context = ssl.create_default_context(
            cafile=str(service_account / "ca.crt")
        )
        self.annotation = annotation
        self.adopt_annotation = adopt_annotation
        self.timeout = timeout

    def _get_json(self, path: str) -> Mapping[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(
            request, context=self.context, timeout=self.timeout
        ) as response:
            return json.load(response)

    def list_desired_hosts(self, zones: tuple[ZoneConfig, ...]) -> list[DesiredHost]:
        items: list[Mapping[str, Any]] = []
        continuation = ""
        while True:
            query = {"limit": "500"}
            if continuation:
                query["continue"] = continuation
            payload = self._get_json(
                "/apis/networking.k8s.io/v1/ingresses?" + urllib.parse.urlencode(query)
            )
            items.extend(payload.get("items", []))
            continuation = str(payload.get("metadata", {}).get("continue", ""))
            if not continuation:
                break

        desired: dict[str, DesiredHost] = {}
        for item in items:
            metadata = item.get("metadata", {})
            annotations = metadata.get("annotations") or {}
            if str(annotations.get(self.annotation, "")).lower() != MANAGED_VALUE:
                continue
            namespace = metadata.get("namespace", "default")
            name = metadata.get("name", "unknown")
            source = f"{namespace}/{name}"
            adopt = str(annotations.get(self.adopt_annotation, "")).lower() in TRUTHY
            for rule in item.get("spec", {}).get("rules") or []:
                raw_hostname = rule.get("host")
                if not raw_hostname:
                    continue
                hostname = normalize_hostname(str(raw_hostname))
                candidate = DesiredHost(
                    hostname=hostname,
                    zone=select_zone(hostname, zones),
                    adopt_existing=adopt,
                    ingress=source,
                )
                previous = desired.get(hostname)
                if previous and previous != candidate:
                    raise SafetyConflict(
                        f"{hostname} is declared by conflicting managed ingresses: "
                        f"{previous.ingress} and {source}"
                    )
                desired[hostname] = candidate
        return [desired[key] for key in sorted(desired)]


class TencentCloudClient:
    def __init__(self, secret_id: str, secret_key: str, timeout: float = 15) -> None:
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.timeout = timeout

    @staticmethod
    def _sign(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()

    def call(
        self, service: str, action: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        endpoint, version = TENCENT_ENDPOINTS[service]
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        timestamp = int(time.time())
        date = dt.datetime.fromtimestamp(timestamp, dt.UTC).strftime("%Y-%m-%d")
        canonical_headers = (
            "content-type:application/json; charset=utf-8\n"
            f"host:{endpoint}\n"
            f"x-tc-action:{action.lower()}\n"
        )
        signed_headers = "content-type;host;x-tc-action"
        canonical_request = (
            "POST\n/\n\n"
            f"{canonical_headers}\n{signed_headers}\n{hashlib.sha256(body).hexdigest()}"
        )
        scope = f"{date}/{service}/tc3_request"
        string_to_sign = (
            "TC3-HMAC-SHA256\n"
            f"{timestamp}\n{scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"
        )
        secret_date = self._sign(f"TC3{self.secret_key}".encode(), date)
        secret_service = self._sign(secret_date, service)
        secret_signing = self._sign(secret_service, "tc3_request")
        signature = hmac.new(
            secret_signing, string_to_sign.encode(), hashlib.sha256
        ).hexdigest()
        authorization = (
            f"TC3-HMAC-SHA256 Credential={self.secret_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        request = urllib.request.Request(
            f"https://{endpoint}/",
            data=body,
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json; charset=utf-8",
                "Host": endpoint,
                "X-TC-Action": action,
                "X-TC-Timestamp": str(timestamp),
                "X-TC-Version": version,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                document = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                document = json.loads(exc.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise TencentApiError(action, f"HTTP_{exc.code}", str(exc)) from exc
        response = document.get("Response", {})
        if error := response.get("Error"):
            raise TencentApiError(
                action,
                str(error.get("Code", "Unknown")),
                str(error.get("Message", "unknown Tencent Cloud API error")),
                str(response.get("RequestId", "")),
            )
        return response


class CloudflareClient:
    def __init__(self, api_token: str, timeout: float = 15) -> None:
        self.api_token = api_token
        self.timeout = timeout

    def _request(
        self,
        action: str,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        url = f"{CLOUDFLARE_API_BASE}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        data = None
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                document = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                document = json.loads(exc.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise CloudflareApiError(action, f"HTTP_{exc.code}", str(exc)) from exc
        except urllib.error.URLError as exc:
            raise CloudflareApiError(action, "NETWORK_ERROR", str(exc.reason)) from exc

        if not isinstance(document, Mapping):
            raise CloudflareApiError(
                action, "INVALID_RESPONSE", "response is not an object"
            )
        if document.get("success") is not True:
            errors = document.get("errors") or []
            if errors:
                first = errors[0]
                code = str(first.get("code", "UNKNOWN"))
                message = str(first.get("message", "unknown Cloudflare API error"))
            else:
                code = "UNKNOWN"
                message = "Cloudflare API reported an unsuccessful response"
            raise CloudflareApiError(action, code, message)
        return document

    def list_dns_records(self, zone_id: str, hostname: str) -> list[Mapping[str, Any]]:
        records: list[Mapping[str, Any]] = []
        page = 1
        while True:
            document = self._request(
                "ListDnsRecords",
                "GET",
                f"/zones/{zone_id}/dns_records",
                query={"name.exact": hostname, "page": page, "per_page": 100},
            )
            batch = document.get("result") or []
            if not isinstance(batch, list):
                raise CloudflareApiError(
                    "ListDnsRecords", "INVALID_RESPONSE", "result is not a list"
                )
            records.extend(item for item in batch if isinstance(item, Mapping))
            result_info = document.get("result_info") or {}
            total_pages = int(result_info.get("total_pages", page))
            if page >= total_pages or not batch:
                return records
            page += 1

    def create_dns_record(
        self, zone_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return self._request(
            "CreateDnsRecord",
            "POST",
            f"/zones/{zone_id}/dns_records",
            payload=payload,
        )

    def overwrite_dns_record(
        self, zone_id: str, record_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return self._request(
            "OverwriteDnsRecord",
            "PUT",
            f"/zones/{zone_id}/dns_records/{record_id}",
            payload=payload,
        )


class DomainReconciler:
    def __init__(
        self,
        tencent_client: TencentCloudClient,
        dns_client: CloudflareClient,
        *,
        dry_run: bool = False,
    ) -> None:
        self.tencent = tencent_client
        self.dns = dns_client
        self.dry_run = dry_run

    def reconcile(self, desired_hosts: Iterable[DesiredHost]) -> int:
        hosts = list(desired_hosts)
        edge_cache: dict[str, dict[str, Mapping[str, Any]]] = {}
        for zone in {item.zone for item in hosts if item.zone.mode == "edgeone"}:
            edge_cache[zone.domain] = self._list_edge_domains(zone)

        errors = 0
        for desired in hosts:
            try:
                if desired.zone.mode == "edgeone":
                    self._reconcile_edgeone(desired, edge_cache[desired.zone.domain])
                else:
                    self._ensure_dns(desired, "A", desired.zone.origin)
            except Exception as exc:  # noqa: BLE001 - isolate one bad host from the remaining hosts
                errors += 1
                log(
                    "error",
                    "host_reconcile_failed",
                    hostname=desired.hostname,
                    ingress=desired.ingress,
                    error=str(exc),
                )
            else:
                log(
                    "info",
                    "host_reconciled",
                    hostname=desired.hostname,
                    mode=desired.zone.mode,
                    ingress=desired.ingress,
                )
        return errors

    def _list_edge_domains(self, zone: ZoneConfig) -> dict[str, Mapping[str, Any]]:
        assert zone.edgeone_zone_id
        result: dict[str, Mapping[str, Any]] = {}
        offset = 0
        while True:
            response = self.tencent.call(
                "teo",
                "DescribeAccelerationDomains",
                {"ZoneId": zone.edgeone_zone_id, "Offset": offset, "Limit": 200},
            )
            batch = response.get("AccelerationDomains") or []
            for item in batch:
                hostname = normalize_hostname(str(item["DomainName"]))
                result[hostname] = item
            offset += len(batch)
            if not batch or offset >= int(response.get("TotalCount", offset)):
                break
        return result

    def _reconcile_edgeone(
        self, desired: DesiredHost, domains: dict[str, Mapping[str, Any]]
    ) -> None:
        zone = desired.zone
        assert zone.edgeone_zone_id
        existing = domains.get(desired.hostname)
        if existing is None:
            payload = {
                "ZoneId": zone.edgeone_zone_id,
                "DomainName": desired.hostname,
                "OriginInfo": {
                    "OriginType": "IP_DOMAIN",
                    "Origin": zone.origin,
                    "HostHeader": zone.host_header_for(desired.hostname),
                },
                "OriginProtocol": zone.origin_protocol,
                "HttpOriginPort": zone.http_origin_port,
                "IPv6Status": zone.ipv6_status,
            }
            self._mutate(
                "teo",
                "CreateAccelerationDomain",
                payload,
                event="edge_domain_created",
                hostname=desired.hostname,
            )
            return

        drift = self._edge_origin_drift(desired, existing)
        if drift:
            status = str(existing.get("DomainStatus", "")).lower()
            if status == "process":
                log(
                    "info",
                    "edge_domain_waiting",
                    hostname=desired.hostname,
                    status=status,
                )
                return
            payload = {
                "ZoneId": zone.edgeone_zone_id,
                "DomainName": desired.hostname,
                "OriginInfo": {
                    "OriginType": "IP_DOMAIN",
                    "Origin": zone.origin,
                    "HostHeader": zone.host_header_for(desired.hostname),
                },
                "OriginProtocol": zone.origin_protocol,
                "HttpOriginPort": zone.http_origin_port,
                "IPv6Status": zone.ipv6_status,
            }
            self._mutate(
                "teo",
                "ModifyAccelerationDomain",
                payload,
                event="edge_origin_updated",
                hostname=desired.hostname,
                fields=drift,
            )
            return

        cname = str(existing.get("Cname", "")).strip().rstrip(".")
        if not cname:
            log(
                "info",
                "edge_domain_waiting",
                hostname=desired.hostname,
                status=existing.get("DomainStatus", "unknown"),
            )
            return
        dns_ready = self._ensure_dns(desired, "CNAME", cname)
        status = str(existing.get("DomainStatus", "")).lower()
        certificate = existing.get("Certificate") or {}
        current_mode = str(certificate.get("Mode", "")).lower()
        if (
            zone.certificate_mode
            and current_mode not in {"eofreecert", "sslcert"}
            and dns_ready
            and status == "online"
        ):
            self._mutate(
                "teo",
                "ModifyHostsCertificate",
                {
                    "ZoneId": zone.edgeone_zone_id,
                    "Hosts": [desired.hostname],
                    "Mode": zone.certificate_mode,
                },
                event="edge_certificate_requested",
                hostname=desired.hostname,
                mode=zone.certificate_mode,
            )

    @staticmethod
    def _edge_origin_drift(
        desired: DesiredHost, existing: Mapping[str, Any]
    ) -> list[str]:
        zone = desired.zone
        origin = existing.get("OriginDetail") or {}
        drift: list[str] = []
        if str(origin.get("OriginType", "")).upper() != "IP_DOMAIN":
            drift.append("originType")
        if (
            str(origin.get("Origin", "")).rstrip(".").lower()
            != zone.origin.rstrip(".").lower()
        ):
            drift.append("origin")
        host_header = str(origin.get("HostHeader") or "").rstrip(".").lower()
        if host_header and host_header != zone.host_header_for(desired.hostname):
            drift.append("hostHeader")
        if (
            "OriginProtocol" in existing
            and str(existing["OriginProtocol"]).upper() != zone.origin_protocol
        ):
            drift.append("originProtocol")
        if (
            "HttpOriginPort" in existing
            and int(existing["HttpOriginPort"]) != zone.http_origin_port
        ):
            drift.append("httpOriginPort")
        if (
            "IPv6Status" in existing
            and str(existing["IPv6Status"]).lower() != zone.ipv6_status
        ):
            drift.append("ipv6Status")
        return drift

    def _ensure_dns(self, desired: DesiredHost, record_type: str, value: str) -> bool:
        zone = desired.zone
        records = self.dns.list_dns_records(zone.cloudflare_zone_id, desired.hostname)
        mutable = [
            record
            for record in records
            if str(record.get("type", "")).upper() in {"A", "AAAA", "CNAME"}
        ]
        if len(mutable) > 1:
            identities = [f"{item.get('type')}:{item.get('id')}" for item in mutable]
            raise SafetyConflict(
                f"refusing to choose among multiple A/AAAA/CNAME records for "
                f"{desired.hostname}: " + ", ".join(identities)
            )

        normalized_value = value.rstrip(".").lower()
        payload = {
            "type": record_type,
            "name": desired.hostname,
            "content": value,
            "ttl": zone.ttl,
            "proxied": False,
            "comment": DNS_RECORD_COMMENT,
        }
        if mutable:
            current = mutable[0]
            matches = (
                str(current.get("type", "")).upper() == record_type
                and str(current.get("content", "")).rstrip(".").lower()
                == normalized_value
                and int(current.get("ttl", zone.ttl)) == zone.ttl
                and current.get("proxied") is False
            )
            if matches:
                return True
            if not desired.adopt_existing:
                raise SafetyConflict(
                    f"existing DNS record for {desired.hostname} differs and adoption is disabled"
                )
            self._mutate_dns(
                "overwrite",
                zone.cloudflare_zone_id,
                str(current["id"]),
                payload,
                event="dns_record_updated",
                hostname=desired.hostname,
                record_type=record_type,
            )
            return False

        self._mutate_dns(
            "create",
            zone.cloudflare_zone_id,
            None,
            payload,
            event="dns_record_created",
            hostname=desired.hostname,
            record_type=record_type,
        )
        return False

    def _mutate(
        self,
        service: str,
        action: str,
        payload: Mapping[str, Any],
        *,
        event: str,
        **fields: Any,
    ) -> None:
        if self.dry_run:
            log("info", f"{event}_dry_run", **fields)
            return
        response = self.tencent.call(service, action, payload)
        log("info", event, request_id=response.get("RequestId", ""), **fields)

    def _mutate_dns(
        self,
        action: str,
        zone_id: str,
        record_id: str | None,
        payload: Mapping[str, Any],
        *,
        event: str,
        **fields: Any,
    ) -> None:
        if self.dry_run:
            log("info", f"{event}_dry_run", **fields)
            return
        if action == "create":
            response = self.dns.create_dns_record(zone_id, payload)
        elif action == "overwrite" and record_id:
            response = self.dns.overwrite_dns_record(zone_id, record_id, payload)
        else:
            raise ReconcileError(f"unsupported DNS mutation: {action}")
        result = response.get("result") or {}
        log("info", event, record_id=result.get("id", record_id or ""), **fields)


class HealthState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = False

    def set_ready(self, value: bool) -> None:
        with self._lock:
            self._ready = value

    def ready(self) -> bool:
        with self._lock:
            return self._ready


def start_health_server(
    state: HealthState, port: int
) -> http.server.ThreadingHTTPServer:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/healthz":
                status = 200
            elif self.path == "/readyz":
                status = 200 if state.ready() else 503
            else:
                status = 404
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(("ok\n" if status == 200 else "not ready\n").encode())

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in TRUTHY


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ReconcileError(f"required environment variable {name} is empty")
    return value


def run_loop(
    source: KubernetesIngressSource,
    reconciler: DomainReconciler,
    zones: tuple[ZoneConfig, ...],
    state: HealthState,
    stop: threading.Event,
    interval: int,
    run_once: bool,
) -> None:
    while not stop.is_set():
        try:
            desired = source.list_desired_hosts(zones)
            if not desired:
                raise ReconcileError("no opted-in Ingress hosts were found")
            errors = reconciler.reconcile(desired)
            state.set_ready(errors == 0)
            log(
                "info" if errors == 0 else "error",
                "reconcile_cycle_completed",
                desired_hosts=len(desired),
                errors=errors,
                dry_run=reconciler.dry_run,
            )
        except Exception as exc:  # noqa: BLE001 - a failed cycle must not terminate the controller
            state.set_ready(False)
            log("error", "reconcile_cycle_failed", error=str(exc))
        if run_once:
            return
        stop.wait(interval)


def main() -> None:
    try:
        zones = load_zones(
            os.environ.get("CONFIG_PATH", "/etc/domain-reconciler/zones.json")
        )
        timeout = float(os.environ.get("HTTP_TIMEOUT_SECONDS", "15"))
        interval = int(os.environ.get("RECONCILE_INTERVAL_SECONDS", "60"))
        if interval < 30:
            raise ReconcileError("RECONCILE_INTERVAL_SECONDS must be at least 30")
        tencent_client = TencentCloudClient(
            require_env("TENCENTCLOUD_SECRET_ID"),
            require_env("TENCENTCLOUD_SECRET_KEY"),
            timeout=timeout,
        )
        dns_client = CloudflareClient(
            require_env("CLOUDFLARE_API_TOKEN"), timeout=timeout
        )
        source = KubernetesIngressSource(timeout=timeout)
        reconciler = DomainReconciler(
            tencent_client, dns_client, dry_run=env_bool("DRY_RUN")
        )
    except Exception as exc:
        log("critical", "startup_failed", error=str(exc))
        raise SystemExit(1) from exc

    state = HealthState()
    server = start_health_server(state, int(os.environ.get("HEALTH_PORT", "8080")))
    stop = threading.Event()

    def request_stop(signum: int, frame: Any) -> None:
        log("info", "shutdown_requested", signal=signum)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    log("info", "controller_started", zones=[zone.domain for zone in zones])
    try:
        run_loop(
            source,
            reconciler,
            zones,
            state,
            stop,
            interval,
            env_bool("RUN_ONCE"),
        )
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
