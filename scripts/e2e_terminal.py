import asyncio
import json
import sys
import urllib.request
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _remote as cfg  # noqa: E402

API = cfg.API
WS = cfg.API.replace("http://", "ws://").replace("https://", "wss://") + (
    "/admin/instances/{}/terminal"
)
ADMIN = cfg.ADMIN


def login():
    req = urllib.request.Request(
        f"{API}/auth/login",
        data=json.dumps(
            {"username": ADMIN[0], "password": ADMIN[1], "remember": True}
        ).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        cookie = resp.headers["Set-Cookie"].split(";", 1)[0]
    return cookie


async def main():
    cookie = login()
    instance_id = None
    req = urllib.request.Request(
        f"{API}/admin/instances", headers={"Cookie": cookie, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        items = json.load(resp)["data"]["items"]
    for item in items:
        if item["status"] == "running":
            instance_id = item["id"]
            break
    if instance_id is None:
        print("no running instance; skipping")
        return

    url = WS.format(instance_id)
    try:
        async with websockets.connect(url, additional_headers={"Cookie": cookie}) as ws:
            await ws.send("echo terminal-ok; id -u\n")
            chunks = []
            for _ in range(12):
                try:
                    data = await asyncio.wait_for(ws.recv(), timeout=4)
                except asyncio.TimeoutError:
                    break
                chunks.append(data if isinstance(data, str) else data.decode(errors="replace"))
                if "terminal-ok" in "".join(chunks):
                    break
            text = "".join(chunks)
            print("OUTPUT:", text.strip()[:400])
            print("PASS" if "terminal-ok" in text else "FAIL")
    except Exception as exc:
        print("WS FAILED:", type(exc).__name__, str(exc)[:300])


asyncio.run(main())