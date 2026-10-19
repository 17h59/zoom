# zoom

Bots pilotables pour le client web Zoom : navigateur headless, micro virtuel PulseAudio, soundboard, GUI web multi-utilisateurs, API et CLI.

## Installation

```
./install.sh        # dépendances système, venv, Chromium, commande `zoombots`
zoombots gui        # interface web chez toi, ouvre le navigateur
```

En ligne de commande : `zoombots start`, `zoombots spawn <id-ou-lien> -c 3 --wait 120`, `zoombots play son.mp3`, `zoombots --help`.

**Héberger pour d'autres utilisateurs**, avec ou sans nom de domaine :

```
./install.sh --server
zoombots server start                                   # par adresse IP (HTTP)
zoombots server start --domain bots.monsite.fr          # HTTPS automatique
zoombots server dns                                     # enregistrement DNS à créer chez ton registrar
```

**Masquer l'adresse IP** de la machine qui lance les bots : `--proxy http://hôte:port` sur `gui` et `server start`, puis `zoombots check-ip`.

Le GUI (http://127.0.0.1:8000) : chaque onglet est une session isolée (déployer des bots sur plusieurs liens, glisser-déposer des sons ou ajouter une vidéo YouTube, répéter une action, piloter chaque bot). Fermer l'onglet retire ses bots. Déploiement partagé : voir « GUI web multi-utilisateurs » dans la doc.

Avec Docker : `ZOOMBOTS_TOKEN=... docker compose up`.

Documentation : [docs/README.md](docs/README.md) (installation, GUI, CLI, serveur partagé et nom de domaine, masquage de l'IP, sécurité…)
