# Serveur partagé

Pour héberger l'application et laisser d'autres personnes y déployer leurs bots, depuis ta machine ou un serveur.

```
zoombots server start                                    # sans domaine : HTTP sur l'IP
zoombots server start --domain bots.monsite.fr           # avec domaine : HTTPS automatique
zoombots server info                                     # adresse, code d'accès, jeton
zoombots shutdown
```

Prérequis : `./install.sh --server` **uniquement si tu veux un nom de domaine** (il installe Caddy) ; le mode par adresse IP n'en a pas besoin. Voir [Installation](installation.md).

## Options de `server start`

| Option | Effet |
|---|---|
| `--domain D` | Nom de domaine : active Caddy et le HTTPS ([Nom de domaine](domain.md)) |
| `--no-domain` | Oublie le domaine configuré (retour à HTTP direct) |
| `--code C` | Code d'accès des utilisateurs : 8 caractères minimum (généré sinon, 72 bits) |
| `--reset-secrets` | Génère un nouveau code d'accès et un nouveau jeton d'administration |
| `--proxy URL[,URL…]` | Proxy de sortie des bots ([Masquer l'IP](privacy-ip.md)) |
| `--no-proxy` | Oublie le proxy configuré |
| `--port N` | Port de l'application (défaut 8000) |
| `--email E` | Adresse pour les avis d'expiration du certificat |

La configuration est écrite dans `~/.config/zoombots/server.json` (droits 0600 : code d'accès, jeton d'administration, proxy). Le code et le jeton sont **générés la première fois puis conservés** : relancer `server start` ne les change pas (`--reset-secrets` pour les renouveler). Le **domaine, le proxy et le port sont mémorisés aussi** : `server start` seul réutilise le domaine d'un lancement précédent ; `--no-domain` et `--no-proxy` les oublient. La configuration n'est écrite **qu'après un démarrage réussi**, et `server start` refuse de toucher à quoi que ce soit tant qu'un serveur tourne (`zoombots shutdown` d'abord).

## Deux secrets, deux rôles

| Secret | Pour qui | Sert à |
|---|---|---|
| **Code d'accès** | Les utilisateurs | Ouvrir une session du GUI |
| **Jeton d'administration** | Toi | `/admin`, API d'administration, CLI, **depuis la machine uniquement** |

## Avec ou sans domaine

| | Sans domaine | Avec domaine |
|---|---|---|
| Écoute de l'application | `0.0.0.0:<port>` | `127.0.0.1:<port>` (Caddy est la seule porte) |
| Protocole | HTTP : le code d'accès circule **en clair** | HTTPS, certificat Let's Encrypt (ou ZeroSSL) automatique |
| Ports à ouvrir | celui de l'application (8000, ou `--port`) | 80 et 443 |
| Noms d'hôte acceptés | tous (`ZOOMBOTS_ALLOWED_HOSTS=*`) | le domaine seul |
| À réserver à | un essai, un réseau de confiance | l'usage réel |

## Ce que `server start` active

- `ZOOMBOTS_MODE=server` : repère le mode pour la CLI (jeton repris de `server.json`). Les [plafonds par IP](limits.md#par-adresse-ip) sont actifs dans tout mode autre que `local`.
- `ZOOMBOTS_PUBLIC_ONLY=1` : l'administration répond 404 à toute requête qui ne vient pas de la machine ([Sécurité](security.md#administration-invisible-de-lextérieur)).
- `ZOOMBOTS_ACCESS_CODE` et `ZOOMBOTS_TOKEN` posés.
- Si un proxy est donné : `ZOOMBOTS_REQUIRE_PROXY=1`, aucun bot ne sort avec l'IP du serveur.
- Sans proxy, le serveur te prévient : Zoom verra l'IP de la machine.

## Arrêt, état

`zoombots shutdown` arrête l'application (les bots de session quittent) et Caddy. Le Caddyfile généré, le journal de Caddy (`caddy.log`) et l'état (`run.json`) sont dans le dossier de données.

## Voir aussi

[Nom de domaine](domain.md) · [Masquer l'IP](privacy-ip.md) · [Limites et quotas](limits.md) · [Sécurité](security.md) · [Docker](docker.md)
