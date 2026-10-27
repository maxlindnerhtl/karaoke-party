"""UDP discovery responder (SPEC section 5.7). The TV broadcasts `KARAOKE_DISCOVER` to the discovery port and takes
the server's IP from the reply's source address; the reply says which HTTP port to use."""
import json
import logging
import socket
import threading

log = logging.getLogger("discovery")

REQUEST = b"KARAOKE_DISCOVER"


class Responder:
    def __init__(self, http_port: int, udp_port: int, host: str = "0.0.0.0"):
        self.reply = json.dumps({"service": "karaoke", "port": http_port}).encode()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, udp_port))
        self.sock.settimeout(0.5)
        self.port = self.sock.getsockname()[1]
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.run, name="discovery", daemon=True)

    def start(self):
        self.thread.start()
        log.info("listening on UDP %d", self.port)
        return self

    def run(self):
        while not self.stopping.is_set():
            try:
                data, addr = self.sock.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:   # e.g. ICMP port unreachable from an earlier reply (Windows), or the socket closed
                continue
            if data.strip() == REQUEST:
                try:
                    self.sock.sendto(self.reply, addr)
                except OSError as e:
                    log.warning("discovery reply to %s failed: %s", addr, e)

    def stop(self):
        self.stopping.set()
        self.thread.join(timeout=2)
        self.sock.close()
