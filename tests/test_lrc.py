from server.lyrics import lrc_segments, parse_lrc


def test_basic_and_fraction_formats():
    lines = parse_lrc("[00:01.5]one\n[00:02.25]two\n[00:03.125]three\n[01:04]four")
    assert [(l.start, l.text) for l in lines] == [(1.5, "one"), (2.25, "two"), (3.125, "three"), (64.0, "four")]


def test_multiple_stamps_metadata_and_sorting():
    text = "[ar:Someone]\n[ti:Song]\n[00:10.00][00:30.00]chorus\n[00:20.00]verse\nno stamp here"
    assert [(l.start, l.text) for l in parse_lrc(text)] == [(10.0, "chorus"), (20.0, "verse"), (30.0, "chorus")]


def test_empty_lines_are_kept_as_breaks_but_not_segments():
    lines = parse_lrc("[00:01.00]a b\n[00:04.00]\n[00:12.00]c")
    assert [l.text for l in lines] == ["a b", "", "c"]
    segs = lrc_segments(lines, duration=20)
    assert segs == [{"start": 1.0, "end": 4.0, "text": "a b"}, {"start": 12.0, "end": 20.0, "text": "c"}]


def test_offset_tag_shifts_lines_earlier():
    lines = parse_lrc("[offset:+500]\n[00:01.00]x\n[00:00.20]y")
    assert [(l.start, l.text) for l in lines] == [(0.0, "y"), (0.5, "x")]


def test_last_segment_end_capped_by_duration():
    segs = lrc_segments(parse_lrc("[00:58.00]end"), duration=60)
    assert segs[0]["end"] == 60


def test_crlf_and_colon_fraction():
    assert [(l.start, l.text) for l in parse_lrc("[00:01:50]x\r\n[00:02.00]y\r\n")] == [(1.5, "x"), (2.0, "y")]
