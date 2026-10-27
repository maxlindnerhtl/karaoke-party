"""Queue, jobs and playback state on top of the database. Shared by the API and the worker process."""
import json
import time
import uuid

from . import audio, db
from .queue_logic import UPCOMING, VOCAL_LEVELS, Entry, insert_index, needs_renumber, next_to_play, position_at

ACTIVE_JOB = ("separating", "lyrics", "rendering")


class QueueError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


def now() -> float:
    return time.time()


# --- guests ---------------------------------------------------------------------------------------------

def add_guest(conn, name: str) -> str:
    name = name.strip()[:40]
    if not name:
        raise QueueError(400, "name required")
    gid = uuid.uuid4().hex
    conn.execute("INSERT INTO guests (id, name, created_at) VALUES (?, ?, ?)", (gid, name, now()))
    return gid


def guest_names(conn) -> dict[str, str]:
    return {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM guests")}


# --- queue ----------------------------------------------------------------------------------------------

def upcoming(conn) -> list:
    q = ",".join("?" * len(UPCOMING))
    return conn.execute(f"SELECT * FROM queue WHERE status IN ({q}) ORDER BY position", UPCOMING).fetchall()


def _entries(rows) -> list[Entry]:
    return [Entry(r["id"], r["added_by"], r["position"], r["status"]) for r in rows]


def _renumber(conn):
    for i, r in enumerate(upcoming(conn), 1):
        conn.execute("UPDATE queue SET position=? WHERE id=?", (float(i), r["id"]))


def add_entry(conn, guest_id: str, *, song_id=None, video_id=None, title=None, artist="", duration=0.0,
              singers=None, key_shift=0, vocal_level=None) -> int:
    guest = conn.execute("SELECT * FROM guests WHERE id=?", (guest_id,)).fetchone()
    if not guest:
        raise QueueError(401, "unknown guest; POST /api/guest first")
    singers = list(dict.fromkeys(singers or [guest_id]))
    if not 1 <= len(singers) <= 2:
        raise QueueError(400, "1 singer, or 2 for a duet")
    for s in singers:
        if not conn.execute("SELECT 1 FROM guests WHERE id=?", (s,)).fetchone():
            raise QueueError(400, f"unknown singer {s}")
    if vocal_level is None:
        vocal_level = db.get_setting(conn, "default_vocal_level")
    if vocal_level not in VOCAL_LEVELS:
        raise QueueError(400, f"vocal_level must be one of {VOCAL_LEVELS}")

    if song_id:
        song = conn.execute("SELECT * FROM songs WHERE id=? AND missing=0", (song_id,)).fetchone()
        if not song:
            raise QueueError(404, "song not in library")
        if not -3 <= key_shift <= 3:
            raise QueueError(400, "key_shift must be -3..+3")
        source, title, artist, duration, status = "library", song["title"], song["artist"], song["duration"], "waiting"
    elif video_id:
        if not title:
            raise QueueError(400, "title required for YouTube entries")
        source, key_shift, status = "youtube", 0, "ready"
    else:
        raise QueueError(400, "song_id or video_id required")

    with db.tx(conn):
        t = now()
        if guest["first_queued_at"] is None:
            conn.execute("UPDATE guests SET first_queued_at=? WHERE id=?", (t, guest_id))
        order = [r["id"] for r in conn.execute(
            "SELECT id FROM guests WHERE first_queued_at IS NOT NULL ORDER BY first_queued_at, created_at")]
        ups = _entries(upcoming(conn))
        pos = position_at(ups, insert_index(ups, guest_id, order))
        cur = conn.execute(
            "INSERT INTO queue (source, song_id, video_id, title, artist, duration, singers, key_shift, vocal_level,"
            " status, added_by, position, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (source, song_id, video_id, title, artist, duration, json.dumps(singers), key_shift, vocal_level,
             status, guest_id, pos, t, t))
        if needs_renumber(_entries(upcoming(conn))):
            _renumber(conn)
        if source == "library":
            ensure_jobs(conn, song_id, key_shift, vocal_level)
        refresh_statuses(conn)
        return cur.lastrowid


