"""API behaviour without the worker: queue persistence, permissions, Range requests, playback flow."""
import os
import time

import pytest
from fastapi.testclient import TestClient

from server import audio, db, state
from server.app import create_app
from server.config import Config

PIN = "4321"


@pytest.fixture
def cfg(tmp_path):
    music = tmp_path / "music"
    (music / "sub").mkdir(parents=True)
    (music / "Band - Song.mp3").write_bytes(os.urandom(3000))
    (music / "sub" / "Other Band - Ballad.flac").write_bytes(os.urandom(5000))
    (music / "notes.txt").write_text("not music")
    return Config(music_path=music, host_pin=PIN, ffmpeg=tmp_path / "ffmpeg.exe", data_dir=tmp_path / "data")


def client(cfg):
    return TestClient(create_app(cfg, watch_library=False))


def guest(c, name):
    return c.post("/api/guest", json={"name": name}).json()["id"]


def song_ids(c):
    return {s["title"]: s["id"] for s in c.get("/api/library").json()["songs"]}


def fake_render(cfg, song_id, key=0, level=0, size=10000):
    """What the worker does when a render finishes."""
    rid = audio.render_id(song_id, key, level)
    path = cfg.renders_dir / f"{rid}.m4a"
    path.write_bytes(bytes(range(256)) * (size // 256))
    conn = db.connect(cfg.db_path)
    with db.tx(conn):
        conn.execute("INSERT INTO renders VALUES (?,?,?,?,?)", (song_id, key, level, str(path), time.time()))
        conn.execute("UPDATE jobs SET state='ready', progress=1, updated_at=? WHERE song_id=? AND kind='main' "
                     "AND key_shift=? AND vocal_level=?", (time.time(), song_id, key, level))
        state.refresh_statuses(conn)
    conn.close()
    return rid


def test_library_index_and_search(cfg):
    with client(cfg) as c:
        songs = c.get("/api/library").json()["songs"]
        assert sorted((s["artist"], s["title"]) for s in songs) == [("Band", "Song"), ("Other Band", "Ballad")]
        hits = c.get("/api/library", params={"q": "balad"}).json()["songs"]
        assert hits[0]["title"] == "Ballad"
        assert c.get("/api/search", params={"q": "zzz", "youtube": 1}).status_code == 503   # no YT_API_KEY


def test_queue_round_robin_jobs_and_restart(cfg):
    with client(cfg) as c:
        ids = song_ids(c)
        a, b = guest(c, "Ann"), guest(c, "Ben")
        for g, title in [(a, "Song"), (a, "Ballad"), (b, "Song")]:
            r = c.post("/api/queue", json={"song_id": ids[title], "key_shift": 0}, headers={"X-Guest-Id": g})
            assert r.status_code == 200, r.text
        r = c.post("/api/queue", json={"video_id": "abc123", "title": "YT song", "duration": 200},
                   headers={"X-Guest-Id": b})
        assert r.json()["status"] == "ready"
        up = c.get("/api/queue").json()["upcoming"]
        assert [(e["singers"][0]["name"], e["title"]) for e in up] == [
            ("Ann", "Song"), ("Ben", "Song"), ("Ann", "Ballad"), ("Ben", "YT song")]
        assert [e["status"] for e in up] == ["waiting", "waiting", "waiting", "ready"]
        jobs = c.get("/api/jobs").json()["jobs"]
        assert sorted((j["kind"], j["vocal_level"]) for j in jobs if j["song_id"] == ids["Song"]) == [
            ("main", 0), ("render", 5), ("render", 10), ("render", 15)]
    with client(cfg) as c:   # "restart": a fresh app on the same database
        up = c.get("/api/queue").json()["upcoming"]
        assert [e["title"] for e in up] == ["Song", "Song", "Ballad", "YT song"]


def test_validation(cfg):
    with client(cfg) as c:
        ids = song_ids(c)
        g = guest(c, "Ann")
        h = {"X-Guest-Id": g}
        assert c.post("/api/queue", json={"song_id": ids["Song"], "key_shift": 4}, headers=h).status_code == 400
        assert c.post("/api/queue", json={"song_id": ids["Song"], "vocal_level": 20}, headers=h).status_code == 400
        assert c.post("/api/queue", json={"song_id": "nope"}, headers=h).status_code == 404
        assert c.post("/api/queue", json={"song_id": ids["Song"]}, headers={"X-Guest-Id": "x"}).status_code == 401
        duet = c.post("/api/queue", json={"song_id": ids["Song"], "singers": [g, guest(c, "Ben")]}, headers=h)
        assert [s["name"] for s in duet.json()["singers"]] == ["Ann", "Ben"]


def test_permissions_and_host_actions(cfg):
    with client(cfg) as c:
        ids = song_ids(c)
        a, b = guest(c, "Ann"), guest(c, "Ben")
        e1 = c.post("/api/queue", json={"song_id": ids["Song"]}, headers={"X-Guest-Id": a}).json()["id"]
        e2 = c.post("/api/queue", json={"song_id": ids["Ballad"]}, headers={"X-Guest-Id": b}).json()["id"]
        assert c.delete(f"/api/queue/{e1}", headers={"X-Guest-Id": b}).status_code == 403
        assert c.post(f"/api/queue/{e2}/move", json={"index": 0}).status_code == 401
        assert c.post(f"/api/queue/{e2}/move", json={"index": 0}, headers={"X-Host-Pin": "0000"}).status_code == 401
        r = c.post(f"/api/queue/{e2}/move", json={"index": 0}, headers={"X-Host-Pin": PIN})
        assert [e["id"] for e in r.json()["upcoming"]] == [e2, e1]
        assert c.post("/api/host/login", json={"pin": PIN}).status_code == 200   # cookie works too
        assert c.post(f"/api/queue/{e2}/skip").json()["ok"]
        assert c.delete(f"/api/queue/{e1}", headers={"X-Guest-Id": a}).status_code == 200
        assert c.get("/api/queue").json()["upcoming"] == []


def test_unready_head_playback_flow_and_play_count(cfg):
    with client(cfg) as c:
        ids = song_ids(c)
        a, b = guest(c, "Ann"), guest(c, "Ben")
        e1 = c.post("/api/queue", json={"song_id": ids["Song"]}, headers={"X-Guest-Id": a}).json()["id"]
        e2 = c.post("/api/queue", json={"song_id": ids["Ballad"], "key_shift": -2}, headers={"X-Guest-Id": b}).json()["id"]
        assert c.post("/api/player/next").json()["entry"] is None   # nothing ready yet
        fake_render(cfg, ids["Ballad"], key=-2)
        nxt = c.post("/api/player/next").json()["entry"]
        assert nxt["id"] == e2 and nxt["media_url"].endswith("_k-2_v0.m4a")
        assert c.get("/api/queue").json()["upcoming"][0]["id"] == e1   # unready head stays on top
        assert c.post("/api/player/position", json={"entry_id": e2, "position": 12.5}).status_code == 200
        assert c.get("/api/now").json()["playback"]["position"] == 12.5
        assert c.post("/api/player/ended", json={"entry_id": e2}).status_code == 200
        assert c.get("/api/now").json()["playing"] is None
        assert {s["title"]: s["play_count"] for s in c.get("/api/library").json()["songs"]} == {"Song": 0, "Ballad": 1}


def test_media_range_requests(cfg):
    with client(cfg) as c:
        ids = song_ids(c)
        c.post("/api/queue", json={"song_id": ids["Song"]}, headers={"X-Guest-Id": guest(c, "Ann")})
        rid = fake_render(cfg, ids["Song"], size=10240)
        full = c.get(f"/media/{rid}.m4a")
        assert full.status_code == 200 and len(full.content) == 10240
        assert full.headers["content-type"] == "audio/mp4"
        part = c.get(f"/media/{rid}.m4a", headers={"Range": "bytes=100-199"})
        assert part.status_code == 206
        assert part.headers["content-range"] == "bytes 100-199/10240"
        assert part.content == full.content[100:200]
        assert c.get(f"/media/{rid}.m4a", headers={"Range": "bytes=10000-"}).content == full.content[10000:]
        head = c.head(f"/media/{rid}.m4a")
        assert head.status_code == 200 and head.headers["content-length"] == "10240" and head.content == b""
        assert c.get(f"/media/{audio.render_id(ids['Song'], 1, 0)}.m4a").status_code == 404
        assert c.get("/media/..%2F..%2Fkaraoke.db").status_code == 404


def test_lyrics_and_offset(cfg):
    with client(cfg) as c:
        sid = song_ids(c)["Song"]
        assert c.get(f"/api/lyrics/{sid}").status_code == 404
        (cfg.lyrics_dir / f"{sid}.json").write_text('{"source": "lrclib", "language": "en", "lines": []}')
        assert c.post(f"/api/offset/{sid}", json={"offset_ms": -300}).json() == {"offset_ms": -300}
        doc = c.get(f"/api/lyrics/{sid}").json()
        assert doc["source"] == "lrclib" and doc["offset_ms"] == -300


def test_websocket_hello_and_queue_event(cfg):
    with client(cfg) as c:
        ids = song_ids(c)
        g = guest(c, "Ann")
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"
            c.post("/api/queue", json={"song_id": ids["Song"]}, headers={"X-Guest-Id": g})
            msg = ws.receive_json()
            assert msg["type"] == "queue_changed" and msg["upcoming"][0]["title"] == "Song"


def test_library_rescan_marks_missing_and_follows_moves(cfg):
    with client(cfg) as c:
        before = song_ids(c)
        (cfg.music_path / "Band - Song.mp3").rename(cfg.music_path / "sub" / "Band - Song.mp3")
        (cfg.music_path / "sub" / "Other Band - Ballad.flac").unlink()
        stats = c.post("/api/library/rescan", headers={"X-Host-Pin": PIN}).json()
        assert stats["missing"] == 1
        assert song_ids(c) == {"Song": before["Song"]}   # same id after the move


def test_requeue_all_renders_keeps_queued_level_first(cfg):
    with client(cfg) as c:
        sid = song_ids(c)["Song"]
        eid = c.post("/api/queue", json={"song_id": sid, "vocal_level": 5}, headers={"X-Guest-Id": guest(c, "Ann")}).json()["id"]
        fake_render(cfg, sid, level=5)
        fake_render(cfg, sid, level=0)
        conn = db.connect(cfg.db_path)
        conn.execute("INSERT INTO loudness VALUES (?, 0, -3.0, 0)", (sid,))
        paths = state.requeue_all_renders(conn)
        assert len(paths) == 2
        assert conn.execute("SELECT COUNT(*) FROM renders").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM loudness").fetchone()[0] == 0
        jobs = {(r["kind"], r["vocal_level"]): r["state"] for r in conn.execute("SELECT * FROM jobs WHERE song_id=?", (sid,))}
        assert jobs[("main", 5)] == "queued" and jobs[("render", 0)] == "queued"
        assert jobs[("render", 10)] == "queued" and jobs[("render", 15)] == "queued"   # all levels for a cached key
        assert conn.execute("SELECT status FROM queue WHERE id=?", (eid,)).fetchone()[0] == "waiting"
        conn.close()


def test_migration_from_schema_1(tmp_path):   # and on through every later migration
    path = tmp_path / "old.db"
    db.init(path)
    conn = db.connect(path)
    conn.executescript("DROP TABLE loudness; ALTER TABLE songs ADD COLUMN loudness_gain_db REAL; "
                       "DROP TABLE yt_cache; DROP TABLE yt_usage; DROP TABLE wishlist; ALTER TABLE jobs DROP COLUMN batch; "
                       "PRAGMA user_version=1;")
    conn.close()
    db.init(path)
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert "loudness_gain_db" not in [r["name"] for r in conn.execute("PRAGMA table_info(songs)")]
    assert conn.execute("SELECT COUNT(*) FROM loudness").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM wishlist").fetchone()[0] == 0   # schema 3 (phase 2)
    assert db.settings(conn)["global_offset_ms"] == 0
    assert "batch" in [r["name"] for r in conn.execute("PRAGMA table_info(jobs)")]   # schema 4 (phase 4)


def test_removing_an_entry_drops_render_jobs_that_could_never_start(cfg):
    with client(cfg) as c:
        sid = song_ids(c)["Song"]
        eid = c.post("/api/queue", json={"song_id": sid, "key_shift": 2}, headers={"X-Guest-Id": guest(c, "Ann")}).json()["id"]
        c.delete(f"/api/queue/{eid}", headers={"X-Host-Pin": PIN})
        assert c.get("/api/jobs").json()["jobs"] == []


def test_requeue_all_renders_rescues_stranded_render_jobs(cfg):
    with client(cfg) as c:
        sid = song_ids(c)["Song"]
        conn = db.connect(cfg.db_path)
        for lv in (5, 10):   # render jobs whose main job is gone (the phase 1 bug)
            state._upsert_job(conn, sid, "render", -1, lv)
        state.requeue_all_renders(conn)
        jobs = sorted((r["kind"], r["vocal_level"]) for r in conn.execute("SELECT * FROM jobs WHERE key_shift=-1"))
        assert jobs == [("main", 0), ("render", 5), ("render", 10), ("render", 15)]
        conn.close()


def test_queue_missing_levels(cfg):
    with client(cfg) as c:
        sid = song_ids(c)["Song"]
        c.post("/api/queue", json={"song_id": sid}, headers={"X-Guest-Id": guest(c, "Ann")})
        fake_render(cfg, sid, key=0, level=0)
        conn = db.connect(cfg.db_path)
        conn.execute("DELETE FROM jobs WHERE kind='render'")
        assert state.queue_missing_levels(conn) == 3
        assert state.queue_missing_levels(conn) == 3   # idempotent: still the same 3 queued jobs
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE kind='render' AND state='queued'").fetchone()[0] == 3
        conn.close()
