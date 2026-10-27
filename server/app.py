"""HTTP + WebSocket API (SPEC section 6, API). Run via `uv run python -m server`."""
import asyncio
import io
import json
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import segno
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import audio, db, netinfo, nightly, state
from .config import ROOT, Config
from .library import Library
from .queue_logic import VOCAL_LEVELS
from .youtube import YouTube, YouTubeError

log = logging.getLogger("api")

POLL_INTERVAL = 0.5
POSITION_BROADCAST_INTERVAL = 1.0
NIGHTLY_CHECK_INTERVAL = 30.0
GOOD_MATCH = 90          # library score (rapidfuzz WRatio) from which YouTube is not searched unless forced
REACTIONS = ("👏", "🔥", "❤️", "😂", "🎤")
REACTION_INTERVAL = 1.0  # per guest


class Hub:
    """WebSocket fan-out. Changes made by the worker are picked up by polling the jobs/queue tables."""

    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    async def send_all(self, msg: dict):
        data = json.dumps(msg)
        for ws in list(self.clients):
            try:
                await ws.send_text(data)
            except Exception:
                self.clients.discard(ws)

    def publish(self, msg: dict):
        """Thread-safe: callable from request handlers (thread pool) and the library watcher."""
        if self.loop and self.clients:
            asyncio.run_coroutine_threadsafe(self.send_all(msg), self.loop)


class GuestIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class QueueIn(BaseModel):
    song_id: str | None = None
    video_id: str | None = None
    title: str | None = None
    artist: str = ""
    duration: float = 0.0
    singers: list[str] | None = None
    key_shift: int = 0
    vocal_level: int | None = None


class MoveIn(BaseModel):
    index: int


class PlayerIn(BaseModel):
    entry_id: int | None = None
    position: float | None = None


class OffsetIn(BaseModel):
    offset_ms: int = Field(ge=-10000, le=10000)


class PinIn(BaseModel):
    pin: str


class WishIn(BaseModel):
    query: str = Field(min_length=1, max_length=200)


class ReactionIn(BaseModel):
    emoji: str


class StarIn(BaseModel):
    starred: bool


class SettingsIn(BaseModel):
    default_vocal_level: int | None = None
    nightly_batch_time: str | None = None     # "HH:MM", or "" for off
    global_offset_ms: int | None = Field(None, ge=-10000, le=10000)


def phone_url(request: Request) -> str:
    return f"http://{request.headers.get('host') or request.url.netloc}/"


