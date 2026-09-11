#!/usr/bin/env python3
"""Generate Flux receivers and reconcile GitHub hooks from the versioned catalog.

Only Python's standard library is required. Runtime credentials and generated
webhook paths stay in memory; neither is printed or written into the checkout.
"""
import argparse
import hashlib
import hmac
import json
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
CATALOG = ROOT / "config/flux-webhooks.json"
MANIFEST = ROOT / "clusters/easy-platform/flux-system/webhooks/receivers.json"


def catalog():
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def receiver(config, hook):
    # JSON string quoting is also valid CEL string quoting.
    repo = json.dumps(hook["repository"])
    branch = json.dumps(hook["branch"])
    expression = f"has(req.repository) && req.repository.full_name == {repo}"
    if hook["event"] == "push":
        expression += (
            f" && has(req.ref) && req.ref == {json.dumps('refs/heads/' + hook['branch'])}"
            " && has(req.deleted) && req.deleted == false"
        )
        kind, api = "GitRepository", "source.toolkit.fluxcd.io/v1"
    else:
        expression += (
            " && has(req.action) && req.action == 'completed'"
            " && has(req.workflow_run) && req.workflow_run.status == 'completed'"
            " && req.workflow_run.conclusion == 'success'"
            " && req.workflow_run.event in ['push', 'workflow_dispatch']"
            f" && req.workflow_run.head_branch == {branch}"
            f" && req.workflow_run.head_repository.full_name == {repo}"
            f" && req.workflow_run.path == {json.dumps(hook['workflow'])}"
        )
        kind, api = "ImageRepository", "image.toolkit.fluxcd.io/v1"
    return {
        "apiVersion": "notification.toolkit.fluxcd.io/v1", "kind": "Receiver",
        "metadata": {"name": hook["name"], "namespace": config["namespace"]},
        "spec": {
            "type": "github", "events": ["ping", hook["event"]],
            "secretRef": {"name": config["secretName"]},
            "resourceFilter": expression,
            "resources": [{"apiVersion": api, "kind": kind, "name": name}
                          for name in hook["resources"]],
        },
    }


