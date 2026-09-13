# Shared campus Tailscale proxy

The cluster runs one persistent userspace Tailscale device in
`tailscale-system`, independently of Easy SWU API releases.

- HTTP/HTTPS proxy: `http://tailscale-proxy.tailscale-system.svc.cluster.local:1055`
- Health: `http://tailscale-proxy.tailscale-system.svc.cluster.local:9002/healthz`
- Metrics: the same internal port at `/metrics`
- State: `/srv/k3s-data/tailscale`, PV/PVC `platform-tailscale`, retained on
  `easy-platform-1`
- Enrollment material: `/etc/platform-secrets/tailscale-auth-key`, restored into
  `tailscale-system/tailscale-auth`; registry credentials use that namespace's
  `tcr-auth`

No public Ingress, host port or NodePort is created. CoreDNS remains the Pod
resolver (`TS_ACCEPT_DNS=false`). The existing pinned Tailscale image, userspace
mode, accepted routes, shields-up setting and resource budget are preserved.
The hostname becomes `platform-campus-gateway`; migration retains the device ID,
keys and tailnet addresses. The Deployment remains single-replica with Recreate;
multiple writers must never share device state. This does not provide node HA.

## Connect another service

Add this label to **both** the application's Namespace and the calling
Deployment's `spec.template.metadata.labels` through its GitOps manifests:

```yaml
platform.lazycampus.com/campus-network-client: "true"
```

In an egress-isolated application namespace, permit the labelled caller to
reach the gateway:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: campus-network-egress
  namespace: your-application
spec:
  podSelector:
    matchLabels:
      platform.lazycampus.com/campus-network-client: "true"
  policyTypes: [Egress]
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: tailscale-system
          podSelector:
            matchLabels:
              app.kubernetes.io/name: tailscale-proxy
      ports:
        - port: 1055
          protocol: TCP
        - port: 9002
          protocol: TCP
```

Retain that application's DNS and other required egress policies. Configure its
HTTP client to use the proxy only for intended campus requests. Existing clients
do not automatically adopt a proxy environment variable; use the networking
library's supported proxy agent. This is an explicit HTTP/HTTPS forward proxy,
not a cluster-wide default route. Arbitrary TCP/UDP needs a separate design.

The namespace and Pod selectors form one AND condition. Namespace label
assignment is an administrator decision; a workload label alone grants no
access. All callers share the gateway's tailnet identity and its permitted
routes. Domain filtering in Easy SWU remains application logic, not a proxy ACL.
When services need different campus permissions, use separate tailnet identities
or an authenticated policy-enforcing proxy.

Easy SWU retains its per-process retries, campus resource checks, aTrust fallback
and student credential lifecycle. Other callers share only the Tailscale
transport. Health reports the presence of a tailnet IP; check actual campus
resources separately. Readiness uses health; liveness checks the local proxy
listener so an upstream outage alone does not cause container restart loops.

## Migrate the existing sidecar

Use the normal PR, main validation and production promotion path. Before merging,
record only the old device's `Self.ID` and `TailscaleIPs` from local Tailscale
status; never dump its state file. Keep the validated runner ready.

The promoted manifests remove the API sidecar and its PVC/PV, switch the API to
the shared Service and create the new gateway. The old PV has a Retain policy;
its host directory is deliberately retained. The new PV uses hostPath
`Directory` rather than `DirectoryOrCreate`, so the new device cannot start with
an accidentally empty state directory. Until bootstrap completes, expect a short
primary-link interruption; Easy SWU's fallback remains available where supported.
Electricity is Tailscale-only and may temporarily be unavailable.

From the administrator checkout, immediately after successful main validation
and promotion, run:

```sh
python scripts/run_shared_tailscale.py \
  --revision <current-production-sha> \
  --run-id <successful-main-validation-run> \
  --expected-device-id <recorded-Self.ID>
```

The runner checks the exact successful validation, current production revision,
and its own/gate source bytes, then transfers only that immutable revision's
bootstrap and verification files over the existing host SSH connection. It does
not apply workloads outside Flux.

The host bootstrap waits up to four minutes for every legacy deployment template
and state-mounting Pod (including terminating Pods) to disappear. It copies the
offline state into `/var/backups/platform-tailscale/legacy-<UTC timestamp>`, checks
the copied state bytes and publishes the new directory atomically. The original
directory is preserved. It reuses the previous root-only enrollment key if the
shared key has not yet been provisioned. Existing valid shared state is never
overwritten on retries. Invalid state or redirected paths stop migration.

Verification waits for both deployments, compares the device ID, probes campus
HTTPS and the electricity HTTP entrance from the API, and checks its readiness.
Four disposable Jobs exercise both namespace/Pod label combinations on both
ports; their namespaces and registry copies are deleted in a finally block.
No student login, billing query or business snapshot write is used.

For a fresh installation with no old state, provision the shared enrollment key
and registry recovery files and use `--fresh-enrollment` instead of an expected
device ID. Keep the existing application bootstrap separate. To repeat live
checks without bootstrap, add `--verify-only`.

## Recovery

If bootstrap fails before publishing the new state directory, the original
state and any completed backup remain untouched. Fix the reported prerequisite
and rerun the same validated revision, or the current newly validated descendant
if production advanced. Do not clear Redis or regenerate the Tailscale key.

A valid shared state directory is authoritative once its device has started.
Do not start the old sidecar against its now-stale source directory. For rollback,
first suspend Flux reconciliation and stop both state writers; wait for their
Pods to disappear. Back up both directories, restore the latest shared state into
the legacy directory with UID/GID 1000 and private permissions, and publish a
tested Git revert restoring the legacy Deployment, PVC/PV and addresses. Resume
Flux only after that revert is promoted. Never rewind production or run both
copies of the same identity. Keep the legacy root secret and original directory
as recovery material until the migration has been accepted.

## References

- [Tailscale container parameters](https://tailscale.com/docs/features/containers/docker/docker-params)
- [Userspace networking](https://tailscale.com/docs/concepts/userspace-networking)
