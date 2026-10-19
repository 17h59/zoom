# zoombots : documentation technique

Bots pilotables pour le client web Zoom : un Chromium (Playwright) par bot, un micro virtuel PulseAudio par bot, un soundboard, une API HTTP, un GUI web multi-utilisateurs, un dashboard d'administration et une CLI.

## Vue d'ensemble

Un « bot » est un Chromium qui charge le client web Zoom, associé à un micro virtuel PulseAudio dédié. L'audio d'un fichier est décodé par ffmpeg et injecté dans le micro virtuel, que le navigateur utilise comme périphérique d'entrée.

```
Navigateurs ──HTTP+WebSocket──►  GUI (/)  ──►  SessionManager ── Session (1 par onglet)
CLI `zoombots` ──HTTP──►  API admin + dashboard (/admin)           │  sons, actions, bots
                              │                                    │
                              └──────────────┬─────────────────────┘
                         BotManager ── Playwright (un driver partagé)
                              │          ├─ persistance (state.json, 0600)
                              │          └─ sémaphore de connexions
                           Bot (1 par instance, possède sa tâche asyncio)
                    ├─ Chromium (process dédié, env PULSE_SOURCE=zb_<id>_mic)
                    └─ VirtualMic
                         null-sink zb_<id>  ──monitor──►  remap-source zb_<id>_mic
                              ▲
                  ffmpeg (décodage) ─pipe─► paplay --device=zb_<id>
```

## Installation et CLI

```
./install.sh                 # paquets système, .venv, Chromium, commande `zoombots`
zoombots start               # serveur en arrière-plan (--headed : Chromium sous xvfb)
zoombots spawn <id|lien> -c 3 --wait 120
zoombots play son.mp3        # sur tous les bots ; -b <bot> pour un seul
zoombots --help
```

`install.sh` est idempotent, fonctionne avec ou sans `sudo` (sous `sudo`, le venv et le lien `~/.local/bin/zoombots` sont créés pour l'utilisateur appelant), prend en charge apt, dnf et pacman, et exige Python ≥ 3.11. Les bibliothèques système de Chromium ne sont installées automatiquement que sous Debian/Ubuntu.

Commandes : `serve`, `start`, `shutdown`, `status`, `logs [-f]`, `spawn`, `list [--json]`, `rm <bots…>|--all|--finished`, `mic <bot> on|off`, `sounds`, `play`, `silence`, `stats`, `events`, `screenshot`. Les bots se désignent par un préfixe d'identifiant. Codes de retour : 0 succès, 1 échec (y compris `spawn --wait` si un bot n'est pas entré, `rm`/`play` en échec partiel), 2 usage.

La CLI ignore `http_proxy` pour parler à l'API, envoie l'en-tête `X-Zoombots` et le jeton `ZOOMBOTS_TOKEN` s'il est défini. Le pidfile (`<data>/server.pid`) n'est honoré que si le PID est bien un process zoombots (`/proc/<pid>/cmdline`). `shutdown` envoie SIGTERM (les bots quittent et les micros sont déchargés) puis SIGKILL après 30 s, avec nettoyage des navigateurs et modules orphelins.

## Modules

