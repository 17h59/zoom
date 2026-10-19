# zoombots : documentation technique

Documentation de l'existant. Le projet n'est pas poursuivi au-delà de ce squelette.

## Vue d'ensemble

Un « bot » est un Chromium (Playwright) qui charge le client web Zoom, associé à un micro virtuel PulseAudio dédié. L'audio d'un fichier est décodé par ffmpeg et injecté dans le micro virtuel, que le navigateur utilise comme périphérique d'entrée.

```
Dashboard / API (FastAPI)
        │
   BotManager ── Playwright (un seul driver partagé)
        │
   Bot (1 par instance)
    ├─ Chromium (process dédié, env PULSE_SOURCE=zb_<id>_mic)
    └─ VirtualMic
         null-sink zb_<id>  ──monitor──►  remap-source zb_<id>_mic
              ▲
   ffmpeg (décodage) | paplay --device=zb_<id>
```

## Modules

| Fichier | Rôle |
|---|---|
| `zoombots/meeting.py` | `Meeting` et `parse_meeting` : extrait l'ID (9 à 11 chiffres) et le `pwd` depuis un ID brut ou un lien `zoom.us/j/...`. Construit l'URL `app.zoom.us/wc/join/<id>`. |
| `zoombots/audio.py` | `VirtualMic` : charge `module-null-sink` et `module-remap-source` via `pactl`, joue un fichier avec `ffmpeg \| paplay`, arrête les lectures (SIGTERM sur le groupe de processus), décharge les modules à la destruction. |
| `zoombots/bot.py` | `Bot` : cycle de vie (`created → joining → in_meeting → error/left`), parcours de connexion piloté par sélecteurs, `set_mic`, `play`, `stop_sounds`, `leave`. |
| `zoombots/manager.py` | `BotManager` : registre des bots, driver Playwright partagé, `spawn` (tâche asyncio par bot), `broadcast`. Liste et résolution des sons dans `sounds/` (protection contre la traversée de chemin). |
| `zoombots/api.py` | API FastAPI et service du dashboard. |
| `zoombots/static/index.html` | Dashboard sans dépendance, rafraîchi toutes les 3 s. |

## Choix techniques

- **Un process Chromium par bot** : l'environnement `PULSE_SOURCE` est fixé au lancement, ce qui impose une isolation par process, donc pas de contextes multiples dans un même navigateur.
- **`ffmpeg | paplay` plutôt que `ffmpeg -f pulse`** : mesuré, le muxer pulse de ffmpeg perd la fin du flux à la sortie, alors que `paplay` vide son buffer. ffmpeg gère tous les formats (mp3, wav, ogg, flac, m4a, opus) et le volume (`-af volume=`).
- **`spawn` asynchrone** : l'endpoint doit être `async` pour qu'`asyncio.create_task` trouve la boucle d'événements (un endpoint synchrone tourne dans un thread sans boucle).
- **Démarrage des lectures** : `play` dé-mute d'abord le bot (`auto_unmute`) via le bouton micro de la barre Zoom.

## API HTTP

| Route | Description |
|---|---|
| `GET /` | Dashboard |
| `GET /bots` | Liste et statut des bots |
| `POST /bots` | `{meeting, passcode?, name, count}` (1 à 20 bots) |
| `DELETE /bots/{id}` | Quitte et supprime |
| `POST /bots/{id}/mic` | `{on: bool}` |
| `GET /sounds` | Fichiers disponibles dans `sounds/` |
| `POST /bots/{id}/sounds/{sound}` | `{volume}` (0 à 2) |
| `POST /bots/{id}/stop` | Arrête les lectures du bot |
| `POST /broadcast/sounds/{sound}` | Joue sur tous les bots dans la réunion |

## Configuration (variables d'environnement)

`ZOOMBOTS_HOST`, `ZOOMBOTS_PORT` (défaut `127.0.0.1:8000`), `ZOOMBOTS_SOUNDS` (dossier des sons), `ZOOMBOTS_HEADED=1` (Chromium non headless, à lancer sous `xvfb-run`).

## État de validation

- **Validé** : création et destruction du micro virtuel, lecture d'un son et capture côté source virtuelle, arrêt des lectures, nettoyage des modules PulseAudio, parsing des ID et liens.
- **Non validé** : le parcours de connexion sur le vrai client web Zoom. Les sélecteurs (`#input-for-name`, `#input-for-pwd`, boutons Join/Audio, `aria-label` du micro) sont des hypothèses non vérifiées et peuvent changer côté Zoom.
- **Non validé** : le fonctionnement de Chromium avec `PULSE_SOURCE` (le test sur page locale a été interrompu).

## Limites connues

- Le Chromium de Playwright doit correspondre à la version du paquet ; un binaire existant peut être utilisé via le paramètre `executable_path` de `launch` (non exposé en configuration pour l'instant).
- `VirtualMic.playing` reste vrai quelques secondes après la fin du son (délai de vidage de `paplay`).
- Aucun état persisté : un redémarrage du serveur perd la liste des bots (les modules PulseAudio d'un process tué brutalement restent chargés).
- Pas d'authentification sur l'API (écoute en local par défaut).
