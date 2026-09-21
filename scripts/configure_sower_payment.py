#!/usr/bin/env python3
"""Register the Sower platform using the existing customer-service payment key."""

import base64
import copy
import json
import subprocess


def kubectl(*args, input_bytes=None):
    result = subprocess.run(
        ["k3s", "kubectl", *args], input=input_bytes, capture_output=True, timeout=60
    )
    if result.returncode:
        raise SystemExit("Kubernetes secret update failed (details suppressed)")
    return result.stdout


def secret(namespace, name):
    return json.loads(kubectl("-n", namespace, "get", "secret", name, "-o", "json"))


def main():
    payment = secret("easy-swu", "easy-swu-wechat-pay")
    customer_service = secret("wecom-kf", "educoder-wecom-runtime")
    platforms = json.loads(base64.b64decode(payment["data"]["SERVICE_ORDERS_PLATFORMS"]))
    integration_secret = base64.b64decode(
        customer_service["data"]["SERVICE_PAYMENT_SECRET"]
    ).decode()
    source = platforms.get("educoder")
    if not source or source.get("secret") != integration_secret:
        raise SystemExit("Existing payment integration credentials do not match")

    sower = copy.deepcopy(source)
    sower.update(
        name="朔日平台",
        enabled=True,
        secret=integration_secret,
        order_summary_name="朔日",
        order_summary_unit="个任务",
        order_summary_quantity="items",
        webhook_url="https://kf.lazycampus.com/callbacks/payments",
        skus={
            "service_units": {
                "version": "1",
                "max_units": 10000,
                "units_by_kind": {"unit": 1},
            }
        },
    )
    platforms["sower"] = sower
    payment["data"]["SERVICE_ORDERS_PLATFORMS"] = base64.b64encode(
        json.dumps(platforms, ensure_ascii=False, separators=(",", ":")).encode()
    ).decode()
    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "easy-swu-wechat-pay", "namespace": "easy-swu"},
        "type": payment.get("type", "Opaque"),
        "data": payment["data"],
    }
    kubectl("apply", "-f", "-", input_bytes=json.dumps(body).encode())
    print("Configured payment platform: sower")


if __name__ == "__main__":
    main()
