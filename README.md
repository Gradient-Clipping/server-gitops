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

## Repository map

- `infrastructure/ingress`: the bundled K3s Traefik chart, exposed only on the
  loopback NodePort `32080` for the host Nginx TLS edge.
- `infrastructure/domain-automation`: an opt-in controller that reconciles
  Ingress hosts into Tencent EdgeOne and Cloudflare DNS.
- `apps/lazycampus-site`: `lazycampus.com` and `www.lazycampus.com`.
- `apps/bbbto-mnp`: `bbbto.com` and `www.bbbto.com`, including a retained
  SQLite persistent volume.
- `apps/smart-shop`: `shop.lazycampus.com` and
  `shop-api.lazycampus.com`, including retained SQLite, uploads, public assets,
  exports, logs, and Redis volumes.

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
and reloads it. A timestamped copy of all three previous site files is retained
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

Deleting a Deployment, namespace, PVC, or Flux object does not delete these
directories. Back them up before schema-changing releases.

## Bootstrap-only secrets

The following Kubernetes secrets are intentionally created out of band and are never committed:

- `flux-system/flux-system`: a fine-grained GitHub token scoped to this repository for read/write GitOps reconciliation.
- `flux-system/tcr-auth`: the Tencent TCR credential used by image reflection.
- `ingress-system/tcr-auth`, `lazycampus-site/tcr-auth`,
  `bbbto-mnp/tcr-auth`, and `smart-shop/tcr-auth`: namespace-scoped TCR pull
  credentials.
- `bbbto-mnp/bbbto-runtime`: the existing book mapping and WeChat credentials.
- `smart-shop/smart-shop-env`: the existing production environment.
- `smart-shop/smart-shop-registration-validation`: the existing local student
  number validation policy.
- `domain-system/tencentcloud-credentials`: a dedicated CAM API key limited to
  the EdgeOne read-and-upsert actions used by domain automation.
- `domain-system/cloudflare-credentials`: an API token with DNS Write access
  limited to the `lazycampus.com` and `bbbto.com` zones.
- `domain-system/tcr-auth`: the registry pull credential for the controller.

K3s encrypts Kubernetes secrets at rest. Their recovery material is stored root-only on the server and must be included in server backups.

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
