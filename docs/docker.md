# Docker

```
ZOOMBOTS_TOKEN=un-jeton-long ZOOMBOTS_ACCESS_CODE=un-code-solide docker compose up -d
```

`docker-compose.yml` construit l'image et lance **un seul conteneur** qui héberge PulseAudio et tous les bots (pas de conteneur par bot).

## L'image

- Base `mcr.microsoft.com/playwright/python` à la version `PLAYWRIGHT_VERSION` (1.49.0), et le paquet `playwright` installé **à la même version** : Chromium doit correspondre exactement au paquet.
- Installe PulseAudio, ffmpeg et xvfb ; s'exécute **sans root** (`pwuser`).
- `ZOOMBOTS_SANDBOX=0` : le sandbox de Chromium demande des espaces de noms utilisateur, souvent absents d'un conteneur ; l'isolation est celle du conteneur ([Sécurité](security.md#chromium-et-son-sandbox)).
- `HEALTHCHECK` sur `/health` (sur `ZOOMBOTS_PORT`). Volumes : `/data` (état, sessions) et `/sounds` (sons du serveur).
- Commande : démarre PulseAudio en mode utilisateur, puis `python -m zoombots`.

## `docker-compose.yml`

| Réglage | Valeur |
|---|---|
| Port publié | `127.0.0.1:8000` (pas exposé directement : mets un proxy HTTPS devant) |
| `init: true` | PID 1 récolte les processus orphelins de Chromium (sinon des zombies s'accumulent) |
| `shm_size` | `2gb` (Chromium plante avec les 64 Mo par défaut) |
| `mem_limit` | `ZOOMBOTS_MEM_LIMIT`, défaut `8g` (20 Chromium épuiseraient la mémoire de l'hôte) |
| `restart`, `no-new-privileges` | `unless-stopped`, activé |
| Volumes | `./sounds:/sounds:ro` (sons du serveur) et `zoombots-data:/data` |
| `ZOOMBOTS_TOKEN`, `ZOOMBOTS_ACCESS_CODE` | **obligatoires** : `docker compose` refuse de démarrer sans eux (le conteneur écoute sur `0.0.0.0` ; code d'accès de 8 caractères ou plus recommandé) |
| `ZOOMBOTS_ALLOWED_HOSTS` | nom de domaine servi (défaut `*`) |
| `ZOOMBOTS_PUBLIC_ONLY` | `1` : administration cachée hors du conteneur |
| `ZOOMBOTS_TRUSTED_PROXIES` | défaut `127.0.0.1,::1,172.16.0.0/12` (réseaux Docker) : voir ci-dessous |
| `ZOOMBOTS_PROXY`, `ZOOMBOTS_REQUIRE_PROXY` | proxy de sortie des bots |
| `ZOOMBOTS_MAX_BOTS` | `10` |

## À savoir

- **HTTPS** : le conteneur ne le fournit pas. Place un proxy (Caddy, nginx) sur l'hôte qui transmet les WebSockets (`Upgrade`) et ajoute `X-Forwarded-For`.
- **Adresse des clients** : derrière Docker, l'application voit l'adresse de la passerelle du réseau Docker, pas celle de l'utilisateur ; elle ne croit `X-Forwarded-For` que des adresses de `ZOOMBOTS_TRUSTED_PROXIES`, qui accepte des **réseaux CIDR**. Le compose y met les réseaux Docker usuels (`172.16.0.0/12`). Si ton réseau Docker est dans une autre plage (`docker network inspect bridge`), ajoute-la ; sinon **tous les utilisateurs comptent comme une seule IP** pour les [plafonds par IP](limits.md#par-adresse-ip). Ne mets pas de plage qui contiendrait des clients non fiables.
- **Administration** : la CLI n'est pas pensée pour être pilotée de l'extérieur : `docker exec -it <conteneur> zoombots list` (le jeton est repris de l'environnement du conteneur).
- **Non validé** : l'image et le compose n'ont pas pu être construits dans l'environnement de développement ([Tests et validation](testing.md)).

## Voir aussi

[Serveur partagé](server.md) · [Installation](installation.md) · [Configuration](configuration.md)
