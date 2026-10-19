# Configuration

Tout se règle par variables d'environnement (`ZOOMBOTS_*`). Les commandes `zoombots gui` et `zoombots server start` posent celles dont elles ont besoin ; le reste est optionnel. Les valeurs sont lues au démarrage du serveur.

## Réseau et confidentialité

| Variable | Défaut | Rôle |
|---|---|---|
| `ZOOMBOTS_HOST`, `ZOOMBOTS_PORT` | `127.0.0.1`, `8000` | Écoute (hors loopback : jeton obligatoire) |
| `ZOOMBOTS_URL` | dérivée | URL de l'API pour la CLI |
| `ZOOMBOTS_ALLOWED_HOSTS` | aucun | Noms d'hôte acceptés en plus de localhost, séparés par des virgules ; `*` = tous |
| `ZOOMBOTS_TRUSTED_PROXIES` | `127.0.0.1,::1` | Proxies dont `X-Forwarded-For` est cru |
| `ZOOMBOTS_PROXY` | aucun | Proxy(s) de sortie des bots, séparés par des virgules ([détail](privacy-ip.md)) |
| `ZOOMBOTS_REQUIRE_PROXY` | `0` | `1` : aucun bot ne démarre sans proxy |

## Modes et accès

| Variable | Défaut | Rôle |
|---|---|---|
| `ZOOMBOTS_MODE` | aucun | `local` : plafonds par IP désactivés ; `server` : serveur partagé |
| `ZOOMBOTS_PUBLIC_ONLY` | `0` | `1` : administration injoignable hors de la machine (404) |
| `ZOOMBOTS_TOKEN` | aucun | Jeton d'administration |
| `ZOOMBOTS_ACCESS_CODE` | aucun | Code demandé pour ouvrir une session du GUI |

## Plafonds ([détail](limits.md))

| Variable | Défaut | Rôle |
|---|---|---|
| `ZOOMBOTS_MAX_BOTS` | `20` | Bots actifs sur le serveur |
| `ZOOMBOTS_MAX_SESSIONS` | `20` | Sessions simultanées |
| `ZOOMBOTS_SESSION_MAX_BOTS` | `10` | Bots actifs par session |
| `ZOOMBOTS_SESSION_MAX_SOUNDS` | `20` | Sons par session |
| `ZOOMBOTS_IP_MAX_SESSIONS` | `3` (`0` en local) | Sessions simultanées par IP |
| `ZOOMBOTS_IP_MAX_BOTS` | `10` (`0` en local) | Bots actifs par IP |
| `ZOOMBOTS_IP_SESSIONS_PER_MINUTE` | `10` (`0` en local) | Créations de session par IP et par minute |
| `ZOOMBOTS_MAX_JOINS` | `3` | Connexions simultanées de bots |
| `ZOOMBOTS_MAX_UPLOAD_MB` | `25` | Taille maximale d'un son |
| `ZOOMBOTS_MAX_SOUND_SECONDS` | `900` | Durée maximale d'un son |
| `ZOOMBOTS_YT_TIMEOUT` | `180` | Délai d'un téléchargement YouTube (s) |

## Comportement des bots ([détail](bot-lifecycle.md))

| Variable | Défaut | Rôle |
|---|---|---|
| `ZOOMBOTS_SESSION_GRACE` | `15` | Délai (s) avant destruction d'une session déconnectée |
| `ZOOMBOTS_ADMIT_TIMEOUT` | `600` | Attente maximale en salle d'attente (s) |
| `ZOOMBOTS_WATCH_INTERVAL` | `3` | Période de surveillance (s) |
| `ZOOMBOTS_MAX_RECONNECTS` | `3` | Reconnexions consécutives |
| `ZOOMBOTS_STABLE_SESSION` | `60` | Durée d'une session « stable » (s) |
| `ZOOMBOTS_EXTRA_DOMAINS` | aucun | Domaines Zoom d'entreprise acceptés dans les liens |
| `ZOOMBOTS_HEADED` | `0` | `1` : Chromium non headless (sous `xvfb-run`) |
| `ZOOMBOTS_CHROMIUM` | aucun | Binaire Chromium personnalisé |

## Fichiers et système

| Variable | Défaut | Rôle |
|---|---|---|
| `ZOOMBOTS_DATA` | `~/.local/state/zoombots` | État, captures, sessions, pidfile, journaux, Caddyfile (chemin absolu) |
| `ZOOMBOTS_STATE` | `<data>/state.json` | Fichier d'état des bots d'administration |
| `ZOOMBOTS_SOUNDS` | `./sounds` | Dossier des sons du serveur |
| `ZOOMBOTS_INSTANCE` | aucun | Nom d'instance ([Audio](audio.md#plusieurs-zoombots-sur-un-même-pulseaudio)) |
| `ZOOMBOTS_LOG_LEVEL` | `INFO` | Niveau de journalisation |

Le répertoire de configuration du serveur partagé est `~/.config/zoombots` (ou `$XDG_CONFIG_HOME/zoombots`).

## Voir aussi

[Modes de lancement](modes.md) · [Limites et quotas](limits.md) · [Masquer l'IP](privacy-ip.md)
