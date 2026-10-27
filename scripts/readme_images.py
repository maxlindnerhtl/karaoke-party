"""Regenerates the README images in docs/images/ with headless Chrome: the banner (from the app logo), screenshots of
the phone and host pages, and the phone demo GIF. Only the made-up demo library is shown (scripts/demo_data.py).

    uv run python scripts/demo_data.py create
    MUSIC_PATH=demo/music DATA_DIR=demo/data HOST_PIN=1234 PORT=8775 DISCOVERY_PORT=8776 uv run python -m server --no-worker
    uv run python scripts/demo_data.py queue --url http://127.0.0.1:8775
    uv run python scripts/readme_images.py --url http://127.0.0.1:8775 [--chrome <exe>]

Needs Chrome or Chromium (--chrome, CHROME, PATH or the usual install folders) and the server's dependencies (httpx, websockets).
"""
import argparse
import asyncio
import base64
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import websockets
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images"


def find_chrome() -> Path:
    names = ("google-chrome", "chromium", "chromium-browser", "chrome")
    known = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
             "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    for c in [os.environ.get("CHROME"), *(shutil.which(n) for n in names), *known]:
        if c and Path(c).is_file():
            return Path(c)
    raise SystemExit("Chrome/Chromium not found: pass --chrome or set CHROME")

PIN = "1234"
PHONE = dict(width=390, height=844, deviceScaleFactor=2, mobile=True)
HOST = dict(width=1280, height=860, deviceScaleFactor=2, mobile=False)

