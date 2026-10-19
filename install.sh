#!/usr/bin/env bash
# Installation clef en main de zoombots (Linux). Idempotent : peut être relancé sans risque.
#   ./install.sh [--no-system-deps] [--no-browser]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEM_DEPS=1
BROWSER=1
for arg in "$@"; do
  case "$arg" in
    --no-system-deps) SYSTEM_DEPS=0 ;;
    --no-browser) BROWSER=0 ;;
    -h|--help) sed -n '2,3p' "$0"; exit 0 ;;
    *) echo "option inconnue: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merreur:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Linux" ] || die "Linux uniquement (PulseAudio requis)."

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 && SUDO="sudo" || SUDO=""
fi

install_system_deps() {
  local missing=()
  command -v python3 >/dev/null || missing+=(python3)
  command -v ffmpeg >/dev/null || missing+=(ffmpeg)
  command -v pactl >/dev/null || missing+=(pulse)
  command -v xvfb-run >/dev/null || missing+=(xvfb)
  python3 -c 'import venv, ensurepip' 2>/dev/null || missing+=(venv)
  [ ${#missing[@]} -eq 0 ] && { say "dépendances système déjà présentes"; return; }

  say "installation des dépendances système : ${missing[*]}"
  if [ "$(id -u)" -ne 0 ] && [ -z "$SUDO" ]; then
    die "droits root requis pour installer ${missing[*]} (relancer avec sudo, ou installer à la main puis --no-system-deps)"
  fi
  if command -v apt-get >/dev/null; then
    $SUDO apt-get update -qq
    $SUDO apt-get install -y -qq python3 python3-venv python3-pip ffmpeg pulseaudio pulseaudio-utils xvfb
  elif command -v dnf >/dev/null; then
    $SUDO dnf install -y python3 python3-pip ffmpeg-free pulseaudio pulseaudio-utils xorg-x11-server-Xvfb
  elif command -v pacman >/dev/null; then
    $SUDO pacman -Sy --noconfirm python python-pip ffmpeg pulseaudio xorg-server-xvfb
  else
    die "gestionnaire de paquets non reconnu : installer python3 (>=3.11), pip, venv, ffmpeg, pulseaudio-utils, xvfb"
  fi
}

[ "$SYSTEM_DEPS" -eq 1 ] && install_system_deps

command -v python3 >/dev/null || die "python3 introuvable"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "Python >= 3.11 requis (trouvé $(python3 -V))"

say "environnement Python (.venv)"
[ -x "$ROOT/.venv/bin/python" ] || python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install -q --upgrade pip
"$ROOT/.venv/bin/python" -m pip install -q -e "$ROOT"

if [ "$BROWSER" -eq 1 ]; then
  say "téléchargement de Chromium (Playwright)"
  if [ "$(id -u)" -eq 0 ] || [ -n "$SUDO" ]; then
    # --with-deps installe les bibliothèques système de Chromium
    if [ "$(id -u)" -eq 0 ]; then
      "$ROOT/.venv/bin/python" -m playwright install --with-deps chromium
    else
      $SUDO env "PATH=$PATH" "$ROOT/.venv/bin/python" -m playwright install-deps chromium
      "$ROOT/.venv/bin/python" -m playwright install chromium
    fi
  else
    "$ROOT/.venv/bin/python" -m playwright install chromium
  fi
fi

mkdir -p "$ROOT/sounds"

# Commande `zoombots` accessible partout
BIN_DIR="${HOME}/.local/bin"
mkdir -p "$BIN_DIR"
ln -sf "$ROOT/.venv/bin/zoombots" "$BIN_DIR/zoombots"

say "vérification"
"$ROOT/.venv/bin/zoombots" --help >/dev/null
ffmpeg -version >/dev/null 2>&1 || die "ffmpeg inutilisable"

echo
say "Installation terminée."
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "   Ajouter au PATH : export PATH=\"$BIN_DIR:\$PATH\"" ;; esac
cat <<MSG
   1. Dépose des fichiers audio dans : $ROOT/sounds
   2. zoombots start                      # lance le serveur (http://127.0.0.1:8000)
   3. zoombots spawn <id-ou-lien> -c 3    # envoie 3 bots
   4. zoombots play <son.mp3>             # joue sur tous les bots
   Aide : zoombots --help
MSG
