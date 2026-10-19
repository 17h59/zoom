#!/usr/bin/env bash
# Installation clef en main de zoombots (Linux). Idempotent : peut être relancé sans risque.
#
#   ./install.sh [--server] [--no-system-deps] [--no-browser]
#
# Sans option : installation pour un usage chez soi (`zoombots gui`).
# --server : ajoute Caddy (HTTPS automatique) pour héberger l'appli à plusieurs utilisateurs avec un nom de domaine.
#
# Fonctionne avec ou sans sudo ; sous `sudo ./install.sh`, l'environnement Python est créé
# pour l'utilisateur appelant (pas pour root).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEM_DEPS=1
BROWSER=1
SERVER=""   # "" : demander si le terminal est interactif ; 1 : installer ; 0 : non

usage() {
  cat <<'USAGE'
Usage : ./install.sh [--server] [--no-system-deps] [--no-browser]
  --server          installe aussi Caddy (HTTPS automatique) pour un serveur partagé avec nom de domaine
  --no-system-deps  n'installe aucun paquet système (python3 >= 3.11, ffmpeg, pulseaudio-utils, xvfb)
  --no-browser      ne télécharge pas Chromium
USAGE
}

for arg in "$@"; do
  case "$arg" in
    --no-system-deps) SYSTEM_DEPS=0 ;;
    --no-browser) BROWSER=0 ;;
    --server) SERVER=1 ;;
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
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6 || true)"
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
  command -v curl   >/dev/null || need+=(curl)
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
    as_root apt-get update -qq || die "apt-get update a échoué (dépôt cassé ?) : corriger puis relancer, ou installer à la main et utiliser --no-system-deps"
    local pk=()
    for n in "${need[@]+"${need[@]}"}"; do
      case "$n" in
        python) pk+=(python3 python3-venv python3-pip) ;;
        venv) pk+=(python3-venv python3-pip) ;;
        ffmpeg) pk+=(ffmpeg) ;;
        curl) pk+=(curl) ;;
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
        curl) pk+=(curl) ;;
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
        curl) pk+=(curl) ;;
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
if [ -x "$VENV/bin/python" ] && ! "$VENV/bin/python" -c 'import sys' >/dev/null 2>&1; then
  warn ".venv cassé (Python déplacé ou supprimé) : recréation"
  rm -rf "$VENV"
fi
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

# Serveur public : Caddy fournit le HTTPS (certificat Let's Encrypt automatique) pour un nom de domaine.
if [ -z "$SERVER" ] && [ -t 0 ] && [ -t 1 ]; then
  read -r -p "Héberger aussi l'appli pour d'autres utilisateurs, avec un nom de domaine ? (installe Caddy) [o/N] " answer || answer=""
  case "$answer" in o|O|oui|y|Y|yes) SERVER=1 ;; *) SERVER=0 ;; esac
fi

install_caddy() {
  local caddy="$BIN_DIR/caddy"
  if command -v caddy >/dev/null 2>&1; then caddy="$(command -v caddy)"; say "Caddy déjà présent ($caddy)"
  elif [ -x "$caddy" ]; then say "Caddy déjà présent ($caddy)"
  else
    # Arch et Fedora : le paquet de la distribution est le plus simple et le plus fiable (le service n'est pas activé).
    if command -v pacman >/dev/null 2>&1 && { [ "$(id -u)" -eq 0 ] || [ ${#SUDO[@]} -gt 0 ]; }; then
      say "installation de Caddy (pacman)"
      as_root pacman -S --needed --noconfirm caddy && caddy="$(command -v caddy)"
    elif command -v dnf >/dev/null 2>&1 && { [ "$(id -u)" -eq 0 ] || [ ${#SUDO[@]} -gt 0 ]; }; then
      say "installation de Caddy (dnf)"
      as_root dnf install -y caddy && caddy="$(command -v caddy)"
    fi
  fi
  if ! command -v caddy >/dev/null 2>&1 && [ ! -x "$caddy" ]; then
    command -v curl >/dev/null || die "curl est requis pour télécharger Caddy"
    local arch
    case "$(uname -m)" in x86_64|amd64) arch="amd64" ;; aarch64|arm64) arch="arm64" ;; armv7l) arch="arm&arm=7" ;; *) die "architecture non gérée pour Caddy : $(uname -m)" ;; esac
    say "téléchargement de Caddy ($arch) : le serveur le compile à la demande, cela peut prendre une minute"
    # délais et nouvelles tentatives : un serveur lent ne doit pas bloquer l'installation indéfiniment
    as_user curl --proto '=https' --tlsv1.2 -fL --progress-bar --connect-timeout 15 --max-time 300 --retry 2 \
      "https://caddyserver.com/api/download?os=linux&arch=$arch" -o "$caddy.tmp" \
      || { rm -f "$caddy.tmp"; die "téléchargement de Caddy impossible : installe-le avec ton gestionnaire de paquets (ex. pacman -S caddy), puis relance ./install.sh --server"; }
    as_user chmod +x "$caddy.tmp"
    as_user "$caddy.tmp" version >/dev/null || { rm -f "$caddy.tmp"; die "le binaire Caddy téléchargé ne fonctionne pas"; }
    as_user mv "$caddy.tmp" "$caddy"
  fi
  # autorise Caddy à écouter sur les ports 80 et 443 sans être root
  if command -v setcap >/dev/null 2>&1 && { [ "$(id -u)" -eq 0 ] || [ ${#SUDO[@]} -gt 0 ]; }; then
    as_root setcap cap_net_bind_service=+ep "$(readlink -f "$caddy")" || warn "setcap a échoué : lancer le serveur avec sudo, ou autoriser les ports 80/443"
  else
    warn "setcap indisponible : Caddy ne pourra pas utiliser les ports 80/443 sans droits root"
  fi
}
[ "$SERVER" = "1" ] && install_caddy

say "vérification"
as_user "$VENV/bin/zoombots" --help >/dev/null
command -v ffmpeg >/dev/null && ffmpeg -version >/dev/null 2>&1 || die "ffmpeg inutilisable"
command -v pactl >/dev/null || die "pactl introuvable"

echo
say "Installation terminée."
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "   Ajouter au PATH : export PATH=\"$BIN_DIR:\$PATH\"" ;; esac
cat <<MSG
   Chez toi (le plus simple) :
     zoombots gui                          # ouvre l'interface web dans ton navigateur
   En ligne de commande :
     zoombots start                        # lance le serveur (http://127.0.0.1:8000)
     zoombots spawn <id-ou-lien> -c 3      # envoie 3 bots
     zoombots play <son.mp3>               # joue sur tous les bots
   Masquer ton adresse IP : zoombots gui --proxy http://hôte:port   puis   zoombots check-ip
   Aide : zoombots --help
MSG
if [ "$SERVER" = "1" ]; then
cat <<MSG

   Serveur partagé (plusieurs utilisateurs) :
     zoombots server start                                 # par adresse IP, sans nom de domaine (HTTP)
     zoombots server start --domain bots.monsite.fr        # avec nom de domaine (HTTPS automatique)
     zoombots server dns                                   # dit quel enregistrement DNS créer chez ton registrar
   Ouvre les ports 80 et 443 (pare-feu, box ou hébergeur) pour le HTTPS.
MSG
fi
