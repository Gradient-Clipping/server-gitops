# Server GitOps

This private repository is the desired state for the single-node `easy-platform-1` K3s cluster.

## Delivery path

1. A source repository builds and tests an image in GitHub Actions.
2. The workflow publishes immutable tags to Tencent TCR.
3. A signed GitHub `workflow_run` webhook for the successful production publisher
   immediately triggers Flux image reflection to select the newest allowed tag.
4. The shared Flux `platform-images` automation commits eligible tag changes to
   this repository's `main` branch.
5. CI validates the exact `main` revision and fast-forwards `production` only
   after success. Stale and divergent revisions are rejected.
6. A signed GitHub `production` push webhook triggers Flux to fetch the validated
   revision and apply it to K3s. See [production promotion](config/PRODUCTION_PROMOTION.md).

Ten independent ImageRepository/ImagePolicy pairs feed one ImageUpdateAutomation
(`flux-system/platform-images`). It selects policies labelled
`platform.lazycampus.com/image-automation: platform-images` and updates marked
images below `clusters/easy-platform`. This replaces seven writers to the same
Git repository/branch: one policy change now queues one Git update, and each
hourly fallback uses one writer. Unmarked images are not managed by this task.
Both Smart Shop images and both Easy SWU images remain independently selectable.

The Git source, image sources and image automation use a one-hour polling
fallback. The Kustomization keeps its five-minute cluster drift checks.
Webhook traffic enters at `hooks.lazycampus.com` through EdgeOne, host Nginx,
Traefik and the existing Flux notification controller. Repository, branch,
workflow, event and success filters are declared in `config/flux-webhooks.json`.
See [webhook provisioning and recovery](config/FLUX_WEBHOOKS.md) for the versioned
bootstrap, GitHub hook reconciler and live verification commands. The signing
token is stored only in GitHub hook settings, a Kubernetes Secret and root-only
server recovery material; business workflows need no additional credentials.

The original `platform-smoke` workload was removed after the first production
workloads exercised the same image automation path.

Easy SWU uses explicit `TRUST_PROXY_CIDRS` in its API Deployment. Deploy the API
change together with `host/nginx/easy-swu`, which replaces caller-supplied
`X-Forwarded-For` chains. The ingress NetworkPolicy remains part of this trust
boundary. Apply host Nginx files through the host configuration workflow; Flux
does not install them. Easy SWU restores the EdgeOne client IP only when the
origin credential matches a root-only include under
`/etc/nginx/private/easy-swu-origin-keys/` and the supplied IP parses correctly.
EdgeOne sets the credential header; Nginx removes it before proxying upstream.
Missing or invalid credentials fall back to the direct peer address.
See [Easy SWU origin IP](docs/easy-swu-origin-ip.md) for the EdgeOne Free setup,
deployment, and regression checks. Never enable blanket proxy trust or trust
the leftmost caller-supplied address.

The six existing Flux controller Deployments are managed in
`infrastructure/flux-controllers` with their current pinned versions and resource
budgets. Controller adoption does not upgrade the controllers, CRDs or RBAC.
Easy SWU API and Bridge share `publish-identity-bridge.yml`; both image policies
remain independent. The publisher preserves that workflow's increasing tag counter.

## Repository map

Easy SWU's API loads mini-program virtual payment configuration from the
`easy-swu-wechat-pay` Secret. Populate `WECHAT_APP_ID`, `WECHAT_APP_SECRET`,
`WECHAT_VIRTUAL_PAY_ENABLED`, `WECHAT_VIRTUAL_PAY_OFFER_ID`,
`WECHAT_VIRTUAL_PAY_ENV=0`, the production/sandbox AppKeys, message Token and
EncodingAESKey before reconciling the API Deployment. Recovery material is
stored in the root-only `/etc/platform-secrets/easy-swu/wechat-pay.json`.
Replace obsolete merchant configuration keys when updating the Secret; the
`easy-swu-wechat-pay-pem` Secret and certificate mount are no longer used.
Credentials must never be committed or built into images.
The application's payment setting remains independently controlled by its
administration interface; publishing payment support does not change that setting.

- `infrastructure/ingress`: the bundled K3s Traefik chart, exposed only on the
  loopback NodePort `32080` for the host Nginx TLS edge.
- `infrastructure/tailscale`: the shared campus HTTP/HTTPS proxy, retained device
  state, and opt-in namespace/Pod access boundary. See [shared Tailscale](config/SHARED_TAILSCALE.md).
