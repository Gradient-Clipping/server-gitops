#!/usr/bin/env python3
"""Verify identity, campus access and both labels of the shared proxy boundary."""
import argparse
import json
import secrets
import subprocess
import time

HOST = "tailscale-proxy.tailscale-system.svc.cluster.local"


def kube(*args, content=None, timeout=60):
    result = subprocess.run(["k3s", "kubectl", *args], input=content, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"kubectl failed (exit {result.returncode}); output withheld")
    return result.stdout


def apply(resource):
    kube("create", "-f", "-", content=json.dumps(resource))


def verify(expected_device_id=None):
    kube("-n", "tailscale-system", "rollout", "status", "deployment/tailscale-proxy", "--timeout=180s", timeout=190)
    kube("-n", "easy-swu", "rollout", "status", "deployment/easy-swu-api", "--timeout=120s", timeout=130)
    state = json.loads(kube("-n", "tailscale-system", "exec", "deployment/tailscale-proxy", "--", "tailscale", "status", "--json"))
    if state.get("BackendState") != "Running" or not state.get("TailscaleIPs"):
        raise ValueError("The shared Tailscale device is not running")
    if expected_device_id and state.get("Self", {}).get("ID") != expected_device_id:
        raise ValueError("Device identity changed during migration")
    api = json.loads(kube("-n", "easy-swu", "get", "deployment", "easy-swu-api", "-o", "json"))
    containers = api["spec"]["template"]["spec"]["containers"]
    if [c["name"] for c in containers] != ["api"]:
        raise ValueError("The API still has a sidecar")
    environment = {v["name"]: v.get("value") for v in containers[0].get("env", [])}
    if environment.get("TAILSCALE_PROXY_URL") != f"http://{HOST}:1055" or environment.get("TAILSCALE_HEALTH_URL") != f"http://{HOST}:9002/healthz":
        raise ValueError("The API does not use the shared service")
    script = """
const assert = require('node:assert/strict');
const axios = require('axios').default;
const {HttpProxyAgent} = require('http-proxy-agent');
const {CampusNetworkGateway} = require('./src/vpn/campus-network');
(async () => {
  const gw = new CampusNetworkGateway({proxyUrl: process.env.TAILSCALE_PROXY_URL, healthUrl: process.env.TAILSCALE_HEALTH_URL});
  try {
    assert.equal((await gw.performHealthCheck({recordState:false})).up, true);
    assert.equal((await gw.probePrimaryResource({recordState:false})).up, true);
    const electricity = await axios.get('http://211.83.23.198/qenergy/', {proxy:false,
      httpAgent:new HttpProxyAgent(process.env.TAILSCALE_PROXY_URL),timeout:15000,maxRedirects:0,validateStatus:()=>true});
    assert.ok(electricity.status >= 200 && electricity.status < 400);
    assert.equal((await fetch('http://127.0.0.1:3000/api/v1/system/ready')).status, 200);
    console.log('API readiness, shared health, campus HTTPS and electricity HTTP passed.');
  } finally { await gw.close(); }
})().catch(() => { console.error('API campus verification failed; response bodies withheld'); process.exit(1); });
"""
    print(kube("-n", "easy-swu", "exec", "deployment/easy-swu-api", "-c", "api", "--", "node", "-e", script).strip(), flush=True)
    access_matrix(containers[0]["image"])
    print("Device identity preserved; shared proxy and API are ready.", flush=True)


def access_matrix(image):
    suffix = secrets.token_hex(4)
    namespaces = ["tailscale-check-allow-" + suffix, "tailscale-check-deny-" + suffix]
    created = []
    jobs = []
    label = "platform.lazycampus.com/campus-network-client"
    registry = json.loads(kube("-n", "tailscale-system", "get", "secret", "tcr-auth", "-o", "json"))
    try:
        for namespace, enabled in zip(namespaces, [True, False]):
            labels = {"pod-security.kubernetes.io/enforce": "restricted",
                      "platform.lazycampus.com/tailscale-verification": "true"}
            if enabled:
                labels[label] = "true"
            apply({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace, "labels": labels}})
            created.append(namespace)
            apply({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "tcr-auth", "namespace": namespace},
                   "type": registry["type"], "data": registry["data"]})
            for pod_enabled in [True, False]:
                name = "labelled" if pod_enabled else "unlabelled"
                allowed = enabled and pod_enabled
                # Raw TCP probes distinguish a denied connection from HTTP/target failures.
                script = """
const net=require('node:net');
const host=HOST;
const expected=EXPECTED;
function connect(port){return new Promise(resolve=>{
  const s=net.connect({host,port}); let done=false;
  const finish=ok=>{if(done)return;done=true;s.destroy();resolve(ok)};
  s.setTimeout(4000);s.on('connect',()=>finish(true));s.on('timeout',()=>finish(false));s.on('error',()=>finish(false));
})}
(async()=>{for(const port of [1055,9002]){
  const actual=await connect(port);
  if(actual!==expected)throw Error('Access boundary mismatch on '+port);
}console.log('Access boundary passed')})().catch(e=>{console.error(e.message);process.exit(1)});
""".replace("HOST", json.dumps(HOST)).replace("EXPECTED", json.dumps(allowed))
                apply({"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": name, "namespace": namespace},
                       "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 60, "ttlSecondsAfterFinished": 300,
                                "template": {"metadata": {"labels": {label: "true"} if pod_enabled else {}},
                                             "spec": {"restartPolicy": "Never", "automountServiceAccountToken": False,
                                                      "imagePullSecrets": [{"name": "tcr-auth"}],
                                                      "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}},
                                                      "containers": [{"name": "verify", "image": image,
                                                                      "command": ["node", "-e", script],
                                                                      "resources": {"requests": {"cpu": "10m", "memory": "32Mi"}, "limits": {"cpu": "100m", "memory": "96Mi"}},
                                                                      "securityContext": {"runAsNonRoot": True, "runAsUser": 10001,
                                                                                          "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                                                                          "capabilities": {"drop": ["ALL"]}}}]}}}})
                jobs.append((namespace, name))
        deadline = time.monotonic() + 90
        pending = set(jobs)
        while pending and time.monotonic() < deadline:
            for namespace, name in list(pending):
                job = json.loads(kube("-n", namespace, "get", "job", name, "-o", "json"))
                if job.get("status", {}).get("failed"):
                    raise ValueError("Cross-namespace proxy verification failed")
                if job.get("status", {}).get("succeeded"):
                    pending.remove((namespace, name))
            if pending:
                time.sleep(2)
        if pending:
            raise TimeoutError("Cross-namespace verification timed out")
        print("All four namespace/Pod label combinations passed on ports 1055 and 9002.", flush=True)
    finally:
        for namespace in created:
            kube("delete", "namespace", namespace, "--wait=false")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-device-id")
    args = parser.parse_args()
    verify(args.expected_device_id)
