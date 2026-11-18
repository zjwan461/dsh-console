"""Stop the console deployment: the DSH instance first, then api/web/worker.

Only touches the containers this project owns; the shared /mysql and /redis
containers are left running.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _remote as cfg  # noqa: E402
from e2e_skills_plugins import Session  # noqa: E402

OURS = ["console-api", "console-web", "console-worker"]


def docker(path: str, method: str = "GET", timeout: int = 120):
    request = urllib.request.Request(f"{cfg.DOCKER}{path}", method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


# 1. The instance through the API, so the console records a clean stop.
try:
    user = Session()
    user.login("e2euser", "e2euser12345")
    status, payload = user.call("GET", "/instances/me")
    instance = (payload or {}).get("data")
    if instance and instance["status"] in ("running", "starting", "waking"):
        print("stopping instance:", user.call("POST", "/instances/me/stop", {})[0])
        for _ in range(30):
            time.sleep(3)
            _, payload = user.call("GET", "/instances/me")
            state = (payload or {}).get("data", {}).get("status")
            if state in ("stopped", "sleeping"):
                print("instance:", state)
                break
        else:
            print("instance still:", state)
    else:
        print("instance already", instance["status"] if instance else "absent")
except Exception as exc:  # noqa: BLE001
    print("could not use the API:", type(exc).__name__, str(exc)[:120])

# 2. The console itself.
for name in OURS:
    code = docker(f"/containers/{name}/stop?t=20", "POST")
    print(f"stop {name}: {code}")

time.sleep(2)
with urllib.request.urlopen(f"{cfg.DOCKER}/containers/json?all=1", timeout=30) as response:
    listing = json.load(response)

print("\n--- console containers ---")
for item in listing:
    name = item["Names"][0]
    if name.lstrip("/") in OURS or name.startswith("/dsh-u-"):
        print(f"{name:<18} {item['State']:<10} {item.get('Status', '')}")