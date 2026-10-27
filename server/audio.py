"""ffmpeg helpers: decoding, loudness measurement, render commands and render cache keys.

Every call uses the configured ffmpeg (FFMPEG_PATH, or the one on PATH), checked for rubberband at startup.
"""
import json
import os
import re
import subprocess
from pathlib import Path

import numpy as np

SAMPLE_RATE = 44100
TARGET_LUFS = -16.0
MAX_TRUE_PEAK = -1.0
# The limiter works at 4x oversampling, so it catches inter-sample peaks. Its ceiling starts 0.5 dB below
# MAX_TRUE_PEAK because AAC encoding adds overshoot (0.1 to 1 dB on the test songs); every render is measured
# after encoding and redone with a lower ceiling if it still peaks above MAX_TRUE_PEAK (see render()).
LIMITER_CEILING = -1.5
OVERSAMPLE_RATE = SAMPLE_RATE * 4
RENDER_ATTEMPTS = 3


def limiter(ceiling_db: float = LIMITER_CEILING) -> str:
    return (f"aresample={OVERSAMPLE_RATE},"
            f"alimiter=limit={10 ** (ceiling_db / 20):.4f}:attack=5:release=100:level=false,"
            f"aresample={SAMPLE_RATE}")


LIMITER = limiter()
RUBBERBAND = "formant=preserved:pitchq=quality:channels=together:transients=mixed"

_RENDER_ID = re.compile(r"^([0-9a-f]{40})_k([+-][0-3])_v(0|5|10|15)$")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def render_id(song_id: str, key_shift: int, vocal_level: int) -> str:
    """Cache key (song_id, key_shift, vocal_level) as a file-name-safe id, e.g. `<sha1>_k+2_v5`."""
    return f"{song_id}_k{key_shift:+d}_v{vocal_level}"


def parse_render_id(rid: str) -> tuple[str, int, int] | None:
    m = _RENDER_ID.match(rid)
    return (m[1], int(m[2]), int(m[3])) if m else None


def pitch_ratio(key_shift: int) -> float:
    return 2 ** (key_shift / 12)


def decode(ffmpeg: Path, src: Path, rate: int, channels: int) -> np.ndarray:
    """Decode any audio file to float32, shape (channels, samples)."""
    raw = subprocess.run(
        [str(ffmpeg), "-nostdin", "-loglevel", "error", "-i", str(src), "-map", "0:a:0",
         "-f", "f32le", "-ac", str(channels), "-ar", str(rate), "-"],
        check=True, capture_output=True, creationflags=_NO_WINDOW).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, channels).T.copy()


def vocal_onset(vocals: np.ndarray, rate: int, rel_db: float = -20.0) -> float:
    """Seconds until the vocal stem first gets loud: the first 1 s frame whose RMS is within `rel_db` of the
    loudest frame. Demucs leaves faint bleed in the vocal stem during intros, so a relative threshold."""
    n = len(vocals) // rate
    if n == 0:
        return 0.0
    rms = np.sqrt(np.mean(np.square(vocals[:n * rate].reshape(n, rate), dtype=np.float64), axis=1))
    if rms.max() <= 0:
        return 0.0
    return float(np.argmax(rms >= rms.max() * 10 ** (rel_db / 20)))


def write_wav(path: Path, audio: np.ndarray, rate: int = SAMPLE_RATE):
    """Write (channels, samples) float32 as a float WAV (no clipping of loud stem sums)."""
    from scipy.io import wavfile
    tmp = path.with_suffix(".tmp.wav")
    wavfile.write(tmp, rate, np.ascontiguousarray(audio.T, dtype=np.float32))
    os.replace(tmp, path)


def measure_filter(key_shift: int) -> str:
    """Analysis chain for the instrumental at a key: rubberband changes loudness by 1-2 dB, so each key is
    measured with its own shift applied."""
    shift = f"rubberband=pitch={pitch_ratio(key_shift):.6f}:{RUBBERBAND}," if key_shift else ""
    return f"{shift}loudnorm=I={TARGET_LUFS}:TP={MAX_TRUE_PEAK}:print_format=json"


