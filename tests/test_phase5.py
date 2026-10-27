"""Phase 5 fixes from the party test: language detection after an intro, one worker at a time, orphaned workers."""
import subprocess
import sys
import threading

import numpy as np

from server import audio, pipeline, worker

RATE = 16000


def test_vocal_onset_skips_a_quiet_intro():
    rng = np.random.default_rng(0)
    intro = rng.normal(0, 0.002, 40 * RATE)        # Demucs bleed during a 40 s instrumental intro
    singing = rng.normal(0, 0.2, 20 * RATE)
    assert audio.vocal_onset(np.concatenate([intro, singing]).astype(np.float32), RATE) == 40.0
    assert audio.vocal_onset(singing.astype(np.float32), RATE) == 0.0
    assert audio.vocal_onset(np.zeros(5 * RATE, np.float32), RATE) == 0.0     # silent stem
    assert audio.vocal_onset(np.zeros(100, np.float32), RATE) == 0.0          # shorter than a frame


class FakeAsr:
    def __init__(self, language):
        self.language = language
        self.seen = None

    def detect_language(self, audio):
        self.seen = len(audio)
        return self.language


def test_detect_language_starts_at_the_first_lyric_line():
    vocals = np.zeros(200 * RATE, np.float32)
    asr = FakeAsr("en")
    assert pipeline.detect_language(asr, vocals, 45.0, {"en"}) == "en"
    assert asr.seen == 155 * RATE                                  # audio from 45 s on
    pipeline.detect_language(asr, vocals, 190.0, {"en"})           # too close to the end: the last 30 s
    assert asr.seen == 30 * RATE


def test_unsupported_language_falls_back_to_english():
    # seen at a party: Whisper heard Latin in a quiet intro, and WhisperX has no align model for "la"
    assert pipeline.detect_language(FakeAsr("la"), np.zeros(60 * RATE, np.float32), 0.0, {"en", "de"}) == "en"
    assert pipeline.align_language("de", {"de", "en"}) == "de"


def test_only_one_process_holds_the_worker_lock(tmp_path):
    path = tmp_path / "worker.lock"
    first = worker.try_lock(path)
    assert first is not None
    assert worker.try_lock(path) is None                           # a second worker can't start
    stop = threading.Event()
    got = []
    t = threading.Thread(target=lambda: got.append(worker.wait_for_lock(path, stop, interval=0.05)))
    t.start()
    first.close()                                                  # the first worker exits
    t.join(5)
    assert got and got[0] is not None
    got[0].close()


def test_wait_for_lock_gives_up_on_stop(tmp_path):
    held = worker.try_lock(tmp_path / "worker.lock")
    stop = threading.Event()
    stop.set()
    assert worker.wait_for_lock(tmp_path / "worker.lock", stop, interval=0.05) is None
    held.close()


def test_watch_parent_notices_the_parent_exit():
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.5)"])
    gone = threading.Event()
    worker.watch_parent(parent.pid, gone.set)
    assert not gone.is_set()
    assert gone.wait(10)
    parent.wait()
    late = threading.Event()
    worker.watch_parent(parent.pid, late.set)                      # already gone
    assert late.wait(1)