BANNER = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=block" rel="stylesheet">
<style>
  html, body { margin: 0; background: transparent; }
  .b { width: 1280px; height: 320px; box-sizing: border-box; border-radius: 28px; overflow: hidden; position: relative;
       background: radial-gradient(120% 140% at 15% 0%, #3a2766 0%, #1d1830 45%, #121118 100%);
       display: flex; align-items: center; gap: 56px; padding: 0 88px; font-family: Roboto, sans-serif; }
  .lines { position: absolute; right: -30px; top: 34px; opacity: .08; }
  .lines div { height: 26px; border-radius: 13px; background: #f1eff8; margin: 0 0 30px auto; }
  h1 { margin: 0; font-size: 92px; font-weight: 700; color: #f1eff8; letter-spacing: -1.5px; line-height: 1; }
  h1 span { color: #b48cff; }
  p { margin: 22px 0 0; font-size: 31px; color: #b8b4c8; font-weight: 400; }
  p b { color: #f1eff8; font-weight: 500; }
</style></head><body><div class="b">
  <div class="lines"><div style="width:250px"></div><div style="width:190px"></div><div style="width:130px"></div></div>
  <img src="ICON" width="200" height="200" style="border-radius:44px;box-shadow:0 18px 50px rgba(0,0,0,.45)">
  <div><h1>Karaoke <span>Party</span></h1>
    <p>Your music, <b>vocals removed</b>, lyrics <b>synced word by word</b>,<br>on your Android TV.</p></div>
</div></body></html>"""


class Page:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def call(self, method, **params):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def js(self, expr):
        r = await self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        return r.get("result", {}).get("value")

    async def goto(self, url, wait=2.0):
        await self.call("Page.navigate", url=url)
        await asyncio.sleep(wait)

    async def device(self, d):
        await self.call("Emulation.setDeviceMetricsOverride", **d)

    async def png(self, clip=None) -> Image.Image:
        params = {"format": "png"}
        if clip:
            params.update(clip=clip, captureBeyondViewport=True)
        r = await self.call("Page.captureScreenshot", **params)
        return Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGB")

    async def type(self, selector, text, frames, delay=0.12):
        await self.js(f"document.querySelector('{selector}').focus()")
        for ch in text:
            await self.call("Input.insertText", text=ch)
            await asyncio.sleep(delay)
            frames.append((await self.png(), 140))


def save(img: Image.Image, name: str):
    img.save(OUT / name, optimize=True)
    print("wrote", (OUT / name).relative_to(ROOT), img.size)


def save_gif(frames, name, width):
    imgs = []
    for img, _ in frames:
        h = round(img.height * width / img.width)
        imgs.append(img.resize((width, h), Image.LANCZOS).quantize(colors=128, method=Image.Quantize.MEDIANCUT,
                                                                     dither=Image.Dither.NONE))
    imgs[0].save(OUT / name, save_all=True, append_images=imgs[1:], duration=[d for _, d in frames], loop=0,
                 optimize=True, disposal=1)
    print("wrote", (OUT / name).relative_to(ROOT), f"{len(imgs)} frames, {(OUT / name).stat().st_size // 1024} KB")


async def banner(p: Page):
    icon = (ROOT / "tvapp" / "art" / "icon.svg").read_text(encoding="utf-8")
    html = BANNER.replace("ICON", "data:image/svg+xml;base64," + base64.b64encode(icon.encode()).decode())
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html)
    await p.device(dict(width=1280, height=320, deviceScaleFactor=2, mobile=False))
    await p.call("Emulation.setDefaultBackgroundColorOverride", color={"r": 0, "g": 0, "b": 0, "a": 0})
    await p.goto(Path(f.name).as_uri(), wait=2.5)
    r = await p.call("Page.captureScreenshot", format="png")
    img = Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGBA")
    img.save(OUT / "banner.png", optimize=True)
    print("wrote docs/images/banner.png", img.size)
    await p.call("Emulation.setDefaultBackgroundColorOverride")


async def pages(p: Page, url: str):
    guests = {g["name"]: g["id"] for g in httpx.get(f"{url}/api/guests").json()["guests"]}
    await p.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark"}])

    # phone screenshots as Lena (her entries are outlined)
    await p.device(PHONE)
    await p.goto(f"{url}/")
    await p.js(f"localStorage.setItem('karaoke.guest', JSON.stringify({json.dumps({'id': guests['Lena'], 'name': 'Lena'})}))")
    await p.goto(f"{url}/")
    await p.type("#q", "la", [], delay=0.05)
    await asyncio.sleep(1.2)
    save(await p.png(), "phone-search.png")
    await p.js("document.querySelector('#tabQueue').click()")
    await asyncio.sleep(1)
    save(await p.png(), "phone-queue.png")

    # host page
    await p.device(HOST)
    await p.goto(f"{url}/host")
    await p.js(f"fetch('/api/host/login',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{pin:'{PIN}'}})}})")
    await p.goto(f"{url}/host", wait=2.5)
    save(await p.png(), "host.png")

    # demo GIF: a new guest joins, finds a song, queues it, sees the queue, sends a reaction
    await p.device(PHONE)
    await p.goto(f"{url}/")
    await p.js("localStorage.clear()")
    await p.goto(f"{url}/")
    frames = [(await p.png(), 1200)]
    await p.type("#joinName", "Mia", frames)
    frames[-1] = (frames[-1][0], 600)
    await p.js("document.querySelector('#joinForm').requestSubmit()")
    await asyncio.sleep(1.2)
    frames.append((await p.png(), 900))
    await p.type("#q", "slow dance", frames, delay=0.08)
    await asyncio.sleep(1.0)
    frames.append((await p.png(), 1800))
    await p.js("document.querySelector('#results [data-act=sing-lib]').click()")
    await asyncio.sleep(0.6)
    frames.append((await p.png(), 1500))
    await p.js("document.querySelector('#tabQueue').click()")
    await asyncio.sleep(1.0)
    frames.append((await p.png(), 2600))
    await p.js("document.querySelectorAll('#reactions button')[1].click()")
    await asyncio.sleep(0.25)
    frames.append((await p.png(), 1800))
    save_gif(frames, "demo.gif", 300)


async def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:8775")
    ap.add_argument("--chrome", type=Path)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    chrome = args.chrome or find_chrome()
    profile = tempfile.mkdtemp()
    proc = subprocess.Popen([str(chrome), "--headless=new", "--remote-debugging-port=9333", f"--user-data-dir={profile}",
                             "--hide-scrollbars", "--allow-file-access-from-files", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                targets = httpx.get("http://127.0.0.1:9333/json").json()
                break
            except httpx.HTTPError:
                time.sleep(0.2)
        target = next(t for t in targets if t["type"] == "page")
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=None) as ws:
            p = Page(ws)
            await p.call("Page.enable")
            await banner(p)
            await pages(p, args.url)
    finally:
        proc.terminate()


if __name__ == "__main__":
    asyncio.run(main())
