# zoom

Bots pilotables pour le client web Zoom : navigateur headless, micro virtuel PulseAudio, soundboard, API, dashboard et CLI.

## Installation

```
./install.sh        # dépendances système, venv, Chromium, commande `zoombots`
zoombots start      # http://127.0.0.1:8000
zoombots spawn <id-ou-lien> -c 3 --wait 120
zoombots play son.mp3
zoombots --help
```

Avec Docker : `ZOOMBOTS_TOKEN=... docker compose up`.

Documentation technique : [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
