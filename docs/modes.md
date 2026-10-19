# Modes de lancement

zoombots se lance de quatre façons. Le choix tient à qui l'utilise et d'où.

| Commande | Pour qui | Exposition | Détail |
|---|---|---|---|
| `zoombots gui` | **Toi seul, chez toi** | Cette machine uniquement | [ci-dessous](#chez-soi--zoombots-gui) |
| `zoombots server start` | **Plusieurs utilisateurs**, sans nom de domaine | Internet, en HTTP | [Serveur partagé](server.md) |
| `zoombots server start --domain …` | **Plusieurs utilisateurs**, avec nom de domaine | Internet, en HTTPS (Caddy) | [Serveur partagé](server.md), [Nom de domaine](domain.md) |
| `zoombots start` / `serve` | Administration seule (dashboard `/admin`, API, CLI) | Cette machine | [CLI](cli.md), [API](api.md#api-dadministration) |

Un seul serveur à la fois par machine : `zoombots shutdown` arrête celui qui tourne (et Caddy le cas échéant). Plusieurs zoombots sur une même machine sont possibles avec `ZOOMBOTS_INSTANCE` : voir [Audio](audio.md#plusieurs-zoombots-sur-un-même-pulseaudio).

## Chez soi : `zoombots gui`

```
zoombots gui                      # lance l'interface et ouvre le navigateur
zoombots gui --port 8123          # autre port
zoombots gui --proxy http://hôte:port   # cache l'IP de cette machine
zoombots gui --no-open            # n'ouvre pas le navigateur
zoombots gui --headed             # Chromium non headless sous xvfb
```

- Écoute sur `127.0.0.1` seulement : rien n'est joignable depuis l'extérieur.
- Pas de code d'accès, pas de domaine, et **aucun plafond par adresse IP** (`ZOOMBOTS_MODE=local`). Les plafonds par session restent (20 bots actifs par session au lieu de 10).
- Si l'interface tourne déjà, la commande ouvre simplement le navigateur dessus.
- Fermer l'onglet retire ses bots ([Sessions](sessions.md)). `zoombots shutdown` arrête tout.
- Sans proxy, Zoom voit l'adresse IP de cette machine : voir [Masquer l'IP](privacy-ip.md).

## Après le lancement

Les commandes de la [CLI](cli.md) (`list`, `spawn`, `stats`…) visent automatiquement le serveur lancé par `gui` ou `server start` (port et jeton d'administration repris de `run.json` et `server.json`).

## Voir aussi

[Installation](installation.md) · [Le GUI](gui.md) · [Configuration](configuration.md)
