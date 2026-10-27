"""Phase 4 server pieces: nightly batch, schema 4 migration, server info / MAC, tray status line."""
from datetime import date, datetime

from fastapi.testclient import TestClient

from server import db, nightly, state
from server.app import create_app
from server.config import Config
from server.netinfo import normalize_mac
from server.runner import processing_status
from tests.test_api import PIN, cfg, fake_render, guest, song_ids  # noqa: F401  (cfg is a fixture)


def test_due_runs_each_slot_once_and_only_in_its_window():
    t = "03:00"
    assert nightly.due(datetime(2026, 10, 27, 2, 59), t, None) is None
    assert nightly.due(datetime(2026, 10, 27, 3, 0), t, None) == date(2026, 10, 27)
    assert nightly.due(datetime(2026, 10, 27, 3, 59), t, "2026-10-26") == date(2026, 10, 27)
    assert nightly.due(datetime(2026, 10, 27, 3, 30), t, "2026-10-27") is None       # already ran
    assert nightly.due(datetime(2026, 10, 27, 12, 0), t, None) is None               # PC was off at 03:00
    assert nightly.due(datetime(2026, 10, 27, 3, 0), None, None) is None             # off
    # a slot shortly before midnight still runs after midnight, for the day it belongs to
    assert nightly.due(datetime(2026, 10, 28, 0, 10), "23:30", "2026-10-26") == date(2026, 10, 27)


def test_batch_queues_starred_and_played_songs_without_a_default_render(cfg):
    with TestClient(create_app(cfg, watch_library=False)) as c:
        ids = song_ids(c)
        conn = db.connect(cfg.db_path)
        assert nightly.candidates(conn) == []                                  # nothing starred or played yet
        c.post(f"/api/library/{ids['Song']}/star", json={"starred": True}, headers={"X-Host-Pin": PIN})
        conn.execute("UPDATE songs SET play_count=3 WHERE id=?", (ids["Ballad"],))
        assert nightly.candidates(conn) == [ids["Song"], ids["Ballad"]]        # starred first
        fake_render(cfg, ids["Ballad"])                                        # already cached
        assert nightly.candidates(conn) == [ids["Song"]]

        db.set_settings(conn, {"nightly_batch_time": "03:00"})
        assert nightly.tick(conn, datetime(2026, 10, 27, 3, 1)) == 1
        jobs = conn.execute("SELECT song_id, kind, key_shift, vocal_level, batch FROM jobs").fetchall()
        assert [tuple(j) for j in jobs] == [(ids["Song"], "main", 0, 0, 1)]    # only the default render
        assert nightly.tick(conn, datetime(2026, 10, 27, 3, 2)) is None        # once per slot
        assert db.get_setting(conn, "nightly_batch_last") == "2026-10-27"


def test_orphan_cleanup_keeps_batch_jobs(cfg):
    with TestClient(create_app(cfg, watch_library=False)) as c:
        ids = song_ids(c)
        h = {"X-Guest-Id": guest(c, "Ann")}
        conn = db.connect(cfg.db_path)
        with db.tx(conn):
            state._upsert_job(conn, ids["Song"], "main", 0, 0, batch=True)
        e = c.post("/api/queue", json={"song_id": ids["Ballad"]}, headers=h).json()
        c.delete(f"/api/queue/{e['id']}", headers=h)                           # runs the orphan cleanup
        left = conn.execute("SELECT song_id FROM jobs WHERE kind='main'").fetchall()
        assert [r["song_id"] for r in left] == [ids["Song"]]


def test_schema_3_database_migrates_to_4(tmp_path):
    path = tmp_path / "k.db"
    db.init(path)
    conn = db.connect(path)
    conn.execute("ALTER TABLE jobs DROP COLUMN batch")
    conn.execute("PRAGMA user_version=3")
    conn.close()
    db.init(path)
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    assert "batch" in [r["name"] for r in conn.execute("PRAGMA table_info(jobs)")]


def test_server_info_uses_pc_mac_from_env(cfg):
    cfg = Config(**{**cfg.__dict__, "pc_mac": "02-00-5E-10-00-01"})
    with TestClient(create_app(cfg, watch_library=False)) as c:
        info = c.get("/api/server_info").json()
        assert info["mac"] == "02:00:5e:10:00:01" and info["port"] == cfg.port


def test_normalize_mac():
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aabb.ccdd.eeff") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("not a mac") is None
    assert normalize_mac("aa:bb:cc:dd:ee") is None


def test_processing_status_line(tmp_path):
    path = tmp_path / "k.db"
    db.init(path)
    conn = db.connect(path)
    assert processing_status(conn, True) == "Processing: idle"
    assert processing_status(conn, False).startswith("Worker not running")
    with db.tx(conn):
        state._upsert_job(conn, "a", "main", 0, 0)
        state._upsert_job(conn, "b", "main", 0, 0)
        conn.execute("UPDATE jobs SET state='separating' WHERE song_id='a'")
    assert processing_status(conn, True) == "Processing: 1 active, 1 queued"
