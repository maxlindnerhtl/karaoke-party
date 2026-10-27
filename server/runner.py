"""Runs the whole server in one process: the API (uvicorn, in a thread), UDP discovery and the supervised worker
process. Used by `python -m server` and by the tray app (`python -m server.tray`)."""
import logging
import os
import socket
import subprocess
import sys
import threading
import time

import uvicorn

from . import config, db
from .app import create_app
from .discovery import Responder
from .state import ACTIVE_JOB

log = logging.getLogger("server")


class Supervisor:
    """Keeps the worker process running; restarts it with backoff if it dies."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.stopping = threading.Event()

    def run(self):
        backoff = 1.0
        while not self.stopping.is_set():
            started = time.monotonic()
            self.proc = subprocess.Popen([sys.executable, "-m", "server.worker", "--parent", str(os.getpid())],
                                         cwd=config.ROOT)
            code = self.proc.wait()
            if self.stopping.is_set():
                break
            backoff = 1.0 if time.monotonic() - started > 60 else min(backoff * 2, 60)
            log.error("worker exited with %s; restarting in %.0fs", code, backoff)
            self.stopping.wait(backoff)

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        self.stopping.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def processing_status(conn, worker_alive: bool | None) -> str:
    """One line for the tray: what the worker is doing."""
    if worker_alive is False:
        return "Worker not running (restarting…)"
    q = ",".join("?" * len(ACTIVE_JOB))
    active = conn.execute(f"SELECT COUNT(*) FROM jobs WHERE state IN ({q})", ACTIVE_JOB).fetchone()[0]
    queued = conn.execute("SELECT COUNT(*) FROM jobs WHERE state='queued'").fetchone()[0]
    failed = conn.execute("SELECT COUNT(*) FROM jobs WHERE state='failed'").fetchone()[0]
    if not active and not queued:
        text = "Processing: idle"
    else:
        text = f"Processing: {active} active, {queued} queued"
    return text + (f", {failed} failed" if failed else "")


class ServerRunner:
    def __init__(self, cfg: config.Config, worker: bool = True):
        self.cfg = cfg
        self.worker = worker
        self.sup: Supervisor | None = None
        self.discovery: Responder | None = None
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None

    def start(self):
        """Starts everything and returns once the API is listening. Raises RuntimeError if it can't start."""
        cfg = self.cfg
        if not port_free(cfg.port):   # before the worker starts: a second instance must not process jobs too
            raise RuntimeError(f"port {cfg.port} is in use: is the server already running?")
        app = create_app(cfg)
        # log_config=None: uvicorn logs through our handlers (it has no console under pythonw)
        self.server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=cfg.port, log_level="warning",
                                                    log_config=None))
        self.thread = threading.Thread(target=self.server.run, name="api", daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 30
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError(f"the API didn't start on port {cfg.port} (see data/logs/server.log)")
            time.sleep(0.1)
        self.discovery = Responder(cfg.port, cfg.discovery_port).start()
        if self.worker:
            self.sup = Supervisor()
            threading.Thread(target=self.sup.run, name="worker-supervisor", daemon=True).start()
        log.info("serving on http://0.0.0.0:%d/ (debug page: /debug)", cfg.port)

    def wait(self):
        """Blocks until the API stops (Ctrl+C stops everything)."""
        try:
            while self.thread and self.thread.is_alive():
                self.thread.join(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def worker_alive(self) -> bool | None:
        return self.sup.alive if self.sup else None

    def status(self) -> str:
        conn = db.connect(self.cfg.db_path)
        try:
            return processing_status(conn, self.worker_alive())
        finally:
            conn.close()

    def stop(self):
        if self.server:
            self.server.should_exit = True
        if self.thread:
            self.thread.join(timeout=15)
        if self.discovery:
            self.discovery.stop()
            self.discovery = None
        if self.sup:
            self.sup.stop()
            self.sup = None
        log.info("server stopped")
