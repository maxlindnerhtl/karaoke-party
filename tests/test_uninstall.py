"""Uninstall path: the install manifest, model-cache tracking, the TV listener restore logic, tv_setup's record,
tv_uninstall's helpers and the stats export. No TV, adb or network needed."""
import io
import json
import socket
import sqlite3
import sys
from pathlib import Path

import pytest

from server import db, install_manifest as im, tv_listeners as tl

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import export_stats  # noqa: E402
import tv_setup  # noqa: E402
import tv_uninstall  # noqa: E402

OURS = "io.github.maxlindnerhtl.karaokeparty/com.karaoke.tv.yt.YtNotificationListener"
LAUNCHER = "com.example.launcher/com.example.launcher.MediaListener"
OTHER = "com.example.other/.Listener"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(im, "ROOT", tmp_path)
    return tmp_path


# --- install manifest ------------------------------------------------------------------------------------------------

def test_missing_manifest_is_empty_and_save_round_trips(root):
    assert im.load() == {}
    data = {}
    im.set_(data, "pc.task", "Karaoke Party server")
    im.add(data, "pc.firewall_rules", "KaraokeParty-Server-TCP")
    im.add(data, "pc.firewall_rules", "KaraokeParty-Server-TCP")      # no duplicates
    im.save(data)
    back = im.load()
    assert back["pc"] == {"task": "Karaoke Party server", "firewall_rules": ["KaraokeParty-Server-TCP"]}
    assert back["version"] == 1 and back["repo"] == str(root)
    assert im.manifest_path() == root / "data" / "install-manifest.json"


def test_corrupt_manifest_counts_as_missing(root):
    im.manifest_path().parent.mkdir(parents=True)
    im.manifest_path().write_text("{not json", encoding="utf-8")
    assert im.load() == {}


def test_a_tool_keeps_its_first_record(root):
    data = {}
    im.record_tool(data, "uv", "/home/u/.local/bin/uv", installed_by_us=True)
    im.record_tool(data, "uv", "/home/u/.local/bin/uv", installed_by_us=False)   # second installer run finds it
    im.record_tool(data, "ffmpeg", "/usr/bin/ffmpeg", installed_by_us=False)
    assert [t["name"] for t in im.removable_tools(data)] == ["uv"]
    assert len(im.get(data, "pc.tools")) == 2


def test_tools_that_were_already_there_are_never_removable():
    data = {"pc": {"tools": [{"name": "uv", "path": "x", "installed_by_us": False},
                             {"name": "python", "path": "y", "installed_by_us": False}]}}
    assert im.removable_tools(data) == []


def test_cli_takes_json_from_the_environment_and_stdin(root, monkeypatch, capsys):
    # Windows PowerShell 5.1 mangles JSON in arguments and pipes, so install.ps1 passes it in an environment variable
    monkeypatch.setenv(im.VALUE_ENV, '{"name": "ffmpeg", "path": "C:\\\\Users\\\\Müller\\\\x", "installed_by_us": true}')
    assert im.main(["add", "pc.tools", "@env"]) == 0
    # stdin works too, also with the BOM PowerShell puts in front
    stdin = io.TextIOWrapper(io.BytesIO('\ufeff{"name": "uv", "path": "C:\\\\uv", "installed_by_us": false}'.encode()))
    monkeypatch.setattr(sys, "stdin", stdin)
    assert im.main(["add", "pc.tools", "-"]) == 0
    assert im.main(["set", "pc.os", '"windows"']) == 0
    assert im.main(["get", "pc.os"]) == 0
    assert capsys.readouterr().out.strip() == '"windows"'
    assert im.load()["pc"]["tools"] == [{"name": "ffmpeg", "path": "C:\\Users\\Müller\\x", "installed_by_us": True},
                                       {"name": "uv", "path": "C:\\uv", "installed_by_us": False}]


# --- model caches ----------------------------------------------------------------------------------------------------

def test_cache_dirs_honour_the_usual_overrides(tmp_path):
    env = {"HOME": str(tmp_path), "HF_HOME": str(tmp_path / "hf"), "TORCH_HOME": str(tmp_path / "t")}
    assert im.cache_dirs(env) == [tmp_path / "hf" / "hub", tmp_path / "t" / "hub" / "checkpoints", tmp_path / "t" / "hub"]
    xdg = im.cache_dirs({"HOME": str(tmp_path), "XDG_CACHE_HOME": str(tmp_path / "c")})
    assert xdg[0] == tmp_path / "c" / "huggingface" / "hub"


def test_only_models_downloaded_during_the_block_are_recorded(root, tmp_path):
    hub, ckpt = tmp_path / "hf" / "hub", tmp_path / "torch" / "hub" / "checkpoints"
    hub.mkdir(parents=True)
    (hub / "models--shared--already-there").mkdir()                  # e.g. another app's copy of the same model
    with im.track_model_downloads(dirs=[hub, ckpt, ckpt.parent]):
        (hub / "models--Systran--faster-whisper-large-v3").mkdir()
        ckpt.mkdir(parents=True)                                       # the folder itself is created too
        (ckpt / "f7e0c4bc-ba3fe64a.th").write_bytes(b"x")
    recorded = im.load()["pc"]["model_caches"]
    assert str(hub / "models--Systran--faster-whisper-large-v3") in recorded
    assert str(ckpt / "f7e0c4bc-ba3fe64a.th") in recorded
    assert str(hub / "models--shared--already-there") not in recorded
    assert str(ckpt) not in recorded                                   # a container, not a model


