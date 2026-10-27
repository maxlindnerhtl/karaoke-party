"""SQLite access. The API process and the worker process share one database file (WAL mode)."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE songs (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    title TEXT NOT NULL,
    artist TEXT NOT NULL DEFAULT '',
    album TEXT NOT NULL DEFAULT '',
    duration REAL NOT NULL DEFAULT 0,
    play_count INTEGER NOT NULL DEFAULT 0,
    starred INTEGER NOT NULL DEFAULT 0,
    missing INTEGER NOT NULL DEFAULT 0,
    lyric_offset_ms INTEGER NOT NULL DEFAULT 0,
    stems_ready INTEGER NOT NULL DEFAULT 0,
    lyrics_source TEXT,
    added_at REAL NOT NULL
);
CREATE INDEX songs_path ON songs(path);

CREATE TABLE guests (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    first_queued_at REAL
);

CREATE TABLE queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL CHECK (source IN ('library', 'youtube')),
    song_id TEXT,
    video_id TEXT,
    title TEXT NOT NULL,
    artist TEXT NOT NULL DEFAULT '',
    duration REAL NOT NULL DEFAULT 0,
    singers TEXT NOT NULL,              -- JSON list of guest ids (1, or 2 for a duet)
    key_shift INTEGER NOT NULL DEFAULT 0,
    vocal_level INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN
        ('waiting', 'preparing', 'ready', 'playing', 'done', 'skipped', 'failed')),
    error TEXT,
    added_by TEXT NOT NULL,
    position REAL NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    started_at REAL,
    ended_at REAL
);
CREATE INDEX queue_status ON queue(status, position);

CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('main', 'render')),
    key_shift INTEGER NOT NULL,
    vocal_level INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN
        ('queued', 'separating', 'lyrics', 'rendering', 'ready', 'failed')),
    progress REAL NOT NULL DEFAULT 0,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    batch INTEGER NOT NULL DEFAULT 0,   -- queued by the nightly batch (kept when no queue entry needs it)
    UNIQUE (song_id, kind, key_shift, vocal_level)
);
CREATE INDEX jobs_state ON jobs(state);

CREATE TABLE renders (
    song_id TEXT NOT NULL,
    key_shift INTEGER NOT NULL,
    vocal_level INTEGER NOT NULL,
    path TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (song_id, key_shift, vocal_level)
);

CREATE TABLE playback (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    queue_id INTEGER,
    position REAL NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'idle',
    updated_at REAL NOT NULL
);

CREATE TABLE plays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id INTEGER,
    song_id TEXT,
    video_id TEXT,
    singers TEXT NOT NULL,
    played_at REAL NOT NULL
);

CREATE TABLE loudness (
    song_id TEXT NOT NULL,
    key_shift INTEGER NOT NULL,
    gain_db REAL NOT NULL,              -- to TARGET_LUFS (server/audio.py); the render's limiter handles peaks
    measured_at REAL NOT NULL,
    PRIMARY KEY (song_id, key_shift)
);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE yt_cache (
    query TEXT PRIMARY KEY,             -- normalized query (server/youtube.py)
    results TEXT NOT NULL,              -- JSON list, ranked (first = auto-pick)
    fetched_at REAL NOT NULL
);

CREATE TABLE yt_usage (
    day TEXT PRIMARY KEY,               -- quota day (Pacific time)
    searches INTEGER NOT NULL DEFAULT 0,
    exhausted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE wishlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('no_results', 'rejected')),
    guest_id TEXT,
    created_at REAL NOT NULL
);
"""

DEFAULT_SETTINGS = {"default_vocal_level": 0, "nightly_batch_time": None, "global_offset_ms": 0}
# internal state in the settings table, not part of GET /api/settings: nightly_batch_last (server/nightly.py),
# processing_device ({device, name}, written by the worker at start, shown on the host page via GET /api/jobs)


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            # executescript commits any open transaction first, so the script carries its own BEGIN/COMMIT.
            settings = "".join(
                f"INSERT INTO settings (key, value) VALUES ('{k}', '{json.dumps(v)}');"
                for k, v in DEFAULT_SETTINGS.items())
            conn.executescript(
                f"BEGIN IMMEDIATE;{SCHEMA}"
                f"INSERT INTO playback (id, updated_at) VALUES (1, {time.time()});{settings}"
                f"PRAGMA user_version={SCHEMA_VERSION};COMMIT;")
        elif version < SCHEMA_VERSION:
            migrate(conn, version)
        elif version > SCHEMA_VERSION:
            raise SystemExit(f"{path} has schema {version}, newer than this code ({SCHEMA_VERSION})")
    finally:
        conn.close()


MIGRATIONS = {
    # 1 -> 2: loudness gain per (song, key) instead of per song; old per-song gains (-14 LUFS target) are dropped
    2: "CREATE TABLE loudness (song_id TEXT NOT NULL, key_shift INTEGER NOT NULL, gain_db REAL NOT NULL, "
       "measured_at REAL NOT NULL, PRIMARY KEY (song_id, key_shift));"
       "ALTER TABLE songs DROP COLUMN loudness_gain_db;",
    # 2 -> 3 (phase 2): YouTube search cache + quota counter, wishlist. New settings fall back to DEFAULT_SETTINGS.
    3: SCHEMA[SCHEMA.index("CREATE TABLE yt_cache"):],
    # 3 -> 4 (phase 4): nightly batch jobs are marked so the orphan cleanup keeps them
    4: "ALTER TABLE jobs ADD COLUMN batch INTEGER NOT NULL DEFAULT 0;",
}


def migrate(conn: sqlite3.Connection, version: int):
    for v in range(version + 1, SCHEMA_VERSION + 1):
        conn.executescript(f"BEGIN IMMEDIATE;{MIGRATIONS[v]}PRAGMA user_version={v};COMMIT;")


@contextmanager
def tx(conn: sqlite3.Connection):
    """An explicit write transaction (BEGIN IMMEDIATE takes the write lock up front)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def get_setting(conn, key):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else DEFAULT_SETTINGS.get(key)


def settings(conn) -> dict:
    """The host settings (DEFAULT_SETTINGS keys); internal state stored in the same table is left out."""
    out = dict(DEFAULT_SETTINGS)
    out.update({r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT key, value FROM settings")
                if r["key"] in DEFAULT_SETTINGS})
    return out


def set_settings(conn, values: dict):
    with tx(conn):
        for k, v in values.items():
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, json.dumps(v)))