- `infrastructure/mysql`: the cluster-wide MySQL 8.4 LTS service, retained data
  and backup volumes, and a daily logical backup job.
- `infrastructure/identity`: Keycloak, Identity Bridge, the `lazycampus` realm,
  Smart Shop OIDC client, campus identity broker, network policies, and image
  automation for `auth.lazycampus.com`.
- `infrastructure/domain-automation`: an opt-in controller that reconciles
  Ingress hosts into Tencent EdgeOne and Cloudflare DNS.
- `apps/lazycampus-site`: `lazycampus.com` and `www.lazycampus.com`, built from
  `Gradient-Clipping/lazycampus-homepage/main` and published as
  `ccr.ccs.tencentyun.com/lazycampus/lazycampus-homepage:1.0.<run-number>`.
  The existing namespace, workload, routing, ImageRepository and ImagePolicy
  names remain `lazycampus-site`. The legacy `Gradient-Clipping/lazycampus-site`
  source repository is retained and only runs verification; it no longer
  publishes production images.
  The one-time EdgeOne cache operation is recorded outside active reconciliation
  in `operations/homepage-cache-refresh-20260908/`; its request was denied by
  the existing restricted CAM policy and requires separate cache verification.
- `apps/bbbto-mnp`: `bbbto.com` and `www.bbbto.com`, including a retained
  SQLite persistent volume.
- `apps/smart-shop`: `shop.lazycampus.com` and
  `shop-api.lazycampus.com`, including retained SQLite, uploads, public assets,
  exports, logs, and Redis volumes.
- `apps/easy-swu`: `easy-api.lazycampus.com` and
  `easy-admin.lazycampus.com`, including the mini-program API, management UI,
  Redis, MinIO, and MinIO backups; campus traffic uses the shared Tailscale proxy.
- `apps/status-page`: the anonymous public service status page at
  `status.lazycampus.com`, built from `Gradient-Clipping/lazycampus-status`.
  Its catalog presents core projects and their expandable services; internal
  Kubernetes inventory is visible only to the Keycloak administrator. Monitoring
  pulls from existing health endpoints and read-only Kubernetes APIs, so other
  applications do not depend on this service. New projects are added through
  `monitors.json` or the scoped administration API. The service uses its own
  `lazycampus_status` MySQL database and no persistent application volume.
  Run `scripts/bootstrap-status-page.sh --runtime-only` from a committed checkout
  before the first Flux rollout, then run the script without that flag to install
  the dedicated Nginx origin-authentication configuration and EdgeOne rule.
  Recovery credentials stay under `/etc/platform-secrets/status-*` and
  `/etc/platform-secrets/mysql-apps`; optional Sender delivery uses a separate
  `status-sender` Kubernetes Secret. The database joins the existing all-database
  backup. This same-host status page does not provide off-host outage monitoring.

- `apps/wecom-kf`: WeChat Customer Service platform in namespace `wecom-kf`, at
  `kf.lazycampus.com/callbacks/wecom/kf`, from the private
  `Gradient-Clipping/wecom-kf` source repository. Uses its own
  `educoder_wecom` MySQL database for durable notifications, conversations and jobs.
  Gateway, action and execution workers provide reply menus and confirmed tasks;
  `/admin` uses automatic SSO and requires the `platform-admin` role.
  Run `scripts/bootstrap-educoder-wecom.sh
  --runtime-only` from a committed checkout before the first rollout, then run
  without that flag for Nginx and its scoped EdgeOne rule. Supply root-only
  `educoder-wecom-corp-id`, `educoder-wecom-callback-token` and
  `educoder-wecom-aes-key` under `/etc/platform-secrets`. The full service also
  requires the API Secret, DeepSeek configuration and persistent encryption/SSO
  keys handled by `scripts/bootstrap-wecom-kf-runtime.py`.
  The bootstrap generates a dedicated origin key. Callback access logging and
  edge caching are disabled; Pod egress permits DNS, MySQL and public HTTPS.
  Database, Secret and image identifiers are retained from bootstrap. The namespace
  migration preserves runtime keys, shared MySQL data and the bank PVC contents;
  see `scripts/wecom-namespace-migration.md` for backups and recovery.
  After `kf.lazycampus.com` is healthy,
  `scripts/retire_wecom_previous_domain.py --apply` can retire only the original
  `educoder.lazycampus.com` DNS/EdgeOne resources, after checking ownership and
  backing up their exact definitions. It never deletes database or workload data.

