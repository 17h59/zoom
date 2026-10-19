# zoom

Bots pilotables pour le client web Zoom : navigateur headless, micro virtuel PulseAudio, soundboard, GUI web multi-utilisateurs, API et CLI.

## Installation

```
./install.sh        # dépendances système, venv, Chromium, commande `zoombots`
zoombots start      # http://127.0.0.1:8000
zoombots spawn <id-ou-lien> -c 3 --wait 120
zoombots play son.mp3
zoombots --help
```

Ouvre ensuite http://127.0.0.1:8000 : chaque onglet est une session isolée (déployer des bots sur plusieurs liens, glisser-déposer des sons ou ajouter une vidéo YouTube, répéter une action, piloter chaque bot). Fermer l'onglet retire ses bots. Déploiement partagé : voir « GUI web multi-utilisateurs » dans la doc.

Avec Docker : `ZOOMBOTS_TOKEN=... docker compose up`.

Documentation technique : [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
