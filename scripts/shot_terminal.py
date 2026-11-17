"""Screenshot the admin terminal (xterm.js) against the local fake driver."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
OUT = Path("shots/terminal")
OUT.mkdir(parents=True, exist_ok=True)


def login(page) -> None:
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.fill('input[placeholder*="用户名"]', "admin")
    page.fill('input[type="password"]', "admin12345")
    page.locator('button:has-text("进入控制台")').click()
    page.wait_for_url(lambda url: "/login" not in url)
    page.wait_for_load_state("networkidle")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN").new_page()
        login(page)
        page.goto(f"{BASE}/admin/instances", wait_until="networkidle")
        page.wait_for_timeout(500)
        page.locator("tr", has_text="linzhiyuan").get_by_text("终端").click()
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "terminal-open.png"))
        print("shot terminal-open")

        # Type into xterm and let the fake PTY echo it back.
        page.locator(".xterm").click()
        page.keyboard.type("uname -a")
        page.keyboard.press("Enter")
        page.wait_for_timeout(600)
        page.keyboard.type("ls -la /data")
        page.keyboard.press("Enter")
        page.wait_for_timeout(600)
        page.screenshot(path=str(OUT / "terminal-typed.png"))
        print("shot terminal-typed")

        browser.close()


if __name__ == "__main__":
    main()