"""Phase 3 server pieces for the TV: UDP discovery and the lobby QR code."""
import json
import socket

from fastapi.testclient import TestClient

from server.app import create_app, phone_url
from server.discovery import REQUEST, Responder
from tests.test_api import cfg  # noqa: F401  (fixture)


def test_discovery_answers_with_the_http_port():
    r = Responder(http_port=8765, udp_port=0, host="127.0.0.1").start()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(2)
            s.sendto(b"something else", ("127.0.0.1", r.port))
            s.sendto(REQUEST, ("127.0.0.1", r.port))
            data, addr = s.recvfrom(512)   # only the real request is answered
        assert json.loads(data) == {"service": "karaoke", "port": 8765}
        assert addr == ("127.0.0.1", r.port)
    finally:
        r.stop()


def test_qr_png_encodes_the_phone_url_from_the_host_header(cfg):
    with TestClient(create_app(cfg, watch_library=False)) as c:
        r = c.get("/qr.png", headers={"Host": "192.168.1.20:8765"})
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert r.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_phone_url_uses_the_host_header():
    class Req:
        headers = {"host": "192.168.1.20:8765"}
    assert phone_url(Req()) == "http://192.168.1.20:8765/"
