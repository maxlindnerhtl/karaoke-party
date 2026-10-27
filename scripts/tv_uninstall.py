#!/usr/bin/env python3
"""Remove the Karaoke Party TV app over adb and undo exactly what scripts/tv_setup.py changed on the TV.

    python scripts/tv_uninstall.py --tv 192.168.1.50
    python scripts/tv_uninstall.py --serial emulator-5554
    python scripts/tv_uninstall.py --check-adb-closed 192.168.1.50     # after switching ADB off on the TV

1. revokes the "display over other apps" appop it granted (Android 10+)
2. uninstalls the app (only this app: nothing else on the TV is touched)
3. checks that its notification-listener entry is gone (Android removes it on uninstall); if it's still there,
   removes only that entry and keeps all others
4. verifies that the other apps' listeners from before the install are all still there

Uses data/install-manifest.json when tv_setup.py recorded the TV there; without it, it works from what it finds.
Needs Python 3.8+ and adb, like tv_setup.py.
"""
from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import install_manifest, tv_listeners  # noqa: E402
from tv_setup import LISTENER, PACKAGE, Adb, connect, find_adb, step  # noqa: E402

SETTING = "enabled_notification_listeners"


def adb_port_state(host: str, port: int = 5555, timeout: float = 3.0) -> str:
    """"refused" (closed: the only proof), "open", or "no answer" (TV off, asleep or filtered: proves nothing)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "open"
    except ConnectionRefusedError:
        return "refused"
    except OSError:
        return "no answer"


def find_record(data: dict, device: str) -> tuple[str | None, dict]:
    tvs = data.get("tv", {}) or {}
    if device in tvs:
        return device, tvs[device]
    host = device.split(":")[0]
    for key, rec in tvs.items():   # same TV under another port (e.g. wireless debugging)
        if key.split(":")[0] == host:
            return key, rec
    if len(tvs) == 1 and device == "default":
        return next(iter(tvs.items()))
    return None, {}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tv", help="TV IP address (network debugging)")
    ap.add_argument("--serial", help="adb device serial instead of --tv")
    ap.add_argument("--package", default=PACKAGE, help=f"application id (default {PACKAGE})")
    ap.add_argument("--check-adb-closed", metavar="TV_IP", help="only check that port 5555 refuses connections")
    args = ap.parse_args()

    if args.check_adb_closed:
        state = adb_port_state(args.check_adb_closed)
        msg = {"refused": "closed: port 5555 refuses connections",
               "open": "OPEN: network ADB is still on (switch it off in Developer options and reboot the TV)",
               "no answer": "no answer: the TV is off, asleep or the port is filtered; this proves nothing, "
                            "check again with the TV on"}[state]
        print(f"{args.check_adb_closed}:5555 {msg}")
        sys.exit(0 if state == "refused" else 1)

    adb = find_adb()
    serial = connect(adb, args.tv) if args.tv else args.serial
    a = Adb(adb, serial)
    sdk = int(a.shell("getprop ro.build.version.sdk").strip())
    component = f"{args.package}/{LISTENER}"
    data = install_manifest.load()
    key, rec = find_record(data, serial or "default")
    current = a.shell(f"settings get secure {SETTING}", check=False).strip()
    # the other apps' listeners we must not lose: from before the install if recorded, else what is there now
    before = rec.get("listeners_before") if rec else None
    expected_others = tv_listeners.remove(before if before is not None else current, component)
    print(f"    Android API {sdk}; manifest record: {'yes (' + key + ')' if rec else 'none, working from what is on the TV'}")

    installed = args.package in a.shell(f"pm list packages {args.package}", check=False)
    if installed and sdk >= 29:
        step("Revoking \"display over other apps\"")
        a.shell(f"appops set {args.package} SYSTEM_ALERT_WINDOW default", check=False)
        print(f"    {a.shell(f'appops get {args.package} SYSTEM_ALERT_WINDOW', check=False) or 'default'}")

    step(f"Uninstalling {args.package}")
    if installed:
        out = a.run("uninstall", args.package, check=False)
        print(f"    {out}")
        if "Success" not in out:
            sys.exit("    uninstall failed")
    else:
        print("    not installed")

    step("Notification listeners")
    after = a.shell(f"settings get secure {SETTING}", check=False).strip()
    action, value = tv_listeners.cleanup(after, component, before)
    if action == "none":
        print("    our entry is gone (Android removed it with the app)")
    else:
        if sdk >= 26 and action == "put":
            a.shell(f"cmd notification disallow_listener {component}", check=False)
        elif action == "put":
            a.shell(f"settings put secure {SETTING} '{value}'")
        else:
            a.shell(f"settings delete secure {SETTING}")
        after = a.shell(f"settings get secure {SETTING}", check=False).strip()
        print(f"    removed our leftover entry ({action})")
    if tv_listeners.contains(after, component):
        sys.exit(f"    our entry is still there: {after}")
    missing = tv_listeners.missing_others(expected_others, after, component)
    others = tv_listeners.parse(expected_others)
    if missing:
        print(f"    WARNING: other listeners from before are missing now: {', '.join(missing)}")
    else:
        print(f"    the other {len(others)} listener(s) are all still there" if others else "    no other listeners")

    if key:
        data["tv"].pop(key, None)
        install_manifest.save(data)
    print("    done. Switch debugging off on the TV now (Developer options), see UNINSTALL.md.")
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()
