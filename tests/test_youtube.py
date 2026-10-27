"""YouTube search: ranking, duration parsing, caching and quota safety (no network: mocked transport)."""
import json
import time

import httpx
import pytest

from server import db, youtube


def video(vid, title, duration, views, channel="Chan"):
    return {"id": vid, "snippet": {"title": title, "channelTitle": channel},
            "contentDetails": {"duration": duration}, "statistics": {"viewCount": str(views)}}


class FakeAPI:
    """Stands in for googleapis.com; counts calls per endpoint."""

    def __init__(self, videos=(), error=None):
        self.videos = list(videos)
        self.error = error
        self.calls = {"search": 0, "videos": 0}
        self.queries = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        kind = request.url.path.rsplit("/", 1)[-1]
        self.calls[kind] += 1
        if self.error:
            return httpx.Response(403, json={"error": {"errors": [{"reason": self.error}]}})
        if kind == "search":
            self.queries.append(request.url.params["q"])
            return httpx.Response(200, json={"items": [{"id": {"videoId": v["id"]}} for v in self.videos]})
        ids = request.url.params["id"].split(",")
        return httpx.Response(200, json={"items": [v for v in self.videos if v["id"] in ids]})


@pytest.fixture
def conn(tmp_path):
    db.init(tmp_path / "k.db")
    c = db.connect(tmp_path / "k.db")
    yield c
    c.close()


def make(api):
    return youtube.YouTube("key", transport=httpx.MockTransport(api))


@pytest.mark.parametrize("iso,sec", [("PT3M12S", 192), ("PT1H2M3S", 3723), ("PT45S", 45), ("PT4M", 240),
                                     ("P0D", 0), ("", 0), ("garbage", 0)])
def test_parse_duration(iso, sec):
    assert youtube.parse_duration(iso) == sec


def test_normalize():
    assert youtube.normalize("  Don't  Stop   Me Now!! ") == "don t stop me now"
    assert youtube.normalize("ABBA - Waterloo") == youtube.normalize("abba waterloo")


def test_rank_prefers_short_karaoke_titles_by_views():
    vids = [
        {"video_id": "a", "title": "Song (Official Video)", "duration": 200, "views": 9_000_000},
        {"video_id": "b", "title": "Song Karaoke Version", "duration": 210, "views": 50_000},
        {"video_id": "c", "title": "Song KARAOKE with lyrics", "duration": 215, "views": 800_000},
        {"video_id": "d", "title": "Song karaoke 1 hour loop", "duration": 3600, "views": 2_000_000},
        {"video_id": "e", "title": "Song Instrumental", "duration": 205, "views": 100_000},
        {"video_id": "f", "title": "Song karaoke LIVE", "duration": 0, "views": 5_000_000},
        {"video_id": "g", "title": "Song cover", "duration": 190, "views": 10},
    ]
    for v in vids:
        v["karaoke"] = youtube.is_karaoke(v)
    ranked = youtube.rank(vids)
    assert [v["video_id"] for v in ranked] == ["c", "e", "b", "a", "d"]   # live stream dropped, top 5 kept


def test_search_appends_karaoke_ranks_and_caches(conn):
    api = FakeAPI([video("x1", "Waterloo (Official)", "PT2M50S", 10_000_000),
                   video("x2", "ABBA &amp; Friends - Waterloo Karaoke", "PT2M55S", 300_000)])
    yt = make(api)
    r = yt.search(conn, "ABBA  Waterloo")
    assert api.queries == ["abba waterloo karaoke"]
    assert not r["cached"] and [v["video_id"] for v in r["results"]] == ["x2", "x1"]
    assert r["results"][0]["title"] == "ABBA & Friends - Waterloo Karaoke" and r["results"][0]["karaoke"]
    again = yt.search(conn, "abba waterloo!")   # same normalized query: served from the cache
    assert again["cached"] and again["results"] == r["results"]
    assert api.calls == {"search": 1, "videos": 1}


def test_cache_expires_after_7_days(conn):
    api = FakeAPI([video("x1", "Song karaoke", "PT3M", 5)])
    yt = make(api)
    yt.search(conn, "song")
    conn.execute("UPDATE yt_cache SET fetched_at = ?", (time.time() - youtube.CACHE_TTL - 1,))
    assert not yt.search(conn, "song")["cached"]
    assert api.calls["search"] == 2


def test_empty_result_skips_videos_call(conn):
    api = FakeAPI([])
    assert make(api).search(conn, "nothing at all")["results"] == []
    assert api.calls == {"search": 1, "videos": 0}


def test_daily_budget_stops_before_the_api(conn):
    api = FakeAPI([video("x1", "Song karaoke", "PT3M", 5)])
    yt = make(api)
    conn.execute("INSERT INTO yt_usage (day, searches) VALUES (?, ?)", (youtube.quota_day(), youtube.DAILY_SEARCHES))
    with pytest.raises(youtube.YouTubeError) as e:
        yt.search(conn, "new query")
    assert e.value.status == 429 and api.calls["search"] == 0


def test_quota_exceeded_blocks_the_rest_of_the_day(conn):
    api = FakeAPI(error="quotaExceeded")
    yt = make(api)
    with pytest.raises(youtube.YouTubeError) as e:
        yt.search(conn, "one")
    assert e.value.status == 429
    with pytest.raises(youtube.YouTubeError):
        yt.search(conn, "two")
    assert api.calls["search"] == 1   # the second query never reached the API


def test_no_api_key_is_a_clear_error(conn):
    with pytest.raises(youtube.YouTubeError) as e:
        youtube.YouTube("").search(conn, "x")
    assert e.value.status == 503


def test_cached_results_are_json(conn):
    make(FakeAPI([video("x1", "Song karaoke", "PT3M", 5)])).search(conn, "song")
    row = conn.execute("SELECT results FROM yt_cache").fetchone()
    assert json.loads(row["results"])[0]["video_id"] == "x1"