def move_entry(conn, entry_id: int, index: int):
    """Host reorder: put the entry at `index` among the upcoming entries."""
    with db.tx(conn):
        ups = _entries(upcoming(conn))
        if entry_id not in [e.id for e in ups]:
            raise QueueError(404, "entry not in the upcoming queue")
        index = max(0, min(index, len(ups) - 1))
        pos = position_at(ups, index, exclude_id=entry_id)
        conn.execute("UPDATE queue SET position=?, updated_at=? WHERE id=?", (pos, now(), entry_id))
        if needs_renumber(_entries(upcoming(conn))):
            _renumber(conn)


def remove_entry(conn, entry_id: int, guest_id: str | None, is_host: bool):
    from .queue_logic import can_remove
    row = conn.execute("SELECT * FROM queue WHERE id=?", (entry_id,)).fetchone()
    if not row or row["status"] not in UPCOMING:
        raise QueueError(404, "entry not in the upcoming queue")
    if not can_remove(row["added_by"], guest_id, is_host):
        raise QueueError(403, "guests can only remove their own entries")
    with db.tx(conn):
        conn.execute("DELETE FROM queue WHERE id=?", (entry_id,))
        _drop_orphan_jobs(conn)


def _drop_orphan_jobs(conn):
    """Queued main jobs that no upcoming entry needs any more are dropped (stems already made stay cached), unless
    the nightly batch queued them.
    Then so are render jobs that can no longer start: a render job waits for a render of its song + key, so
    without one and without a pending main job for that key it would wait forever. Queueing the song again
    recreates both (ensure_jobs)."""
    q = ",".join("?" * len(UPCOMING))
    conn.execute(
        f"DELETE FROM jobs WHERE state='queued' AND kind='main' AND batch=0 AND NOT EXISTS (SELECT 1 FROM queue WHERE "
        f"queue.song_id=jobs.song_id AND queue.key_shift=jobs.key_shift AND queue.vocal_level=jobs.vocal_level "
        f"AND queue.status IN ({q}))", UPCOMING)
    conn.execute(
        "DELETE FROM jobs WHERE kind='render' AND state='queued' "
        "AND NOT EXISTS (SELECT 1 FROM renders r WHERE r.song_id=jobs.song_id AND r.key_shift=jobs.key_shift) "
        "AND NOT EXISTS (SELECT 1 FROM jobs m WHERE m.kind='main' AND m.song_id=jobs.song_id "
        "AND m.key_shift=jobs.key_shift AND m.state NOT IN ('ready', 'failed'))")


def skip_entry(conn, entry_id: int):
    """Host skip: the playing entry ends as skipped; an upcoming one leaves the queue as skipped."""
    with db.tx(conn):
        row = conn.execute("SELECT * FROM queue WHERE id=?", (entry_id,)).fetchone()
        if not row or row["status"] not in UPCOMING + ("playing",):
            raise QueueError(404, "entry is not playing or upcoming")
        conn.execute("UPDATE queue SET status='skipped', ended_at=?, updated_at=? WHERE id=?", (now(), now(), entry_id))
        if row["status"] == "playing":
            conn.execute("UPDATE playback SET queue_id=NULL, state='idle', position=0, updated_at=? WHERE id=1", (now(),))
        _drop_orphan_jobs(conn)
    return row["status"] == "playing"


# --- jobs -----------------------------------------------------------------------------------------------

def has_render(conn, song_id, key_shift, vocal_level) -> bool:
    return conn.execute("SELECT 1 FROM renders WHERE song_id=? AND key_shift=? AND vocal_level=?",
                        (song_id, key_shift, vocal_level)).fetchone() is not None


