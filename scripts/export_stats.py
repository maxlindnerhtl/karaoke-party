#!/usr/bin/env python3
"""Export party stats from the database before uninstalling (or any time): most sung songs, top singers, the full
play history, and the library's play counts and stars.

    python scripts/export_stats.py                 # -> ~/karaoke-party-stats-<date>.json + .csv (play history)
    python scripts/export_stats.py --out stats.json

Reads data/karaoke.db (or DATA_DIR from .env) read-only. Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "DATA_DIR" and value.strip():
                p = Path(value.strip())
                return p if p.is_absolute() else ROOT / p
    return ROOT / "data"


def export(db: Path) -> dict:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM guests")}
    history = []
    for r in conn.execute("SELECT p.played_at, p.singers, q.title, q.artist, q.source FROM plays p "
                          "LEFT JOIN queue q ON q.id = p.queue_id ORDER BY p.played_at"):
        history.append({"played_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["played_at"])),
                        "title": r["title"], "artist": r["artist"], "source": r["source"],
                        "singers": [names.get(g, "?") for g in json.loads(r["singers"])]})
    songs: dict[tuple, int] = {}
    singers: dict[str, int] = {}
    for h in history:
        k = (h["title"], h["artist"], h["source"])
        songs[k] = songs.get(k, 0) + 1
        for s in h["singers"]:
            singers[s] = singers.get(s, 0) + 1
    library = [dict(r) for r in conn.execute(
        "SELECT title, artist, album, play_count, starred, lyric_offset_ms FROM songs "
        "WHERE play_count > 0 OR starred = 1 OR lyric_offset_ms != 0 ORDER BY play_count DESC, title")]
    conn.close()
    return {
        "exported_at": time.strftime("%Y-%m-%d %H:%M"),
        "total_plays": len(history),
        "most_sung": [{"title": t, "artist": a, "source": s, "plays": n}
                      for (t, a, s), n in sorted(songs.items(), key=lambda kv: -kv[1])],
        "top_singers": [{"name": n, "songs": c} for n, c in sorted(singers.items(), key=lambda kv: (-kv[1], kv[0]))],
        "history": history,
        "library": library,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=Path.home() / f"karaoke-party-stats-{time.strftime('%Y-%m-%d')}.json")
    args = ap.parse_args()
    db = data_dir() / "karaoke.db"
    if not db.is_file():
        raise SystemExit(f"no database at {db}")
    stats = export(db)
    args.out.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    with args.out.with_suffix(".csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["played_at", "title", "artist", "source", "singers"])
        for h in stats["history"]:
            w.writerow([h["played_at"], h["title"], h["artist"], h["source"], " & ".join(h["singers"])])
    print(f"{stats['total_plays']} plays, {len(stats['top_singers'])} singers -> {args.out} (+ .csv)")


if __name__ == "__main__":
    main()
