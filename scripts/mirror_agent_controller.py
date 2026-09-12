#!/usr/bin/env python3
"""Mirror the pinned official controller from an exactly validated production tree.

Run after this script/config reaches production:
  python scripts/mirror_agent_controller.py --revision FULL_SHA --run-id CI_RUN_ID

Copies the original manifest/layers; never builds an image or changes Kubernetes.
Requires git, gh, ssh, and Python 3.10+. Only crane is downloaded, checksum-pinned
to https://github.com/google/go-containerregistry/releases/tag/v0.22.1.
"""
import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

from production_gate import api, validate_run

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "Gradient-Clipping/server-gitops"
SERVER = "root@1.14.95.189"
REGISTRY = "ccr.ccs.tencentyun.com"
UPSTREAM = "registry.k8s.io/agent-sandbox/agent-sandbox-controller"
INDEX = "sha256:dc700a37ecf4e680a146098a30996f420507e76e83a27dbd293f2b8114b14daa"
DIGEST = "sha256:e38c6b2e5daeb2681f8f5ab172671de19d4d3d7052c88a40a34f298bb84649f8"
TARGET = REGISTRY + "/lazycampus/agent-sandbox-controller:v1.0.2"
CRANE_VERSION = "v0.22.1"
CRANE_ASSETS = {
    "Windows": ("go-containerregistry_Windows_x86_64.tar.gz",
                "0e073ea8192c3b8442ec8aaf44d53c1050a09084669fae3a6ceb0f2026cf8b21", "crane.exe"),
    "Linux": ("go-containerregistry_Linux_x86_64.tar.gz",
              "0ab7a1d6932a213aed964ce97666c3077fe691c8606413674a8b3e0b9ec4cda0", "crane"),
}
FILES = (
    "config/production.json",
    "clusters/easy-platform/infrastructure/agent-sandbox/kustomization.yaml",
    "scripts/production_gate.py",
    "scripts/mirror_agent_controller.py",
)


class CommandError(RuntimeError):
    def __init__(self, executable, code, stderr=b""):
        super().__init__(f"{Path(executable).name} failed with exit {code}; output withheld")
        self.stderr = stderr  # Internal classification only; may contain credentials.