| Fichier | Rôle |
|---|---|
| `zoombots/meeting.py` | `Meeting` et `parse_meeting` : ID (9 à 11 chiffres) et `pwd` depuis un ID brut ou un lien. Domaines Zoom acceptés par liste blanche (voir ci-dessous). Passcode encodé dans l'URL. |
| `zoombots/audio.py` | `VirtualMic` : modules `null-sink` et `remap-source` via `pactl` (rollback si le chargement échoue), lecture `ffmpeg` → `paplay` par deux sous-processus reliés par un pipe (stderr de ffmpeg journalisé, 4 lectures simultanées max par bot), `cleanup_orphans`. |
| `zoombots/bot.py` | `Bot` : cycle de vie, parcours de connexion, surveillance, reconnexion, `set_mic`, `play`, `leave`. |
| `zoombots/manager.py` | `BotManager` : registre, driver Playwright partagé, persistance, plafond de bots actifs, `broadcast`, `stats`. |
| `zoombots/state.py` | Persistance JSON atomique (`fsync`, droits 0600), fichier illisible conservé en `.bak`, entrées validées. |
| `zoombots/stats.py` | RSS/CPU par bot : un balayage de `/proc` retrouve les process Chromium via `PULSE_SOURCE`. |
| `zoombots/log.py` | Logs JSON et journal d'événements par bot. |
| `zoombots/api.py` | API d'administration FastAPI, garde de sécurité, service du GUI (`/`) et du dashboard (`/admin`). |
| `zoombots/sessions.py` | Sessions du GUI : bots, sons (uploads, YouTube), actions répétées, présence, expiration. |
| `zoombots/gui_api.py` | Routes `/api` et WebSocket du GUI. |
| `zoombots/cli.py` | Interface en ligne de commande. |
| `zoombots/static/` | GUI (`gui.html`, `gui.css`, `gui.js`) et dashboard d'administration (`index.html`, `app.js`), sans `innerHTML`. |

## GUI web multi-utilisateurs

`/` sert une interface pour déployer et piloter des bots, conçue pour un petit serveur partagé. Chaque visiteur obtient **sa propre session** : à l'arrivée la page est vide, et il ne voit et ne contrôle que ses bots, ses sons et ses actions.

- **Déployer** : une liste de liens ou ID de réunion (un par ligne, `lien | passcode` si besoin) et un nombre de bots, soit *répartis* sur les liens (`split` : 10 bots sur 3 liens donnent 4, 3, 3), soit *par lien* (`each`). Les bots sont nommés `<nom> 1…N`.
- **Sons** : glisser-déposer de fichiers audio ou vidéo n'importe où sur la page (ou clic pour parcourir) ; ajout depuis un lien **YouTube** (yt-dlp, extraction mp3). Les sons du dossier `sounds/` du serveur sont proposés en plus (« bibliothèque »). Un son se glisse sur un bot pour le jouer une fois.
- **Action** : jouer un son *N* fois sur tous les bots actifs ou sur une sélection, chaque lecture attendant la fin de la précédente, avec une pause entre les répétitions, un décalage entre les bots et un volume. Une action lancée avant que le bot soit entré dans la réunion démarre dès son admission. Une nouvelle action remplace la précédente du bot ; trois échecs de lecture de suite l'arrêtent en erreur.
- **Pilotage individuel** : un clic sur un bot ouvre un panneau (statut, historique, capture d'écran en direct, micro on/off, jouer, arrêter l'action, retirer le bot). En haut : « Tout stopper » (actions et sons) et « Quitter tous les bots ».
- **Fermer l'onglet ferme les bots** : le navigateur garde un WebSocket ouvert (`/api/ws`) qui pousse l'état en direct ; quand il reste fermé plus de `ZOOMBOTS_SESSION_GRACE` secondes (15), la session est détruite (actions annulées, bots retirés, fichiers supprimés). Un rechargement de page dans ce délai reprend la même session (identifiant gardé dans `sessionStorage`) ; un nouvel onglet est une nouvelle session vide. Une session jamais connectée expire au bout de 30 s.

### Isolation et limites

