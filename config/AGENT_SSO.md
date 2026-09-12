# Agent Admin SSO

`agent.lazycampus.com` uses the existing `lazycampus` Keycloak realm and the
confidential `lazycampus-agent-admin` client. Only the `platform-admin` realm role
grants dashboard access. Opening the site starts authorization automatically;
an existing Keycloak session needs no additional login button.

The client, exact callback/logout URLs, PKCE requirement and role mapper are managed
by `infrastructure/identity/agent-admin.yaml`. Its initial Job and hourly reconciler
use the existing Keycloak image and administrative Secret. This does not upgrade
Keycloak or change other clients.

After this commit passes validation and becomes the exact current `production`
revision, provision its dedicated credential:

```powershell
python scripts/run_agent_sso_bootstrap.py --revision <40-character-production-sha> --run-id <successful-validation-run>
```

The runner keeps `/etc/platform-secrets/agent-admin-oidc-client-secret` mode 0600 and
creates matching `agent-admin-oidc-secret` Secrets in `identity-system` and
`lazycampus-agent`. Repeated runs preserve the value; mismatches fail without
rotating credentials. Recovery uses that retained file and the same gated runner.

After the bootstrap Job completes, enable `AGENT_ADMIN_SSO_ENABLED=true` and the
Secret reference in the AstrBot Deployment with the SSO-capable image. HTTP probes
use `/auth/sso/health`. Password/setup/desktop login and standalone old JWT or API
keys cannot authenticate the dashboard in SSO mode. Native channel callbacks retain
their own signature checks; QQ users are unaffected.

Provider credentials stay in `/AstrBot/data/agent-sso.sqlite3`, covered by the
existing AstrBot PVC and backup. The browser receives only an opaque secure session
cookie. Sessions have an eight-hour maximum and 30-minute idle expiry; refreshed
identity and role checks run at least every two minutes during use. Keycloak signed
backchannel logout revokes local sessions, including active dashboard streams.
Expired authentication state is pruned during normal use. The Agent sandbox never
receives these credentials or this volume.
