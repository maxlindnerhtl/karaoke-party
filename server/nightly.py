"""Nightly batch (SPEC section 6): at the host's configured time, prepare starred and most-played songs that aren't
cached yet, so they are ready instantly at the next party.

"Cached" = the render guests get by default (key 0, default vocal level) exists. The batch queues only that main job
(separate -> lyrics -> render); the other vocal levels follow when a guest queues the song. Batch jobs are marked
(`jobs.batch`), so the orphan cleanup keeps them, and the worker runs them after every job a queue entry needs.
"""
import logging
from datetime import date, datetime, timedelta

from . import db, state

log = logging.getLogger("nightly")

TOP_PLAYED = 20                  # most-played songs considered, besides all starred ones
WINDOW = timedelta(minutes=60)   # a batch only starts within this long after its time (the PC may be off at that time)


def due(now: datetime, batch_time: str | None, last_run: str | None) -> date | None:
    """The date of the batch slot to run now, or None. A slot runs once, and only within WINDOW after its time."""
    if not batch_time:
        return None
    hh, mm = (int(x) for x in batch_time.split(":"))
    for day in (now.date(), now.date() - timedelta(days=1)):   # yesterday's slot, for a time shortly before midnight
        slot = datetime.combine(day, datetime.min.time()).replace(hour=hh, minute=mm)
        if slot <= now < slot + WINDOW and last_run != day.isoformat():
            return day
    return None


def candidates(conn) -> list[str]:
    """Starred songs, then the most played, that have no default render yet (and no pending or failed main job)."""
    level = db.get_setting(conn, "default_vocal_level")
    rows = conn.execute(
        "SELECT id FROM songs s WHERE missing=0 AND (starred=1 OR id IN "
        "  (SELECT id FROM songs WHERE play_count > 0 AND missing=0 ORDER BY play_count DESC LIMIT ?)) "
        "AND NOT EXISTS (SELECT 1 FROM renders r WHERE r.song_id=s.id AND r.key_shift=0 AND r.vocal_level=?) "
        "AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.song_id=s.id AND j.kind='main' AND j.key_shift=0 "
        "  AND j.vocal_level=? AND j.state != 'ready') "
        "ORDER BY starred DESC, play_count DESC, title COLLATE NOCASE", (TOP_PLAYED, level, level)).fetchall()
    return [r["id"] for r in rows]


def run_batch(conn) -> int:
    """Queue the batch jobs. Returns how many songs were queued."""
    level = db.get_setting(conn, "default_vocal_level")
    with db.tx(conn):
        ids = candidates(conn)
        for sid in ids:
            # a finished job whose render file is gone: queue it again
            conn.execute("DELETE FROM jobs WHERE song_id=? AND kind='main' AND key_shift=0 AND vocal_level=? "
                         "AND state='ready'", (sid, level))
            state._upsert_job(conn, sid, "main", 0, level, batch=True)
    return len(ids)


def tick(conn, now: datetime | None = None) -> int | None:
    """Called periodically by the API process. Runs the batch if a slot is due; returns the number queued."""
    s = db.settings(conn)
    slot = due(now or datetime.now(), s["nightly_batch_time"], db.get_setting(conn, "nightly_batch_last"))
    if slot is None:
        return None
    db.set_settings(conn, {"nightly_batch_last": slot.isoformat()})
    n = run_batch(conn)
    log.info("nightly batch (%s %s): %d songs queued", slot, s["nightly_batch_time"], n)
    return n
