# La CLI

`zoombots` pilote le serveur par son [API HTTP](api.md#api-dadministration). Les bots se désignent par un **préfixe d'identifiant** (`zoombots rm 76b5`).

## Serveur

| Commande | Effet |
|---|---|
| `zoombots gui [--port N] [--proxy URL] [--no-open] [--headed]` | Interface web chez soi ([Modes](modes.md#chez-soi--zoombots-gui)) |
| `zoombots server start [options]` | Serveur partagé ([Serveur partagé](server.md)) |
| `zoombots server dns [--domain D]` | Enregistrement DNS à créer et vérification ([Nom de domaine](domain.md)) |
| `zoombots server info` | Rappelle l'adresse, le code d'accès et le jeton |
| `zoombots start [--headed]` | Serveur d'administration en arrière-plan |
| `zoombots serve` | Le même, au premier plan |
| `zoombots shutdown` | Arrête le serveur (les bots quittent, les micros sont déchargés) et Caddy |
| `zoombots status` | État du serveur et liste des bots |
| `zoombots logs [-f] [-n N]` | Journal du serveur |
| `zoombots check-ip [--proxy URL]` | Vérifie le masquage de l'IP ([Masquer l'IP](privacy-ip.md#vérifier)) |

## Bots d'administration

| Commande | Effet |
|---|---|
| `zoombots spawn <id\|lien> [-p passcode] [-n nom] [-c N] [--no-reconnect] [--wait SEC]` | Crée N bots ; `--wait` attend leur connexion et rend 1 si l'un n'est pas entré |
| `zoombots list [--json]` | Liste les bots |
| `zoombots rm <bots…>` · `--all` · `--finished` | Fait quitter et supprime |
| `zoombots mic <bot> on\|off` | Micro d'un bot |
| `zoombots sounds` | Sons du dossier `sounds/` |
| `zoombots play <son> [-b bot] [-v volume]` | Joue sur tous les bots d'administration, ou un seul |
| `zoombots silence [bot]` | Arrête les sons |
| `zoombots stats [--json]` | RAM et CPU, capacité estimée |
| `zoombots events <bot>` | Historique d'un bot |
| `zoombots screenshot <bot> [-o fichier]` | Capture d'écran |

Les commandes de bots ne voient **que les bots d'administration** : les bots des sessions du GUI ne sont ni listés par `list` ni touchés par `play` ou `rm --finished` ([Architecture](architecture.md#deux-familles-de-bots)).

## Codes de retour

`0` succès · `1` échec (serveur injoignable, erreur de l'API, `spawn --wait` avec un bot qui n'est pas entré, échec partiel de `rm` ou `play`, `check-ip` sans masquage) · `2` usage incorrect.

## Détails de fonctionnement

- La CLI **ignore `http_proxy`** pour parler à l'API (les appels vers localhost ne doivent pas passer par un proxy).
- Elle envoie l'en-tête `X-Zoombots` et, si `ZOOMBOTS_TOKEN` est défini, `Authorization: Bearer`.
- Après `gui` ou `server start`, elle reprend l'adresse du serveur dans `<data>/run.json` et, en mode serveur, le jeton dans `~/.config/zoombots/server.json`. `ZOOMBOTS_URL` et `ZOOMBOTS_TOKEN` l'emportent.
- Le fichier de PID n'est honoré que si le processus est bien un zoombots (`/proc/<pid>/cmdline`) : un PID recyclé n'est jamais signalé.
- `shutdown` envoie SIGTERM puis, après 30 s, SIGKILL, avec nettoyage des navigateurs et modules PulseAudio orphelins.
- `server.log` est tournant (5 Mo, un seul fichier de rotation `.log.1`).
- Les dossiers : données et journaux dans `~/.local/state/zoombots` (`ZOOMBOTS_DATA`), configuration du serveur dans `~/.config/zoombots`.

## Voir aussi

[Modes de lancement](modes.md) · [API HTTP](api.md) · [Configuration](configuration.md)