def test_nothing_new_writes_no_manifest(root, tmp_path):
    with im.track_model_downloads(dirs=[tmp_path / "nothing"]):
        pass
    assert not im.manifest_path().exists()


# --- TV listener logic -----------------------------------------------------------------------------------------------

def test_parse_handles_null_and_empty():
    assert tl.parse("null") == [] and tl.parse("") == [] and tl.parse(None) == [] and tl.parse(" null\n") == []
    assert tl.parse(f"{LAUNCHER}:{OURS}") == [LAUNCHER, OURS]


def test_add_keeps_other_entries_and_never_duplicates():
    assert tl.add("null", OURS) == OURS
    assert tl.add(LAUNCHER, OURS) == f"{LAUNCHER}:{OURS}"
    assert tl.add(f"{LAUNCHER}:{OURS}", OURS) == f"{LAUNCHER}:{OURS}"
    # Android may have stored an entry in its short form: the long one is the same component
    assert tl.add(OTHER, "com.example.other/com.example.other.Listener") == OTHER


def test_short_and_long_component_forms_are_the_same():
    assert tl.same("com.example.other/.Listener", "com.example.other/com.example.other.Listener")
    assert not tl.same(LAUNCHER, OTHER)


def test_remove_only_removes_ours_and_keeps_the_order():
    assert tl.remove(f"{LAUNCHER}:{OURS}:{OTHER}", OURS) == f"{LAUNCHER}:{OTHER}"
    assert tl.remove(f"{OTHER}:{LAUNCHER}", OURS) == f"{OTHER}:{LAUNCHER}"


def test_cleanup_after_uninstall():
    # Android removed our entry with the app: nothing to do
    assert tl.cleanup(LAUNCHER, OURS, before=LAUNCHER) == ("none", None)
    # still there: write the list without it
    assert tl.cleanup(f"{LAUNCHER}:{OURS}", OURS, before=LAUNCHER) == ("put", LAUNCHER)
    # ours was the only one and the setting didn't exist before: delete it again
    assert tl.cleanup(OURS, OURS, before="null") == ("delete", None)


def test_missing_others_finds_lost_entries():
    before = f"{LAUNCHER}:{OTHER}"
    assert tl.missing_others(before, f"{OTHER}:{LAUNCHER}", OURS) == []
    assert tl.missing_others(before, LAUNCHER, OURS) == [OTHER]
    assert tl.missing_others(f"{LAUNCHER}:{OURS}", LAUNCHER, OURS) == []   # ours going away is the point


# --- tv_setup's record and tv_uninstall's helpers --------------------------------------------------------------------

def test_tv_setup_records_the_listeners_from_before_the_first_grant(root):
    tv_setup.record("192.168.1.50:5555", tv_setup.PACKAGE, 34, LAUNCHER, overlay=True)
    tv_setup.record("192.168.1.50:5555", tv_setup.PACKAGE, 34, f"{LAUNCHER}:{OTHER}", overlay=False)   # rerun
    rec = im.load()["tv"]["192.168.1.50:5555"]
    assert rec["listeners_before"] == LAUNCHER and rec["appops"] == ["SYSTEM_ALERT_WINDOW"] and rec["sdk"] == 34


def test_find_record_matches_the_same_tv_on_another_port():
    data = {"tv": {"192.168.1.50:5555": {"sdk": 24}}}
    assert tv_uninstall.find_record(data, "192.168.1.50:5555")[0] == "192.168.1.50:5555"
    assert tv_uninstall.find_record(data, "192.168.1.50:41234")[0] == "192.168.1.50:5555"
    assert tv_uninstall.find_record(data, "192.168.1.60:5555") == (None, {})


def test_adb_port_check_reports_a_closed_port_as_refused():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                                          # nothing listens there now
    assert tv_uninstall.adb_port_state("127.0.0.1", port) == "refused"
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        assert tv_uninstall.adb_port_state("127.0.0.1", srv.getsockname()[1]) == "open"


# --- stats export ----------------------------------------------------------------------------------------------------

def test_export_stats(tmp_path):
    path = tmp_path / "k.db"
    db.init(path)
    conn = db.connect(path)
    with db.tx(conn):
        conn.execute("INSERT INTO guests (id, name, created_at) VALUES ('a', 'Lena', 0), ('b', 'Jonas', 0)")
        for i, (title, singers) in enumerate([("Paper Lanterns", ["a"]), ("Paper Lanterns", ["a", "b"]),
                                               ("Glass Harbor", ["b"])], start=1):
            conn.execute("INSERT INTO queue (id, source, song_id, title, artist, singers, status, added_by, position, "
                         "created_at, updated_at) VALUES (?, 'library', ?, ?, 'Demo', ?, 'done', 'a', ?, 0, 0)",
                         (i, title, title, json.dumps(singers), i))
            conn.execute("INSERT INTO plays (queue_id, song_id, singers, played_at) VALUES (?, ?, ?, ?)",
                         (i, title, json.dumps(singers), 1_700_000_000 + i))
    conn.close()
    stats = export_stats.export(path)
    assert stats["total_plays"] == 3
    assert stats["most_sung"][0] == {"title": "Paper Lanterns", "artist": "Demo", "source": "library", "plays": 2}
    assert stats["top_singers"] == [{"name": "Jonas", "songs": 2}, {"name": "Lena", "songs": 2}]
    assert stats["history"][1]["singers"] == ["Lena", "Jonas"]
    # read-only: the export never writes to the database
    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM plays").fetchone()[0] == 3
