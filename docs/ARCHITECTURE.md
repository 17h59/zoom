# zoombots : documentation technique

Documentation de l'existant. Les fonctionnalités du tableau « Fonctionnalités manquantes » sont désormais implémentées, avec les réserves indiquées dans « État de validation ».

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
| `zoombots/bot.py` | `Bot` : cycle de vie (`created → joining → waiting_room → in_meeting → reconnecting → ended/denied/error/left`), parcours de connexion, surveillance de l'état réel et reconnexion automatique (`run`), `set_mic`, `play`, `stop_sounds`, `leave`. |
| `zoombots/manager.py` | `BotManager` : registre des bots, driver Playwright partagé, `spawn` (tâche asyncio par bot), `broadcast`. Liste et résolution des sons dans `sounds/` (protection contre la traversée de chemin). |
| `zoombots/state.py` | Persistance JSON atomique de la liste des bots (`ZOOMBOTS_STATE`, défaut `.zoombots/state.json`). |
| `zoombots/stats.py` | Mesure RSS/CPU par bot en retrouvant ses process Chromium via `PULSE_SOURCE` dans `/proc`. |
| `zoombots/log.py` | Logs JSON (une ligne par événement) et journal d'événements par bot. |
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
| `GET /stats` | RAM/CPU par bot, RAM système, capacité estimée |
| `GET /bots/{id}/events` | Journal des 50 derniers changements d'état |
| `GET /bots/{id}/screenshot` | Capture en direct, ou dernière capture d'erreur |

`POST /bots` accepte aussi `auto_reconnect` (défaut `true`) et répond 429 au-delà de `ZOOMBOTS_MAX_BOTS`.

## Configuration (variables d'environnement)

`ZOOMBOTS_HOST`, `ZOOMBOTS_PORT` (défaut `127.0.0.1:8000`), `ZOOMBOTS_SOUNDS` (dossier des sons), `ZOOMBOTS_HEADED=1` (Chromium non headless, à lancer sous `xvfb-run`), `ZOOMBOTS_CHROMIUM` (binaire Chromium personnalisé), `ZOOMBOTS_MAX_BOTS` (défaut 20), `ZOOMBOTS_MAX_JOINS` (connexions simultanées, défaut 3), `ZOOMBOTS_ADMIT_TIMEOUT` (attente en salle d'attente, défaut 600 s), `ZOOMBOTS_WATCH_INTERVAL` (défaut 3 s), `ZOOMBOTS_MAX_RECONNECTS` (défaut 3), `ZOOMBOTS_DATA` (état, captures), `ZOOMBOTS_STATE`, `ZOOMBOTS_LOG_LEVEL`.

## État de validation

- **Testé automatiquement** (`pytest`, 23 tests) : parsing des réunions, classification des états de page (salle d'attente, refus, fin, réunion invalide), boucle de reconnexion (reconnexion après coupure, pas de retry sur refus, abandon après N essais), persistance et restauration, limite de bots, traversée de chemin, API.
- **Validé manuellement** : micro virtuel, lecture et capture, arrêt des lectures, nettoyage PulseAudio, lecture de `/proc` pour les ressources.
- **Non validé** : le parcours de connexion sur le vrai client web Zoom. Les sélecteurs (`#input-for-name`, `#input-for-pwd`, boutons Join/Audio/Leave, `aria-label` du micro) et les textes FR/EN de détection d'état sont des hypothèses : aucun accès à une vraie réunion dans l'environnement de développement. À valider avec `/bots/{id}/screenshot` et les logs.
- **Non validé** : Chromium avec `PULSE_SOURCE`, le `Dockerfile` et `docker-compose.yml`.

## Limites connues

- Le Chromium de Playwright doit correspondre à la version du paquet ; un binaire existant peut être utilisé via `ZOOMBOTS_CHROMIUM`.
- `VirtualMic.playing` reste vrai quelques secondes après la fin du son (délai de vidage de `paplay`).
- Après un redémarrage, les bots rejoignent la réunion comme de nouveaux participants (l'état de mute et les lectures en cours ne sont pas restaurés).
- Pas d'authentification sur l'API (écoute en local par défaut).

## Périmètre

Linux uniquement (Docker éventuel). Aucune compatibilité macOS/Windows n'est visée. Hors périmètre pour l'instant : dessin, partage d'écran, réception audio, authentification de l'API.

## Fonctionnalités ajoutées

| Fonctionnalité | Implémentation |
|---|---|
| Salle d'attente / admission | Pendant la connexion, la page est classée toutes les secondes (`classify_text`) : statut `waiting_room`, `denied` (refus, réunion invalide), `ended`. Délai d'attente configurable. |
| État réel dans la réunion | Boucle `_watch` : navigateur arrêté, page fermée, expulsion ou fin de réunion font quitter `in_meeting`. |
| Reconnexion automatique | Backoff exponentiel (2, 4, 8… 30 s), `ZOOMBOTS_MAX_RECONNECTS` essais, désactivable par bot. Pas de reconnexion si la réunion est finie ou l'admission refusée. |
| Persistance | Liste des bots écrite à chaque changement ; relancés au démarrage du serveur. Les modules PulseAudio `zb_*` orphelins sont déchargés au démarrage. |
| Mesure des ressources | `/stats` : RSS et CPU par bot, RAM système, capacité estimée (RAM disponible / RSS moyen), plafond `ZOOMBOTS_MAX_BOTS`. |
| Optimisation | Chromium allégé (sans GPU, sans sortie audio, viewport 800x600), connexions limitées en parallèle pour lisser le pic CPU, déploiement Docker unique (un PulseAudio et un conteneur pour tous les bots). |
| Observabilité | Logs JSON, journal d'événements par bot, capture d'écran automatique en cas d'erreur, endpoint de capture. |
| Tests | Suite `pytest` (`pip install -e .[dev]`). |

### Limite de la mutualisation

Un process Chromium par bot reste nécessaire : `PULSE_SOURCE` est fixé au lancement, donc un Chromium partagé par plusieurs bots partagerait le même micro. La mutualisation se fait au niveau du conteneur (un serveur PulseAudio, plusieurs bots), pas du navigateur. Le coût reste linéaire ; utiliser `/stats` pour mesurer le plafond réel.

## Bugs connus

- `VirtualMic.playing` reste vrai quelques secondes après la fin du son.
- Chromium avec `PULSE_SOURCE` n'a pas été validé (test interrompu).
