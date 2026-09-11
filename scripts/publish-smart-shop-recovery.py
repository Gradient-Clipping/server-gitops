#!/usr/bin/env python3
"""Publish verified Smart Shop source locally when cross-region CI uploads stall."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import urllib.request

REPOSITORY = "ystemsrx/smart-shop"
IMAGE = "ccr.ccs.tencentyun.com/lazycampus/smart-shop-backend"
SOURCE = "https://github.com/" + REPOSITORY
RUNTIME_INPUTS = ("docker/Dockerfile.backend", "docker/backend-entrypoint.sh", "backend/requirements.txt", ".dockerignore")


def github(path):
    request = urllib.request.Request("https://api.github.com/repos/" + REPOSITORY + path,
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "LazyCampus-release-recovery"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def verified_tree(revision):
    checks = github(f"/commits/{revision}/check-runs")
    for name in ("verify-backend", "verify-frontend"):
        if not any(c["name"] == name and c["head_sha"] == revision and c["conclusion"] == "success" for c in checks["check_runs"]):
            raise ValueError(f"Required successful check is missing: {name}")
    tree = github(f"/git/trees/{revision}?recursive=1")
    if tree.get("truncated"):
        raise ValueError("Source tree is incomplete")
    return {entry["path"]: entry["sha"] for entry in tree["tree"]
            if entry["type"] == "blob" and (entry["path"] == ".dockerignore" or entry["path"].startswith(("backend/", "docker/")))}


def validate_archive(archive, revision, expected):
    found = set()
    size = 0
    if archive.pax_headers.get("comment", "").strip() != revision:
        raise ValueError("Archive revision does not match")
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or "\\" in member.name or not (member.isdir() or member.isfile()):
            raise ValueError("Unsafe archive entry")
        if member.isdir():
            continue
        size += member.size
        if size > 64 * 1024 * 1024 or member.size > 16 * 1024 * 1024:
            raise ValueError("Archive exceeds the source size limit")
        if member.name not in expected or member.name in found:
            raise ValueError("Unexpected or duplicate source file")
        data = archive.extractfile(member).read()
        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if actual != expected[member.name]:
            raise ValueError("Source file does not match the verified Git commit")
        found.add(member.name)
    if found != set(expected):
        raise ValueError("Verified source files are missing")


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def compatible_runtime(expected, baseline):
    if any(not expected.get(path) or expected[path] != baseline.get(path) for path in RUNTIME_INPUTS):
        raise ValueError("Runtime inputs changed; a complete Dockerfile build is required")


def registry_environment(directory):
    environment = {**os.environ, "DOCKER_CONFIG": str(directory / "docker-config")}
    secret_dir = Path("/etc/platform-secrets")
    run(["docker", "login", "ccr.ccs.tencentyun.com", "--username", (secret_dir / "tcr-username").read_text().strip(), "--password-stdin"],
        input=(secret_dir / "tcr-password").read_text(), text=True, capture_output=True, env=environment)
    return environment


def check_image(image):
    code = """
import sqlite3
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.monitoring import install_monitoring
with sqlite3.connect('/tmp/status-smoke.db') as db:
    for table in ('products','product_variants','carts','orders','payment_qr_codes'):
        db.execute('CREATE TABLE '+table+' (id INTEGER)')
app=FastAPI()
install_monitoring(app)
with TestClient(app) as client:
    assert client.get('/internal/monitoring/v1/state').status_code==404
    response=client.get('/internal/monitoring/v1/state',headers={'Authorization':'Bearer '+'s'*32})
    assert response.status_code==200
    assert all(c['status']=='operational' for c in response.json()['components'].values())
    assert set(response.json()['components'])=={'catalog','orders','payments'}