def _upsert_job(conn, song_id, kind, key_shift, vocal_level, batch=False):
    t = now()
    conn.execute(
        "INSERT INTO jobs (song_id, kind, key_shift, vocal_level, state, created_at, updated_at, batch) "
        "VALUES (?,?,?,?,'queued',?,?,?) ON CONFLICT (song_id, kind, key_shift, vocal_level) DO UPDATE SET "
        "state='queued', progress=0, error=NULL, updated_at=excluded.updated_at WHERE jobs.state='failed'",
        (song_id, kind, key_shift, vocal_level, t, t, int(batch)))


def ensure_jobs(conn, song_id: str, key_shift: int, vocal_level: int):
    """Main job (separate -> lyrics -> render the queued level) unless that render exists, plus background
    render jobs for the other vocal levels of the same key."""
    if not has_render(conn, song_id, key_shift, vocal_level):
        _upsert_job(conn, song_id, "main", key_shift, vocal_level)
    for level in VOCAL_LEVELS:
        if level != vocal_level and not has_render(conn, song_id, key_shift, level):
            _upsert_job(conn, song_id, "render", key_shift, level)


def refresh_statuses(conn):
    """Derive each upcoming library entry's status from renders and its main job."""
    rows = conn.execute("SELECT * FROM queue WHERE source='library' AND status IN "
                        "('waiting','preparing','ready','failed')").fetchall()
    for r in rows:
        key = (r["song_id"], r["key_shift"], r["vocal_level"])
        if has_render(conn, *key):
            status, error = "ready", None
        else:
            job = conn.execute("SELECT state, error FROM jobs WHERE song_id=? AND kind='main' AND key_shift=? "
                               "AND vocal_level=?", key).fetchone()
            if job is None or job["state"] == "ready":   # e.g. render file deleted: process again
                ensure_jobs(conn, *key)
                status, error = "waiting", None
            elif job["state"] == "failed":
                status, error = "failed", job["error"]
            elif job["state"] in ACTIVE_JOB:
                status, error = "preparing", None
            else:
                status, error = "waiting", None
        if (status, error) != (r["status"], r["error"]):
            conn.execute("UPDATE queue SET status=?, error=?, updated_at=? WHERE id=?", (status, error, now(), r["id"]))


def requeue_all_renders(conn) -> list[str]:
    """Drop every cached render and loudness gain and queue the same renders again (e.g. after a loudness
    change). Per song + key, one render goes through a main job so the render loop's "queued level first" rule
    holds; stems and lyrics are kept. Returns the render file paths for the caller to delete."""
    rows = conn.execute("SELECT song_id, key_shift, vocal_level, path FROM renders").fetchall()
    groups: dict[tuple, set[int]] = {}
    for r in rows:
        groups.setdefault((r["song_id"], r["key_shift"]), set()).add(r["vocal_level"])
    # queued render jobs are renders in waiting too (possibly stranded without their main job): include them
    for r in conn.execute("SELECT song_id, key_shift, vocal_level FROM jobs WHERE kind='render' AND state='queued'"):
        groups.setdefault((r["song_id"], r["key_shift"]), set()).add(r["vocal_level"])
    for levels in groups.values():   # every cached key gets all vocal levels, as for a queued song
        levels.update(VOCAL_LEVELS)
    requeue = ("INSERT INTO jobs (song_id, kind, key_shift, vocal_level, state, created_at, updated_at) "
               "VALUES (?,?,?,?,'queued',?,?) ON CONFLICT (song_id, kind, key_shift, vocal_level) DO UPDATE SET "
               "state='queued', progress=0, error=NULL, updated_at=excluded.updated_at")
    with db.tx(conn):
        conn.execute("DELETE FROM renders")
        conn.execute("DELETE FROM loudness")
        for (sid, key), levels in groups.items():
            mains = {r["vocal_level"] for r in conn.execute(
                "SELECT vocal_level FROM jobs WHERE song_id=? AND key_shift=? AND kind='main'", (sid, key))}
            main_levels = [lv for lv in levels if lv in mains] or [min(levels)]
            for lv in sorted(levels):
                kind = "main" if lv in main_levels else "render"
                conn.execute(requeue, (sid, kind, key, lv, now(), now()))
                if kind == "main":   # rendered by its main job; a render job for the same level would repeat it
                    conn.execute("DELETE FROM jobs WHERE kind='render' AND song_id=? AND key_shift=? AND vocal_level=?",
                                 (sid, key, lv))
        refresh_statuses(conn)
    return [r["path"] for r in rows]


