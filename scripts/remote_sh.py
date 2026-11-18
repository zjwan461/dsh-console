"""Run an arbitrary command inside a remote container (no SSH needed).

    python scripts/remote_sh.py mysql mysql -uroot -proot -e "show tables"
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _remote as cfg  # noqa: E402


def call(path: str, method: str = "GET", body: dict | None = None, timeout: int = 300):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{cfg.DOCKER}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def strip_frames(raw: bytes) -> str:
    out = bytearray()
    index = 0
    while index + 8 <= len(raw):
        size = int.from_bytes(raw[index + 4 : index + 8], "big")
        chunk = raw[index + 8 : index + 8 + size]
        if len(chunk) < size:
            break
        out.extend(chunk)
        index += 8 + size
    return (out or raw).decode("utf-8", errors="replace")


def run(container: str, cmd: list[str]) -> tuple[int, str]:
    status, body = call(
        f"/containers/{container}/exec",
        "POST",
        {"AttachStdout": True, "AttachStderr": True, "Cmd": cmd, "Tty": False},
    )
    if status not in (200, 201):
        return 1, f"exec create failed {status} {body!r}"
    exec_id = json.loads(body)["Id"]
    _, raw = call(f"/exec/{exec_id}/start", "POST", {"Detach": False, "Tty": False})
    _, info = call(f"/exec/{exec_id}/json")
    try:
        code = json.loads(info).get("ExitCode", 0) or 0
    except ValueError:
        code = 0
    return code, strip_frames(raw)


if __name__ == "__main__":
    code, output = run(sys.argv[1], sys.argv[2:])
    print(output, end="")
    raise SystemExit(code)