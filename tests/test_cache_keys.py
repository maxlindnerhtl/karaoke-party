from pathlib import Path

import pytest

from server import audio

SID = "0123456789abcdef0123456789abcdef01234567"


@pytest.mark.parametrize("key,level,expected", [(0, 0, "_k+0_v0"), (2, 5, "_k+2_v5"), (-3, 15, "_k-3_v15")])
def test_render_id_roundtrip(key, level, expected):
    rid = audio.render_id(SID, key, level)
    assert rid == SID + expected
    assert audio.parse_render_id(rid) == (SID, key, level)


def test_render_id_is_unique_per_cache_key():
    ids = {audio.render_id(SID, k, v) for k in range(-3, 4) for v in (0, 5, 10, 15)}
    assert len(ids) == 7 * 4


@pytest.mark.parametrize("bad", ["../etc/passwd", SID + "_k+4_v0", SID + "_k+1_v20", SID + "_k1_v0", "x_k+0_v0",
                                 SID + "_k+0_v0.m4a", SID.upper() + "_k+0_v0"])
def test_parse_rejects_anything_else(bad):
    assert audio.parse_render_id(bad) is None


def test_filter_key0_no_vocals_has_no_rubberband():
    f = audio.render_filter(0, 0, -3.5)
    assert "rubberband" not in f and "amix" not in f and "volume=-3.50dB" in f
    assert f.endswith(audio.LIMITER + "[out]")


def test_filter_with_shift_and_vocals():
    f = audio.render_filter(2, 10, 1.0)
    assert "volume=0.10" in f and "amix=inputs=2:normalize=0" in f
    assert "rubberband=pitch=1.122462:formant=preserved:pitchq=quality:channels=together:transients=mixed" in f
    assert "volume=+1.00dB,aresample=176400,alimiter=" in f   # limiter after the gain
    assert "loudnorm" not in f


def test_next_ceiling_lowers_by_the_overshoot():
    assert audio.next_ceiling(-1.5, -1.2) is None              # already under -1 dBTP
    assert audio.next_ceiling(-1.5, -1.0) is None
    assert audio.next_ceiling(-1.5, -0.53) == -2.17            # 0.47 dB over, plus 0.2 dB margin
    assert audio.render_filter(0, 0, 0.0, -2.17).endswith(audio.limiter(-2.17) + "[out]")
    assert "limit=0.7789" in audio.limiter(-2.17)


def test_limiter_oversamples_and_leaves_codec_headroom():
    assert audio.LIMITER == ("aresample=176400,alimiter=limit=0.8414:attack=5:release=100:level=false,"
                             "aresample=44100")
    assert audio.LIMITER_CEILING < audio.MAX_TRUE_PEAK


def test_gain_targets_minus_16():
    assert audio.gain_for(-20.0) == 4.0     # a quiet song is turned up (the limiter handles its peaks)
    assert audio.gain_for(-9.0) == -7.0     # a loud master is turned down
    assert audio.gain_for(-15.1) == -0.9    # the ballad at +2, no longer capped by its true peak
    assert audio.gain_for(float("-inf")) == 0.0


def test_measure_filter_applies_the_key_shift():
    assert audio.measure_filter(0) == "loudnorm=I=-16.0:TP=-1.0:print_format=json"
    f = audio.measure_filter(-1)
    assert f.startswith("rubberband=pitch=0.943874:formant=preserved:pitchq=quality:channels=together:transients=mixed,")
    assert f.endswith("loudnorm=I=-16.0:TP=-1.0:print_format=json")


def test_render_cmd_uses_configured_ffmpeg():
    ffmpeg = Path("tools") / "ffmpeg"
    cmd = audio.render_cmd(ffmpeg, Path("stems"), Path("out.m4a"), 0, 0, 0.0)
    assert cmd[0] == str(ffmpeg)
    assert "aac" in cmd and "192k" in cmd
