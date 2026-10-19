# API HTTP

Deux API distinctes. Les requêtes qui **modifient** l'état doivent porter l'en-tête `X-Zoombots` (anti-CSRF, voir [Sécurité](security.md#csrf)).

## API d'administration

Pour la CLI et le dashboard `/admin`. Elle voit les bots d'administration (et le dashboard liste tous les bots). Protégée par `ZOOMBOTS_TOKEN` quand il est défini (`Authorization: Bearer …`) ; avec `ZOOMBOTS_PUBLIC_ONLY=1`, elle n'est joignable que depuis la machine.

| Route | Description |
|---|---|
| `GET /admin` | Dashboard d'administration (cookie via `/admin?token=…`) |
| `GET /health` | Sonde, sans authentification |
| `GET /bots` | Liste et statut des bots |
| `POST /bots` | `{meeting, passcode?, name, count, auto_reconnect}` (1 à 20 bots, nom ≤ 60, passcode ≤ 64) ; 429 au-delà de `ZOOMBOTS_MAX_BOTS` |
| `DELETE /bots?finished=true` | Retire les bots d'administration terminés |
| `DELETE /bots/{id}` | Quitte et supprime |
| `POST /bots/{id}/mic` | `{on}` → `{ok, changed}` |
| `GET /sounds` | Sons du dossier `sounds/` |
| `POST /bots/{id}/sounds/{sound}` | `{volume}` (0 à 2) |
| `POST /bots/{id}/stop` | Arrête les lectures du bot |
| `POST /broadcast/sounds/{sound}` | Joue sur les bots d'administration dans la réunion → `{played, failed:[{bot,error}], bots}` |
| `GET /stats` | RAM/CPU par bot, RAM système, capacité estimée |
| `GET /bots/{id}/events` | 50 derniers changements d'état |
| `GET /bots/{id}/screenshot` | Capture en direct, ou dernière capture d'erreur |

Un nom de son doit avoir une extension audio, sans séparateur de chemin ni caractère nul (404 sinon).

## API du GUI

Préfixe `/api`. Publique (le [code d'accès](server.md#deux-secrets-deux-rôles) éventuel est vérifié à la création de session). Toutes les routes, sauf `config` et `POST session`, exigent l'en-tête `X-Session: <identifiant>` ; sans lui ou avec un identifiant inconnu : 401.

| Route | Description |
|---|---|
| `GET /api/config` | Limites, délai de grâce, présence d'un code d'accès |
| `POST /api/session` | `{code?}` → `{sid}` ; 401 (code), 429 (plafonds par IP), 503 (serveur plein) |
| `DELETE /api/session` | Détruit la session (bots retirés, fichiers supprimés) |
| `GET /api/state` | Instantané : bots (avec action), sons, bibliothèque, téléchargements |
| `WS /api/ws` | Premier message `{sid}` ; état poussé à chaque changement. Codes de fermeture : 4401 session inconnue, 4403 hôte ou origine refusés |
| `POST /api/deploy` | `{links:[{meeting, passcode?}], count, mode: split\|each, name, auto_reconnect}` → `{bots:[id…]}` |
| `DELETE /api/bots[?finished=true]` | Retire tous les bots (ou les terminés) de la session |
| `DELETE /api/bots/{id}` | Retire un bot |
| `POST /api/bots/{id}/mic` | `{on}` |
| `GET /api/bots/{id}/events` · `GET /api/bots/{id}/screenshot` | Historique, capture |
| `PUT /api/sounds?name=…` | Corps = le fichier ; → `{id, name, duration, size, source}` ; 413 si trop gros |
| `DELETE /api/sounds/{id}` | Supprime un son |
| `POST /api/youtube` | `{url}` → `{job}` : téléchargement en arrière-plan |
| `POST /api/actions` | `{bots?, sound, count, gap, volume, stagger}` ; `bots` absent = tous les bots actifs ; `sound` = identifiant de session ou `lib:<nom>` |
| `POST /api/actions/stop` | `{bots?}` : arrête les actions et coupe les sons |

Codes d'erreur : 404 (bot ou son inconnu dans cette session), 410 (session terminée), 413, 422 (validation, message lisible), 429 (plafond atteint).

## Voir aussi

[Sessions](sessions.md) · [Sécurité](security.md) · [Limites et quotas](limits.md) · [La CLI](cli.md)
