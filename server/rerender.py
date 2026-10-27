"""Re-render every cached render (after a loudness or render-settings change). Stems and lyrics are kept.

    uv run python -m server.rerender                  re-render everything
    uv run python -m server.rerender --missing-only   only queue vocal levels missing for a cached key

The running worker (or the next one started) picks the queued jobs up. Only files in data/renders are deleted.
"""
import sys
from pathlib import Path

from . import config, db, state


def main():
    cfg = config.load()
    db.init(cfg.db_path)
    conn = db.connect(cfg.db_path)
    if "--missing-only" in sys.argv[1:]:
        print(f"queued {state.queue_missing_levels(conn)} missing renders")
        return
    paths = state.requeue_all_renders(conn)
    renders = cfg.renders_dir.resolve()
    deleted = 0
    for p in map(Path, paths):
        if p.resolve().parent == renders and p.is_file():   # never touch anything outside data/renders
            p.unlink()
            deleted += 1
    print(f"queued {len(paths)} renders again, deleted {deleted} old files")


if __name__ == "__main__":
    main()