- L'identifiant de session (192 bits aléatoires) est le seul accès à une session, envoyé dans `X-Session` (jamais dans une URL). Chaque bot porte son propriétaire : les routes de session ne voient que les bots de la session ; un son d'une session est inutilisable par une autre. Les bots des sessions ne sont **pas persistés** (ils ne survivent pas à leur onglet) et l'API d'administration ne les touche pas (`broadcast`, `rm --finished`).
- Quotas : `ZOOMBOTS_MAX_SESSIONS` (20) sessions, `ZOOMBOTS_SESSION_MAX_BOTS` (10) bots actifs par session en plus du plafond global `ZOOMBOTS_MAX_BOTS`, 50 bots par déploiement, 20 liens, `ZOOMBOTS_SESSION_MAX_SOUNDS` (20) sons, `ZOOMBOTS_MAX_UPLOAD_MB` (25) par fichier, `ZOOMBOTS_MAX_SOUND_SECONDS` (900) de durée, 2 téléchargements YouTube simultanés, 1000 répétitions par action.
- **Contrôle d'accès** : `ZOOMBOTS_ACCESS_CODE` demande un code à l'ouverture d'une session (sans lui, quiconque atteint le serveur peut déployer des bots).
- **Uploads** : corps de requête lu en flux (`PUT /api/sounds?name=…`, sans dépendance multipart), plafonné en octets, extension dans une liste blanche, nom nettoyé, fichier stocké sous un identifiant généré dans le dossier de la session, validé par `ffprobe` (piste audio présente, durée plafonnée) ; un fichier refusé est supprimé.
- **YouTube** : seuls `youtube.com`, `www.`, `m.`, `music.youtube.com` et `youtu.be` sont acceptés (pas de SSRF vers un autre hôte via yt-dlp) ; `--no-playlist`, `--ignore-config`, taille et durée plafonnées, délai `ZOOMBOTS_YT_TIMEOUT` (180 s), sortie confinée au dossier de la session.
- Les dossiers de session sont sous `<data>/sessions/` et vidés à chaque démarrage du serveur.

### API du GUI (`/api`)

| Route | Description |
|---|---|
| `GET /api/config` | Limites et présence d'un code d'accès |
| `POST /api/session` | `{code?}` → `{sid}` ; `DELETE /api/session` détruit la session |
| `GET /api/state` | Instantané : bots (avec action), sons, bibliothèque, téléchargements |
| `WS /api/ws` | Premier message `{sid}` ; l'état est poussé à chaque changement ; contrôle de `Host` et d'`Origin` |
| `POST /api/deploy` | `{links:[{meeting, passcode?}], count, mode: split|each, name, auto_reconnect}` |
| `DELETE /api/bots[?finished=true]`, `DELETE /api/bots/{id}` | Retirer des bots |
| `POST /api/bots/{id}/mic` · `GET …/events` · `GET …/screenshot` | Micro, historique, capture |
| `PUT /api/sounds?name=` · `DELETE /api/sounds/{id}` | Envoyer / supprimer un son |
| `POST /api/youtube` | `{url}` : téléchargement en arrière-plan |
| `POST /api/actions` | `{bots?, sound, count, gap, volume, stagger}` (`sound` : id de session ou `lib:<nom>`) |
| `POST /api/actions/stop` | `{bots?}` : arrête actions et sons |

### Déploiement sur un petit serveur

```
ZOOMBOTS_HOST=0.0.0.0 ZOOMBOTS_TOKEN=<admin> ZOOMBOTS_ACCESS_CODE=<code> \
ZOOMBOTS_ALLOWED_HOSTS=zoom.example.com zoombots start
```

`ZOOMBOTS_TOKEN` protège l'administration (`/admin`, API historique, CLI) ; le GUI `/` et `/api` n'en ont pas besoin. `ZOOMBOTS_ALLOWED_HOSTS` doit contenir le nom de domaine public (protection contre le DNS rebinding). Placer un proxy TLS devant (nginx, Caddy) : sans HTTPS, le code d'accès et l'identifiant de session circulent en clair ; le proxy doit transmettre les WebSockets (`Upgrade`). Compter environ 300 à 500 Mo de RAM par bot : voir `zoombots stats` pour calibrer `ZOOMBOTS_MAX_BOTS` sur la machine.

## Cycle de vie d'un bot

`created → joining → waiting_room → in_meeting → reconnecting → … → ended | denied | error | left`

