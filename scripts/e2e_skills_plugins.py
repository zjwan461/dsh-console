"""Real-machine verification for the skill repository and plugin catalog.

Everything here needs a live daemon: a real DSH image, real bind mounts, real
`entrypoint.sh` behaviour. Run it after `scripts/redeploy_remote.py`:

    python scripts/e2e_skills_plugins.py
"""

from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _remote as cfg  # noqa: E402
from remote_sh import run as sh  # noqa: E402

API = cfg.API
DOCKER = cfg.DOCKER
DSH_IMAGE = "ghcr.io/zjwan461/dsh-docker:0.2.1-rc.5"
BOUNDARY = "----dshconsolee2e"

failures: list[str] = []


def _zip_tree(root: Path, prefix: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in root.rglob("*"):
            if path.is_file():
                archive.write(path, f"{prefix}/{path.relative_to(root).as_posix()}")
    return buffer.getvalue()


def _zip_tree_files(files: dict[str, str], prefix: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(f"{prefix}/{name}", content)
    return buffer.getvalue()


def _multipart(filename: str, data: bytes) -> bytes:
    return (
        f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{filename}"\r\nContent-Type: application/zip\r\n\r\n'
    ).encode() + data + f"\r\n--{BOUNDARY}--\r\n".encode()


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" -- {detail}"))
    if not ok:
        failures.append(name)


def docker(path: str, method: str = "GET", body=None, timeout: int = 300):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{DOCKER}{path}", data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def logs(container: str, tail: int = 300) -> str:
    _, raw = docker(f"/containers/{container}/logs?stdout=1&stderr=1&tail={tail}")
    if not isinstance(raw, bytes):
        return str(raw)
    out = bytearray()
    index = 0
    while index + 8 <= len(raw):
        size = int.from_bytes(raw[index + 4 : index + 8], "big")
        out.extend(raw[index + 8 : index + 8 + size])
        index += 8 + size
    return (out or raw).decode("utf-8", errors="replace")