def rendered(config):
    value = {"apiVersion": "v1", "kind": "List",
             "items": [receiver(config, hook) for hook in config["hooks"]]}
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def command(args, data=None):
    result = subprocess.run(args, input=data, capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    if result.returncode:
        # Do not include output: provider errors can repeat a sensitive URL/body.
        raise RuntimeError(f"{args[0]} command failed (exit {result.returncode})")
    return result.stdout


def gh_api(path, method="GET", payload=None):
    args = ["gh", "api", "--method", method, path]
    if payload is not None:
        args += ["--input", "-"]
    value = command(args, None if payload is None else json.dumps(payload))
    return json.loads(value) if value.strip() else None


def cluster(server, kind, name=None):
    cmd = f"k3s kubectl -n flux-system get {kind}"
    if name:
        cmd += " " + name
    return json.loads(command(["ssh", "-o", "BatchMode=yes", server, cmd + " -o json"]))


def endpoints(config, server):
    values = cluster(server, "receivers")
    result = {}
    for item in values["items"]:
        status = item.get("status", {})
        if not any(c["type"] == "Ready" and c["status"] == "True"
                   for c in status.get("conditions", [])):
            continue
        if status.get("webhookPath", "").startswith("/hook/"):
            result[item["metadata"]["name"]] = (
                "https://" + config["host"] + status["webhookPath"])
    for hook in config["hooks"]:
        if hook["name"] not in result:
            raise RuntimeError(f"Receiver is not ready: {hook['name']}")
    return result


def runtime_token(config, server):
    value = command(["ssh", "-o", "BatchMode=yes", server,
                     "cat " + config["secretFile"]]).strip()
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise RuntimeError("Unexpected webhook credential format")
    return value


def desired_hook(hook, url, token=None):
    result = {"name": "web", "active": True, "events": [hook["event"]],
              "config": {"url": url, "content_type": "json", "insecure_ssl": "0"}}
    if token is not None:
        result["config"]["secret"] = token
    return result


def hook_matches(existing, desired):
    return (existing.get("active") is True
            and set(existing.get("events", [])) == set(desired["events"])
            and all(str(existing.get("config", {}).get(k)) == str(v)
                    for k, v in desired["config"].items() if k != "secret"))


def reconcile(config, args):
    urls = endpoints(config, args.server)
    token = runtime_token(config, args.server) if args.apply else None
    repositories = {}
    for hook in config["hooks"]:
        repo = hook["repository"]
        if repo not in repositories:
            # GitHub permits at most 20 hooks of a given event per repository.
            repositories[repo] = gh_api(f"repos/{repo}/hooks?per_page=100")
        url = urls[hook["name"]]
        matches = [h for h in repositories[repo] if h.get("config", {}).get("url") == url]
        if len(matches) > 1:
            raise RuntimeError(f"Duplicate managed hooks in {repo}: {hook['name']}")
        desired = desired_hook(hook, url, token)
        existing = matches[0] if matches else None
        action = ("unchanged" if existing and hook_matches(existing, desired)
                  and not args.refresh_secret else "update" if existing else "create")
        if args.apply and action != "unchanged":
            path = f"repos/{repo}/hooks" + (f"/{existing['id']}" if existing else "")
            gh_api(path, "PATCH" if existing else "POST", desired)
        print(f"{hook['name']} ({repo}): {action}" + (" [dry run]" if not args.apply else ""), flush=True)


def sample_payload(hook):
    payload = {"repository": {"full_name": hook["repository"]}}
    if hook["event"] == "push":
        payload.update(ref="refs/heads/" + hook["branch"], deleted=False)
    else:
        payload.update(action="completed", workflow_run={
            "status": "completed", "conclusion": "success", "event": "push",
            "head_branch": hook["branch"], "path": hook["workflow"],
            "head_repository": {"full_name": hook["repository"]},
        })
    return payload


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post(url, hook, token, payload, valid_signature=True):
    body = json.dumps(payload).encode()
    key = token.encode() if valid_signature else b"invalid-test-signature"
    headers = {"Content-Type": "application/json", "User-Agent": "LazyCampus-Webhook-Verification",
               "X-GitHub-Event": hook["event"], "X-GitHub-Delivery": str(uuid.uuid4()),
               "X-Hub-Signature": "sha1=" + hmac.new(key, body, hashlib.sha1).hexdigest(),
               "X-Hub-Signature-256": "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()}
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("Webhook endpoint connection failed") from None


def marks(server, hook):
    kind = "gitrepositories" if hook["event"] == "push" else "imagerepositories"
    items = cluster(server, kind)["items"]
    selected = {item["metadata"]["name"]: item for item in items
                if item["metadata"]["name"] in hook["resources"]}
    if set(selected) != set(hook["resources"]):
        raise RuntimeError("A receiver target is missing")
    return {name: item["metadata"].get("annotations", {}).get("reconcile.fluxcd.io/requestedAt")
            for name, item in selected.items()}


def verify(config, args):
    urls = endpoints(config, args.server)
    token = runtime_token(config, args.server)
    for hook in config["hooks"]:
        if args.hook and hook["name"] != args.hook:
            continue
        before = marks(args.server, hook)
        valid = sample_payload(hook)
        rejected = post(urls[hook["name"]], hook, token, valid, valid_signature=False)
        if rejected not in (400, 401, 403):
            raise RuntimeError(f"Invalid signature not rejected: {hook['name']} (HTTP {rejected})")
        invalid = []
        if hook["event"] == "push":
            invalid += [{**valid, "ref": "refs/heads/not-production"}, {**valid, "deleted": True}]
        else:
            for patch in ({"conclusion": "failure"}, {"event": "pull_request"},
                          {"head_branch": "not-production"}, {"path": ".github/workflows/other.yml"},
                          {"head_repository": {"full_name": "untrusted/fork"}}):
                invalid.append({**valid, "workflow_run": {**valid["workflow_run"], **patch}})
        invalid.append({**valid, "repository": {"full_name": "untrusted/repository"}})
        for payload in invalid:
            status = post(urls[hook["name"]], hook, token, payload)
            if status not in (200, 202):
                raise RuntimeError(f"Filtered payload failed: {hook['name']} (HTTP {status})")
        if marks(args.server, hook) != before:
            raise RuntimeError(f"A rejected event requested reconciliation: {hook['name']}")
        status = post(urls[hook["name"]], hook, token, valid)
        if status not in (200, 202):
            raise RuntimeError(f"Valid webhook failed: {hook['name']} (HTTP {status})")
        after = marks(args.server, hook)
        if any(after[name] == before[name] for name in before):
            raise RuntimeError(f"Valid webhook did not trigger every target: {hook['name']}")
        print(f"{hook['name']}: signature, event filtering and all target reconciliations passed", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["render", "check", "reconcile", "verify"])
    parser.add_argument("--server", default="root@1.14.95.189")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--refresh-secret", action="store_true")
    parser.add_argument("--hook")
    args = parser.parse_args()
    config = catalog()
    if args.action == "render":
        MANIFEST.write_text(rendered(config), encoding="utf-8", newline="\n")
    elif args.action == "check":
        if MANIFEST.read_text(encoding="utf-8") != rendered(config):
            raise RuntimeError("Generated receivers differ; run scripts/flux_webhooks.py render")
        print("Generated receivers match the catalog")
    elif args.action == "reconcile":
        reconcile(config, args)
    else:
        verify(config, args)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
