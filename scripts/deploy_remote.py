"""Deploy the dsh-console stack on a remote Docker host (plain HTTP, IP access).

Host-specific values come from the environment (see scripts/_remote.py); nothing
is hardcoded, so this file is safe to commit.

    export DSH_REMOTE_DOCKER=http://10.0.0.10:2375
    export DSH_CONSOLE_URL=http://10.0.0.10:38080
    export DSH_API_URL=http://10.0.0.10:38000/api/v1
    export DSH_ADMIN_PASSWORD=********
    export DSH_MYSQL_DSN='mysql+pymysql://u:p@db:3306/dsh_console?charset=utf8mb4'
    export DSH_HOST_DATA_ROOT=/opt/dsh-console/data

Run after scripts/build_remote.py so both images exist.
"""

from __future__ import annotations

import base64
import io
import json
import os
import secrets
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _remote as cfg  # noqa: E402

BASE = cfg.DOCKER
NET_CONSOLE = cfg.NET_CONSOLE
NET_EDGE = cfg.NET_EDGE
API_IMAGE = cfg.API_IMAGE
WEB_IMAGE = cfg.WEB_IMAGE
DATA_ROOT = cfg.DATA_ROOT
BASE_DOMAIN = cfg.BASE_DOMAIN
HTTP_PORT = cfg.HTTP_PORT
API_PORT = cfg.API_PORT
REDIS_URL = cfg.REDIS_URL
MYSQL_DSN = cfg.MYSQL_DSN
ADMIN_USER, ADMIN_PASSWORD = cfg.ADMIN

SECRETS_FILE = os.path.join(os.path.dirname(__file__), ".remote-secrets.json")

BOOTSTRAP_CADDY = """{
\tadmin 0.0.0.0:2019
\tauto_https off
}

:80 {
\trespond "console bootstrapping" 200
}
"""


def call(path, method="GET", body=None, timeout=300, raw=None, ctype=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    headers = {"Content-Type": ctype or "application/json"}
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers=headers)
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
                # Long build streams can be cut by the TCP port-forward; keep what
                # arrived and let the caller decide from the image list.
                pass
            buf = b"".join(chunks)
            if not buf:
                return resp.status, None
            ctype = resp.headers.get("Content-Type", "")
            if ctype.startswith("application/json") and not path.startswith("/build"):
                try:
                    return resp.status, json.loads(buf)
                except json.JSONDecodeError:
                    return resp.status, buf.decode("utf-8", errors="replace")
            return resp.status, buf.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")


def ensure_network(name: str) -> None:
    status, _ = call(f"/networks/{name}")
    if status == 200:
        print(f"network {name}: exists")
        return
    status, body = call("/networks/create", "POST", {"Name": name, "CheckDuplicate": True})
    print(f"network {name}: {status}")


def build_web() -> bool:
    dist = os.path.abspath("web/dist")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name in os.listdir(dist):
            tar.add(os.path.join(dist, name), arcname=name)
        for fname, content in (
            ("Dockerfile", b"FROM caddy:2-alpine\nCOPY . /srv/console-web\nCOPY Caddyfile.bootstrap /etc/caddy/Caddyfile\n"),
            ("Caddyfile.bootstrap", BOOTSTRAP_CADDY.encode()),
        ):
            info = tarfile.TarInfo(fname)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    tag = urllib.parse.quote(WEB_IMAGE)
    print("building web image ...")
    status, body = call(
        f"/build?t={tag}&rm=1", "POST", raw=buf.getvalue(), ctype="application/x-tar", timeout=900
    )
    text = str(body)
    ok = status == 200 and '"error"' not in text
    if not ok:
        print("  web build failed:", text[:1500])
        return False
    print("  web build: OK")
    return True


