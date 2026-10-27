#!/usr/bin/env bash
# Removes Karaoke Party from this Linux PC: undoes what scripts/install.sh recorded in data/install-manifest.json.
#   1. stops the server and worker of THIS installation (the systemd user service, and processes run from its .venv)
#   2. removes the systemd user service; asked first (sudo): the ufw rules and linger it added
#   3. asked first, with sizes: deletes .venv, the data folder and the model downloads the worker made
#   4. asked first: removes tools the installer added (uv, its Python); never ones that were already there
# The music folder is never touched. Without a manifest (installs from before manifests), it detects the service and
# the folders; tools and model caches that can't be attributed are listed but left alone. TV: scripts/tv_uninstall.py.
#
# Usage: scripts/uninstall.sh [--yes] [--keep-data] [--keep-tools] [--keep-linger]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="karaoke-party.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
YES=0; KEEP_DATA=0; KEEP_TOOLS=0; KEEP_LINGER=0
for arg in "$@"; do
    case "$arg" in
        --yes) YES=1 ;;
        --keep-data) KEEP_DATA=1 ;;
        --keep-tools) KEEP_TOOLS=1 ;;
        --keep-linger) KEEP_LINGER=1 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

step() { printf '\033[36m==> %s\033[0m\n' "$*"; }
ok() { printf '    \033[32m%s\033[0m\n' "$*"; }
warn() { printf '    \033[33m%s\033[0m\n' "$*"; }
ask() {
    if [[ $YES == 1 ]]; then echo "$1 (y/N) y  [--yes]"; return 0; fi
    local a; read -r -p "$1 (y/N) " a || true; [[ "$a" =~ ^[yY] ]]
}
# the manifest helper is standard-library Python: any python3 works, also after .venv is gone
PY="$(command -v python3 || true)"; [[ -z "$PY" && -x "$ROOT/.venv/bin/python" ]] && PY="$ROOT/.venv/bin/python"
mget() { [[ -n "$PY" ]] && "$PY" "$ROOT/server/install_manifest.py" get "$1" || echo null; }
# list items of a JSON array, one per line (strings as-is, objects as compact JSON)
jitems() { "$PY" -c 'import json,sys; [print(x if isinstance(x,str) else json.dumps(x)) for x in (json.loads(sys.stdin.read()) or [])]'; }
jfield() { "$PY" -c "import json,sys; v=json.loads(sys.stdin.read()).get('$1'); print('' if v is None else (str(v).lower() if isinstance(v,bool) else v))"; }

