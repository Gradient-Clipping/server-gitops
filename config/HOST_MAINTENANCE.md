# Host security and node resource maintenance

This is a separate host maintenance phase, not a K3s, MySQL, Keycloak, Flux,
Traefik or Docker version upgrade. The node has 4 CPUs and about 7.25 GiB memory.
Reserve 250m CPU / 1280 MiB for Kubernetes daemons and 250m / 512 MiB for the OS.
Memory eviction starts at 500 MiB available; all disk eviction defaults remain
explicit. About 5 GiB remains allocatable for Pods, above their measured requests.
These are scheduling reservations; no unsupported host cgroup enforcement is added.

The old unattended-upgrades file enabled only the original jammy release pocket;
jammy-security was commented out. `host/apt/99-platform-security` restores security
origins, keeps daily timers, excludes separately maintained infrastructure packages,
and disables automatic reboot and kernel/package removal. Modified configuration
files are preserved with force-confdef/force-confold; review vendor `.dpkg-dist`
files through Git during maintenance. The existing kernel is
retained for recovery. Future kernel updates still need a planned reboot.

Use `scripts/run_host_maintenance.py` from the operator workstation with the
current production SHA and its successful main validation run ID. The driver
verifies the exact revision through GitHub, sends only its versioned host files
over SSH and leaves GitHub management credentials on the workstation.

1. Merge the host manifests, scripts and `maintenance/host-security-preflight`.
   Wait for its Job to dump all MySQL databases, restore into isolated emptyDir
   storage with networking/events disabled, and pass table inventory/integrity checks.
2. Run `plan`, then `backup` using a unique directory of the form
   `/srv/k3s-backups/maintenance/YYYYMMDDTHHMMSSZ`.
3. Backup captures the verified MySQL dump, consistent SQLite copies (including
   the K3s datastore), other application volume files, K3s server token/TLS,
   platform secrets, host configuration and package inventory. MySQL live data
   files are excluded; recovery uses the verified logical dump. Object/Redis files
   are a filesystem copy, not a cross-application transactional snapshot.
4. AES-256 GPG encryption uses a root-only passphrase file. The script decrypts
   the archive, checks every regular file hash and restores SQLite for integrity
   checks, then removes only its own plaintext staging directory.
5. Run `export --output <local-directory>` to copy the encrypted archive off-host,
   verify its hash and save the key protected by Windows CurrentUser DPAPI. A
   DPAPI round trip is checked before recording matching offsite proof on the host.
   Keep the Windows user profile available for key recovery; never put these files
   or plaintext recovery material into Git. The artifact directory and server
   timestamp directory identify the exact recovery bundle.
6. Run `apply`: validate backup/offsite proof, apply the managed security policy,
   select exact Ubuntu security candidate versions, simulate the batch and reject
   infrastructure upgrades/removals, install it once, then check dpkg/Nginx and
   remaining eligible updates, and stage the K3s configuration. Timers resume on
   success or failure. Root-only `security-upgrade.log` contains package diagnostics.
   The one-time backlog uses APT's batch solver to avoid unattended-upgrade's slow
   per-package fallback and configuration prompts; daily security updates retain
   unattended-upgrades. Let an existing update finish before starting another
   maintenance phase; never terminate dpkg during installation.
   For an existing minimal-step updater, `finish-update-chunk --pid <PID>` checks
   its exact command and owning maintenance process, then requests Ubuntu's normal
   SIGTERM shutdown after the current chunk. The installed handler only sets a
   stop flag, checked between chunks. Wait for that process and driver to exit and
   require a clean dpkg audit before running batch catch-up. Never send SIGKILL.
7. Run `reboot`, then `postcheck` after SSH returns. Verify the new kernel, node
   allocatable resources, actual kubelet reservations/eviction, all workloads,
   production reconciliation and public service checks. Do not report completion
   solely because package installation succeeded.

Recovery: choose the retained prior kernel from the provider console if boot
fails. For a bad K3s configuration, recover the previous config from the decrypted
archive, reinstall it with root-only permissions and restart K3s. Recover the K3s
SQLite datastore together with its matching server token, encryption material and
TLS while K3s is stopped. Restore MySQL into an isolated instance first, then use
the verified logical restore procedure in a planned outage. SQLite files are
replaced only with their writers stopped. The maintenance script never overwrites
live application data during verification. A full OS package rollback may require
reprovisioning the same Ubuntu release and restoring this bundle; do not attempt
blind package downgrades.

After successful maintenance remove the temporary Job and generated ConfigMap
from the root Kustomization through Git. Retain the encrypted backup and reports.

## 2026-09-12 verification (UTC+8)

The isolated restore completed with 6 databases and 209 tables passing checks.
The encrypted recovery bundle at `/srv/k3s-backups/maintenance/20260911T193900Z`
verified 1,193 regular files and 4 SQLite copies; its matching 40,031,919-byte
offsite copy and Windows DPAPI key recovery were checked before updates.

The node rebooted successfully into `5.15.0-191-generic`; K3s remains
`v1.36.3+k3s1`. Nginx is `1.18.0-6ubuntu14.20`, OpenSSH
`1:8.9p1-3ubuntu0.17`, OpenSSL `3.0.2-0ubuntu1.29`, libc6
`2.35-0ubuntu3.15`. No eligible security updates remain and dpkg audit is clean.
Docker 26.1.3 and its host containerd 1.6.33 were not upgraded; K3s continues
using its own embedded containerd. The prior kernel is retained.

Actual kubelet config reports the two reservations and full eviction thresholds
above. Allocatable resources are 3500m CPU and 5256524Ki memory (about 5 GiB),
with no memory, disk or PID pressure. Business workloads and Flux returned to
Ready; the open platform retried OIDC discovery while Keycloak was starting.
The temporary restore Job/ConfigMap are removed from reconciliation after this
verification; existing PVCs and the recovery bundle are retained.

References: [Kubernetes reservations](https://kubernetes.io/docs/tasks/administer-cluster/reserve-compute-resources/),
[Ubuntu automatic security updates](https://documentation.ubuntu.com/server/how-to/software/automatic-updates/).