- Le bot possède sa tâche asyncio (`Bot.start` / `leave`). `leave()` clique sur Leave, annule la tâche, l'attend, puis ferme Chromium et décharge les modules : annuler un bot en pleine connexion ne laisse ni navigateur ni micro.
- **Connexion** : le sémaphore `ZOOMBOTS_MAX_JOINS` ne couvre que le lancement de Chromium, le chargement et le clic sur Join. L'attente en salle d'attente (jusqu'à `ZOOMBOTS_ADMIT_TIMEOUT`) le libère, donc elle ne bloque pas les autres bots.
- **Classification d'état** (`classify_text`) : appliquée uniquement aux titres, dialogues et alertes (`h1-h3`, `[role=dialog|alertdialog|alert]`), jamais au chat, aux sous-titres ou aux noms de participants. Le texte de la page entière n'est lu que pendant l'admission, pour la salle d'attente. Le contexte du navigateur est forcé en `en-US` (les libellés français restent reconnus en secours).
- **Surveillance** : une fin (`ended`, `denied`) n'est décidée que si la barre d'outils (bouton Leave) est absente `LOST_CYCLES` (3) cycles de suite et qu'un message système la confirme.
- **Reconnexion** : backoff 2, 4, 8… 30 s, `ZOOMBOTS_MAX_RECONNECTS` essais. Le compteur ne repart de zéro qu'après une session stable de `ZOOMBOTS_STABLE_SESSION` secondes (60), ce qui borne une boucle connexion/coupure immédiate. Aucune reconnexion si la réunion est finie ou l'admission refusée.
- **Mute** : `set_mic` vise le bouton par libellé exact (`mute my microphone`, pas « Select a microphone »), renvoie si l'état a changé, attend 0,4 s après le dé-mute avant un son. Bouton absent ou délai dépassé : `RuntimeError` (HTTP 409).
- Les passcodes sont masqués (`***`) dans les erreurs, événements et logs.

## Domaines Zoom

Le lien fourni détermine l'hôte du client web (`https://<hôte>/wc/join/<id>`). Hôtes acceptés : `zoom.us`, `zoomgov.com`, `zoom.com`, `zoom.com.cn`, `zoomgov.cn` et tous leurs sous-domaines (régionaux `us02web.zoom.us`, d'entreprise `acme.zoom.us`). `zoom.us` et `www.zoom.us` sont ramenés à `app.zoom.us`, `zoomgov.com` à `www.zoomgov.com`. Formes de chemin reconnues : `/j/`, `/s/`, `/wc/join/`, `/wc/`. Un hôte hors liste est refusé (un lien arbitraire ne doit pas faire naviguer le navigateur ailleurs). Domaines d'entreprise personnalisés : `ZOOMBOTS_EXTRA_DOMAINS=meet.example.com,…`. Un ID de plus de 11 chiffres est refusé, jamais tronqué. L'hôte est persisté avec le bot.

## Choix techniques

