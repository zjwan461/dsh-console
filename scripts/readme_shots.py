"""Generate the screenshots used by README.md (docs/images/*.jpg).

These are deliberately captured from the local FakeDriver environment: no real
host, IP or user data appears, only the demo dataset from scripts/seed_demo.py.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
OUT = Path("docs/images")
OUT.mkdir(parents=True, exist_ok=True)

ADMIN = ("admin", "admin12345")
MEMBER = ("linzhiyuan", "demo12345")


def login(page, user: str, password: str) -> None:
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.fill('input[placeholder*="用户名"]', user)
    page.fill('input[type="password"]', password)
    page.locator('button:has-text("进入控制台")').click()
    page.wait_for_url(lambda url: "/login" not in url)
    page.wait_for_load_state("networkidle")


def shot(page, name: str) -> None:
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / f"{name}.jpg"), type="jpeg", quality=86)
    print("saved", name)


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=1,
            locale="zh-CN",
        )
        page = context.new_page()

        # 1. Login: the visual hook.
        page.goto(f"{BASE}/login", wait_until="networkidle")
        page.screenshot(path=str(OUT / "login.jpg"), type="jpeg", quality=90)
        print("saved login")

        # 2. Member dashboard with a running instance + quota + announcement.
        login(page, *MEMBER)
        page.goto(f"{BASE}/", wait_until="networkidle")
        shot(page, "dashboard")

        # 3. Admin: operations table (shorter viewport keeps the shot tight).
        page.context.clear_cookies()
        login(page, *ADMIN)
        page.set_viewport_size({"width": 1440, "height": 470})
        page.goto(f"{BASE}/admin/instances", wait_until="networkidle")
        shot(page, "instances")

        # 4. Admin: image versions (the registry / tar import workflow).
        page.set_viewport_size({"width": 1440, "height": 560})
        page.goto(f"{BASE}/admin/images", wait_until="networkidle")
        shot(page, "versions")

        # 5. Admin: user & quota management.
        page.set_viewport_size({"width": 1440, "height": 640})
        page.goto(f"{BASE}/admin/users", wait_until="networkidle")
        shot(page, "users")

        browser.close()


if __name__ == "__main__":
    main()