print('Image report authentication and read-only component checks passed.')
"""
    environment = {"ADMIN_USERNAME": "status-test", "ADMIN_PASSWORD": "status-test-password", "API_KEY": "status-test",
                   "API_URL": "https://example.invalid", "DB_PATH": "/tmp/status-smoke.db", "JWT_SECRET_KEY": "status-test-secret",
                   "SHOP_NAME": "Status Test", "STATUS_MONITOR_TOKEN": "s" * 32}
    args = ["docker", "run", "--rm", "--network=none", "--cpus=1", "--memory=512m", "--entrypoint=python"]
    for key, value in environment.items():
        args += ["--env", key + "=" + value]
    run(args + [image, "-c", code])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("build", "push"))
    parser.add_argument("--revision", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--runtime-base", help="Reuse an existing release only when all runtime inputs are unchanged")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision) or not re.fullmatch(r"1\.0\.[1-9][0-9]*", args.tag):
        parser.error("An exact Git revision and release tag are required")
    if args.runtime_base and not re.fullmatch(r"1\.0\.[1-9][0-9]*", args.runtime_base):
        parser.error("Runtime base must be an existing release tag")
    if os.name != "posix":
        parser.error("Run this recovery publisher on the existing Linux server")
    expected = verified_tree(args.revision)
    local = f"smart-shop-verified:sha-{args.revision}"
    with tempfile.TemporaryDirectory(prefix="status-shop-release-", dir="/var/tmp") as temporary:
        directory = Path(temporary)
        os.chmod(directory, 0o700)
        environment = registry_environment(directory) if args.runtime_base or args.phase == "push" else dict(os.environ)
        if args.phase == "build":
            if not args.archive:
                parser.error("Build requires a Git source archive")
            source_directory = directory / "source"
            source_directory.mkdir(mode=0o700)
            with tarfile.open(args.archive, "r:") as archive:
                validate_archive(archive, args.revision, expected)
                archive.extractall(source_directory)
            dockerfile = "docker/Dockerfile.backend"
            if args.runtime_base:
                base = IMAGE + ":" + args.runtime_base
                run(["docker", "pull", base], env=environment)
                metadata = json.loads(run(["docker", "image", "inspect", base], capture_output=True, text=True, env=environment).stdout)[0]
                labels = metadata["Config"].get("Labels") or {}
                baseline = labels.get("org.opencontainers.image.revision", "")
                if labels.get("org.opencontainers.image.source") != SOURCE or not re.fullmatch(r"[0-9a-f]{40}", baseline):
                    raise ValueError("Runtime base provenance is missing")
                compatible_runtime(expected, verified_tree(baseline))
                digest = next(value for value in metadata["RepoDigests"] if value.startswith(IMAGE + "@sha256:"))
                dockerfile = "Dockerfile.recovery"
                (source_directory / dockerfile).write_text(f"""FROM {digest}
USER root
RUN rm -rf /app/backend
WORKDIR /app/backend
COPY --chown=10001:10001 backend/ /app/backend/
COPY --chown=0:0 docker/backend-entrypoint.sh /usr/local/bin/backend-entrypoint
RUN mkdir -p /app/backend/items /app/public /app/backend/logs /app/backend/data /app/backend/exports && chown -R 10001:10001 /app/backend /app/public && chmod 0755 /usr/local/bin/backend-entrypoint
USER 10001:10001
""")
                print("Reusing verified unchanged runtime:", digest, flush=True)
            environment["DOCKER_BUILDKIT"] = "0"
            run(["docker", "build", "--memory=768m", "--memory-swap=768m", "--cpu-period=100000", "--cpu-quota=100000",
                 "--label", f"org.opencontainers.image.revision={args.revision}",
                 "--label", f"org.opencontainers.image.source={SOURCE}",
                 "--file", dockerfile, "--tag", local, "."], cwd=source_directory, env=environment)
            print("Verified source built; cancel the stalled CI publish before the push phase.")
            return
        inspect = run(["docker", "image", "inspect", local], capture_output=True, text=True)
        labels = json.loads(inspect.stdout)[0]["Config"]["Labels"]
        if labels.get("org.opencontainers.image.revision") != args.revision or labels.get("org.opencontainers.image.source") != SOURCE:
            raise ValueError("Built image provenance does not match")
        check_image(local)
        target = IMAGE + ":" + args.tag
        existing = subprocess.run(["docker", "manifest", "inspect", target], capture_output=True, text=True, env=environment)
        if existing.returncode == 0:
            raise ValueError("Release tag already exists; verify the existing deployment instead of overwriting it")
        if "no such manifest" not in existing.stderr.lower() and "manifest unknown" not in existing.stderr.lower():
            raise ValueError("Could not safely establish that the release tag is absent")
        for tag in (args.tag, "sha-" + args.revision):
            target = IMAGE + ":" + tag
            run(["docker", "tag", local, target], env=environment)
            run(["docker", "push", target], env=environment)
        print("Verified image published. Flux owns deployment and rollout.")


if __name__ == "__main__":
    main()
