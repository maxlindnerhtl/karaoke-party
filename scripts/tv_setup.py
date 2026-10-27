#!/usr/bin/env python3
"""Set up the Karaoke Party TV app over adb: connect, install the APK, grant YouTube end detection, start the app.

    python scripts/tv_setup.py --tv 192.168.1.50 --apk karaoke-party.apk
    python scripts/tv_setup.py --tv 192.168.1.50            # grant + start only (app already installed)
    python scripts/tv_setup.py --serial emulator-5554 --grant-only

Needs only Python 3.8+ and adb (Android SDK platform-tools); no other packages. The TV must have network debugging
on (see README, "TV setup"); the first connection shows "Allow USB debugging?" on the TV: accept it.

The grant lets the app read YouTube's playback state (MediaSessionManager.getActiveSessions needs an enabled
notification listener), so YouTube songs return to the karaoke app when they end:
  Android 8+:  cmd notification allow_listener <component>
  Android 7:   no allow_listener command; the component is appended to settings secure enabled_notification_listeners
On Android 10+ it also allows "display over other apps" (appops), so the app can come back to the front by itself
even if the listener grant is refused. Without any grant YouTube songs return after a timer (video length + 60 s).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import install_manifest, tv_listeners  # noqa: E402  (standard library only)

PACKAGE = "io.github.maxlindnerhtl.karaokeparty"
LISTENER = "com.karaoke.tv.yt.YtNotificationListener"
ACTIVITY = "com.karaoke.tv.MainActivity"


def find_adb() -> str:
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(env):
            exe = os.path.join(os.environ[env], "platform-tools", "adb.exe" if os.name == "nt" else "adb")
            if os.path.isfile(exe):
                return exe
    found = shutil.which("adb")
    if not found:
        sys.exit("adb not found. Install the Android SDK platform-tools "
                 "(https://developer.android.com/tools/releases/platform-tools) and put adb on PATH, or set ANDROID_HOME.")
    return found


class Adb:
    def __init__(self, adb: str, serial: str | None):
        self.adb = adb
        self.serial = serial

    def run(self, *args: str, check: bool = True, timeout: int = 120) -> str:
        cmd = [self.adb] + (["-s", self.serial] if self.serial else []) + list(args)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout + r.stderr).strip()
        if check and r.returncode != 0:
            sys.exit(f"failed: {' '.join(cmd)}\n{out}")
        return out

    def shell(self, command: str, check: bool = True) -> str:
        return self.run("shell", command, check=check)


def step(msg: str):
    print(f"==> {msg}", flush=True)


def connect(adb: str, tv: str) -> str:
    target = tv if ":" in tv else f"{tv}:5555"
    step(f"Connecting to {target}")
    for attempt in range(30):
        out = subprocess.run([adb, "connect", target], capture_output=True, text=True, timeout=30).stdout.strip()
        state = subprocess.run([adb, "-s", target, "get-state"], capture_output=True, text=True, timeout=30)
        if state.stdout.strip() == "device":
            print(f"    connected ({out})")
            return target
        if "refused" in out or "cannot connect" in out:
            sys.exit(f"    {out}\n    Is network debugging (ADB) on in the TV's developer options? Same network?")
        if attempt == 0:
            print("    Waiting for the TV: accept \"Allow USB debugging?\" on the TV (tick \"Always allow\")...")
        time.sleep(2)
    sys.exit("    the TV didn't accept the connection (see the prompt on the TV, then run this again)")


def grant_listener(a: Adb, sdk: int, component: str) -> bool:
    step(f"Notification-listener grant (YouTube end detection), Android API {sdk}")
    if sdk >= 26:
        out = a.shell(f"cmd notification allow_listener {component}", check=False)
        if out:
            print(f"    {out}")
    else:
        current = a.shell("settings get secure enabled_notification_listeners").strip()
        if not tv_listeners.contains(current, component):
            # keep the other apps' listeners
            a.shell(f"settings put secure enabled_notification_listeners '{tv_listeners.add(current, component)}'")
    granted = tv_listeners.contains(a.shell("settings get secure enabled_notification_listeners", check=False), component)
    print(f"    {'granted' if granted else 'NOT granted: YouTube songs will return after a timer instead'}")
    return granted


def grant_overlay(a: Adb, sdk: int, package: str) -> bool:
    if sdk < 29:
        return False   # before Android 10 a background app may bring itself to the front anyway
    step("Display over other apps (lets the app come back after a YouTube song, Android 10+)")
    out = a.shell(f"appops set {package} SYSTEM_ALERT_WINDOW allow", check=False)
    mode = a.shell(f"appops get {package} SYSTEM_ALERT_WINDOW", check=False)
    print(f"    {out or mode}")
    return "allow" in mode


def record(device: str, package: str, sdk: int, listeners_before: str, overlay: bool):
    """Notes in data/install-manifest.json what this script changed on the TV, for scripts/tv_uninstall.py. The
    listener value from before the first grant is kept across reruns."""
    data = install_manifest.load()
    tv = dict(data.get("tv", {}).get(device) or {})   # device names contain dots: no dotted-key lookup
    tv.setdefault("listeners_before", listeners_before)
    tv.update(package=package, sdk=sdk)
    if overlay:
        tv["appops"] = sorted(set(tv.get("appops", [])) | {"SYSTEM_ALERT_WINDOW"})
    data.setdefault("tv", {})[device] = tv
    install_manifest.save(data)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tv", help="TV IP address (network debugging), e.g. 192.168.1.50")
    ap.add_argument("--serial", help="adb device serial instead of --tv (e.g. emulator-5554, or a USB device)")
    ap.add_argument("--apk", help="install this APK first")
    ap.add_argument("--package", default=PACKAGE, help=f"application id (default {PACKAGE})")
    ap.add_argument("--grant-only", action="store_true", help="only the grants: don't start the app")
    args = ap.parse_args()

    adb = find_adb()
    serial = connect(adb, args.tv) if args.tv else args.serial
    a = Adb(adb, serial)
    sdk = int(a.shell("getprop ro.build.version.sdk").strip())
    release = a.shell("getprop ro.build.version.release").strip()
    model = a.shell("getprop ro.product.model").strip()
    print(f"    {model}, Android {release} (API {sdk})")
    if sdk < 24:
        sys.exit("    the app needs Android 7.0 (API 24) or newer")

    if args.apk:
        step(f"Installing {args.apk}")
        out = a.run("install", "-r", os.path.abspath(args.apk), check=False, timeout=600)
        print(f"    {out.splitlines()[-1] if out else ''}")
        if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in out:
            sys.exit(f"    an app with the same id but another signature is installed: uninstall it first\n"
                     f"    (adb uninstall {args.package}), then run this again")
        if "Success" not in out:
            sys.exit("    install failed")
    if args.package not in a.shell(f"pm list packages {args.package}", check=False):
        sys.exit(f"    {args.package} is not installed: pass --apk <file>")

    component = f"{args.package}/{LISTENER}"
    before = a.shell("settings get secure enabled_notification_listeners", check=False).strip()
    if tv_listeners.contains(before, component):
        before = tv_listeners.remove(before, component) or "null"   # rerun: the value without our entry
    grant_listener(a, sdk, component)
    overlay = grant_overlay(a, sdk, args.package)
    record(serial or "default", args.package, sdk, before, overlay)
    if "youtube" not in a.shell("pm list packages youtube", check=False):
        print("    note: no YouTube app found; YouTube entries can't play (library songs work)")

    if not args.grant_only:
        step("Starting the app")
        a.shell(f"am start -n {args.package}/{ACTIVITY}", check=False)
        print("    done: the TV shows the lobby and finds the server by itself (same network, server running)")


if __name__ == "__main__":
    main()
