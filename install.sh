#!/usr/bin/env bash
# Installation clef en main de zoombots (Linux). Idempotent : peut être relancé sans risque.
#
#   ./install.sh [--no-system-deps] [--no-browser]
#
# Fonctionne avec ou sans sudo ; sous `sudo ./install.sh`, l'environnement Python est créé
# pour l'utilisateur appelant (pas pour root).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEM_DEPS=1
BROWSER=1

usage() {
  cat <<'USAGE'
Usage : ./install.sh [--no-system-deps] [--no-browser]
  --no-system-deps  n'installe aucun paquet système (python3 >= 3.11, ffmpeg, pulseaudio-utils, xvfb)
  --no-browser      ne télécharge pas Chromium
USAGE
}

for arg in "$@"; do
  case "$arg" in
    --no-system-deps) SYSTEM_DEPS=0 ;;
    --no-browser) BROWSER=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "option inconnue: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mattention:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merreur:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Linux" ] || die "Linux uniquement (PulseAudio requis)."

# Utilisateur cible : celui qui a lancé sudo, sinon l'utilisateur courant.
TARGET_USER="$(id -un)"
RUN_AS=()
if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  TARGET_USER="$SUDO_USER"
  RUN_AS=(sudo -u "$SUDO_USER" -H)
fi
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
[ -n "$TARGET_HOME" ] || TARGET_HOME="$HOME"
as_user() { "${RUN_AS[@]+"${RUN_AS[@]}"}" "$@"; }

SUDO=()
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 && SUDO=(sudo)
fi
as_root() { "${SUDO[@]+"${SUDO[@]}"}" "$@"; }

# Cherche un Python >= 3.11 (python3 d'abord, puis les versions explicites).
find_python() {
  local c
  for c in python3 python3.13 python3.12 python3.11; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      command -v "$c"; return 0
    fi
  done
  return 1
}

have_venv() { "$1" -c 'import venv, ensurepip' 2>/dev/null; }

