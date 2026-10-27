#!/usr/bin/env python3
"""The install manifest: what the installers changed on this PC and on TVs, so the uninstallers can undo exactly that.

Stored as data/install-manifest.json in the repo (also when DATA_DIR points elsewhere: the manifest describes the
installation, so it lives with it). Standard library only, so any Python 3.8+ can run it: the Linux uninstaller
uses the system python3, also after .venv is gone.

    python server/install_manifest.py set <dotted.key> <json value | - | @env>   # see read_value()
    python server/install_manifest.py add <dotted.key> <json value | - | @env>   # append to a list (no duplicates)
    python server/install_manifest.py get <dotted.key>                  # prints JSON (null if missing)
    python server/install_manifest.py path

Layout (every key optional; a missing manifest = an install from before manifests, detected instead):
    {"version": 1, "repo": "...", "data_dir": "...",
     "pc": {"os": "windows|linux", "task": "...", "service": "...", "firewall_rules": [...],
            "ufw_rules": [...], "linger_enabled": true, "network_profile": {"interface": .., "previous": ..},
            "tools": [{"name": "uv", "path": "...", "installed_by_us": true}, ...],
            "model_caches": ["<path created by the worker's first model download>", ...]},
     "tv": {"<serial>": {"package": "...", "sdk": 24, "listeners_before": "...", "appops": ["SYSTEM_ALERT_WINDOW"]}}}
"""
from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = 1


def manifest_path(root: Path | None = None) -> Path:
    return (root or ROOT) / "data" / "install-manifest.json"


def load(root: Path | None = None) -> dict:
    p = manifest_path(root)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(data: dict, root: Path | None = None):
    p = manifest_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    data.setdefault("version", VERSION)
    data.setdefault("repo", str(root or ROOT))
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def _walk(data: dict, key: str, create: bool):
    parts = key.split(".")
    node = data
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            if not create:
                return None, parts[-1]
            node[part] = {}
        node = node[part]
    return node, parts[-1]


def get(data: dict, key: str, default=None):
    node, last = _walk(data, key, create=False)
    return default if node is None else node.get(last, default)


def set_(data: dict, key: str, value):
    node, last = _walk(data, key, create=True)
    node[last] = value


def add(data: dict, key: str, value):
    """Appends `value` to the list at `key` unless it's already there. Dicts with the same "name" and "path" count as
    the same item, and the first record wins: a second installer run finds the tool it installed the first time and
    must not overwrite `installed_by_us: true` with false."""
    node, last = _walk(data, key, create=True)
    items = node.setdefault(last, [])
    for i, item in enumerate(items):
        if item == value:
            return
        if (isinstance(item, dict) and isinstance(value, dict) and "name" in value
                and item.get("name") == value.get("name") and item.get("path") == value.get("path")):
            items[i] = {**value, **item}
            return
    items.append(value)


def record_tool(data: dict, name: str, path: str, installed_by_us: bool):
    add(data, "pc.tools", {"name": name, "path": path, "installed_by_us": bool(installed_by_us)})


def removable_tools(data: dict) -> list[dict]:
    """Tools the installer added itself; anything that was already there is never offered for removal."""
    return [t for t in get(data, "pc.tools", []) if t.get("installed_by_us")]


# --- model caches ------------------------------------------------------------------------------------------------

def cache_dirs(env=os.environ) -> list[Path]:
    """Where torch hub, Hugging Face and faster-whisper put downloaded models (honouring the usual overrides)."""
    home = Path(env.get("USERPROFILE") or env.get("HOME") or Path.home())
    xdg = Path(env.get("XDG_CACHE_HOME") or home / ".cache")
    hf = Path(env.get("HF_HOME") or xdg / "huggingface")
    torch = Path(env.get("TORCH_HOME") or xdg / "torch")
    dirs = [Path(env.get("HF_HUB_CACHE") or hf / "hub"), torch / "hub" / "checkpoints", torch / "hub"]
    seen, out = set(), []
    for d in dirs:
        if str(d) not in seen:
            seen.add(str(d))
            out.append(d)
    return out


def snapshot(dirs: list[Path]) -> set[str]:
    out = set()
    for d in dirs:
        if d.is_dir():
            out.update(str(d / name) for name in os.listdir(d))
    return out


def new_entries(before: set[str], after: set[str], dirs: list[Path]) -> list[str]:
    """Entries that appeared, minus the cache folders themselves (e.g. torch/hub/checkpoints inside torch/hub)."""
    containers = {str(d) for d in dirs}
    return sorted(p for p in after - before if p not in containers)


@contextmanager
def track_model_downloads(root: Path | None = None, dirs: list[Path] | None = None):
    """Records cache entries that appear while the block runs (a model's first download) as created by us. Entries
    that existed before (e.g. shared with another app using the same model) are never recorded, so the uninstaller
    can't delete them."""
    dirs = dirs if dirs is not None else cache_dirs()
    before = snapshot(dirs)
    try:
        yield
    finally:
        created = new_entries(before, snapshot(dirs), dirs)
        if created:
            data = load(root)
            for p in created:
                add(data, "pc.model_caches", p)
            save(data, root)


def dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for base, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(base, f))
            except OSError:
                pass
    return total


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


VALUE_ENV = "KARAOKE_MANIFEST_VALUE"


def read_value(arg: str) -> str:
    """The JSON value argument. "@env" reads it from the KARAOKE_MANIFEST_VALUE environment variable (what
    install.ps1 uses: Windows PowerShell 5.1 strips double quotes from native-command arguments and pipes text as
    ASCII with a BOM, while environment variables are Unicode-safe). "-" reads stdin (a BOM is ignored)."""
    if arg == "@env":
        return os.environ[VALUE_ENV]
    if arg == "-":
        return sys.stdin.buffer.read().decode("utf-8-sig")
    return arg


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("set", "add", "get", "path", "sizes"):
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "path":
        print(manifest_path())
        return 0
    if cmd == "sizes":   # sizes <path>...: "<bytes>\t<human>\t<path>" per existing path
        for p in argv[1:]:
            if Path(p).exists():
                n = dir_size(Path(p))
                print(f"{n}\t{human(n)}\t{p}")
        return 0
    data = load()
    if cmd == "get":
        print(json.dumps(get(data, argv[1])))
        return 0
    value = json.loads(read_value(argv[2]))
    (set_ if cmd == "set" else add)(data, argv[1], value)
    save(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
