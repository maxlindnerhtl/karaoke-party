"""The PC's LAN IP and the MAC address of that adapter (for Wake-on-LAN from the TV, SPEC section 9)."""
import logging
import re
import socket

log = logging.getLogger("netinfo")


def lan_ip() -> str | None:
    """The address of the interface with the default route (a UDP connect sends no packet)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))   # TEST-NET-1, never routed anywhere
            return s.getsockname()[0]
    except OSError:
        return None


def normalize_mac(mac: str) -> str | None:
    """"AA-BB-CC-DD-EE-FF", "aabb.ccdd.eeff", ... -> "aa:bb:cc:dd:ee:ff"; None if it isn't a MAC."""
    hexdigits = re.sub(r"[^0-9a-fA-F]", "", mac or "")
    if len(hexdigits) != 12 or not re.fullmatch(r"[0-9a-fA-F:\-. ]+", mac.strip()):
        return None
    return ":".join(hexdigits[i:i + 2] for i in range(0, 12, 2)).lower()


def mac_for_ip(ip: str) -> str | None:
    """MAC of the network adapter that has `ip` (psutil, works on Windows and Linux without admin rights)."""
    if not ip:
        return None
    import psutil
    try:
        addrs = psutil.net_if_addrs()
    except OSError as e:
        log.warning("MAC lookup failed: %s", e)
        return None
    for entries in addrs.values():
        if any(a.family == socket.AF_INET and a.address == ip for a in entries):
            for a in entries:
                mac = normalize_mac(a.address) if a.family == psutil.AF_LINK else None
                if mac and mac != "00:00:00:00:00:00":
                    return mac
    return None
