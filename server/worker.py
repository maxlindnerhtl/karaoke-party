"""Processing worker: a separate process that works through the jobs table (SPEC section 5.4).

Two loops:
- GPU loop: main jobs (separate -> lyrics -> render the queued vocal level), one at a time, in queue order.
  The entry is ready as soon as its queued level is rendered.
- Render loop: background renders of the other vocal levels (ffmpeg, CPU), in parallel with the GPU loop,
  only once the queued level of that song + key exists.
Models stay loaded between jobs. Progress goes to the jobs table; the API broadcasts it over the WebSocket.

Run: uv run python -m server.worker
"""
import logging
import os
import signal
import sys
import threading
import time
import traceback
import warnings
from logging.handlers import RotatingFileHandler

from . import audio, config, db, pipeline, state

log = logging.getLogger("worker")

# pyannote (pulled in by whisperx) warns at import that torchcodec can't load its FFmpeg DLLs. We pass audio as
# arrays, so its file decoding is never used.
warnings.filterwarnings("ignore", message=r"\s*torchcodec is not installed correctly", category=UserWarning,
                        module=r"pyannote\.audio\.core\.io")

IDLE_SLEEP = 0.5
_ORDER = ("(SELECT MIN(q.position) FROM queue q WHERE q.song_id=j.song_id AND q.key_shift=j.key_shift "
          "AND q.status IN ('waiting','preparing','ready','failed','playing'))")


