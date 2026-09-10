# Server GitOps

This private repository is the desired state for the single-node `easy-platform-1` K3s cluster.

## Delivery path

1. A source repository builds and tests an image in GitHub Actions.
2. The workflow publishes immutable tags to Tencent TCR.
3. Flux image reflection selects the newest allowed tag.
4. Flux image automation commits the tag change to this repository.
5. Flux reconciliation applies the reviewed desired state to K3s.

The original `platform-smoke` workload was removed after the first production
workloads exercised the same image automation path.

Easy SWU uses explicit `TRUST_PROXY_CIDRS` in its API Deployment. Deploy the API
change together with `host/nginx/easy-swu`, which replaces caller-supplied
`X-Forwarded-For` chains. The ingress NetworkPolicy remains part of this trust
boundary. Apply host Nginx files through the host configuration workflow; Flux
does not install them. With EdgeOne and no verified real-IP configuration,
limits apply to the edge node IP. Restore client IPs only after installing and
maintaining verified EdgeOne source CIDRs and checking that EdgeOne overwrites
the chosen client-IP header. Never enable blanket proxy trust or trust the
leftmost caller-supplied address.

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
  The existing namespace, workload, routing and image automation resource names
  remain `lazycampus-site`. The legacy `Gradient-Clipping/lazycampus-site`
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
  Redis, MinIO, a persistent Tailscale userspace gateway, and MinIO backups.

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
- `/srv/k3s-data/easy-swu/tailscale`
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
14 days under `/srv/k3s-backups/mysql`. These local backups protect against
application-level mistakes but do not replace an off-server backup of the
host paths and `/etc/platform-secrets`.

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
values. Keycloak imports the realm on first start, while the hourly profile
reconciler keeps the managed identity attributes aligned with Git.

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
`easy-swu-admin` OIDC client exists, then place the Baidu Maps and Tailscale
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
the API accepts only the `ystemsrx` OIDC identity with the `platform-admin`
realm role.

The Tailscale sidecar uses userspace networking and exposes only a loopback HTTP
proxy to the API container. Its state survives Pod recreation under
`/srv/k3s-data/easy-swu/tailscale`; DNS takeover is disabled so Kubernetes
service discovery continues to use CoreDNS. Redis contains sessions and cache,
while MinIO contains calendars and publication media. MinIO is mirrored daily
to `/srv/k3s-backups/easy-swu-minio` with 14-day retention; the shared MySQL
backup includes the `easy_swu` database.

Useful checks:

```sh
k3s kubectl -n easy-swu get deploy,pod,service,ingress,pvc,cronjob
k3s kubectl -n easy-swu rollout status deployment/easy-swu-api --timeout=600s
k3s kubectl -n easy-swu rollout status deployment/easy-swu-admin --timeout=300s
curl -fsS https://easy-api.lazycampus.com/api/v1/system/ready
curl -fsS https://easy-admin.lazycampus.com/healthz
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
- `easy-swu/easy-swu-tailscale`: the Tailscale enrollment key used only by the
  campus-network sidecar.

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
- Keycloak 客户端由 `clusters/easy-platform/infrastructure/identity/open-platform.yaml` 的初始化 Job 和每小时调谐任务维护。复用已有 `ystemsrx` 管理员；本配置不创建用户或修改管理员密码。
- 仅学校身份和指定管理员可以登录；客户端使用精确回调地址、PKCE S256、内部后端登出与受控身份属性映射。
- Easy SWU 通过独立 HMAC 签名处理 `/internal/platform/v1/` 查询。公网 Easy SWU Ingress 仅发布 `/api/v1`，内部查询入口不暴露；共享服务继续使用原有校园缓存及会话。

首次部署顺序：

1. 将已提交的本仓库版本放到 K3s 宿主机，执行 `bash scripts/bootstrap-open-platform.sh --runtime-only`，准备数据库和 Secret。
2. 合并上述 Kubernetes 配置至 `main`，等待 Flux 创建应用、Keycloak 客户端及自动域名。
3. 执行同一版本的 `bash scripts/bootstrap-open-platform.sh`，安装平台专用 Nginx 文件及 EdgeOne 规则。
4. 验证 `https://platform.lazycampus.com/readyz` 中的提交版本，并检查 SSO、应用授权和校园查询。

引导脚本只操作开放平台所需资源，运行密钥保存在 `/etc/platform-secrets/platform-*`。Nginx 仅在回源密钥校验通过后接受 EdgeOne 的客户端 IP，清除转发给应用的回源密钥；EdgeOne 对本域名禁用缓存与离线缓存。规则来源为 `scripts/reconcile-open-platform-edge.py`，默认执行为只读计划，添加 `--apply` 才写入，`--check` 只读核对已启用规则、缓存、回源密钥及真实 IP 配置；其余域名规则保持不变。
