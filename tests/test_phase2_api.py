"""Phase 2 API: search with YouTube, wishlist, reactions, stars, settings, stats, pages, live updates."""
import time

import httpx
from fastapi.testclient import TestClient

from server.app import create_app
from server.config import Config
from tests.test_api import PIN, cfg, guest, song_ids  # noqa: F401  (cfg is a fixture)
from tests.test_youtube import FakeAPI, video


def yt_client(cfg, api):
    cfg = Config(**{**cfg.__dict__, "yt_api_key": "key"})
    return TestClient(create_app(cfg, watch_library=False, yt_transport=httpx.MockTransport(api)))


def test_search_uses_youtube_only_on_submit_without_a_good_library_match(cfg):
    api = FakeAPI([video("v1", "Unknown Song Karaoke", "PT3M", 1000)])
    with yt_client(cfg, api) as c:
        h = {"X-Guest-Id": guest(c, "Ann")}
        typing = c.get("/api/search", params={"q": "unknown song"}).json()
        assert typing["youtube"] is None and api.calls["search"] == 0          # as-you-type: never YouTube
        good = c.get("/api/search", params={"q": "band song", "youtube": 1}, headers=h).json()
        assert good["good_match"] and good["youtube"] is None and api.calls["search"] == 0
        forced = c.get("/api/search", params={"q": "band song", "youtube": 1, "force": 1}, headers=h).json()
        assert forced["youtube"]["results"][0]["video_id"] == "v1" and api.calls["search"] == 1
        r = c.get("/api/search", params={"q": "unknown song", "youtube": 1}, headers=h).json()
        assert r["youtube"]["results"][0]["video_id"] == "v1" and api.calls["search"] == 2
        r = c.get("/api/search", params={"q": "Unknown  Song", "youtube": 1}, headers=h).json()
        assert r["youtube"]["cached"] and api.calls["search"] == 2


def test_youtube_nothing_found_goes_to_wishlist(cfg):
    with yt_client(cfg, FakeAPI([])) as c:
        h = {"X-Guest-Id": guest(c, "Ann")}
        with c.websocket_connect("/ws") as ws:
            ws.receive_json()
            r = c.get("/api/search", params={"q": "obscure tune", "youtube": 1}, headers=h).json()
            assert r["youtube"]["results"] == [] and r["youtube"]["wishlisted"]
            assert ws.receive_json()["type"] == "wishlist_changed"
        assert c.get("/api/wishlist").status_code == 401
        w = c.get("/api/wishlist", headers={"X-Host-Pin": PIN}).json()["wishlist"]
        assert [(x["query"], x["reason"], x["guest"]) for x in w] == [("obscure tune", "no_results", "Ann")]


def test_rejected_results_go_to_wishlist_once_and_host_deletes(cfg):
    with TestClient(create_app(cfg, watch_library=False)) as c:
        h = {"X-Guest-Id": guest(c, "Ann")}
        c.post("/api/wishlist", json={"query": "Some Song"}, headers=h)
        c.post("/api/wishlist", json={"query": "some song"}, headers=h)
        hp = {"X-Host-Pin": PIN}
        w = c.get("/api/wishlist", headers=hp).json()["wishlist"]
        assert len(w) == 1 and w[0]["reason"] == "rejected"
        c.delete(f"/api/wishlist/{w[0]['id']}", headers=hp)
        assert c.get("/api/wishlist", headers=hp).json()["wishlist"] == []


def test_queue_a_youtube_result(cfg):
    with yt_client(cfg, FakeAPI([video("v1", "Unknown Song Karaoke", "PT3M5S", 1000)])) as c:
        h = {"X-Guest-Id": guest(c, "Ann")}
        v = c.get("/api/search", params={"q": "unknown song", "youtube": 1}, headers=h).json()["youtube"]["results"][0]
        e = c.post("/api/queue", json={"video_id": v["video_id"], "title": v["title"], "duration": v["duration"]},
                   headers=h).json()
        assert e["source"] == "youtube" and e["status"] == "ready" and e["duration"] == 185


def test_reactions_are_rate_limited_and_broadcast(cfg):
    with TestClient(create_app(cfg, watch_library=False)) as c:
        a, b = guest(c, "Ann"), guest(c, "Ben")
        with c.websocket_connect("/ws") as ws:
            ws.receive_json()
            assert c.post("/api/reaction", json={"emoji": "🔥"}, headers={"X-Guest-Id": a}).status_code == 200
            assert ws.receive_json() == {"type": "reaction", "emoji": "🔥", "name": "Ann"}
            assert c.post("/api/reaction", json={"emoji": "👏"}, headers={"X-Guest-Id": a}).status_code == 429
            assert c.post("/api/reaction", json={"emoji": "👏"}, headers={"X-Guest-Id": b}).status_code == 200
            time.sleep(1.05)
            assert c.post("/api/reaction", json={"emoji": "❤️"}, headers={"X-Guest-Id": a}).status_code == 200
        assert c.post("/api/reaction", json={"emoji": "💩"}, headers={"X-Guest-Id": b}).status_code == 400
        assert c.post("/api/reaction", json={"emoji": "🔥"}, headers={"X-Guest-Id": "nobody"}).status_code == 401


