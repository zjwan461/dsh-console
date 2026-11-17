"""Capture UI screenshots with Playwright driving the installed Chrome.

No browser download: Playwright is pointed at the system Chrome via channel="chrome".

    python scripts/shots.py [--out DIR] [--width W] [--height H]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
ADMIN = ("admin", "admin12345")

PAGES = [
    ("dashboard", "/", "user"),
    ("usage", "/usage", "user"),
    ("snapshots", "/snapshots", "user"),
    ("files", "/files", "user"),
    ("tickets", "/tickets", "user"),
    ("settings", "/settings", "user"),
    ("admin-users", "/admin/users", "admin"),
    ("admin-images", "/admin/images", "admin"),
    ("admin-planes", "/admin/plans", "admin"),
    ("admin-metrics", "/admin/metrics", "admin"),
    ("admin-instances", "/admin/instances", "admin"),
    ("admin-tickets", "/admin/tickets", "admin"),
    ("admin-announcements", "/admin/announcements", "admin"),
    ("admin-audit", "/admin/audit", "admin"),
]

DEMO_USER = ("linzhiyuan", "demo12345")


def login(page, username: str, password: str) -> None:
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.fill('input[placeholder*="用户名"], input[autocomplete="username"]', username)
    page.fill('input[type="password"]', password)
    button = page.locator('button:has-text("进入控制台"), button:has-text("登录")').first
    button.click()
    page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
    page.wait_for_load_state("networkidle")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="shots")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--only", default="")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    only = {name.strip() for name in args.only.split(",") if name.strip()}

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=1,
            locale="zh-CN",
        )
        page = context.new_page()

        # Login page first, while logged out.
        page.goto(f"{BASE}/login", wait_until="networkidle")
        page.screenshot(path=str(out / "login.png"))
        print("shot login")

        # A regular member's dashboard (admin has no instance of its own).
        login(page, *DEMO_USER)
        page.goto(f"{BASE}/", wait_until="networkidle")
        page.wait_for_timeout(600)
        page.screenshot(path=str(out / "dashboard-member.png"))
        print("shot dashboard-member")

        # Admin pages.
        page.context.clear_cookies()
        login(page, *ADMIN)

        for name, path, _role in PAGES:
            if only and name not in only:
                continue
            page.goto(f"{BASE}{path}", wait_until="networkidle")
            page.wait_for_timeout(700)
            page.screenshot(path=str(out / f"{name}.png"))
            print("shot", name)

        # The images page has a second tab; capture it explicitly.
        if not only or "admin-images-remote" in only:
            page.goto(f"{BASE}/admin/images", wait_until="networkidle")
            page.get_by_role("tab", name="仓库版本").click()
            page.wait_for_timeout(900)
            page.screenshot(path=str(out / "admin-images-remote.png"))
            print("shot admin-images-remote")

        # Mobile view of the dashboard, to check responsiveness.
        if not only or "mobile" in only:
            mobile = browser.new_context(viewport={"width": 390, "height": 844}, locale="zh-CN")
            mpage = mobile.new_page()
            login(mpage, *ADMIN)
            mpage.goto(f"{BASE}/", wait_until="networkidle")
            mpage.wait_for_timeout(500)
            mpage.screenshot(path=str(out / "mobile-dashboard.png"))
            print("shot mobile-dashboard")

        browser.close()
    print(f"saved to {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())