install_system_deps() {
  local py=""
  py="$(find_python || true)"
  local need=()
  [ -n "$py" ] || need+=(python)
  [ -z "$py" ] || have_venv "$py" || need+=(venv)
  command -v ffmpeg >/dev/null || need+=(ffmpeg)
  command -v pactl  >/dev/null || need+=(pulse)
  command -v pulseaudio >/dev/null || command -v pipewire-pulse >/dev/null || need+=(pulseserver)
  local want_xvfb=0
  command -v xvfb-run >/dev/null || want_xvfb=1

  if [ ${#need[@]} -eq 0 ] && [ "$want_xvfb" -eq 0 ]; then
    say "dépendances système déjà présentes"; return
  fi
  if [ "$(id -u)" -ne 0 ] && [ ${#SUDO[@]} -eq 0 ]; then
    [ ${#need[@]} -eq 0 ] && { warn "xvfb-run absent (--headed indisponible) ; pas de droits root pour l'installer"; return; }
    die "droits root requis pour installer : ${need[*]} (relancer avec sudo, ou installer à la main puis --no-system-deps)"
  fi

  say "installation des dépendances système : ${need[*]:-} $([ "$want_xvfb" -eq 1 ] && echo xvfb)"
  if command -v apt-get >/dev/null; then
    as_root apt-get update -qq
    local pk=()
    for n in "${need[@]+"${need[@]}"}"; do
      case "$n" in
        python) pk+=(python3 python3-venv python3-pip) ;;
        venv) pk+=(python3-venv python3-pip) ;;
        ffmpeg) pk+=(ffmpeg) ;;
        pulse) pk+=(pulseaudio-utils) ;;
        pulseserver) pk+=(pulseaudio) ;;
      esac
    done
    [ ${#pk[@]} -eq 0 ] || as_root apt-get install -y -qq "${pk[@]}" || die "échec de l'installation de : ${pk[*]}"
    [ "$want_xvfb" -eq 0 ] || as_root apt-get install -y -qq xvfb || warn "xvfb non installé (--headed indisponible)"
  elif command -v dnf >/dev/null; then
    local pk=()
    for n in "${need[@]+"${need[@]}"}"; do
      case "$n" in
        python|venv) pk+=(python3 python3-pip) ;;
        ffmpeg) pk+=(ffmpeg-free) ;;
        pulse) pk+=(pulseaudio-utils) ;;
        pulseserver) pk+=(pipewire-pulseaudio) ;;  # évite le conflit pulseaudio / pipewire-pulseaudio des Fedora récentes
      esac
    done
    [ ${#pk[@]} -eq 0 ] || as_root dnf install -y "${pk[@]}" || die "échec de l'installation de : ${pk[*]}"
    [ "$want_xvfb" -eq 0 ] || as_root dnf install -y xorg-x11-server-Xvfb || warn "xvfb non installé"
  elif command -v pacman >/dev/null; then
    local pk=()
    for n in "${need[@]+"${need[@]}"}"; do
      case "$n" in
        python|venv) pk+=(python python-pip) ;;
        ffmpeg) pk+=(ffmpeg) ;;
        pulse|pulseserver) pk+=(libpulse pulseaudio) ;;
      esac
    done
    # pas de -y : un rafraîchissement sans mise à jour complète (-Syu) est déconseillé sur Arch
    [ ${#pk[@]} -eq 0 ] || as_root pacman -S --needed --noconfirm "${pk[@]}" || die "échec de l'installation de : ${pk[*]} (essayer pacman -Syu)"
    [ "$want_xvfb" -eq 0 ] || as_root pacman -S --needed --noconfirm xorg-server-xvfb || warn "xvfb non installé"
  else
    die "gestionnaire de paquets non reconnu : installer python3 (>=3.11) avec venv, ffmpeg, pulseaudio-utils, xvfb, puis relancer avec --no-system-deps"
  fi
}

[ "$SYSTEM_DEPS" -eq 1 ] && install_system_deps

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
  found="$(command -v python3 >/dev/null && python3 -V 2>&1 || echo "python3 absent")"
  die "Python >= 3.11 requis ($found). Sur Ubuntu 22.04 : ajouter le PPA deadsnakes (python3.12), ou utiliser le Dockerfile."
fi
have_venv "$PYTHON" || die "le module venv de $PYTHON est manquant (paquet python3-venv)"

VENV="$ROOT/.venv"
say "environnement Python ($VENV, $("$PYTHON" -V 2>&1))"
[ -x "$VENV/bin/python" ] || as_user "$PYTHON" -m venv "$VENV"
as_user "$VENV/bin/python" -m pip install -q -e "$ROOT"

if [ "$BROWSER" -eq 1 ]; then
  say "téléchargement de Chromium (Playwright)"
  if [ -f /etc/debian_version ] && { [ "$(id -u)" -eq 0 ] || [ ${#SUDO[@]} -gt 0 ]; }; then
    # bibliothèques système de Chromium (Debian/Ubuntu uniquement)
    as_root env "PATH=$PATH" "$VENV/bin/python" -m playwright install-deps chromium \
      || warn "playwright install-deps a échoué : Chromium pourrait manquer de bibliothèques"
  else
    warn "bibliothèques système de Chromium non installées automatiquement (distribution non Debian/Ubuntu ou pas de sudo)"
  fi
  as_user "$VENV/bin/python" -m playwright install chromium
fi

as_user mkdir -p "$ROOT/sounds"

# Commande `zoombots` accessible partout
BIN_DIR="$TARGET_HOME/.local/bin"
as_user mkdir -p "$BIN_DIR"
if [ -e "$BIN_DIR/zoombots" ] && [ ! -L "$BIN_DIR/zoombots" ]; then
  warn "$BIN_DIR/zoombots existe et n'est pas un lien : laissé tel quel. Utiliser $VENV/bin/zoombots"
else
  as_user ln -sf "$VENV/bin/zoombots" "$BIN_DIR/zoombots"
fi

say "vérification"
as_user "$VENV/bin/zoombots" --help >/dev/null
command -v ffmpeg >/dev/null && ffmpeg -version >/dev/null 2>&1 || die "ffmpeg inutilisable"
command -v pactl >/dev/null || die "pactl introuvable"

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
