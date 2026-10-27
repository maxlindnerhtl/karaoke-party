"""Music library: index MUSIC_PATH (read-only), watch it for changes, fuzzy search."""
import hashlib
import logging
import os
import threading
import time
from pathlib import Path

import mutagen
from rapidfuzz import fuzz, process
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import db

log = logging.getLogger("library")

EXTENSIONS = {".mp3", ".m4a", ".flac", ".ogg", ".wav"}
CHUNK = 1 << 20


def song_id(path: Path) -> str:
    """Fast content hash: file size + first and last MB. Survives renames and moves."""
    size = path.stat().st_size
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(CHUNK))
        if size > CHUNK:
            f.seek(max(size - CHUNK, CHUNK))
            h.update(f.read(CHUNK))
    return h.hexdigest()


def parse_filename(stem: str) -> tuple[str, str]:
    """'Artist - Title' -> (artist, title); no separator -> ('', stem)."""
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", stem.strip()


def read_tags(path: Path) -> dict:
    artist, title = parse_filename(path.stem)
    meta = {"artist": artist, "title": title, "album": "", "duration": 0.0}
    try:
        f = mutagen.File(path, easy=True)
    except Exception as e:  # unreadable tags must not stop indexing
        log.warning("tags unreadable for %s: %s", path, e)
        return meta
    if f is None:
        return meta
    if f.info and getattr(f.info, "length", None):
        meta["duration"] = round(float(f.info.length), 3)
    tags = f.tags or {}
    for key in ("artist", "title", "album"):
        try:
            value = tags.get(key)
        except Exception:
            value = None
        if value:
            meta[key] = str(value[0] if isinstance(value, list) else value).strip()
    return meta


def scan(conn, music_path: Path) -> dict:
    """Incremental scan: unchanged files (same size + mtime) are skipped; vanished files are marked missing."""
    stats = {"added": 0, "updated": 0, "missing": 0, "unchanged": 0}
    known = {r["path"]: r for r in conn.execute("SELECT id, path, size, mtime FROM songs")}
    seen = set()
    for root, _dirs, files in os.walk(music_path):
        for name in files:
            p = Path(root) / name
            if p.suffix.lower() not in EXTENSIONS:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            key = str(p)
            seen.add(key)
            row = known.get(key)
            if row and row["size"] == st.st_size and abs(row["mtime"] - st.st_mtime) < 1e-3:
                stats["unchanged"] += 1
                continue
            try:
                sid = song_id(p)
            except OSError as e:   # still being copied, locked, ...
                log.info("skipping %s for now: %s", p, e)
                seen.discard(key)
                continue
            meta = read_tags(p)
            with db.tx(conn):
                exists = conn.execute("SELECT 1 FROM songs WHERE id = ?", (sid,)).fetchone()
                if exists:
                    conn.execute(
                        "UPDATE songs SET path=?, size=?, mtime=?, title=?, artist=?, album=?, duration=?, missing=0 "
                        "WHERE id=?",
                        (key, st.st_size, st.st_mtime, meta["title"], meta["artist"], meta["album"],
                         meta["duration"], sid))
                    stats["updated"] += 1
                else:
                    conn.execute(
                        "INSERT INTO songs (id, path, size, mtime, title, artist, album, duration, added_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (sid, key, st.st_size, st.st_mtime, meta["title"], meta["artist"], meta["album"],
                         meta["duration"], time.time()))
                    stats["added"] += 1
                if row and row["id"] != sid:   # file content changed: the old id no longer has a file here
                    conn.execute("UPDATE songs SET missing=1 WHERE id=? AND path=?", (row["id"], key))
    gone = [p for p in known if p not in seen]
    if gone:
        with db.tx(conn):
            # a moved file already had its row re-pointed to the new path above, so this only hits vanished files
            for p in gone:
                cur = conn.execute("UPDATE songs SET missing=1 WHERE path=? AND missing=0", (p,))
                stats["missing"] += cur.rowcount
    return stats


class Library:
    """Owns the in-memory search index and the folder watcher (runs in the API process)."""

    def __init__(self, db_path: Path, music_path: Path, on_change=None):
        self.db_path = db_path
        self.music_path = music_path
        self.on_change = on_change
        self._conn = db.connect(db_path)
        self._lock = threading.Lock()
        self._index: list[dict] = []
        self._dirty = threading.Event()
        self._stop = threading.Event()
        self._observer = None
        self._thread = None

    def rescan(self) -> dict:
        with self._lock:
            stats = scan(self._conn, self.music_path)
            self._reload()
        log.info("library scan: %s", stats)
        if self.on_change and (stats["added"] or stats["updated"] or stats["missing"]):
            self.on_change(stats)
        return stats

    def _reload(self):
        rows = self._conn.execute(
            "SELECT id, title, artist, album, duration, play_count, starred, lyrics_source, added_at "
            "FROM songs WHERE missing = 0").fetchall()
        self._index = [dict(r) for r in rows]

    def start(self):
        self.rescan()
        handler = _Handler(self._dirty)
        self._observer = Observer()
        self._observer.schedule(handler, str(self.music_path), recursive=True)
        self._observer.daemon = True
        self._observer.start()
        self._thread = threading.Thread(target=self._debounce, name="library-watch", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._dirty.set()
        if self._observer:
            self._observer.stop()

    def _debounce(self):
        """Rescan 2 s after the last change (files being copied fire many events)."""
        while not self._stop.is_set():
            self._dirty.wait()
            while self._dirty.is_set() and not self._stop.is_set():
                self._dirty.clear()
                time.sleep(2)
            if not self._stop.is_set():
                try:
                    self.rescan()
                except Exception:
                    log.exception("rescan failed")

    def songs(self, sort: str = "title") -> list[dict]:
        keys = {
            "title": lambda s: (s["title"].lower(), s["artist"].lower()),
            "artist": lambda s: (s["artist"].lower(), s["title"].lower()),
            "most_played": lambda s: (-s["play_count"], s["title"].lower()),
            "recent": lambda s: -s["added_at"],
        }
        return sorted(self._index, key=keys.get(sort, keys["title"]))

    def search(self, q: str, limit: int = 20, min_score: float = 60) -> list[dict]:
        q = q.strip().lower()
        if not q:
            return self.songs()[:limit]
        choices = {i: f"{s['artist']} {s['title']}".lower() for i, s in enumerate(self._index)}
        hits = process.extract(q, choices, scorer=fuzz.WRatio, limit=limit, score_cutoff=min_score)
        return [dict(self._index[i], score=round(score, 1)) for _text, score, i in hits]

    def get(self, sid: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM songs WHERE id = ?", (sid,)).fetchone()
        return dict(row) if row else None

    def touch(self):
        with self._lock:
            self._reload()


class _Handler(FileSystemEventHandler):
    def __init__(self, dirty: threading.Event):
        self.dirty = dirty

    def on_any_event(self, event):
        paths = [getattr(event, "src_path", ""), getattr(event, "dest_path", "")]
        if event.is_directory or any(Path(p).suffix.lower() in EXTENSIONS for p in paths if p):
            self.dirty.set()
