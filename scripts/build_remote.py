"""Build the console images on the remote Docker daemon and (re)start the stack.

The build context is streamed as a tar, so no local Docker is required.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _remote as cfg  # noqa: E402

BASE = cfg.DOCKER
NETWORK = cfg.NET_CONSOLE
API_IMAGE = cfg.API_IMAGE
WEB_IMAGE = cfg.WEB_IMAGE
PIP_INDEX = os.environ.get(
    "DSH_PIP_INDEX", "https://repo.huaweicloud.com/repository/pypi/simple"
)

SKIP = {".venv", "__pycache__", "tests", ".pytest_cache", "test_data", ".ruff_cache"}


def call(path, method="GET", body=None, timeout=300, raw_body=None, headers=None):
    data = raw_body if raw_body is not None else (
        json.dumps(body).encode() if body is not None else None
    )
    hdrs = {"Content-Type": "application/json"}
    if raw_body is not None:
        hdrs["Content-Type"] = "application/x-tar"
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            buf = resp.read()
            if not buf:
                return resp.status, None
            if resp.headers.get("Content-Type", "").startswith("application/json"):
                return resp.status, json.loads(buf)
            return resp.status, buf.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")


def make_tar(root: str, extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    root = os.path.abspath(root)
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for current, dirs, files in os.walk(root):
            dirs[:] = [
                d
                for d in dirs
                if d not in SKIP and not d.endswith(".egg-info") and d != "node_modules"
            ]
            for name in files:
                full = os.path.join(current, name)
                rel = os.path.relpath(full, root).replace("\\", "/")
                if name.endswith(".pyc") or name.endswith(".db"):
                    continue
                tar.add(full, arcname=rel)
        for name, content in (extra or {}).items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def build(image: str, context_tar: bytes, dockerfile: str = "Dockerfile",
          buildargs: dict | None = None) -> bool:
    query = f"?t={urllib.parse.quote(image)}&dockerfile={dockerfile}&rm=1&forcerm=1"
    if buildargs:
        for key, value in buildargs.items():
            query += f"&buildargs={urllib.parse.quote(json.dumps({key: value}))}"
    print(f"building {image} ...")
    status, body = call(f"/build{query}", "POST", raw_body=context_tar, timeout=1800)
    if status != 200:
        print(f"  build request failed {status}: {str(body)[:800]}")
        return False
    ok = True
    for line in str(body).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("error"):
            print("  ERROR:", entry["error"][:2000])
            ok = False
        elif entry.get("stream"):
            text = entry["stream"].strip()
            if text and not text.startswith("--->"):
                print("  ", text[:200])
    print(f"  {image}: {'OK' if ok else 'FAILED'}")
    return ok


def ensure_container(name: str, spec: dict) -> None:
    status, body = call(f"/containers/{name}/json")
    if status == 200:
        print(f"{name}: recreating")
        call(f"/containers/{name}?force=1", "DELETE")
    status, body = call(f"/containers/create?name={name}", "POST", spec)
    if status not in (200, 201):
        print(f"{name}: create failed {status} {body}")
        return
    print(f"{name}: start {call(f'/containers/{name}/start', 'POST')}")


def wait_http(name: str, url: str, attempts: int = 30) -> None:
    """Probe from inside the network using a throwaway container is overkill;
    instead poll the container's log for readiness."""
    for _ in range(attempts):
        status, logs = call(f"/containers/{name}/logs?stdout=1&stderr=1&tail=40")
        text = str(logs)
        if "api ready" in text or "Application startup complete" in text:
            print(f"{name}: ready")
            return
        if "Traceback" in text or "Error" in text:
            print(f"{name}: error in logs -> {text[-1500:]}")
            return
        time.sleep(2)
    print(f"{name}: readiness not observed, continuing")


if __name__ == "__main__":
    import urllib.parse

    api_ok = build(
        API_IMAGE,
        make_tar("api"),
        dockerfile="Dockerfile",
        buildargs={"PIP_INDEX_URL": PIP_INDEX},
    )
    if not api_ok:
        raise SystemExit("api image build failed")

    dist_dir = os.path.abspath("web/dist")
    if not os.path.isdir(dist_dir):
        raise SystemExit("web/dist missing; run npm run build first")
    web_tar = make_tar(dist_dir)  # context = dist contents
    web_dockerfile = (
        b"FROM caddy:2-alpine\n"
        b"COPY . /srv/console-web\n"
    )
    # Send the Dockerfile alongside the dist files in the same context.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name in os.listdir(dist_dir):
            tar.add(os.path.join(dist_dir, name), arcname=name)
        info = tarfile.TarInfo("Dockerfile")
        info.size = len(web_dockerfile)
        tar.addfile(info, io.BytesIO(web_dockerfile))
    web_ok = build(WEB_IMAGE, buf.getvalue(), dockerfile="Dockerfile")
    if not web_ok:
        raise SystemExit("web image build failed")
    print("images built")