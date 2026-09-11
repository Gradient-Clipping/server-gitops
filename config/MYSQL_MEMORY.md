# Shared MySQL memory profile

The single MySQL 8.4.11 instance has a 1 GiB container limit. Keep its 256 MiB
InnoDB buffer pool, 256 MiB on-disk redo capacity, 100-connection ceiling,
transaction durability, and Performance Schema enabled. Auxiliary allocations
are explicitly bounded in `infrastructure/mysql/config.yaml` under
`clusters/easy-platform`.

## Baseline and settings

On 2026-09-11, after approximately 20 days of uptime, the process RSS was about
969 MiB and its container working set about 962 MiB. Performance Schema reported
297 MiB, InnoDB 445 MiB (including a 64 MiB log buffer), and the SQL layer 95 MiB.
There were 17 connections, a lifetime peak of 25, 209 prepared statements, and
1,249 statement digests. Buffer-pool data occupied approximately 75 MiB.

| Setting | Previous | Tuned | Reason |
| --- | --- | --- | --- |
| InnoDB log buffer | 64 MiB | 16 MiB | Reduce reserved RAM; preserve redo capacity and commit durability. |
| MyISAM key buffer | 8 MiB | 1 MiB | The shared application tables use InnoDB. |
| Open table cache / instances | 4,000 / 16 | 1,024 / 4 | Reduce cached table objects and duplication on the four-core host. |
| Table definition cache | 2,000 | 512 | Allow room above the current schema inventory. |
| TempTable shared RAM budget | 1 GiB | 64 MiB | Spill larger internal temporary tables to InnoDB disk storage. Per-thread allocations are additional. |
| Statement digest capacity | 10,000 | 2,048 | Keep headroom above the measured SQL diversity. |
| Performance Schema accounts / hosts / users | Autoscaled | 32 / 32 / 16 | Bound aggregation arrays while retaining attribution. |
| Instrumented thread capacity | Autoscaled | 192 | Allow all 100 client connections plus background threads. |
| Global long histories | 10,000 rows each | 0 | These consumers were disabled; retain current and per-thread history. |

Changing `config.yaml` requires incrementing the pod-template annotation
`platform.lazycampus.com/config-revision` in `statefulset.yaml`: the mounted
ConfigMap uses `subPath`, and several Performance Schema settings are startup-only.
Flux then performs one normal StatefulSet rollout. The single replica briefly
interrupts database connections; clients must reconnect.

## Backup and deployment verification

`clusters/easy-platform/maintenance/mysql-memory-preflight` contains the
versioned preflight used for this profile. It is activated by a Git change to
the root Kustomization, with a fresh Job name for each run, and removed from
the root resource list after successful completion.

The 2026-09-11 run restored all six database schemas and passed `CHECK TABLE`
for all 206 base tables using the candidate profile. Its backup was
`/srv/k3s-backups/mysql/all-databases-20260911T150501Z.sql`, with SHA-256
`4117e30739c857e8070315c179875624accda50e6230a875aebc1322ab066f12`.
The isolated instance reported 91.71 MiB of Performance Schema allocations
after restoration; production measurements must also include normal clients.

The Job writes a new consistent all-database dump to the existing backup PVC,
checks its completion marker and SHA-256, validates the candidate with the
production MySQL binary, and restores the full dump into a separate `emptyDir`.
The isolated server has no TCP listener, no binary logging, and no event
scheduler. It compares database/table inventories and runs native `CHECK TABLE`
statements on the restored databases. Production data is only read. Removing the Job through
GitOps removes only its temporary restore data; the new backup stays under
`/srv/k3s-backups/mysql` and follows the existing five-day retention policy.

After rollout, verify all requested variables, MySQL readiness, application
reconnections, and the following counters over an observation interval:

- Container `memory.current`, working set, and `memory.events` (`oom`,
  `oom_kill`, and changes in `max`).
- Performance Schema memory totals and `Performance_schema_*_lost`, especially
  account, host, user, thread, table-handle, and digest coverage.
- `Table_open_cache_hits`, `Table_open_cache_misses`, `Opened_tables`,
  `Innodb_log_waits`, connection errors, and temporary-table disk use.

Restarting clears caches and counters. Compare subsequent intervals and do not
interpret an immediate post-restart memory measurement as a long-term plateau.
If capacity loss or sustained table-cache churn appears, increase only the
relevant limit through Git. To roll back, restore the preceding settings and
change the pod-template revision again; no data restore is needed for this
configuration-only change. A data restore, if separately required, must target
an isolated instance first and use the verified dump rather than overwrite the
live data directory.

References: [MySQL memory allocation](https://dev.mysql.com/doc/refman/8.4/en/memory-use.html),
[Performance Schema sizing](https://dev.mysql.com/doc/refman/8.4/en/performance-schema-system-variables.html),
[Performance Schema memory lifecycle](https://dev.mysql.com/doc/refman/8.4/en/performance-schema-memory-model.html).
