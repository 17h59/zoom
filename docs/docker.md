# Docker

```
ZOOMBOTS_TOKEN=un-jeton-long docker compose up -d
```

`docker-compose.yml` construit l'image et lance **un seul conteneur** qui héberge PulseAudio et tous les bots (pas de conteneur par bot).

## L'image

- Base `mcr.microsoft.com/playwright/python` à la version `PLAYWRIGHT_VERSION` (1.49.0), et le paquet `playwright` installé **à la même version** : Chromium doit correspondre exactement au paquet.
- Installe PulseAudio, ffmpeg et xvfb ; s'exécute **sans root** (`pwuser`).
- `HEALTHCHECK` sur `/health`. Volumes : `/data` (état, sessions) et `/sounds` (sons du serveur).
- Commande : démarre PulseAudio en mode utilisateur, puis `python -m zoombots`.

## `docker-compose.yml`

| Réglage | Valeur |
|---|---|
| Port publié | `127.0.0.1:8000` (pas exposé directement : mets un proxy HTTPS devant) |
| `shm_size` | `2gb` (Chromium plante avec les 64 Mo par défaut) |
| `restart` | `unless-stopped` |
| `ZOOMBOTS_TOKEN` | **obligatoire** (le conteneur écoute sur `0.0.0.0`) |
| `ZOOMBOTS_ACCESS_CODE` | optionnel, code d'accès des utilisateurs |
| `ZOOMBOTS_ALLOWED_HOSTS` | nom de domaine servi (`*` pour tous) |
| `ZOOMBOTS_PUBLIC_ONLY` | `1` : administration cachée hors du conteneur |
| `ZOOMBOTS_PROXY`, `ZOOMBOTS_REQUIRE_PROXY` | proxy de sortie des bots |
| `ZOOMBOTS_MAX_BOTS` | `10` |

## À savoir

- **HTTPS** : le conteneur ne le fournit pas. Place un proxy (Caddy, nginx) sur l'hôte qui transmet les WebSockets (`Upgrade`) et ajoute `X-Forwarded-For`.
- **Adresse des clients** : derrière Docker, l'application voit l'adresse de la passerelle du réseau Docker, pas celle de l'utilisateur, et ne croit `X-Forwarded-For` que des adresses de `ZOOMBOTS_TRUSTED_PROXIES` (par défaut `127.0.0.1,::1`). Tant que l'adresse de la passerelle n'est pas déclarée dans cette variable, **tous les utilisateurs comptent comme une seule IP** pour les [plafonds par IP](limits.md#par-adresse-ip). Déclare-la, ou compte sur les plafonds par session et globaux.
- **Administration** : `zoombots` (la CLI) n'est pas pensée pour être pilotée de l'extérieur du conteneur : `docker exec` dans le conteneur est le chemin prévu.
- **Non validé** : l'image et le compose n'ont pas pu être construits dans l'environnement de développement ([Tests et validation](testing.md)).

## Voir aussi

[Serveur partagé](server.md) · [Installation](installation.md) · [Configuration](configuration.md)