def queue_missing_levels(conn) -> int:
    """Render jobs for vocal levels missing from a song + key that already has a render."""
    n = 0
    with db.tx(conn):
        for r in conn.execute("SELECT DISTINCT song_id, key_shift FROM renders").fetchall():
            for lv in VOCAL_LEVELS:
                if not has_render(conn, r["song_id"], r["key_shift"], lv):
                    conn.execute("DELETE FROM jobs WHERE song_id=? AND key_shift=? AND vocal_level=? AND state='ready'",
                                 (r["song_id"], r["key_shift"], lv))
                    _upsert_job(conn, r["song_id"], "render", r["key_shift"], lv)
                    n += 1
    return n


def retry_entry(conn, entry_id: int):
    with db.tx(conn):
        r = conn.execute("SELECT * FROM queue WHERE id=? AND status='failed'", (entry_id,)).fetchone()
        if not r:
            raise QueueError(404, "no failed entry with that id")
        ensure_jobs(conn, r["song_id"], r["key_shift"], r["vocal_level"])
        refresh_statuses(conn)


# --- playback (reported by the TV) ----------------------------------------------------------------------

def playing(conn):
    return conn.execute("SELECT * FROM queue WHERE status='playing' ORDER BY started_at DESC LIMIT 1").fetchone()


def start_next(conn):
    """Pick the next entry to play (head if ready, else the first ready one) and mark it playing."""
    with db.tx(conn):
        cur = playing(conn)
        if cur:
            return cur
        nxt = next_to_play(_entries(upcoming(conn)))
        if not nxt:
            conn.execute("UPDATE playback SET queue_id=NULL, state='idle', position=0, updated_at=? WHERE id=1", (now(),))
            return None
        t = now()
        conn.execute("UPDATE queue SET status='playing', started_at=?, updated_at=? WHERE id=?", (t, t, nxt.id))
        conn.execute("UPDATE playback SET queue_id=?, state='playing', position=0, updated_at=? WHERE id=1", (nxt.id, t))
        return conn.execute("SELECT * FROM queue WHERE id=?", (nxt.id,)).fetchone()


def report(conn, event: str, entry_id: int | None, position: float | None):
    """TV playback events: started / position / paused / ended / skip."""
    with db.tx(conn):
        cur = playing(conn)
        if cur is None or (entry_id is not None and cur["id"] != entry_id):
            raise QueueError(409, "that entry is not playing")
        t = now()
        if event in ("started", "position", "paused"):
            state = "paused" if event == "paused" else "playing"
            conn.execute("UPDATE playback SET position=?, state=?, updated_at=? WHERE id=1", (position or 0, state, t))
            return cur
        status = "done" if event == "ended" else "skipped"
        conn.execute("UPDATE queue SET status=?, ended_at=?, updated_at=? WHERE id=?", (status, t, t, cur["id"]))
        conn.execute("UPDATE playback SET queue_id=NULL, state='idle', position=0, updated_at=? WHERE id=1", (t,))
        if status == "done":
            conn.execute("INSERT INTO plays (queue_id, song_id, video_id, singers, played_at) VALUES (?,?,?,?,?)",
                         (cur["id"], cur["song_id"], cur["video_id"], cur["singers"], t))
            if cur["song_id"]:
                conn.execute("UPDATE songs SET play_count = play_count + 1 WHERE id=?", (cur["song_id"],))
        return cur


# --- wishlist + stats -----------------------------------------------------------------------------------