def test_star_settings_and_global_offset(cfg):
    with TestClient(create_app(cfg, watch_library=False)) as c:
        sid = song_ids(c)["Song"]
        hp = {"X-Host-Pin": PIN}
        assert c.post(f"/api/library/{sid}/star", json={"starred": True}).status_code == 401
        assert c.post(f"/api/library/{sid}/star", json={"starred": True}, headers=hp).json() == {"starred": True}
        assert {s["title"]: s["starred"] for s in c.get("/api/library").json()["songs"]}["Song"] == 1

        assert c.get("/api/settings").json() == {"default_vocal_level": 0, "nightly_batch_time": None, "global_offset_ms": 0}
        assert c.post("/api/host/settings", json={"default_vocal_level": 5}).status_code == 401
        assert c.post("/api/host/settings", json={"default_vocal_level": 7}, headers=hp).status_code == 400
        assert c.post("/api/host/settings", json={"nightly_batch_time": "25:00"}, headers=hp).status_code == 400
        s = c.post("/api/host/settings", json={"default_vocal_level": 10, "nightly_batch_time": "03:30",
                                                "global_offset_ms": -250}, headers=hp).json()
        assert s == {"default_vocal_level": 10, "nightly_batch_time": "03:30", "global_offset_ms": -250}
        assert c.post("/api/host/settings", json={"nightly_batch_time": ""}, headers=hp).json()["nightly_batch_time"] is None

        e = c.post("/api/queue", json={"song_id": sid}, headers={"X-Guest-Id": guest(c, "Ann")}).json()
        assert e["vocal_level"] == 10   # the default applies to new entries
        (cfg.lyrics_dir / f"{sid}.json").write_text('{"source": "auto", "language": "en", "lines": []}')
        doc = c.get(f"/api/lyrics/{sid}").json()
        assert doc["offset_ms"] == 0 and doc["global_offset_ms"] == -250


def test_stats_and_show_on_tv(cfg):
    with TestClient(create_app(cfg, watch_library=False)) as c:
        from tests.test_api import fake_render
        ids = song_ids(c)
        a, b = guest(c, "Ann"), guest(c, "Ben")
        for g, singers in [(a, None), (b, [b, a])]:
            body = {"song_id": ids["Song"]} | ({"singers": singers} if singers else {})
            c.post("/api/queue", json=body, headers={"X-Guest-Id": g})
        fake_render(cfg, ids["Song"])
        for _ in range(2):
            eid = c.post("/api/player/next").json()["entry"]["id"]
            c.post("/api/player/ended", json={"entry_id": eid})
        st = c.get("/api/stats").json()
        assert st["total_plays"] == 2
        assert st["songs"] == [{"title": "Song", "artist": "Band", "source": "library", "plays": 2}]
        assert st["singers"] == [{"name": "Ann", "songs": 2}, {"name": "Ben", "songs": 1}]
        with c.websocket_connect("/ws") as ws:
            ws.receive_json()
            assert c.post("/api/host/show_stats").status_code == 401
            c.post("/api/host/show_stats", headers={"X-Host-Pin": PIN})
            msg = ws.receive_json()
            assert msg["type"] == "show_stats" and msg["singers"][0]["name"] == "Ann"


def test_two_phones_see_each_others_entries_live(cfg):
    with TestClient(create_app(cfg, watch_library=False)) as c:
        ids = song_ids(c)
        a, b = guest(c, "Ann"), guest(c, "Ben")
        with c.websocket_connect("/ws") as phone_a, c.websocket_connect("/ws") as phone_b:
            phone_a.receive_json(), phone_b.receive_json()
            c.post("/api/queue", json={"song_id": ids["Song"]}, headers={"X-Guest-Id": a})
            assert [e["title"] for e in phone_b.receive_json()["upcoming"]] == ["Song"]
            assert [e["title"] for e in phone_a.receive_json()["upcoming"]] == ["Song"]
            c.post("/api/queue", json={"song_id": ids["Ballad"]}, headers={"X-Guest-Id": b})
            assert [e["singers"][0]["name"] for e in phone_a.receive_json()["upcoming"]] == ["Ann", "Ben"]


def test_pages_host_check_and_guests(cfg):
    with TestClient(create_app(cfg, watch_library=False)) as c:
        assert "Karaoke" in c.get("/").text and c.get("/").headers["content-type"].startswith("text/html")
        assert "Karaoke Host" in c.get("/host").text
        for f in ("style.css", "common.js", "app.js", "host.js"):
            assert c.get(f"/static/{f}").status_code == 200
        assert c.get("/api/host/check").status_code == 401
        assert c.post("/api/host/login", json={"pin": "nope"}).status_code == 401
        assert c.post("/api/host/login", json={"pin": PIN}).status_code == 200
        assert c.get("/api/host/check").status_code == 200   # cookie
        c.post("/api/host/logout")
        assert c.get("/api/host/check").status_code == 401
        guest(c, "Zoe"), guest(c, "ann")
        assert [g["name"] for g in c.get("/api/guests").json()["guests"]] == ["ann", "Zoe"]