env_value() {
    [[ -f "$ROOT/.env" ]] || return 0
    sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ROOT/.env" | tail -n1 | tr -d '\r'
}
abspath() { [[ -z "$1" ]] && return 0; [[ "$1" = /* ]] && realpath -m "$1" || realpath -m "$ROOT/$1"; }
# true if $1 is the music folder, inside it, or contains it
touches_music() {
    [[ -z "$MUSIC" ]] && return 1
    local p; p="$(abspath "$1")"
    [[ "$p" == "$MUSIC" || "$p" == "$MUSIC"/* || "$MUSIC" == "$p"/* ]]
}

MUSIC="$(abspath "$(env_value MUSIC_PATH)")"
MANIFEST="$ROOT/data/install-manifest.json"
HAVE_MANIFEST=0; [[ -f "$MANIFEST" && -n "$PY" ]] && HAVE_MANIFEST=1

DATA_DIR="$(mget data_dir | "$PY" -c 'import json,sys; print(json.loads(sys.stdin.read()) or "")' 2>/dev/null || true)"
[[ -z "$DATA_DIR" ]] && { DATA_DIR="$(env_value DATA_DIR)"; DATA_DIR="$(abspath "${DATA_DIR:-data}")"; }

FOLDERS=("$ROOT/.venv" "$ROOT/data")
[[ "$(abspath "$DATA_DIR")" != "$ROOT/data" ]] && FOLDERS+=("$DATA_DIR")
CACHES=(); TOOLS=(); UFW=()
if [[ $HAVE_MANIFEST == 1 ]]; then
    while IFS= read -r c; do [[ -n "$c" ]] && CACHES+=("$c"); done < <(mget pc.model_caches | jitems)
    while IFS= read -r t; do
        [[ -n "$t" && "$(echo "$t" | jfield installed_by_us)" == "true" ]] && TOOLS+=("$t")
    done < <(mget pc.tools | jitems)
    while IFS= read -r r; do [[ -n "$r" ]] && UFW+=("$r"); done < <(mget pc.ufw_rules | jitems)
    LINGER="$(mget pc.linger_enabled)"
else
    LINGER=null
fi
SERVICE_FILE="$UNIT_DIR/$SERVICE"
# processes run from this installation's .venv
mapfile -t PIDS < <(pgrep -f "$ROOT/.venv/bin/python" || true)

step "Karaoke Party in $ROOT"
if [[ $HAVE_MANIFEST == 1 ]]; then ok "install manifest: $MANIFEST"; else warn "no install manifest (installed before manifests): detecting what is there"; fi
echo
echo "This will remove:"
echo "  processes:       ${PIDS[*]:-none running}"
[[ -f "$SERVICE_FILE" ]] && echo "  service:         $SERVICE (systemd user service)" || echo "  service:         none"
for r in "${UFW[@]}"; do echo "  ufw rule:        allow $r (sudo)"; done
[[ "$LINGER" == "true" ]] && echo "  linger:          loginctl disable-linger $USER (sudo; enabled by the installer)"
DEL=()
for f in "${FOLDERS[@]}" "${CACHES[@]}"; do
    [[ -e "$f" ]] || continue
    if touches_music "$f"; then warn "SKIPPED (inside or containing the music folder): $f"; continue; fi
    DEL+=("$f"); echo "  folder:          $f  ($(du -sh "$f" 2>/dev/null | cut -f1))"
done
for t in "${TOOLS[@]}"; do echo "  tool (optional): $(echo "$t" | jfield name) at $(echo "$t" | jfield path) (installed by the installer)"; done
[[ $HAVE_MANIFEST == 0 ]] && echo "  model caches:    unknown without a manifest; they may be shared with other apps, so they stay"
[[ -n "$MUSIC" ]] && echo "  never touched:   your music folder $MUSIC"
echo
ask "Go ahead?" || { warn "nothing changed"; exit 0; }

step "Stopping server and worker"
if [[ -f "$SERVICE_FILE" ]]; then systemctl --user disable --now "$SERVICE" 2>/dev/null || true; fi
for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null && ok "stopped $pid" || true; done
sleep 2
mapfile -t LEFT < <(pgrep -f "$ROOT/.venv/bin/python" || true)
for pid in "${LEFT[@]}"; do kill -9 "$pid" 2>/dev/null || true; done

if [[ -f "$SERVICE_FILE" ]]; then
    rm -f "$SERVICE_FILE"
    systemctl --user daemon-reload 2>/dev/null || true
    ok "removed $SERVICE"
fi

if (( ${#UFW[@]} )) && ask "Remove the ufw rules the installer added (sudo)?"; then
    for r in "${UFW[@]}"; do
        # shellcheck disable=SC2086
        sudo ufw delete allow $r && ok "removed ufw rule: allow $r"
    done
fi
if [[ "$LINGER" == "true" && $KEEP_LINGER == 0 ]] && ask "Disable linger for $USER again (sudo; the installer enabled it)?"; then
    sudo loginctl disable-linger "$USER" && ok "linger disabled"
fi

if (( ${#DEL[@]} )) && [[ $KEEP_DATA == 1 ]]; then
    warn "folders kept (--keep-data)"
elif (( ${#DEL[@]} )); then
    step "Deleting $(du -shc "${DEL[@]}" 2>/dev/null | tail -n1 | cut -f1) (.venv, data incl. the database, downloaded models)"
    if ask "    Delete these folders? The song database and processed songs are lost (export stats first if you want them)."; then
        for f in "${DEL[@]}"; do rm -rf -- "$f" && ok "deleted $f"; done
    else
        warn "folders kept"
    fi
fi

if (( ${#TOOLS[@]} )) && [[ $KEEP_TOOLS == 1 ]]; then
    warn "tools kept (--keep-tools)"
elif (( ${#TOOLS[@]} )) && ask "Remove the tools the installer added? (ones you had before stay)"; then
    UVBIN=""
    for t in "${TOOLS[@]}"; do [[ "$(echo "$t" | jfield name)" == uv ]] && UVBIN="$(echo "$t" | jfield path)"; done
    [[ -z "$UVBIN" ]] && UVBIN="$(command -v uv || true)"
    for t in "${TOOLS[@]}"; do
        name="$(echo "$t" | jfield name)"; path="$(echo "$t" | jfield path)"
        case "$name" in
            python) [[ -x "$UVBIN" ]] && "$UVBIN" python uninstall 3.11 >/dev/null 2>&1 && ok "removed the uv-managed Python 3.11" ;;
            uv) ;;   # last, after its Python and cache
            *) touches_music "$path" && { warn "SKIPPED (music folder): $path"; continue; }
               rm -rf -- "$path" && ok "removed $name: $path" ;;
        esac
    done
    for t in "${TOOLS[@]}"; do
        if [[ "$(echo "$t" | jfield name)" == uv && -x "$UVBIN" ]]; then
            "$UVBIN" cache clean >/dev/null 2>&1 || true
            dir="$(dirname "$UVBIN")"
            rm -f "$dir/uv" "$dir/uvx"
            ok "removed uv (and its cache) from $dir"
        fi
    done
fi

echo
printf '\033[32mDone. The repository folder itself (code and your .env with the PIN) is still there: delete it when you like.\033[0m\n'
echo "For the TV: python3 scripts/tv_uninstall.py --tv <TV IP>   (see UNINSTALL.md)"
