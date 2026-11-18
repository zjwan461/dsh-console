"""Rebuild and redeploy the console images on the remote daemon.

Reuses scripts/_remote.py for everything host-specific.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _remote as cfg  # noqa: E402

SKIP = {".venv", "__pycache__", "tests", ".pytest_cache", "test_data", ".ruff_cache"}


def call(path, method="GET", raw=None, body=None, timeout=1800, ctype="application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(
        f"{cfg.DOCKER}{path}",
        data=data,
        method=method,
        headers={"Content-Type": ctype},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            chunks = []
            try:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
            except (ConnectionResetError, TimeoutError, OSError):
                pass
            buf = b"".join(chunks)
            if not buf:
                return resp.status, None
            if ctype == "application/json" and not path.startswith("/build"):
                try:
                    return resp.status, json.loads(buf)
                except json.JSONDecodeError:
                    return resp.status, buf.decode("utf-8", errors="replace")
            return resp.status, buf.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def tar_api() -> bytes:
    buf = io.BytesIO()
    root = os.path.abspath("api")
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for current, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP and not d.endswith(".egg-info")]
            for name in files:
                if name.endswith((".pyc", ".db")):
                    continue
                full = os.path.join(current, name)
                rel = os.path.relpath(full, root).replace("\\", "/")
                tar.add(full, arcname=rel)
    return buf.getvalue()


def build(image: str, context: bytes) -> bool:
    tag = urllib.parse.quote(image)
    print(f"building {image} ...")
    ctype = "application/x-tar"
    status, body = call(f"/build?t={tag}&rm=1", "POST", raw=context, ctype=ctype)
    text = str(body)
    ok = status == 200 and '"error"' not in text
    if not ok:
        print("  failed:", text[:1200])
    else:
        print("  ok")
    return ok


def ensure(name: str, spec: dict) -> None:
    call(f"/containers/{name}?force=1", "DELETE", timeout=180)
    status, body = call(f"/containers/create?name={name}", "POST", body=spec)
    if status not in (200, 201):
        raise SystemExit(f"{name}: create failed {status} {body}")
    call(f"/containers/{name}/start", "POST")


def main() -> None:
    dist = os.path.abspath("web/dist")
    web_buf = io.BytesIO()
    with tarfile.open(fileobj=web_buf, mode="w") as tar:
        for name in os.listdir(dist):
            tar.add(os.path.join(dist, name), arcname=name)
        dockerfile = (
            b"FROM caddy:2-alpine\n"
            b"COPY . /srv/console-web\n"
            b"COPY Caddyfile.bootstrap /etc/caddy/Caddyfile\n"
        )
        bootstrap = (
            b'{\n\tadmin 0.0.0.0:2019\n\tauto_https off\n}\n\n'
            b':80 {\n\trespond "console bootstrapping" 200\n}\n'
        )
        for fname, content in (
            ("Dockerfile", dockerfile),
            ("Caddyfile.bootstrap", bootstrap),
        ):
            info = tarfile.TarInfo(fname)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))

    if not build(cfg.API_IMAGE, tar_api()):
        raise SystemExit("api build failed")
    if not build(cfg.WEB_IMAGE, web_buf.getvalue()):
        raise SystemExit("web build failed")

    # Recreate the API (new code) and the web proxy (new dist); keep the worker.
    secrets_path = Path(__file__).with_name(".remote-secrets.json")
    secrets_data = json.loads(secrets_path.read_text(encoding="utf-8"))
    env = [
        "ENV=prod",
        "LOG_LEVEL=INFO",
        f"DATABASE_URL={cfg.MYSQL_DSN}",
        f"REDIS_URL={cfg.REDIS_URL}",
        "REDIS_FAKE=0",
        f"SESSION_SECRET={secrets_data['session_secret']}",
        f"MASTER_KEY={secrets_data['master_key']}",
        "COOKIE_SECURE=false",
        f"CONSOLE_BASE_DOMAIN={cfg.BASE_DOMAIN}",
        "CONSOLE_NAME=优墨云舱",
        f"BOOTSTRAP_ADMIN_USERNAME={cfg.ADMIN[0]}",
        f"BOOTSTRAP_ADMIN_PASSWORD={secrets_data['admin_password']}",
        "DATA_ROOT=/srv/dsh-console/data",
        f"HOST_DATA_ROOT={cfg.DATA_ROOT}",
        "DOCKER_HOST=unix:///var/run/docker.sock",
        "INSTANCE_NETWORK=" + cfg.NET_EDGE,
        "CADDY_ENABLED=1",
        "CADDY_ADMIN_URL=http://console-web:2019",
        "CADDY_ACCESS_LOG_ENABLED=1",
        "PUBLIC_SCHEME=http",
        f"PUBLIC_PORT={cfg.HTTP_PORT}",
        "JOBS_INLINE=0",
        f"DSH_IMAGE_REPOSITORY={os.environ.get('DSH_IMAGE_REPOSITORY', 'ghcr.io/zjwan461/dsh-docker')}",
        "REGISTRY_CHECK_ENABLED=1",
        "REGISTRY_CHECK_INTERVAL_HOURS=6",
        "TZ=Asia/Shanghai",
    ]
    binds = ["/var/run/docker.sock:/var/run/docker.sock", f"{cfg.DATA_ROOT}:/srv/dsh-console/data"]
    ensure(
        "console-api",
        {
            "Image": cfg.API_IMAGE,
            "Env": env,
            "HostConfig": {
                "Binds": binds,
                "RestartPolicy": {"Name": "unless-stopped"},
                "NetworkMode": cfg.NET_CONSOLE,
                "PortBindings": {"8000/tcp": [{"HostPort": str(cfg.API_PORT)}]},
            },
            "NetworkingConfig": {
                "EndpointsConfig": {cfg.NET_CONSOLE: {"Aliases": ["console-api"]}, cfg.NET_EDGE: {}}
            },
            "ExposedPorts": {"8000/tcp": {}},
        },
    )
    ensure(
        "console-web",
        {
            "Image": cfg.WEB_IMAGE,
            "HostConfig": {
                "RestartPolicy": {"Name": "unless-stopped"},
                "NetworkMode": cfg.NET_CONSOLE,
                "PortBindings": {"80/tcp": [{"HostPort": str(cfg.HTTP_PORT)}]},
            },
            "NetworkingConfig": {
                "EndpointsConfig": {
                    cfg.NET_CONSOLE: {"Aliases": ["console-web"]},
                    cfg.NET_EDGE: {},
                }
            },
            "ExposedPorts": {"80/tcp": {}},
        },
    )
    # The worker runs the same code: it owns the skill/plugin reconcile loops, so
    # a new image has to be picked up here too.
    ensure(
        "console-worker",
        {
            "Image": cfg.API_IMAGE,
            "Cmd": ["python", "-m", "app.worker"],
            "Env": env,
            "HostConfig": {
                "Binds": binds,
                "RestartPolicy": {"Name": "unless-stopped"},
                "NetworkMode": cfg.NET_CONSOLE,
            },
            "NetworkingConfig": {
                "EndpointsConfig": {cfg.NET_CONSOLE: {"Aliases": ["console-worker"]}}
            },
        },
    )
    print("redeployed")


if __name__ == "__main__":
    subprocess.run([sys.executable, "-c", "pass"], check=False)
    main()