def create_app(cfg: Config, *, watch_library: bool = True, yt_transport=None) -> FastAPI:
    db.init(cfg.db_path)
    cfg.ensure_dirs()
    hub = Hub()
    yt = YouTube(cfg.yt_api_key, transport=yt_transport)
    library = Library(cfg.db_path, cfg.music_path, on_change=lambda s: hub.publish({"type": "library_changed", **s}))

    def conn():
        c = db.connect(cfg.db_path)
        try:
            yield c
        finally:
            c.close()

    def is_host(request: Request) -> bool:
        pin = request.headers.get("x-host-pin") or request.cookies.get("host_pin") or ""
        return secrets.compare_digest(pin.encode(), cfg.host_pin.encode())

    def require_host(request: Request):
        if not is_host(request):
            raise HTTPException(401, "host PIN required")

    seen = {"queue": 0.0}   # newest queue.updated_at already broadcast, so the poller doesn't repeat it

    def queue_changed(c):
        seen["queue"] = max(seen["queue"], c.execute("SELECT COALESCE(MAX(updated_at), 0) FROM queue").fetchone()[0])
        hub.publish({"type": "queue_changed", **state.queue_view(c)})

    async def poll_changes():
        """Broadcast job progress and queue changes the worker made (it writes only to the database)."""
        c = db.connect(cfg.db_path)
        last_job = c.execute("SELECT COALESCE(MAX(updated_at), 0) FROM jobs").fetchone()[0]
        seen["queue"] = c.execute("SELECT COALESCE(MAX(updated_at), 0) FROM queue").fetchone()[0]
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            try:
                jobs = c.execute("SELECT * FROM jobs WHERE updated_at > ? ORDER BY updated_at", (last_job,)).fetchall()
                for j in jobs:
                    await hub.send_all({"type": "job_progress", "job": state.job_view(j)})
                    last_job = max(last_job, j["updated_at"])
                mq = c.execute("SELECT COALESCE(MAX(updated_at), 0) FROM queue").fetchone()[0]
                if mq > seen["queue"]:
                    seen["queue"] = mq
                    await hub.send_all({"type": "queue_changed", **state.queue_view(c)})
                if any(j["state"] == "ready" for j in jobs):
                    library.touch()
            except Exception:
                log.exception("change poll failed")

    async def nightly_loop():
        """Starts the nightly batch at its time (the worker picks the jobs up; poll_changes broadcasts progress)."""
        c = db.connect(cfg.db_path)
        while True:
            try:
                await asyncio.to_thread(nightly.tick, c)
            except Exception:
                log.exception("nightly batch check failed")
            await asyncio.sleep(NIGHTLY_CHECK_INTERVAL)

    net = {}   # LAN IP + MAC, looked up on the first /api/server_info

    def lookup_net():
        ip = netinfo.lan_ip()
        net["ip"] = ip
        net["mac"] = netinfo.normalize_mac(cfg.pc_mac) if cfg.pc_mac else netinfo.mac_for_ip(ip)
        log.info("LAN IP %s, MAC %s%s", ip, net["mac"], " (PC_MAC)" if cfg.pc_mac else "")

    @asynccontextmanager
    async def lifespan(_app):
        hub.loop = asyncio.get_running_loop()
        if watch_library:
            await asyncio.to_thread(library.start)
        else:
            library.rescan()
        tasks = [asyncio.create_task(poll_changes()), asyncio.create_task(nightly_loop())]
        yield
        for t in tasks:
            t.cancel()
        library.stop()

    app = FastAPI(title="Karaoke", lifespan=lifespan)
    app.state.library = library
    app.state.hub = hub

    @app.exception_handler(state.QueueError)
    async def queue_error(_req, e: state.QueueError):
        return JSONResponse({"detail": str(e)}, status_code=e.status)

    # --- library --------------------------------------------------------------------------------------

    @app.get("/api/library")
    def get_library(q: str = "", sort: str = "title", limit: int = 200):
        songs = library.search(q, limit=limit) if q else library.songs(sort)[:limit]
        return {"songs": songs}

    @app.get("/api/search")
    def search(q: str, youtube: int = 0, force: int = 0, x_guest_id: str | None = Header(None), c=Depends(conn)):
        """Library hits always. YouTube only with youtube=1 (explicit submit) and only if the library has no good
        match, unless force=1 ("Search YouTube anyway"). An empty YouTube result goes to the wishlist."""
        hits = library.search(q, limit=20)
        good = bool(hits) and hits[0]["score"] >= GOOD_MATCH
        out = {"library": hits, "good_match": good, "youtube": None}
        if youtube and (force or not good):
            try:
                out["youtube"] = yt.search(c, q)
            except YouTubeError as e:
                raise HTTPException(e.status, str(e)) from None
            if not out["youtube"]["results"]:
                state.add_wish(c, q, "no_results", x_guest_id)
                hub.publish({"type": "wishlist_changed"})
                out["youtube"]["wishlisted"] = True
        return out

    @app.post("/api/library/rescan", dependencies=[Depends(require_host)])
    def rescan():
        return library.rescan()

    @app.post("/api/library/{song_id}/star", dependencies=[Depends(require_host)])
    def star(song_id: str, body: StarIn, c=Depends(conn)):
        if c.execute("UPDATE songs SET starred=? WHERE id=?", (int(body.starred), song_id)).rowcount == 0:
            raise HTTPException(404, "unknown song")
        library.touch()
        hub.publish({"type": "library_changed", "song_id": song_id, "starred": body.starred})
        return {"starred": body.starred}

    # --- wishlist, reactions, stats -------------------------------------------------------------------

    @app.post("/api/wishlist")
    def wish(body: WishIn, x_guest_id: str | None = Header(None), c=Depends(conn)):
        """The guest rejected every YouTube result for this search."""
        wid = state.add_wish(c, body.query, "rejected", x_guest_id)
        hub.publish({"type": "wishlist_changed"})
        return {"id": wid}

    @app.get("/api/wishlist", dependencies=[Depends(require_host)])
    def get_wishlist(c=Depends(conn)):
        return {"wishlist": state.wishlist(c)}

    @app.delete("/api/wishlist/{wish_id}", dependencies=[Depends(require_host)])
    def delete_wish(wish_id: int, c=Depends(conn)):
        c.execute("DELETE FROM wishlist WHERE id=?", (wish_id,))
        hub.publish({"type": "wishlist_changed"})
        return {"ok": True}

    last_reaction: dict[str, float] = {}

    @app.post("/api/reaction")
    def reaction(body: ReactionIn, x_guest_id: str = Header(...), c=Depends(conn)):
        if body.emoji not in REACTIONS:
            raise HTTPException(400, f"emoji must be one of {' '.join(REACTIONS)}")
        guest = c.execute("SELECT name FROM guests WHERE id=?", (x_guest_id,)).fetchone()
        if not guest:
            raise HTTPException(401, "unknown guest; POST /api/guest first")
        t = time.monotonic()
        if t - last_reaction.get(x_guest_id, -REACTION_INTERVAL) < REACTION_INTERVAL:
            raise HTTPException(429, "one reaction per second")
        last_reaction[x_guest_id] = t
        hub.publish({"type": "reaction", "emoji": body.emoji, "name": guest["name"]})
        return {"ok": True}

    @app.get("/api/stats")
    def get_stats(c=Depends(conn)):
        return state.stats(c)

    # --- guests + queue -------------------------------------------------------------------------------

    @app.post("/api/guest")
    def new_guest(body: GuestIn, c=Depends(conn)):
        return {"id": state.add_guest(c, body.name), "name": body.name.strip()}

    @app.get("/api/guests")
    def guests(c=Depends(conn)):
        """Names for the duet partner picker."""
        rows = c.execute("SELECT id, name FROM guests ORDER BY first_queued_at IS NULL, name COLLATE NOCASE")
        return {"guests": [dict(r) for r in rows]}

    @app.get("/api/queue")
    def get_queue(c=Depends(conn)):
        return state.queue_view(c)

    @app.post("/api/queue")
    def add_to_queue(body: QueueIn, x_guest_id: str = Header(...), c=Depends(conn)):
        eid = state.add_entry(c, x_guest_id, **body.model_dump())
        queue_changed(c)
        row = c.execute("SELECT * FROM queue WHERE id=?", (eid,)).fetchone()
        return state.entry_view(c, row)

    @app.delete("/api/queue/{entry_id}")
    def delete_from_queue(entry_id: int, request: Request, x_guest_id: str | None = Header(None), c=Depends(conn)):
        state.remove_entry(c, entry_id, x_guest_id, is_host(request))
        queue_changed(c)
        return {"ok": True}

    @app.post("/api/queue/{entry_id}/move", dependencies=[Depends(require_host)])
    def move(entry_id: int, body: MoveIn, c=Depends(conn)):
        state.move_entry(c, entry_id, body.index)
        queue_changed(c)
        return state.queue_view(c)

    @app.post("/api/queue/{entry_id}/skip", dependencies=[Depends(require_host)])
    def skip(entry_id: int, c=Depends(conn)):
        was_playing = state.skip_entry(c, entry_id)
        if was_playing:
            hub.publish({"type": "skip", "entry_id": entry_id})
        queue_changed(c)
        return {"ok": True, "was_playing": was_playing}

    @app.post("/api/queue/{entry_id}/retry", dependencies=[Depends(require_host)])
    def retry(entry_id: int, c=Depends(conn)):
        state.retry_entry(c, entry_id)
        queue_changed(c)
        return {"ok": True}

    # --- playback (TV) --------------------------------------------------------------------------------

    @app.get("/api/now")
    def now_playing(c=Depends(conn)):
        v = state.queue_view(c)
        return {"playing": v["playing"], "playback": v["playback"]}

    last_position = {"t": 0.0}

    @app.post("/api/player/{event}")
    def player(event: str, body: PlayerIn = Body(default_factory=PlayerIn), c=Depends(conn)):
        if event == "next":
            row = state.start_next(c)
            queue_changed(c)
            entry = state.entry_view(c, row) if row else None
            hub.publish({"type": "now_playing", "entry": entry})
            return {"entry": entry}
        if event not in ("started", "position", "paused", "ended", "skip"):
            raise HTTPException(404, "events: next, started, position, paused, ended, skip")
        row = state.report(c, event, body.entry_id, body.position)
        if event == "position":
            if time.monotonic() - last_position["t"] >= POSITION_BROADCAST_INTERVAL:
                last_position["t"] = time.monotonic()
                hub.publish({"type": "position", "entry_id": row["id"], "position": body.position})
        else:
            hub.publish({"type": "now_playing", "entry": None if event in ("ended", "skip") else state.entry_view(c, row)})
            queue_changed(c)
            if event == "ended":
                library.touch()   # play counts changed
        return {"ok": True}

    # --- media + lyrics -------------------------------------------------------------------------------

    @app.api_route("/media/{name}", methods=["GET", "HEAD"])
    def media(name: str, c=Depends(conn)):
        if not name.endswith(".m4a") or not (key := audio.parse_render_id(name[:-4])):
            raise HTTPException(404, "unknown render")
        row = c.execute("SELECT path FROM renders WHERE song_id=? AND key_shift=? AND vocal_level=?", key).fetchone()
        path = cfg.renders_dir / name
        if not row or not path.is_file():
            raise HTTPException(404, "render not ready")
        # FileResponse answers Range requests with 206 Partial Content
        return FileResponse(path, media_type="audio/mp4")

    @app.get("/api/lyrics/{song_id}")
    def get_lyrics(song_id: str, c=Depends(conn)):
        song = c.execute("SELECT lyric_offset_ms FROM songs WHERE id=?", (song_id,)).fetchone()
        path = cfg.lyrics_dir / f"{song_id}.json"
        if not song or not path.is_file() or not path.resolve().parent == cfg.lyrics_dir.resolve():
            raise HTTPException(404, "no lyrics yet")
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["offset_ms"] = song["lyric_offset_ms"]
        doc["global_offset_ms"] = db.get_setting(c, "global_offset_ms")   # the TV adds both
        return doc

    @app.post("/api/offset/{song_id}")
    def set_offset(song_id: str, body: OffsetIn, c=Depends(conn)):
        if c.execute("UPDATE songs SET lyric_offset_ms=? WHERE id=?", (body.offset_ms, song_id)).rowcount == 0:
            raise HTTPException(404, "unknown song")
        hub.publish({"type": "offset_changed", "song_id": song_id, "offset_ms": body.offset_ms})
        return {"offset_ms": body.offset_ms}

    # --- jobs (debug/host) ----------------------------------------------------------------------------

    @app.get("/api/jobs")
    def jobs(c=Depends(conn)):
        rows = c.execute("SELECT * FROM jobs ORDER BY updated_at DESC LIMIT 100").fetchall()
        return {"jobs": [state.job_view(r) for r in rows], "device": db.get_setting(c, "processing_device")}

    @app.post("/api/host/login")
    def host_login(body: PinIn, response: Response):
        if not secrets.compare_digest(body.pin.encode(), cfg.host_pin.encode()):
            raise HTTPException(401, "wrong PIN")
        response.set_cookie("host_pin", body.pin, httponly=True, samesite="strict", max_age=30 * 24 * 3600)
        return {"ok": True}

    @app.post("/api/host/logout")
    def host_logout(response: Response):
        response.delete_cookie("host_pin")
        return {"ok": True}

    @app.get("/api/host/check", dependencies=[Depends(require_host)])
    def host_check():
        return {"ok": True}

    # --- host settings + stats on the TV --------------------------------------------------------------

    @app.get("/api/settings")
    def get_settings(c=Depends(conn)):
        return db.settings(c)

    @app.post("/api/host/settings", dependencies=[Depends(require_host)])
    def set_settings(body: SettingsIn, c=Depends(conn)):
        values = body.model_dump(exclude_none=True)
        if "default_vocal_level" in values and values["default_vocal_level"] not in VOCAL_LEVELS:
            raise HTTPException(400, f"default_vocal_level must be one of {VOCAL_LEVELS}")
        if "nightly_batch_time" in values:
            t = values["nightly_batch_time"].strip()
            if t and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
                raise HTTPException(400, "nightly_batch_time must be HH:MM, or empty for off")
            values["nightly_batch_time"] = t or None
        db.set_settings(c, values)
        out = db.settings(c)
        hub.publish({"type": "settings_changed", "settings": out})
        return out

    @app.post("/api/host/show_stats", dependencies=[Depends(require_host)])
    def show_stats(c=Depends(conn)):
        hub.publish({"type": "show_stats", **state.stats(c)})
        return {"ok": True}

    # --- WebSocket ------------------------------------------------------------------------------------

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        hub.clients.add(websocket)
        c = db.connect(cfg.db_path)
        try:
            await websocket.send_text(json.dumps({"type": "hello", **state.queue_view(c)}))
        finally:
            c.close()
        try:
            while True:
                await websocket.receive_text()   # clients only listen; keeps the socket open
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(websocket)

    # --- TV lobby -------------------------------------------------------------------------------------

    @app.get("/api/server_info")
    def server_info():
        """For the TV: the PC's MAC address (Wake-on-LAN from the offline screen) and LAN IP."""
        if "mac" not in net:
            lookup_net()
        return {"mac": net["mac"], "ip": net["ip"], "port": cfg.port}

    @app.get("/qr.png")
    def qr(request: Request):
        """QR code for the phone page. The TV loads this via the PC's LAN IP, so the Host header is the URL to show."""
        buf = io.BytesIO()
        segno.make(phone_url(request), error="m").save(buf, kind="png", scale=1, border=2)
        return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-cache"})

    # --- pages ----------------------------------------------------------------------------------------

    web = ROOT / "web"
    app.mount("/static", StaticFiles(directory=web), name="static")

    @app.get("/debug")
    def debug_page():
        return FileResponse(web / "debug.html")

    @app.get("/host")
    def host_page():
        return FileResponse(web / "host.html")

    @app.get("/")
    def index():
        return FileResponse(web / "index.html")

    return app
