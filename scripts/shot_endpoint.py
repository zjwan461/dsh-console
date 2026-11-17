"""Screenshot the model endpoint settings (user + admin)."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
OUT = Path("shots/endpoint")
OUT.mkdir(parents=True, exist_ok=True)


def login(page, user: str, password: str) -> None:
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.fill('input[placeholder*="用户名"]', user)
    page.fill('input[type="password"]', password)
    page.locator('button:has-text("进入控制台")').click()
    page.wait_for_url(lambda url: "/login" not in url)
    page.wait_for_load_state("networkidle")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN").new_page()

        login(page, "linzhiyuan", "demo12345")
        page.goto(f"{BASE}/settings", wait_until="networkidle")
        page.get_by_role("tab", name="模型 Key").click()
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "user-model-key.png"))
        print("shot user-model-key")

        # Fill a custom endpoint to show the populated state.
        page.get_by_placeholder("https://api.deepseek.com/v1（留空 = 官方地址）").fill(
            "https://gateway.example.com/v1"
        )
        page.get_by_placeholder("sk-...（留空则沿用已保存的 Key）").fill("sk-demo-1234567890")
        page.get_by_role("button", name="保存", exact=True).click()
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "user-model-key-saved.png"))
        print("shot user-model-key-saved")

        page.context.clear_cookies()
        login(page, "admin", "admin12345")
        page.goto(f"{BASE}/admin/settings", wait_until="networkidle")
        page.wait_for_timeout(500)
        card = page.locator("div.card", has=page.get_by_role("heading", name="平台模型 Key"))
        card.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        card.screenshot(path=str(OUT / "admin-platform-key.png"))
        print("shot admin-platform-key")

        browser.close()


if __name__ == "__main__":
    main()