def load_secrets() -> dict:
    if os.path.exists(SECRETS_FILE):
        with open(SECRETS_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    data = {
        "session_secret": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("="),
        "master_key": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("="),
        "admin_password": os.environ.get("DSH_ADMIN_PASSWORD", "admin12345"),
    }
    with open(SECRETS_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return data


def api_env(secrets_data: dict) -> list[str]:
    return [
        "ENV=prod",
        "LOG_LEVEL=INFO",
        f"DATABASE_URL={MYSQL_DSN}",
        f"REDIS_URL={REDIS_URL}",
        "REDIS_FAKE=0",
        f"SESSION_SECRET={secrets_data['session_secret']}",
        f"MASTER_KEY={secrets_data['master_key']}",
        "COOKIE_SECURE=false",
        f"CONSOLE_BASE_DOMAIN={BASE_DOMAIN}",
        "CONSOLE_NAME=优墨云舱",
        "BOOTSTRAP_ADMIN_USERNAME=" + ADMIN_USER,
        f"BOOTSTRAP_ADMIN_PASSWORD={secrets_data['admin_password']}",
        "DATA_ROOT=/srv/dsh-console/data",
        f"HOST_DATA_ROOT={DATA_ROOT}",
        "DOCKER_HOST=unix:///var/run/docker.sock",
        "DOCKER_FAKE=0",
        f"INSTANCE_NETWORK={NET_EDGE}",
        "INSTANCE_UID=1000",
        "INSTANCE_GID=1000",
        "CADDY_ENABLED=1",
        "CADDY_ADMIN_URL=http://console-web:2019",
        "CADDY_ACCESS_LOG_ENABLED=1",
        "PUBLIC_SCHEME=http",
        "CADDY_LISTEN_PORT=80",
        "JOBS_INLINE=0",
        "TZ=Asia/Shanghai",
        "MEMORY_RESERVE_MB=4096",
        "CPU_OVERCOMMIT=4.0",
        "NODE_METRICS_ENABLED=1",
    ]


def run_container(name: str, spec: dict) -> str | None:
    call(f"/containers/{name}?force=1", "DELETE", timeout=120)
    status, body = call(f"/containers/create?name={name}", "POST", spec)
    if status not in (200, 201):
        print(f"{name}: create failed {status} {body}")
        return None
    status, _ = call(f"/containers/{name}/start", "POST")
    print(f"{name}: start {status}")
    return body["Id"]


def main() -> None:
    import urllib.parse  # noqa: F401  (used above)

    ensure_network(NET_CONSOLE)
    ensure_network(NET_EDGE)
    secrets_data = load_secrets()

    if not build_web():
        raise SystemExit("web build failed")

    common_env = api_env(secrets_data)
    binds = [
        "/var/run/docker.sock:/var/run/docker.sock",
        f"{DATA_ROOT}:/srv/dsh-console/data",
    ]
    nets = {
        NET_CONSOLE: {"Aliases": ["console-api"]},
        NET_EDGE: {},
    }
    api_id = run_container(
        "console-api",
        {
            "Image": API_IMAGE,
            "Env": common_env,
            "HostConfig": {
                "Binds": binds,
                "RestartPolicy": {"Name": "unless-stopped"},
                "NetworkMode": NET_CONSOLE,
                "PortBindings": {"8000/tcp": [{"HostPort": str(API_PORT)}]},
            },
            "NetworkingConfig": {"EndpointsConfig": nets},
            "ExposedPorts": {"8000/tcp": {}},
            "Labels": {"dsh.console.managed": "true"},
        },
    )
    run_container(
        "console-worker",
        {
            "Image": API_IMAGE,
            "Cmd": ["python", "-m", "app.worker"],
            "Env": common_env,
            "HostConfig": {
                "Binds": binds,
                "RestartPolicy": {"Name": "unless-stopped"},
                "NetworkMode": NET_CONSOLE,
            },
            "Labels": {"dsh.console.managed": "true"},
        },
    )
    run_container(
        "console-web",
        {
            "Image": WEB_IMAGE,
            "HostConfig": {
                "RestartPolicy": {"Name": "unless-stopped"},
                "NetworkMode": NET_CONSOLE,
                "PortBindings": {"80/tcp": [{"HostPort": str(HTTP_PORT)}]},
            },
            "NetworkingConfig": {
                "EndpointsConfig": {NET_CONSOLE: {"Aliases": ["console-web"]}, NET_EDGE: {}}
            },
            "ExposedPorts": {"80/tcp": {}},
            "Labels": {"dsh.console.managed": "true"},
        },
    )

    print("waiting for API ...")
    for _ in range(40):
        time.sleep(3)
        status, logs = call("/containers/console-api/logs?stdout=1&stderr=1&tail=30")
        text = str(logs)
        if "api ready" in text:
            print("API READY")
            print(text[-800:])
            return
        if "Traceback" in text or "ERROR" in text:
            print("API ERROR:")
            print(text[-3000:])
            return
    status, logs = call("/containers/console-api/logs?stdout=1&stderr=1&tail=60")
    print("API logs tail:")
    print(str(logs)[-3000:])


if __name__ == "__main__":
    main()