Domains are declared in each application's `ingress.yaml`. An Ingress with
`platform.lazycampus.com/domain-automation: enabled` is reconciled every minute.
Hosts under `lazycampus.com` receive an EdgeOne acceleration domain, a
Cloudflare DNS-only CNAME, and an EdgeOne free certificate. Hosts under
`bbbto.com` receive a Cloudflare DNS-only A record pointing to the server. The
controller updates an existing A/AAAA/CNAME only when
`platform.lazycampus.com/domain-adopt-existing: "true"` is present. It
waits for a newly created EdgeOne domain to report `online` before switching
Cloudflare to its CNAME. It
deliberately never deletes a cloud domain or DNS record when a host is removed
from Git, so accidental manifest deletion cannot remove production DNS. Host
Nginx continues to forward the original `Host` header to Traefik.

Before installing Traefik, run `scripts/configure-k3s-nodeports.sh` once on the
server. It restricts every Kubernetes NodePort to `127.0.0.0/8`, restarts K3s,
and verifies that the node returns to `Ready`. This makes port `32080`
unreachable from the public interface while allowing host Nginx to use it.

After all application probes pass, `scripts/install-nginx-k3s-edge.sh` installs
the versioned files in `host/nginx`, validates the complete Nginx configuration,
and reloads it. A timestamped copy of every previous site file is retained
under `/var/backups`; validation failure restores the old files automatically.

Application credentials and registry pull credentials are intentionally absent
from Git. They are restored from root-only files on the server by
`scripts/bootstrap-runtime-secrets.sh` before Flux applies workloads.

## Persistent data

Static persistent volumes use node affinity for `easy-platform-1`, a `Retain`
reclaim policy, and the following host paths:

- `/srv/k3s-data/bbbto-mnp`
- `/srv/k3s-data/smart-shop`
- `/srv/k3s-data/smart-shop-redis`
- `/srv/k3s-data/mysql`
- `/srv/k3s-data/easy-swu/redis`
- `/srv/k3s-data/easy-swu/minio`
- `/srv/k3s-data/tailscale` (shared gateway)
- `/srv/k3s-data/easy-swu/tailscale` (offline migration recovery copy)
- `/srv/k3s-backups/mysql`
- `/srv/k3s-backups/easy-swu-minio`

Deleting a Deployment, namespace, PVC, or Flux object does not delete these
directories. Back them up before schema-changing releases.

## Shared MySQL

Applications must use the single cluster service
`mysql.mysql-system.svc.cluster.local:3306` instead of deploying their own
MySQL server. The service is ClusterIP-only and is not exposed by an Ingress or
NodePort. MySQL is pinned to an explicit 8.4 LTS image and upgrades are
deliberate Git changes rather than automatic database rollouts.

Each application receives a separate database and least-privilege user. On the
server, after the application's namespace exists, run:

```sh
/usr/local/sbin/provision-mysql-database \
  <database> <application-namespace> [secret-name] [username]
```

The idempotent script creates the database, limits the user to that database,
and writes `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`,
`MYSQL_PASSWORD`, and `DATABASE_URL` into the application namespace Secret.
Passwords are generated once and retained under
`/etc/platform-secrets/mysql-apps`; no credential is committed or printed.
Database schema migrations remain owned by each application release.
The installed helper is versioned as `scripts/provision-mysql-database.sh` in
this repository.

A logical backup of all databases runs daily at 03:17 Asia/Shanghai and keeps
5 days under `/srv/k3s-backups/mysql`. These local backups protect against
application-level mistakes but do not replace an off-server backup of the
host paths and `/etc/platform-secrets`.

The shared instance uses a bounded memory profile for its 1 GiB container limit.
See [the memory profile and recovery checks](config/MYSQL_MEMORY.md) before
changing its cache or Performance Schema sizing.

## Unified identity platform

The public identity endpoint is `https://auth.lazycampus.com`. Host Nginx
terminates TLS and forwards only the campus broker and realm endpoints to the
loopback Traefik NodePort. The Keycloak administration console is not exposed
by the public Ingress.

Before the first reconciliation, install and run the idempotent bootstrap:

```sh
install -m 0755 scripts/bootstrap-identity-secrets.sh \
  /usr/local/sbin/bootstrap-identity-secrets
/usr/local/sbin/bootstrap-identity-secrets
```