class Session:
    def __init__(self) -> None:
        self.cookie: str | None = None
        self.csrf: str | None = None

    def call(self, method: str, path: str, body=None, raw=None, ctype="application/json"):
        if raw is None:
            data = json.dumps(body).encode() if body is not None else None
        else:
            data = raw
        headers = {"Content-Type": ctype, "Accept": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if self.csrf and method != "GET":
            headers["X-CSRF-Token"] = self.csrf
        request = urllib.request.Request(f"{API}{path}", data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                for value in response.headers.get_all("Set-Cookie") or []:
                    self.cookie = value.split(";", 1)[0]
                payload = response.read()
                return response.status, (json.loads(payload) if payload else None)
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(body_text)
            except ValueError:
                return exc.code, body_text

    def login(self, user: str, password: str) -> None:
        status, payload = self.call(
            "POST", "/auth/login", {"username": user, "password": password, "remember": True}
        )
        assert status == 200, payload
        self.csrf = payload["data"]["csrf_token"]


def ensure_image(admin: Session) -> int:
    status, payload = admin.call("GET", "/admin/images")
    assert status == 200, payload
    for item in payload["data"]["items"]:
        if item.get("image_ref") == DSH_IMAGE or item.get("version") == "0.2.1-rc.5":
            admin.call("PATCH", f"/admin/images/{item['id']}", {"status": "ready", "is_default": True})
            return item["id"]
    status, payload = admin.call(
        "POST",
        "/admin/images",
        {"image_ref": DSH_IMAGE, "version": "0.2.1-rc.5", "is_default": True},
    )
    assert status in (200, 201), payload
    image_id = payload["data"]["id"]
    admin.call("PATCH", f"/admin/images/{image_id}", {"status": "ready"})
    return image_id


def ensure_user(admin: Session, username: str, password: str) -> int:
    status, payload = admin.call("GET", "/admin/users?q=" + username)
    if status == 200:
        for item in payload["data"]["items"]:
            if item["username"] == username:
                admin.call("PATCH", f"/admin/users/{item['id']}", {"status": "active"})
                # UserPatchIn has no password field; the dedicated endpoint owns it.
                admin.call("POST", f"/admin/users/{item['id']}/reset-password", {"new_password": password})
                return item["id"]
    status, payload = admin.call(
        "POST",
        "/admin/users",
        {
            "username": username,
            "password": password,
            "display_name": "技能验证",
            "role": "user",
            "must_change_password": False,
        },
    )
    assert status in (200, 201), payload
    return payload["data"]["id"]


def main() -> None:
    admin = Session()
    admin.login(*cfg.ADMIN)

    status, health = admin.call("GET", "/system/health")
    data = (health or {}).get("data", {})
    check(
        "health",
        status == 200 and data.get("mysql", {}).get("ok") and data.get("docker", {}).get("ok"),
        str(health)[:300],
    )

    # ── MySQL migration ────────────────────────────────────────────────────
    code, out = sh(
        "mysql",
        [
            "mysql",
            "-uroot",
            "-proot",
            "-N",
            "-e",
            "select version_num from dsh_console.alembic_version;"
            " select count(*) from information_schema.tables where table_schema='dsh_console'"
            " and table_name in ('skills','skill_revisions','skill_grants','user_skill_prefs',"
            "'plugins','plugin_revisions','plugin_grants');",
        ],
    )
    check("migration at head", any("c9f1a2b3d4e5" in line for line in out.splitlines()), out[:200])
    check("7 new tables", any(line.strip() == "7" for line in out.splitlines()), out[-200:])

    code, out = sh(
        "mysql",
        [
            "mysql",
            "-uroot",
            "-proot",
            "-N",
            "-e",
            "select column_name,column_type from information_schema.columns"
            " where table_schema='dsh_console' and table_name='skills' order by ordinal_position;",
        ],
    )
    check("skills.created_by unsigned", "created_by\tbigint unsigned" in out, out[:400])

    # ── skill CRUD against real MySQL ──────────────────────────────────────
    name = "e2e-remote-skill"
    status, payload = admin.call("GET", f"/admin/skills?q={name}")
    existing = [
        item
        for item in (payload or {}).get("data", {}).get("items", [])
        if item["name"] == name
    ]
    if existing:
        # Names stay unique even after a soft delete, so re-use the row.
        skill_id = existing[0]["id"]
        status, payload = admin.call(
            "PUT",
            f"/admin/skills/{skill_id}",
            {
                "name": name,
                "description": "真机验证用技能",
                "when_to_use": "验证技能下发",
                "body": "# E2E Remote Skill\n\n真机验证：应出现在 /data/dsh/skills/e2e-remote-skill。\n",
                "changelog": "e2e rerun",
            },
        )
        check("update existing skill", status == 200, str(payload)[:300])
        skill_id = payload["data"]["id"]
    else:
        status, payload = admin.call(
            "POST",
            "/admin/skills",
            {
                "name": name,
                "description": "真机验证用技能",
                "when_to_use": "验证技能下发",
                "body": "# E2E Remote Skill\n\n真机验证：应出现在 /data/dsh/skills/e2e-remote-skill。\n",
            },
        )
        check("create skill", status == 201, str(payload)[:300])
        if status != 201:
            raise SystemExit("cannot create the probe skill")
        skill_id = payload["data"]["id"]
        check(
            "draft by default",
            payload["data"]["status"] == "draft",
            str(payload["data"]["status"]),
        )

    status, payload = admin.call(
        "PUT", f"/admin/skills/{skill_id}/grants", {"grants": [{"scope": "all", "target_id": 0}]}
    )
    check("grant all", status == 200, str(payload)[:200])
    status, payload = admin.call("POST", f"/admin/skills/{skill_id}/publish")
    check("publish skill", status == 200, str(payload)[:200])

    # ── plugin import ──────────────────────────────────────────────────────
    # The image ships @local/dsh-file-download; importing it again must be refused,
    # otherwise two directories would claim the same package name.
    builtin_dir = Path(__file__).resolve().parents[2] / "dsh-docker" / "plugins" / "dsh-file-download"
    if builtin_dir.is_dir():
        colliding = _zip_tree(builtin_dir, "bundle")
        status, payload = admin.call(
            "POST",
            "/admin/plugins/import",
            raw=_multipart("bundle.zip", colliding),
            ctype=f"multipart/form-data; boundary={BOUNDARY}",
        )
        code = (payload or {}).get("error", {}).get("code") if isinstance(payload, dict) else None
        check(
            "built-in plugin name refused",
            status == 409 and code == "PLUGIN_NAME_COLLIDES",
            f"{status} {str(payload)[:200]}",
        )
    else:
        print("skip collision check: dsh-docker/plugins not found next to this repo")

    # A probe bundle that does not exist in the image: minimal cordis plugin.
    probe = _zip_tree_files(
        {
            "package.json": json.dumps(
                {
                    "name": "@local/e2e-probe",
                    "version": "1.0.0",
                    "private": True,
                    "type": "module",
                    "exports": {".": "./index.js"},
                    "dsh": {"bundle": {"patch": "./cordis.patch.yml"}},
                }
            ),
            "index.js": (
                "export default function apply(ctx) {\n"
                "  const log = ctx.logger ?? console\n"
                "  log.info('dsh-console e2e probe loaded')\n"
                "}\n"
            ),
            "cordis.patch.yml": (
                "- insert:\n    - id: e2e-probe\n      name: '@local/e2e-probe'\n"
            ),
        },
        "bundle",
    )
    plugin_name = "@local/e2e-probe"
    status, payload = admin.call(
        "POST",
        "/admin/plugins/import",
        raw=_multipart("probe.zip", probe),
        ctype=f"multipart/form-data; boundary={BOUNDARY}",
    )
    check("import probe plugin", status in (201, 409), str(payload)[:300])

    status, payload = admin.call("GET", "/admin/plugins")
    plugin = next(
        (item for item in payload["data"]["items"] if item["package_name"] == plugin_name), None
    )
    check("probe plugin listed", plugin is not None, str(payload)[:200])
    if plugin and plugin.get("status") == "enabled":
        # Start from "not assigned" so the enable below is a real change and the
        # restart flag has something to record.
        admin.call("POST", f"/admin/plugins/{plugin['id']}/disable")

    # ── instance ───────────────────────────────────────────────────────────
    # This host only has ~7.6 GB and reserves 4 GB, so a *new* 2 GB instance is
    # refused by admission control. Reuse the existing probe instance instead:
    # starting a stopped one is what the feature needs to exercise anyway.
    ensure_image(admin)
    username, password = "e2euser", "e2euser12345"
    user_id = ensure_user(admin, username, password)

    user = Session()
    user.login(username, password)
    status, payload = user.call("GET", "/instances/me")
    instance = (payload or {}).get("data")
    if instance is None:
        status, payload = user.call("POST", "/instances/me", {})
        check("create instance", status in (200, 202), str(payload)[:300])
    elif instance["status"] != "running" or instance.get("needs_restart"):
        status, payload = user.call("POST", "/instances/me/restart", {})
        check("restart instance", status in (200, 202), str(payload)[:300])
    print("waiting for the instance to run ...")

    for _ in range(60):
        time.sleep(10)
        status, payload = user.call("GET", "/instances/me")
        instance = (payload or {}).get("data")
        if instance and instance["status"] == "running":
            break
        print("  ", instance["status"] if instance else "none", (instance or {}).get("status_reason"))
    check("instance running", bool(instance) and instance["status"] == "running", str(instance)[:300])
    if not instance or instance["status"] != "running":
        raise SystemExit("instance did not reach running; aborting")

    container = instance.get("container_name") or f"dsh-u-{user_id}"
    status, _ = docker(f"/containers/{container}/json")
    check("dsh container exists", status == 200, container)

    code, out = sh(container, ["sh", "-lc", "cat /data/dsh/skills/e2e-remote-skill/SKILL.md 2>&1"])
    check("skill materialised in container", "E2E Remote Skill" in out, out[:300])

    code, out = sh(container, ["sh", "-lc", "ls /data/dsh/skills 2>&1"])
    check("skill dir listing", "e2e-remote-skill" in out, out[:300])

    if plugin:
        # Assigning a plugin marks the instance for a restart; the mount only
        # exists on a container created after that.
        admin.call("POST", f"/admin/plugins/{plugin['id']}/enable")
        admin.call(
            "PUT",
            f"/admin/plugins/{plugin['id']}/grants",
            {"grants": [{"scope": "all", "target_id": 0}]},
        )
        status, payload = user.call("GET", "/instances/me")
        check(
            "plugin change flags a restart",
            (payload or {}).get("data", {}).get("needs_restart") is True,
            str((payload or {}).get("data", {}).get("needs_restart")),
        )
        user.call("POST", "/instances/me/restart", {})
        for _ in range(40):
            time.sleep(10)
            status, payload = user.call("GET", "/instances/me")
            if (payload or {}).get("data", {}).get("status") == "running":
                break
        check(
            "running after plugin assignment",
            (payload or {}).get("data", {}).get("status") == "running",
            str(payload)[:200],
        )
        check(
            "restart flag cleared",
            (payload or {}).get("data", {}).get("needs_restart") is False,
            str((payload or {}).get("data", {}).get("needs_restart")),
        )

        code, out = sh(container, ["sh", "-lc", "ls /opt/dsh-plugins 2>&1"])
        check("plugin mounted beside built-ins", "local__e2e-probe" in out, out[:400])
        check(
            "image built-ins still visible",
            "dsh-file-download" in out and "mobile-layout" in out,
            out[:400],
        )

        text = logs(container, 400)
        check(
            "entrypoint enabled the plugin",
            "bundled plugin @local/e2e-probe: enabled" in text,
            "\n".join(line for line in text.splitlines() if "plugin" in line)[-600:],
        )
        check("no plugin tree failure", "plugin tree failed to load" not in text, text[-600:])

        code, out = sh(container, ["sh", "-lc", "cat /data/dsh/cordis.patch.yml 2>&1"])
        check("patch block seeded", "@local/e2e-probe" in out, out[-400:])

        # Disable → restart → the block must go and the container must boot.
        admin.call("POST", f"/admin/plugins/{plugin['id']}/disable")
        code, out = sh(container, ["sh", "-lc", "cat /data/dsh/cordis.patch.yml 2>&1"])
        check("patch block removed on disable", "@local/e2e-probe" not in out, out[-400:])

        user.call("POST", "/instances/me/restart", {})
        for _ in range(40):
            time.sleep(10)
            status, payload = user.call("GET", "/instances/me")
            if (payload or {}).get("data", {}).get("status") == "running":
                break
        check(
            "running after plugin removal",
            (payload or {}).get("data", {}).get("status") == "running",
            str(payload)[:300],
        )

        text = logs(container, 400)
        check("no plugin tree failure after removal", "plugin tree failed to load" not in text, text[-600:])
        code, out = sh(container, ["sh", "-lc", "ls /opt/dsh-plugins 2>&1"])
        check("plugin no longer mounted", "local__e2e-probe" not in out, out[:400])
        code, out = sh(container, ["sh", "-lc", "cat /data/dsh/cordis.patch.yml 2>&1"])
        check("patch block stays removed", "@local/e2e-probe" not in out, out[-400:])

    # ── user toggle ────────────────────────────────────────────────────────
    status, payload = user.call("GET", "/me/skills")
    items = (payload or {}).get("data", {}).get("items", [])
    names = [item["name"] for item in items]
    check("skill visible to user", "e2e-remote-skill" in names, str(names))
    skill = next(item for item in items if item["name"] == "e2e-remote-skill")
    assert skill["enabled"] is True

    user.call(
        "PUT",
        "/me/skills",
        {"enabled_ids": [s["id"] for s in items if s["id"] != skill["id"]]},
    )
    code, out = sh(container, ["sh", "-lc", "ls /data/dsh/skills 2>&1"])
    check("disabled skill removed from container", "e2e-remote-skill" not in out, out[:300])

    user.call("PUT", "/me/skills", {"enabled_ids": [s["id"] for s in items]})
    code, out = sh(container, ["sh", "-lc", "ls /data/dsh/skills 2>&1"])
    check("re-enabled skill back in container", "e2e-remote-skill" in out, out[:300])

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()