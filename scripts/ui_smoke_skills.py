from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
errors: list[str] = []


def login(page, user, password):
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.fill('input[placeholder*="用户名"]', user)
    page.fill('input[type="password"]', password)
    page.locator('button:has-text("进入控制台")').click()
    page.wait_for_url(lambda url: "/login" not in url)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN").new_page()
    page.on(
        "console",
        lambda msg: errors.append(msg.text)
        if msg.type == "error" and "Failed to load resource" not in msg.text
        else None,
    )
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    login(page, "admin", "admin12345")
    page.goto(f"{BASE}/admin/skills", wait_until="networkidle")
    page.get_by_role("button", name="编辑").first.click()
    page.wait_for_selector(".el-dialog:visible")
    assert page.locator(".el-dialog textarea").first.input_value().strip(), "editor body empty"
    page.get_by_role("button", name="版本历史").click()
    page.wait_for_timeout(600)
    assert page.get_by_text("版本历史").count() >= 1
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    page.goto(f"{BASE}/admin/skills", wait_until="networkidle")
    page.get_by_role("button", name="开放范围").first.click()
    page.wait_for_selector(".el-dialog:visible")
    assert page.get_by_text("全员").count() >= 1
    page.keyboard.press("Escape")

    page.goto(f"{BASE}/admin/plugins", wait_until="networkidle")
    page.get_by_role("button", name="分配").first.click()
    page.wait_for_selector(".el-dialog:visible")
    assert page.get_by_text("按套餐").count() >= 1
    page.keyboard.press("Escape")

    page.context.clear_cookies()
    login(page, "zhaoyun", "demo12345")
    page.goto(f"{BASE}/", wait_until="networkidle")
    page.wait_for_timeout(800)
    assert page.get_by_text("插件变更，待重启").count() >= 1, "restart badge missing"

    page.goto(f"{BASE}/settings", wait_until="networkidle")
    page.get_by_role("tab", name="技能").click()
    page.wait_for_timeout(500)
    boxes = page.locator(".skill-list .el-checkbox")
    before = boxes.count()
    assert before >= 3, f"expected skills, got {before}"
    boxes.first.click()
    page.get_by_role("button", name="保存并应用").click()
    page.wait_for_timeout(1200)
    assert page.get_by_text("已应用").count() >= 1, "save toast missing"
    page.wait_for_timeout(500)
    page.screenshot(path="shots/skills/user-skills-toggle.png")

    # Restore the default (all enabled) so demo state stays sane.
    page.get_by_role("button", name="全部启用").click()
    page.get_by_role("button", name="保存并应用").click()
    page.wait_for_timeout(1200)

    browser.close()

print("errors:", errors[:10])
assert not errors, errors