# La CLI

`zoombots` pilote le serveur par son [API HTTP](api.md#api-dadministration). Les bots se désignent par un **préfixe d'identifiant** (`zoombots rm 76b5`).

## Serveur

| Commande | Effet |
|---|---|
| `zoombots gui [--port N] [--proxy URL] [--no-open] [--headed]` | Interface web chez soi ([Modes](modes.md#chez-soi--zoombots-gui)) |
| `zoombots server start [options]` | Serveur partagé ; options `--domain`, `--no-domain`, `--code`, `--proxy`, `--no-proxy`, `--reset-secrets`, `--port`, `--email` ([Serveur partagé](server.md)) |
| `zoombots server dns [--domain D]` | Enregistrement DNS à créer et vérification ([Nom de domaine](domain.md)) |
| `zoombots server info` | Rappelle l'adresse, le code d'accès et le jeton |
| `zoombots start [--headed]` | Serveur d'administration en arrière-plan |
| `zoombots serve` | Le même, au premier plan |
| `zoombots shutdown` | Arrête le serveur (les bots quittent, les micros sont déchargés) et Caddy |
| `zoombots status` | État du serveur et liste des bots |
| `zoombots logs [-f] [-n N]` | Journal du serveur |
| `zoombots check-ip [--proxy URL …]` | Vérifie le masquage de l'IP ; `--proxy` est répétable ([Masquer l'IP](privacy-ip.md#vérifier)) |

## Bots d'administration

| Commande | Effet |
|---|---|
| `zoombots spawn <id\|lien> [-p passcode] [-n nom] [-c N] [--no-reconnect] [--no-respawn] [--wait SEC]` | Crée N bots ; `--no-respawn` désactive le respawn sur ban ; `--wait` attend leur connexion et rend 1 si l'un n'est pas entré |
| `zoombots list [--json]` | Liste les bots |
| `zoombots rm <bots…>` · `--all` · `--finished` | Fait quitter et supprime |
| `zoombots mic <bot> on\|off` | Micro d'un bot |
| `zoombots sounds` | Sons du dossier `sounds/` |
| `zoombots play <son> [-b bot] [-v volume]` | Joue sur tous les bots d'administration, ou un seul |
| `zoombots silence [bot]` | Arrête les sons |
| `zoombots stats [--json]` | RAM et CPU, capacité estimée |
| `zoombots events <bot>` | Historique d'un bot |
| `zoombots screenshot <bot> [-o fichier]` | Capture d'écran |

L'administration voit **tous les bots du serveur**, y compris ceux des sessions du GUI : `list` les affiche avec la colonne `OWNER` (début de l'identifiant de session), et `rm`, `mic`, `events`, `screenshot` les atteignent (utile pour l'opérateur ; retirer un bot de session le retire de l'onglet de son utilisateur). Seules les actions de masse l'ignorent : `play` sans `-b` (diffusion) et `rm --finished` ne concernent que les bots d'administration ([Architecture](architecture.md#deux-familles-de-bots)).

## Codes de retour

`0` succès · `1` échec (serveur injoignable, erreur de l'API, `spawn --wait` avec un bot qui n'est pas entré, échec partiel de `rm` ou `play`, `check-ip` sans masquage, `server dns` si le domaine ne pointe pas vers ce serveur, `server info` sans configuration, `server start` refusé) · `2` usage incorrect.

## Détails de fonctionnement

- La CLI **ignore `http_proxy`** pour parler à l'API (les appels vers localhost ne doivent pas passer par un proxy).
- Elle envoie l'en-tête `X-Zoombots` et, si `ZOOMBOTS_TOKEN` est défini, `Authorization: Bearer`.
- Après `gui` ou `server start`, elle reprend l'adresse du serveur dans `<data>/run.json` et le jeton d'administration : celui, aléatoire, que `gui` génère à chaque lancement et range dans `run.json`, ou, en mode serveur, celui de `~/.config/zoombots/server.json`. `ZOOMBOTS_URL` et `ZOOMBOTS_TOKEN` l'emportent. Un `run.json` dont le serveur ne tourne plus est ignoré.
- Le fichier de PID n'est honoré que si le processus est bien un zoombots (`/proc/<pid>/cmdline` contient l'argument exact `zoombots`, ou un exécutable `…/zoombots`) : un PID recyclé, ou un éditeur ouvert sur `zoombots/cli.py`, n'est jamais signalé. Idem pour Caddy.
- `shutdown` arrête l'application (SIGTERM, puis SIGKILL après 30 s avec nettoyage des navigateurs et modules PulseAudio orphelins), **puis** Caddy, dont il attend la libération des ports.
- `start`, `gui` et `server start` refusent de démarrer si un serveur tourne déjà ; `gui` rouvre alors seulement le navigateur sur le serveur local déjà lancé.
- `server.log` est tournant (5 Mo, un seul fichier de rotation `.log.1`) ; il ne contient pas les URL d'administration (journal d'accès désactivé : `/admin?token=…` n'y figure jamais).
- Les dossiers : données et journaux dans `~/.local/state/zoombots` (`ZOOMBOTS_DATA`, ou `$XDG_STATE_HOME/zoombots`), créé en **0700** ; les fichiers de PID, `run.json`, journaux, Caddyfile sont en **0600**. Configuration du serveur dans `~/.config/zoombots` (`$XDG_CONFIG_HOME/zoombots`).
- `--proxy` et `--code` passent par la ligne de commande, donc par `ps` : pour des identifiants sensibles, préfère `ZOOMBOTS_PROXY` dans l'environnement.

## Voir aussi

[Modes de lancement](modes.md) · [Furtivité et anti-ban](anti-ban.md) · [API HTTP](api.md) · [Configuration](configuration.md)
