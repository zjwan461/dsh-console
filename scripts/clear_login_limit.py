import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _remote as cfg


def call(path, method="GET", body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{cfg.DOCKER}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def frames(raw: bytes) -> str:
    out = bytearray()
    i = 0
    while i + 8 <= len(raw):
        size = int.from_bytes(raw[i + 4 : i + 8], "big")
        out.extend(raw[i + 8 : i + 8 + size])
        i += 8 + size
    return (out or raw).decode("utf-8", errors="replace")


def sh(container, cmd):
    status, body = call(
        f"/containers/{container}/exec", "POST",
        {"AttachStdout": True, "AttachStderr": True, "Cmd": ["sh", "-c", cmd]},
    )
    eid = json.loads(body)["Id"]
    status, raw = call(f"/exec/{eid}/start", "POST", {"Detach": False, "Tty": False})
    return frames(raw)


print(sh("dsh-console-redis", "redis-cli --scan --pattern 'rl:login:*'"))
print(sh("dsh-console-redis", "redis-cli --scan --pattern 'rl:login:*' | xargs -r redis-cli del"))
print(sh("dsh-console-redis", "redis-cli --scan --pattern 'rl:login:*' | wc -l"))