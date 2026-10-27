"""Processing steps for one library song (SPEC section 6, Processing pipeline). Runs in the worker process.

Models are loaded once and stay resident between jobs. Demucs and WhisperX only ever get decoded audio
(arrays), never files, so they never shell out to an ffmpeg other than the configured one (which may lack
rubberband or decode differently).
"""
import json
import logging
import os
import time
from pathlib import Path

from . import audio, install_manifest, lyrics
from .config import Config

log = logging.getLogger("pipeline")

DEMUCS_MODEL = "htdemucs_ft"
WHISPER_MODEL = {"cuda": "large-v3", "cpu": "medium"}   # default per device; WHISPER_MODEL in .env overrides it
WHISPER_RATE = 16000
FALLBACK_LANGUAGE = "en"   # when no align model exists for the detected language


def pick_device(preference: str = "auto") -> str:
    """"cuda" if requested or available, else "cpu" (works, but a song takes many minutes instead of about one)."""
    import torch
    if preference == "cpu":
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if preference == "cuda":
        log.error("DEVICE=cuda, but torch sees no CUDA GPU (driver missing or too old?); using the CPU")
    return "cpu"


def device_info(device: str) -> dict:
    """For the host page: which device processes songs."""
    if device == "cuda":
        import torch
        return {"device": "cuda", "name": torch.cuda.get_device_name(0)}
    return {"device": "cpu", "name": "CPU"}


class Models:
    """Lazily loaded, then kept: Demucs separator, WhisperX ASR model, align models per language. Model files a load
    downloads for the first time are recorded in the install manifest, so the uninstaller can remove them (and only
    them: caches another app already had are left alone)."""

    def __init__(self, device: str = "cuda", whisper_model: str = ""):
        self.device = device
        self.whisper_model = whisper_model or WHISPER_MODEL[device]
        # float16 needs a GPU; int8 is the fast CPU path of faster-whisper
        self.compute_type = "float16" if device == "cuda" else "int8"
        self._separator = None
        self._asr = None
        self._align: dict[str, tuple] = {}

    def separator(self, callback=None):
        if self._separator is None:
            from demucs.api import Separator
            t = time.perf_counter()
            with install_manifest.track_model_downloads():
                self._separator = Separator(model=DEMUCS_MODEL, device=self.device, progress=False)
            log.info("demucs %s loaded in %.1fs", DEMUCS_MODEL, time.perf_counter() - t)
        self._separator.update_parameter(callback=callback)
        return self._separator

    def asr(self):
        if self._asr is None:
            import whisperx
            t = time.perf_counter()
            with install_manifest.track_model_downloads():
                self._asr = whisperx.load_model(self.whisper_model, self.device, compute_type=self.compute_type)
            log.info("whisper %s (%s) loaded in %.1fs", self.whisper_model, self.compute_type, time.perf_counter() - t)
        return self._asr

    def aligner(self, language: str):
        if language not in self._align:
            import whisperx
            t = time.perf_counter()
            with install_manifest.track_model_downloads():
                self._align[language] = whisperx.load_align_model(language_code=language, device=self.device)
            log.info("align model %s loaded in %.1fs", language, time.perf_counter() - t)
        return self._align[language]


def stems_dir(cfg: Config, song_id: str) -> Path:
    return cfg.stems_dir / song_id


def separate(cfg: Config, models: Models, song: dict, progress) -> Path:
    """Demucs -> vocals.wav + no_vocals.wav (sum of the other stems) in data/stems/<id>/."""
    import torch
    out = stems_dir(cfg, song["id"])
    out.mkdir(parents=True, exist_ok=True)
    wav = torch.from_numpy(audio.decode(cfg.ffmpeg, Path(song["path"]), audio.SAMPLE_RATE, 2))

    def on_progress(d):
        # bag of 4 models; each reports segment offsets over the track length
        n = max(d.get("models", 1), 1)
        done = d.get("model_idx_in_bag", 0) + d.get("segment_offset", 0) / max(d.get("audio_length", 1), 1)
        progress(min(done / n, 1.0))

    sep = models.separator(callback=on_progress)
    t = time.perf_counter()
    with torch.inference_mode():
        _, stems = sep.separate_tensor(wav, audio.SAMPLE_RATE)
    vocals = stems["vocals"].cpu().numpy()
    rest = sum(v for k, v in stems.items() if k != "vocals").cpu().numpy()
    audio.write_wav(out / "vocals.wav", vocals, sep.samplerate)
    audio.write_wav(out / "no_vocals.wav", rest, sep.samplerate)
    log.info("separated %s in %.1fs", song["id"], time.perf_counter() - t)
    if models.device == "cuda":
        torch.cuda.empty_cache()
    return out


