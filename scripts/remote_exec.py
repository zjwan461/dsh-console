"""Run a Python snippet inside a remote container and print its output."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _remote as cfg  # noqa: E402

BASE = cfg.DOCKER


def call(path, method="GET", body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            buf = resp.read()
            return resp.status, buf
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def strip_frames(raw: bytes) -> str:
    """Docker's non-TTY output is 8-byte-header framed."""
    out = bytearray()
    index = 0
    while index + 8 <= len(raw):
        size = int.from_bytes(raw[index + 4 : index + 8], "big")
        chunk = raw[index + 8 : index + 8 + size]
        if len(chunk) < size:
            break
        out.extend(chunk)
        index += 8 + size
    if not out:
        return raw.decode("utf-8", errors="replace")
    return out.decode("utf-8", errors="replace")


def exec_in(container: str, cmd: list[str]) -> str:
    status, body = call(
        f"/containers/{container}/exec",
        "POST",
        {"AttachStdout": True, "AttachStderr": True, "Cmd": cmd},
    )
    if status not in (200, 201):
        return f"exec create failed {status} {body!r}"
    eid = json.loads(body)["Id"]
    status, raw = call(f"/exec/{eid}/start", "POST", {"Detach": False, "Tty": False})
    return strip_frames(raw)


if __name__ == "__main__":
    container = sys.argv[1] if len(sys.argv) > 1 else "console-api"
    source_file = sys.argv[2] if len(sys.argv) > 2 else ""
    if source_file:
        with open(source_file, encoding="utf-8") as fh:
            code = fh.read()
        import base64

        encoded = base64.b64encode(code.encode()).decode()
        snippet = f"import base64;exec(base64.b64decode('{encoded}').decode())"
    else:
        snippet = sys.argv[3] if len(sys.argv) > 3 else "print('no source')"
    print(exec_in(container, ["python", "-c", snippet]))