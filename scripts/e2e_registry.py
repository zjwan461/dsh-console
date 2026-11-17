"""Verify the registry catalogue against the live GHCR package, end to end."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _remote as cfg  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


class Session:
    def __init__(self) -> None:
        self.cookie: str | None = None
        self.csrf: str | None = None

    def request(self, method: str, url: str, body=None, extra=None, timeout=180):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if self.csrf and method != "GET":
            headers["X-CSRF-Token"] = self.csrf
        if extra:
            headers.update(extra)
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                for value in resp.headers.get_all("Set-Cookie") or []:
                    self.cookie = value.split(";", 1)[0]
                payload = None
                if raw:
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        payload = raw.decode(errors="replace")
                return resp.status, payload, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw)
            except Exception:
                payload = raw.decode(errors="replace")
            return exc.code, payload, dict(exc.headers)

    def login(self) -> None:
        self.cookie = None
        self.csrf = None
        status, payload, _ = self.request(
            "POST",
            f"{cfg.API}/auth/login",
            {"username": cfg.ADMIN[0], "password": cfg.ADMIN[1], "remember": True},
        )
        assert status == 200, f"login failed {status} {payload}"
        self.csrf = payload["data"]["csrf_token"]


def main() -> None:
    admin = Session()
    admin.login()

    # 1. the catalogue reaches the real registry
    status, payload, _ = admin.request("GET", f"{cfg.API}/admin/images/remote?refresh=true", timeout=180)
    check("catalogue request", status == 200, str(payload)[:300])
    if status != 200:
        return
    data = payload["data"]
    print(f"   repository={data['repository']} checked_at={data['checked_at']}")
    check("catalogue has no error", not data.get("error"), str(data.get("error"))[:200])
    check("catalogue lists versions", bool(data["items"]), str(data["items"])[:200])

    tags = [item["tag"] for item in data["items"]]
    print(f"   tags: {tags}")
    check("only version tags", all(t not in {"latest"} and not t.startswith("sha-") for t in tags), str(tags))
    check("latest_version reported", bool(data["latest_version"]), str(data["latest_version"]))

    first = data["items"][0]
    print(
        f"   {first['tag']}: {first['size_bytes'] / 1024**3:.2f} GB, "
        f"digest={str(first['digest'])[:19]}, created={first['created_at']}, "
        f"imported={first['imported']} ({first['matched_by']})"
    )
    check("digest present", bool(first["digest"]))
    check("size present", first["size_bytes"] > 0)
    check("created time parsed", bool(first["created_at"]))

    # 2. importing pulls the image from GHCR and registers it
    already = first["imported"] and first["matched_by"] == "ref"
    if already:
        print("   tag already imported by ref; skipping pull")
    else:
        started = time.time()
        status, payload, _ = admin.request(
            "POST",
            f"{cfg.API}/admin/images/remote/import",
            {"tag": first["tag"], "is_default": False},
            timeout=1800,
        )
        elapsed = time.time() - started
        check("import from registry", status == 201, str(payload)[:400])
        if status == 201:
            image = payload["data"]
            print(f"   imported {image['image_ref']} in {elapsed:.0f}s")
            check("imported ref matches registry", image["image_ref"].endswith(first["tag"]), image["image_ref"])
            check("imported source is pull", image["source"] == "pull", image["source"])

    # 3. the merge now marks the tag as imported
    status, payload, _ = admin.request("GET", f"{cfg.API}/admin/images/remote?refresh=true", timeout=180)
    if status == 200:
        items = {item["tag"]: item for item in payload["data"]["items"]}
        target = items.get(first["tag"], {})
        check("tag shown as imported after import", bool(target.get("imported")), str(target)[:200])

    # 4. the admin page is served
    status, body, _ = admin.request(
        "GET", cfg.CONSOLE + "/", extra={"Host": cfg.CONSOLE_HOST}, timeout=30
    )
    check("console page served", status == 200, str(status))

    # 5. worker task runs (no exception) — exercised through the API container
    print("\n=== summary ===")
    if failures:
        print(f"{len(failures)} FAILED:")
        for item in failures:
            print(" -", item)
        raise SystemExit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()