It creates the `identity-system` namespace, registry/runtime Secrets, dedicated
`keycloak` and `identity_bridge` databases, and updates the existing Smart Shop
runtime Secret with its Identity Bridge and OIDC settings. Recovery material
remains under `/etc/platform-secrets`; rerunning the command preserves existing
values. Before running it, store the initial password for the additional managed
platform administrator in the root-only
`/etc/platform-secrets/keycloak-additional-platform-admin-password` file. Keycloak
imports the realm on first start, while the hourly profile reconciler keeps the
managed identity attributes and the `platform-admin` role for `ystemsrx` and
`gyx517120273` aligned with Git. The additional account receives the default
Headlamp read-only group, not the Kubernetes `cluster-admin` binding, and must
change its initial password and configure TOTP on first login.

Useful checks:

```sh
k3s kubectl -n identity-system get deploy,pod,service,ingress,cronjob
k3s kubectl -n identity-system rollout status deployment/keycloak --timeout=600s
k3s kubectl -n identity-system rollout status deployment/identity-bridge --timeout=300s
curl -fsS https://auth.lazycampus.com/realms/lazycampus/.well-known/openid-configuration >/dev/null
curl -fsS https://auth.lazycampus.com/campus/.well-known/openid-configuration >/dev/null
```

Smart Shop keeps its original application session and third-party login route,
and additionally exposes the OIDC authorization-code flow. Easy SWU retains its
existing mini-program session contract and delivers successful identity changes
to Identity Bridge through its outbox.

## Easy SWU

Before the first Easy SWU reconciliation, run the identity bootstrap so the
`easy-swu-admin` OIDC client exists, then place the Baidu Maps
values in the root-only files documented by
`scripts/bootstrap-easy-swu-secrets.sh`. Install and run the idempotent
bootstrap:

```sh
install -m 0755 scripts/bootstrap-easy-swu-secrets.sh \
  /usr/local/sbin/bootstrap-easy-swu-secrets
/usr/local/sbin/bootstrap-easy-swu-secrets
```

The bootstrap creates the `easy_swu` database and least-privilege account,
generates new API and MinIO secrets, restores TCR pull access, prepares retained
host directories, and creates the runtime Secrets without printing their
values. The API performs its own ordered migrations before serving traffic.
The management UI has no application-local login. It redirects to Keycloak and
the API accepts any OIDC identity with the `platform-admin` realm role; usernames
remain part of the audit identity but are not an authorization allowlist.

The API connects to the shared `tailscale-proxy.tailscale-system.svc.cluster.local`
userspace HTTP proxy on port 1055 and its health endpoint on port 9002. The gateway
has its own release lifecycle and retained state; Kubernetes DNS continues to use
CoreDNS. Bootstrap, migration, access labels and verification are documented in
[shared Tailscale](config/SHARED_TAILSCALE.md). Redis contains sessions and cache,
while MinIO contains calendars and publication media. MinIO is mirrored daily
to `/srv/k3s-backups/easy-swu-minio` with 14-day retention; the shared MySQL
backup includes the `easy_swu` database.

EdgeOne terminates public HTTPS for `easy-api` and `easy-admin` and applies the
versioned origin-response budgets. `scripts/reconcile-easy-swu-edge.py` owns
them: 40 seconds for the campus API, and 120 seconds for
`/api/v1/admin/watermarks/decode`, which the general rule excludes because
EdgeOne assigns rule priority itself and accepts only the prefixed
`and not ${...} in [...]` negation. The origin-credential header rule stays
manual: its value is a secret, as `docs/easy-swu-origin-ip.md` describes.

Useful checks:

```sh
k3s kubectl -n easy-swu get deploy,pod,service,ingress,pvc,cronjob
k3s kubectl -n easy-swu rollout status deployment/easy-swu-api --timeout=600s
k3s kubectl -n easy-swu rollout status deployment/easy-swu-admin --timeout=300s
curl -fsS https://easy-api.lazycampus.com/api/v1/system/ready
curl -fsS https://easy-admin.lazycampus.com/healthz
python3 scripts/reconcile-easy-swu-edge.py --check
python3 -B -m unittest discover -s tests -v
```

## Bootstrap-only secrets

The following Kubernetes secrets are intentionally created out of band and are never committed:

- `flux-system/flux-system`: a fine-grained GitHub token scoped to this repository for read/write GitOps reconciliation.
- `flux-system/tcr-auth`: the Tencent TCR credential used by image reflection.
- `ingress-system/tcr-auth`, `lazycampus-site/tcr-auth`,
  `bbbto-mnp/tcr-auth`, `smart-shop/tcr-auth`, and `easy-swu/tcr-auth`:
  namespace-scoped TCR pull credentials.
