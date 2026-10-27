"""Tray app (Windows only; on Linux a systemd user service runs `python -m server` instead, see scripts/install.sh):
runs the server (API, discovery, worker) with a tray icon.

    .venv\\Scripts\\pythonw.exe -m server.tray     (what autostart runs: no console window)
    uv run python -m server.tray                  (same, with a console for the logs)

Menu: open host page, open music folder, processing status, restart, quit.
"""
import ctypes
import logging
import os
import sys
import threading
import time
import webbrowser

import pystray
from PIL import Image, ImageDraw

from . import config
from .runner import ServerRunner, port_free
from .worker import setup_logging

log = logging.getLogger("tray")

STATUS_INTERVAL = 3.0


def message(text: str, error: bool = True):
    """A Windows message box (the tray app has no console to print to)."""
    ctypes.windll.user32.MessageBoxW(0, text, "Karaoke", 0x10 if error else 0x40)


def icon_image() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=(123, 63, 228, 255))          # the web pages' accent purple
    d.rounded_rectangle((24, 12, 40, 38), radius=8, fill="white")  # microphone
    d.arc((18, 22, 46, 46), 0, 180, fill="white", width=3)
    d.line((32, 46, 32, 53), fill="white", width=3)
    d.line((24, 53, 40, 53), fill="white", width=3)
    return img


class Tray:
    def __init__(self, cfg: config.Config):
        self.cfg = cfg
        self.runner = ServerRunner(cfg)
        self.status = "Starting…"
        self.busy = threading.Lock()   # restart / quit
        self.icon = pystray.Icon("karaoke", icon_image(), "Karaoke", menu=pystray.Menu(
            pystray.MenuItem("Open host page", self.open_host, default=True),
            pystray.MenuItem("Open music folder", self.open_music),
            pystray.MenuItem(lambda _item: self.status, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Restart server", self.restart),
            pystray.MenuItem("Quit", self.quit),
        ))

    def open_host(self, *_):
        webbrowser.open(f"http://localhost:{self.cfg.port}/host")

    def open_music(self, *_):
        os.startfile(self.cfg.music_path)   # opens Explorer; nothing is written there

    def refresh(self):
        while True:
            try:
                self.status = self.runner.status()
            except Exception as e:
                self.status = f"Status unavailable ({type(e).__name__})"
            self.icon.title = f"Karaoke: {self.status}"
            self.icon.update_menu()
            time.sleep(STATUS_INTERVAL)

    def restart(self, *_):
        def work():
            with self.busy:
                self.status = "Restarting…"
                self.icon.update_menu()
                log.info("restart requested from the tray")
                self.runner.stop()
                deadline = time.monotonic() + 20
                while not port_free(self.cfg.port) and time.monotonic() < deadline:
                    time.sleep(0.5)
                self.runner = ServerRunner(self.cfg)
                try:
                    self.runner.start()
                except RuntimeError as e:
                    log.error("restart failed: %s", e)
                    message(f"Restart failed:\n{e}")
        threading.Thread(target=work, name="restart", daemon=True).start()

    def quit(self, *_):
        with self.busy:
            log.info("quit from the tray")
            self.runner.stop()
        self.icon.stop()

    def run(self):
        self.runner.start()
        threading.Thread(target=self.refresh, name="tray-status", daemon=True).start()
        self.icon.run()


def main():
    if sys.platform != "win32":
        raise SystemExit("The tray app is Windows-only. On Linux run `python -m server` or the systemd service "
                         "from scripts/install.sh.")
    cfg = config.load()
    cfg.ensure_dirs()
    setup_logging(cfg, "server")
    if not port_free(cfg.port):
        message(f"The karaoke server is already running (port {cfg.port} is in use).", error=False)
        return
    try:
        config.check(cfg)
        Tray(cfg).run()
    except (SystemExit, RuntimeError) as e:   # config.check exits with a message; start raises RuntimeError
        log.error("tray: %s", e)
        message(f"The karaoke server could not start:\n{e}")


if __name__ == "__main__":
    main()