- **Un process Chromium par bot** : `PULSE_SOURCE` est fixé au lancement, donc un Chromium partagé partagerait le même micro. La mutualisation se fait au niveau du conteneur (un PulseAudio, plusieurs bots), pas du navigateur ; le coût reste linéaire. Chromium est allégé (sans GPU, sans sortie audio, 800x600).
- **`ffmpeg | paplay` plutôt que `ffmpeg -f pulse`** : le muxer pulse de ffmpeg perd la fin du flux, `paplay` vide son buffer. Pas de shell : deux processus, chacun dans son groupe, arrêtés par SIGTERM. `VirtualMic.playing` reste vrai tant que `paplay` vide son buffer, ce qui correspond à du son encore en cours.
- **Écriture de l'état groupée** : plusieurs changements dans un même tour de boucle donnent une seule écriture. `shutdown` fige la liste (les `left` de l'arrêt ne la vident pas) : les bots actifs sont relancés au prochain démarrage.
- **Bots terminés** (`ended`, `denied`, `error`, `left`) : non persistés, non comptés dans `ZOOMBOTS_MAX_BOTS`, retirés par `rm --finished` ou `DELETE /bots?finished=true`.
- **Démarrage** : les modules PulseAudio orphelins de l'instance (`zb_[<instance>_]<8 hex>`) sont déchargés. `ZOOMBOTS_INSTANCE` (1 à 16 caractères `[a-z0-9]`) préfixe les noms PulseAudio et le dossier de données par défaut : plusieurs zoombots peuvent ainsi partager un serveur PulseAudio (chacun avec son port, `ZOOMBOTS_PORT`, et `ZOOMBOTS_INSTANCE` dans l'environnement de sa CLI). Le nettoyage, la mesure de ressources et l'arrêt forcé ne touchent que les ressources de leur instance. Les entrées d'état invalides (ID de bot, réunion, domaine) sont ignorées sans bloquer le démarrage.
- **Routes** `async` : elles s'exécutent dans la boucle, donc sans accès concurrent au registre. `/stats` s'exécute dans un thread (balayage de `/proc`).

## API HTTP

| Route | Description |
|---|---|
| `GET /` | GUI utilisateur |
| `GET /admin` | Dashboard d'administration (tous les bots) |
| `GET /health` | Sonde (sans authentification) |
| `GET /bots` | Liste et statut des bots |
| `POST /bots` | `{meeting, passcode?, name, count, auto_reconnect}` (1 à 20 bots, nom ≤ 60, passcode ≤ 64). 429 au-delà de `ZOOMBOTS_MAX_BOTS` bots actifs |
| `DELETE /bots?finished=true` | Retire les bots terminés |
| `DELETE /bots/{id}` | Quitte et supprime |
| `POST /bots/{id}/mic` | `{on: bool}` → `{changed}` |
| `GET /sounds` | Fichiers disponibles dans `sounds/` |
| `POST /bots/{id}/sounds/{sound}` | `{volume}` (0 à 2) |
| `POST /bots/{id}/stop` | Arrête les lectures du bot |
| `POST /broadcast/sounds/{sound}` | Joue sur tous les bots dans la réunion → `{played, failed:[{bot,error}]}` (`bots` = `played`) |
| `GET /stats` | RAM/CPU par bot, RAM système, capacité estimée |
| `GET /bots/{id}/events` | 50 derniers changements d'état |
| `GET /bots/{id}/screenshot` | Capture en direct, ou dernière capture d'erreur |

Noms de sons : extension audio obligatoire, pas de séparateur de chemin ni de NUL (404 sinon).

## Sécurité de l'API

L'API pilote des navigateurs et lit des captures d'écran : elle n'a pas de comptes, mais elle est protégée contre les attaques depuis le navigateur de l'utilisateur.

