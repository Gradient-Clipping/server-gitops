# Flux webhook delivery

`flux-webhooks.json` is the source of truth for repository hooks, release branches,
workflow paths, and Flux targets. `scripts/flux_webhooks.py render` generates the
committed Receiver manifests; CI rejects a stale generated file. Adding an image
source requires adding its publishing workflow to this catalog.

GitHub sends `server-gitops/main` push events to the Git source receiver. For each
image publisher, GitHub sends `workflow_run` events after GitHub Actions runs.
Flux accepts only completed, successful `push` or `workflow_dispatch` runs from
the configured source repository, branch, and workflow path. PRs, forks, other
branches/workflows, and failed runs cannot request image reconciliation. The
existing image policies still decide which immutable TCR version is deployable.
The image controllers notify downstream automation when a policy changes.

All nine ImagePolicy objects carry
`platform.lazycampus.com/image-automation: platform-images`. One shared
`ImageUpdateAutomation/flux-system/platform-images` selects that label, updates
Setters below `clusters/easy-platform`, and commits to `server-gitops/main`.
The previous seven automation objects are pruned by Flux. ImageRepository,
ImagePolicy, Receiver, registry and workload identities remain unchanged.

The installed image-automation-controller v1.2.4 enqueues all automations in the
namespace on an ImagePolicy change. A policySelector alone does not narrow this
event fan-out, and even a no-change reconciliation can contact Git to check its
revision. A single writer removes those six redundant reconciliations without
upgrading or modifying Flux. Concurrent image changes can be included together;
an image arriving after a snapshot queues another pass. The hourly fallback and
normal Flux error retries remain active.

When adding an image, add its labelled policy and image setter under the shared
update path; do not create another writer to the same branch. To pause automatic
updates for one application, change its policy label value to `paused` in Git
(both policies for Smart Shop or Easy SWU). Wait for Flux to apply the label
before committing a rollback. Restore `platform-images` to re-enable it; the
next image event or hourly fallback picks it up. Suspending `platform-images`
pauses automatic image commits for every application.

Inspect `status.observedPolicies` on `platform-images` to verify the selected
images and `status.lastPushCommit` to trace its Git write. Validate a real
publishing workflow after rollout: one corresponding image scan, one writer
reconciliation/commit, then the Git push receiver and workload rollout. Changing
the automation configuration itself causes a one-time initial reconciliation.

Traffic follows Cloudflare DNS-only CNAME -> EdgeOne HTTPS -> host Nginx HTTP ->
Traefik -> Flux `webhook-receiver`. Only `/hook/` accepts POST requests on
`hooks.lazycampus.com`. Nginx disables access logging on this host and limits
request rates; Traefik already omits request paths from its access log. The
notification controller verifies GitHub HMAC signatures. The Kubernetes API and
controller administration interfaces are not exposed by this ingress.

## Provisioning from committed code

1. Commit and push the manifests and scripts, retaining the old polling intervals
   during initial setup. Validate the manifests and wait for CI to pass.
2. From that exact committed checkout on the K3s host, execute
   `bash scripts/bootstrap-flux-webhooks.sh`. This idempotently generates the
   root-only `/etc/platform-secrets/flux-github-webhook-token`, reconciles the
   `flux-system/flux-github-webhook-token` Secret, and installs only the versioned
   webhook Nginx site. It tests the whole Nginx configuration before reloading and
   restores that site on validation failure. Previous host configuration is saved
   under `/var/backups/flux-webhooks-*`. Keep the token in off-server encrypted
   recovery backups; never put its value in Git or command output.
3. Merge the receiver/ingress manifests. Flux creates the receivers, and the
   domain controller provisions EdgeOne, DNS, and the public certificate. Wait
   for receiver readiness and public HTTPS before registering GitHub hooks.
4. On an administrator workstation with Python 3, SSH access, and `gh` authenticated
   with repository webhook administration permission, run
   `python scripts/flux_webhooks.py reconcile` to preview, then rerun with `--apply`.
   The versioned reconciler reads the actual receiver paths and token into memory,
   creates missing hooks, corrects drift, and preserves unrelated hooks. Repeating
   it is a no-op. It never stores GitHub credentials on the production server.
5. Run `python scripts/flux_webhooks.py verify` to exercise signature rejection,
   event filtering, and reconciliation of every configured target through public
   HTTPS. It requests reconciliation of existing source/image state; it does not
   publish an image or change application data. `--hook NAME` selects one receiver.
   On the K3s host, use `verify --server local` to read cluster state and the token
   locally while still sending signed requests through the public HTTPS endpoint.
6. After real GitHub deliveries succeed, set the GitRepository, ImageRepository,
   and ImageUpdateAutomation intervals to `1h` in Git. Keep the Kustomization
   interval at `5m` for cluster drift correction. A missed webhook is recovered
   by periodic source/image checks; normal deployments remain event driven.

No additional webhook secrets or notification steps are needed in business
repository workflows. The catalog currently covers seven source repositories,
eight publishing workflows, and nine image sources, including open-platform.

## Verification, maintenance and recovery

Check receiver conditions without printing their secret-derived paths:

```sh
k3s kubectl -n flux-system get receivers \
  -o custom-columns=NAME:.metadata.name,READY:.status.conditions[-1].status
```

GitHub hook delivery status and Flux `reconcile.fluxcd.io/requestedAt` /
`status.lastHandledReconcileAt` provide event-to-reconciliation evidence. Delivery
payloads and HTTP response bodies need not be logged to diagnose status codes.
GitHub webhook delivery failures are not guaranteed to retry automatically, so
hourly polling must remain enabled. Do not suspend sources to disable polling:
suspension also blocks webhook-triggered reconciliation.

If delivery fails, restore the relevant intervals to `1m0s` in Git and reconcile
the Git source through a working receiver or wait for the hourly fallback. Keep
the receiver and domain in place while diagnosing; deleting them is unnecessary.
For credential rotation, replace the root-only token through the runtime secret
process, rerun bootstrap, wait for receivers to regenerate their paths, then
reconcile GitHub hooks with `--apply`. Changed paths create new hooks; explicitly
review and remove old GitHub hooks afterwards. `--refresh-secret` updates the
secret on an existing matching URL when restoring a configuration.
