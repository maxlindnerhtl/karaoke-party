"""Lyrics: LRC parsing, LRCLIB lookup, and turning WhisperX output into the stored JSON (SPEC section 6.2).

Stored format: {source, language, lines: [{start, end, text, words: [{start, end, text}]}]}
"""
import re
from dataclasses import dataclass

import httpx
from rapidfuzz import fuzz

LRCLIB = "https://lrclib.net"
USER_AGENT = "karaoke-party/0.1 (private LAN karaoke for house parties; python-httpx)"
DURATION_TOLERANCE = 3.0

_STAMP = re.compile(r"\[(\d+):(\d{1,2}(?:[.:]\d{1,3})?)\]")
_OFFSET = re.compile(r"^\[offset:\s*([+-]?\d+)\]", re.I)


@dataclass
class LrcLine:
    start: float
    text: str


def parse_lrc(text: str) -> list[LrcLine]:
    """Parse synced LRC. Handles several stamps per line, [mm:ss], [mm:ss.xx], [mm:ss.xxx], the [offset:ms]
    tag, and metadata tags. Empty lines are kept (they mark instrumental breaks)."""
    offset = 0.0
    lines = []
    for raw in text.splitlines():
        raw = raw.strip()
        m = _OFFSET.match(raw)
        if m:
            offset = int(m[1]) / 1000   # positive offset = lyrics earlier
            continue
        stamps = _STAMP.findall(raw)
        if not stamps:
            continue
        words = _STAMP.sub("", raw).strip()
        for mm, ss in stamps:
            lines.append(LrcLine(int(mm) * 60 + float(ss.replace(":", ".")), words))
    for line in lines:
        line.start = max(0.0, round(line.start - offset, 3))
    lines.sort(key=lambda l: l.start)
    return lines


def lrc_segments(lines: list[LrcLine], duration: float) -> list[dict]:
    """LRC lines with text as WhisperX segments; each ends where the next line (or a break) starts."""
    segs = []
    for i, line in enumerate(lines):
        if not line.text:
            continue
        end = lines[i + 1].start if i + 1 < len(lines) else min(line.start + 8.0, duration or line.start + 8.0)
        if end <= line.start:
            end = line.start + 0.5
        segs.append({"start": line.start, "end": end, "text": line.text})
    return segs


# --- LRCLIB ---------------------------------------------------------------------------------------------

def _score(hit: dict, artist: str, title: str) -> float:
    return (fuzz.WRatio(title.lower(), (hit.get("trackName") or "").lower()) * 0.6
            + fuzz.WRatio(artist.lower(), (hit.get("artistName") or "").lower()) * 0.4)


def lrclib_lookup(artist: str, title: str, album: str, duration: float,
                  client: httpx.Client | None = None) -> dict | None:
    """Best LRCLIB record for a song, or None. Any non-200 from /api/get counts as a miss (LRCLIB answers
    503 on some misses), then /api/search picks the best fuzzy title/artist match within +-3 s."""
    own = client is None
    client = client or httpx.Client(base_url=LRCLIB, headers={"User-Agent": USER_AGENT}, timeout=20)
    try:
        params = {"artist_name": artist, "track_name": title, "duration": round(duration)}
        if album:
            params["album_name"] = album
        try:
            r = client.get("/api/get", params=params)
            if r.status_code == 200 and (r.json().get("syncedLyrics") or r.json().get("instrumental")):
                return r.json()
        except httpx.HTTPError:
            pass
        r = client.get("/api/search", params={"artist_name": artist, "track_name": title})
        r.raise_for_status()
        hits = [h for h in r.json() if abs((h.get("duration") or 0) - duration) <= DURATION_TOLERANCE]
        if not hits:
            return None
        # prefer synced lyrics, then the best name match, then the closest duration
        return max(hits, key=lambda h: (bool(h.get("syncedLyrics")), _score(h, artist, title),
                                        -abs((h.get("duration") or 0) - duration)))
    finally:
        if own:
            client.close()


# --- WhisperX output -> stored lines ---------------------------------------------------------------------

def _clean_words(words: list[dict], lo: float, hi: float) -> list[dict]:
    """Words as {start, end, text}; words WhisperX couldn't time get times interpolated from neighbours."""
    out = [{"start": w.get("start"), "end": w.get("end"), "text": (w.get("word") or w.get("text") or "").strip()}
           for w in words]
    out = [w for w in out if w["text"]]
    n = len(out)
    for i, w in enumerate(out):
        if w["start"] is None:
            prev_end = next((out[j]["end"] for j in range(i - 1, -1, -1) if out[j]["end"] is not None), lo)
            nxt = next(((j, out[j]["start"]) for j in range(i + 1, n) if out[j]["start"] is not None), (n, hi))
            gap = max(nxt[1] - prev_end, 0.0)
            step = gap / (nxt[0] - i + 1)
            w["start"] = round(prev_end + step * 0.5, 3)
            w["end"] = round(w["start"] + step, 3)
        if w["end"] is None or w["end"] < w["start"]:
            w["end"] = w["start"]
    return out


def lines_from_lrc(lrc: list[dict], aligned: list[dict]) -> list[dict]:
    """Map aligned words back onto the LRC lines (WhisperX may merge or split segments), by word count."""
    words = [w for seg in aligned for w in seg.get("words", [])]
    lines, k = [], 0
    for seg in lrc:
        n = len(seg["text"].split())
        chunk = _clean_words(words[k:k + n], seg["start"], seg["end"])
        k += n
        if len(chunk) != n:   # alignment came up short: spread the line's words evenly
            step = (seg["end"] - seg["start"]) / max(n, 1)
            chunk = [{"start": round(seg["start"] + i * step, 3), "end": round(seg["start"] + (i + 1) * step, 3),
                      "text": t} for i, t in enumerate(seg["text"].split())]
        lines.append({"start": chunk[0]["start"] if chunk else seg["start"],
                      "end": chunk[-1]["end"] if chunk else seg["end"],
                      "text": seg["text"], "words": chunk})
    return lines


MAX_WORDS = 8
MIN_WORDS_AT_PUNCT = 4
PAUSE = 0.6


def lines_from_auto(aligned: list[dict]) -> list[dict]:
    """Split auto-transcribed segments (often ~15 s long) into display lines: at pauses > 0.6 s, after
    sentence punctuation once a line has 4+ words, and after at most 8 words."""
    lines = []
    for seg in aligned:
        words = _clean_words(seg.get("words", []), seg.get("start", 0.0), seg.get("end", 0.0))
        cur: list[dict] = []
        for w in words:
            if cur and (w["start"] - cur[-1]["end"] > PAUSE or len(cur) >= MAX_WORDS):
                lines.append(cur)
                cur = []
            cur.append(w)
            if len(cur) >= MIN_WORDS_AT_PUNCT and w["text"][-1] in ".?!,;":
                lines.append(cur)
                cur = []
        if cur:
            lines.append(cur)
    return [{"start": l[0]["start"], "end": l[-1]["end"], "text": " ".join(w["text"] for w in l), "words": l}
            for l in lines]


def document(source: str, language: str | None, lines: list[dict]) -> dict:
    return {"source": source, "language": language, "lines": lines}
