"""The TV's `enabled_notification_listeners` secure setting: adding and removing our component without touching the
other apps' entries. Pure functions (standard library only), used by scripts/tv_setup.py and scripts/tv_uninstall.py.

The value is a ':'-separated list of flattened component names ("pkg/cls"); `settings get` prints "null" when the
setting doesn't exist.
"""
from __future__ import annotations


def parse(value: str | None) -> list[str]:
    if value is None:
        return []
    value = value.strip()
    if value in ("", "null"):
        return []
    return [e.strip() for e in value.split(":") if e.strip()]


def join(entries: list[str]) -> str:
    return ":".join(entries)


def short(component: str) -> str:
    """"pkg/pkg.Cls" -> "pkg/.Cls": Android may store either form."""
    pkg, _, cls = component.partition("/")
    return f"{pkg}/{cls[len(pkg):]}" if cls.startswith(pkg + ".") else component


def same(a: str, b: str) -> bool:
    return a == b or short(a) == short(b)


def contains(value: str | None, component: str) -> bool:
    return any(same(e, component) for e in parse(value))


def add(value: str | None, component: str) -> str:
    """Appends `component` (Android 7 has no `cmd notification allow_listener`), keeping every other entry."""
    entries = parse(value)
    if not any(same(e, component) for e in entries):
        entries.append(component)
    return join(entries)


def remove(value: str | None, component: str) -> str:
    """Removes only `component`; the other entries stay in their order."""
    return join([e for e in parse(value) if not same(e, component)])


def missing_others(before: str | None, after: str | None, component: str) -> list[str]:
    """Entries of other apps that were there `before` and are gone `after` (should always be empty)."""
    now = parse(after)
    return [e for e in parse(before) if not same(e, component) and not any(same(e, n) for n in now)]


def cleanup(current: str | None, component: str, before: str | None = None) -> tuple[str, str | None]:
    """What to do with the setting after our app is uninstalled. Returns (action, value):
    ("none", None)    our entry is already gone (Android removes it on uninstall)
    ("put", value)    write `value`: ours removed, everything else kept
    ("delete", None)  ours was the only entry and the setting didn't exist before the install: delete it again
    """
    if not contains(current, component):
        return "none", None
    value = remove(current, component)
    if value == "" and (before is None or before.strip() in ("", "null")):
        return "delete", None
    return "put", value
