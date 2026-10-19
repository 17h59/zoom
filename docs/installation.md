# Installation

```
./install.sh
```

Linux uniquement (PulseAudio est requis). Le script est **idempotent** : on peut le relancer sans risque.

## Ce que fait `install.sh`

1. Installe les paquets système manquants : Python ≥ 3.11 avec `venv`, `ffmpeg`, `curl`, `pactl` (PulseAudio), un serveur PulseAudio ou PipeWire-pulse, et `xvfb` (optionnel : sert à `--headed`). Gestionnaires pris en charge : apt, dnf, pacman.
2. Crée l'environnement Python `.venv/` et installe le projet (`pip install -e .`, qui tire Playwright, FastAPI, uvicorn et yt-dlp).
3. Télécharge Chromium (Playwright). Sous Debian/Ubuntu, installe aussi ses bibliothèques système.
4. Crée le dossier `sounds/` et le lien `~/.local/bin/zoombots`.
5. Vérifie `zoombots --help`, `ffmpeg` et `pactl`.

Un `.venv` cassé (Python déplacé ou supprimé) est recréé. Le script lance `playwright install-deps` **en root** depuis ce venv sous Debian/Ubuntu : si le dossier du projet est modifiable par d'autres que toi, n'exécute pas `install.sh` avec `sudo`.

## Options

| Option | Effet |
|---|---|
| `--server` | Installe aussi Caddy (HTTPS automatique) pour [héberger pour d'autres](server.md) ; en terminal interactif, le script pose la question |
| `--no-system-deps` | N'installe aucun paquet système |
| `--no-browser` | Ne télécharge pas Chromium |
| `-h`, `--help` | Aide |

## Cas particuliers

- **`sudo ./install.sh`** : le venv et le lien `~/.local/bin/zoombots` sont créés pour l'utilisateur qui a lancé `sudo`, pas pour root.
- **Sans droits root** : le script n'installe que ce qu'il peut ; s'il manque un paquet obligatoire, il le dit et s'arrête (installe-le à la main puis relance avec `--no-system-deps`).
- **Python < 3.11** (Ubuntu 22.04) : le script cherche `python3.12`, `python3.11`…, puis s'arrête avec une consigne ; le [Dockerfile](docker.md) évite le problème.
- **Autres distributions que Debian/Ubuntu** : les bibliothèques système de Chromium ne sont pas installées automatiquement ; si Chromium ne démarre pas, voir [Dépannage](troubleshooting.md).
- **`~/.local/bin` absent du `PATH`** : le script l'indique ; ajoute `export PATH="$HOME/.local/bin:$PATH"` à ton profil.
- **`--server`** télécharge le binaire Caddy officiel (en HTTPS uniquement, architectures amd64, arm64 et armv7) dans `~/.local/bin/caddy` et lui donne le droit d'écouter sur les ports 80/443 sans être root (`setcap`, ce qui demande `sudo`). Le binaire est celui de la version courante, **sans vérification d'empreinte** : le script s'assure seulement qu'il s'exécute.

## Ensuite

[Modes de lancement](modes.md) : `zoombots gui` chez toi, ou `zoombots server start` pour héberger.

## Voir aussi

[Dépannage](troubleshooting.md) · [Docker](docker.md) · [Configuration](configuration.md)