class Worker:
    def __init__(self, cfg: config.Config):
        self.cfg = cfg
        self.models: pipeline.Models | None = None   # created in run(), once the device is known
        self.stop = threading.Event()
        self.orphaned = False

    def parent_gone(self):
        log.warning("the server process is gone; finishing the current job, then exiting")
        self.orphaned = True
        self.stop.set()

    # --- job bookkeeping --------------------------------------------------------------------------------

    def _claim(self, conn, kind: str):
        extra = ("AND EXISTS (SELECT 1 FROM renders r WHERE r.song_id=j.song_id AND r.key_shift=j.key_shift)"
                 if kind == "render" else "")
        with db.tx(conn):
            row = conn.execute(
                f"SELECT j.* FROM jobs j WHERE j.kind=? AND j.state='queued' {extra} "
                f"ORDER BY {_ORDER} IS NULL, {_ORDER}, j.created_at LIMIT 1", (kind,)).fetchone()
            if row is None:
                return None
            first = "rendering" if kind == "render" else "separating"
            conn.execute("UPDATE jobs SET state=?, progress=0, updated_at=? WHERE id=?", (first, time.time(), row["id"]))
            state.refresh_statuses(conn)
            return dict(row)

    def _set(self, conn, job_id: int, st: str | None = None, progress: float | None = None, error: str | None = None):
        sets, args = ["updated_at=?"], [time.time()]
        if st:
            sets.append("state=?"); args.append(st)
        if progress is not None:
            sets.append("progress=?"); args.append(progress)
        if error is not None:
            sets.append("error=?"); args.append(error[:2000])
        with db.tx(conn):
            conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", (*args, job_id))
            if st:
                state.refresh_statuses(conn)

    def _add_render(self, conn, song_id, key_shift, vocal_level, path):
        with db.tx(conn):
            conn.execute("INSERT OR REPLACE INTO renders (song_id, key_shift, vocal_level, path, created_at) "
                         "VALUES (?,?,?,?,?)", (song_id, key_shift, vocal_level, str(path), time.time()))

    def _gain(self, conn, song_id: str, key_shift: int) -> float:
        """Loudness gain for (song, key), measured once and cached in the loudness table."""
        row = conn.execute("SELECT gain_db FROM loudness WHERE song_id=? AND key_shift=?", (song_id, key_shift)).fetchone()
        if row:
            return row["gain_db"]
        t = time.perf_counter()
        gain = audio.measure_gain(self.cfg.ffmpeg, pipeline.stems_dir(self.cfg, song_id), key_shift)
        with db.tx(conn):
            conn.execute("INSERT OR REPLACE INTO loudness (song_id, key_shift, gain_db, measured_at) VALUES (?,?,?,?)",
                         (song_id, key_shift, gain, time.time()))
        log.info("loudness %s key %+d: gain %+.2f dB (%.1fs)", song_id, key_shift, gain, time.perf_counter() - t)
        return gain

    def _song(self, conn, song_id) -> dict:
        row = conn.execute("SELECT * FROM songs WHERE id=?", (song_id,)).fetchone()
        if row is None or row["missing"]:
            raise RuntimeError("song file is missing from the library")
        return dict(row)

    # --- main jobs (GPU) --------------------------------------------------------------------------------

    def run_main(self, conn, job):
        jid, sid = job["id"], job["song_id"]
        song = self._song(conn, sid)
        t0 = time.perf_counter()
        if not (song["stems_ready"] and pipeline.stems_exist(self.cfg, sid)):
            last = [0.0]

            def progress(p):
                if time.monotonic() - last[0] > 0.5:   # at most 2 DB writes per second
                    last[0] = time.monotonic()
                    self._set(conn, jid, progress=round(p, 3))

            pipeline.separate(self.cfg, self.models, song, progress)
            with db.tx(conn):
                conn.execute("UPDATE songs SET stems_ready=1 WHERE id=?", (sid,))
        t1 = time.perf_counter()

        if not (self.cfg.lyrics_dir / f"{sid}.json").is_file():
            self._set(conn, jid, "lyrics", progress=0)
            doc = pipeline.make_lyrics(self.cfg, self.models, song)
            pipeline.save_lyrics(self.cfg, sid, doc)
            with db.tx(conn):
                conn.execute("UPDATE songs SET lyrics_source=? WHERE id=?", (doc["source"], sid))
        t2 = time.perf_counter()

        self._set(conn, jid, "rendering", progress=0)
        gain = self._gain(conn, sid, job["key_shift"])
        out = pipeline.render(self.cfg, sid, job["key_shift"], job["vocal_level"], gain)
        self._add_render(conn, sid, job["key_shift"], job["vocal_level"], out)
        self._set(conn, jid, "ready", progress=1)
        log.info("main job %s ready: %s - %s (separate %.1fs, lyrics %.1fs, loudness+render %.1fs)",
                 jid, song["artist"], song["title"], t1 - t0, t2 - t1, time.perf_counter() - t2)

    def run_render(self, conn, job):
        song = self._song(conn, job["song_id"])
        gain = self._gain(conn, song["id"], job["key_shift"])
        out = pipeline.render(self.cfg, song["id"], job["key_shift"], job["vocal_level"], gain)
        self._add_render(conn, song["id"], job["key_shift"], job["vocal_level"], out)
        self._set(conn, job["id"], "ready", progress=1)

    # --- loops ------------------------------------------------------------------------------------------

    def loop(self, kind: str, run):
        conn = db.connect(self.cfg.db_path)
        if kind == "main":
            try:
                pipeline.warmup(self.models)
            except Exception:
                log.exception("model warmup failed; models load on the first job instead")
        while not self.stop.is_set():
            try:
                job = self._claim(conn, kind)
            except Exception:
                log.exception("claiming a %s job failed", kind)
                job = None
            if job is None:
                self.stop.wait(IDLE_SLEEP)
                continue
            log.info("%s job %s: song %s key %+d vocals %d%%", kind, job["id"], job["song_id"], job["key_shift"],
                     job["vocal_level"])
            try:
                run(conn, job)
            except Exception as e:
                log.error("%s job %s failed:\n%s", kind, job["id"], traceback.format_exc())
                self._set(conn, job["id"], "failed", error=f"{type(e).__name__}: {e}")

    def recover(self):
        """Jobs interrupted by a crash or restart start over."""
        conn = db.connect(self.cfg.db_path)
        with db.tx(conn):
            n = conn.execute("UPDATE jobs SET state='queued', progress=0, updated_at=? WHERE state IN "
                             "('separating','lyrics','rendering')", (time.time(),)).rowcount
            state.refresh_statuses(conn)
        conn.close()
        if n:
            log.info("re-queued %d interrupted jobs", n)

    def init_device(self):
        """Picks CUDA or the CPU and stores it for the host page (internal setting `processing_device`)."""
        device = pipeline.pick_device(self.cfg.device)
        self.models = pipeline.Models(device, self.cfg.whisper_model)
        info = pipeline.device_info(device)
        if device == "cpu":
            log.warning("processing on the CPU: this works, but a song takes several minutes (often 5-15) instead "
                        "of about one. An NVIDIA GPU with CUDA makes it much faster (see README).")
        else:
            log.info("processing on %s (CUDA)", info["name"])
        conn = db.connect(self.cfg.db_path)
        try:
            db.set_settings(conn, {"processing_device": info})
        finally:
            conn.close()

    def run(self):
        self.init_device()
        self.recover()
        threads = [threading.Thread(target=self.loop, args=("main", self.run_main), name="gpu", daemon=True),
                   threading.Thread(target=self.loop, args=("render", self.run_render), name="render", daemon=True)]
        for t in threads:
            t.start()
        log.info("worker running")
        while not self.stop.is_set():
            self.stop.wait(1)
        for t in threads:
            # orphaned: finish the current job (its ffmpeg would otherwise keep writing the same file the next
            # worker renders); the next worker waits for the lock meanwhile
            t.join(timeout=None if self.orphaned else 10)


