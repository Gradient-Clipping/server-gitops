# WeCom Namespace Migration

The application namespace is `wecom-kf`. The existing MySQL database and users,
Secret names, image repository, host secret files and public URLs are retained.
The application manifests move to `clusters/easy-platform/apps/wecom-kf`.

The one-time migration from `educoder-wecom` is staged through validated GitOps:

1. Confirm no `kf_jobs` rows are pending or running. Run
   `python3 scripts/migrate-wecom-namespace.py prepare` on the K3s host. This copies
   and verifies six Secrets without exposing their contents and changes the old
   bank PV reclaim policy to Retain.
2. Deploy the preparation revision: old API and Ingress stay active; the new API,
   namespace, policy and PVC are created. Both worker deployments have zero
   replicas. Incoming callback notifications remain durable in shared MySQL.
3. Once old worker pods have terminated, run
   `python3 scripts/migrate-wecom-namespace.py bank` on the K3s host. A temporary
   pod binds the new PVC. The script snapshots the old SQLite database under
   `/var/backups/wecom-kf-namespace-<timestamp>/`, restores the new volume, checks
   integrity, row counts and SHA-256 equality, and removes the temporary pod.
   Existing destination databases are never overwritten. A failed copy leaves
   both worker deployments stopped for inspection; do not activate a partial copy.
4. Deploy the cutover revision: enable new workers, include the new Ingress and
   Flux image resources, and remove the old application directory from the root
   Kustomization. Flux prunes the old namespace; its original bank PV and snapshot
   remain available for recovery.
5. Verify both deployments, all worker heartbeats, PVC, database access, public
   health, signed callback verification and automatic administrator SSO.

The helper deliberately supports only this source and destination on the existing
single-node local-path cluster. For rollback, stage the old namespace and Secrets
from root-only host files, restore an appropriate bank snapshot and switch back
through GitOps. Never run both sets of workers concurrently or seed over a bank
that may contain newer answers.
