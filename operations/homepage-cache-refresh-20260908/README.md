# Homepage cache refresh: 2026-09-08

This directory records an inactive, one-time operation. It is deliberately
outside `clusters/easy-platform` and is not part of Flux reconciliation.

The homepage repository migration is deployed:

- Source: `Gradient-Clipping/lazycampus-homepage/main`.
- Image: `ccr.ccs.tencentyun.com/lazycampus/lazycampus-homepage:1.0.4`.
- Workload and routing: the existing `lazycampus-site` resources.
- Origin asset: `index-CQX4zw0p.js`.
- Public browser verification with a release query parameter: 26 checks passed.

The operator chose to refresh EdgeOne manually and explicitly excluded cache
refresh from the remaining deployment acceptance. No automated retry or IAM
permission expansion is planned.

At the migration check, EdgeOne still returned the legacy HTML for the bare
root URLs. The GitOps-managed Job attempted to refresh only the HTTPS `/` and
`/index.html` URLs on `lazycampus.com` and `www.lazycampus.com`, in zone
`zone-3solmvkeru39`. Tencent Cloud rejected the request with
`AuthFailure.UnauthorizedOperation` (request ID
`464d9bc1-924c-4e43-ae54-44e718b5f263`). No successful purge Job ID was returned.

The existing domain-controller CAM policy, versioned at
`policies/tencent-domain-reconciler.json`, permits only its domain read/upsert
actions and requests from `1.14.95.189/32`. It does not permit
`teo:CreatePurgeTask`. This operation did not change that policy or attach any
additional cloud permissions. Credentials remain in their existing Secret;
the manifest contains references only.

The failed Job has been removed from the active Kustomization so that a
one-time cache operation cannot keep normal application reconciliation in a
failed state or run again during cluster recovery.

Before retrying, verify whether the bare root URLs have already revalidated.
If they still return the legacy HTML, resolve the missing cache-refresh
permission with an authorized account. Record any permission changes as code,
retain the server IP restriction, and limit access to this EdgeOne zone.
Do not repeatedly submit the same unauthorized request or grant general
EdgeOne administration. A new GitOps attempt needs a new Job name; remove it
from active reconciliation after the result has been recorded here.

Deployment acceptance requires the new image to be available, its production
assets to load, and the Flux Kustomization to be Ready. After the operator's
cache refresh, both bare HTTPS root URLs should return the new homepage.
The new origin serves HTML with `Cache-Control: no-cache` and fingerprinted
assets with an immutable cache policy.

API reference: [CreatePurgeTask](https://cloud.tencent.cn/document/product/1552/80703).
