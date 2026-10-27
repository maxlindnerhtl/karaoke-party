"""Portable setup: ffmpeg lookup and the rubberband check, processing device, MAC lookup, CPU warning data."""
import socket
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from server import config, db, netinfo
from server.app import create_app
from server.config import Config

ENV_KEYS = ("MUSIC_PATH", "HOST_PIN", "FFMPEG_PATH", "DEVICE", "WHISPER_MODEL", "DATA_DIR")


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setenv("MUSIC_PATH", str(music))
    monkeypatch.setenv("HOST_PIN", "1234")
    empty = tmp_path / "empty.env"
    empty.write_text("")
    return empty


def test_ffmpeg_path_is_optional_and_found_on_path(env, monkeypatch):
    monkeypatch.setattr(config.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
    assert config.load(env).ffmpeg == config.Path("/usr/bin/ffmpeg")
    monkeypatch.setenv("FFMPEG_PATH", "/opt/ff/ffmpeg")
    assert config.load(env).ffmpeg == config.Path("/opt/ff/ffmpeg")


def test_device_setting(env, monkeypatch):
    assert config.load(env).device == "auto"
    monkeypatch.setenv("DEVICE", "CPU")
    assert config.load(env).device == "cpu"
    monkeypatch.setenv("DEVICE", "tpu")
    with pytest.raises(SystemExit, match="DEVICE"):
        config.load(env)


def test_missing_ffmpeg_explains_how_to_install_it(env, monkeypatch):
    monkeypatch.setattr(config.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit) as e:
        config.check(config.load(env))
    assert "ffmpeg not found" in str(e.value) and config.ffmpeg_hint() in str(e.value)


def test_ffmpeg_without_rubberband_is_rejected(env, tmp_path, monkeypatch):
    fake = tmp_path / "ffmpeg"
    fake.write_text("")
    monkeypatch.setenv("FFMPEG_PATH", str(fake))
    monkeypatch.setattr(config, "has_rubberband", lambda _ffmpeg: False)
    with pytest.raises(SystemExit, match="no rubberband filter"):
        config.check(config.load(env))
    monkeypatch.setattr(config, "has_rubberband", lambda _ffmpeg: True)
    config.check(config.load(env))


def test_has_rubberband_reads_the_filter_list(monkeypatch):
    out = " ... rubberband        A->A       Apply time-stretching and pitch-shifting.\n"
    monkeypatch.setattr(config.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=out))
    assert config.has_rubberband(config.Path("ffmpeg"))
    monkeypatch.setattr(config.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=" ... loudnorm ...\n"))
    assert not config.has_rubberband(config.Path("ffmpeg"))


def test_install_hint_per_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert "install.ps1" in config.ffmpeg_hint()
    monkeypatch.setattr(sys, "platform", "linux")
    assert "apt install ffmpeg" in config.ffmpeg_hint()


def test_mac_for_ip_uses_the_adapter_with_that_ip(monkeypatch):
    import psutil
    a = lambda family, address: SimpleNamespace(family=family, address=address)
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: {
        "lo": [a(socket.AF_INET, "127.0.0.1"), a(psutil.AF_LINK, "00:00:00:00:00:00")],
        "wlan0": [a(psutil.AF_LINK, "02-00-5E-10-00-01"), a(socket.AF_INET, "192.168.1.20")],
    })
    assert netinfo.mac_for_ip("192.168.1.20") == "02:00:5e:10:00:01"
    assert netinfo.mac_for_ip("10.0.0.1") is None
    assert netinfo.mac_for_ip("127.0.0.1") is None


def test_jobs_report_the_processing_device(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    cfg = Config(music_path=music, host_pin="1234", ffmpeg=tmp_path / "ffmpeg", data_dir=tmp_path / "data")
    with TestClient(create_app(cfg, watch_library=False)) as c:
        assert c.get("/api/jobs", headers={"X-Host-Pin": "1234"}).json()["device"] is None
        conn = db.connect(cfg.db_path)
        db.set_settings(conn, {"processing_device": {"device": "cpu", "name": "CPU"}})
        conn.close()
        assert c.get("/api/jobs", headers={"X-Host-Pin": "1234"}).json()["device"]["device"] == "cpu"
        assert "processing_device" not in c.get("/api/settings").json()
