import httpx

from server.lyrics import lines_from_auto, lines_from_lrc, lrclib_lookup


def w(t, s, e):
    return {"word": t, "start": s, "end": e}


def test_auto_lines_split_on_pause_punctuation_and_length():
    seg = {"start": 0, "end": 20, "words": [
        w("one", 0.0, 0.3), w("two", 0.4, 0.6), w("three", 0.7, 0.9),                         # a pause follows
        w("four", 2.0, 2.2), w("five", 2.3, 2.4), w("six", 2.5, 2.6), w("seven.", 2.7, 2.8),  # punctuation
        *[w(f"x{i}", 3.0 + i * 0.2, 3.1 + i * 0.2) for i in range(10)],                      # 10 words, no pause
    ]}
    lines = lines_from_auto([seg])
    assert [l["text"] for l in lines][:2] == ["one two three", "four five six seven."]
    assert [len(l["words"]) for l in lines[2:]] == [8, 2]
    assert lines[0]["start"] == 0.0 and lines[0]["end"] == 0.9


def test_untimed_words_are_interpolated():
    seg = {"start": 0, "end": 4, "words": [w("a", 0.0, 0.5), {"word": "2"}, w("c", 2.0, 2.5)]}
    words = lines_from_auto([seg])[0]["words"]
    assert words[0]["end"] <= words[1]["start"] < words[1]["end"] <= words[2]["start"]


def test_lrc_lines_keep_lrc_breaks_even_if_alignment_merges_segments():
    lrc = [{"start": 1.0, "end": 3.0, "text": "hello there"}, {"start": 3.0, "end": 5.0, "text": "general kenobi"}]
    aligned = [{"words": [w("hello", 1.1, 1.4), w("there", 1.5, 1.9), w("general", 3.1, 3.5), w("kenobi", 3.6, 4.2)]}]
    lines = lines_from_lrc(lrc, aligned)
    assert [l["text"] for l in lines] == ["hello there", "general kenobi"]
    assert lines[1]["words"][0] == {"start": 3.1, "end": 3.5, "text": "general"}


def test_lrc_line_without_alignment_spreads_words():
    lines = lines_from_lrc([{"start": 0.0, "end": 2.0, "text": "a b"}], [])
    assert lines[0]["words"] == [{"start": 0.0, "end": 1.0, "text": "a"}, {"start": 1.0, "end": 2.0, "text": "b"}]


def _client(handler):
    return httpx.Client(base_url="https://lrclib.net", transport=httpx.MockTransport(handler))


def test_lrclib_get_hit():
    def handler(req):
        assert req.url.path == "/api/get"
        return httpx.Response(200, json={"id": 1, "syncedLyrics": "[00:01.00]x", "duration": 190})
    assert lrclib_lookup("A", "T", "Al", 190.4, client=_client(handler))["id"] == 1


def test_lrclib_503_falls_back_to_search_with_duration_window():
    def handler(req):
        if req.url.path == "/api/get":
            return httpx.Response(503)
        return httpx.Response(200, json=[
            {"id": 1, "trackName": "Song", "artistName": "Band", "duration": 200.0, "syncedLyrics": "[00:01.00]x"},
            {"id": 2, "trackName": "Song (Live)", "artistName": "Band", "duration": 191.0, "syncedLyrics": "[00:01.00]x"},
            {"id": 3, "trackName": "Song", "artistName": "Band", "duration": 192.0, "syncedLyrics": "[00:01.00]x"},
            {"id": 4, "trackName": "Song", "artistName": "Band", "duration": 190.0, "syncedLyrics": None},
        ])
    assert lrclib_lookup("Band", "Song", "", 190.6, client=_client(handler))["id"] == 3


def test_lrclib_no_match():
    def handler(req):
        return httpx.Response(404) if req.url.path == "/api/get" else httpx.Response(200, json=[])
    assert lrclib_lookup("A", "T", "", 100, client=_client(handler)) is None
