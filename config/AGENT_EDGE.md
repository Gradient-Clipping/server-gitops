# Agent public WebSocket transport

`scripts/reconcile-agent-edge.py` owns one EdgeOne L7 rule in the existing Lazy Campus zone. Its exact host condition includes only `preview.lazycampus.com` and `agent.lazycampus.com`. It enables WebSocket with a 120-second idle timeout, disables edge caching, and disables offline cache. Applications should send WebSocket heartbeats within that interval and reconnect after interruption. The known previous two-host rule is updated in place; unrelated rules remain untouched.

After the replacement hostname is healthy, add `--retire-previous --apply` to the
gated `run_agent_edge.py` command to retire `agent-admin.lazycampus.com`. It verifies
Ingress and Nginx no longer reference it, checks replacement SSO health, saves its
exact DNS and EdgeOne configuration under the private host bootstrap state, withdraws
the matching CNAME, then disables/deletes only that acceleration domain. `Force` is
false, so associated resources cannot be implicitly deleted. Repeated runs verify
absence. This does not delete workloads, volumes, host data or repositories; the old
hostname has no redirect. Recovery requires a reviewed GitOps routing/client change
and the retained configuration snapshot, not an untracked console edit.

Cloudflare remains DNS-only. The path is EdgeOne → host Nginx HTTP/1.1 → Traefik → backend/AstrBot. The preview ingress still exposes only `/p/`; this rule does not publish backend control APIs. Rule reconciliation uses the existing Tencent `DescribeL7AccRules`, `CreateL7AccRules`, `ModifyL7AccRule` permissions. No additional secret enters the Agent Sandbox.

Domain retirement additionally needs `ModifyAccelerationDomainStatuses` and
`DeleteAccelerationDomains`. The additive CAM policy in
[`agent-domain-retirement-policy.json`](agent-domain-retirement-policy.json) scopes
these actions to Lazy Campus site `zone-3solmvkeru39` in account `100041495148`;
the retirement code further restricts the target to the exact previous hostname.
The current service principal is CAM user `100052141393` (verified with STS
`GetCallerIdentity`). On 2026-09-12 the first action returned
`AuthFailure.UnauthorizedOperation`; a cloud administrator must attach the versioned
policy before the same managed command can finish EdgeOne retirement. The CNAME
withdrawal is independent of this permission, and a retry preserves the original
snapshot. The runner reports only API action, error code and request ID on failure.
See [Tencent's resource-level CAM permissions](https://intl.cloud.tencent.com/zh/document/product/598/57164).

The 2026-09-12 acceptance test found that the same synthetic WebSocket endpoint returned 101 and echoed a frame through both host Nginx port 80 and Traefik port 32080, while the EdgeOne public endpoint returned 404. No existing EdgeOne L7 rule covered these two hosts. HTTP previews already worked.

After committing the scripts and passing the GitOps validation/promotion workflow, run from the matching checkout:

```powershell
uv run python scripts/run_agent_edge.py --revision <full-current-production-sha> --run-id <successful-validation-run-id> --apply
uv run python scripts/run_agent_edge.py --revision <full-current-production-sha> --run-id <successful-validation-run-id>
```

The wrapper validates the exact current `production` revision, workflow repository, branch, event and successful validation job. It uploads only an archive of that revision, executes the managed script on the production host using the existing root-only credential files, and prints a limited rule summary. Without `--apply` it only checks the cloud rule. Cloud updates are idempotent; an existing same-name rule with different ownership or unrelated host scope is refused. Other rules are never replaced or reordered.

Verify public `wss://preview.lazycampus.com/p/<synthetic-slug>/ws` handshake and echo after propagation. Recheck that public `/v1/usage` and `/openapi.json` remain 404. The upstream API supports the `WebSocket` action with `WebSocketParameters` ([official data types](https://cloud.tencent.com/document/api/1552/80721)); HTTP/1.1 and per-host configuration are described in the [official WebSocket guide](https://edgeone.ai/document/46971).

This rule is applied explicitly from a validated revision, following the platform/status page rule pattern; the domain controller continues to own DNS, acceleration domains and certificates. For drift checks, rerun the check command. Change or rollback rule behavior in Git and apply the newly validated revision.