- `bbbto-mnp/bbbto-runtime`: the existing book mapping and WeChat credentials.
- `smart-shop/smart-shop-env`: the existing production environment.
- `smart-shop/smart-shop-registration-validation`: the existing local student
  number validation policy.
- `domain-system/tencentcloud-credentials`: a dedicated CAM API key limited to
  the EdgeOne read-and-upsert actions used by domain automation.
- `domain-system/cloudflare-credentials`: an API token with DNS Write access
  limited to the `lazycampus.com` and `bbbto.com` zones.
- `domain-system/tcr-auth`: the registry pull credential for the controller.
- `mysql-system/tcr-auth`: the registry pull credential for MySQL.
- `mysql-system/mysql-credentials`: fixed root and backup-user passwords used
  by MySQL initialization and daily backups.
- `easy-swu/mysql-easy-swu`: the dedicated shared-MySQL connection values.
- `easy-swu/easy-swu-runtime`: API, OIDC client, MinIO, Baidu Maps, and Identity
  Bridge synchronization values.
- `tailscale-system/tailscale-auth`: the shared gateway enrollment key.
- `tailscale-system/tcr-auth`: the shared gateway registry pull credential.
- `easy-swu/easy-swu-tailscale`: retained legacy enrollment material for migration
  recovery; new deployments do not reference it.

K3s encrypts Kubernetes secrets at rest. Their recovery material is stored root-only on the server and must be included in server backups.

Create root-only `mysql-root-password` and `mysql-backup-password` files in
`/etc/platform-secrets`, then run `scripts/bootstrap-mysql-secrets.sh` before
the first MySQL reconciliation. Changing those files later does not rotate the
passwords already stored inside MySQL.

Create the three root-only files `tencentcloud-secret-id`,
`tencentcloud-secret-key`, and `cloudflare-api-token` in
`/etc/platform-secrets`, then run
`scripts/bootstrap-domain-reconciler-secrets.sh`. The script also copies the
existing TCR credential into `domain-system` and never prints any secret value.
The custom CAM policy is versioned at
`policies/tencent-domain-reconciler.json`; it contains no delete permission and
accepts API calls only from the server's public IP.

## Domain reconciliation

The domain controller uses a Kubernetes Ingress LIST/WATCH. Only changes to
managed hostnames, adoption settings, zone configuration, or the optional
`platform.lazycampus.com/domain-reconcile-request` annotation enqueue cloud work.
Ingress status updates and unrelated annotations do not trigger provider calls.
Events are coalesced on a five-second local tick. Service-account tokens are
reloaded for each connection; disconnected watches resume at the latest resource
version, and expired versions (HTTP/event 410) trigger a fresh list. New cloud
batches pause while the watch is disconnected or configuration is invalid.

Startup and `FULL_RECONCILE_INTERVAL_SECONDS=3600` perform full drift audits.
Cloudflare records are read once per selected zone and EdgeOne domains once per
selected EdgeOne zone, with pagination and complete-response validation. At the
current size (two Cloudflare zones, one EdgeOne zone, one page each), an unchanged
audit uses three reads, or about 72 per day plus startup/events/retries. Pending
or failed hosts alone retry after 15, 30, 60, 120, 240, then 300 seconds, with up
to 20% jitter. HTTP 429 Retry-After and Tencent request-limit errors enforce a
provider-wide cooldown. Ready hosts leave the retry queue.

An EdgeOne domain must be online before the controller creates/updates its CNAME.
Certificate application follows DNS convergence; a request is not completion.
The controller waits for a deployed, unexpired certificate, and never repeatedly
requests a certificate whose mode is already `eofreecert` or `sslcert`.
Removing an Ingress or disabling automation only cancels local work; the existing
adoption guard and no-automatic-deletion policy remain in effect.

Inspect `reconcile_batch_completed` for the reason, selected/ready/pending/error
counts and actual `cloud_api_calls`. Every five minutes `controller_idle_status`
reports cumulative calls, queued hosts, readiness and the next audit. These
status logs make no cloud requests. `/readyz` reflects valid input/watch state
and reconciliation errors, not whether all certificates have finished applying.

For an immediate scoped audit, change the following annotation on the target
Ingress **in Git** and let Flux apply it (use a new value each time):

```yaml
platform.lazycampus.com/domain-reconcile-request: "2026-09-11T06:00:00Z"
```

