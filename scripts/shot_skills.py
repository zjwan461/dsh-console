from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
OUT = Path("shots/skills")
OUT.mkdir(parents=True, exist_ok=True)


def login(page, user, password):
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.fill('input[placeholder*="用户名"]', user)
    page.fill('input[type="password"]', password)
    page.locator('button:has-text("进入控制台")').click()
    page.wait_for_url(lambda url: "/login" not in url)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_context(viewport={"width": 1440, "height": 820}, locale="zh-CN").new_page()
    login(page, "admin", "admin12345")
    page.goto(f"{BASE}/admin/skills", wait_until="networkidle")
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / "admin-skills.png"))

    page.get_by_text("编辑", exact=True).first.click()
    page.wait_for_timeout(700)
    page.screenshot(path=str(OUT / "admin-skill-edit.png"))
    page.keyboard.press("Escape")

    page.goto(f"{BASE}/admin/plugins", wait_until="networkidle")
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / "admin-plugins.png"))

    page.context.clear_cookies()
    login(page, "zhaoyun", "demo12345")
    page.goto(f"{BASE}/settings", wait_until="networkidle")
    page.get_by_role("tab", name="技能").click()
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / "user-skills.png"))
    browser.close()
print("done")