def measure_gain(ffmpeg: Path, stems: Path, key_shift: int) -> float:
    """Gain in dB that brings the instrumental at `key_shift` to -16 LUFS (the render's limiter takes care of
    the peaks)."""
    err = subprocess.run(
        [str(ffmpeg), "-nostdin", "-hide_banner", "-i", str(stems / "no_vocals.wav"),
         "-af", measure_filter(key_shift), "-f", "null", "-"],
        check=True, capture_output=True, text=True, creationflags=_NO_WINDOW).stderr
    stats = json.loads(err[err.rindex("{"):err.rindex("}") + 1])
    return gain_for(float(stats["input_i"]))


def gain_for(integrated_lufs: float) -> float:
    if integrated_lufs == float("-inf"):   # silence
        return 0.0
    return round(TARGET_LUFS - integrated_lufs, 2)


def render_filter(key_shift: int, vocal_level: int, gain_db: float, ceiling_db: float = LIMITER_CEILING) -> str:
    """filter_complex for inputs [0]=instrumental, [1]=vocals."""
    if vocal_level:
        chain = f"[1:a]volume={vocal_level / 100:.2f}[v];[0:a][v]amix=inputs=2:normalize=0"
    else:
        chain = "[0:a]anull"
    if key_shift:
        chain += f",rubberband=pitch={pitch_ratio(key_shift):.6f}:{RUBBERBAND}"
    return chain + f",volume={gain_db:+.2f}dB,{limiter(ceiling_db)}[out]"


def render_cmd(ffmpeg: Path, stems: Path, out: Path, key_shift: int, vocal_level: int, gain_db: float,
               ceiling_db: float = LIMITER_CEILING) -> list[str]:
    return [str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(stems / "no_vocals.wav"), "-i", str(stems / "vocals.wav"),
            "-filter_complex", render_filter(key_shift, vocal_level, gain_db, ceiling_db), "-map", "[out]",
            "-ar", str(SAMPLE_RATE), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-f", "mp4", str(out)]


def true_peak(ffmpeg: Path, src: Path) -> float:
    """True peak (dBTP, 4x oversampled) of a decoded file."""
    err = subprocess.run(
        [str(ffmpeg), "-nostdin", "-hide_banner", "-i", str(src), "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        check=True, capture_output=True, text=True, creationflags=_NO_WINDOW).stderr
    return float(json.loads(err[err.rindex("{"):err.rindex("}") + 1])["input_tp"])


def next_ceiling(ceiling_db: float, measured_tp: float) -> float | None:
    """Lower ceiling for another attempt when the encoded render peaks above MAX_TRUE_PEAK, else None."""
    if measured_tp <= MAX_TRUE_PEAK:
        return None
    return round(ceiling_db - (measured_tp - MAX_TRUE_PEAK) - 0.2, 2)


def render(ffmpeg: Path, stems: Path, out: Path, key_shift: int, vocal_level: int, gain_db: float) -> tuple[float, float]:
    """Render, check the encoded file's true peak, and redo with a lower limiter ceiling if it is above
    MAX_TRUE_PEAK. Returns (ceiling used, measured true peak)."""
    tmp = out.with_suffix(".tmp")
    ceiling = LIMITER_CEILING
    for _ in range(RENDER_ATTEMPTS):
        subprocess.run(render_cmd(ffmpeg, stems, tmp, key_shift, vocal_level, gain_db, ceiling),
                       check=True, capture_output=True, creationflags=_NO_WINDOW)
        tp = true_peak(ffmpeg, tmp)
        lower = next_ceiling(ceiling, tp)
        if lower is None:
            break
        ceiling = lower
    else:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"true peak {tp:+.2f} dBTP still above {MAX_TRUE_PEAK} after {RENDER_ATTEMPTS} renders")
    os.replace(tmp, out)
    return ceiling, tp
