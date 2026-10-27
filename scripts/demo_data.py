"""Demo library for screenshots and trying the app without real music: invented songs (sine tones), invented
lyrics, and ready renders, so nothing needs processing (no GPU, no network).

    uv run python scripts/demo_data.py create            # writes demo/music + demo/data (gitignored)
    MUSIC_PATH=demo/music DATA_DIR=demo/data HOST_PIN=1234 uv run python -m server --no-worker
    uv run python scripts/demo_data.py queue             # guests + queue entries, through the running server's API

Every artist, title and lyric line here is made up. Needs ffmpeg (any build; rubberband isn't used).
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import audio, db, library  # noqa: E402
from server.config import find_ffmpeg  # noqa: E402

SONGS = [   # artist, title, tone (Hz), lyric lines
    ("Nova Lane", "Paper Lanterns", 220, [
        "We hung paper lanterns on the old fire escape",
        "Counting every window that the city forgot",
        "Hold the light a little longer",
        "Hold the light until the morning comes",
    ]),
    ("The Velvet Static", "Satellite Summer", 247, [
        "Radio is humming in a borrowed car",
        "Every song is ours tonight",
        "Satellite summer, spinning us around",
        "Never coming down",
    ]),
    ("Mira & the Undertow", "Glass Harbor", 196, [
        "Down by the glass harbor the boats are asleep",
        "I wrote your name in salt upon the pier",
    ]),
    ("Cobalt Kites", "Neon Postcards", 262, ["Neon postcards from a town we never knew"]),
    ("Juniper Road", "Slow Dance Machine", 175, ["Put a coin in the slow dance machine"]),
    ("Static Bloom", "Midnight Arcade", 294, ["Meet me at the midnight arcade"]),
]
DURATION = 90.0
LEVELS = (0, 5, 10, 15)
GUESTS = ["Lena", "Jonas", "Priya", "Sam"]


def lyrics_doc(lines: list[str]) -> dict:
    """Lines spread over the song with evenly timed words, starting after an 8 s intro."""
    out, t = [], 8.0
    for text in lines * 3:
        words = text.split()
        step = 3.6 / len(words)
        timed = [{"start": round(t + i * step, 2), "end": round(t + (i + 1) * step - 0.05, 2), "text": w}
                 for i, w in enumerate(words)]
        out.append({"start": round(t, 2), "end": round(t + 3.6, 2), "text": text, "words": timed})
        t += 4.4
        if t > DURATION - 6:
            break
    return {"source": "lrclib", "language": "en", "lines": out}


def create(base: Path, ffmpeg: Path):
    music, data = base / "music", base / "data"
    if base.exists():
        shutil.rmtree(base)
    music.mkdir(parents=True)
    for artist, title, hz, _ in SONGS:
        subprocess.run([str(ffmpeg), "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"sine=frequency={hz}:duration={DURATION}", "-ac", "2", "-c:a", "libmp3lame", "-b:a", "96k",
                        "-metadata", f"artist={artist}", "-metadata", f"title={title}",
                        "-metadata", "album=Demo Songs", str(music / f"{artist} - {title}.mp3")], check=True)
    for d in ("lyrics", "renders", "logs"):
        (data / d).mkdir(parents=True)
    db.init(data / "karaoke.db")
    conn = db.connect(data / "karaoke.db")
    library.scan(conn, music)
    by_title = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM songs")}
    for artist, title, hz, lines in SONGS[:4]:   # these four are "processed"; the rest show as not prepared yet
        sid = by_title[title]
        (data / "lyrics" / f"{sid}.json").write_text(json.dumps(lyrics_doc(lines)), encoding="utf-8")
        src = music / f"{artist} - {title}.mp3"
        for level in LEVELS:
            out = data / "renders" / f"{audio.render_id(sid, 0, level)}.m4a"
            subprocess.run([str(ffmpeg), "-nostdin", "-loglevel", "error", "-i", str(src), "-c:a", "aac",
                            "-b:a", "128k", str(out)], check=True)
            with db.tx(conn):
                conn.execute("INSERT INTO renders (song_id, key_shift, vocal_level, path, created_at) VALUES (?,?,?,?,?)",
                             (sid, 0, level, str(out), time.time()))
        with db.tx(conn):
            conn.execute("UPDATE songs SET stems_ready=1, lyrics_source='lrclib' WHERE id=?", (sid,))
    conn.close()
    print(f"demo library in {base} ({len(SONGS)} songs, 4 ready to play)")


def queue(url: str):
    c = httpx.Client(base_url=url, timeout=10)
    ids = {n: c.post("/api/guest", json={"name": n}).json()["id"] for n in GUESTS}
    songs = {s["title"]: s["id"] for s in c.get("/api/library").json()["songs"]}
    plan = [("Lena", "Paper Lanterns", None), ("Jonas", "Satellite Summer", None),
            ("Priya", "Glass Harbor", "Sam"), ("Sam", "Neon Postcards", None), ("Lena", "Midnight Arcade", None)]
    for guest, title, partner in plan:
        singers = [ids[guest], ids[partner]] if partner else None
        r = c.post("/api/queue", headers={"X-Guest-Id": ids[guest]},
                   json={"song_id": songs[title], "singers": singers})
        r.raise_for_status()
    c.post("/api/queue", headers={"X-Guest-Id": ids["Jonas"]},
           json={"video_id": "demo0000000", "title": "Example Song (Karaoke Version)", "duration": 215}).raise_for_status()
    print(f"queued {len(plan) + 1} entries for {', '.join(GUESTS)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    cr = sub.add_parser("create", help="write the demo music folder and data dir")
    cr.add_argument("--dir", type=Path, default=ROOT / "demo")
    qu = sub.add_parser("queue", help="add guests and queue entries via the running server")
    qu.add_argument("--url", default="http://127.0.0.1:8765")
    args = ap.parse_args()
    if args.cmd == "create":
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            sys.exit("ffmpeg not found (set FFMPEG_PATH or put ffmpeg on PATH)")
        create(args.dir.resolve(), ffmpeg)
    else:
        queue(args.url)


if __name__ == "__main__":
    main()
