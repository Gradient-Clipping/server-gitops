# Easy SWU origin IP

## Verified production path

The September 2026 production check on `1.14.95.189` confirmed this path:

```text
Client -> EdgeOne -> host Nginx :80 -> Traefik NodePort 127.0.0.1:32080
       -> Ingress -> api Service -> Fastify
```

The administration domain adds its own Nginx between the Ingress and API.
The host previously appended the EdgeOne node address to `X-Forwarded-For`.
Fastify correctly stopped at that untrusted public proxy and recorded the edge
address. A marked health request confirmed that EdgeOne already overwrites
`EO-Connecting-IP` with the connecting client's public IP, even when the caller
supplies a forged value. The actual host file also lagged behind the Git version;
Flux does not install files outside Kubernetes.

## Origin authentication

The site uses EdgeOne Free. Its origin protection CIDR list is unavailable, so
do not use a stale public IP list, trust a single observed node forever, or enable
global proxy trust. Instead, scope an EdgeOne request-header rule to exactly:

- `easy-api.lazycampus.com`
- `easy-admin.lazycampus.com`

Set `X-Easy-SWU-Origin-Key` to the 64-character random key generated on the host.
Use **Set**, not **Add**: EdgeOne must replace caller-supplied header instances.
The key is an origin credential; do not commit it, include it in command-line
arguments, paste it into logs, or send it to clients. The Nginx site matches the
key, validates the IPv4/IPv6 value using `geo`, then replaces both `X-Real-IP` and
`X-Forwarded-For` with that client address. It strips the key before proxying to
Traefik. Missing/incorrect keys and invalid/missing IP values fall back to the
TCP peer. Host access logs retain the TCP peer; API session/audit/rate-limit
sources use the verified forwarded IP.

This is IP provenance checking, not an additional login requirement or a public
origin firewall. Existing HTTP origin transport is preserved; deployments that
require protection from interception on the origin network should enable HTTPS
origin transport and certificate verification as a separate coordinated change.

## Install

Run on the host from this repository:

```sh
bash scripts/install-easy-swu-origin-ip.sh
```

The installer creates `/etc/platform-secrets/easy-swu-origin-key` once with mode
`0600`, writes the matching private Nginx map, backs up the existing site, validates
the complete configuration, and reloads Nginx. Validation/reload failures restore
the old site. Repeated installs keep the same key. Only the Easy SWU site is
replaced; other virtual hosts are untouched.

Configure the corresponding EdgeOne rule using the host key. Until it propagates,
requests continue to work and retain the edge-node IP. A missing private map also
falls back safely, so the general host installer remains usable before setup.
Do not dump `nginx -T` into logs after installation: included files contain the
origin key. Use `nginx -t` for validation and hashes for version checks.

For rotation, retain both old and new map entries during EdgeOne propagation,
switch the EdgeOne rule, verify all requests use the new key, then remove the old
entry. Never regenerate the key merely because the server is being redeployed.

## Verify

```sh
python3 -B -m unittest discover -s tests -v
bash -n scripts/install-easy-swu-origin-ip.sh
```

The tests run a separate Nginx process on a random loopback port, forwarding to a
temporary header-echo server. They cover both domains, IPv4/IPv6, forged forwarded
chains, missing/wrong keys, missing/invalid IPs, repeated headers, and stripping
the origin key. They do not access production users or modify the main Nginx.

After deploying, send uniquely marked health requests through each public domain
and directly to the origin, including forged and duplicate IP/key headers. Check
that EdgeOne overwrites the key and `EO-Connecting-IP`, that API logs identify the
actual test client's public IP, and that direct requests cannot choose that IP.
Use only the marked requests when inspecting headers; never persist unrelated
user request headers. Confirm both public health endpoints remain successful and
the API/admin Pods remain ready. No application image or Redis schema changes are
needed: existing sessions update their latest IP on the next authenticated request.

References: [EdgeOne origin headers](https://edgeone.ai/document/54211),
[EdgeOne request-header Set semantics](https://edgeone.ai/document/46186),
[origin protection availability](https://intl.cloud.tencent.com/zh/document/product/1145/48535?lang=zh),
[Nginx geo address validation](https://nginx.org/en/docs/http/ngx_http_geo_module.html).