The controller reads the projected zone ConfigMap locally every five seconds;
Kubernetes projection latency may delay hot reload. No extra ConfigMap RBAC is
required. A new controller process always reconstructs state with a full audit.

## Adding a domain

1. Add the hostname and route to the application's `Ingress` in this repository.
2. Add the domain automation annotation and, when intentionally taking over an
   existing A/CNAME, the adoption annotation shown above.
3. Add a zone entry to `infrastructure/domain-automation/config.yaml` only when
   the hostname belongs to a new registered root domain. Include its Cloudflare
   zone ID and choose either direct A records or EdgeOne CNAMEs. Use
   `hostHeaderOverrides` for an EdgeOne hostname that intentionally reuses a
   different origin virtual host.
4. Merge the change. Flux applies the Ingress, and the controller performs the
   EdgeOne/Cloudflare upsert. Cloud-side deletion remains a deliberate manual
   step.

Flux uses GitHub directly because repeated checks from the server were consistently successful, while the EdgeOne-hosted Xget endpoint intermittently returned upstream `504` responses for this private repository. `xget.lazycampus.com` remains available as an IP-restricted fallback and Gitee is not part of the delivery path.

## Controller bootstrap

Flux CLI `v2.9.4` installs the standard controllers plus image reflection and automation:

```sh
KUBECONFIG=/etc/rancher/k3s/k3s.yaml flux install \
  --components-extra=image-reflector-controller,image-automation-controller \
  --network-policy=true \
  --watch-all-namespaces=true
```

After the initial installation has cached the pinned controller images, run `scripts/bootstrap-flux-controllers-tcr.sh` as root on the server. It mirrors only the node's `linux/amd64` images into TCR, switches all six deployments, and waits for every rollout. This server-side bootstrap avoids the unreliable cross-region upload path from GitHub-hosted runners to TCR.

## Lazy Campus 开放平台

- 源码：`Gradient-Clipping/lazycampus-platform`，发布分支 `main`，镜像 `lazycampus/lazycampus-platform:1.0.<run_number>`。
- 配置：`clusters/easy-platform/apps/open-platform/`；域名 `platform.lazycampus.com`。
- 独立命名空间 `open-platform`、MySQL 数据库及账户 `lazycampus_platform`、Redis 数据目录 `/srv/k3s-data/open-platform/redis`。
- Sender 事务邮件通过独立的 `platform-sender` Secret 注入。把 API 密钥及发件地址分别写入 `/etc/platform-secrets/platform-sender-api-key`、`platform-sender-from-email`（权限 0600），再执行运行时引导；API 密钥不进入 Git。未配置时只提供站内通知；配置后用户仍需验证个人邮箱并主动开启邮件提醒。
- Keycloak 客户端由 `clusters/easy-platform/infrastructure/identity/open-platform.yaml` 的初始化 Job 和每小时调谐任务维护。复用已有 `ystemsrx` 管理员；本配置不创建用户或修改管理员密码。
- 仅学校身份和指定管理员可以登录；客户端使用精确回调地址、PKCE S256、内部后端登出与受控身份属性映射。
- Easy SWU 通过独立 HMAC 签名处理 `/internal/platform/v1/` 查询。公网 Easy SWU Ingress 仅发布 `/api/v1`，内部查询入口不暴露；共享服务继续使用原有校园缓存及会话。

首次部署顺序：

1. 将已提交的本仓库版本放到 K3s 宿主机，执行 `bash scripts/bootstrap-open-platform.sh --runtime-only`，准备数据库和 Secret。
2. 合并上述 Kubernetes 配置至 `main`，等待 Flux 创建应用、Keycloak 客户端及自动域名。
3. 执行同一版本的 `bash scripts/bootstrap-open-platform.sh`，安装平台专用 Nginx 文件及 EdgeOne 规则。
4. 验证 `https://platform.lazycampus.com/readyz` 中的提交版本，并检查 SSO、应用授权和校园查询。

引导脚本只操作开放平台所需资源，运行密钥保存在 `/etc/platform-secrets/platform-*`。Nginx 仅在回源密钥校验通过后接受 EdgeOne 的客户端 IP，清除转发给应用的回源密钥；EdgeOne 对本域名禁用缓存与离线缓存。规则来源为 `scripts/reconcile-open-platform-edge.py`，默认执行为只读计划，添加 `--apply` 才写入，`--check` 只读核对已启用规则、缓存、回源密钥及真实 IP 配置；其余域名规则保持不变。
