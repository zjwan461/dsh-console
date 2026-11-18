"""Record the README demo GIF: create an instance, then open a real PTY terminal.

Two clean clips are recorded (the login screen is captured on a throw-away page
and discarded), then concatenated and converted to an optimised GIF with ffmpeg.

    python scripts/readme_demo.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
API = "http://127.0.0.1:8000/api/v1"
OUT = Path("docs/images")
WORK = Path("shots/demo")
WIDTH, HEIGHT = 1280, 800
MEMBER = ("zhaoyun", "demo12345")
ADMIN = ("admin", "admin12345")


def _api(method: str, path: str, cookie: str = "", csrf: str = "", body=None):
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    if csrf and method != "GET":
        headers["X-CSRF-Token"] = csrf
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        return response.status, json.loads(raw) if raw else None


def reset_demo_instance() -> None:
    """Remove zhaoyun's instance so a recording always starts from zero."""
    request = urllib.request.Request(
        API + "/auth/login",
        data=json.dumps(
            {"username": ADMIN[0], "password": ADMIN[1], "remember": True}
        ).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        csrf = json.loads(response.read())["data"]["csrf_token"]

    _, listing = _api("GET", "/admin/instances", cookie=cookie)
    for item in listing["data"]["items"]:
        if item["user"]["username"] == MEMBER[0]:
            _api("DELETE", f"/admin/instances/{item['id']}", cookie=cookie, csrf=csrf)
            print(f"removed stale instance #{item['id']}")


CURSOR_JS = """
window.addEventListener('DOMContentLoaded', () => {
  const dot = document.createElement('div');
  dot.id = '__demo_cursor';
  Object.assign(dot.style, {
    position: 'fixed', width: '12px', height: '12px', borderRadius: '50%',
    background: 'rgba(47,111,99,0.35)', border: '2px solid #2f6f63',
    boxShadow: '0 0 0 4px rgba(47,111,99,0.10)',
    zIndex: 999999, pointerEvents: 'none', transform: 'translate(-50%,-50%)',
    transition: 'transform .08s ease, background .08s ease',
  });
  document.body.appendChild(dot);
  window.addEventListener('mousemove', (e) => {
    dot.style.left = e.clientX + 'px';
    dot.style.top = e.clientY + 'px';
  }, true);
  window.addEventListener('mousedown', () => {
    dot.style.transform = 'translate(-50%,-50%) scale(0.65)';
    dot.style.background = 'rgba(47,111,99,0.6)';
  }, true);
  window.addEventListener('mouseup', () => {
    dot.style.transform = 'translate(-50%,-50%) scale(1)';
    dot.style.background = 'rgba(47,111,99,0.35)';
  }, true);
});
"""


def click(page, locator) -> None:
    box = locator.bounding_box()
    if box:
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=8)
        page.wait_for_timeout(180)
    locator.click()


def login(page, user: str, password: str) -> None:
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.fill('input[placeholder*="用户名"]', user)
    page.fill('input[type="password"]', password)
    click(page, page.locator('button:has-text("进入控制台")'))
    page.wait_for_url(lambda url: "/login" not in url)
    page.wait_for_load_state("networkidle")


def _record(browser, video_dir: Path, credentials: tuple[str, str], action) -> Path:
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        record_video_dir=str(video_dir),
        record_video_size={"width": WIDTH, "height": HEIGHT},
        locale="zh-CN",
    )
    context.add_init_script(CURSOR_JS)
    # Log in on a throw-away page so the clip starts on the product UI.
    warm = context.new_page()
    login(warm, *credentials)
    warm.close()

    page = context.new_page()
    action(page)
    video = page.video
    context.close()
    return Path(video.path())


def record_user(browser, video_dir: Path) -> Path:
    def action(page) -> None:
        page.goto(f"{BASE}/", wait_until="networkidle")
        page.wait_for_timeout(600)

        click(page, page.locator('button:has-text("创建我的 DSH 云电脑")').first)
        page.wait_for_timeout(700)
        dialog = page.locator(".el-dialog:visible")
        click(page, dialog.get_by_role("button", name="创建", exact=True))
        page.wait_for_timeout(400)
        page.locator("text=运行中").first.wait_for(timeout=10000)
        page.wait_for_timeout(600)
        open_button = page.locator('button:has-text("打开云舱")').first
        if open_button.count():
            box = open_button.bounding_box()
            if box:
                page.mouse.move(
                    box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=10
                )
        page.wait_for_timeout(700)

    return _record(browser, video_dir, MEMBER, action)


def record_admin(browser, video_dir: Path) -> Path:
    def action(page) -> None:
        page.goto(f"{BASE}/admin/instances", wait_until="networkidle")
        page.wait_for_timeout(500)

        row = page.locator("tr", has_text=MEMBER[0]).first
        click(page, row.get_by_text("终端", exact=True))
        page.wait_for_timeout(900)

        page.locator(".xterm").click()
        page.wait_for_timeout(250)
        page.keyboard.type("uname -a", delay=55)
        page.keyboard.press("Enter")
        page.wait_for_timeout(450)
        page.keyboard.type("pwd", delay=55)
        page.keyboard.press("Enter")
        page.wait_for_timeout(800)

    return _record(browser, video_dir, ADMIN, action)


def run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1500:])


def build_gif(user: Path, admin: Path, out: Path, tmp: Path) -> None:
    user_mp4 = tmp / "user.mp4"
    admin_mp4 = tmp / "admin.mp4"
    for src, dst in ((user, user_mp4), (admin, admin_mp4)):
        run_ffmpeg(
            [
                "ffmpeg", "-y",
                # Playwright starts recording on about:blank; skip that lead-in.
                "-ss", "0.8", "-i", str(src),
                "-t", "4.2",
                "-vf", f"scale={WIDTH}:{HEIGHT},fps=12",
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                str(dst),
            ]
        )

    listing = tmp / "concat.txt"
    listing.write_text(
        f"file '{user_mp4.resolve().as_posix()}'\nfile '{admin_mp4.resolve().as_posix()}'\n",
        encoding="utf-8",
    )
    run_ffmpeg(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-vf",
            (
                "fps=10,scale=900:-2:flags=lanczos,"
                "split[a][b];[a]palettegen=max_colors=128[p];"
                "[b][p]paletteuse=dither=sierra2_4a"
            ),
            "-loop", "0",
            str(out),
        ]
    )


def main() -> None:
    reset_demo_instance()
    OUT.mkdir(parents=True, exist_ok=True)
    if WORK.exists():
        shutil.rmtree(WORK)
    user_dir = WORK / "user"
    admin_dir = WORK / "admin"
    user_dir.mkdir(parents=True)
    admin_dir.mkdir(parents=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        user_video = record_user(browser, user_dir)
        admin_video = record_admin(browser, admin_dir)
        browser.close()

    print("user clip:", user_video)
    print("admin clip:", admin_video)
    build_dir = WORK / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    build_gif(user_video, admin_video, OUT / "demo.gif", build_dir)
    size = (OUT / "demo.gif").stat().st_size
    print(f"saved {OUT / 'demo.gif'} ({size / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    main()