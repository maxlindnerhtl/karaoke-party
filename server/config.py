"""Configuration from .env. See .env.example for the keys."""
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

FFMPEG_HINT = {
    "win32": "Run scripts\\install.ps1 (it downloads an ffmpeg build with rubberband and sets FFMPEG_PATH), or get "
             "an ffmpeg build from https://www.gyan.dev/ffmpeg/builds/ and set FFMPEG_PATH in .env to its ffmpeg.exe.",
    "linux": "Install your distribution's ffmpeg (Debian/Ubuntu: sudo apt install ffmpeg; Fedora: sudo dnf install "
             "ffmpeg from RPM Fusion; Arch: sudo pacman -S ffmpeg), or set FFMPEG_PATH in .env to an "
             "ffmpeg built with --enable-librubberband.",
}


@dataclass(frozen=True)
class Config:
    music_path: Path
    host_pin: str
    ffmpeg: Path
    data_dir: Path
    port: int = 8765
    yt_api_key: str = ""
    discovery_port: int = 8766
    pc_mac: str = ""          # Wake-on-LAN target; empty = the MAC of the adapter with the LAN IP
    device: str = "auto"      # processing device: auto (CUDA if available, else CPU), cuda or cpu
    whisper_model: str = ""   # empty = large-v3 on CUDA, medium on CPU

    @property
    def db_path(self) -> Path:
        return self.data_dir / "karaoke.db"

    @property
    def stems_dir(self) -> Path:
        return self.data_dir / "stems"

    @property
    def lyrics_dir(self) -> Path:
        return self.data_dir / "lyrics"

    @property
    def renders_dir(self) -> Path:
        return self.data_dir / "renders"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_dirs(self):
        for d in (self.data_dir, self.stems_dir, self.lyrics_dir, self.renders_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


def find_ffmpeg() -> Path | None:
    """FFMPEG_PATH if set, else the ffmpeg on PATH."""
    if os.environ.get("FFMPEG_PATH"):
        return Path(os.environ["FFMPEG_PATH"])
    found = shutil.which("ffmpeg")
    return Path(found) if found else None


def load(env_file: Path | None = None) -> Config:
    load_dotenv(env_file or ROOT / ".env")
    missing = [k for k in ("MUSIC_PATH", "HOST_PIN") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"missing in .env: {', '.join(missing)} (see .env.example)")
    device = os.environ.get("DEVICE", "auto").strip().lower() or "auto"
    if device not in ("auto", "cuda", "cpu"):
        raise SystemExit(f"DEVICE must be auto, cuda or cpu (got {device!r})")
    data_dir = Path(os.environ.get("DATA_DIR", ROOT / "data"))
    return Config(
        music_path=Path(os.environ["MUSIC_PATH"]),
        host_pin=os.environ["HOST_PIN"],
        ffmpeg=find_ffmpeg() or Path("ffmpeg"),
        data_dir=data_dir if data_dir.is_absolute() else ROOT / data_dir,
        port=int(os.environ.get("PORT", "8765")),
        yt_api_key=os.environ.get("YT_API_KEY", ""),
        discovery_port=int(os.environ.get("DISCOVERY_PORT", "8766")),
        pc_mac=os.environ.get("PC_MAC", ""),
        device=device,
        whisper_model=os.environ.get("WHISPER_MODEL", "").strip(),
    )


def has_rubberband(ffmpeg: Path) -> bool:
    try:
        out = subprocess.run([str(ffmpeg), "-hide_banner", "-filters"], capture_output=True, text=True,
                             timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return " rubberband " in out


def ffmpeg_hint() -> str:
    return FFMPEG_HINT.get(sys.platform, FFMPEG_HINT["linux"])


def check(cfg: Config):
    """Fail fast on a setup that would only break later in the worker."""
    if not cfg.music_path.is_dir():
        raise SystemExit(f"MUSIC_PATH is not a folder: {cfg.music_path}")
    if not cfg.ffmpeg.is_absolute() and not shutil.which(str(cfg.ffmpeg)):
        raise SystemExit(f"ffmpeg not found (FFMPEG_PATH is not set and there is no ffmpeg on PATH).\n{ffmpeg_hint()}")
    if cfg.ffmpeg.is_absolute() and not cfg.ffmpeg.is_file():
        raise SystemExit(f"FFMPEG_PATH not found: {cfg.ffmpeg}\n{ffmpeg_hint()}")
    if not has_rubberband(cfg.ffmpeg):
        raise SystemExit(f"{cfg.ffmpeg} has no rubberband filter (needed for key shifts; check with "
                         f"`ffmpeg -hide_banner -filters`).\n{ffmpeg_hint()}")
