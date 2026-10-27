#!/usr/bin/env bash
# Karaoke Party setup on Linux. Idempotent: safe to run again. (Windows: scripts/install.ps1)
#
# User-level steps (no root):
#   1. .env from .env.example (if missing)
#   2. uv (pinned version), Python 3.11, the project env (uv sync --frozen: torch, demucs, whisperx, ...)
#   3. checks for ffmpeg with the rubberband filter (install it with your package manager)
#   4. a systemd *user* service that runs the server (~/.config/systemd/user/karaoke-party.service)
# Steps that need sudo are only printed, or run after asking:
#   - "loginctl enable-linger" so the service starts at boot without a login
#   - firewall rules (ufw or firewalld) for the server and discovery ports
#
# Usage: scripts/install.sh [--no-service] [--uninstall]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV_VERSION="0.13.5"
SERVICE="karaoke-party.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

step() { printf '\033[36m==> %s\033[0m\n' "$*"; }
ok() { printf '    \033[32m%s\033[0m\n' "$*"; }
warn() { printf '    \033[33m%s\033[0m\n' "$*"; }
ask() { local a; read -r -p "$1 (y/N) " a || true; [[ "$a" =~ ^[yY] ]]; }

env_value() {   # value of a key in .env, empty if unset
    [[ -f "$ROOT/.env" ]] || return 0
    sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ROOT/.env" | tail -n1 | tr -d '\r'
}

ports() {
    PORT="$(env_value PORT)"; PORT="${PORT:-8765}"
    DISC="$(env_value DISCOVERY_PORT)"; DISC="${DISC:-8766}"
}

uninstall() {
    step "Removing the systemd user service"
    systemctl --user disable --now "$SERVICE" 2>/dev/null || true
    rm -f "$UNIT_DIR/$SERVICE"
    systemctl --user daemon-reload || true
    ok "removed $SERVICE (files, .env and data/ are left alone)"
    ports
    warn "If you added firewall rules for ports $PORT/$DISC, remove them yourself:"
    warn "  ufw: sudo ufw status numbered, then sudo ufw delete <number> for each 'Karaoke Party' rule"
    warn "  firewalld: sudo firewall-cmd --permanent --remove-port=$PORT/tcp --remove-port=$DISC/udp && sudo firewall-cmd --reload"
}

firewall() {
    ports
    step "Firewall (TCP $PORT for phones and the TV, UDP $DISC for TV discovery)"
    if command -v firewall-cmd >/dev/null && systemctl is-active --quiet firewalld; then
        warn "firewalld is active. Allow the ports in your home zone, e.g.:"
        warn "  sudo firewall-cmd --permanent --add-port=$PORT/tcp --add-port=$DISC/udp && sudo firewall-cmd --reload"
    elif command -v ufw >/dev/null; then
        warn "If ufw is active, these rules allow the ports from a 192.168.x.x LAN (adjust for 10.x / 172.16.x):"
        warn "  sudo ufw allow proto tcp from 192.168.0.0/16 to any port $PORT comment 'Karaoke Party'"
        warn "  sudo ufw allow proto udp from 192.168.0.0/16 to any port $DISC comment 'Karaoke Party'"
        if ask "    Run them now (sudo)?"; then
            sudo ufw allow proto tcp from 192.168.0.0/16 to any port "$PORT" comment "Karaoke Party"
            sudo ufw allow proto udp from 192.168.0.0/16 to any port "$DISC" comment "Karaoke Party"
            ok "ufw rules added"
        fi
    else
        ok "no ufw/firewalld found; nothing to do"
    fi
}

if [[ "${1:-}" == "--uninstall" ]]; then uninstall; exit 0; fi

step ".env"
if [[ ! -f "$ROOT/.env" ]]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
    warn ".env created from .env.example: fill in MUSIC_PATH, HOST_PIN and YT_API_KEY."
else
    ok ".env exists"
fi

step "uv $UV_VERSION"
UV="$(command -v uv || true)"
[[ -z "$UV" && -x "$HOME/.local/bin/uv" ]] && UV="$HOME/.local/bin/uv"
if [[ -z "$UV" ]]; then
    # the official installer, pinned; installs to ~/.local/bin (no root)
    curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | sh
    UV="$HOME/.local/bin/uv"
fi
ok "$("$UV" --version)"

step "Python 3.11 + project environment (torch, demucs, whisperx; the first run downloads several GB)"
"$UV" python find 3.11 >/dev/null 2>&1 || "$UV" python install 3.11
(cd "$ROOT" && "$UV" sync --frozen)
GPU="$("$ROOT/.venv/bin/python" -c "import torch; print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')" 2>/dev/null || true)"
if [[ -n "$GPU" ]]; then
    ok "torch sees the GPU: $GPU (CUDA)"
else
    warn "torch sees no CUDA GPU: songs will be processed on the CPU. That works, but takes several minutes per song."
    warn "With an NVIDIA GPU: install the proprietary NVIDIA driver (570 or newer, CUDA 12.8) and run this again."
fi

step "ffmpeg with rubberband"
FFMPEG="$(env_value FFMPEG_PATH)"; FFMPEG="${FFMPEG:-$(command -v ffmpeg || true)}"
if [[ -n "$FFMPEG" ]] && "$FFMPEG" -hide_banner -filters 2>/dev/null | grep -q ' rubberband '; then
    ok "$FFMPEG has rubberband"
else
    warn "${FFMPEG:-ffmpeg (not found)} has no rubberband filter. Install your distribution's ffmpeg:"
    warn "  Debian/Ubuntu: sudo apt install ffmpeg    Fedora: sudo dnf install ffmpeg (RPM Fusion)    Arch: sudo pacman -S ffmpeg"
    warn "or set FFMPEG_PATH in .env to an ffmpeg built with --enable-librubberband. The server won't start without it."
fi

if [[ "${1:-}" != "--no-service" ]]; then
    step "systemd user service ($SERVICE)"
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/$SERVICE" <<EOF
[Unit]
Description=Karaoke Party server (API, TV discovery, processing worker)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$ROOT
ExecStart=$ROOT/.venv/bin/python -m server
Restart=on-failure
RestartSec=5
# the worker finishes its current song when the server stops
TimeoutStopSec=120

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable "$SERVICE" >/dev/null
    ok "enabled; start it with: systemctl --user start $SERVICE   (logs: journalctl --user -u $SERVICE, data/logs/)"
    if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]]; then
        warn "The service runs while you are logged in. To start it at boot without a login:"
        warn "  sudo loginctl enable-linger $USER"
    fi
fi

firewall

echo
printf '\033[32mDone. Fill in .env, then: systemctl --user start %s  (or: uv run python -m server)\033[0m\n' "$SERVICE"
