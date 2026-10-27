"""Start the API server, UDP discovery and the processing worker (a supervised child process).

    uv run python -m server               API on 0.0.0.0:PORT + discovery + worker
    uv run python -m server --no-worker   API only (run `python -m server.worker` yourself)

The tray app (`python -m server.tray`, used by autostart) runs the same thing with a tray icon.
"""
import argparse

from . import config
from .runner import ServerRunner, port_free
from .worker import setup_logging


def main():
    ap = argparse.ArgumentParser(prog="python -m server")
    ap.add_argument("--no-worker", action="store_true", help="don't start the processing worker")
    args = ap.parse_args()

    cfg = config.load()
    if not port_free(cfg.port):   # before anything else: a second instance must not process jobs too
        raise SystemExit(f"port {cfg.port} is in use: is the server already running?")
    config.check(cfg)
    cfg.ensure_dirs()
    setup_logging(cfg, "server")
    runner = ServerRunner(cfg, worker=not args.no_worker)
    try:
        runner.start()
    except RuntimeError as e:
        raise SystemExit(str(e)) from None
    runner.wait()


if __name__ == "__main__":
    main()