- **DNS rebinding** : l'en-tête `Host` doit être `localhost`, `127.0.0.1` ou `[::1]` (ou figurer dans `ZOOMBOTS_ALLOWED_HOSTS`), sinon 403.
- **CSRF** : toute requête qui modifie l'état doit porter l'en-tête `X-Zoombots` (le dashboard et la CLI l'envoient). Un en-tête personnalisé impose un preflight CORS que le serveur ne satisfait jamais ; `Sec-Fetch-Site: cross-site` est aussi refusé.
- **Jeton** : `ZOOMBOTS_TOKEN` rend `Authorization: Bearer <jeton>` obligatoire pour l'administration (le dashboard : `/admin?token=<jeton>`, qui pose un cookie `HttpOnly; SameSite=Strict`). Le GUI (`/`, `/static/`, `/api/`) y échappe : il est protégé par les secrets de session et, au choix, `ZOOMBOTS_ACCESS_CODE`. Écouter hors loopback (`ZOOMBOTS_HOST`) sans jeton fait échouer le démarrage.
- **XSS** : le dashboard construit le DOM avec `textContent`, sans `innerHTML`, et toutes les réponses portent une CSP (`script-src 'self'`).
- **Secrets** : `state.json` (passcodes) en 0600 ; passcodes masqués dans erreurs et logs.
- Limites : le jeton est comparé en temps constant mais circule en clair en HTTP ; pour un accès distant, placer l'API derrière un proxy TLS.

## Configuration (variables d'environnement)

| Variable | Défaut | Rôle |
|---|---|---|
| `ZOOMBOTS_HOST`, `ZOOMBOTS_PORT` | `127.0.0.1`, `8000` | Écoute (hors loopback : jeton obligatoire) |
| `ZOOMBOTS_URL` | dérivée | URL de l'API pour la CLI |
| `ZOOMBOTS_TOKEN` | aucun | Jeton d'administration (`/admin`, API historique, CLI) |
| `ZOOMBOTS_ACCESS_CODE` | aucun | Code demandé pour ouvrir une session du GUI |
| `ZOOMBOTS_MAX_SESSIONS` | `20` | Sessions GUI simultanées |
| `ZOOMBOTS_SESSION_MAX_BOTS` | `10` | Bots actifs par session |
| `ZOOMBOTS_SESSION_GRACE` | `15` | Délai (s) avant destruction d'une session déconnectée |
| `ZOOMBOTS_SESSION_MAX_SOUNDS` | `20` | Sons par session |
| `ZOOMBOTS_MAX_UPLOAD_MB` | `25` | Taille maximale d'un son |
| `ZOOMBOTS_MAX_SOUND_SECONDS` | `900` | Durée maximale d'un son |
| `ZOOMBOTS_YT_TIMEOUT` | `180` | Délai d'un téléchargement YouTube (s) |
| `ZOOMBOTS_ALLOWED_HOSTS` | aucun | Hôtes supplémentaires acceptés dans `Host` |
| `ZOOMBOTS_SOUNDS` | `./sounds` | Dossier des sons |
| `ZOOMBOTS_DATA` | `~/.local/state/zoombots` | État, captures, pidfile, logs (chemin absolu) |
| `ZOOMBOTS_STATE` | `<data>/state.json` | Fichier d'état |
| `ZOOMBOTS_HEADED` | `0` | `1` : Chromium non headless (sous `xvfb-run`) |
| `ZOOMBOTS_CHROMIUM` | aucun | Binaire Chromium personnalisé |
| `ZOOMBOTS_MAX_BOTS` | `20` | Plafond de bots actifs |
| `ZOOMBOTS_MAX_JOINS` | `3` | Connexions simultanées (hors attente) |
| `ZOOMBOTS_ADMIT_TIMEOUT` | `600` | Attente maximale en salle d'attente (s) |
| `ZOOMBOTS_WATCH_INTERVAL` | `3` | Période de surveillance (s) |
| `ZOOMBOTS_MAX_RECONNECTS` | `3` | Reconnexions consécutives |
| `ZOOMBOTS_STABLE_SESSION` | `60` | Durée d'une session « stable » (s) |
| `ZOOMBOTS_EXTRA_DOMAINS` | aucun | Domaines Zoom d'entreprise (liste séparée par des virgules) |
| `ZOOMBOTS_INSTANCE` | aucun | Nom d'instance (plusieurs zoombots sur un même PulseAudio) |
| `ZOOMBOTS_LOG_LEVEL` | `INFO` | Niveau de log |

## Docker

`docker compose up` (avec `ZOOMBOTS_TOKEN` défini) lance un conteneur qui héberge PulseAudio et tous les bots. L'image est épinglée sur une version de Playwright (`PLAYWRIGHT_VERSION`) identique à l'image de base, tourne sans root (`pwuser`), a un `HEALTHCHECK` sur `/health` et `restart: unless-stopped` côté compose. `shm_size: 2gb` est nécessaire à Chromium.

## État de validation

- **Testé automatiquement** (`pip install -e .[dev] && pytest`, 131 tests) : parsing et domaines, classification (y compris faux positifs de chat), reconnexion et bornage, libération du sémaphore, annulation sans fuite, persistance (cycle `start → shutdown → start` réel), validation des entrées, sécurité de l'API (Host, CSRF, jeton, CSP), pipeline audio (avec un `paplay` factice), rollback PulseAudio, nettoyage des orphelins, CLI. Sessions du GUI : répartition des bots, isolation entre utilisateurs, quotas, actions répétées (séquence, attente d'admission, arrêt, remplacement, échecs), uploads (formats, taille, durée, fichier corrompu), YouTube (avec un faux yt-dlp), expiration et destruction sur fermeture du WebSocket, API HTTP et WebSocket (code d'accès, `Host`, `Origin`, CSRF). Un test pilote le **vrai GUI dans Chromium** contre un vrai serveur uvicorn avec de faux bots : déploiement, envoi d'un son, action, panneau d'un bot, glisser-déposer sur un bot, rechargement, second onglet isolé, fermeture de l'onglet qui retire les bots. Tests d'intégration sur **vrai Chromium** avec de fausses pages Zoom : chat contenant « I refuse », dialogue d'expulsion, salle d'attente, surveillance, basculement du micro.
- **Validé manuellement** : cycle `start → spawn → status → rm → shutdown` de la CLI contre un vrai serveur (échec de lancement de Chromium compris : reconnexion, passage en `error`, aucun module PulseAudio résiduel).
- **Non validé** : le parcours de connexion sur le vrai client web Zoom. Les sélecteurs (`#input-for-name`, `#input-for-pwd`, boutons Join/Audio/Leave, `aria-label` du micro) et les textes de détection d'état sont des hypothèses, faute d'accès à une réunion. À vérifier avec `zoombots screenshot` et `zoombots logs`.
- **Non validé** : le téléchargement YouTube réel (pas d'accès à YouTube dans l'environnement de développement : seul le pipeline autour de yt-dlp est testé, avec un faux yt-dlp ; YouTube change souvent, d'où `pip install -U yt-dlp` à prévoir), la charge de plusieurs utilisateurs simultanés sur de vrais Chromium, le déploiement derrière un proxy TLS.
- **Non validé** : le contenu audio réellement reçu par Zoom (la capture du micro virtuel était silencieuse dans l'environnement de développement, même pour un `paplay` direct, donc non imputable au code), Chromium avec `PULSE_SOURCE` sur une vraie réunion, le `Dockerfile` et `docker-compose.yml`, `install.sh` hors Debian/Ubuntu, le contournement des domaines Zoom pour les comptes `zoomgov`.

## Limites connues

- Après un redémarrage, les bots rejoignent la réunion comme de nouveaux participants (mute et lectures en cours non restaurés).
- Plusieurs zoombots sur un même serveur PulseAudio exigent des `ZOOMBOTS_INSTANCE` distincts (sans instance, deux zoombots se déchargent mutuellement leurs micros au démarrage).
- Chromium de Playwright : sa version doit correspondre au paquet. Un binaire existant s'utilise via `ZOOMBOTS_CHROMIUM` (sous root, Chromium exige `--no-sandbox`, non ajouté par défaut).
- Linux uniquement. Hors périmètre : dessin, partage d'écran, réception audio, comptes utilisateurs persistants (les sessions du GUI sont éphémères).
- La limite d'une session est par onglet : un utilisateur qui ouvre dix onglets obtient dix sessions (borné par `ZOOMBOTS_MAX_SESSIONS` et `ZOOMBOTS_MAX_BOTS`) ; il n'y a pas de limitation par adresse IP.
- `uvicorn` et `playwright` ne sont pas verrouillés (pas de lockfile).
