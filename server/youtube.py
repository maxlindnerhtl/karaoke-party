"""YouTube search (SPEC section 6, YouTube search): Data API v3, cached per normalized query, quota-safe.

search.list costs 100 of the default 10,000 daily units, so it runs only on explicit submit, results are cached
for 7 days, and a daily budget stops us before the API does. Durations and view counts come from videos.list
(1 unit). Nothing is ever downloaded: entries are played in the YouTube app on the TV.
"""
import html
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import db

log = logging.getLogger("youtube")

API = "https://www.googleapis.com/youtube/v3"
CACHE_TTL = 7 * 24 * 3600
MAX_DURATION = 8 * 60
TOP = 5
SEARCH_RESULTS = 15
DAILY_SEARCHES = 95          # of ~100 that 10,000 units allow; leaves room for manual testing in the console
KARAOKE_WORDS = ("karaoke", "instrumental")

try:
    from zoneinfo import ZoneInfo
    QUOTA_TZ = ZoneInfo("America/Los_Angeles")   # the API quota resets at midnight Pacific time
except Exception:
    QUOTA_TZ = timezone(timedelta(hours=-8))


class YouTubeError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


def normalize(q: str) -> str:
    """Cache key: lowercase, punctuation dropped, whitespace collapsed."""
    return " ".join(re.sub(r"[^\w\s]", " ", q.lower()).split())


def parse_duration(iso: str) -> int:
    """ISO 8601 duration (PT1H2M3S) -> seconds; 0 for live streams (P0D) or anything unparsable."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def is_karaoke(v: dict) -> bool:
    return any(w in v["title"].lower() for w in KARAOKE_WORDS) and 0 < v["duration"] <= MAX_DURATION


def rank(videos: list[dict]) -> list[dict]:
    """Auto-pick order: karaoke/instrumental titles of at most 8 min first, then the rest; each by views.
    Live streams (no duration) are dropped. The first one is the pick; the top 5 are kept for "Not this one?"."""
    videos = [v for v in videos if v["duration"] > 0]
    return sorted(videos, key=lambda v: (not is_karaoke(v), -v["views"]))[:TOP]


def quota_day(t: float | None = None) -> str:
    return datetime.fromtimestamp(t or time.time(), QUOTA_TZ).strftime("%Y-%m-%d")


class YouTube:
    def __init__(self, api_key: str, transport: httpx.BaseTransport | None = None):
        self.api_key = api_key
        self.http = httpx.Client(timeout=10, transport=transport)

    def search(self, conn, query: str) -> dict:
        """{query, cached, results: [{video_id, title, channel, duration, views, karaoke}]} (results[0] = pick)."""
        key = normalize(query)
        if not key:
            raise YouTubeError(400, "empty query")
        row = conn.execute("SELECT results, fetched_at FROM yt_cache WHERE query=?", (key,)).fetchone()
        if row and time.time() - row["fetched_at"] < CACHE_TTL:
            return {"query": key, "cached": True, "results": json.loads(row["results"])}
        if not self.api_key:
            raise YouTubeError(503, "YouTube search is not set up (YT_API_KEY missing in .env)")
        self._spend(conn)
        results = rank(self._fetch(conn, key))
        with db.tx(conn):
            conn.execute("INSERT OR REPLACE INTO yt_cache (query, results, fetched_at) VALUES (?,?,?)",
                         (key, json.dumps(results), time.time()))
            conn.execute("DELETE FROM yt_cache WHERE fetched_at < ?", (time.time() - CACHE_TTL,))
        return {"query": key, "cached": False, "results": results}

    def _spend(self, conn):
        day = quota_day()
        with db.tx(conn):
            row = conn.execute("SELECT searches, exhausted FROM yt_usage WHERE day=?", (day,)).fetchone()
            if row and (row["exhausted"] or row["searches"] >= DAILY_SEARCHES):
                raise YouTubeError(429, "YouTube search limit reached for today; library songs still work")
            conn.execute("INSERT INTO yt_usage (day, searches) VALUES (?, 1) "
                         "ON CONFLICT (day) DO UPDATE SET searches = searches + 1", (day,))

    def _get(self, conn, path: str, params: dict) -> dict:
        try:
            r = self.http.get(f"{API}/{path}", params={**params, "key": self.api_key})
        except httpx.HTTPError as e:
            raise YouTubeError(502, f"YouTube unreachable: {e}") from None
        if r.status_code == 200:
            return r.json()
        reasons = {e.get("reason") for e in r.json().get("error", {}).get("errors", [])} if r.content else set()
        log.warning("YouTube %s -> %s %s", path, r.status_code, reasons or r.text[:200])
        if reasons & {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}:
            with db.tx(conn):
                conn.execute("UPDATE yt_usage SET exhausted=1 WHERE day=?", (quota_day(),))
            raise YouTubeError(429, "YouTube search limit reached for today; library songs still work")
        raise YouTubeError(502, f"YouTube API error {r.status_code}")

    def _fetch(self, conn, key: str) -> list[dict]:
        found = self._get(conn, "search", {"part": "snippet", "q": f"{key} karaoke", "type": "video",
                                           "maxResults": SEARCH_RESULTS})
        ids = [it["id"]["videoId"] for it in found.get("items", []) if it.get("id", {}).get("videoId")]
        if not ids:
            return []
        details = self._get(conn, "videos", {"part": "snippet,contentDetails,statistics", "id": ",".join(ids)})
        out = []
        for it in details.get("items", []):
            sn = it.get("snippet", {})
            out.append({
                "video_id": it["id"],
                "title": html.unescape(sn.get("title", "")),
                "channel": html.unescape(sn.get("channelTitle", "")),
                "duration": parse_duration(it.get("contentDetails", {}).get("duration", "")),
                "views": int(it.get("statistics", {}).get("viewCount", 0)),
            })
        for v in out:
            v["karaoke"] = is_karaoke(v)
        return out
