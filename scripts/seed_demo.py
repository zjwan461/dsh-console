"""Seed a local demo dataset for UI work (SQLite + FakeDriver)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"


class Client:
    def __init__(self):
        self.cookie = ""
        self.csrf = ""

    def call(self, method, path, body=None, expect=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if self.csrf and method != "GET":
            headers["X-CSRF-Token"] = self.csrf
        req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                for value in resp.headers.get_all("Set-Cookie") or []:
                    self.cookie = value.split(";", 1)[0]
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            payload = None
            try:
                payload = json.loads(raw)
            except Exception:
                payload = raw.decode(errors="replace")
            return exc.code, payload

    def login(self, username, password):
        self.cookie = ""
        self.csrf = ""
        status, payload = self.call(
            "POST", "/auth/login", {"username": username, "password": password, "remember": True}
        )
        if status != 200:
            raise SystemExit(f"login {username} failed: {status} {payload}")
        self.csrf = payload["data"]["csrf_token"]
        return payload["data"]["user"]


def main():
    admin = Client()
    admin.login("admin", "admin12345")

    images = [
        ("ghcr.io/zjwan461/dsh-docker:0.2.1-rc.5", "0.2.1-rc.5", "首个正式候选版本：Office/Chrome/Python 三层全开", True),
        ("ghcr.io/zjwan461/dsh-docker:0.2.0-rc.1", "0.2.0-rc.1", "引入插件热加载", False),
        ("ghcr.io/zjwan461/dsh-docker:0.1.9", "0.1.9", "旧版，保留用于回滚", False),
    ]
    for ref, version, note, is_default in images:
        status, payload = admin.call(
            "POST",
            "/admin/images",
            {"image_ref": ref, "version": version, "changelog": note, "is_default": is_default},
        )
        print("image", version, status)

    users = [
        ("linzhiyuan", "林知远", "linzhiyuan@example.com"),
        ("chenmoxiang", "陈墨香", "chenmoxiang@example.com"),
        ("wangshu", "王澍", "wangshu@example.com"),
        ("zhaoyun", "赵云舒", "zhaoyun@example.com"),
        ("sunqi", "孙祺", "sunqi@example.com"),
    ]
    for username, display, email in users:
        status, payload = admin.call(
            "POST",
            "/admin/users",
            {
                "username": username,
                "password": "demo12345",
                "display_name": display,
                "email": email,
                "role": "user",
            },
        )
        print("user", username, status, "" if status in (200, 201) else str(payload)[:120])

    # One instance per demo user, plus usage data by starting them.
    for username, *_ in users[:3]:
        user = Client()
        user.login(username, "demo12345")
        status, payload = user.call("POST", "/instances/me", {})
        print("instance", username, status, "" if status in (200, 201) else str(payload)[:160])
        if status in (200, 201):
            user.call("POST", "/instances/me/start", {})
            user.call("POST", "/instances/me/heartbeat", {})

    # A ticket and an announcement so those pages are not empty.
    user = Client()
    user.login("linzhiyuan", "demo12345")
    status, payload = user.call(
        "POST",
        "/me/tickets",
        {"subject": "子域名打开后一直转圈", "body": "从控制台点「打开」后页面停在加载态，刷新也一样。", "category": "bug", "priority": "high"},
    )
    print("ticket", status, "" if status in (200, 201) else str(payload)[:160])

    admin.call(
        "POST",
        "/admin/announcements",
        {
            "title": "0.2.1-rc.5 已上线，建议升级",
            "body_md": "新的 Office/Chrome/Python 层已就绪，可在「版本」页一键导入并设为默认。",
            "level": "info",
            "status": "published",
            "pinned": True,
        },
    )
    print("seeded")


if __name__ == "__main__":
    main()