def try_lock(path):
    """Exclusive lock on `path`, held until the returned file is closed or the process dies; None if another
    process holds it. Only one worker may claim jobs: a second one would also re-queue the first one's running
    jobs (recover) and both would write the same render files."""
    f = open(path, "a+b")
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        f.close()
        return None


def watch_parent(pid: int, on_gone):
    """Calls on_gone (from a thread) when process `pid` exits. Child processes don't end with their parent (on
    Windows never, on Linux not on a crash), so without this a crashed server leaves its worker running next to
    the restarted server's worker."""
    if sys.platform != "win32":
        import select
        try:
            fd = os.pidfd_open(pid)   # Linux 5.3+; like the Windows handle, it rules out pid reuse
        except ProcessLookupError:
            on_gone()
            return

        def wait_fd():
            select.select([fd], [], [])   # readable once the process has exited
            os.close(fd)
            on_gone()

        threading.Thread(target=wait_fd, name="parent-watch", daemon=True).start()
        return
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE; holding the handle rules out pid reuse
    if not handle:
        on_gone()
        return

    def wait():
        kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
        kernel32.CloseHandle(handle)
        on_gone()

    threading.Thread(target=wait, name="parent-watch", daemon=True).start()


def wait_for_lock(path, stop: threading.Event, interval: float = 2.0):
    """Blocks until this process holds the worker lock (or stop is set; then None)."""
    waiting = False
    while not stop.is_set():
        lock = try_lock(path)
        if lock:
            if waiting:
                log.info("the other worker is gone; starting")
            return lock
        if not waiting:
            log.warning("another worker holds %s; waiting for it to exit", path)
            waiting = True
        stop.wait(interval)
    return None


def setup_logging(cfg: config.Config, name: str):
    """Rotating log file in data/logs (2 MB x 4), plus the console if there is one."""
    # under pythonw (tray app, autostart) there is no console: sys.stdout/stderr are None, and libraries that write
    # progress bars to them (tqdm) would crash
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    console = sys.stderr is not None
    if not console:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handlers = [RotatingFileHandler(cfg.logs_dir / f"{name}.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler())
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)
    for noisy in ("httpx", "whisperx", "pyannote", "lightning", "speechbrain", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main():
    cfg = config.load()
    config.check(cfg)
    cfg.ensure_dirs()
    setup_logging(cfg, "worker")
    db.init(cfg.db_path)
    w = Worker(cfg)
    signal.signal(signal.SIGINT, lambda *_: w.stop.set())
    signal.signal(signal.SIGTERM, lambda *_: w.stop.set())
    if "--parent" in sys.argv:   # started by the server (runner.Supervisor)
        watch_parent(int(sys.argv[sys.argv.index("--parent") + 1]), w.parent_gone)
    lock = wait_for_lock(cfg.data_dir / "worker.lock", w.stop)
    if lock:
        with lock:
            w.run()


if __name__ == "__main__":
    main()