def command(args, *, data=None, env=None, timeout=120):
    try:
        result = subprocess.run([str(arg) for arg in args], input=data, capture_output=True,
                                cwd=ROOT, env=env, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{Path(args[0]).name} exceeded its bounded execution time; output withheld") from None
    if result.returncode:
        raise CommandError(str(args[0]), result.returncode, result.stderr)
    return result.stdout


def archive_files(revision):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("A complete GitOps commit SHA is required")
    command(["git", "fetch", "origin", "production"])
    payload = command(["git", "archive", "--format=tar", revision, *FILES])
    contents = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for name in FILES:
            member = archive.getmember(name)
            if not member.isfile() or member.size > 1024 * 1024:
                raise ValueError("Unexpected file in the selected GitOps archive")
            contents[name] = archive.extractfile(member).read()
    # Config comes solely from git archive. The executed entrypoint and helper
    # must also be that reviewed revision (allow native checkout line endings).
    for name in FILES[2:]:
        if (ROOT / name).read_bytes().replace(b"\r\n", b"\n") != contents[name].replace(b"\r\n", b"\n"):
            raise ValueError("Mirror entrypoint or gate helper differs from the selected committed revision")
    return contents


def read_config(contents):
    config = json.loads(contents[FILES[0]])
    expected = {
        "repository": REPOSITORY, "sourceBranch": "main", "productionBranch": "production",
        "validationWorkflow": ".github/workflows/validate.yml", "validationJob": "validate",
    }
    if any(config.get(key) != value for key, value in expected.items()):
        raise ValueError("Unexpected production gate configuration")
    # Fail closed on anything beyond the current simple, literal images block.
    # Fixed destination constants prevent this parser from selecting another repo.
    text = contents[FILES[1]].decode("utf-8")
    sections = re.findall(r"(?m)^images:\s*\n((?:[ \t]+[^\n]*\n)+)", text)
    if len(sections) != 1:
        raise ValueError("Expected one literal controller image mapping")
    values = {}
    for line in sections[0].splitlines():
        match = re.fullmatch(r"\s*(?:- )?(name|newName|newTag|digest): ([^\s]+)\s*", line)
        if not match or match[1] in values:
            raise ValueError("Unsupported controller image mapping")
        values[match[1]] = match[2]
    if values != {"name": UPSTREAM, "newName": TARGET.rsplit(":", 1)[0], "newTag": "v1.0.2", "digest": DIGEST}:
        raise ValueError("Selected production tree does not pin the expected controller mirror")
    return config


def require_production(config, revision, run_id):
    prefix = "repos/" + config["repository"]
    run = api(f"{prefix}/actions/runs/{run_id}")
    jobs = api(f"{prefix}/actions/runs/{run_id}/jobs?filter=latest&per_page=100")["jobs"]
    validate_run(config, revision, run, jobs)
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise ValueError("The selected validation run has not completed successfully")
    current = api(f"{prefix}/git/ref/heads/{config['productionBranch']}")["object"]["sha"]
    if current != revision:
        raise ValueError("Mirror requires the exact current production revision")


def restrict_directory(directory):
    if os.name == "nt":
        identity = command(["whoami", "/user", "/fo", "csv", "/nh"]).decode("utf-8", errors="replace").strip()
        rows = list(csv.reader(io.StringIO(identity)))
        if len(rows) != 1 or len(rows[0]) != 2 or not re.fullmatch(r"S-1-(?:\d+-)*\d+", rows[0][1]):
            raise RuntimeError("Cannot determine the current Windows user SID")
        command(["icacls", directory, "/inheritance:r", "/grant:r", "*" + rows[0][1] + ":(OI)(CI)F"])
    else:
        directory.chmod(0o700)


def download_archive(url, target):
    deadline = time.monotonic() + 240
    size = 0
    with urllib.request.urlopen(url, timeout=30) as response, target.open("wb") as output:
        if not response.geturl().startswith("https://"):
            raise ValueError("Crane release download must remain on HTTPS")
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > 64 * 1024 * 1024 or time.monotonic() > deadline:
                raise RuntimeError("Crane release download exceeded its budget")
            output.write(chunk)


def extract_crane(archive_path, directory, expected_digest, executable):
    digest = hashlib.sha256()
    with archive_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected_digest:
        raise ValueError("Crane release archive checksum mismatch")
    with tarfile.open(archive_path, "r:gz") as archive:
        members = [member for member in archive.getmembers() if member.name == executable]
        if len(members) != 1 or not members[0].isfile() or not 0 < members[0].size <= 100 * 1024 * 1024:
            raise ValueError("Unexpected crane executable archive layout")
        target = directory / executable
        # Never extract paths, links or any other archive members.
        with archive.extractfile(members[0]) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
    target.chmod(0o700)
    return target


def prepare_crane(directory, local_archive=None):
    system = platform.system()
    if system not in CRANE_ASSETS or platform.machine().lower() not in ("amd64", "x86_64"):
        raise ValueError("Pinned crane packages support Windows/Linux x86_64 operators")
    asset, digest, executable = CRANE_ASSETS[system]
    archive_path = local_archive.resolve(strict=True) if local_archive else directory / asset
    if not local_archive:
        download_archive("https://github.com/google/go-containerregistry/releases/download/" + CRANE_VERSION + "/" + asset, archive_path)
    result = extract_crane(archive_path, directory, digest, executable)
    if command([result, "version"]).decode().strip().lstrip("v") != CRANE_VERSION.lstrip("v"):
        raise ValueError("Downloaded crane reported an unexpected version")
    return result


def registry_environment(directory):
    # SSH stdout is captured into memory only. No credential is placed in argv,
    # inherited shell source, diagnostics, Git, or a permanent docker config.
    script = b"""import base64,json,pathlib
root=pathlib.Path('/etc/platform-secrets')
username=(root/'tcr-username').read_text().strip()
password=(root/'tcr-password').read_text().strip()
if not username or not password: raise SystemExit(1)
auth=base64.b64encode((username+':'+password).encode()).decode()
print(json.dumps({'auths': {'ccr.ccs.tencentyun.com': {'auth': auth}}}))
"""
    ssh = shutil.which("ssh.exe") or shutil.which("ssh") or "ssh"
    data = json.loads(command([ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", SERVER, "python3 -"], data=script))
    try:
        auth = data["auths"][REGISTRY]["auth"]
        decoded = base64.b64decode(auth, validate=True).decode()
        if ":" not in decoded or not all(decoded.split(":", 1)):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("The server did not provide a valid TCR credential") from None
    docker_config = directory / "docker-config"
    docker_config.mkdir(mode=0o700)
    path = docker_config / "config.json"
    path.write_text(json.dumps({"auths": {REGISTRY: {"auth": auth}}}), encoding="utf-8")
    path.chmod(0o600)
    return {**os.environ, "DOCKER_CONFIG": str(docker_config)}


def crane_digest(crane, reference, environment, *, allow_missing=False):
    try:
        value = command([crane, "digest", reference], env=environment, timeout=120).decode().strip()
    except CommandError as error:
        if allow_missing and re.search(rb"MANIFEST_UNKNOWN|manifest unknown|404 Not Found", error.stderr):
            return None
        raise
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError("Registry returned an unexpected digest")
    return value


def verify_upstream(crane, environment):
    if crane_digest(crane, UPSTREAM + ":v1.0.2", environment) != INDEX:
        raise ValueError("Official controller tag no longer matches the pinned index")
    index = json.loads(command([crane, "manifest", UPSTREAM + "@" + INDEX], env=environment))
    matches = [item for item in index.get("manifests", []) if item.get("platform", {}).get("os") == "linux" and item.get("platform", {}).get("architecture") == "amd64"]
    if len(matches) != 1 or matches[0].get("digest") != DIGEST:
        raise ValueError("Official index does not identify the pinned Linux amd64 manifest")
    if crane_digest(crane, UPSTREAM + "@" + DIGEST, environment) != DIGEST:
        raise ValueError("Official Linux amd64 manifest digest mismatch")


def mirror(crane, environment, before_copy):
    verify_upstream(crane, environment)
    existing = crane_digest(crane, TARGET, environment, allow_missing=True)
    if existing == DIGEST:
        return "already verified"
    if existing is not None:
        raise ValueError("Target tag has a different digest; refusing to overwrite it")
    before_copy()  # Recheck the current production gate immediately before writing.
    print("Copying the pinned controller manifest and layers to TCR...", flush=True)
    command([crane, "copy", "--no-clobber", "--jobs", "2", UPSTREAM + "@" + DIGEST, TARGET], env=environment, timeout=1200)
    if crane_digest(crane, TARGET, environment) != DIGEST:
        raise ValueError("Target digest verification failed after copy")
    return "copied and verified"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--crane-archive", type=Path, help="Optional already-downloaded pinned release archive")
    args = parser.parse_args()
    if args.run_id <= 0:
        raise ValueError("A positive successful validation run ID is required")
    config = read_config(archive_files(args.revision))
    gate = lambda: require_production(config, args.revision, args.run_id)
    gate()
    print("Validated current production revision; preparing checksum-pinned crane...", flush=True)
    with tempfile.TemporaryDirectory(prefix="agent-controller-mirror-") as temporary:
        directory = Path(temporary)
        restrict_directory(directory)
        crane = prepare_crane(directory, args.crane_archive)
        environment = registry_environment(directory)
        result = mirror(crane, environment, gate)
        print(f"Controller {result}: {TARGET}@{DIGEST}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, tarfile.TarError):
        # Library/network errors can carry URLs, subprocess stderr or credentials.
        # Deliberately expose only this fixed message; never dump exception objects.
        print("Controller mirror failed; no Kubernetes objects were changed. Verify CI/revision, pinned inputs and registry reachability.", file=sys.stderr)
        sys.exit(1)
