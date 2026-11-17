"""Provision the dsh-console runtime on the remote Docker host.

Creates the network, a dedicated Redis, and a data directory (via a throwaway
container bind-mounting the host path). Idempotent: existing objects are reused.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _remote as cfg  # noqa: E402

BASE = cfg.DOCKER
NETWORK = cfg.NET_CONSOLE
NET_EDGE = cfg.NET_EDGE
REDIS_NAME = "dsh-console-redis"
DATA_ROOT = cfg.DATA_ROOT


def call(path: str, method: str = "GET", body: dict | None = None, timeout: int = 60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return resp.status, None
            # Docker streams (logs/attach) are a framed binary stream, not JSON.
            if resp.headers.get("Content-Type", "").startswith("application/json"):
                return resp.status, json.loads(raw)
            return resp.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        return exc.code, raw


def ensure_network() -> None:
    status, body = call(f"/networks/{NETWORK}")
    if status == 200:
        print(f"network {NETWORK}: exists")
        return
    status, body = call(
        "/networks/create",
        "POST",
        {"Name": NETWORK, "Driver": "bridge", "CheckDuplicate": True},
    )
    print(f"network {NETWORK}: create -> {status} {body}")


def ensure_redis() -> None:
    status, body = call(f"/containers/{REDIS_NAME}/json")
    if status == 200:
        state = body.get("State", {}).get("Status")
        print(f"redis {REDIS_NAME}: exists ({state})")
        if state != "running":
            print("  starting:", call(f"/containers/{REDIS_NAME}/start", "POST"))
        return
    status, body = call(
        f"/containers/create?name={REDIS_NAME}",
        "POST",
        {
            "Image": "redis:7-alpine",
            "Cmd": ["redis-server", "--appendonly", "yes"],
            "Env": ["TZ=Asia/Shanghai"],
            "HostConfig": {
                "Binds": [f"{DATA_ROOT}/redis:/data"],
                "RestartPolicy": {"Name": "unless-stopped"},
                "NetworkMode": NETWORK,
            },
            "NetworkingConfig": {"EndpointsConfig": {NETWORK: {"Aliases": ["redis"]}}},
            "Labels": {"dsh.console.managed": "true"},
        },
    )
    print(f"redis create -> {status} {body}")
    if status in (200, 201):
        print("  start:", call(f"/containers/{REDIS_NAME}/start", "POST"))


def ensure_data_dir() -> None:
    """Docker creates bind sources as root; pre-create so ownership is explicit."""
    status, body = call(
        "/containers/create?name=dsh-console-bootstrap",
        "POST",
        {
            "Image": "redis:7-alpine",
            "Cmd": [
                "sh",
                "-c",
                "mkdir -p /data/instances /data/snapshots /data/redis /data/images "
                "&& chmod 755 /data && ls -la /data",
            ],
            "HostConfig": {"Binds": [f"{DATA_ROOT}:/data"], "NetworkMode": "none"},
        },
    )
    if status not in (200, 201):
        print(f"bootstrap create failed: {status} {body}")
        return
    cid = body["Id"]
    print("bootstrap start:", call(f"/containers/{cid}/start", "POST"))
    print("bootstrap wait:", call(f"/containers/{cid}/wait", "POST", timeout=60))
    status, log = call(f"/containers/{cid}/logs?stdout=1&stderr=1", timeout=30)
    print("--- data dir ---")
    print(log if isinstance(log, str) else json.dumps(log))
    call(f"/containers/{cid}?force=1", "DELETE")


if __name__ == "__main__":
    ensure_network()
    ensure_data_dir()
    ensure_redis()
    print("done")
    sys.exit(0)