def add_wish(conn, query: str, reason: str, guest_id: str | None) -> int:
    """A song nobody could find: no library hit and YouTube had nothing, or the guest rejected every result.
    The same query again only refreshes the existing row."""
    query = " ".join(query.split())[:200]
    if not query:
        raise QueueError(400, "query required")
    with db.tx(conn):
        row = conn.execute("SELECT id FROM wishlist WHERE lower(query)=lower(?)", (query,)).fetchone()
        if row:
            conn.execute("UPDATE wishlist SET reason=?, guest_id=?, created_at=? WHERE id=?",
                         (reason, guest_id, now(), row["id"]))
            return row["id"]
        return conn.execute("INSERT INTO wishlist (query, reason, guest_id, created_at) VALUES (?,?,?,?)",
                            (query, reason, guest_id, now())).lastrowid


def wishlist(conn) -> list[dict]:
    names = guest_names(conn)
    return [{"id": r["id"], "query": r["query"], "reason": r["reason"], "guest": names.get(r["guest_id"]),
             "created_at": r["created_at"]}
            for r in conn.execute("SELECT * FROM wishlist ORDER BY created_at DESC")]


def stats(conn, limit: int = 10) -> dict:
    """Most sung songs and top singers, from finished plays (skipped entries don't count)."""
    names = guest_names(conn)
    songs = conn.execute(
        "SELECT q.title, q.artist, q.source, COUNT(*) AS plays FROM plays p JOIN queue q ON q.id = p.queue_id "
        "GROUP BY COALESCE(p.song_id, p.video_id) ORDER BY plays DESC, MAX(p.played_at) DESC LIMIT ?",
        (limit,)).fetchall()
    singers: dict[str, dict] = {}
    for r in conn.execute("SELECT singers FROM plays"):
        for gid in json.loads(r["singers"]):
            name = names.get(gid, "?")
            s = singers.setdefault(name.casefold(), {"name": name, "songs": 0})
            s["songs"] += 1
    top = sorted(singers.values(), key=lambda s: (-s["songs"], s["name"].casefold()))[:limit]
    return {"songs": [dict(r) for r in songs], "singers": top,
            "total_plays": conn.execute("SELECT COUNT(*) FROM plays").fetchone()[0]}


# --- snapshots for the API / WebSocket ------------------------------------------------------------------

def entry_view(conn, r, names=None) -> dict:
    names = names if names is not None else guest_names(conn)
    d = {k: r[k] for k in ("id", "source", "song_id", "video_id", "title", "artist", "duration", "key_shift",
                           "vocal_level", "status", "error", "added_by", "position")}
    d["singers"] = [{"id": s, "name": names.get(s, "?")} for s in json.loads(r["singers"])]
    if r["source"] == "library":
        if r["status"] in ("ready", "playing"):
            rid = audio.render_id(r["song_id"], r["key_shift"], r["vocal_level"])
            d["render_id"] = rid
            d["media_url"] = f"/media/{rid}.m4a"
            d["lyrics_url"] = f"/api/lyrics/{r['song_id']}"
            d["vocal_levels_ready"] = [lv for lv in VOCAL_LEVELS if has_render(conn, r["song_id"], r["key_shift"], lv)]
        job = conn.execute("SELECT state, progress FROM jobs WHERE song_id=? AND kind='main' AND key_shift=? "
                           "AND vocal_level=?", (r["song_id"], r["key_shift"], r["vocal_level"])).fetchone()
        if job and r["status"] in ("waiting", "preparing"):
            d["job"] = {"state": job["state"], "progress": round(job["progress"], 3)}
    return d


def queue_view(conn) -> dict:
    names = guest_names(conn)
    cur = playing(conn)
    pb = conn.execute("SELECT * FROM playback WHERE id=1").fetchone()
    return {
        "playing": entry_view(conn, cur, names) if cur else None,
        "playback": {"state": pb["state"], "position": pb["position"], "updated_at": pb["updated_at"]},
        "upcoming": [entry_view(conn, r, names) for r in upcoming(conn)],
    }


def job_view(r) -> dict:
    return {k: r[k] for k in ("id", "song_id", "kind", "key_shift", "vocal_level", "state", "progress", "error",
                              "updated_at")}