def align_language(language: str, supported) -> str:
    """The detected language if WhisperX has an align model for it, else English."""
    if language in supported:
        return language
    log.warning("no align model for detected language %r; using %s", language, FALLBACK_LANGUAGE)
    return FALLBACK_LANGUAGE


def supported_align_languages() -> set[str]:
    from whisperx import alignment
    return set(alignment.DEFAULT_ALIGN_MODELS_TORCH) | set(alignment.DEFAULT_ALIGN_MODELS_HF)


def detect_language(asr, vocals, start: float | None, supported: set[str] | None = None) -> str:
    """Whisper looks at 30 s only. From the start of the audio that is often just an intro (one song with a long
    quiet intro was detected as Latin on near-silence), so detect from where the singing starts: the first LRC line, else the vocal onset."""
    if start is None:
        start = audio.vocal_onset(vocals, WHISPER_RATE)
    first = max(0, min(int(start * WHISPER_RATE), len(vocals) - 30 * WHISPER_RATE))
    language = asr.detect_language(vocals[first:])
    return align_language(language, supported if supported is not None else supported_align_languages())


def make_lyrics(cfg: Config, models: Models, song: dict) -> dict:
    """LRCLIB synced lyrics aligned to the vocal stem (source lrclib), else WhisperX transcription (auto)."""
    import whisperx
    vocals = audio.decode(cfg.ffmpeg, stems_dir(cfg, song["id"]) / "vocals.wav", WHISPER_RATE, 1)[0]
    duration = song["duration"] or len(vocals) / WHISPER_RATE
    t = time.perf_counter()
    try:
        hit = lyrics.lrclib_lookup(song["artist"], song["title"], song["album"], duration)
    except Exception as e:   # LRCLIB down: fall back to transcription rather than failing the song
        log.warning("LRCLIB lookup failed for %s: %s", song["id"], e)
        hit = None
    log.info("LRCLIB %s for %s in %.1fs", "hit" if hit else "miss", song["id"], time.perf_counter() - t)

    if hit and hit.get("instrumental"):
        return lyrics.document("instrumental", None, [])
    asr = models.asr()
    synced = hit and hit.get("syncedLyrics")
    if synced:
        lrc = lyrics.lrc_segments(lyrics.parse_lrc(synced), duration)
        language = detect_language(asr, vocals, lrc[0]["start"] if lrc else None)
        model, meta = models.aligner(language)
        t = time.perf_counter()
        aligned = whisperx.align(lrc, model, meta, vocals, models.device)["segments"]
        log.info("aligned %d LRC lines (%s) in %.1fs", len(lrc), language, time.perf_counter() - t)
        return lyrics.document("lrclib", language, lyrics.lines_from_lrc(lrc, aligned))

    t = time.perf_counter()
    language = detect_language(asr, vocals, None)
    result = asr.transcribe(vocals, batch_size=16, language=language)
    model, meta = models.aligner(language)
    aligned = whisperx.align(result["segments"], model, meta, vocals, models.device)["segments"]
    log.info("transcribed + aligned %s (%s) in %.1fs", song["id"], language, time.perf_counter() - t)
    return lyrics.document("auto", language, lyrics.lines_from_auto(aligned))


def save_lyrics(cfg: Config, song_id: str, doc: dict) -> Path:
    path = cfg.lyrics_dir / f"{song_id}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def render(cfg: Config, song_id: str, key_shift: int, vocal_level: int, gain_db: float) -> Path:
    out = cfg.renders_dir / f"{audio.render_id(song_id, key_shift, vocal_level)}.m4a"
    t = time.perf_counter()
    ceiling, tp = audio.render(cfg.ffmpeg, stems_dir(cfg, song_id), out, key_shift, vocal_level, gain_db)
    log.info("rendered %s in %.1fs (limiter ceiling %.2f dB, true peak %+.2f dBTP)",
             out.name, time.perf_counter() - t, ceiling, tp)
    return out


def stems_exist(cfg: Config, song_id: str) -> bool:
    d = stems_dir(cfg, song_id)
    return (d / "vocals.wav").is_file() and (d / "no_vocals.wav").is_file()


def warmup(models: Models):
    """Load every model up front so the first song doesn't pay for it."""
    models.separator()
    models.asr()
    models.aligner("en")
