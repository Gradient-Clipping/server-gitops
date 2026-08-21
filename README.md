# Server GitOps

This private repository is the desired state for the single-node `easy-platform-1` K3s cluster.

## Delivery path

1. A source repository builds and tests an image in GitHub Actions.
2. The workflow publishes immutable tags to Tencent TCR.
3. Flux image reflection selects the newest allowed tag.
4. Flux image automation commits the tag change to this repository.
5. Flux reconciliation applies the reviewed desired state to K3s.

The current `platform-smoke` workload exists only to validate that path. It is a private `ClusterIP` service and does not occupy ports `80` or `443`.

## Bootstrap-only secrets

The following Kubernetes secrets are intentionally created out of band and are never committed:

- `flux-system/flux-system`: a fine-grained GitHub token scoped to this repository for read/write GitOps reconciliation.
- `flux-system/tcr-auth`: the Tencent TCR credential used by image reflection.
- `platform-smoke/tcr-auth`: the Tencent TCR image pull credential.

K3s encrypts Kubernetes secrets at rest. Their recovery material is stored root-only on the server and must be included in server backups.

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
